# Deploying HomerunIQ

Goal: a real, public URL anyone can visit — not just localhost.

**Architecture:** Supabase (database) + Render (backend API) + Render or
Vercel (frontend).

> **Prerequisite:** everything below deploys *from a GitHub repository*.
> Neither Render nor Vercel accepts direct file uploads. See Step 0.

---

## Step 0 — Push to GitHub

Easiest path with no terminal:

1. Download [GitHub Desktop](https://desktop.github.com/) and sign in.
2. **File → Add Local Repository** → select this `homerun-iq` folder.
3. It'll say it isn't a repo yet → click **"create a repository"**.
4. Click **Publish repository**.

Or from a terminal inside this folder:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/homerun-iq.git
git push -u origin main
```

The included `.gitignore` keeps `backend/.env` and `frontend/.env.local` out
of the repo, so your secrets stay local. You'll set those values in each
host's dashboard instead.

---

## Step 1 — Database (Supabase)

1. Create a project at [supabase.com](https://supabase.com).
2. **SQL Editor** → paste all of `database/schema.sql` → **Run**.
   (Safe to re-run; it's idempotent.)
3. **Settings → API** → copy:
   - **Project URL** → this becomes `SUPABASE_URL`
   - **`service_role` secret** → this becomes `SUPABASE_KEY`

> Use the `service_role` key, **not** `anon`. The backend needs to write, and
> anon is blocked by row-level security. Never expose `service_role` in
> frontend code — it belongs only in backend environment variables.

---

## Step 2 — Backend (Render)

1. [render.com](https://render.com) → **New +** → **Web Service** → connect your repo.
2. Configure:
   - **Root Directory:** `backend`
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
3. Add environment variables:

   | Key | Value |
   |---|---|
   | `APP_ENV` | `production` |
   | `FRONTEND_URL` | your frontend URL (fill in after Step 3) |
   | `SUPABASE_URL` | from Step 1 |
   | `SUPABASE_KEY` | service_role key from Step 1 |
   | `STRIPE_SECRET_KEY` | optional |
   | `STRIPE_WEBHOOK_SECRET` | optional |
   | `STRIPE_PRICE_PRO` / `_ELITE` / `_EDGE` | optional |

4. Deploy. Note the URL, e.g. `https://homerun-iq-backend.onrender.com`.
5. Verify: visit `https://YOUR-BACKEND-URL/` — you should see
   `"database_backend": "supabase"` and `"statcast_enabled": true`.

**Build note:** installing `pandas` + `pybaseball` makes the first build take
several minutes. That's normal.

---

## Step 3 — Frontend

### Option A: Render (works fully, including WebSockets)

1. **New +** → **Web Service** → same repo.
   - **Root Directory:** `frontend`
   - **Runtime:** Node
   - **Build Command:** `npm install && npm run build`
   - **Start Command:** `npm run start`
2. Environment variables:

   | Key | Value |
   |---|---|
   | `NEXT_PUBLIC_API_URL` | `https://YOUR-BACKEND-URL` |
   | `NEXT_PUBLIC_WS_URL` | `wss://YOUR-BACKEND-URL/ws/live` |

3. Deploy.

### Option B: Vercel

1. [vercel.com](https://vercel.com) → **Add New → Project** → import the repo.
2. **Root Directory:** `frontend` (Next.js auto-detected).
3. Same two environment variables as above.
4. Deploy.

> `NEXT_PUBLIC_*` values are baked in at build time — after changing them you
> must **redeploy**, not just restart.
>
> Use `wss://` (not `ws://`) for the WebSocket on any HTTPS site. Browsers
> block insecure WebSockets from secure pages.

---

## Step 4 — Connect the two

1. Back in the **backend** service, set `FRONTEND_URL` to your live frontend URL.
2. Redeploy the backend.

CORS already allows any `*.onrender.com` and `*.vercel.app` origin via regex,
so preview deployments work without extra configuration.

---

## Step 5 — Stripe (optional)

1. Stripe Dashboard (test mode) → create 3 recurring prices: $19 / $49 / $99.
2. Put the price IDs in the backend env vars.
3. **Developers → Webhooks → Add endpoint:**
   - URL: `https://YOUR-BACKEND-URL/api/stripe/webhook`
   - Events: `checkout.session.completed`,
     `customer.subscription.updated`, `customer.subscription.deleted`
4. Copy the signing secret → `STRIPE_WEBHOOK_SECRET` → redeploy.

---

## Verifying the deploy

```bash
curl https://YOUR-BACKEND-URL/
curl "https://YOUR-BACKEND-URL/api/predictions/today?limit=3"
```

Checklist:

- [ ] Backend root shows `"database_backend": "supabase"`
- [ ] Backend root shows `"statcast_enabled": true`
- [ ] Predictions return `"data_source": "live"`
- [ ] Frontend board loads with real player names
- [ ] `/live` shows a green **connected** dot
- [ ] `/games` lists today's real schedule

---

## Troubleshooting

**Frontend loads but shows a connection error**
`NEXT_PUBLIC_API_URL` is wrong or the frontend wasn't redeployed after
changing it. It's baked in at build time.

**`/live` never connects**
You're likely using `ws://` on an HTTPS page. Switch to `wss://` and redeploy.

**Predictions say `"data_source": "mock"` in production but `"live"` locally**
MLB's API blocks some datacenter IP ranges. The fallback keeps the app
functional. Options: try a different region, or run the backend somewhere with
a residential-like IP.

**First request takes ~30 seconds**
Render free tier sleeps after inactivity. Upgrade to a paid instance for
always-on, or accept the cold start.

**Build fails on `pandas` or `pybaseball`**
Confirm the runtime is Python 3 and root directory is `backend`. Render's free
tier can occasionally time out on the first heavy build — retrying usually works.

---

## Costs

| Service | Free tier | Paid |
|---|---|---|
| Supabase | 500MB DB, plenty here | ~$25/mo Pro |
| Render backend | Sleeps after inactivity | ~$7/mo always-on |
| Render/Vercel frontend | Generous free tier | ~$7–20/mo |

You can run the whole thing on free tiers; the only real tradeoff is cold starts.
