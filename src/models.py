"""Internal representations. One shape per stage of the pipeline.

Every component consumes and returns these, so swapping an implementation
(rule-based retrieval for Chroma, stub generation for a real model) never
changes the pipeline or the decision log.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

CHANNELS = ("email", "chat", "docs_comment", "forum")


@dataclass(frozen=True)
class Ticket:
    """One ticket, normalised out of any of the four channels (A2)."""

    ticket_id: str
    channel: str
    subject: str
    body: str
    received_at: str
    customer_id: str
    customer_tier: str
    customer_region: str
    language_fluency: str
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def text(self) -> str:
        """Subject and body together, for classification and retrieval."""
        return f"{self.subject}\n{self.body}".strip()

    @property
    def labels(self) -> dict[str, Any]:
        """Ground-truth labels when the input file carries them. Never used
        by the pipeline itself — only by the metrics layer."""
        return self.raw.get("labels") or {}


@dataclass(frozen=True)
class Classification:
    intent: str
    urgency: str
    confidence: float
    alternatives: list[tuple[str, float]] = field(default_factory=list)


@dataclass(frozen=True)
class Passage:
    doc_id: str
    title: str
    text: str
    score: float


@dataclass(frozen=True)
class Routing:
    action: str          # "auto_respond" | "escalate"
    reason: str          # readable by a support manager
    rule: str            # which rule fired, for the log


@dataclass(frozen=True)
class Draft:
    text: str
    citations: list[str] = field(default_factory=list)
    abstained: bool = False


@dataclass(frozen=True)
class Validation:
    blocked: bool
    checks: dict[str, Any] = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)


@dataclass
class Outcome:
    """What happened to one ticket. Exactly one per ticket processed (A8)."""

    ticket_id: str
    channel: str
    action: str                      # answered | escalated | blocked
    reason: str
    rule: str
    intent: str | None = None
    urgency: str | None = None
    confidence: float | None = None
    sources: list[str] = field(default_factory=list)
    response: str | None = None
    citations: list[str] = field(default_factory=list)
    guardrail_findings: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    degraded: bool = False
    error: str | None = None
    audit_details: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        return asdict(self)
