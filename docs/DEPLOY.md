# Going live: Google sign-in, cloud database, hosting

This takes the app from "runs on my laptop" to a hosted site where people
sign in with Google and each person sees only their own work.

```
Browser ──► Vercel (web/, Next.js) ──► API host (app/, Python) ──► Supabase
                                                                  ├─ Postgres  (records)
                                                                  ├─ Storage   (uploaded files)
                                                                  └─ Auth      (Google sign-in)
```

Plan about an hour. You'll collect a few values as you go; the last section
says where each one goes.

---

## 1. Supabase project

1. Go to https://supabase.com, sign in, and click **New project**.
   Pick a name, a strong **database password** (save it), and the region
   closest to your users.
2. When it's ready, open **Project Settings → API** (or **API Keys**) and copy:
   - **Project URL**: `https://<ref>.supabase.co`
   - **anon / publishable key**: safe to put in the web app
   - **service_role / secret key**: server only, never put it in the web app or in git
3. Open **Connect** (top bar) → **Session pooler** and copy the URI. It looks like
   `postgresql://postgres.<ref>:[YOUR-PASSWORD]@aws-0-<region>.pooler.supabase.com:5432/postgres`.
   Put your database password in place of `[YOUR-PASSWORD]`.

You don't need to create tables or a storage bucket. On first start the API
creates its tables in a private `bip` schema (with row-level security on and
no public access) and a private `bip-files` bucket.

**File size limit:** Supabase's free plan accepts files up to **50 MB**.
Bigger .pbix files need a paid plan, plus raising the limit under
**Storage → Settings → Upload file size limit**.

## 2. Google sign-in

1. Go to https://console.cloud.google.com and create a project (or pick one).
2. **APIs & Services → OAuth consent screen**:
   - User type **External** (or **Internal** if everyone is in one Google Workspace).
   - App name, support email and developer email.
   - Under **Authorized domains** add `supabase.co`.
   - Scopes: the defaults (`email`, `profile`, `openid`) are enough.
   - While the app is in **Testing**, only the test users you list can sign
     in. Click **Publish app** when you're ready for everyone.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**.
   - **Authorized JavaScript origins**: `http://localhost:3000` and your Vercel URL,
     e.g. `https://bi-investigation-platform.vercel.app`.
   - **Authorized redirect URIs**: `https://<ref>.supabase.co/auth/v1/callback`
     (Supabase shows the exact value on its Google provider page).
   - Copy the **Client ID** and **Client secret**.
4. Back in Supabase: **Authentication → Sign In / Providers → Google**.
   Turn it on and paste the Client ID and Client secret.
5. Supabase **Authentication → URL Configuration**:
   - **Site URL**: your Vercel URL.
   - **Redirect URLs**: add
     - `http://localhost:3000/auth/callback`
     - `https://<your-app>.vercel.app/auth/callback`
     - optional, for Vercel preview builds: `https://*-<your-team>.vercel.app/auth/callback`

## 3. Host the API

Any host that runs a Docker image works: Render, Railway, Fly.io, Azure App
Service, Google Cloud Run. The repo's `Dockerfile` builds the API. Steps for
**Render**:

1. https://render.com → **New → Web Service** → connect the GitHub repo.
2. Runtime **Docker** (it finds the `Dockerfile`). Pick an instance with at
   least **2 GB RAM**, since large .pbix files are read in memory.
3. **Health check path**: `/health`.
4. Add the environment variables from the table below (API column) and deploy.
5. Copy the service URL, e.g. `https://bi-api.onrender.com`. Opening
   `https://bi-api.onrender.com/health` should show `{"status":"ok"}`.

Free instances that go to sleep make the first request slow. The data is
safe either way: records live in Postgres and files in Storage, and the API
re-downloads a file the first time it needs it.

## 4. Host the web app on Vercel

1. https://vercel.com → **Add New → Project** → import the GitHub repo.
2. **Root Directory**: `web`. Framework: Next.js (detected).
3. Add the environment variables from the table below (Vercel column) and deploy.
4. Put the final Vercel URL into:
   - the API's `BIP_CORS_ORIGINS` and `BIP_WEB_URL` (then redeploy the API);
   - Supabase **Site URL** and **Redirect URLs** (step 2.5);
   - Google **Authorized JavaScript origins** (step 2.3).

## 5. Environment variables

| Variable | API host | Vercel | Value |
|---|---|---|---|
| `SUPABASE_URL` | ✅ | | Project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | ✅ | ❌ never | service_role / secret key |
| `DATABASE_URL` | ✅ | | Session pooler URI with your password |
| `BIP_CORS_ORIGINS` | ✅ | | Vercel URL, e.g. `https://bi-investigation-platform.vercel.app` |
| `BIP_WEB_URL` | ✅ | | same Vercel URL |
| `BIP_ALLOWED_DOMAINS` | optional | | e.g. `jmangroup.com`: only these domains can sign in |
| `BIP_ALLOWED_EMAILS` | optional | | e.g. `keerthanmuniraja@gmail.com`: extra individual addresses |
| `SUPABASE_JWT_SECRET` | only for old projects | | legacy JWT secret; leave unset on new projects |
| `NEXT_PUBLIC_API_URL` | | ✅ | API URL, e.g. `https://bi-api.onrender.com` |
| `NEXT_PUBLIC_SUPABASE_URL` | | ✅ | Project URL |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | | ✅ | publishable key (`sb_publishable_…`) |

With neither allow-list set, **any Google account** can sign in and get its
own empty workspace. To keep it to your team, set `BIP_ALLOWED_DOMAINS`.

## 6. Bring over your existing local work (optional)

Your current dashboards, results and investigations are in the local
`data/` folder. To move them into your account in the cloud:

```bash
# in the project folder, with SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and DATABASE_URL in .env
python scripts/migrate_to_cloud.py --owner you@gmail.com            # shows what would be copied
python scripts/migrate_to_cloud.py --owner you@gmail.com --apply    # copies records and uploads the files
```

Use the email of the Google account you sign in with. Running it twice is safe.

## 7. Local development

- **Without cloud** (no `.env`): `cd web && npm run dev` gives you no sign-in,
  SQLite and local files, the same as before.
- **Against your Supabase project:** copy `.env.example` to `.env` (API) and
  `web/.env.example` to `web/.env.local` (web app), fill them in, then run
  `cd web && npm run dev`. Sign-in redirects to `http://localhost:3000/auth/callback`,
  which you added in step 2.5.

## How privacy works

- The web app signs in with Supabase and sends the session token with every
  API call. The API checks the token's signature: against Supabase's
  published keys, or against `SUPABASE_JWT_SECRET` on older projects.
- Every record is stored with its owner's email, and every read and write
  is limited to the signed-in person. Another person's record ids return
  "not found".
- Uploaded files are kept in a private bucket under a folder per person. Only
  the API, using the service key, can read them.
- The database tables are in a private schema with row-level security on and
  no public policies, so Supabase's public REST API can't reach them.
