/*
 * The signed-in session (username + password sign-in, see app/auth.py).
 *
 * Signing in returns a token from the API; it's kept in this browser's
 * localStorage and sent with every request. Whether sign-in is on at all
 * is decided by the API (GET /api/info), so the web app needs no settings
 * for it.
 */
"use client";

export type SessionUser = { username: string; name: string };
export type Session = { token: string; user: SessionUser };

const KEY = "bip-session";
let current: Session | null = null;

export function loadSession(): Session | null {
  try {
    const raw = localStorage.getItem(KEY);
    current = raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    current = null;
  }
  return current;
}

export function saveSession(s: Session) {
  current = s;
  try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* storage blocked: lasts until reload */ }
}

export function clearSession() {
  current = null;
  try { localStorage.removeItem(KEY); } catch { /* ignore */ }
}

/* Requests wait here until the app knows it may call the API: signed in,
   or sign-in switched off. The app stays mounted (hidden) behind the
   sign-in screen, so nothing is fetched — and nothing fails — before that. */
let release: (() => void) | null = null;
let ready: Promise<void> = new Promise((r) => (release = r));
export function setOpen(open: boolean) {
  if (open) { release?.(); release = null; }
  else if (!release) ready = new Promise((r) => (release = r));
}

/** the token to send (null when sign-in is off), once requests may go */
export async function accessToken(): Promise<string | null> {
  await ready;
  return current?.token ?? null;
}

/* The API answered 401 (session over): the auth provider shows the sign-in screen. */
export const AUTH_EVENT = "bip:session-ended";
export function reportSessionEnded(message: string) {
  window.dispatchEvent(new CustomEvent<string>(AUTH_EVENT, { detail: message }));
}
