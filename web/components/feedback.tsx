"use client";

/* Toasts and confirm dialogs, available anywhere via toast() / useConfirm(). */
import { AnimatePresence, motion } from "motion/react";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { spring } from "@/lib/motion";

type Toast = { id: number; msg: string; kind: string };

/** fire-and-forget toast (also used by the canvas engine via the same event) */
export function toast(msg: string, kind: "" | "ok" | "bad" = "") {
  window.dispatchEvent(new CustomEvent("bip:toast", { detail: { msg, kind } }));
}

export function Toaster() {
  const [items, setItems] = useState<Toast[]>([]);
  const next = useRef(0);
  useEffect(() => {
    const onToast = (e: Event) => {
      const { msg, kind } = (e as CustomEvent<{ msg: string; kind: string }>).detail;
      const id = ++next.current;
      setItems((list) => [...list.slice(-4), { id, msg, kind }]);
      setTimeout(() => setItems((list) => list.filter((t) => t.id !== id)), kind === "bad" ? 8000 : 3500);
    };
    window.addEventListener("bip:toast", onToast);
    return () => window.removeEventListener("bip:toast", onToast);
  }, []);
  return (
    <div className="toast-wrap" aria-live="polite">
      <AnimatePresence initial={false}>
        {items.map((t) => (
          <motion.div key={t.id} layout className={`toast ${t.kind}`} initial={{ opacity: 0, x: 40, scale: 0.96 }} animate={{ opacity: 1, x: 0, scale: 1 }}
            exit={{ opacity: 0, x: 40, transition: { duration: 0.18 } }} transition={spring}
            onClick={() => setItems((list) => list.filter((x) => x.id !== t.id))}>
            {t.msg}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

/* ---------------------------------------------------------------- confirm */
type ConfirmOptions = { title: string; text?: ReactNode; confirm?: string; danger?: boolean };
type Pending = ConfirmOptions & { resolve: (ok: boolean) => void };
const ConfirmCtx = createContext<(o: ConfirmOptions) => Promise<boolean>>(async () => false);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const ask = useCallback((o: ConfirmOptions) => new Promise<boolean>((resolve) => setPending({ ...o, resolve })), []);
  const close = (ok: boolean) => { pending?.resolve(ok); setPending(null); };
  useEffect(() => {
    if (!pending) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(false); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  });
  return (
    <ConfirmCtx.Provider value={ask}>
      {children}
      <AnimatePresence>
        {pending ? (
          <motion.div className="modal-back" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={(e) => e.target === e.currentTarget && close(false)}>
            <motion.div className="modal" role="dialog" aria-modal="true" aria-label={pending.title}
              initial={{ opacity: 0, scale: 0.94, y: 12 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.96, y: 8 }} transition={spring}>
              <div className="card-head"><h2>{pending.title}</h2></div>
              <div className="card-body"><div className="muted">{pending.text ?? "This can't be undone."}</div></div>
              <div className="modal-foot">
                <button className="btn" onClick={() => close(false)} autoFocus>Cancel</button>
                <button className={`btn ${pending.danger === false ? "primary" : "danger"}`} onClick={() => close(true)}>{pending.confirm || "Delete"}</button>
              </div>
            </motion.div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </ConfirmCtx.Provider>
  );
}

export const useConfirm = () => useContext(ConfirmCtx);
