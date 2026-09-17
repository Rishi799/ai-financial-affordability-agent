"""Load all dataset CSVs into typed models and expose lookups (real schema)."""

from __future__ import annotations

import os
from typing import Dict, List, Optional

import config
from dataio import Table, read_csv
from models import Event, ImageRef, Message, PaymentOption, Profile, Request, _split_list


def _find_file(basename: str) -> Optional[str]:
    if not os.path.isdir(config.DATASET_DIR):
        return None
    target = basename.lower()
    for name in os.listdir(config.DATASET_DIR):
        if name.lower() == target:
            return os.path.join(config.DATASET_DIR, name)
    return None


def _load_table(key: str, schema_key: Optional[str] = None) -> Table:
    path = _find_file(config.FILES[key])
    rows = read_csv(path) if path else []
    return Table(rows, config.SCHEMA.get(schema_key or key, {}))


class DataStore:
    def __init__(self, use_sample_requests: bool = False):
        self.use_sample_requests = use_sample_requests
        self._load()
        self._index()

    def _load(self) -> None:
        self.t_requests = _load_table("requests")
        self.t_samples = _load_table("sample_requests", schema_key="requests")
        self.t_profiles = _load_table("profiles")
        self.t_events = _load_table("events")
        self.t_rates = _load_table("exchange_rates")
        self.t_options = _load_table("payment_options")
        self.t_messages = _load_table("messages")
        self.t_images = _load_table("images")

        self.profiles: Dict[str, Profile] = {}
        t = self.t_profiles
        for r in t:
            p = Profile(
                user_id=t.get_str(r, "user_id"),
                home_currency=(t.get_str(r, "home_currency", "USD") or "USD").upper(),
                balance=t.get_amount(r, "balance") or 0.0,
                minimum_balance_to_keep=t.get_amount(r, "minimum_balance_to_keep") or 0.0,
                priorities=t.get_str(r, "priorities"),
                protect=_split_list(t.get_str(r, "protect")),
                willing_reduce=_split_list(t.get_str(r, "willing_reduce")),
                willing_stop=_split_list(t.get_str(r, "willing_stop")),
                payment_methods=set(_split_list(t.get_str(r, "payment_methods"))),
                max_installment_months=t.get_int(r, "max_installment_months"),
                raw=r,
            )
            if p.user_id:
                self.profiles[p.user_id] = p

        self.events: List[Event] = []
        t = self.t_events
        for r in t:
            self.events.append(Event(
                event_id=t.get_str(r, "event_id"),
                user_id=t.get_str(r, "user_id"),
                event_type=t.get_str(r, "event_type"),
                description=t.get_str(r, "description"),
                category=t.get_str(r, "category"),
                direction=t.get_str(r, "direction"),
                amount=t.get_amount(r, "amount"),
                currency=(t.get_str(r, "currency") or None),
                event_date=t.get_date(r, "event_date"),
                settlement_date=t.get_date(r, "settlement_date"),
                status=t.get_str(r, "status").lower(),
                linked_event_id=t.get_str(r, "linked_event_id"),
                flexibility=(t.get_str(r, "flexibility", "fixed") or "fixed").lower(),
                minimum_allowed_amount=t.get_amount(r, "minimum_allowed_amount"),
                raw=r,
            ))

        self.options: List[PaymentOption] = []
        t = self.t_options
        for r in t:
            self.options.append(PaymentOption(
                payment_option_id=t.get_str(r, "payment_option_id"),
                request_id=t.get_str(r, "request_id"),
                method=t.get_str(r, "method").lower(),
                payment_amount=t.get_amount(r, "payment_amount"),
                num_payments=t.get_int(r, "num_payments"),
                first_payment_date=t.get_date(r, "first_payment_date"),
                frequency_days=t.get_int(r, "frequency_days"),
                financing_fee=t.get_amount(r, "financing_fee"),
                total_payable=t.get_amount(r, "total_payable"),
                raw=r,
            ))

        self.messages: List[Message] = []
        t = self.t_messages
        for r in t:
            self.messages.append(Message(
                message_id=t.get_str(r, "message_id"),
                user_id=t.get_str(r, "user_id"),
                request_id=t.get_str(r, "request_id"),
                related_event_id=t.get_str(r, "related_event_id"),
                date=t.get_date(r, "date"),
                source_type=t.get_str(r, "source_type"),
                text=t.get_str(r, "text"),
                raw=r,
            ))

        self.images: List[ImageRef] = []
        t = self.t_images
        for r in t:
            self.images.append(ImageRef(
                image_id=t.get_str(r, "image_id"),
                user_id=t.get_str(r, "user_id"),
                request_id=t.get_str(r, "request_id"),
                related_event_id=t.get_str(r, "related_event_id"),
                file=t.get_str(r, "file"),
                raw=r,
            ))

        self.requests = self._build_requests(
            self.t_samples if self.use_sample_requests else self.t_requests
        )

    def _build_requests(self, t: Table) -> List[Request]:
        out: List[Request] = []
        for r in t:
            out.append(Request(
                request_id=t.get_str(r, "request_id"),
                user_id=t.get_str(r, "user_id"),
                request_date=t.get_date(r, "request_date"),
                request_type=t.get_str(r, "request_type"),
                requested_amount=t.get_amount(r, "requested_amount") or 0.0,
                desired_completion_date=t.get_date(r, "desired_completion_date"),
                allows_partial_payment=t.get_bool(r, "allows_partial_payment", False),
                request_text=t.get_str(r, "request_text"),
                raw=r,
            ))
        return out

    def _index(self) -> None:
        self.events_by_user: Dict[str, List[Event]] = {}
        for ev in self.events:
            self.events_by_user.setdefault(ev.user_id, []).append(ev)
        self.event_by_id: Dict[str, Event] = {e.event_id: e for e in self.events if e.event_id}

        self.options_by_request: Dict[str, List[PaymentOption]] = {}
        for o in self.options:
            self.options_by_request.setdefault(o.request_id, []).append(o)
        for lst in self.options_by_request.values():
            lst.sort(key=lambda o: _sort_key(o.payment_option_id))

        self.messages_by_request: Dict[str, List[Message]] = {}
        self.messages_by_user: Dict[str, List[Message]] = {}
        for m in self.messages:
            if m.request_id:
                self.messages_by_request.setdefault(m.request_id, []).append(m)
            if m.user_id:
                self.messages_by_user.setdefault(m.user_id, []).append(m)

        self.images_by_event: Dict[str, List[ImageRef]] = {}
        self.images_by_request: Dict[str, List[ImageRef]] = {}
        for im in self.images:
            if im.related_event_id:
                self.images_by_event.setdefault(im.related_event_id, []).append(im)
            if im.request_id:
                self.images_by_request.setdefault(im.request_id, []).append(im)

    def profile_for(self, user_id: str) -> Optional[Profile]:
        return self.profiles.get(user_id)

    def events_for(self, user_id: str) -> List[Event]:
        return self.events_by_user.get(user_id, [])

    def options_for(self, request_id: str) -> List[PaymentOption]:
        return self.options_by_request.get(request_id, [])


def _sort_key(value: str):
    s = str(value)
    digits = "".join(ch for ch in s if ch.isdigit())
    return (0, int(digits)) if digits else (1, s)
