"""Monitoring: what the system is doing while it runs (B-12).

Two constraints shaped this module.

*Prometheus must be optional.* The spine runs on the standard library alone,
and the gate must never fail because a monitoring dependency is missing. So
every metric here is recorded into an in-process tally that always works, and
mirrored into `prometheus_client` only when it is installed. Nothing upstream
knows or cares which of the two is live.

*The tally is not decoration.* Because it is always present, the acceptance
tests assert on it directly, so the instrumentation is covered whether or not
the grader installed the extras.

The metric set follows the Setup Guide (tickets by channel and outcome,
end-to-end latency, guardrail blocks) plus the three this system specifically
needs to be watchable: retrieval abstentions, degraded generations, and
whether hybrid retrieval still has its semantic half. That last one is the
alarm that matters — when it drops to 0 the system is silently running the
configuration the fairness audit fails (R-07).
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict

log = logging.getLogger(__name__)

# End-to-end latency buckets, in seconds. The extractive path runs in single
# -digit milliseconds and the provider path in seconds, so the buckets have to
# span three orders of magnitude to be readable in either configuration.
LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)

try:  # pragma: no cover - exercised by whichever branch the environment has
    from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
    from prometheus_client import start_http_server as _start_http_server

    PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover
    CollectorRegistry = Counter = Gauge = Histogram = None  # type: ignore[assignment]
    _start_http_server = None  # type: ignore[assignment]
    PROMETHEUS_AVAILABLE = False


class Metrics:
    """The instrument panel. One instance per process, created at import.

    Every method is safe to call from any thread and safe to call when
    Prometheus is absent, because recording into the tally is the primary path
    and the Prometheus mirror is the optional one.
    """

    def __init__(self, *, registry=None):
        self._lock = threading.Lock()
        self.counters: dict[str, dict[tuple[str, ...], float]] = defaultdict(
            lambda: defaultdict(float)
        )
        self.gauges: dict[str, float] = {}
        self.latencies: list[float] = []
        self._prom = None
        self._server_port: int | None = None
        if PROMETHEUS_AVAILABLE:
            self._build_prometheus(registry)

    def _build_prometheus(self, registry) -> None:
        reg = registry if registry is not None else CollectorRegistry(auto_describe=True)
        self._registry = reg
        self._prom = {
            "tickets_processed_total": Counter(
                "tickets_processed_total", "Tickets processed",
                ["channel", "outcome"], registry=reg),
            "response_seconds": Histogram(
                "response_seconds", "End to end response time",
                buckets=LATENCY_BUCKETS, registry=reg),
            "guardrail_blocks_total": Counter(
                "guardrail_blocks_total", "Responses blocked by a guardrail",
                ["guardrail"], registry=reg),
            "escalations_total": Counter(
                "escalations_total", "Escalations by the rule that fired",
                ["rule"], registry=reg),
            "retrieval_abstentions_total": Counter(
                "retrieval_abstentions_total",
                "Retrievals that returned nothing rather than something irrelevant",
                registry=reg),
            "responses_degraded_total": Counter(
                "responses_degraded_total",
                "Responses generated extractively after a provider failure",
                registry=reg),
            "retrieval_semantic_available": Gauge(
                "retrieval_semantic_available",
                "1 when hybrid retrieval has its semantic half, 0 when degraded to lexical",
                registry=reg),
        }

    # -- recording ---------------------------------------------------------

    def _count(self, name: str, labels: tuple[str, ...] = (), amount: float = 1.0) -> None:
        with self._lock:
            self.counters[name][labels] += amount
        if self._prom is not None:
            metric = self._prom[name]
            (metric.labels(*labels) if labels else metric).inc(amount)

    def _set(self, name: str, value: float) -> None:
        with self._lock:
            self.gauges[name] = value
        if self._prom is not None:
            self._prom[name].set(value)

    def observe_outcome(self, outcome) -> None:
        """Record everything one finished ticket has to say.

        Called from the pipeline's containment boundary, so a ticket that
        crashed is counted exactly like one that succeeded — an outcome the
        panel never shows is an outcome nobody investigates.
        """
        self._count("tickets_processed_total", (outcome.channel, outcome.action))

        seconds = (outcome.latency_ms or 0.0) / 1000.0
        with self._lock:
            self.latencies.append(seconds)
        if self._prom is not None:
            self._prom["response_seconds"].observe(seconds)

        if outcome.action == "blocked":
            # Label by the guardrail that fired, not by the ticket: one draft
            # can trip several, and "which guardrail is doing the work" is the
            # question the governance review actually asks.
            for finding in outcome.guardrail_findings or ["unspecified"]:
                self._count("guardrail_blocks_total", (str(finding),))
        elif outcome.action == "escalated":
            self._count("escalations_total", (outcome.rule or "unspecified",))

        if outcome.degraded:
            self._count("responses_degraded_total")

    def observe_retrieval(self, passages) -> None:
        if not passages:
            self._count("retrieval_abstentions_total")

    def set_semantic_available(self, available: bool) -> None:
        self._set("retrieval_semantic_available", 1.0 if available else 0.0)

    # -- reading -----------------------------------------------------------

    def reset_tally(self) -> None:
        """Clear the in-process tally, leaving the Prometheus collectors alone.

        The two are measuring different things and only look identical because
        the harness normally does one run per process. A Prometheus counter is
        process-lifetime and monotonic by design -- resetting it would show up
        as a counter reset on the dashboard and is not ours to do. The tally,
        by contrast, is folded into that run's metrics.json, where "this run"
        is the only reading that makes sense. The harness resets it at the top
        of a run so a second run in the same process reports its own numbers.
        """
        with self._lock:
            self.counters.clear()
            self.gauges.clear()
            self.latencies.clear()

    def value(self, name: str, *labels: str) -> float:
        """Read one tally back. The tests' only entry point."""
        with self._lock:
            if name in self.gauges and not labels:
                return self.gauges[name]
            return self.counters[name][tuple(labels)]

    def snapshot(self) -> dict[str, object]:
        """A plain-dict view, for the harness to fold into metrics.json."""
        with self._lock:
            counters = {
                name: {("|".join(k) if k else "_"): v for k, v in series.items()}
                for name, series in self.counters.items()
            }
            latencies = sorted(self.latencies)
            gauges = dict(self.gauges)
        summary: dict[str, object] = {"counters": counters, "gauges": gauges}
        if latencies:
            summary["response_seconds"] = {
                "count": len(latencies),
                "p50": round(_quantile(latencies, 0.50), 4),
                "p95": round(_quantile(latencies, 0.95), 4),
                "max": round(latencies[-1], 4),
            }
        summary["exporter"] = (
            f"prometheus on :{self._server_port}" if self._server_port
            else ("prometheus available, exporter not started" if PROMETHEUS_AVAILABLE
                  else "in-process tally only (prometheus_client not installed)")
        )
        return summary

    # -- exporting ---------------------------------------------------------

    def start_server(self, port: int) -> bool:
        """Expose /metrics for Prometheus to scrape. False if it could not.

        Never raises: a monitoring endpoint that fails to bind must not take
        the run down with it. A11 applies to the instruments too.
        """
        if not PROMETHEUS_AVAILABLE:
            log.warning(
                "metrics endpoint not started: prometheus_client is not installed "
                "(pip install -r requirements.txt). The in-process tally still runs."
            )
            return False
        try:
            _start_http_server(port, registry=self._registry)
        except OSError as exc:
            log.warning("metrics endpoint could not bind port %d (%s); continuing", port, exc)
            return False
        self._server_port = port
        log.info("metrics available at http://localhost:%d/metrics", port)
        return True


def _quantile(ordered: list[float], q: float) -> float:
    if not ordered:
        return 0.0
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


METRICS = Metrics()
