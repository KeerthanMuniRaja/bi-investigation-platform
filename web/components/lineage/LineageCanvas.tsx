"use client";

/*
 * React wrapper around the imperative lineage engine (lib/lineage/explorer.js).
 * The engine owns its SVG; React only creates it, swaps the focus object,
 * and destroys it (Next keeps hidden routes alive, so cleanup matters).
 */
import { useEffect, useRef } from "react";
import { LineageExplorer } from "@/lib/lineage/explorer";
import type { LxNode } from "@/lib/types";

const Engine = LineageExplorer as unknown as new (host: HTMLElement, options: Record<string, unknown>) => Explorer;

export type Explorer = {
  rootId: string;
  nodes: Map<string, LxNode>;
  sel: string | null;
  sel2: string | null;
  setRoot: (id: string) => void;
  applyHighlight: () => void;
  centerOn: (id: string) => void;
  fit: (animate?: boolean) => void;
  destroy: () => void;
};

export function LineageCanvas({ cid, root, embedded = true, up, down, maxSteps = 12, onInspect, onChange, onReady, className = "ws-lineage" }: {
  cid: string; root: string | null; embedded?: boolean; up?: number; down?: number; maxSteps?: number;
  onInspect?: (node: LxNode) => void; onChange?: (s: { node: string; up: number; down: number }) => void;
  onReady?: (ex: Explorer | null) => void; className?: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const ex = useRef<Explorer | null>(null);
  const inspectRef = useRef(onInspect);
  const changeRef = useRef(onChange);
  const readyRef = useRef(onReady);
  useEffect(() => { inspectRef.current = onInspect; changeRef.current = onChange; readyRef.current = onReady; });

  useEffect(() => {
    if (!host.current) return;
    const explorer = new Engine(host.current, {
      cid, root, up, down, maxSteps, embedded,
      onInspect: (n: LxNode) => inspectRef.current?.(n),
      onChange: (s: { node: string; up: number; down: number }) => changeRef.current?.(s),
    });
    ex.current = explorer;
    readyRef.current?.(explorer);
    return () => { explorer.destroy(); ex.current = null; readyRef.current?.(null); };
    // the engine is created once per connection; focus changes go through setRoot below
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cid]);

  useEffect(() => {
    if (ex.current && root && ex.current.rootId !== root) ex.current.setRoot(root);
  }, [root]);

  return <div ref={host} className={className} />;
}
