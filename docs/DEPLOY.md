# Going live: sign-in, cloud database, hosting

This takes the app from "runs on my laptop" to a hosted site where people
sign in with a **username and password** and each person sees only their
own work.

```
Browser ──► Vercel (web/, Next.js) ──► API host (app/, Python) ──► Supabase
                                                                  ├─ Postgres  (accounts + records)
                                                                  └─ Storage   (uploaded files)
```

Accounts are kept in the app's own database, in a `users` table: username,
display name and a salted **scrypt hash** of the password (the password
itself is never stored). Signing in gives the browser a signed token that
lasts `BIP_SESSION_DAYS` days.

---

## 1. Supabase project (database + file storage)

1. https://supabase.com → **New project**. Pick a name, a strong **database
   password** (save it), and the region closest to your users.
2. **Connect** (top bar) → **Session pooler** → copy the URI:
   `postgresql://postgres.<ref>:[YOUR-PASSWORD]@aws-0-<region>.pooler.supabase.com:5432/postgres`.
   Put your database password in place of `[YOUR-PASSWORD]`. This is `DATABASE_URL`.
3. **Project Settings → API Keys**: copy the **Project URL** (`SUPABASE_URL`)
   and the **secret / service_role key** (`SUPABASE_SERVICE_ROLE_KEY`). They
   are only needed for file storage, and the key belongs on the API server
   only.

You don't need to create tables or a bucket. On first start the API creates
`bip.users`, `bip.documents` and `bip.counters` in a private `bip` schema
(row-level security on, no public access) and a private `bip-files` bucket.

**File size limit:** the free plan accepts files up to **50 MB**. Bigger
.pbix files need a paid plan, plus raising the limit under
**Storage → Settings → Upload file size limit**.

## 2. Host the API

Any host that runs a Docker image works: Render, Railway, Fly.io, Azure App
Service, Google Cloud Run. The repo's `Dockerfile` builds the API. Steps for
**Render**:

1. https://render.com → **New → Web Service** → connect the GitHub repo.
2. Runtime **Docker**. Pick an instance with at least **2 GB RAM**, since
   large .pbix files are read in memory.
3. **Health check path**: `/health`.
4. Add the environment variables from the table below (API column) and deploy.
5. Opening `https://<your-api>/health` should show `{"status":"ok"}`.

## 3. Host the web app on Vercel

1. https://vercel.com → **Add New → Project** → import the GitHub repo.
2. **Root Directory**: `web`.
3. Add `NEXT_PUBLIC_API_URL` = your API's URL, then deploy. The web app needs
   no sign-in settings: it asks the API whether sign-in is on.
4. Put the Vercel URL into the API's `BIP_CORS_ORIGINS` and `BIP_WEB_URL`,
   then redeploy the API.

## 4. Environment variables

| Variable | Where | Value |
|---|---|---|
| `BIP_AUTH` | API | `password`: turns sign-in on |
| `BIP_SECRET_KEY` | API | long random string, the same on every API server: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `BIP_ALLOW_SIGNUP` | API | `true` (anyone can create an account) or `false` (only accounts you add) |
| `BIP_SESSION_DAYS` | API | how long a sign-in lasts (default `7`) |
| `DATABASE_URL` | API | Session pooler URI with your password |
| `SUPABASE_URL` | API | Project URL (file storage) |
| `SUPABASE_SERVICE_ROLE_KEY` | API only, never Vercel | secret / service_role key (file storage) |
| `BIP_CORS_ORIGINS` | API | Vercel URL, e.g. `https://bi-investigation-platform.vercel.app` |
| `BIP_WEB_URL` | API | same Vercel URL |
| `NEXT_PUBLIC_API_URL` | Vercel | API URL, e.g. `https://bi-api.onrender.com` |

If `BIP_SECRET_KEY` is missing, the API generates one and keeps it in
`<BIP_DATA_DIR>/secret.key`. That's fine on your laptop, but on a host whose
disk is wiped on redeploy everyone would be signed out each time, so set it.

## 5. Accounts

- **Open sign-up** (`BIP_ALLOW_SIGNUP=true`): the sign-in page has a
  **Create account** tab.
- **Closed** (`false`): only you add accounts. The very first account can
  always be created on the sign-in page, so you can set things up.
- From the project folder (it uses the database in `.env`):

```bash
python scripts/create_user.py add keerthan --name "Keerthan"   # asks for the password
python scripts/create_user.py list
python scripts/create_user.py reset-password keerthan          # also signs them out everywhere
python scripts/create_user.py disable keerthan                 # / enable
python scripts/create_user.py claim-local keerthan             # give work made before sign-in to this account
```

People change their own password from the account menu (top right). That
signs them out of every other browser.

## 6. Bring over your existing local work

Everything made while sign-in was off belongs to the "local" workspace.

- **Same database, now with sign-in on:** create your account, then run
  `python scripts/create_user.py claim-local <username>`.
- **Moving from the local SQLite file to Supabase:**
  `python scripts/migrate_to_cloud.py --owner <username>` shows what would
  move; add `--apply` to copy the records and upload the files.

## 7. Local development

- No `.env`: no sign-in, SQLite and local files. Run `cd web && npm run dev`.
- With sign-in, locally: put `BIP_AUTH=password` in `.env` and restart. The
  sign-in page appears; create your account, then `claim-local` your
  existing work.

## How privacy works

- Every API call carries the sign-in token. The API checks its signature and
  that the account still exists, isn't switched off, and hasn't changed its
  password since.
- Every record is stored with its owner's username, and every read and write
  is limited to the signed-in person. Another person's record ids return
  "not found".
- Repeated wrong passwords for a username, or from one address, are slowed
  down: after 8 failures, wait 15 minutes. Unknown usernames and wrong
  passwords give the same answer, so nobody can probe which accounts exist.
- Uploaded files sit in a private bucket under a folder per person. Only the
  API, using the service key, can read them.
- The database tables are in a private schema with row-level security on
  and no public policies, so Supabase's public REST API can't reach them.
