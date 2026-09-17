"""Typed domain models (aligned to the real dataset schema)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Set

import config


def _split_list(s: str) -> List[str]:
    return [x.strip() for x in (s or "").replace(",", "|").split("|") if x.strip()]


@dataclass
class Profile:
    user_id: str
    home_currency: str = "USD"
    balance: float = 0.0
    minimum_balance_to_keep: float = 0.0
    priorities: str = ""
    protect: List[str] = field(default_factory=list)          # categories to protect
    willing_reduce: List[str] = field(default_factory=list)   # categories reducible
    willing_stop: List[str] = field(default_factory=list)     # categories stoppable
    payment_methods: Set[str] = field(default_factory=set)    # methods user will consider
    max_installment_months: Optional[int] = None
    raw: Dict = field(default_factory=dict)

    def accepts(self, method: str) -> bool:
        # If unspecified, accept all (never block a safe recommendation on missing data).
        return (not self.payment_methods) or (method in self.payment_methods)


@dataclass
class Event:
    event_id: str
    user_id: str
    event_type: str = ""
    description: str = ""
    category: str = ""
    direction: str = ""          # "debit" (expense) | "credit" (income)
    amount: Optional[float] = None
    currency: Optional[str] = None
    event_date: Optional[date] = None
    settlement_date: Optional[date] = None
    status: str = ""
    linked_event_id: str = ""
    flexibility: str = "fixed"
    minimum_allowed_amount: Optional[float] = None
    raw: Dict = field(default_factory=dict)

    resolved_amount: Optional[float] = None  # amount after image extraction

    @property
    def eff_amount(self) -> Optional[float]:
        return self.resolved_amount if self.resolved_amount is not None else self.amount

    def is_income(self) -> bool:
        d = (self.direction or "").lower()
        if d in ("credit", "in", "inflow", "income", "+"):
            return True
        if d in ("debit", "out", "outflow", "expense", "-"):
            return False
        t = (self.event_type or "").lower()
        return t in ("income", "salary", "refund", "credit")

    def is_stoppable(self) -> bool:
        return self.flexibility in (config.FLEX_STOPPABLE, config.FLEX_REDUCIBLE_OR_STOPPABLE)

    def is_reducible(self) -> bool:
        return self.flexibility in (config.FLEX_REDUCIBLE, config.FLEX_REDUCIBLE_OR_STOPPABLE)

    def is_flexible(self) -> bool:
        return self.flexibility != config.FLEX_FIXED


@dataclass
class Request:
    request_id: str
    user_id: str
    request_date: Optional[date] = None
    request_type: str = ""
    requested_amount: float = 0.0
    desired_completion_date: Optional[date] = None
    allows_partial_payment: bool = False
    request_text: str = ""
    raw: Dict = field(default_factory=dict)


@dataclass
class PaymentOption:
    payment_option_id: str
    request_id: str
    method: str = ""                       # full_payment | installments
    payment_amount: Optional[float] = None
    num_payments: Optional[int] = None
    first_payment_date: Optional[date] = None
    frequency_days: Optional[int] = None
    financing_fee: Optional[float] = None
    total_payable: Optional[float] = None
    raw: Dict = field(default_factory=dict)


@dataclass
class Message:
    message_id: str
    user_id: str = ""
    request_id: str = ""
    related_event_id: str = ""
    date: Optional[date] = None
    source_type: str = ""
    text: str = ""
    raw: Dict = field(default_factory=dict)


@dataclass
class ImageRef:
    image_id: str
    user_id: str = ""
    request_id: str = ""
    related_event_id: str = ""
    file: str = ""
    raw: Dict = field(default_factory=dict)
