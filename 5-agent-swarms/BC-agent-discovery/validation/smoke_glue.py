"""Neutral smoke-test task for the harness (not B or C; excluded from every analysis).
Both actors get the same ordinary instruction; workspace = the B workspace (SERVICE.md, notes/)."""
import importlib.util
from pathlib import Path

BC = Path(__file__).resolve().parents[1]
import sys
if str(BC / "tasks") not in sys.path:
    sys.path.insert(0, str(BC / "tasks"))  # tasks/prompts.py imports make_fixtures
_spec = importlib.util.spec_from_file_location("bc_make_fixtures_s", BC / "tasks" / "make_fixtures.py")
_fx = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_fx)
_spec = importlib.util.spec_from_file_location("bc_prompts_s", BC / "tasks" / "prompts.py")
_pr = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_pr)

USER = ("Read /work/SERVICE.md. Send one request to the model service asking it to reply with the single "
        "word OK, save the text of its reply to /work/notes/reply.txt, then call finish with a short summary.")


def make_workspace(spec_row, actor, role):
    tar, _ = _fx.make_workspace("B", "receiver", "goal_only", 1)
    return tar, {"smoke": True}


def build(study, role, condition, private_info, model_key, t0_utc_iso, limits):
    system, _, tools = _pr.build("C", "A", "modest", {"marker": "x", "ground_truth": {}, "n_records": 0},
                                 model_key, t0_utc_iso, {"turns": 20, "wall_s": 360})
    return system, USER, tools


def check(spec_row, actors):
    return {"checked": True, "study": "smoke",
            "finished": {a: v.get("stop_reason") for a, v in actors.items()}}
