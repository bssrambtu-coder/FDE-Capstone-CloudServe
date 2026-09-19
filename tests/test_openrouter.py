"""Offline contract tests: never send credentials or requests to a service."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from src.config import load_local_env
from src.providers import (OpenRouterProvider, ProviderIncomplete, ProviderUnavailable,
                           ProviderTimeout, RateLimited)


class OpenRouterTests(unittest.TestCase):
    def client(self):
        return OpenRouterProvider(api_key="test-placeholder", model="test/model", timeout=4)

    @patch("src.providers.urllib.request.urlopen")
    def test_request_and_response_contract(self, send):
        send.return_value.__enter__.return_value.read.return_value = json.dumps(
            {"choices": [{"message": {"content": "A cited answer."},
                           "finish_reason": "stop"}]}).encode()
        self.assertEqual(self.client().complete("Question", max_tokens=60), "A cited answer.")
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/chat/completions")
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "test/model")
        self.assertEqual(payload["max_tokens"], 60)
        self.assertEqual(send.call_args.kwargs["timeout"], 4)

    @patch("src.providers.urllib.request.urlopen")
    def test_rate_limit_and_http_errors_do_not_disclose_payloads(self, send):
        for status, error in ((429, RateLimited), (401, ProviderUnavailable), (503, ProviderUnavailable)):
            send.side_effect = HTTPError("url", status, "sensitive upstream message", {}, None)
            with self.assertRaises(error) as raised:
                self.client().complete("Question")
            self.assertNotIn("sensitive", str(raised.exception))

    @patch("src.providers.urllib.request.urlopen")
    def test_timeout(self, send):
        send.side_effect = TimeoutError()
        with self.assertRaises(ProviderTimeout):
            self.client().complete("Question")

    @patch("src.providers.urllib.request.urlopen")
    def test_malformed_response_is_a_provider_failure(self, send):
        for body in (b"not JSON", b"{}", b'{"choices": []}'):
            send.return_value.__enter__.return_value.read.return_value = body
            with self.assertRaises(ProviderUnavailable):
                self.client().complete("Question")

    @patch("src.providers.urllib.request.urlopen")
    def test_length_limited_or_mid_sentence_response_is_rejected(self, send):
        for content, reason in (("This was cut off", "length"),
                                ("This was cut off", "stop"),
                                ("Complete answer.", None)):
            send.return_value.__enter__.return_value.read.return_value = json.dumps(
                {"choices": [{"message": {"content": content},
                               "finish_reason": reason}]}).encode()
            with self.assertRaises(ProviderIncomplete):
                self.client().complete("Question")

    def test_missing_key_fails_before_network_access(self):
        for key in ("", "your_key_here"):
            with self.assertRaises(ProviderUnavailable):
                OpenRouterProvider(api_key=key, model="test/model")

    def test_env_loading_preserves_process_values_and_does_not_expand_text(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"TEST_EXISTING": "process"}):
            path = Path(tmp) / ".env"
            path.write_text('TEST_EXISTING=file\nTEST_LITERAL="$(do-not-execute)"\n', encoding="utf-8")
            load_local_env(path)
            self.assertEqual(os.environ["TEST_EXISTING"], "process")
            self.assertEqual(os.environ["TEST_LITERAL"], "$(do-not-execute)")
