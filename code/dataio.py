"""
CSV read/write helpers plus a schema-aware Table wrapper.

Everything here is standard-library only (the target environment has no pandas
and no network to install it). A `Table` couples a list of raw row-dicts with a
resolved mapping from *logical* field names (see config.SCHEMA) to the real
header strings found in the file, and exposes typed getters.
"""

from __future__ import annotations

import csv
import os
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional

from config import DATE_FMT, normalize_header


# --------------------------------------------------------------------------- #
# Low-level CSV I/O
# --------------------------------------------------------------------------- #

def read_csv(path: str) -> List[Dict[str, str]]:
    """Read a CSV into a list of dicts. Returns [] if the file is missing."""
    if not path or not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return [dict(row) for row in reader]


def read_headers(path: str) -> List[str]:
    if not path or not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        for row in reader:
            return row
    return []


def write_csv(path: str, columns: List[str], rows: Iterable[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in columns})


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #

def parse_date(value: Any) -> Optional[date]:
    """Parse a YYYY-MM-DD date; tolerate a few common variants; None if blank."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return None
    for fmt in (DATE_FMT, "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s[:10] if fmt == DATE_FMT else s, fmt).date()
        except ValueError:
            continue
    # Last resort: ISO datetime.
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return None


def parse_amount(value: Any) -> Optional[float]:
    """Parse a monetary value. Blank/None -> None (NOT zero, per spec)."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return None
    # Strip currency symbols, thousands separators, spaces.
    cleaned = []
    for ch in s:
        if ch.isdigit() or ch in ".-":
            cleaned.append(ch)
    cleaned = "".join(cleaned)
    if cleaned in ("", "-", ".", "-."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    s = str(value).strip().lower()
    if s in ("true", "yes", "y", "1", "t"):
        return True
    if s in ("false", "no", "n", "0", "f"):
        return False
    return default


def fmt_amount(value: float) -> str:
    """amount_safe_to_pay style: integer if whole, else 2dp with trailing zeros
    stripped (e.g. 603.30 -> '603.3', 737.0 -> '737', 83.05 -> '83.05')."""
    if value is None:
        return ""
    r = round(float(value) + 0.0, 2)
    if abs(r - round(r)) < 1e-9:
        return str(int(round(r)))
    return f"{r:.2f}".rstrip("0").rstrip(".")


def fmt_money2(value: float) -> str:
    """payment_plan / full-payment style: integer if whole, else exactly 2dp
    keeping trailing zeros (e.g. 620.4 -> '620.40', 25256 -> '25256')."""
    if value is None:
        return ""
    r = round(float(value) + 0.0, 2)
    if abs(r - round(r)) < 1e-9:
        return str(int(round(r)))
    return f"{r:.2f}"


_MONTHS = ["", "January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]


def fmt_human_amount(value: float, currency: str = "") -> str:
    """Explanation style: thousands-separated, 2dp only if fractional, with code."""
    if value is None:
        return ""
    r = round(float(value) + 0.0, 2)
    if abs(r - round(r)) < 1e-9:
        s = f"{int(round(r)):,}"
    else:
        s = f"{r:,.2f}"
    return (f"{currency} {s}").strip()


def fmt_human_date(d: Optional[date]) -> str:
    """'2024-06-15' -> '15 June 2024' (matches sample explanations)."""
    if not d:
        return ""
    return f"{d.day} {_MONTHS[d.month]} {d.year}"


def fmt_date(d: Optional[date]) -> str:
    return d.strftime(DATE_FMT) if d else ""


# --------------------------------------------------------------------------- #
# Schema-aware table
# --------------------------------------------------------------------------- #

class Table:
    """A list of raw rows plus a logical->actual header map for one dataset file."""

    def __init__(self, rows: List[Dict[str, str]], schema: Dict[str, List[str]]):
        self.rows = rows
        self.headers = list(rows[0].keys()) if rows else []
        self._norm_to_actual = {normalize_header(h): h for h in self.headers}
        # Resolve each logical field to a concrete header (or None).
        self.resolved: Dict[str, Optional[str]] = {}
        for logical, candidates in schema.items():
            self.resolved[logical] = self._resolve(candidates)

    def _resolve(self, candidates: List[str]) -> Optional[str]:
        for cand in candidates:
            actual = self._norm_to_actual.get(normalize_header(cand))
            if actual is not None:
                return actual
        return None

    def has(self, logical: str) -> bool:
        return self.resolved.get(logical) is not None

    def raw(self, row: Dict[str, str], logical: str) -> Any:
        col = self.resolved.get(logical)
        if col is None:
            return None
        return row.get(col)

    def get_str(self, row: Dict[str, str], logical: str, default: str = "") -> str:
        v = self.raw(row, logical)
        if v is None:
            return default
        s = str(v).strip()
        return s if s else default

    def get_amount(self, row: Dict[str, str], logical: str) -> Optional[float]:
        return parse_amount(self.raw(row, logical))

    def get_date(self, row: Dict[str, str], logical: str) -> Optional[date]:
        return parse_date(self.raw(row, logical))

    def get_bool(self, row: Dict[str, str], logical: str, default: bool = False) -> bool:
        return parse_bool(self.raw(row, logical), default)

    def get_int(self, row: Dict[str, str], logical: str) -> Optional[int]:
        a = parse_amount(self.raw(row, logical))
        return int(round(a)) if a is not None else None

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self):
        return iter(self.rows)
