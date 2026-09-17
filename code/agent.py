"""
Agent orchestrator: wires the data, image resolution, forecast, persona and
decision layers together and produces one output row per request.
"""

from __future__ import annotations

from typing import Dict, List

from currency import CurrencyConverter
from data_loader import DataStore
from decision import DecisionEngine
from forecast import ForecastContext
from image_extraction import AmountResolver
from message_parser import MessageParser
from personas import PersonaEngine


class FinancialAgent:
    def __init__(self, use_sample_requests: bool = False, verbose: bool = False):
        self.verbose = verbose
        self.store = DataStore(use_sample_requests=use_sample_requests)
        self.resolver = AmountResolver(self.store, verbose=verbose)
        self.resolver.resolve_all()  # fill event.resolved_amount (image/cache/VLM)
        self.converter = CurrencyConverter(self.store.t_rates)
        self.persona_engine = PersonaEngine(self.store)
        self.decider = DecisionEngine(self.store, self.converter)
        self.msg_parser = MessageParser(verbose=verbose)

    def run(self) -> List[Dict]:
        rows: List[Dict] = []
        for req in self.store.requests:
            rows.append(self._process(req))
        self.resolver.save_cache()
        self.msg_parser.save_cache()
        return rows

    def _msgs_for(self, req):
        msgs = list(self.store.messages_by_request.get(req.request_id, []))
        msgs += [m for m in self.store.messages_by_user.get(req.user_id, [])
                 if not m.request_id]
        return msgs

    def _income_update_for(self, req):
        """Confirmed payroll update applicable to this request, from messages."""
        msgs = self._msgs_for(req)
        return self.msg_parser.income_update_for(msgs) if msgs else None

    def explain_all(self) -> List[Dict]:
        """Rich per-request bundles (decision + forecast series) for reporting."""
        from datetime import date as _date
        bundles: List[Dict] = []
        for req in self.store.requests:
            profile = self.store.profile_for(req.user_id)
            if profile is None:
                continue
            events = self.store.events_for(req.user_id)
            start = req.request_date or _date.today()
            msgs = self._msgs_for(req)
            ctx = ForecastContext(profile, events, self.converter, start,
                                  income_update=self._income_update_for(req),
                                  project_income=not self.msg_parser.suppress_income(msgs))
            persona = self.persona_engine.build(req, profile, ctx)
            row = self.decider.decide(req, profile, ctx, persona)
            d = self.decider.last
            base = ctx.forecast()
            plan_fc = ctx.forecast(d.get("overrides") or {})
            def ser(fc, extra=None):
                return [{"d": dd.isoformat(), "b": round(b, 2)} for dd, b in fc.series(extra)]
            bundles.append({
                "row": row,
                "user_id": req.user_id,
                "home_currency": profile.home_currency,
                "requested": req.requested_amount,
                "min_balance": profile.minimum_balance_to_keep,
                "start_balance": profile.balance,
                "deadline": req.desired_completion_date.isoformat() if req.desired_completion_date else "",
                "request_text": req.request_text,
                "persona": {
                    "fingerprint": persona.fingerprint(),
                    "monthly_income": round(persona.monthly_income, 2),
                    "monthly_essential": round(persona.monthly_essential, 2),
                    "monthly_flexible": round(persona.monthly_flexible, 2),
                    "runway_months": round(persona.runway_months, 2),
                    "dti": round(persona.dti, 3),
                    "need_vs_want": persona.need_vs_want,
                    "money_personality": persona.money_personality,
                },
                "series_base": ser(base),
                "series_plan": ser(plan_fc, d.get("payments") or []),
                "streams": [{"category": s.category, "income": s.is_income,
                             "period": s.period, "amount": round(s.amount, 2)}
                            for s in ctx.streams],
            })
        return bundles

    def _process(self, req) -> Dict:
        profile = self.store.profile_for(req.user_id)
        if profile is None:
            # No profile: cannot forecast; be conservative.
            from config import Method, Status, NONE_TOKEN
            return {
                "request_id": req.request_id,
                "amount_safe_to_pay": "0",
                "affordability_status": Status.NOT_AFFORDABLE,
                "recommended_payment_method": Method.NOT_RECOMMENDED,
                "payment_plan": NONE_TOKEN,
                "earliest_date_for_full_payment": NONE_TOKEN,
                "spending_changes_needed": NONE_TOKEN,
                "decision_explanation": "No financial profile found for this user.",
            }
        from datetime import date as _date
        events = self.store.events_for(req.user_id)
        start = req.request_date or _date.today()
        msgs = self._msgs_for(req)
        income_update = self.msg_parser.income_update_for(msgs) if msgs else None
        ctx = ForecastContext(profile, events, self.converter, start,
                              income_update=income_update,
                              project_income=not self.msg_parser.suppress_income(msgs))
        persona = self.persona_engine.build(req, profile, ctx)
        try:
            return self.decider.decide(req, profile, ctx, persona)
        except Exception as exc:  # never let one bad row kill the whole run
            if self.verbose:
                import traceback
                traceback.print_exc()
            from config import Method, Status, NONE_TOKEN
            return {
                "request_id": req.request_id,
                "amount_safe_to_pay": "0",
                "affordability_status": Status.NOT_AFFORDABLE,
                "recommended_payment_method": Method.NOT_RECOMMENDED,
                "payment_plan": NONE_TOKEN,
                "earliest_date_for_full_payment": NONE_TOKEN,
                "spending_changes_needed": NONE_TOKEN,
                "decision_explanation": f"Could not evaluate request ({exc}).",
            }
