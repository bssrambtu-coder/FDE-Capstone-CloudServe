"""Local API contracts; skipped in the dependency-free test lane."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from src.config import Config
from src.control import AutomationControl
from src.ingest import normalise
from src.models import Classification, Passage
from src.monitoring import Metrics
from src.pipeline import Pipeline

try:
    from fastapi.testclient import TestClient
    from src.api import create_app
except ImportError:
    TestClient = None


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.control = AutomationControl(self.root / "paused")
        self.config = replace(Config(), retrieval_backend="lexical",
                              database_url=str(self.root / "decisions.db"),
                              kill_switch_path=str(self.control.path))
        self.provider = Mock(complete=Mock(return_value="Rotate the key under settings. [DOC-AUTH-004]"))
        self.pipeline = Pipeline(config=self.config, control=self.control,
            classifier=Mock(classify=Mock(return_value=Classification("api_key_issue", "low", .95))),
            retriever=Mock(search=Mock(return_value=[Passage("DOC-AUTH-004", "Keys",
                                      "Rotate the key under settings.", .9)])),
            provider=self.provider, metrics=Metrics())
        self.payload = {"ticket_id": "API-1", "channel": "chat", "body": "How do I rotate my key?"}

    def test_pause_survives_new_control_and_skips_provider(self):
        self.control.disable()
        self.assertTrue(AutomationControl(self.control.path).disabled)
        result = self.pipeline.process(normalise(self.payload))
        self.assertEqual(result.rule, "automation_paused")
        self.assertIsNone(result.response)
        self.provider.complete.assert_not_called()
        self.control.enable()
        self.assertFalse(self.control.disabled)

    def test_pause_during_generation_withholds_reply(self):
        def generate(*args, **kwargs):
            self.control.disable()
            return "Rotate the key under settings. [DOC-AUTH-004]"
        self.provider.complete.side_effect = generate
        result = self.pipeline.process(normalise(self.payload))
        self.assertEqual(result.rule, "automation_paused")
        self.assertIsNone(result.response)

    def test_sqlite_absolute_path_is_preserved(self):
        self.assertEqual(replace(self.config, database_url="sqlite:////tmp/test.db").sqlite_path,
                         "/tmp/test.db")


@unittest.skipIf(TestClient is None, "API dependencies are optional")
class ApiTests(ControlTests):
    def client(self):
        return TestClient(create_app(config=self.config, pipeline=self.pipeline))

    def test_answer_logged_and_repeated_id_has_separate_audit(self):
        with self.client() as client:
            for _ in range(2):
                response = client.post("/tickets", json=self.payload)
                self.assertEqual(response.status_code, 200)
                self.assertIsNotNone(response.json()["customer_response"])
                self.assertIsNone(response.json()["agent_draft"])
            self.assertEqual(client.get("/health").status_code, 200)
            self.assertEqual(client.get("/metrics").status_code, 200)
            prom = client.get("/metrics/prometheus")
            self.assertEqual(prom.status_code, 200)
            self.assertIn("tickets_processed_total", prom.text)
        with closing(sqlite3.connect(self.config.sqlite_path)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM decisions").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT count(DISTINCT run_id) FROM decisions").fetchone()[0], 2)

    def test_rejects_gold_labels_empty_body_and_invalid_channel(self):
        with self.client() as client:
            for update in ({"labels": {"intent": "gold"}}, {"body": " "}, {"channel": "sms"}):
                self.assertEqual(client.post("/tickets", json={**self.payload, **update}).status_code, 422)
        self.provider.complete.assert_not_called()

    def test_paused_ticket_logged_without_customer_reply(self):
        self.control.disable()
        with self.client() as client:
            self.assertTrue(client.get("/health").json()["automatic_replies_paused"])
            result = client.post("/tickets", json=self.payload).json()
            self.assertEqual(result["action"], "escalated")
            self.assertIsNone(result["customer_response"])
        with closing(sqlite3.connect(self.config.sqlite_path)) as conn:
            self.assertEqual(conn.execute("SELECT rule FROM decisions").fetchone()[0], "automation_paused")

    def test_logging_failure_releases_no_reply(self):
        with self.client() as client, patch("src.api.DecisionLog", side_effect=OSError("private detail")):
            response = client.post("/tickets", json=self.payload)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private detail", response.text)
        self.assertNotIn("Rotate", response.text)

    def test_escalated_draft_is_not_a_customer_response(self):
        self.pipeline.classifier.classify.return_value = Classification("security_incident", "high", .95)
        with self.client() as client:
            result = client.post("/tickets", json=self.payload).json()
        self.assertEqual(result["action"], "escalated")
        self.assertIsNone(result["customer_response"])
        self.assertIsNotNone(result["agent_draft"])
