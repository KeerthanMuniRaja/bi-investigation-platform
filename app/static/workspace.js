"use strict";

/* =====================================================================
   Dashboard workspace — the main layout.

   Left sidebar  : uploaded dashboards; each opens to list its pages.
   Right sidebar : the measures used on the selected page (or all of
                   them), grouped by display folder, with search.
   Main          : empty until a measure is picked, then its whole
                   lineage, source -> visuals. Double-click any object in
                   the lineage to see everything the file tells us about it.

   State lives in the URL: #/?c=<connection>&p=<page>&m=<measure>.
   Loaded before app.js; uses its helpers ($, esc, GET, api, toast…) at
   run time only.
   ===================================================================== */
const WS = {
  models: null,                       // [{connection_id, name, file_name, ...}]
  overviews: {},                      // cid -> Promise<overview>
  open: new Set((() => { try { return JSON.parse(localStorage.getItem("ws-open") || "[]"); } catch { return []; } })()),
  scope: (() => { try { return localStorage.getItem("ws-scope") || "page"; } catch { return "page"; } })(),
  query: "",
  folded: new Set(),                  // collapsed folders in the measure list
  explorer: null,
  current: {},                        // {cid, pid, mid}
};
const wsSaveOpen = () => { try { localStorage.setItem("ws-open", JSON.stringify([...WS.open])); } catch { /* ignore */ } };
const wsHref = (o) => `#/${q(Object.fromEntries(Object.entries(o).filter(([, v]) => v)))}`;
const wsIcon = (d, cls = "") => `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const WS_I = {
  chev: '<path d="M9 6l6 6-6 6"/>',
  dash: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M7 15v-3M12 15V8M17 15v-5"/>',
  page: '<path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z"/><path d="M14 3v5h5"/>',
  sigma: '<path d="M18 7V4H6l6 8-6 8h12v-3"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  reload: '<path d="M4 4v6h6M20 20v-6h-6M5 15a7 7 0 0 0 12 3M19 9A7 7 0 0 0 7 6"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  overview: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  health: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  rel: '<circle cx="6" cy="6" r="3"/><circle cx="18" cy="18" r="3"/><path d="M8.5 8.5l7 7"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 10v10"/>',
  report: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 15v-3M12 15V8M17 15v-5"/>',
};

/* the router tells us about every navigation, so the sidebar can mark where we are */
function wsRouteChanged(name, path) {
  if (name === "workspace") return;  // viewWorkspace sets WS.current itself
  const m = String(path || "").match(/^\/models\/([^/?]+)/);
  WS.current = { cid: m ? decodeURIComponent(m[1]) : null };
  paintDashNav();
}

/* overview = model documentation from the server (cached per session) */
function wsOverview(cid) {
  if (!WS.overviews[cid]) {
    WS.overviews[cid] = api("GET", `/connections/${encodeURIComponent(cid)}/overview`).catch((e) => { delete WS.overviews[cid]; throw e; });
  }
  return WS.overviews[cid];
}

/* page id -> the measures its visuals show directly */
function wsPageMeasures(ov, page) {
  const ids = new Set();
  for (const v of page?.visuals || []) for (const f of v.fields || []) if (f.type === "MEASURE") ids.add(f.id);
  return ov.measures.filter((m) => ids.has(m.id));
}

/* ------------------------------------------------------- left sidebar */
async function initDashNav() {
  const nav = $("#dash-nav");
  if (!nav) return;
  $("#dash-filter").oninput = debounce(() => paintDashNav(), 120);
  document.addEventListener("keydown", (e) => {
    if (e.key !== "/" || e.target.matches?.("input, textarea, select") || document.querySelector(".modal-back")) return;
    const search = $("#rightbar:not([hidden]) .rb-search input");
    if (search) { e.preventDefault(); search.focus(); search.select(); }
  });
  $("#dash-upload").onchange = (e) => e.target.files.length && wsUpload(e.target.files[0]);
  await refreshDashNav();
}

async function refreshDashNav() {
  const nav = $("#dash-nav");
  try {
    WS.models = await api("GET", "/model-files");
  } catch (e) {
    nav.innerHTML = `<div class="dash-empty">${esc(e.message)}</div>`;
    return;
  }
  paintDashNav();
}

function paintDashNav() {
  const nav = $("#dash-nav");
  if (!nav || !WS.models) return;
  const needle = ($("#dash-filter")?.value || "").trim().toLowerCase();
  if (!WS.models.length) {
    nav.innerHTML = `<div class="dash-empty">No dashboards yet.<br>Use <b>+</b> above to upload a .pbix.</div>`;
    return;
  }
  const { cid: curC, pid: curP } = WS.current;
  const nameCount = {};
  for (const m of WS.models) nameCount[m.name] = (nameCount[m.name] || 0) + 1;
  nav.innerHTML = WS.models.map((m) => {
    const dup = nameCount[m.name] > 1;
    const open = WS.open.has(m.connection_id) || m.connection_id === curC || !!needle;
    return `<div class="dash ${open ? "open" : ""} ${m.connection_id === curC ? "current" : ""}" data-cid="${esc(m.connection_id)}">
      <div class="dash-row">
        <button class="dash-toggle" data-toggle aria-expanded="${open}" title="${esc(m.file_name || m.name)}">
          ${wsIcon(WS_I.chev, "chev")}<span class="dash-ico">${wsIcon(WS_I.dash)}</span>
          <span class="dash-label"><span class="dash-name">${esc(m.name)}</span>${dup ? `<span class="dash-sub">read ${esc(fmt.date(m.last_synced_at))}</span>` : ""}</span>
        </button>
        <span class="dash-tools">
          <button data-reread title="Re-read the file">${wsIcon(WS_I.reload)}</button>
          <button data-delete title="Remove this dashboard">${wsIcon(WS_I.trash)}</button>
        </span>
      </div>
      <div class="dash-pages" data-pages>${open ? `<div class="dash-loading"><span class="spinner"></span>Reading pages…</div>` : ""}</div>
    </div>`;
  }).join("");
  for (const el of $$(".dash", nav)) {
    const cid = el.dataset.cid;
    if (el.classList.contains("open")) wsFillPages(el, cid, needle, curP);
    $("[data-toggle]", el).onclick = () => {
      const opening = !el.classList.contains("open");
      el.classList.toggle("open", opening);
      $("[data-toggle]", el).setAttribute("aria-expanded", String(opening));
      opening ? WS.open.add(cid) : WS.open.delete(cid);
      wsSaveOpen();
      if (opening) {
        $("[data-pages]", el).innerHTML = `<div class="dash-loading"><span class="spinner"></span>Reading pages…</div>`;
        wsFillPages(el, cid, needle, curP);
      }
    };
    $("[data-reread]", el).onclick = async (e) => {
      e.stopPropagation();
      const btn = e.currentTarget;
      btn.classList.add("spin");
      try {
        await syncConnection(cid);
        delete WS.overviews[cid];
        toast("Re-read the file", "ok");
        paintDashNav();
        if (WS.current.cid === cid) render();
      } catch (err) { toast(err.message, "bad"); } finally { btn.classList.remove("spin"); }
    };
    $("[data-delete]", el).onclick = (e) => {
      e.stopPropagation();
      const m = WS.models.find((x) => x.connection_id === cid);
      confirmDelete(`dashboard "${m?.name || cid}"`, async () => {
        await DEL(`/model-files/${encodeURIComponent(cid)}`);
        delete WS.overviews[cid];
        WS.open.delete(cid);
        wsSaveOpen();
        await refreshDashNav();
        if (WS.current.cid === cid) location.hash = "#/";
      });
    };
  }
}

async function wsFillPages(el, cid, needle, curP) {
  const box = $("[data-pages]", el);
  try {
    const ov = await wsOverview(cid);
    if (!box.isConnected) return;
    const pages = ov.pages.filter((p) => !needle || p.name.toLowerCase().includes(needle)
      || (WS.models.find((m) => m.connection_id === cid)?.name || "").toLowerCase().includes(needle));
    const modelHref = (tab) => `#/models/${encodeURIComponent(cid)}?tab=${tab}`;
    const here = location.hash;
    const tool = (tab, label, ic, count, bad) => `<a class="page-link tool ${here === modelHref(tab) ? "active" : ""}" href="${modelHref(tab)}">
        ${wsIcon(ic)}<span class="pl-name">${label}</span>${count != null ? `<span class="pl-count ${bad ? "bad" : ""}">${fmt.int(count)}</span>` : ""}</a>`;
    const c = ov.counts || {};
    const modelLinks = needle ? "" : `<div class="dash-sec">Model</div>
      ${tool("overview", "Overview", WS_I.overview)}${tool("health", "Health check", WS_I.health, c.issues, c.issues > 0)}
      ${tool("relationships", "Relationships", WS_I.rel, c.relationships)}${tool("tables", "Tables", WS_I.table, c.tables)}
      ${tool("report", "Report map", WS_I.report, c.visuals)}
      <div class="dash-sec">Pages</div>`;
    box.innerHTML = modelLinks + (pages.length ? pages.map((p) => {
      const n = wsPageMeasures(ov, p).length;
      return `<a class="page-link ${p.id === curP && cid === WS.current.cid ? "active" : ""}" href="${wsHref({ c: cid, p: p.id })}" title="${esc(p.name)}">
        ${wsIcon(WS_I.page)}<span class="pl-name">${esc(p.name)}</span>${p.hidden ? `<span class="pl-tag">hidden</span>` : ""}<span class="pl-count" title="${n} measure(s) on this page">${n}</span></a>`;
    }).join("") : `<div class="dash-empty small">${ov.pages.length ? "No pages match." : "This file has no report pages (model only)."}</div>`);
  } catch (e) {
    if (box.isConnected) box.innerHTML = `<div class="dash-empty small bad">${esc(e.message)}</div>`;
  }
}

async function wsUpload(file) {
  const nav = $("#dash-nav");
  nav.insertAdjacentHTML("afterbegin", `<div class="dash-uploading"><span class="spinner"></span>Reading ${esc(file.name)}…</div>`);
  try {
    const fd = new FormData();
    fd.append("file", file);
    const res = await api("POST", "/model-files", fd);
    toast(`Read ${res.name}: ${res.counts.pages} pages, ${res.counts.measures} measures`, "ok");
    WS.open.add(res.connection_id);
    wsSaveOpen();
    await refreshDashNav();
    location.hash = wsHref({ c: res.connection_id });
  } catch (e) {
    toast(e.message, "bad");
    $(".dash-uploading", nav)?.remove();
  } finally {
    $("#dash-upload").value = "";
  }
}

/* ----------------------------------------------------- right sidebar */
function wsShowRightbar(on) {
  const rb = $("#rightbar");
  if (!rb) return;
  rb.hidden = !on;
  document.body.classList.toggle("has-rightbar", on);
}

function paintRightbar(cid, ov, page, mid) {
  const rb = $("#rightbar");
  const prevScroll = $(".rb-list", rb)?.scrollTop || 0;
  const onPage = page ? wsPageMeasures(ov, page) : [];
  let scope = WS.scope === "page" && page && onPage.length ? "page" : "all";
  const needle = WS.query.trim().toLowerCase();
  // a search always looks through every measure, by name, folder, table — and DAX
  let list = needle ? ov.measures : scope === "page" ? onPage : ov.measures;
  if (needle) {
    list = list.map((m) => ({ m, name: m.name.toLowerCase().includes(needle), other: [m.folder, m.table].some((x) => String(x || "").toLowerCase().includes(needle)),
      dax: String(m.expression || "").toLowerCase().includes(needle) }))
      .filter((r) => r.name || r.other || r.dax).sort((a, b) => (b.name - a.name) || a.m.name.localeCompare(b.m.name))
      .map((r) => ({ ...r.m, _inDax: !r.name && !r.other && r.dax }));
  }
  const mark = (text) => {
    const t = String(text ?? "");
    if (!needle) return esc(t);
    const i = t.toLowerCase().indexOf(needle);
    return i < 0 ? esc(t) : `${esc(t.slice(0, i))}<mark>${esc(t.slice(i, i + needle.length))}</mark>${esc(t.slice(i + needle.length))}`;
  };
  const groups = new Map();
  for (const m of list) {
    const key = m.folder || "";
    (groups.get(key) || groups.set(key, []).get(key)).push(m);
  }
  const keys = [...groups.keys()].sort((a, b) => (a === "") - (b === "") || a.localeCompare(b));
  const item = (m) => `<a class="rb-item ${m.id === mid ? "active" : ""}" href="${wsHref({ c: cid, p: page?.id, m: m.id })}" title="${esc(m.name)}">
      <span class="rb-ico cat-${esc((m.category || "").replace(/\W+/g, "-"))}">${wsIcon(WS_I.sigma)}</span>
      <span class="rb-text"><span class="rb-name">${mark(m.name)}</span>
        <span class="rb-meta">${m._inDax ? `<span class="rb-hit">matched in DAX</span>` : esc(m.category || "")}${m.used_by_visuals ? ` · ${m.used_by_visuals} visual${m.used_by_visuals === 1 ? "" : "s"}` : ""}</span></span>
      ${m.unresolved?.length ? `<span class="rb-flag bad" title="References fields that don't exist">!</span>` : m.unused ? `<span class="rb-flag warn" title="Not used by any visual">○</span>` : ""}
    </a>`;
  const body = !list.length
    ? `<div class="rb-empty">${needle ? `No measure matches “${esc(WS.query)}”.` : page ? "No measures on this page's visuals — they show plain columns." : "This model has no measures."}</div>`
    : keys.map((k) => {
      const items = groups.get(k);
      if (keys.length === 1 && !k) return items.map(item).join("");
      const folded = WS.folded.has(k) && !needle;
      return `<div class="rb-folder ${folded ? "folded" : ""}"><button class="rb-fhead" data-folder="${esc(k)}">${wsIcon(WS_I.chev, "chev")}${wsIcon(WS_I.folder, "fico")}<span>${esc(k || "No folder")}</span><b>${items.length}</b></button>
        <div class="rb-fitems">${items.map(item).join("")}</div></div>`;
    }).join("");
  rb.innerHTML = `
    <div class="rb-head">
      <div class="rb-eyebrow">${wsIcon(WS_I.sigma)}Measures</div>
      <div class="rb-title">${esc(page?.name || "All pages")}</div>
      <div class="rb-sub">${esc(ov.name)}</div>
    </div>
    <div class="rb-search">${wsIcon(WS_I.search)}<input type="search" placeholder="Search all ${ov.measures.length} measures…" value="${esc(WS.query)}" aria-label="Search measures">${WS.query ? `<button data-clear title="Clear">${wsIcon(WS_I.close)}</button>` : ""}</div>
    <div class="rb-scope" ${needle ? "data-searching" : ""}>
      <button data-scope="page" class="${scope === "page" && !needle ? "on" : ""}" ${page && onPage.length ? "" : "disabled"}>This page <b>${onPage.length}</b></button>
      <button data-scope="all" class="${scope === "all" || needle ? "on" : ""}">All measures <b>${ov.measures.length}</b></button>
    </div>
    ${needle ? `<div class="rb-note">${list.length} result${list.length === 1 ? "" : "s"} across all measures</div>` : ""}
    <div class="rb-list">${body}</div>`;
  const input = $(".rb-search input", rb);
  input.oninput = debounce(() => {
    WS.query = input.value;
    paintRightbar(cid, ov, page, mid);
    const again = $(".rb-search input", rb);
    again.focus();
    again.setSelectionRange(again.value.length, again.value.length);
  }, 140);
  input.onkeydown = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); $(".rb-item", rb)?.focus(); }
    else if (e.key === "Enter") { const first = $(".rb-item", rb); if (first) { e.preventDefault(); location.hash = first.getAttribute("href"); } }
  };
  $(".rb-list", rb).onkeydown = (e) => {
    const items = $$(".rb-item", rb).filter((x) => x.offsetParent);
    const i = items.indexOf(document.activeElement);
    if (e.key === "ArrowDown" && i >= 0) { e.preventDefault(); items[Math.min(items.length - 1, i + 1)].focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); i <= 0 ? input.focus() : items[i - 1].focus(); }
    else if (e.key === "Escape") input.focus();
  };
  $("[data-clear]", rb)?.addEventListener("click", () => { WS.query = ""; paintRightbar(cid, ov, page, mid); $(".rb-search input", rb).focus(); });
  $$("[data-scope]", rb).forEach((b) => (b.onclick = () => {
    WS.scope = b.dataset.scope;
    WS.query = "";
    try { localStorage.setItem("ws-scope", WS.scope); } catch { /* ignore */ }
    paintRightbar(cid, ov, page, mid);
  }));
  $$("[data-folder]", rb).forEach((b) => (b.onclick = () => {
    const k = b.dataset.folder;
    WS.folded.has(k) ? WS.folded.delete(k) : WS.folded.add(k);
    b.parentElement.classList.toggle("folded");
  }));
  const listEl = $(".rb-list", rb);
  listEl.scrollTop = prevScroll;
  const active = $(".rb-item.active", listEl);
  if (active && (active.offsetTop < listEl.scrollTop || active.offsetTop > listEl.scrollTop + listEl.clientHeight - 40)) active.scrollIntoView({ block: "center" });
}

/* ------------------------------------------------------------- main */
async function viewWorkspace(view, params) {
  const cid = params.get("c"), pid = params.get("p"), mid = params.get("m");
  WS.current = { cid, pid, mid };
  view.classList.add("ws-page");
  if (!cid && !WS.restored) {
    WS.restored = true;  // once per visit: going "home" later should stay home
    let last = null;
    try { last = JSON.parse(localStorage.getItem("ws-last") || "null"); } catch { /* ignore */ }
    const models = WS.models || await api("GET", "/model-files").catch(() => []);
    if (last?.c && models.some((m) => m.connection_id === last.c)) { location.replace(wsHref(last)); return; }
  }
  WS.restored = true;
  if (cid) { try { localStorage.setItem("ws-last", JSON.stringify({ c: cid, p: pid, m: mid })); } catch { /* ignore */ } }
  paintDashNav();
  if (!cid) {
    wsShowRightbar(false);
    const models = WS.models || await api("GET", "/model-files");
    view.innerHTML = models.length
      ? wsEmpty("Pick a dashboard page", "Open a dashboard on the left and choose one of its pages. Its measures appear on the right; pick one to see where it comes from and where it goes.", WS_I.dash)
      : `<div class="ws-welcome">${wsEmpty("Upload your first dashboard", "Drop a Power BI file to explore its pages, measures and lineage — read locally, nothing leaves this machine.", WS_I.dash)}<div class="ws-drop">${modelDropzone()}</div></div>`;
    if (!models.length) wireModelDropzone(view);
    return;
  }
  view.innerHTML = `<div class="ws-loading">${loading("Reading the dashboard…")}</div>`;
  let ov;
  try {
    ov = await wsOverview(cid);
  } catch (e) {
    view.innerHTML = `<div class="card"><div class="card-body">${alertBox("bad", [e.message])}</div></div>`;
    wsShowRightbar(false);
    return;
  }
  if (!isLive(view)) return;
  const page = ov.pages.find((p) => p.id === pid) || null;
  wsCrumbs(ov, page, ov.measures.find((m) => m.id === mid) || (mid ? { name: mid } : null));
  wsShowRightbar(true);
  paintRightbar(cid, ov, page, mid);
  if (!mid) {
    const n = page ? wsPageMeasures(ov, page).length : ov.measures.length;
    view.innerHTML = page
      ? wsEmpty(page.name, n ? `${n} measure${n === 1 ? "" : "s"} on this page — pick one on the right to see its full lineage.` : "No measures on this page. Switch to “All measures” on the right, or search.", WS_I.page)
      : wsDashboardSummary(cid, ov);
    return;
  }
  const m = ov.measures.find((x) => x.id === mid);
  view.innerHTML = `
    <div class="ws-main">
      <div class="ws-title">
        <span class="ws-kind">${wsIcon(WS_I.sigma)}${m ? "Measure" : "Lineage"}</span>
        <h1>${esc(m?.name || mid)}</h1>
        <span class="ws-hint">Click a box to trace its line · <b>double-click</b> for details · <b>+ / −</b> expands or collapses a step</span>
      </div>
      <div class="ws-lineage" data-lineage></div>
      <section class="ws-inspect" data-inspect hidden></section>
    </div>`;
  WS.explorer = new LineageExplorer($("[data-lineage]", view), {
    cid, root: mid, up: 10, down: 10, maxSteps: 12, embedded: true,
    onInspect: (node) => wsInspect(view, cid, ov, node),
  });
}

/* a dashboard picked but no page yet: what's in it, and the way to every view of it */
function wsDashboardSummary(cid, ov) {
  const c = ov.counts || {};
  const href = (tab) => `#/models/${encodeURIComponent(cid)}?tab=${tab}`;
  const card = (tab, ic, title, text, value, kind = "") => `<a class="ws-card ${kind}" href="${href(tab)}">
      <span class="ws-card-ico">${wsIcon(ic)}</span><span class="ws-card-body"><b>${esc(title)}</b><span>${esc(text)}</span></span>
      ${value != null ? `<span class="ws-card-val">${value}</span>` : ""}</a>`;
  const top = ov.insights.filter((i) => i.severity === "high" || i.severity === "medium").slice(0, 4);
  return `<div class="ws-summary">
    <div class="ws-sum-head"><span class="ws-kind">${wsIcon(WS_I.dash)}Dashboard</span><h1>${esc(ov.name)}</h1>
      <p>Pick a page on the left to see its measures, or open any view of the model below.</p></div>
    <div class="kpis">
      ${kpi("Pages", fmt.int(c.pages))}${kpi("Visuals", fmt.int(c.visuals))}${kpi("Measures", fmt.int(c.measures), `${fmt.int(c.unused_measures)} unused`)}
      ${kpi("Tables", fmt.int(c.tables), `${fmt.int(c.columns)} columns`)}${kpi("Relationships", fmt.int(c.relationships))}
      ${kpi("Issues", fmt.int(c.issues), "high / medium", c.issues ? "warn" : "ok", href("health"))}
    </div>
    <div class="ws-cards">
      ${card("overview", WS_I.overview, "Overview", "Data sources, health summary, tables by type")}
      ${card("health", WS_I.health, "Health check", "Broken references, unused objects, risky modelling", fmt.int(c.issues), c.issues ? "warn" : "")}
      ${card("relationships", WS_I.rel, "Relationships", "Model diagram, cardinality, filter direction", fmt.int(c.relationships))}
      ${card("tables", WS_I.table, "Tables & columns", "How each table is loaded, Power Query steps, data preview", fmt.int(c.tables))}
      ${card("measures", WS_I.sigma, "All measures", "Every measure explained, with its DAX and usage", fmt.int(c.measures))}
      ${card("report", WS_I.report, "Report map", "Every page and visual, the fields it shows and its filters", fmt.int(c.visuals))}
    </div>
    ${top.length ? `<div class="card"><div class="card-head"><h2>Needs attention</h2><span class="spacer"></span><a class="small" href="${href("health")}">All findings →</a></div>
      <div class="card-body stack">${top.map((i) => `<div class="row" style="flex-wrap:nowrap;align-items:flex-start">${badge(i.severity, i.severity === "high" ? "bad" : "warn")}<span class="small">${esc(i.title)}</span></div>`).join("")}</div></div>` : ""}
  </div>`;
}

function wsEmpty(title, text, ic) {
  return `<div class="ws-empty"><div class="ws-empty-ico">${wsIcon(ic)}</div><h2>${esc(title)}</h2><p>${esc(text)}</p></div>`;
}

function wsCrumbs(ov, page, m) {
  const parts = [["Dashboards", "#/"], [ov.name, wsHref({ c: WS.current.cid })]];
  if (page) parts.push([page.name, wsHref({ c: WS.current.cid, p: page.id })]);
  if (m) parts.push([m.name, ""]);
  $("#crumbs").innerHTML = parts.map(([t, h], i) => (i === parts.length - 1 ? `<b>${esc(t)}</b>` : `<a href="${esc(h)}">${esc(t)}</a><span class="sep">/</span>`)).join("");
}

/* ---------------------------------------------- details (double-click) */
function daxHtml(src) {
  const text = String(src || "").replace(/^\s*\n/, "").replace(/\s+$/, "");
  const re = /(\/\/[^\n]*|--[^\n]*|\/\*[\s\S]*?(?:\*\/|$))|("(?:[^"]|"")*")|('(?:[^']|'')+')|(\[(?:[^\]]|\]\])+\])|(\b\d+(?:\.\d+)?\b)|\b([A-Za-z_][\w.]*)(?=\s*\()|\b(VAR|RETURN|IN|NOT|AND|OR|TRUE|FALSE|ASC|DESC|let|each)\b/g;
  let out = "", last = 0;
  for (const m of text.matchAll(re)) {
    const cls = m[1] ? "c" : m[2] ? "s" : m[3] ? "t" : m[4] ? "r" : m[5] ? "n" : m[6] ? "f" : "k";
    out += esc(text.slice(last, m.index)) + `<span class="dx-${cls}">${esc(m[0])}</span>`;
    last = m.index + m[0].length;
  }
  return out + esc(text.slice(last));
}

function wsIndex(ov) {
  if (ov._index) return ov._index;
  const idx = { measures: new Map(), tables: new Map(), columns: new Map(), visuals: new Map(), byName: new Map() };
  for (const m of ov.measures) { idx.measures.set(m.id, m); idx.byName.set(m.name, m); }
  for (const t of ov.tables) {
    idx.tables.set(t.id || t.name, t);
    for (const c of t.columns || []) idx.columns.set(c.id, { ...c, table: t.name, tableKind: t.kind, tableSource: t.source });
  }
  for (const p of ov.pages) for (const v of p.visuals || []) idx.visuals.set(v.id, { ...v, page: p });
  ov._index = idx;
  return idx;
}

/* "Sales[Amount]", "Sales[Qty]" -> Sales: Amount, Qty */
function wsColumnsByTable(ids) {
  const groups = new Map();
  for (const id of ids) {
    const i = id.lastIndexOf("[");
    const table = i > 0 ? id.slice(0, i) : "", col = i > 0 ? id.slice(i + 1, -1) : id;
    (groups.get(table) || groups.set(table, []).get(table)).push([id, col]);
  }
  return [...groups].map(([table, cols]) => `<div class="ins-colgroup"><span class="ins-colt">${esc(table)}</span>${cols.map(([id, col]) =>
    `<button class="ins-chip" data-jump="${esc(id)}"><i style="background:${lxType("COLUMN").color}"></i>${esc(col)}</button>`).join("")}</div>`).join("");
}

function wsInspect(view, cid, ov, node) {
  const box = $("[data-inspect]", view);
  if (!box) return;
  const idx = wsIndex(ov);
  const t = lxType(node.type);
  const kv = (rows) => `<dl class="ins-kv">${rows.filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;
  const chip = (text, color, jump) => `<button class="ins-chip" ${jump ? `data-jump="${esc(jump)}"` : "disabled"}><i style="background:${color}"></i>${esc(text)}</button>`;
  const code = (src, label) => src ? `<div class="ins-code"><div class="ins-code-head"><span>${esc(label)}</span><button class="lx-btn" data-copy="${esc(src)}" title="Copy">${wsIcon(WS_I.copy)}Copy</button></div><pre class="mono">${daxHtml(src)}</pre></div>` : "";
  const section = (title, inner) => inner ? `<div class="ins-sec"><h3>${esc(title)}</h3>${inner}</div>` : "";
  let body = "", badges = "";

  if (node.type === "MEASURE") {
    const m = idx.measures.get(node.id) || {};
    badges = [m.category && `<span class="badge info">${esc(m.category)}</span>`, m.unused && `<span class="badge warn">not used by any visual</span>`,
      m.unresolved?.length && `<span class="badge bad">broken reference</span>`].filter(Boolean).join("");
    body = `
      ${m.explanation ? `<div class="ins-explain"><b>In plain English:</b> ${esc(m.explanation)}</div>` : ""}
      <div class="ins-grid">
        <div>${code(m.expression || node.properties?.dax_expression, "DAX")}
          ${m.unresolved?.length ? alertBox("bad", [`References that don't exist in the model: ${m.unresolved.join(", ")}`]) : ""}</div>
        <div>${kv([["Home table", esc(m.table)], ["Display folder", m.folder ? `${wsIcon(WS_I.folder, "inl")}${esc(m.folder)}` : `<span class="faint">none</span>`],
          ["Description", m.description ? esc(m.description) : `<span class="faint">none</span>`], ["Kind", esc(m.category)],
          ["Used by", `${m.used_by_visuals || 0} visual(s), ${m.used_by_measures || 0} measure(s)`]])}
          ${section("Depends on", [...(m.depends_on_measures || []).map((n) => chip(n, lxType("MEASURE").color, idx.byName.get(n)?.id)),
            ...(m.depends_on_columns || []).map((c) => chip(c, lxType("COLUMN").color, c)), ...(m.depends_on_tables || []).map((tb) => chip(tb, lxType("TABLE").color, tb))].join("") || '<span class="faint small">Nothing — a constant or unrecognised references.</span>')}
          ${section("Shown on", (m.visual_ids || []).map((v) => { const vis = idx.visuals.get(v); return chip(vis ? `${vis.page.name} › ${vis.title}` : v, lxType("VISUAL").color, v); }).join("") || '<span class="faint small">No visual in this report.</span>')}
          ${section("Used by measures", ov.measures.filter((x) => (x.depends_on_measures || []).includes(m.name)).map((x) => chip(x.name, lxType("MEASURE").color, x.id)).join(""))}
          ${section("Reads columns", wsColumnsByTable(m.depends_on_columns || []))}
        </div>
      </div>`;
  } else if (node.type === "COLUMN") {
    const c = idx.columns.get(node.id) || {};
    badges = [c.calculated && `<span class="badge info">calculated</span>`, c.hidden && `<span class="badge neutral">hidden</span>`, c.used === false && `<span class="badge warn">unused</span>`].filter(Boolean).join("");
    body = `
      ${c.explanation ? `<div class="ins-explain"><b>In plain English:</b> ${esc(c.explanation)}</div>` : ""}
      <div class="ins-grid">
        <div>${code(c.expression, "DAX (calculated column)") || `<div class="ins-explain faint">A stored column, loaded from ${esc(c.table || "its table")}${c.tableSource ? ` (${esc(c.tableSource)})` : ""}.</div>`}</div>
        <div>${kv([["Table", c.table ? chip(c.table, lxType("TABLE").color, c.table) : ""], ["Data type", esc(c.data_type || node.properties?.data_type)],
          ["Table kind", esc(c.tableKind)], ["Loads from", esc(c.tableSource)], ["Hidden", c.hidden ? "yes" : "no"]])}</div>
      </div>`;
  } else if (node.type === "TABLE") {
    const tb = idx.tables.get(node.id) || {};
    badges = [`<span class="badge info">${esc(tb.kind || node.properties?.kind || "table")}</span>`, tb.hidden && `<span class="badge neutral">hidden</span>`, tb.used === false && `<span class="badge warn">unused</span>`].filter(Boolean).join("");
    body = `
      ${tb.summary ? `<div class="ins-explain"><b>How it's loaded:</b> ${esc(tb.summary)}</div>` : ""}
      <div class="ins-grid">
        <div>${tb.steps?.length ? section(`Power Query steps (${tb.steps.length})`, `<ol class="ins-steps">${tb.steps.map((s) => `<li><b>${esc(s.name)}</b><span>${esc(s.description)}</span></li>`).join("")}</ol>`) : ""}
          ${code(tb.expression, tb.kind === "calculated" || tb.kind === "parameter" ? "DAX definition" : "Power Query (M)")}</div>
        <div>${kv([["Loads from", esc(tb.source)], ["Rows", tb.rows != null ? fmt.int(tb.rows) : ""], ["Column count", `${tb.column_count ?? ""}${tb.calculated_columns ? ` (${tb.calculated_columns} calculated)` : ""}`],
          ["Measures stored here", tb.measure_count || ""], ["Relationships", tb.has_relationships ? "yes" : "none"], ["Built from", (tb.depends_on_tables || []).map((d) => chip(d, lxType("TABLE").color, d)).join("")]])}
          ${section("Columns", `<div class="ins-cols">${(tb.columns || []).map((c) => `<button class="ins-col" data-jump="${esc(c.id)}"><span>${esc(c.name)}</span><em>${esc(c.data_type)}${c.calculated ? " · calc" : ""}</em></button>`).join("")}</div>`)}</div>
      </div>`;
  } else if (node.type === "VISUAL") {
    const v = idx.visuals.get(node.id) || {};
    badges = [v.type && `<span class="badge info">${esc(v.type)}</span>`, v.broken?.length && `<span class="badge bad">${v.broken.length} broken field(s)</span>`].filter(Boolean).join("");
    body = `<div class="ins-grid">
        <div>${kv([["Page", esc(v.page?.name)], ["Visual type", esc(v.type)], ["Visual id", `<code>${esc(node.id)}</code>`]])}</div>
        <div>${section("Shows", (v.fields || []).map((f) => chip(f.name, f.type === "MISSING" ? "var(--bad)" : lxType(f.type).color, f.type === "MISSING" ? "" : f.id)).join("") || '<span class="faint small">No data fields.</span>')}
          ${section("Filtered by", (v.filters || []).map((f) => chip(f.name, f.missing ? "var(--bad)" : lxType("COLUMN").color, f.missing ? "" : f.id)).join("") || '<span class="faint small">No filters.</span>')}
          ${v.broken?.length ? alertBox("bad", [`Fields that no longer exist in the model: ${v.broken.join(", ")}`]) : ""}</div>
      </div>`;
  } else if (node.type === "DATA_SOURCE") {
    const tables = ov.tables.filter((tb) => tb.source && (node.name === tb.source || node.id === `SRC::${tb.source}`));
    const src = ov.data_sources.find((s) => s.tables.some((x) => tables.some((tb) => tb.name === x.model_table)));
    body = `<div class="ins-grid">
        <div>${kv([["Kind", esc(src?.kind)], ["Server", esc(src?.server)], ["Database", esc(src?.database)], ["Path", esc(src?.path)],
          ["Upstream", node.properties?.upstream_traced ? "traced" : `<span class="faint">ETL / views upstream of this source aren't visible in the file</span>`]])}</div>
        <div>${section("Loads these tables", (src?.tables || tables.map((tb) => ({ model_table: tb.name }))).map((x) => chip(`${x.model_table}${x.source_object ? ` ← ${x.source_object}` : ""}${x.native_query ? " (SQL query)" : ""}`, lxType("TABLE").color, x.model_table)).join(""))}</div>
      </div>`;
  } else {
    body = alertBox(node.type === "MISSING" ? "bad" : "info", [node.type === "MISSING" ? `“${node.name}” is referenced by the report or a formula but doesn't exist in the model — it was renamed or removed.` : `${t.label}: ${node.name}`]);
  }

  box.hidden = false;
  box.innerHTML = `
    <header class="ins-head">
      <span class="ins-ico" style="background:${t.color}">${esc(t.glyph)}</span>
      <div class="ins-titles"><div class="ins-type" style="color:${t.color}">${esc(t.label)}</div><h2>${esc(node.name)}</h2><div class="ins-badges">${badges}</div></div>
      <div class="ins-actions">
        ${node.id !== WS.explorer?.rootId && node.type !== "MISSING" ? `<a class="btn sm primary" href="${wsHref({ c: cid, p: WS.current.pid, m: node.id })}">${wsIcon(WS_I.link)}Trace this instead</a>` : ""}
        <button class="btn sm ghost" data-close-ins title="Close">${wsIcon(WS_I.close)}</button>
      </div>
    </header>
    <div class="ins-body">${body}</div>`;
  view.querySelector(".ws-main")?.classList.add("inspecting");
  box.classList.remove("flash");
  void box.offsetWidth;
  box.classList.add("flash");
  setTimeout(() => WS.explorer?.fit(), 420);
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  $("[data-close-ins]", box).onclick = () => { box.hidden = true; view.querySelector(".ws-main")?.classList.remove("inspecting"); setTimeout(() => WS.explorer?.fit(), 420); };
  $$("[data-copy]", box).forEach((b) => (b.onclick = () => navigator.clipboard?.writeText(b.dataset.copy).then(() => toast("Copied", "ok"), () => toast("Couldn't copy", "bad"))));
  $$("[data-jump]", box).forEach((b) => (b.onclick = () => {
    const id = b.dataset.jump;
    const n = WS.explorer?.nodes.get(id);
    if (n) {
      Object.assign(WS.explorer, { sel: id, sel2: null });
      WS.explorer.applyHighlight();
      WS.explorer.centerOn(id);
      wsInspect(view, cid, ov, n);
    }
    else location.hash = wsHref({ c: cid, p: WS.current.pid, m: id });  // not on the canvas: trace it
  }));
}
