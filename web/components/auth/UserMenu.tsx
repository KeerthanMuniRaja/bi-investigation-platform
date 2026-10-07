"use client";

import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { spring } from "@/lib/motion";
import { I, Icon } from "@/components/ui";
import { useAuth } from "./AuthProvider";

/** initials in a coloured circle (no profile picture is used) */
function Avatar({ name, size = 30 }: { name: string; size?: number }) {
  const initials = name.split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("");
  // a stable colour per person
  const hue = [...name].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
  return <span className="avatar initials" style={{ width: size, height: size, fontSize: size * 0.4, background: `hsl(${hue} 62% 46%)` }}>{initials || "?"}</span>;
}

/** top-bar account button: who is signed in, and sign out */
export function UserMenu() {
  const { state, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !box.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", close); };
  }, [open]);

  if (state.status === "disabled") return <span className="local-chip" title="Sign-in is off: everything is stored on this machine">Local workspace</span>;
  const a = state.account;
  if (!a) return null;
  return (
    <div className="user-menu" ref={box}>
      <motion.button className="user-btn" onClick={() => setOpen(!open)} whileTap={{ scale: 0.95 }} aria-haspopup="menu" aria-expanded={open} title={a.email}>
        <Avatar name={a.name} />
      </motion.button>
      <AnimatePresence>
        {open ? (
          <motion.div className="user-pop" role="menu" initial={{ opacity: 0, y: -6, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -4, scale: 0.98 }} transition={spring}>
            <div className="user-who">
              <Avatar name={a.name} size={40} />
              <div><b>{a.name}</b><span>{a.email}</span></div>
            </div>
            <div className="user-note"><Icon d={I.check} size={14} />Your dashboards and results are visible only to you.</div>
            <button className="user-item" role="menuitem" onClick={() => { setOpen(false); void signOut(); }}>
              <Icon d={I.arrow} />Sign out
            </button>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
