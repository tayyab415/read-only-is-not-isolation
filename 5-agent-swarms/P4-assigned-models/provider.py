"""Guarded Bedrock subject calls. Importing this module makes no provider call."""
from datetime import datetime, timezone
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import threading
import time

HERE = Path(__file__).resolve().parent
BUDGET = HERE.parent / "P-budget"
spec = importlib.util.spec_from_file_location("p4_canonical_budget", BUDGET / "guard.py")
budget = importlib.util.module_from_spec(spec)
spec.loader.exec_module(budget)
IO_LOCK = threading.Lock()
SYSTEM = "You are a client in an owned infrastructure study. Return one JSON action only."
MAX_TOKENS = 2048


def append(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with IO_LOCK, path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with path.open("a") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


def utc():
    return datetime.now(timezone.utc).isoformat()


def make_client():
    import boto3
    from botocore.config import Config
    return boto3.client("bedrock-runtime", region_name="us-east-1",
                        config=Config(connect_timeout=10, read_timeout=90,
                                      retries={"total_max_attempts": 1}))


def client_contract(client):
    config = client.meta.config
    if client.meta.region_name != "us-east-1" \
            or config.retries.get("total_max_attempts") != 1:
        raise RuntimeError("The subject client must disable retries and use the approved region")


def provider_error(error):
    response = getattr(error, "response", {})
    code = response.get("Error", {}).get("Code", type(error).__name__)
    # Record only a provider's structured error message. Do not serialize
    # transport exceptions, environment, credential objects, or HTTP requests.
    message = response.get("Error", {}).get("Message", "")[:500]
    words = message.lower()
    policy = any(value in words for value in ("policy", "content filter", "safety", "blocked content"))
    return {"error_type": type(error).__name__, "provider_code": code,
            "provider_message": message, "explicit_policy_censoring": policy}


def invoke(model, history, calls_path, gate, metadata, client=None, ledger=None):
    """Check scientific gates, reserve, and retain one exact non-retried call."""
    gate()
    ledger = ledger or budget.Ledger()
    if model not in ledger.config["model_ids"]:
        raise ValueError("Unapproved or withdrawn subject model")
    request = {"modelId": ledger.config["model_ids"][model],
               "system": [{"text": SYSTEM}], "messages": history,
               "inferenceConfig": {"maxTokens": MAX_TOKENS}}
    client = client or make_client()
    client_contract(client)
    key = ledger.reserve(model, request, MAX_TOKENS, metadata=metadata)
    if key is None:
        append(calls_path, {"event": "budget_stop", "utc": utc(), "metadata": metadata})
        return {"status": "budget_stop", "text": None}
    start = time.time_ns()
    append(calls_path, {"event": "reserved", "utc": utc(), "call_key": key,
                       "metadata": metadata, "start_utc_ns": start, "request": request})
    try:
        response = client.converse(**request)
    except Exception as error:
        failure = provider_error(error)
        cost = ledger.finish(key, error=failure["provider_code"])
        append(calls_path, {"event": "failed", "call_key": key, "metadata": metadata,
                           "start_utc_ns": start, "end_utc_ns": time.time_ns(),
                           "budget_event": cost, **failure})
        return {"status": "provider_failure", "text": None, "call_key": key, **failure}
    cost = ledger.finish(key, usage=response.get("usage"))
    end = time.time_ns()
    append(calls_path, {"event": "completed", "call_key": key, "metadata": metadata,
                       "start_utc_ns": start, "end_utc_ns": end,
                       "response": response, "budget_event": cost})
    # Opaque reasoning or other provider blocks remain evidence. The client
    # sees only the visible text and its own permitted observations.
    text = "".join(block.get("text", "") for block in
                   response.get("output", {}).get("message", {}).get("content", []))
    if cost.get("must_stop"):
        return {"status": "cost_overrun", "text": None, "call_key": key}
    return {"status": "completed", "text": text, "call_key": key}
