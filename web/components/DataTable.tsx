"use client";

import { motion } from "motion/react";
import { useRouter } from "next/navigation";

const nf2 = new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const nf0 = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });

function cell(v: unknown, col: string) {
  if (typeof v !== "number") return v == null ? "" : String(v);
  if (Number.isInteger(v)) return /\b(year|yr|period|id|code|key|no|number)\b/i.test(col.replace(/_/g, " ")) ? String(v) : nf0.format(v);
  return nf2.format(v);
}

/** compact preview of rows given as objects keyed by column */
export function PreviewTable({ columns, rows, max = 8 }: { columns: string[]; rows: Record<string, unknown>[]; max?: number }) {
  if (!columns.length) return <div className="empty small">No columns found.</div>;
  return (
    <div className="table-wrap">
      <table className="data">
        <thead><tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>
          {rows.slice(0, max).map((r, i) => (
            <motion.tr key={i} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: Math.min(i, 20) * 0.015 }}>
              {columns.map((c) => <td key={c} className={typeof r[c] === "number" ? "num" : ""}>{cell(r[c], c)}</td>)}
            </motion.tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** table row that opens a page; reachable by keyboard, ignores clicks on controls inside */
export function GoRow({ href, children, i = 0 }: { href: string; children: React.ReactNode; i?: number }) {
  const router = useRouter();
  const fromControl = (t: EventTarget) => (t as Element).closest("a,button,input,select,textarea,label,summary");
  return (
    <motion.tr className="clickable" tabIndex={0} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: Math.min(i, 20) * 0.02 }}
      onClick={(e) => { if (!fromControl(e.target)) router.push(href); }}
      onKeyDown={(e) => { if (e.key === "Enter" && e.target === e.currentTarget) router.push(href); }}
      onMouseEnter={() => router.prefetch(href)}>
      {children}
    </motion.tr>
  );
}
