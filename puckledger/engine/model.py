"""Model probabilities from features + fitted coefficients (data/coef.json).

Players: Poisson goals / points, negative-binomial shots.  Teams: Poisson regulation goals with an
empty-net / extra-attacker correction (late one-goal games often become two-goal games or ties), then OT/SO.
"""
import json
import os

import numpy as np
from scipy.special import betainc, gammaincc, gammaln

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COEF = json.load(open(os.path.join(HERE, "data", "coef.json")))
F_TO_2, F_TO_TIE = COEF.get("en_to2", 0.194), COEF.get("en_tie", 0.204)


def lam(name, df):
    c = COEF[name]
    return np.exp(c["b0"] + sum(df[k].values * v for k, v in c["b"].items()))


def pois_pmf(k, lmb):
    k = np.asarray(k, dtype=float)
    return np.exp(k * np.log(lmb) - lmb - gammaln(k + 1))


def p_over_shots(mu, line):
    """P(shots > line) for a half-point line (e.g. 2.5 -> 3+). Negative binomial, mean mu, size r."""
    r = COEF["shots_nb_r"]
    k = int(np.floor(line))
    p = r / (r + np.asarray(mu, dtype=float))
    return 1 - betainc(r, k + 1, p)


def p_at_least(lmb, k):
    """P(Poisson(lmb) >= k)."""
    if k <= 0:
        return np.ones_like(np.asarray(lmb, dtype=float))
    return 1 - gammaincc(k, np.asarray(lmb, dtype=float))


def game_dist(lh, la, n=12):
    """Joint P(final home goals, final away goals) as books grade it (OT/SO winner +1)."""
    G = np.arange(n)
    M = np.outer(pois_pmf(G, lh), pois_pmf(G, la))
    D = G[:, None] - G[None, :]
    out = M * np.where(np.abs(D) == 1, 1 - F_TO_2 - F_TO_TIE, 1)
    hw, aw = M * (D == 1), M * (D == -1)
    out[1:, :] += (hw * F_TO_2)[:-1, :]       # home by 1 -> home by 2 (empty-net goal)
    out[:, 1:] += (aw * F_TO_2)[:, :-1]
    out[:, 1:] += (hw * F_TO_TIE)[:, :-1]     # home by 1 -> tie (away scores with extra attacker)
    out[1:, :] += (aw * F_TO_TIE)[:-1, :]
    p_ot_home = 0.5 + 0.5 * (lh / (lh + la) - 0.5)
    t = np.diag(out).copy()
    final = out - np.diag(t)
    # tie at k-k after regulation -> home wins k+1:k or away wins k:k+1 in OT/shootout
    for k in range(n - 1):
        final[k + 1, k] += t[k] * p_ot_home
        final[k, k + 1] += t[k] * (1 - p_ot_home)
    return final / final.sum()


def game_probs(lh, la, total_lines=(4.5, 5.5, 6.5, 7.5)):
    F = game_dist(lh, la)
    n = F.shape[0]
    G = np.arange(n)
    D = G[:, None] - G[None, :]
    T = G[:, None] + G[None, :]
    out = {"lh": float(lh), "la": float(la),
           "home_win": float(F[D > 0].sum()), "away_win": float(F[D < 0].sum()),
           "home_m15": float(F[D >= 2].sum()), "away_m15": float(F[D <= -2].sum()),
           "exp_total": float((F * T).sum())}
    out["home_p15"] = 1 - out["away_m15"]
    out["away_p15"] = 1 - out["home_m15"]
    out["over"] = {str(L): float(F[T > L].sum()) for L in total_lines}
    return out


def p_total_over(lh, la, line):
    F = game_dist(lh, la)
    G = np.arange(F.shape[0])
    T = G[:, None] + G[None, :]
    if float(line).is_integer():   # whole-number total: push when equal -> return P(over | no push)
        over, push = F[T > line].sum(), F[T == line].sum()
        return float(over / max(1 - push, 1e-9))
    return float(F[T > line].sum())


def fair_american(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return int(round(-100 * p / (1 - p))) if p >= 0.5 else int(round(100 * (1 - p) / p))


def american_to_decimal(a):
    a = float(a)
    return 1 + (a / 100 if a > 0 else 100 / -a)
