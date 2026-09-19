"""Regression tests for release safety, independent of model downloads."""
import unittest
from unittest.mock import Mock

from src.config import Config
from src.generate import generate
from src.ingest import normalise
from src.models import Classification, Draft, Passage
from src.monitoring import Metrics
from src.pipeline import Pipeline
from src.route import route
from src.validate import validate


class SafetyRegressions(unittest.TestCase):
    def setUp(self):
        self.ticket = normalise({"ticket_id": "SAFE-1", "body": "How do I rotate a key?"})
        self.passages = [Passage("DOC-AUTH-004", "Keys", "Rotate the key under settings.", 0.9)]
        self.unsupported = "Purple elephants deliver chocolate parcels across distant galaxies. [DOC-AUTH-004]"

    def pipeline(self, text, intent="api_key_issue"):
        return Pipeline(config=Config(retrieval_backend="lexical"),
                        classifier=Mock(classify=Mock(return_value=Classification(intent, "medium", .9))),
                        retriever=Mock(search=Mock(return_value=self.passages)),
                        provider=Mock(complete=Mock(return_value=text)), metrics=Metrics())

    def test_unsupported_claim_is_withheld_end_to_end(self):
        result = self.pipeline(self.unsupported).process(self.ticket)
        self.assertEqual(result.action, "blocked")
        self.assertIsNone(result.response)
        self.assertTrue(any(x.startswith("unsupported_claims") for x in result.guardrail_findings))

    def test_invented_citation_cannot_hide_beside_valid_citation(self):
        text = "Rotate the key under settings. [DOC-AUTH-004] [DOC-FAKE-999]"
        draft, degraded = generate(self.ticket, self.passages,
                                   provider=Mock(complete=Mock(return_value=text)))
        self.assertFalse(degraded)
        self.assertIn("DOC-FAKE-999", draft.citations)
        self.assertTrue(validate(draft, self.passages).blocked)

    def test_text_is_checked_even_when_citation_metadata_omits_fabrication(self):
        draft = Draft("Rotate the key under settings. [DOC-AUTH-004] [DOC-FAKE-999]",
                      ["DOC-AUTH-004"])
        self.assertIn("unresolvable_citation:DOC-FAKE-999", validate(draft, self.passages).findings)

    def test_missing_evidence_blocks_but_abstention_is_allowed(self):
        self.assertTrue(validate(Draft("Some answer"), []).blocked)
        self.assertTrue(validate(Draft("Rotate the key under settings."), self.passages).blocked)
        self.assertFalse(validate(Draft("Unable to answer.", abstained=True), []).blocked)

    def test_truncated_provider_draft_falls_back_to_complete_grounded_text(self):
        result = self.pipeline(
            "Rotate the key under settings, then ask the account owner"
        ).process(self.ticket)
        self.assertEqual(result.action, "answered")
        self.assertTrue(result.degraded)
        self.assertTrue(result.response.rstrip().endswith("."))

    def test_validator_blocks_incomplete_text_as_defence_in_depth(self):
        verdict = validate(Draft("Rotate the key under settings [DOC-AUTH-004]",
                                 ["DOC-AUTH-004"]), self.passages)
        self.assertTrue(verdict.blocked)
        self.assertIn("incomplete_response", verdict.findings)

    def test_escalation_draft_is_also_validated(self):
        result = self.pipeline("Contact private@example.com. [DOC-AUTH-004]",
                               intent="security_incident").process(self.ticket)
        self.assertEqual(result.action, "escalated")
        self.assertIsNone(result.response)
        self.assertIn("private_data:email_address", result.guardrail_findings)

    def test_nonfinite_or_out_of_range_confidence_never_auto_answers(self):
        for confidence in (float("nan"), float("inf"), -.1, 1.1):
            with self.subTest(confidence=confidence):
                result = route(self.ticket, Classification("api_key_issue", "low", confidence),
                               self.passages, threshold=.8)
                self.assertEqual(result.rule, "invalid_confidence")

    def test_invalid_threshold_never_auto_answers(self):
        for threshold in (float("nan"), float("inf"), -.1, 1.1):
            result = route(self.ticket, Classification("api_key_issue", "low", .9),
                           self.passages, threshold=threshold)
            self.assertEqual(result.rule, "invalid_threshold")
