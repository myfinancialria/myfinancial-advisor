# MyFinancial · Robo-Advisor

A goal-based **mutual-fund robo-advisor, screener and portfolio analyser** for a
SEBI Registered Investment Adviser (RIA) — **Direct plans only**. Single,
self-contained web app; fund NAVs refresh automatically every weekday.

**Live site:** https://myfinancialria.github.io/myfinancial-advisor/

## What it does
- Risk-profile questionnaire → SEBI-style band
- Goal & retirement/emergency planning → required SIP, asset allocation
- Whole-market Direct-Growth fund screener with per-category ranking
- Recommended portfolio with **expected return, volatility, and stock look-through**
- Portfolio (CAS) analysis: returns, beta/alpha, overlap, drift, unit-level rebalancing
- **All-Weather collateral (F&O / option sellers):** amount + risk + tenure → pledgeable ETF allocation, buy-list, cash/non-cash margin plan, red flags & drift-rebalancing
- Tax planning (FY2025-26) + retirement SWP

> ⚠️ Educational / advisory-support tool. Mutual funds are subject to market risk.
> Returns shown are illustrative and not guaranteed. Beta, alpha and stock overlap
> are estimates; stock holdings shown are illustrative until real factsheet data is
> loaded. Not a substitute for a registered adviser or a CA.

## Daily data refresh
`.github/workflows/daily-nav.yml` rebuilds the app every weekday (08:00 IST) with
the latest AMFI NAVs + mfapi returns and commits it, so GitHub Pages redeploys
automatically. Data sources: AMFI (NAVAll.txt) and mfapi.in — both free.

Built with [Claude Code](https://claude.com/claude-code).
