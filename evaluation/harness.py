"""The unattended evaluation run (A9, A10).

    python -m evaluation.harness --input <tickets.json> --output <dir>

Takes an input path and an output path as arguments because it will be pointed
at a file this repository has never seen. Nothing about the ticket count, the
filename or the presence of labels is assumed.

Every ticket produces either a sent answer or a logged escalation; none are
silently dropped. One ticket failing never stops the run. A metrics report is
written at the end without further manual work.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict
from pathlib import Path

from src.config import CONFIG, Config
from src.decision_log import DecisionLog
from src.ingest import load_tickets
from src.pipeline import Pipeline
from src.providers import get_provider

from . import metrics as metrics_mod

log = logging.getLogger("harness")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m evaluation.harness",
        description="Process a ticket file end to end and write a metrics report.",
    )
    p.add_argument("--input", required=True, help="path to a ticket JSON file")
    p.add_argument("--output", required=True, help="directory for results and metrics")
    p.add_argument("--corpus", default=CONFIG.corpus_path, help="documentation corpus")
    p.add_argument("--threshold", type=float, default=CONFIG.confidence_threshold)
    p.add_argument("--retrieval-floor", type=float, default=CONFIG.retrieval_floor)
    p.add_argument("--backend", choices=("lexical", "chroma", "hybrid"),
                   default=CONFIG.retrieval_backend,
                   help="hybrid is recommended; lexical needs no dependencies")
    p.add_argument("--semantic-gate", type=float, default=CONFIG.semantic_gate)
    p.add_argument("--db", default=CONFIG.sqlite_path, help="decision log database")
    p.add_argument("--use-provider", action="store_true",
                   help="call the model provider; without it generation is extractive")
    p.add_argument("--limit", type=int, default=None, help="process only the first N tickets")
    p.add_argument("--progress-every", type=int, default=25)
    p.add_argument("--log-level", default=CONFIG.log_level)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    in_path = Path(args.input)
    if not in_path.exists():
        log.error("input file not found: %s", in_path)
        return 2
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    config = Config(
        confidence_threshold=args.threshold,
        retrieval_floor=args.retrieval_floor,
        corpus_path=args.corpus,
        retrieval_backend=args.backend,
        semantic_gate=args.semantic_gate,
    )

    log.info("loading tickets from %s", in_path)
    tickets = list(load_tickets(in_path))
    if args.limit:
        tickets = tickets[: args.limit]
    if not tickets:
        log.error("no tickets found in %s", in_path)
        return 2
    log.info("%d tickets loaded", len(tickets))

    provider = get_provider() if args.use_provider else None
    pipeline = Pipeline(config=config, provider=provider)
    log.info("retriever=%s threshold=%.2f floor=%.2f provider=%s",
             getattr(pipeline.retriever, "name", "?"), config.confidence_threshold,
             config.retrieval_floor, "on" if provider else "off (extractive)")

    decision_log = DecisionLog(args.db)
    run_id = decision_log.start_run(
        input_path=str(in_path), output_path=str(out_dir),
        config={"threshold": config.confidence_threshold,
                "retrieval_floor": config.retrieval_floor,
                "backend": args.backend,
                "provider": bool(provider)},
    )
    log.info("run %s started", run_id)

    started = time.perf_counter()
    outcomes = []
    for i, ticket in enumerate(tickets, 1):
        outcome = pipeline.process(ticket)
        outcomes.append(outcome)
        decision_log.record(outcome, ticket.text)
        if i % args.progress_every == 0:
            decision_log.commit()          # partial progress survives a crash
            log.info("%d/%d processed", i, len(tickets))
    decision_log.commit()
    elapsed = time.perf_counter() - started

    if provider is not None:
        provider.cache.flush()
    provider_stats = {
        "calls": getattr(provider, "calls", 0),
        "cache_hits": getattr(provider, "cache_hits", 0),
        "degradations": getattr(provider, "degradations", 0),
        "circuit_breaker_open": getattr(provider, "breaker_open", False),
    } if provider else {"enabled": False}

    logged = decision_log.count(run_id)
    decision_log.finish_run(len(tickets))

    report = metrics_mod.compute(
        outcomes, tickets, logged_decisions=logged, provider_stats=provider_stats
    )
    report["run"] = {
        "run_id": run_id,
        "input": str(in_path),
        "tickets": len(tickets),
        "wall_clock_seconds": round(elapsed, 2),
        "tickets_per_second": round(len(tickets) / elapsed, 2) if elapsed else None,
        "config": {"threshold": config.confidence_threshold,
                   "retrieval_floor": config.retrieval_floor,
                   "backend": args.backend,
                   "retriever": getattr(pipeline.retriever, "name", "?"),
                   "provider_enabled": bool(provider)},
    }

    (out_dir / "results.json").write_text(
        json.dumps([asdict(o) for o in outcomes], indent=1), encoding="utf-8"
    )
    (out_dir / "metrics.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    (out_dir / "metrics.md").write_text(metrics_mod.to_markdown(report), encoding="utf-8")
    decision_log.close()

    v = report["volume"]
    log.info("done in %.1fs: %d answered, %d escalated, %d blocked, %d errors",
             elapsed, v["answered_automatically"], v["escalated"],
             v["blocked_by_guardrails"], v["pipeline_errors"])
    log.info("wrote %s", ", ".join(
        str(out_dir / f) for f in ("results.json", "metrics.json", "metrics.md")))

    if logged != len(tickets):
        log.error("decision log does not reconcile: %d logged against %d tickets",
                  logged, len(tickets))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
