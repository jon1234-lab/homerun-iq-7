# Lamplighter

NHL player-prop picks site (anytime goals, 1+ points, 3+ shots on goal) with a Whop paywall.
The picks come from the GoalIQ props model (`nhl/props/` in this repo), which publishes to Supabase every morning.
This folder is only the website: it reads those picks and gates tonight's board behind Whop.

## Run on Render
- Build command: `cd lamplighter && pip install -r requirements.txt`
- Start command: `cd lamplighter && uvicorn main:app --host 0.0.0.0 --port $PORT`

## Environment variables
| Key | What it is |
|---|---|
| GOALIQ_DB_SECRET | Server secret for reading today's (paywalled) picks from Supabase |
| SESSION_SECRET | Any long random string; signs login cookies |
| PREVIEW_KEY | Owner preview: visit /auth/preview?key=THIS to see the full board |
| PUBLIC_URL | The site's address, e.g. https://lamplighter.onrender.com (no trailing slash) |
| WHOP_CLIENT_ID | Whop developer app ID (app_...) |
| WHOP_CLIENT_SECRET | Whop developer app client secret |
| WHOP_API_KEY | Whop API key, used server-side to check memberships |
| WHOP_PRODUCT_ID | The Lamplighter product ID (prod_...); every plan on it unlocks the board |
| WHOP_CHECKOUT_URL | Checkout link for the monthly plan |
| WHOP_DAYPASS_URL | Checkout link for the day pass |
| PRICE_MONTHLY / PRICE_DAYPASS | Labels shown on the site, e.g. $14.99 and $4.99 |
| X_HANDLE | Optional, e.g. @LamplighterHQ, shown in the footer |

Whop OAuth redirect URI to register: `PUBLIC_URL + /auth/callback`.

## Editing the page
Edit `index.html`, then run `python3 build_page.py` to regenerate `page.py` (the server serves page.py).
