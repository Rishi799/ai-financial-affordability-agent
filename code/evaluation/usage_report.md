# AI Usage Report — Buy or Wait?

## Summary

The final full-dataset run (`python code/main.py`, producing `output.csv` for all
250 requests) is **fully deterministic and makes zero LLM/VLM API calls at run
time**. The financial forecast, 90-day safety check, plan search, and tie-breaking
are pure standard-library Python, so the run is exactly reproducible offline and
on the grader's machine with no API keys or network.

AI was used in two ways: (1) **offline, during development**, Claude's vision read
the 16 payslip/bill/receipt images to populate a small amount cache; and (2) the
code ships **optional Claude backends** (VLM for images, LLM for messages) that
activate only when `ANTHROPIC_API_KEY` is set — they were not needed for the final
run because the deterministic paths and the cache cover the dataset.

## Final full-dataset run (what actually executes for output.csv)

| Field | Value |
|---|---|
| Model provider | none (deterministic) |
| Model name(s) | none |
| Model calls | 0 |
| Input tokens | 0 |
| Output tokens | 0 |
| Requests processed | 250 |
| Total tokens | 0 |
| Avg tokens / request | 0 |
| Estimated total cost | $0.00 |
| Estimated cost / request | $0.00 |

Message understanding for the final run used the deterministic multilingual
parser (`message_parser.py`), validated 8/8 on the provided sample messages, so
no LLM tokens were consumed.

## Offline AI assistance that produced cached inputs (not part of the scored run)

| Item | Provider / Model | Calls | Est. input tokens | Est. output tokens | Est. cost |
|---|---|---|---|---|---|
| Image amount extraction (16 images → `code/amounts_cache.json`) | Anthropic / Claude (vision, via Claude Code) | 16 | ~24,000 (≈1.5k image tokens each) | ~320 (≈20 each) | ≈ $0.30 |

Notes:
- Each blank-amount financial event linked in `images.csv` was resolved by reading
  `dataset/media/images/<image_id>.png` and extracting the single relevant total
  (net pay, balance due, or invoice total), in the event's own currency.
- Token/cost figures are estimates; image token counts depend on resolution.
- Re-running extraction programmatically is supported via
  `python code/extract_images.py` with `ANTHROPIC_API_KEY` set (Claude VLM backend).

## Reproducibility & safety

- Deterministic core → identical `output.csv` on every run.
- All message/image content is treated as untrusted data: only numbers, dates and
  intent are extracted, never instructions.
- No secrets are stored in the repo; API keys are read from environment variables
  only and are never required for the scored run.
