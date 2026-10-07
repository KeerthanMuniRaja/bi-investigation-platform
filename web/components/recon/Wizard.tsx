"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { AnimatePresence, LayoutGroup, motion } from "motion/react";
import { useEffect, useState } from "react";
import { enc, get, post, qs, refresh, uploadFiles, useApi } from "@/lib/api";
import { fmt } from "@/lib/format";
import { collapse, fadeUp, softSpring, spring } from "@/lib/motion";
import { href } from "@/lib/routes";
import { PreviewTable } from "@/components/DataTable";
import { FileDropzone } from "@/components/UploadZone";
import { toast } from "@/components/feedback";
import { useCrumbs } from "@/components/shell/Crumbs";
import { AlertBox, EmptyState, I, Icon, Loading, PageHead, Spinner, Stagger } from "@/components/ui";
import type { FileRecord } from "@/lib/types";
import { SideLabel, type ColumnPair, type ReconConfig } from "./bits";

type Side = "left" | "right";
interface Dataset {
  dataset_id: string; name: string; row_count: number; columns: { name: string }[]; platform?: string | null;
  header_row?: number | null; warnings: string[]; created_at?: string; preview?: Record<string, unknown>[];
}
interface Suggestion {
  config: ReconConfig; warnings: string[];
  key_columns: { left: string; right: string; score: number; reason: string }[];
  value_columns: { left: string; right: string; score: number; reason: string }[];
}
const SIDE_TEXT = {
  left: { title: "Source to check", hint: "Usually the BI dashboard visual or tab" },
  right: { title: "Reference", hint: "Usually the finance / source Excel" },
};
const pickId = <T,>(items: T[] | undefined, id: string | null, key: keyof T) =>
  items?.some((i) => i[key] === id) ? id : ((items?.[0]?.[key] as string | undefined) ?? null);

/* wizard state survives leaving the page and coming back (module scope) */
let saved: { left: Dataset | null; right: Dataset | null; config: ReconConfig | null; suggestion: Suggestion | null } = { left: null, right: null, config: null, suggestion: null };

export function Wizard() {
  useCrumbs([{ label: "Reconcile" }, { label: "New reconciliation" }]);
  const router = useRouter();
  const [left, setLeft] = useState<Dataset | null>(saved.left);
  const [right, setRight] = useState<Dataset | null>(saved.right);
  const [config, setConfig] = useState<ReconConfig | null>(saved.config);
  const [suggestion, setSuggestion] = useState<Suggestion | null>(saved.suggestion);
  const [suggestErr, setSuggestErr] = useState<{ at: string; msg: string } | null>(null);
  const [epoch, setEpoch] = useState(0);
  useEffect(() => { saved = { left, right, config, suggestion }; }, [left, right, config, suggestion]);

  const pair = left && right ? `${left.dataset_id}|${right.dataset_id}` : null;
  useEffect(() => {
    if (!pair || config) return;
    let live = true;
    post<Suggestion>("/recon/suggest", { left_dataset_id: left!.dataset_id, right_dataset_id: right!.dataset_id })
      .then((s) => { if (live) { setSuggestion(s); setConfig(s.config); } })
      .catch((e) => { if (live) setSuggestErr({ at: `${pair}:${epoch}`, msg: (e as Error).message }); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pair, config, epoch]);

  const choose = (side: Side, ds: Dataset | null) => {
    (side === "left" ? setLeft : setRight)(ds);
    setConfig(null);
    setSuggestion(null);
  };
  const reset = () => { setLeft(null); setRight(null); setConfig(null); setSuggestion(null); setEpoch((e) => e + 1); };
  const s1 = !!left, s2 = !!right, s3 = !!config;
  const steps: [string, boolean, boolean][] = [["Source A", s1, !s1], ["Source B", s2, s1 && !s2], ["Match columns", s3 && s1 && s2, s1 && s2 && !s3], ["Run", false, s1 && s2 && s3]];

  return (
    <Stagger>
      <PageHead title="New reconciliation" text="Pick the two things that should agree. Columns are matched for you — review the mapping, then run."
        actions={<button className="btn ghost" onClick={reset}><Icon d={I.reload} />Start over</button>} />
      <motion.div className="steps" variants={fadeUp}>
        <LayoutGroup id="wiz-steps">
          {steps.map(([text, done, current], i) => (
            <span key={text} className="step-wrap">
              {i ? <span className="step-sep">›</span> : null}
              <motion.div className={`step-pill ${done ? "done" : current ? "current" : ""}`} layout transition={spring}>
                {current ? <motion.span layoutId="step-glow" className="step-glow" transition={spring} /> : null}
                <span className="n">
                  <AnimatePresence mode="wait" initial={false}>
                    <motion.span key={done ? "d" : "n"} initial={{ scale: 0.4, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.4, opacity: 0 }}>{done ? "✓" : i + 1}</motion.span>
                  </AnimatePresence>
                </span>{text}
              </motion.div>
            </span>
          ))}
        </LayoutGroup>
      </motion.div>
      <div className="grid-2">
        <SideCard key={`l${epoch}`} side="left" ds={left} onChoose={(d) => choose("left", d)} />
        <SideCard key={`r${epoch}`} side="right" ds={right} onChoose={(d) => choose("right", d)} />
      </div>
      <motion.section className="card" variants={fadeUp} layout="position">
        {!(left && right) ? (
          <div className="card-head"><h2>Match columns</h2><span className="sub">Choose both sides first — columns will be matched automatically.</span></div>
        ) : !config ? (
          <><div className="card-head"><h2>Match columns</h2></div><div className="card-body">{suggestErr?.at === `${pair}:${epoch}` ? <AlertBox kind="bad" items={[suggestErr.msg]} /> : <Loading text="Matching columns…" />}</div></>
        ) : (
          <Mapping left={left} right={right} cfg={config} suggestion={suggestion} onChange={setConfig} onResuggest={() => { setConfig(null); setEpoch((e) => e + 1); }} />
        )}
      </motion.section>
      <AnimatePresence>
        {left && right && config ? (
          <RunBox key={pair} left={left} right={right} config={config} onDone={(rid, name) => { refresh("/recon"); refresh("/investigations"); router.push(href.result(rid, name)); }} />
        ) : null}
      </AnimatePresence>
    </Stagger>
  );
}

/* ================================================================ sides */
function SideCard({ side, ds, onChoose }: { side: Side; ds: Dataset | null; onChoose: (d: Dataset | null) => void }) {
  const t = SIDE_TEXT[side];
  const [tab, setTab] = useState<"files" | "bi" | "dataset">("files");
  async function use(summary: Dataset) {
    const full = await get<{ rows: Record<string, unknown>[] }>(`/datasets/${enc(summary.dataset_id)}?limit=8`);
    onChoose({ ...summary, preview: full.rows });
  }
  return (
    <motion.section className={`card side-card ${ds ? "chosen" : ""}`} variants={fadeUp} layout="position">
      <div className="card-head"><SideLabel side={side} /><div><h2>{t.title}</h2><div className="sub">{t.hint}</div></div></div>
      <AnimatePresence mode="wait" initial={false}>
        {ds ? (
          <motion.div key="ds" className="card-body dataset-card" initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }} transition={softSpring}>
            <div className="row"><strong>{ds.name}</strong><span className="spacer" /><button className="btn sm" onClick={() => onChoose(null)}>Change</button></div>
            <div className="dataset-meta"><span>{fmt.int(ds.row_count)} rows</span><span>{ds.columns.length} columns</span>{ds.platform ? <span>{ds.platform}</span> : null}{ds.header_row != null ? <span>header on row {ds.header_row + 1}</span> : null}</div>
            <AlertBox kind="warn" items={ds.warnings} />
            <PreviewTable columns={ds.columns.map((c) => c.name)} rows={ds.preview || []} />
          </motion.div>
        ) : (
          <motion.div key="pick" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <LayoutGroup id={`side-tabs-${side}`}>
              <div className="tabs">
                {([["files", "Excel / CSV file"], ["bi", "BI connection"], ["dataset", "Saved dataset"]] as const).map(([id, label]) => (
                  <button key={id} className={`tab ${tab === id ? "active" : ""}`} onClick={() => setTab(id)}>
                    {label}{tab === id ? <motion.span layoutId={`side-ul-${side}`} className="tab-underline" transition={spring} /> : null}
                  </button>
                ))}
              </div>
            </LayoutGroup>
            <div className="card-body">
              <AnimatePresence mode="wait" initial={false}>
                <motion.div key={tab} initial={{ opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -8 }} transition={{ duration: 0.16 }}>
                  {tab === "files" ? <FilePicker onUse={use} /> : tab === "bi" ? <BIPicker onUse={use} /> : <DatasetPicker onUse={use} />}
                </motion.div>
              </AnimatePresence>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.section>
  );
}

function UseButton({ label, onClick, disabled, primary = true }: { label: string; onClick: () => Promise<unknown>; disabled?: boolean; primary?: boolean }) {
  const [busy, setBusy] = useState(false);
  return (
    <button className={`btn ${primary ? "primary" : ""}`} disabled={busy || disabled} onClick={async () => {
      setBusy(true);
      try { await onClick(); } catch (e) { toast((e as Error).message, "bad"); } finally { setBusy(false); }
    }}>{busy ? <Spinner /> : null}{label}</button>
  );
}

/* ---- file: upload or choose, pick the tab, confirm the header row */
interface Preview { raw: unknown[][]; header_row: number; detected_header_row: number; warnings: string[]; row_count: number; columns: string[] }
function FilePicker({ onUse }: { onUse: (d: Dataset) => Promise<void> }) {
  const { data: all, mutate } = useApi<FileRecord[]>("/files");
  const files = all?.filter((f) => f.kind !== "model");
  const [fileId, setFileId] = useState<string | null>(null);
  const [sheet, setSheet] = useState<string | null>(null);
  const [header, setHeader] = useState<number | null>(null);
  const file = files?.find((f) => f.file_id === fileId);
  const sheetName = sheet || file?.sheets[0]?.name || null;
  const { data: p, error, isLoading } = useApi<Preview>(file && sheetName ? `/files/${enc(file.file_id)}/preview${qs({ sheet: sheetName, header_row: header, limit: 5 })}` : null, { keepPreviousData: true });
  return (
    <div className="stack">
      <FileDropzone onFiles={async ([f]) => {
        try {
          const [rec] = await uploadFiles([f]);
          await mutate();
          setFileId(rec.file_id); setSheet(rec.sheets[0]?.name || null); setHeader(null);
          toast(`Uploaded ${rec.name}`, "ok");
        } catch (e) { toast((e as Error).message, "bad"); }
      }} />
      <label className="field">Or choose an uploaded file
        <select value={fileId || ""} onChange={(e) => { setFileId(e.target.value || null); setSheet(null); setHeader(null); }}>
          <option value="">Choose a file…</option>{files?.map((f) => <option key={f.file_id} value={f.file_id}>{f.name}</option>)}
        </select></label>
      <AnimatePresence initial={false}>
        {file ? (
          <motion.div key={file.file_id} className="stack" initial="closed" animate="open" exit="closed" variants={collapse} style={{ overflow: "hidden" }}>
            <div className="form-grid">
              <label className="field">Tab<select value={sheetName || ""} onChange={(e) => { setSheet(e.target.value); setHeader(null); }}>{file.sheets.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}</select></label>
              {p ? <label className="field">Header row<select value={p.header_row} onChange={(e) => setHeader(Number(e.target.value))}>
                {p.raw.map((_, i) => <option key={i} value={i}>Row {i + 1}{i === p.detected_header_row ? " (detected)" : ""}</option>)}</select></label> : null}
            </div>
            {error ? <AlertBox kind="bad" items={[error.message]} /> : !p ? <Loading text="Reading tab…" /> : (
              <div className={`stack ${isLoading ? "is-loading" : ""}`}>
                <div className="small muted">Top of the tab — the highlighted row is used as column names. Click a row to change it.</div>
                <div className="table-wrap"><table className="data raw"><tbody>
                  {p.raw.map((r, i) => (
                    <tr key={i} className={`clickable ${i === p.header_row ? "header-row" : ""}`} onClick={() => setHeader(i)}>
                      <td className="rownum">{i + 1}</td>{r.map((c, k) => <td key={k}>{c == null ? "" : String(c)}</td>)}
                    </tr>
                  ))}
                </tbody></table></div>
                <AlertBox kind="info" items={p.warnings} />
                <div className="row"><span className="muted small">{fmt.int(p.row_count)} data rows · {p.columns.length} columns</span><span className="spacer" />
                  <UseButton label="Use this tab" onClick={async () => onUse(await post<Dataset>("/datasets", { kind: "file", file_id: file.file_id, sheet: sheetName, header_row: p.header_row }))} /></div>
              </div>
            )}
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

/* ---- BI: connection → workspace → report → page → visual → filters */
const showValue = (v: unknown) => (v === null || v === undefined ? "(Blank)" : String(v));
const parseValues = (text: string) => String(text || "").split(",").map((v) => v.trim()).filter(Boolean).map((v) => (/^\(blank\)$/i.test(v) ? null : v));
const OPS: [string, string][] = [["in", "is one of"], ["not_in", "is not"], [">", ">"], [">=", "≥"], ["<", "<"], ["<=", "≤"], ["between", "between"]];
type Named = { id: string; name: string };
interface Visual { id: string; title: string; default_filters?: { field: string; operator?: string; values?: unknown[] }[]; filter_notes?: string[] }
type FilterRow = { field: string; operator: string; values: string };

function BIPicker({ onUse }: { onUse: (d: Dataset) => Promise<void> }) {
  const [sel, setSel] = useState<{ c: string | null; w: string | null; r: string | null; p: string | null; v: string | null }>({ c: null, w: null, r: null, p: null, v: null });
  const { data: conns } = useApi<Named[]>("/connections");
  const c = pickId(conns, sel.c, "id");
  const base = c ? `/connections/${enc(c)}` : null;
  const { data: workspaces, error: ew } = useApi<Named[]>(base ? `${base}/workspaces` : null);
  const w = pickId(workspaces, sel.w, "id");
  const { data: reports, error: er } = useApi<Named[]>(base && w ? `${base}/reports${qs({ workspace_id: w })}` : null);
  const r = pickId(reports, sel.r, "id");
  const { data: pages, error: ep } = useApi<Named[]>(base && r ? `${base}/reports/${enc(r)}/pages` : null);
  const p = pickId(pages, sel.p, "id");
  const { data: visuals, error: ev } = useApi<Visual[]>(base && r && p ? `${base}/reports/${enc(r)}/pages/${enc(p)}/visuals` : null);
  const v = pickId(visuals, sel.v, "id");
  const visual = visuals?.find((x) => x.id === v);
  const [filters, setFilters] = useState<{ for: string; rows: FilterRow[] } | null>(null);
  const [preview, setPreview] = useState<{ columns: string[]; rows: Record<string, unknown>[]; row_count: number; warnings: string[] } | null>(null);
  // a new visual starts from the filters set on it in the report
  const rows: FilterRow[] = filters && filters.for === v ? filters.rows
    : (visual?.default_filters || []).map((f) => ({ field: f.field, operator: f.operator || "in", values: (f.values || []).map(showValue).join(", ") }));
  const setRows = (next: FilterRow[]) => setFilters({ for: v!, rows: next });

  if (!conns) return <Loading />;
  if (!conns.length) return (
    <EmptyState title="No BI connections yet" text="Connect Power BI, Tableau, a PBIP folder, or upload exports on the Sources page." icon={I.sources}>
      <Link className="btn primary" href={href.sources}>Add a connection</Link>
    </EmptyState>
  );
  const select = (label: string, items: (Named | Visual)[] | undefined, value: string | null, onPick: (id: string) => void) => (
    <label className="field">{label}
      <select value={value || ""} onChange={(e) => onPick(e.target.value)} disabled={!items}>
        {items?.map((i) => <option key={i.id} value={i.id}>{"title" in i ? i.title : i.name}</option>)}
      </select></label>
  );
  const payload = () => ({
    report_id: r, page_id: p, visual_id: v,
    filters: rows.filter((f) => f.field.trim()).map((f) => ({ field: f.field.trim(), operator: f.operator || "in", values: parseValues(f.values) })),
  });
  return (
    <div className="stack">
      <div className="form-grid">
        {select("Connection", conns, c, (id) => setSel({ c: id, w: null, r: null, p: null, v: null }))}
        {c ? select("Workspace / project", workspaces, w, (id) => setSel({ ...sel, c, w: id, r: null, p: null, v: null })) : null}
        {w ? select("Report / workbook / file", reports, r, (id) => setSel({ ...sel, c, w, r: id, p: null, v: null })) : null}
        {r ? select("Page / tab", pages, p, (id) => setSel({ ...sel, c, w, r, p: id, v: null })) : null}
        {p ? select("Visual", visuals, v, (id) => setSel({ c, w, r, p, v: id })) : null}
      </div>
      <AlertBox kind="bad" items={[ew?.message, er?.message, ep?.message, ev?.message]} />
      {visual ? (
        <motion.div className="stack" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}>
          <div className="row"><h3>Filters</h3><span className="muted small">Pre-filled with the filters set in the report — edit, add or remove. Empty values mean no filter.</span></div>
          {visual.filter_notes?.length ? (
            <div className="alert warn"><div><b>Report filters that can&apos;t be reproduced from the file</b> — the dashboard may show different numbers:
              <ul>{visual.filter_notes.map((n, i) => <li key={i}>{n}</li>)}</ul></div></div>
          ) : null}
          <AnimatePresence initial={false}>
            {rows.map((f, i) => (
              <motion.div key={i} className="filter-row" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}>
                <input type="text" placeholder="Table[Column]" value={f.field} onChange={(e) => setRows(rows.map((x, k) => (k === i ? { ...x, field: e.target.value } : x)))} />
                <select aria-label="Operator" value={f.operator} onChange={(e) => setRows(rows.map((x, k) => (k === i ? { ...x, operator: e.target.value } : x)))}>{OPS.map(([o, l]) => <option key={o} value={o}>{l}</option>)}</select>
                <input type="text" placeholder={f.operator === "between" ? "from, to" : "value1, value2"} value={f.values} onChange={(e) => setRows(rows.map((x, k) => (k === i ? { ...x, values: e.target.value } : x)))} />
                <button className="btn ghost sm" aria-label="Remove filter" onClick={() => setRows(rows.filter((_, k) => k !== i))}>✕</button>
              </motion.div>
            ))}
          </AnimatePresence>
          <div className="row">
            <button className="btn sm" onClick={() => setRows([...rows, { field: "", operator: "in", values: "" }])}><Icon d={I.plus} />Add filter</button><span className="spacer" />
            <UseButton label="Preview" primary={false} onClick={async () => setPreview(await post(`${base}/visual-data`, { ...payload(), limit: 8 }))} />
            <UseButton label="Use this data" onClick={async () => onUse(await post<Dataset>("/datasets", { kind: "bi_visual", connection_id: c, ...payload() }))} />
          </div>
          {preview ? (
            <motion.div className="stack" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
              <AlertBox kind="info" items={preview.warnings} /><div className="small muted">{fmt.int(preview.row_count)} rows</div>
              <PreviewTable columns={preview.columns} rows={preview.rows} />
            </motion.div>
          ) : null}
        </motion.div>
      ) : visuals && !visuals.length ? <div className="empty small">No data visuals on this page.</div> : null}
    </div>
  );
}

function DatasetPicker({ onUse }: { onUse: (d: Dataset) => Promise<void> }) {
  const { data: list } = useApi<Dataset[]>("/datasets");
  const [id, setId] = useState<string | null>(null);
  if (!list) return <Loading />;
  if (!list.length) return <div className="empty small">No saved datasets yet — every file tab or visual you load is saved here for reuse.</div>;
  const cur = id || list[0].dataset_id;
  return (
    <div className="stack">
      <label className="field">Dataset<select value={cur} onChange={(e) => setId(e.target.value)}>
        {list.map((d) => <option key={d.dataset_id} value={d.dataset_id}>{d.name} · {fmt.int(d.row_count)} rows · {fmt.date(d.created_at)}</option>)}</select></label>
      <div className="row"><span className="spacer" /><UseButton label="Use this dataset" onClick={() => onUse(list.find((d) => d.dataset_id === cur)!)} /></div>
    </div>
  );
}

/* ============================================================== mapping */
function Mapping({ left, right, cfg, suggestion, onChange, onResuggest }: {
  left: Dataset; right: Dataset; cfg: ReconConfig; suggestion: Suggestion | null; onChange: (c: ReconConfig) => void; onResuggest: () => void;
}) {
  const [adv, setAdv] = useState(false);
  const L = left.columns.map((c) => c.name), R = right.columns.map((c) => c.name);
  const update = (fn: (c: ReconConfig) => void) => { const next = structuredClone(cfg); fn(next); onChange(next); };
  const listOf = (c: ReconConfig, kind: "key" | "value") => (kind === "key" ? c.key_columns : c.value_columns);
  const used = [...cfg.key_columns, ...cfg.value_columns];
  const unL = L.filter((c) => !used.some((p) => p.left === c)), unR = R.filter((c) => !used.some((p) => p.right === c));
  const score = (kind: string, p: ColumnPair) => {
    const m = (kind === "key" ? suggestion?.key_columns : suggestion?.value_columns)?.find((x) => x.left === p.left && x.right === p.right);
    return m ? <span className="match-score" title={m.reason}><span className="ms-bar"><motion.span initial={{ width: 0 }} animate={{ width: `${Math.round(m.score * 100)}%` }} /></span>{Math.round(m.score * 100)}%</span> : <span className="faint small">manual</span>;
  };
  const colSel = (cols: string[], value: string, onPick: (v: string) => void) => <select value={value} onChange={(e) => onPick(e.target.value)}>{cols.map((c) => <option key={c} value={c}>{c}</option>)}</select>;
  const row = (kind: "key" | "value", p: ColumnPair, i: number) => (
    <motion.tr key={`${kind}-${i}`} layout initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
      <td><select value={kind} className={`role-${kind}`} onChange={(e) => update((c) => {
        const [item] = listOf(c, kind).splice(i, 1);
        const to = e.target.value as "key" | "value";
        listOf(c, to).push(to === "value" ? { left: item.left, right: item.right, tolerance_abs: 0.01, tolerance_pct: 0 } : { left: item.left, right: item.right });
      })}><option value="key">Key</option><option value="value">Value</option></select></td>
      <td>{colSel(L, p.left, (v) => update((c) => { listOf(c, kind)[i].left = v; }))}</td>
      <td>{colSel(R, p.right, (v) => update((c) => { listOf(c, kind)[i].right = v; }))}</td>
      <td>{kind === "value" ? <input type="number" step="any" min="0" value={p.tolerance_abs ?? 0} onChange={(e) => update((c) => { c.value_columns[i].tolerance_abs = Number(e.target.value || 0); })} /> : <span className="faint">—</span>}</td>
      <td>{kind === "value" ? <input type="number" step="any" min="0" value={p.tolerance_pct ?? 0} onChange={(e) => update((c) => { c.value_columns[i].tolerance_pct = Number(e.target.value || 0); })} /> : <span className="faint">—</span>}</td>
      <td>{score(kind, p)}</td>
      <td><button className="btn ghost sm" aria-label="Remove" onClick={() => update((c) => { listOf(c, kind).splice(i, 1); })}>✕</button></td>
    </motion.tr>
  );
  const filterBlock = (side: Side) => {
    const cols = side === "left" ? L : R;
    const f = side === "left" ? cfg.left_filters : cfg.right_filters;
    const fk = side === "left" ? "left_filters" : "right_filters";
    return (
      <div className="stack">
        <h3>Only include rows from {side === "left" ? "A" : "B"} where…</h3>
        {Object.entries(f).map(([col, vals]) => (
          <div key={col} className="row">
            <select aria-label="Column" value={col} onChange={(e) => update((c) => { const v = c[fk][col]; delete c[fk][col]; c[fk][e.target.value] = v; })}>{cols.map((x) => <option key={x} value={x}>{x}</option>)}</select>
            <input type="text" defaultValue={vals.join(", ")} placeholder="values, comma separated" style={{ flex: 1 }}
              onBlur={(e) => update((c) => { c[fk][col] = e.target.value.split(",").map((v) => v.trim()).filter(Boolean); })} />
            <button className="btn ghost sm" aria-label="Remove filter" onClick={() => update((c) => { delete c[fk][col]; })}>✕</button>
          </div>
        ))}
        <div><button className="btn sm" onClick={() => update((c) => { const col = cols.find((x) => !(x in c[fk])); if (col) c[fk][col] = []; })}><Icon d={I.plus} />Filter {side === "left" ? "A" : "B"}</button></div>
      </div>
    );
  };
  return (
    <>
      <div className="card-head"><h2>Match columns</h2><span className="sub">Keys identify a row (product, account, month…). Values are the numbers that should agree.</span></div>
      <div className="card-body stack">
        <AlertBox kind="info" items={suggestion?.warnings} />
        <div className="table-wrap"><table className="data map-table">
          <thead><tr><th style={{ width: 110 }}>Role</th><th><SideLabel side="left" /> {left.name}</th><th><SideLabel side="right" /> {right.name}</th><th>Tolerance ±</th><th>Tolerance %</th><th>Match</th><th /></tr></thead>
          <tbody><AnimatePresence initial={false}>{cfg.key_columns.map((p, i) => row("key", p, i))}{cfg.value_columns.map((p, i) => row("value", p, i))}</AnimatePresence></tbody>
        </table></div>
        <div className="row">
          {(["key", "value"] as const).map((k) => (
            <button key={k} className="btn sm" onClick={() => update((c) => {
              const l = unL[0] || L[0], r = unR[0] || R[0];
              listOf(c, k).push(k === "value" ? { left: l, right: r, tolerance_abs: 0.01, tolerance_pct: 0 } : { left: l, right: r });
            })}><Icon d={I.plus} />{k === "key" ? "Key" : "Value"} column</button>
          ))}
          <span className="spacer" /><button className="btn ghost sm" onClick={onResuggest}><Icon d={I.reload} />Re-suggest</button>
        </div>
        {unL.length || unR.length ? <div className="small muted">Not compared — <b>A:</b> {unL.join(", ") || "none"} · <b>B:</b> {unR.join(", ") || "none"}</div> : null}
        {!cfg.value_columns.length ? <AlertBox kind="warn" items={["Add at least one value column — the numbers that should agree."]} /> : null}
        <div className="adv">
          <button className="adv-toggle" onClick={() => setAdv(!adv)} aria-expanded={adv}>
            <motion.span animate={{ rotate: adv ? 90 : 0 }} transition={spring} className="chev-wrap"><Icon d={I.chev} size={14} /></motion.span>Advanced options
          </button>
          <AnimatePresence initial={false}>
            {adv ? (
              <motion.div initial="closed" animate="open" exit="closed" variants={collapse} style={{ overflow: "hidden" }}>
                <div className="stack" style={{ paddingTop: 12 }}>
                  <div className="form-grid">
                    <label className="field">Duplicate keys<select value={cfg.aggregate} onChange={(e) => update((c) => { c.aggregate = e.target.value; })}>
                      <option value="auto">Sum them (recommended)</option><option value="sum">Always sum</option><option value="first">Keep first row</option></select></label>
                    <label className="field">Multiply A values by<input type="number" step="any" value={cfg.scale_left} onChange={(e) => update((c) => { c.scale_left = Number(e.target.value || 1); })} /></label>
                    <label className="field">Multiply B values by<input type="number" step="any" value={cfg.scale_right} onChange={(e) => update((c) => { c.scale_right = Number(e.target.value || 1); })} /></label>
                  </div>
                  <div className="row">
                    <label className="check"><input type="checkbox" checked={cfg.case_insensitive_keys} onChange={(e) => update((c) => { c.case_insensitive_keys = e.target.checked; })} /> Ignore case and extra spaces in keys</label>
                    <label className="check"><input type="checkbox" checked={cfg.treat_blank_as_zero} onChange={(e) => update((c) => { c.treat_blank_as_zero = e.target.checked; })} /> Treat blank values as zero</label>
                  </div>
                  <div className="grid-2">{filterBlock("left")}{filterBlock("right")}</div>
                </div>
              </motion.div>
            ) : null}
          </AnimatePresence>
        </div>
      </div>
    </>
  );
}

/* ============================================================== run box */
function RunBox({ left, right, config, onDone }: { left: Dataset; right: Dataset; config: ReconConfig; onDone: (id: string, name: string) => void }) {
  const auto = `${left.name} vs ${right.name}`;
  const [name, setName] = useState("");
  const [investigate, setInvestigate] = useState(true);
  const [busy, setBusy] = useState(false);
  // the backend reads an empty value list as "keep nothing", so drop filters with no values
  const keepFilled = (f: Record<string, string[]>) => Object.fromEntries(Object.entries(f || {}).filter(([, vals]) => vals && vals.length));
  async function run() {
    if (!config.value_columns.length) { toast("Add at least one value column to compare.", "bad"); return; }
    setBusy(true);
    try {
      const res = await post<{ reconciliation: { reconciliation_id: string; name: string } }>("/recon/run?limit=1", {
        left_dataset_id: left.dataset_id, right_dataset_id: right.dataset_id,
        config: { ...config, left_filters: keepFilled(config.left_filters), right_filters: keepFilled(config.right_filters) },
        name: name.trim() || auto, create_investigation: investigate,
      });
      onDone(res.reconciliation.reconciliation_id, res.reconciliation.name);
    } catch (e) { toast((e as Error).message, "bad"); setBusy(false); }
  }
  return (
    <motion.section className="card run-box" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 8 }} transition={softSpring}>
      <div className="card-body"><div className="form-grid" style={{ alignItems: "end" }}>
        <label className="field" style={{ gridColumn: "span 2" }}>Name<input type="text" value={name} placeholder={auto} onChange={(e) => setName(e.target.value)} /></label>
        <label className="check"><input type="checkbox" checked={investigate} onChange={(e) => setInvestigate(e.target.checked)} /> Open an investigation with the likely causes</label>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <motion.button className="btn primary lg" onClick={run} disabled={busy} whileHover={{ scale: busy ? 1 : 1.03 }} whileTap={{ scale: 0.97 }}>
            {busy ? <><Spinner />Comparing…</> : <><Icon d={I.play} />Run reconciliation</>}
          </motion.button>
        </div>
      </div></div>
    </motion.section>
  );
}
