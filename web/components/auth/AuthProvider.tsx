"use client";

/*
 * Who is signed in, and the gate in front of the app. The API says whether
 * sign-in is on (GET /api/info). When it is, signed-out people see the
 * sign-in screen; the app stays mounted but hidden behind it and its
 * requests wait for a session (lib/session.ts). When it's off, the app
 * opens straight away as a single local workspace.
 */
import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { mutate } from "swr";
import { ApiError, api, openApi } from "@/lib/api";
import { AUTH_EVENT, clearSession, loadSession, saveSession, setOpen, type Session, type SessionUser } from "@/lib/session";
import { Offline, SignIn } from "./SignIn";

type Status = "loading" | "offline" | "disabled" | "signed-out" | "signed-in";
type AuthState = { status: Status; account: SessionUser | null; message: string | null; signup: boolean };
type Info = { auth: boolean; signup: boolean };

type AuthApi = {
  state: AuthState;
  signIn: (username: string, password: string) => Promise<void>;
  signUp: (username: string, password: string, name: string) => Promise<void>;
  signOut: () => void;
  changePassword: (current: string, next: string) => Promise<void>;
};
const Ctx = createContext<AuthApi>({
  state: { status: "disabled", account: null, message: null, signup: false },
  signIn: async () => {}, signUp: async () => {}, signOut: () => {}, changePassword: async () => {},
});
export const useAuth = () => useContext(Ctx);

/** drop everything cached for the previous person: API responses and remembered places */
function forgetWorkspace() {
  void mutate(() => true, undefined, { revalidate: false });
  try {
    for (const k of ["ws-last-path", "ws-open", "ws-scope"]) localStorage.removeItem(k);
  } catch { /* storage blocked */ }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [state, setState] = useState<AuthState>({ status: "loading", account: null, message: null, signup: false });
  const lastUser = useRef<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => { setOpen(state.status === "signed-in" || state.status === "disabled"); }, [state.status]);

  // ask the API how sign-in works here, then restore a saved session
  useEffect(() => {
    let live = true;
    (async () => {
      let info: Info;
      try {
        info = await openApi<Info>("GET", "/api/info");
      } catch (e) {
        if (live) setState((s) => ({ ...s, status: "offline", message: (e as Error).message }));
        return;
      }
      if (!info.auth) { if (live) setState({ status: "disabled", account: null, message: null, signup: false }); return; }
      const saved = loadSession();
      if (!saved) { if (live) setState({ status: "signed-out", account: null, message: null, signup: info.signup }); return; }
      try {
        const me = await openApi<{ username: string; name: string }>("GET", "/me", undefined, saved.token);
        lastUser.current = me.username;
        if (live) setState({ status: "signed-in", account: { username: me.username, name: me.name }, message: null, signup: info.signup });
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) clearSession();
        if (live) setState({ status: "signed-out", account: null, signup: info.signup,
          message: e instanceof ApiError && e.status === 401 ? "Your session ended — sign in again." : (e as Error).message });
      }
    })();
    return () => { live = false; };
  }, [attempt]);

  // the API refused the token mid-session (expired, password changed elsewhere)
  useEffect(() => {
    const ended = (e: Event) => {
      clearSession();
      setState((s) => (s.status === "signed-in" ? { ...s, status: "signed-out", account: null, message: (e as CustomEvent<string>).detail || "Your session ended — sign in again." } : s));
    };
    window.addEventListener(AUTH_EVENT, ended);
    return () => window.removeEventListener(AUTH_EVENT, ended);
  }, []);

  const begin = useCallback((session: Session) => {
    if (lastUser.current && lastUser.current !== session.user.username) forgetWorkspace();  // someone else on this browser
    lastUser.current = session.user.username;
    saveSession(session);
    setState((s) => ({ ...s, status: "signed-in", account: session.user, message: null }));
  }, []);

  const signIn = useCallback(async (username: string, password: string) => {
    begin(await openApi<Session>("POST", "/auth/login", { username, password }));
  }, [begin]);

  const signUp = useCallback(async (username: string, password: string, name: string) => {
    begin(await openApi<Session>("POST", "/auth/signup", { username, password, display_name: name || null }));
  }, [begin]);

  const signOut = useCallback(() => {
    setOpen(false);
    clearSession();
    forgetWorkspace();
    lastUser.current = null;
    setState((s) => ({ ...s, status: "signed-out", account: null, message: null }));
    router.replace("/");
  }, [router]);

  const changePassword = useCallback(async (current: string, next: string) => {
    const session = await api<Session>("POST", "/auth/password", { current_password: current, new_password: next });
    saveSession(session);
  }, []);

  const open = state.status === "signed-in" || state.status === "disabled";
  let screen: ReactNode = null;
  if (state.status === "loading") screen = <div className="app-boot"><span className="spinner" /></div>;
  else if (state.status === "offline") screen = <Offline message={state.message} onRetry={() => { setState((s) => ({ ...s, status: "loading" })); setAttempt((n) => n + 1); }} />;
  else if (state.status === "signed-out") screen = <SignIn onSignIn={signIn} onSignUp={signUp} signup={state.signup} message={state.message} />;
  return (
    <Ctx.Provider value={{ state, signIn, signUp, signOut, changePassword }}>
      {/* always rendered (hidden while signed out) so every page keeps a real, mounted route */}
      <div style={{ display: open ? "contents" : "none" }} aria-hidden={!open}>{children}</div>
      {screen}
    </Ctx.Provider>
  );
}
