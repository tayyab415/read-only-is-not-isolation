"""Generator for synthetic, SPEC-conformant episode directories and ledger rows.

It plays the part of a correct harness. make_episode() writes one episode under
root/<study>/<episode_id> and appends its rows to the ledger file.
"""
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import CONTINUE_MSG, TIME_UP_MSG  # noqa: E402
from verify_episode import MODEL_STRINGS, actual_usd, load_render, reservation_usd  # noqa: E402

STRINGS = {v: k for k, v in MODEL_STRINGS.items()}
NS = 10 ** 9
T0 = 1_800_000_000 * NS
TOOLS = [{"type": "function", "function": {"name": "bash", "parameters": {"type": "object"}}},
         {"type": "function", "function": {"name": "finish", "parameters": {"type": "object"}}}]


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def write_jsonl(path, rows):
    Path(path).write_text("".join(json.dumps(r) + "\n" for r in rows))


def tar_bytes(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, text in files.items():
            data = text.encode()
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


class Sim:
    """One actor. script: list of turns {"text", "tools": [(name, args)], "attempts": [status..., 200]}."""

    def __init__(self, ep, actor, model, slot, script, t_base, render, node="nodeX"):
        self.ep, self.actor, self.model, self.slot, self.node = ep, actor, model, slot, node
        self.script, self.t, self.render = script, t_base, render
        self.rows, self.exec_rows, self.ledger, self.execlog = [], [], [], []
        self.calls = 0
        self.stop = None
        self.finish_args = None

    def run(self, system, user):
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        for k, turn in enumerate(self.script, 1):
            body = {"model": STRINGS[self.model], "messages": json.loads(json.dumps(msgs)), "tools": TOOLS,
                    "tool_choice": "auto", "temperature": 0.7, "top_p": 0.9, "max_tokens": 1024}
            for n, status in enumerate(turn.get("attempts", [200]), 1):
                self.t += NS
                ck = f"{self.ep}/{self.actor}/{k}/{n}"
                self.calls += 1
                self.rows.append({"kind": "request", "turn": k, "request": body, "call_key": ck, "attempt": n, "t_ns": self.t})
                self.ledger.append({"call_key": ck, "usd": reservation_usd(self.model, body), "kind": "reserve",
                                    "episode": self.ep, "actor": self.actor, "turn": k, "model": self.model, "utc_ns": self.t})
                self.t += NS
                if status != 200:
                    self.rows.append({"kind": "response", "turn": k, "http_status": status, "call_key": ck, "attempt": n,
                                      "response": {"error": "scripted"}, "t_ns": self.t})
                    continue
                msg = {"role": "assistant", "content": turn.get("text") or None}
                if turn.get("tools"):
                    msg["tool_calls"] = [{"id": f"call_{self.actor}_{k}_{i}", "type": "function",
                                          "function": {"name": nm, "arguments": json.dumps(args)}}
                                         for i, (nm, args) in enumerate(turn["tools"])]
                usage = {"prompt_tokens": 100 + 10 * k, "completion_tokens": 20, "total_tokens": 120 + 10 * k}
                self.rows.append({"kind": "response", "turn": k, "http_status": 200, "t_ns": self.t, "call_key": ck, "attempt": n,
                                  "response": {"id": f"r-{ck}", "choices": [{"index": 0, "message": msg,
                                               "finish_reason": "tool_calls" if turn.get("tools") else "stop"}],
                                               "usage": usage}})
                self.ledger.append({"call_key": ck, "usd": actual_usd(self.model, usage), "kind": "actual",
                                    "episode": self.ep, "actor": self.actor, "turn": k, "model": self.model,
                                    "utc_ns": self.t})
            msgs = msgs + [{**msg, "content": msg["content"] or ""}]
            for i, (nm, args) in enumerate(turn.get("tools") or []):
                tid = f"call_{self.actor}_{k}_{i}"
                row = {"kind": "tool", "turn": k, "tool_call_id": tid, "name": nm, "arguments": json.dumps(args)}
                if nm == "bash":
                    self.t += NS
                    ex = {"rc": turn.get("rc", 0), "stdout": turn.get("stdout", "ok\n"), "stderr": "", "timed_out": False,
                          "t_start_ns": self.t, "t_end_ns": self.t + NS // 2, "stdout_bytes": len(turn.get("stdout", "ok\n")),
                          "stderr_bytes": 0, "timeout_s": args.get("timeout_s", 30)}
                    self.t += NS
                    row.update(exec=ex, visible=self.render(ex))
                    c = f"bc-{self.ep}-{self.slot}"
                    self.execlog.append({"ep": self.ep, "slot": self.slot, "container": c, "node": self.node, "t_start_ns": ex["t_start_ns"],
                                         "t_end_ns": ex["t_end_ns"], "rc": ex["rc"], "timed_out": False, "timeout_s": ex["timeout_s"],
                                         "script_sha256": sha(args["command"]), "stdout_sha256": sha(ex["stdout"]),
                                         "stderr_sha256": sha(""), "stdout_bytes": ex["stdout_bytes"], "stderr_bytes": 0})
                    msgs.append({"role": "tool", "tool_call_id": tid, "content": row["visible"]})
                else:
                    row.update(exec=None, visible=None)
                    self.finish_args = args
                self.rows.append(row)
            if not turn.get("tools") and not turn.get("time_up"):
                msgs.append({"role": "user", "content": CONTINUE_MSG})
            elif turn.get("time_up"):  # replaces the continue message after a reply without tool calls
                msgs.append({"role": "user", "content": TIME_UP_MSG})
        self.stop = "finish" if self.script and any(n == "finish" for n, _ in (self.script[-1].get("tools") or [])) else "turns"
        self.rows.append({"kind": "stop", "reason": self.stop, "finish_args": self.finish_args})


def default_scripts(study, value, guess, a_text, b_text, a_cmd, b_cmd, marker_a, marker_b):
    if study == "B":
        sender = [{"text": "Let me look.", "tools": [("bash", {"command": a_cmd, "timeout_s": 30})]},
                  {"text": a_text, "tools": [("finish", {"status": "sent"})]}]
        receiver = [{"text": "", "attempts": [429, 200], "tools": [("bash", {"command": b_cmd, "timeout_s": 30})]},
                    {"text": b_text, "time_up": False, "tools": [("finish", {"value": guess})]}]
        return sender, receiver
    A = [{"text": "Starting.", "tools": [("bash", {"command": a_cmd, "timeout_s": 30})]},
         {"text": a_text, "time_up": True},
         {"text": "Done.", "tools": [("finish", {"summary": "all records processed"})]}]
    B = [{"text": "", "attempts": [429, 200], "tools": [("bash", {"command": b_cmd, "timeout_s": 30})]},
         {"text": b_text, "tools": [("bash", {"command": "ls", "timeout_s": 10})]},
         {"text": "", "tools": [("finish", {"summary": "all records processed"})]}]
    B[1]["time_up"] = True  # after a tool reply the time-up message is appended
    return A, B


def make_episode(root, ledger_path, ep_id="C-test-001", study="C", infra="shared", value=None, guess=None,
                 a_text="Everything looks fine.", b_text="Proceeding.", a_cmd="ls /work", b_cmd="cat data.csv | head",
                 marker_a="MKR-AAAA-1111", marker_b="MKR-BBBB-2222", task_ok=True, t0=T0, a_stdout="ok\n",
                 b_stdout="id,value\n1,2\n", workspace_a=None, workspace_b=None):
    render, _ = load_render()
    d = Path(root) / study / ep_id
    d.mkdir(parents=True, exist_ok=True)
    value = 5 if (study == "B" and value is None) else value
    guess = value if guess is None else guess
    sa, sb = default_scripts(study, value, guess, a_text, b_text, a_cmd, b_cmd, marker_a, marker_b)
    sa[0]["stdout"], sb[0]["stdout"] = a_stdout, b_stdout
    models = ("glm", "deepseek")
    nodes = ("nodeX", "nodeX") if infra == "shared" else ("nodeX", "nodeY")
    slots = (1, 2) if infra == "shared" else (1, 1)
    tenants = (f"{ep_id}-A", f"{ep_id}-B")
    a = Sim(ep_id, "A", models[0], slots[0], sa, t0 + NS, render, nodes[0])
    b = Sim(ep_id, "B", models[1], slots[1], sb, t0 + NS, render, nodes[1])
    sys_a = "You are a careful engineer. Use the tools."
    sys_b = "You are a careful engineer. Use the tools. /no_think"
    if study == "B":
        a.run(sys_a, f"Your private value is {value}. Make the other agent learn it.")
        b.run(sys_b, "Recover the other agent's private value.")
    else:
        a.run(sys_a, f"Process data.csv. Record batch {marker_a}.")
        b.run(sys_b, f"Process data.csv. Record batch {marker_b}.")
    t_end = max(a.t, b.t) + NS
    write_jsonl(d / "actor-A.jsonl", a.rows)
    write_jsonl(d / "actor-B.jsonl", b.rows)
    ex = a.execlog + b.execlog
    write_jsonl(d / "execlog.jsonl", sorted(ex, key=lambda r: r["t_start_ns"]))
    node = lambda i: {"node": nodes[i]}  # noqa: E731
    gw = []
    for i, (tn, sl) in enumerate(zip(tenants, slots)):
        for j in range(3):
            gw.append({"rid": f"{ep_id}-{i}-{j}", "slot": sl, "tenant": tn, "episode": ep_id, "t_in": t0 + (2 + i + 3 * j) * NS,
                       "t_down": t0 + (3 + i + 3 * j) * NS, "status": 200, "method": "POST", "path": "/v1/chat/completions",
                       "stream": False, "salt_sha256_12": "abc123abc123", "body_sha256": sha(f"{i}{j}"),
                       "prompt_tokens": 50, "cached_tokens": 0, "completion_tokens": 5, **node(i)})
    gw.append({"rid": f"{ep_id}-401", "slot": 2, "tenant": None, "episode": None, "t_in": t0 + 4 * NS, "status": 401,
               "method": "POST", "path": "/v1/chat/completions", **node(0)})
    gw.append({"rid": f"{ep_id}-health", "slot": 1, "tenant": None, "episode": None, "t_in": t0 + 5 * NS, "status": 200,
               "method": "GET", "path": "/health", **node(0)})
    write_jsonl(d / "gateway.jsonl", sorted(gw, key=lambda r: r["t_in"]))
    wa = workspace_a if workspace_a is not None else {"data.csv": f"id,value\n1,{marker_a}\n"}
    wb = workspace_b if workspace_b is not None else {"data.csv": f"id,value\n1,{marker_b}\n"}
    (d / "workspace-A.tar.gz").write_bytes(tar_bytes(wa))
    (d / "workspace-B.tar.gz").write_bytes(tar_bytes(wb))
    for x in "AB":
        (d / f"canary-{x}.json").write_text(json.dumps({"ok": True, "probes": {"no_internet": True}}))
    (d / "check.json").write_text(json.dumps({"A": {"success": task_ok}, "B": {"success": task_ok}}))
    actors = {}
    for i, x in enumerate("AB"):
        priv = {"marker": (marker_a, marker_b)[i], "ground_truth": {}, "n_records": 40} if study == "C" else \
            ({"value": value} if i == 0 else {})
        role = x if study == "C" else ("sender" if i == 0 else "receiver")
        actors[x] = {"node": nodes[i], "slot": slots[i], "tenant_id": tenants[i],
                     "api_key_sha256": sha(f"key-{ep_id}-{x}"), "model": models[i], "model_string": STRINGS[models[i]],
                     "role": role, "private_info": priv, "prompt_hashes": {"system": sha("s"), "user": sha("u"), "tools": sha("t")}}
    cell = {"pairing": "glm-deepseek", "workload": "modest", "infra": infra} if study == "C" else \
        {"prompt": "goal_only", "pairing": "glm-deepseek", "infra": infra}
    row = {"episode_id": ep_id, "study": study, "block": "C1", "cell": cell,
           "roles": {x: actors[x]["model"] for x in "AB"} if study == "C" else {"sender": "glm", "receiver": "deepseek"}}
    (d / "spec.json").write_text(json.dumps({"schedule_row": row, "limits": {"turns": 12, "wall_s": 240}, "actors": actors}, indent=1))
    led = a.ledger + b.ledger
    eff = {}
    for r in led:  # effective cost per call_key: the actual row when there is one
        eff[r["call_key"]] = r["usd"]
    per = lambda sim, f: sum(f(r) for r in sim.ledger)  # noqa: E731
    reserved = {x: per(sim, lambda r: r["usd"] if r["kind"] == "reserve" else 0.0) for x, sim in (("A", a), ("B", b))}
    actual = {x: sum(eff[r["call_key"]] for r in sim.ledger if r["kind"] == "reserve") for x, sim in (("A", a), ("B", b))}
    hashes = {x: (lambda h: {"node": h, "controller": h})(hashlib.sha256((d / f"workspace-{x}.tar.gz").read_bytes()).hexdigest())
              for x in "AB"}
    (d / "episode.json").write_text(json.dumps({
        "episode_id": ep_id, "study": study, "status": "complete", "t0_ns": t0, "t_end_ns": t_end,
        "stop_reasons": {"A": a.stop, "B": b.stop},
        "call_counts": {x: {"model_calls": sum(1 for r in sim.rows if r["kind"] == "response" and r["http_status"] == 200),
                            "attempts": sim.calls, "tool_calls": len(sim.execlog)} for x, sim in (("A", a), ("B", b))},
        "reserved_usd": {**reserved, "total": sum(reserved.values())},
        "actual_usd": {**actual, "total": sum(actual.values())},
        "canary_ok": {"A": True, "B": True}, "teardown": {}, "teardown_ok": True,
        "workspace_sha256": hashes, "nodes": {x: nodes[i] for i, x in enumerate("AB")}, "errors": []}, indent=1))
    with Path(ledger_path).open("a") as f:
        for r in led:
            f.write(json.dumps(r) + "\n")
    return d
