const nf2 = new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const nf0 = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });

export const fmt = {
  num: (v: number | null | undefined) => (v == null || Number.isNaN(v) ? "—" : nf2.format(v)),
  int: (v: number | null | undefined) => (v == null ? "—" : nf0.format(v)),
  signed: (v: number | null | undefined) => (v == null ? "—" : (v > 0.004 ? "+" : "") + nf2.format(v)),
  pct: (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`),
  /** never shows 100.0% unless it really is 100 */
  rate: (v: number | null | undefined) => {
    if (v == null || Number.isNaN(Number(v))) return "—";
    const n = Number(v);
    return `${(n === 100 ? 100 : n < 100 ? Math.min(99.9, Math.floor(n * 10 + 1e-9) / 10) : Math.floor(n * 10) / 10).toFixed(1)}%`;
  },
  date: (s: string | null | undefined) => (s ? new Date(s).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—"),
  bytes: (n: number) => (n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`),
};

export const signClass = (v: number | null | undefined) => (v == null ? "" : v > 0.004 ? "pos" : v < -0.004 ? "neg" : "");

/* object types in the lineage graph: label, colour token, glyph */
export const NODE_TYPES: Record<string, { label: string; color: string; glyph: string }> = {
  DATA_SOURCE: { label: "Data source", color: "var(--n-source)", glyph: "DB" },
  TABLE: { label: "Table", color: "var(--n-table)", glyph: "T" },
  COLUMN: { label: "Column", color: "var(--n-column)", glyph: "C" },
  MEASURE: { label: "Measure", color: "var(--n-measure)", glyph: "Σ" },
  VISUAL: { label: "Visual", color: "var(--n-visual)", glyph: "V" },
  MISSING: { label: "Missing field", color: "var(--bad)", glyph: "!" },
};
export const nodeType = (t: string | null | undefined) =>
  NODE_TYPES[t || ""] || { label: String(t || "object").toLowerCase().replace("_", " "), color: "var(--n-other)", glyph: "•" };

export const CONF_TEXT: Record<string, [string, string]> = {
  KNOWN: ["Known", "ok"], VERIFIED: ["Verified", "ok"], PARTIALLY_VERIFIED: ["Partially verified", "warn"],
  INFERRED: ["Inferred", "info"], UNKNOWN: ["Unknown", "neutral"], UNAVAILABLE: ["Unavailable", "bad"], UNSUPPORTED: ["Unsupported", "bad"],
};
