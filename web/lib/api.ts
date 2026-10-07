/*
 * Talking to the Python API (FastAPI, app/). The browser calls it directly
 * — CORS is enabled there — so large .pbix uploads stream straight to it.
 */
"use client";

import useSWR, { mutate as globalMutate, type SWRConfiguration } from "swr";
import { accessToken, reportAuthProblem } from "./supabase";
import type { FileRecord } from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

function detailOf(data: unknown, res: Response): string {
  const d = (data as { detail?: unknown } | null)?.detail;
  if (Array.isArray(d)) {
    return d.map((x) => (x && typeof x === "object" && "msg" in x
      ? `${((x as { loc?: unknown[] }).loc || []).slice(1).join(".")}: ${(x as { msg: string }).msg}`
      : String(x))).join("; ");
  }
  if (d && typeof d === "object") return JSON.stringify(d);
  if (typeof d === "string") return d;
  return `${res.status} ${res.statusText}`;
}

/** headers every request carries: the signed-in user's token, when sign-in is on */
export async function authHeaders(): Promise<Record<string, string>> {
  const token = await accessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function checkAuth(res: Response, message: string) {
  if (res.status === 401) reportAuthProblem({ kind: "expired", message });
  else if (res.status === 403 && /isn't allowed/i.test(message)) reportAuthProblem({ kind: "denied", message });
}

export async function api<T = unknown>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const init: RequestInit = { method, signal, headers: await authHeaders() };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, init);
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(`Can't reach the Python API at ${API_URL} — is it running? (uvicorn app.main:app)`, 0);
  }
  const text = await res.text();
  let data: unknown = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const message = detailOf(data, res);
    checkAuth(res, message);
    throw new ApiError(message, res.status);
  }
  return data as T;
}

/** download a file the API generates (Excel exports): fetched with the token, then saved */
export async function download(path: string, fallbackName: string) {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { headers: await authHeaders() });
  } catch {
    throw new ApiError(`Can't reach the Python API at ${API_URL} — is it running? (uvicorn app.main:app)`, 0);
  }
  if (!res.ok) {
    const text = await res.text();
    let data: unknown = null;
    try { data = JSON.parse(text); } catch { data = text; }
    const message = detailOf(data, res);
    checkAuth(res, message);
    throw new ApiError(message, res.status);
  }
  const disposition = res.headers.get("content-disposition") || "";
  const match = /filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/i.exec(disposition);
  const name = match ? decodeURIComponent(match[1] || match[2]) : fallbackName;
  const url = URL.createObjectURL(await res.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

export const get = <T,>(path: string) => api<T>("GET", path);
export const post = <T,>(path: string, body?: unknown) => api<T>("POST", path, body ?? {});
export const put = <T,>(path: string, body?: unknown) => api<T>("PUT", path, body);
export const patch = <T,>(path: string, body?: unknown) => api<T>("PATCH", path, body);
export const del = <T,>(path: string) => api<T>("DELETE", path);

/** query string from an object, skipping empty values */
export function qs(obj: Record<string, unknown>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(obj)) if (v !== undefined && v !== null && v !== "") u.set(k, String(v));
  const s = u.toString();
  return s ? `?${s}` : "";
}

export const enc = encodeURIComponent;

/** SWR hook over the API; `null` path = don't fetch yet */
export function useApi<T>(path: string | null, config?: SWRConfiguration<T>) {
  return useSWR<T>(path, (p: string) => get<T>(p), { revalidateOnFocus: false, shouldRetryOnError: false, ...config });
}

/** model documentation — expensive to build, so cached for the session */
export const overviewKey = (cid: string) => `/connections/${enc(cid)}/overview`;

export function refresh(prefix: string) {
  return globalMutate((key) => typeof key === "string" && key.startsWith(prefix), undefined, { revalidate: true });
}

/** re-read a model / re-sync a connection, then drop everything cached for it */
export async function syncConnection(cid: string) {
  const r = await post<{ nodes: number; edges: number; warnings: string[] }>(`/connections/${enc(cid)}/sync`);
  await refresh(`/connections/${enc(cid)}`);
  return r;
}

export async function uploadModel(file: File) {
  const fd = new FormData();
  fd.append("file", file);
  return api<{ connection_id: string; name: string; counts: Record<string, number>; warnings: string[] }>("POST", "/model-files", fd);
}

export async function uploadFiles(files: FileList | File[]) {
  const fd = new FormData();
  for (const f of Array.from(files)) fd.append("files", f);
  return api<FileRecord[]>("POST", "/files", fd);
}

