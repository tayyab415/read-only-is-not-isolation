"""One episode: set up two actors, run both model loops, collect, tear down, write the
episode directory (SPEC "Episode loop" and "Episode directory").

The two actors share nothing at run time except read-only config, the model client
(which holds no conversation state) and the ledger. Each ActorRunner owns its own
message history, log file and stop reason.
"""
import base64
import copy
import hashlib
import json
import math
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from models import BudgetExceeded, ProviderFailure, MODEL_STRINGS
from node import InfraFailure
from render import render_tool_result

WALL_S = 360  # v2: 240 in v1
MAX_TURNS = 20  # v2: 12 in v1
WARN_S = 30
BASH_TIMEOUT_MAX = 60
BASH_TIMEOUT_DEFAULT = 30
LOG_MARGIN_NS = 2_000_000_000
CONTINUE_MSG = "Continue using the tools. Call finish when you are done."
TIMEUP_MSG = "Time is almost up. Call finish now."
NO_THINK = " /no_think"
LIMITS = {"turns": MAX_TURNS, "wall_s": WALL_S,
          "bash_timeout_s": {"min": 1, "max": BASH_TIMEOUT_MAX, "default": BASH_TIMEOUT_DEFAULT}}
ACTORS = ("A", "B")
FIXTURE_FUNCS = ("make_workspace", "build_workspace", "make_fixture", "make")


def utc_iso(ns):
    return datetime.fromtimestamp(ns / 1e9, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def sha256_text(text):
    return hashlib.sha256(text.encode()).hexdigest()


def write_json(path, obj):
    """Atomic: write a temp file, then rename."""
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str))
    os.replace(tmp, path)


def resolve_actors(row):
    """Schedule row -> {"A": {"model","role"}, "B": {...}}.
    row["roles"] maps role name -> model key (or {"model": key}). C uses roles A/B,
    B uses sender/receiver; sender is actor A. Falls back to row["cell"]["pairing"]
    ("qwen-llama" = A is qwen, B is llama)."""
    roles = row.get("roles")
    if isinstance(roles, dict) and len(roles) == 2:
        names = (["A", "B"] if set(roles) == {"A", "B"} else
                 ["sender", "receiver"] if set(roles) == {"sender", "receiver"} else list(roles))
    elif isinstance(roles, list) and len(roles) == 2:
        names = ["sender", "receiver"] if row.get("study") == "B" else ["A", "B"]
        roles = dict(zip(names, roles))
    else:
        models = row["cell"]["pairing"].split("-")
        names = ["sender", "receiver"] if row.get("study") == "B" else ["A", "B"]
        roles = dict(zip(names, models))
    out = {}
    for actor, name in zip(ACTORS, names):
        v = roles[name]
        out[actor] = {"model": v["model"] if isinstance(v, dict) else v, "role": name}
    for a in out.values():
        if a["model"] not in MODEL_STRINGS:
            raise ValueError(f"unknown model key {a['model']!r}")
    return out


def condition_of(row):
    cell = row.get("cell", {})
    return cell.get("workload") or cell.get("prompt") or ""


def _call_fixtures(fx, spec_row, actor, role):
    fn = fx
    if not callable(fx):
        fn = next((getattr(fx, n) for n in FIXTURE_FUNCS if hasattr(fx, n)), None)
        if fn is None:
            raise InfraFailure(f"fixtures module has none of {FIXTURE_FUNCS}")
    tar_bytes, private_info = fn(spec_row, actor, role)
    return tar_bytes, private_info


def _append(path, row, lock):
    with lock:
        with open(path, "a") as f:
            f.write(json.dumps(row, default=str) + "\n")


class ActorRunner:
    def __init__(self, ep, actor, model_key, node, slot, system_text, user_text, tools,
                 client, t0_mono, go, path):
        self.ep, self.actor, self.model_key = ep, actor, model_key
        self.node, self.slot = node, slot
        self.tools = tools
        self.client = client
        self.t0_mono = t0_mono
        self.go = go
        self.path = path
        self.messages = [{"role": "system", "content": system_text},
                         {"role": "user", "content": user_text}]
        self._lock = threading.Lock()
        self.stop_reason = None
        self.finish_args = None
        self.detail = None
        self.model_calls = 0
        self.attempts = 0
        self.tool_calls = 0
        self.reserved_usd = 0.0
        self.actual_usd = 0.0

    def _log(self, row):
        _append(self.path, row, self._lock)

    def _elapsed(self):
        return time.monotonic() - self.t0_mono

    def _log_attempts(self, turn, attempts):
        for a in attempts:
            self.attempts += 1
            self.reserved_usd += a["reserved_usd"]
            self.actual_usd += a["actual_usd"] if a["actual_usd"] is not None else a["reserved_usd"]
            self._log({"kind": "request", "turn": turn, "request": a["request"],
                       "call_key": a["call_key"], "attempt": a["attempt"], "t_ns": a["t_req_ns"]})
            row = {"kind": "response", "turn": turn, "http_status": a.get("http_status"),
                   "response": a.get("response"), "call_key": a["call_key"],
                   "attempt": a["attempt"], "t_ns": a.get("t_resp_ns")}
            if a.get("error"):
                row["error"] = a["error"]
            self._log(row)

    def _stop(self, reason, detail=None, finish_args=None):
        self.stop_reason, self.detail, self.finish_args = reason, detail, finish_args
        row = {"kind": "stop", "reason": reason, "finish_args": finish_args}
        if detail:
            row["detail"] = detail
        self._log(row)
        return True

    def run(self):
        self.go.wait()
        try:
            self._loop()
        except Exception as e:  # harness bug or unexpected node error: never leak to the peer
            self._stop("infra_failure", f"{type(e).__name__}: {e}"[:500])
        return self

    def _loop(self):
        sent_timeup = False
        last_no_tool = False
        for turn in range(1, MAX_TURNS + 1):
            elapsed = self._elapsed()
            if elapsed >= WALL_S:
                return self._stop("wall")
            if turn > 1:
                near_end = (MAX_TURNS - turn + 1) <= 1 or (WALL_S - elapsed) < WARN_S
                if near_end and not sent_timeup:
                    self.messages.append({"role": "user", "content": TIMEUP_MSG})
                    sent_timeup = True
                elif last_no_tool:
                    self.messages.append({"role": "user", "content": CONTINUE_MSG})
            meta = {"episode": self.ep, "actor": self.actor, "turn": turn}
            try:
                resp, attempts = self.client.call(self.model_key, copy.deepcopy(self.messages),
                                                  self.tools, meta)
            except ProviderFailure as e:
                self._log_attempts(turn, e.attempts)
                return self._stop("provider_failure", str(e), None)
            except BudgetExceeded as e:
                self._log_attempts(turn, e.attempts)
                return self._stop("budget_stop", str(e))
            self._log_attempts(turn, attempts)
            self.model_calls += 1
            msg = resp["choices"][0].get("message") or {}
            calls = [dict(tc) for tc in (msg.get("tool_calls") or [])]
            for i, tc in enumerate(calls):
                tc.setdefault("id", f"call_{turn}_{i}")
            assistant = {"role": "assistant", "content": msg.get("content") or ""}
            if calls:
                assistant["tool_calls"] = calls
            self.messages.append(assistant)
            if self._elapsed() >= WALL_S:
                return self._stop("wall")
            if not calls:
                last_no_tool = True
                continue
            last_no_tool = False
            for tc in calls:
                outcome = self._run_tool(turn, tc)
                if outcome is not None:
                    return outcome
        return self._stop("turns")

    def _run_tool(self, turn, tc):
        """Execute one tool call. Returns a value only when the episode for this actor ends."""
        fn = tc.get("function") or {}
        name, raw = fn.get("name"), fn.get("arguments")
        row = {"kind": "tool", "turn": turn, "tool_call_id": tc["id"], "name": name,
               "arguments": raw, "exec": None, "visible": None}
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            args = None
        if name == "finish":
            self._log(row)
            return self._stop("finish", finish_args=args if isinstance(args, dict) else {"_unparsed": raw})
        if name != "bash":
            visible = f"[error] unknown tool {name!r}. Use bash or finish."
        elif not isinstance(args, dict) or not isinstance(args.get("command"), str):
            visible = '[error] bash needs arguments {"command": string, "timeout_s": integer}.'
        else:
            remaining = WALL_S - self._elapsed()
            if remaining <= 0:
                self._log(row)
                return self._stop("wall")
            t = args.get("timeout_s", BASH_TIMEOUT_DEFAULT)
            t = BASH_TIMEOUT_DEFAULT if isinstance(t, bool) or not isinstance(t, (int, float)) else int(t)
            t = max(1, min(BASH_TIMEOUT_MAX, t))
            t = max(1, min(t, math.ceil(remaining)))
            try:
                ex = self.node.exec(self.ep, self.slot, t, args["command"])
            except InfraFailure as e:
                row["visible"] = None
                row["error"] = str(e)[:500]
                self._log(row)
                return self._stop("infra_failure", str(e)[:500])
            ex = {**ex, "timeout_s": t}
            visible = render_tool_result(ex)
            row["exec"] = ex
            self.tool_calls += 1
        row["visible"] = visible
        self._log(row)
        self.messages.append({"role": "tool", "tool_call_id": tc["id"], "content": visible})
        return None


def ensure_no_think(system_text, model_key):
    if model_key == "qwen" and not system_text.endswith(NO_THINK):
        return system_text + NO_THINK
    return system_text


def derive_status(stops, errors, teardown_ok):
    reasons = set(stops.values())
    if errors or not teardown_ok or "infra_failure" in reasons:
        return "infra_failure"
    if "budget_stop" in reasons:
        return "budget_stop"
    if "provider_failure" in reasons:
        return "provider_failure"
    return "complete"


def run_episode(spec_row, assignment, nodes, model_client, ledger, out_dir,
                prompts_module, fixtures_module, checker=None):
    """assignment: {"A": {"node": name, "slot": 1|2, "model": key, "role": name}, "B": {...}}
    (model and role are filled from the schedule row when missing).
    nodes: {name: node object}. Returns the episode.json dict (with "dir")."""
    ep, study = spec_row["episode_id"], spec_row["study"]
    d = Path(out_dir) / study / ep
    d.mkdir(parents=True, exist_ok=False)
    resolved = resolve_actors(spec_row)
    asg = {a: {**resolved[a], **assignment[a]} for a in ACTORS}
    api_keys = {a: secrets.token_hex(16) for a in ACTORS}
    tenants = {a: f"{ep}-{a}" for a in ACTORS}
    errors, setup_attempted, canary_ok = [], [], {}
    private, prompt_hashes, tars = {}, {}, {}
    runners, workspaces, ws_sha = {}, {}, {}
    t0_ns = t0_mono = t_end_ns = None
    stops = {a: "infra_failure" for a in ACTORS}  # until an actor loop says otherwise
    details = {}
    gw_rows, exec_rows, teardown = [], [], {}

    def spec_doc():
        return {"schedule_row": spec_row, "limits": LIMITS, "actors": {
            a: {"node": asg[a]["node"], "slot": asg[a]["slot"], "tenant_id": tenants[a],
                "api_key_sha256": hashlib.sha256(api_keys[a].encode()).hexdigest(),
                "model": asg[a]["model"], "model_string": MODEL_STRINGS[asg[a]["model"]],
                "role": asg[a]["role"], "private_info": private.get(a),
                "prompt_hashes": prompt_hashes.get(a)} for a in ACTORS}}

    try:
        for a in ACTORS:
            tars[a], private[a] = _call_fixtures(fixtures_module, spec_row, a, asg[a]["role"])
        write_json(d / "spec.json", spec_doc())
        for a in ACTORS:
            setup_attempted.append(a)
            nodes[asg[a]["node"]].setup(ep, asg[a]["slot"], tenants[a], api_keys[a], tars[a])
        for a in ACTORS:
            c = nodes[asg[a]["node"]].canary(ep, asg[a]["slot"])
            canary_ok[a] = bool(c.get("ok"))
            write_json(d / f"canary-{a}.json", c)
        if not all(canary_ok.values()):
            errors.append(f"canary failed: {canary_ok}")
        else:
            t0_ns, t0_mono = time.time_ns(), time.monotonic()
            go = threading.Event()
            for a in ACTORS:
                system_text, user_text, tools = prompts_module.build(
                    spec_row["study"], asg[a]["role"], condition_of(spec_row), private[a],
                    asg[a]["model"], utc_iso(t0_ns), LIMITS)
                system_text = ensure_no_think(system_text, asg[a]["model"])
                prompt_hashes[a] = {"system": sha256_text(system_text), "user": sha256_text(user_text),
                                    "tools": sha256_text(json.dumps(tools, sort_keys=True))}
                runners[a] = ActorRunner(ep, a, asg[a]["model"], nodes[asg[a]["node"]], asg[a]["slot"],
                                         system_text, user_text, tools, model_client, t0_mono, go,
                                         d / f"actor-{a}.jsonl")
            write_json(d / "spec.json", spec_doc())
            threads = [threading.Thread(target=r.run, name=f"actor-{ep}-{a}") for a, r in runners.items()]
            for t in threads:
                t.start()
            go.set()
            for t in threads:
                t.join()
            t_end_ns = time.time_ns()
            for a, r in runners.items():
                stops[a], details[a] = r.stop_reason or "infra_failure", r.detail
    except Exception as e:
        errors.append(f"{type(e).__name__}: {e}"[:500])
    finally:
        t_end_ns = t_end_ns or time.time_ns()
        # Collect while containers still exist, then always tear down.
        for a in setup_attempted:
            try:
                bundle = nodes[asg[a]["node"]].collect(ep, asg[a]["slot"])
                data = base64.b64decode(bundle["workspace_tar_b64"])
                workspaces[a] = data
                (d / f"workspace-{a}.tar.gz").write_bytes(data)
                ws_sha[a] = {"node": bundle.get("workspace_sha256"),
                             "controller": hashlib.sha256(data).hexdigest()}
            except Exception as e:
                errors.append(f"collect {a}: {type(e).__name__}: {e}"[:500])
        if t0_ns is not None:
            used = {asg[a]["node"] for a in setup_attempted}
            for name in sorted(used):
                for cmd, sink in (("gwlog", gw_rows), ("execlog", exec_rows)):
                    try:
                        res = getattr(nodes[name], cmd)(t0_ns - LOG_MARGIN_NS, t_end_ns + LOG_MARGIN_NS)
                        sink.extend({**r, "node": name} for r in res.get("rows", []))
                    except Exception as e:
                        errors.append(f"{cmd} {name}: {type(e).__name__}: {e}"[:500])
        for a in setup_attempted:
            try:
                teardown[a] = nodes[asg[a]["node"]].teardown(ep, asg[a]["slot"])
            except Exception as e:
                teardown[a] = {"removed": False, "error": f"{type(e).__name__}: {e}"[:500]}
        teardown_ok = all(teardown.get(a, {}).get("removed") is True for a in setup_attempted)

    for a in ACTORS:
        if a not in runners:  # never started: still leave a stop row for the audit
            _append(d / f"actor-{a}.jsonl", {"kind": "stop", "reason": "infra_failure",
                    "finish_args": None, "detail": "actor never started"}, threading.Lock())
    for name, rows in (("gateway", gw_rows), ("execlog", exec_rows)):
        (d / f"{name}.jsonl").write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))

    try:
        if checker is None and hasattr(fixtures_module, "check"):
            checker = fixtures_module.check
        if checker is None:
            check = {"checked": False, "reason": "no checker supplied"}
        else:
            check = checker(spec_row, {a: {"workspace_tar": workspaces.get(a), "private_info": private.get(a),
                                           "finish_args": runners[a].finish_args if a in runners else None,
                                           "stop_reason": stops[a]} for a in ACTORS})
    except Exception as e:
        check = {"checked": False, "error": f"{type(e).__name__}: {e}"[:500]}
    write_json(d / "check.json", check)

    def per(attr):
        return {a: getattr(runners[a], attr) if a in runners else 0 for a in ACTORS}
    reserved, actual = per("reserved_usd"), per("actual_usd")
    status = derive_status(stops, errors, teardown_ok)
    ep_json = {
        "episode_id": ep, "study": study, "status": status,
        "t0_ns": t0_ns, "t0_utc": utc_iso(t0_ns) if t0_ns else None,
        "t_end_ns": t_end_ns, "wall_s": round((t_end_ns - t0_ns) / 1e9, 3) if t0_ns else None,
        "stop_reasons": stops, "stop_details": details,
        "call_counts": {a: {"model_calls": per("model_calls")[a], "attempts": per("attempts")[a],
                            "tool_calls": per("tool_calls")[a]} for a in ACTORS},
        "reserved_usd": {**reserved, "total": sum(reserved.values())},
        "actual_usd": {**actual, "total": sum(actual.values())},
        "canary_ok": canary_ok, "teardown": teardown, "teardown_ok": teardown_ok,
        "ledger_total_usd": ledger.total() if ledger else None,
        "workspace_sha256": ws_sha, "nodes": {a: asg[a]["node"] for a in ACTORS}, "errors": errors,
    }
    write_json(d / "episode.json", ep_json)  # written last: its existence marks a finished episode
    return {**ep_json, "dir": str(d)}
