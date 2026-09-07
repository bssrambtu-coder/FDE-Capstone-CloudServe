"""Sweep the confidence threshold against the development set (D13).

    python3 scripts/sweep_threshold.py --backend hybrid

The threshold is the last untuned control in the system. It has carried the
Brief's illustrative 0.80 since the spine was built, and both R-08 gaps point
at it, so it needs sweeping rather than defending.

Why this does not simply re-run the harness at each threshold: the threshold
only enters at `route`, but classification and retrieval are what cost the
time -- with the hybrid backend every ticket pays for an embedding. So each
ticket is classified and retrieved once, and the pipeline is then replayed at
each threshold over those cached results. The replay drives the real
`Pipeline.process`, not a reimplementation of it, so generation, the guardrails
and the abstention path all behave exactly as they do in a live run. Only the
recomputation is skipped, never the logic.

The measure that decides the threshold is route agreement -- whether the system
answered exactly when the expert answered -- not automation rate. Answering
more is trivially achievable and worth nothing if the extra answers are ones a
person should have handled.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from collections import defaultdict
from dataclasses import replace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.config import CONFIG
from src.ingest import load_tickets
from src.models import Classification
from src.monitoring import Metrics
from src.pipeline import Pipeline
from src.retrieve import build_retriever

DEV = "Capstone_Pack/05_Datasets/development_tickets.json"
FIELDS = ("customer_region", "customer_tier", "language_fluency")
TOLERANCE_PP = 5.0


class _Replay:
    """Serves the classification and retrieval already computed for a ticket.

    One object stands in for both the classifier and the retriever. The
    pipeline calls `classify` first and `search` second for the same ticket,
    so a single cursor is enough and no ticket id has to be threaded through
    an interface that does not take one.
    """

    name = "replay"

    def __init__(self, cached: dict[str, tuple[Classification, list]], semantic: bool):
        self.cached = cached
        self.semantic_available = semantic
        self.current: str | None = None

    def for_ticket(self, ticket_id: str) -> None:
        self.current = ticket_id

    def classify(self, text: str) -> Classification:
        return self.cached[self.current][0]

    def search(self, query: str, *, top_k: int = 5) -> list:
        return self.cached[self.current][1]


def precompute(tickets, backend: str, gate: float) -> tuple[dict, bool]:
    """Classify and retrieve once per ticket. The expensive pass, run once."""
    config = replace(CONFIG, retrieval_backend=backend, semantic_gate=gate)
    pipe = Pipeline(config=config, metrics=Metrics())
    semantic = bool(getattr(pipe.retriever, "semantic_available", False))
    from src.route import NO_DOC_INTENTS

    cached = {}
    for i, t in enumerate(tickets, 1):
        classification = pipe.classifier.classify(t.text)
        passages = []
        if classification.intent not in NO_DOC_INTENTS:
            passages = pipe.retriever.search(t.text, top_k=config.retrieval_top_k)
        cached[t.ticket_id] = (classification, passages)
        if i % 100 == 0:
            print(f"  precomputed {i}/{len(tickets)}", file=sys.stderr)
    return cached, semantic


def evaluate(tickets, cached, semantic, threshold: float, backend: str, gate: float) -> dict:
    replay = _Replay(cached, semantic)
    config = replace(CONFIG, confidence_threshold=threshold,
                     retrieval_backend=backend, semantic_gate=gate)
    pipe = Pipeline(config=config, classifier=replay, retriever=replay, metrics=Metrics())

    rows = []
    for t in tickets:
        replay.for_ticket(t.ticket_id)
        o = pipe.process(t)
        expected = t.labels.get("expected_route")
        answered = o.action == "answered"
        rows.append({
            "answered": answered,
            "expected_auto": (expected == "auto_respond") if expected else None,
            "agrees": (answered == (expected == "auto_respond")) if expected else None,
            "rule": o.rule,
            "groups": {f: getattr(t, f, "unknown") for f in FIELDS},
        })

    judged = [r for r in rows if r["agrees"] is not None]
    n = len(judged) or 1
    # Over-answering is the dangerous error: the system replied where the
    # expert would not have. Under-answering only costs an escalation.
    over = sum(1 for r in judged if r["answered"] and not r["expected_auto"])
    under = sum(1 for r in judged if not r["answered"] and r["expected_auto"])

    gaps = {}
    for field in FIELDS:
        groups = defaultdict(lambda: [0, 0])
        for r in judged:
            key = r["groups"][field]
            groups[key][0] += 1
            groups[key][1] += int(r["agrees"])
        rates = {k: 100 * hit / tot for k, (tot, hit) in groups.items() if tot >= 12}
        gaps[field] = round(max(rates.values()) - min(rates.values()), 2) if len(rates) > 1 else None

    measurable = [g for g in gaps.values() if g is not None]
    return {
        "threshold": round(threshold, 3),
        "automation_rate_pct": round(100 * sum(r["answered"] for r in rows) / len(rows), 2),
        "route_agreement_pct": round(100 * sum(r["agrees"] for r in judged) / n, 2),
        "over_answered_pct": round(100 * over / n, 2),
        "under_answered_pct": round(100 * under / n, 2),
        "gaps_pp": gaps,
        "max_gap_pp": round(max(measurable), 2) if measurable else None,
        "fairness_condition_holds": (max(measurable) <= TOLERANCE_PP) if measurable else None,
        "rules": dict(sorted(
            ((r["rule"], sum(1 for x in rows if x["rule"] == r["rule"])) for r in rows),
            key=lambda kv: -kv[1])),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=DEV)
    ap.add_argument("--output", default="evaluation/threshold")
    ap.add_argument("--backend", choices=("lexical", "chroma", "hybrid"),
                    default=CONFIG.retrieval_backend)
    ap.add_argument("--semantic-gate", type=float, default=CONFIG.semantic_gate)
    ap.add_argument("--from", dest="lo", type=float, default=0.50)
    ap.add_argument("--to", dest="hi", type=float, default=0.999)
    ap.add_argument("--step", type=float, default=0.025)
    args = ap.parse_args()
    logging.basicConfig(level=logging.ERROR)

    tickets = list(load_tickets(args.input))
    print(f"input: {args.input}  n={len(tickets)}  backend={args.backend}", file=sys.stderr)
    cached, semantic = precompute(tickets, args.backend, args.semantic_gate)
    if not semantic and args.backend == "hybrid":
        print("  ! hybrid has no semantic backend; sweeping the degraded configuration",
              file=sys.stderr)

    confidences = sorted(c.confidence for c, _ in cached.values())
    distribution = {
        f"p{q}": round(confidences[min(len(confidences) - 1, int(q / 100 * len(confidences)))], 4)
        for q in (1, 5, 10, 25, 50, 75, 90, 99)
    }

    grid = []
    x = args.lo
    while x <= args.hi + 1e-9:
        grid.append(round(x, 3))
        x += args.step
    for extra in (0.80, 0.95, 0.98, 0.985, 0.99, 0.995):
        if extra not in grid and args.lo <= extra <= args.hi:
            grid.append(extra)
    grid = sorted(set(grid))

    results = [evaluate(tickets, cached, semantic, t, args.backend, args.semantic_gate)
               for t in grid]

    # The threshold that maximises route agreement, breaking ties towards the
    # lower one: two thresholds that agree with the expert equally often are
    # not equally good, and the one that escalates less wastes less of a
    # person's time.
    best = max(results, key=lambda r: (r["route_agreement_pct"], -r["threshold"]))
    passing = [r for r in results if r["fairness_condition_holds"]]
    best_fair = max(passing, key=lambda r: (r["route_agreement_pct"], -r["threshold"])) \
        if passing else None

    report = {
        "input": args.input,
        "backend": args.backend,
        "semantic_available": semantic,
        "tickets": len(tickets),
        "confidence_distribution": distribution,
        "current_threshold": CONFIG.confidence_threshold,
        "best_by_agreement": best,
        "best_passing_fairness": best_fair,
        "sweep": results,
    }
    out = pathlib.Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"threshold_{args.backend}.json").write_text(json.dumps(report, indent=1), encoding="utf-8")

    print(f"\nconfidence distribution: {distribution}")
    print(f"\n{'thresh':>7} {'auto%':>7} {'agree%':>7} {'over%':>7} {'under%':>7} "
          f"{'maxgap':>7}  fairness")
    for r in results:
        mark = " <-- current" if abs(r["threshold"] - CONFIG.confidence_threshold) < 1e-9 else ""
        gap = f"{r['max_gap_pp']:.2f}" if r["max_gap_pp"] is not None else "n/a"
        holds = {True: "ok", False: "FAIL", None: "n/a"}[r["fairness_condition_holds"]]
        print(f"{r['threshold']:>7.3f} {r['automation_rate_pct']:>7.2f} "
              f"{r['route_agreement_pct']:>7.2f} {r['over_answered_pct']:>7.2f} "
              f"{r['under_answered_pct']:>7.2f} {gap:>7}  {holds}{mark}")
    print(f"\nbest by agreement:        {best['threshold']} "
          f"({best['route_agreement_pct']}% agreement, max gap {best['max_gap_pp']}pp)")
    if best_fair:
        print(f"best passing fairness:    {best_fair['threshold']} "
              f"({best_fair['route_agreement_pct']}% agreement, max gap {best_fair['max_gap_pp']}pp)")
    else:
        print("best passing fairness:    none -- no threshold brings every gap inside 5pp")
    print(f"\nwrote {out / f'threshold_{args.backend}.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
