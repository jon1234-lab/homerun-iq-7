"""Sweater numbers for the history wall's jersey icons, from current NHL rosters."""
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import nhl_data as D

TEAMS = ("ANA BOS BUF CGY CAR CHI COL CBJ DAL DET EDM FLA LAK MIN MTL NSH NJD NYI NYR OTT "
         "PHI PIT SJS SEA STL TBL TOR UTA VAN VGK WSH WPG").split()


def _norm(name):
    name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", name)


def rosters():
    """{team: [(normalized full name, normalized last name, number)]} for every club."""
    def one(t):
        try:
            return t, [(_norm(p["name"]), _norm(p["name"].split()[-1]), p["num"], p.get("id")) for p in D.roster(t) if p.get("num") is not None]
        except Exception:
            return t, []
    with ThreadPoolExecutor(8) as ex:
        out = dict(ex.map(one, TEAMS))
    if sum(len(v) for v in out.values()) < 300:
        raise RuntimeError("rosters didn't load")
    return out


def finder(rs):
    by_team, by_name, by_last, by_id = {}, {}, {}, {}
    for t, ps in rs.items():
        for full, last, num, pid in ps:
            by_team[(t, full)] = num
            if pid:
                by_id[int(pid)] = num
            by_name.setdefault(full, set()).add(num)
            by_last.setdefault((t, last), set()).add(num)

    def num(team, name, pid=None):
        if pid and int(pid) in by_id:
            return by_id[int(pid)]
        full = _norm(name)
        if (team, full) in by_team:
            return by_team[(team, full)]
        for pool in (by_name.get(full), by_last.get((team, _norm((name or " ").split()[-1])))):
            if pool and len(pool) == 1:
                return next(iter(pool))
        return None
    return num
