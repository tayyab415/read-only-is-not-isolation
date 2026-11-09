"""Atomic model-spend ledger (SPEC "Budget").

Rows are appended under a threading lock plus an fcntl lock on a sibling lock
file, so several threads and several controller processes share one cap.
Effective total = last row per call_key.
"""
import fcntl
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

PRICES = {  # USD per million tokens
    "glm": {"in": 0.15, "out": 0.50},
    "deepseek": {"in": 0.09, "out": 0.18},
}
MULTIPLIER = 1.2
MAX_TOKENS = 2048  # v2: 1024 in v1
DEFAULT_CAP = 8.50  # HF: 10 USD total for BC; 1.50 kept for fresh confirmation (user, 2026-11-09)


def reservation_usd(model_key, request, max_tokens=MAX_TOKENS):
    price = PRICES[model_key]
    est_in = len(json.dumps(request).encode()) / 2
    return MULTIPLIER * (est_in * price["in"] + max_tokens * price["out"]) / 1e6


def actual_usd(model_key, usage):
    price = PRICES[model_key]
    return MULTIPLIER * (usage["prompt_tokens"] * price["in"]
                         + usage["completion_tokens"] * price["out"]) / 1e6


class Ledger:
    def __init__(self, path, cap=DEFAULT_CAP):
        self.path = Path(path)
        self.cap = cap
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.path.with_name(self.path.name + ".lock")
        self._tlock = threading.Lock()

    @contextmanager
    def _locked(self):
        with self._tlock:
            with open(self._lock_path, "a") as lf:
                fcntl.flock(lf, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lf, fcntl.LOCK_UN)

    def _effective_total(self):
        if not self.path.exists():
            return 0.0
        amounts = {}
        with self.path.open() as f:
            for n, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as e:
                    raise ValueError(f"corrupt ledger row {n} in {self.path}") from e
                amounts[row["call_key"]] = row["usd"]
        return sum(amounts.values())

    def _append(self, row):
        line = json.dumps(row) + "\n"
        with self.path.open("a") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def total(self):
        with self._locked():
            return self._effective_total()

    def _row(self, kind, call_key, usd, episode, actor, turn, model):
        return {"call_key": call_key, "usd": usd, "kind": kind, "episode": episode,
                "actor": actor, "turn": turn, "model": model, "utc_ns": time.time_ns()}

    def reserve(self, call_key, usd, *, episode, actor, turn, model):
        """Append a reservation unless the effective total would exceed the cap."""
        with self._locked():
            if self._effective_total() + usd > self.cap:
                return False
            self._append(self._row("reserve", call_key, usd, episode, actor, turn, model))
            return True

    def actual(self, call_key, usd, *, episode, actor, turn, model):
        """Replace a reservation with the actual cost. Never refused."""
        with self._locked():
            self._append(self._row("actual", call_key, usd, episode, actor, turn, model))
