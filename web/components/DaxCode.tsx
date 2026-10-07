"use client";

import { motion } from "motion/react";
import { useState } from "react";
import { I, Icon } from "./ui";

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));

/** DAX / Power Query syntax colouring (escaped, so safe for innerHTML) */
export function highlight(src: string): string {
  const text = String(src || "").replace(/^\s*\n/, "").replace(/\s+$/, "");
  const re = /(\/\/[^\n]*|--[^\n]*|\/\*[\s\S]*?(?:\*\/|$))|("(?:[^"]|"")*")|('(?:[^']|'')+')|(\[(?:[^\]]|\]\])+\])|(\b\d+(?:\.\d+)?\b)|\b([A-Za-z_][\w.]*)(?=\s*\()|\b(VAR|RETURN|IN|NOT|AND|OR|TRUE|FALSE|ASC|DESC|let|each)\b/g;
  let out = "", last = 0;
  for (const m of text.matchAll(re)) {
    const cls = m[1] ? "c" : m[2] ? "s" : m[3] ? "t" : m[4] ? "r" : m[5] ? "n" : m[6] ? "f" : "k";
    out += esc(text.slice(last, m.index)) + `<span class="dx-${cls}">${esc(m[0])}</span>`;
    last = (m.index || 0) + m[0].length;
  }
  return out + esc(text.slice(last));
}

export function DaxCode({ code, label }: { code: string | null | undefined; label: string }) {
  const [copied, setCopied] = useState(false);
  if (!code) return null;
  const copy = () => navigator.clipboard?.writeText(code).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1400); });
  return (
    <div className="ins-code">
      <div className="ins-code-head">
        <span>{label}</span>
        <motion.button className="lx-btn" onClick={copy} whileTap={{ scale: 0.92 }} title="Copy">
          <Icon d={copied ? I.check : I.copy} />{copied ? "Copied" : "Copy"}
        </motion.button>
      </div>
      <pre className="mono" dangerouslySetInnerHTML={{ __html: highlight(code) }} />
    </div>
  );
}
