"""The six components in sequence, with per-ticket failure containment.

One ticket that misbehaves must never stop the run (A9, A11), so every ticket
is processed inside a boundary that converts any unexpected exception into a
logged escalation. That way the run always produces either a sent answer or a
logged escalation for every ticket, and none are silently dropped.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict

from .classify import Classifier
from .config import CONFIG, Config
from .control import AutomationControl
from .generate import generate
from .models import Outcome, Ticket
from .monitoring import METRICS, Metrics
from .retrieve import Retriever, build_retriever
from .route import NO_DOC_INTENTS, route
from .validate import validate

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(
        self,
        *,
        config: Config = CONFIG,
        classifier: Classifier | None = None,
        retriever: Retriever | None = None,
        provider=None,
        metrics: Metrics = METRICS,
        control: AutomationControl | None = None,
    ):
        self.config = config
        self.control = control or AutomationControl(config.kill_switch_path)
        self.classifier = classifier or Classifier()
        self.retriever = retriever or build_retriever(
            config.corpus_path, floor=config.retrieval_floor,
            backend=config.retrieval_backend, gate=config.semantic_gate,
        )
        self.provider = provider
        self.metrics = metrics
        # 0 means retrieval is running lexical-only, whether that was chosen or
        # degraded into. Either way R-07 is open, so the panel should say so
        # rather than distinguish a deliberate lexical run from a broken hybrid
        # one -- the fairness consequence is identical.
        self.metrics.set_semantic_available(
            bool(getattr(self.retriever, "semantic_available", False))
        )

    def process(self, ticket: Ticket) -> Outcome:
        started = time.perf_counter()
        try:
            if self.control.disabled:
                outcome = self._paused(ticket)
            else:
                outcome = self._process(ticket, started)
            # Recheck after generation so a pause also catches in-flight work.
            if outcome.action == "answered" and self.control.disabled:
                outcome.action = "escalated"
                outcome.rule = "automation_paused"
                outcome.reason = "Automatic replies are paused by the operator."
                outcome.response = None
                outcome.citations = []
        except Exception as exc:  # noqa: BLE001 - the containment boundary
            log.exception("ticket %s failed, escalating", ticket.ticket_id)
            outcome = Outcome(
                ticket_id=ticket.ticket_id,
                channel=ticket.channel,
                action="escalated",
                rule="pipeline_error",
                reason="The system could not process this ticket and passed it to a person.",
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                intent="unclear_request", urgency="medium", confidence=0.0,
                error=type(exc).__name__,
            )
        # Recorded on both paths, and outside the try, so a crashed ticket is
        # as visible on the dashboard as a successful one.
        self.metrics.observe_outcome(outcome)
        return outcome

    @staticmethod
    def _paused(ticket):
        return Outcome(ticket_id=ticket.ticket_id, channel=ticket.channel,
                       action="escalated", rule="automation_paused",
                       intent="unclear_request", urgency="medium", confidence=0.0,
                       reason="Automatic replies are paused by the operator.")

    def _process(self, ticket: Ticket, started: float) -> Outcome:
        classification = self.classifier.classify(ticket.text)

        passages = []
        if classification.intent not in NO_DOC_INTENTS:
            passages = self.retriever.search(ticket.text, top_k=self.config.retrieval_top_k)
            self.metrics.observe_retrieval(passages)
            self.metrics.set_semantic_available(bool(getattr(self.retriever, "semantic_available", False)))

        routing = route(
            ticket, classification, passages, threshold=self.config.confidence_threshold
        )

        outcome = Outcome(
            ticket_id=ticket.ticket_id,
            channel=ticket.channel,
            action="escalated",
            reason=routing.reason,
            rule=routing.rule,
            intent=classification.intent,
            urgency=classification.urgency,
            confidence=classification.confidence,
            sources=[p.doc_id for p in passages],
            audit_details={"classification": asdict(classification),
                           "retrieval": [asdict(p) for p in passages],
                           "threshold": self.config.confidence_threshold,
                           "retrieval_backend": self.config.retrieval_backend,
                           "provider_model": str(getattr(getattr(self.provider, "inner", self.provider), "model", "extractive"))},
        )

        if routing.action == "escalate":
            # An escalation carries the drafted summary and the retrieved
            # sources with it: the Brief is explicit that a ticket arriving
            # with context is worth more to an agent than a raw one.
            if passages:
                draft, degraded = generate(ticket, passages, provider=self.provider)
                verdict = validate(draft, passages)
                outcome.audit_details["validation"] = verdict.checks
                outcome.guardrail_findings = verdict.findings
                outcome.response = None if verdict.blocked else draft.text
                outcome.citations = [] if verdict.blocked else draft.citations
                outcome.degraded = degraded
            outcome.latency_ms = round((time.perf_counter() - started) * 1000, 2)
            return outcome

        draft, degraded = generate(ticket, passages, provider=self.provider)
        outcome.degraded = degraded

        verdict = validate(draft, passages)
        outcome.audit_details["validation"] = verdict.checks
        outcome.guardrail_findings = verdict.findings

        if verdict.blocked:
            outcome.action = "blocked"
            outcome.rule = "guardrail_block"
            outcome.reason = (
                "The drafted reply was withheld by a guardrail ("
                + ", ".join(verdict.findings)
                + ") and the ticket was passed to a person."
            )
            outcome.response = None
        elif draft.abstained:
            outcome.action = "escalated"
            outcome.rule = "generation_abstained"
            outcome.reason = (
                "The documentation did not support a specific answer, so the system "
                "declined to reply and escalated instead."
            )
            outcome.response = draft.text
        else:
            outcome.action = "answered"
            outcome.rule = routing.rule
            outcome.response = draft.text
            outcome.citations = draft.citations

        outcome.latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return outcome
