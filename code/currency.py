"""
Dated currency conversion backed by exchange_rates.csv.

The spec provides fixed dated rates and says live rates are not needed. We build
a lookup keyed by (from, to) with a sorted list of (date, rate), and for a
requested date pick the rate effective on-or-before it (falling back to the
nearest available date, then to an inverse pair, then identity).
"""

from __future__ import annotations

import bisect
from datetime import date
from typing import Dict, List, Optional, Tuple

from dataio import Table, parse_amount, parse_date


class CurrencyConverter:
    def __init__(self, rates_table: Table):
        # (FROM, TO) -> sorted list of (date, rate)
        self._series: Dict[Tuple[str, str], List[Tuple[date, float]]] = {}
        for row in rates_table:
            frm = (rates_table.get_str(row, "from_currency") or "").upper()
            to = (rates_table.get_str(row, "to_currency") or "").upper()
            d = rates_table.get_date(row, "date")
            rate = rates_table.get_amount(row, "rate")
            if not frm or not to or rate is None:
                continue
            self._series.setdefault((frm, to), []).append((d, rate))
        for key in self._series:
            # Put dated entries first (sorted), undated (None) last.
            self._series[key].sort(key=lambda t: (t[0] is None, t[0] or date.min))

    def _lookup(self, frm: str, to: str, on: Optional[date]) -> Optional[float]:
        series = self._series.get((frm, to))
        if not series:
            return None
        if on is None:
            return series[-1][1]
        dated = [(d, r) for d, r in series if d is not None]
        if not dated:
            return series[-1][1]
        dates = [d for d, _ in dated]
        # Effective on-or-before `on`.
        idx = bisect.bisect_right(dates, on) - 1
        if idx >= 0:
            return dated[idx][1]
        # No earlier rate: use the earliest available.
        return dated[0][1]

    def convert(self, amount: float, frm: Optional[str], to: Optional[str],
                on: Optional[date] = None) -> float:
        if amount is None:
            return 0.0
        frm = (frm or "").upper()
        to = (to or "").upper()
        if not frm or not to or frm == to:
            return amount
        direct = self._lookup(frm, to, on)
        if direct is not None:
            return amount * direct
        inverse = self._lookup(to, frm, on)
        if inverse:
            return amount / inverse
        # No rate available: assume 1:1 rather than dropping the value.
        return amount
