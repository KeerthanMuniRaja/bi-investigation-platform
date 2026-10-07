"use client";

import Link from "next/link";
import { AnimatePresence, motion } from "motion/react";
import { useDeferredValue, useMemo, useRef, useState, type MouseEvent } from "react";
import { enc, get, qs } from "@/lib/api";
import { fmt } from "@/lib/format";
import { collapse, fadeUp, softSpring, spring } from "@/lib/motion";
import { modelPath, wsPath } from "@/lib/routes";
import { modelDiagram } from "@/lib/modelDiagram";
import type { Overview, RelationshipRow, TableRow } from "@/lib/types";
import { DaxCode } from "@/components/DaxCode";
import { PreviewTable } from "@/components/DataTable";
import { toast } from "@/components/feedback";
import { AlertBox, Badge, EmptyState, I, Icon, Kpi, Spinner } from "@/components/ui";

const SEV_KIND: Record<string, string> = { high: "bad", medium: "warn", low: "info", info: "neutral" };
const KIND_TEXT: Record<string, string> = { import: "Imported", calculated: "Calculated (DAX)", auto_date: "Auto date", measures: "Measure table", parameter: "Parameter", entered_data: "Entered data", table: "Table" };
const PREVIEWABLE = ["import", "entered_data", "calculated"];
const list = { hidden: {}, show: { transition: { staggerChildren: 0.04 } } };

function LineageLink({ cid, ov, id, label = "Lineage" }: { cid: string; ov: Overview; id: string; label?: string }) {
  return <Link className="btn sm" href={wsPath({ connection_id: cid, name: ov.name }, ov, { object: id })}><Icon d={I.lineage} />{label}</Link>;
}

/* --------------------------------------------------------------- overview */
export function OverviewTab({ cid, ov }: { cid: string; ov: Overview }) {
  const c = ov.counts;
  return (
    <motion.div className="stack" initial="hidden" animate="show" variants={list}>
      <motion.div className="kpis kpis-6" variants={list}>
        <Kpi label="Pages" value={c.pages} /><Kpi label="Visuals" value={c.visuals} />
        <Kpi label="Measures" value={c.measures} hint={`${fmt.int(c.unused_measures)} unused`} />
        <Kpi label="Tables" value={c.tables} hint={`${fmt.int(c.columns)} columns · ${fmt.int(c.calculated_columns)} calculated`} />
        <Kpi label="Relationships" value={c.relationships} />
        <Kpi label="Issues to review" value={c.issues} hint="high / medium" kind={c.issues ? "warn" : "ok"} href={modelPath({ connection_id: cid, name: ov.name }, "health-check")} />
      </motion.div>
      <div className="grid-2">
        <motion.section className="card" variants={fadeUp}>
          <div className="card-head"><div><h2>Where the data comes from</h2><div className="sub">from Power Query</div></div></div>
          <div className="card-body stack">
            {ov.data_sources.length ? ov.data_sources.map((s, i) => (
              <motion.div key={i} className="stack" style={{ gap: 6 }} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...softSpring, delay: 0.1 + i * 0.05 }}>
                <div className="row"><Badge kind="info">{s.kind || "source"}</Badge><b className="small">{[s.server, s.database].filter(Boolean).join(" / ") || s.path || ""}</b></div>
                <div className="table-wrap"><table className="data"><tbody>
                  {s.tables.map((t) => <tr key={t.model_table}><td className="small">{t.model_table}</td><td className="small muted">{t.source_object || "—"}{t.native_query ? " · SQL query" : ""}</td></tr>)}
                </tbody></table></div>
              </motion.div>
            )) : <div className="empty small">No external sources found — tables are calculated or typed into Power BI.</div>}
          </div>
        </motion.section>
        <div className="stack">
          <motion.section className="card" variants={fadeUp}>
            <div className="card-head"><h2>Health summary</h2><span className="spacer" /><Link className="small" href={modelPath({ connection_id: cid, name: ov.name }, "health-check")}>All findings →</Link></div>
            <div className="card-body stack">
              {ov.insights.slice(0, 6).map((i, n) => (
                <div key={n} className="row attn"><Badge kind={SEV_KIND[i.severity]}>{i.severity}</Badge><span className="small">{i.title}</span></div>
              ))}
              {!ov.insights.length ? <div className="muted small">No issues found.</div> : null}
            </div>
          </motion.section>
          <motion.section className="card" variants={fadeUp}>
            <div className="card-head"><h2>Tables by type</h2></div>
            <div className="card-body"><div className="chips">
              {Object.entries(c.tables_by_kind).map(([k, n]) => <Badge key={k}>{KIND_TEXT[k] || k} · {n}</Badge>)}
            </div></div>
          </motion.section>
        </div>
      </div>
    </motion.div>
  );
}

/* ----------------------------------------------------------------- report */
export function ReportTab({ cid, ov }: { cid: string; ov: Overview }) {
  if (!ov.pages.length) return <div className="card"><EmptyState title="No report pages" text="This file holds the model only." icon={I.report} /></div>;
  return (
    <motion.div className="stack" initial="hidden" animate="show" variants={list}>
      {ov.pages.map((p) => (
        <motion.section key={p.id} className="card" variants={fadeUp}>
          <div className="card-head">
            <h2>{p.name}</h2>{p.hidden ? <Badge>hidden</Badge> : null}
            <span className="sub">{p.visuals.length} data visual(s){p.other_visuals ? ` · ${p.other_visuals} other element(s)` : ""}</span>
            <span className="spacer" />
            <Link className="btn sm ghost" href={wsPath({ connection_id: cid, name: ov.name }, ov, { page: p.id })}>Open page →</Link>
          </div>
          <div className="card-body tight">
            {p.visuals.length ? (
              <div className="table-wrap"><table className="data">
                <thead><tr><th>Visual</th><th>Type</th><th>Shows</th><th>Filtered by</th><th /></tr></thead>
                <tbody>{p.visuals.map((v) => (
                  <tr key={v.id}>
                    <td>{v.title}{v.broken.length ? <> <Badge kind="bad">{v.broken.length} broken</Badge></> : null}</td>
                    <td className="small muted">{v.type}</td>
                    <td><div className="chips">{v.fields.map((f) => <Badge key={f.id} kind={f.type === "MISSING" ? "bad" : f.type === "MEASURE" ? "info" : "neutral"} title={f.id}>{f.name}</Badge>)}</div></td>
                    <td><div className="chips">{v.filters.slice(0, 6).map((f) => <Badge key={f.id} kind={f.missing ? "bad" : "neutral"} title={f.id}>{f.name}</Badge>)}
                      {v.filters.length > 6 ? <span className="faint small">+{v.filters.length - 6}</span> : null}</div></td>
                    <td><LineageLink cid={cid} ov={ov} id={v.id} /></td>
                  </tr>
                ))}</tbody>
              </table></div>
            ) : <div className="empty small">No data visuals on this page.</div>}
          </div>
        </motion.section>
      ))}
    </motion.div>
  );
}

/* --------------------------------------------------------------- measures */
/** the selected measure lives in the URL (/model/measures/<measure>), so it can be shared */
export function MeasuresTab({ cid, ov, sel, setSel }: { cid: string; ov: Overview; sel: string | null; setSel: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("");
  const [unused, setUnused] = useState(false);
  const needle = useDeferredValue(q).toLowerCase();
  const cats = useMemo(() => [...new Set(ov.measures.map((m) => m.category))].sort(), [ov]);
  const rows = ov.measures.filter((m) => (!needle || m.name.toLowerCase().includes(needle) || (m.expression || "").toLowerCase().includes(needle))
    && (!cat || m.category === cat) && (!unused || m.unused));
  const m = ov.measures.find((x) => x.id === sel);
  const visualTitle = useMemo(() => {
    const t: Record<string, string> = {};
    ov.pages.forEach((p) => p.visuals.forEach((v) => (t[v.id] = `${p.name} › ${v.title}`)));
    return t;
  }, [ov]);
  return (
    <div className="grid-2 measures-split">
      <motion.section className="card" variants={fadeUp} initial="hidden" animate="show">
        <div className="card-head" style={{ gap: 8 }}>
          <input type="search" placeholder="Search measures or DAX…" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: 1 }} />
          <select value={cat} onChange={(e) => setCat(e.target.value)} aria-label="Category"><option value="">All types</option>{cats.map((x) => <option key={x} value={x}>{x}</option>)}</select>
          <label className="check small"><input type="checkbox" checked={unused} onChange={(e) => setUnused(e.target.checked)} /> Unused</label>
        </div>
        <div className="card-body tight" style={{ maxHeight: 680, overflow: "auto" }}>
          {rows.length ? (
            <table className="data"><tbody>
              {rows.map((x) => (
                <tr key={x.id} className={`clickable ${x.id === sel ? "selected" : ""}`} onClick={() => setSel(x.id)}>
                  <td><div>{x.name}{x.unresolved.length ? <> <Badge kind="bad">broken ref</Badge></> : null}</div><div className="faint small">{x.folder ? `${x.folder} · ` : ""}{x.category}</div></td>
                  <td className="num small">{x.unused ? <Badge kind="warn">unused</Badge> : <span className="muted">{x.used_by_visuals} visual{x.used_by_visuals === 1 ? "" : "s"}</span>}</td>
                </tr>
              ))}
            </tbody></table>
          ) : <div className="empty small">No measures match.</div>}
        </div>
      </motion.section>
      <AnimatePresence mode="wait">
        {m ? (
          <motion.section key={m.id} className="card" initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -10 }} transition={softSpring}>
            <div className="card-head">
              <div><h2>{m.name}</h2><div className="sub">{m.table}{m.folder ? ` · ${m.folder}` : ""} · {m.category}</div></div>
              <span className="spacer" /><LineageLink cid={cid} ov={ov} id={m.id} label="Lineage & details" />
            </div>
            <div className="card-body stack">
              <div className="ins-explain"><b>In plain English:</b> {m.explanation}</div>
              {m.description ? <div className="small"><b>Description:</b> {m.description}</div> : null}
              {m.unresolved.length ? <AlertBox kind="bad" items={[`References that don't exist in the model: ${m.unresolved.join(", ")}`]} /> : null}
              <DaxCode code={m.expression} label="DAX" />
              <div className="grid-2">
                <div><h3>Depends on</h3><div className="stack small" style={{ gap: 4, marginTop: 6 }}>
                  {m.depends_on_measures.map((n) => { const t = ov.measures.find((x) => x.name === n); return <a key={n} href="#" onClick={(e) => { e.preventDefault(); if (t) setSel(t.id); }}>[{n}]</a>; })}
                  {m.depends_on_columns.map((x) => <span key={x}>{x}</span>)}{m.depends_on_tables.map((x) => <span key={x}>table {x}</span>)}
                  {!m.depends_on_measures.length && !m.depends_on_columns.length && !m.depends_on_tables.length ? <span className="muted">Nothing (constant or unrecognised references)</span> : null}
                </div></div>
                <div><h3>Used by</h3><div className="stack small" style={{ gap: 4, marginTop: 6 }}>
                  {m.visual_ids.map((v) => <span key={v}>{visualTitle[v] || v}</span>)}
                  {!m.visual_ids.length ? <span className="muted">No visuals in this report</span> : null}
                  {m.used_by_measures ? <span className="muted">+ {m.used_by_measures} other measure(s)</span> : null}
                </div></div>
              </div>
            </div>
          </motion.section>
        ) : (
          <motion.section key="empty" className="card" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <EmptyState title="Select a measure" text="See what it calculates in plain English, what it depends on and which visuals use it." icon={I.sigma} />
          </motion.section>
        )}
      </AnimatePresence>
    </div>
  );
}

/* ----------------------------------------------------------------- tables */
export function TablesTab({ cid, ov }: { cid: string; ov: Overview }) {
  const [showAuto, setShowAuto] = useState(false);
  const tables = ov.tables.filter((t) => showAuto || t.kind !== "auto_date");
  return (
    <motion.div className="stack" initial="hidden" animate="show" variants={list}>
      <div className="row"><span className="muted small">{tables.length} table(s)</span><span className="spacer" />
        <button className="btn ghost sm" onClick={() => setShowAuto(!showAuto)}>{showAuto ? "Hide" : "Show"} auto date tables</button></div>
      {tables.map((t) => <TableCard key={t.id} cid={cid} ov={ov} t={t} pbix={ov.format === "pbix"} />)}
    </motion.div>
  );
}

function TableCard({ cid, ov, t, pbix }: { cid: string; ov: Overview; t: TableRow; pbix: boolean }) {
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<{ columns: string[]; rows: Record<string, unknown>[] } | null>(null);
  const [loading, setLoading] = useState(false);
  async function loadPreview() {
    setLoading(true);
    try { setPreview(await get(`/connections/${enc(cid)}/table-data${qs({ table: t.name, limit: 50 })}`)); }
    catch (e) { toast((e as Error).message, "bad"); } finally { setLoading(false); }
  }
  return (
    <motion.section className={`card table-card ${open ? "open" : ""}`} variants={fadeUp} layout="position">
      <button className="card-head table-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <motion.span animate={{ rotate: open ? 90 : 0 }} transition={spring} className="chev-wrap"><Icon d={I.chev} /></motion.span>
        <b>{t.name}</b>
        <Badge kind={t.kind === "import" ? "info" : "neutral"}>{KIND_TEXT[t.kind] || t.kind}</Badge>
        {t.hidden ? <Badge>hidden</Badge> : null}{!t.used ? <Badge kind="warn">unused</Badge> : null}
        <span className="sub">{t.rows != null ? `${fmt.int(t.rows)} rows · ` : ""}{t.column_count} columns{t.measure_count ? ` · ${t.measure_count} measures` : ""}</span>
        <span className="spacer" /><span className="small muted table-src">{t.source || ""}</span>
      </button>
      <AnimatePresence initial={false}>
        {open ? (
          <motion.div initial="closed" animate="open" exit="closed" variants={collapse} style={{ overflow: "hidden" }}>
            <div className="card-body stack">
              {t.summary ? <div className="ins-explain"><b>How it&apos;s loaded:</b> {t.summary}</div> : null}
              {t.depends_on_tables.length ? <div className="small"><b>Built from:</b> {t.depends_on_tables.join(", ")}</div> : null}
              {t.steps.length ? (
                <details className="adv"><summary>Power Query steps ({t.steps.length})</summary>
                  <ol className="ins-steps">{t.steps.map((s, i) => <li key={i}><b>{s.name}</b><span>{s.description}</span></li>)}</ol></details>
              ) : null}
              {t.expression ? <details className="adv"><summary>{t.kind === "calculated" || t.kind === "parameter" ? "DAX definition" : "M query"}</summary><DaxCode code={t.expression} label={t.kind === "calculated" || t.kind === "parameter" ? "DAX" : "Power Query (M)"} /></details> : null}
              <div className="table-wrap"><table className="data">
                <thead><tr><th>Column</th><th>Type</th><th>Status</th><th /></tr></thead>
                <tbody>{t.columns.map((col) => (
                  <tr key={col.id}>
                    <td>{col.name}{col.calculated ? <div className="faint small" title={col.expression || ""}>= {col.explanation || col.expression}</div> : null}</td>
                    <td className="small muted">{col.data_type}</td>
                    <td><div className="chips">{col.calculated ? <Badge kind="info">calculated</Badge> : null}{col.hidden ? <Badge>hidden</Badge> : null}{!col.used ? <Badge kind="warn">unused</Badge> : null}</div></td>
                    <td><LineageLink cid={cid} ov={ov} id={col.id} label="Impact" /></td>
                  </tr>
                ))}</tbody>
              </table></div>
              {pbix && PREVIEWABLE.includes(t.kind) ? (
                <div className="stack">
                  <div><button className="btn sm" onClick={loadPreview} disabled={loading}>{loading ? <Spinner /> : null}Preview data</button></div>
                  {preview ? <><div className="small muted">First {preview.rows.length} rows</div><PreviewTable columns={preview.columns} rows={preview.rows} max={50} /></> : null}
                </div>
              ) : null}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </motion.section>
  );
}

/* ---------------------------------------------------------- relationships */
export function RelationshipsTab({ ov }: { ov: Overview }) {
  const rels = ov.relationships;
  const [q, setQ] = useState("");
  const [show, setShow] = useState("");
  const [focus, setFocus] = useState<string | null>(null);
  const canvas = useRef<HTMLDivElement>(null);
  const svg = useMemo(() => (rels.length ? modelDiagram(rels) : ""), [rels]);
  if (!rels.length) return <div className="card"><EmptyState title="No relationships in this model" text="Every table stands alone, so visuals can only combine fields from one table (or through DAX)." icon={I.rel} /></div>;

  const isolated = ov.tables.filter((t) => ["import", "calculated"].includes(t.kind) && !t.has_relationships && t.used).map((t) => t.name);
  const counts = { inactive: rels.filter((r) => !r.active).length, both: rels.filter((r) => r.direction === "both").length,
    m2m: rels.filter((r) => r.cardinality === "many-to-many").length, problems: rels.filter((r) => r.type_mismatch || r.missing.length).length };
  const needle = q.toLowerCase();
  const rows = rels.filter((r) => (!needle || `${r.from} ${r.to}`.toLowerCase().includes(needle))
    && (!focus || r.from_table === focus || r.to_table === focus)
    && (!show || (show === "issues" ? r.missing.length || r.type_mismatch || r.cardinality === "many-to-many" : show === "inactive" ? !r.active : r.direction === "both")));

  const onCanvasClick = (e: MouseEvent) => {
    const g = (e.target as Element).closest<SVGGElement>(".tbl");
    if (!g) return;
    const t = g.dataset.t!;
    const next = focus === t ? null : t;
    setFocus(next);
    const root = canvas.current!;
    const linked = new Set(next ? rels.filter((r) => r.from_table === next || r.to_table === next).flatMap((r) => [r.from_table, r.to_table]) : []);
    root.querySelectorAll<SVGGElement>(".tbl").forEach((x) => { x.classList.toggle("focus", x.dataset.t === next); x.classList.toggle("dim", !!next && !linked.has(x.dataset.t!)); });
    root.querySelectorAll<SVGGElement>(".rel").forEach((x) => { const on = !!next && (x.dataset.a === next || x.dataset.b === next); x.classList.toggle("focus", on); x.classList.toggle("dim", !!next && !on); });
  };
  const card = (r: RelationshipRow) => r.cardinality === "many-to-many" ? ["*", "*", "Many-to-many", "bad"] : r.cardinality === "one-to-one" ? ["1", "1", "One-to-one", "info"] : ["*", "1", "Many-to-one", "neutral"];

  return (
    <motion.div className="stack" initial="hidden" animate="show" variants={list}>
      <motion.div className="kpis" variants={list}>
        <Kpi label="Relationships" value={rels.length} hint={`${fmt.int(rels.length - counts.inactive)} active · ${fmt.int(counts.inactive)} inactive`} />
        <Kpi label="Bi-directional" value={counts.both} hint="filters flow both ways" kind={counts.both ? "warn" : "ok"} />
        <Kpi label="Many-to-many" value={counts.m2m} hint="can double count" kind={counts.m2m ? "bad" : "ok"} />
        <Kpi label="Key problems" value={counts.problems} hint="type mismatch / missing column" kind={counts.problems ? "bad" : "ok"} />
        <Kpi label="Unrelated tables" value={isolated.length} hint="used tables with no relationship" kind={isolated.length ? "warn" : "ok"} />
      </motion.div>
      <motion.section className="card" variants={fadeUp}>
        <div className="card-head">
          <div><h2>Model diagram</h2><div className="sub">Fact tables on the left, the tables they look up on the right. Arrows show which way filters flow. Click a table to focus on it.</div></div>
          <span className="spacer" />
          <div className="legend"><span><i className="line" />single</span><span><i className="line" style={{ borderColor: "var(--warn)" }} />both ways</span><span><i className="line dashed" />inactive</span><span><i className="line" style={{ borderColor: "var(--bad)" }} />many-to-many</span></div>
        </div>
        <div className="model-canvas" ref={canvas} onClick={onCanvasClick} dangerouslySetInnerHTML={{ __html: svg }} />
      </motion.section>
      {isolated.length ? <AlertBox kind="warn" items={[`${isolated.length} data table(s) aren't related to anything: ${isolated.join(", ")}. Visuals that mix them with other tables won't filter as expected.`]} /> : null}
      <motion.section className="card" variants={fadeUp}>
        <div className="card-head" style={{ gap: 8 }}>
          <h2>All relationships</h2>{focus ? <button className="btn sm" onClick={() => setFocus(null)}>Showing {focus} ✕</button> : null}<span className="spacer" />
          <input type="search" placeholder="Search tables or columns…" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 240 }} />
          <select value={show} onChange={(e) => setShow(e.target.value)} aria-label="Show"><option value="">All</option><option value="issues">Needs attention</option><option value="inactive">Inactive</option><option value="both">Bi-directional</option></select>
        </div>
        <div className="card-body tight">
          {rows.length ? (
            <div className="table-wrap"><table className="data">
              <thead><tr><th>From · many side</th><th style={{ textAlign: "center" }}>Join</th><th>To · one side</th><th>Cardinality</th><th>Filter direction</th><th>Status</th><th>Checks</th></tr></thead>
              <tbody>{rows.map((r) => {
                const [a, b, label, kind] = card(r);
                return (
                  <motion.tr key={r.id} layout initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                    <td><div className="cell-title">{r.from_table}</div><div className="cell-sub mono">{r.from_column}{r.from_type ? ` · ${r.from_type}` : ""}</div></td>
                    <td><div className="rel-line" title={r.direction === "both" ? "Filters flow both ways" : `Filters flow from ${r.to_table} to ${r.from_table}`}><span>{a}</span>
                      <span className={`bar ${r.active ? "" : "dashed"} ${r.direction === "both" ? "both" : ""}`} style={{ transform: "scaleX(-1)" }} /><span>{b}</span></div></td>
                    <td><div className="cell-title">{r.to_table}</div><div className="cell-sub mono">{r.to_column}{r.to_type ? ` · ${r.to_type}` : ""}</div></td>
                    <td><Badge kind={kind}>{label}</Badge></td>
                    <td>{r.direction === "both" ? <Badge kind="warn">Both ways</Badge> : <Badge>Single</Badge>}</td>
                    <td>{r.active ? <Badge kind="ok">Active</Badge> : <Badge title="Only used where a measure calls USERELATIONSHIP">Inactive</Badge>}</td>
                    <td><div className="chips">{r.missing.length ? <Badge kind="bad">Missing column</Badge> : null}{r.type_mismatch ? <Badge kind="bad" title={`${r.from_type} vs ${r.to_type}`}>Type mismatch</Badge> : null}
                      {!r.missing.length && !r.type_mismatch ? <span className="faint small ok-check"><Icon d={I.check} size={14} /> OK</span> : null}</div></td>
                  </motion.tr>
                );
              })}</tbody>
            </table></div>
          ) : <EmptyState title="No relationships match" text={focus ? `Filtered to ${focus}.` : ""} icon={I.rel} />}
        </div>
      </motion.section>
    </motion.div>
  );
}

/* ----------------------------------------------------------------- health */
export function HealthTab({ ov }: { ov: Overview }) {
  const [openSet, setOpenSet] = useState<Set<number>>(() => new Set(ov.insights.map((i, n) => (i.severity === "high" ? n : -1)).filter((n) => n >= 0)));
  if (!ov.insights.length) return <div className="card"><EmptyState title="No issues found" text="The model passed every check." icon={I.check} /></div>;
  const bySev = ["high", "medium", "low", "info"].map((s) => [s, ov.insights.filter((i) => i.severity === s).length] as const);
  return (
    <motion.div className="stack" initial="hidden" animate="show" variants={list}>
      <motion.div className="kpis" variants={list}>
        {bySev.map(([s, n]) => <Kpi key={s} label={s} value={n} kind={n ? (s === "high" ? "bad" : s === "medium" ? "warn" : "") : "ok"} />)}
      </motion.div>
      {ov.insights.map((i, n) => {
        const open = openSet.has(n);
        return (
          <motion.section key={n} className={`card insight sev-${i.severity}`} variants={fadeUp}>
            <div className="card-head"><Badge kind={SEV_KIND[i.severity]}>{i.severity}</Badge><h2>{i.title}</h2><span className="spacer" /><span className="faint small">{i.category}</span></div>
            <div className="card-body stack">
              <div className="small">{i.detail}</div>
              {i.items.length ? (
                <>
                  <button className="btn ghost sm" style={{ alignSelf: "flex-start" }} onClick={() => setOpenSet((s) => { const x = new Set(s); if (x.has(n)) x.delete(n); else x.add(n); return x; })}>
                    <motion.span animate={{ rotate: open ? 90 : 0 }} transition={spring} className="chev-wrap"><Icon d={I.chev} /></motion.span>{i.count} item(s)
                  </button>
                  <AnimatePresence initial={false}>
                    {open ? (
                      <motion.ul className="small insight-items" initial="closed" animate="open" exit="closed" variants={collapse}>
                        {i.items.map((x, k) => <li key={k}>{x}</li>)}
                      </motion.ul>
                    ) : null}
                  </AnimatePresence>
                </>
              ) : null}
            </div>
          </motion.section>
        );
      })}
    </motion.div>
  );
}
