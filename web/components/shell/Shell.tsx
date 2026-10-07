"use client";

import { AnimatePresence, motion } from "motion/react";
import { useSyncExternalStore, type ReactNode } from "react";
import { CrumbTrail, CrumbsProvider } from "./Crumbs";
import { MeasuresPanel } from "./MeasuresPanel";
import { Sidebar } from "./Sidebar";
import { ConfirmProvider, Toaster } from "@/components/feedback";
import { I, Icon } from "@/components/ui";
import { API_URL } from "@/lib/api";
import { UserMenu } from "@/components/auth/UserMenu";
import { useWsRoute } from "@/lib/routes";

const DARK_MQ = "(prefers-color-scheme: dark)";
const isDark = () => { const t = document.documentElement.dataset.theme; return t ? t === "dark" : matchMedia(DARK_MQ).matches; };
function subscribeTheme(cb: () => void) {
  const mq = matchMedia(DARK_MQ);
  mq.addEventListener("change", cb);
  window.addEventListener("bip-theme", cb);
  return () => { mq.removeEventListener("change", cb); window.removeEventListener("bip-theme", cb); };
}

function ThemeToggle() {
  const dark = useSyncExternalStore(subscribeTheme, isDark, () => null);
  const toggle = () => {
    const next = !dark;
    document.documentElement.dataset.theme = next ? "dark" : "light";
    try { localStorage.setItem("bip-theme", next ? "dark" : "light"); } catch { /* ignore */ }
    window.dispatchEvent(new Event("bip-theme"));
  };
  return (
    <motion.button className="btn ghost icon-btn" onClick={toggle} whileTap={{ scale: 0.9, rotate: -20 }} title="Toggle light / dark theme" aria-label="Toggle theme">
      <AnimatePresence mode="wait" initial={false}>
        <motion.span key={String(dark)} initial={{ opacity: 0, rotate: -60, scale: 0.6 }} animate={{ opacity: 1, rotate: 0, scale: 1 }} exit={{ opacity: 0, rotate: 60, scale: 0.6 }}
          transition={{ duration: 0.2 }} style={{ display: "grid" }}>
          <Icon d={dark ? I.sun : I.moon} />
        </motion.span>
      </AnimatePresence>
    </motion.button>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const route = useWsRoute();
  const showMeasures = !!route && !route.model;  // the measures panel belongs to the dashboard workspace
  return (
    <ConfirmProvider>
      <CrumbsProvider>
        <div className={`app ${showMeasures ? "with-rightbar" : ""}`}>
          <Sidebar />
          <main className="main">
            <header className="topbar">
              <CrumbTrail />
              <span className="spacer" />
              <a className="btn ghost sm" href={`${API_URL}/docs`} target="_blank" rel="noopener noreferrer">API docs</a>
              <ThemeToggle />
              <UserMenu />
            </header>
            <div className="content">{children}</div>
          </main>
          <AnimatePresence>{showMeasures ? <MeasuresPanel key="measures" /> : null}</AnimatePresence>
        </div>
        <Toaster />
      </CrumbsProvider>
    </ConfirmProvider>
  );
}
