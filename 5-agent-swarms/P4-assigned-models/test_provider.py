from pathlib import Path
import tempfile
import types
import unittest

import provider


class FakeLedger:
    def __init__(self, allow=True):
        self.config = {"model_ids": {"luna": "us.openai.gpt-6-luna"}}
        self.allow, self.events = allow, []

    def reserve(self, model, request, maximum, metadata):
        self.events.append(("reserve", model, request, maximum, metadata))
        return "key-1" if self.allow else None

    def finish(self, key, usage=None, error=None):
        self.events.append(("finish", key, usage, error))
        return {"event": "committed" if usage else "failed_reserved",
                "call_key": key, "usd": "0.01"}


class FakeClient:
    def __init__(self, failure=False, attempts=1):
        self.meta = types.SimpleNamespace(region_name="us-east-1",
                    config=types.SimpleNamespace(retries={"total_max_attempts": attempts}))
        self.failure, self.calls = failure, []

    def converse(self, **request):
        self.calls.append(request)
        if self.failure:
            raise RuntimeError("Private transport exception should not be retained")
        return {"output": {"message": {"content": [
            {"reasoningContent": {"text": "opaque"}}, {"text": '{"action":"finish"}'}]}},
            "usage": {"inputTokens": 50, "outputTokens": 10}}


class ProviderTests(unittest.TestCase):
    def invoke(self, root, **kwargs):
        return provider.invoke("luna", [{"role": "user", "content": [{"text": "owned fixture"}]}],
                               Path(root) / "calls.jsonl", kwargs.pop("gate", lambda: None),
                               {"study": "test", "agent": "alice"}, **kwargs)

    def test_gate_failure_precedes_reservation_and_provider(self):
        def reject():
            raise RuntimeError("No scientific permission")
        with tempfile.TemporaryDirectory() as root:
            ledger, client = FakeLedger(), FakeClient()
            with self.assertRaises(RuntimeError):
                self.invoke(root, gate=reject, ledger=ledger, client=client)
            self.assertFalse(ledger.events)
            self.assertFalse(client.calls)
            self.assertFalse((Path(root) / "calls.jsonl").exists())

    def test_exact_request_and_visible_text(self):
        with tempfile.TemporaryDirectory() as root:
            ledger, client = FakeLedger(), FakeClient()
            result = self.invoke(root, ledger=ledger, client=client)
            self.assertEqual(result["text"], '{"action":"finish"}')
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(ledger.events[0][2], client.calls[0])
            self.assertEqual(client.calls[0]["inferenceConfig"]["maxTokens"], 2048)
            self.assertEqual(ledger.events[1][0], "finish")

    def test_failure_keeps_reservation_and_no_transport_secrets(self):
        with tempfile.TemporaryDirectory() as root:
            ledger, client = FakeLedger(), FakeClient(failure=True)
            result = self.invoke(root, ledger=ledger, client=client)
            self.assertEqual(result["status"], "provider_failure")
            self.assertEqual(len(client.calls), 1)
            self.assertIsNone(ledger.events[-1][2])
            self.assertNotIn("Private transport", (Path(root) / "calls.jsonl").read_text())

    def test_cap_prevents_call(self):
        with tempfile.TemporaryDirectory() as root:
            client = FakeClient()
            result = self.invoke(root, ledger=FakeLedger(allow=False), client=client)
            self.assertEqual(result["status"], "budget_stop")
            self.assertFalse(client.calls)

    def test_withdrawn_subject_never_reaches_provider_or_ledger(self):
        with tempfile.TemporaryDirectory() as root:
            ledger, client = FakeLedger(), FakeClient()
            with self.assertRaisesRegex(ValueError, "withdrawn"):
                provider.invoke("terra", [], Path(root) / "calls.jsonl",
                                lambda: None, {}, client=client, ledger=ledger)
            self.assertFalse(ledger.events)
            self.assertFalse(client.calls)
            self.assertFalse((Path(root) / "calls.jsonl").exists())

    def test_sdk_retry_configuration_blocks_call(self):
        with tempfile.TemporaryDirectory() as root:
            ledger, client = FakeLedger(), FakeClient(attempts=2)
            with self.assertRaises(RuntimeError):
                self.invoke(root, ledger=ledger, client=client)
            self.assertFalse(ledger.events)
            self.assertFalse(client.calls)


if __name__ == "__main__":
    unittest.main()
