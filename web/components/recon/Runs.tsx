"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { AnimatePresence, LayoutGroup, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { download, enc, get, post, qs, useApi } from "@/lib/api";
import { fmt, signClass } from "@/lib/format";
import { fadeUp, spring } from "@/lib/motion";
import { href, recordId, safeDecode } from "@/lib/routes";
import { GoRow } from "@/components/DataTable";
import { Modal } from "@/components/Modal";
import { toast } from "@/components/feedback";
import { useCrumbs } from "@/components/shell/Crumbs";
import { AlertBox, Card, ConfBadge, EmptyState, I, Icon, Kpi, Loading, PageHead, Spinner, Stagger } from "@/components/ui";
import { BarRow, CauseCards, RunBadge, SideLabel, StatusBadge, type Hypothesis, type ReconConfig, type RunSummary } from "./bits";

/* ============================================================== list */
/** /results — or, given a template, /templates/<template>: that template's run history */
export function RunsList({ template = null }: { template?: string | null }) {
  const router = useRouter();
  const pathname = usePathname();
  const { data: runs, error } = useApi<RunSummary[]>(`/recon${qs({ definition_id: template })}`);
  const { data: templates } = useApi<{ definition_id: string; name: string }[]>(template ? "/recon-templates" : null);
  const tpl = template ? templates?.find((t) => t.definition_id === template) : undefined;
  const tplName = template ? tpl?.name || "…" : "";
  useEffect(() => {
    if (!tpl) return;
    const canonical = href.template(tpl.definition_id, tpl.name);
    if (safeDecode(canonical) !== safeDecode(pathname)) router.replace(canonical, { scroll: false });
  }, [tpl, pathname, router]);
  useCrumbs(template ? [{ label: "Reconcile" }, { label: "Templates", href: href.templates }, { label: tplName }] : [{ label: "Reconcile" }, { label: "Results" }]);
  return (
    <Stagger>
      <PageHead title={template ? `History · ${tplName}` : "Results"} text={template ? "Every run of this template, newest first." : <>Every reconciliation you&apos;ve run, newest first.</>}
        actions={<>{template ? <Link className="btn ghost" href={href.results}>All results</Link> : null}<Link className="btn primary" href={href.reconcile}><Icon d={I.plus} />New reconciliation</Link></>} />
      <AlertBox kind="bad" items={error ? [error.message] : null} />
      <Card bodyClass="card-body tight">
        {!runs ? <Loading /> : runs.length ? (
          <div className="table-wrap"><table className="data">
            <thead><tr><th>Name</th><th>Result</th><th className="num">Match rate</th><th className="num">Keys</th><th className="num">Differences</th><th className="num">Total difference</th><th>Confidence</th><th>Run</th></tr></thead>
            <tbody>{runs.map((r, i) => (
              <GoRow key={r.reconciliation_id} href={href.result(r.reconciliation_id, r.name)} i={i}>
                <td>{r.name}<div className="faint small">{r.left_name} vs {r.right_name}</div></td>
                <td><RunBadge s={r.status} /></td><td className="num"><RateCell v={r.match_rate_pct} /></td><td className="num">{fmt.int(r.total_keys)}</td>
                <td className="num">{fmt.int(r.differences)}</td><td className={`num ${signClass(r.top_difference)}`}>{fmt.signed(r.top_difference)}</td>
                <td><ConfBadge c={r.confidence} /></td><td className="muted small">{fmt.date(r.created_at)}</td>
              </GoRow>
            ))}</tbody>
          </table></div>
        ) : (
          <EmptyState title="No reconciliations yet" text="Run your first one to see results here." icon={I.results}>
            <Link className="btn primary" href={href.reconcile}>New reconciliation</Link>
          </EmptyState>
        )}
      </Card>
    </Stagger>
  );
}

/** match rate with a thin progress bar under it */
function RateCell({ v }: { v: number }) {
  const kind = v >= 99.99 ? "ok" : v < 90 ? "bad" : "warn";
  return (
    <div className="rate-cell">{fmt.rate(v)}
      <div className={`rate-bar ${kind}`}><motion.span initial={{ width: 0 }} animate={{ width: `${Math.max(2, Math.min(100, v || 0))}%` }} transition={{ type: "spring", stiffness: 160, damping: 24 }} /></div>
    </div>
  );
}

/* ============================================================ detail */
interface ValueSummary {
  label: string; mismatched_rows: number; total_left: number; total_right: number; difference: number; difference_pct: number;
  bridge: { missing_from_right: number; missing_from_left: number; value_differences: number; total: number };
}
interface RunRow { status: string; keys: Record<string, unknown>; left_rows: number; right_rows: number; cells: { left: number | null; right: number | null; difference: number | null; within_tolerance: boolean }[] }
interface Recon {
  reconciliation_id: string; name: string; status: string; confidence: string; created_at: string; left_name: string; right_name: string;
  definition_id?: string | null; investigation_id?: string | null; warnings: string[]; config: ReconConfig; hypotheses: Hypothesis[];
  summary: { match_rate_pct: number; total_keys: number; matched: number; mismatched: number; only_left: number; only_right: number };
  value_summaries: ValueSummary[]; drivers: { dimension: string; value_label: string; members: { member: string; difference: number }[] }[];
  rows: RunRow[];
}
interface RunPage { reconciliation: Recon; offset: number; row_total: number }

const label = (p: { label?: string | null; left: string; right: string }) => p.label || (p.left === p.right ? p.left : `${p.left} / ${p.right}`);

/** /results/<name>-<number> */
export function RunDetail({ slug }: { slug: string }) {
  const id = recordId("result", slug);
  const router = useRouter();
  const pathname = usePathname();
  const { data: first, error } = useApi<RunPage>(`/recon/${enc(id)}?limit=100`);
  const [tplOpen, setTplOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const rec = first?.reconciliation;
  const canonical = rec ? href.result(rec.reconciliation_id, rec.name) : null;
  useEffect(() => {
    if (canonical && safeDecode(canonical) !== safeDecode(pathname)) router.replace(canonical, { scroll: false });
  }, [canonical, pathname, router]);
  useCrumbs([{ label: "Reconcile" }, { label: "Results", href: href.results }, { label: rec?.name || "…" }]);
  if (error) return <AlertBox kind="bad" items={[error.message]} />;
  if (!rec || !first) return <div className="ws-loading"><Loading text="Loading result…" /></div>;
  const s = rec.summary;
  const A = rec.left_name, B = rec.right_name;

  async function run(what: string, fn: () => Promise<void>) {
    setBusy(what);
    try { await fn(); } catch (e) { toast((e as Error).message, "bad"); } finally { setBusy(""); }
  }
  const investigate = () => run("inv", async () => {
    const created = await post<{ investigation_id: string }>(`/recon/${enc(id)}/investigate`, {});
    router.push(href.investigation(created.investigation_id));
  });
  const rerun = () => run("rerun", async () => {
    const res = await post<{ reconciliation: { reconciliation_id: string; name: string } }>(`/recon-templates/${enc(rec.definition_id!)}/run?limit=1`, {});
    router.push(href.result(res.reconciliation.reconciliation_id, res.reconciliation.name));
  });

  return (
    <Stagger>
      <PageHead
        title={<><div className="row" style={{ marginBottom: 6 }}><RunBadge s={rec.status} /><ConfBadge c={rec.confidence} /><span className="faint small" style={{ fontWeight: 400 }}>{rec.reconciliation_id} · {fmt.date(rec.created_at)}</span></div>{rec.name}</>}
        text={<><SideLabel side="left" /> {A} &nbsp;vs&nbsp; <SideLabel side="right" /> {B}</>}
        actions={<>
          <button className="btn" onClick={() => run("xlsx", () => download(`/recon/${enc(id)}/export`, `${rec.name}.xlsx`))} disabled={!!busy}>{busy === "xlsx" ? <Spinner /> : <Icon d={I.download} />}Download Excel</button>
          {rec.definition_id ? <button className="btn" onClick={rerun} disabled={!!busy}>{busy === "rerun" ? <Spinner /> : <Icon d={I.reload} />}Run again</button>
            : <button className="btn" onClick={() => setTplOpen(true)}><Icon d={I.template} />Save as template</button>}
          {rec.investigation_id ? <Link className="btn primary" href={href.investigation(rec.investigation_id)}><Icon d={I.investigate} />Open investigation</Link>
            : <button className="btn primary" onClick={investigate} disabled={!!busy}>{busy === "inv" ? <Spinner /> : <Icon d={I.investigate} />}Investigate</button>}
        </>} />
      <motion.div className="kpis" variants={{ hidden: {}, show: { transition: { staggerChildren: 0.05 } } }}>
        <Kpi label="Match rate" value={s.match_rate_pct} display={fmt.rate(s.match_rate_pct)} hint={`${fmt.int(s.total_keys)} keys compared`} kind={s.match_rate_pct >= 99.99 ? "ok" : s.match_rate_pct < 90 ? "bad" : "warn"} />
        <Kpi label="Matched" value={s.matched} hint="within tolerance" kind="ok" />
        <Kpi label="Values differ" value={s.mismatched} hint="same key, different value" kind={s.mismatched ? "warn" : ""} />
        <Kpi label="Only in A" value={s.only_left} hint={`missing from ${B}`} kind={s.only_left ? "bad" : ""} />
        <Kpi label="Only in B" value={s.only_right} hint={`missing from ${A}`} kind={s.only_right ? "bad" : ""} />
      </motion.div>
      <AlertBox kind="warn" items={rec.warnings} />
      {rec.value_summaries.map((vs) => {
        const b = vs.bridge;
        const parts: [string, number][] = [[`Rows only in A (missing from ${B})`, b.missing_from_right], [`Rows only in B (missing from ${A})`, b.missing_from_left], ["Different values on matched rows", b.value_differences]];
        const max = Math.max(...parts.map((p) => Math.abs(p[1])), 1e-9);
        return (
          <Card key={vs.label} title={vs.label} sub={`${fmt.int(vs.mismatched_rows)} value difference(s)`} bodyClass="card-body stack">
            <div className="totals">
              <div className="t"><div className="l">Total A · {A}</div><div className="v">{fmt.num(vs.total_left)}</div></div>
              <div className="t"><div className="l">Total B · {B}</div><div className="v">{fmt.num(vs.total_right)}</div></div>
              <div className="t"><div className="l">Difference (B − A)</div><div className={`v ${signClass(vs.difference)}`}>{fmt.signed(vs.difference)}</div><div className="small muted">{fmt.pct(vs.difference_pct)}</div></div>
            </div>
            <div><h3 style={{ marginBottom: 6 }}>Where the difference comes from</h3>
              {parts.map(([l, v], i) => <BarRow key={l} label={l} value={v} max={max} i={i} />)}
              <div className="bar-row" style={{ borderTop: "1px solid var(--border)", marginTop: 4, paddingTop: 8 }}><b className="label">Total difference</b><span /><b className={`num ${signClass(b.total)}`}>{fmt.signed(b.total)}</b></div>
            </div>
          </Card>
        );
      })}
      <div className="grid-2">
        <Card title="Likely causes" sub="Ranked by how well the data supports them" bodyClass="card-body stack"><CauseCards hypotheses={rec.hypotheses} /></Card>
        <Card title="What's driving it" sub="Share of the difference by key" bodyClass="card-body stack">
          {rec.drivers.length ? rec.drivers.map((d) => {
            const max = Math.max(...d.members.map((m) => Math.abs(m.difference)), 1e-9);
            return <div key={d.dimension + d.value_label}><h3 style={{ marginBottom: 4 }}>By {d.dimension} <span className="faint small">· {d.value_label}</span></h3>
              {d.members.map((m, i) => <BarRow key={m.member} label={String(m.member)} value={m.difference} max={max} i={i} />)}</div>;
          }) : <div className="empty small">{rec.status === "MATCHED" ? "Nothing to explain — everything matches." : "No key columns, so there's no breakdown."}</div>}
        </Card>
      </div>
      <RowsCard id={id} first={first} />
      <SaveTemplate open={tplOpen} onClose={() => setTplOpen(false)} id={id} name={rec.name} />
    </Stagger>
  );
}

function RowsCard({ id, first }: { id: string; first: RunPage }) {
  const rec = first.reconciliation;
  const s = rec.summary;
  const keyLabels = rec.config.key_columns.length ? rec.config.key_columns.map(label) : ["Scope"];
  const valueLabels = rec.config.value_columns.map(label);
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<RunPage>(first);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const seq = useRef(0);
  const firstLoad = useRef(true);
  useEffect(() => {
    if (firstLoad.current) { firstLoad.current = false; return; }
    const mine = ++seq.current;
    const t = setTimeout(async () => {
      setLoading(true);
      try {
        const p = await get<RunPage>(`/recon/${enc(id)}${qs({ status, search, offset, limit: 100 })}`);
        if (mine === seq.current) { setPage(p); setErr(null); }
      } catch (e) { if (mine === seq.current) setErr((e as Error).message); }
      finally { if (mine === seq.current) setLoading(false); }
    }, search ? 250 : 0);
    return () => clearTimeout(t);
  }, [id, status, search, offset]);
  const chips: [string, string, number][] = [["", "All", s.total_keys], ["MISMATCH,ONLY_LEFT,ONLY_RIGHT", "Differences", s.mismatched + s.only_left + s.only_right], ["MISMATCH", "Value differs", s.mismatched], ["ONLY_LEFT", "Only in A", s.only_left], ["ONLY_RIGHT", "Only in B", s.only_right], ["MATCH", "Matched", s.matched]];
  const rows = page.reconciliation.rows;
  return (
    <motion.section className="card" variants={fadeUp}>
      <div className="card-head" style={{ flexWrap: "wrap" }}><h2>Rows</h2><span className="spacer" />
        <LayoutGroup id="row-chips"><div className="chips">
          {chips.map(([v, l, n]) => (
            <button key={v} className={`chip ${v === status ? "active" : ""}`} onClick={() => { setStatus(v); setOffset(0); }}>
              {v === status ? <motion.span layoutId="chip-bg" className="chip-bg" transition={spring} /> : null}
              <span className="chip-text">{l}<span className="count">{fmt.int(n)}</span></span>
            </button>
          ))}
        </div></LayoutGroup>
        <input type="search" placeholder="Search keys…" value={search} onChange={(e) => { setSearch(e.target.value); setOffset(0); }} style={{ width: 200 }} />
      </div>
      <div className={`card-body tight ${loading ? "is-loading" : ""}`}>
        <AlertBox kind="bad" items={err ? [err] : null} />
        {!rows.length ? <div className="empty small">No rows match this filter.</div> : (
          <>
            <div className="table-wrap"><table className="data">
              <thead><tr><th>Status</th>{keyLabels.map((k) => <th key={k}>{k}</th>)}
                {valueLabels.map((v) => [<th key={`${v}a`} className="num">{v} · A</th>, <th key={`${v}b`} className="num">{v} · B</th>, <th key={`${v}d`} className="num">Difference</th>])}</tr></thead>
              <AnimatePresence mode="wait" initial={false}>
                <motion.tbody key={`${status}|${search}|${page.offset}`} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }}>
                  {rows.map((r, i) => (
                    <tr key={i}>
                      <td><StatusBadge s={r.status} />{r.left_rows > 1 || r.right_rows > 1 ? <div className="faint small">{r.left_rows}↔{r.right_rows} rows</div> : null}</td>
                      {keyLabels.map((k) => <td key={k}>{String(r.keys[k] ?? "")}</td>)}
                      {r.cells.map((c, j) => [
                        <td key={`${j}a`} className="num">{fmt.num(c.left)}</td>, <td key={`${j}b`} className="num">{fmt.num(c.right)}</td>,
                        <td key={`${j}d`} className={`num ${c.within_tolerance ? "" : signClass(c.difference)}`}>{c.within_tolerance && r.status === "MATCH" ? <span className="faint">—</span> : fmt.signed(c.difference)}</td>,
                      ])}
                    </tr>
                  ))}
                </motion.tbody>
              </AnimatePresence>
            </table></div>
            <div className="pager"><span>{fmt.int(page.offset + 1)}–{fmt.int(page.offset + rows.length)} of {fmt.int(page.row_total)}</span>
              <button className="btn sm" disabled={page.offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>Previous</button>
              <button className="btn sm" disabled={page.offset + rows.length >= page.row_total} onClick={() => setOffset(offset + 100)}>Next</button></div>
          </>
        )}
      </div>
    </motion.section>
  );
}

function SaveTemplate({ open, onClose, id, name }: { open: boolean; onClose: () => void; id: string; name: string }) {
  const [tname, setName] = useState(name);
  const [desc, setDesc] = useState("");
  const [inv, setInv] = useState(false);
  const router = useRouter();
  return (
    <Modal open={open} onClose={onClose} title="Save as template" onConfirm={async () => {
      const d = await post<{ name: string }>(`/recon/${enc(id)}/save-template`, { name: tname, description: desc, create_investigation: inv });
      toast(`Template "${d.name}" saved`, "ok");
      router.push(href.templates);
    }}>
      <p className="muted small" style={{ margin: 0 }}>Saves both sources and this column mapping. Next period, re-run it in one click — or with newly uploaded versions of the files.</p>
      <label className="field">Template name<input type="text" value={tname} onChange={(e) => setName(e.target.value)} /></label>
      <label className="field">Description<textarea value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="e.g. Month-end check: stock value in Power BI vs the ERP ledger" /></label>
      <label className="check"><input type="checkbox" checked={inv} onChange={(e) => setInv(e.target.checked)} /> Open an investigation automatically when differences are found</label>
    </Modal>
  );
}
