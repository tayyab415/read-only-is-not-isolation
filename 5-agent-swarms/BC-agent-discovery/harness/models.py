"""HF router client and a scripted fake with the same interface.

call(model_key, messages, tools, meta) -> (response_json, attempts)
  meta: {"episode", "actor", "turn"}
  attempts: one dict per HTTP attempt (request body, status, response, costs).
Failures raise ProviderFailure or BudgetExceeded; both carry .attempts so the
caller can still log what was sent.
"""
import importlib.util
import json
import threading
import time
from pathlib import Path

import requests

import ledger as ledger_mod

URL = "https://router.huggingface.co/v1/chat/completions"
MODEL_STRINGS = {
    # v2 (2026-11-09 09:55 IST): v1 subjects Qwen3-32B and Llama-3.3-70B could not do the task.
    "glm": "zai-org/GLM-5.3-Flash:deepinfra",
    "deepseek": "deepseek-ai/DeepSeek-V4-Flash:deepinfra",
}
TEMPERATURE = 0.7
TOP_P = 0.9
MAX_TOKENS = ledger_mod.MAX_TOKENS
BACKOFF_S = (2, 4, 8)  # three retries after the first attempt
RETRY_STATUS = lambda s: s == 429 or 500 <= s <= 599  # noqa: E731
RETRIEVE_PY = Path(__file__).resolve().parents[4] / "library" / "retrieve.py"


class ProviderFailure(Exception):
    def __init__(self, message, attempts=None, http_status=None):
        super().__init__(message)
        self.attempts = attempts or []
        self.http_status = http_status


class BudgetExceeded(Exception):
    def __init__(self, message, attempts=None):
        super().__init__(message)
        self.attempts = attempts or []


def load_token():
    """HF token via library/retrieve.py:credential(), loaded by file path."""
    spec = importlib.util.spec_from_file_location("bc_retrieve", RETRIEVE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    token, _source = mod.credential()
    if not token:
        raise RuntimeError("no Hugging Face credential found")
    return token


def build_body(model_key, messages, tools):
    return {"model": MODEL_STRINGS[model_key], "messages": messages, "tools": tools,
            "tool_choice": "auto", "temperature": TEMPERATURE, "top_p": TOP_P,
            "max_tokens": MAX_TOKENS}


def assert_no_token(directory, token):
    """Raise AssertionError if the token string appears in any file under directory
    (tar.gz members are unpacked and scanned too). The message never includes the token."""
    import tarfile
    needle = token.encode()
    hits = []
    for p in Path(directory).rglob("*"):
        if not p.is_file():
            continue
        if needle in p.read_bytes():
            hits.append(str(p))
            continue
        if p.name.endswith((".tar.gz", ".tgz")):
            try:
                with tarfile.open(p) as tf:
                    for m in tf.getmembers():
                        f = tf.extractfile(m) if m.isfile() else None
                        if f is not None and needle in f.read():
                            hits.append(f"{p}:{m.name}")
            except (tarfile.TarError, OSError, EOFError):
                pass
    if hits:
        raise AssertionError(f"HF token found in: {hits}")


class HFClient:
    def __init__(self, token, ledger, url=URL, timeout=(10, 120), post=None, sleep=time.sleep):
        self._token = token
        self.ledger = ledger
        self.url = url
        self.timeout = timeout
        self._post = post or requests.post
        self._sleep = sleep

    def __repr__(self):
        return "HFClient(url=%r)" % self.url

    def _scrub(self, text):
        return str(text).replace(self._token, "[redacted]")

    def _send(self, body):
        """-> (http_status or None, response json/dict or None, error str or None)"""
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        try:
            r = self._post(self.url, headers=headers, json=body, timeout=self.timeout)
        except requests.RequestException as e:
            return None, None, self._scrub(f"{type(e).__name__}: {e}")[:500]
        try:
            return r.status_code, r.json(), None
        except ValueError:
            return r.status_code, {"_raw_text": self._scrub(r.text)[:2000]}, "non-json body"

    def call(self, model_key, messages, tools, meta):
        body = build_body(model_key, messages, tools)
        attempts = []
        for attempt in range(1, len(BACKOFF_S) + 2):
            call_key = f"{meta['episode']}/{meta['actor']}/{meta['turn']}/{attempt}"
            usd = ledger_mod.reservation_usd(model_key, body, MAX_TOKENS)
            if not self.ledger.reserve(call_key, usd, episode=meta["episode"],
                                       actor=meta["actor"], turn=meta["turn"], model=model_key):
                raise BudgetExceeded(f"budget cap reached before {call_key}", attempts)
            rec = {"attempt": attempt, "call_key": call_key, "request": body,
                   "reserved_usd": usd, "actual_usd": None, "t_req_ns": time.time_ns()}
            attempts.append(rec)
            status, resp, err = self._send(body)
            rec.update(t_resp_ns=time.time_ns(), http_status=status, response=resp, error=err)
            ok = (status == 200 and err is None and isinstance(resp, dict)
                  and isinstance(resp.get("choices"), list) and resp["choices"])
            if ok:
                usage = resp.get("usage") or {}
                if isinstance(usage.get("prompt_tokens"), int) and isinstance(usage.get("completion_tokens"), int):
                    rec["actual_usd"] = ledger_mod.actual_usd(model_key, usage)
                    self.ledger.actual(call_key, rec["actual_usd"], episode=meta["episode"],
                                       actor=meta["actor"], turn=meta["turn"], model=model_key)
                return resp, attempts
            retriable = status is None or RETRY_STATUS(status) or (status == 200)
            if not retriable:
                raise ProviderFailure(f"HTTP {status}", attempts, status)
            if attempt > len(BACKOFF_S):
                raise ProviderFailure(f"retries exhausted (last: {status or err})", attempts, status)
            self._sleep(BACKOFF_S[attempt - 1])


class FakeFail:
    """Script entry that makes FakeModel raise ProviderFailure for that turn."""
    def __init__(self, http_status=400):
        self.http_status = http_status


class FakeModel:
    """Replays scripted turns, no network. script: {actor: [turn, ...]} or a list used by
    every actor ('*' key also works). A turn is a list of tool calls
    [{"name":..., "arguments": {...} or str}], a str (plain reply, no tool call), or FakeFail.
    Past the end of a script the reply is plain text. Goes through the ledger like HFClient."""

    def __init__(self, script, ledger=None):
        self.script = script
        self.ledger = ledger
        self.requests = []  # (actor, turn, request body) for assertions
        self._lock = threading.Lock()

    def _turns(self, actor):
        if isinstance(self.script, list):
            return self.script
        return self.script.get(actor, self.script.get("*", []))

    def call(self, model_key, messages, tools, meta):
        body = build_body(model_key, messages, tools)
        call_key = f"{meta['episode']}/{meta['actor']}/{meta['turn']}/1"
        usd = ledger_mod.reservation_usd(model_key, body, MAX_TOKENS)
        if self.ledger and not self.ledger.reserve(call_key, usd, episode=meta["episode"],
                                                   actor=meta["actor"], turn=meta["turn"], model=model_key):
            raise BudgetExceeded("budget cap reached", [])
        with self._lock:
            self.requests.append((meta["actor"], meta["turn"], json.loads(json.dumps(body))))
        turns = self._turns(meta["actor"])
        idx = meta["turn"] - 1
        entry = turns[idx] if idx < len(turns) else "(no more scripted turns)"
        rec = {"attempt": 1, "call_key": call_key, "request": body, "reserved_usd": usd,
               "actual_usd": None, "t_req_ns": time.time_ns()}
        if isinstance(entry, FakeFail):
            rec.update(t_resp_ns=time.time_ns(), http_status=entry.http_status,
                       response={"error": "scripted failure"}, error=None)
            raise ProviderFailure(f"HTTP {entry.http_status}", [rec], entry.http_status)
        message = {"role": "assistant", "content": entry if isinstance(entry, str) else ""}
        if isinstance(entry, list):
            message["tool_calls"] = [
                {"id": f"call_{meta['actor']}_{meta['turn']}_{i}", "type": "function",
                 "function": {"name": c["name"],
                              "arguments": c["arguments"] if isinstance(c["arguments"], str)
                              else json.dumps(c["arguments"])}}
                for i, c in enumerate(entry)]
        usage = {"prompt_tokens": len(json.dumps(body)) // 4, "completion_tokens": 20}
        resp = {"id": f"fake-{call_key}", "model": MODEL_STRINGS[model_key],
                "choices": [{"index": 0, "message": message,
                             "finish_reason": "tool_calls" if isinstance(entry, list) else "stop"}],
                "usage": {**usage, "total_tokens": usage["prompt_tokens"] + usage["completion_tokens"]}}
        rec.update(t_resp_ns=time.time_ns(), http_status=200, response=resp, error=None,
                   actual_usd=ledger_mod.actual_usd(model_key, usage))
        if self.ledger:
            self.ledger.actual(call_key, rec["actual_usd"], episode=meta["episode"],
                               actor=meta["actor"], turn=meta["turn"], model=model_key)
        return resp, [rec]
