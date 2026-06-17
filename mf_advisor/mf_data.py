#!/usr/bin/env python3
"""
mf_data.py — Mutual-fund universe for the MyFinancial Robo-Advisor.

RIA model: we deal only in **Direct plans, Growth option** (no commission).

Two universes from one build:
  • CURATED  — a vetted shortlist the robo-advisor recommends portfolios from
               (always enriched with trailing returns).
  • FULL     — (`--full`) every Direct-Growth scheme AMFI lists, bucketed into
               categories and screener-ranked (per-category star rating 1–5 and
               rank), so the screener can browse the whole market.

Pipeline:
  1. AMFI NAVAll.txt  → every scheme + today's NAV (free, official).
  2. Keep Direct + Growth, bucket into asset classes / categories.
  3. Enrich (bounded) with CAGR 1y/3y/5y + annualised vol from mfapi.in.
  4. Rank within each category; write data/mf_funds.json.

Network is optional — embedded offline universe used if AMFI/mfapi are down.

Usage:
    python3 mf_advisor/mf_data.py                  # curated only (24 funds, all enriched)
    python3 mf_advisor/mf_data.py --full           # whole market + ranking (slower)
    python3 mf_advisor/mf_data.py --full --enrich 400   # cap mfapi calls (default 350)
    python3 mf_advisor/mf_data.py --no-network     # embedded offline universe
    python3 mf_advisor/mf_data.py --max-age 7      # reuse NAV cache if < N days old
"""
from __future__ import annotations
import argparse, json, math, os, sys, time
from datetime import datetime, date

try:
    import requests
except Exception:                                  # pragma: no cover
    requests = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "mf_funds.json")
NAV_CACHE = os.path.join(DATA, ".amfi_navall.txt")

AMFI_NAV_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
MFAPI = "https://api.mfapi.in/mf/{code}"
HTTP_TIMEOUT = 25
_SESSION = requests.Session() if requests else None   # connection reuse for bulk enrichment

# --- Asset-class taxonomy -------------------------------------------------
# bucket -> (asset_class, expected nominal return %, ann. vol %, label)
CATEGORIES = {
    "equity_largecap":   ("equity", 12.0, 16.0, "Large Cap"),
    "equity_largemid":   ("equity", 13.0, 18.0, "Large & Mid Cap"),
    "equity_flexi":      ("equity", 13.0, 17.0, "Flexi / Multi Cap"),
    "equity_midcap":     ("equity", 14.0, 20.0, "Mid Cap"),
    "equity_smallcap":   ("equity", 15.0, 24.0, "Small Cap"),
    "equity_index":      ("equity", 11.5, 16.0, "Index / ETF FoF"),
    "equity_value":      ("equity", 13.0, 18.0, "Value / Contra"),
    "equity_thematic":   ("equity", 14.0, 22.0, "Sectoral / Thematic"),
    "elss":              ("equity", 13.0, 17.0, "ELSS (tax-saver)"),
    "hybrid_aggressive": ("hybrid", 11.0, 12.0, "Aggressive Hybrid"),
    "hybrid_balanced":   ("hybrid",  9.5, 8.0,  "Balanced Advantage"),
    "hybrid_conservative":("hybrid", 8.5, 5.0,  "Conservative Hybrid"),
    "hybrid_multiasset": ("hybrid", 10.0, 9.0,  "Multi Asset"),
    "hybrid_equitysaving":("hybrid", 8.5, 5.5,  "Equity Savings"),
    "debt_corporate":    ("debt",    7.5, 3.0,  "Corporate Bond"),
    "debt_banking_psu":  ("debt",    7.4, 2.8,  "Banking & PSU"),
    "debt_gilt":         ("debt",    7.3, 4.0,  "Gilt"),
    "debt_dynamic":      ("debt",    7.4, 3.5,  "Dynamic Bond"),
    "debt_short":        ("debt",    7.0, 2.0,  "Short Duration"),
    "debt_ultrashort":   ("debt",    6.8, 1.2,  "Ultra Short / Low Dur"),
    "debt_moneymarket":  ("debt",    6.9, 1.0,  "Money Market"),
    "debt_liquid":       ("debt",    6.5, 0.7,  "Liquid / Overnight"),
    "gold":              ("gold",    9.0, 14.0, "Gold / Silver"),
    "other":             ("other",   7.0, 8.0,  "Other"),
}

# Buckets the robo-advisor will recommend from (clean, mainstream sleeves).
RECO_BUCKETS = {
    "equity_largecap","equity_largemid","equity_flexi","equity_midcap",
    "equity_smallcap","equity_index","elss","hybrid_aggressive","hybrid_balanced",
    "debt_corporate","debt_banking_psu","debt_short","debt_liquid","gold",
}

# --- Curated recommendation shortlist ------------------------------------
# Resolved to an AMFI scheme code by matching amc + name fragment on the
# Direct-Growth rows (robust to code changes). offline = fallback metrics.
CURATED = [
    ("UTI",            "Nifty 50 Index",          "equity_index",      dict(nav=160.2, r1=11.8, r3=14.1, r5=15.2)),
    ("HDFC",           "Index Fund-S&P BSE Sensex","equity_index",     dict(nav=720.5, r1=11.5, r3=13.9, r5=15.0)),
    ("ICICI Prudential","Nifty Next 50 Index",    "equity_index",      dict(nav=58.9,  r1=13.0, r3=16.2, r5=17.1)),
    ("Parag Parikh",   "Flexi Cap",               "equity_flexi",      dict(nav=82.4,  r1=16.5, r3=19.8, r5=22.4)),
    ("HDFC",           "Flexi Cap",               "equity_flexi",      dict(nav=1820.0,r1=18.0, r3=24.0, r5=22.0)),
    ("Mirae Asset",    "Large & Midcap",          "equity_largemid",   dict(nav=148.0, r1=14.0, r3=17.5, r5=20.0)),
    ("Nippon India",   "Large Cap",               "equity_largecap",   dict(nav=88.6,  r1=15.0, r3=20.1, r5=19.0)),
    ("ICICI Prudential","Bluechip",               "equity_largecap",   dict(nav=112.3, r1=14.2, r3=18.0, r5=18.2)),
    ("Motilal Oswal",  "Midcap",                  "equity_midcap",     dict(nav=104.0, r1=20.0, r3=29.0, r5=27.0)),
    ("HDFC",           "Mid-Cap Opportunities",   "equity_midcap",     dict(nav=200.5, r1=18.5, r3=27.0, r5=26.0)),
    ("Nippon India",   "Small Cap",               "equity_smallcap",   dict(nav=178.0, r1=19.0, r3=30.0, r5=33.0)),
    ("Quant",          "Small Cap",               "equity_smallcap",   dict(nav=270.0, r1=16.0, r3=28.0, r5=38.0)),
    ("Mirae Asset",    "ELSS Tax Saver",          "elss",              dict(nav=48.5,  r1=13.0, r3=16.0, r5=19.0)),
    ("Quant",          "ELSS Tax Saver",          "elss",              dict(nav=420.0, r1=14.0, r3=22.0, r5=30.0)),
    ("ICICI Prudential","Equity & Debt",          "hybrid_aggressive", dict(nav=395.0, r1=15.0, r3=21.0, r5=19.5)),
    ("HDFC",           "Balanced Advantage",      "hybrid_balanced",   dict(nav=520.0, r1=13.0, r3=17.5, r5=16.0)),
    ("ICICI Prudential","Balanced Advantage",     "hybrid_balanced",   dict(nav=72.0,  r1=11.5, r3=13.0, r5=13.5)),
    ("HDFC",           "Corporate Bond",          "debt_corporate",    dict(nav=32.5,  r1=8.0,  r3=6.8,  r5=7.4)),
    ("ICICI Prudential","Corporate Bond",         "debt_corporate",    dict(nav=29.8,  r1=8.0,  r3=6.9,  r5=7.5)),
    ("HDFC",           "Short Term Debt",         "debt_short",        dict(nav=31.2,  r1=7.8,  r3=6.5,  r5=7.0)),
    ("ICICI Prudential","Liquid",                 "debt_liquid",       dict(nav=372.0, r1=7.1,  r3=6.0,  r5=5.4)),
    ("Nippon India",   "Liquid",                  "debt_liquid",       dict(nav=6050.0,r1=7.1,  r3=6.0,  r5=5.4)),
    ("Nippon India",   "Gold Savings",            "gold",              dict(nav=33.5,  r1=20.0, r3=15.0, r5=13.0)),
    ("HDFC",           "Gold",                    "gold",              dict(nav=24.0,  r1=20.0, r3=15.0, r5=13.0)),
]


def log(*a):
    print("[mf_data]", *a, file=sys.stderr)


# --- AMFI parsing ---------------------------------------------------------
def fetch_navall(use_network: bool, max_age_days):
    if max_age_days is not None and os.path.exists(NAV_CACHE):
        age = (time.time() - os.path.getmtime(NAV_CACHE)) / 86400.0
        if age <= max_age_days:
            log(f"using cached NAVAll.txt ({age:.1f}d old)")
            return open(NAV_CACHE, encoding="utf-8", errors="replace").read()
    if not (use_network and requests):
        return None
    try:
        log("downloading AMFI NAVAll.txt …")
        r = requests.get(AMFI_NAV_URL, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        os.makedirs(DATA, exist_ok=True)
        open(NAV_CACHE, "w", encoding="utf-8").write(r.text)
        return r.text
    except Exception as e:
        log("AMFI fetch failed:", e)
        return None


def parse_navall(text: str) -> list:
    rows, cur_cat, cur_amc = [], "", ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if ";" not in line:
            if "Scheme" in line and "(" in line:
                cur_cat = line
            elif "Mutual Fund" in line:
                cur_amc = line
            continue
        parts = line.split(";")
        if len(parts) < 6 or parts[0] == "Scheme Code":
            continue
        code, _i1, _i2, name, nav, dt = parts[:6]
        nl = name.lower()
        if "direct" not in nl or "growth" not in nl:
            continue
        # skip dividend/IDCW/bonus/segregated noise even if "growth" appears
        if any(k in nl for k in ("idcw", "dividend", "bonus", "segregated")):
            continue
        try:
            navf = float(nav)
        except ValueError:
            continue
        rows.append(dict(code=code.strip(), name=name.strip(), amc=cur_amc,
                         raw_cat=cur_cat, nav=navf, nav_date=dt.strip()))
    return rows


def bucket_of(raw_cat: str, name: str):
    c = (raw_cat + " " + name).lower()
    # debt
    if "overnight" in c or "liquid" in c:                 return "debt_liquid"
    if "money market" in c:                               return "debt_moneymarket"
    if "ultra short" in c or "low duration" in c:         return "debt_ultrashort"
    if "banking" in c and "psu" in c:                     return "debt_banking_psu"
    if "gilt" in c or "government securities" in c:        return "debt_gilt"
    if "dynamic bond" in c:                               return "debt_dynamic"
    if "corporate bond" in c:                             return "debt_corporate"
    if "short duration" in c or "short term" in c:        return "debt_short"
    # commodity
    if "gold" in c or "silver" in c:                      return "gold"
    # hybrid
    if "balanced advantage" in c or "dynamic asset" in c: return "hybrid_balanced"
    if "aggressive hybrid" in c or "equity & debt" in c or "equity and debt" in c: return "hybrid_aggressive"
    if "conservative hybrid" in c:                        return "hybrid_conservative"
    if "multi asset" in c:                                return "hybrid_multiasset"
    if "equity savings" in c:                             return "hybrid_equitysaving"
    if "arbitrage" in c:                                  return "hybrid_equitysaving"
    # equity
    if "elss" in c or "tax saver" in c:                   return "elss"
    if "index" in c or "nifty" in c or "sensex" in c or "etf" in c: return "equity_index"
    if "small cap" in c:                                  return "equity_smallcap"
    if "mid cap" in c or "midcap" in c:                   return "equity_midcap"
    if "large & mid" in c or "large and mid" in c:        return "equity_largemid"
    if "large cap" in c or "bluechip" in c or "blue chip" in c: return "equity_largecap"
    if "value" in c or "contra" in c or "dividend yield" in c: return "equity_value"
    if "sectoral" in c or "thematic" in c or "infrastructure" in c or "pharma" in c \
       or "technology" in c or "consumption" in c or "banking" in c: return "equity_thematic"
    if "flexi cap" in c or "multi cap" in c or "focused" in c or "multicap" in c: return "equity_flexi"
    if "equity" in c:                                     return "equity_flexi"
    if "debt" in c or "bond" in c or "duration" in c or "credit" in c: return "debt_short"
    return "other"


# --- mfapi.in returns enrichment -----------------------------------------
def enrich_returns(code: str, timeout=HTTP_TIMEOUT):
    if not requests:
        return None
    try:
        r = _SESSION.get(MFAPI.format(code=code), timeout=timeout)
        r.raise_for_status()
        hist = r.json().get("data", [])
        if len(hist) < 30:
            return None
        series = []
        for h in hist:
            try:
                series.append((datetime.strptime(h["date"], "%d-%m-%Y").date(), float(h["nav"])))
            except Exception:
                continue
        series.sort()
        if len(series) < 30:
            return None
        latest_d, latest_n = series[-1]

        def cagr(years):
            try:
                target = latest_d.replace(year=latest_d.year - int(years))
            except ValueError:
                target = latest_d
            past = min(series, key=lambda x: abs((x[0] - target).days))
            if abs((past[0] - target).days) > 60 or past[1] <= 0:
                return None
            return round(((latest_n / past[1]) ** (1.0 / years) - 1.0) * 100, 1)

        navs = [n for _, n in series[-260:]]
        rets = [navs[i] / navs[i - 1] - 1 for i in range(1, len(navs)) if navs[i - 1] > 0]
        vol = None
        if len(rets) > 20:
            m = sum(rets) / len(rets)
            var = sum((x - m) ** 2 for x in rets) / (len(rets) - 1)
            vol = round(math.sqrt(var) * math.sqrt(252) * 100, 1)
        return dict(r1=cagr(1), r3=cagr(3), r5=cagr(5), vol=vol, nav=round(latest_n, 4))
    except Exception:
        return None


# --- ranking --------------------------------------------------------------
def rank_universe(funds: list):
    """Per-category rank + 1–5 star rating by 3y (fallback 1y) return."""
    by_cat = {}
    for f in funds:
        if f.get("r3") is not None or f.get("r1") is not None:
            by_cat.setdefault(f["bucket"], []).append(f)
    for bucket, group in by_cat.items():
        group.sort(key=lambda x: (x.get("r3") if x.get("r3") is not None else x.get("r1", -99)),
                   reverse=True)
        n = len(group)
        for i, f in enumerate(group):
            f["rank_in_cat"] = i + 1
            f["cat_size"] = n
            pct = i / n if n > 1 else 0          # 0 = best
            f["stars"] = 5 if pct < 0.2 else 4 if pct < 0.4 else 3 if pct < 0.6 else 2 if pct < 0.8 else 1


# --- Build ----------------------------------------------------------------
def resolve_curated(rows: list):
    out = []
    for amc_f, name_f, bucket, offline in CURATED:
        match = None
        for r in rows:
            hay = (r["amc"] + " " + r["name"]).lower()
            toks = name_f.lower().split()
            if amc_f.lower() in hay and all(t in r["name"].lower() for t in toks):
                # prefer exact "nifty 50" over "nifty next 50"
                if "next" in r["name"].lower() and "next" not in name_f.lower():
                    continue
                match = r
                break
        out.append((amc_f, name_f, bucket, offline, match))
    return out


def build(use_network=True, max_age_days=None, full=False, enrich_cap=350):
    asset_cls = {b: CATEGORIES[b][0] for b in CATEGORIES}
    text = fetch_navall(use_network, max_age_days)
    funds, nav_date, source = [], None, "offline"

    if text:
        rows = parse_navall(text)
        log(f"parsed {len(rows)} Direct-Growth schemes from AMFI")
        nav_date = rows[0]["nav_date"] if rows else None
        source = "amfi"

        # 1) curated shortlist (always enriched, flagged for the robo engine)
        curated_codes = set()
        resolved = resolve_curated(rows)
        for i, (amc_f, name_f, bucket, offline, m) in enumerate(resolved):
            ac, exp_ret, exp_vol, cat_label = CATEGORIES[bucket]
            f = dict(id=f"C{i+1:03d}", bucket=bucket, asset_class=ac,
                     category=cat_label, exp_return=exp_ret, curated=True)
            if m:
                curated_codes.add(m["code"])
                f.update(code=m["code"], name=m["name"], amc=m["amc"], nav=m["nav"])
                if use_network:
                    met = enrich_returns(m["code"])
                    if met:
                        f.update({k: v for k, v in met.items() if v is not None})
                        time.sleep(0.12)
            else:
                f.update(code=None, name=f"{amc_f} {name_f} Direct-Growth", amc=amc_f, nav=offline["nav"])
            f.setdefault("r1", offline["r1"]); f.setdefault("r3", offline["r3"])
            f.setdefault("r5", offline["r5"]); f.setdefault("vol", exp_vol)
            funds.append(f)

        # 2) full universe (bucketed; bounded enrichment + ranking)
        if full:
            log(f"ingesting full universe; enriching up to {enrich_cap} via mfapi …")
            per_bucket, used_codes, enriched = {}, set(curated_codes), 0
            cap_each = max(8, enrich_cap // max(1, len(CATEGORIES)))
            for j, r in enumerate(rows):
                if r["code"] in used_codes:
                    continue
                bucket = bucket_of(r["raw_cat"], r["name"])
                ac, exp_ret, exp_vol, cat_label = CATEGORIES[bucket]
                f = dict(id=f"A{j:05d}", bucket=bucket, asset_class=ac,
                         category=cat_label, exp_return=exp_ret, code=r["code"],
                         name=r["name"], amc=r["amc"], nav=r["nav"])
                cnt = per_bucket.get(bucket, 0)
                if use_network and enriched < enrich_cap and cnt < cap_each:
                    met = enrich_returns(r["code"], timeout=8)   # short timeout: don't let stalls dominate
                    if met:
                        f.update({k: v for k, v in met.items() if v is not None})
                        enriched += 1
                        time.sleep(0.05)
                per_bucket[bucket] = cnt + 1
                funds.append(f)
            log(f"full universe: {len(funds)} funds ({enriched} enriched live)")
    else:
        log("no network/cache — using embedded offline universe")
        for i, (amc_f, name_f, bucket, offline) in enumerate(CURATED):
            ac, exp_ret, exp_vol, cat_label = CATEGORIES[bucket]
            funds.append(dict(id=f"C{i+1:03d}", code=None, curated=True,
                              name=f"{amc_f} {name_f} Direct-Growth", amc=amc_f,
                              bucket=bucket, asset_class=ac, category=cat_label,
                              nav=offline["nav"], r1=offline["r1"], r3=offline["r3"],
                              r5=offline["r5"], vol=exp_vol, exp_return=exp_ret))

    rank_universe(funds)
    payload = dict(
        generated=date.today().isoformat(), nav_date=nav_date, source=source,
        full=full, plan="Direct - Growth only (RIA)", categories=CATEGORIES,
        asset_class=asset_cls, count=len(funds), funds=funds,
    )
    os.makedirs(DATA, exist_ok=True)
    json.dump(payload, open(OUT, "w"), separators=(",", ":"))
    log(f"wrote {len(funds)} funds → {OUT} (source={source}, full={full})")
    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-network", action="store_true")
    ap.add_argument("--full", action="store_true", help="ingest the whole Direct-Growth market + rank")
    ap.add_argument("--enrich", type=int, default=350, help="max mfapi return-history calls in --full")
    ap.add_argument("--max-age", type=float, default=None)
    a = ap.parse_args()
    build(use_network=not a.no_network, max_age_days=a.max_age,
          full=a.full, enrich_cap=a.enrich)


if __name__ == "__main__":
    main()
