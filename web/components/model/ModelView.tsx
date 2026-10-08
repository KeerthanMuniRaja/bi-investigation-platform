"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { AnimatePresence, LayoutGroup, motion } from "motion/react";
import { useEffect, useState } from "react";
import { overviewKey, syncConnection, useApi } from "@/lib/api";
import { fmt } from "@/lib/format";
import { fadeUp, spring } from "@/lib/motion";
import { MODEL_TABS, href, modelPath, safeDecode, slugsOf, useWsRoute, type ModelTab, type WsRoute } from "@/lib/routes";
import type { Overview } from "@/lib/types";
import { useCrumbs } from "@/components/shell/Crumbs";
import { toast } from "@/components/feedback";
import { AlertBox, Badge, EmptyState, I, Icon, Loading, Spinner } from "@/components/ui";
import { HealthTab, MeasuresTab, OverviewTab, RelationshipsTab, ReportTab, TablesTab } from "./tabs";

type Tab = ModelTab;
const TAB_LABEL: Record<Tab, string> = { overview: "Overview", "report-map": "Report map", measures: "Measures", tables: "Tables", relationships: "Relationships", "health-check": "Health check" };

/** /dashboards/<dashboard>/model/<tab>[/<measure>] */
export function ModelView() {
  const route = useWsRoute();
  // Next keeps recently visited pages mounted (hidden): after you leave the
  // model view the address is another page's, so there's nothing to show
  if (!route?.model) return null;
  return <ModelPage route={route} />;
}

function ModelPage({ route }: { route: WsRoute }) {
  const router = useRouter();
  const pathname = usePathname();
  const cid = route.cid;
  const [seg, measureSlug] = route.model || [];
  const tab = (MODEL_TABS as readonly string[]).includes(seg || "overview") ? ((seg || "overview") as Tab) : null;
  const { data: ov, error } = useApi<Overview>(overviewKey(cid), { dedupingInterval: 600000 });
  const [busy, setBusy] = useState(false);
  const dash = { connection_id: cid, name: ov?.name };
  const measureId = ov && measureSlug ? slugsOf(ov).measures.toId.get(measureSlug) ?? (ov.measures.some((m) => m.id === measureSlug) ? measureSlug : null) : null;
  const canonical = ov && tab ? modelPath(dash, tab, ov, measureId) : null;
  useEffect(() => {
    if (canonical && safeDecode(canonical) !== safeDecode(pathname)) router.replace(canonical, { scroll: false });
  }, [canonical, pathname, router]);
  useCrumbs([{ label: "Dashboards", href: "/" }, { label: ov?.name || "…", href: ov ? href.dashboard(dash) : undefined }, { label: tab ? TAB_LABEL[tab] : "Model" }]);

  if (error) return <AlertBox kind="bad" items={[error.message]} />;
  if (!ov) return <div className="ws-loading"><Loading text="Reading the model…" /></div>;
  if (!tab) {
    return <EmptyState title="No such view" text={`“${seg}” isn't a view of this model. Try one of the tabs below.`} icon={I.search}>
      <div className="row" style={{ justifyContent: "center" }}>{MODEL_TABS.map((t) => <Link key={t} className="btn sm" href={modelPath(dash, t)}>{TAB_LABEL[t]}</Link>)}</div>
    </EmptyState>;
  }
  const c = ov.counts;
  const issues = ov.insights.filter((i) => i.severity === "high" || i.severity === "medium").length;
  const relIssues = ov.relationships.filter((r) => r.missing.length || r.type_mismatch || r.cardinality === "many-to-many").length;
  const tabs: [Tab, string, number | null, boolean][] = [
    ["overview", "Overview", null, false], ["report-map", "Report map", c.pages, false], ["measures", "Measures", c.measures, false],
    ["tables", "Tables", c.tables, false], ["relationships", "Relationships", c.relationships, relIssues > 0], ["health-check", "Health check", issues, issues > 0],
  ];

  async function reread() {
    setBusy(true);
    try { await syncConnection(cid); toast("Re-read the file", "ok"); } catch (e) { toast((e as Error).message, "bad"); } finally { setBusy(false); }
  }

  return (
    <motion.div className="page-stack" initial="hidden" animate="show" variants={{ hidden: {}, show: { transition: { staggerChildren: 0.05 } } }}>
      <motion.div className="page-head" variants={fadeUp}>
        <div>
          <div className="row">{ov.format ? <Badge kind="info">{ov.format.toUpperCase()}</Badge> : null}<span className="faint small">{cid}</span></div>
          <h1 style={{ marginTop: 8 }}>{ov.name}</h1>
          <p>{fmt.int(c.visuals)} visuals on {c.pages} pages · {fmt.int(c.measures)} measures · {c.tables} tables · {c.relationships} relationships · {c.data_sources} data source(s)</p>
        </div>
        <div className="row">
          <button className="btn ghost" onClick={reread} disabled={busy}>{busy ? <Spinner /> : <Icon d={I.reload} />}Re-read file</button>
          <Link className="btn primary" href={href.dashboard(dash)}><Icon d={I.lineage} />Open in workspace</Link>
        </div>
      </motion.div>
      <AlertBox kind="warn" items={ov.warnings} />
      <motion.div className="card tabs-card" variants={fadeUp}>
        <LayoutGroup id="model-tabs">
          <div className="tabs">
            {tabs.map(([id, label, n, bad]) => (
              <Link key={id} href={modelPath(dash, id)} className={`tab ${tab === id ? "active" : ""}`} scroll={false}>
                {label}{n != null ? <span className={`count ${bad ? "bad" : ""}`}>{fmt.int(n)}</span> : null}
                {tab === id ? <motion.span layoutId="tab-underline" className="tab-underline" transition={spring} /> : null}
              </Link>
            ))}
          </div>
        </LayoutGroup>
      </motion.div>
      <AnimatePresence mode="wait">
        <motion.div key={tab} className="stack tab-body" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.22 }}>
          {tab === "overview" ? <OverviewTab cid={cid} ov={ov} /> : null}
          {tab === "report-map" ? <ReportTab cid={cid} ov={ov} /> : null}
          {tab === "measures" ? <MeasuresTab cid={cid} ov={ov} sel={measureId} setSel={(id) => router.replace(modelPath(dash, "measures", ov, id), { scroll: false })} /> : null}
          {tab === "tables" ? <TablesTab cid={cid} ov={ov} /> : null}
          {tab === "relationships" ? <RelationshipsTab ov={ov} /> : null}
          {tab === "health-check" ? <HealthTab ov={ov} /> : null}
        </motion.div>
      </AnimatePresence>
    </motion.div>
  );
}
