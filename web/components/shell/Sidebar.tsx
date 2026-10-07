"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { AnimatePresence, LayoutGroup, motion } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { del, overviewKey, syncConnection, uploadModel, useApi, refresh } from "@/lib/api";
import { fmt } from "@/lib/format";
import { collapse, spring } from "@/lib/motion";
import { store } from "@/lib/nav";
import { href, modelPath, resolveWs, useWsRoute, wsPath, type ModelTab } from "@/lib/routes";
import { pageMeasureIds } from "@/lib/overview";
import type { ModelFile, Overview } from "@/lib/types";
import { toast, useConfirm } from "@/components/feedback";
import { I, Icon, Spinner } from "@/components/ui";
import { useAuth } from "@/components/auth/AuthProvider";

const TOOLS = [
  { href: href.reconcile, label: "Reconcile", icon: I.reconcile },
  { href: href.results, label: "Results", icon: I.results },
  { href: href.templates, label: "Templates", icon: I.template },
  { href: "/investigations", label: "Investigations", icon: I.investigate },
  { href: href.lineage(), label: "Lineage explorer", icon: I.lineage },
  { href: href.sources, label: "Sources & files", icon: I.sources },
];

/** the sliding highlight behind the active item (shared layout animation) */
function ActivePill() {
  return <motion.span layoutId="nav-active" className="nav-pill" transition={spring} />;
}

export function Sidebar() {
  const { data: models, error, isLoading } = useApi<ModelFile[]>("/model-files");
  const [filter, setFilter] = useState("");
  const [uploading, setUploading] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const router = useRouter();
  const pathname = usePathname();
  const signedIn = useAuth().state.status === "signed-in";

  async function onUpload(file: File) {
    setUploading(file.name);
    try {
      const res = await uploadModel(file);
      toast(`Read ${res.name}: ${res.counts.pages} pages, ${res.counts.measures} measures`, "ok");
      await refresh("/model-files");
      router.push(href.dashboard({ connection_id: res.connection_id, name: res.name }));
    } catch (e) {
      toast((e as Error).message, "bad");
    } finally {
      setUploading(null);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  const names = useMemo(() => {
    const n: Record<string, number> = {};
    for (const m of models || []) n[m.name] = (n[m.name] || 0) + 1;
    return n;
  }, [models]);

  return (
    <aside className="sidebar">
      <Link href="/" className="brand">
        <motion.div className="brand-mark" whileHover={{ rotate: -6, scale: 1.05 }} transition={spring}>
          <svg viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round"><path d="M4 17l5-6 4 4 7-9" /></svg>
        </motion.div>
        <div><div className="brand-name">BI Trust Platform</div><div className="brand-sub">Reconcile · Explain · Trace</div></div>
      </Link>

      <div className="dash-head">
        <span>Dashboards</span>
        <motion.button className="dash-upload" title="Upload a Power BI file (.pbix, .pbit, .bim, zipped PBIP)" whileHover={{ scale: 1.08 }} whileTap={{ scale: 0.92 }}
          onClick={() => fileRef.current?.click()}>
          <Icon d={I.plus} />
        </motion.button>
        <input ref={fileRef} type="file" hidden accept=".pbix,.pbit,.bim,.zip" onChange={(e) => e.target.files?.[0] && onUpload(e.target.files[0])} />
      </div>
      <div className="dash-filter"><input type="search" placeholder="Filter dashboards & pages…" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter dashboards and pages" /></div>

      <LayoutGroup id="sidebar">
        <nav className="dash-nav" aria-label="Dashboards">
          <AnimatePresence>
            {uploading ? (
              <motion.div className="dash-uploading" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}>
                <Spinner />Reading {uploading}…
              </motion.div>
            ) : null}
          </AnimatePresence>
          {error ? <div className="dash-empty bad">{error.message}</div> : null}
          {isLoading ? <div className="dash-loading"><Spinner />Loading…</div> : null}
          {models && !models.length ? <div className="dash-empty">No dashboards yet.<br />Use <b>+</b> above to upload a .pbix.</div> : null}
          {models?.map((m, i) => (
            <motion.div key={m.connection_id} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ ...spring, delay: i * 0.04 }}>
              <DashboardItem model={m} filter={filter.trim().toLowerCase()} dup={names[m.name] > 1} />
            </motion.div>
          ))}
        </nav>

        <div className="dash-head tools-head"><span>Tools</span></div>
        <nav className="tool-nav" aria-label="Tools">
          {TOOLS.map((t) => {
            const active = pathname === t.href || pathname.startsWith(`${t.href}/`);
            return (
              <Link key={t.href} href={t.href} className={active ? "active" : ""}>
                {active ? <ActivePill /> : null}
                <Icon d={t.icon} /><span>{t.label}</span>
              </Link>
            );
          })}
        </nav>
      </LayoutGroup>
      <div className="sidebar-foot"><span className="dot" />{signedIn ? "Private to your account" : "Runs locally · nothing leaves this machine"}</div>
    </aside>
  );
}

function DashboardItem({ model, filter, dup }: { model: ModelFile; filter: string; dup: boolean }) {
  const cid = model.connection_id;
  const route = useWsRoute();
  const router = useRouter();
  const confirm = useConfirm();
  const isCurrent = route?.cid === cid;
  const [open, setOpen] = useState<boolean>(() => store.get<string[]>("ws-open", []).includes(cid));
  const [busy, setBusy] = useState(false);
  const expanded = open || isCurrent || !!filter;
  const { data: ov, error } = useApi<Overview>(expanded ? overviewKey(cid) : null, { dedupingInterval: 600000 });

  useEffect(() => {
    const list = new Set(store.get<string[]>("ws-open", []));
    if (open) list.add(cid); else list.delete(cid);
    store.set("ws-open", [...list]);
  }, [open, cid]);

  const nameHit = model.name.toLowerCase().includes(filter);
  const pages = (ov?.pages || []).filter((p) => !filter || nameHit || p.name.toLowerCase().includes(filter));
  if (filter && ov && !nameHit && !pages.length) return null;

  const curP = isCurrent && ov && !route!.model ? resolveWs(ov, route!).pageId : null;
  const curTab = isCurrent && route!.model ? route!.model[0] || "overview" : null;
  const c = ov?.counts;
  const dash = { connection_id: cid, name: model.name };

  async function reread(e: React.MouseEvent) {
    e.stopPropagation();
    setBusy(true);
    try { await syncConnection(cid); toast("Re-read the file", "ok"); } catch (err) { toast((err as Error).message, "bad"); } finally { setBusy(false); }
  }
  async function remove(e: React.MouseEvent) {
    e.stopPropagation();
    if (!(await confirm({ title: `Remove “${model.name}”?`, text: "The dashboard file and everything read from it are removed from this machine." }))) return;
    try {
      await del(`/model-files/${encodeURIComponent(cid)}`);
      await refresh("/model-files");
      if (isCurrent) router.push("/");
      toast("Dashboard removed", "ok");
    } catch (err) { toast((err as Error).message, "bad"); }
  }

  const tool = (tab: ModelTab, label: string, icon: string, count?: number, bad?: boolean) => (
    <Link key={tab} href={modelPath(dash, tab)} className={`page-link tool ${curTab === tab ? "active" : ""}`}>
      {curTab === tab ? <ActivePill /> : null}
      <Icon d={icon} /><span className="pl-name">{label}</span>
      {count != null ? <span className={`pl-count ${bad ? "bad" : ""}`}>{fmt.int(count)}</span> : null}
    </Link>
  );

  return (
    <div className={`dash ${expanded ? "open" : ""} ${isCurrent ? "current" : ""}`}>
      <div className="dash-row">
        <button className="dash-toggle" onClick={() => setOpen(!expanded)} aria-expanded={expanded} title={model.file_name || model.name}>
          <motion.span className="chev-wrap" animate={{ rotate: expanded ? 90 : 0 }} transition={spring}><Icon d={I.chev} className="chev" /></motion.span>
          <span className="dash-ico"><Icon d={I.dash} /></span>
          <span className="dash-label">
            <span className="dash-name">{model.name}</span>
            {dup ? <span className="dash-sub">read {fmt.date(model.last_synced_at)}</span> : null}
          </span>
        </button>
        <span className="dash-tools">
          <button onClick={reread} title="Re-read the file" className={busy ? "spin" : ""}><Icon d={I.reload} /></button>
          <button onClick={remove} title="Remove this dashboard" className="danger"><Icon d={I.trash} /></button>
        </span>
      </div>
      <AnimatePresence initial={false}>
        {expanded ? (
          <motion.div className="dash-pages" key="pages" initial="closed" animate="open" exit="closed" variants={collapse} style={{ overflow: "hidden" }}>
            {error ? <div className="dash-empty small bad dash-err" title={error.message}><b>Couldn&apos;t read this file.</b> <span>{error.message}</span><button className="btn ghost sm" onClick={() => syncConnection(cid).catch((e) => toast((e as Error).message, "bad"))}>Retry</button></div> : !ov ? <div className="dash-loading"><Spinner />Reading pages…</div> : (
              <>
                {!filter ? (
                  <>
                    <div className="dash-sec">Model</div>
                    {tool("overview", "Overview", I.overview)}
                    {tool("health-check", "Health check", I.health, c?.issues, (c?.issues || 0) > 0)}
                    {tool("relationships", "Relationships", I.rel, c?.relationships)}
                    {tool("tables", "Tables", I.table, c?.tables)}
                    {tool("measures", "Measures", I.sigma, c?.measures)}
                    {tool("report-map", "Report map", I.report, c?.visuals)}
                    <div className="dash-sec">Pages</div>
                  </>
                ) : null}
                {pages.length ? pages.map((p) => {
                  const n = pageMeasureIds(p).size;
                  const active = curP === p.id;
                  return (
                    <Link key={p.id} href={wsPath(dash, ov, { page: p.id })} className={`page-link ${active ? "active" : ""}`} title={p.name}>
                      {active ? <ActivePill /> : null}
                      <Icon d={I.page} /><span className="pl-name">{p.name}</span>
                      {p.hidden ? <span className="pl-tag">hidden</span> : null}
                      <span className="pl-count" title={`${n} measure(s) on this page`}>{n}</span>
                    </Link>
                  );
                }) : <div className="dash-empty small">{ov.pages.length ? "No pages match." : "This file has no report pages (model only)."}</div>}
              </>
            )}
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
