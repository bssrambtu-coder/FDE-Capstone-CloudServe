"""Route: answer automatically or escalate, deterministically (A5).

Rules are evaluated in a fixed order and the first match wins, so the same
input always yields the same decision and the reason is a single named rule
rather than an opaque score. Every branch records language a support manager
could read, which the Build Specification requires.
"""

from __future__ import annotations

from .models import Classification, Passage, Routing, Ticket

# Intents that are never answered automatically, whatever the confidence.
#
# Derived from the development set: compliance_request, security_incident,
# feature_request and unclear_request are labelled for escalation in 100% of
# their 87 occurrences. Two of them (feature_request, unclear_request) also
# have no supporting documentation in any instance. These are commitments the
# system is not entitled to make on CloudServe's behalf.
POLICY_ESCALATE = frozenset(
    {"compliance_request", "security_incident", "feature_request", "unclear_request"}
)

# Intents with no documentation coverage at all: retrieval is skipped rather
# than run and discarded, which saves a search and keeps the log honest.
NO_DOC_INTENTS = frozenset({"feature_request", "unclear_request"})


def route(
    ticket: Ticket,
    classification: Classification,
    passages: list[Passage],
    *,
    threshold: float,
) -> Routing:
    if classification.intent in POLICY_ESCALATE:
        return Routing(
            action="escalate",
            reason=(
                f"'{classification.intent}' is always handled by a person: this class of "
                f"request commits CloudServe to something the system cannot promise."
            ),
            rule="policy_intent",
        )

    if classification.confidence < threshold:
        return Routing(
            action="escalate",
            reason=(
                f"The request was read as '{classification.intent}' but only with "
                f"{classification.confidence:.0%} confidence, below the {threshold:.0%} bar "
                f"for answering without review."
            ),
            rule="low_confidence",
        )

    if not passages:
        return Routing(
            action="escalate",
            reason=(
                "No documentation passage was relevant enough to support an answer, so "
                "replying would mean guessing."
            ),
            rule="no_supporting_documentation",
        )

    return Routing(
        action="auto_respond",
        reason=(
            f"Read as '{classification.intent}' with {classification.confidence:.0%} "
            f"confidence and supported by {len(passages)} documentation passage(s): "
            f"{', '.join(p.doc_id for p in passages)}."
        ),
        rule="confident_and_supported",
    )
