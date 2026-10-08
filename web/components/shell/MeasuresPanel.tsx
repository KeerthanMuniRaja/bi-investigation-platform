"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { AnimatePresence, LayoutGroup, motion } from "motion/react";
import { useDeferredValue, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { overviewKey, useApi } from "@/lib/api";
import { collapse, spring } from "@/lib/motion";
import { store } from "@/lib/nav";
import { resolveWs, wsPath, type WsRoute } from "@/lib/routes";
import { pageMeasures } from "@/lib/overview";
import type { MeasureRow, Overview } from "@/lib/types";
import { I, Icon, Loading } from "@/components/ui";

type Row = MeasureRow & { inDax?: boolean };

function Mark({ text, needle }: { text: string; needle: string }) {
  if (!needle) return <>{text}</>;
  const i = text.toLowerCase().indexOf(needle);
  if (i < 0) return <>{text}</>;
  return <>{text.slice(0, i)}<mark>{text.slice(i, i + needle.length)}</mark>{text.slice(i + needle.length)}</>;
}

export function MeasuresPanel({ route }: { route: WsRoute }) {
  const router = useRouter();
  const cid = route.cid;
  const { data: ov, error } = useApi<Overview>(overviewKey(cid), { dedupingInterval: 600000 });
  const { pageId: pid, objectId: mid } = ov ? resolveWs(ov, route) : { pageId: null, objectId: null };
  const dash = { connection_id: cid, name: ov?.name };
  const [scopePref, setScopePref] = useState<"page" | "all">(() => store.get("ws-scope", "page"));
  const [query, setQuery] = useState("");
  const [folded, setFolded] = useState<Set<string>>(new Set());
  const deferred = useDeferredValue(query);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const page = ov?.pages.find((p) => p.id === pid) || null;
  const onPage = useMemo(() => (ov && page ? pageMeasures(ov, page) : []), [ov, page]);
  const needle = deferred.trim().toLowerCase();
  const scope = scopePref === "page" && page && onPage.length ? "page" : "all";

  const rows: Row[] = useMemo(() => {
    if (!ov) return [];
    if (!needle) return scope === "page" ? onPage : ov.measures;
    return ov.measures
      .map((m) => ({ m, name: m.name.toLowerCase().includes(needle), other: [m.folder, m.table].some((x) => String(x || "").toLowerCase().includes(needle)),
        dax: String(m.expression || "").toLowerCase().includes(needle) }))
      .filter((r) => r.name || r.other || r.dax)
      .sort((a, b) => Number(b.name) - Number(a.name) || a.m.name.localeCompare(b.m.name))
      .map((r) => ({ ...r.m, inDax: !r.name && !r.other && r.dax }));
  }, [ov, needle, scope, onPage]);

  const groups = useMemo(() => {
    const g = new Map<string, Row[]>();
    for (const m of rows) { const k = m.folder || ""; if (!g.has(k)) g.set(k, []); g.get(k)!.push(m); }
    return [...g.entries()].sort(([a], [b]) => Number(a === "") - Number(b === "") || a.localeCompare(b));
  }, [rows]);

  // "/" focuses the search from anywhere on the page
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/" || (e.target as HTMLElement).matches?.("input, textarea, select") || document.querySelector(".modal-back")) return;
      e.preventDefault();
      inputRef.current?.focus();
      inputRef.current?.select();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // keep the active measure in view
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(".rb-item.active");
    el?.scrollIntoView({ block: "nearest" });
  }, [mid, ov]);

  const setScope = (s: "page" | "all") => { setScopePref(s); store.set("ws-scope", s); setQuery(""); };
  const items = () => [...(listRef.current?.querySelectorAll<HTMLElement>(".rb-item") || [])];
  const onListKey = (e: React.KeyboardEvent) => {
    const list = items();
    const i = list.indexOf(document.activeElement as HTMLElement);
    if (e.key === "ArrowDown" && i >= 0) { e.preventDefault(); list[Math.min(list.length - 1, i + 1)]?.focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); if (i <= 0) inputRef.current?.focus(); else list[i - 1].focus(); }
    else if (e.key === "Escape") inputRef.current?.focus();
  };

  const item = (m: Row, n: number): ReactNode => (
    <motion.div key={m.id} layout="position" initial={{ opacity: 0, x: 10 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }}
      transition={{ ...spring, delay: Math.min(n, 14) * 0.018 }}>
      <Link href={wsPath(dash, ov, { page: pid, object: m.id })} scroll={false} className={`rb-item ${m.id === mid ? "active" : ""}`} title={m.name}>
        {m.id === mid ? <motion.span layoutId="rb-active" className="rb-pill" transition={spring} /> : null}
        <span className="rb-ico"><Icon d={I.sigma} /></span>
        <span className="rb-text">
          <span className="rb-name"><Mark text={m.name} needle={needle} /></span>
          <span className="rb-meta">
            {m.inDax ? <span className="rb-hit">matched in DAX</span> : m.category}
            {m.used_by_visuals ? ` · ${m.used_by_visuals} visual${m.used_by_visuals === 1 ? "" : "s"}` : ""}
          </span>
        </span>
        {m.unresolved?.length ? <span className="rb-flag bad" title="References fields that don't exist">!</span>
          : m.unused ? <span className="rb-flag warn" title="Not used by any visual">○</span> : null}
      </Link>
    </motion.div>
  );

  return (
    <motion.aside className="rightbar" initial={{ opacity: 0, x: 24 }} animate={{ opacity: 1, x: 0 }} transition={spring} aria-label="Measures">
      <div className="rb-head">
        <div className="rb-eyebrow"><Icon d={I.sigma} />Measures</div>
        <AnimatePresence mode="wait" initial={false}>
          <motion.div key={page?.id || "all"} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.16 }}>
            <div className="rb-title">{page?.name || "All pages"}</div>
            <div className="rb-sub">{ov?.name || "…"}</div>
          </motion.div>
        </AnimatePresence>
      </div>
      {error ? <div className="rb-empty">{error.message}</div> : !ov ? <div className="rb-empty"><Loading text="Reading the dashboard…" /></div> : (
        <>
          <div className="rb-search">
            <Icon d={I.search} />
            <input ref={inputRef} type="search" placeholder={`Search all ${ov.measures.length} measures…`} value={query} aria-label="Search measures"
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") { e.preventDefault(); items()[0]?.focus(); }
                else if (e.key === "Enter") { const first = items()[0]; if (first) { e.preventDefault(); router.push(first.getAttribute("href")!); } }
              }} />
            {query ? <button onClick={() => { setQuery(""); inputRef.current?.focus(); }} title="Clear"><Icon d={I.close} /></button> : null}
          </div>
          <LayoutGroup id="rb-scope">
            <div className={`rb-scope ${needle ? "searching" : ""}`}>
              {(["page", "all"] as const).map((s) => {
                const on = (needle ? "all" : scope) === s;
                return (
                  <button key={s} onClick={() => setScope(s)} disabled={s === "page" && !(page && onPage.length)} className={on ? "on" : ""}>
                    {on ? <motion.span layoutId="scope-pill" className="scope-pill" transition={spring} /> : null}
                    <span>{s === "page" ? "This page" : "All measures"} <b>{s === "page" ? onPage.length : ov.measures.length}</b></span>
                  </button>
                );
              })}
            </div>
          </LayoutGroup>
          <AnimatePresence>{needle ? <motion.div className="rb-note" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}>{rows.length} result{rows.length === 1 ? "" : "s"} across all measures</motion.div> : null}</AnimatePresence>
          <div className="rb-list" ref={listRef} onKeyDown={onListKey}>
            <LayoutGroup id="rb-list">
              {!rows.length ? (
                <div className="rb-empty">{needle ? `No measure matches “${query}”.` : page ? "No measures on this page's visuals — they show plain columns." : "This model has no measures."}</div>
              ) : groups.length === 1 && !groups[0][0] ? (
                <AnimatePresence initial>{groups[0][1].map(item)}</AnimatePresence>
              ) : groups.map(([folder, list]) => {
                const isFolded = folded.has(folder) && !needle;
                return (
                  <div key={folder} className="rb-folder">
                    <button className="rb-fhead" onClick={() => setFolded((f) => { const n = new Set(f); if (n.has(folder)) n.delete(folder); else n.add(folder); return n; })}>
                      <motion.span animate={{ rotate: isFolded ? 0 : 90 }} transition={spring} className="chev-wrap"><Icon d={I.chev} className="chev" /></motion.span>
                      <Icon d={I.folder} className="fico" /><span>{folder || "No folder"}</span><b>{list.length}</b>
                    </button>
                    <AnimatePresence initial={false}>
                      {!isFolded ? (
                        <motion.div className="rb-fitems" initial="closed" animate="open" exit="closed" variants={collapse} style={{ overflow: "hidden" }}>
                          {list.map(item)}
                        </motion.div>
                      ) : null}
                    </AnimatePresence>
                  </div>
                );
              })}
            </LayoutGroup>
          </div>
        </>
      )}
    </motion.aside>
  );
}
