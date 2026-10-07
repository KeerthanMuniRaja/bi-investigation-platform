"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { motion } from "motion/react";
import { useEffect, useState } from "react";
import { overviewKey, syncConnection, useApi } from "@/lib/api";
import { fmt } from "@/lib/format";
import { fadeUp } from "@/lib/motion";
import { href, lineagePath, parseWsPath, resolveWs, safeDecode } from "@/lib/routes";
import type { Overview } from "@/lib/types";
import { toast } from "@/components/feedback";
import { useCrumbs } from "@/components/shell/Crumbs";
import { AlertBox, Card, EmptyState, I, Icon, Loading, PageHead, Spinner, Stagger } from "@/components/ui";
import { LineageCanvas } from "./LineageCanvas";

type Conn = { id: string; name: string; platform: string };

/**
 * Full lineage & impact explorer for any connection:
 *   /lineage                                         -> the first dashboard
 *   /lineage/<dashboard>                             -> pick an object
 *   /lineage/<dashboard>/measures/<measure>?up=2&down=3
 *   /lineage/<dashboard>/tables/<table>/columns/<column>
 * The object part reads exactly like the dashboard workspace's.
 */
export function LineagePage() {
  const pathname = usePathname();
  const params = useSearchParams();
  const router = useRouter();
  const { data: conns, error } = useApi<Conn[]>("/connections");
  const [busy, setBusy] = useState(false);
  const [gen, setGen] = useState(0);
  useCrumbs([{ label: "Understand" }, { label: "Lineage & impact" }]);

  const route = parseWsPath(pathname.replace(/^\/lineage(?=\/|$)/, "/dashboards"));
  const conn = route ? conns?.find((c) => c.id === route.cid) : undefined;
  // model files have an overview (names for readable addresses); live connections may not
  const { data: ov, error: ovError } = useApi<Overview>(conn ? overviewKey(conn.id) : null, { dedupingInterval: 600000 });
  const ready = !!conn && (!route?.object || !!ov || !!ovError);
  const focus = route && ready ? (ov ? resolveWs(ov, route).objectId : route.object?.kind === "raw" ? route.object.slug : null) : null;
  const dash = conn ? { connection_id: conn.id, name: conn.name } : null;
  const num = (k: string) => (params.has(k) && Number.isFinite(Number(params.get(k))) ? Math.max(0, Math.min(8, Number(params.get(k)))) : undefined);

  // /lineage on its own opens the first dashboard; raw ids become readable
  useEffect(() => {
    if (!conns?.length) return;
    if (!route) { router.replace(href.lineage({ connection_id: conns[0].id, name: conns[0].name })); return; }
    if (!dash || !ready || (route.object && !focus)) return;
    const canonical = lineagePath(dash, ov, focus, { up: num("up"), down: num("down") });
    const here = pathname + (params.size ? `?${params}` : "");
    if (safeDecode(canonical) !== safeDecode(here)) router.replace(canonical, { scroll: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conns, route?.cid, ready, focus]);

  async function sync() {
    if (!conn) return;
    setBusy(true);
    try {
      const r = await syncConnection(conn.id);
      toast(`Synced: ${fmt.int(r.nodes)} objects, ${fmt.int(r.edges)} dependencies`, "ok");
      setGen((g) => g + 1); // remount the engine so it re-reads the graph
    } catch (e) { toast((e as Error).message, "bad"); } finally { setBusy(false); }
  }

  return (
    <Stagger>
      <PageHead eyebrow="Understand" title="Lineage & impact"
        text={<>Follow any number from its source system to every visual that shows it. Use <b>+</b> / <b>−</b> on a node to expand or collapse a step, click a node to trace its line, shift-click a second one for the path between them.</>}
        actions={conns?.length ? <>
          <select value={conn?.id || ""} onChange={(e) => { const c = conns.find((x) => x.id === e.target.value); if (c) router.push(href.lineage({ connection_id: c.id, name: c.name }), { scroll: false }); }} aria-label="Dashboard or connection">
            {conns.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <button className="btn" onClick={sync} disabled={busy || !conn}>{busy ? <Spinner /> : <Icon d={I.reload} />}Sync</button>
        </> : null} />
      <AlertBox kind="bad" items={error ? [error.message] : null} />
      {!conns ? <Loading /> : !conns.length ? (
        <Card><EmptyState title="No connections yet" text="Open a Power BI file or add a connection first." icon={I.link}>
          <Link className="btn primary" href="/">Open a Power BI file</Link></EmptyState></Card>
      ) : route && !conn ? (
        <Card><EmptyState title="Dashboard not found" text={`Nothing matches “${route.dash}”. It may have been removed — pick another one above.`} icon={I.search} /></Card>
      ) : route?.object && ready && !focus ? (
        <Card><EmptyState title="Not found in this dashboard" text={`There's no ${route.object.kind} called “${route.object.slug}” in ${conn?.name}.`} icon={I.search}>
          {dash ? <Link className="btn primary" href={href.lineage(dash)}>Explore {conn?.name}</Link> : null}</EmptyState></Card>
      ) : !conn || !ready ? <Loading /> : (
        <motion.div variants={fadeUp} className="lineage-full">
          <LineageCanvas key={`${conn.id}:${gen}`} cid={conn.id} root={focus} embedded={false} up={num("up")} down={num("down")} maxSteps={8} className="lineage-host"
            onChange={({ node, up, down }) => history.replaceState(null, "", lineagePath(dash!, ov, node, { up, down }))} />
        </motion.div>
      )}
    </Stagger>
  );
}
