"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "motion/react";
import { useState } from "react";
import { del, enc, post, refresh, uploadFiles, useApi } from "@/lib/api";
import { fmt } from "@/lib/format";
import { fadeUp } from "@/lib/motion";
import { href } from "@/lib/routes";
import { Modal } from "@/components/Modal";
import { toast, useConfirm } from "@/components/feedback";
import { useCrumbs } from "@/components/shell/Crumbs";
import { AlertBox, Card, EmptyState, I, Icon, Loading, PageHead, Spinner, Stagger } from "@/components/ui";
import type { FileRecord } from "@/lib/types";
import { RunBadge, SideLabel, type ReconConfig, type RunSummary } from "./bits";

interface Spec { kind: string; file_id?: string; sheet?: string; connection_id?: string; page_id?: string; visual_id?: string; model_id?: string; dataset_id?: string; filters?: { field: string; values?: unknown[] }[] }
interface Template { definition_id: string; name: string; description?: string | null; left: Spec; right: Spec; config: ReconConfig; run_ids: string[] }

export function Templates() {
  useCrumbs([{ label: "Reconcile" }, { label: "Templates" }]);
  const { data: templates, error } = useApi<Template[]>("/recon-templates");
  const { data: files } = useApi<FileRecord[]>("/files");
  const { data: runs } = useApi<RunSummary[]>("/recon");
  const router = useRouter();
  const confirm = useConfirm();
  const [busy, setBusy] = useState<string | null>(null);
  const [withFiles, setWithFiles] = useState<Template | null>(null);
  const fileName = Object.fromEntries((files || []).map((f) => [f.file_id, f.name]));
  const runById = Object.fromEntries((runs || []).map((r) => [r.reconciliation_id, r]));

  const describe = (spec: Spec) => {
    if (spec.kind === "file") return `File · ${fileName[spec.file_id!] || spec.file_id}${spec.sheet ? ` › ${spec.sheet}` : ""}`;
    if (spec.kind === "bi_visual") {
      const f = (spec.filters || []).filter((x) => x.values?.length).map((x) => `${x.field}=${x.values!.join("/")}`).join(", ");
      return `BI visual · ${spec.visual_id} on ${spec.page_id} (${spec.connection_id})${f ? ` · ${f}` : ""}`;
    }
    if (spec.kind === "bi_query") return `Query on ${spec.model_id} (${spec.connection_id})`;
    return `Saved dataset · ${spec.dataset_id}`;
  };
  async function runNow(t: Template) {
    setBusy(t.definition_id);
    try {
      const res = await post<{ reconciliation: { reconciliation_id: string; name: string } }>(`/recon-templates/${enc(t.definition_id)}/run?limit=1`, {});
      await refresh("/recon");
      router.push(href.result(res.reconciliation.reconciliation_id, res.reconciliation.name));
    } catch (e) { toast((e as Error).message, "bad"); } finally { setBusy(null); }
  }
  async function remove(t: Template) {
    if (!(await confirm({ title: `Delete template "${t.name}"?` }))) return;
    try { await del(`/recon-templates/${enc(t.definition_id)}`); toast("Template deleted", "ok"); await refresh("/recon-templates"); } catch (e) { toast((e as Error).message, "bad"); }
  }

  return (
    <Stagger>
      <PageHead title="Templates" text="Saved reconciliations you repeat — month-end checks, weekly refresh checks. Each run re-extracts both sides fresh and compares them with the saved mapping." />
      <AlertBox kind="bad" items={error ? [error.message] : null} />
      {!templates ? <Loading /> : templates.length ? (
        <AnimatePresence initial={false}>
          {templates.map((t) => {
            const last = runById[t.run_ids[t.run_ids.length - 1]];
            const hasFile = t.left.kind === "file" || t.right.kind === "file";
            return (
              <motion.section key={t.definition_id} className="card tpl-card" variants={fadeUp} layout exit={{ opacity: 0, scale: 0.97 }} whileHover={{ y: -2 }}>
                <div className="card-head"><div><h2>{t.name}</h2>{t.description ? <div className="sub">{t.description}</div> : null}</div><span className="spacer" />
                  {last ? <><RunBadge s={last.status} /><span className="small muted">last run {fmt.date(last.created_at)} · {fmt.rate(last.match_rate_pct)} match</span></> : <span className="small faint">never run</span>}</div>
                <div className="card-body stack">
                  <div className="small"><SideLabel side="left" /> {describe(t.left)}</div>
                  <div className="small"><SideLabel side="right" /> {describe(t.right)}</div>
                  <div className="small muted">{t.config.key_columns.length} key column(s), {t.config.value_columns.length} value column(s) · {t.run_ids.length} run(s)</div>
                  <div className="row">
                    <button className="btn primary" onClick={() => runNow(t)} disabled={!!busy}>{busy === t.definition_id ? <Spinner /> : <Icon d={I.play} />}Run now</button>
                    {hasFile ? <button className="btn" onClick={() => setWithFiles(t)}><Icon d={I.upload} />Run with updated files…</button> : null}
                    <Link className="btn ghost" href={href.template(t.definition_id, t.name)}>History</Link><span className="spacer" />
                    <button className="btn ghost danger" onClick={() => remove(t)}><Icon d={I.trash} />Delete</button>
                  </div>
                </div>
              </motion.section>
            );
          })}
        </AnimatePresence>
      ) : (
        <Card><EmptyState title="No templates yet" text='Run a reconciliation, then choose "Save as template" on its result page.' icon={I.template}>
          <Link className="btn primary" href={href.reconcile}>New reconciliation</Link></EmptyState></Card>
      )}
      <RunWithFiles t={withFiles} onClose={() => setWithFiles(null)} fileName={fileName} />
    </Stagger>
  );
}

function RunWithFiles({ t, onClose, fileName }: { t: Template | null; onClose: () => void; fileName: Record<string, string> }) {
  const router = useRouter();
  const [picked, setPicked] = useState<{ left?: File; right?: File }>({});
  const input = (side: "left" | "right", spec: Spec) => spec.kind !== "file" ? null : (
    <label className="field"><span><SideLabel side={side} /> New version of {fileName[spec.file_id!] || "file"} (optional)</span>
      <input type="file" accept=".xlsx,.xlsm,.csv,.tsv,.txt" onChange={(e) => setPicked((p) => ({ ...p, [side]: e.target.files?.[0] }))} /></label>
  );
  return (
    <Modal open={!!t} onClose={() => { setPicked({}); onClose(); }} title={t ? `Run "${t.name}" with updated files` : ""} confirm="Upload & run" onConfirm={async () => {
      const body: Record<string, string> = {};
      for (const side of ["left", "right"] as const) {
        const f = picked[side];
        if (f) body[`${side}_file_id`] = (await uploadFiles([f]))[0].file_id;
      }
      const res = await post<{ reconciliation: { reconciliation_id: string; name: string } }>(`/recon-templates/${enc(t!.definition_id)}/run?limit=1`, body);
      setPicked({});
      router.push(href.result(res.reconciliation.reconciliation_id, res.reconciliation.name));
    }}>
      {t ? <>{input("left", t.left)}{input("right", t.right)}</> : null}
      <div className="small muted">The same tab and column mapping are used. Leave a file empty to reuse the previous version.</div>
    </Modal>
  );
}
