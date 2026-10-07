"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { overviewKey, useApi } from "@/lib/api";
import { fmt, signClass } from "@/lib/format";
import { fadeUp, softSpring, spring } from "@/lib/motion";
import { store } from "@/lib/nav";
import { href, modelPath, recordId, resolveWs, safeDecode, useWsRoute, wsPath, type Dash, type WsRoute } from "@/lib/routes";
import { pageMeasures } from "@/lib/overview";
import type { LxNode, ModelFile, Overview } from "@/lib/types";
import { useCrumbs } from "@/components/shell/Crumbs";
import { LineageCanvas, type Explorer } from "@/components/lineage/LineageCanvas";
import { ModelDropzone } from "@/components/UploadZone";
import { AlertBox, Badge, I, Icon, Kpi, Loading, Stagger } from "@/components/ui";
import { Inspect } from "./Inspect";
import { GoRow } from "@/components/DataTable";
import { RunBadge } from "@/components/recon/bits";

let restored = false; // reopen the last place once per visit
const LAST = "ws-last-path";

/** "/" — the dashboards home */
export function Workspace() {
  const router = useRouter();
  const { data: models } = useApi<ModelFile[]>("/model-files");
  useEffect(() => {
    if (restored || !models) return;
    restored = true;
    const last = store.get<string | null>(LAST, null);
    const cid = typeof last === "string" && last.startsWith("/dashboards/") ? recordId("dashboard", last.split("/")[2] || "") : null;
    if (cid && models.some((x) => x.connection_id === cid)) router.replace(last!);
  }, [models, router]);
  return <Home models={models} />;
}

/**
 * Layout for everything under /dashboards/<dashboard>. It stays mounted while
 * you move between pages and measures, so the lineage canvas animates from
 * one focus to the next instead of being rebuilt. Model views render their
 * own layout (children).
 */
export function DashboardWorkspace({ children }: { children: ReactNode }) {
  const route = useWsRoute();
  useEffect(() => { restored = true; }, []);
  if (!route) return <>{children}</>;
  if (route.model) return <>{children}</>;
  // the page files under this layout render nothing; they're mounted so every address is a real route
  return <>{children}<DashboardArea route={route} /></>;
}

/* ------------------------------------------------------------------ home */
function Home({ models }: { models?: ModelFile[] }) {
  useCrumbs([{ label: "Dashboards" }]);
  if (!models) return <div className="ws-loading"><Loading /></div>;
  return (
    <Stagger className="page-stack">
      <motion.div className="hero" variants={fadeUp}>
        <div className="eyebrow">Workspace</div>
        <h1>{models.length ? "Your dashboards" : "Explore your first dashboard"}</h1>
        <p>Open a dashboard to browse its pages and measures, trace any number back to its source, and check the model&apos;s health.</p>
      </motion.div>
      {models.length ? (
        <motion.div className="dash-grid" variants={{ hidden: {}, show: { transition: { staggerChildren: 0.05 } } }}>
          {models.map((md) => (
            <motion.div key={md.connection_id} variants={fadeUp} whileHover={{ y: -4 }} transition={spring}>
              <Link href={href.dashboard(md)} className="dash-card">
                <span className="dash-card-ico"><Icon d={I.dash} /></span>
                <span className="dash-card-body">
                  <b>{md.name}</b>
                  <span>{md.file_name || md.platform}</span>
                  <em>Read {fmt.date(md.last_synced_at)}</em>
                </span>
                <span className="dash-card-go"><Icon d={I.arrow} /></span>
              </Link>
            </motion.div>
          ))}
        </motion.div>
      ) : null}
      <motion.div variants={fadeUp}><ModelDropzone /></motion.div>
      <Activity />
    </Stagger>
  );
}

/* reconciliation activity across the workspace (the old overview page) */
interface Stats {
  reconciliations: number; reconciliations_with_differences: number; average_match_rate_pct: number | null; rows_compared: number;
  open_investigations: number; investigations: number; templates: number; connections: number; files: number;
  recent_runs: { reconciliation_id: string; name: string; status: string; match_rate_pct: number; difference: number; created_at: string }[];
}
function Activity() {
  const { data: s } = useApi<Stats>("/stats");
  if (!s) return null;
  return (
    <motion.section className="stack activity" variants={fadeUp}>
      <div className="row"><h2 className="section-title">Reconciliation activity</h2><span className="spacer" /><Link className="btn sm primary" href="/reconcile"><Icon d={I.plus} />New reconciliation</Link></div>
      <motion.div className="kpis" variants={{ hidden: {}, show: { transition: { staggerChildren: 0.05 } } }} initial="hidden" animate="show">
        <Kpi label="Reconciliations run" value={s.reconciliations} hint={`${fmt.int(s.reconciliations_with_differences)} with differences`} href={href.results} />
        <Kpi label="Average match rate" value={s.average_match_rate_pct} display={fmt.rate(s.average_match_rate_pct)} hint={`${fmt.int(s.rows_compared)} keys compared`} />
        <Kpi label="Open investigations" value={s.open_investigations} hint={`${fmt.int(s.investigations)} in total`} kind={s.open_investigations ? "warn" : ""} href={href.investigations("INVESTIGATING")} />
        <Kpi label="Saved templates" value={s.templates} hint="re-run in one click" href="/templates" />
        <Kpi label="Sources" value={s.connections} hint={`connections · ${fmt.int(s.files)} files`} href="/sources" />
      </motion.div>
      {s.recent_runs.length ? (
        <div className="card"><div className="card-head"><h2>Recent reconciliations</h2><span className="spacer" /><Link href={href.results} className="small">View all</Link></div>
          <div className="card-body tight"><div className="table-wrap"><table className="data">
            <thead><tr><th>Name</th><th>Result</th><th className="num">Match rate</th><th className="num">Difference</th><th>Run</th></tr></thead>
            <tbody>{s.recent_runs.map((r, i) => (
              <GoRow key={r.reconciliation_id} href={href.result(r.reconciliation_id, r.name)} i={i}>
                <td>{r.name}</td><td><RunBadge s={r.status} /></td><td className="num">{fmt.rate(r.match_rate_pct)}</td>
                <td className={`num ${signClass(r.difference)}`}>{fmt.signed(r.difference)}</td><td className="muted small">{fmt.date(r.created_at)}</td>
              </GoRow>
            ))}</tbody>
          </table></div></div></div>
      ) : null}
    </motion.section>
  );
}

/* ---------------------------------------------------- dashboard selected */
function DashboardArea({ route }: { route: WsRoute }) {
  const cid = route.cid;
  const router = useRouter();
  const pathname = usePathname();
  const { data: ov, error } = useApi<Overview>(overviewKey(cid), { dedupingInterval: 600000 });
  const dash: Dash = { connection_id: cid, name: ov?.name };
  const { pageId: pid, objectId: mid } = ov ? resolveWs(ov, route) : { pageId: null, objectId: null };
  const page = ov?.pages.find((x) => x.id === pid) || null;
  const lost = !!ov && (!!(route.page && !pid) || !!(route.object && !mid));
  const canonical = ov && !lost ? wsPath(dash, ov, { page: pid, object: mid }) : null;

  // raw ids, old names or a renamed dashboard: show the readable address
  useEffect(() => {
    if (!canonical) return;
    if (safeDecode(canonical) !== safeDecode(pathname)) router.replace(canonical, { scroll: false });
    else store.set(LAST, canonical);
  }, [canonical, pathname, router]);

  useCrumbs([
    { label: "Dashboards", href: "/" },
    { label: ov?.name || "…", href: ov ? href.dashboard(dash) : undefined },
    ...(page ? [{ label: page.name, href: wsPath(dash, ov, { page: page.id }) }] : []),
    ...(mid ? [{ label: objectName(ov, mid) }] : []),
  ]);
  if (error) return <AlertBox kind="bad" items={[error.message]} />;
  if (!ov) return <div className="ws-loading"><Loading text="Reading the dashboard…" /></div>;
  if (lost) {
    const what = route.object && !mid ? route.object : null;
    return <Center icon={I.search} title="Not found in this dashboard"
      text={`There's no ${what ? (what.kind === "raw" ? "object" : what.kind) : "page"} called “${what ? what.slug : route.page}” in ${ov.name}. It may have been renamed or removed in the file.`}
      action={<Link className="btn primary" href={href.dashboard(dash)}>Back to {ov.name}</Link>} />;
  }
  if (mid) return <MeasureView key={cid} cid={cid} pid={pid} mid={mid} ov={ov} />;
  if (page) {
    const n = pageMeasures(ov, page).length;
    return (
      <Center icon={I.page} title={page.name}
        text={n ? `${n} measure${n === 1 ? "" : "s"} on this page — pick one on the right to see its full lineage.` : "No measures on this page. Switch to “All measures” on the right, or search."} />
    );
  }
  return <DashboardSummary cid={cid} ov={ov} />;
}

/** display name for any object id (measure, table, column, visual) */
function objectName(ov: Overview | undefined, id: string) {
  if (!ov) return "…";
  const m = ov.measures.find((x) => x.id === id);
  if (m) return m.name;
  for (const p of ov.pages) { const v = p.visuals.find((x) => x.id === id); if (v) return v.title; }
  return id;
}

function Center({ icon, title, text, action }: { icon: string; title: string; text: string; action?: ReactNode }) {
  return (
    <motion.div className="ws-empty" initial="hidden" animate="show" variants={{ hidden: {}, show: { transition: { staggerChildren: 0.07 } } }}>
      <motion.div className="ws-empty-ico" variants={fadeUp} animate={{ y: [0, -6, 0] }} transition={{ y: { duration: 4, repeat: Infinity, ease: "easeInOut" } }}><Icon d={icon} /></motion.div>
      <motion.h2 variants={fadeUp}>{title}</motion.h2>
      <motion.p variants={fadeUp}>{text}</motion.p>
      {action ? <motion.div variants={fadeUp}>{action}</motion.div> : null}
    </motion.div>
  );
}

function DashboardSummary({ cid, ov }: { cid: string; ov: Overview }) {
  const c = ov.counts;
  const top = ov.insights.filter((i) => i.severity === "high" || i.severity === "medium").slice(0, 5);
  const dash: Dash = { connection_id: cid, name: ov.name };
  const cards: [Parameters<typeof modelPath>[1] & string, string, string, string, number | null, boolean][] = [
    ["overview", I.overview, "Overview", "Data sources, health summary, tables by type", null, false],
    ["health-check", I.health, "Health check", "Broken references, unused objects, risky modelling", c.issues, c.issues > 0],
    ["relationships", I.rel, "Relationships", "Model diagram, cardinality, filter direction", c.relationships, false],
    ["tables", I.table, "Tables & columns", "How each table is loaded, Power Query steps, data preview", c.tables, false],
    ["measures", I.sigma, "All measures", "Every measure explained, with its DAX and usage", c.measures, false],
    ["report-map", I.report, "Report map", "Every page and visual, the fields it shows and its filters", c.visuals, false],
  ];
  return (
    <Stagger className="page-stack">
      <motion.div className="hero" variants={fadeUp}>
        <span className="ws-kind"><Icon d={I.dash} />Dashboard</span>
        <h1>{ov.name}</h1>
        <p>Pick a page on the left to see its measures, or open any view of the model below.</p>
      </motion.div>
      <AlertBox kind="warn" items={ov.warnings} />
      <motion.div className="kpis kpis-6" variants={{ hidden: {}, show: { transition: { staggerChildren: 0.05 } } }}>
        <Kpi label="Pages" value={c.pages} />
        <Kpi label="Visuals" value={c.visuals} />
        <Kpi label="Measures" value={c.measures} hint={`${fmt.int(c.unused_measures)} unused`} />
        <Kpi label="Tables" value={c.tables} hint={`${fmt.int(c.columns)} columns`} />
        <Kpi label="Relationships" value={c.relationships} />
        <Kpi label="Issues" value={c.issues} hint="high / medium" kind={c.issues ? "warn" : "ok"} href={modelPath(dash, "health-check")} />
      </motion.div>
      <motion.div className="ws-cards" variants={{ hidden: {}, show: { transition: { staggerChildren: 0.04 } } }}>
        {cards.map(([tab, icon, title, text, value, warn]) => (
          <motion.div key={tab} variants={fadeUp} whileHover={{ y: -3 }} whileTap={{ scale: 0.98 }} transition={spring}>
            <Link className={`ws-card ${warn ? "warn" : ""}`} href={modelPath(dash, tab)}>
              <span className="ws-card-ico"><Icon d={icon} /></span>
              <span className="ws-card-body"><b>{title}</b><span>{text}</span></span>
              {value != null ? <span className="ws-card-val">{fmt.int(value)}</span> : <span className="ws-card-go"><Icon d={I.arrow} /></span>}
            </Link>
          </motion.div>
        ))}
      </motion.div>
      {top.length ? (
        <motion.section className="card" variants={fadeUp}>
          <div className="card-head"><h2>Needs attention</h2><span className="spacer" /><Link className="small" href={modelPath(dash, "health-check")}>All findings →</Link></div>
          <div className="card-body stack">
            {top.map((i, n) => (
              <motion.div key={n} className="row attn" initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ ...softSpring, delay: 0.2 + n * 0.05 }}>
                <Badge kind={i.severity === "high" ? "bad" : "warn"}>{i.severity}</Badge><span className="small">{i.title}</span>
              </motion.div>
            ))}
          </div>
        </motion.section>
      ) : null}
    </Stagger>
  );
}

/* ---------------------------------------------------------- measure view */
function MeasureView({ cid, pid, mid, ov }: { cid: string; pid: string | null; mid: string; ov: Overview }) {
  const router = useRouter();
  const [insp, setInsp] = useState<{ mid: string; node: LxNode } | null>(null);
  const inspected = insp?.mid === mid ? insp.node : null;  // a new measure starts with the panel closed
  const setInspected = useCallback((node: LxNode | null) => setInsp(node ? { mid, node } : null), [mid]);
  const explorer = useRef<Explorer | null>(null);
  const measure = ov.measures.find((x) => x.id === mid);

  useEffect(() => {
    const t = setTimeout(() => explorer.current?.fit(), 450);  // canvas resized
    return () => clearTimeout(t);
  }, [inspected]);

  const jump = useCallback((id: string) => {
    const ex = explorer.current;
    const n = ex?.nodes.get(id);
    if (ex && n) {
      ex.sel = id; ex.sel2 = null;
      ex.applyHighlight();
      ex.centerOn(id);
      setInspected(n);
    } else router.push(wsPath({ connection_id: cid, name: ov.name }, ov, { page: pid, object: id }));  // not on the canvas: trace it
  }, [cid, pid, ov, router, setInspected]);

  return (
    <motion.div className={`ws-main ${inspected ? "inspecting" : ""}`} initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
      <div className="ws-title">
        <motion.span className="ws-kind" layout><Icon d={I.sigma} />{measure ? "Measure" : "Lineage"}</motion.span>
        <AnimatePresence mode="wait" initial={false}>
          <motion.h1 key={mid} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.18 }}>
            {measure?.name || objectName(ov, mid)}
          </motion.h1>
        </AnimatePresence>
        <span className="ws-hint">Click a box to trace its line · <b>double-click</b> for details · <b>+ / −</b> expands or collapses a step</span>
      </div>
      <LineageCanvas cid={cid} root={mid} up={10} down={10} onInspect={setInspected} onReady={(ex) => { explorer.current = ex; }} />
      <AnimatePresence mode="wait">
        {inspected ? (
          <Inspect key={inspected.id} cid={cid} pid={pid} ov={ov} node={inspected} rootId={mid} onJump={jump} onClose={() => setInspected(null)} />
        ) : null}
      </AnimatePresence>
    </motion.div>
  );
}
