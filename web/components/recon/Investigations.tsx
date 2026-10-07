"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { AnimatePresence, LayoutGroup, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { del, download, enc, get, patch, post, qs, refresh, useApi } from "@/lib/api";
import { fmt, nodeType } from "@/lib/format";
import { spring } from "@/lib/motion";
import { href, recordId, safeDecode, statusFromUrl } from "@/lib/routes";
import { GoRow } from "@/components/DataTable";
import { Modal } from "@/components/Modal";
import { toast, useConfirm } from "@/components/feedback";
import { useCrumbs } from "@/components/shell/Crumbs";
import { AlertBox, Badge, Card, ConfBadge, EmptyState, I, Icon, Loading, PageHead, Spinner, Stagger } from "@/components/ui";
import { CauseCards, INV_TEXT, InvBadge, type Hypothesis, type InvestigationSummary } from "./bits";

const FILTERS: [string, string][] = [["", "All"], ["OPEN", "Open"], ["INVESTIGATING", "Investigating"], ["ROOT_CAUSE_FOUND", "Root cause found"], ["RESOLVED", "Resolved"], ["INCONCLUSIVE", "Inconclusive"]];

/* ============================================================== list */
export function InvestigationsList() {
  const raw = useSearchParams().get("status");
  const status = statusFromUrl(raw);  // ?status=root-cause-found
  const router = useRouter();
  // ?status=ROOT_CAUSE_FOUND (older links) reads as ?status=root-cause-found
  useEffect(() => {
    if (status && raw !== href.investigations(status).split("=")[1]) router.replace(href.investigations(status), { scroll: false });
  }, [raw, status, router]);
  useCrumbs([{ label: "Investigate" }, { label: "Investigations" }]);
  const { data: list, error } = useApi<InvestigationSummary[]>(`/investigations${qs({ status })}`);
  return (
    <Stagger>
      <PageHead title="Investigations" text="Each case keeps the question, evidence, ranked causes and findings — so the same problem is never investigated from scratch twice." />
      <LayoutGroup id="inv-chips">
        <motion.div className="chips" variants={{ hidden: { opacity: 0 }, show: { opacity: 1 } }}>
          {FILTERS.map(([v, l]) => (
            <Link key={v} className={`chip ${v === status ? "active" : ""}`} href={href.investigations(v)} scroll={false}>
              {v === status ? <motion.span layoutId="inv-chip" className="chip-bg" transition={spring} /> : null}<span className="chip-text">{l}</span>
            </Link>
          ))}
        </motion.div>
      </LayoutGroup>
      <AlertBox kind="bad" items={error ? [error.message] : null} />
      <Card bodyClass="card-body tight">
        {!list ? <Loading /> : list.length ? (
          <div className="table-wrap"><table className="data">
            <thead><tr><th>Question</th><th>Status</th><th>Confidence</th><th>Top likely cause</th><th className="num">Evidence</th><th>Updated</th></tr></thead>
            <tbody>{list.map((i, n) => (
              <GoRow key={i.investigation_id} href={href.investigation(i.investigation_id, i.question)} i={n}>
                <td>{i.question}<div className="faint small">{i.investigation_id}{i.owner ? ` · ${i.owner}` : ""}</div></td>
                <td><InvBadge s={i.status} /></td><td><ConfBadge c={i.confidence} /></td><td className="small">{i.top_hypothesis || "—"}</td>
                <td className="num">{i.evidence_count}</td><td className="muted small">{fmt.date(i.updated_at)}</td>
              </GoRow>
            ))}</tbody>
          </table></div>
        ) : (
          <EmptyState title={`No investigations${status ? " with this status" : ""}`} text="Investigations are created from a reconciliation result, or automatically when you tick the option before running." icon={I.investigate} />
        )}
      </Card>
    </Stagger>
  );
}

/* ============================================================ detail */
interface Investigation {
  investigation_id: string; question: string; status: string; confidence: string; created_at: string; connection_id?: string | null;
  reconciliation_id?: string | null; snapshot_ids: string[]; data_reconciliation_ids: string[]; hypotheses: Hypothesis[];
  findings: { finding_id: string; summary: string; confidence: string; evidence_ids: string[] }[];
  notes: { text: string; author: string; created_at: string }[];
  evidence: { evidence_id: string; kind: string; description: string; confidence: string; captured_at: string }[];
  lineage?: { coverage_pct: number; overall_confidence: string; steps: { from_name: string; to_name: string; edge_type: string }[]; gaps: string[] } | null;
  impact?: { risk: string; explanation: string; affected?: { type: string; name: string }[] } | null;
}

/** /investigations/<question>-<number> */
export function InvestigationDetail({ slug }: { slug: string }) {
  const id = recordId("investigation", slug);
  const pathname = usePathname();
  const key = `/investigations/${enc(id)}`;
  const { data: inv, error, mutate } = useApi<Investigation>(key);
  const router = useRouter();
  const confirm = useConfirm();
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [picker, setPicker] = useState<"lineage" | "impact" | null>(null);
  const canonical = inv ? href.investigation(inv.investigation_id, inv.question) : null;
  useEffect(() => {
    if (canonical && safeDecode(canonical) !== safeDecode(pathname)) router.replace(canonical, { scroll: false });
  }, [canonical, pathname, router]);
  useCrumbs([{ label: "Investigate" }, { label: "Investigations", href: href.investigations() }, { label: inv?.question || "…" }]);
  if (error) return <AlertBox kind="bad" items={[error.message]} />;
  if (!inv) return <div className="ws-loading"><Loading text="Loading investigation…" /></div>;
  const canExport = inv.data_reconciliation_ids.length || (inv.reconciliation_id && inv.snapshot_ids.length >= 2);

  async function setStatus(status: string) {
    try { await patch(key, { status }); toast("Status updated", "ok"); await mutate(); refresh("/investigations"); } catch (e) { toast((e as Error).message, "bad"); }
  }
  async function addNote() {
    if (!note.trim()) return;
    setSaving(true);
    try { await post(`${key}/notes`, { text: note.trim() }); setNote(""); await mutate(); } catch (e) { toast((e as Error).message, "bad"); } finally { setSaving(false); }
  }
  async function remove() {
    if (!(await confirm({ title: "Delete this investigation?" }))) return;
    try { await del(key); await refresh("/investigations"); router.push(href.investigations()); } catch (e) { toast((e as Error).message, "bad"); }
  }

  return (
    <Stagger>
      <PageHead
        title={<><div className="row" style={{ marginBottom: 6 }}><InvBadge s={inv.status} /><ConfBadge c={inv.confidence} /><span className="faint small" style={{ fontWeight: 400 }}>{inv.investigation_id} · opened {fmt.date(inv.created_at)}</span></div>{inv.question}</>}
        text="Overall confidence is only as strong as the weakest finding — the platform never claims more certainty than the evidence supports."
        actions={<>
          <select value={inv.status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">{Object.entries(INV_TEXT).map(([s, l]) => <option key={s} value={s}>{l}</option>)}</select>
          {canExport ? <button className="btn" onClick={() => download(`/exports/investigations/${enc(id)}.xlsx`, `${inv.investigation_id}.xlsx`).catch((e) => toast((e as Error).message, "bad"))}><Icon d={I.download} />Download workbook</button> : null}
          <button className="btn ghost danger" onClick={remove}><Icon d={I.trash} />Delete</button>
        </>} />
      <div className="grid-2">
        <div className="stack">
          <Card title="Likely causes" sub={`${inv.hypotheses.length} ranked`} bodyClass="card-body stack"><CauseCards hypotheses={inv.hypotheses} /></Card>
          <Card title="Findings" bodyClass="card-body tight">
            <div className="table-wrap"><table className="data"><tbody>
              {inv.findings.length ? inv.findings.map((f) => (
                <tr key={f.finding_id}><td>{f.summary}<div className="faint small">{f.finding_id} · evidence {f.evidence_ids.join(", ")}</div></td><td><ConfBadge c={f.confidence} /></td></tr>
              )) : <tr><td className="muted">No findings yet.</td></tr>}
            </tbody></table></div>
          </Card>
          {inv.lineage ? (
            <Card title="Lineage" sub={`coverage ${inv.lineage.coverage_pct}%`} right={<ConfBadge c={inv.lineage.overall_confidence} />} bodyClass="card-body stack small">
              <ol className="lin-steps">
                {inv.lineage.steps.map((s, i) => (
                  <motion.li key={i} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.04 }}>
                    {s.from_name} <span className="faint">— {s.edge_type.toLowerCase().replace(/_/g, " ")} →</span> {s.to_name}
                  </motion.li>
                ))}
              </ol>
              <AlertBox kind="warn" items={inv.lineage.gaps} />
            </Card>
          ) : null}
          {inv.impact ? (
            <Card title="Impact" right={<Badge kind={inv.impact.risk === "HIGH" ? "bad" : inv.impact.risk === "MEDIUM" ? "warn" : "ok"}>{inv.impact.risk} risk</Badge>} bodyClass="card-body small">
              {inv.impact.explanation}
              {inv.impact.affected?.length ? <ul>{inv.impact.affected.map((a, i) => <li key={i}>{a.type.toLowerCase()}: {a.name}</li>)}</ul> : null}
            </Card>
          ) : null}
        </div>
        <div className="stack">
          <Card title="Linked work" bodyClass="card-body stack small">
            {inv.data_reconciliation_ids.length ? inv.data_reconciliation_ids.map((r) => <Link key={r} href={href.result(r)}>Reconciliation {r} →</Link>) : <span className="muted">No reconciliations linked.</span>}
            {inv.connection_id ? (
              <div className="row"><span className="muted">Connection {inv.connection_id}</span><span className="spacer" />
                <button className="btn sm" onClick={() => setPicker("lineage")}><Icon d={I.lineage} />Add lineage</button>
                <button className="btn sm" onClick={() => setPicker("impact")}><Icon d={I.health} />Add impact</button></div>
            ) : null}
          </Card>
          <Card title="Notes" bodyClass="card-body stack">
            <div className="timeline">
              <AnimatePresence initial={false}>
                {inv.notes.map((n, i) => (
                  <motion.div key={`${n.created_at}-${i}`} className="item" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }}>
                    <div>{n.text}</div><div className="meta">{n.author} · {fmt.date(n.created_at)}</div>
                  </motion.div>
                ))}
              </AnimatePresence>
              {!inv.notes.length ? <span className="muted small">No notes yet — record what you checked and what you found.</span> : null}
            </div>
            <textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="Add a note…" onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) addNote(); }} />
            <div className="row"><span className="faint small">Ctrl + Enter to save</span><span className="spacer" /><button className="btn" onClick={addNote} disabled={saving || !note.trim()}>{saving ? <Spinner /> : <Icon d={I.note} />}Add note</button></div>
          </Card>
          <Card title="Evidence" sub={`${inv.evidence.length} item(s)`} bodyClass="card-body stack">
            <div className="timeline">
              {inv.evidence.map((e, i) => (
                <motion.div key={e.evidence_id} className="item" initial={{ opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: Math.min(i, 12) * 0.03 }}>
                  <div className="row"><b className="small">{e.kind.replace(/_/g, " ")}</b><ConfBadge c={e.confidence} /></div>
                  <div className="small">{e.description}</div><div className="meta">{e.evidence_id} · {fmt.date(e.captured_at)}</div>
                </motion.div>
              ))}
            </div>
          </Card>
        </div>
      </div>
      {inv.connection_id ? (
        <NodePicker open={!!picker} onClose={() => setPicker(null)} cid={inv.connection_id}
          title={picker === "lineage" ? "Trace lineage of…" : "Assess impact of changing…"}
          onPick={async (nodeId) => { await post(`${key}/${picker}`, { node_id: nodeId, connection_id: inv.connection_id }); await mutate(); toast(picker === "lineage" ? "Lineage added" : "Impact added", "ok"); }} />
      ) : null}
    </Stagger>
  );
}

/** search the connection's objects and pick one */
export function NodePicker({ open, onClose, cid, title, onPick }: { open: boolean; onClose: () => void; cid: string; title: string; onPick: (id: string) => Promise<void> }) {
  const [q, setQ] = useState("");
  const [nodes, setNodes] = useState<{ id: string; name: string; type: string }[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const seq = useRef(0);
  useEffect(() => {
    if (!open) return;
    const mine = ++seq.current;
    const t = setTimeout(async () => {
      try {
        const r = await get<{ id: string; name: string; type: string }[]>(`/connections/${enc(cid)}/nodes${qs({ search: q, limit: 60 })}`);
        if (mine === seq.current) { setNodes(r); setErr(null); }
      } catch (e) { if (mine === seq.current) setErr((e as Error).message); }
    }, 200);
    return () => clearTimeout(t);
  }, [open, q, cid]);
  async function pick(id: string) {
    try { await onPick(id); onClose(); } catch (e) { toast((e as Error).message, "bad"); }
  }
  return (
    <Modal open={open} onClose={onClose} title={title} confirm={null}>
      <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search visuals, measures, columns…" />
      <div style={{ maxHeight: 340, overflow: "auto" }}>
        <AlertBox kind="bad" items={err ? [err] : null} />
        {!nodes ? <Loading /> : nodes.length ? (
          <table className="data"><tbody>
            {nodes.map((n) => {
              const t = nodeType(n.type);
              return (
                <tr key={n.id} className="clickable" tabIndex={0} onClick={() => pick(n.id)} onKeyDown={(e) => e.key === "Enter" && pick(n.id)}>
                  <td><span className="type-dot" style={{ background: t.color }} />{n.name}</td><td><Badge>{t.label}</Badge></td>
                </tr>
              );
            })}
          </tbody></table>
        ) : <div className="empty small">No matches.</div>}
      </div>
    </Modal>
  );
}
