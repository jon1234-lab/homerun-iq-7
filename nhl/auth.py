"""Sign in with Whop + membership check.

Env:
  WHOP_CLIENT_ID     app_xxx  (Whop developer dashboard -> your app -> OAuth)
  WHOP_API_KEY       company API key, used server-side to check access (secret)
  WHOP_PRODUCT_ID    prod_xxx of the GoalIQ Pro product
  WHOP_CHECKOUT_URL  the product's checkout link shown on "Subscribe"
  PUBLIC_URL         e.g. https://goal-iq-gaqn.onrender.com  (redirect URI = PUBLIC_URL + /auth/callback)
  SESSION_SECRET     random string for signing cookies
  PREVIEW_KEY        optional: visiting /auth/preview?key=... grants Pro on that browser (owner testing)

Flow: /auth/login -> Whop authorize (PKCE S256) -> /auth/callback -> token -> userinfo (sub = user id)
-> signed session cookie. Every gated request re-checks access (cached 5 min) with
GET https://api.whop.com/api/v1/users/{user_id}/access/{product_id} -> has_access.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from urllib.parse import urlencode

import requests

WHOP_AUTH = "https://api.whop.com/oauth/authorize"
WHOP_TOKEN = "https://api.whop.com/oauth/token"
WHOP_USERINFO = "https://api.whop.com/oauth/userinfo"
WHOP_ACCESS = "https://api.whop.com/api/v1/users/{uid}/access/{rid}"

CLIENT_ID = os.environ.get("WHOP_CLIENT_ID", "")
API_KEY = os.environ.get("WHOP_API_KEY", "")
PRODUCT_ID = os.environ.get("WHOP_PRODUCT_ID", "")
CHECKOUT_URL = os.environ.get("WHOP_CHECKOUT_URL", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
SECRET = (os.environ.get("SESSION_SECRET") or secrets.token_urlsafe(32)).encode()
PREVIEW_KEY = os.environ.get("PREVIEW_KEY", "")

SESSION_COOKIE = "giq_session"
FLOW_COOKIE = "giq_oauth"
SESSION_DAYS = 30
ACCESS_TTL = 300

_access_cache, _lock = {}, threading.Lock()


def configured():
    return bool(CLIENT_ID and API_KEY and PRODUCT_ID and PUBLIC_URL)


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def sign(obj, ttl):
    body = _b64(json.dumps(obj | {"exp": int(time.time()) + ttl}, separators=(",", ":")).encode())
    mac = _b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())
    return f"{body}.{mac}"


def unsign(token):
    try:
        body, mac = token.split(".")
        good = _b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(mac, good):
            return None
        obj = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return obj if obj.get("exp", 0) > time.time() else None
    except Exception:
        return None


def start_login(next_path="/"):
    """Returns (authorize_url, flow_cookie_value)."""
    verifier = _b64(secrets.token_bytes(32))
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    state, nonce = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
    q = {"response_type": "code", "client_id": CLIENT_ID, "redirect_uri": PUBLIC_URL + "/auth/callback",
         "scope": "openid profile email", "state": state, "nonce": nonce,
         "code_challenge": challenge, "code_challenge_method": "S256"}
    flow = sign({"v": verifier, "s": state, "n": next_path if next_path.startswith("/") else "/"}, 900)
    return f"{WHOP_AUTH}?{urlencode(q)}", flow


def finish_login(code, state, flow_cookie):
    """Returns (session_cookie_value, next_path) or raises ValueError with a user-facing reason."""
    flow = unsign(flow_cookie or "")
    if not flow or not hmac.compare_digest(flow["s"], state or ""):
        raise ValueError("Your sign-in link expired. Try signing in again.")
    r = requests.post(WHOP_TOKEN, json={"grant_type": "authorization_code", "code": code,
                                        "redirect_uri": PUBLIC_URL + "/auth/callback",
                                        "client_id": CLIENT_ID, "code_verifier": flow["v"]}, timeout=20)
    if r.status_code != 200:
        raise ValueError("Whop didn't accept the sign-in. Try again.")
    tok = r.json()["access_token"]
    u = requests.get(WHOP_USERINFO, headers={"Authorization": f"Bearer {tok}"}, timeout=20)
    if u.status_code != 200:
        raise ValueError("Couldn't read your Whop profile. Try again.")
    info = u.json()
    user = {"uid": info["sub"], "name": info.get("preferred_username") or info.get("name") or "",
            "email": info.get("email") or "", "pic": info.get("picture") or ""}
    return sign(user, SESSION_DAYS * 86400), flow["n"]


def has_access(uid):
    if not (API_KEY and PRODUCT_ID and uid):
        return False
    now = time.time()
    with _lock:
        hit = _access_cache.get(uid)
        if hit and now - hit[0] < ACCESS_TTL:
            return hit[1]
    try:
        r = requests.get(WHOP_ACCESS.format(uid=uid, rid=PRODUCT_ID),
                         headers={"Authorization": f"Bearer {API_KEY}"}, timeout=10)
        ok = r.status_code == 200 and bool(r.json().get("has_access"))
    except Exception:
        with _lock:   # Whop unreachable: keep the last known answer rather than locking out a paying user
            hit = _access_cache.get(uid)
        return hit[1] if hit else False
    with _lock:
        _access_cache[uid] = (now, ok)
    return ok


def viewer(cookies):
    """{'logged_in', 'pro', 'name', 'pic', 'preview'} for the current request."""
    s = unsign(cookies.get(SESSION_COOKIE, "") or "")
    if not s:
        return {"logged_in": False, "pro": False, "name": "", "pic": "", "preview": False}
    if s.get("preview"):
        return {"logged_in": True, "pro": True, "name": "Owner preview", "pic": "", "preview": True}
    return {"logged_in": True, "pro": has_access(s["uid"]), "name": s.get("name", ""), "pic": s.get("pic", ""),
            "preview": False}


def preview_cookie(key):
    if PREVIEW_KEY and key and hmac.compare_digest(key, PREVIEW_KEY):
        return sign({"preview": True}, 7 * 86400)
    return None
