"use client";

/*
 * Readable URLs. Every address is built from names, not internal ids:
 *
 *   /dashboards/sales-dashboard-4                                  dashboard summary
 *   /dashboards/sales-dashboard-4/pages/revenue-overview      a report page
 *   /dashboards/sales-dashboard-4/pages/summary/measures/total-revenue      a measure's lineage (seen from a page)
 *   /dashboards/sales-dashboard-4/measures/total-revenue                 a measure's lineage
 *   /dashboards/sales-dashboard-4/tables/fct                       a table's lineage
 *   /dashboards/sales-dashboard-4/tables/fct/columns/region        a column's lineage
 *   /dashboards/sales-dashboard-4/pages/summary/visuals/revenue-by-month a visual's lineage
 *   /dashboards/sales-dashboard-4/model/relationships              a model view
 *   /results/bi-sales-vs-finance-sales-2                           a reconciliation result
 *   /investigations/why-dont-bi-sales-and-finance-agree-1
 *   /templates/month-end-stock-check-1                            a template's run history
 *
 * Saved records (dashboards, results, investigations, templates) end in
 * their record number, so two dashboards with the same name never clash and
 * a rename never breaks a link. Objects inside a dashboard (pages, measures,
 * tables, columns, visuals) use their name alone; the rare duplicate gets -2,
 * -3. Raw ids are still accepted everywhere and are replaced by the readable
 * form once the page has loaded.
 */

import { usePathname } from "next/navigation";
import { useMemo } from "react";
import { indexOf } from "./overview";
import type { Overview } from "./types";

export function slugify(text: unknown, max = 60): string {
  const s = String(text ?? "")
    .normalize("NFKD").replace(/[̀-ͯ]/g, "")
    .toLowerCase().replace(/['’]/g, "").replace(/&/g, " and ")  // "don't" -> "dont", not "don-t"
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  if (s.length <= max) return s;
  const cut = s.slice(0, max);
  const at = cut.lastIndexOf("-");
  return (at > max / 2 ? cut.slice(0, at) : cut).replace(/-+$/, "");
}

export const safeDecode = (s: string) => { try { return decodeURIComponent(s); } catch { return s; } };

/* ------------------------------------------------------- saved records */
const PREFIX = { dashboard: "CONN", result: "DREC", investigation: "INV", template: "TPL" } as const;
export type RecordKind = keyof typeof PREFIX;

/** "sales-dashboard-4" for CONN0004 named "Sales Dashboard" */
export function recordSlug(kind: RecordKind, id: string, name?: string | null): string {
  const m = /^[A-Za-z]+0*(\d+)$/.exec(id);
  if (!m) return encodeURIComponent(id);
  return `${slugify(name, 48) || kind}-${m[1]}`;
}

/** the record id a URL word points at: "sales-dashboard-4" -> CONN0004 (a raw id passes through) */
export function recordId(kind: RecordKind, slug: string): string {
  const raw = safeDecode(slug);
  if (new RegExp(`^${PREFIX[kind]}\\d+$`, "i").test(raw)) return raw.toUpperCase();
  const m = /(?:^|-)(\d+)$/.exec(raw);
  return m ? `${PREFIX[kind]}${m[1].padStart(4, "0")}` : raw;
}

export const href = {
  home: "/",
  dashboard: (d: Dash) => `/dashboards/${dashSlug(d)}`,
  results: "/results",
  result: (id: string, name?: string | null) => `/results/${recordSlug("result", id, name)}`,
  investigations: (status?: string | null) => `/investigations${status ? `?status=${slugify(status)}` : ""}`,
  investigation: (id: string, question?: string | null) => `/investigations/${recordSlug("investigation", id, question)}`,
  templates: "/templates",
  template: (id: string, name?: string | null) => `/templates/${recordSlug("template", id, name)}`,
  reconcile: "/reconcile",
  sources: "/sources",
  lineage: (d?: Dash | null, focus?: string | null) => (d ? `/lineage/${dashSlug(d)}${focus ? `?focus=${encodeURIComponent(focus)}` : ""}` : "/lineage"),
};

/** investigation status in the URL: ?status=root-cause-found <-> ROOT_CAUSE_FOUND */
export const statusFromUrl = (s: string | null) => (s ? s.replace(/-/g, "_").toUpperCase() : "");

/* ----------------------------------------------------------- dashboards */
export type Dash = { connection_id: string; name?: string | null };
export const dashSlug = (d: Dash) => recordSlug("dashboard", d.connection_id, d.name);

/** names -> URL words for everything inside one dashboard, unique per scope */
type Slugs = { toSlug: Map<string, string>; toId: Map<string, string> };
function uniqueSlugs(items: { id: string; name: string }[], fallback: string): Slugs {
  const toSlug = new Map<string, string>(), toId = new Map<string, string>();
  for (const it of items) {
    const base = slugify(it.name) || fallback;
    let s = base;
    for (let k = 2; toId.has(s); k++) s = `${base}-${k}`;
    toSlug.set(it.id, s);
    toId.set(s, it.id);
  }
  return { toSlug, toId };
}

const slugCache = new WeakMap<Overview, ReturnType<typeof buildSlugs>>();
function buildSlugs(ov: Overview) {
  const pages = uniqueSlugs(ov.pages, "page");
  const measures = uniqueSlugs(ov.measures, "measure");
  const tables = uniqueSlugs(ov.tables.map((t) => ({ id: t.id || t.name, name: t.name })), "table");
  const columns = new Map<string, Slugs>(ov.tables.map((t) => [t.id || t.name, uniqueSlugs(t.columns || [], "column")]));
  const visuals = new Map<string, Slugs>(ov.pages.map((p) => [p.id, uniqueSlugs(p.visuals.map((v) => ({ id: v.id, name: v.title })), "visual")]));
  return { pages, measures, tables, columns, visuals };
}
export function slugsOf(ov: Overview) {
  let s = slugCache.get(ov);
  if (!s) { s = buildSlugs(ov); slugCache.set(ov, s); }
  return s;
}

/** URL path for a place in the dashboard workspace */
export function wsPath(d: Dash, ov?: Overview | null, at: { page?: string | null; object?: string | null } = {}): string {
  let path = `/dashboards/${dashSlug(d)}`;
  let page = at.page || null;
  let rest = "";
  if (at.object) {
    if (!ov) rest = `/objects/${encodeURIComponent(at.object)}`;
    else {
      const idx = indexOf(ov), s = slugsOf(ov), id = at.object;
      const col = idx.columns.get(id);
      const vis = idx.visuals.get(id);
      if (idx.measures.has(id)) rest = `/measures/${s.measures.toSlug.get(id)}`;
      else if (idx.tables.has(id)) rest = `/tables/${s.tables.toSlug.get(id)}`;
      else if (col) {
        const tid = ov.tables.find((t) => t.name === col.table)?.id || col.table;
        rest = `/tables/${s.tables.toSlug.get(tid)}/columns/${s.columns.get(tid)?.toSlug.get(id)}`;
      } else if (vis) { page = vis.page.id; rest = `/visuals/${s.visuals.get(vis.page.id)?.toSlug.get(id)}`; }
      else rest = `/objects/${encodeURIComponent(id)}`;
    }
  }
  if (page) path += `/pages/${ov ? slugsOf(ov).pages.toSlug.get(page) || encodeURIComponent(page) : encodeURIComponent(page)}`;
  return path + rest;
}

export const MODEL_TABS = ["overview", "report-map", "measures", "tables", "relationships", "health-check"] as const;
export type ModelTab = (typeof MODEL_TABS)[number];

/** /dashboards/<d>/model[/<tab>[/<measure>]] */
export function modelPath(d: Dash, tab: ModelTab = "overview", ov?: Overview | null, measure?: string | null): string {
  let p = `/dashboards/${dashSlug(d)}/model`;
  if (tab !== "overview") p += `/${tab}`;
  if (tab === "measures" && measure) p += `/${(ov && slugsOf(ov).measures.toSlug.get(measure)) || encodeURIComponent(measure)}`;
  return p;
}

/* ------------------------------------------------------------- parsing */
export interface WsRoute {
  dash: string;                         // dashboard URL word
  cid: string;                          // its connection id
  page: string | null;                  // page URL word
  object: { kind: "measure" | "table" | "column" | "visual" | "raw"; slug: string; table?: string } | null;
  model: string[] | null;               // segments after /model, when on a model view
}

/** read a /dashboards/... pathname; null anywhere else */
export function parseWsPath(pathname: string): WsRoute | null {
  const seg = pathname.split("/").filter(Boolean).map(safeDecode);
  if (seg[0] !== "dashboards" || !seg[1]) return null;
  const route: WsRoute = { dash: seg[1], cid: recordId("dashboard", seg[1]), page: null, object: null, model: null };
  let rest = seg.slice(2);
  if (rest[0] === "model") { route.model = rest.slice(1); return route; }
  if (rest[0] === "pages" && rest[1]) { route.page = rest[1]; rest = rest.slice(2); }
  const [kind, a, sub, b] = rest;
  if (!a) return route;
  if (kind === "measures") route.object = { kind: "measure", slug: a };
  else if (kind === "tables") route.object = sub === "columns" && b ? { kind: "column", slug: b, table: a } : { kind: "table", slug: a };
  else if (kind === "visuals") route.object = { kind: "visual", slug: a };
  else if (kind === "objects") route.object = { kind: "raw", slug: a };
  return route;
}

/** the page and object ids a parsed route points at within this dashboard */
export function resolveWs(ov: Overview, r: WsRoute): { pageId: string | null; objectId: string | null } {
  const s = slugsOf(ov), idx = indexOf(ov);
  const pick = (map: Slugs | undefined, slug: string, exists: (id: string) => boolean) => map?.toId.get(slug) ?? (exists(slug) ? slug : null);
  let pageId = r.page ? pick(s.pages, r.page, (id) => ov.pages.some((p) => p.id === id)) : null;
  let objectId: string | null = null;
  const o = r.object;
  if (o?.kind === "measure") objectId = pick(s.measures, o.slug, (id) => idx.measures.has(id));
  else if (o?.kind === "table") objectId = pick(s.tables, o.slug, (id) => idx.tables.has(id));
  else if (o?.kind === "column") {
    const tid = pick(s.tables, o.table!, (id) => idx.tables.has(id));
    objectId = tid ? pick(s.columns.get(tid), o.slug, (id) => idx.columns.has(id)) : null;
  } else if (o?.kind === "visual") {
    const pages = pageId ? [pageId] : ov.pages.map((p) => p.id);
    for (const p of pages) { objectId = pick(s.visuals.get(p), o.slug, (id) => idx.visuals.get(id)?.page.id === p); if (objectId) { pageId = p; break; } }
  } else if (o?.kind === "raw") objectId = o.slug;
  return { pageId, objectId };
}

/** the dashboard route of the current URL (null outside /dashboards/...) */
export function useWsRoute(): WsRoute | null {
  const pathname = usePathname();
  return useMemo(() => parseWsPath(pathname), [pathname]);
}

/** lineage explorer address for an object: /lineage/<dashboard>/measures/<m>?up=2&down=2 */
export function lineagePath(d: Dash, ov?: Overview | null, object?: string | null, steps?: { up?: number; down?: number }): string {
  const path = wsPath(d, ov, { object }).replace(/^\/dashboards\//, "/lineage/");
  const q = new URLSearchParams();
  if (steps?.up != null) q.set("up", String(steps.up));
  if (steps?.down != null) q.set("down", String(steps.down));
  return q.size ? `${path}?${q}` : path;
}
