"use client";

import { AnimatePresence, LayoutGroup, motion, useAnimate, useMotionValue, useReducedMotion, useSpring, useTransform } from "motion/react";
import { useEffect, useState, type FormEvent, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import { collapse, fadeUp, softSpring, spring } from "@/lib/motion";
import { AlertBox, I, Icon, Spinner } from "@/components/ui";

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

/* ------------------------------------------------------------ the form */
function strength(pw: string): [number, string] {
  let score = 0;
  if (pw.length >= 8) score++;
  if (pw.length >= 12) score++;
  if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) score++;
  if (/\d/.test(pw) && /[^A-Za-z0-9]/.test(pw)) score++;
  return [score, ["Too short", "Weak", "Fair", "Good", "Strong"][pw.length < 8 ? 0 : Math.max(1, score)]];
}

function Field({ icon, label, children, right, hint }: { icon: string; label: string; children: ReactNode; right?: ReactNode; hint?: ReactNode }) {
  return (
    <label className="af-field">
      <span className="af-label">{label}</span>
      <span className="af-input"><Icon d={icon} />{children}{right}</span>
      {hint}
    </label>
  );
}

function AuthForm({ onSignIn, onSignUp, signup, message }: {
  onSignIn: (u: string, p: string) => Promise<void>; onSignUp: (u: string, p: string, n: string) => Promise<void>; signup: boolean; message: string | null;
}) {
  const [mode, setMode] = useState<"in" | "up">("in");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [name, setName] = useState("");
  const [show, setShow] = useState(false);
  const [caps, setCaps] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scope, animate] = useAnimate();
  const creating = mode === "up";
  const [score, label] = strength(password);

  const fail = (msg: string) => {
    setError(msg);
    void animate(scope.current, { x: [0, -10, 10, -6, 6, -2, 0] }, { duration: 0.45 });
  };
  const switchTo = (m: "in" | "up") => { setMode(m); setError(null); setConfirm(""); };
  const capsCheck = (e: KeyboardEvent<HTMLInputElement>) => setCaps(e.getModifierState?.("CapsLock") ?? false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!username.trim() || !password) return fail("Enter your username and password.");
    if (creating && password.length < 8) return fail("Use at least 8 characters for your password.");
    if (creating && password !== confirm) return fail("The two passwords don't match.");
    setBusy(true);
    setError(null);
    try {
      if (creating) await onSignUp(username.trim(), password, name.trim());
      else await onSignIn(username.trim(), password);
    } catch (err) {
      fail((err as Error).message);
      setPassword("");
      setConfirm("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <motion.form ref={scope} className="si-card-body" onSubmit={submit} noValidate initial="hidden" animate="show"
      variants={{ hidden: {}, show: { transition: { staggerChildren: 0.06, delayChildren: 0.3 } } }}>
      <motion.div variants={fadeUp} className="si-welcome"><span className="si-wave" aria-hidden="true">👋</span>{creating ? "New here?" : "Welcome back"}</motion.div>
      <AnimatePresence mode="wait" initial={false}>
        <motion.h2 key={mode} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.2 }}>
          {creating ? "Create your account" : "Sign in to continue"}
        </motion.h2>
      </AnimatePresence>
      {signup ? (
        <motion.div variants={fadeUp}>
          <LayoutGroup id="auth-tabs">
            <div className="af-tabs" role="tablist">
              {([["in", "Sign in"], ["up", "Create account"]] as const).map(([m, text]) => (
                <button key={m} type="button" role="tab" aria-selected={mode === m} className={mode === m ? "on" : ""} onClick={() => switchTo(m)}>
                  {mode === m ? <motion.span layoutId="auth-tab" className="af-tab-pill" transition={spring} /> : null}
                  <span>{text}</span>
                </button>
              ))}
            </div>
          </LayoutGroup>
        </motion.div>
      ) : null}
      <AnimatePresence initial={false}>
        {error || message ? (
          <motion.div key={error || message} initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}>
            <AlertBox kind={error ? "bad" : "warn"} items={[error || message]} />
          </motion.div>
        ) : null}
      </AnimatePresence>
      <motion.div variants={fadeUp}>
        <Field icon={I.user} label="Username">
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoCapitalize="none" spellCheck={false}
            placeholder={creating ? "e.g. keerthan" : "Your username"} autoFocus />
        </Field>
      </motion.div>
      <AnimatePresence initial={false}>
        {creating ? (
          <motion.div key="name" initial="closed" animate="open" exit="closed" variants={collapse} style={{ overflow: "hidden" }}>
            <Field icon={I.note} label="Your name (optional)">
              <input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" placeholder="Shown in the app" />
            </Field>
          </motion.div>
        ) : null}
      </AnimatePresence>
      <motion.div variants={fadeUp}>
        <Field icon={I.lock} label="Password"
          right={<button type="button" className="af-eye" onClick={() => setShow(!show)} aria-label={show ? "Hide password" : "Show password"} title={show ? "Hide password" : "Show password"}>
            <AnimatePresence mode="wait" initial={false}>
              <motion.span key={String(show)} initial={{ opacity: 0, scale: 0.6 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.6 }} transition={{ duration: 0.12 }} style={{ display: "grid" }}>
                <Icon d={show ? I.eyeOff : I.eye} />
              </motion.span>
            </AnimatePresence>
          </button>}
          hint={
            <AnimatePresence initial={false}>
              {caps ? <motion.span key="caps" className="af-caps" initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>Caps Lock is on</motion.span> : null}
              {creating && password ? (
                <motion.span key="meter" className="af-meter" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                  <span className="af-bar"><motion.span className={`s${score}`} animate={{ width: `${password.length < 8 ? 12 : 25 + score * 18.75}%` }} transition={spring} /></span>
                  <span className="af-meter-text">{label}</span>
                </motion.span>
              ) : null}
            </AnimatePresence>
          }>
          <input type={show ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} onKeyUp={capsCheck} onKeyDown={capsCheck}
            autoComplete={creating ? "new-password" : "current-password"} placeholder={creating ? "At least 8 characters" : "Your password"} />
        </Field>
      </motion.div>
      <AnimatePresence initial={false}>
        {creating ? (
          <motion.div key="confirm" initial="closed" animate="open" exit="closed" variants={collapse} style={{ overflow: "hidden" }}>
            <Field icon={I.lock} label="Repeat password"
              right={confirm ? <motion.span className={`af-match ${confirm === password ? "ok" : "no"}`} initial={{ scale: 0 }} animate={{ scale: 1 }} transition={spring}>
                <Icon d={confirm === password ? I.check : I.close} size={14} /></motion.span> : null}>
              <input type={show ? "text" : "password"} value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" placeholder="Type it again" />
            </Field>
          </motion.div>
        ) : null}
      </AnimatePresence>
      <motion.button variants={fadeUp} type="submit" className="af-submit" disabled={busy} whileHover={{ y: -2 }} whileTap={{ scale: 0.98 }}>
        <span className="af-shine" aria-hidden="true" />
        {busy ? <Spinner /> : <Icon d={creating ? I.plus : I.arrow} />}
        <span>{busy ? (creating ? "Creating your account…" : "Signing in…") : creating ? "Create account" : "Sign in"}</span>
      </motion.button>
      <motion.p variants={fadeUp} className="af-foot">
        {signup
          ? creating ? <>Already have an account? <button type="button" onClick={() => switchTo("in")}>Sign in</button></>
                     : <>New here? <button type="button" onClick={() => switchTo("up")}>Create an account</button></>
          : "Accounts are created by the administrator."}
      </motion.p>
    </motion.form>
  );
}

/** the screen people see before signing in */
export function SignIn({ onSignIn, onSignUp, signup, message }: {
  onSignIn: (u: string, p: string) => Promise<void>; onSignUp: (u: string, p: string, n: string) => Promise<void>; signup: boolean; message: string | null;
}) {
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
        <TiltCard><AuthForm onSignIn={onSignIn} onSignUp={onSignUp} signup={signup} message={message} /></TiltCard>
      </section>
    </div>
  );
}

/** the API can't be reached */
export function Offline({ message, onRetry }: { message: string | null; onRetry: () => void }) {
  return (
    <div className="si-page single">
      <div className="si-grid dim" aria-hidden="true" />
      <TiltCard className="narrow">
        <div className="si-card-body">
          <Brand />
          <motion.div className="si-denied-ico" initial={{ scale: 0.5, rotate: -20 }} animate={{ scale: 1, rotate: 0 }} transition={spring}><Icon d={I.close} /></motion.div>
          <h2>Can&apos;t reach the server</h2>
          <p className="muted">{message || "The app's API isn't answering."}</p>
          <p className="muted small">If you run it yourself, start it with <code>npm run dev</code> in the <code>web</code> folder.</p>
          <motion.button className="btn primary" onClick={onRetry} whileHover={{ y: -2 }} whileTap={{ scale: 0.97 }}><Icon d={I.reload} />Try again</motion.button>
        </div>
      </TiltCard>
    </div>
  );
}
