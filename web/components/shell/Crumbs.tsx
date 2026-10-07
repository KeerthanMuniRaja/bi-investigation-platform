"use client";

import Link from "next/link";
import { AnimatePresence, motion } from "motion/react";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type Crumb = { label: string; href?: string };

const Ctx = createContext<{ crumbs: Crumb[]; set: (c: Crumb[]) => void }>({ crumbs: [], set: () => {} });

export function CrumbsProvider({ children }: { children: ReactNode }) {
  const [crumbs, set] = useState<Crumb[]>([]);
  return <Ctx.Provider value={{ crumbs, set }}>{children}</Ctx.Provider>;
}

/** a page declares its breadcrumb trail */
export function useCrumbs(crumbs: Crumb[]) {
  const { set } = useContext(Ctx);
  const key = JSON.stringify(crumbs);
  useEffect(() => { set(JSON.parse(key)); }, [key, set]);
}

export function CrumbTrail() {
  const { crumbs } = useContext(Ctx);
  return (
    <nav className="crumbs" aria-label="Breadcrumb">
      <AnimatePresence mode="popLayout" initial={false}>
        {crumbs.map((c, i) => (
          <motion.span key={`${i}-${c.label}`} className="crumb" layout initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }}
            transition={{ duration: 0.18, delay: i * 0.03 }}>
            {i > 0 ? <span className="sep">/</span> : null}
            {i === crumbs.length - 1 || !c.href ? <b>{c.label}</b> : <Link href={c.href}>{c.label}</Link>}
          </motion.span>
        ))}
      </AnimatePresence>
    </nav>
  );
}
