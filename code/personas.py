"""
Financial-persona engine (advisory personalization layer).

Computes an 11-signal "financial fingerprint" from the inferred recurring
streams (ability) and the profile's category/preference fields (attitude). It
enriches explanations and can break ties the spec leaves open, but never
overrides the scored safety-check math or the fixed tie-breaker order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import config
from forecast import ForecastContext, RecurringStream
from models import Profile, Request

_MONTHLY_FACTOR = {
    "daily": 30.0, "weekly": 52 / 12.0, "biweekly": 26 / 12.0,
    "monthly": 1.0, "quarterly": 1 / 3.0, "yearly": 1 / 12.0,
}

_NEED_KW = ("rent", "medical", "hospital", "repair", "emergency", "insurance",
            "tuition", "education", "utility", "loan", "debt", "grocery")
_WANT_KW = ("laptop", "phone", "tv", "vacation", "holiday", "trip", "travel",
            "concert", "watch", "upgrade", "gaming", "camera", "luxury", "membership")


@dataclass
class Persona:
    monthly_income: float = 0.0
    monthly_essential: float = 0.0
    monthly_flexible: float = 0.0
    monthly_surplus: float = 0.0
    runway_months: float = 0.0
    dti: float = 0.0
    buffer_stance: str = "standard"
    need_vs_want: str = "discretionary"
    debt_attitude: str = "neutral"
    cut_willingness: str = "balanced"
    money_personality: str = "balanced"
    financing_headroom: str = "unknown"

    def fingerprint(self) -> str:
        return (f"{self.buffer_stance} buffer, {self.need_vs_want}, "
                f"{self.debt_attitude}, {self.runway_months:.1f}mo runway, "
                f"{self.dti*100:.0f}% DTI")


class PersonaEngine:
    def __init__(self, store):
        self.store = store

    def _monthly(self, ctx: ForecastContext, s: RecurringStream) -> float:
        factor = _MONTHLY_FACTOR.get(s.period, 1.0)
        amt = ctx.converter.convert(s.amount, s.currency or ctx.home, ctx.home, ctx.start)
        return amt * factor

    def classify_need(self, req: Request) -> str:
        text = (req.request_type + " " + req.request_text).lower()
        if any(k in text for k in ("emergency", "medical", "hospital", "repair", "urgent")):
            return "emergency"
        if any(k in text for k in _NEED_KW):
            return "essential"
        if any(k in text for k in _WANT_KW):
            return "discretionary"
        return "discretionary"

    def build(self, req: Request, profile: Profile, ctx: ForecastContext) -> Persona:
        income = essential = flexible = debt = 0.0
        for s in ctx.streams:
            m = self._monthly(ctx, s)
            if s.is_income:
                income += m
            else:
                if s.flexibility != config.FLEX_FIXED:
                    flexible += m
                else:
                    essential += m
                if s.category in ("debt_repayment", "debt", "loan"):
                    debt += m
        surplus = income - essential - flexible
        runway = (profile.balance / essential) if essential > 0 else 12.0
        runway = min(runway, 12.0)
        dti = (debt / income) if income > 0 else 0.0

        months_buffer = (profile.minimum_balance_to_keep / essential) if essential > 0 else 0.0
        if months_buffer >= 6:
            buffer_stance = "conservative"
        elif months_buffer <= 2:
            buffer_stance = "aggressive"
        else:
            buffer_stance = "standard"

        methods = profile.payment_methods
        if methods and "installments" not in methods and "partial_payment" not in methods:
            debt_attitude = "debt_averse"
        elif "installments" in methods:
            debt_attitude = "leverage_friendly"
        else:
            debt_attitude = "neutral"

        if profile.willing_stop or profile.willing_reduce:
            cut_willingness = "frugal"
        elif profile.protect and not (profile.willing_stop or profile.willing_reduce):
            cut_willingness = "protective"
        else:
            cut_willingness = "balanced"

        if buffer_stance == "conservative" and debt_attitude == "debt_averse":
            personality = "saver"
        elif surplus < 0:
            personality = "spender"
        elif cut_willingness == "frugal":
            personality = "planner"
        else:
            personality = "balanced"

        opts = self.store.options_for(req.request_id)
        headroom = "none" if not opts else ("tight" if dti > 0.43 else "ok")

        return Persona(
            monthly_income=income, monthly_essential=essential,
            monthly_flexible=flexible, monthly_surplus=surplus,
            runway_months=runway, dti=dti, buffer_stance=buffer_stance,
            need_vs_want=self.classify_need(req), debt_attitude=debt_attitude,
            cut_willingness=cut_willingness, money_personality=personality,
            financing_headroom=headroom,
        )
