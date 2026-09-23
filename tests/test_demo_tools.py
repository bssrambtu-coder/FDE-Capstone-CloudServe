"""The demonstration tools must show the system as it is.

These hold the recording tooling to the claims it makes on screen: the
guardrail step's scripted draft really is blocked by the real validator, the
demo page sends nothing a customer's ticket would not carry and loads nothing
from the network, the test report counts what unittest counts, and the
provisioned Grafana dashboard has not drifted from the importable one.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from src.config import CONFIG
from src.control import AutomationControl
from src.ingest import load_tickets
from src.monitoring import Metrics
from src.pipeline import Pipeline

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "Capstone_Pack/05_Datasets/validation_tickets.json"


def load_script(name: str):
    """Import a file from scripts/ without running its main()."""
    spec = importlib.util.spec_from_file_location(f"_script_{name}", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    logging.disable(logging.CRITICAL)  # the scripts configure logging on import
    return module


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


class GuardrailDemoIsReal(unittest.TestCase):
    """Step 3 claims the real validator withholds its scripted draft."""

    def test_scripted_draft_is_blocked_and_nothing_released(self):
        demo = load_script("demo")
        ticket = {t.ticket_id: t for t in load_tickets(VALIDATION)}[demo.GUARDRAIL]
        with tempfile.TemporaryDirectory() as tmp:
            control = AutomationControl(Path(tmp) / "paused")
            config = replace(CONFIG, kill_switch_path=str(control.path))
            base = Pipeline(config=config, control=control, metrics=Metrics())
            honest = base.process(ticket)
            if not honest.sources:
                self.skipTest("retrieval returned nothing for the guardrail ticket in this lane")
            scripted = Pipeline(config=config, control=control, metrics=Metrics(),
                                classifier=base.classifier, retriever=base.retriever,
                                provider=demo.Scripted(demo.scripted_bad_draft(honest.sources[0])))
            outcome = scripted.process(ticket)
        self.assertEqual(outcome.action, "blocked")
        self.assertIsNone(outcome.response, "a blocked draft must not be released")
        self.assertTrue(any(f.startswith("forbidden_claim") for f in outcome.guardrail_findings))

    def test_every_rehearsed_ticket_exists(self):
        demo = load_script("demo")
        ids = {t.ticket_id for t in load_tickets(VALIDATION)}
        for tid in [demo.SUCCESS[0], demo.GUARDRAIL] + [e[0] for e in demo.ESCALATIONS]:
            self.assertIn(tid, ids)


class TestReportCountsWhatUnittestCounts(unittest.TestCase):
    def test_method_names_read_as_sentences(self):
        report = load_script("test_report")
        self.assertEqual(report.sentence("test_private_data_blocks"), "Private data blocks")
        self.assertEqual(report.sentence("test_api_key_blocks"), "API key blocks")
        self.assertEqual(report.sentence("test_jsonl_and_wrapped_shapes_are_accepted"),
                         "JSONL and wrapped shapes are accepted")

    def test_inherited_tests_are_counted_once_as_distinct(self):
        report = load_script("test_report")
        rec = lambda cls, method, inherited="": {"module": "m", "cls": cls, "method": method,
                                                 "inherited_from": inherited}
        records = [rec("Control", "test_a"), rec("Api", "test_a", "Control"), rec("Api", "test_b")]
        self.assertEqual(report.distinct(records), 2)


class DashboardCopyIsInSync(unittest.TestCase):
    def test_provisioned_dashboard_matches_the_importable_source(self):
        sync = load_script("sync_grafana_dashboard")
        self.assertEqual(sync.TARGET.read_text(encoding="utf-8"), sync.render(),
                         "run: python scripts/sync_grafana_dashboard.py")

    def test_provisioned_dashboard_binds_the_provisioned_datasource(self):
        sync = load_script("sync_grafana_dashboard")
        dash = json.loads(sync.TARGET.read_text(encoding="utf-8"))
        self.assertNotIn("${DS_PROMETHEUS}", json.dumps(dash))
        uids = {p["datasource"]["uid"] for p in dash["panels"] if p.get("datasource")}
        self.assertEqual(uids, {sync.DATASOURCE_UID})
        provisioning = (ROOT / "monitoring/grafana/provisioning/datasources/prometheus.yml").read_text()
        self.assertIn(f"uid: {sync.DATASOURCE_UID}", provisioning)


class TrafficSendsOnlyCustomerFields(unittest.TestCase):
    def test_labels_and_history_are_never_sent(self):
        traffic = load_script("demo_traffic")
        self.assertNotIn("labels", traffic.FIELDS)
        self.assertNotIn("history", traffic.FIELDS)


try:
    from fastapi.testclient import TestClient
    from src.api import create_app
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, "API dependencies are optional")
class DemoPage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        config = replace(CONFIG, retrieval_backend="lexical", database_url=str(root / "d.db"),
                         kill_switch_path=str(root / "paused"))
        self.client = TestClient(create_app(config=config))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_page_loads_nothing_from_the_network(self):
        html = self.client.get("/demo").text
        self.assertIn("CloudServe", html)
        for external in ("https://", "http://cdn", "googleapis", "unpkg", "jsdelivr"):
            self.assertNotIn(external, html)

    def test_samples_carry_no_evaluation_labels(self):
        samples = self.client.get("/demo/samples").json()
        self.assertTrue(samples)
        for s in samples:
            self.assertNotIn("labels", s["ticket"])
            self.assertNotIn("history", s["ticket"])
            # And the API accepts them as-is, which it would not if a label slipped in.
            self.assertEqual(self.client.post("/tickets", json=s["ticket"]).status_code, 200)

    def test_recent_decisions_expose_no_ticket_text(self):
        self.client.post("/tickets", json={"ticket_id": "P-1", "channel": "chat", "body": "hello there"})
        rows = self.client.get("/demo/recent").json()
        self.assertEqual(rows[0]["ticket_id"], "P-1")
        self.assertEqual(set(rows[0]), {"id", "logged_at", "ticket_id", "channel", "action", "rule"})

    def test_the_page_offers_no_pause_control(self):
        """The README commits to no remote administration endpoint."""
        routes = {r.path for r in self.client.app.routes}
        self.assertFalse({p for p in routes if "pause" in p or "control" in p or "disable" in p})


if __name__ == "__main__":
    unittest.main()
