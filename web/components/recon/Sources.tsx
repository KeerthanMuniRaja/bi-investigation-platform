"use client";

import Link from "next/link";
import { AnimatePresence, motion } from "motion/react";
import { useState } from "react";
import { del, enc, get, post, put, refresh, syncConnection, uploadFiles, useApi } from "@/lib/api";
import { fmt } from "@/lib/format";
import { href, modelPath } from "@/lib/routes";
import { FileDropzone } from "@/components/UploadZone";
import { Modal } from "@/components/Modal";
import { toast, useConfirm } from "@/components/feedback";
import { useCrumbs } from "@/components/shell/Crumbs";
import { AlertBox, Badge, Card, EmptyState, I, Icon, Loading, PageHead, Spinner, Stagger } from "@/components/ui";
import type { FileRecord } from "@/lib/types";

interface Conn {
  id: string; name: string; platform: string; mode: string; file_ids: string[]; server_url?: string | null; pbip_dir?: string | null;
  last_synced_at?: string | null; needs_credentials: boolean;
}
interface Platform { id: string; name: string; description: string; fields: string[] }

const SOURCE_PLATFORMS: [string, string][] = [["powerbi", "Power BI"], ["tableau", "Tableau"], ["qlik", "Qlik"], ["looker", "Looker"], ["excel", "Excel / other"], ["generic", "Other BI tool"]];
const FIELDS: Record<string, [string, string, string]> = {
  tenant_id: ["Tenant ID", "text", "Azure AD tenant (directory) ID"],
  client_id: ["Client ID", "text", "App registration (service principal) ID"],
  client_secret: ["Client secret", "password", "Kept in memory only"],
  workspace_id: ["Workspace ID", "text", "Power BI workspace (group) ID"],
  pbip_dir: ["PBIP folder path", "text", "Folder on this server containing *.Report / *.SemanticModel"],
  server_url: ["Server URL", "text", "e.g. https://prod-uk-a.online.tableau.com"],
  site: ["Site", "text", "Site content URL (blank for Default)"],
  pat_name: ["Token name", "text", "Personal access token name"],
  pat_secret: ["Token secret", "password", "Kept in memory only"],
  api_version: ["REST API version", "text", "e.g. 3.21"],
};

export function Sources() {
  useCrumbs([{ label: "Setup" }, { label: "Sources & files" }]);
  const { data: conns, error: e1 } = useApi<Conn[]>("/connections");
  const { data: files, error: e2 } = useApi<FileRecord[]>("/files");
  const { data: platforms } = useApi<Platform[]>("/platforms");
  const confirm = useConfirm();
  const [busy, setBusy] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [secretFor, setSecretFor] = useState<Conn | null>(null);
  const [filesFor, setFilesFor] = useState<Conn | null>(null);
  const pname = Object.fromEntries((platforms || []).map((p) => [p.id, p.name]));
  const reload = () => Promise.all([refresh("/connections"), refresh("/files"), refresh("/model-files")]);

  async function run(key: string, fn: () => Promise<void>) {
    setBusy(key);
    try { await fn(); } catch (e) { toast((e as Error).message, "bad"); } finally { setBusy(null); }
  }
  const sync = (c: Conn) => run(`sync:${c.id}`, async () => {
    const r = await syncConnection(c.id);
    toast(`Synced: ${fmt.int(r.nodes)} objects, ${fmt.int(r.edges)} dependencies${r.warnings.length ? ` · ${r.warnings.length} warning(s)` : ""}`, r.warnings.length ? "" : "ok");
    r.warnings.slice(0, 3).forEach((w) => toast(w));
    await reload();
  });
  const test = (c: Conn) => run(`test:${c.id}`, async () => {
    const h = await get<{ reachable: boolean; error?: string; workspaces?: number; capabilities?: { notes?: string[] } }>(`/connections/${enc(c.id)}/health`);
    if (!h.reachable) throw new Error(h.error || "Not reachable");
    toast(`Connected — ${h.workspaces} workspace(s)/project(s) visible${h.capabilities?.notes?.length ? `. ${h.capabilities.notes[0]}` : ""}`, "ok");
  });
  async function removeConn(c: Conn) {
    if (!(await confirm({ title: `Delete connection "${c.name}"?` }))) return;
    await run(`del:${c.id}`, async () => { await del(`/connections/${enc(c.id)}`); toast("Connection deleted", "ok"); await reload(); });
  }
  async function removeFile(f: FileRecord) {
    if (!(await confirm({ title: `Delete file "${f.name}"?` }))) return;
    await run(`delf:${f.file_id}`, async () => { await del(`/files/${enc(f.file_id)}`); toast("File deleted", "ok"); await reload(); });
  }

  return (
    <Stagger>
      <PageHead title="Sources & files" text={'Connect BI platforms and upload files. Anything any BI tool can export works through the "Exported files" connection.'}
        actions={<button className="btn primary" onClick={() => setAdding(true)} disabled={!platforms}><Icon d={I.plus} />Add connection</button>} />
      <AlertBox kind="bad" items={[e1?.message, e2?.message]} />
      <Card title="Connections" sub={conns ? `${conns.length}` : undefined} bodyClass="card-body tight">
        {!conns ? <Loading /> : conns.length ? (
          <div className="table-wrap"><table className="data">
            <thead><tr><th>Name</th><th>Type</th><th>Status</th><th /></tr></thead>
            <tbody><AnimatePresence initial={false}>{conns.map((c, i) => (
              <motion.tr key={c.id} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ delay: Math.min(i, 12) * 0.025 }}>
                <td><b>{c.name}</b><div className="faint small">{c.id}{c.server_url ? ` · ${c.server_url}` : ""}{c.pbip_dir ? ` · ${c.pbip_dir}` : ""}{c.file_ids.length ? ` · ${c.file_ids.length} file(s)` : ""}</div></td>
                <td>{pname[c.platform] || c.platform}</td>
                <td>{c.needs_credentials ? <Badge kind="warn">Needs credentials</Badge> : c.last_synced_at ? <Badge kind="ok">Synced {fmt.date(c.last_synced_at)}</Badge> : <Badge>Not synced</Badge>}</td>
                <td><div className="row" style={{ justifyContent: "flex-end", flexWrap: "nowrap" }}>
                  {c.needs_credentials ? <button className="btn sm" onClick={() => setSecretFor(c)}><Icon d={I.key} />Credentials</button> : null}
                  {c.platform === "files" ? <button className="btn sm" onClick={() => setFilesFor(c)}><Icon d={I.plus} />Files</button> : null}
                  <button className="btn sm" onClick={() => test(c)} disabled={!!busy}>{busy === `test:${c.id}` ? <Spinner /> : null}Test</button>
                  <button className="btn sm" onClick={() => sync(c)} disabled={!!busy}>{busy === `sync:${c.id}` ? <Spinner /> : <Icon d={I.reload} />}Sync</button>
                  {c.platform === "pbix" ? <Link className="btn sm" href={modelPath({ connection_id: c.id, name: c.name })}>Model</Link> : null}
                  <Link className="btn sm" href={href.lineage({ connection_id: c.id, name: c.name })}><Icon d={I.lineage} />Lineage</Link>
                  <button className="btn ghost sm danger icon-only" onClick={() => removeConn(c)} aria-label={`Delete connection ${c.name}`}><Icon d={I.trash} /></button>
                </div></td>
              </motion.tr>
            ))}</AnimatePresence></tbody>
          </table></div>
        ) : <EmptyState title="No connections yet" text={'Add Power BI, Tableau, a PBIP folder — or upload exports below and create an "Exported files" connection.'} icon={I.sources} />}
      </Card>
      <Card title="Files" sub="Excel workbooks, CSV exports — each tab can be reconciled" bodyClass="card-body stack">
        <FileDropzone multiple onFiles={async (list) => {
          try { const saved = await uploadFiles(list); toast(`Uploaded ${saved.length} file(s)`, "ok"); await refresh("/files"); } catch (e) { toast((e as Error).message, "bad"); }
        }} />
        {files?.length ? (
          <div className="table-wrap"><table className="data">
            <thead><tr><th>File</th><th>Tabs</th><th className="num">Size</th><th>Uploaded</th><th /></tr></thead>
            <tbody><AnimatePresence initial={false}>{files.map((f, i) => (
              <motion.tr key={f.file_id} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ delay: Math.min(i, 12) * 0.025 }}>
                <td>{f.name}<div className="faint small">{f.file_id}</div></td>
                <td className="small">{f.kind === "model" ? <Badge kind="info">Power BI file</Badge> : f.sheets.map((s, k) => <span key={s.name}>{k ? ", " : ""}{s.name} <span className="faint">({s.rows}×{s.columns})</span></span>)}</td>
                <td className="num">{fmt.bytes(f.size)}</td><td className="muted small">{fmt.date(f.uploaded_at)}</td>
                <td><button className="btn ghost sm danger icon-only" onClick={() => removeFile(f)} aria-label={`Delete file ${f.name}`}><Icon d={I.trash} /></button></td>
              </motion.tr>
            ))}</AnimatePresence></tbody>
          </table></div>
        ) : null}
      </Card>
      {platforms ? <AddConnection open={adding} onClose={() => setAdding(false)} platforms={platforms} files={files || []} onDone={reload} /> : null}
      <Secret c={secretFor} onClose={() => setSecretFor(null)} onDone={reload} />
      <AddFiles c={filesFor} onClose={() => setFilesFor(null)} files={files || []} onDone={reload} />
    </Stagger>
  );
}

function AddConnection({ open, onClose, platforms, files, onDone }: { open: boolean; onClose: () => void; platforms: Platform[]; files: FileRecord[]; onDone: () => Promise<unknown> }) {
  // Power BI files become connections by uploading them, not here
  const choosable = platforms.filter((p) => p.id !== "pbix");
  const [platform, setPlatform] = useState(choosable[0]?.id || "");
  const [vals, setVals] = useState<Record<string, string>>({ api_version: "3.21", source_platform: "generic" });
  const [picked, setPicked] = useState<string[]>([]);
  const def = choosable.find((p) => p.id === platform);
  const set = (k: string, v: string) => setVals((s) => ({ ...s, [k]: v }));
  const close = () => { setVals({ api_version: "3.21", source_platform: "generic" }); setPicked([]); onClose(); };
  const dataFiles = files.filter((f) => f.kind !== "model");
  return (
    <Modal open={open} onClose={close} title="Add connection" confirm="Connect" onConfirm={async () => {
      const body: Record<string, unknown> = { platform, file_ids: picked };
      for (const [k, v] of Object.entries(vals)) if (v && (k === "name" || def?.fields.includes(k))) body[k] = v;
      const c = await post<Conn>("/connections", body);
      toast(`Connected "${c.name}"`, "ok");
      if (["pbip", "files"].includes(c.platform)) {
        try { await syncConnection(c.id); } catch (e) { toast(`Connected, but sync failed: ${(e as Error).message}`, "bad"); }
      }
      await onDone();
      close();
      return false;
    }}>
      <label className="field">Type<select value={platform} onChange={(e) => setPlatform(e.target.value)}>{choosable.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
      {def ? <div className="small muted">{def.description}</div> : null}
      <div className="small muted">Have a .pbix / .pbit / .bim file? Drop it on the <Link href="/" onClick={close}>Dashboards</Link> page instead.</div>
      <label className="field">Name<input type="text" value={vals.name || ""} onChange={(e) => set("name", e.target.value)} placeholder="e.g. Finance workspace" /></label>
      <AnimatePresence mode="wait">
        <motion.div key={platform} className="stack" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.18 }}>
          {def?.fields.map((f) => {
            if (f === "model_file") return <div key={f} className="alert info">Use the upload box on the Dashboards page to open a .pbix / .pbit / .bim / PBIP zip.</div>;
            if (f === "file_ids") return (
              <div key={f} className="stack"><span className="small muted">Files in this connection (each file = a report, each tab = a page)</span>
                {dataFiles.length ? dataFiles.map((x) => (
                  <label key={x.file_id} className="check"><input type="checkbox" checked={picked.includes(x.file_id)} onChange={(e) => setPicked((p) => (e.target.checked ? [...p, x.file_id] : p.filter((i) => i !== x.file_id)))} /> {x.name}</label>
                )) : <span className="alert warn">Upload files on this page first.</span>}
              </div>
            );
            if (f === "source_platform") return <label key={f} className="field">Exported from<select value={vals.source_platform} onChange={(e) => set("source_platform", e.target.value)}>{SOURCE_PLATFORMS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>;
            const [label, type, hint] = FIELDS[f] || [f, "text", ""];
            return <label key={f} className="field">{label}<input type={type} value={vals[f] || ""} onChange={(e) => set(f, e.target.value)} placeholder={hint} /></label>;
          })}
        </motion.div>
      </AnimatePresence>
    </Modal>
  );
}

function Secret({ c, onClose, onDone }: { c: Conn | null; onClose: () => void; onDone: () => Promise<unknown> }) {
  const [v, setV] = useState("");
  const pb = c?.platform === "powerbi";
  return (
    <Modal open={!!c} onClose={() => { setV(""); onClose(); }} title="Enter credentials" onConfirm={async () => {
      await put(`/connections/${enc(c!.id)}/secrets`, { [pb ? "client_secret" : "pat_secret"]: v });
      setV("");
      await onDone();
    }}>
      <p className="muted small" style={{ margin: 0 }}>Secrets are kept in server memory only — never written to disk — so they&apos;re needed again after a restart (or set them as environment variables).</p>
      <label className="field">{pb ? "Client secret" : "Personal access token secret"}<input type="password" value={v} onChange={(e) => setV(e.target.value)} /></label>
    </Modal>
  );
}

function AddFiles({ c, onClose, files, onDone }: { c: Conn | null; onClose: () => void; files: FileRecord[]; onDone: () => Promise<unknown> }) {
  const [picked, setPicked] = useState<string[]>([]);
  const avail = c ? files.filter((f) => f.kind !== "model" && !c.file_ids.includes(f.file_id)) : [];
  return (
    <Modal open={!!c} onClose={() => { setPicked([]); onClose(); }} title="Add files to connection" onConfirm={async () => {
      if (picked.length) await post(`/connections/${enc(c!.id)}/files`, { file_ids: picked });
      setPicked([]);
      await onDone();
    }}>
      {avail.length ? avail.map((f) => (
        <label key={f.file_id} className="check"><input type="checkbox" checked={picked.includes(f.file_id)} onChange={(e) => setPicked((p) => (e.target.checked ? [...p, f.file_id] : p.filter((i) => i !== f.file_id)))} /> {f.name}</label>
      )) : <p className="muted">Upload more files first.</p>}
    </Modal>
  );
}
