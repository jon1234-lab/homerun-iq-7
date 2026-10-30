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


def enabled():
    return bool(DB_SECRET)
