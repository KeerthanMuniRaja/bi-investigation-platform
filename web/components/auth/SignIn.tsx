"use client";

import { AnimatePresence, motion, useMotionValue, useReducedMotion, useSpring, useTransform } from "motion/react";
import { useEffect, useState, type MouseEvent } from "react";
import { fadeUp, softSpring, spring } from "@/lib/motion";
import { AlertBox, I, Icon, Spinner } from "@/components/ui";
import type { Account } from "./AuthProvider";

function GoogleMark() {
  return (
    <svg width="20" height="20" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z" />
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z" />
    </svg>
  );
}

function Brand({ light = false }: { light?: boolean }) {
  return (
    <div className={`si-brand ${light ? "light" : ""}`}>
      <motion.div className="brand-mark" initial={{ rotate: -14, scale: 0.6, opacity: 0 }} animate={{ rotate: 0, scale: 1, opacity: 1 }} transition={spring}
        whileHover={{ rotate: -6, scale: 1.06 }}>
        <svg viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round"><path d="M4 17l5-6 4 4 7-9" /></svg>
      </motion.div>
      <div><div className="si-brand-name">BI Trust Platform</div><div className="si-brand-sub">Reconcile · Explain · Trace</div></div>
    </div>
  );
}

/* ---------------------------------------------------- lineage illustration */
const NODES = [
  { id: "src", x: 18, y: 92, w: 92, label: "SQL Server", kind: "Source", color: "var(--n-source)" },
  { id: "t1", x: 150, y: 40, w: 84, label: "Sales", kind: "Table", color: "var(--n-table)" },
  { id: "t2", x: 150, y: 144, w: 84, label: "Calendar", kind: "Table", color: "var(--n-table)" },
  { id: "m", x: 276, y: 92, w: 92, label: "Revenue", kind: "Measure", color: "var(--n-measure)" },
  { id: "v1", x: 410, y: 40, w: 104, label: "Revenue trend", kind: "Visual", color: "var(--n-visual)" },
  { id: "v2", x: 410, y: 144, w: 104, label: "Region table", kind: "Visual", color: "var(--n-visual)" },
] as const;
const H = 40;
const node = (id: string) => NODES.find((n) => n.id === id)!;
const EDGES: [string, string][] = [["src", "t1"], ["src", "t2"], ["t1", "m"], ["t2", "m"], ["m", "v1"], ["m", "v2"]];
const edgePath = (a: string, b: string) => {
  const s = node(a), t = node(b);
  const x1 = s.x + s.w, y1 = s.y + H / 2, x2 = t.x, y2 = t.y + H / 2, mx = (x1 + x2) / 2;
  return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
};

function LineageArt() {
  const still = useReducedMotion();
  return (
    <motion.svg className="si-art" viewBox="0 0 530 200" role="img" aria-label="A number traced from its data source, through tables and a measure, to the visuals that show it"
      initial="hidden" animate="show">
      <defs>
        <linearGradient id="si-edge" x1="0" x2="1"><stop offset="0" stopColor="#7c9cff" stopOpacity=".25" /><stop offset="1" stopColor="#7c9cff" stopOpacity=".7" /></linearGradient>
        <filter id="si-glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3" /></filter>
      </defs>
      {EDGES.map(([a, b], i) => (
        <g key={a + b}>
          <motion.path id={`si-e-${i}`} d={edgePath(a, b)} fill="none" stroke="url(#si-edge)" strokeWidth={2}
            variants={{ hidden: { pathLength: 0, opacity: 0 }, show: { pathLength: 1, opacity: 1, transition: { duration: 0.9, delay: 0.35 + i * 0.12, ease: "easeInOut" } } }} />
          {!still ? (
            <circle r={3.2} fill="#c7d4ff" filter="url(#si-glow)">
              <animateMotion dur={`${2.6 + (i % 3) * 0.5}s`} begin={`${1.4 + i * 0.35}s`} repeatCount="indefinite"><mpath href={`#si-e-${i}`} /></animateMotion>
            </circle>
          ) : null}
        </g>
      ))}
      {NODES.map((n, i) => (
        <motion.g key={n.id} variants={{ hidden: { opacity: 0, y: 8, scale: 0.9 }, show: { opacity: 1, y: 0, scale: 1, transition: { ...spring, delay: 0.1 + i * 0.09 } } }}
          style={{ transformOrigin: `${n.x + n.w / 2}px ${n.y + H / 2}px` }}>
          <rect x={n.x} y={n.y} width={n.w} height={H} rx={10} fill="rgba(255,255,255,.06)" stroke="rgba(255,255,255,.14)" />
          <rect x={n.x} y={n.y} width={4} height={H} rx={2} fill={n.color} />
          <text x={n.x + 13} y={n.y + 16} className="si-art-kind">{n.kind}</text>
          <text x={n.x + 13} y={n.y + 31} className="si-art-label">{n.label}</text>
        </motion.g>
      ))}
    </motion.svg>
  );
}

/* --------------------------------------------------------------- sign-in */
const ENDINGS = ["from source to visual.", "against finance's Excel.", "before anyone asks.", "with the evidence kept."];

const POINTS: [string, string, string][] = [
  [I.lineage, "Trace every number", "From the data source through each measure to every visual that shows it."],
  [I.reconcile, "Reconcile against Excel", "See which rows differ, by how much, and the most likely cause."],
  [I.health, "Check model health", "Broken references, risky relationships and unused objects, from the file alone."],
];

function RotatingEnding() {
  const [i, setI] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setI((n) => (n + 1) % ENDINGS.length), 2800);
    return () => clearInterval(t);
  }, []);
  return (
    <span className="si-rotate" aria-live="polite">
      <AnimatePresence mode="wait">
        <motion.span key={i} initial={{ y: "60%", opacity: 0, filter: "blur(6px)" }} animate={{ y: 0, opacity: 1, filter: "blur(0px)" }}
          exit={{ y: "-60%", opacity: 0, filter: "blur(6px)" }} transition={{ duration: 0.45, ease: [0.2, 0.8, 0.2, 1] }}>
          {ENDINGS[i]}
        </motion.span>
      </AnimatePresence>
    </span>
  );
}

/** a card that leans gently towards the pointer */
function TiltCard({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  const still = useReducedMotion();
  const mx = useMotionValue(0), my = useMotionValue(0);
  const rx = useSpring(useTransform(my, [-0.5, 0.5], [5, -5]), { stiffness: 180, damping: 18 });
  const ry = useSpring(useTransform(mx, [-0.5, 0.5], [-6, 6]), { stiffness: 180, damping: 18 });
  const glowX = useTransform(mx, [-0.5, 0.5], ["20%", "80%"]);
  const glowY = useTransform(my, [-0.5, 0.5], ["10%", "90%"]);
  const move = (e: MouseEvent<HTMLDivElement>) => {
    if (still) return;
    const r = e.currentTarget.getBoundingClientRect();
    mx.set((e.clientX - r.left) / r.width - 0.5);
    my.set((e.clientY - r.top) / r.height - 0.5);
  };
  return (
    <motion.div className={`si-card ${className}`} onMouseMove={move} onMouseLeave={() => { mx.set(0); my.set(0); }}
      style={{ rotateX: rx, rotateY: ry, transformPerspective: 900, "--gx": glowX, "--gy": glowY } as never}
      initial={{ opacity: 0, y: 24, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }} transition={{ ...softSpring, delay: 0.2 }}>
      <span className="si-card-glow" aria-hidden="true" />
      {children}
    </motion.div>
  );
}

/** the screen people see before signing in */
export function SignIn({ onSignIn, message }: { onSignIn: () => Promise<void>; message: string | null }) {
  const [busy, setBusy] = useState(false);
  const stagger = { hidden: {}, show: { transition: { staggerChildren: 0.08, delayChildren: 0.05 } } };
  return (
    <div className="si-page">
      <motion.section className="si-hero" initial="hidden" animate="show" variants={stagger}>
        <div className="si-grid" aria-hidden="true" />
        <div className="si-orbs" aria-hidden="true">
          {[0, 1, 2].map((i) => (
            <motion.span key={i} animate={{ y: [0, -22, 0], x: [0, i % 2 ? 14 : -14, 0], scale: [1, 1.08, 1] }}
              transition={{ duration: 10 + i * 2.5, repeat: Infinity, ease: "easeInOut" }} />
          ))}
        </div>
        <motion.div variants={fadeUp}><Brand light /></motion.div>
        <motion.h1 variants={fadeUp}>Trust every number,<br /><RotatingEnding /></motion.h1>
        <motion.p variants={fadeUp} className="si-lead">Open a Power BI file to see where each number comes from, check it against the finance Excel, and keep the evidence for next time.</motion.p>
        <motion.div variants={fadeUp} className="si-art-wrap"><LineageArt /></motion.div>
        <motion.div className="si-points" variants={stagger}>
          {POINTS.map(([icon, title, text]) => (
            <motion.div key={title} className="si-point" variants={fadeUp} whileHover={{ y: -3, backgroundColor: "rgba(255,255,255,0.08)" }}>
              <motion.span className="si-point-ico" whileHover={{ rotate: [0, -10, 8, 0] }} transition={{ duration: 0.5 }}><Icon d={icon} /></motion.span>
              <div><b>{title}</b><span>{text}</span></div>
            </motion.div>
          ))}
        </motion.div>
      </motion.section>

      <section className="si-side">
        <TiltCard>
          <motion.div className="si-card-body" initial="hidden" animate="show" variants={{ hidden: {}, show: { transition: { staggerChildren: 0.07, delayChildren: 0.35 } } }}>
            <motion.div variants={fadeUp} className="si-welcome"><span className="si-wave" aria-hidden="true">👋</span>Welcome back</motion.div>
            <motion.h2 variants={fadeUp}>Sign in to continue</motion.h2>
            <motion.p variants={fadeUp} className="muted">Use your Google account to open your dashboards, results and investigations.</motion.p>
            <AnimatePresence>{message ? <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}><AlertBox kind="warn" items={[message]} /></motion.div> : null}</AnimatePresence>
            <motion.button variants={fadeUp} className="si-google" disabled={busy} whileHover={{ y: -2 }} whileTap={{ scale: 0.98 }}
              onClick={async () => { setBusy(true); try { await onSignIn(); } finally { setBusy(false); } }}>
              <span className="si-google-shine" aria-hidden="true" />
              {busy ? <Spinner /> : <GoogleMark />}<span>{busy ? "Opening Google…" : "Continue with Google"}</span>
            </motion.button>
            <motion.div variants={fadeUp} className="si-divider"><span>What you get</span></motion.div>
            <motion.ul variants={fadeUp} className="si-perks">
              <li><Icon d={I.dash} size={15} />Your own private workspace</li>
              <li><Icon d={I.lineage} size={15} />Full lineage for every measure</li>
              <li><Icon d={I.results} size={15} />Reconciliations you can re-run</li>
            </motion.ul>
          </motion.div>
        </TiltCard>
      </section>
    </div>
  );
}

/** signed in, but the API doesn't allow this account */
export function Denied({ account, message, onSignOut }: { account: Account | null; message: string | null; onSignOut: () => Promise<void> }) {
  return (
    <div className="si-page single">
      <div className="si-grid dim" aria-hidden="true" />
      <TiltCard className="narrow">
        <div className="si-card-body">
          <Brand />
          <motion.div className="si-denied-ico" initial={{ scale: 0.5, rotate: -20 }} animate={{ scale: 1, rotate: 0 }} transition={spring}><Icon d={I.close} /></motion.div>
          <h2>This account can&apos;t use the app</h2>
          <p className="muted">{message || `${account?.email} isn't on the list of allowed accounts.`}</p>
          <p className="muted small">Ask the person who runs this app to add your address, or sign in with a different Google account.</p>
          <motion.button className="btn primary" onClick={onSignOut} whileHover={{ y: -2 }} whileTap={{ scale: 0.97 }}><Icon d={I.arrow} />Use a different account</motion.button>
        </div>
      </TiltCard>
    </div>
  );
}
