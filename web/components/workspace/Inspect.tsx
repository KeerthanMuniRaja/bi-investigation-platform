"use client";

/* Everything the file tells us about one object (opened by double-click). */
import Link from "next/link";
import { motion } from "motion/react";
import type { ReactNode } from "react";
import { DaxCode } from "@/components/DaxCode";
import { AlertBox, Badge, I, Icon } from "@/components/ui";
import { nodeType } from "@/lib/format";
import { softSpring } from "@/lib/motion";
import { wsPath } from "@/lib/routes";
import { indexOf } from "@/lib/overview";
import type { LxNode, Overview, RelationshipRow } from "@/lib/types";

function Chip({ text, type, onJump, color }: { text: string; type: string; onJump?: () => void; color?: string }) {
  return (
    <motion.button className="ins-chip" onClick={onJump} disabled={!onJump} whileHover={onJump ? { y: -2 } : undefined} whileTap={onJump ? { scale: 0.96 } : undefined}>
      <i style={{ background: color || nodeType(type).color }} />{text}
    </motion.button>
  );
}

function Kv({ rows }: { rows: [string, ReactNode][] }) {
  const shown = rows.filter(([, v]) => v !== undefined && v !== null && v !== "" && v !== false);
  return <dl className="ins-kv">{shown.map(([k, v]) => <div key={k} style={{ display: "contents" }}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  const empty = !children || (Array.isArray(children) && !children.length);
  if (empty) return null;
  return <motion.div className="ins-sec" variants={{ hidden: { opacity: 0, y: 6 }, show: { opacity: 1, y: 0 } }}><h3>{title}</h3>{children}</motion.div>;
}

const none = <span className="faint">none</span>;

/** the joins one table (or one key column) takes part in, seen from that side */
function Relationships({ rels, side, onJump }: { rels: RelationshipRow[]; side: string; onJump: (id: string) => void }) {
  if (!rels.length) return null;
  return (
    <Section title={`Relationships (${rels.length})`}>
      <div className="ins-rels">
        {rels.map((r) => {
          const fromHere = r.from_table === side || r.from === side;
          const [mine, other, otherTable] = fromHere ? [r.from_column, `${r.to_table}[${r.to_column}]`, r.to_table] : [r.to_column, `${r.from_table}[${r.from_column}]`, r.from_table];
          const card = r.cardinality === "many-to-many" ? "*:*" : r.cardinality === "one-to-one" ? "1:1" : fromHere ? "*:1" : "1:*";
          return (
            <motion.div key={r.id} className={`ins-rel ${r.active ? "" : "inactive"}`} whileHover={{ x: 2 }}>
              <span className="ins-rel-key mono" title={mine}>{mine}</span>
              <span className={`ins-rel-line ${r.direction === "both" ? "both" : ""}`} title={r.direction === "both" ? "filters flow both ways" : fromHere ? `filters flow from ${otherTable} to here` : `filters flow from here to ${otherTable}`}>
                <em>{card}</em>
              </span>
              <button className="ins-rel-other" onClick={() => onJump(otherTable)} title={`Open ${otherTable}`}>
                <i style={{ background: nodeType("TABLE").color }} />{other}
              </button>
              <span className="ins-rel-tags">
                {!r.active ? <Badge>inactive</Badge> : null}{r.direction === "both" ? <Badge kind="warn">both ways</Badge> : null}
                {r.cardinality === "many-to-many" ? <Badge kind="bad">many-to-many</Badge> : null}
                {r.missing.length ? <Badge kind="bad">missing column</Badge> : null}{r.type_mismatch ? <Badge kind="bad">type mismatch</Badge> : null}
              </span>
            </motion.div>
          );
        })}
      </div>
    </Section>
  );
}

export function Inspect({ cid, pid, ov, node, rootId, onJump, onClose }: {
  cid: string; pid: string | null; ov: Overview; node: LxNode; rootId: string | null;
  onJump: (id: string) => void; onClose: () => void;
}) {
  const idx = indexOf(ov);
  const t = nodeType(node.type);
  let badges: ReactNode = null;
  let body: ReactNode = null;

  if (node.type === "MEASURE") {
    const m = idx.measures.get(node.id);
    const usedBy = ov.measures.filter((x) => (x.depends_on_measures || []).includes(m?.name || ""));
    const colGroups = new Map<string, string[]>();
    for (const id of m?.depends_on_columns || []) {
      const i = id.lastIndexOf("[");
      const tb = i > 0 ? id.slice(0, i) : "";
      if (!colGroups.has(tb)) colGroups.set(tb, []);
      colGroups.get(tb)!.push(id);
    }
    badges = <>{m?.category ? <Badge kind="info">{m.category}</Badge> : null}{m?.unused ? <Badge kind="warn">not used by any visual</Badge> : null}{m?.unresolved?.length ? <Badge kind="bad">broken reference</Badge> : null}</>;
    body = (
      <>
        {m?.explanation ? <div className="ins-explain"><b>In plain English:</b> {m.explanation}</div> : null}
        <div className="ins-grid">
          <div>
            <DaxCode code={m?.expression || (node.properties?.dax_expression as string)} label="DAX" />
            {m?.unresolved?.length ? <AlertBox kind="bad" items={[`References that don't exist in the model: ${m.unresolved.join(", ")}`]} /> : null}
          </div>
          <div>
            <Kv rows={[
              ["Home table", m?.table], ["Display folder", m?.folder ? <><Icon d={I.folder} className="inl" />{m.folder}</> : none],
              ["Description", m?.description || none], ["Kind", m?.category],
              ["Used by", `${m?.used_by_visuals || 0} visual(s), ${m?.used_by_measures || 0} measure(s)`],
            ]} />
            <Section title="Depends on">
              {(m?.depends_on_measures || []).map((n) => <Chip key={`m${n}`} text={n} type="MEASURE" onJump={idx.byName.get(n) ? () => onJump(idx.byName.get(n)!.id) : undefined} />)}
              {(m?.depends_on_tables || []).map((tb) => <Chip key={`t${tb}`} text={tb} type="TABLE" onJump={() => onJump(tb)} />)}
            </Section>
            <Section title="Reads columns">
              {[...colGroups].map(([tb, ids]) => (
                <div key={tb} className="ins-colgroup"><span className="ins-colt">{tb}</span>
                  {ids.map((id) => <Chip key={id} text={id.slice(id.lastIndexOf("[") + 1, -1)} type="COLUMN" onJump={() => onJump(id)} />)}</div>
              ))}
            </Section>
            <Section title="Shown on">
              {(m?.visual_ids || []).map((v) => { const vis = idx.visuals.get(v); return <Chip key={v} text={vis ? `${vis.page.name} › ${vis.title}` : v} type="VISUAL" onJump={() => onJump(v)} />; })}
            </Section>
            <Section title="Used by measures">{usedBy.map((x) => <Chip key={x.id} text={x.name} type="MEASURE" onJump={() => onJump(x.id)} />)}</Section>
          </div>
        </div>
      </>
    );
  } else if (node.type === "COLUMN") {
    const c = idx.columns.get(node.id);
    badges = <>{c?.calculated ? <Badge kind="info">calculated</Badge> : null}{c?.hidden ? <Badge>hidden</Badge> : null}{c && !c.used ? <Badge kind="warn">unused</Badge> : null}</>;
    body = (
      <>
        {c?.explanation ? <div className="ins-explain"><b>In plain English:</b> {c.explanation}</div> : null}
        <div className="ins-grid">
          <div>{c?.expression ? <DaxCode code={c.expression} label="DAX (calculated column)" /> :
            <div className="ins-explain faint">A stored column, loaded from {c?.table || "its table"}{c?.tableSource ? ` (${c.tableSource})` : ""}.</div>}</div>
          <div><Kv rows={[
            ["Table", c?.table ? <Chip text={c.table} type="TABLE" onJump={() => onJump(c.table)} /> : null],
            ["Data type", c?.data_type || (node.properties?.data_type as string)], ["Table kind", c?.tableKind], ["Loads from", c?.tableSource],
            ["Hidden", c ? (c.hidden ? "yes" : "no") : null],
          ]} />
          <Relationships rels={ov.relationships.filter((r) => r.from === node.id || r.to === node.id)} side={node.id} onJump={onJump} /></div>
        </div>
      </>
    );
  } else if (node.type === "TABLE") {
    const tb = idx.tables.get(node.id);
    badges = <><Badge kind="info">{tb?.kind || (node.properties?.kind as string) || "table"}</Badge>{tb?.hidden ? <Badge>hidden</Badge> : null}{tb && !tb.used ? <Badge kind="warn">unused</Badge> : null}</>;
    body = (
      <>
        {tb?.summary ? <div className="ins-explain"><b>How it&apos;s loaded:</b> {tb.summary}</div> : null}
        <div className="ins-grid">
          <div>
            <Section title={`Power Query steps (${tb?.steps?.length || 0})`}>
              {tb?.steps?.length ? <ol className="ins-steps">{tb.steps.map((s, i) => <li key={i}><b>{s.name}</b><span>{s.description}</span></li>)}</ol> : null}
            </Section>
            <DaxCode code={tb?.expression} label={tb?.kind === "calculated" || tb?.kind === "parameter" ? "DAX definition" : "Power Query (M)"} />
          </div>
          <div>
            <Kv rows={[
              ["Loads from", tb?.source], ["Rows", tb?.rows != null ? tb.rows.toLocaleString() : null],
              ["Column count", tb ? `${tb.column_count}${tb.calculated_columns ? ` (${tb.calculated_columns} calculated)` : ""}` : null],
              ["Measures stored here", tb?.measure_count || null], ["Relationships", tb && !tb.has_relationships ? <span className="faint">none — not joined to any table</span> : null],
              ["Built from", tb?.depends_on_tables?.length ? <>{tb.depends_on_tables.map((d) => <Chip key={d} text={d} type="TABLE" onJump={() => onJump(d)} />)}</> : null],
            ]} />
            <Relationships rels={ov.relationships.filter((r) => r.from_table === node.id || r.to_table === node.id)} side={node.id} onJump={onJump} />
            <Section title="Columns">
              {tb?.columns?.length ? <div className="ins-cols">{tb.columns.map((c) => (
                <motion.button key={c.id} className="ins-col" onClick={() => onJump(c.id)} whileHover={{ y: -2 }}><span>{c.name}</span><em>{c.data_type}{c.calculated ? " · calc" : ""}</em></motion.button>
              ))}</div> : null}
            </Section>
          </div>
        </div>
      </>
    );
  } else if (node.type === "VISUAL") {
    const v = idx.visuals.get(node.id);
    badges = <>{v?.type ? <Badge kind="info">{v.type}</Badge> : null}{v?.broken?.length ? <Badge kind="bad">{v.broken.length} broken field(s)</Badge> : null}</>;
    body = (
      <div className="ins-grid">
        <div><Kv rows={[["Page", v?.page.name], ["Visual type", v?.type], ["Visual id", <code key="id">{node.id}</code>]]} /></div>
        <div>
          <Section title="Shows">{(v?.fields || []).map((f) => <Chip key={f.id} text={f.name} type={f.type} color={f.type === "MISSING" ? "var(--bad)" : undefined} onJump={f.type === "MISSING" ? undefined : () => onJump(f.id)} />)}</Section>
          <Section title="Filtered by">{(v?.filters || []).map((f) => <Chip key={f.id} text={f.name} type="COLUMN" color={f.missing ? "var(--bad)" : undefined} onJump={f.missing ? undefined : () => onJump(f.id)} />)}</Section>
          {v?.broken?.length ? <AlertBox kind="bad" items={[`Fields that no longer exist in the model: ${v.broken.join(", ")}`]} /> : null}
        </div>
      </div>
    );
  } else if (node.type === "DATA_SOURCE") {
    const tables = ov.tables.filter((tb) => tb.source && (node.name === tb.source || node.id === `SRC::${tb.source}`));
    const src = ov.data_sources.find((s) => s.tables.some((x) => tables.some((tb) => tb.name === x.model_table)));
    body = (
      <div className="ins-grid">
        <div><Kv rows={[["Kind", src?.kind], ["Server", src?.server], ["Database", src?.database], ["Path", src?.path],
          ["Upstream", node.properties?.upstream_traced ? "traced" : <span className="faint">ETL / views upstream of this source aren&apos;t visible in the file</span>]]} /></div>
        <div><Section title="Loads these tables">
          {(src?.tables || tables.map((tb) => ({ model_table: tb.name, source_object: null, native_query: false }))).map((x) => (
            <Chip key={x.model_table} text={`${x.model_table}${x.source_object ? ` ← ${x.source_object}` : ""}${x.native_query ? " (SQL query)" : ""}`} type="TABLE" onJump={() => onJump(x.model_table)} />
          ))}
        </Section></div>
      </div>
    );
  } else {
    body = <AlertBox kind={node.type === "MISSING" ? "bad" : "info"} items={[node.type === "MISSING"
      ? `“${node.name}” is referenced by the report or a formula but doesn't exist in the model — it was renamed or removed.` : `${t.label}: ${node.name}`]} />;
  }

  return (
    <motion.section className="ws-inspect" layout initial={{ opacity: 0, y: 24, scale: 0.99 }} animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 16, transition: { duration: 0.18 } }} transition={softSpring}>
      <header className="ins-head">
        <motion.span className="ins-ico" style={{ background: t.color }} initial={{ scale: 0.6, rotate: -12 }} animate={{ scale: 1, rotate: 0 }} transition={softSpring}>{t.glyph}</motion.span>
        <div className="ins-titles">
          <div className="ins-type" style={{ color: t.color }}>{t.label}</div>
          <h2>{node.name}</h2>
          {badges ? <div className="ins-badges">{badges}</div> : null}
        </div>
        <div className="ins-actions">
          {node.id !== rootId && node.type !== "MISSING" ? <Link className="btn sm primary" href={wsPath({ connection_id: cid, name: ov.name }, ov, { page: pid, object: node.id })}><Icon d={I.link} />Trace this instead</Link> : null}
          <button className="btn sm ghost" onClick={onClose} title="Close"><Icon d={I.close} /></button>
        </div>
      </header>
      <motion.div className="ins-body" key={node.id} initial="hidden" animate="show" variants={{ hidden: {}, show: { transition: { staggerChildren: 0.05 } } }}>
        {body}
      </motion.div>
    </motion.section>
  );
}
