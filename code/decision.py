"""
Decision engine: request + forecast context + persona -> the seven outputs.

Flow (derived from the solved sample_requests):
  A. Full payment safe today AND user accepts full_payment      -> affordable_now
  B. Some no-cut plan (installments / partial) completes by the  -> affordable_with_plan
     deadline safely                                              (ranked by 6 tie-breakers)
  C. A spending change (stop/reduce a flexible stream the user is -> affordable_with_plan
     willing to touch) makes a completing plan safe               (+ spending_changes_needed)
  D. The full amount only becomes safe on a later date -> wait   -> affordable_later
  E. Nothing completes safely within 90 days                     -> not_affordable

`amount_safe_to_pay` and `earliest_date_for_full_payment` are always computed on
the no-change base forecast; earliest is BLANK when the full amount never fits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import combinations
from typing import Dict, List, Optional, Tuple

import config
from config import Method, Status, NONE_TOKEN
from dataio import fmt_amount, fmt_date, fmt_money2, fmt_human_amount, fmt_human_date
from forecast import Forecast, ForecastContext, RecurringStream
from models import PaymentOption, Profile, Request
from personas import Persona


@dataclass
class Plan:
    method: str
    payments: List[Tuple[date, float]] = field(default_factory=list)
    overrides: Dict[str, Optional[float]] = field(default_factory=dict)
    option_id: str = ""
    total_paid: float = 0.0
    completes: bool = False
    by_deadline: bool = False
    safe: bool = False

    @property
    def start_date(self) -> Optional[date]:
        return min((d for d, _ in self.payments), default=None)

    @property
    def num_payments(self) -> int:
        return len(self.payments)

    @property
    def num_cuts(self) -> int:
        return len(self.overrides)


class DecisionEngine:
    def __init__(self, store, converter):
        self.store = store
        self.converter = converter
        self.last: Dict = {}  # details of the most recent decision (for reporting)

    # --------------------------------------------------------------- public
    def decide(self, req: Request, profile: Profile, ctx: ForecastContext,
               persona: Persona) -> Dict:
        start = req.request_date or date.today()
        horizon = config.FORECAST_HORIZON_DAYS
        deadline = req.desired_completion_date or (start + timedelta(days=horizon))
        requested = float(req.requested_amount or 0.0)
        home = profile.home_currency

        # Two forecasts:
        #  * `cons` reserves a recurring debit that lands on the request date, and
        #    backs the conservative measures amount_safe_to_pay and earliest_date.
        #  * `strict` projects only strictly-future flows and backs plan-safety
        #    checks, which matched the solved samples best.
        cons = ctx.forecast(reserve_start_expenses=True)
        base = ctx.forecast()
        safe_today = max(0.0, min(requested, cons.headroom_today()))
        # Plan/branch logic uses the strict earliest; the *reported* earliest date
        # in with-plan cases uses the conservative forecast (matches samples).
        earliest_full = self._earliest_full_date(base, start, horizon, requested)
        earliest_cons = self._earliest_full_date(cons, start, horizon, requested)

        if requested <= config.EPS:
            return self._emit(req, safe_today, Status.AFFORDABLE_NOW, Method.FULL,
                              [(start, 0.0)], earliest_full, {},
                              "No payment required.")

        accepts_full = profile.accepts(Method.FULL)
        accepts_inst = profile.accepts(Method.INSTALLMENTS)
        accepts_partial = profile.accepts(Method.PARTIAL)

        # A) full today, no changes. Judged on the conservative forecast so a
        # bill due on the request date can force a plan/cut instead of a same-day
        # full payment.
        if accepts_full and cons.is_safe([(start, requested)]):
            expl = (f"Pay {fmt_human_amount(requested, home)} today. This leaves at "
                    f"least {fmt_human_amount(profile.minimum_balance_to_keep, home)} "
                    f"available over the next 90 days.")
            return self._emit(req, safe_today, Status.AFFORDABLE_NOW, Method.FULL,
                              [(start, requested)], earliest_full, {}, expl)

        # B) no-cut completing plans (installments / partial).
        plans = self._candidates(req, profile, base, start, deadline, horizon,
                                  requested, accepts_inst, overrides={})
        if accepts_partial:
            # Partial uses the conservative forecast: safe_today is conservative,
            # so the remainder's safe date must be too (else both land today).
            pp = self._partial_plan(req, cons, start, deadline, requested,
                                    safe_today, earliest_cons)
            if pp:
                plans.append(pp)
        completing = [p for p in plans if p.safe and p.completes and p.by_deadline]
        if completing:
            best = min(completing, key=self._rank_key)
            return self._emit_plan(req, profile, safe_today, best, earliest_cons)

        # C) spending-change-enabled plans (full-today or installments after cuts).
        cut_completing: List[Plan] = []
        for override in self._cut_options(ctx, profile):
            cf = ctx.forecast(override)
            cut_plans: List[Plan] = []
            if accepts_full and cf.is_safe([(start, requested)]):
                cut_plans.append(Plan(Method.FULL, [(start, requested)], overrides=override,
                                      total_paid=requested, completes=True,
                                      by_deadline=True, safe=True))
            cut_plans += self._candidates(req, profile, cf, start, deadline, horizon,
                                          requested, accepts_inst, overrides=override)
            cut_completing.extend(
                p for p in cut_plans if p.safe and p.completes and p.by_deadline
            )
        if cut_completing:
            # Prefer a no-cut wait that still meets the deadline over cutting
            # (spec ranks "avoid spending changes" above using cuts). Gate on the
            # conservative earliest so cases whose safe full date is only after the
            # deadline (which genuinely need a cut) still cut.
            if accepts_full and earliest_cons is not None and earliest_cons <= deadline:
                e_d = earliest_cons
                expl = (f"Pay {fmt_human_amount(requested, home)} in full on "
                        f"{fmt_human_date(e_d)}. Paying earlier would take the balance "
                        f"below the {fmt_human_amount(profile.minimum_balance_to_keep, home)} minimum.")
                return self._emit(req, safe_today, Status.AFFORDABLE_LATER, Method.WAIT,
                                  [(e_d, requested)], e_d, {}, expl)
            # Compare every allowed cut combination globally.  Returning the
            # first viable prefix makes the result depend on savings sort order
            # instead of the specification's plan ranking rules.
            best = min(cut_completing, key=self._rank_key)
            return self._emit_plan(req, profile, safe_today, best, earliest_cons)

        # D) wait and pay full later. Enter on the strict earliest (keeps the
        # status decision stable) but report/pay on the conservative earliest.
        if accepts_full and earliest_full is not None:
            e_d = earliest_cons if earliest_cons is not None else earliest_full
            expl = (f"Pay {fmt_human_amount(requested, home)} in full on "
                    f"{fmt_human_date(e_d)}. Paying earlier would take the "
                    f"balance below the {fmt_human_amount(profile.minimum_balance_to_keep, home)} "
                    f"minimum.")
            return self._emit(req, safe_today, Status.AFFORDABLE_LATER, Method.WAIT,
                              [(e_d, requested)], e_d, {}, expl)

        # E) not affordable.
        expl = (f"Do not proceed with the {fmt_human_amount(requested, home)} request. "
                f"Although {fmt_human_amount(safe_today, home)} is available today, the full "
                f"amount cannot be completed safely within 90 days.")
        return self._emit(req, safe_today, Status.NOT_AFFORDABLE, Method.NOT_RECOMMENDED,
                          [], None, {}, expl)

    # ----------------------------------------------------------- candidates
    def _candidates(self, req, profile, forecast: Forecast, start, deadline, horizon,
                    requested, accepts_inst, overrides) -> List[Plan]:
        """Installment plans for a given forecast (must match a supplied option)."""
        plans: List[Plan] = []
        if accepts_inst:
            for opt in self.store.options_for(req.request_id):
                if opt.method != Method.INSTALLMENTS:
                    continue
                if not self._within_installment_limit(opt, profile):
                    continue
                sched, total = self._option_schedule(opt, start, profile)
                if not sched:
                    continue
                last = max(d for d, _ in sched)
                plans.append(Plan(
                    Method.INSTALLMENTS, sched, overrides=dict(overrides),
                    option_id=opt.payment_option_id, total_paid=total,
                    completes=(total + config.EPS) >= requested,
                    by_deadline=(last <= deadline),
                    safe=forecast.is_safe(sched)))
        return plans

    def _partial_plan(self, req, base: Forecast, start, deadline, requested,
                      safe_today, earliest_full) -> Optional[Plan]:
        """Spec §6.2: exactly two payments -- safe_today on request_date, then the
        remainder on earliest_date_for_full_payment (<= deadline)."""
        if not req.allows_partial_payment:
            return None
        if not (0 < safe_today < requested - config.EPS):
            return None
        if earliest_full is None or earliest_full > deadline:
            return None
        remainder = requested - safe_today
        pays = [(start, safe_today), (earliest_full, remainder)]
        return Plan(Method.PARTIAL, pays, total_paid=requested, completes=True,
                    by_deadline=True, safe=base.is_safe(pays))

    def _within_installment_limit(self, opt: PaymentOption, profile: Profile) -> bool:
        if profile.max_installment_months is None or opt.num_payments is None:
            return True
        months = opt.num_payments * (opt.frequency_days or 30) / 30.0
        return months <= profile.max_installment_months + config.EPS

    def _option_schedule(self, opt: PaymentOption, start: date, profile: Profile):
        home = profile.home_currency
        first = opt.first_payment_date or start
        n = opt.num_payments or 1
        per = opt.payment_amount
        if per is None and opt.total_payable and n:
            per = opt.total_payable / n
        if per is None:
            return [], 0.0
        step = opt.frequency_days or 30
        sched: List[Tuple[date, float]] = []
        d = first
        for _ in range(n):
            sched.append((d, per))
            d = d + timedelta(days=step)
        total = opt.total_payable if opt.total_payable else per * n
        return sched, total

    def _earliest_full_date(self, forecast: Forecast, start: date, horizon: int,
                            requested: float, already_paid_today: float = 0.0,
                            through: Optional[date] = None) -> Optional[date]:
        remainder = requested - already_paid_today
        if remainder <= config.EPS:
            return start
        for i in range(0, horizon + 1):
            d = start + timedelta(days=i)
            extra = [(d, remainder)]
            if already_paid_today > config.EPS:
                extra.append((start, already_paid_today))
            if forecast.is_safe(extra, through=through):
                return d
        return None

    # ------------------------------------------------------------ tie-break
    def _rank_key(self, p: Plan):
        return (
            0 if p.by_deadline else 1,
            0 if p.num_cuts == 0 else 1,
            round(p.total_paid, 2),
            (p.start_date - date.min).days if p.start_date else 10**9,
            p.num_payments,
            _opt_sort(p.option_id),
            p.num_cuts,
        )

    # --------------------------------------------------------------- cuts
    def _cut_options(self, ctx: ForecastContext, profile: Profile
                     ) -> List[Dict[str, Optional[float]]]:
        """Greedy prefixes of allowed spending changes (largest monthly saving first)."""
        actions: List[Tuple[float, str, Optional[float]]] = []  # (saving, key, new_amount)
        for s in ctx.streams:
            if s.is_income:
                continue
            can_stop = (s.category in profile.willing_stop) and s.flexibility in (
                config.FLEX_STOPPABLE, config.FLEX_REDUCIBLE_OR_STOPPABLE)
            can_reduce = (s.category in profile.willing_reduce) and s.flexibility in (
                config.FLEX_REDUCIBLE, config.FLEX_REDUCIBLE_OR_STOPPABLE) and s.min_allowed is not None
            if can_stop:
                actions.append((s.amount, s.key, None))
            elif can_reduce:
                actions.append((max(0.0, s.amount - s.min_allowed), s.key, s.min_allowed))
        actions.sort(key=lambda a: a[0], reverse=True)
        options: List[Dict[str, Optional[float]]] = []
        # Try every combination up to the contract's three-change limit. A
        # greedy prefix can miss a valid/superior plan when the largest saving
        # is not part of the best combination.
        for size in range(1, min(3, len(actions)) + 1):
            for combo in combinations(actions, size):
                options.append({key: new_amt for _, key, new_amt in combo})
        return options

    # ----------------------------------------------------------- emit
    def _emit_plan(self, req, profile, safe_today, plan: Plan, earliest_full) -> Dict:
        home = profile.home_currency
        minb = fmt_human_amount(profile.minimum_balance_to_keep, home)
        if plan.method == Method.INSTALLMENTS:
            per = plan.payments[0][1]
            expl = (f"Use {plan.num_payments} installments of {fmt_human_amount(per, home)}, "
                    f"starting {fmt_human_date(plan.start_date)}. This leaves at least {minb} available.")
        elif plan.method == Method.PARTIAL:
            rem = plan.payments[-1]
            expl = (f"Pay {fmt_human_amount(plan.payments[0][1], home)} now and "
                    f"{fmt_human_amount(rem[1], home)} by {fmt_human_date(rem[0])}. "
                    f"This keeps the {minb} minimum available.")
        else:  # full with cuts
            cut_phrase = self._cut_phrase(req, plan.overrides)
            expl = (f"{cut_phrase}, then pay {fmt_human_amount(req.requested_amount, home)} "
                    f"today. This leaves at least {minb} available.")
        return self._emit(req, safe_today, Status.AFFORDABLE_WITH_PLAN, plan.method,
                          plan.payments, earliest_full, plan.overrides, expl)

    def _cut_phrase(self, req, overrides: Dict[str, Optional[float]]) -> str:
        parts = []
        for key, new_amt in overrides.items():
            ev = self.store.event_by_id.get(key)
            desc = (ev.description or ev.category) if ev else key
            if new_amt is None:
                parts.append(f"Stop the {desc.lower()}")
            else:
                parts.append(f"Reduce the {desc.lower()}")
        return "; ".join(parts) if parts else "Adjust flexible spending"

    def _emit(self, req: Request, safe_today: float, status: str, method: str,
              payments: List[Tuple[date, float]], earliest_full: Optional[date],
              overrides: Dict[str, Optional[float]], explanation: str) -> Dict:
        self.last = {"payments": list(payments), "overrides": dict(overrides),
                     "status": status, "method": method, "safe_today": safe_today}
        return {
            "request_id": req.request_id,
            "amount_safe_to_pay": fmt_amount(round(safe_today, 2)),
            "affordability_status": status,
            "recommended_payment_method": method,
            "payment_plan": _fmt_plan(payments),
            # BLANK (not "none") when the full amount never becomes safe.
            "earliest_date_for_full_payment": fmt_date(earliest_full) if earliest_full else "",
            "spending_changes_needed": _fmt_changes(overrides),
            "decision_explanation": explanation.replace("\n", " ").strip(),
        }


def _opt_sort(option_id: str):
    s = str(option_id or "")
    digits = "".join(ch for ch in s if ch.isdigit())
    return (0, int(digits)) if digits else (1, s if s else "~")


def _fmt_plan(payments: List[Tuple[date, float]]) -> str:
    pays = [(d, a) for d, a in payments if a and abs(a) > config.EPS]
    if not pays:
        return NONE_TOKEN
    pays.sort(key=lambda t: t[0])
    return "|".join(f"{fmt_date(d)}:{fmt_money2(a)}" for d, a in pays)


def _fmt_changes(overrides: Dict[str, Optional[float]]) -> str:
    if not overrides:
        return NONE_TOKEN
    parts = []
    for event_id, new_amt in overrides.items():
        if new_amt is None:
            parts.append(f"stop:{event_id}")
        else:
            parts.append(f"reduce_to:{event_id}:{fmt_money2(new_amt)}")
    return "|".join(parts)
