"use client";

import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { spring } from "@/lib/motion";
import { toast } from "./feedback";
import { Spinner } from "./ui";

const FOCUSABLE = 'a[href],button:not([disabled]),input:not([type=hidden]):not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

/**
 * Animated dialog for forms. `onConfirm` may be async; returning false keeps it
 * open, a thrown error is shown as a toast and keeps it open too.
 */
export function Modal({ open, title, onClose, onConfirm, confirm = "Save", danger = false, wide = false, children }: {
  open: boolean; title: string; onClose: () => void; onConfirm?: () => Promise<unknown> | unknown;
  confirm?: string | null; danger?: boolean; wide?: boolean; children: ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    const t = setTimeout(() => (box.current?.querySelector<HTMLElement>("input:not([type=checkbox]):not([type=hidden]),select,textarea") || box.current?.querySelector<HTMLElement>(FOCUSABLE))?.focus(), 60);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); onClose(); return; }
      if (e.key !== "Tab" || !box.current) return;
      const items = [...box.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null);
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => { clearTimeout(t); document.removeEventListener("keydown", onKey); if (prev?.isConnected) prev.focus(); };
  }, [open, onClose]);

  async function ok() {
    setBusy(true);
    try { if ((await onConfirm?.()) !== false) onClose(); }
    catch (e) { toast((e as Error).message, "bad"); }
    finally { setBusy(false); }
  }

  return (
    <AnimatePresence>
      {open ? (
        <motion.div className="modal-back" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={(e) => e.target === e.currentTarget && onClose()}>
          <motion.div ref={box} className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}
            initial={{ opacity: 0, scale: 0.94, y: 14 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.96, y: 8 }} transition={spring}>
            <div className="card-head"><h2>{title}</h2></div>
            <div className="card-body stack">{children}</div>
            <div className="modal-foot">
              <button className="btn" onClick={onClose}>Cancel</button>
              {confirm ? <button className={`btn ${danger ? "danger" : "primary"}`} onClick={ok} disabled={busy}>{busy ? <Spinner /> : null}{confirm}</button> : null}
            </div>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
