"""
Central configuration: paths, constants, enums, and a defensive schema resolver.

The schema below reflects the REAL dataset headers (verified against the repo).
Every logical field still lists candidate header names so minor variations keep
resolving, but the first candidate is the actual column in this dataset.
"""

from __future__ import annotations

import os

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

CODE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(CODE_DIR)


def _first_existing(*candidates: str) -> str:
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return candidates[0]


DATASET_DIR = _first_existing(
    os.environ.get("BUY_OR_WAIT_DATASET", ""),
    os.path.join(REPO_ROOT, "dataset"),
    os.path.join(CODE_DIR, "dataset"),
    os.path.join(REPO_ROOT, "data"),
)
MEDIA_DIR = _first_existing(
    os.path.join(DATASET_DIR, "media", "images"),
    os.path.join(DATASET_DIR, "media"),
    os.path.join(DATASET_DIR, "images"),
)

OUTPUT_PATH = os.path.join(REPO_ROOT, "output.csv")
AMOUNTS_CACHE_PATH = os.environ.get(
    "BUY_OR_WAIT_CACHE", os.path.join(CODE_DIR, "amounts_cache.json"))
MESSAGES_CACHE_PATH = os.path.join(CODE_DIR, "messages_cache.json")
EVAL_DIR = os.path.join(CODE_DIR, "evaluation")
USAGE_REPORT_PATH = os.path.join(EVAL_DIR, "usage_report.md")

FILES = {
    "requests": "requests.csv",
    "sample_requests": "sample_requests.csv",
    "profiles": "financial_profiles.csv",
    "events": "financial_events.csv",
    "exchange_rates": "exchange_rates.csv",
    "payment_options": "request_payment_options.csv",
    "messages": "messages.csv",
    "images": "images.csv",
    "output_template": "output.csv",
}

# --------------------------------------------------------------------------- #
# Core constants
# --------------------------------------------------------------------------- #

FORECAST_HORIZON_DAYS = 90
DATE_FMT = "%Y-%m-%d"
EPS = 1e-6

# Status semantics (financial_events.status).
STATUS_IGNORE = {"cancelled", "canceled", "failed", "declined", "reversed", "void"}
STATUS_PENDING = {"pending", "processing", "authorized", "hold"}
STATUS_FUTURE_CONFIRMED = {"scheduled"}
STATUS_SETTLED = {"settled", "completed", "posted", "cleared", "paid"}

# Flexibility values (financial_events.flexibility).
FLEX_FIXED = "fixed"
FLEX_STOPPABLE = "stoppable"
FLEX_REDUCIBLE = "reducible"
FLEX_REDUCIBLE_OR_STOPPABLE = "reducible_or_stoppable"

# --------------------------------------------------------------------------- #
# Output enums
# --------------------------------------------------------------------------- #

class Status:
    AFFORDABLE_NOW = "affordable_now"
    AFFORDABLE_WITH_PLAN = "affordable_with_plan"
    AFFORDABLE_LATER = "affordable_later"
    NOT_AFFORDABLE = "not_affordable"


class Method:
    FULL = "full_payment"
    PARTIAL = "partial_payment"
    INSTALLMENTS = "installments"
    WAIT = "wait"
    NOT_RECOMMENDED = "not_recommended"


NONE_TOKEN = "none"

OUTPUT_COLUMNS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]

# --------------------------------------------------------------------------- #
# Schema: logical field -> candidate header names
# --------------------------------------------------------------------------- #

SCHEMA = {
    "requests": {
        "request_id": ["request_id"],
        "user_id": ["user_id"],
        "request_date": ["request_date"],
        "request_type": ["request_type"],
        "requested_amount": ["requested_amount"],
        "desired_completion_date": ["desired_completion_date"],
        "allows_partial_payment": ["allows_partial_payment"],
        "request_text": ["request_text"],
    },
    "profiles": {
        "user_id": ["user_id"],
        "home_currency": ["home_currency"],
        "balance": ["current_available_balance", "current_balance", "balance"],
        "minimum_balance_to_keep": ["minimum_balance_to_keep"],
        "priorities": ["financial_priorities", "priorities"],
        "protect": ["expense_categories_to_protect"],
        "willing_reduce": ["expense_categories_user_is_willing_to_reduce"],
        "willing_stop": ["expense_categories_user_is_willing_to_stop"],
        "payment_methods": ["payment_methods_user_will_consider", "payment_methods"],
        "max_installment_months": ["max_installment_months"],
    },
    "events": {
        "event_id": ["event_id"],
        "user_id": ["user_id"],
        "event_type": ["event_type", "type"],
        "description": ["description", "label"],
        "category": ["category"],
        "direction": ["direction"],
        "amount": ["amount"],
        "currency": ["currency"],
        "event_date": ["event_date", "date"],
        "settlement_date": ["settlement_date"],
        "status": ["status"],
        "linked_event_id": ["linked_event_id"],
        "flexibility": ["flexibility"],
        "minimum_allowed_amount": ["minimum_allowed_amount"],
    },
    "exchange_rates": {
        "date": ["rate_date", "date"],
        "from_currency": ["from_currency", "from", "base"],
        "to_currency": ["to_currency", "to", "quote"],
        "rate": ["rate"],
    },
    "payment_options": {
        "payment_option_id": ["payment_option_id"],
        "request_id": ["request_id"],
        "method": ["payment_method", "method"],
        "payment_amount": ["payment_amount", "installment_amount"],
        "num_payments": ["number_of_payments", "num_payments"],
        "first_payment_date": ["first_payment_date"],
        "frequency_days": ["payment_frequency_days"],
        "financing_fee": ["financing_fee"],
        "total_payable": ["total_payable_amount", "total_payable"],
    },
    "messages": {
        "message_id": ["message_id"],
        "user_id": ["user_id"],
        "request_id": ["request_id"],
        "related_event_id": ["related_event_id"],
        "date": ["sent_at", "date"],
        "source_type": ["source_type"],
        "text": ["message_text", "text"],
    },
    "images": {
        "image_id": ["image_id"],
        "user_id": ["user_id"],
        "request_id": ["request_id"],
        "related_event_id": ["related_event_id"],
        "file": ["file", "filename", "path", "image_path"],
    },
}


def normalize_header(h: str) -> str:
    return (h or "").strip().lower().replace("-", "_").replace(" ", "_")
