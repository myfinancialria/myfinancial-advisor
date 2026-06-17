#!/usr/bin/env python3
"""
update_daily.py — refresh fund NAVs/returns and re-inject Advisor.html.

Thin wrapper around build_advisor.build so the daily refresh is one command,
schedulable from cron / launchd / GitHub Actions. Defaults to the fast curated
build (what the robo-advisor recommends from); pass --full for the whole market.

AMFI publishes NAVs late evening IST; mfapi reflects them next morning. Schedule
this for ~08:00 IST.

    # local cron (crontab -e):  refresh every weekday 08:00 IST
    0 8 * * 1-5  cd /path/to/Screener && /usr/bin/python3 mf_advisor/update_daily.py >> data/.update.log 2>&1

    # macOS launchd / VS Code task / GitHub Action also work — see README.

Usage:
    python3 mf_advisor/update_daily.py            # curated, refetch NAVs
    python3 mf_advisor/update_daily.py --full      # whole market + ranking
"""
from __future__ import annotations
import argparse, json, os, re, sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_advisor                                 # noqa: E402


def _embedded_is_full() -> bool:
    """Detect whether Advisor.html currently embeds the full-market universe."""
    try:
        html = open(build_advisor.HTML, encoding="utf-8").read()
        blob = re.search(r"/\*FUNDS\*/(.*?)/\*FUNDS\*/", html, re.DOTALL).group(1)
        return bool(json.loads(blob).get("full"))
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--full", action="store_true", help="force whole-market build")
    g.add_argument("--curated", action="store_true", help="force curated build")
    ap.add_argument("--enrich", type=int, default=350)
    a = ap.parse_args()
    # default: keep whatever the file already uses, so the daily job doesn't shrink it
    full = True if a.full else False if a.curated else _embedded_is_full()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[update_daily] {stamp} — refreshing ({'full' if full else 'curated'})")
    funds = build_advisor.mf_data.build(use_network=True, max_age_days=0,
                                        full=full, enrich_cap=a.enrich)
    models = build_advisor.robo.model_table()
    html = open(build_advisor.HTML, encoding="utf-8").read()
    html = build_advisor.inject(html, "FUNDS", funds)
    html = build_advisor.inject(html, "MODELS", models)
    open(build_advisor.HTML, "w", encoding="utf-8").write(html)
    print(f"[update_daily] done — {len(funds['funds'])} funds, NAV {funds.get('nav_date')}")


if __name__ == "__main__":
    main()
