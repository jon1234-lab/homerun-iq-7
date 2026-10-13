"""Cron entrypoint (Render cron job, every 20 min through the NHL day):
  1. rebuild today's slate -> upsert pregame predictions, lock games that have started
  2. grade FINAL games for yesterday and today (never mid-game)
"""
import datetime as dt
from zoneinfo import ZoneInfo

import db
import nhl_data as D
import slate

FINAL = {"FINAL", "OFF"}


def et_today():
    return dt.datetime.now(ZoneInfo("America/New_York")).date()


def grade_date(date):
    results = {}
    for g in D.schedule(date):
        state, goals = D.boxscore_goals(g["game_id"])
        if state in FINAL:
            results[g["game_id"]] = goals
    return db.grade(results), len(results)


def main():
    today = et_today()
    print("slate", today, db.save_slate(slate.build(today.isoformat())))
    for d in (today - dt.timedelta(days=1), today):
        n, finals = grade_date(d.isoformat())
        print("graded", d, n, "rows across", finals, "final games")


if __name__ == "__main__":
    main()
