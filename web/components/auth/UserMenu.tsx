"use client";

import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { spring } from "@/lib/motion";
import { Modal } from "@/components/Modal";
import { toast } from "@/components/feedback";
import { I, Icon } from "@/components/ui";
import { useAuth } from "./AuthProvider";

/** initials in a coloured circle */
function Avatar({ name, size = 30 }: { name: string; size?: number }) {
  const initials = name.split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("");
  // a stable colour per person
  const hue = [...name].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
  return <span className="avatar initials" style={{ width: size, height: size, fontSize: size * 0.4, background: `hsl(${hue} 62% 46%)` }}>{initials || "?"}</span>;
}

function ChangePassword({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { changePassword } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const close = () => { setCurrent(""); setNext(""); setAgain(""); onClose(); };
  return (
    <Modal open={open} onClose={close} title="Change password" confirm="Change password" onConfirm={async () => {
      if (next.length < 8) throw new Error("Use at least 8 characters for the new password.");
      if (next !== again) throw new Error("The two new passwords don't match.");
      await changePassword(current, next);
      toast("Password changed — you're signed out everywhere else", "ok");
      close();
      return false;
    }}>
      <label className="field">Current password<input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} /></label>
      <label className="field">New password<input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} placeholder="At least 8 characters" /></label>
      <label className="field">Repeat new password<input type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} /></label>
      <p className="muted small" style={{ margin: 0 }}>Other browsers where you&apos;re signed in will be signed out.</p>
    </Modal>
  );
}

/** top-bar account button: who is signed in, change password, sign out */
export function UserMenu() {
  const { state, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  const [pw, setPw] = useState(false);
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
      <motion.button className="user-btn" onClick={() => setOpen(!open)} whileTap={{ scale: 0.95 }} aria-haspopup="menu" aria-expanded={open} title={a.username}>
        <Avatar name={a.name || a.username} />
      </motion.button>
      <AnimatePresence>
        {open ? (
          <motion.div className="user-pop" role="menu" initial={{ opacity: 0, y: -6, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -4, scale: 0.98 }} transition={spring}>
            <div className="user-who">
              <Avatar name={a.name || a.username} size={40} />
              <div><b>{a.name || a.username}</b><span>@{a.username}</span></div>
            </div>
            <div className="user-note"><Icon d={I.check} size={14} />Your dashboards and results are visible only to you.</div>
            <button className="user-item" role="menuitem" onClick={() => { setOpen(false); setPw(true); }}><Icon d={I.key} />Change password</button>
            {state.temporary ? (
              <div className="user-note"><Icon d={I.clock} size={14} />Power BI dashboards are removed when you sign out, or after {state.idleMinutes} minutes with the site closed.</div>
            ) : null}
            <button className="user-item" role="menuitem" onClick={() => { setOpen(false); signOut(); }}><Icon d={I.logout} />Sign out</button>
          </motion.div>
        ) : null}
      </AnimatePresence>
      <ChangePassword open={pw} onClose={() => setPw(false)} />
    </div>
  );
}
