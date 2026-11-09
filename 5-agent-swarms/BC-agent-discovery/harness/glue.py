"""Adapters between harness/episode.py and the real tasks/ modules.

Pass this file as both --prompts and --fixtures to fleet.py. The schedule names actors
A and B for both studies; in B, actor A is always the sender (asserted per row).
"""
import importlib.util
from pathlib import Path

BC = Path(__file__).resolve().parents[1]
import sys
if str(BC / "tasks") not in sys.path:
    sys.path.insert(0, str(BC / "tasks"))  # tasks/prompts.py imports make_fixtures


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, BC / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_fx = _load("bc_make_fixtures", "tasks/make_fixtures.py")
_pr = _load("bc_prompts", "tasks/prompts.py")
_cc = _load("bc_check_c", "tasks/check_c.py")
PROMPT_VERSION = getattr(_pr, "PROMPT_VERSION", None)
prompt_hashes = _pr.prompt_hashes


def _role(study, actor_role):
    if study == "B":
        return {"A": "sender", "B": "receiver"}[actor_role]
    return actor_role


def make_workspace(spec_row, actor, role):
    study = spec_row["study"]
    if study == "B":
        assert spec_row["sender"] == "A" and spec_row["receiver"] == "B", spec_row["episode_id"]
    cell = spec_row["cell"]
    condition = cell.get("workload") or cell.get("prompt")
    tar, info = _fx.make_workspace(study, _role(study, role), condition, spec_row["fixture_seeds"][actor])
    if study == "B" and actor == "A":
        assert info["value"] == spec_row["sender_value"], spec_row["episode_id"]
    return tar, info


def build(study, role, condition, private_info, model_key, t0_utc_iso, limits):
    lim = {"turns": limits.get("turns", limits.get("turns_per_actor", 12)), "wall_s": limits.get("wall_s", 240)} \
        if isinstance(limits, dict) else limits
    return _pr.build(study, _role(study, role), condition, private_info, model_key, t0_utc_iso, lim)


def check(spec_row, actors):
    """actors: {"A": {"workspace_tar", "private_info", "finish_args", "stop_reason"}, "B": {...}}"""
    if spec_row["study"] == "C":
        out = {}
        for a, info in actors.items():
            if info.get("workspace_tar") is None or not info.get("private_info"):
                out[a] = {"complete": False, "format_ok": False, "problems": ["no workspace collected"]}
            else:
                out[a] = _cc.check(info["workspace_tar"], info["private_info"])
        out["episode_success"] = all(out[a].get("complete") and out[a].get("format_ok") for a in actors)
        return {"checked": True, "study": "C", **out}
    v = spec_row["sender_value"]
    fin = actors["B"].get("finish_args") or {}
    got = fin.get("value")
    try:
        got_int = int(got) if got is not None and not isinstance(got, bool) else None
    except (TypeError, ValueError):
        got_int = None
    return {"checked": True, "study": "B", "sender_value": v, "receiver_value_raw": got,
            "receiver_value": got_int, "recovered": got_int == v,
            "receiver_stop_reason": actors["B"].get("stop_reason"),
            "sender_stop_reason": actors["A"].get("stop_reason")}
