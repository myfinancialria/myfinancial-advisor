#!/usr/bin/env python3
"""
build_advisor.py — one command to build the Robo-Advisor app.

Runs the fund-data pipeline, pulls the robo model grid, and injects both JSON
blobs into Advisor.html between comment fences (same pattern as Discover.html):

    const FUNDS  = /*FUNDS*/{}/*FUNDS*/;
    const MODELS = /*MODELS*/{}/*MODELS*/;

Usage:
    python3 mf_advisor/build_advisor.py                 # fetch live + inject
    python3 mf_advisor/build_advisor.py --no-network    # offline universe
    python3 mf_advisor/build_advisor.py --max-age 7
"""
from __future__ import annotations
import argparse, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mf_data, robo                                   # noqa: E402

HTML = os.path.join(ROOT, "Advisor.html")


def inject(html: str, marker: str, payload: dict) -> str:
    blob = json.dumps(payload, separators=(",", ":"))
    pat = re.compile(r"/\*" + marker + r"\*/.*?/\*" + marker + r"\*/", re.DOTALL)
    repl = "/*" + marker + "*/" + blob + "/*" + marker + "*/"
    if not pat.search(html):
        raise SystemExit(f"marker /*{marker}*/…/*{marker}*/ not found in Advisor.html")
    return pat.sub(lambda _: repl, html, count=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-network", action="store_true")
    ap.add_argument("--full", action="store_true",
                    help="ingest the whole Direct-Growth market + ranking (slower)")
    ap.add_argument("--enrich", type=int, default=350,
                    help="max mfapi return-history calls in --full mode")
    ap.add_argument("--max-age", type=float, default=None)
    a = ap.parse_args()

    funds = mf_data.build(use_network=not a.no_network, max_age_days=a.max_age,
                          full=a.full, enrich_cap=a.enrich)
    models = robo.model_table()
    holdings_db = {}
    hpath = os.path.join(ROOT, "data", "fund_holdings.json")
    if os.path.exists(hpath):
        holdings_db = json.load(open(hpath))

    if not os.path.exists(HTML):
        raise SystemExit(f"{HTML} missing — create Advisor.html first")
    html = open(HTML, encoding="utf-8").read()
    html = inject(html, "FUNDS", funds)
    html = inject(html, "MODELS", models)
    html = inject(html, "HOLDINGS", holdings_db)
    open(HTML, "w", encoding="utf-8").write(html)
    print(f"[build_advisor] injected {len(funds['funds'])} funds + robo models "
          f"+ holdings({len(holdings_db.get('schemes', {}))} schemes) → {HTML}")


if __name__ == "__main__":
    main()
