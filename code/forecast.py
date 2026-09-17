"""
90-day balance forecast engine (recurrence-inference model).

The dataset has no `frequency` column -- events are dated historical
transactions. So we LEARN each user's recurring cash-flow streams from history
(grouping by category + direction, estimating a period and a representative
amount) and PROJECT them forward across the 90-day horizon from the request
date. The starting balance (`current_available_balance`) is taken as-of the
request date, so only strictly-future flows are projected.

Filtering follows the spec: cancelled/failed and reversal/refund records and
pending credits are excluded; `scheduled` future items are confirmed and kept.
A `ForecastContext` can rebuild the forecast with spending-change overrides
(stop a stream, or reduce it to a floor) keyed by the stream's representative
event_id -- which is exactly what `spending_changes_needed` references.
"""

from __future__ import annotations

import calendar
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median, mean, pstdev
from typing import Dict, List, Optional, Tuple

import config
from models import Event, Profile


# --------------------------------------------------------------------------- #
# Date helpers
# --------------------------------------------------------------------------- #

def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


def _canonical_period(gap_days: float) -> str:
    if gap_days <= 3:
        return "daily"
    if gap_days <= 10:
        return "weekly"
    if gap_days <= 20:
        return "biweekly"
    if gap_days <= 45:
        return "monthly"
    if gap_days <= 135:
        return "quarterly"
    if gap_days <= 400:
        return "yearly"
    return "monthly"


def _step(d: date, period: str) -> date:
    if period == "monthly":
        return _add_months(d, 1)
    if period == "quarterly":
        return _add_months(d, 3)
    if period == "yearly":
        return _add_months(d, 12)
    step = {"daily": 1, "weekly": 7, "biweekly": 14}.get(period, 30)
    return d + timedelta(days=step)


def _occurrences(anchor: date, period: str, start: date, end: date,
                 inclusive_start: bool = False) -> List[date]:
    """Occurrences through `end`. When `inclusive_start`, an occurrence landing on
    `start` is kept (an unpaid expense due on the request date must be reserved);
    otherwise occurrences must be strictly after `start` (used for income, so a
    paycheck is never assumed to have already arrived on the request date)."""
    if anchor is None:
        return []
    out: List[date] = []
    d = anchor
    guard = 0
    limit = start if inclusive_start else start
    while d < limit and guard < 100000:
        d = _step(d, period); guard += 1
    if not inclusive_start:
        while d <= start and guard < 100000:
            d = _step(d, period); guard += 1
    while d <= end and guard < 200000:
        out.append(d)
        d = _step(d, period); guard += 1
    return out


# --------------------------------------------------------------------------- #
# Recurring stream
# --------------------------------------------------------------------------- #

@dataclass
class RecurringStream:
    key: str                 # representative event_id (referenced by spending changes)
    category: str
    is_income: bool
    period: str
    amount: float            # per-occurrence amount (in `currency`)
    currency: str
    last_date: date
    flexibility: str = "fixed"
    min_allowed: Optional[float] = None

    def occurrences(self, start: date, end: date, inclusive_start: bool = False) -> List[date]:
        return _occurrences(self.last_date, self.period, start, end, inclusive_start)


def _usable_for_inference(ev: Event) -> bool:
    if ev.status in config.STATUS_IGNORE:
        return False
    if ev.status in config.STATUS_PENDING:
        return False
    if ev.linked_event_id:            # settlement/reversal of another record
        return False
    if (ev.event_type or "").lower() in ("refund", "reversal", "adjustment"):
        return False
    if ev.event_date is None or ev.eff_amount is None:
        return False
    return True


def learn_streams(events: List[Event]) -> List[RecurringStream]:
    """Infer recurring streams grouped by (category, direction)."""
    groups: Dict[Tuple[str, bool], List[Event]] = {}
    for ev in events:
        if not _usable_for_inference(ev):
            continue
        groups.setdefault((ev.category.lower(), ev.is_income()), []).append(ev)

    streams: List[RecurringStream] = []
    for (category, is_income), evs in groups.items():
        evs = [e for e in evs if e.event_date and e.eff_amount is not None]
        if len(evs) < 2:
            continue  # not enough evidence to treat as recurring
        evs.sort(key=lambda e: e.event_date)

        # For income, keep only regular salary paydays and drop irregular extras
        # (commissions/bonuses). A salary payday recurs with a CONSISTENT amount
        # (low coefficient of variation); commissions vary widely, and one-off
        # bonuses appear only once. This preserves genuine multi-payday salaries
        # (e.g. paid on the 7th and 20th) while removing variable commissions
        # that would otherwise corrupt the inferred cadence and amount.
        evs_used = evs
        if is_income and len(evs) >= 3:
            byday: Dict[int, List[float]] = {}
            for e in evs:
                byday.setdefault(e.event_date.day, []).append(abs(e.eff_amount))
            keep_days = set()
            for d, v in byday.items():
                if len(v) < 2:
                    continue
                m = mean(v)
                cv = (pstdev(v) / m) if m else 0.0
                if cv <= 0.35:            # consistent -> a real salary payline
                    keep_days.add(d)
            if keep_days:
                payline = [e for e in evs if e.event_date.day in keep_days]
                if len(payline) >= 2:
                    evs_used = payline

        gaps = [(evs_used[i].event_date - evs_used[i - 1].event_date).days
                for i in range(1, len(evs_used)) if (evs_used[i].event_date - evs_used[i - 1].event_date).days > 0]
        if not gaps:
            continue
        period = _canonical_period(median(gaps))
        recent = evs_used[-6:]
        if is_income:
            amount = abs(recent[-1].eff_amount)      # latest confirmed pay (captures raises)
        else:
            amount = median([abs(e.eff_amount) for e in recent])
        rep = evs_used[-1]
        # Phase monthly-ish streams on the MODAL day-of-month so one off-cycle
        # occurrence (e.g. a payslip dated month-end) doesn't skew projections.
        anchor = rep.event_date
        if period in ("monthly", "quarterly", "yearly"):
            modal_day = Counter(e.event_date.day for e in evs_used).most_common(1)[0][0]
            last_dom = calendar.monthrange(anchor.year, anchor.month)[1]
            anchor = date(anchor.year, anchor.month, min(modal_day, last_dom))
        streams.append(RecurringStream(
            key=rep.event_id,
            category=category,
            is_income=is_income,
            period=period,
            amount=amount,
            currency=rep.currency or "",
            last_date=anchor,
            flexibility=rep.flexibility,
            min_allowed=rep.minimum_allowed_amount,
        ))
    return streams


# --------------------------------------------------------------------------- #
# Forecast object
# --------------------------------------------------------------------------- #

class Forecast:
    def __init__(self, start_balance: float, minimum_balance: float,
                 cashflows: List[Tuple[date, float]], start: date, horizon: int):
        self.start_balance = start_balance
        self.minimum_balance = minimum_balance
        self.start = start
        self.horizon = horizon
        self._by_date: Dict[date, float] = {}
        for d, amt in cashflows:
            self._by_date[d] = self._by_date.get(d, 0.0) + amt

    def min_balance(self, extra_outflows: Optional[List[Tuple[date, float]]] = None,
                    through: Optional[date] = None) -> Tuple[float, Optional[date]]:
        """Minimum end-of-day balance from the start through `through` (defaults to
        the full 90-day horizon). `through` lets plan/earliest checks require the
        minimum only up to the completion deadline."""
        deltas: Dict[date, float] = dict(self._by_date)
        if extra_outflows:
            for d, amt in extra_outflows:
                deltas[d] = deltas.get(d, 0.0) - abs(amt)
        end = self.start + timedelta(days=self.horizon)
        if through is not None:
            end = min(end, max(through, self.start))
        bal = self.start_balance
        min_bal, min_day = bal, self.start
        day = self.start
        while day <= end:
            bal += deltas.get(day, 0.0)
            if bal < min_bal - config.EPS:
                min_bal, min_day = bal, day
            day += timedelta(days=1)
        return min_bal, min_day

    def is_safe(self, extra_outflows: Optional[List[Tuple[date, float]]] = None,
                through: Optional[date] = None) -> bool:
        min_bal, _ = self.min_balance(extra_outflows, through=through)
        return min_bal >= self.minimum_balance - config.EPS

    def headroom_today(self, through: Optional[date] = None) -> float:
        min_bal, _ = self.min_balance(through=through)
        return max(0.0, min_bal - self.minimum_balance)

    def series(self, extra_outflows: Optional[List[Tuple[date, float]]] = None
               ) -> List[Tuple[date, float]]:
        """End-of-day balance for each day across the horizon (for charts)."""
        deltas: Dict[date, float] = dict(self._by_date)
        if extra_outflows:
            for d, amt in extra_outflows:
                deltas[d] = deltas.get(d, 0.0) - abs(amt)
        out: List[Tuple[date, float]] = []
        bal = self.start_balance
        for i in range(0, self.horizon + 1):
            day = self.start + timedelta(days=i)
            bal += deltas.get(day, 0.0)
            out.append((day, bal))
        return out


# --------------------------------------------------------------------------- #
# Forecast context (streams + overrides)
# --------------------------------------------------------------------------- #

_INCOME_CATS = ("salary", "income", "payroll", "wage", "wages")


class ForecastContext:
    def __init__(self, profile: Profile, events: List[Event], converter,
                 start: date, horizon: int = config.FORECAST_HORIZON_DAYS,
                 income_update=None, project_income: bool = True):
        self.profile = profile
        self.converter = converter
        self.start = start
        self.horizon = horizon
        self.home = (profile.home_currency or "USD").upper()
        self.end = start + timedelta(days=horizon)
        self.streams = learn_streams(events)
        # Recurring income is projected from history by default. It is suppressed
        # only when a message says the income is pending/uncommitted (e.g. a gig
        # payout not yet settled) and nothing confirms it -- we never invent
        # income the evidence marks as not-yet-received. Expenses always project.
        self.project_income = project_income
        self.stream_by_key: Dict[str, RecurringStream] = {s.key: s for s in self.streams}
        # (effective_date, amount, currency) from a confirmed payroll message.
        self.income_update = income_update
        self._income_keys = self._primary_income_keys()
        self._one_offs = self._future_one_offs(events)

    def _has_future_income(self, events: List[Event]) -> bool:
        for ev in events:
            if not ev.is_income():
                continue
            if ev.status in config.STATUS_IGNORE or ev.status in config.STATUS_PENDING:
                continue
            if ev.event_date and ev.event_date > self.start:
                return True   # a scheduled/confirmed future paycheck exists
        return False

    def _primary_income_keys(self):
        """Income streams a payroll message applies to (salary-like, or the
        single largest income stream if none are explicitly salary-tagged)."""
        income = [s for s in self.streams if s.is_income]
        keyed = {s.key for s in income if any(c in s.category for c in _INCOME_CATS)}
        if keyed:
            return keyed
        if income:
            biggest = max(income, key=lambda s: s.amount)
            return {biggest.key}
        return set()

    def _future_one_offs(self, events: List[Event]) -> List[Tuple[date, float]]:
        """Confirmed future events not covered by any recurring stream."""
        stream_cats = {(s.category, s.is_income) for s in self.streams}
        flows: List[Tuple[date, float]] = []
        for ev in events:
            if ev.event_date is None or ev.eff_amount is None:
                continue
            if not (self.start < ev.event_date <= self.end):
                continue
            if ev.status in config.STATUS_IGNORE:
                continue
            if ev.status in config.STATUS_PENDING and ev.is_income():
                continue  # ignore pending credits
            if ev.linked_event_id or (ev.event_type or "").lower() in ("refund", "reversal"):
                continue
            # A pending debit is a specific upcoming charge -> reserve it even if
            # its category also recurs (spec: "Reserve pending debits"). For a
            # scheduled/settled event in a recurring category, skip it only when
            # the inferred stream actually has an occurrence on that date. A
            # different date is a confirmed one-off and must not disappear just
            # because the category happens to recur.
            is_pending_debit = ev.status in config.STATUS_PENDING and not ev.is_income()
            if not is_pending_debit and (ev.category.lower(), ev.is_income()) in stream_cats:
                covered = any(
                    s.category == ev.category.lower() and s.is_income == ev.is_income()
                    and ev.event_date in s.occurrences(self.start, self.end)
                    for s in self.streams
                )
                if covered:
                    continue
            sign = 1.0 if ev.is_income() else -1.0
            conv = self.converter.convert(abs(ev.eff_amount), ev.currency or self.home,
                                          self.home, ev.event_date)
            flows.append((ev.event_date, sign * conv))
        return flows

    def forecast(self, overrides: Optional[Dict[str, Optional[float]]] = None,
                 reserve_start_expenses: bool = False) -> Forecast:
        """Build a Forecast. `overrides` maps stream key -> new per-occurrence
        amount (or None to stop the stream). When `reserve_start_expenses`, an
        expense whose cycle lands on the request date is reserved (used only for
        the conservative amount_safe_to_pay measure)."""
        overrides = overrides or {}
        cashflows: List[Tuple[date, float]] = list(self._one_offs)
        upd_eff = upd_amt = upd_cur = None
        upd_once = False
        if self.income_update:
            upd_eff, upd_amt, upd_cur = self.income_update[:3]
            upd_once = self.income_update[3] if len(self.income_update) > 3 else False
            upd_eff = upd_eff or self.start
        for s in self.streams:
            if s.is_income and not self.project_income:
                continue  # unconfirmed future income is not invented
            amount = s.amount
            if s.key in overrides:
                new_amt = overrides[s.key]
                if new_amt is None:
                    continue  # stopped
                amount = new_amt
            sign = 1.0 if s.is_income else -1.0
            apply_update = (upd_amt is not None) and (s.key in self._income_keys)
            inc_start = reserve_start_expenses and not s.is_income
            applied_once = False
            for d in s.occurrences(self.start, self.end, inclusive_start=inc_start):
                use_update = apply_update and d >= upd_eff and not (upd_once and applied_once)
                if use_update:
                    conv = self.converter.convert(upd_amt, upd_cur or self.home, self.home, d)
                    if upd_once:
                        applied_once = True  # a "next/temporary" change hits one cycle only
                else:
                    conv = self.converter.convert(amount, s.currency or self.home, self.home, d)
                cashflows.append((d, sign * conv))
        return Forecast(self.profile.balance, self.profile.minimum_balance_to_keep,
                        cashflows, self.start, self.horizon)
