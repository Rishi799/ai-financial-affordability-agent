# AI Financial Affordability Agent — "Buy or Wait?"

An AI-powered agent that decides whether a user can **safely afford** a requested
expense — and, if so, *how* to pay for it (pay in full, partially, in
installments, wait, or not proceed). Built for the HackerRank Orchestrate
"Buy or Wait?" challenge.

It goes well beyond "balance ≥ price": it forecasts the user's finances 90 days
ahead from recurring income/expenses, confirmed and pending payments, payment
options, and information extracted from **messages and images** — and only
recommends a plan that keeps the balance above the user's minimum cushion the
whole way.

## Approach: a hybrid engine

A **deterministic financial engine** does the maths and safety guarantees
(reproducible, no hallucination), and **AI is used only where it's genuinely
better** — reading documents and understanding language:

- **Deterministic core** — 90-day daily balance forecast, the minimum-balance
  safety check, constrained plan search, and the spec's tie-breakers.
- **Vision (Claude)** — reads payslip/bill/receipt **images** to recover amounts
  for events with a blank amount (net pay, balances, invoice totals).
- **Language** — a multilingual (English + Bahasa Indonesia) **message parser**
  that folds confirmed salary changes into the forecast and ignores pending
  bonuses/commissions.

## The 7 outputs (per request)

`amount_safe_to_pay`, `affordability_status`, `recommended_payment_method`,
`payment_plan`, `earliest_date_for_full_payment`, `spending_changes_needed`,
`decision_explanation`.

## How the forecast works

The dataset has **no frequency column**, so recurring streams are *inferred* from
transaction history (period + representative amount), with:

- income cadence taken from **consistent paydays only** (so variable commissions
  don't corrupt the base salary),
- recurrences phased on the **modal day-of-month**,
- spec-compliant filtering (ignore cancelled/failed, reversals, and pending
  credits; reserve pending debits; keep scheduled income; ignore unrealised
  investments),
- dated currency conversion.

A plan is emitted only if the simulated balance stays ≥ `minimum_balance_to_keep`
on every day of the horizon **and** completes by the deadline.

## Repository layout

```
code/
  config.py  dataio.py  models.py  data_loader.py   # data layer + schema resolver
  currency.py  forecast.py                          # dated FX + 90-day forecast/safety
  image_extraction.py  message_parser.py            # VLM + multilingual NLP
  personas.py  decision.py  agent.py                # persona layer + plan search + orchestration
  main.py  evaluate.py  report.py                   # entry point, eval workflow, dashboard
  amounts_cache.json                                # image-extracted amounts (reproducible)
  evaluation/usage_report.md                        # AI usage report
output.csv                                          # predictions for all requests
dashboard.html                                      # explainability dashboard
```

> The challenge `dataset/` (provided corpus) is intentionally **not** included.
> Place it at the repo root as `dataset/` to run.

## Run

```bash
python3 code/main.py        # reads dataset/ -> writes output.csv
python3 code/evaluate.py    # scores against the 25 solved samples
python3 code/report.py      # builds dashboard.html
```

Pure Python standard library — no third-party runtime dependencies. Optional
Claude backends (vision/LLM) activate only when `ANTHROPIC_API_KEY` is set.

## Results (25 solved samples)

recommended_payment_method **92%** · spending_changes_needed **92%** ·
affordability_status **88%** · payment_plan **88%** · earliest_date **80%** ·
amount_safe_to_pay 16% (≈48% within 10%). Output validated: 250 rows, exact
schema, deterministic.

## Security

All message/image content is treated as **untrusted data** — only numbers, dates
and intent are extracted, never instructions.
