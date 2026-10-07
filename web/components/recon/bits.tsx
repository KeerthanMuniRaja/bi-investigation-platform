"use client";

import { motion } from "motion/react";
import { fmt, signClass } from "@/lib/format";
import { softSpring } from "@/lib/motion";
import { Badge, ConfBadge } from "@/components/ui";

/* ---------------------------------------------------------------- shapes */
export interface Hypothesis {
  title?: string; description: string; confidence: string; score?: number;
  affected_amount?: number | null; facts?: string[]; suggested_action?: string | null;
}
export interface ColumnPair { left: string; right: string; label?: string | null; tolerance_abs?: number; tolerance_pct?: number }
export interface ReconConfig {
  key_columns: ColumnPair[]; value_columns: ColumnPair[]; aggregate: string; scale_left: number; scale_right: number;
  case_insensitive_keys: boolean; treat_blank_as_zero: boolean; left_filters: Record<string, string[]>; right_filters: Record<string, string[]>;
}
export interface RunSummary {
  reconciliation_id: string; name: string; status: string; left_name: string; right_name: string; match_rate_pct: number;
  total_keys: number; differences: number; top_difference: number; confidence: string; created_at: string; difference?: number;
}
export interface InvestigationSummary {
  investigation_id: string; question: string; status: string; confidence: string; owner?: string | null;
  top_hypothesis?: string | null; evidence_count: number; updated_at: string;
}

/* ---------------------------------------------------------------- badges */
const STATUS: Record<string, [string, string]> = { MATCH: ["Match", "ok"], MISMATCH: ["Value differs", "warn"], ONLY_LEFT: ["Only in A", "bad"], ONLY_RIGHT: ["Only in B", "info"] };
export const INV_TEXT: Record<string, string> = { OPEN: "Open", INVESTIGATING: "Investigating", ROOT_CAUSE_FOUND: "Root cause found", RESOLVED: "Resolved", INCONCLUSIVE: "Inconclusive" };
const INV_KIND: Record<string, string> = { OPEN: "neutral", INVESTIGATING: "info", ROOT_CAUSE_FOUND: "ok", RESOLVED: "ok", INCONCLUSIVE: "warn" };

export const RunBadge = ({ s }: { s: string }) => (s === "MATCHED" ? <Badge kind="ok">Matched</Badge> : <Badge kind="warn">Differences found</Badge>);
export const StatusBadge = ({ s }: { s: string }) => { const [t, k] = STATUS[s] || [s, "neutral"]; return <Badge kind={k}>{t}</Badge>; };
export const InvBadge = ({ s }: { s: string }) => <Badge kind={INV_KIND[s] || "neutral"}>{INV_TEXT[s] || s}</Badge>;
export const SideLabel = ({ side }: { side: "left" | "right" }) => <span className={`side-label ${side}`}>{side === "left" ? "A" : "B"}</span>;

/* ------------------------------------------------------------------ bars */
/** centred bar: positive grows right, negative grows left */
export function DivergeBar({ value, max, delay = 0 }: { value: number; max: number; delay?: number }) {
  const w = max > 0 ? Math.min(50, (Math.abs(value) / max) * 50) : 0;
  return (
    <div className="diverge"><div className="axis" />
      <motion.div className={`fill ${value < 0 ? "neg" : "pos"}`} initial={{ width: 0 }} animate={{ width: `${w}%` }} transition={{ ...softSpring, delay }} />
    </div>
  );
}

export function BarRow({ label, value, max, i = 0 }: { label: string; value: number; max: number; i?: number }) {
  return (
    <div className="bar-row"><span className="label" title={label}>{label}</span><DivergeBar value={value} max={max} delay={0.1 + i * 0.05} />
      <span className={`num ${signClass(value)}`}>{fmt.signed(value)}</span></div>
  );
}

/* --------------------------------------------------------------- causes */
export function CauseCards({ hypotheses }: { hypotheses: Hypothesis[] }) {
  if (!hypotheses.length) return <div className="empty small">No specific cause identified.</div>;
  return (
    <>
      {hypotheses.map((h, i) => {
        const pct = Math.round((h.score || 0) * 100);
        return (
          <motion.div key={i} className={`cause ${i === 0 ? "top" : ""}`} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...softSpring, delay: i * 0.06 }}>
            <div className="cause-head"><span className="cause-rank">{i + 1}</span><span className="cause-title">{h.title || h.description}</span>
              <ConfBadge c={h.confidence} />{h.affected_amount != null ? <span className={`num small ${signClass(h.affected_amount)}`}>{fmt.signed(h.affected_amount)}</span> : null}</div>
            <div className="row small muted" style={{ gap: 8 }}><span>Likelihood</span>
              <div className="meter" style={{ width: 140 }}><motion.span initial={{ width: 0 }} animate={{ width: `${pct}%` }} transition={{ ...softSpring, delay: 0.15 + i * 0.06 }} /></div><span>{pct}%</span></div>
            {h.title && h.description ? <div className="small">{h.description}</div> : null}
            {h.facts?.length ? <ul>{h.facts.map((f, k) => <li key={k}>{f}</li>)}</ul> : null}
            {h.suggested_action ? <div className="action"><b>Next step:</b> {h.suggested_action}</div> : null}
          </motion.div>
        );
      })}
    </>
  );
}
