/*
 * Relationship diagram as an SVG string (layered star/snowflake layout:
 * a table's column is the longest chain of many -> one hops leading to it,
 * so fact tables sit left and the dimensions they look up fan out right).
 */
import type { RelationshipRow } from "./types";

const esc = (v: unknown) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));

export function modelDiagram(rels: RelationshipRow[]): string {
  const tables = [...new Set(rels.flatMap((r) => [r.from_table, r.to_table]))];
  const keys = new Map<string, string[]>(tables.map((t) => [t, []]));
  const addKey = (t: string, c: string) => { const k = keys.get(t)!; if (!k.includes(c)) k.push(c); };
  rels.forEach((r) => { addKey(r.from_table, r.from_column); addKey(r.to_table, r.to_column); });
  const level = new Map<string, number>(tables.map((t) => [t, 0]));
  for (let pass = 0; pass < tables.length; pass++) {
    let changed = false;
    for (const r of rels) {
      const d = level.get(r.from_table)! + 1;
      if (d > level.get(r.to_table)! && d < tables.length) { level.set(r.to_table, d); changed = true; }
    }
    if (!changed) break;
  }
  const cols: string[][] = [];
  for (const t of tables) (cols[level.get(t)!] = cols[level.get(t)!] || []).push(t);
  const filled = cols.filter(Boolean);
  const neighbours = (t: string) => rels.flatMap((r) => (r.from_table === t ? [r.to_table] : r.to_table === t ? [r.from_table] : []));
  filled[0].sort();
  for (let sweep = 0; sweep < 2; sweep++) {
    for (let i = 0; i < filled.length; i++) {
      const rank = new Map(filled.flatMap((c) => c.map((t, j) => [t, j] as [string, number])));
      filled[i].sort((a, b) => {
        const avg = (t: string) => { const n = neighbours(t).filter((x) => level.get(x) !== level.get(t)); return n.length ? n.reduce((s, x) => s + rank.get(x)!, 0) / n.length : rank.get(t)!; };
        return avg(a) - avg(b);
      });
    }
  }
  const role = (t: string) => { const many = rels.some((r) => r.from_table === t), one = rels.some((r) => r.to_table === t); return many && !one ? "fact" : one && !many ? "dim" : "mid"; };
  const COLORS: Record<string, string> = { fact: "var(--n-measure)", dim: "var(--n-column)", mid: "var(--n-table)" };
  const W = 228, HEAD = 30, ROW = 19, GX = 120, GY = 22, P = 24, MAXK = 6;
  const height = (t: string) => HEAD + 10 + Math.min(keys.get(t)!.length, MAXK + (keys.get(t)!.length > MAXK ? 1 : 0)) * ROW + 6;
  const colHeights = filled.map((c) => c.reduce((s, t) => s + height(t) + GY, -GY));
  const H = Math.max(...colHeights) + P * 2;
  const pos = new Map<string, { x: number; y: number; h: number }>();
  filled.forEach((c, i) => {
    let y = P + (H - P * 2 - colHeights[i]) / 2;
    c.forEach((t) => { pos.set(t, { x: P + i * (W + GX), y, h: height(t) }); y += height(t) + GY; });
  });
  const Wt = P * 2 + filled.length * (W + GX) - GX;
  const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
  const keyY = (t: string, c: string) => { const i = Math.min(keys.get(t)!.indexOf(c), MAXK); return pos.get(t)!.y + HEAD + 10 + i * ROW + ROW / 2; };
  const edges = rels.map((r) => {
    const a = pos.get(r.from_table)!, b = pos.get(r.to_table)!;
    const y1 = keyY(r.from_table, r.from_column), y2 = keyY(r.to_table, r.to_column);
    let x1: number, x2: number, d: string;
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
    const p = pos.get(t)!, k = keys.get(t)!, r = role(t);
    return `<g class="tbl" data-t="${esc(t)}" transform="translate(${p.x},${p.y})"><title>${esc(t)} (${r === "fact" ? "fact / many side" : r === "dim" ? "lookup / one side" : "both sides"})</title>
      <rect class="box" width="${W}" height="${p.h}" rx="9"></rect>
      <path class="head" d="M0,9 a9,9 0 0 1 9,-9 h${W - 18} a9,9 0 0 1 9,9 v${HEAD - 9} h-${W} z" fill="${COLORS[r]}"></path>
      <text class="title" x="12" y="19.5">${esc(clip(t, 28))}</text>
      ${k.slice(0, MAXK).map((c, i) => `<circle cx="16" cy="${HEAD + 10 + i * ROW + 9}" r="2.6" fill="${COLORS[r]}"></circle><text class="key" x="25" y="${HEAD + 10 + i * ROW + 13}">${esc(clip(c, 28))}</text>`).join("")}
      ${k.length > MAXK ? `<text class="meta" x="14" y="${HEAD + 10 + MAXK * ROW + 13}">+${k.length - MAXK} more key column(s)</text>` : ""}</g>`;
  }).join("");
  return `<svg class="model-svg" width="${Wt}" height="${H}" viewBox="0 0 ${Wt} ${H}" role="img" aria-label="Model relationship diagram">
    <defs><marker id="mk-arrow" viewBox="0 0 10 10" refX="11" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--faint)"></path></marker></defs>
    ${edges}${nodes}</svg>`;
}
