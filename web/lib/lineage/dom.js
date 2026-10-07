/*
 * Small DOM/API helpers the imperative lineage canvas (explorer.js) uses.
 * Everything React-side lives in components/; this keeps the canvas engine
 * framework-free (it draws thousands of SVG nodes with hand-tuned motion).
 */
import { API_URL } from "../api";

export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];
export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const q = (obj) => {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(obj)) if (v !== undefined && v !== null && v !== "") u.set(k, v);
  const s = u.toString();
  return s ? `?${s}` : "";
};
export const debounce = (fn, ms) => {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
};
export const isAbort = (e) => e?.name === "AbortError";
const nf0 = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
export const fmt = { int: (v) => (v == null ? "—" : nf0.format(v)) };

/** toasts are rendered by the React <Toaster>; the canvas just asks for one */
export const toast = (msg, kind = "") => window.dispatchEvent(new CustomEvent("bip:toast", { detail: { msg, kind } }));
export const loading = (text = "Loading…") => `<div class="loading"><span class="spinner"></span>${esc(text)}</div>`;
export const alertBox = (kind, items) =>
  !items || !items.length ? "" : `<div class="alert ${kind}">${items.length === 1 ? esc(items[0]) : `<ul>${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>`}</div>`;

export async function GET(path) {
  const res = await fetch(`${API_URL}${path}`);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const err = new Error((data && data.detail && (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail))) || `${res.status} ${res.statusText}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

export async function syncConnection(cid) {
  const res = await fetch(`${API_URL}/connections/${encodeURIComponent(cid)}/sync`, { method: "POST" });
  if (!res.ok) throw new Error(`Sync failed (${res.status})`);
  return res.json();
}

export async function act(btn, label, fn) {
  const before = btn?.innerHTML;
  if (btn) { btn.disabled = true; btn.innerHTML = `<span class="spinner"></span> ${esc(label)}`; }
  try { return await fn(); } catch (e) { toast(e.message, "bad"); return undefined; } finally { if (btn) { btn.disabled = false; btn.innerHTML = before; } }
}
