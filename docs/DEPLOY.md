# Going live on Neon + Cloudflare R2 + Vercel

```
                     ┌──────────── Vercel ────────────┐
Browser ──► web app (project 2, folder web/) ──► API (project 1, repo root, FastAPI) ──► Neon Postgres
   │                                                                                     (accounts + records)
   └──── big file uploads go straight to ─────────────────────────────────────────► Cloudflare R2 bucket
```

- **Neon** (Postgres) holds the accounts (username + hashed password) and
  every record: each person's dashboards, files, results and investigations.
- **Cloudflare R2** holds the uploaded files. The free tier gives 10 GB of
  storage with no per-file size limit that matters here, and downloads are
  free.
- **Power BI files are read once and not kept.** The app saves a snapshot of
  what it read in Postgres, typically under 1 MB. The snapshot covers the
  model, measures with DAX, relationships, report pages and visuals, lineage,
  and the first 50 rows of each table for previews. The uploaded .pbix is
  then deleted from R2.
- **Vercel** runs both parts from the same GitHub repo, as two projects: the
  website, and the Python API as a serverless function.
- People **sign up with a username and password**. After signing in, each
  person sees only the data they loaded.

Plan about 30 minutes.

---

## 1. Neon (database)

1. https://neon.tech → **New project**. Pick a region close to your Vercel
   functions (Vercel's default is Washington DC, `iad1`, so pick **US East**).
2. **Dashboard → Connect.** Turn **Connection pooling** on and copy the
   connection string. Its host contains `-pooler`. This is `DATABASE_URL`.
   Use the pooled one on Vercel, because serverless functions open many short
   connections.
3. **Create the schema.** Open **SQL Editor**, paste the whole of
   [`supabase/migrations/20261008000000_bip_schema.sql`](../supabase/migrations/20261008000000_bip_schema.sql)
   (plain Postgres; the folder name is historical) and click **Run**. It
   creates:

   | Table | Holds |
   |---|---|
   | `bip.users` | accounts: username, display name, salted scrypt password hash (never the password itself) |
   | `bip.documents` | every record as JSON, with the username that owns it |
   | `bip.counters` | running numbers for readable ids (CONN0001, FILE0001, …) |

   If you skip this step, set `BIP_DB_AUTO_SCHEMA=true` and the API creates
   the schema on its first request.

## 2. Cloudflare R2 (file storage)

1. https://dash.cloudflare.com → **R2 Object Storage**. The first time, R2
   asks you to add a payment method; the free tier still applies.
2. **Create bucket** → name `bip-files`, location **Automatic** → **Create**.
3. **Allow the website to upload to it.** Open the bucket → **Settings →
   CORS Policy → Add CORS policy**, and paste the following, with your
   website's address:

   ```json
   [
     {
       "AllowedOrigins": ["https://your-web-app.vercel.app", "http://localhost:3000"],
       "AllowedMethods": ["PUT", "GET", "HEAD"],
       "AllowedHeaders": ["*"],
       "ExposeHeaders": ["ETag"],
       "MaxAgeSeconds": 3600
     }
   ]
   ```

   If the API token in the next step has *Admin Read & Write* permission, the
   API sets this policy by itself from `BIP_WEB_URL`.
4. **R2 overview → Manage API tokens → Create API token**:
   - Permissions: **Object Read & Write**, or **Admin Read & Write** if you
     want the API to set CORS itself.
   - Specify bucket: `bip-files`.
   - **Create.** Copy the **Access Key ID** and the **Secret Access Key**.
     The secret is shown only once.
5. Your **Account ID** is shown on the R2 overview page (right-hand side).

## 3. Vercel project 1: the API

1. https://vercel.com → **Add New → Project** → import
   `KeerthanMuniRaja/bi-investigation-platform`.
2. Click **Import single project** on the **app (FastAPI)** row. Keep
   **Root Directory** `./` and **Application Preset** *FastAPI*. Vercel runs
   `app/main.py` as one function, installs `requirements.txt`, and reads
   `vercel.json` (a 300 s time limit) and `.python-version` (Python 3.12).
3. **Environment Variables:**

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | Neon pooled connection string (step 1.2) |
   | `R2_ACCOUNT_ID` | Cloudflare account ID (step 2.5) |
   | `R2_ACCESS_KEY_ID` | from step 2.4 |
   | `R2_SECRET_ACCESS_KEY` | from step 2.4 |
   | `R2_BUCKET` | `bip-files` |
   | `BIP_AUTH` | `password` |
   | `BIP_SECRET_KEY` | a long random string: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
   | `BIP_ALLOW_SIGNUP` | `true` (anyone can create an account) |
   | `BIP_DB_AUTO_SCHEMA` | `false` (you created the schema in step 1.3) |
   | `BIP_DB_POOL_SIZE` | `3` |
   | `BIP_WEB_URL` | the website's URL from section 4, e.g. `https://your-web-app.vercel.app` |

   No `SUPABASE_*` variables: if they are set, remove them. With the `R2_*`
   variables present, R2 is used anyway.
4. **Deploy.** Then open `https://<api-project>.vercel.app/health/database`.
   It should show:
   - `"status":"ok"` for the database;
   - `"storage":"r2"` and `"storage_status":"ok"` for the bucket.
5. **Settings → Security (Firewall):** keep **Bot Protection / Attack
   Challenge Mode** *off* for the API project. The web app calls the API from
   the browser, and a challenge page breaks those calls.

## 4. Vercel project 2: the website

1. **Add New → Project** → import the same repo again, then click **Import
   single project** on the **web (Next.js)** row.
2. **Root Directory**: `web`. Framework: Next.js (detected).
3. **Environment Variables:** set `NEXT_PUBLIC_API_URL` to the API project's
   URL, e.g. `https://<api-project>.vercel.app`.
4. **Deploy.** Then put this site's URL in the API project's `BIP_WEB_URL`
   and in the R2 CORS policy (step 2.3), and redeploy the API
   (**Deployments → … → Redeploy**).

Open the site: you'll see the sign-in page. Choose **Create account**.

## 5. What Vercel limits

The API runs as a serverless function, so a few Vercel limits apply:

| Limit | Effect |
|---|---|
| ~4.5 MB per request/response | Uploads are not affected: they go straight to R2. Very large Excel exports (over 4.5 MB) won't download. |
| 300 s per request (Hobby), 800 s (Pro) | A Power BI file that takes longer than that to read won't open. Files of tens of MB open in seconds. |
| 2 GB memory (Hobby), 4 GB (Pro) | Very large models may run out of memory. After two failed reads, the dashboard says so instead of retrying. |
| ~500 MB scratch disk | The working copy of a file must fit; the copy of record stays in R2. |

**What needs the original .pbix:** only reconciling against the numbers a
Power BI *visual* would compute from every row of a table. Lineage, measures,
relationships, the health check, report map, table previews and all Excel/CSV
reconciliations work from the snapshot. To keep the originals, set
`BIP_KEEP_MODEL_FILES=true` on the API (they then use R2 space).

For .pbix files in the hundreds of MB, run the API on a normal server
instead. The repo's `Dockerfile` works on Render, Railway, Fly.io or Azure
with the same environment variables; only `NEXT_PUBLIC_API_URL` on the website
changes.

## 6. Accounts

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

## 7. Bring over your existing local work (optional)

Create your account on the live site first. Then, on your computer, put
`DATABASE_URL` and the `R2_*` variables in `.env` and run:

```bash
python scripts/migrate_to_cloud.py --owner <your-username>           # shows what would move
python scripts/migrate_to_cloud.py --owner <your-username> --apply   # copies records and uploads the files
```

## 8. Local development

Nothing changes: with no `.env`, `cd web && npm run dev` runs everything
locally with no sign-in (SQLite and the local `data/` folder). To try sign-in
locally, put `BIP_AUTH=password` in a `.env` file in the project folder and
restart.

## Other storage

Any S3-compatible store works through the same variables: set `R2_ENDPOINT`
to its endpoint (for example AWS S3, Backblaze B2 or MinIO) instead of
`R2_ACCOUNT_ID`. Supabase Storage (`SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`)
is still supported, but its free plan caps files at 50 MB.

## How privacy works

- Every API call carries the sign-in token (HS256, signed with
  `BIP_SECRET_KEY`). The API checks it, and also checks that the account
  still exists, isn't disabled and hasn't changed its password since.
- Every record is stored with its owner's username, and every read and write
  is limited to the signed-in person. Another person's record ids return
  "not found".
- Uploads go to a one-time, one-hour upload link inside the person's own
  folder, and the API only imports files from that person's folder. The
  bucket is private: nothing in it is publicly readable.
- After 8 wrong passwords for a username, or from one address, the next
  attempt waits 15 minutes. Unknown usernames and wrong passwords get the
  same answer, so nobody can probe which accounts exist.
