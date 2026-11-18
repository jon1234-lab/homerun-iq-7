"""Turn raw NHL stats-API game rows into three tidy tables:

sg  skater-games  (one row per skater per game)
tg  team-games    (one row per team per game, final score as books grade it: shootout winner +1)
gg  goalie-games  (one row per goalie appearance)
"""
import numpy as np
import pandas as pd

POS = {"C": "F", "L": "F", "R": "F", "D": "D"}


def skater_table(summary: pd.DataFrame, toi: pd.DataFrame) -> pd.DataFrame:
    s = summary[["seasonId", "gameId", "gameDate", "playerId", "skaterFullName", "positionCode", "teamAbbrev",
                 "opponentTeamAbbrev", "homeRoad", "goals", "assists", "points", "shots", "ppPoints", "ppGoals",
                 "timeOnIcePerGame"]].copy()
    t = toi[["gameId", "playerId", "ppTimeOnIce", "shTimeOnIce"]]
    s = s.merge(t, on=["gameId", "playerId"], how="left")
    s = s.rename(columns={"seasonId": "season", "gameDate": "date", "skaterFullName": "name", "positionCode": "pos_raw",
                          "teamAbbrev": "team", "opponentTeamAbbrev": "opp", "ppPoints": "ppp", "ppGoals": "ppg",
                          "timeOnIcePerGame": "toi", "ppTimeOnIce": "pptoi", "shTimeOnIce": "shtoi"})
    s["pos"] = s.pos_raw.map(POS).fillna("F")
    s["home"] = (s.homeRoad == "H").astype(np.int8)
    for c in ("goals", "assists", "points", "shots", "ppp", "ppg", "toi", "pptoi", "shtoi"):
        s[c] = pd.to_numeric(s[c], errors="coerce").fillna(0).astype(np.float32)
    s["date"] = pd.to_datetime(s.date)
    s[["gameId", "playerId", "season"]] = s[["gameId", "playerId", "season"]].astype("int64")
    return s.drop(columns=["homeRoad", "pos_raw"]).sort_values(["date", "gameId", "playerId"]).reset_index(drop=True)


def team_table(team: pd.DataFrame) -> pd.DataFrame:
    team = team.infer_objects()
    t = team[["seasonId", "gameId", "gameDate", "teamFullName", "opponentTeamAbbrev", "homeRoad", "goalsFor",
              "goalsAgainst", "shotsForPerGame", "shotsAgainstPerGame", "wins", "winsInRegulation", "winsInShootout",
              "otLosses", "losses"]].copy()
    # a row's own abbreviation is the opponent abbreviation written on the other row of the same game
    other = t[["gameId", "teamFullName", "opponentTeamAbbrev"]].rename(
        columns={"teamFullName": "oppFull", "opponentTeamAbbrev": "team"})
    t = t.merge(other, on="gameId")
    t = t[t.teamFullName != t.oppFull].copy()
    t = t.rename(columns={"seasonId": "season", "gameDate": "date", "opponentTeamAbbrev": "opp", "goalsFor": "gf_raw",
                          "goalsAgainst": "ga_raw", "shotsForPerGame": "sf", "shotsAgainstPerGame": "sa"})
    t["home"] = (t.homeRoad == "H").astype(np.int8)
    so_win = t.winsInShootout.fillna(0).astype(int)
    so_loss = ((t.gf_raw == t.ga_raw) & (so_win == 0)).astype(int)   # tied after OT -> lost the shootout
    t["gf"] = t.gf_raw + so_win
    t["ga"] = t.ga_raw + so_loss
    t["win"] = (t.gf > t.ga).astype(np.int8)
    t["reg_win"] = t.winsInRegulation.fillna(0).astype(np.int8)
    t["went_ot"] = ((t.win == 1) & (t.reg_win == 0) | (t.otLosses.fillna(0) > 0)).astype(np.int8)
    t["date"] = pd.to_datetime(t.date)
    t[["gameId", "season"]] = t[["gameId", "season"]].astype("int64")
    return t[["season", "gameId", "date", "team", "teamFullName", "opp", "home", "gf", "ga", "gf_raw", "ga_raw",
              "sf", "sa", "win", "reg_win", "went_ot"]].sort_values(["date", "gameId", "home"]).reset_index(drop=True)


def goalie_table(goalie: pd.DataFrame) -> pd.DataFrame:
    g = goalie[["seasonId", "gameId", "gameDate", "playerId", "goalieFullName", "teamAbbrev", "opponentTeamAbbrev",
                "gamesStarted", "shotsAgainst", "saves", "goalsAgainst", "timeOnIce"]].copy()
    g = g.rename(columns={"seasonId": "season", "gameDate": "date", "goalieFullName": "name", "teamAbbrev": "team",
                          "opponentTeamAbbrev": "opp", "gamesStarted": "started", "shotsAgainst": "sa",
                          "goalsAgainst": "ga", "timeOnIce": "toi"})
    for c in ("started", "sa", "saves", "ga", "toi"):
        g[c] = pd.to_numeric(g[c], errors="coerce").fillna(0)
    g["date"] = pd.to_datetime(g.date)
    g[["gameId", "playerId", "season"]] = g[["gameId", "playerId", "season"]].astype("int64")
    return g.sort_values(["date", "gameId", "playerId"]).reset_index(drop=True)
