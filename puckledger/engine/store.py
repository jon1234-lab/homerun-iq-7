"""Supabase access through the private pl_* functions (secret in PL_DB_SECRET)."""
import json
import os

import numpy as np
import requests

URL = os.environ.get("SUPABASE_URL", "https://lfpfdpfktvjpnjwmegyd.supabase.co").rstrip("/")
KEY = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_DyoU7kki74m2sYKvBqBHUQ_QsXc5BF9")
SECRET = os.environ.get("PL_DB_SECRET", "")
S = requests.Session()


def enabled():
    return bool(SECRET)


class _Enc(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return None if np.isnan(o) else float(o)
        if hasattr(o, "isoformat"):
            return o.isoformat()
        return super().default(o)


def _rpc(fn, **args):
    body = json.dumps({"secret": SECRET, **args}, cls=_Enc)
    r = S.post(f"{URL}/rest/v1/rpc/{fn}", data=body, timeout=60,
               headers={"apikey": KEY, "Content-Type": "application/json"})
    if r.status_code >= 300:
        raise RuntimeError(f"{fn}: {r.status_code} {r.text[:300]}")
    return r.json() if r.text else None


def put_bets(rows):
    n = 0
    for i in range(0, len(rows), 500):
        n += _rpc("pl_put_bets", rows=json.loads(json.dumps(rows[i:i + 500], cls=_Enc))) or 0
    return n


def get_bets(d_from, d_to):
    return _rpc("pl_get_bets", d_from=str(d_from), d_to=str(d_to)) or []


def put_kv(k, v):
    _rpc("pl_set_kv", key_name=k, val=json.loads(json.dumps(v, cls=_Enc)))


def get_kv(*keys):
    return _rpc("pl_get_kv", keys=list(keys)) or {}


def kv_prefix(prefix):
    return _rpc("pl_kv_prefix", prefix=prefix) or {}
