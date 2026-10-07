"use strict";

/* =====================================================================
   BI Trust Platform — single-page web app (no build step).
   Every screen talks to the same REST API documented at /docs.
   ===================================================================== */

/* ------------------------------------------------------------ utilities */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf2 = new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const nf0 = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
const fmt = {
  num: (v) => (v == null || Number.isNaN(v) ? "—" : nf2.format(v)),
  int: (v) => (v == null ? "—" : nf0.format(v)),
  signed: (v) => (v == null ? "—" : (v > 0.004 ? "+" : "") + nf2.format(v)),
  pct: (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`),
  // floor to 1 dp so 99.97% never reads as "100.0%" — only an exact 100 does
  rate: (v) => {
    const n = Number(v);
    if (v == null || Number.isNaN(n)) return "—";
    return `${(n === 100 ? 100 : n < 100 ? Math.min(99.9, Math.floor(n * 10 + 1e-9) / 10) : Math.floor(n * 10) / 10).toFixed(1)}%`;
  },
  date: (s) => (s ? new Date(s).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—"),
  bytes: (n) => (n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`),
  cell: (v, col = "") => {
    if (typeof v !== "number") return esc(v);
    if (Number.isInteger(v)) return /\b(year|yr|period|id|code|key|no|number)\b/i.test(String(col).replace(/_/g, " ")) ? esc(String(v)) : esc(nf0.format(v));
    return esc(nf2.format(v));
  },
};
const signClass = (v) => (v > 0.004 ? "pos" : v < -0.004 ? "neg" : "");
const q = (obj) => {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(obj)) if (v !== undefined && v !== null && v !== "") u.set(k, v);
  const s = u.toString();
  return s ? `?${s}` : "";
};
const debounce = (fn, ms) => {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
};
// first item's id unless the remembered one is still in the freshly loaded list
const pick = (items, id, key = "id") => (items.some((i) => i[key] === id) ? id : items[0]?.[key] ?? null);
const isAbort = (e) => e?.name === "AbortError";

/* ------------------------------------------------------------------ API */
// GETs belong to the current route: navigating aborts the previous route's requests.
let routeCtl = new AbortController();
async function api(method, path, body, signal) {
  const opts = { method, headers: {}, signal };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    let msg = data && data.detail ? data.detail : `${res.status} ${res.statusText}`;
    if (Array.isArray(msg)) msg = msg.map((d) => (d && d.msg ? `${(d.loc || []).slice(1).join(".")}: ${d.msg}` : typeof d === "string" ? d : JSON.stringify(d))).join("; ");
    else if (typeof msg === "object") msg = JSON.stringify(msg);
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return data;
}
const GET = (p) => api("GET", p, undefined, routeCtl.signal);
const POST = (p, b) => api("POST", p, b ?? {});
const PUT = (p, b) => api("PUT", p, b);
const PATCH = (p, b) => api("PATCH", p, b);
const DEL = (p) => api("DELETE", p);
async function uploadFiles(fileList) {
  const fd = new FormData();
  for (const f of fileList) fd.append("files", f);
  return api("POST", "/files", fd);
}
// lineage graphs fetched per connection; any re-sync / re-read makes them stale
const graphCache = {};
const graphGen = {}; // bumped by every sync, so a graph fetched before it is never cached
async function syncConnection(cid) {
  delete graphCache[cid];
  graphGen[cid] = (graphGen[cid] || 0) + 1;
  try { return await POST(`/connections/${encodeURIComponent(cid)}/sync`); } finally { delete graphCache[cid]; graphGen[cid] += 1; }
}

/* ------------------------------------------------------------ UI bits */
function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), kind === "bad" ? 8000 : 3500);
}
const loading = (text = "Loading…") => `<div class="loading"><span class="spinner"></span>${esc(text)}</div>`;
const badge = (text, kind = "neutral") => `<span class="badge ${kind}">${esc(text)}</span>`;
const CONF_KIND = { KNOWN: "ok", VERIFIED: "ok", PARTIALLY_VERIFIED: "warn", INFERRED: "info", UNKNOWN: "neutral", UNAVAILABLE: "bad", UNSUPPORTED: "bad" };
const CONF_TEXT = { KNOWN: "Known", VERIFIED: "Verified", PARTIALLY_VERIFIED: "Partially verified", INFERRED: "Inferred", UNKNOWN: "Unknown", UNAVAILABLE: "Unavailable", UNSUPPORTED: "Unsupported" };
const confBadge = (c) => badge(CONF_TEXT[c] || c || "Unknown", CONF_KIND[c] || "neutral");
const STATUS = { MATCH: ["Match", "ok"], MISMATCH: ["Value differs", "warn"], ONLY_LEFT: ["Only in A", "bad"], ONLY_RIGHT: ["Only in B", "info"] };
const statusBadge = (s) => badge(...(STATUS[s] || [s, "neutral"]));
const INV_KIND = { OPEN: "neutral", INVESTIGATING: "info", ROOT_CAUSE_FOUND: "ok", RESOLVED: "ok", INCONCLUSIVE: "warn" };
const INV_TEXT = { OPEN: "Open", INVESTIGATING: "Investigating", ROOT_CAUSE_FOUND: "Root cause found", RESOLVED: "Resolved", INCONCLUSIVE: "Inconclusive" };
const invBadge = (s) => badge(INV_TEXT[s] || s, INV_KIND[s] || "neutral");
const runBadge = (s) => (s === "MATCHED" ? badge("Matched", "ok") : badge("Differences found", "warn"));
const kpi = (label, value, hint = "", kind = "", href = "") => {
  const inner = `<div class="label">${esc(label)}</div><div class="value">${value}</div>${hint ? `<div class="hint">${hint}</div>` : ""}`;
  return href ? `<a class="kpi link ${kind}" href="${esc(href)}">${inner}</a>` : `<div class="kpi ${kind}">${inner}</div>`;
};
const alertBox = (kind, items) =>
  !items || !items.length ? "" : `<div class="alert ${kind}">${items.length === 1 ? esc(items[0]) : `<ul>${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>`}</div>`;
const ICONS = {
  upload: '<path d="M12 16V4M7 9l5-5 5 5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
  file: '<path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z"/><path d="M14 3v5h5"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
  chart: '<path d="M4 19V9M10 19V5M16 19v-7M22 19H2"/>',
  model: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><path d="M14 17.5h7M17.5 14v7"/>',
  check: '<path d="M5 12l5 5 9-10"/>',
};
const icon = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ""}</svg>`;
const emptyState = (title, text = "", action = "", ic = "search") =>
  `<div class="empty"><div class="empty-icon">${icon(ic)}</div><h3>${esc(title)}</h3>${text ? `<p>${text}</p>` : ""}${action}</div>`;

function busy(btn, on, label) {
  if (!btn) return;
  if (on) {
    btn.dataset.label = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span> ${esc(label || "Working…")}`;
  } else {
    btn.disabled = false;
    if (btn.dataset.label) btn.innerHTML = btn.dataset.label;
  }
}
async function act(btn, label, fn) {
  busy(btn, true, label);
  try {
    return await fn();
  } catch (e) {
    if (!isAbort(e)) toast(e.message, "bad");
    return undefined;
  } finally {
    busy(btn, false);
  }
}

const FOCUSABLE = 'a[href],button:not([disabled]),input:not([type=hidden]):not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
function modal({ title, body, confirm = "Save", cancel = "Cancel", onConfirm, danger = false, onOpen }) {
  const prevFocus = document.activeElement;
  const back = document.createElement("div");
  back.className = "modal-back";
  back.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-label="${esc(title)}">
    <div class="card-head"><h2>${esc(title)}</h2></div>
    <div class="card-body">${body}</div>
    <div class="modal-foot"><button class="btn" data-x>${esc(cancel)}</button>${confirm ? `<button class="btn ${danger ? "danger" : "primary"}" data-ok>${esc(confirm)}</button>` : ""}</div>
  </div>`;
  document.body.append(back);
  const onKey = (e) => {
    if ($$(".modal-back").pop() !== back) return; // only the top-most modal reacts
    if (e.key === "Escape") { e.preventDefault(); close(); return; }
    if (e.key !== "Tab") return;
    const items = $$(FOCUSABLE, back).filter((el) => el.offsetParent !== null);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (!back.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
    else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };
  const close = () => {
    document.removeEventListener("keydown", onKey);
    back.remove();
    if (prevFocus && prevFocus.isConnected && typeof prevFocus.focus === "function") prevFocus.focus();
  };
  back.close = close;
  document.addEventListener("keydown", onKey);
  back.addEventListener("click", (e) => { if (e.target === back || e.target.closest('a[href^="#"]')) close(); });
  $("[data-x]", back).onclick = close;
  const ok = $("[data-ok]", back);
  if (ok) {
    ok.onclick = async () => {
      busy(ok, true, "Working…");
      try {
        if ((await onConfirm?.(back)) !== false) close();
      } catch (e) {
        toast(e.message, "bad");
      } finally {
        busy(ok, false);
      }
    };
  }
  onOpen?.(back);
  ($("input:not([type=checkbox]):not([type=hidden]),select,textarea", back) || ok || $("[data-x]", back)).focus();
  return back;
}
function confirmDelete(what, onConfirm) {
  modal({ title: `Delete ${what}?`, body: `<p class="muted" style="margin:0">This can't be undone.</p>`, confirm: "Delete", danger: true, onConfirm });
}

function divergeBar(value, maxAbs) {
  const w = maxAbs > 0 ? Math.min(50, (Math.abs(value) / maxAbs) * 50) : 0;
  return `<div class="diverge"><div class="axis"></div><div class="fill ${value < 0 ? "neg" : "pos"}" style="width:${w}%"></div></div>`;
}

function previewTable(columns, rows, max = 8) {
  if (!columns.length) return `<div class="empty small">No columns found.</div>`;
  return `<div class="table-wrap"><table class="data"><thead><tr>${columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>
    <tbody>${rows.slice(0, max).map((r) => `<tr>${columns.map((c) => `<td class="${typeof r[c] === "number" ? "num" : ""}">${fmt.cell(r[c], c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

function dropzone(label = "Drop Excel or CSV files here, or <strong>browse</strong>", multiple = false) {
  return `<label class="dropzone" data-drop><div class="dz-icon">${icon("upload")}</div>${label}<div class="small faint" style="margin-top:4px">.xlsx · .xlsm · .csv · .tsv</div>
    <input type="file" hidden accept=".xlsx,.xlsm,.csv,.tsv,.txt" ${multiple ? "multiple" : ""}></label>`;
}
// browse + drag & drop for any <label class="dropzone"> holding a hidden file input
function wireDrop(zone, onFiles) {
  if (!zone) return;
  const input = $("input[type=file]", zone);
  input.onchange = () => input.files.length && onFiles(input.files);
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("drag"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("drag"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("drag");
    if (e.dataTransfer.files.length) onFiles(e.dataTransfer.files);
  });
}
const wireDropzone = (root, onFiles) => wireDrop($("[data-drop]", root), onFiles);

/* --------------------------------------------------------------- router */
const routes = [
  [/^\/?$/, "workspace", viewWorkspace],   // dashboards -> pages -> measures -> lineage (workspace.js)
  [/^\/overview$/, "home", viewHome],
  [/^\/models$/, "models", viewModels],
  [/^\/models\/([^/]+)$/, "models", viewModel],
  [/^\/reconcile$/, "reconcile", viewReconcile],
  [/^\/runs$/, "runs", viewRuns],
  [/^\/runs\/([^/]+)$/, "runs", viewRun],
  [/^\/templates$/, "templates", viewTemplates],
  [/^\/investigations$/, "investigations", viewInvestigations],
  [/^\/investigations\/([^/]+)$/, "investigations", viewInvestigation],
  [/^\/lineage$/, "lineage", viewLineage],
  [/^\/sources$/, "sources", viewSources],
];
const ROUTE_LABEL = {
  workspace: ["", "Dashboards"], home: ["", "Overview"], models: ["Understand", "Power BI models"], lineage: ["Understand", "Lineage & impact"],
  reconcile: ["Reconcile", "New reconciliation"], runs: ["Reconcile", "Results"], templates: ["Reconcile", "Templates"],
  investigations: ["Investigate", "Investigations"], sources: ["Setup", "Sources"],
};
function setCrumbs(name, detail) {
  const [section, label] = ROUTE_LABEL[name] || ["", ""];
  const parts = [section, label, detail].filter(Boolean);
  $("#crumbs").innerHTML = parts.map((p, i) => (i === parts.length - 1 ? `<b>${esc(p)}</b>` : `<span>${esc(p)}</span><span class="sep">/</span>`)).join("");
}
/* Each render gets a fresh #view element and a fresh AbortController: the
   previous route's GETs are aborted, and anything a slow earlier view still
   writes lands in its own detached element instead of over the newer page.
   `isLive(view)` is the explicit check for writes outside the view (crumbs…). */
let renderToken = 0;
let lastPath = null;
const isLive = (view) => view.isConnected && view.dataset.token === String(renderToken);
async function render() {
  const raw = location.hash.replace(/^#/, "") || "/";
  const [path, query] = raw.split("?");
  const params = new URLSearchParams(query || "");
  for (const [re, name, fn] of routes) {
    const m = path.match(re);
    if (!m) continue;
    routeCtl.abort();
    routeCtl = new AbortController();
    $$("#nav a, #tool-nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === name));
    if (typeof wsRouteChanged === "function") wsRouteChanged(name, path);
    setCrumbs(name);
    if (name !== "workspace" && typeof wsShowRightbar === "function") wsShowRightbar(false);
    const token = ++renderToken;
    const old = $("#view");
    const view = old.cloneNode(false);
    view.dataset.token = String(token);
    view.innerHTML = loading();
    old.replaceWith(view);
    if (lastPath !== path) window.scrollTo(0, 0);
    lastPath = path;
    try {
      await fn(view, ...m.slice(1).map(decodeURIComponent), params);
    } catch (e) {
      if (!isAbort(e) && token === renderToken) view.innerHTML = `<div class="card"><div class="card-body">${alertBox("bad", [e.message])}</div></div>`;
    }
    return;
  }
  location.hash = "#/";
}
window.addEventListener("hashchange", render);
window.addEventListener("unhandledrejection", (e) => { if (isAbort(e.reason)) e.preventDefault(); });

// rows that open a page: one delegated handler, reachable by keyboard (tabindex=0 + Enter)
const goRow = (hash) => `class="clickable" tabindex="0" data-go="${esc(hash)}"`;
const fromControl = (e) => e.target.closest("a,button,input,select,textarea,label,summary");
document.addEventListener("click", (e) => {
  const row = e.target.closest("[data-go]");
  if (row && !fromControl(e)) location.hash = row.dataset.go;
});
document.addEventListener("keydown", (e) => {
  const row = e.key === "Enter" && e.target.closest?.("[data-go]");
  if (row && row === e.target) { e.preventDefault(); location.hash = row.dataset.go; }
});

/* ================================================================ HOME */
async function viewHome(view) {
  const s = await GET("/stats");
  const started = s.reconciliations > 0;
  view.innerHTML = `
    <div class="page-head">
      <div><div class="eyebrow">Workspace</div><h1>Trust every number on the dashboard</h1>
        <p>Compare BI dashboards with Excel and with each other, see exactly why the numbers differ, and keep the evidence for the next time someone asks.</p></div>
      <div class="row"><a class="btn" href="#/models">Open a Power BI file</a><a class="btn primary" href="#/reconcile">New reconciliation</a></div>
    </div>
    <div class="card"><div class="card-head"><div><h2>Understand a Power BI report</h2><div class="sub">Lineage, relationships, measure explanations, data sources and a health check — from the file alone</div></div></div>
      <div class="card-body">${modelDropzone()}</div></div>
    <div class="kpis">
      ${kpi("Reconciliations run", fmt.int(s.reconciliations), `${fmt.int(s.reconciliations_with_differences)} with differences`)}
      ${kpi("Average match rate", fmt.rate(s.average_match_rate_pct), `${fmt.int(s.rows_compared)} keys compared`)}
      ${kpi("Open investigations", fmt.int(s.open_investigations), `${fmt.int(s.investigations)} in total`, s.open_investigations ? "warn" : "", "#/investigations?status=INVESTIGATING")}
      ${kpi("Saved templates", fmt.int(s.templates), "re-run in one click")}
      ${kpi("Sources", `${fmt.int(s.connections)}`, `connections · ${fmt.int(s.files)} files`)}
    </div>
    ${started ? `
    <div class="card"><div class="card-head"><h2>Recent reconciliations</h2><span class="spacer"></span><a href="#/runs" class="small">View all</a></div>
      <div class="card-body tight"><div class="table-wrap"><table class="data"><thead><tr><th>Name</th><th>Result</th><th class="num">Match rate</th><th class="num">Difference</th><th>Run</th></tr></thead>
      <tbody>${s.recent_runs.map((r) => `<tr ${goRow(`#/runs/${r.reconciliation_id}`)}><td>${esc(r.name)}</td><td>${runBadge(r.status)}</td>
        <td class="num">${fmt.rate(r.match_rate_pct)}</td><td class="num ${signClass(r.difference)}">${fmt.signed(r.difference)}</td><td class="muted">${fmt.date(r.created_at)}</td></tr>`).join("")}</tbody></table></div></div></div>` : `
    <div class="card"><div class="card-head"><h2>Get started in three steps</h2></div>
      <div class="card-body"><div class="grid-3">
        <div class="stack"><h3>1 · Add a source</h3><p class="muted small" style="margin:0">Connect Power BI or Tableau, open a Power BI project folder, or just upload what any BI tool exports (Excel/CSV).</p><a href="#/sources" class="small">Go to sources →</a></div>
        <div class="stack"><h3>2 · Pick what should agree</h3><p class="muted small" style="margin:0">Choose a dashboard visual or tab on one side and the finance Excel tab on the other. Headers, totals and formats are handled for you.</p></div>
        <div class="stack"><h3>3 · See why they differ</h3><p class="muted small" style="margin:0">Columns are matched automatically. Every difference is explained — missing rows, units, rounding, timing — and exportable to Excel.</p></div>
      </div></div>
    </div>`}`;
  wireModelDropzone(view);
}

/* ======================================================= RECONCILE WIZARD */
const freshWizard = () => ({
  left: null, right: null, config: null, suggestion: null, suggesting: null, name: "", nameEdited: false, advOpen: false,
  investigate: true, picker: { left: { tab: "files" }, right: { tab: "files" } },
});
const wiz = freshWizard();
const resetWizard = () => Object.assign(wiz, freshWizard());
const wizPair = () => (wiz.left && wiz.right ? `${wiz.left.dataset_id}|${wiz.right.dataset_id}` : null);
const SIDE_TEXT = {
  left: { letter: "A", title: "Source to check", hint: "Usually the BI dashboard visual or tab" },
  right: { letter: "B", title: "Reference", hint: "Usually the finance / source Excel" },
};

async function useDataset(side, summary) {
  const full = await GET(`/datasets/${encodeURIComponent(summary.dataset_id)}?limit=8`);
  wiz[side] = { ...summary, preview: full.rows };
  wiz.config = null;
  wiz.suggestion = null;
  renderSide(side);
  refreshWizard();
}

async function viewReconcile(view) {
  view.innerHTML = `
    <div class="page-head">
      <div><h1>New reconciliation</h1><p>Pick the two things that should agree. Columns are matched for you — review the mapping, then run.</p></div>
      <div class="row"><button class="btn ghost" id="reset">Start over</button></div>
    </div>
    <div class="steps" id="steps"></div>
    <div class="grid-2"><div class="card" id="side-left"></div><div class="card" id="side-right"></div></div>
    <div class="card" id="mapping"></div>
    <div class="card" id="runbox"></div>`;
  $("#reset").onclick = () => { resetWizard(); history.replaceState(null, "", "#/reconcile"); render(); };
  renderSide("left");
  renderSide("right");
  refreshWizard();
}

function refreshWizard() {
  const steps = $("#steps");
  if (!steps) return;
  const s1 = !!wiz.left, s2 = !!wiz.right, s3 = !!wiz.config;
  const pill = (n, text, done, current) => `<div class="step-pill ${done ? "done" : current ? "current" : ""}"><span class="n">${done ? "✓" : n}</span>${text}</div>`;
  steps.innerHTML = [
    pill(1, "Source A", s1, !s1),
    pill(2, "Source B", s2, s1 && !s2),
    pill(3, "Match columns", s3 && s1 && s2, s1 && s2 && !s3),
    pill(4, "Run", false, s1 && s2 && s3),
  ].join('<span class="step-sep">›</span>');
  renderMapping();
  renderRunBox();
}

function renderSide(side) {
  const card = $(`#side-${side}`);
  if (!card) return;
  const t = SIDE_TEXT[side];
  const head = `<div class="card-head"><span class="side-label ${side}">${t.letter}</span><div><h2>${t.title}</h2><div class="sub">${t.hint}</div></div></div>`;
  const ds = wiz[side];
  if (ds) {
    card.innerHTML = `${head}<div class="card-body dataset-card">
      <div class="row"><strong>${esc(ds.name)}</strong><span class="spacer"></span><button class="btn sm" data-change>Change</button></div>
      <div class="dataset-meta"><span>${fmt.int(ds.row_count)} rows</span><span>${ds.columns.length} columns</span>${ds.platform ? `<span>${esc(ds.platform)}</span>` : ""}${ds.header_row != null ? `<span>header on row ${ds.header_row + 1}</span>` : ""}</div>
      ${alertBox("warn", ds.warnings)}
      ${previewTable(ds.columns.map((c) => c.name), ds.preview || [])}
    </div>`;
    $("[data-change]", card).onclick = () => { wiz[side] = null; wiz.config = null; wiz.suggestion = null; renderSide(side); refreshWizard(); };
    return;
  }
  const tab = wiz.picker[side].tab;
  card.innerHTML = `${head}
    <div class="tabs">${[["files", "Excel / CSV file"], ["bi", "BI connection"], ["dataset", "Saved dataset"]]
      .map(([id, label]) => `<button class="tab ${tab === id ? "active" : ""}" data-tab="${id}">${label}</button>`).join("")}</div>
    <div class="card-body" data-body>${loading()}</div>`;
  $$("[data-tab]", card).forEach((b) => (b.onclick = () => { wiz.picker[side] = { tab: b.dataset.tab }; renderSide(side); }));
  const body = $("[data-body]", card);
  ({ files: pickerFiles, bi: pickerBI, dataset: pickerDataset })[tab](side, body).catch((e) => { if (!isAbort(e)) body.innerHTML = alertBox("bad", [e.message]); });
}

/* ---- file picker: upload or choose a file, pick the tab, confirm the header */
async function pickerFiles(side, body) {
  const st = wiz.picker[side];
  const files = (await GET("/files")).filter((f) => f.kind !== "model");
  body.innerHTML = `<div class="stack">
    ${dropzone()}
    <label class="field">Or choose an uploaded file
      <select data-file><option value="">Choose a file…</option>${files.map((f) => `<option value="${esc(f.file_id)}" ${st.file_id === f.file_id ? "selected" : ""}>${esc(f.name)}</option>`).join("")}</select></label>
    <div data-sheet></div></div>`;
  wireDropzone(body, async (list) => {
    body.querySelector("[data-drop]").innerHTML = loading("Uploading…");
    try {
      const [rec] = await uploadFiles([list[0]]);
      Object.assign(st, { file_id: rec.file_id, sheet: rec.sheets[0]?.name, header_row: null });
      toast(`Uploaded ${rec.name}`, "ok");
    } catch (e) { toast(e.message, "bad"); }
    renderSide(side);
  });
  $("[data-file]", body).onchange = (e) => { Object.assign(st, { file_id: e.target.value || null, sheet: null, header_row: null }); renderSide(side); };
  if (!st.file_id) return;
  const file = files.find((f) => f.file_id === st.file_id);
  if (!file) return;
  st.sheet = st.sheet || file.sheets[0]?.name;
  const area = $("[data-sheet]", body);
  area.innerHTML = loading("Reading tab…");
  const sheetSelect = `<label class="field">Tab<select data-sheet-select>${file.sheets.map((s) => `<option value="${esc(s.name)}" ${s.name === st.sheet ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select></label>`;
  const onSheet = (e) => { st.sheet = e.target.value; st.header_row = null; renderSide(side); };
  let p;
  try {
    p = await GET(`/files/${encodeURIComponent(st.file_id)}/preview${q({ sheet: st.sheet, header_row: st.header_row, limit: 5 })}`);
  } catch (e) {
    if (isAbort(e)) return;
    // keep the picker usable: the error belongs to this tab only
    area.innerHTML = `<div class="stack"><div class="form-grid">${sheetSelect}</div>${alertBox("bad", [e.message])}</div>`;
    $("[data-sheet-select]", area).onchange = onSheet;
    return;
  }
  area.innerHTML = `<div class="stack">
    <div class="form-grid">
      ${sheetSelect}
      <label class="field">Header row<select data-header>${p.raw.map((_, i) => `<option value="${i}" ${i === p.header_row ? "selected" : ""}>Row ${i + 1}${i === p.detected_header_row ? " (detected)" : ""}</option>`).join("")}</select></label>
    </div>
    <div class="small muted">Top of the tab — the highlighted row is used as column names. Click a row to change it.</div>
    <div class="table-wrap"><table class="data raw"><tbody>${p.raw.map((r, i) => `<tr class="${i === p.header_row ? "header-row" : ""} clickable" data-row="${i}"><td class="rownum">${i + 1}</td>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
    ${alertBox("info", p.warnings)}
    <div class="row"><span class="muted small">${fmt.int(p.row_count)} data rows · ${p.columns.length} columns</span><span class="spacer"></span><button class="btn primary" data-use>Use this tab</button></div>
  </div>`;
  $("[data-sheet-select]", area).onchange = onSheet;
  $("[data-header]", area).onchange = (e) => { st.header_row = Number(e.target.value); renderSide(side); };
  $$("[data-row]", area).forEach((tr) => (tr.onclick = () => { st.header_row = Number(tr.dataset.row); renderSide(side); }));
  $("[data-use]", area).onclick = (e) => act(e.currentTarget, "Loading…", async () => {
    const ds = await POST("/datasets", { kind: "file", file_id: st.file_id, sheet: st.sheet, header_row: p.header_row });
    await useDataset(side, ds);
  });
}

/* ---- filter values: shown comma-separated, with BLANK as "(Blank)" */
const showValue = (v) => (v === null || v === undefined ? "(Blank)" : String(v));
const parseValues = (text) => String(text || "").split(",").map((v) => v.trim()).filter(Boolean).map((v) => (/^\(blank\)$/i.test(v) ? null : v));

/* ---- BI picker: connection → workspace → report → page/tab → visual → filters */
async function pickerBI(side, body) {
  const st = wiz.picker[side];
  const conns = await GET("/connections");
  if (!conns.length) {
    body.innerHTML = `<div class="empty"><h3>No BI connections yet</h3><p>Connect Power BI, Tableau, a PBIP folder, or upload exports on the Sources page.</p>
      <div class="row" style="justify-content:center"><a class="btn primary" href="#/sources">Add a connection</a></div></div>`;
    return;
  }
  st.connection_id = pick(conns, st.connection_id);
  const opt = (items, sel, id = "id", label = "name") => items.map((i) => `<option value="${esc(i[id])}" ${i[id] === sel ? "selected" : ""}>${esc(i[label])}</option>`).join("");
  body.innerHTML = `<div class="stack"><div class="form-grid" data-selects>
      <label class="field">Connection<select data-k="connection_id">${opt(conns, st.connection_id)}</select></label>
    </div><div data-rest></div></div>`;
  const selects = $("[data-selects]", body);
  const rest = $("[data-rest]", body);
  const bind = (key, reset) => {
    const el = $(`[data-k=${key}]`, body);
    if (el) el.onchange = () => { st[key] = el.value; for (const r of reset) st[r] = null; renderSide(side); };
  };
  bind("connection_id", ["workspace_id", "report_id", "page_id", "visual_id", "filters"]);
  const c = encodeURIComponent(st.connection_id);
  try {
    const workspaces = await GET(`/connections/${c}/workspaces`);
    st.workspace_id = pick(workspaces, st.workspace_id);
    selects.insertAdjacentHTML("beforeend", `<label class="field">Workspace / project<select data-k="workspace_id">${opt(workspaces, st.workspace_id)}</select></label>`);
    bind("workspace_id", ["report_id", "page_id", "visual_id", "filters"]);
    if (!st.workspace_id) return;
    const reports = await GET(`/connections/${c}/reports${q({ workspace_id: st.workspace_id })}`);
    st.report_id = pick(reports, st.report_id);
    selects.insertAdjacentHTML("beforeend", `<label class="field">Report / workbook / file<select data-k="report_id">${opt(reports, st.report_id)}</select></label>`);
    bind("report_id", ["page_id", "visual_id", "filters"]);
    if (!st.report_id) return;
    const pages = await GET(`/connections/${c}/reports/${encodeURIComponent(st.report_id)}/pages`);
    st.page_id = pick(pages, st.page_id);
    selects.insertAdjacentHTML("beforeend", `<label class="field">Page / tab<select data-k="page_id">${opt(pages, st.page_id)}</select></label>`);
    bind("page_id", ["visual_id", "filters"]);
    if (!st.page_id) return;
    const visuals = await GET(`/connections/${c}/reports/${encodeURIComponent(st.report_id)}/pages/${encodeURIComponent(st.page_id)}/visuals`);
    const vid = pick(visuals, st.visual_id);
    if (vid !== st.visual_id) st.filters = null; // filters belonged to a visual that no longer exists
    st.visual_id = vid;
    selects.insertAdjacentHTML("beforeend", `<label class="field">Visual<select data-k="visual_id">${opt(visuals, st.visual_id, "id", "title")}</select></label>`);
    bind("visual_id", ["filters"]);
    const visual = visuals.find((v) => v.id === st.visual_id);
    if (!visual) return;
    if (!st.filters) st.filters = (visual.default_filters || []).map((f) => ({ field: f.field, operator: f.operator || "in", values: (f.values || []).map(showValue).join(", ") }));
    st.notes = visual.filter_notes || [];
    renderFilters();
  } catch (e) {
    if (!isAbort(e)) rest.innerHTML = alertBox("bad", [e.message]);
  }

  function renderFilters() {
    const ops = [["in", "is one of"], ["not_in", "is not"], [">", ">"], [">=", "≥"], ["<", "<"], ["<=", "≤"], ["between", "between"]];
    rest.innerHTML = `<div class="stack">
      <div class="row"><h3>Filters</h3><span class="muted small">Pre-filled with the filters set in the report — edit, add or remove. Empty values mean no filter.</span></div>
      ${st.notes.length ? `<div class="alert warn"><div><b>Report filters that can't be reproduced from the file</b> — the dashboard may show different numbers:<ul>${st.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul></div></div>` : ""}
      ${st.filters.map((f, i) => `<div class="filter-row" data-f="${i}"><input type="text" data-field placeholder="Table[Column]" value="${esc(f.field)}">
        <select data-op aria-label="Operator">${ops.map(([v, l]) => `<option value="${v}" ${v === f.operator ? "selected" : ""}>${l}</option>`).join("")}</select>
        <input type="text" data-values placeholder="${f.operator === "between" ? "from, to" : "value1, value2"}" value="${esc(f.values)}"><button class="btn ghost sm" data-rm aria-label="Remove filter">✕</button></div>`).join("")}
      <div class="row"><button class="btn sm" data-add>+ Add filter</button><span class="spacer"></span><button class="btn" data-preview>Preview</button><button class="btn primary" data-use>Use this data</button></div>
      <div data-out></div></div>`;
    $$("[data-f]", rest).forEach((row) => {
      const i = Number(row.dataset.f);
      $("[data-field]", row).oninput = (e) => (st.filters[i].field = e.target.value);
      $("[data-op]", row).onchange = (e) => (st.filters[i].operator = e.target.value);
      $("[data-values]", row).oninput = (e) => (st.filters[i].values = e.target.value);
      $("[data-rm]", row).onclick = () => { st.filters.splice(i, 1); renderFilters(); };
    });
    $("[data-add]", rest).onclick = () => { st.filters.push({ field: "", operator: "in", values: "" }); renderFilters(); };
    const payload = () => ({
      report_id: st.report_id, page_id: st.page_id, visual_id: st.visual_id,
      filters: st.filters.filter((f) => f.field.trim()).map((f) => ({ field: f.field.trim(), operator: f.operator || "in", values: parseValues(f.values) })),
    });
    $("[data-preview]", rest).onclick = (e) => act(e.currentTarget, "Loading…", async () => {
      const d = await POST(`/connections/${c}/visual-data`, { ...payload(), limit: 8 });
      $("[data-out]", rest).innerHTML = `<div class="stack">${alertBox("info", d.warnings)}<div class="small muted">${fmt.int(d.row_count)} rows</div>${previewTable(d.columns, d.rows)}</div>`;
    });
    $("[data-use]", rest).onclick = (e) => act(e.currentTarget, "Loading…", async () => {
      const ds = await POST("/datasets", { kind: "bi_visual", connection_id: st.connection_id, ...payload() });
      await useDataset(side, ds);
    });
  }
}

async function pickerDataset(side, body) {
  const list = await GET("/datasets");
  if (!list.length) {
    body.innerHTML = `<div class="empty">No saved datasets yet — every file tab or visual you load is saved here for reuse.</div>`;
    return;
  }
  body.innerHTML = `<div class="stack"><label class="field">Dataset<select data-ds>${list.map((d) => `<option value="${esc(d.dataset_id)}">${esc(d.name)} · ${fmt.int(d.row_count)} rows · ${fmt.date(d.created_at)}</option>`).join("")}</select></label>
    <div class="row"><span class="spacer"></span><button class="btn primary" data-use>Use this dataset</button></div></div>`;
  $("[data-use]", body).onclick = (e) => act(e.currentTarget, "Loading…", async () => {
    const id = $("[data-ds]", body).value;
    await useDataset(side, list.find((d) => d.dataset_id === id));
  });
}

/* ---- mapping editor */
async function renderMapping() {
  const card = $("#mapping");
  if (!card) return;
  if (!(wiz.left && wiz.right)) {
    card.innerHTML = `<div class="card-head"><h2>Match columns</h2><span class="sub">Choose both sides first — columns will be matched automatically.</span></div>`;
    return;
  }
  if (!wiz.config) {
    const pair = wizPair();
    card.innerHTML = `<div class="card-head"><h2>Match columns</h2></div><div class="card-body">${loading("Matching columns…")}</div>`;
    if (wiz.suggesting === pair) return; // already asked for this pair; its answer re-renders
    wiz.suggesting = pair;
    let suggestion, error;
    try {
      suggestion = await POST("/recon/suggest", { left_dataset_id: wiz.left.dataset_id, right_dataset_id: wiz.right.dataset_id });
    } catch (e) { error = e; }
    // a side was changed or cleared while we waited: this answer is for another pair
    if (wiz.suggesting === pair) wiz.suggesting = null;
    if (wizPair() !== pair || wiz.config) return;
    const target = $("#mapping");
    if (error) {
      if (target) target.innerHTML = `<div class="card-head"><h2>Match columns</h2></div><div class="card-body">${alertBox("bad", [error.message])}</div>`;
      return;
    }
    wiz.suggestion = suggestion;
    wiz.config = suggestion.config;
    refreshWizard();
    return;
  }
  const cfg = wiz.config;
  const L = wiz.left.columns.map((c) => c.name), R = wiz.right.columns.map((c) => c.name);
  const colSel = (cols, sel, side, kind, i) => `<select data-kind="${kind}" data-i="${i}" data-f="${side}">${cols.map((c) => `<option value="${esc(c)}" ${c === sel ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>`;
  const scoreOf = (kind, l, r) => {
    const list = (kind === "key" ? wiz.suggestion?.key_columns : wiz.suggestion?.value_columns) || [];
    const m = list.find((x) => x.left === l && x.right === r);
    return m ? `<span class="faint small" title="${esc(m.reason)}">${Math.round(m.score * 100)}% match</span>` : `<span class="faint small">manual</span>`;
  };
  const row = (kind, p, i) => `<tr>
    <td><select data-role="${kind}" data-i="${i}"><option value="key" ${kind === "key" ? "selected" : ""}>Key</option><option value="value" ${kind === "value" ? "selected" : ""}>Value</option></select></td>
    <td>${colSel(L, p.left, "left", kind, i)}</td><td>${colSel(R, p.right, "right", kind, i)}</td>
    <td>${kind === "value" ? `<input type="number" step="any" min="0" data-kind="value" data-i="${i}" data-f="tolerance_abs" value="${p.tolerance_abs}">` : '<span class="faint">—</span>'}</td>
    <td>${kind === "value" ? `<input type="number" step="any" min="0" data-kind="value" data-i="${i}" data-f="tolerance_pct" value="${p.tolerance_pct}">` : '<span class="faint">—</span>'}</td>
    <td>${scoreOf(kind, p.left, p.right)}</td>
    <td><button class="btn ghost sm" data-remove="${kind}" data-i="${i}" aria-label="Remove">✕</button></td></tr>`;
  const usedL = new Set([...cfg.key_columns, ...cfg.value_columns].map((p) => p.left));
  const usedR = new Set([...cfg.key_columns, ...cfg.value_columns].map((p) => p.right));
  const unL = L.filter((c) => !usedL.has(c)), unR = R.filter((c) => !usedR.has(c));
  const filterRows = (side) => {
    const cols = side === "left" ? L : R;
    const f = side === "left" ? cfg.left_filters : cfg.right_filters;
    return Object.entries(f).map(([col, vals]) => `<div class="row" data-filter="${side}" data-col="${esc(col)}"><select data-fcol aria-label="Column">${cols.map((c) => `<option value="${esc(c)}" ${c === col ? "selected" : ""}>${esc(c)}</option>`).join("")}</select><input type="text" data-fvals value="${esc(vals.join(", "))}" placeholder="values, comma separated" style="flex:1"><button class="btn ghost sm" data-frm aria-label="Remove filter">✕</button></div>`).join("");
  };
  card.innerHTML = `<div class="card-head"><h2>Match columns</h2><span class="sub">Keys identify a row (product, account, month…). Values are the numbers that should agree.</span></div>
    <div class="card-body stack">
      ${alertBox("info", wiz.suggestion?.warnings)}
      <div class="table-wrap"><table class="data map-table"><thead><tr><th style="width:110px">Role</th><th><span class="side-label left">A</span> ${esc(wiz.left.name)}</th><th><span class="side-label right">B</span> ${esc(wiz.right.name)}</th><th>Tolerance ±</th><th>Tolerance %</th><th></th><th></th></tr></thead>
      <tbody>${cfg.key_columns.map((p, i) => row("key", p, i)).join("")}${cfg.value_columns.map((p, i) => row("value", p, i)).join("")}</tbody></table></div>
      <div class="row"><button class="btn sm" data-add="key">+ Key column</button><button class="btn sm" data-add="value">+ Value column</button>
        <span class="spacer"></span><button class="btn ghost sm" data-resuggest>Re-suggest</button></div>
      ${unL.length || unR.length ? `<div class="small muted">Not compared — <b>A:</b> ${esc(unL.join(", ") || "none")} · <b>B:</b> ${esc(unR.join(", ") || "none")}</div>` : ""}
      <details class="adv" data-adv ${wiz.advOpen ? "open" : ""}><summary>Advanced options</summary><div class="stack">
        <div class="form-grid">
          <label class="field">Duplicate keys<select data-opt="aggregate"><option value="auto" ${cfg.aggregate === "auto" ? "selected" : ""}>Sum them (recommended)</option><option value="sum" ${cfg.aggregate === "sum" ? "selected" : ""}>Always sum</option><option value="first" ${cfg.aggregate === "first" ? "selected" : ""}>Keep first row</option></select></label>
          <label class="field">Multiply A values by<input type="number" step="any" data-opt="scale_left" value="${cfg.scale_left}"></label>
          <label class="field">Multiply B values by<input type="number" step="any" data-opt="scale_right" value="${cfg.scale_right}"></label>
        </div>
        <div class="row"><label class="check"><input type="checkbox" data-opt="case_insensitive_keys" ${cfg.case_insensitive_keys ? "checked" : ""}> Ignore case and extra spaces in keys</label>
          <label class="check"><input type="checkbox" data-opt="treat_blank_as_zero" ${cfg.treat_blank_as_zero ? "checked" : ""}> Treat blank values as zero</label></div>
        <div class="grid-2">
          <div class="stack"><div class="row"><h3>Only include rows from A where…</h3></div>${filterRows("left")}<div><button class="btn sm" data-fadd="left">+ Filter A</button></div></div>
          <div class="stack"><div class="row"><h3>Only include rows from B where…</h3></div>${filterRows("right")}<div><button class="btn sm" data-fadd="right">+ Filter B</button></div></div>
        </div>
      </div></details>
    </div>`;

  const listOf = (kind) => (kind === "key" ? cfg.key_columns : cfg.value_columns);
  $("[data-adv]", card).addEventListener("toggle", (e) => (wiz.advOpen = e.currentTarget.open));
  $$("select[data-f], input[data-f]", card).forEach((el) => (el.onchange = () => {
    const item = listOf(el.dataset.kind)[Number(el.dataset.i)];
    item[el.dataset.f] = el.type === "number" ? Number(el.value || 0) : el.value;
    renderMapping();
  }));
  $$("select[data-role]", card).forEach((el) => (el.onchange = () => {
    const from = el.dataset.role, to = el.value;
    const [item] = listOf(from).splice(Number(el.dataset.i), 1);
    listOf(to).push(to === "value" ? { left: item.left, right: item.right, tolerance_abs: 0.01, tolerance_pct: 0 } : { left: item.left, right: item.right });
    renderMapping();
  }));
  $$("[data-remove]", card).forEach((b) => (b.onclick = () => { listOf(b.dataset.remove).splice(Number(b.dataset.i), 1); refreshWizard(); }));
  $$("[data-add]", card).forEach((b) => (b.onclick = () => {
    const l = unL[0] || L[0], r = unR[0] || R[0];
    listOf(b.dataset.add).push(b.dataset.add === "value" ? { left: l, right: r, tolerance_abs: 0.01, tolerance_pct: 0 } : { left: l, right: r });
    renderMapping();
  }));
  $("[data-resuggest]", card).onclick = () => { wiz.config = null; refreshWizard(); };
  $$("[data-opt]", card).forEach((el) => (el.onchange = () => {
    cfg[el.dataset.opt] = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value || 1) : el.value;
  }));
  $$("[data-fadd]", card).forEach((b) => (b.onclick = () => {
    const side = b.dataset.fadd, cols = side === "left" ? L : R, f = side === "left" ? cfg.left_filters : cfg.right_filters;
    const col = cols.find((c) => !(c in f));
    if (col) f[col] = [];
    renderMapping();
  }));
  $$("[data-filter]", card).forEach((rowEl) => {
    const f = rowEl.dataset.filter === "left" ? cfg.left_filters : cfg.right_filters;
    const col = rowEl.dataset.col;
    $("[data-fcol]", rowEl).onchange = (e) => { const v = f[col]; delete f[col]; f[e.target.value] = v; renderMapping(); };
    $("[data-fvals]", rowEl).onchange = (e) => (f[col] = e.target.value.split(",").map((v) => v.trim()).filter(Boolean));
    $("[data-frm]", rowEl).onclick = () => { delete f[col]; renderMapping(); };
  });
}

function renderRunBox() {
  const card = $("#runbox");
  if (!card) return;
  if (!(wiz.left && wiz.right && wiz.config)) { card.classList.add("hidden"); return; }
  card.classList.remove("hidden");
  if (!wiz.nameEdited) wiz.name = `${wiz.left.name} vs ${wiz.right.name}`;
  card.innerHTML = `<div class="card-body"><div class="form-grid" style="align-items:end">
      <label class="field" style="grid-column: span 2">Name<input type="text" data-name value="${esc(wiz.name)}"></label>
      <label class="check"><input type="checkbox" data-inv ${wiz.investigate ? "checked" : ""}> Open an investigation with the likely causes</label>
      <div class="row" style="justify-content:flex-end"><button class="btn primary" data-run>Run reconciliation</button></div>
    </div></div>`;
  // once edited, the name sticks; clearing it brings back the automatic "A vs B"
  $("[data-name]", card).oninput = (e) => { wiz.name = e.target.value; wiz.nameEdited = !!e.target.value.trim(); };
  $("[data-inv]", card).onchange = (e) => (wiz.investigate = e.target.checked);
  // the backend reads an empty value list as "keep nothing", so drop filters with no values
  const keepFilled = (f) => Object.fromEntries(Object.entries(f || {}).filter(([, vals]) => vals && vals.length));
  $("[data-run]", card).onclick = (e) => act(e.currentTarget, "Comparing…", async () => {
    if (!wiz.config.value_columns.length) throw new Error("Add at least one value column to compare.");
    const config = { ...wiz.config, left_filters: keepFilled(wiz.config.left_filters), right_filters: keepFilled(wiz.config.right_filters) };
    const res = await POST("/recon/run?limit=1", {
      left_dataset_id: wiz.left.dataset_id, right_dataset_id: wiz.right.dataset_id,
      config, name: wiz.name.trim() || `${wiz.left.name} vs ${wiz.right.name}`, create_investigation: wiz.investigate,
    });
    location.hash = `#/runs/${res.reconciliation.reconciliation_id}`;
  });
}

/* ============================================================ RESULTS */
async function viewRuns(view, params) {
  const template = params.get("template");
  const [runs, templates] = await Promise.all([
    GET(`/recon${q({ definition_id: template })}`),
    template ? GET("/recon-templates").catch((e) => { if (isAbort(e)) throw e; return []; }) : [],
  ]);
  const tplName = template ? templates.find((t) => t.definition_id === template)?.name || template : "";
  view.innerHTML = `
    <div class="page-head"><div><h1>Results</h1><p>Every reconciliation you've run${template ? ` for template <b>${esc(tplName)}</b>` : ""}, newest first.</p></div>
      <a class="btn primary" href="#/reconcile">New reconciliation</a></div>
    <div class="card"><div class="card-body tight">${runs.length ? `<div class="table-wrap"><table class="data"><thead><tr>
      <th>Name</th><th>Result</th><th class="num">Match rate</th><th class="num">Keys</th><th class="num">Differences</th><th class="num">Total difference</th><th>Confidence</th><th>Run</th></tr></thead><tbody>
      ${runs.map((r) => `<tr ${goRow(`#/runs/${r.reconciliation_id}`)}><td>${esc(r.name)}<div class="faint small">${esc(r.left_name)} vs ${esc(r.right_name)}</div></td>
        <td>${runBadge(r.status)}</td><td class="num">${fmt.rate(r.match_rate_pct)}</td><td class="num">${fmt.int(r.total_keys)}</td><td class="num">${fmt.int(r.differences)}</td>
        <td class="num ${signClass(r.top_difference)}">${fmt.signed(r.top_difference)}</td><td>${confBadge(r.confidence)}</td><td class="muted small">${fmt.date(r.created_at)}</td></tr>`).join("")}
      </tbody></table></div>` : emptyState("No reconciliations yet", "Run your first one to see results here.", `<a class="btn primary" href="#/reconcile">New reconciliation</a>`, "chart")}</div></div>`;
}

function causeCards(hypotheses) {
  if (!hypotheses.length) return `<div class="empty small">No specific cause identified.</div>`;
  return hypotheses.map((h, i) => `<div class="cause ${i === 0 ? "top" : ""}">
      <div class="cause-head"><span class="cause-rank">${i + 1}</span><span class="cause-title">${esc(h.title || h.description)}</span>
        ${confBadge(h.confidence)}${h.affected_amount != null ? `<span class="num small ${signClass(h.affected_amount)}">${fmt.signed(h.affected_amount)}</span>` : ""}</div>
      <div class="row small muted" style="gap:8px"><span>Likelihood</span><div class="meter" style="width:140px"><span style="width:${Math.round((h.score || 0) * 100)}%"></span></div><span>${Math.round((h.score || 0) * 100)}%</span></div>
      ${h.title && h.description ? `<div class="small">${esc(h.description)}</div>` : ""}
      ${h.facts?.length ? `<ul>${h.facts.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}
      ${h.suggested_action ? `<div class="action"><b>Next step:</b> ${esc(h.suggested_action)}</div>` : ""}
    </div>`).join("");
}

async function viewRun(view, id) {
  const state = { status: "", search: "", offset: 0, limit: 100 };
  const first = await GET(`/recon/${encodeURIComponent(id)}?limit=${state.limit}`);
  const rec = first.reconciliation;
  const s = rec.summary;
  const A = esc(rec.left_name), B = esc(rec.right_name);
  const keyLabels = rec.config.key_columns.length ? rec.config.key_columns.map((p) => p.label || (p.left === p.right ? p.left : `${p.left} / ${p.right}`)) : ["Scope"];
  const valueLabels = rec.config.value_columns.map((p) => p.label || (p.left === p.right ? p.left : `${p.left} / ${p.right}`));

  view.innerHTML = `
    <div class="page-head">
      <div><div class="row">${runBadge(rec.status)}${confBadge(rec.confidence)}<span class="faint small">${esc(rec.reconciliation_id)} · ${fmt.date(rec.created_at)}</span></div>
        <h1 style="margin-top:6px">${esc(rec.name)}</h1>
        <p><span class="side-label left">A</span> ${A} &nbsp;vs&nbsp; <span class="side-label right">B</span> ${B}</p></div>
      <div class="row">
        <a class="btn" href="/recon/${encodeURIComponent(id)}/export">Download Excel</a>
        ${rec.definition_id ? `<button class="btn" data-rerun>Run again</button>` : `<button class="btn" data-template>Save as template</button>`}
        ${rec.investigation_id ? `<a class="btn primary" href="#/investigations/${esc(rec.investigation_id)}">Open investigation</a>` : `<button class="btn primary" data-investigate>Investigate</button>`}
      </div>
    </div>
    <div class="kpis">
      ${kpi("Match rate", fmt.rate(s.match_rate_pct), `${fmt.int(s.total_keys)} keys compared`, s.match_rate_pct >= 99.99 ? "ok" : s.match_rate_pct < 90 ? "bad" : "warn")}
      ${kpi("Matched", fmt.int(s.matched), "within tolerance", "ok")}
      ${kpi("Values differ", fmt.int(s.mismatched), "same key, different value", s.mismatched ? "warn" : "")}
      ${kpi(`Only in A`, fmt.int(s.only_left), `missing from ${B}`, s.only_left ? "bad" : "")}
      ${kpi(`Only in B`, fmt.int(s.only_right), `missing from ${A}`, s.only_right ? "bad" : "")}
    </div>
    ${alertBox("warn", rec.warnings)}
    ${rec.value_summaries.map((vs) => {
      const b = vs.bridge;
      const parts = [[`Rows only in A (missing from ${rec.right_name})`, b.missing_from_right], [`Rows only in B (missing from ${rec.left_name})`, b.missing_from_left], ["Different values on matched rows", b.value_differences]];
      const max = Math.max(...parts.map((p) => Math.abs(p[1])), 1e-9);
      return `<div class="card"><div class="card-head"><h2>${esc(vs.label)}</h2><span class="sub">${fmt.int(vs.mismatched_rows)} value difference(s)</span></div>
        <div class="card-body stack">
          <div class="totals">
            <div class="t"><div class="l">Total A · ${A}</div><div class="v">${fmt.num(vs.total_left)}</div></div>
            <div class="t"><div class="l">Total B · ${B}</div><div class="v">${fmt.num(vs.total_right)}</div></div>
            <div class="t"><div class="l">Difference (B − A)</div><div class="v ${signClass(vs.difference)}">${fmt.signed(vs.difference)}</div><div class="small muted">${fmt.pct(vs.difference_pct)}</div></div>
          </div>
          <div><h3 style="margin-bottom:6px">Where the difference comes from</h3>
            ${parts.map(([label, v]) => `<div class="bar-row"><span class="label" title="${esc(label)}">${esc(label)}</span>${divergeBar(v, max)}<span class="num ${signClass(v)}">${fmt.signed(v)}</span></div>`).join("")}
            <div class="bar-row" style="border-top:1px solid var(--border);margin-top:4px;padding-top:8px"><b class="label">Total difference</b><span></span><b class="num ${signClass(b.total)}">${fmt.signed(b.total)}</b></div>
          </div>
        </div></div>`;
    }).join("")}
    <div class="grid-2">
      <div class="card"><div class="card-head"><h2>Likely causes</h2><span class="sub">Ranked by how well the data supports them</span></div><div class="card-body stack">${causeCards(rec.hypotheses)}</div></div>
      <div class="card"><div class="card-head"><h2>What's driving it</h2><span class="sub">Share of the difference by key</span></div><div class="card-body stack">
        ${rec.drivers.length ? rec.drivers.map((d) => {
          const max = Math.max(...d.members.map((m) => Math.abs(m.difference)), 1e-9);
          return `<div><h3 style="margin-bottom:4px">By ${esc(d.dimension)} <span class="faint small">· ${esc(d.value_label)}</span></h3>
            ${d.members.map((m) => `<div class="bar-row"><span class="label" title="${esc(m.member)}">${esc(m.member)}</span>${divergeBar(m.difference, max)}<span class="num ${signClass(m.difference)}">${fmt.signed(m.difference)}</span></div>`).join("")}</div>`;
        }).join("") : `<div class="empty small">${rec.status === "MATCHED" ? "Nothing to explain — everything matches." : "No key columns, so there's no breakdown."}</div>`}
      </div></div>
    </div>
    <div class="card" id="rows-card">
      <div class="card-head"><h2>Rows</h2><span class="spacer"></span>
        <div class="chips" data-chips>
          ${[["", "All", s.total_keys], ["MISMATCH,ONLY_LEFT,ONLY_RIGHT", "Differences", s.mismatched + s.only_left + s.only_right], ["MISMATCH", "Value differs", s.mismatched], ["ONLY_LEFT", "Only in A", s.only_left], ["ONLY_RIGHT", "Only in B", s.only_right], ["MATCH", "Matched", s.matched]]
            .map(([v, l, n]) => `<button class="chip ${v === state.status ? "active" : ""}" data-status="${v}">${l}<span class="count">${fmt.int(n)}</span></button>`).join("")}
        </div>
        <input type="search" placeholder="Search keys…" data-search style="width:200px">
      </div>
      <div class="card-body tight" data-rows></div>
    </div>`;

  const rowsBody = $("[data-rows]", view);
  function drawRows(page) {
    const rows = page.reconciliation.rows;
    if (!rows.length) { rowsBody.innerHTML = `<div class="empty small">No rows match this filter.</div>`; return; }
    rowsBody.innerHTML = `<div class="table-wrap"><table class="data"><thead><tr><th>Status</th>${keyLabels.map((k) => `<th>${esc(k)}</th>`).join("")}
      ${valueLabels.map((v) => `<th class="num">${esc(v)} · A</th><th class="num">${esc(v)} · B</th><th class="num">Difference</th>`).join("")}</tr></thead><tbody>
      ${rows.map((r) => `<tr><td>${statusBadge(r.status)}${r.left_rows > 1 || r.right_rows > 1 ? `<div class="faint small">${r.left_rows}↔${r.right_rows} rows</div>` : ""}</td>
        ${keyLabels.map((k) => `<td>${esc(r.keys[k])}</td>`).join("")}
        ${r.cells.map((c) => `<td class="num">${fmt.num(c.left)}</td><td class="num">${fmt.num(c.right)}</td><td class="num ${c.within_tolerance ? "" : signClass(c.difference)}">${c.within_tolerance && r.status === "MATCH" ? '<span class="faint">—</span>' : fmt.signed(c.difference)}</td>`).join("")}</tr>`).join("")}
      </tbody></table></div>
      <div class="pager"><span>${fmt.int(page.offset + 1)}–${fmt.int(page.offset + rows.length)} of ${fmt.int(page.row_total)}</span>
        <button class="btn sm" data-prev ${page.offset === 0 ? "disabled" : ""}>Previous</button><button class="btn sm" data-next ${page.offset + rows.length >= page.row_total ? "disabled" : ""}>Next</button></div>`;
    $("[data-prev]", rowsBody).onclick = () => { state.offset = Math.max(0, state.offset - state.limit); load(); };
    $("[data-next]", rowsBody).onclick = () => { state.offset += state.limit; load(); };
  }
  let seq = 0; // only the latest request may draw (chips / search / paging can overlap)
  async function load() {
    const mine = ++seq;
    rowsBody.innerHTML = loading();
    try {
      const page = await GET(`/recon/${encodeURIComponent(id)}${q({ ...state })}`);
      if (mine === seq) drawRows(page);
    } catch (e) {
      if (mine === seq && !isAbort(e)) rowsBody.innerHTML = alertBox("bad", [e.message]);
    }
  }
  drawRows(first);
  $$("[data-status]", view).forEach((chip) => (chip.onclick = () => {
    state.status = chip.dataset.status;
    state.offset = 0;
    $$("[data-status]", view).forEach((c) => c.classList.toggle("active", c === chip));
    load();
  }));
  $("[data-search]", view).oninput = debounce((e) => { state.search = e.target.value; state.offset = 0; load(); }, 250);

  const inv = $("[data-investigate]", view);
  if (inv) inv.onclick = (e) => act(e.currentTarget, "Creating…", async () => {
    const created = await POST(`/recon/${encodeURIComponent(id)}/investigate`, {});
    location.hash = `#/investigations/${created.investigation_id}`;
  });
  const tpl = $("[data-template]", view);
  if (tpl) tpl.onclick = () => modal({
    title: "Save as template",
    body: `<p class="muted small" style="margin:0">Saves both sources and this column mapping. Next period, re-run it in one click — or with newly uploaded versions of the files.</p>
      <label class="field">Template name<input type="text" data-tname value="${esc(rec.name)}"></label>
      <label class="field">Description<textarea data-tdesc placeholder="e.g. Month-end check: stock value in Power BI vs the ERP ledger"></textarea></label>
      <label class="check"><input type="checkbox" data-tinv> Open an investigation automatically when differences are found</label>`,
    onConfirm: async (m) => {
      const d = await POST(`/recon/${encodeURIComponent(id)}/save-template`, { name: $("[data-tname]", m).value, description: $("[data-tdesc]", m).value, create_investigation: $("[data-tinv]", m).checked });
      toast(`Template "${d.name}" saved`, "ok");
      render();
    },
  });
  const rerun = $("[data-rerun]", view);
  if (rerun) rerun.onclick = (e) => act(e.currentTarget, "Running…", async () => {
    const res = await POST(`/recon-templates/${encodeURIComponent(rec.definition_id)}/run?limit=1`, {});
    location.hash = `#/runs/${res.reconciliation.reconciliation_id}`;
  });
}

/* ========================================================== TEMPLATES */
async function viewTemplates(view) {
  const [templates, files, runs] = await Promise.all([GET("/recon-templates"), GET("/files"), GET("/recon")]);
  const fileName = Object.fromEntries(files.map((f) => [f.file_id, f.name]));
  const runById = Object.fromEntries(runs.map((r) => [r.reconciliation_id, r]));
  const describe = (spec) => {
    if (spec.kind === "file") return `File · ${esc(fileName[spec.file_id] || spec.file_id)}${spec.sheet ? ` › ${esc(spec.sheet)}` : ""}`;
    if (spec.kind === "bi_visual") {
      const f = (spec.filters || []).filter((x) => x.values?.length).map((x) => `${x.field}=${x.values.join("/")}`).join(", ");
      return `BI visual · ${esc(spec.visual_id)} on ${esc(spec.page_id)} (${esc(spec.connection_id)})${f ? ` · ${esc(f)}` : ""}`;
    }
    if (spec.kind === "bi_query") return `Query on ${esc(spec.model_id)} (${esc(spec.connection_id)})`;
    return `Saved dataset · ${esc(spec.dataset_id)}`;
  };
  view.innerHTML = `
    <div class="page-head"><div><h1>Templates</h1><p>Saved reconciliations you repeat — month-end checks, weekly refresh checks. Each run re-extracts both sides fresh and compares them with the saved mapping.</p></div></div>
    ${templates.length ? templates.map((t) => {
      const last = runById[t.run_ids[t.run_ids.length - 1]];
      const hasFile = t.left.kind === "file" || t.right.kind === "file";
      return `<div class="card"><div class="card-head"><div><h2>${esc(t.name)}</h2>${t.description ? `<div class="sub">${esc(t.description)}</div>` : ""}</div><span class="spacer"></span>
          ${last ? `${runBadge(last.status)} <span class="small muted">last run ${fmt.date(last.created_at)} · ${fmt.rate(last.match_rate_pct)} match</span>` : ""}</div>
        <div class="card-body stack">
          <div class="small"><span class="side-label left">A</span> ${describe(t.left)}</div>
          <div class="small"><span class="side-label right">B</span> ${describe(t.right)}</div>
          <div class="small muted">${t.config.key_columns.length} key column(s), ${t.config.value_columns.length} value column(s) · ${t.run_ids.length} run(s)</div>
          <div class="row"><button class="btn primary" data-run="${esc(t.definition_id)}">Run now</button>
            ${hasFile ? `<button class="btn" data-runfiles="${esc(t.definition_id)}">Run with updated files…</button>` : ""}
            <a class="btn ghost" href="#/runs?template=${esc(t.definition_id)}">History</a><span class="spacer"></span>
            <button class="btn ghost danger" data-del="${esc(t.definition_id)}">Delete</button></div>
        </div></div>`;
    }).join("") : `<div class="card"><div class="empty"><h3>No templates yet</h3><p>Run a reconciliation, then choose "Save as template" on its result page.</p><a class="btn primary" href="#/reconcile">New reconciliation</a></div></div>`}`;

  const go = (res) => (location.hash = `#/runs/${res.reconciliation.reconciliation_id}`);
  $$("[data-run]", view).forEach((b) => (b.onclick = (e) => act(e.currentTarget, "Running…", async () => go(await POST(`/recon-templates/${encodeURIComponent(b.dataset.run)}/run?limit=1`, {})))));
  $$("[data-del]", view).forEach((b) => (b.onclick = () => confirmDelete("this template", async () => { await DEL(`/recon-templates/${encodeURIComponent(b.dataset.del)}`); render(); })));
  $$("[data-runfiles]", view).forEach((b) => (b.onclick = () => {
    const t = templates.find((x) => x.definition_id === b.dataset.runfiles);
    const input = (side, spec) => spec.kind !== "file" ? "" : `<label class="field"><span><span class="side-label ${side}">${side === "left" ? "A" : "B"}</span> New version of ${esc(fileName[spec.file_id] || "file")} (optional)</span><input type="file" data-file="${side}" accept=".xlsx,.xlsm,.csv,.tsv,.txt"></label>`;
    modal({
      title: `Run "${t.name}" with updated files`,
      body: `${input("left", t.left)}${input("right", t.right)}<div class="small muted">The same tab and column mapping are used. Leave a file empty to reuse the previous version.</div>`,
      confirm: "Upload & run",
      onConfirm: async (m) => {
        const body = {};
        for (const side of ["left", "right"]) {
          const el = $(`[data-file=${side}]`, m);
          if (el && el.files.length) body[`${side}_file_id`] = (await uploadFiles([el.files[0]]))[0].file_id;
        }
        go(await POST(`/recon-templates/${encodeURIComponent(t.definition_id)}/run?limit=1`, body));
      },
    });
  }));
}

/* ====================================================== INVESTIGATIONS */
async function viewInvestigations(view, params) {
  const status = params.get("status") || "";
  const list = await GET(`/investigations${q({ status })}`);
  view.innerHTML = `
    <div class="page-head"><div><h1>Investigations</h1><p>Each case keeps the question, evidence, ranked causes and findings — so the same problem is never investigated from scratch twice.</p></div></div>
    <div class="chips">${[["", "All"], ["OPEN", "Open"], ["INVESTIGATING", "Investigating"], ["ROOT_CAUSE_FOUND", "Root cause found"], ["RESOLVED", "Resolved"], ["INCONCLUSIVE", "Inconclusive"]]
      .map(([v, l]) => `<a class="chip ${v === status ? "active" : ""}" href="#/investigations${v ? `?status=${v}` : ""}">${l}</a>`).join("")}</div>
    <div class="card"><div class="card-body tight">${list.length ? `<div class="table-wrap"><table class="data"><thead><tr><th>Question</th><th>Status</th><th>Confidence</th><th>Top likely cause</th><th class="num">Evidence</th><th>Updated</th></tr></thead><tbody>
      ${list.map((i) => `<tr ${goRow(`#/investigations/${i.investigation_id}`)}><td>${esc(i.question)}<div class="faint small">${esc(i.investigation_id)}${i.owner ? ` · ${esc(i.owner)}` : ""}</div></td>
        <td>${invBadge(i.status)}</td><td>${confBadge(i.confidence)}</td><td class="small">${esc(i.top_hypothesis || "—")}</td><td class="num">${i.evidence_count}</td><td class="muted small">${fmt.date(i.updated_at)}</td></tr>`).join("")}
      </tbody></table></div>` : `<div class="empty"><h3>No investigations${status ? " with this status" : ""}</h3><p>Investigations are created from a reconciliation result, or automatically when you tick the option before running.</p></div>`}</div></div>`;
}

async function viewInvestigation(view, id) {
  const inv = await GET(`/investigations/${encodeURIComponent(id)}`);
  const canExport = inv.data_reconciliation_ids.length || (inv.reconciliation_id && inv.snapshot_ids.length >= 2);
  view.innerHTML = `
    <div class="page-head">
      <div><div class="row">${invBadge(inv.status)}${confBadge(inv.confidence)}<span class="faint small">${esc(inv.investigation_id)} · opened ${fmt.date(inv.created_at)}</span></div>
        <h1 style="margin-top:6px">${esc(inv.question)}</h1>
        <p>Overall confidence is only as strong as the weakest finding — the platform never claims more certainty than the evidence supports.</p></div>
      <div class="row">
        <select data-status aria-label="Status">${Object.keys(INV_TEXT).map((s) => `<option value="${s}" ${s === inv.status ? "selected" : ""}>${INV_TEXT[s]}</option>`).join("")}</select>
        ${canExport ? `<a class="btn" href="/exports/investigations/${encodeURIComponent(id)}.xlsx">Download workbook</a>` : ""}
        <button class="btn ghost danger" data-del>Delete</button>
      </div>
    </div>
    <div class="grid-2">
      <div class="stack">
        <div class="card"><div class="card-head"><h2>Likely causes</h2><span class="sub">${inv.hypotheses.length} ranked</span></div><div class="card-body stack">${causeCards(inv.hypotheses)}</div></div>
        <div class="card"><div class="card-head"><h2>Findings</h2></div><div class="card-body tight"><div class="table-wrap"><table class="data"><tbody>
          ${inv.findings.map((f) => `<tr><td>${esc(f.summary)}<div class="faint small">${esc(f.finding_id)} · evidence ${esc(f.evidence_ids.join(", "))}</div></td><td>${confBadge(f.confidence)}</td></tr>`).join("") || `<tr><td class="muted">No findings yet.</td></tr>`}
        </tbody></table></div></div></div>
        ${inv.lineage ? `<div class="card"><div class="card-head"><h2>Lineage</h2><span class="sub">coverage ${inv.lineage.coverage_pct}%</span><span class="spacer"></span>${confBadge(inv.lineage.overall_confidence)}</div><div class="card-body stack small">
          ${inv.lineage.steps.map((s) => `<div>${esc(s.from_name)} <span class="faint">— ${esc(s.edge_type.toLowerCase().replace("_", " "))} →</span> ${esc(s.to_name)}</div>`).join("")}
          ${alertBox("warn", inv.lineage.gaps)}</div></div>` : ""}
        ${inv.impact ? `<div class="card"><div class="card-head"><h2>Impact</h2><span class="spacer"></span>${badge(`${inv.impact.risk} risk`, inv.impact.risk === "HIGH" ? "bad" : inv.impact.risk === "MEDIUM" ? "warn" : "ok")}</div><div class="card-body small">${esc(inv.impact.explanation)}
          ${inv.impact.affected?.length ? `<ul>${inv.impact.affected.map((a) => `<li>${esc(a.type.toLowerCase())}: ${esc(a.name)}</li>`).join("")}</ul>` : ""}</div></div>` : ""}
      </div>
      <div class="stack">
        <div class="card"><div class="card-head"><h2>Linked work</h2></div><div class="card-body stack small">
          ${inv.data_reconciliation_ids.map((r) => `<a href="#/runs/${esc(r)}">Reconciliation ${esc(r)} →</a>`).join("") || '<span class="muted">No reconciliations linked.</span>'}
          ${inv.connection_id ? `<div class="row"><span class="muted">Connection ${esc(inv.connection_id)}</span><span class="spacer"></span><button class="btn sm" data-graph="lineage">Add lineage</button><button class="btn sm" data-graph="impact">Add impact</button></div>` : ""}
        </div></div>
        <div class="card"><div class="card-head"><h2>Notes</h2></div><div class="card-body stack">
          <div class="timeline">${inv.notes.map((n) => `<div class="item"><div>${esc(n.text)}</div><div class="meta">${esc(n.author)} · ${fmt.date(n.created_at)}</div></div>`).join("") || '<span class="muted small">No notes yet — record what you checked and what you found.</span>'}</div>
          <textarea data-note placeholder="Add a note…"></textarea><div class="row"><span class="spacer"></span><button class="btn" data-addnote>Add note</button></div>
        </div></div>
        <div class="card"><div class="card-head"><h2>Evidence</h2><span class="sub">${inv.evidence.length} item(s)</span></div><div class="card-body stack">
          <div class="timeline">${inv.evidence.map((e) => `<div class="item"><div class="row"><b class="small">${esc(e.kind.replace(/_/g, " "))}</b>${confBadge(e.confidence)}</div><div class="small">${esc(e.description)}</div><div class="meta">${esc(e.evidence_id)} · ${fmt.date(e.captured_at)}</div></div>`).join("")}</div>
        </div></div>
      </div>
    </div>`;
  $("[data-status]", view).onchange = async (e) => {
    try { await PATCH(`/investigations/${encodeURIComponent(id)}`, { status: e.target.value }); toast("Status updated", "ok"); render(); } catch (err) { toast(err.message, "bad"); }
  };
  $("[data-addnote]", view).onclick = (e) => act(e.currentTarget, "Saving…", async () => {
    const text = $("[data-note]", view).value.trim();
    if (!text) return;
    await POST(`/investigations/${encodeURIComponent(id)}/notes`, { text });
    render();
  });
  $("[data-del]", view).onclick = () => confirmDelete("this investigation", async () => { await DEL(`/investigations/${encodeURIComponent(id)}`); location.hash = "#/investigations"; });
  $$("[data-graph]", view).forEach((b) => (b.onclick = () => nodePicker(inv.connection_id, b.dataset.graph === "lineage" ? "Trace lineage of…" : "Assess impact of changing…", async (nodeId) => {
    await POST(`/investigations/${encodeURIComponent(id)}/${b.dataset.graph}`, { node_id: nodeId, connection_id: inv.connection_id });
    render();
  })));
}

function nodePicker(connectionId, title, onPick) {
  modal({
    title,
    body: `<input type="search" data-q placeholder="Search visuals, measures, columns…"><div data-list style="max-height:320px;overflow:auto">${loading()}</div>`,
    confirm: null,
    onOpen: (m) => {
      const list = $("[data-list]", m);
      let seq = 0;
      const search = async (text) => {
        const mine = ++seq;
        try {
          const nodes = await GET(`/connections/${encodeURIComponent(connectionId)}/nodes${q({ search: text, limit: 60 })}`);
          if (mine !== seq) return;
          list.innerHTML = nodes.length ? `<table class="data"><tbody>${nodes.map((n) => `<tr class="clickable" tabindex="0" data-id="${esc(n.id)}"><td>${esc(n.name)}</td><td>${badge(n.type.toLowerCase())}</td></tr>`).join("")}</tbody></table>` : `<div class="empty small">No matches.</div>`;
          $$("[data-id]", list).forEach((tr) => {
            tr.onclick = async () => { try { await onPick(tr.dataset.id); m.close(); } catch (e) { toast(e.message, "bad"); } };
            tr.onkeydown = (e) => { if (e.key === "Enter") tr.onclick(); };
          });
        } catch (e) { if (mine === seq && !isAbort(e)) list.innerHTML = alertBox("bad", [e.message]); }
      };
      $("[data-q]", m).oninput = debounce((e) => search(e.target.value), 200);
      search("");
    },
  });
}

/* ============================================================= SOURCES */
const SOURCE_PLATFORMS = [["powerbi", "Power BI"], ["tableau", "Tableau"], ["qlik", "Qlik"], ["looker", "Looker"], ["excel", "Excel / other"], ["generic", "Other BI tool"]];
async function viewSources(view) {
  const [conns, files, platforms] = await Promise.all([GET("/connections"), GET("/files"), GET("/platforms")]);
  const pname = Object.fromEntries(platforms.map((p) => [p.id, p.name]));
  view.innerHTML = `
    <div class="page-head"><div><h1>Sources</h1><p>Connect BI platforms and upload files. Anything any BI tool can export works through the "Exported files" connection.</p></div>
      <div class="row"><button class="btn primary" data-add>Add connection</button></div></div>
    <div class="card"><div class="card-head"><h2>Connections</h2><span class="sub">${conns.length}</span></div><div class="card-body tight">
      ${conns.length ? `<div class="table-wrap"><table class="data"><thead><tr><th>Name</th><th>Type</th><th>Status</th><th></th></tr></thead><tbody>
      ${conns.map((c) => `<tr><td><b>${esc(c.name)}</b><div class="faint small">${esc(c.id)}${c.server_url ? ` · ${esc(c.server_url)}` : ""}${c.pbip_dir ? ` · ${esc(c.pbip_dir)}` : ""}${c.file_ids.length ? ` · ${c.file_ids.length} file(s)` : ""}</div></td>
        <td>${esc(pname[c.platform] || c.platform)}</td>
        <td>${c.needs_credentials ? badge("Needs credentials", "warn") : c.last_synced_at ? badge(`Synced ${fmt.date(c.last_synced_at)}`, "ok") : badge("Not synced", "neutral")}</td>
        <td><div class="row" style="justify-content:flex-end">
          ${c.needs_credentials ? `<button class="btn sm" data-secret="${esc(c.id)}" data-platform="${esc(c.platform)}">Enter credentials</button>` : ""}
          ${c.platform === "files" ? `<button class="btn sm" data-addfiles="${esc(c.id)}">Add files</button>` : ""}
          <button class="btn sm" data-health="${esc(c.id)}">Test</button>
          <button class="btn sm" data-sync="${esc(c.id)}">Sync metadata</button>
          ${c.platform === "pbix" ? `<a class="btn sm" href="#/models/${esc(c.id)}">Open model</a>` : ""}<a class="btn sm" href="#/lineage?c=${esc(c.id)}">Lineage</a>
          <button class="btn ghost sm danger" data-delconn="${esc(c.id)}" aria-label="Delete connection">✕</button></div></td></tr>`).join("")}
      </tbody></table></div>` : `<div class="empty"><h3>No connections yet</h3><p>Add Power BI, Tableau, a PBIP folder — or upload exports below and create an "Exported files" connection.</p></div>`}
    </div></div>
    <div class="card"><div class="card-head"><h2>Files</h2><span class="sub">Excel workbooks, CSV exports — each tab can be reconciled</span></div>
      <div class="card-body stack">${dropzone("Drop Excel or CSV files here, or <strong>browse</strong> — you can select several", true)}
      ${files.length ? `<div class="table-wrap"><table class="data"><thead><tr><th>File</th><th>Tabs</th><th class="num">Size</th><th>Uploaded</th><th></th></tr></thead><tbody>
        ${files.map((f) => `<tr><td>${esc(f.name)}<div class="faint small">${esc(f.file_id)}</div></td><td class="small">${f.kind === "model" ? badge("Power BI file", "info") : f.sheets.map((s) => `${esc(s.name)} <span class="faint">(${s.rows}×${s.columns})</span>`).join(", ")}</td>
          <td class="num">${fmt.bytes(f.size)}</td><td class="muted small">${fmt.date(f.uploaded_at)}</td><td><button class="btn ghost sm danger" data-delfile="${esc(f.file_id)}" aria-label="Delete file">✕</button></td></tr>`).join("")}
      </tbody></table></div>` : ""}</div></div>`;

  wireDropzone(view, async (list) => {
    try { const saved = await uploadFiles(list); toast(`Uploaded ${saved.length} file(s)`, "ok"); render(); } catch (e) { toast(e.message, "bad"); }
  });
  $("[data-add]", view).onclick = () => addConnectionModal(platforms, files);
  $$("[data-sync]", view).forEach((b) => (b.onclick = (e) => act(e.currentTarget, "Syncing…", async () => {
    const r = await syncConnection(b.dataset.sync);
    toast(`Synced: ${r.nodes} objects, ${r.edges} dependencies${r.warnings.length ? ` · ${r.warnings.length} warning(s)` : ""}`, r.warnings.length ? "" : "ok");
    r.warnings.forEach((w) => toast(w));
    render();
  })));
  $$("[data-health]", view).forEach((b) => (b.onclick = (e) => act(e.currentTarget, "Testing…", async () => {
    const h = await GET(`/connections/${encodeURIComponent(b.dataset.health)}/health`);
    if (!h.reachable) throw new Error(h.error || "Not reachable");
    toast(`Connected — ${h.workspaces} workspace(s)/project(s) visible${h.capabilities?.notes?.length ? `. ${h.capabilities.notes[0]}` : ""}`, "ok");
  })));
  $$("[data-delconn]", view).forEach((b) => (b.onclick = () => confirmDelete("this connection", async () => { await DEL(`/connections/${encodeURIComponent(b.dataset.delconn)}`); delete graphCache[b.dataset.delconn]; render(); })));
  $$("[data-delfile]", view).forEach((b) => (b.onclick = () => confirmDelete("this file", async () => { await DEL(`/files/${encodeURIComponent(b.dataset.delfile)}`); render(); })));
  $$("[data-secret]", view).forEach((b) => (b.onclick = () => {
    const pb = b.dataset.platform === "powerbi";
    modal({
      title: "Enter credentials",
      body: `<p class="muted small" style="margin:0">Secrets are kept in server memory only — never written to disk — so they're needed again after a restart (or set them as environment variables).</p>
        <label class="field">${pb ? "Client secret" : "Personal access token secret"}<input type="password" data-s></label>`,
      onConfirm: async (m) => { await PUT(`/connections/${encodeURIComponent(b.dataset.secret)}/secrets`, { [pb ? "client_secret" : "pat_secret"]: $("[data-s]", m).value }); render(); },
    });
  }));
  $$("[data-addfiles]", view).forEach((b) => (b.onclick = () => {
    const conn = conns.find((c) => c.id === b.dataset.addfiles);
    const avail = files.filter((f) => !conn.file_ids.includes(f.file_id));
    modal({
      title: "Add files to connection",
      body: avail.length ? avail.map((f) => `<label class="check"><input type="checkbox" value="${esc(f.file_id)}"> ${esc(f.name)}</label>`).join("") : `<p class="muted">Upload more files first.</p>`,
      onConfirm: async (m) => {
        const ids = $$("input:checked", m).map((i) => i.value);
        if (ids.length) await POST(`/connections/${encodeURIComponent(conn.id)}/files`, { file_ids: ids });
        render();
      },
    });
  }));
}

function addConnectionModal(platforms, files) {
  const FIELDS = {
    tenant_id: ["Tenant ID", "text", "Azure AD tenant (directory) ID"],
    client_id: ["Client ID", "text", "App registration (service principal) ID"],
    client_secret: ["Client secret", "password", "Kept in memory only"],
    workspace_id: ["Workspace ID", "text", "Power BI workspace (group) ID"],
    pbip_dir: ["PBIP folder path", "text", "Folder on this server containing *.Report / *.SemanticModel"],
    server_url: ["Server URL", "text", "e.g. https://prod-uk-a.online.tableau.com"],
    site: ["Site", "text", "Site content URL (blank for Default)"],
    pat_name: ["Token name", "text", "Personal access token name"],
    pat_secret: ["Token secret", "password", "Kept in memory only"],
    api_version: ["REST API version", "text", "e.g. 3.21"],
  };
  const fieldsFor = (p) => {
    const def = platforms.find((x) => x.id === p);
    return def.fields.map((f) => {
      if (f === "model_file") return `<div class="alert info">Use the upload box on the Power BI models page (or Home) to open a .pbix / .pbit / .bim / PBIP zip.</div>`;
      if (f === "file_ids") return `<div class="stack"><span class="small muted">Files in this connection (each file = a report, each tab = a page)</span>${files.length ? files.map((x) => `<label class="check"><input type="checkbox" data-fileid value="${esc(x.file_id)}"> ${esc(x.name)}</label>`).join("") : '<span class="alert warn">Upload files on the Sources page first.</span>'}</div>`;
      if (f === "source_platform") return `<label class="field">Exported from<select data-field="source_platform">${SOURCE_PLATFORMS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select></label>`;
      const [label, type, hint] = FIELDS[f];
      return `<label class="field">${label}<input type="${type}" data-field="${f}" placeholder="${esc(hint)}" ${f === "api_version" ? 'value="3.21"' : ""}></label>`;
    }).join("");
  };
  // Power BI files become connections by uploading them on the Models page, not here
  const choosable = platforms.filter((p) => p.id !== "pbix");
  modal({
    title: "Add connection",
    body: `<label class="field">Type<select data-platform>${choosable.map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("")}</select></label>
      <div class="small muted" data-desc></div>
      <div class="small muted">Have a .pbix / .pbit / .bim file? Open it on the <a href="#/models">Power BI models</a> page instead.</div>
      <label class="field">Name<input type="text" data-field="name" placeholder="e.g. Finance workspace"></label>
      <div class="stack" data-fields></div>`,
    confirm: "Connect",
    onOpen: (m) => {
      const sel = $("[data-platform]", m);
      const draw = () => {
        const def = platforms.find((x) => x.id === sel.value);
        $("[data-desc]", m).textContent = def.description;
        $("[data-fields]", m).innerHTML = fieldsFor(sel.value);
      };
      sel.onchange = draw;
      draw();
    },
    onConfirm: async (m) => {
      const body = { platform: $("[data-platform]", m).value };
      $$("[data-field]", m).forEach((el) => { if (el.value) body[el.dataset.field] = el.value; });
      body.file_ids = $$("[data-fileid]:checked", m).map((el) => el.value);
      const c = await POST("/connections", body);
      toast(`Connected "${c.name}"`, "ok");
      if (["pbip", "files"].includes(c.platform)) {
        try { await syncConnection(c.id); } catch (e) { toast(`Connected, but sync failed: ${e.message}`, "bad"); }
      }
      render();
    },
  });
}

/* ============================================================= LINEAGE */
async function viewLineage(view, params) {
  const conns = await GET("/connections");
  if (!conns.length) {
    view.innerHTML = `<div class="page-head"><div><h1>Lineage &amp; impact</h1><p>Where does a number come from, and what breaks if something changes?</p></div></div>
      <div class="card">${emptyState("No connections yet", "Open a Power BI file or add a connection first.", `<a class="btn primary" href="#/models">Open a Power BI file</a>`, "link")}</div>`;
    return;
  }
  const cid = pick(conns, params.get("c"));
  const num = (k) => (params.has(k) && Number.isFinite(Number(params.get(k))) ? Math.max(0, Math.min(8, Number(params.get(k)))) : undefined);
  view.innerHTML = `
    <div class="page-head"><div><div class="eyebrow">Understand</div><h1>Lineage &amp; impact</h1>
        <p>Follow any number from its source system to every visual that shows it. Use <b>+</b> / <b>−</b> on a node to expand or collapse a step, click a node to trace its line, shift-click a second one for the path between them.</p></div>
      <div class="row"><select data-conn aria-label="Connection">${conns.map((c) => `<option value="${esc(c.id)}" ${c.id === cid ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>
        <button class="btn" data-sync>Sync</button></div></div>
    <div data-explorer></div>`;
  $("[data-conn]", view).onchange = (e) => (location.hash = `#/lineage?c=${encodeURIComponent(e.target.value)}`);
  $("[data-sync]", view).onclick = (e) => act(e.currentTarget, "Syncing…", async () => {
    const r = await syncConnection(cid);
    toast(`Synced: ${fmt.int(r.nodes)} objects, ${fmt.int(r.edges)} dependencies`, "ok");
    render();
  });
  new LineageExplorer($("[data-explorer]", view), {
    cid, root: params.get("n"), up: num("up"), down: num("down"),
    // shareable URL without re-rendering the page
    onChange: ({ node, up, down }) => history.replaceState(null, "", `#/lineage${q({ c: cid, n: node, up, down })}`),
  });
}

/* Layered star/snowflake layout: a table's column is the longest chain of
   many → one hops leading to it, so fact tables sit on the left and the
   dimensions they look up fan out to the right. */
function modelDiagram(rels) {
  const tables = [...new Set(rels.flatMap((r) => [r.from_table, r.to_table]))];
  const keys = new Map(tables.map((t) => [t, []]));
  const addKey = (t, c) => { const k = keys.get(t); if (!k.includes(c)) k.push(c); };
  rels.forEach((r) => { addKey(r.from_table, r.from_column); addKey(r.to_table, r.to_column); });
  const level = new Map(tables.map((t) => [t, 0]));
  for (let pass = 0; pass < tables.length; pass++) {
    let changed = false;
    for (const r of rels) {
      const d = level.get(r.from_table) + 1;
      if (d > level.get(r.to_table) && d < tables.length) { level.set(r.to_table, d); changed = true; }
    }
    if (!changed) break;
  }
  const cols = [];
  for (const t of tables) (cols[level.get(t)] = cols[level.get(t)] || []).push(t);
  const filled = cols.filter(Boolean);
  // order each column by the average position of its neighbours (fewer crossings)
  const neighbours = (t) => rels.flatMap((r) => (r.from_table === t ? [r.to_table] : r.to_table === t ? [r.from_table] : []));
  filled[0].sort();
  for (let sweep = 0; sweep < 2; sweep++) {
    for (let i = 0; i < filled.length; i++) {
      const rank = new Map(filled.flatMap((c) => c.map((t, j) => [t, j])));
      filled[i].sort((a, b) => {
        const avg = (t) => { const n = neighbours(t).filter((x) => level.get(x) !== level.get(t)); return n.length ? n.reduce((s, x) => s + rank.get(x), 0) / n.length : rank.get(t); };
        return avg(a) - avg(b);
      });
    }
  }
  const role = (t) => { const many = rels.some((r) => r.from_table === t), one = rels.some((r) => r.to_table === t); return many && !one ? "fact" : one && !many ? "dim" : "mid"; };
  const COLORS = { fact: "var(--n-measure)", dim: "var(--n-column)", mid: "var(--n-table)" };
  const W = 228, HEAD = 30, ROW = 19, GX = 120, GY = 22, P = 24, MAXK = 6;
  const height = (t) => HEAD + 10 + Math.min(keys.get(t).length, MAXK + (keys.get(t).length > MAXK ? 1 : 0)) * ROW + 6;
  const colHeights = filled.map((c) => c.reduce((s, t) => s + height(t) + GY, -GY));
  const H = Math.max(...colHeights) + P * 2;
  const pos = new Map();
  filled.forEach((c, i) => {
    let y = P + (H - P * 2 - colHeights[i]) / 2;
    c.forEach((t) => { pos.set(t, { x: P + i * (W + GX), y, h: height(t) }); y += height(t) + GY; });
  });
  const Wt = P * 2 + filled.length * (W + GX) - GX;
  const clip = (s, n) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
  const keyY = (t, c) => { const i = Math.min(keys.get(t).indexOf(c), MAXK); return pos.get(t).y + HEAD + 10 + i * ROW + ROW / 2; };
  const edges = rels.map((r) => {
    const a = pos.get(r.from_table), b = pos.get(r.to_table);
    const y1 = keyY(r.from_table, r.from_column), y2 = keyY(r.to_table, r.to_column);
    let x1, x2, d;
    if (b.x > a.x) { x1 = a.x + W; x2 = b.x; d = `M${x1},${y1} C${x1 + GX / 2},${y1} ${x2 - GX / 2},${y2} ${x2},${y2}`; }
    else if (b.x < a.x) { x1 = a.x; x2 = b.x + W; d = `M${x1},${y1} C${x1 - GX / 2},${y1} ${x2 + GX / 2},${y2} ${x2},${y2}`; }
    else { x1 = a.x + W; x2 = b.x + W; const bulge = x1 + 46; d = `M${x1},${y1} C${bulge},${y1} ${bulge},${y2} ${x2},${y2}`; }
    const [ca, cb] = r.cardinality === "many-to-many" ? ["*", "*"] : r.cardinality === "one-to-one" ? ["1", "1"] : ["*", "1"];
    const lx1 = x1 + (x2 >= x1 ? 9 : -9), lx2 = x2 + (x2 >= x1 ? -9 : 9);
    const cls = [r.active ? "" : "inactive", r.direction === "both" ? "both" : "", r.cardinality === "many-to-many" ? "m2m" : ""].join(" ");
    return `<g class="rel ${cls}" data-a="${esc(r.from_table)}" data-b="${esc(r.to_table)}"><title>${esc(r.from)} → ${esc(r.to)}\n${esc(r.cardinality)} · ${r.direction === "both" ? "filters both ways" : "single direction"} · ${r.active ? "active" : "inactive"}</title>
      <path d="${d}" marker-start="url(#mk-arrow)" ${r.direction === "both" ? 'marker-end="url(#mk-arrow)"' : ""}></path>
      <circle class="end" cx="${x1}" cy="${y1}" r="3.5"></circle><circle class="end" cx="${x2}" cy="${y2}" r="3.5"></circle>
      <text x="${lx1}" y="${y1 - 6}" text-anchor="middle">${ca}</text><text x="${lx2}" y="${y2 - 6}" text-anchor="middle">${cb}</text></g>`;
  }).join("");
  const nodes = tables.map((t) => {
    const p = pos.get(t), k = keys.get(t), r = role(t);
    const shown = k.slice(0, MAXK);
    return `<g class="tbl" data-t="${esc(t)}" transform="translate(${p.x},${p.y})"><title>${esc(t)} (${r === "fact" ? "fact / many side" : r === "dim" ? "lookup / one side" : "both sides"})</title>
      <rect class="box" width="${W}" height="${p.h}" rx="9"></rect>
      <path class="head" d="M0,9 a9,9 0 0 1 9,-9 h${W - 18} a9,9 0 0 1 9,9 v${HEAD - 9} h-${W} z" fill="${COLORS[r]}"></path>
      <text class="title" x="12" y="19.5">${esc(clip(t, 28))}</text>
      ${shown.map((c, i) => `<circle cx="16" cy="${HEAD + 10 + i * ROW + 9}" r="2.6" fill="${COLORS[r]}"></circle><text class="key" x="25" y="${HEAD + 10 + i * ROW + 13}">${esc(clip(c, 28))}</text>`).join("")}
      ${k.length > MAXK ? `<text class="meta" x="14" y="${HEAD + 10 + MAXK * ROW + 13}">+${k.length - MAXK} more key column(s)</text>` : ""}</g>`;
  }).join("");
  return `<svg class="model-svg" width="${Wt}" height="${H}" viewBox="0 0 ${Wt} ${H}" role="img" aria-label="Model relationship diagram">
    <defs><marker id="mk-arrow" viewBox="0 0 10 10" refX="11" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--faint)"></path></marker></defs>
    ${edges}${nodes}</svg>`;
}

/* ======================================================= POWER BI MODELS */
const SEV_KIND = { high: "bad", medium: "warn", low: "info", info: "neutral" };
// tables whose rows live in the .pbix (row counts can be unknown, so don't gate on t.rows)
const PREVIEWABLE = ["import", "entered_data", "calculated"];
const KIND_TEXT = { import: "Imported", calculated: "Calculated (DAX)", auto_date: "Auto date", measures: "Measure table", parameter: "Parameter", entered_data: "Entered data", table: "Table" };

function modelDropzone() {
  return `<label class="dropzone" data-model-drop><div class="dz-icon">${icon("model")}</div><strong>Drop a Power BI file here</strong> or <span style="color:var(--accent);font-weight:600">browse</span>
    <div class="small faint" style="margin-top:4px">.pbix · .pbit · .bim · zipped PBIP project — read locally, nothing is sent anywhere else</div>
    <input type="file" hidden accept=".pbix,.pbit,.bim,.zip"></label>`;
}
function wireModelDropzone(root) {
  const zone = $("[data-model-drop]", root);
  wireDrop(zone, async ([file]) => {
    zone.innerHTML = `${loading(`Reading ${file.name} — model, Power Query, pages and visuals…`)}`;
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await api("POST", "/model-files", fd);
      toast(`Read ${res.name}: ${res.counts.measures} measures, ${res.counts.tables} tables, ${res.counts.visuals} visuals`, "ok");
      location.hash = `#/models/${res.connection_id}`;
    } catch (e) {
      toast(e.message, "bad");
      render();
    }
  });
}

async function viewModels(view) {
  const list = await GET("/model-files");
  view.innerHTML = `
    <div class="page-head"><div><h1>Power BI models</h1>
      <p>Upload a report file and get its lineage, measure explanations, data sources and a health check — straight from the file, no exports or credentials.</p></div></div>
    <div class="card"><div class="card-body">${modelDropzone()}</div></div>
    <div class="card"><div class="card-head"><h2>Opened models</h2><span class="sub">${list.length}</span></div><div class="card-body tight">
      ${list.length ? `<div class="table-wrap"><table class="data"><thead><tr><th>Model</th><th>Source</th><th>Read</th><th></th></tr></thead><tbody>
        ${list.map((m) => `<tr ${goRow(`#/models/${m.connection_id}`)}><td><b>${esc(m.name)}</b><div class="faint small">${esc(m.connection_id)}</div></td>
          <td class="small">${esc(m.file_name || m.platform)}</td><td class="muted small">${fmt.date(m.last_synced_at)}</td>
          <td><div class="row" style="justify-content:flex-end;flex-wrap:nowrap"><span class="small">Open →</span><button class="btn ghost sm danger" data-delmodel="${esc(m.connection_id)}" aria-label="Delete model ${esc(m.name)}">Delete</button></div></td></tr>`).join("")}
      </tbody></table></div>` : emptyState("No models opened yet", "Drop a .pbix above to see its relationships, lineage, measures and health check.", "", "model")}</div></div>`;
  wireModelDropzone(view);
  $$("[data-delmodel]", view).forEach((b) => (b.onclick = () => confirmDelete("this model and its uploaded file", async () => {
    const id = b.dataset.delmodel;
    try {
      await DEL(`/model-files/${encodeURIComponent(id)}`);
    } catch (e) {
      if (e.status === 404 || e.status === 405) throw new Error(`Couldn't delete ${id}: ${e.message}`);
      throw e;
    }
    delete graphCache[id];
    toast("Model deleted", "ok");
    render();
  })));
}

async function viewModel(view, cid, params) {
  const tab = params.get("tab") || "overview";
  const ov = await GET(`/connections/${encodeURIComponent(cid)}/overview`);
  const c = ov.counts;
  const issues = ov.insights.filter((i) => i.severity === "high" || i.severity === "medium").length;
  const relIssues = ov.relationships.filter((r) => r.missing.length || r.type_mismatch || r.cardinality === "many-to-many").length;
  const tabs = [["overview", "Overview"], ["report", "Report", c.pages], ["measures", "Measures", c.measures], ["tables", "Tables", c.tables],
    ["relationships", "Relationships", c.relationships, relIssues], ["health", "Health check", issues, issues]];
  if (!isLive(view)) return; // a newer route took over while the overview loaded
  setCrumbs("models", ov.name);
  view.innerHTML = `
    <div class="page-head">
      <div><div class="row">${ov.format ? badge(ov.format.toUpperCase(), "info") : ""}<span class="faint small">${esc(cid)}</span></div>
        <h1 style="margin-top:8px">${esc(ov.name)}</h1>
        <p>${fmt.int(c.visuals)} visuals on ${c.pages} pages · ${fmt.int(c.measures)} measures · ${c.tables} tables · ${c.relationships} relationships · ${c.data_sources} data source(s)</p></div>
      <div class="row"><button class="btn ghost" data-reread>Re-read file</button><a class="btn primary" href="#/lineage?c=${encodeURIComponent(cid)}">Lineage explorer</a></div>
    </div>
    ${alertBox("warn", ov.warnings)}
    <div class="card"><div class="tabs" style="border-bottom:none">${tabs.map(([id, label, n, bad]) => `<a class="tab ${tab === id ? "active" : ""}" href="#/models/${encodeURIComponent(cid)}?tab=${id}">${esc(label)}${n != null ? `<span class="count ${bad ? "bad" : ""}">${fmt.int(n)}</span>` : ""}</a>`).join("")}</div></div>
    <div data-tab-body class="stack"></div>`;
  $("[data-reread]", view).onclick = (e) => act(e.currentTarget, "Reading…", async () => { await syncConnection(cid); render(); });
  const body = $("[data-tab-body]", view);
  const visualTitle = {};
  ov.pages.forEach((p) => p.visuals.forEach((v) => (visualTitle[v.id] = `${p.name} › ${v.title}`)));
  const lineageLink = (id, label = "Lineage") => `<a class="btn sm" href="#/lineage?c=${encodeURIComponent(cid)}&n=${encodeURIComponent(id)}">${label}</a>`;
  ({ overview: tabOverview, report: tabReport, measures: tabMeasures, tables: tabTables, relationships: tabRelationships, health: tabHealth })[tab]?.();

  function tabOverview() {
    body.innerHTML = `
      <div class="kpis">
        ${kpi("Pages", fmt.int(c.pages))}${kpi("Visuals", fmt.int(c.visuals))}${kpi("Measures", fmt.int(c.measures), `${fmt.int(c.unused_measures)} unused`)}
        ${kpi("Tables", fmt.int(c.tables), `${fmt.int(c.columns)} columns · ${fmt.int(c.calculated_columns)} calculated`)}
        ${kpi("Relationships", fmt.int(c.relationships))}${kpi("Issues to review", fmt.int(c.issues), "high / medium", c.issues ? "warn" : "ok")}
      </div>
      <div class="grid-2">
        <div class="card"><div class="card-head"><h2>Where the data comes from</h2><span class="sub">from Power Query</span></div><div class="card-body stack">
          ${ov.data_sources.length ? ov.data_sources.map((s) => `<div class="stack" style="gap:6px"><div class="row">${badge(s.kind || "source", "info")}<b class="small">${esc([s.server, s.database].filter(Boolean).join(" / ") || s.path || "")}</b></div>
            <div class="table-wrap"><table class="data"><tbody>${s.tables.map((t) => `<tr><td class="small">${esc(t.model_table)}</td><td class="small muted">${esc(t.source_object || "—")}${t.native_query ? " · SQL query" : ""}</td></tr>`).join("")}</tbody></table></div></div>`).join("")
            : `<div class="empty small">No external sources found — tables are calculated or typed into Power BI.</div>`}
        </div></div>
        <div class="stack">
          <div class="card"><div class="card-head"><h2>Health summary</h2><span class="spacer"></span><a class="small" href="#/models/${encodeURIComponent(cid)}?tab=health">All findings →</a></div><div class="card-body stack">
            ${ov.insights.slice(0, 6).map((i) => `<div class="row" style="align-items:flex-start;flex-wrap:nowrap">${badge(i.severity, SEV_KIND[i.severity])}<span class="small">${esc(i.title)}</span></div>`).join("") || '<div class="muted small">No issues found.</div>'}
          </div></div>
          <div class="card"><div class="card-head"><h2>Tables by type</h2></div><div class="card-body"><div class="chips">
            ${Object.entries(c.tables_by_kind).map(([k, n]) => `<span class="badge neutral">${esc(KIND_TEXT[k] || k)} · ${n}</span>`).join("")}</div></div></div>
        </div>
      </div>`;
  }

  function tabReport() {
    body.innerHTML = ov.pages.map((p) => `<div class="card"><div class="card-head"><h2>${esc(p.name)}</h2>${p.hidden ? badge("hidden", "neutral") : ""}
        <span class="sub">${p.visuals.length} data visual(s)${p.other_visuals ? ` · ${p.other_visuals} other element(s)` : ""}</span></div>
      <div class="card-body tight">${p.visuals.length ? `<div class="table-wrap"><table class="data"><thead><tr><th>Visual</th><th>Type</th><th>Shows</th><th>Filtered by</th><th></th></tr></thead><tbody>
        ${p.visuals.map((v) => `<tr><td>${esc(v.title)}${v.broken.length ? ` ${badge(`${v.broken.length} broken`, "bad")}` : ""}</td><td class="small muted">${esc(v.type || "")}</td>
          <td><div class="chips">${v.fields.map((f) => `<span class="badge ${f.type === "MISSING" ? "bad" : f.type === "MEASURE" ? "info" : "neutral"}" title="${esc(f.id)}">${esc(f.name)}</span>`).join("")}</div></td>
          <td><div class="chips">${v.filters.slice(0, 6).map((f) => `<span class="badge ${f.missing ? "bad" : "neutral"}" title="${esc(f.id)}">${esc(f.name)}</span>`).join("")}${v.filters.length > 6 ? `<span class="faint small">+${v.filters.length - 6}</span>` : ""}</div></td>
          <td>${lineageLink(v.id)}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty small">No data visuals on this page.</div>`}</div></div>`).join("") || `<div class="card"><div class="empty">No report pages in this file (model only).</div></div>`;
  }

  function tabMeasures() {
    const cats = [...new Set(ov.measures.map((m) => m.category))].sort();
    const st = { q: "", cat: "", unused: false, sel: params.get("m") || null };
    body.innerHTML = `<div class="grid-2" style="grid-template-columns:minmax(0,420px) minmax(0,1fr)">
      <div class="card"><div class="card-head" style="gap:8px"><input type="search" data-q placeholder="Search measures…" style="flex:1">
        <select data-cat aria-label="Category"><option value="">All types</option>${cats.map((x) => `<option value="${esc(x)}">${esc(x)}</option>`).join("")}</select>
        <label class="check small"><input type="checkbox" data-unused> Unused</label></div>
        <div class="card-body tight" data-list style="max-height:680px;overflow:auto"></div></div>
      <div data-detail class="stack"><div class="card">${emptyState("Select a measure", "See what it calculates in plain English, what it depends on and which visuals use it.", "", "chart")}</div></div></div>`;
    const listEl = $("[data-list]", body);
    const draw = () => {
      const q = st.q.toLowerCase();
      const rows = ov.measures.filter((m) => (!q || m.name.toLowerCase().includes(q) || (m.expression || "").toLowerCase().includes(q))
        && (!st.cat || m.category === st.cat) && (!st.unused || m.unused));
      listEl.innerHTML = rows.length ? `<table class="data"><tbody>${rows.map((m) => `<tr class="clickable ${m.id === st.sel ? "selected" : ""}" data-id="${esc(m.id)}">
        <td><div>${esc(m.name)}${m.unresolved.length ? ` ${badge("broken ref", "bad")}` : ""}</div><div class="faint small">${esc(m.folder ? `${m.folder} · ` : "")}${esc(m.category)}</div></td>
        <td class="num small">${m.unused ? badge("unused", "warn") : `<span class="muted">${m.used_by_visuals} visual${m.used_by_visuals === 1 ? "" : "s"}</span>`}</td></tr>`).join("")}</tbody></table>` : `<div class="empty small">No measures match.</div>`;
      $$("[data-id]", listEl).forEach((tr) => (tr.onclick = () => { st.sel = tr.dataset.id; draw(); detail(); }));
    };
    const detail = () => {
      const m = ov.measures.find((x) => x.id === st.sel);
      const el = $("[data-detail]", body);
      if (!m) return;
      el.innerHTML = `<div class="card"><div class="card-head"><div><h2>${esc(m.name)}</h2><div class="sub">${esc(m.table)}${m.folder ? ` · ${esc(m.folder)}` : ""} · ${esc(m.category)}</div></div><span class="spacer"></span>
          ${lineageLink(m.id, "Lineage & impact")}</div>
        <div class="card-body stack">
          <div class="alert info"><div><b>In plain English:</b> ${esc(m.explanation)}</div></div>
          ${m.description ? `<div class="small"><b>Description:</b> ${esc(m.description)}</div>` : ""}
          ${m.unresolved.length ? alertBox("bad", [`References that don't exist in the model: ${m.unresolved.join(", ")}`]) : ""}
          <div><div class="small muted">DAX</div><pre class="mono" style="white-space:pre-wrap;margin:4px 0 0;background:var(--surface-2);padding:10px;border-radius:6px">${esc(m.expression)}</pre></div>
          <div class="grid-2">
            <div><h3>Depends on</h3><div class="stack small" style="gap:4px;margin-top:6px">
              ${m.depends_on_measures.map((n) => `<a href="#" data-jump="${esc(n)}">[${esc(n)}]</a>`).join("")}
              ${m.depends_on_columns.map((x) => `<span>${esc(x)}</span>`).join("")}${m.depends_on_tables.map((x) => `<span>table ${esc(x)}</span>`).join("")}
              ${!m.depends_on_measures.length && !m.depends_on_columns.length && !m.depends_on_tables.length ? '<span class="muted">Nothing (constant or unrecognised references)</span>' : ""}</div></div>
            <div><h3>Used by</h3><div class="stack small" style="gap:4px;margin-top:6px">
              ${m.visual_ids.map((v) => `<span>${esc(visualTitle[v] || v)}</span>`).join("") || '<span class="muted">No visuals in this report</span>'}
              ${m.used_by_measures ? `<span class="muted">+ ${m.used_by_measures} other measure(s)</span>` : ""}</div></div>
          </div>
        </div></div>`;
      $$("[data-jump]", el).forEach((a) => (a.onclick = (e) => { e.preventDefault(); const t = ov.measures.find((x) => x.name === a.dataset.jump); if (t) { st.sel = t.id; draw(); detail(); } }));
    };
    $("[data-q]", body).oninput = debounce((e) => { st.q = e.target.value; draw(); }, 150);
    $("[data-cat]", body).onchange = (e) => { st.cat = e.target.value; draw(); };
    $("[data-unused]", body).onchange = (e) => { st.unused = e.target.checked; draw(); };
    draw();
    if (st.sel) detail();
  }

  function tabTables() {
    const showAuto = params.get("auto") === "1";
    const tables = ov.tables.filter((t) => showAuto || t.kind !== "auto_date");
    body.innerHTML = `<div class="row"><span class="muted small">${tables.length} table(s)</span><span class="spacer"></span>
        <a class="small" href="#/models/${encodeURIComponent(cid)}?tab=tables${showAuto ? "" : "&auto=1"}">${showAuto ? "Hide" : "Show"} auto date tables</a></div>
      ${tables.map((t, i) => `<details class="card" data-i="${i}"><summary class="card-head" style="cursor:pointer;list-style:none">
          <b>${esc(t.name)}</b>${badge(KIND_TEXT[t.kind] || t.kind, t.kind === "import" ? "info" : "neutral")}${t.hidden ? badge("hidden", "neutral") : ""}${!t.used ? badge("unused", "warn") : ""}
          <span class="sub">${t.rows != null ? `${fmt.int(t.rows)} rows · ` : ""}${t.column_count} columns${t.measure_count ? ` · ${t.measure_count} measures` : ""}</span>
          <span class="spacer"></span><span class="small muted" style="max-width:460px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(t.source || "")}</span></summary>
        <div class="card-body stack" data-body></div></details>`).join("")}`;
    $$("details[data-i]", body).forEach((d) => d.addEventListener("toggle", () => {
      if (!d.open || d.dataset.loaded) return;
      d.dataset.loaded = "1";
      const t = tables[Number(d.dataset.i)];
      const el = $("[data-body]", d);
      el.innerHTML = `
        ${t.summary ? `<div class="alert info"><div><b>How it's loaded:</b> ${esc(t.summary)}</div></div>` : ""}
        ${t.depends_on_tables.length ? `<div class="small"><b>Built from:</b> ${t.depends_on_tables.map(esc).join(", ")}</div>` : ""}
        ${t.steps.length ? `<details class="adv"><summary>Power Query steps (${t.steps.length})</summary><ol class="small" style="margin:0;padding-left:20px">${t.steps.map((s) => `<li><b>${esc(s.name)}</b> <span class="muted">— ${esc(s.description)}</span></li>`).join("")}</ol></details>` : ""}
        ${t.expression ? `<details class="adv"><summary>${t.kind === "calculated" || t.kind === "parameter" ? "DAX definition" : "M query"}</summary><pre class="mono" style="white-space:pre-wrap;margin:0;background:var(--surface-2);padding:10px;border-radius:6px;max-height:320px;overflow:auto">${esc(t.expression)}</pre></details>` : ""}
        <div class="table-wrap"><table class="data"><thead><tr><th>Column</th><th>Type</th><th>Status</th><th></th></tr></thead><tbody>
          ${t.columns.map((col) => `<tr><td>${esc(col.name)}${col.calculated ? `<div class="faint small" title="${esc(col.expression)}">= ${esc(col.explanation || col.expression)}</div>` : ""}</td>
            <td class="small muted">${esc(col.data_type)}</td>
            <td>${col.calculated ? badge("calculated", "info") : ""}${col.hidden ? badge("hidden", "neutral") : ""}${!col.used ? badge("unused", "warn") : ""}</td>
            <td>${lineageLink(col.id, "Impact")}</td></tr>`).join("")}</tbody></table></div>
        ${ov.format === "pbix" && PREVIEWABLE.includes(t.kind) ? `<div><button class="btn sm" data-preview>Preview data</button><div data-out></div></div>` : ""}`;
      const btn = $("[data-preview]", el);
      if (btn) btn.onclick = (e) => act(e.currentTarget, "Loading…", async () => {
        const dat = await GET(`/connections/${encodeURIComponent(cid)}/table-data${q({ table: t.name, limit: 50 })}`);
        $("[data-out]", el).innerHTML = `<div class="small muted" style="margin:8px 0">First ${dat.rows.length} rows</div>${previewTable(dat.columns, dat.rows, 50)}`;
      });
    }));
  }

  function tabRelationships() {
    const rels = ov.relationships;
    if (!rels.length) {
      body.innerHTML = `<div class="card">${emptyState("No relationships in this model", "Every table stands alone, so visuals can only combine fields from one table (or through DAX).", "", "link")}</div>`;
      return;
    }
    // same rule as the health check: data tables in use that nothing relates to
    const isolated = ov.tables.filter((t) => ["import", "calculated"].includes(t.kind) && !t.has_relationships && t.used).map((t) => t.name);
    const counts = {
      inactive: rels.filter((r) => !r.active).length, both: rels.filter((r) => r.direction === "both").length,
      m2m: rels.filter((r) => r.cardinality === "many-to-many").length, mismatch: rels.filter((r) => r.type_mismatch).length,
      missing: rels.filter((r) => r.missing.length).length,
    };
    const st = { q: "", show: "", focus: null };
    body.innerHTML = `
      <div class="kpis">
        ${kpi("Relationships", fmt.int(rels.length), `${fmt.int(rels.length - counts.inactive)} active · ${fmt.int(counts.inactive)} inactive`)}
        ${kpi("Bi-directional", fmt.int(counts.both), "filters flow both ways", counts.both ? "warn" : "ok")}
        ${kpi("Many-to-many", fmt.int(counts.m2m), "can double count", counts.m2m ? "bad" : "ok")}
        ${kpi("Key problems", fmt.int(counts.mismatch + counts.missing), "type mismatch / missing column", counts.mismatch + counts.missing ? "bad" : "ok")}
        ${kpi("Unrelated tables", fmt.int(isolated.length), "used tables with no relationship", isolated.length ? "warn" : "ok")}
      </div>
      <div class="card"><div class="card-head"><div><h2>Model diagram</h2><div class="sub">Fact tables on the left, the tables they look up on the right. Arrows show which way filters flow. Click a table to focus on it.</div></div>
        <span class="spacer"></span><div class="legend"><span><i class="line"></i>single</span><span><i class="line" style="border-color:var(--warn)"></i>both ways</span><span><i class="line dashed"></i>inactive</span><span><i class="line" style="border-color:var(--bad)"></i>many-to-many</span></div></div>
        <div class="model-canvas" data-canvas>${modelDiagram(rels)}</div></div>
      ${isolated.length ? `<div class="alert warn"><div><b>${isolated.length} data table(s) aren't related to anything:</b> ${isolated.map(esc).join(", ")}. Visuals that mix them with other tables won't filter as expected.</div></div>` : ""}
      <div class="card"><div class="card-head" style="gap:8px"><h2>All relationships</h2><span class="spacer"></span>
        <input type="search" data-q placeholder="Search tables or columns…" style="width:240px">
        <select data-show aria-label="Show"><option value="">All</option><option value="issues">Needs attention</option><option value="inactive">Inactive</option><option value="both">Bi-directional</option></select></div>
        <div class="card-body tight" data-list></div></div>`;
    const card = (r) => r.cardinality === "many-to-many" ? ["*", "*", "Many-to-many", "bad"] : r.cardinality === "one-to-one" ? ["1", "1", "One-to-one", "info"] : ["*", "1", "Many-to-one", "neutral"];
    const draw = () => {
      const q = st.q.toLowerCase();
      const rows = rels.filter((r) => (!q || `${r.from} ${r.to}`.toLowerCase().includes(q))
        && (!st.focus || r.from_table === st.focus || r.to_table === st.focus)
        && (!st.show || (st.show === "issues" ? r.missing.length || r.type_mismatch || r.cardinality === "many-to-many" : st.show === "inactive" ? !r.active : r.direction === "both")));
      $("[data-list]", body).innerHTML = rows.length ? `<div class="table-wrap"><table class="data"><thead><tr><th>From · many side</th><th style="text-align:center">Join</th><th>To · one side</th><th>Cardinality</th><th>Filter direction</th><th>Status</th><th>Checks</th></tr></thead><tbody>
        ${rows.map((r) => { const [a, b, label, kind] = card(r); return `<tr>
          <td><div class="cell-title">${esc(r.from_table)}</div><div class="cell-sub mono">${esc(r.from_column)}${r.from_type ? ` · ${esc(r.from_type)}` : ""}</div></td>
          <td><div class="rel-line" title="${r.direction === "both" ? "Filters flow both ways" : `Filters flow from ${esc(r.to_table)} to ${esc(r.from_table)}`}"><span>${a}</span><span class="bar ${r.active ? "" : "dashed"} ${r.direction === "both" ? "both" : ""}" style="transform:scaleX(-1)"></span><span>${b}</span></div></td>
          <td><div class="cell-title">${esc(r.to_table)}</div><div class="cell-sub mono">${esc(r.to_column)}${r.to_type ? ` · ${esc(r.to_type)}` : ""}</div></td>
          <td>${badge(label, kind)}</td>
          <td>${r.direction === "both" ? badge("Both ways", "warn") : badge("Single", "neutral")}</td>
          <td>${r.active ? badge("Active", "ok") : `<span title="Only used where a measure calls USERELATIONSHIP">${badge("Inactive", "neutral")}</span>`}</td>
          <td><div class="chips">${r.missing.length ? badge("Missing column", "bad") : ""}${r.type_mismatch ? `<span title="${esc(r.from_type)} vs ${esc(r.to_type)}">${badge("Type mismatch", "bad")}</span>` : ""}${!r.missing.length && !r.type_mismatch ? `<span class="faint small">${icon("check").replace("<svg", '<svg width="14" height="14" style="vertical-align:-2px;color:var(--ok)"')} OK</span>` : ""}</div></td></tr>`; }).join("")}
        </tbody></table></div>` : emptyState("No relationships match", st.focus ? `Filtered to <b>${esc(st.focus)}</b>.` : "", st.focus ? `<button class="btn sm" data-clear>Show all tables</button>` : "");
      const clear = $("[data-clear]", body);
      if (clear) clear.onclick = () => focus(null);
    };
    const focus = (table) => {
      st.focus = st.focus === table ? null : table;
      const svg = $("[data-canvas] svg", body);
      const linked = new Set(st.focus ? rels.filter((r) => r.from_table === st.focus || r.to_table === st.focus).flatMap((r) => [r.from_table, r.to_table]) : []);
      $$(".tbl", svg).forEach((g) => { g.classList.toggle("focus", g.dataset.t === st.focus); g.classList.toggle("dim", !!st.focus && !linked.has(g.dataset.t)); });
      $$(".rel", svg).forEach((g) => { const on = st.focus && (g.dataset.a === st.focus || g.dataset.b === st.focus); g.classList.toggle("focus", !!on); g.classList.toggle("dim", !!st.focus && !on); });
      draw();
    };
    $$("[data-canvas] .tbl", body).forEach((g) => (g.onclick = () => focus(g.dataset.t)));
    $("[data-q]", body).oninput = debounce((e) => { st.q = e.target.value; draw(); }, 150);
    $("[data-show]", body).onchange = (e) => { st.show = e.target.value; draw(); };
    draw();
  }

  function tabHealth() {
    body.innerHTML = ov.insights.length ? ov.insights.map((i) => `<div class="card"><div class="card-head">${badge(i.severity, SEV_KIND[i.severity])}<h2>${esc(i.title)}</h2><span class="spacer"></span><span class="faint small">${esc(i.category)}</span></div>
      <div class="card-body stack"><div class="small">${esc(i.detail)}</div>
      ${i.items.length ? `<details class="adv" ${i.severity === "high" ? "open" : ""}><summary>${i.count} item(s)</summary><ul class="small" style="margin:0;padding-left:18px;max-height:320px;overflow:auto">${i.items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>` : ""}</div></div>`).join("")
      : `<div class="card"><div class="empty"><h3>No issues found</h3></div></div>`;
  }
}

/* ============================================================== THEME */
(function theme() {
  const root = document.documentElement;
  try { const saved = localStorage.getItem("bip-theme"); if (saved) root.dataset.theme = saved; } catch { /* storage unavailable */ }
  $("#theme-toggle").onclick = () => {
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("bip-theme", root.dataset.theme); } catch { /* ignore */ }
  };
})();

if (typeof initDashNav === "function") initDashNav();
render();
