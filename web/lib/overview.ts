import type { MeasureRow, Overview, PageRow } from "./types";

/** measure ids a page's visuals show directly */
export function pageMeasureIds(page: PageRow | null | undefined): Set<string> {
  const ids = new Set<string>();
  for (const v of page?.visuals || []) for (const f of v.fields || []) if (f.type === "MEASURE") ids.add(f.id);
  return ids;
}

export function pageMeasures(ov: Overview, page: PageRow | null | undefined): MeasureRow[] {
  const ids = pageMeasureIds(page);
  return ov.measures.filter((m) => ids.has(m.id));
}

/** lookup tables over an overview, built once per overview object */
const cache = new WeakMap<Overview, ReturnType<typeof build>>();
function build(ov: Overview) {
  const measures = new Map(ov.measures.map((m) => [m.id, m]));
  const byName = new Map(ov.measures.map((m) => [m.name, m]));
  const tables = new Map(ov.tables.map((t) => [t.id || t.name, t]));
  const columns = new Map<string, Overview["tables"][number]["columns"][number] & { table: string; tableKind: string; tableSource: string | null }>();
  for (const t of ov.tables) for (const c of t.columns || []) columns.set(c.id, { ...c, table: t.name, tableKind: t.kind, tableSource: t.source });
  const visuals = new Map<string, Overview["pages"][number]["visuals"][number] & { page: PageRow }>();
  for (const p of ov.pages) for (const v of p.visuals || []) visuals.set(v.id, { ...v, page: p });
  return { measures, byName, tables, columns, visuals };
}
export function indexOf(ov: Overview) {
  let idx = cache.get(ov);
  if (!idx) { idx = build(ov); cache.set(ov, idx); }
  return idx;
}
