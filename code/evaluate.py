"""
Evaluation harness: score predictions against the solved sample_requests.csv.

Runs the agent over the sample requests, then compares each of the seven output
fields to the expected values embedded in sample_requests.csv (matched by
request_id). Reports per-field accuracy and a per-request diff so we can see
exactly where the engine diverges and tune the logic -- with NO test labels
baked into the agent itself.

Usage:  python code/evaluate.py [--verbose]
"""

from __future__ import annotations

import argparse
import os
from typing import Dict, List

import config
from agent import FinancialAgent
from data_loader import _find_file
from dataio import Table, parse_amount, read_csv
from config import OUTPUT_COLUMNS, normalize_header

# How each field is compared.
_NUMERIC = {"amount_safe_to_pay"}
_SET_FIELDS = {"payment_plan", "spending_changes_needed"}  # order-insensitive token sets
_EXACT = {"affordability_status", "recommended_payment_method",
          "earliest_date_for_full_payment"}
_SOFT = {"decision_explanation"}  # not scored for exactness


def _norm_tokens(s: str) -> set:
    s = (s or "").strip().lower()
    if s in ("", "none"):
        return set()
    return {tok.strip() for tok in s.split("|") if tok.strip()}


def _cmp(field: str, pred: str, exp: str) -> bool:
    if field in _NUMERIC:
        a, b = parse_amount(pred), parse_amount(exp)
        if a is None and b is None:
            return True
        if a is None or b is None:
            return False
        return abs(a - b) <= max(1.0, 0.01 * abs(b))  # 1 unit or 1% tolerance
    if field in _SET_FIELDS:
        return _norm_tokens(pred) == _norm_tokens(exp)
    if field in _EXACT:
        return (pred or "").strip().lower() == (exp or "").strip().lower()
    return True  # soft fields always "pass"


def evaluate(verbose: bool = False) -> Dict:
    path = _find_file(config.FILES["sample_requests"])
    if not path or not os.path.exists(path):
        print("sample_requests.csv not found; cannot evaluate.")
        return {}

    raw = read_csv(path)
    # Expected values live in columns named like the output schema.
    norm_map = {normalize_header(h): h for h in (raw[0].keys() if raw else [])}
    expected: Dict[str, Dict[str, str]] = {}
    rid_col = norm_map.get("request_id", "request_id")
    for row in raw:
        rid = str(row.get(rid_col, "")).strip()
        expected[rid] = {
            col: row.get(norm_map.get(col, col), "") for col in OUTPUT_COLUMNS
        }

    agent = FinancialAgent(use_sample_requests=True, verbose=verbose)
    preds = {r["request_id"]: r for r in agent.run()}

    scored_fields = [c for c in OUTPUT_COLUMNS if c not in _SOFT and c != "request_id"]
    field_hits = {f: 0 for f in scored_fields}
    field_total = {f: 0 for f in scored_fields}
    full_match = 0
    diffs: List[str] = []

    for rid, exp in expected.items():
        pred = preds.get(rid)
        if not pred:
            diffs.append(f"{rid}: MISSING prediction")
            continue
        row_ok = True
        row_diff = []
        for f in scored_fields:
            if not exp.get(f) and f == "earliest_date_for_full_payment":
                pass
            field_total[f] += 1
            ok = _cmp(f, pred.get(f, ""), exp.get(f, ""))
            if ok:
                field_hits[f] += 1
            else:
                row_ok = False
                row_diff.append(f"  {f}: pred={pred.get(f,'')!r} exp={exp.get(f,'')!r}")
        if row_ok:
            full_match += 1
        elif verbose:
            diffs.append(f"{rid}:\n" + "\n".join(row_diff))

    n = len(expected)
    report = ["# Evaluation report", "",
              f"Samples: {n}", f"Exact full-row matches: {full_match}/{n} "
              f"({100*full_match/max(1,n):.1f}%)", "", "## Per-field accuracy", ""]
    for f in scored_fields:
        tot = field_total[f] or 1
        report.append(f"- {f}: {field_hits[f]}/{tot} ({100*field_hits[f]/tot:.1f}%)")
    if diffs:
        report += ["", "## Divergences", "", *diffs]

    text = "\n".join(report)
    os.makedirs(config.EVAL_DIR, exist_ok=True)
    with open(os.path.join(config.EVAL_DIR, "eval_report.md"), "w", encoding="utf-8") as fh:
        fh.write(text)
    # Print the summary + all per-field lines (divergences only when verbose).
    if verbose:
        print(text)
    else:
        print("\n".join(report[:7 + len(scored_fields)]))
    return {"full_match": full_match, "n": n,
            "field_hits": field_hits, "field_total": field_total}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    evaluate(verbose=a.verbose)
