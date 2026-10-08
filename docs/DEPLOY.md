# Going live on Supabase + Vercel

```
                     ┌──────────── Vercel ────────────┐
Browser ──► web app (project 2, folder web/) ──► API (project 1, repo root, FastAPI) ──► Supabase Postgres
   │                                                                                         (accounts + records)
   └──── big file uploads go straight to ─────────────────────────────────────────────► Supabase Storage
```

- **Supabase** holds the accounts (username + hashed password), every record
  (each person's dashboards, files, results, investigations) and the
  uploaded Excel/CSV files.
- **Power BI files are read once and not kept.** The app saves a snapshot of
  what it read (model, measures with DAX, relationships, report pages and
  visuals, lineage, plus the first 50 rows of each table for previews) in
  Postgres, typically under 1 MB, and deletes the uploaded .pbix. That saves
  storage, and dashboards open fast even on a cold start.
- **Vercel** runs both parts from the same GitHub repo, as two projects:
  the website, and the Python API as a serverless function.
- People **sign up with a username and password**. After signing in, each
  person sees only the data they loaded.

Plan about 30–45 minutes.

---

## 1. Supabase

1. https://supabase.com → **New project**. Pick a name, a strong **database
   password** (save it), and a region close to your users.
2. **Create the database schema.** Open **SQL Editor → New query**, paste the
   whole of [`supabase/migrations/20261008000000_bip_schema.sql`](../supabase/migrations/20261008000000_bip_schema.sql)
   and click **Run**. It creates:

   | Table | Holds |
   |---|---|
   | `bip.users` | accounts: username, display name, salted scrypt password hash (never the password itself) |
   | `bip.documents` | every record as JSON, with the username that owns it |
   | `bip.counters` | running numbers for readable ids (CONN0001, FILE0001, …) |

   The tables are in a private `bip` schema with row-level security on, so
   Supabase's public API can't read them; only the app's API can. You'll see
   them under **Table Editor → schema `bip`**. (With the Supabase CLI you can
   run `supabase db push` instead.)
3. Collect these values:
   - **Connect → Transaction pooler** URI (port **6543**). Put your database
     password in it. This is `DATABASE_URL`. Use the *transaction* pooler on
     Vercel: serverless functions open many short connections.
   - **Project Settings → API Keys**: the **Project URL** (`SUPABASE_URL`) and
     the **secret / service_role key** (`SUPABASE_SERVICE_ROLE_KEY`). The key
     goes in the API project only, never in the web project.
4. **Storage:** nothing to create; the API makes a private `bip-files` bucket
   on first use. The free plan accepts files up to **50 MB**. Bigger .pbix
   files need a paid plan, plus raising the limit under
   **Storage → Settings → Upload file size limit**.

## 2. Vercel project 1: the API

1. https://vercel.com → **Add New → Project** → import
   `KeerthanMuniRaja/bi-investigation-platform`.
2. Vercel shows two apps it found in the repo. Click **Import single project**
   on the **app (FastAPI)** row. Keep **Root Directory** `./` and **Application
   Preset** *FastAPI*. Vercel runs `app/main.py` as one function, installs
   `requirements.txt`, and reads `vercel.json` (a 300 s time limit) and
   `.python-version` (Python 3.12).
3. **Environment Variables:**

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | Transaction pooler URI with your password |
   | `SUPABASE_URL` | `https://<ref>.supabase.co` |
   | `SUPABASE_SERVICE_ROLE_KEY` | secret / service_role key |
   | `BIP_AUTH` | `password` |
   | `BIP_SECRET_KEY` | a long random string: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
   | `BIP_ALLOW_SIGNUP` | `true` (anyone can create an account) |
   | `BIP_DB_AUTO_SCHEMA` | `false` (you created the schema in step 1.2) |
   | `BIP_DB_POOL_SIZE` | `3` |
   | `BIP_KEEP_MODEL_FILES` | `false` (Power BI files are deleted after reading; see section 4) |
   | `BIP_CORS_ORIGINS` | the web app's URL from step 3, e.g. `https://bi-investigation-platform.vercel.app` |
   | `BIP_WEB_URL` | the same URL |

4. **Deploy.** Opening `https://<api-project>.vercel.app/health` should show
   `{"status":"ok"}`, and `/api/info` should show `"auth":true`.

## 3. Vercel project 2: the website

1. **Add New → Project** → import the same repo again, then click **Import
   single project** on the **web (Next.js)** row.
2. **Root Directory**: `web`. Framework: Next.js (detected).
3. **Environment Variables:** `NEXT_PUBLIC_API_URL` = the API project's URL,
   e.g. `https://bi-investigation-platform-api.vercel.app`.
4. **Deploy**, then put this site's URL into the API project's
   `BIP_CORS_ORIGINS` and `BIP_WEB_URL`, and redeploy the API
   (**Deployments → … → Redeploy**).

Open the site: you'll see the sign-in page. Choose **Create account**.

## 4. What Vercel limits

The API runs as a serverless function, so a few Vercel limits apply:

| Limit | Effect |
|---|---|
| ~4.5 MB per request/response | Uploads are not affected: they go straight to Supabase Storage. Very large Excel exports (over 4.5 MB) won't download. |
| 300 s per request (Hobby), 800 s (Pro) | A Power BI file that takes longer than that to read won't open. Files of tens of MB open in seconds. |
| 2 GB memory (Hobby), 4 GB (Pro) | Very large models may run out of memory. |
| ~500 MB scratch disk | The working copy of a file must fit; the copy of record stays in Supabase Storage. |
| Supabase free plan: 50 MB per file | Raise it on a paid plan (step 1.4). |

**What needs the original .pbix:** only reconciling against the numbers a
Power BI *visual* would compute from every row of a table. Lineage, measures,
relationships, the health check, report map, table previews and all
Excel/CSV reconciliations work from the snapshot. To keep the originals,
set `BIP_KEEP_MODEL_FILES=true` on the API (they then use Storage space).

For .pbix files in the hundreds of MB or above 1 GB, run the API on a normal
server instead: the repo's `Dockerfile` works on Render, Railway, Fly.io or
Azure with the same environment variables. Only `NEXT_PUBLIC_API_URL` on
the website changes.

## 5. Accounts

- With `BIP_ALLOW_SIGNUP=true` anyone can create an account from the sign-in
  page. To close sign-ups, set it to `false` and redeploy the API; the first
  account can still be created.
- From your computer, with `DATABASE_URL` in a local `.env`:

```bash
python scripts/create_user.py list
python scripts/create_user.py add someone --name "Someone"     # asks for the password
python scripts/create_user.py reset-password someone           # also signs them out everywhere
python scripts/create_user.py disable someone                  # / enable
```

People change their own password from the account menu (top right).

## 6. Bring over your existing local work (optional)

Create your account on the live site first, then from your computer, with
`DATABASE_URL`, `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` in `.env`:

```bash
python scripts/migrate_to_cloud.py --owner <your-username>           # shows what would move
python scripts/migrate_to_cloud.py --owner <your-username> --apply   # copies records and uploads the files
```

## 7. Local development

Nothing changes: with no `.env`, `cd web && npm run dev` runs everything
locally with no sign-in. To try sign-in locally, put `BIP_AUTH=password` in a
`.env` file in the project folder and restart.

## How privacy works

- Every API call carries the sign-in token (HS256, signed with
  `BIP_SECRET_KEY`). The API checks it, and checks that the account still
  exists, isn't disabled and hasn't changed its password since.
- Every record is stored with its owner's username, and every read and write
  is limited to the signed-in person. Another person's record ids return
  "not found".
- Uploads go to a one-time storage URL inside the person's own folder, and the
  API only imports files from that person's folder.
- After 8 wrong passwords for a username, or from one address, the next
  attempt waits 15 minutes. Unknown usernames and wrong passwords get the
  same answer, so nobody can probe which accounts exist.
