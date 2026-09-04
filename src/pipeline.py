"""The six components in sequence, with per-ticket failure containment.

One ticket that misbehaves must never stop the run (A9, A11), so every ticket
is processed inside a boundary that converts any unexpected exception into a
logged escalation. That way the run always produces either a sent answer or a
logged escalation for every ticket, and none are silently dropped.
"""

from __future__ import annotations

import logging
import time

from .classify import Classifier
from .config import CONFIG, Config
from .generate import generate
from .models import Outcome, Ticket
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
    ):
        self.config = config
        self.classifier = classifier or Classifier()
        self.retriever = retriever or build_retriever(
            config.corpus_path, floor=config.retrieval_floor,
            backend=config.retrieval_backend, gate=config.semantic_gate,
        )
        self.provider = provider

    def process(self, ticket: Ticket) -> Outcome:
        started = time.perf_counter()
        try:
            return self._process(ticket, started)
        except Exception as exc:  # noqa: BLE001 - the containment boundary
            log.exception("ticket %s failed, escalating", ticket.ticket_id)
            return Outcome(
                ticket_id=ticket.ticket_id,
                channel=ticket.channel,
                action="escalated",
                rule="pipeline_error",
                reason="The system could not process this ticket and passed it to a person.",
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                error=f"{type(exc).__name__}: {exc}",
            )

    def _process(self, ticket: Ticket, started: float) -> Outcome:
        classification = self.classifier.classify(ticket.text)

        passages = []
        if classification.intent not in NO_DOC_INTENTS:
            passages = self.retriever.search(ticket.text, top_k=self.config.retrieval_top_k)

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
        )

        if routing.action == "escalate":
            # An escalation carries the drafted summary and the retrieved
            # sources with it: the Brief is explicit that a ticket arriving
            # with context is worth more to an agent than a raw one.
            if passages:
                draft, degraded = generate(ticket, passages, provider=self.provider)
                outcome.response = draft.text
                outcome.citations = draft.citations
                outcome.degraded = degraded
            outcome.latency_ms = round((time.perf_counter() - started) * 1000, 2)
            return outcome

        draft, degraded = generate(ticket, passages, provider=self.provider)
        outcome.degraded = degraded

        verdict = validate(draft, passages)
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
