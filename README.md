# BI Investigation & Trust Platform

**Upload a Power BI file and understand it.** Drop in a `.pbix` (or `.pbit`,
`.bim`, or a PBIP project) and the platform reads the report itself, with no
exports, no credentials and no Power BI service:

| You get | From |
|---|---|
| **Lineage** for any visual, measure or column: visual → measures → columns → tables → the Power Query source (SQL Server db.schema.table, Snowflake, Excel on SharePoint, dataflows…) | The model, the report layout and each table's M query |
| **Impact analysis**: what breaks if a column, measure or table changes | The same dependency graph, walked downstream |
| **Every measure explained in plain English**, with its DAX, what it depends on and which visuals use it | A built-in DAX explainer (deterministic, no AI service) |
| **Where the data comes from**: every source system and which model tables load from it, plus the Power Query steps in plain English | Power Query (M) parsing |
| **Report map**: every page and visual, the fields each one shows and the filters applied (visual, page and report level) | Both the legacy `Report/Layout` and the newer PBIR formats |
| **Health check**: broken field references, formulas pointing at missing fields, unused measures and columns, auto date/time bloat, bi-directional and many-to-many relationships, disconnected tables, hard-coded years, IFERROR, entered-data tables | Cross-checking all of the above |
| **Table data** stored in a `.pbix`, for preview or as a reconciliation source | The model's VertiPaq storage (via `pbixray`) |

The platform also **reconciles** dashboards against Excel and against each other (below).

Built to remove the manual work in BI reconciliation:

| Manual today | In the platform |
|---|---|
| Export the visual, open the finance workbook, line rows up by hand | Pick a visual (or any exported tab) and an Excel tab. Header rows, merged cells, total rows and `£1,234` / `(1,200)` / `3.6M` formats are handled for you |
| Work out which column is which | Columns are matched automatically (names, synonyms, shared values), and you confirm the mapping |
| VLOOKUPs and a pivot to find what's different | Every key is marked **match / value differs / only in A / only in B**, with tolerances |
| "The totals don't agree, but why?" | A **variance bridge** (missing rows vs changed values) plus **ranked likely causes**: scope/filter, cut-off timing, key formatting, units (£ vs £k), FX/VAT factor, sign convention, rounding, reclassification, double counting, specific records |
| Rebuild the same check every month end | **Templates**: save the setup once, then re-run it with one click or with new file versions |
| Explain the answer in a meeting | An Excel workbook with the summary, bridge, causes, differences and mapping, plus an investigation case holding notes and evidence |

It works with **any BI tool**:

| Source | What you get |
|---|---|
| **Power BI file** (`.pbix`, `.pbit`, `.bim`, PBIP folder or zip) | The full model, Power Query sources, pages, visuals and filters, read from the file. Lineage, impact and the health check with no credentials. `.pbix` also exposes the stored table data |
| **Exported files** (Power BI "Export data", Tableau crosstab, Qlik, Looker, SSRS, Excel…) | Each file is a report and each tab is a page. Full reconciliation from day one, with no API access needed |
| **Power BI (live)** | Service principal + Execute Queries. Tables, columns, measures and relationships via `INFO.VIEW.*`; measure dependencies via `INFO.CALCDEPENDENCY` (or parsed from DAX). Visual data and snapshots via generated `SUMMARIZECOLUMNS` when a PBIP export is configured |
| **Power BI project (PBIP folder)** | Offline and credential-free. Full visual → measure → column → table → source lineage and impact analysis from PBIR/TMDL files |
| **Tableau Server / Cloud** | Personal access token. View data via REST (`/views/{id}/data`, with `vf_` filters) and lineage via the Metadata API |

## Quick start

The app has two parts: a **Python API** (FastAPI, in `app/`) that reads the
files and does all the work, and a **Next.js frontend** (in `web/`) that you
use in the browser.

```bash
pip install -r requirements.txt
cd web && npm install

npm run dev          # starts both: the API on :8000 and the web app on :3000
# Web app:  http://localhost:3000/
# API docs: http://localhost:8000/docs
```

Run them separately if you prefer: `uvicorn app.main:app --reload` from the
project root (the folder that contains `app/`) and `npm run dev:web` in
`web/`. The frontend calls the API at `NEXT_PUBLIC_API_URL` (default
`http://127.0.0.1:8000`; set it in `web/.env.local`). The API allows
browser calls from `localhost:3000`; add other origins with
`BIP_CORS_ORIGINS` (comma separated). Other scripts in `web/`:
`npm run build` / `npm start` (production), `npm run typecheck`,
`npm run lint`.

Opening the API address `http://localhost:8000/` takes you to the app,
and old `#/…` links (`http://localhost:8000/#/reconcile`) open the same
place there (`http://localhost:3000/reconcile`). If the app isn't running,
that page says how to start it. Point it elsewhere with `BIP_WEB_URL`. The
previous plain-JS interface is still available at
`http://localhost:8000/legacy`.

**Dashboard workspace (home screen).** The left sidebar lists every
uploaded dashboard (**+** uploads a new `.pbix`); open one to see its
model views and pages. Pick a page and its measures appear in the right
sidebar, grouped by display folder, with a search over all measures
(name, folder, table and DAX). Pick a measure to see its whole lineage,
from the data source to every visual; double-click any object for its
details: DAX with syntax highlighting, plain-English explanation, folder,
description, dependencies, columns read, the measures and visuals that use
it, Power Query steps for tables, and fields and filters for visuals.
Keyboard: `/` search, ↑/↓ and Enter in the measure list. The app reopens
where you left off. Below the dashboards, the home screen summarises
reconciliation activity.

1. **Power BI models**: drop a `.pbix` to see its lineage, measures,
   relationships (with a model diagram), sources and health check.
   Everything is read locally on this machine.
2. **Lineage & impact**: an interactive graph from the source system to
   every visual. Set how many steps to load upstream / downstream
   (top-left), use **+ / −** on any node to expand or collapse one more
   step, click a node to light up its whole lineage line (click again to
   turn it off), shift-click a second node for the path between the two,
   double-click to re-centre. Also: search (`/`), fit (`F`), zoom and pan,
   type filters that bridge hidden objects, a minimap, impact analysis per
   object, SVG/PNG export, full screen and shareable links.
3. **Sources**: upload dashboard exports / finance Excel files, or add a
   live Power BI or Tableau connection.
4. **New reconciliation**: pick side A (the dashboard visual or tab) and
   side B (the Excel tab), confirm the column mapping, and run.
5. **Results**: review the variance bridge and likely causes, download the
   Excel workbook, open an investigation, or save it as a template.

Data is stored in `./data/` (SQLite and uploaded files). Change it with
`BIP_DATA_DIR`; exports go to `EXPORT_DIR` (default `./data/exports`).

**Tests:** `pip install -r requirements-dev.txt` then `python -m pytest`. The suite uses
the small files in `samples/` and the PBIP project in `fixtures/`
(regenerate the samples with `python scripts/make_sample_files.py`); every
test runs against its own temporary data folder.

## Deploying

The two parts are hosted separately:

- **Web app (`web/`) on Vercel.** Import the repo, set **Root Directory**
  to `web`, and add the environment variable `NEXT_PUBLIC_API_URL` with the
  API's public URL (see `web/.env.example`). Vercel detects Next.js and
  builds it.
- **API (`app/`) on a server with a disk**, e.g. Render, Railway, Fly.io,
  Azure App Service or a VM:
  `pip install -r requirements.txt` then
  `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. It can't run on
  Vercel: it keeps its SQLite database and uploaded files on disk
  (`BIP_DATA_DIR`), takes uploads of hundreds of MB, and reading a model
  can take minutes. Set `BIP_CORS_ORIGINS` to the web app's URL (for
  example `https://your-app.vercel.app`) and `BIP_WEB_URL` to the same URL.

The API has no login yet: anyone who can reach its URL can read and upload
models. Keep it private (VPN, IP allow-list or an auth proxy) until
authentication is added.

## Architecture

```
app/
  models/        Pydantic data contracts: graph (Node/Edge/Confidence), Dataset + DatasetSpec,
                 DataReconciliation (+ bridge, drivers, hypotheses), Investigation, snapshots
  sources/       files.py: Excel/CSV reading, header detection, merged cells, total/note rows
  connectors/    BIConnector interface + implementations:
                   model_file.py (.pbix/.pbit/.bim/PBIP: model + report + Power Query)
                   files.py (any BI export) · powerbi.py (live) · pbip.py (PBIP/TMDL offline)
                   tableau.py · registry.py (build from a saved connection)
  engine/        Platform-agnostic logic. It never imports a specific connector:
                   values.py              number/key normalization shared by every comparison
                   mapping.py             automatic column mapping
                   data_reconciliation.py row-level comparison + variance bridge
                   hypotheses.py          ranked root-cause hypotheses
                   variance.py            dimension drivers + rate/volume decomposition
                   dax.py                 DAX reference parsing + SUMMARIZECOLUMNS builder
                   dax_explain.py         plain-English DAX explanations
                   powerquery.py          M parsing: source systems, query references, steps
                   model_insights.py      model documentation + health check
                   extraction/normalization/graph_store/lineage/impact   metadata graph
                   investigation.py       evidence, findings, hypotheses → case; honest status
                   excel_export.py        reconciliation + investigation workbooks
  services/      recon_service.py: orchestration (upload, materialize datasets, run, templates)
  store/         database.py: SQLite document store (pydantic models are the schema)
  api/           FastAPI routers (connections, semantic/lineage, files, datasets, recon,
                 templates, snapshots, investigations, exports)
  static/        The previous web app (plain HTML/CSS/JS), still served at :8000
web/             The frontend: Next.js (App Router) + React + TypeScript + Motion
  app/           routes (the folder tree is the URL scheme, see "Addresses" below)
  components/    shell (sidebars, breadcrumbs), workspace, model views, lineage, recon
  lib/           API client (SWR), formatting, motion presets, lineage engine
```

**Addresses.** Every URL is made of names, so a link says what it opens:

| Address | Opens |
|---|---|
| `/` | your dashboards and reconciliation activity |
| `/dashboards/sales-dashboard-4` | one dashboard's summary |
| `/dashboards/sales-dashboard-4/pages/revenue-overview` | a report page (its measures on the right) |
| `…/pages/<page>/measures/total-revenue` or `/dashboards/<d>/measures/<m>` | a measure's full lineage |
| `/dashboards/<d>/tables/<table>[/columns/<column>]`, `…/pages/<p>/visuals/<v>` | a table, column or visual's lineage |
| `/dashboards/<d>/model[/report-map\|measures\|tables\|relationships\|health-check]` | model documentation |
| `/lineage/<d>/measures/<m>?up=2&down=3` | the full lineage explorer |
| `/results/<name>-<n>`, `/investigations/<question>-<n>`, `/templates/<name>-<n>` | a result, an investigation case, a template's run history |
| `/reconcile`, `/sources` | new reconciliation, sources & files |

Saved records end in their record number (`sales-dashboard-4` is
connection `CONN0004`), so same-named dashboards never clash and renaming
never breaks a link. Objects inside a dashboard use their name; a rare
duplicate gets `-2`. Raw ids and links from before (`/?c=…&m=…`,
`/models/…?tab=…`, `/runs/…`) still work and are redirected to the
readable form. The scheme lives in `web/lib/routes.ts`.

**How data flows:** a *DatasetSpec* (an Excel tab, a BI visual plus
filters, a custom query) becomes a *Dataset*. Two datasets and a
*ReconciliationConfig* (keys, values, tolerances, filters, scaling) produce
a *DataReconciliation*. That can be saved as a template, attached to an
*Investigation*, and exported.

**Adding a BI platform** means implementing `BIConnector` (the metadata
methods plus `get_visual_data`) and adding one branch in
`connectors/registry.py`. Nothing in `engine/` or `api/` changes.

### Design principles carried through

- **No false completeness.** When a connector can't do something, the API
  returns `501` with the reason instead of an empty result. Lineage reports
  its coverage and gaps, including what lies upstream of each data source.
- **Explicit confidence.** Every step, finding and hypothesis carries
  KNOWN / VERIFIED / PARTIALLY_VERIFIED / INFERRED / … and investigations
  roll up to their weakest finding. Dependencies parsed from DAX text are
  INFERRED, never VERIFIED.
- **Root cause only when earned.** An investigation is marked
  `ROOT_CAUSE_FOUND` only when a hypothesis is supported by strong evidence.
  A computed variance on its own doesn't count.

## Connecting real platforms

**Power BI**: create a service principal, add it to the workspace, and
enable *"Dataset Execute Queries REST API"* (plus service principal API
access) in the tenant settings. In the web app choose *Sources → Add
connection → Power BI*, or set `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`,
`AZURE_CLIENT_SECRET` and `POWERBI_WORKSPACE_ID`. For visual-level data,
save the report as a PBIP project (Desktop → *Save as → Power BI project*
or a Fabric git checkout) and set `pbip_dir` / `POWERBI_PBIP_DIR`. Without
PBIP, you can still reconcile a custom DAX query (dataset
`kind: "bi_query"`) or an exported file.

**Tableau**: create a personal access token and use *Sources → Add
connection → Tableau*, or set `TABLEAU_SERVER_URL`, `TABLEAU_SITE`,
`TABLEAU_PAT_NAME` and `TABLEAU_PAT_SECRET`.

Secrets are held in server memory only and are never written to disk.
After a restart, re-enter them (*Enter credentials*) or supply them via
environment variables.

> The live Power BI and Tableau connectors follow the documented APIs but
> have **not yet been run against a live tenant**. Verify the `INFO.VIEW.*` field names
> and the Metadata API schema against your tenant on first use.

## API at a glance

| Area | Endpoints |
|---|---|
| Power BI files | `POST /model-files` (multipart `file`) · `GET /connections/{id}/overview` (docs + health check) · `GET /connections/{id}/table-data?table=` · `POST /dax/explain` |
| Sources | `GET /platforms` · `POST/GET/DELETE /connections` · `POST /connections/{id}/sync` · browse `/connections/{id}/workspaces · reports · pages · visuals` · `POST /connections/{id}/visual-data` |
| Files | `POST /files` (multipart) · `GET /files/{id}/preview?sheet=&header_row=` |
| Datasets | `POST /datasets` (`kind`: `file`, `bi_visual`, `bi_query`, `dataset`) · `POST /datasets/inline` |
| Reconcile | `POST /recon/suggest` · `POST /recon/run` · `GET /recon/{id}?status=&search=` · `GET /recon/{id}/export` · `POST /recon/{id}/investigate` · `POST /recon/{id}/save-template` |
| Templates | `GET/POST/PUT/DELETE /recon-templates` · `POST /recon-templates/{id}/run` (optionally with new file ids) |
| Analysis | `POST /analysis/rate-volume` · `GET /connections/{id}/lineage?node_id=` · `GET /connections/{id}/impact?node_id=` |
| Interactive lineage | `GET /connections/{id}/lineage-graph?node_id=&up=2&down=2` (neighbourhood with per-node neighbour counts) · `GET /connections/{id}/lineage-graph/expand?node_id=&direction=up\|down&level=` · `DELETE /model-files/{id}` |
| Investigations | `POST/GET/PATCH/DELETE /investigations` · notes, lineage, impact, evidence · `GET /exports/investigations/{id}.xlsx` |

### Limits of reading a file

- **Measure values** can't be computed without the Power BI engine. The file
  gives the definitions and the stored table data, not what a card shows. To
  reconcile measure values, connect the published report live (Power BI
  connection) or reconcile the underlying tables.
- **Thin reports** (live-connected to a published dataset) contain no model.
  Upload the dataset's `.pbix`, or connect live.
- **DAX dependencies are parsed from the formula text,** so lineage edges are
  marked INFERRED. `INFO.CALCDEPENDENCY` (live connection) gives VERIFIED ones.
- **Server and database names set by Power Query parameters** show as
  `‹parameter›`. The real values live in the Power BI service settings.

## Not built yet / next steps

1. **Scheduling and alerts.** Run templates on a schedule (e.g. after each
   dataset refresh) and notify on new differences.
2. **Authentication and roles.** The API currently has no login. Put it
   behind your SSO or reverse proxy before sharing it on a network.
3. **More connectors.** Qlik Sense (Engine API), Looker and Power BI
   Report Server. Until then, the exported-files connection covers them.
4. **Dev vs Prod model comparison** (spec §19–20): diff two PBIP/TMDL
   versions of the same model.
5. **AI-assisted explanations** (§43–44) layered on top of the
   deterministic evidence, never replacing it.
6. **Postgres** in place of SQLite for multi-user deployments. Only
   `store/database.py` needs to change.
