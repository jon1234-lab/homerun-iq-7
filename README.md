# HomerunIQ — MLB Home Run Intelligence

Real-time MLB home run probability analytics. Computes a 0–100 **HRI** score
and HR probability for every batter/pitcher/park matchup on today's slate,
streams live updates over WebSocket, and gates premium features behind Stripe.

**Everything runs on real data** — real games, real active rosters, real
Statcast batter metrics, real pitcher stats, real weather. No API keys needed
for any of it.

---

## 🚀 Run it without writing any code

**All you need is [Docker Desktop](https://www.docker.com/products/docker-desktop/).**

1. Install Docker Desktop and open it — wait until it says **"Engine running"**.
2. In this folder:
   - **Mac/Linux:** double-click `start.sh` (or right-click → Open)
   - **Windows:** double-click `start.bat`
3. First run builds for a few minutes, then your browser opens to
   **http://localhost:3000**.
4. To stop: double-click `stop.sh` / `stop.bat`.

> The first page load pulls ~30 team rosters plus the season Statcast
> leaderboard, so give it 10–30 seconds. Everything is cached after that.

---

## 📊 What's real

| Data | Source | Key needed |
|---|---|---|
| Today's games, venues, status | MLB Stats API | No |
| Probable starting pitchers | MLB Stats API | No |
| Active rosters (batters) | MLB Stats API | No |
| Pitcher HR/9 + flyball rate | MLB Stats API season stats | No |
| Barrel %, hard-hit %, xSLG, exit velo | Baseball Savant via `pybaseball` | No |
| Wind + temperature per park | Open-Meteo | No |
| Park factors (all 30 parks) | Built-in table | No |

Every response carries provenance so you always know what you're looking at:

- `data_source` — `live` or `mock` (games)
- `batter_data_source` — `live` / `mixed` / `mock`
- `weather_source` — `live` or `simulated`
- per-player `batter_stats_source` on each prediction

The UI shows a matching badge, and tags any individual player running on a
league-average estimate with a small **est** marker. If any upstream source is
unreachable, that piece degrades to a clearly-labeled fallback — the app never
crashes and never silently fakes data.

---

## 🧠 The HRI model

`backend/app/services/hri_engine.py`:

```
batter_power      = 0.35·barrel% + 0.25·hardhit% + 0.25·xSLG + 0.15·exit_velo
pitcher_weakness  = 0.60·HR9 + 0.40·flyball%
environment       = 0.35·wind + 0.25·temp + 0.40·park_factor

HRI (0–100)       = 100 · (0.50·batter_power + 0.30·pitcher_weakness + 0.20·environment)
HR probability    = HRI_raw · 0.45
```

All inputs normalize to [0,1] first, so output is bounded and deterministic.
Wind is *signed*: computed from real wind direction against each park's
center-field bearing, so "blowing out" helps and "blowing in" hurts.

---

## 🔌 API

| Endpoint | Description |
|---|---|
| `GET /` | Service status + which backends are active |
| `GET /health` | Health check |
| `GET /api/predictions/today?limit=50` | Ranked HRI board |
| `GET /api/predictions/today/detailed` | **Elite+** — component breakdown + raw inputs |
| `GET /api/games/today` | Today's real schedule |
| `GET /api/player/{id}` | Player detail (real MLBAM id or demo id) |
| `POST /api/stripe/create-checkout-session` | Start a subscription |
| `POST /api/stripe/webhook` | Stripe events → plan updates |
| `WS /ws/live` | Live feed, broadcasts every 10s |

Interactive docs at **http://localhost:8000/docs**.

Plan gating uses an `X-User-Id` header (stand-in for real auth):

```bash
curl -H "X-User-Id: demo-user-001" \
  "http://localhost:8000/api/predictions/today/detailed?limit=5"
```

---

## 🗄️ Optional: real database (Supabase)

Works fine without this — it falls back to in-memory storage. To persist:

1. Run `database/schema.sql` in the Supabase SQL editor.
2. In `backend/.env`, set `SUPABASE_URL` and `SUPABASE_KEY`
   (**service_role** secret from Settings → API, *not* the anon key).
3. Restart. `GET /` will report `"database_backend": "supabase"`.

---

## 💳 Optional: Stripe test mode

1. Create 3 recurring prices in Stripe test mode: Pro $19, Elite $49, Edge $99.
2. Fill `STRIPE_SECRET_KEY` and the three `STRIPE_PRICE_*` values in `backend/.env`.
3. For local webhooks: `stripe listen --forward-to localhost:8000/api/stripe/webhook`,
   then set `STRIPE_WEBHOOK_SECRET`.
4. Go to `/upgrade`, pay with test card `4242 4242 4242 4242`.

---

## 🛠️ Manual setup (developers)

```bash
# Backend
cd backend
python3.11 -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload

# Frontend (new terminal)
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

---

## 🚢 Deployment

See **DEPLOYMENT.md** for full step-by-step instructions (Render + Supabase).

---

## ⚠️ Known caveats

- **Cloud IPs:** MLB's Stats API sometimes blocks datacenter IP ranges (AWS,
  GCP, Render) while allowing home connections. If a deployed instance shows
  `"mock"` where local shows `"live"`, that's why — the fallback keeps it working.
- **Free-tier cold starts:** Render free services sleep after inactivity;
  first request afterward takes ~30s to wake.
- **First request of the day** is the slowest (rosters + Statcast leaderboard),
  then cached for hours.
- **Not betting advice.** Model estimates for entertainment and analysis.

## 📁 Structure

```
homerun-iq/
├── start.sh / start.bat      One-click launcher (Docker)
├── stop.sh  / stop.bat       One-click shutdown
├── docker-compose.yml
├── backend/                  FastAPI
│   └── app/
│       ├── main.py           Entrypoint, CORS, routers
│       ├── config.py         Env settings
│       ├── database.py       Supabase + in-memory fallback
│       ├── schemas.py        Pydantic models
│       ├── routers/          predictions, games, players, stripe, websocket
│       ├── services/         mlb_data, statcast, weather, hri_engine, cache, plan_access
│       └── data/mock_data.py Fallback slate + park factors
├── frontend/                 Next.js App Router + TS + Tailwind
│   ├── app/                  / · /live · /games · /upgrade
│   ├── components/           Leaderboard, PlayerCard, TrendIndicator, StatusBadge
│   └── lib/                  api, types, status
└── database/schema.sql
```
