"""Reads the props model's output from Supabase.

Today's picks, parlays and boards come through the private goaliq_read() function (needs GOALIQ_DB_SECRET);
anything already graded is public and read with the publishable key.
"""
import os
import threading
import time

import requests

URL = os.environ.get("SUPABASE_URL", "https://lfpfdpfktvjpnjwmegyd.supabase.co").rstrip("/")
PUBLISHABLE = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_DyoU7kki74m2sYKvBqBHUQ_QsXc5BF9")
DB_SECRET = os.environ.get("GOALIQ_DB_SECRET", "")
TTL = 120

_cache, _lock = {}, threading.Lock()


def _h():
    return {"apikey": PUBLISHABLE, "Content-Type": "application/json"}


def _cached(key, fn, ttl=TTL):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (now, val)
    return val


def _rpc(what, d=None):
    r = requests.post(f"{URL}/rest/v1/rpc/goaliq_read", headers=_h(), timeout=30,
                      json={"secret": DB_SECRET, "what": what, "d": d})
    r.raise_for_status()
    return r.json()


def picks(d):
    return _cached(("picks", d), lambda: _rpc("picks", d))


def parlays(d):
    return _cached(("parlays", d), lambda: _rpc("parlays", d))


def snapshots():
    return _cached("snapshots", lambda: _rpc("snapshots"))


def record():
    return _cached("record", lambda: _rpc("record"), ttl=600)


def history():
    """Every graded pick with names. Public rows only (the database hides a night until it's over)."""
    out, off, page = [], 0, 1000
    while True:
        r = requests.get(f"{URL}/rest/v1/nhl_props_picks", timeout=30,
                         headers={"apikey": PUBLISHABLE, "Range": f"{off}-{off + page - 1}"},
                         params={"select": "pick_date,market,rank,player,team,opp,p,fair_odds,result,stat",
                                 "result": "in.(Hit,Miss)", "order": "pick_date.asc,market.asc,rank.asc"})
        r.raise_for_status()
        chunk = r.json()
        out += [{"d": x["pick_date"], "m": x["market"], "r": x["rank"], "n": x["player"], "t": x["team"], "o": x["opp"],
                 "p": float(x["p"]), "f": x["fair_odds"], "res": x["result"], "s": x["stat"]} for x in chunk]
        if len(chunk) < page:
            return out
        off += page


def enabled():
    return bool(DB_SECRET)
