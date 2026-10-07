/*
 * Lineage canvas engine (ported from app/static/lineage.js as an ES module).
 * React mounts it through components/lineage/LineageCanvas.tsx and calls
 * destroy() on unmount — Next.js keeps hidden routes alive (Activity), so
 * global listeners must be released explicitly.
 */
import { $, $$, esc, q, debounce, isAbort, fmt, toast, loading, alertBox, GET, syncConnection, act } from "./dom";


/* =====================================================================
   Interactive lineage explorer.

   Data flows left -> right: data source -> table -> column -> measure ->
   visual. The focus object sits in the middle; the step limits (top-left)
   decide how far upstream / downstream is loaded, and every node carries
   +/- pills to expand or collapse one more step in either direction.

   Click a node to light up its whole lineage line (click again: off),
   shift-click a second node for the path between the two, double-click to
   re-centre on it. Uses app.js helpers ($, esc, GET, q, toast, isAbort).
   ===================================================================== */
const LX_TYPES = {
  DATA_SOURCE: { label: "Data source", color: "var(--n-source)", glyph: "DB", rank: 0 },
  TABLE: { label: "Table", color: "var(--n-table)", glyph: "T", rank: 1 },
  COLUMN: { label: "Column", color: "var(--n-column)", glyph: "C", rank: 2 },
  MEASURE: { label: "Measure", color: "var(--n-measure)", glyph: "Σ", rank: 3 },
  VISUAL: { label: "Visual", color: "var(--n-visual)", glyph: "V", rank: 4 },
  REPORT: { label: "Report", color: "var(--n-other)", glyph: "R", rank: 5 },
  PAGE: { label: "Page", color: "var(--n-other)", glyph: "P", rank: 5 },
  MISSING: { label: "Missing field", color: "var(--bad)", glyph: "!", rank: 6 },
};
const lxType = (t) => LX_TYPES[t] || { label: String(t || "object").toLowerCase().replace("_", " "), color: "var(--n-other)", glyph: "•", rank: 7 };
const LX_FILTERABLE = ["DATA_SOURCE", "TABLE", "COLUMN", "MEASURE", "VISUAL"];
const LX_CONF = { KNOWN: "var(--ok)", VERIFIED: "var(--ok)", PARTIALLY_VERIFIED: "var(--warn)", INFERRED: "var(--info)" };
const LX = { W: 236, H: 62, GX: 120, GY: 22, PORT: 11 };
const LX_NS = "http://www.w3.org/2000/svg";

const lxEl = (tag, attrs = {}, parent = null) => {
  const e = document.createElementNS(LX_NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) e.setAttribute(k, v);
  if (parent) parent.append(e);
  return e;
};
const lxText = (parent, attrs, text) => { const t = lxEl("text", attrs, parent); t.textContent = text; return t; };
const lxClip = (s, n) => { s = String(s ?? ""); return s.length > n ? `${s.slice(0, n - 1)}…` : s; };
const lxKey = (e) => `${e.source}\u0001${e.target}\u0001${e.type}`;
const lxPrefs = {
  get(k, d) { try { const v = localStorage.getItem(`lx-${k}`); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(`lx-${k}`, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};
const lxIcon = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const LX_I = {
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
  zin: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4M8 11h6M11 8v6"/>',
  zout: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4M8 11h6"/>',
  fit: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  full: '<path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"/>',
  dl: '<path d="M12 4v11M7 10l5 5 5-5M4 19h16"/>',
  filter: '<path d="M3 5h18l-7 8v6l-4-2v-4z"/>',
  reset: '<path d="M4 4v6h6M20 20v-6h-6M5 15a7 7 0 0 0 12 3M19 9A7 7 0 0 0 7 6"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>',
};

export const lxTypeOf = (t) => lxType(t);

export class LineageExplorer {
  constructor(host, { cid, root = null, up, down, onChange, embedded = false, onInspect = null, maxSteps = 8 }) {
    this.host = host;
    this.cid = cid;
    this.onChange = onChange || (() => {});
    // embedded: the page around it provides the object list and the details
    // panel; double-click hands the node to onInspect instead of re-focusing
    this.embedded = embedded;
    this.onInspect = onInspect;
    this.maxSteps = maxSteps;
    this.up = Number.isFinite(up) ? up : lxPrefs.get("up", 2);
    this.down = Number.isFinite(down) ? down : lxPrefs.get("down", 2);
    this.hiddenTypes = new Set(lxPrefs.get("hidden", []));
    this.showFilters = lxPrefs.get("filters", true);
    this.nodes = new Map();
    this.edges = new Map();
    this.pos = new Map();         // id -> {x, y} currently drawn
    this.nodeEls = new Map();
    this.view = { x: 0, y: 0, k: 1 };
    this.sel = null;              // highlight anchor (click)
    this.sel2 = null;             // second anchor (shift-click): path between
    this.seq = 0;
    this.drawerSeq = 0;
    this.browseOpen = embedded ? false : lxPrefs.get("browse", true);
    this.browseType = lxPrefs.get("browseType", "");
    this.catalog = null;
    this.build();
    if (root) this.setRoot(root);  // a linked object loads straight away, in parallel with the object list
    if (embedded) return;
    this.loadCatalog().then(() => {
      if (this.fallback) { this.autoFocus(this.fallback); this.fallback = null; }
      else if (!root) (this.catalog?.length ? this.autoFocus("") : this.showEmpty());
    });
  }

  /* nothing chosen (or a stale link): open the first visual so the canvas is never empty */
  autoFocus(reason) {
    const first = this.catalog?.find((n) => n.type === "VISUAL") || this.catalog?.find((n) => n.type === "MEASURE") || this.catalog?.[0];
    if (!first) { this.showEmpty(); return; }
    if (reason) toast(reason, "");
    this.setRoot(first.id);
  }

  /* --------------------------------------------------------- objects panel */
  async loadCatalog() {
    const list = $("[data-browse-list]", this.root$);
    try {
      this.catalog = await GET(`${this.api}/nodes${q({ limit: 100000 })}`);
      this.renderBrowse();
    } catch (e) {
      if (isAbort(e)) return;
      this.catalog = [];
      if (list) list.innerHTML = e.status === 409
        ? `<div class="lx-browse-empty">This model hasn't been read yet.<br><button class="btn sm primary" data-sync-now>Sync now</button></div>`
        : alertBox("bad", [e.message]);
      $("[data-sync-now]", this.root$)?.addEventListener("click", async (ev) => {
        await act(ev.currentTarget, "Syncing…", async () => { await syncConnection(this.cid); await this.loadCatalog(); this.autoFocus(""); });
      });
    }
  }

  renderBrowse() {
    const box = $("[data-browse]", this.root$);
    if (!box || !this.catalog) return;
    const counts = {};
    for (const n of this.catalog) counts[n.type] = (counts[n.type] || 0) + 1;
    const tabs = [["", "All", this.catalog.length], ...["VISUAL", "MEASURE", "COLUMN", "TABLE", "DATA_SOURCE"].filter((t) => counts[t]).map((t) => [t, `${lxType(t).label}s`, counts[t]])];
    $("[data-browse-tabs]", box).innerHTML = tabs.map(([t, label, n]) => `<button class="${t === this.browseType ? "on" : ""}" data-btype="${t}">${t ? `<i style="background:${lxType(t).color}"></i>` : ""}${esc(label)}<span>${fmt.int(n)}</span></button>`).join("");
    $$("[data-btype]", box).forEach((b) => (b.onclick = () => { this.browseType = b.dataset.btype; lxPrefs.set("browseType", this.browseType); this.renderBrowse(); }));
    const needle = ($("[data-browse-q]", box).value || "").trim().toLowerCase();
    const rows = this.catalog.filter((n) => (!this.browseType || n.type === this.browseType)
      && (!needle || n.name.toLowerCase().includes(needle) || String(n.parent_id || "").toLowerCase().includes(needle) || String(n.properties?.page_name || "").toLowerCase().includes(needle)));
    const MAX = 400;
    const sub = (n) => n.type === "VISUAL" ? n.properties?.page_name || n.properties?.visual_type || "" : n.parent_id || lxType(n.type).label;
    $("[data-browse-list]", box).innerHTML = rows.length
      ? rows.slice(0, MAX).map((n) => `<button class="${n.id === this.rootId ? "on" : ""}" data-pick="${esc(n.id)}" title="${esc(n.name)}">
          <i style="background:${lxType(n.type).color}"></i><span class="nm">${esc(n.name)}</span><span class="sb">${esc(sub(n))}</span></button>`).join("")
        + (rows.length > MAX ? `<div class="lx-browse-empty">Showing ${MAX} of ${fmt.int(rows.length)} — type to narrow down.</div>` : "")
      : `<div class="lx-browse-empty">No objects match.</div>`;
    $$("[data-pick]", box).forEach((b) => (b.onclick = () => this.setRoot(b.dataset.pick)));
  }

  toggleBrowse(open = !this.browseOpen) {
    this.browseOpen = open;
    lxPrefs.set("browse", open);
    this.root$.classList.toggle("browse-open", open);
    $("[data-act=browse]", this.root$)?.classList.toggle("on", open);
  }

  /* screen space the side panels cover, so fit/centre use the visible part */
  insets() {
    const left = this.browseOpen ? ($("[data-browse]", this.root$)?.getBoundingClientRect().width || 0) + 12 : 0;
    const right = this.drawer.classList.contains("open") ? this.drawer.getBoundingClientRect().width : 0;
    return { left, right };
  }

  get api() { return `/connections/${encodeURIComponent(this.cid)}`; }

  /* ------------------------------------------------------------ chrome */
  build() {
    const chips = LX_FILTERABLE.map((t) => `<button class="lx-chip ${this.hiddenTypes.has(t) ? "off" : ""}" data-type="${t}" title="Show / hide ${lxType(t).label.toLowerCase()}s (paths are bridged)"><i style="background:${lxType(t).color}"></i>${lxType(t).label}s</button>`).join("");
    this.host.innerHTML = `
      <div class="lx" tabindex="0" aria-label="Lineage explorer">
        <div class="lx-canvas"><svg aria-hidden="true">
          <defs>
            <pattern id="lx-dots" width="22" height="22" patternUnits="userSpaceOnUse"><circle cx="1.5" cy="1.5" r="1.1" fill="var(--border-strong)" opacity=".55"></circle></pattern>
            <marker id="lx-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z"></path></marker>
            <marker id="lx-arrow-hl" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z"></path></marker>
          </defs>
          <g class="lx-vp"><rect class="lx-grid" x="-20000" y="-20000" width="40000" height="40000"></rect><g class="lx-edges"></g><g class="lx-nodes"></g></g>
        </svg></div>
        <div class="lx-panel lx-tl">
          ${this.embedded ? "" : `<button class="lx-btn ${this.browseOpen ? "on" : ""}" data-act="browse" title="Show / hide the list of all objects">${lxIcon('<path d="M4 6h16M4 12h16M4 18h10"/>')}Objects</button>
          <div class="lx-sep"></div>`}
          <div class="lx-search">${lxIcon(LX_I.search)}<input type="search" placeholder="Find a visual, measure, column…" aria-label="Find an object" data-search><div class="lx-results" hidden></div></div>
          <div class="lx-sep"></div>
          <div class="lx-step" title="How many steps upstream (towards the data source) to load">
            <span class="lbl"><i style="background:var(--n-source)"></i>Upstream</span>
            <button class="lx-btn" data-step="up:-1" aria-label="Fewer upstream steps">−</button><span class="val" data-val="up">${this.up}</span><button class="lx-btn" data-step="up:1" aria-label="More upstream steps">+</button>
          </div>
          <div class="lx-step" title="How many steps downstream (towards the visuals) to load">
            <span class="lbl"><i style="background:var(--n-visual)"></i>Downstream</span>
            <button class="lx-btn" data-step="down:-1" aria-label="Fewer downstream steps">−</button><span class="val" data-val="down">${this.down}</span><button class="lx-btn" data-step="down:1" aria-label="More downstream steps">+</button>
          </div>
        </div>
        <div class="lx-panel lx-tr">
          <button class="lx-btn" data-act="zout" title="Zoom out (-)">${lxIcon(LX_I.zout)}</button>
          <button class="lx-btn" data-act="zin" title="Zoom in (+)">${lxIcon(LX_I.zin)}</button>
          <button class="lx-btn" data-act="fit" title="Fit to screen (F)">${lxIcon(LX_I.fit)}</button>
          <button class="lx-btn" data-act="focus" title="Centre on the focus object (C)">${lxIcon(LX_I.target)}</button>
          <div class="lx-sep"></div>
          <button class="lx-btn" data-act="types" title="Filter object types">${lxIcon(LX_I.filter)}</button>
          <button class="lx-btn ${this.showFilters ? "on" : ""}" data-act="filters" title="Show links from report / page / visual filters">Filters</button>
          <button class="lx-btn" data-act="reset" title="Reload and collapse everything (R)">${lxIcon(LX_I.reset)}</button>
          <div class="lx-sep"></div>
          <button class="lx-btn" data-act="svg" title="Download as SVG">${lxIcon(LX_I.dl)}SVG</button>
          <button class="lx-btn" data-act="png" title="Download as PNG">PNG</button>
          <button class="lx-btn" data-act="full" title="Full screen">${lxIcon(LX_I.full)}</button>
        </div>
        <div class="lx-panel lx-tr" data-types-panel style="top:62px" hidden><div class="lx-chips">${chips}</div></div>
        <div class="lx-browse" data-browse>
          <div class="lx-browse-tabs" data-browse-tabs></div>
          <div class="lx-browse-q">${lxIcon(LX_I.search)}<input type="search" placeholder="Filter objects…" data-browse-q aria-label="Filter objects"></div>
          <div class="lx-browse-list" data-browse-list>${typeof loading === "function" ? loading("Reading the model…") : ""}</div>
        </div>
        <div class="lx-panel lx-bl" data-legend></div>
        <div class="lx-drawer" aria-live="polite"></div>
        <div class="lx-mini" title="Overview — click to move there"><svg></svg></div>
        <div class="lx-tip" hidden></div>
        <div data-overlay></div>
      </div>`;
    this.root$ = $(".lx", this.host);
    this.canvas = $(".lx-canvas", this.root$);
    this.svg = $(".lx-canvas svg", this.root$);
    this.vp = $(".lx-vp", this.root$);
    this.gEdges = $(".lx-edges", this.root$);
    this.gNodes = $(".lx-nodes", this.root$);
    this.drawer = $(".lx-drawer", this.root$);
    this.mini = $(".lx-mini svg", this.root$);
    this.overlay = $("[data-overlay]", this.root$);
    this.root$.classList.toggle("browse-open", this.browseOpen);
    $("[data-browse-q]", this.root$).oninput = debounce(() => this.renderBrowse(), 120);
    this.wire();
  }

  wire() {
    const r = this.root$;
    $$("[data-step]", r).forEach((b) => (b.onclick = () => {
      const [dir, delta] = b.dataset.step.split(":");
      const next = Math.max(0, Math.min(this.maxSteps, this[dir] + Number(delta)));
      if (next === this[dir]) return;
      this[dir] = next;
      lxPrefs.set(dir, next);
      $(`[data-val=${dir}]`, r).textContent = next;
      this.reloadSoon();
    }));
    $$("[data-act]", r).forEach((b) => (b.onclick = () => this.action(b.dataset.act, b)));
    $$("[data-type]", r).forEach((b) => (b.onclick = () => {
      const t = b.dataset.type;
      this.hiddenTypes.has(t) ? this.hiddenTypes.delete(t) : this.hiddenTypes.add(t);
      b.classList.toggle("off", this.hiddenTypes.has(t));
      lxPrefs.set("hidden", [...this.hiddenTypes]);
      this.relayout();
    }));
    this.wireSearch();
    this.wirePanZoom();
    this.wireMini();
    this.onKey = (e) => {
      if (!this.alive()) return;
      if (e.target.matches?.("input, textarea, select")) { if (e.key === "Escape") e.target.blur(); return; }
      if (document.querySelector(".modal-back")) return;
      const k = e.key;
      if (k === "/" && !this.embedded) { e.preventDefault(); $("[data-search]", this.root$).focus(); }
      else if (k === "f" || k === "F") this.fit();
      else if (k === "c" || k === "C") this.centerOn(this.rootId);
      else if (k === "r" || k === "R") this.load(true);
      else if (k === "+" || k === "=") this.zoomBy(1.2);
      else if (k === "-" || k === "_") this.zoomBy(1 / 1.2);
      else if (k === "Escape") { this.select(null); this.closeDrawer(); }
    };
    document.addEventListener("keydown", this.onKey);
    this.resizeObs = new ResizeObserver(() => this.drawMini());
    this.resizeObs.observe(this.canvas);
  }

  action(act, btn) {
    if (act === "browse") { this.toggleBrowse(); return; }
    if (act === "zin") this.zoomBy(1.25);
    else if (act === "zout") this.zoomBy(1 / 1.25);
    else if (act === "fit") this.fit();
    else if (act === "focus") this.centerOn(this.rootId);
    else if (act === "reset") this.load(true);
    else if (act === "types") { const p = $("[data-types-panel]", this.root$); p.hidden = !p.hidden; btn.classList.toggle("on", !p.hidden); }
    else if (act === "filters") { this.showFilters = !this.showFilters; btn.classList.toggle("on", this.showFilters); lxPrefs.set("filters", this.showFilters); this.relayout(); }
    else if (act === "svg") this.exportImage("svg");
    else if (act === "png") this.exportImage("png");
    else if (act === "full") {
      if (document.fullscreenElement) document.exitFullscreen?.();
      else this.root$.requestFullscreen?.().then(() => setTimeout(() => this.fit(), 120)).catch(() => toast("Full screen isn't available here.", "bad"));
    }
  }

  wireSearch() {
    const input = $("[data-search]", this.root$);
    const box = $(".lx-results", this.root$);
    let seq = 0, items = [], active = 0;
    const close = () => { box.hidden = true; };
    const paint = () => {
      box.innerHTML = items.length ? items.map((n, i) => `<button class="${i === active ? "active" : ""}" data-i="${i}">
          <span class="t" style="color:${lxType(n.type).color}">${esc(lxType(n.type).glyph)}</span><span class="n">${esc(n.name)}</span>
          <span class="s">${esc(n.properties?.page_name || n.parent_id || lxType(n.type).label)}</span></button>`).join("")
        : `<div class="small muted" style="padding:10px">No matches</div>`;
      box.hidden = false;
      $$("button", box).forEach((b) => (b.onmousedown = (e) => { e.preventDefault(); pickItem(items[Number(b.dataset.i)]); }));
    };
    const pickItem = (n) => { if (!n) return; close(); input.value = ""; input.blur(); this.setRoot(n.id); };
    const search = debounce(async () => {
      const text = input.value.trim();
      if (!text) { close(); return; }
      const mine = ++seq;
      try {
        const res = await GET(`${this.api}/nodes${q({ search: text, limit: 14 })}`);
        if (mine !== seq) return;
        items = res; active = 0; paint();
      } catch (e) { if (!isAbort(e)) toast(e.message, "bad"); }
    }, 160);
    input.oninput = search;
    input.onfocus = () => { if (input.value.trim() && items.length) paint(); };
    input.onblur = () => setTimeout(close, 120);
    input.onkeydown = (e) => {
      if (box.hidden) return;
      if (e.key === "ArrowDown") { active = Math.min(items.length - 1, active + 1); paint(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { active = Math.max(0, active - 1); paint(); e.preventDefault(); }
      else if (e.key === "Enter") { pickItem(items[active]); e.preventDefault(); }
    };
  }

  /* ---------------------------------------------------------- data */
  setRoot(id) {
    this.rootId = id;
    this.freshRoot = true;  // the details drawer follows the new focus object
    this.sel = this.sel2 = null;
    this.load(true);
  }

  reloadSoon() {
    clearTimeout(this.reloadTimer);
    this.reloadTimer = setTimeout(() => this.load(true), 260);
  }

  /* React unmount / hide: release everything global */
  destroy() {
    this.destroyed = true;
    document.removeEventListener("keydown", this.onKey);
    window.removeEventListener("pointermove", this.onMove);
    window.removeEventListener("pointerup", this.onUp);
    window.removeEventListener("pointerup", this.onMiniUp);
    this.resizeObs?.disconnect();
    cancelAnimationFrame(this.anim);
    cancelAnimationFrame(this.viewAnim);
    clearTimeout(this.reloadTimer);
    this.seq++;  // ignore any request still in flight
    this.host.replaceChildren();
  }

  /* the page was navigated away from: drop global listeners */
  alive() {
    if (this.host.isConnected && !this.destroyed) return true;
    document.removeEventListener("keydown", this.onKey);
    window.removeEventListener("pointermove", this.onMove);
    window.removeEventListener("pointerup", this.onUp);
    window.removeEventListener("pointerup", this.onMiniUp);
    this.resizeObs?.disconnect();
    return false;
  }

  async load(fit) {
    if (!this.rootId) return;
    const mine = ++this.seq;
    this.setLoading(true);
    try {
      const g = await GET(`${this.api}/lineage-graph${q({ node_id: this.rootId, up: this.up, down: this.down })}`);
      if (mine !== this.seq) return;
      this.nodes = new Map(g.nodes.map((n) => [n.id, n]));
      this.edges = new Map(g.edges.map((e) => [lxKey(e), e]));
      this.trunc = { up: g.truncated_up, down: g.truncated_down, limit: g.limit_reached };
      if (this.sel && !this.nodes.has(this.sel)) this.sel = this.sel2 = null;
      $(".lx-empty", this.root$)?.remove();
      this.onChange({ node: this.rootId, up: this.up, down: this.down });
      this.renderBrowse();
      this.relayout({ fit, anchor: this.rootId });
      if (this.embedded) this.freshRoot = false;
      else if (this.freshRoot) { this.freshRoot = false; this.openDrawer(this.rootId); }
      else if (this.drawerId && this.nodes.has(this.drawerId)) this.openDrawer(this.drawerId);
      else if (this.drawerId) this.closeDrawer();
    } catch (e) {
      if (mine !== this.seq || isAbort(e)) return;
      if (e.status === 404) {
        const msg = "That object isn't in this model any more — showing another one.";
        if (this.catalog) this.autoFocus(msg); else this.fallback = msg;  // the object list is still loading
        return;
      }
      if (e.status === 409) { $("[data-sync-now]", this.root$) || this.loadCatalog(); return; }
      toast(e.message, "bad");
    } finally {
      if (mine === this.seq) this.setLoading(false);
    }
  }

  async expand(id, dir) {
    const n = this.nodes.get(id);
    if (!n) return;
    this.setLoading(true);
    try {
      const g = await GET(`${this.api}/lineage-graph/expand${q({ node_id: id, direction: dir, steps: 1, level: n.level })}`);
      let added = 0;
      for (const m of g.nodes) {
        if (!this.nodes.has(m.id)) { this.nodes.set(m.id, m); added++; }
      }
      for (const e of g.edges) this.edges.set(lxKey(e), e);
      if (this.hiddenTypes.size && g.nodes.some((m) => m.id !== id && this.hiddenTypes.has(m.type))) {
        toast("Some new objects are of a hidden type — their paths are bridged.", "");
      }
      this.relayout({ anchor: id, spawnFrom: id });
      if (!added) toast("Everything in that direction is already on the canvas.", "");
    } catch (e) {
      if (!isAbort(e)) toast(e.message, "bad");
    } finally {
      this.setLoading(false);
    }
  }

  collapse(id, dir) {
    // drop this node's links in that direction, then everything no longer connected to the focus object
    for (const [k, e] of this.edges) if ((dir === "up" && e.source === id) || (dir === "down" && e.target === id)) this.edges.delete(k);
    const keep = this.connected(this.rootId, [...this.edges.values()]);
    for (const nid of [...this.nodes.keys()]) if (!keep.has(nid)) this.nodes.delete(nid);
    for (const [k, e] of this.edges) if (!this.nodes.has(e.source) || !this.nodes.has(e.target)) this.edges.delete(k);
    if (this.sel && !this.nodes.has(this.sel)) this.sel = this.sel2 = null;
    if (this.sel2 && !this.nodes.has(this.sel2)) this.sel2 = null;
    this.relayout({ anchor: id });
  }

  connected(start, edges) {
    const adj = new Map();
    for (const e of edges) {
      (adj.get(e.source) || adj.set(e.source, []).get(e.source)).push(e.target);
      (adj.get(e.target) || adj.set(e.target, []).get(e.target)).push(e.source);
    }
    const seen = new Set([start]);
    const stack = [start];
    while (stack.length) for (const nxt of adj.get(stack.pop()) || []) if (!seen.has(nxt)) { seen.add(nxt); stack.push(nxt); }
    return seen;
  }

  /* links a node already shows in one direction, out of how many it has */
  shown(id, dir) {
    const s = new Set();
    for (const e of this.edges.values()) {
      if (dir === "up" && e.source === id) s.add(e.target);
      if (dir === "down" && e.target === id) s.add(e.source);
    }
    return s.size;
  }

  /* the graph actually drawn: hidden types bridged over, filter links optional */
  visibleGraph() {
    let edges = [...this.edges.values()].filter((e) => this.showFilters || e.type !== "FILTERED_BY").map((e) => ({ ...e, bridge: false }));
    const hidden = [...this.nodes.values()].filter((n) => n.id !== this.rootId && this.hiddenTypes.has(n.type)).map((n) => n.id);
    for (const h of hidden) {
      const providers = edges.filter((e) => e.source === h).map((e) => e.target);
      const consumers = edges.filter((e) => e.target === h).map((e) => e.source);
      edges = edges.filter((e) => e.source !== h && e.target !== h);
      for (const c of consumers) for (const p of providers) if (c !== p) edges.push({ source: c, target: p, type: "BRIDGE", confidence: "INFERRED", bridge: true });
    }
    const seen = new Map();
    for (const e of edges) seen.set(lxKey(e), e);
    edges = [...seen.values()];
    const keep = this.connected(this.rootId, edges);
    const nodes = [...this.nodes.keys()].filter((id) => keep.has(id) && !hidden.includes(id));
    return { nodes, edges: edges.filter((e) => keep.has(e.source) && keep.has(e.target)) };
  }

  /* --------------------------------------------------------- layout */
  layout(ids, edges) {
    const lv = new Map(ids.map((id) => [id, this.nodes.get(id)?.level ?? 0]));
    lv.set(this.rootId, 0);
    // providers (edge target) must sit left of consumers (edge source)
    for (let pass = 0; pass < 60; pass++) {
      let changed = false;
      for (const e of edges) {
        const p = e.target, c = e.source;
        if (!lv.has(p) || !lv.has(c) || lv.get(c) > lv.get(p)) continue;
        if (p === this.rootId || (c !== this.rootId && lv.get(c) >= 0)) lv.set(c, lv.get(p) + 1);
        else lv.set(p, lv.get(c) - 1);
        changed = true;
      }
      if (!changed) break;
    }
    const levels = [...new Set(lv.values())].sort((a, b) => a - b);
    const cols = levels.map((l) => ids.filter((id) => lv.get(id) === l));
    const name = (id) => String(this.nodes.get(id)?.name || id).toLowerCase();
    cols.forEach((c) => c.sort((a, b) => lxType(this.nodes.get(a)?.type).rank - lxType(this.nodes.get(b)?.type).rank || name(a).localeCompare(name(b))));
    const adj = new Map(ids.map((id) => [id, []]));
    for (const e of edges) { adj.get(e.source)?.push(e.target); adj.get(e.target)?.push(e.source); }
    for (let sweep = 0; sweep < 4; sweep++) {
      const order = sweep % 2 === 0 ? cols.map((_, i) => i) : cols.map((_, i) => cols.length - 1 - i);
      for (const ci of order) {
        const ref = cols[sweep % 2 === 0 ? ci - 1 : ci + 1];
        if (!ref) continue;
        const idx = new Map(ref.map((id, i) => [id, i]));
        const bary = (id) => { const ns = adj.get(id).filter((x) => idx.has(x)); return ns.length ? ns.reduce((s, x) => s + idx.get(x), 0) / ns.length : null; };
        const current = new Map(cols[ci].map((id, i) => [id, i]));
        cols[ci].sort((a, b) => (bary(a) ?? current.get(a)) - (bary(b) ?? current.get(b)));
      }
    }
    const heights = cols.map((c) => c.length * (LX.H + LX.GY) - LX.GY);
    const tallest = Math.max(...heights, LX.H);
    const out = new Map();
    cols.forEach((c, i) => {
      const top = (tallest - heights[i]) / 2;
      c.forEach((id, j) => out.set(id, { x: i * (LX.W + LX.GX), y: top + j * (LX.H + LX.GY) }));
    });
    return out;
  }

  relayout({ fit = false, anchor = null, spawnFrom = null } = {}) {
    const { nodes, edges } = this.visibleGraph();
    this.visible = { nodes, edges };
    const target = this.layout(nodes, edges);
    // keep the anchor (focus / clicked node) where it is on screen: no jumping
    const pin = anchor && this.pos.get(anchor) && target.get(anchor);
    if (pin) {
      const dx = this.pos.get(anchor).x - target.get(anchor).x, dy = this.pos.get(anchor).y - target.get(anchor).y;
      for (const p of target.values()) { p.x += dx; p.y += dy; }
    }
    this.renderNodes(nodes, target, spawnFrom);
    this.renderEdges(edges);
    this.animateTo(target, () => {
      this.applyHighlight();
      this.drawMini();
      if (fit || !pin) this.fit(!fit);
    });
    this.drawLegend();
    this.drawNotes();
  }

  /* --------------------------------------------------------- drawing */
  renderNodes(ids, target, spawnFrom) {
    const live = new Set(ids);
    for (const [id, g] of this.nodeEls) {
      if (!live.has(id)) {
        g.classList.add("leave");
        this.nodeEls.delete(id);
        this.pos.delete(id);
        setTimeout(() => g.remove(), 260);
      }
    }
    for (const id of ids) {
      const n = this.nodes.get(id);
      let g = this.nodeEls.get(id);
      if (!g) {
        g = lxEl("g", { class: "lx-node enter", "data-id": id }, this.gNodes);
        this.nodeEls.set(id, g);
        const from = (spawnFrom && this.pos.get(spawnFrom)) || target.get(id);
        this.pos.set(id, { ...from });
        g.style.transform = `translate(${from.x}px, ${from.y}px)`;
        setTimeout(() => g.classList.remove("enter"), 500);
      }
      this.drawNode(g, n, id);
    }
  }

  drawNode(g, n, id) {
    const t = lxType(n?.type);
    const { W, H } = LX;
    g.replaceChildren();
    g.classList.toggle("root", id === this.rootId);
    g.classList.toggle("missing", n?.type === "MISSING");
    if (id === this.rootId) lxEl("rect", { class: "halo", x: -3, y: -3, width: W + 6, height: H + 6, rx: 14 }, g);
    lxEl("rect", { class: "card", width: W, height: H, rx: 12 }, g);
    lxEl("rect", { class: "stripe", x: 0, y: 12, width: 4, height: H - 24, rx: 2, fill: t.color }, g);
    const badgeG = lxEl("g", { style: `color:${t.color}` }, g);
    lxEl("circle", { class: "badge-bg", cx: 28, cy: H / 2, r: 15, fill: t.color }, badgeG);
    lxText(badgeG, { class: "glyph", x: 28, y: H / 2 + 4, "text-anchor": "middle" }, t.glyph);
    lxText(g, { class: "type", x: 52, y: 19, fill: t.color }, t.label.toUpperCase() + (n?.properties?.calculated ? " · CALC" : ""));
    lxText(g, { class: "name", x: 52, y: 36 }, lxClip(n?.name || id, 25));
    lxText(g, { class: "sub", x: 52, y: 51 }, lxClip(this.subtitle(n), 32));
    if (LX_CONF[n?.confidence] && n.type === "DATA_SOURCE") lxEl("circle", { cx: W - 14, cy: 14, r: 3.5, fill: LX_CONF[n.confidence] }, g);
    for (const dir of ["up", "down"]) {
      const total = dir === "up" ? n?.up_count || 0 : n?.down_count || 0;
      const x = dir === "up" ? 0 : W, y = H / 2;
      if (!total) { lxEl("circle", { cx: x, cy: y, r: 3, fill: "var(--border-strong)" }, g); continue; }
      const hiddenCount = Math.max(0, total - this.shown(id, dir));
      const label = hiddenCount ? `+${hiddenCount > 99 ? "99" : hiddenCount}` : "−";
      const w = hiddenCount ? 16 + label.length * 6 : 22;
      const pill = lxEl("g", { class: `lx-exp ${hiddenCount ? "more" : ""}`, "data-exp": dir, transform: `translate(${x},${y})` }, g);
      if (hiddenCount) lxEl("rect", { class: "ring", x: -w / 2, y: -10, width: w, height: 20, rx: 10 }, pill);
      lxEl("rect", { x: -w / 2, y: -10, width: w, height: 20, rx: 10, fill: "var(--surface)", stroke: hiddenCount ? "var(--accent)" : "var(--border-strong)" }, pill);
      lxText(pill, { x: 0, y: 0.5 }, label);
      const tip = lxEl("title", {}, pill);
      tip.textContent = hiddenCount ? `Show ${hiddenCount} more ${dir === "up" ? "upstream (where this comes from)" : "downstream (what uses this)"}` : `Collapse ${dir === "up" ? "upstream" : "downstream"}`;
    }
  }

  subtitle(n) {
    if (!n) return "";
    const p = n.properties || {};
    if (n.type === "VISUAL") return [p.page_name, p.visual_type].filter(Boolean).join(" · ");
    if (n.type === "COLUMN") return [n.parent_id, p.data_type].filter(Boolean).join(" · ");
    if (n.type === "MEASURE") return n.parent_id ? `in ${n.parent_id}` : "";
    if (n.type === "TABLE") return [p.kind, p.source_ref].filter(Boolean).join(" · ");
    if (n.type === "DATA_SOURCE") return p.upstream_traced ? "source system" : "source · upstream not traced";
    if (n.type === "MISSING") return "renamed or removed from the model";
    return n.parent_id || "";
  }

  renderEdges(edges) {
    const prev = this.edgeKeys || new Set();
    this.edgeKeys = new Set(edges.map(lxKey));
    this.gEdges.replaceChildren();
    this.edgeEls = edges.map((e) => {
      const k = lxKey(e);
      const cls = ["lx-edge", e.confidence === "INFERRED" ? "inferred" : "", e.type === "FILTERED_BY" ? "filter" : "", e.bridge ? "bridge" : "", prev.has(k) ? "" : "enter"].join(" ");
      const path = lxEl("path", { class: cls, "marker-end": "url(#lx-arrow)" }, this.gEdges);
      const title = lxEl("title", {}, path);
      const a = this.nodes.get(e.target)?.name || e.target, b = this.nodes.get(e.source)?.name || e.source;
      title.textContent = `${a} → ${b}\n${e.bridge ? "via hidden objects" : `${String(e.type).toLowerCase().replace("_", " ")} · ${String(e.confidence || "").toLowerCase().replace("_", " ")}`}`;
      if (!prev.has(k)) setTimeout(() => path.classList.remove("enter"), 750);
      return { e, path };
    });
  }

  edgePath(e) {
    const p = this.pos.get(e.target), c = this.pos.get(e.source); // data flows provider -> consumer
    if (!p || !c) return "";
    const { W, H, PORT } = LX;
    const y1 = p.y + H / 2, y2 = c.y + H / 2;
    if (c.x > p.x + 1) {
      const x1 = p.x + W + PORT, x2 = c.x - PORT, mx = (x1 + x2) / 2;
      return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
    }
    const x1 = p.x + W + PORT, x2 = c.x + W + PORT, bulge = Math.max(x1, x2) + 70;
    return `M${x1},${y1} C${bulge},${y1} ${bulge},${y2} ${x2},${y2}`;
  }

  paint() {
    for (const [id, g] of this.nodeEls) { const p = this.pos.get(id); if (p) g.style.transform = `translate(${p.x}px, ${p.y}px)`; }
    for (const { e, path } of this.edgeEls || []) path.setAttribute("d", this.edgePath(e));
  }

  animateTo(target, done) {
    cancelAnimationFrame(this.anim);
    const start = new Map([...target.keys()].map((id) => [id, { ...(this.pos.get(id) || target.get(id)) }]));
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const t0 = performance.now(), dur = reduce ? 0 : 480;
    const ease = (t) => 1 - Math.pow(1 - t, 3);
    const step = (now) => {
      const t = dur ? Math.min(1, (now - t0) / dur) : 1, k = ease(t);
      for (const [id, to] of target) {
        const from = start.get(id);
        this.pos.set(id, { x: from.x + (to.x - from.x) * k, y: from.y + (to.y - from.y) * k });
      }
      this.paint();
      if (t < 1) this.anim = requestAnimationFrame(step); else done?.();
    };
    this.anim = requestAnimationFrame(step);
  }

  /* ------------------------------------------------------ highlighting */
  reach(start, dir) {
    const out = new Set(), stack = [start];
    while (stack.length) {
      const cur = stack.pop();
      for (const { e } of this.edgeEls || []) {
        const nxt = dir === "up" ? (e.source === cur ? e.target : null) : (e.target === cur ? e.source : null);
        if (nxt && !out.has(nxt)) { out.add(nxt); stack.push(nxt); }
      }
    }
    return out;
  }

  select(id, additive = false) {
    if (additive && this.sel && id && id !== this.sel) this.sel2 = this.sel2 === id ? null : id;
    else if (this.sel === id && !this.sel2) this.sel = null;          // second click: lineage line off
    else { this.sel = id; this.sel2 = null; }
    this.applyHighlight();
    if (this.embedded) return;  // the host page shows details on double-click
    if (this.sel2) this.openDrawer(this.sel2);
    else if (this.sel) this.openDrawer(this.sel);
    else this.closeDrawer();
  }

  applyHighlight() {
    let on = null;
    if (this.sel && this.nodeEls.has(this.sel)) {
      const a = this.sel, upA = this.reach(a, "up"), downA = this.reach(a, "down");
      if (this.sel2 && this.nodeEls.has(this.sel2)) {
        const b = this.sel2, upB = this.reach(b, "up"), downB = this.reach(b, "down");
        on = new Set([a, b]);
        for (const x of downA) if (upB.has(x)) on.add(x);
        for (const x of upA) if (downB.has(x)) on.add(x);
        if (!downA.has(b) && !upA.has(b)) { toast("These two objects aren't on the same lineage path.", ""); on = new Set([a, b]); }
      } else on = new Set([a, ...upA, ...downA]);
    }
    for (const [id, g] of this.nodeEls) {
      g.classList.toggle("sel", id === this.sel || id === this.sel2);
      g.classList.toggle("dim", !!on && !on.has(id));
    }
    for (const { e, path } of this.edgeEls || []) {
      const lit = !!on && on.has(e.source) && on.has(e.target) && (this.sel2 ? true : this.onLine(e));
      path.classList.toggle("hl", lit);
      path.classList.toggle("dim", !!on && !lit);
      path.setAttribute("marker-end", lit ? "url(#lx-arrow-hl)" : "url(#lx-arrow)");
      if (lit) this.gEdges.append(path); // draw lit lines on top
    }
  }

  onLine(e) {
    // with one anchor, a link is on its line if it lies on the anchor's upstream or downstream chain
    const a = this.sel;
    if (!this._lineCache || this._lineCache.a !== a || this._lineCache.edges !== this.edgeEls) {
      this._lineCache = { a, edges: this.edgeEls, up: new Set([a, ...this.reach(a, "up")]), down: new Set([a, ...this.reach(a, "down")]) };
    }
    const { up, down } = this._lineCache;
    return (up.has(e.source) && up.has(e.target)) || (down.has(e.source) && down.has(e.target));
  }

  /* --------------------------------------------------------- pan / zoom */
  setView(v, animate = false) {
    const apply = (x, y, k) => { this.view = { x, y, k }; this.vp.setAttribute("transform", `translate(${x},${y}) scale(${k})`); this.drawMini(); };
    if (!animate || matchMedia("(prefers-reduced-motion: reduce)").matches) { apply(v.x, v.y, v.k); return; }
    const from = { ...this.view }, t0 = performance.now(), dur = 380;
    cancelAnimationFrame(this.viewAnim);
    const step = (now) => {
      const t = Math.min(1, (now - t0) / dur), k = 1 - Math.pow(1 - t, 3);
      apply(from.x + (v.x - from.x) * k, from.y + (v.y - from.y) * k, from.k + (v.k - from.k) * k);
      if (t < 1) this.viewAnim = requestAnimationFrame(step);
    };
    this.viewAnim = requestAnimationFrame(step);
  }

  zoomBy(f, cx, cy) {
    const r = this.canvas.getBoundingClientRect();
    cx ??= r.width / 2; cy ??= r.height / 2;
    const k = Math.max(0.15, Math.min(2.5, this.view.k * f));
    const ratio = k / this.view.k;
    this.setView({ x: cx - (cx - this.view.x) * ratio, y: cy - (cy - this.view.y) * ratio, k });
  }

  bounds() {
    const ps = [...this.pos.values()];
    if (!ps.length) return null;
    const xs = ps.map((p) => p.x), ys = ps.map((p) => p.y);
    return { x: Math.min(...xs) - 30, y: Math.min(...ys) - 30, w: Math.max(...xs) - Math.min(...xs) + LX.W + 60, h: Math.max(...ys) - Math.min(...ys) + LX.H + 60 };
  }

  fit(animate = true) {
    const b = this.bounds();
    if (!b) return;
    const r = this.canvas.getBoundingClientRect();
    const { left, right } = this.insets();
    const availW = Math.max(200, r.width - left - right - 40), availH = Math.max(200, r.height - 150);
    const k = Math.max(0.2, Math.min(1.15, availW / b.w, availH / b.h));
    this.setView({ x: left + 20 + (availW - b.w * k) / 2 - b.x * k, y: 80 + (availH - b.h * k) / 2 - b.y * k, k }, animate);
  }

  centerOn(id) {
    const p = id && this.pos.get(id);
    if (!p) return;
    const r = this.canvas.getBoundingClientRect();
    const { left, right } = this.insets();
    const k = Math.max(this.view.k, 0.8);
    this.setView({ x: left + (r.width - left - right) / 2 - (p.x + LX.W / 2) * k, y: r.height / 2 - (p.y + LX.H / 2) * k, k }, true);
  }

  wirePanZoom() {
    let drag = null;
    this.canvas.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = this.canvas.getBoundingClientRect();
      this.zoomBy(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });
    this.canvas.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      drag = { x: e.clientX, y: e.clientY, vx: this.view.x, vy: this.view.y, moved: false, target: e.target };
    });
    // hover card: readable details however far the graph is zoomed out
    const tip = $(".lx-tip", this.root$);
    let tipId = null;
    const hideTip = () => { tip.hidden = true; tipId = null; };
    this.canvas.addEventListener("pointermove", (e) => {
      const node = !drag && e.target.closest?.(".lx-node");
      if (!node) { hideTip(); return; }
      const id = node.dataset.id;
      if (id !== tipId) {
        tipId = id;
        const n = this.nodes.get(id) || { name: id };
        const t = lxType(n.type);
        const sub = this.subtitle(n);
        tip.innerHTML = `<div class="tt" style="color:${t.color}">${esc(t.label)}${id === this.rootId ? " · focus" : ""}</div>
          <div class="tn">${esc(n.name || id)}</div>${sub ? `<div class="ts">${esc(sub)}</div>` : ""}
          <div class="tc"><span>↑ ${n.up_count ?? 0} source${n.up_count === 1 ? "" : "s"}</span><span>↓ ${n.down_count ?? 0} use${n.down_count === 1 ? "" : "s"}</span></div>
          <div class="th">Click: trace line · Double-click: ${this.embedded ? "details" : "focus"}</div>`;
        tip.hidden = false;
      }
      const r = this.root$.getBoundingClientRect();
      const w = tip.offsetWidth, h = tip.offsetHeight;
      let x = e.clientX - r.left + 16, y = e.clientY - r.top + 16;
      if (x + w > r.width - 8) x = e.clientX - r.left - w - 16;
      if (y + h > r.height - 8) y = e.clientY - r.top - h - 16;
      tip.style.transform = `translate(${Math.max(8, x)}px, ${Math.max(8, y)}px)`;
    });
    this.canvas.addEventListener("pointerleave", hideTip);
    this.canvas.addEventListener("pointerdown", hideTip);
    this.onMove = (e) => {
      if (!drag || !this.alive()) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (!drag.moved && Math.hypot(dx, dy) < 4) return;
      if (!drag.moved) { drag.moved = true; this.canvas.classList.add("panning"); this.canvas.setPointerCapture?.(e.pointerId); }
      this.setView({ x: drag.vx + dx, y: drag.vy + dy, k: this.view.k });
    };
    this.onUp = (e) => {
      if (!drag || !this.alive()) { drag = null; return; }
      const d = drag;
      drag = null;
      this.canvas.classList.remove("panning");
      if (d.moved) return;
      const exp = d.target.closest?.(".lx-exp");
      const node = d.target.closest?.(".lx-node");
      if (exp && node) {
        const id = node.dataset.id, dir = exp.dataset.exp;
        const n = this.nodes.get(id);
        const total = dir === "up" ? n.up_count : n.down_count;
        if (this.shown(id, dir) < total) this.expand(id, dir); else this.collapse(id, dir);
      } else if (node) {
        const id = node.dataset.id;
        const now = performance.now();
        if (this.lastClick && this.lastClick.id === id && now - this.lastClick.t < 320) {
          this.lastClick = null;
          if (this.embedded && this.onInspect) {
            if (this.sel !== id) { this.sel = id; this.sel2 = null; this.applyHighlight(); }  // keep its line lit
            this.onInspect(this.nodes.get(id) || { id, name: id, type: "MISSING" });
          } else if (id !== this.rootId) { toast(`Focused on ${this.nodes.get(id)?.name || id}`, "ok"); this.setRoot(id); }
          return;
        }
        this.lastClick = { id, t: now };
        this.select(id, e.shiftKey);
      } else if (!d.target.closest?.(".lx-panel, .lx-drawer, .lx-mini")) {
        if (this.sel) this.select(null);
      }
    };
    window.addEventListener("pointermove", this.onMove);
    window.addEventListener("pointerup", this.onUp);
  }

  /* ------------------------------------------------------------ minimap */
  wireMini() {
    const box = $(".lx-mini", this.root$);
    const go = (e) => {
      const m = this.miniMap;
      if (!m) return;
      const r = box.getBoundingClientRect();
      const gx = (e.clientX - r.left - m.ox) / m.s + m.b.x, gy = (e.clientY - r.top - m.oy) / m.s + m.b.y;
      const c = this.canvas.getBoundingClientRect();
      this.setView({ x: c.width / 2 - gx * this.view.k, y: c.height / 2 - gy * this.view.k, k: this.view.k });
    };
    let down = false;
    box.addEventListener("pointerdown", (e) => { down = true; go(e); e.stopPropagation(); });
    box.addEventListener("pointermove", (e) => { if (down) go(e); });
    this.onMiniUp = () => (down = false);
    window.addEventListener("pointerup", this.onMiniUp);
  }

  drawMini() {
    const b = this.bounds();
    if (!b || !this.mini.isConnected) { this.mini.replaceChildren(); return; }
    const W = 190, H = 120, s = Math.min(W / b.w, H / b.h) * 0.9;
    const ox = (W - b.w * s) / 2, oy = (H - b.h * s) / 2;
    this.miniMap = { b, s, ox, oy };
    this.mini.replaceChildren();
    for (const [id, p] of this.pos) {
      const n = this.nodes.get(id);
      lxEl("rect", { class: "n", x: ox + (p.x - b.x) * s, y: oy + (p.y - b.y) * s, width: Math.max(2, LX.W * s), height: Math.max(2, LX.H * s),
        fill: id === this.rootId ? "var(--accent)" : lxType(n?.type).color, opacity: id === this.rootId ? 1 : 0.7 }, this.mini);
    }
    const c = this.canvas.getBoundingClientRect();
    const vx = (-this.view.x / this.view.k - b.x) * s + ox, vy = (-this.view.y / this.view.k - b.y) * s + oy;
    lxEl("rect", { class: "vp", x: vx, y: vy, width: (c.width / this.view.k) * s, height: (c.height / this.view.k) * s, rx: 3 }, this.mini);
  }

  /* ------------------------------------------------- legend / overlays */
  drawLegend() {
    const n = this.visible?.nodes.length || 0, e = this.visible?.edges.length || 0;
    const counts = {};
    for (const id of this.visible?.nodes || []) { const t = this.nodes.get(id)?.type; counts[t] = (counts[t] || 0) + 1; }
    $("[data-legend]", this.root$).innerHTML = `
      ${LX_FILTERABLE.filter((t) => counts[t]).map((t) => `<span class="lx-legend"><i style="background:${lxType(t).color}"></i>${lxType(t).label} <b>${counts[t]}</b></span>`).join("")}
      <span class="lx-legend"><i class="ln"></i>declared</span><span class="lx-legend"><i class="ln d"></i>inferred</span><span class="lx-legend"><i class="ln f"></i>filter</span>
      <span class="faint">${n} objects · ${e} links</span>
      <span class="lx-help"><kbd>/</kbd> find <kbd>F</kbd> fit <kbd>Esc</kbd> clear</span>`;
  }

  drawNotes() {
    $(".lx-note", this.root$)?.remove();
    const t = this.trunc || {};
    const msg = t.limit ? "Large graph: showing the first 600 objects — lower the step limits or focus on a narrower object."
      : t.up && t.down ? `There's more upstream and downstream — raise the step limits or use + on a node.`
      : t.up ? `More upstream beyond ${this.up} step(s) — raise the limit or use + on a node.`
      : t.down ? `More downstream beyond ${this.down} step(s) — raise the limit or use + on a node.` : "";
    // inside the legend panel, so it can never overlap it
    if (msg) $("[data-legend]", this.root$).insertAdjacentHTML("afterbegin", `<span class="lx-note badge info">${esc(msg)}</span>`);
  }

  setLoading(on) {
    $(".lx-loading", this.root$)?.remove();
    if (on) this.root$.insertAdjacentHTML("beforeend", `<div class="lx-loading">${typeof loading === "function" ? loading("Tracing lineage…") : ""}</div>`);
  }

  async showEmpty() {
    this.root$.insertAdjacentHTML("beforeend", `<div class="lx-empty"><div class="box"><h3>Trace any number end to end</h3>
      <p>Search above, or start from one of these. You'll see where it comes from (left) and everything that uses it (right).</p>
      <div class="lx-quick" data-quick>${typeof loading === "function" ? loading() : ""}</div></div></div>`);
    try {
      const [vis, mea, tab] = await Promise.all(["VISUAL", "MEASURE", "TABLE"].map((type) => GET(`${this.api}/nodes${q({ type, limit: type === "TABLE" ? 4 : 6 })}`)));
      const quick = $("[data-quick]", this.root$);
      if (!quick) return;
      const all = [...vis, ...mea, ...tab];
      quick.innerHTML = all.length ? all.map((n) => `<button data-id="${esc(n.id)}"><i style="background:${lxType(n.type).color}"></i>${esc(lxClip(n.name, 40))}</button>`).join("")
        : `<span class="muted small">Nothing synced yet — use Sync above.</span>`;
      $$("button", quick).forEach((b) => (b.onclick = () => this.setRoot(b.dataset.id)));
    } catch (e) {
      const quick = $("[data-quick]", this.root$);
      if (quick && !isAbort(e)) quick.innerHTML = e.status === 409 ? `<span class="muted small">This connection hasn't been synced yet — click Sync above.</span>` : alertBox("bad", [e.message]);
    }
  }

  /* ------------------------------------------------------------ drawer */
  closeDrawer() {
    this.drawer.classList.remove("open");
    this.root$.classList.remove("drawer-open");
    this.drawerId = null;
  }

  async openDrawer(id) {
    const n = this.nodes.get(id);
    if (!n) return;
    this.drawerId = id;
    const t = lxType(n.type), p = n.properties || {};
    const ups = [...this.edges.values()].filter((e) => e.source === id).map((e) => e.target);
    const downs = [...this.edges.values()].filter((e) => e.target === id).map((e) => e.source);
    const list = (ids) => ids.length ? `<div class="list">${[...new Set(ids)].map((x) => { const m = this.nodes.get(x); return `<button data-jump="${esc(x)}"><i style="background:${lxType(m?.type).color}"></i>${esc(m?.name || x)}</button>`; }).join("")}</div>` : `<div class="small faint">None on the canvas.</div>`;
    const dax = p.dax_expression || p.expression;
    const kv = [["Type", t.label], ["ID", n.id], ["In", n.parent_id], ["Page", p.page_name], ["Visual type", p.visual_type], ["Data type", p.data_type],
      ["Table kind", p.kind], ["Loads from", p.source_ref], ["Folder", p.display_folder]].filter(([, v]) => v);
    // the app's model views; the raw ids here are swapped for readable names when the page opens
    const model = `/dashboards/${encodeURIComponent(this.cid)}/model`;
    const modelLink = n.type === "MEASURE" ? `${model}/measures/${encodeURIComponent(n.id)}`
      : n.type === "TABLE" || n.type === "COLUMN" ? `${model}/tables` : n.type === "VISUAL" ? `${model}/report-map` : "";
    this.drawer.innerHTML = `
      <header><div class="ico" style="background:${t.color}">${esc(t.glyph)}</div>
        <div style="flex:1;min-width:0"><div class="small" style="color:${t.color};font-weight:700;letter-spacing:.05em">${esc(t.label.toUpperCase())}${id === this.rootId ? " · FOCUS" : ""}</div><h3>${esc(n.name)}</h3></div>
        <button class="lx-btn" data-close aria-label="Close details">${lxIcon(LX_I.close)}</button></header>
      <div class="body">
        <div class="actions">
          ${id !== this.rootId ? `<button class="btn sm primary" data-root>Focus here</button>` : ""}
          ${n.up_count > this.shown(id, "up") ? `<button class="btn sm" data-x="up">+ Upstream</button>` : ""}
          ${n.down_count > this.shown(id, "down") ? `<button class="btn sm" data-x="down">+ Downstream</button>` : ""}
          <button class="btn sm" data-hl>${this.sel === id ? "Hide lineage line" : "Show lineage line"}</button>
          <button class="btn sm ghost" data-copy>Copy name</button>
          ${modelLink ? `<a class="btn sm ghost" href="${esc(modelLink)}">Open in model</a>` : ""}
        </div>
        <div class="stats"><div class="stat"><b>${n.up_count}</b><span>direct sources upstream</span></div><div class="stat"><b>${n.down_count}</b><span>direct users downstream</span></div></div>
        <dl class="kvs">${kv.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
        ${dax ? `<div><div class="small muted" style="margin-bottom:4px">${n.type === "MEASURE" ? "DAX" : n.type === "TABLE" ? "Definition" : "Expression"}</div><pre class="mono">${esc(dax)}</pre></div>` : ""}
        ${n.type === "MISSING" ? alertBox("bad", ["This field is referenced by the report or a formula but doesn't exist in the model — it was renamed or removed."]) : ""}
        <div><h3 style="margin-bottom:6px">Comes from</h3>${list(ups)}</div>
        <div><h3 style="margin-bottom:6px">Used by</h3>${list(downs)}</div>
        <div data-impact><h3 style="margin-bottom:6px">If this changes</h3><div class="small muted">${typeof loading === "function" ? loading("Assessing impact…") : ""}</div></div>
      </div>`;
    this.drawer.classList.add("open");
    this.root$.classList.add("drawer-open");
    $("[data-close]", this.drawer).onclick = () => this.closeDrawer();
    $("[data-root]", this.drawer)?.addEventListener("click", () => this.setRoot(id));
    $$("[data-x]", this.drawer).forEach((b) => (b.onclick = () => this.expand(id, b.dataset.x)));
    $("[data-hl]", this.drawer).onclick = () => this.select(id);
    $("[data-copy]", this.drawer).onclick = () => navigator.clipboard?.writeText(n.name).then(() => toast("Copied", "ok"), () => toast("Couldn't copy", "bad"));
    $$("[data-jump]", this.drawer).forEach((b) => (b.onclick = () => { this.select(b.dataset.jump); this.centerOn(b.dataset.jump); }));
    const mine = ++this.drawerSeq;
    try {
      const imp = await GET(`${this.api}/impact${q({ node_id: id })}`);
      if (mine !== this.drawerSeq) return;
      const byType = {};
      for (const a of imp.affected || []) byType[a.type] = (byType[a.type] || 0) + 1;
      const kind = imp.risk === "HIGH" ? "bad" : imp.risk === "MEDIUM" ? "warn" : "ok";
      $("[data-impact]", this.drawer).innerHTML = `<h3 style="margin-bottom:6px">If this changes <span class="badge ${kind}" style="margin-left:6px">${esc(imp.risk)} risk</span></h3>
        <div class="small" style="margin-bottom:8px">${esc(imp.explanation)}</div>
        <div class="chips">${Object.entries(byType).map(([ty, c]) => `<span class="badge neutral"><i style="display:inline-block;width:7px;height:7px;border-radius:50%;background:${lxType(ty).color}"></i>${c} ${esc(lxType(ty).label.toLowerCase())}${c === 1 ? "" : "s"}</span>`).join("") || '<span class="small faint">Nothing depends on it.</span>'}</div>`;
    } catch (e) {
      if (mine === this.drawerSeq && !isAbort(e)) $("[data-impact]", this.drawer).innerHTML = `<div class="small faint">Impact unavailable: ${esc(e.message)}</div>`;
    }
  }

  /* ------------------------------------------------------------ export */
  exportImage(kind) {
    const b = this.bounds();
    if (!b) { toast("Nothing to export yet.", "bad"); return; }
    const clone = this.svg.cloneNode(true);
    const src = [this.svg, ...this.svg.querySelectorAll("*")], dst = [clone, ...clone.querySelectorAll("*")];
    const props = ["fill", "stroke", "stroke-width", "stroke-dasharray", "opacity", "font-family", "font-size", "font-weight", "letter-spacing",
      "filter", "text-anchor", "dominant-baseline"];
    src.forEach((s, i) => {
      const cs = getComputedStyle(s);
      const d = dst[i];
      d.removeAttribute("class");
      d.setAttribute("style", props.map((p) => `${p}:${cs.getPropertyValue(p)}`).join(";") + (s.style.transform ? `;transform:${s.style.transform}` : ""));
      if (d.tagName === "path" && s.getAttribute("marker-end")) d.setAttribute("stroke-dashoffset", "0");
    });
    dst[src.indexOf(this.vp)].removeAttribute("transform"); // export in graph coordinates, not the current pan/zoom
    dst[src.indexOf($(".lx-grid", this.svg))].remove();
    const bg = getComputedStyle(this.root$).getPropertyValue("--surface-2").trim() || "#fff";
    clone.setAttribute("viewBox", `${b.x} ${b.y} ${b.w} ${b.h}`);
    clone.setAttribute("width", b.w);
    clone.setAttribute("height", b.h);
    clone.setAttribute("xmlns", LX_NS);
    clone.querySelector("defs").after(lxEl("rect", { x: b.x, y: b.y, width: b.w, height: b.h, fill: bg }));
    const xml = new XMLSerializer().serializeToString(clone);
    const name = `lineage-${(this.nodes.get(this.rootId)?.name || "graph").replace(/[^\w-]+/g, "_").slice(0, 40)}`;
    const save = (blob, ext) => { const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `${name}.${ext}`; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000); };
    if (kind === "svg") { save(new Blob([xml], { type: "image/svg+xml" }), "svg"); return; }
    const img = new Image();
    const scale = Math.min(2, 8000 / Math.max(b.w, b.h));
    img.onload = () => {
      const c = document.createElement("canvas");
      c.width = b.w * scale; c.height = b.h * scale;
      const ctx = c.getContext("2d");
      ctx.scale(scale, scale);
      ctx.drawImage(img, 0, 0, b.w, b.h);
      c.toBlob((blob) => (blob ? save(blob, "png") : toast("PNG export failed.", "bad")), "image/png");
    };
    img.onerror = () => toast("PNG export failed — try SVG.", "bad");
    img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(xml)}`;
  }
}
