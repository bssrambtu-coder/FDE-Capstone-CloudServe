"""Validate: the guardrails that run on every response before release (A7).

Runs on every generated response, not only in testing, and can block. A
guardrail that only warns is not a guardrail. Records what it checked and what
it found whether or not it blocked, which A8 needs for reconciliation.
"""

from __future__ import annotations

import re

from .generate import TEMPLATE_SENTENCES
from .models import Draft, Passage, Validation

# Patterns that must never appear in an outbound response.
PII_PATTERNS = {
    "email_address": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b"),
    "api_key": re.compile(r"\b(?:sk|pk|api|key|token|bearer)[-_][A-Za-z0-9]{12,}\b", re.I),
    "payment_card": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    "private_ip": re.compile(r"\b(?:10|127|192\.168|172\.(?:1[6-9]|2\d|3[01]))(?:\.\d{1,3}){2,3}\b"),
    "aws_key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
}

# Commitments the system is not entitled to make. Taken from the must_not_claim
# field of ground_truth_responses.json, which is consistent across all 200
# expert answers.
FORBIDDEN_CLAIMS = (
    "a refund has been issued",
    "refund has been processed",
    "the issue has been fixed on our side",
    "this has been fixed",
    "we have resolved it on our end",
    "guaranteed",
    "we will have this fixed by",
    "it will be fixed by",
)

_SENT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[a-z0-9]+")


_CITE = re.compile(r"\[([A-Z]+-[A-Z]+-\d+)\]")
_BOILERPLATE = {" ".join(_WORD.findall(s.lower())) for s in TEMPLATE_SENTENCES}


def _unsupported_sentences(draft: Draft, passages: list[Passage], *, floor: float = 0.35) -> list[str]:
    """Sentences whose content words are largely absent from their source.

    A sentence carrying a citation is measured against that document only, so
    a claim cited to the wrong passage is caught rather than excused by
    another passage in the same result set. Uncited sentences are measured
    against the whole retrieved set. Fixed courtesy phrases are skipped: they
    make no factual claim and would otherwise flag every grounded answer.

    A crude but honest check. It catches a draft that drifts away from its
    sources; it cannot catch a fluent paraphrase that reverses a meaning, and
    the report should say so rather than imply the guardrail is stronger than
    it is.
    """
    if not passages:
        return []
    by_id = {p.doc_id: set(_WORD.findall(p.text.lower())) for p in passages}
    everything = set().union(*by_id.values()) if by_id else set()

    out = []
    for sentence in _SENT.split(draft.text):
        for line in sentence.split("\n"):
            normalised = " ".join(_WORD.findall(line.lower()))
            if not normalised or normalised in _BOILERPLATE:
                continue
            cited = _CITE.findall(line)
            reference = set().union(*(by_id.get(c, set()) for c in cited)) if cited else everything
            if not reference:
                continue
            words = [w for w in _WORD.findall(_CITE.sub("", line).lower()) if len(w) > 3]
            if len(words) < 6:
                continue
            if sum(1 for w in words if w in reference) / len(words) < floor:
                out.append(line.strip())
    return out


def validate(draft: Draft, passages: list[Passage]) -> Validation:
    findings: list[str] = []
    checks: dict[str, object] = {}

    pii = {name: len(pat.findall(draft.text)) for name, pat in PII_PATTERNS.items()}
    pii = {k: v for k, v in pii.items() if v}
    checks["private_data"] = pii or "none"
    findings += [f"private_data:{k}" for k in pii]

    lowered = draft.text.lower()
    forbidden = [c for c in FORBIDDEN_CLAIMS if c in lowered]
    checks["forbidden_claims"] = forbidden or "none"
    findings += [f"forbidden_claim:{c}" for c in forbidden]

    unresolvable = [c for c in draft.citations if c not in {p.doc_id for p in passages}]
    checks["citations_resolve"] = not unresolvable
    findings += [f"unresolvable_citation:{c}" for c in unresolvable]

    unsupported = [] if draft.abstained else _unsupported_sentences(draft, passages)
    checks["unsupported_sentences"] = len(unsupported)
    if unsupported:
        findings.append(f"unsupported_claims:{len(unsupported)}")

    # Private data, a forbidden commitment or a citation that does not resolve
    # blocks release outright. Weak grounding is recorded but escalated rather
    # than blocked, because the drafted text still helps the agent.
    blocked = bool(pii or forbidden or unresolvable)
    checks["blocked"] = blocked
    return Validation(blocked=blocked, checks=checks, findings=findings)
