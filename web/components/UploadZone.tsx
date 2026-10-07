"use client";

import { useRouter } from "next/navigation";
import { motion } from "motion/react";
import { useRef, useState } from "react";
import { refresh, uploadModel } from "@/lib/api";
import { href } from "@/lib/routes";
import { toast } from "./feedback";
import { I, Icon, Spinner } from "./ui";

/** drop zone for Power BI files; opens the dashboard once it's read */
export function ModelDropzone({ compact = false }: { compact?: boolean }) {
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const router = useRouter();

  async function go(file: File) {
    setBusy(file.name);
    try {
      const res = await uploadModel(file);
      toast(`Read ${res.name}: ${res.counts.pages} pages, ${res.counts.measures} measures`, "ok");
      await refresh("/model-files");
      router.push(href.dashboard({ connection_id: res.connection_id, name: res.name }));
    } catch (e) {
      toast((e as Error).message, "bad");
    } finally {
      setBusy(null);
    }
  }

  return (
    <motion.label className={`dropzone ${drag ? "drag" : ""} ${compact ? "compact" : ""}`} whileHover={{ y: -2 }} animate={{ scale: drag ? 1.015 : 1 }}
      onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files[0]; if (f) go(f); }}>
      {busy ? (
        <div className="loading" style={{ justifyContent: "center" }}><Spinner />Reading {busy} — model, Power Query, pages and visuals…</div>
      ) : (
        <>
          <motion.div className="dz-icon" animate={{ y: drag ? -4 : 0 }}><Icon d={I.upload} /></motion.div>
          <strong>Drop a Power BI file here</strong> or <span className="dz-browse">browse</span>
          <div className="small faint" style={{ marginTop: 4 }}>.pbix · .pbit · .bim · zipped PBIP — read locally, nothing leaves this machine</div>
        </>
      )}
      <input ref={input} type="file" hidden accept=".pbix,.pbit,.bim,.zip" onChange={(e) => e.target.files?.[0] && go(e.target.files[0])} />
    </motion.label>
  );
}

/** drop zone for Excel / CSV files (the reconciliation inputs) */
export function FileDropzone({ onFiles, multiple = false, label }: { onFiles: (files: File[]) => Promise<unknown> | unknown; multiple?: boolean; label?: string }) {
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  async function go(list: FileList | null) {
    if (!list?.length) return;
    setBusy(true);
    try { await onFiles(Array.from(multiple ? list : [list[0]])); } finally { setBusy(false); }
  }
  return (
    <motion.label className={`dropzone ${drag ? "drag" : ""}`} whileHover={{ y: -2 }} animate={{ scale: drag ? 1.015 : 1 }}
      onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); go(e.dataTransfer.files); }}>
      {busy ? <div className="loading" style={{ justifyContent: "center" }}><Spinner />Uploading…</div> : (
        <>
          <motion.div className="dz-icon" animate={{ y: drag ? -4 : 0 }}><Icon d={I.upload} /></motion.div>
          {label || "Drop Excel or CSV files here, or"} <span className="dz-browse">browse</span>
          <div className="small faint" style={{ marginTop: 4 }}>.xlsx · .xlsm · .csv · .tsv{multiple ? " — you can select several" : ""}</div>
        </>
      )}
      <input type="file" hidden multiple={multiple} accept=".xlsx,.xlsm,.csv,.tsv,.txt" onChange={(e) => { go(e.target.files); e.target.value = ""; }} />
    </motion.label>
  );
}
