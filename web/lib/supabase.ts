/*
 * Supabase in the browser: Google sign-in and the session token.
 *
 *   NEXT_PUBLIC_SUPABASE_URL              https://<project>.supabase.co
 *   NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY  the project's publishable key, sb_publishable_…
 *                                         (or NEXT_PUBLIC_SUPABASE_ANON_KEY, the older name)
 * Both are safe in the browser.
 *
 * Without them sign-in is off and the app talks to a local API with no
 * accounts (local development).
 */
"use client";

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const URL_ = process.env.NEXT_PUBLIC_SUPABASE_URL?.trim();
// each read is spelled out: Next only inlines literal process.env.NEXT_PUBLIC_* references
const KEY = (process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY || process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY)?.trim();

export const authEnabled = !!(URL_ && KEY);

let client: SupabaseClient | null = null;
export function supabase(): SupabaseClient | null {
  if (!authEnabled || typeof window === "undefined") return null;
  if (!client) {
    client = createClient(URL_!, KEY!, {
      auth: { flowType: "pkce", persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
    });
  }
  return client;
}

/* Requests wait here until someone is signed in: the app stays mounted (just
   hidden) behind the sign-in screen, so nothing is fetched — and nothing
   fails — before there's a session. */
let release: (() => void) | null = null;
let ready: Promise<void> = authEnabled ? new Promise((r) => (release = r)) : Promise.resolve();
export function setSignedIn(signedIn: boolean) {
  if (!authEnabled) return;
  if (signedIn) { release?.(); release = null; }
  else if (!release) ready = new Promise((r) => (release = r));
}

/** the current access token (refreshed when it's about to expire), or null */
export async function accessToken(): Promise<string | null> {
  const sb = supabase();
  if (!sb) return null;
  await ready;
  const { data } = await sb.auth.getSession();
  return data.session?.access_token ?? null;
}

/* The API answered 401 (session gone) or 403 (account not allowed): the
   auth provider listens and shows the right screen. */
export type AuthProblem = { kind: "expired" | "denied"; message: string };
export const AUTH_EVENT = "bip:auth-problem";
export function reportAuthProblem(problem: AuthProblem) {
  window.dispatchEvent(new CustomEvent<AuthProblem>(AUTH_EVENT, { detail: problem }));
}
