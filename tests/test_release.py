"""Release regressions for audit durability, monitoring and policy boundaries."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from src.decision_log import DecisionLog
from src.models import Outcome, Classification, Passage
from src.ingest import normalise
from src.route import route
from src.providers import Cache, ProviderIncomplete, ResilientProvider
from src.validate import validate
from src.models import Draft


class ReleaseTests(unittest.TestCase):
    def test_duplicate_source_ids_are_distinct_persistent_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = DecisionLog(str(Path(tmp) / 'audit.db'))
            try:
                run = log.start_run(input_path='test', output_path='test', config={})
                o = Outcome('same', 'chat', 'answered', 'reason', 'rule', audit_details={'validation': {'blocked': False}})
                first = log.record(o, 'original full text')
                log.record(o, 'second occurrence')
                log.mark_paused(first)
                log.finish_run(2)
            finally:
                log.close()
            reopened = DecisionLog(str(Path(tmp) / 'audit.db'))
            try:
                self.assertEqual(reopened.count(run), 2)
                rows = reopened.conn.execute('SELECT action, audit_details FROM decisions ORDER BY id').fetchall()
                self.assertEqual([r[0] for r in rows], ['escalated', 'answered'])
                self.assertFalse(json.loads(rows[1][1])['validation']['blocked'])
            finally:
                reopened.close()

    def test_incomplete_completion_is_not_cached_or_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            inner = Mock(complete=Mock(side_effect=ProviderIncomplete('length')))
            wrapped = ResilientProvider(inner, cache=Cache(Path(tmp)/'cache.json'), sleep=lambda _: None)
            with self.assertRaises(ProviderIncomplete):
                wrapped.complete('question')
            self.assertEqual(wrapped.calls, 1)
            self.assertEqual(wrapped.cache._data, {})

    def test_sensitive_policy_ignores_gold_labels_and_customer_group(self):
        for text in ['Need to rotate an exposed key', 'Please refund this disputed charge']:
            t = normalise({'body': text, 'labels': {'expected_route': 'auto_respond'}, 'customer_tier': 'enterprise'})
            result = route(t, Classification('api_key_issue', 'low', .99),
                           [Passage('DOC-A', 'Keys', 'Rotate keys.', .9)], threshold=.8)
            self.assertEqual(result.rule, 'sensitive_request')

    def test_wrong_source_after_sentence_is_not_attached_to_next_claim(self):
        passages = [Passage('DOC-A', 'Keys', 'Rotate production credentials securely using administrator settings.', .9),
                    Passage('DOC-B', 'Billing', 'Invoice billing payment balance account statement details.', .8)]
        v = validate(Draft('Rotate production credentials securely using administrator settings. [DOC-B]', ['DOC-B']), passages)
        self.assertTrue(v.blocked)

    def test_reason_never_states_certainty(self):
        """A3: a calibrated 0.9999 must not be written as "100% confidence"."""
        from src.models import Ticket
        ticket = Ticket('T', 'chat', '', 'rotate key', '', 'C', 'standard', 'europe', 'fluent')
        passages = [Passage('DOC-A', 'Keys', 'Rotate the key under settings.', .9)]
        answered = route(ticket, Classification('api_usage_question', 'low', .9999), passages, threshold=.8)
        self.assertEqual(answered.rule, 'confident_and_supported')
        self.assertIn('99.99% confidence', answered.reason)
        self.assertNotIn('100%', answered.reason)
        low = route(ticket, Classification('api_usage_question', 'low', .996), passages, threshold=.999)
        self.assertEqual(low.rule, 'low_confidence')
        self.assertNotIn('100%', low.reason)

    def test_unclassified_outcomes_record_no_intent(self):
        """Paused and crashed tickets are never classified, so they must not be
        logged as unclear_request: that placeholder inflates the class in any
        audit and scores as a wrong prediction when labels are present."""
        from dataclasses import replace
        from src.config import Config
        from src.control import AutomationControl
        from src.models import Ticket
        from src.monitoring import Metrics
        from src.pipeline import Pipeline
        ticket = Ticket('T', 'chat', '', 'rotate key', '', 'C', 'standard', 'europe', 'fluent')
        with tempfile.TemporaryDirectory() as tmp:
            control = AutomationControl(Path(tmp) / 'paused')
            config = replace(Config(), retrieval_backend='lexical', kill_switch_path=str(control.path))
            control.disable()
            paused = Pipeline(config=config, control=control, metrics=Metrics(),
                              classifier=Mock(), retriever=Mock()).process(ticket)
            control.enable()
            crashed = Pipeline(config=config, control=control, metrics=Metrics(), retriever=Mock(),
                               classifier=Mock(classify=Mock(side_effect=RuntimeError('boom')))).process(ticket)
        for o, rule in ((paused, 'automation_paused'), (crashed, 'pipeline_error')):
            self.assertEqual(o.rule, rule)
            self.assertIsNone(o.intent)
            self.assertIsNone(o.confidence)
