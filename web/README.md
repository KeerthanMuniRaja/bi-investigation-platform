# BI Trust Platform — web frontend

Next.js (App Router) + React + TypeScript, animated with Motion. All data
comes from the Python API in `../app` — see the main README.

```bash
npm install
npm run dev        # API on :8000 + web app on :3000
npm run dev:web    # only the web app (API already running)
npm run build && npm start
npm run typecheck && npm run lint
```

`NEXT_PUBLIC_API_URL` (in `.env.local`) points at the API; default
`http://127.0.0.1:8000`. To build while `next dev` is running, use a separate
output folder: `NEXT_DIST_DIR=.next-build npm run build`.

URLs are readable (`/dashboards/sales-dashboard-4/pages/summary/measures/total-revenue`); the scheme and its parser
are in `lib/routes.ts`, and the `app/` folder tree mirrors it.

Layout: `app/` routes, `components/` UI (shell, workspace, model, lineage,
recon), `lib/` API client, formatting, motion presets and the lineage
engine (`lib/lineage/explorer.js`, an imperative SVG engine wrapped by
`components/lineage/LineageCanvas.tsx`).
