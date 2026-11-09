"""Build tasks/schedule.json: 96 C episodes and 48 B episodes in blocks of 12.

Run `python3 tasks/schedule.py` from BC/. The file is a JSON list of rows in run
order. The schedule is a pure function of SCHEDULE_SEED.
"""
import hashlib
import itertools
import json
import random
import sys
from collections import Counter
from pathlib import Path

try:
    from . import make_fixtures
except ImportError:
    import make_fixtures

SCHEDULE_SEED = 202611092  # v2; v1 used 20261109
OUT_PATH = Path(__file__).resolve().parent / "schedule.json"

PAIRINGS = ("glm-glm", "deepseek-deepseek", "glm-deepseek")
INFRAS = ("shared", "independent")
C_WORKLOADS = ("modest", "pressure")
B_PROMPTS = ("goal_only", "mechanism")
C_REPLICATES = 8
B_REPLICATES = 4
# C1 B1 C2 B2 C3 B3 C4 B4 C5 C6 C7 C8
BLOCK_ORDER = ["C1", "B1", "C2", "B2", "C3", "B3", "C4", "B4", "C5", "C6", "C7", "C8"]


def cells(study):
    if study == "C":
        return [{"pairing": p, "workload": w, "infra": i}
                for p, w, i in itertools.product(PAIRINGS, C_WORKLOADS, INFRAS)]
    return [{"pairing": p, "prompt": q, "infra": i}
            for q, p, i in itertools.product(B_PROMPTS, PAIRINGS, INFRAS)]


def _roles(pairing, replicate):
    """Model key for role A and role B. Mixed pairs alternate A across replicates."""
    if pairing == "glm-glm":
        return {"A": "glm", "B": "glm"}
    if pairing == "deepseek-deepseek":
        return {"A": "deepseek", "B": "deepseek"}
    return {"A": "glm", "B": "deepseek"} if replicate % 2 == 1 else {"A": "deepseek", "B": "glm"}


def _seed(episode_id, actor):
    h = hashlib.sha256(f"bc-fixture-seed|{SCHEDULE_SEED}|{episode_id}|{actor}".encode()).digest()
    return int.from_bytes(h[:6], "big")


def build_schedule():
    rows = []
    for label in BLOCK_ORDER:
        study, replicate = label[0], int(label[1:])
        block_cells = cells(study)
        rng = random.Random(f"bc-schedule|{SCHEDULE_SEED}|{label}")
        rng.shuffle(block_cells)
        for pos, cell in enumerate(block_cells, 1):
            eid = f"{study}-b{replicate:02d}-{pos:02d}"
            row = {
                "episode_id": eid,
                "study": study,
                "block": replicate,
                "block_label": label,
                "position": pos,
                "global_index": len(rows),
                "cell": cell,
                "roles": _roles(cell["pairing"], replicate),
                "fixture_seeds": {"A": _seed(eid, "A"), "B": _seed(eid, "B")},
            }
            if study == "B":
                row["sender"] = "A"
                row["receiver"] = "B"
                row["sender_value"] = make_fixtures.sender_value(row["fixture_seeds"]["A"])
            rows.append(row)
    return rows


def summary_table(rows):
    lines = []
    for study in ("C", "B"):
        sel = [r for r in rows if r["study"] == study]
        key2 = "workload" if study == "C" else "prompt"
        counts = Counter((r["cell"]["pairing"], r["cell"][key2], r["cell"]["infra"]) for r in sel)
        a_models = Counter((r["cell"]["pairing"], r["cell"][key2], r["cell"]["infra"], r["roles"]["A"]) for r in sel)
        lines.append(f"Study {study}: {len(sel)} episodes")
        lines.append(f"  {'pairing':<12}{key2:<10}{'infra':<13}{'n':>3}  A=glm A=deepseek")
        for (p, k, i), n in sorted(counts.items()):
            lines.append(f"  {p:<12}{k:<10}{i:<13}{n:>3}  {a_models[(p, k, i, 'glm')]:>5} {a_models[(p, k, i, 'deepseek')]:>10}")
    return "\n".join(lines)


def write_schedule(path=OUT_PATH):
    rows = build_schedule()
    Path(path).write_text(json.dumps(rows, indent=1) + "\n")
    return rows


def load_schedule(path=OUT_PATH):
    return json.loads(Path(path).read_text())


if __name__ == "__main__":
    rows = write_schedule()
    print(summary_table(rows))
    print(f"\nwrote {len(rows)} rows to {OUT_PATH}")
    print("sender values:", dict(sorted(Counter(r["sender_value"] for r in rows if r["study"] == "B").items())))
    sys.exit(0)
