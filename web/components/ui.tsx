"use client";

import { animate, motion, useInView, useMotionValue, useTransform } from "motion/react";
import Link from "next/link";
import { useEffect, useRef, type ReactNode } from "react";
import { fadeUp } from "@/lib/motion";
import { CONF_TEXT } from "@/lib/format";

export function Icon({ d, className = "", size }: { d: string; className?: string; size?: number }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round"
      width={size} height={size} dangerouslySetInnerHTML={{ __html: d }} />
  );
}

export const I = {
  chev: '<path d="M9 6l6 6-6 6"/>',
  dash: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M7 15v-3M12 15V8M17 15v-5"/>',
  page: '<path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z"/><path d="M14 3v5h5"/>',
  sigma: '<path d="M18 7V4H6l6 8-6 8h12v-3"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  reload: '<path d="M4 4v6h6M20 20v-6h-6M5 15a7 7 0 0 0 12 3M19 9A7 7 0 0 0 7 6"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  overview: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  health: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  rel: '<circle cx="6" cy="6" r="3"/><circle cx="18" cy="18" r="3"/><path d="M8.5 8.5l7 7"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 10v10"/>',
  report: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 15v-3M12 15V8M17 15v-5"/>',
  reconcile: '<path d="M7 7h11l-3-3M17 17H6l3 3"/>',
  results: '<path d="M4 19V9M10 19V5M16 19v-7M22 19H2"/>',
  template: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 9h16M9 9v11"/>',
  investigate: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
  lineage: '<circle cx="5" cy="12" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="19" cy="18" r="2"/><path d="M7 12h4l6-6M11 12l6 6"/>',
  sources: '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
  download: '<path d="M12 4v12M7 11l5 5 5-5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
  play: '<path d="M7 5l12 7-12 7z"/>',
  note: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
  test: '<path d="M5 12l5 5 9-10"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  check: '<path d="M5 12l5 5 9-10"/>',
  model: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><path d="M14 17.5h7M17.5 14v7"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
};

export function Badge({ children, kind = "neutral", title }: { children: ReactNode; kind?: string; title?: string }) {
  return <span className={`badge ${kind}`} title={title}>{children}</span>;
}

export function ConfBadge({ c }: { c?: string | null }) {
  const [text, kind] = CONF_TEXT[c || ""] || [c || "Unknown", "neutral"];
  return <Badge kind={kind}>{text}</Badge>;
}

export function Spinner() {
  return <span className="spinner" />;
}

export function Loading({ text = "Loading…" }: { text?: string }) {
  return <div className="loading"><Spinner />{text}</div>;
}

export function AlertBox({ kind, items }: { kind: string; items?: (string | null | undefined)[] | null }) {
  const list = (items || []).filter(Boolean) as string[];
  if (!list.length) return null;
  return (
    <motion.div className={`alert ${kind}`} initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }}>
      {list.length === 1 ? list[0] : <ul>{list.map((i, n) => <li key={n}>{i}</li>)}</ul>}
    </motion.div>
  );
}

export function EmptyState({ title, text, icon = I.search, children }: { title: string; text?: ReactNode; icon?: string; children?: ReactNode }) {
  return (
    <motion.div className="empty" initial="hidden" animate="show" variants={fadeUp}>
      <motion.div className="empty-icon" animate={{ y: [0, -4, 0] }} transition={{ duration: 4, repeat: Infinity, ease: "easeInOut" }}><Icon d={icon} /></motion.div>
      <h3>{title}</h3>
      {text ? <p>{text}</p> : null}
      {children}
    </motion.div>
  );
}

/** number that counts up when it scrolls into view */
export function CountUp({ value, format = (n: number) => Math.round(n).toLocaleString() }: { value: number | null | undefined; format?: (n: number) => string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true });
  const mv = useMotionValue(0);
  const text = useTransform(mv, (v) => format(v));
  useEffect(() => {
    if (value == null || !inView) return;
    const controls = animate(mv, value, { duration: 0.9, ease: [0.16, 1, 0.3, 1] });
    return () => controls.stop();
  }, [value, inView, mv]);
  if (value == null) return <span>—</span>;
  return <motion.span ref={ref}>{text}</motion.span>;
}

export function Kpi({ label, value, hint, kind = "", href, display }: {
  label: string; value: number | null | undefined; hint?: ReactNode; kind?: string; href?: string; display?: string;
}) {
  const inner = (
    <>
      <div className="label">{label}</div>
      <div className="value">{display ?? <CountUp value={value} />}</div>
      {hint ? <div className="hint">{hint}</div> : null}
    </>
  );
  const props = { className: `kpi ${kind} ${href ? "link" : ""}`, variants: fadeUp, whileHover: href ? { y: -3 } : undefined };
  return href ? <motion.div {...props}><Link href={href} className="kpi-link">{inner}</Link></motion.div> : <motion.div {...props}>{inner}</motion.div>;
}

export function Card({ title, sub, right, children, className = "", bodyClass = "card-body" }: {
  title?: ReactNode; sub?: ReactNode; right?: ReactNode; children?: ReactNode; className?: string; bodyClass?: string;
}) {
  return (
    <motion.section className={`card ${className}`} variants={fadeUp}>
      {title || right ? (
        <div className="card-head">
          <div>{title ? <h2>{title}</h2> : null}{sub ? <div className="sub">{sub}</div> : null}</div>
          <span className="spacer" />
          {right}
        </div>
      ) : null}
      {children !== undefined ? <div className={bodyClass}>{children}</div> : null}
    </motion.section>
  );
}

export function PageHead({ eyebrow, title, text, actions }: { eyebrow?: string; title: ReactNode; text?: ReactNode; actions?: ReactNode }) {
  return (
    <motion.div className="page-head" variants={fadeUp}>
      <div>{eyebrow ? <div className="eyebrow">{eyebrow}</div> : null}<h1>{title}</h1>{text ? <p>{text}</p> : null}</div>
      {actions ? <div className="row">{actions}</div> : null}
    </motion.div>
  );
}

/** page body: children animate in, staggered */
export function Stagger({ children, className = "page-stack", gap = 0.04 }: { children: ReactNode; className?: string; gap?: number }) {
  return (
    <motion.div className={className} initial="hidden" animate="show" variants={{ hidden: {}, show: { transition: { staggerChildren: gap } } }}>
      {children}
    </motion.div>
  );
}
