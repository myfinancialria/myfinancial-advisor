#!/usr/bin/env python3
"""
robo.py — the advisory brain behind the MyFinancial Robo-Advisor.

Pure, side-effect-free functions (no I/O) so the same logic can run server-side
in Python *and* be mirrored in the browser. It encodes three things an RIA must
document for every client:

  1. RISK PROFILE   — a SEBI-style questionnaire scored to a risk band.
  2. ASSET ALLOCATION — band + investment horizon → equity/debt/gold split,
     with a glide path that de-risks as the goal approaches.
  3. GOAL MATH      — future-value / required-SIP / lumpsum for a target corpus,
     using the blended expected return of the recommended allocation.

These are model portfolios for a registered adviser to review and approve — the
output is advice support, not auto-execution. Run as a script to emit the model
table the HTML embeds, or to sanity-check a sample client.
"""
from __future__ import annotations
import json, sys

# --- 1. Risk-profile questionnaire ---------------------------------------
# Each question scores 1 (most conservative) .. 4 (most aggressive).
QUESTIONNAIRE = [
    dict(id="age", q="Your age", opts=[
        ("Over 60", 1), ("46–60", 2), ("36–45", 3), ("18–35", 4)]),
    dict(id="income_stability", q="How stable is your income?", opts=[
        ("Irregular / uncertain", 1), ("Somewhat stable", 2),
        ("Stable salaried", 3), ("Very stable + growing", 4)]),
    dict(id="emergency", q="Months of expenses kept as emergency fund", opts=[
        ("None", 1), ("Up to 3 months", 2), ("3–6 months", 3), ("Over 6 months", 4)]),
    dict(id="experience", q="Your investing experience", opts=[
        ("None — first time", 1), ("Bank FDs / RDs only", 2),
        ("Some mutual funds", 3), ("Direct equity / many funds", 4)]),
    dict(id="reaction", q="Your ₹1,00,000 falls to ₹80,000 in 3 months. You…", opts=[
        ("Sell everything", 1), ("Sell some", 2),
        ("Hold and wait", 3), ("Invest more — it's cheaper", 4)]),
    dict(id="goal_importance", q="If this goal falls short, the impact is…", opts=[
        ("Severe — it's essential", 1), ("High", 2),
        ("Moderate", 3), ("Low — it's a stretch goal", 4)]),
    dict(id="return_pref", q="Which outcome do you prefer over 1 year?", opts=[
        ("+4% best, 0% worst (safe)", 1), ("+9% best, −4% worst", 2),
        ("+16% best, −12% worst", 3), ("+25% best, −22% worst", 4)]),
    dict(id="knowledge", q="“Equity funds can fall 30%+ in a bad year.” This is…", opts=[
        ("News to me / scary", 1), ("Known but worrying", 2),
        ("Expected, I can sit through it", 3), ("An opportunity to add", 4)]),
]
MIN_SCORE = len(QUESTIONNAIRE) * 1
MAX_SCORE = len(QUESTIONNAIRE) * 4

# Risk bands by normalised score (0..100).
BANDS = [
    ("Conservative", 0,   30, "Capital protection first; low tolerance for loss."),
    ("Moderate",     30,  50, "Balanced growth; can sit through moderate dips."),
    ("Balanced",     50,  68, "Growth-tilted; accepts meaningful volatility."),
    ("Aggressive",   68,  85, "Long-horizon growth; comfortable with big swings."),
    ("Very Aggressive", 85, 101, "Maximum growth; high volatility is fine."),
]


def score_profile(answers: dict[str, int]) -> dict:
    """answers: {question_id: chosen_score 1..4}. Returns band + normalised score."""
    raw = sum(int(answers.get(q["id"], 1)) for q in QUESTIONNAIRE)
    norm = round((raw - MIN_SCORE) / (MAX_SCORE - MIN_SCORE) * 100, 1)
    band, desc = "Conservative", BANDS[0][3]
    for name, lo, hi, d in BANDS:
        if lo <= norm < hi:
            band, desc = name, d
            break
    return dict(raw=raw, score=norm, band=band, description=desc)


# --- 2. Asset allocation --------------------------------------------------
# Base equity weight per risk band (for a long, 10y+ horizon).
BASE_EQUITY = {"Conservative": 25, "Moderate": 45, "Balanced": 60,
               "Aggressive": 75, "Very Aggressive": 90}


def allocation(band: str, years: float) -> dict:
    """
    Risk band + years-to-goal → {equity, debt, gold} percentages.
    Glide path: short horizons cap equity hard (sequence-of-returns risk),
    long horizons get the band's full equity weight. Gold is a small diversifier
    that scales with the equity sleeve. Always sums to 100.
    """
    eq = BASE_EQUITY.get(band, 45)
    # Horizon ceiling on equity — money you need soon should not ride the market.
    if years < 1:      eq = min(eq, 0)
    elif years < 3:    eq = min(eq, 30)
    elif years < 5:    eq = min(eq, 50)
    elif years < 7:    eq = min(eq, 70)
    elif years < 10:   eq = min(eq, 85)
    gold = round(eq * 0.10)                 # ~10% of the equity sleeve, diversifier
    gold = min(gold, 10)
    eq = eq - gold if eq - gold >= 0 else eq
    debt = 100 - eq - gold
    return dict(equity=int(round(eq)), debt=int(round(debt)), gold=int(round(gold)))


def blended_return(alloc: dict, exp=(("equity", 12.5), ("debt", 7.0), ("gold", 9.0))) -> float:
    """Expected nominal return of an allocation, weighted by asset class."""
    exp = dict(exp)
    return round(sum(alloc.get(k, 0) / 100.0 * exp[k] for k in exp), 2)


# --- 3. Goal math ---------------------------------------------------------
def future_value_sip(monthly: float, years: float, annual_return: float) -> float:
    """FV of a monthly SIP compounded monthly."""
    n = int(round(years * 12)); r = annual_return / 100.0 / 12.0
    if r == 0:
        return monthly * n
    return monthly * (((1 + r) ** n - 1) / r) * (1 + r)


def future_value_lumpsum(amount: float, years: float, annual_return: float) -> float:
    return amount * (1 + annual_return / 100.0) ** years


def required_sip(target: float, years: float, annual_return: float,
                 existing_lumpsum: float = 0.0) -> float:
    """Monthly SIP needed to reach `target` (net of an existing lumpsum's growth)."""
    fv_lump = future_value_lumpsum(existing_lumpsum, years, annual_return)
    need = max(target - fv_lump, 0.0)
    n = int(round(years * 12)); r = annual_return / 100.0 / 12.0
    if need <= 0:
        return 0.0
    if r == 0:
        return need / n
    return need / ((((1 + r) ** n - 1) / r) * (1 + r))


def inflate(amount_today: float, years: float, inflation: float = 6.0) -> float:
    """Today's cost grown to a future cost (so goals are in future rupees)."""
    return amount_today * (1 + inflation / 100.0) ** years


def plan_goal(target_today: float, years: float, band: str,
              existing_lumpsum: float = 0.0, inflation: float = 6.0) -> dict:
    """End-to-end goal plan: inflate target, allocate, size the SIP."""
    alloc = allocation(band, years)
    ret = blended_return(alloc)
    target_future = round(inflate(target_today, years, inflation))
    sip = round(required_sip(target_future, years, ret, existing_lumpsum))
    proj_sip = round(future_value_sip(sip, years, ret))
    proj_lump = round(future_value_lumpsum(existing_lumpsum, years, ret))
    return dict(
        target_today=round(target_today), target_future=target_future,
        years=years, inflation=inflation, band=band, allocation=alloc,
        expected_return=ret, existing_lumpsum=round(existing_lumpsum),
        required_sip=sip, projected_corpus=proj_sip + proj_lump,
        total_invested=round(sip * years * 12 + existing_lumpsum),
    )


# --- 4. Auto-calculated goals --------------------------------------------
def emergency_fund(monthly_expense: float, months: int = 6) -> dict:
    """
    Emergency corpus = `months` of expenses, parked in liquid/ultra-short debt.
    Immediate horizon, so allocation is ~all debt (capital safety over growth).
    """
    target = monthly_expense * months
    return dict(goal="Emergency fund", target_today=round(target), months=months,
                monthly_expense=round(monthly_expense), years=0.5,
                allocation=dict(equity=0, debt=100, gold=0),
                where="Liquid / ultra-short debt fund — instant access, no market risk.",
                note=f"{months} months of expenses. Build it before investing for long-term goals.")


def retirement_corpus(current_age: int, retire_age: int, life_expectancy: int,
                      monthly_expense_today: float, inflation: float = 6.0,
                      post_ret_return: float = 8.0) -> dict:
    """
    Expense-replacement retirement planning.

      years_to_retire   = retire_age - current_age           (accumulation)
      retirement_years  = life_expectancy - retire_age       (decumulation)
      expense @retire   = today's expense grown by inflation
      corpus @retire    = PV (at retirement) of an inflation-growing expense
                          stream for retirement_years, discounted at the
                          post-retirement return (growing-annuity).

    Returns the corpus target (future rupees at retirement) and the first-year
    monthly withdrawal it must fund. Pair with required_sip() for the SIP.
    """
    yrs_acc = max(retire_age - current_age, 0)
    yrs_dec = max(life_expectancy - retire_age, 1)
    monthly_at_retire = monthly_expense_today * (1 + inflation / 100) ** yrs_acc
    annual_at_retire = monthly_at_retire * 12
    r, g = post_ret_return / 100, inflation / 100
    if abs(r - g) < 1e-9:
        corpus = annual_at_retire * yrs_dec / (1 + r)
    else:
        corpus = annual_at_retire / (r - g) * (1 - ((1 + g) / (1 + r)) ** yrs_dec)
    return dict(goal="Retirement", current_age=current_age, retire_age=retire_age,
                life_expectancy=life_expectancy, years_to_retire=yrs_acc,
                retirement_years=yrs_dec, inflation=inflation,
                post_ret_return=post_ret_return,
                monthly_expense_today=round(monthly_expense_today),
                monthly_expense_at_retire=round(monthly_at_retire),
                corpus_at_retirement=round(corpus),
                first_year_withdrawal_monthly=round(monthly_at_retire))


def model_table() -> dict:
    """Pre-computed band × horizon allocation grid for the HTML to embed."""
    horizons = [1, 3, 5, 7, 10, 15, 20]
    table = {}
    for band in BASE_EQUITY:
        table[band] = {str(y): {**allocation(band, y),
                                "ret": blended_return(allocation(band, y))}
                       for y in horizons}
    return dict(questionnaire=QUESTIONNAIRE, bands=[b[0] for b in BANDS],
                base_equity=BASE_EQUITY, models=table)


if __name__ == "__main__":
    if "--demo" in sys.argv:
        ans = {q["id"]: 4 for q in QUESTIONNAIRE}      # all-aggressive sample
        prof = score_profile(ans)
        plan = plan_goal(2_000_000, 15, prof["band"])
        print(json.dumps(dict(profile=prof, plan=plan), indent=2))
    else:
        print(json.dumps(model_table(), indent=2))
