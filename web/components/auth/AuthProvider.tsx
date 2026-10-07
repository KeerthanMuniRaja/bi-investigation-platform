"use client";

/*
 * Who is signed in, and the gate in front of the app. Signed out (or not
 * allowed) people see the sign-in screen; the app stays mounted but hidden
 * behind it and its requests wait for a session (lib/supabase.ts). With
 * sign-in off (no Supabase settings) the app opens straight away, as a
 * single local workspace.
 */
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { mutate } from "swr";
import type { Session } from "@supabase/supabase-js";
import { AUTH_EVENT, authEnabled, googleEnabled, setSignedIn, supabase, type AuthProblem } from "@/lib/supabase";
import { Denied, SignIn } from "./SignIn";

export type Account = { email: string; name: string };
type Status = "disabled" | "loading" | "signed-out" | "signed-in" | "denied";
type AuthState = { status: Status; account: Account | null; message: string | null };

const NEXT_KEY = "bip-next";
const CALLBACK = "/auth/callback";

const Ctx = createContext<{ state: AuthState; signIn: () => Promise<void>; signOut: () => Promise<void> }>({
  state: { status: "disabled", account: null, message: null },
  signIn: async () => {},
  signOut: async () => {},
});
export const useAuth = () => useContext(Ctx);

function accountOf(session: Session | null): Account | null {
  const u = session?.user;
  if (!u?.email) return null;
  const meta = (u.user_metadata || {}) as Record<string, string | undefined>;
  return { email: u.email.toLowerCase(), name: meta.full_name || meta.name || u.email.split("@")[0] };
}

/** drop everything cached for the previous person: API responses and remembered places */
function forgetWorkspace() {
  void mutate(() => true, undefined, { revalidate: false });
  try {
    for (const k of ["ws-last-path", "ws-open", "ws-scope"]) localStorage.removeItem(k);
  } catch { /* storage blocked */ }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [state, setState] = useState<AuthState>(() => ({ status: authEnabled ? "loading" : "disabled", account: null, message: null }));
  const lastEmail = useRef<string | null>(null);
  useEffect(() => { setSignedIn(state.status === "signed-in"); }, [state.status]);

  useEffect(() => {
    const sb = supabase();
    if (!sb) return;
    const apply = (session: Session | null, message: string | null = null) => {
      const account = accountOf(session);
      if (account?.email !== lastEmail.current) {
        if (lastEmail.current) forgetWorkspace();  // a different person on this browser
        lastEmail.current = account?.email ?? null;
      }
      setState((s) => (account ? { status: s.status === "denied" && s.account?.email === account.email ? "denied" : "signed-in", account, message: s.status === "denied" ? s.message : null }
        : { status: "signed-out", account: null, message: message ?? (s.status === "loading" ? null : s.message) }));
    };
    // Google sends people back with ?error=... when they cancel or something fails
    const params = new URLSearchParams(window.location.search);
    const oauthError = params.get("error_description") || params.get("error");
    sb.auth.getSession().then(({ data, error }) => apply(data.session, oauthError || error?.message || null));
    const { data: sub } = sb.auth.onAuthStateChange((_event, session) => apply(session));
    const onProblem = (e: Event) => {
      const p = (e as CustomEvent<AuthProblem>).detail;
      if (p.kind === "denied") setState((s) => ({ ...s, status: "denied", message: p.message }));
      else {
        // the API refused the token: try a refresh once, else back to the sign-in screen
        sb.auth.refreshSession().then(({ error }) => {
          if (error) void sb.auth.signOut().then(() => setState({ status: "signed-out", account: null, message: "Your session ended — sign in again." }));
        });
      }
    };
    window.addEventListener(AUTH_EVENT, onProblem);
    return () => { sub.subscription.unsubscribe(); window.removeEventListener(AUTH_EVENT, onProblem); };
  }, []);

  // back from Google: continue to where the person was going
  useEffect(() => {
    if (state.status !== "signed-in" || pathname !== CALLBACK) return;
    let next = "/";
    try { next = sessionStorage.getItem(NEXT_KEY) || "/"; sessionStorage.removeItem(NEXT_KEY); } catch { /* ignore */ }
    router.replace(next.startsWith("/") && !next.startsWith("//") ? next : "/");
  }, [state.status, pathname, router]);

  const signIn = useCallback(async () => {
    const sb = supabase();
    if (!sb) return;
    try {
      const here = window.location.pathname + window.location.search;
      sessionStorage.setItem(NEXT_KEY, here.startsWith(CALLBACK) ? "/" : here);
    } catch { /* ignore */ }
    // Supabase answers a disabled provider with a bare JSON error page: check first, explain here
    if (!(await googleEnabled())) {
      setState((s) => ({ ...s, status: "signed-out", message: "Google sign-in isn't switched on for this app yet. In Supabase, open Authentication → Sign In / Providers → Google, turn it on and add the Google client ID and secret." }));
      return;
    }
    const { error } = await sb.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${window.location.origin}${CALLBACK}`, queryParams: { prompt: "select_account" } },
    });
    if (error) setState((s) => ({ ...s, status: "signed-out", message: error.message }));
  }, []);

  const signOut = useCallback(async () => {
    const sb = supabase();
    if (!sb) return;
    setSignedIn(false);
    await sb.auth.signOut();
    forgetWorkspace();
    lastEmail.current = null;
    setState({ status: "signed-out", account: null, message: null });
    router.replace("/");
  }, [router]);

  const open = state.status === "signed-in" || state.status === "disabled";
  let screen: ReactNode = null;
  if (state.status === "loading") screen = <div className="app-boot"><span className="spinner" /></div>;
  else if (state.status === "signed-out") screen = <SignIn onSignIn={signIn} message={state.message} />;
  else if (state.status === "denied") screen = <Denied account={state.account} message={state.message} onSignOut={signOut} />;
  return (
    <Ctx.Provider value={{ state, signIn, signOut }}>
      {/* always rendered (hidden while signed out) so every page keeps a real, mounted route */}
      <div style={{ display: open ? "contents" : "none" }} aria-hidden={!open}>{children}</div>
      {screen}
    </Ctx.Provider>
  );
}
