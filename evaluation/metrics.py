"""Metrics, computed by code rather than by hand afterwards (A10).

The Build Specification names four groups that must be present: Volume,
Business, Technical and Governance. The fairness slices are added because the
governance framework treats a gap over five percentage points between customer
groups as a condition that either holds or does not.

Where a figure cannot be computed from the input available, it is reported as
null with a stated reason rather than filled with a plausible number.
"""

from __future__ import annotations

import statistics as st
from collections import Counter, defaultdict
from typing import Any

from src.models import Outcome, Ticket

FAIRNESS_FIELDS = ("customer_region", "customer_tier", "language_fluency")
FAIRNESS_TOLERANCE_PP = 5.0


def _pct(numerator: int, denominator: int) -> float | None:
    return round(100 * numerator / denominator, 2) if denominator else None


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo), 2)


def per_class(pairs: list[tuple[str, str]]) -> dict[str, dict[str, Any]]:
    """Precision, recall and F1 per class from (gold, predicted) pairs."""
    labels = sorted({g for g, _ in pairs} | {p for _, p in pairs})
    out = {}
    for label in labels:
        tp = sum(1 for g, p in pairs if g == label and p == label)
        fp = sum(1 for g, p in pairs if g != label and p == label)
        fn = sum(1 for g, p in pairs if g == label and p != label)
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision and recall
            else None
        )
        out[label] = {
            "support": tp + fn,
            "precision": round(precision, 4) if precision is not None else None,
            "recall": round(recall, 4) if recall is not None else None,
            "f1": round(f1, 4) if f1 is not None else None,
        }
    return out


def compute(
    outcomes: list[Outcome],
    tickets: list[Ticket],
    *,
    logged_decisions: int | None = None,
    provider_stats: dict | None = None,
) -> dict[str, Any]:
    n = len(outcomes)
    by_id = {t.ticket_id: t for t in tickets}
    actions = Counter(o.action for o in outcomes)
    answered = actions["answered"]
    escalated = actions["escalated"]
    blocked = actions["blocked"]
    latencies = [o.latency_ms for o in outcomes if o.latency_ms]

    # --- labels, when the input file carries them. The hidden set uses the
    # same schema, so this fills in automatically; if it does not, the
    # label-dependent figures are reported as null.
    intent_pairs, urgency_pairs = [], []
    retrieval_expected = retrieval_hit = retrieval_abstain_ok = 0
    for o in outcomes:
        labels = by_id.get(o.ticket_id, None)
        labels = labels.labels if labels else {}
        if labels.get("intent") and o.intent:
            intent_pairs.append((labels["intent"], o.intent))
        if labels.get("urgency") and o.urgency:
            urgency_pairs.append((labels["urgency"], o.urgency))
        if "expected_doc_ids" in labels:
            expected = set(labels["expected_doc_ids"] or [])
            got = set(o.sources)
            if expected:
                retrieval_expected += 1
                if got & expected:
                    retrieval_hit += 1
            elif not got:
                retrieval_abstain_ok += 1

    labelled = bool(intent_pairs)
    no_doc_total = sum(
        1 for o in outcomes
        if "expected_doc_ids" in (by_id.get(o.ticket_id).labels if by_id.get(o.ticket_id) else {})
        and not (by_id[o.ticket_id].labels.get("expected_doc_ids") or [])
    )

    guardrail_types = Counter()
    for o in outcomes:
        for finding in o.guardrail_findings:
            guardrail_types[finding.split(":")[0]] += 1
    private_data_detections = sum(
        1 for o in outcomes if any(f.startswith("private_data") for f in o.guardrail_findings)
    )

    metrics: dict[str, Any] = {
        "volume": {
            "tickets_processed": n,
            "answered_automatically": answered,
            "escalated": escalated,
            "blocked_by_guardrails": blocked,
            "pipeline_errors": sum(1 for o in outcomes if o.error),
            "degraded_responses": sum(1 for o in outcomes if o.degraded),
        },
        "business": {
            # Resolution without escalation is the closest available proxy for
            # first contact resolution: whether the customer was in fact
            # satisfied cannot be observed from a batch run, and the report
            # should not present this figure as measured CSAT.
            "first_contact_resolution_pct": _pct(answered, n),
            "escalation_rate_pct": _pct(escalated + blocked, n),
            "mean_response_time_ms": round(st.mean(latencies), 2) if latencies else None,
            "median_response_time_ms": round(st.median(latencies), 2) if latencies else None,
            "customer_satisfaction": None,
            "customer_satisfaction_note": (
                "Not measurable from an unattended batch run; requires post-response survey."
            ),
            "repeat_contacts": None,
            "repeat_contacts_note": (
                "Requires a seven-day window after each reply; not observable in a single run."
            ),
        },
        "technical": {
            "intent": {
                "accuracy": round(
                    sum(1 for g, p in intent_pairs if g == p) / len(intent_pairs), 4
                ) if intent_pairs else None,
                "macro_precision": None,
                "per_class": per_class(intent_pairs) if intent_pairs else None,
            },
            "urgency": {
                "accuracy": round(
                    sum(1 for g, p in urgency_pairs if g == p) / len(urgency_pairs), 4
                ) if urgency_pairs else None,
                "per_class": per_class(urgency_pairs) if urgency_pairs else None,
            },
            "retrieval": {
                "hit_rate_pct": _pct(retrieval_hit, retrieval_expected),
                "tickets_expecting_a_document": retrieval_expected,
                "correct_abstention_pct": _pct(retrieval_abstain_ok, no_doc_total),
                "tickets_expecting_no_document": no_doc_total,
            },
            "latency_ms": {
                "median": _quantile(latencies, 0.50),
                "p95": _quantile(latencies, 0.95),
                "max": round(max(latencies), 2) if latencies else None,
            },
            "labels_present": labelled,
        },
        "governance": {
            "decisions_logged": logged_decisions,
            "tickets_processed": n,
            "log_reconciles": (logged_decisions == n) if logged_decisions is not None else None,
            "guardrail_activations_by_type": dict(guardrail_types) or {},
            "private_data_detections_in_outbound": private_data_detections,
            "responses_released": answered,
        },
        "provider": provider_stats or {},
    }

    if intent_pairs:
        precisions = [
            v["precision"] for v in metrics["technical"]["intent"]["per_class"].values()
            if v["precision"] is not None
        ]
        metrics["technical"]["intent"]["macro_precision"] = (
            round(st.mean(precisions), 4) if precisions else None
        )

    # --- fairness: automation rate by customer group, against a 5pp condition
    fairness = {}
    for field in FAIRNESS_FIELDS:
        groups = defaultdict(lambda: [0, 0])
        for o in outcomes:
            ticket = by_id.get(o.ticket_id)
            if not ticket:
                continue
            key = getattr(ticket, field, "unknown")
            groups[key][0] += 1
            if o.action == "answered":
                groups[key][1] += 1
        rates = {k: 100 * a / t for k, (t, a) in groups.items() if t}
        spread = (max(rates.values()) - min(rates.values())) if len(rates) > 1 else 0.0
        fairness[field] = {
            "automation_rate_pct": {k: round(v, 2) for k, v in sorted(rates.items())},
            "group_sizes": {k: t for k, (t, _) in sorted(groups.items())},
            "spread_pp": round(spread, 2),
            "within_tolerance": spread <= FAIRNESS_TOLERANCE_PP,
            "tolerance_pp": FAIRNESS_TOLERANCE_PP,
        }
    metrics["governance"]["fairness"] = fairness
    return metrics


def to_markdown(metrics: dict[str, Any]) -> str:
    v, b, t, g = (metrics[k] for k in ("volume", "business", "technical", "governance"))
    lines = ["# Evaluation metrics", "", "## Volume", ""]
    lines += [f"- {k.replace('_', ' ')}: {val}" for k, val in v.items()]
    lines += ["", "## Business outcomes", ""]
    lines += [f"- {k.replace('_', ' ')}: {val}" for k, val in b.items() if not k.endswith("note")]
    lines += ["", "## Technical performance", ""]
    lines += [
        f"- intent accuracy: {t['intent']['accuracy']}",
        f"- intent macro precision: {t['intent']['macro_precision']}",
        f"- urgency accuracy: {t['urgency']['accuracy']}",
        f"- retrieval hit rate: {t['retrieval']['hit_rate_pct']}% "
        f"(n={t['retrieval']['tickets_expecting_a_document']})",
        f"- correct abstention: {t['retrieval']['correct_abstention_pct']}% "
        f"(n={t['retrieval']['tickets_expecting_no_document']})",
        f"- latency median/p95/max ms: {t['latency_ms']['median']} / "
        f"{t['latency_ms']['p95']} / {t['latency_ms']['max']}",
    ]
    lines += ["", "## Governance", ""]
    lines += [
        f"- decisions logged: {g['decisions_logged']} against {g['tickets_processed']} "
        f"tickets processed (reconciles: {g['log_reconciles']})",
        f"- guardrail activations: {g['guardrail_activations_by_type'] or 'none'}",
        f"- private data in outbound responses: {g['private_data_detections_in_outbound']}",
        "",
        "### Fairness: automation rate by customer group",
        "",
    ]
    for field, data in g["fairness"].items():
        verdict = "within tolerance" if data["within_tolerance"] else "EXCEEDS TOLERANCE"
        lines.append(f"- **{field}** spread {data['spread_pp']}pp ({verdict}, "
                     f"tolerance {data['tolerance_pp']}pp)")
        for group, rate in data["automation_rate_pct"].items():
            lines.append(f"  - {group}: {rate}% (n={data['group_sizes'][group]})")
    return "\n".join(lines) + "\n"
