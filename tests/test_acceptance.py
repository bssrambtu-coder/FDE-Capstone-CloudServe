"""Tests named by the acceptance criterion each one covers.

Runs on the standard library alone so `python -m unittest` works on a clean
checkout with nothing installed (A12). pytest will collect these too.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from evaluation import harness
from src.classify import Classifier
from src.generate import generate
from src.ingest import load_tickets, normalise, normalise_channel
from src.models import CHANNELS, Draft, Outcome, Passage, Ticket
from src.monitoring import Metrics
from src.pipeline import Pipeline
from src.providers import (Cache, FaultInjector, ProviderUnavailable,
                           ResilientProvider, StubProvider)
from src.retrieve import LexicalRetriever, load_corpus
from src.route import POLICY_ESCALATE, route
from src.validate import validate

def setUpModule():
    """The resilience tests deliberately induce provider failures, which the
    pipeline logs as warnings. Silence them so a passing run is quiet."""
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


CORPUS = "Capstone_Pack/05_Datasets/documentation.json"
TICKETS = "Capstone_Pack/05_Datasets/validation_tickets.json"


def a_ticket(**kw) -> Ticket:
    base = dict(ticket_id="T-1", channel="email", subject="s", body="b",
                received_at="", customer_id="C", customer_tier="standard",
                customer_region="europe", language_fluency="fluent")
    base.update(kw)
    return Ticket(**base)


class ScriptedProvider:
    """Returns exactly what the test tells it to, so a guardrail can be
    driven from the provider side the way a real model would drive it."""

    def __init__(self, reply: str):
        self.reply = reply

    def complete(self, prompt: str, *, max_tokens: int = 512) -> str:
        return self.reply


class A2Ingest(unittest.TestCase):
    def test_all_four_channels_normalise(self):
        for channel in CHANNELS:
            t = normalise({"ticket_id": "X", "channel": channel, "body": "hello"})
            self.assertEqual(t.channel, channel)

    def test_channel_aliases_map_to_the_four(self):
        for alias, expected in [("Live_Chat", "chat"), ("e-mail", "email"),
                                ("community_forum", "forum"), ("docs", "docs_comment")]:
            self.assertEqual(normalise_channel(alias), expected)

    def test_survives_missing_fields_and_junk(self):
        for record in [{}, {"body": None}, {"subject": 123}, "not a dict", []]:
            t = normalise(record)
            self.assertTrue(t.ticket_id)
            self.assertIsInstance(t.text, str)

    def test_strips_control_characters_and_collapses_whitespace(self):
        t = normalise({"body": "a​b\r\n\r\n   c    d "})
        self.assertEqual(t.body, "ab\nc d")

    def test_real_file_covers_every_channel(self):
        channels = {t.channel for t in load_tickets(TICKETS)}
        self.assertEqual(channels, set(CHANNELS))


class A3Classify(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clf = Classifier()

    def test_confidence_is_a_probability(self):
        for t in list(load_tickets(TICKETS))[:20]:
            c = self.clf.classify(t.text)
            self.assertGreaterEqual(c.confidence, 0.0)
            self.assertLessEqual(c.confidence, 1.0)

    def test_never_states_certainty(self):
        # Calibration is Laplace-smoothed, so 1.0 should be unreachable.
        for t in list(load_tickets(TICKETS))[:40]:
            self.assertLess(self.clf.classify(t.text).confidence, 1.0)

    def test_returns_fallback_instead_of_raising(self):
        for text in ["", "   ", "zzz qqq wwww", " "]:
            c = self.clf.classify(text)
            self.assertEqual(c.intent, "unclear_request")
            self.assertEqual(c.confidence, 0.0)

    def test_records_alternatives_considered(self):
        c = self.clf.classify("my api key returns 401 unauthorized")
        self.assertTrue(c.alternatives, "alternatives must be recorded, not only the choice")


class A4Retrieve(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = load_corpus(CORPUS)
        cls.ids = {d["doc_id"] for d in cls.corpus}
        cls.r = LexicalRetriever(cls.corpus, floor=0.42)

    def test_sources_resolve_to_the_real_corpus(self):
        for t in list(load_tickets(TICKETS))[:30]:
            for p in self.r.search(t.text, top_k=5):
                self.assertIn(p.doc_id, self.ids)

    def test_returns_nothing_rather_than_something_irrelevant(self):
        self.assertEqual(self.r.search("the quick brown fox jumped over a lazy dog"), [])
        self.assertEqual(self.r.search(""), [])
        self.assertEqual(self.r.search("zzzz qqqq wwww"), [])

    def test_a_query_matching_one_word_of_many_scores_low(self):
        """The normalising mass counts terms the corpus has never seen, so
        coverage matters: matching one word out of nine must not score as if
        it matched all nine."""
        scored = LexicalRetriever(self.corpus, floor=0.0)
        hits = scored.search("the quick brown fox jumped over a lazy dog")
        self.assertTrue(hits, "expected a scored-but-rejected result")
        self.assertLess(hits[0].score, 0.42)

    def test_results_are_ranked_and_capped(self):
        hits = self.r.search("api key 401 unauthorized rotation", top_k=3)
        self.assertLessEqual(len(hits), 3)
        self.assertEqual([h.score for h in hits], sorted((h.score for h in hits), reverse=True))


def _chroma_available() -> bool:
    try:
        import chromadb  # noqa: F401
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return True


class A4HybridRetrieval(unittest.TestCase):
    """The hybrid backend is the configuration the fairness condition holds
    under. It is optional, so these skip rather than fail when the extras are
    not installed - the zero-dependency default must still pass A12."""

    @unittest.skipUnless(_chroma_available(), "chromadb/sentence-transformers not installed")
    def test_hybrid_ranks_and_abstains(self):
        from src.retrieve import HybridRetriever
        corpus = load_corpus(CORPUS)
        r = HybridRetriever(corpus, gate=0.60)
        self.assertTrue(r.semantic_available, "semantic backend should be live")
        hits = r.search("my api key returns 401 unauthorized", top_k=3)
        self.assertTrue(hits)
        self.assertIn("DOC-AUTH-004", {h.doc_id for h in hits})
        self.assertEqual(r.search("the quick brown fox jumped over a lazy dog"), [])

    def test_hybrid_degrades_to_lexical_without_the_extras(self):
        """Constructed with an unreachable store, it must still answer rather
        than raise - and must say so, because degrading reopens R-07."""
        from src.retrieve import HybridRetriever
        r = HybridRetriever(load_corpus(CORPUS), gate=0.60, path="/dev/null/nope")
        hits = r.search("my api key returns 401 unauthorized", top_k=3)
        self.assertTrue(hits, "must fall back rather than return nothing")


class A5RoutingDeterminism(unittest.TestCase):
    def test_same_input_yields_the_same_decision(self):
        pipe = Pipeline()
        for t in list(load_tickets(TICKETS))[:25]:
            first, second = pipe.process(t), pipe.process(t)
            self.assertEqual(
                (first.action, first.rule, first.intent, first.confidence, first.sources),
                (second.action, second.rule, second.intent, second.confidence, second.sources),
                f"{t.ticket_id} routed differently on a second pass",
            )

    def test_policy_intents_escalate_whatever_the_confidence(self):
        from src.models import Classification
        for intent in POLICY_ESCALATE:
            r = route(a_ticket(), Classification(intent, "high", 0.999),
                      [Passage("DOC-A", "t", "text", 0.9)], threshold=0.8)
            self.assertEqual(r.action, "escalate")
            self.assertEqual(r.rule, "policy_intent")

    def test_reason_is_readable_prose(self):
        from src.models import Classification
        r = route(a_ticket(), Classification("billing_query", "low", 0.9),
                  [Passage("DOC-BILL-001", "t", "text", 0.9)], threshold=0.8)
        self.assertGreater(len(r.reason.split()), 6)


class A6Citations(unittest.TestCase):
    def test_citations_only_reference_retrieved_passages(self):
        pipe = Pipeline()
        for t in list(load_tickets(TICKETS))[:40]:
            o = pipe.process(t)
            self.assertTrue(set(o.citations) <= set(o.sources),
                            f"{t.ticket_id} cited {o.citations} but retrieved {o.sources}")

    def test_a_provider_draft_without_a_resolvable_citation_is_discarded(self):
        passages = [Passage("DOC-AUTH-004", "Keys", "Rotate the key in the dashboard.", 0.9)]
        draft, degraded = generate(
            a_ticket(body="key rotation"), passages,
            provider=ScriptedProvider("Do this thing. [DOC-MADE-999]"),
        )
        self.assertTrue(degraded)
        self.assertTrue(set(draft.citations) <= {"DOC-AUTH-004"})


class A7Guardrails(unittest.TestCase):
    def setUp(self):
        self.passages = [Passage("DOC-A", "T", "Rotate the key under settings.", 0.9)]

    def test_private_data_blocks(self):
        v = validate(Draft("Mail admin@acme.com. [DOC-A]", ["DOC-A"]), self.passages)
        self.assertTrue(v.blocked)
        self.assertIn("private_data:email_address", v.findings)

    def test_api_key_blocks(self):
        v = validate(Draft("Use sk-abcdefghijklmnop123 now. [DOC-A]", ["DOC-A"]), self.passages)
        self.assertTrue(v.blocked)

    def test_forbidden_commitment_blocks(self):
        v = validate(Draft("A refund has been issued. [DOC-A]", ["DOC-A"]), self.passages)
        self.assertTrue(v.blocked)
        self.assertTrue(any(f.startswith("forbidden_claim") for f in v.findings))

    def test_unresolvable_citation_blocks(self):
        v = validate(Draft("See here. [DOC-NOPE-1]", ["DOC-NOPE-1"]), self.passages)
        self.assertTrue(v.blocked)

    def test_a_grounded_answer_is_not_blocked(self):
        v = validate(Draft("Rotate the key under settings. [DOC-A]", ["DOC-A"]), self.passages)
        self.assertFalse(v.blocked)
        self.assertEqual(v.findings, [])

    def test_guardrail_blocks_end_to_end_through_the_pipeline(self):
        """The criterion is that a response is blocked rather than sent, so
        this drives the whole pipeline rather than the validator alone."""
        pipe = Pipeline(provider=ScriptedProvider(
            "Your refund has been processed, contact billing@cloudserve.io. [DOC-AUTH-004]"
        ))
        engineered = a_ticket(
            ticket_id="ENGINEERED-1",
            body="my api key returns 401 unauthorized when i rotate it",
        )
        o = pipe.process(engineered)
        self.assertEqual(o.action, "blocked")
        self.assertIsNone(o.response, "a blocked response must not be released")
        self.assertTrue(o.guardrail_findings)

    def test_validator_records_what_it_checked_even_when_it_passes(self):
        v = validate(Draft("Rotate the key under settings. [DOC-A]", ["DOC-A"]), self.passages)
        for key in ("private_data", "forbidden_claims", "citations_resolve"):
            self.assertIn(key, v.checks)


class A11Resilience(unittest.TestCase):
    def _pipeline(self, mode):
        provider = ResilientProvider(
            FaultInjector(StubProvider(), mode=mode),
            cache=Cache(Path(tempfile.mkdtemp()) / "c.json"),
            max_retries=2, sleep=lambda _s: None,
        )
        return Pipeline(provider=provider), provider

    def test_every_failure_mode_degrades_instead_of_crashing(self):
        tickets = list(load_tickets(TICKETS))[:6]
        for mode in ("outage", "timeout", "ratelimit", "malformed"):
            pipe, _ = self._pipeline(mode)
            for t in tickets:
                o = pipe.process(t)
                self.assertIn(o.action, {"answered", "escalated", "blocked"})
                self.assertIsNone(o.error, f"{mode} produced an unhandled error")

    def test_circuit_breaker_opens_and_stops_calling_a_dead_provider(self):
        _, provider = self._pipeline("outage")
        for i in range(8):
            try:
                provider.complete(f"prompt {i}")
            except ProviderUnavailable:
                pass
        self.assertTrue(provider.breaker_open)

    def test_cache_makes_a_repeated_prompt_free(self):
        provider = ResilientProvider(
            FaultInjector(StubProvider(), mode=""),
            cache=Cache(Path(tempfile.mkdtemp()) / "c.json"), sleep=lambda _s: None)
        a = provider.complete("same prompt")
        b = provider.complete("same prompt")
        self.assertEqual(a, b)
        self.assertEqual(provider.cache_hits, 1)

    def test_malformed_and_empty_tickets_do_not_stop_the_run(self):
        pipe = Pipeline()
        for body in ["", " ", "x" * 20000, "\U0001f642\U0001f642"]:
            o = pipe.process(a_ticket(body=body))
            self.assertIsNone(o.error)


class A9A10HarnessRun(unittest.TestCase):
    def test_unattended_run_over_an_unseen_file_produces_everything(self):
        """The harness must accept an input path it has never seen, process
        every ticket, log every decision and write a metrics report."""
        source = list(load_tickets(TICKETS))[:15]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            unseen = tmp / "a-file-this-repo-has-never-seen.json"
            unseen.write_text(json.dumps([t.raw for t in source]), encoding="utf-8")
            out = tmp / "results"
            code = harness.main([
                "--input", str(unseen), "--output", str(out),
                "--db", str(tmp / "decisions.db"), "--log-level", "ERROR",
            ])
            self.assertEqual(code, 0)

            results = json.loads((out / "results.json").read_text())
            self.assertEqual(len(results), len(source))
            # every ticket produced an answer or a logged escalation, none dropped
            self.assertEqual({r["ticket_id"] for r in results}, {t.ticket_id for t in source})
            for r in results:
                self.assertIn(r["action"], {"answered", "escalated", "blocked"})

            metrics = json.loads((out / "metrics.json").read_text())
            for group in ("volume", "business", "technical", "governance"):
                self.assertIn(group, metrics)
            self.assertTrue((out / "metrics.md").read_text().startswith("# Evaluation"))

            # A8: logged decisions reconcile against tickets processed
            conn = sqlite3.connect(tmp / "decisions.db")
            logged = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
            self.assertEqual(logged, len(source))
            row = conn.execute(
                "SELECT input_text, intent, confidence, sources, action, reason"
                " FROM decisions LIMIT 1").fetchone()
            self.assertTrue(all(f is not None for f in row),
                            "every required decision-log field must be populated")
            conn.close()

    def test_jsonl_and_wrapped_shapes_are_accepted(self):
        source = [t.raw for t in list(load_tickets(TICKETS))[:3]]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "w.json").write_text(json.dumps({"tickets": source}), encoding="utf-8")
            self.assertEqual(len(list(load_tickets(tmp / "w.json"))), 3)
            (tmp / "l.jsonl").write_text("\n".join(json.dumps(r) for r in source), encoding="utf-8")
            self.assertEqual(len(list(load_tickets(tmp / "l.jsonl"))), 3)

    def test_missing_input_exits_non_zero_without_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                harness.main(["--input", str(Path(tmp) / "nope.json"),
                              "--output", str(Path(tmp) / "o"), "--log-level", "CRITICAL"]),
                2,
            )


class B12Monitoring(unittest.TestCase):
    """Monitoring is instrumentation, so the tests drive the pipeline and read
    the tally back rather than asserting on the collectors directly.

    Every assertion here uses the in-process tally, which is present whether or
    not prometheus_client is installed. That is deliberate: the instrumentation
    has to be covered on a clean checkout, not only where the extras happen to
    be available.
    """

    def test_every_finished_ticket_is_counted_by_channel_and_outcome(self):
        m = Metrics()
        pipe = Pipeline(metrics=m)
        for t in list(load_tickets(TICKETS))[:12]:
            pipe.process(t)
        total = sum(m.counters["tickets_processed_total"].values())
        self.assertEqual(total, 12, "a ticket the panel never counts is one nobody investigates")

    def test_a_ticket_that_crashes_is_counted_like_any_other(self):
        """The containment boundary must not swallow the ticket from the
        dashboard as well as from the run."""

        class Boom:
            def classify(self, text):
                raise RuntimeError("induced")

        m = Metrics()
        outcome = Pipeline(classifier=Boom(), metrics=m).process(a_ticket())
        self.assertEqual(outcome.rule, "pipeline_error")
        self.assertEqual(m.value("tickets_processed_total", "email", "escalated"), 1)
        self.assertEqual(m.value("escalations_total", "pipeline_error"), 1)

    def test_a_block_is_counted_against_the_guardrail_that_fired(self):
        m = Metrics()
        pipe = Pipeline(metrics=m, provider=ScriptedProvider(
            "Your refund has been processed, contact billing@cloudserve.io. [DOC-AUTH-004]"
        ))
        o = pipe.process(a_ticket(ticket_id="ENGINEERED-1",
                                  body="my api key returns 401 unauthorized when i rotate it"))
        self.assertEqual(o.action, "blocked")
        self.assertTrue(o.guardrail_findings)
        for finding in o.guardrail_findings:
            self.assertEqual(m.value("guardrail_blocks_total", finding), 1)

    def test_retrieval_abstention_is_counted(self):
        m = Metrics()
        m.observe_retrieval([])
        m.observe_retrieval([Passage("DOC-A", "T", "text", 0.9)])
        self.assertEqual(m.value("retrieval_abstentions_total"), 1)

    def test_the_semantic_gauge_reports_what_retrieval_actually_has(self):
        """0 whenever retrieval is lexical-only, chosen or degraded. Both mean
        the fairness condition no longer holds (R-07)."""
        m = Metrics()
        Pipeline(metrics=m, retriever=LexicalRetriever(load_corpus(CORPUS)))
        self.assertEqual(m.value("retrieval_semantic_available"), 0.0)

        m2 = Metrics()

        class FakeHybrid:
            name, semantic_available = "hybrid-rrf", True

            def search(self, query, *, top_k=5):
                return []

        Pipeline(metrics=m2, retriever=FakeHybrid())
        self.assertEqual(m2.value("retrieval_semantic_available"), 1.0)

    def test_the_exporter_never_takes_the_run_down_with_it(self):
        """A11 applies to the instruments. A port that cannot bind, or a
        missing dependency, is a warning and nothing more."""
        m = Metrics()
        self.assertIsInstance(m.start_server(1), bool)  # port 1 needs root

    def test_the_snapshot_survives_into_the_metrics_report(self):
        source = [t.raw for t in list(load_tickets(TICKETS))[:8]]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "in.json").write_text(json.dumps(source), encoding="utf-8")
            rc = harness.main(["--input", str(tmp / "in.json"), "--output", str(tmp / "out"),
                               "--db", str(tmp / "d.db"), "--log-level", "CRITICAL"])
            self.assertEqual(rc, 0)
            report = json.loads((tmp / "out" / "metrics.json").read_text())
        self.assertIn("monitoring", report)
        counted = sum(report["monitoring"]["counters"]["tickets_processed_total"].values())
        self.assertEqual(counted, 8)
        self.assertIn("retrieval_semantic_available", report["monitoring"]["gauges"])

    def test_the_dashboard_only_plots_metrics_the_system_exports(self):
        """A dashboard panel querying a metric nobody emits is a blank panel
        during an incident, which is worse than no panel at all."""
        dashboard = json.loads(Path("monitoring/grafana_dashboard.json").read_text())
        exported = set(Metrics().counters) | {
            "tickets_processed_total", "response_seconds", "guardrail_blocks_total",
            "escalations_total", "retrieval_abstentions_total",
            "responses_degraded_total", "retrieval_semantic_available",
        }
        referenced = set()
        for panel in dashboard["panels"]:
            for tgt in panel.get("targets", []):
                for token in re.findall(r"[a-z_][a-z0-9_]*", tgt["expr"]):
                    if token.endswith("_bucket"):
                        token = token[: -len("_bucket")]
                    if token.endswith("_total") or token in exported:
                        referenced.add(token)
        self.assertTrue(referenced, "the dashboard queries nothing")
        self.assertEqual(referenced - exported, set(),
                         "dashboard references a metric the system does not export")


if __name__ == "__main__":
    unittest.main()
