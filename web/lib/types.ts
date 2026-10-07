/* Shapes returned by the Python API (see app/engine/model_insights.py etc.). */

export interface ModelFile {
  connection_id: string;
  name: string;
  platform: string;
  file_name: string | null;
  last_synced_at: string | null;
}

export interface MeasureRow {
  id: string;
  name: string;
  table: string;
  folder: string | null;
  description: string | null;
  expression: string;
  explanation: string;
  category: string;
  depends_on_measures: string[];
  depends_on_columns: string[];
  depends_on_tables: string[];
  used_by_visuals: number;
  used_by_measures: number;
  visual_ids: string[];
  unused: boolean;
  unresolved: string[];
}

export interface ColumnRow {
  id: string;
  name: string;
  data_type: string;
  hidden: boolean;
  calculated: boolean;
  expression: string | null;
  used: boolean;
  explanation: string | null;
}

export interface TableRow {
  id: string;
  name: string;
  kind: string;
  hidden: boolean;
  rows: number | null;
  columns: ColumnRow[];
  column_count: number;
  calculated_columns: number;
  measure_count: number;
  source: string | null;
  summary: string | null;
  steps: { name: string; description: string }[];
  expression: string | null;
  depends_on_tables: string[];
  used: boolean;
  has_relationships: boolean;
}

export interface VisualRow {
  id: string;
  title: string;
  type: string | null;
  fields: { id: string; name: string; type: string }[];
  filters: { id: string; name: string; missing: boolean }[];
  broken: string[];
}

export interface PageRow {
  id: string;
  name: string;
  visuals: VisualRow[];
  hidden: boolean;
  other_visuals: number;
}

export interface RelationshipRow {
  id: string;
  from: string;
  to: string;
  from_table: string;
  to_table: string;
  from_column: string;
  to_column: string;
  from_kind?: string;
  to_kind?: string;
  direction: "single" | "both";
  active: boolean;
  cardinality: string;
  from_type?: string | null;
  to_type?: string | null;
  type_mismatch: boolean;
  missing: string[];
}

export interface Insight {
  severity: "high" | "medium" | "low" | "info";
  title: string;
  detail: string;
  items: string[];
  count: number;
  category: string;
}

export interface DataSource {
  kind: string | null;
  server: string | null;
  database: string | null;
  path: string | null;
  tables: { model_table: string; source_object: string | null; native_query: boolean }[];
}

export interface Overview {
  name: string;
  format: string | null;
  warnings: string[];
  connection_id: string;
  counts: {
    pages: number; visuals: number; tables: number; tables_by_kind: Record<string, number>;
    columns: number; calculated_columns: number; measures: number; relationships: number;
    data_sources: number; unused_measures: number; issues: number;
  };
  data_sources: DataSource[];
  tables: TableRow[];
  measures: MeasureRow[];
  relationships: RelationshipRow[];
  pages: PageRow[];
  insights: Insight[];
}

export interface FileRecord {
  file_id: string;
  name: string;
  stored_as: string;
  size: number;
  kind: string;
  sheets: { name: string; rows: number; columns: number }[];
  uploaded_at: string;
}

export interface LxNode {
  id: string;
  name: string;
  type: string;
  level: number;
  parent_id: string | null;
  properties: Record<string, unknown>;
  up_count: number;
  down_count: number;
  confidence: string;
}
