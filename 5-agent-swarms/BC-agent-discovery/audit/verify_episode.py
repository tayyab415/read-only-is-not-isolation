#!/usr/bin/env python3
"""Offline audit of BC episode directories. Missing, duplicate or inconsistent records fail.

verify(episode_dir, ledger_path, secrets=[...]) -> {"ok", "failures", "checks", ...}
CLI: python3 audit/verify_episode.py DATA_DIR [--ledger P] [--secret S ...] [--secrets-file F]
     writes audit/verification.json (keyed by episode id). Run from BC/.
"""
import argparse
import collections
import datetime
import hashlib
import importlib.util
import json
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (AUDIT_DIR, BC_DIR, CONTINUE_MSG, TIME_UP_MSG, actor_info, actors_of,  # noqa: E402
                    discover_episodes, episode_id_of, find_key, good_response, parse_actor_log,
                    read_json, read_jsonl, sha256_text, study_of, tar_members, to_ns)

PRICES = {"glm": (0.15, 0.50), "deepseek": (0.09, 0.18), "qwen": (0.08, 0.28), "llama": (0.135, 0.40)}
MODEL_STRINGS = {"zai-org/GLM-5.3-Flash:deepinfra": "glm", "deepseek-ai/DeepSeek-V4-Flash:deepinfra": "deepseek",
                 "Qwen/Qwen3-32B:deepinfra": "qwen", "meta-llama/Llama-3.3-70B-Instruct:novita": "llama"}
MULTIPLIER = 1.2
ACTORS = ("A", "B")
SECTIONS = ("files", "spec", "episode", "history", "tools", "gateway", "ledger", "secrets")
OK_STOPS = ("finish", "turns", "wall")


def load_render():
    """render_tool_result from ../harness/render.py when present, else the local copy."""
    path = BC_DIR / "harness" / "render.py"
    if path.exists():
        try:
            spec = importlib.util.spec_from_file_location("bc_harness_render", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.render_tool_result, "harness/render.py"
        except Exception:  # fall through to the local copy; the source is recorded in the report
            pass
    from _render_fallback import render_tool_result
    return render_tool_result, "audit/_render_fallback.py"


class Report:
    def __init__(self):
        self.failures, self.infos = [], collections.defaultdict(dict)

    def fail(self, section, code, detail, file=None, line=None):
        self.failures.append({"section": section, "code": code, "detail": str(detail)[:500],
                              "file": file, "line": line})

    def info(self, section, **kw):
        self.infos[section].update(kw)

    def checks(self):
        out = {}
        for s in SECTIONS:
            codes = sorted({f["code"] for f in self.failures if f["section"] == s})
            out[s] = {"passed": not codes, "failure_codes": codes, **self.infos.get(s, {})}
        return out


class LedgerIndex:
    _cache = {}

    def __init__(self, path):
        self.path = Path(path)
        rows, self.errors = read_jsonl(self.path)
        self.reserve = collections.defaultdict(list)
        self.actual = collections.defaultdict(list)
        self.episode_rows = collections.defaultdict(list)
        for line, r in rows:
            ck, kind = r.get("call_key"), r.get("kind")
            if not isinstance(ck, str) or kind not in ("reserve", "actual"):
                self.errors.append(f"{self.path.name}:{line} row without call_key or known kind")
                continue
            (self.reserve if kind == "reserve" else self.actual)[ck].append(r)
            self.episode_rows[r.get("episode")].append(r)

    def window(self, lo_ns, hi_ns):
        """Rows whose utc_ns lies in [lo_ns, hi_ns]. v1 and v2 reuse episode ids and call_keys
        (episode/actor/turn/attempt) in one shared ledger, so rows are scoped by time.
        (Post-freeze audit fix, 2026-11-09 10:45 IST; see audit/CHANGES.md.)"""
        w = object.__new__(LedgerIndex)
        w.path, w.errors = self.path, list(self.errors)
        w.reserve, w.actual = collections.defaultdict(list), collections.defaultdict(list)
        w.episode_rows = collections.defaultdict(list)
        for kind, src, dst in (("reserve", self.reserve, w.reserve), ("actual", self.actual, w.actual)):
            for ck, rows in src.items():
                for r in rows:
                    if isinstance(r.get("utc_ns"), int) and lo_ns <= r["utc_ns"] <= hi_ns:
                        dst[ck].append(r)
                        w.episode_rows[r.get("episode")].append(r)
        return w

    @classmethod
    def get(cls, path):
        p = Path(path)
        try:
            st = p.stat()
            key = (str(p.resolve()), st.st_mtime_ns, st.st_size)
        except OSError:
            return cls(p)
        if key not in cls._cache:
            cls._cache = {key: cls(p)}
        return cls._cache[key]


def _close(a, b):
    return isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) <= 1e-12 + 1e-9 * abs(b)


def reservation_usd(model_key, request):
    pin, pout = PRICES[model_key]
    est_in = len(json.dumps(request).encode()) / 2
    return MULTIPLIER * (est_in * pin + request.get("max_tokens", 2048) * pout) / 1e6


def actual_usd(model_key, usage):
    pin, pout = PRICES[model_key]
    return MULTIPLIER * (usage["prompt_tokens"] * pin + usage["completion_tokens"] * pout) / 1e6


def stop_reasons_of(ep):
    raw = find_key(ep, ["stop_reasons", "stop", "stops", "actors"], depth=1)
    out = {}
    if isinstance(raw, dict):
        for a, v in raw.items():
            out[a] = v if isinstance(v, str) else (find_key(v, ["stop_reason", "reason", "stop"]) if isinstance(v, dict) else None)
    return out


def call_counts_of(ep):
    """-> {actor: {"model_calls":n, "attempts":n, "tool_calls":n}}; a bare int means model calls or attempts."""
    raw = find_key(ep, ["call_counts", "calls"], depth=1)
    out = {}
    if isinstance(raw, dict):
        for a, v in raw.items():
            out[a] = v if isinstance(v, dict) else ({"any": v} if isinstance(v, int) else None)
    return out


def assistant_message(resp_msg, turn):
    """The assistant message as the harness puts it in the next request (SPEC: the logged assistant
    message). Null content becomes "", only role/content/tool_calls survive, missing ids are filled."""
    msg = {"role": "assistant", "content": resp_msg.get("content") or ""}
    calls = [dict(tc) for tc in (resp_msg.get("tool_calls") or [])]
    for i, tc in enumerate(calls):
        tc.setdefault("id", f"call_{turn}_{i}")
    if calls:
        msg["tool_calls"] = calls
    return msg


def tool_call_args(arguments):
    if isinstance(arguments, dict):
        return arguments
    try:
        v = json.loads(arguments)
        return v if isinstance(v, dict) else None
    except (TypeError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------- actor log

def check_actor(rep, d, actor, rows, render, last_stop, wall_info):
    """History reconstruction and tool-row consistency. Returns facts used by later checks."""
    fname = f"actor-{actor}.jsonl"
    parsed = parse_actor_log(rows)
    for code, detail, line in parsed["order_errors"]:
        rep.fail("history", code, detail, fname, line)
    turns = parsed["turns"]
    facts = {"turns": 0, "attempts": [], "tool_rows": [], "stop": None, "last_good_turn": 0}

    stops = parsed["stops"]
    if len(stops) != 1:
        rep.fail("history", "stop_row_count", f"{len(stops)} stop rows", fname)
    elif rows and rows[-1][1] is not stops[0][1]:
        rep.fail("history", "stop_not_last", "stop row is not the last row", fname, stops[0][0])
    facts["stop"] = stops[-1][1] if stops else None
    stop_reason = (facts["stop"] or {}).get("reason")

    # turn and attempt sequence
    keys = [k for k in turns if isinstance(k, int)]
    if len(keys) != len(turns):
        rep.fail("history", "bad_turn_number", "non-integer turn", fname)
    if sorted(keys) != list(range(1, len(keys) + 1)):
        rep.fail("history", "turn_gap", f"turns present: {sorted(keys)}", fname)
    last_seen = 0
    for line, row in ((ln, r) for ln, r in rows if r.get("kind") == "request"):
        if isinstance(row.get("turn"), int):
            if row["turn"] < last_seen:
                rep.fail("history", "duplicate_turn", f"turn {row['turn']} requested after turn {last_seen}", fname, line)
            last_seen = max(last_seen, row["turn"])

    call_keys = []
    for k in sorted(keys):
        atts = turns[k]["attempts"]
        for i, a in enumerate(atts, 1):
            req = a["req"]
            call_keys.append((req.get("call_key"), a))
            if req.get("attempt") != i:
                rep.fail("history", "duplicate_turn",
                         f"turn {k} attempt numbers {[x['req'].get('attempt') for x in atts]}", fname, a["req_line"])
                break
            if not isinstance(req.get("request"), dict) or not isinstance(req["request"].get("messages"), list):
                rep.fail("history", "request_malformed", f"turn {k} attempt {i}", fname, a["req_line"])
            if i < len(atts):
                st = (a["resp"] or {}).get("http_status") if a["resp"] else None
                retriable = a["resp"] is None or st == 429 or (isinstance(st, int) and 500 <= st <= 599) \
                    or (st == 200 and not good_response(a["resp"]))
                if good_response(a["resp"]) or not retriable:
                    rep.fail("history", "duplicate_turn",
                             f"turn {k} attempt {i} was followed by a retry (status {st})", fname, a["req_line"])
            if i > 1 and atts[i - 1]["req"].get("request") != atts[0]["req"].get("request"):
                rep.fail("history", "retry_body_changed", f"turn {k}", fname, a["req_line"])
        facts["attempts"].extend(atts)
    facts["call_keys"] = call_keys
    facts["turns"] = len(keys)

    def final(k):
        return turns[k]["attempts"][-1] if turns.get(k) and turns[k]["attempts"] else None

    good = [k for k in sorted(keys) if final(k) and good_response(final(k)["resp"])]
    facts["last_good_turn"] = good[-1] if good else 0
    if keys:
        t1 = final(1)
        msgs = t1["req"]["request"].get("messages") if t1 and isinstance(t1["req"].get("request"), dict) else None
        if not (isinstance(msgs, list) and msgs and isinstance(msgs[0], dict) and msgs[0].get("role") == "system"):
            rep.fail("history", "turn1_malformed", "first message is not a system message", fname)
    for k in sorted(keys)[:-1]:
        if k in good:
            continue
        rep.fail("history", "request_after_failed_turn", f"turn {k} has no usable response but turn {k + 1} exists", fname)

    # tool rows per good turn, then reconstruction of the next request
    for k in good:
        resp_msg = assistant_message(final(k)["resp"]["response"]["choices"][0]["message"], k)
        calls = resp_msg.get("tool_calls") or []
        rows_k = turns[k]["tools"]
        by_id, ids = {}, [c.get("id") for c in calls]
        for line, tr in rows_k:
            tid = tr.get("tool_call_id")
            if tid in by_id:
                rep.fail("history", "duplicate_tool_row", f"turn {k} {tid}", fname, line)
            by_id[tid] = (line, tr)
            if tid not in ids:
                rep.fail("history", "tool_row_unknown_call", f"turn {k} {tid}", fname, line)
        is_last_good = k == good[-1]
        missing_seen = False
        if any(tr.get("name") == "finish" for _, tr in rows_k) and k + 1 in turns:
            rep.fail("history", "request_after_finish", f"turn {k + 1} requested after a finish call", fname)
        for c in calls:
            tid = c.get("id")
            if tid not in by_id:
                missing_seen = True
                # not run: calls after finish, or the actor stopped on wall or infra failure here
                if not (is_last_good and stop_reason in ("finish", "infra_failure", "wall")):
                    rep.fail("history", "tool_row_missing", f"turn {k} {tid}", fname)
                continue
            if missing_seen:
                rep.fail("history", "tool_row_order", f"turn {k} {tid} ran after a call that did not", fname)
            line, tr = by_id[tid]
            fn = c.get("function") or {}
            if tr.get("name") != fn.get("name"):
                rep.fail("tools", "tool_name_mismatch", f"turn {k} {tid}", fname, line)
            if tool_call_args(tr.get("arguments")) != tool_call_args(fn.get("arguments")) or \
                    (tool_call_args(fn.get("arguments")) is None and tr.get("arguments") != fn.get("arguments")):
                rep.fail("tools", "tool_arguments_mismatch", f"turn {k} {tid}", fname, line)
            ex = tr.get("exec")
            if tr.get("name") == "bash":
                if not isinstance(ex, dict) or not all(isinstance(ex.get(x), int) for x in ("t_start_ns", "t_end_ns", "rc")):
                    vis = tr.get("visible")
                    if isinstance(vis, str) and vis.startswith("[error]") and ex is None:
                        continue  # bad bash arguments: the model saw an error message, nothing ran
                    if not (is_last_good and stop_reason in ("infra_failure", "wall") and vis is None):
                        rep.fail("tools", "tool_exec_missing", f"turn {k} {tid}", fname, line)
                    continue
                if tr.get("visible") != render(ex):
                    rep.fail("tools", "tool_visible_mismatch", f"turn {k} {tid}", fname, line)
                facts["tool_rows"].append({"actor": actor, "turn": k, "line": line, "row": tr, "exec": ex})

        # reconstruct the following request
        if k + 1 not in turns or not final(k + 1):
            continue
        prev = final(k)["req"]["request"]["messages"] if isinstance(final(k)["req"].get("request"), dict) else None
        nxt = final(k + 1)["req"].get("request") if final(k + 1) else None
        if not (isinstance(prev, list) and isinstance(nxt, dict) and isinstance(nxt.get("messages"), list)):
            continue
        cur = nxt["messages"]
        exp = [resp_msg]
        for c in calls:
            tid = c.get("id")
            if tid not in by_id:
                continue
            tr = by_id[tid][1]
            ex = tr.get("exec")
            content = render(ex) if tr.get("name") == "bash" and isinstance(ex, dict) else tr.get("visible")
            exp.append({"role": "tool", "tool_call_id": tid, "content": content})
        if not calls:
            exp.append({"role": "user", "content": CONTINUE_MSG})
        timeup = {"role": "user", "content": TIME_UP_MSG}
        line = final(k + 1)["req_line"]
        if cur[:len(prev)] != prev:
            i = next((i for i, (x, y) in enumerate(zip(cur, prev)) if x != y), min(len(cur), len(prev)))
            rep.fail("history", "history_prefix_mismatch", f"turn {k + 1}: message {i} differs from turn {k} request", fname, line)
            continue
        suffix = cur[len(prev):]
        # the time-up message may follow, or (after a reply without tool calls) replace the continue message
        if suffix in (exp, exp + [timeup]) or (not calls and suffix == exp[:-1] + [timeup]):
            continue
        i = next((i for i, (x, y) in enumerate(zip(suffix, exp)) if x != y), min(len(suffix), len(exp)))
        want = exp[i] if i < len(exp) else None
        got = suffix[i] if i < len(suffix) else None
        if want and want.get("role") == "tool" and got is None:
            code = "history_missing_tool_result"
        elif want and got and want.get("role") == "tool" and got.get("role") == "tool" \
                and want["tool_call_id"] == got.get("tool_call_id"):
            code = "tool_content_mismatch"
        elif want and want.get("role") == "tool" and got and got.get("role") == "tool":
            code = "history_tool_order"
        elif want and want.get("role") == "tool" and got:
            code = "history_missing_tool_result"
        elif want is None:
            code = "history_extra_message"
        else:
            code = "history_mismatch"
        rep.fail("history", code, f"turn {k + 1}: expected message {i} {want and want.get('role')}, got {got and got.get('role')}", fname, line)
    return facts


# ---------------------------------------------------------------- main

def verify(episode_dir, ledger_path, secrets=(), clock_tolerance_s=2.0, max_turns=20, wall_s=360.0,
           strict_cost=True):
    d = Path(episode_dir)
    rep = Report()
    render, render_src = load_render()
    rep.info("history", render_source=render_src)
    result = {"episode_id": d.name, "study": None, "status": None}

    def finish_report():
        rep.info("secrets", secrets_checked=len([s for s in secrets if s]))
        return {**result, "ok": not rep.failures, "failures": rep.failures, "checks": rep.checks()}

    spec, err = read_json(d / "spec.json")
    if spec is None or not isinstance(spec, dict):
        rep.fail("spec", "spec_missing", err or "spec.json is not an object", "spec.json")
        spec = {}
    ep, err = read_json(d / "episode.json")
    if ep is None or not isinstance(ep, dict):
        rep.fail("episode", "episode_json_missing", err or "episode.json is not an object", "episode.json")
        ep = {}
    ep_id = episode_id_of(spec, d)
    result.update(episode_id=ep_id, study=study_of(spec, d), status=ep.get("status"))
    status = ep.get("status")
    complete = status == "complete"

    # files
    required = ["spec.json", "episode.json", "actor-A.jsonl", "actor-B.jsonl"]
    if complete:
        required += ["gateway.jsonl", "execlog.jsonl", "workspace-A.tar.gz", "workspace-B.tar.gz",
                     "canary-A.json", "canary-B.json", "check.json"]
    for name in required:
        if not (d / name).exists():
            rep.fail("files", "file_missing", name, name)
    if status not in ("complete", "provider_failure", "infra_failure", "budget_stop"):
        rep.fail("episode", "bad_status", f"status={status!r}", "episode.json")
    if complete:
        for a in ACTORS:
            cj, e = read_json(d / f"canary-{a}.json")
            if cj is not None and not (isinstance(cj, dict) and cj.get("ok") is True):
                rep.fail("files", "canary_failed", f"canary-{a}.json ok is not true", f"canary-{a}.json")

    # spec
    actors = actors_of(spec)
    info = {a: actor_info(spec, a) for a in ACTORS}
    if sorted(actors) != list(ACTORS):
        rep.fail("spec", "spec_actors_missing", f"actors found: {sorted(actors)}", "spec.json")
    for a in ACTORS:
        for key in ("tenant", "slot"):
            if not info[a][key]:
                rep.fail("spec", "spec_field_missing", f"actor {a} {key}", "spec.json")
    infra = find_key(spec, ["infra", "infrastructure"])
    if infra not in ("shared", "independent"):
        rep.fail("spec", "spec_infra_unknown", f"infra={infra!r}", "spec.json")
    tenants = {i["tenant"]: a for a, i in info.items() if i["tenant"]}
    if len(tenants) != 2:
        rep.fail("spec", "tenant_collision", "actors need two distinct tenants", "spec.json")
    if infra == "shared" and info["A"]["slot"] == info["B"]["slot"]:
        rep.fail("spec", "slot_collision", "shared infra needs distinct slots", "spec.json")

    t0, t_end = to_ns(ep.get("t0_ns", ep.get("t0"))), to_ns(ep.get("t_end_ns", ep.get("t_end")))
    if t0 is None or t_end is None or t_end < t0:
        rep.fail("episode", "episode_window_bad", f"t0={ep.get('t0_ns', ep.get('t0'))!r} t_end={ep.get('t_end_ns', ep.get('t_end'))!r}", "episode.json")
        t0 = t_end = None
    tol = int(clock_tolerance_s * 1e9)

    # actor logs
    facts, logs = {}, {}
    ep_stops = stop_reasons_of(ep)
    for a in ACTORS:
        if not (d / f"actor-{a}.jsonl").exists():
            continue
        rows, errs = read_jsonl(d / f"actor-{a}.jsonl")
        for e in errs:
            rep.fail("history", "malformed_row", e, f"actor-{a}.jsonl")
        logs[a] = rows
        facts[a] = check_actor(rep, d, a, rows, render, ep_stops.get(a), None)

    # exec window and execlog join
    ex_rows, errs = read_jsonl(d / "execlog.jsonl") if (d / "execlog.jsonl").exists() else ([], [])
    for e in errs:
        rep.fail("tools", "malformed_row", e, "execlog.jsonl")
    containers = {}
    for a in ACTORS:
        i = info[a]
        c = i["container"] or (f"bc-{ep_id}-{i['slot']}" if i["slot"] else None)
        containers[a] = c
    by_key = collections.defaultdict(list)  # (container, t_start_ns) -> [(line, row)]
    for line, r in ex_rows:
        ep_of = r.get("ep", r.get("episode"))  # node_ctl writes "ep"; FakeNode writes "episode"
        r.setdefault("ep", ep_of)
        key = (r.get("container") or f"bc-{ep_of}-{r.get('slot')}", r.get("t_start_ns"))
        by_key[key].append((line, r))
    matched, matched_rows, slack_min = set(), set(), None
    for a in ACTORS:
        for tr in (facts.get(a) or {}).get("tool_rows", []):
            ex, fname, line = tr["exec"], f"actor-{a}.jsonl", tr["line"]
            if t0 is not None:
                lo, hi = ex["t_start_ns"] - t0, t_end - ex["t_end_ns"]
                slack_min = min(x for x in (slack_min, lo, hi) if x is not None)
                if ex["t_start_ns"] < t0 - tol or ex["t_end_ns"] > t_end + tol or ex["t_end_ns"] < ex["t_start_ns"]:
                    rep.fail("tools", "exec_outside_window", f"turn {tr['turn']} exec [{ex['t_start_ns']},{ex['t_end_ns']}] "
                             f"vs window [{t0},{t_end}] tol {clock_tolerance_s}s", fname, line)
            if not complete and not (d / "execlog.jsonl").exists():
                continue
            key = (containers[a], ex["t_start_ns"])
            # the harness tags rows with their node; independent actors may share a container name
            cands = [h for h in by_key.get(key, []) if h[1].get("node") in (None, info[a]["node"])]
            mkey = key + (info[a]["node"],)
            if mkey in matched:
                rep.fail("tools", "exec_duplicate", f"two tool rows share {key}", fname, line)
            if len(cands) > 1:
                rep.fail("tools", "execlog_duplicate", f"{len(cands)} execlog rows for {key}", "execlog.jsonl", cands[1][0])
            if not cands:
                rep.fail("tools", "execlog_missing", f"no node execlog row for turn {tr['turn']}", fname, line)
                continue
            matched.add(mkey)
            matched_rows.add(id(cands[0][1]))
            r = cands[0][1]
            for k in ("rc", "t_end_ns", "timed_out", "stdout_bytes", "stderr_bytes"):
                if k in r and r[k] != ex.get(k):
                    rep.fail("tools", "exec_execlog_mismatch", f"{k}: tool row {ex.get(k)!r} vs execlog {r[k]!r}", fname, line)
            for k, v in (("stdout_sha256", ex.get("stdout", "")), ("stderr_sha256", ex.get("stderr", ""))):
                if k in r and r[k] != sha256_text(v):
                    rep.fail("tools", "exec_hash_mismatch", f"{k} turn {tr['turn']}", fname, line)
            args = tool_call_args(tr["row"].get("arguments")) or {}
            if "script_sha256" in r and isinstance(args.get("command"), str) and r["script_sha256"] != sha256_text(args["command"]):
                rep.fail("tools", "exec_script_hash_mismatch", f"turn {tr['turn']}", fname, line)
    own = set(c for c in containers.values() if c)
    foreign_margin = 0
    for key, hits in by_key.items():
        for line, r in hits:
            if id(r) in matched_rows:
                continue
            mine = r.get("ep") == ep_id or key[0] in own
            if mine:
                if t0 is None or t0 - tol <= key[1] <= t_end + tol:
                    rep.fail("tools", "execlog_unmatched", f"execlog row {key} has no tool row", "execlog.jsonl", line)
            elif t0 is not None and t0 <= key[1] <= t_end:
                rep.fail("tools", "foreign_execlog_row", f"execlog row of {key[0]} inside the window", "execlog.jsonl", line)
            else:
                foreign_margin += 1
    rep.info("tools", n_tool_rows=sum(len((facts.get(a) or {}).get("tool_rows", [])) for a in ACTORS),
             n_execlog_rows=len(ex_rows), foreign_margin_execlog_rows=foreign_margin,
             min_window_slack_s=None if slack_min is None else slack_min / 1e9, clock_tolerance_s=clock_tolerance_s)

    # gateway
    if (d / "gateway.jsonl").exists():
        gw, errs = read_jsonl(d / "gateway.jsonl")
        for e in errs:
            rep.fail("gateway", "malformed_row", e, "gateway.jsonl")
        n401, n401_slot, health, other_null, margin_foreign, rids, node_field = 0, collections.Counter(), 0, 0, 0, set(), False
        for line, r in gw:
            if r.get("rid") in rids:
                rep.fail("gateway", "gateway_duplicate_rid", r.get("rid"), "gateway.jsonl", line)
            rids.add(r.get("rid"))
            node_field |= "node" in r
            tn, t_in, st, slot = r.get("tenant"), r.get("t_in"), r.get("status"), str(r.get("slot"))
            inside = t0 is not None and isinstance(t_in, int) and t0 <= t_in <= t_end
            if tn is None:
                if st == 401:
                    n401 += 1
                    n401_slot[slot] += 1
                elif r.get("path") == "/health":
                    health += 1
                else:
                    other_null += 1
                continue
            if st == 401:
                rep.fail("gateway", "401_with_tenant", f"tenant {tn!r} on a 401 row", "gateway.jsonl", line)
            if tn not in tenants:
                if inside or t0 is None:
                    rep.fail("gateway", "foreign_tenant_row", f"tenant {tn!r} slot {slot} inside the window", "gateway.jsonl", line)
                else:
                    margin_foreign += 1
                continue
            a = tenants[tn]
            if r.get("episode") not in (None, ep_id):
                rep.fail("gateway", "tenant_episode_mismatch", f"row episode {r.get('episode')!r}", "gateway.jsonl", line)
            if slot != info[a]["slot"]:
                rep.fail("gateway", "tenant_slot_mismatch", f"tenant of {a} seen on slot {slot}", "gateway.jsonl", line)
            if t0 is not None and isinstance(t_in, int) and t_in < t0 - tol:
                rep.fail("gateway", "row_before_episode", f"tenant row {t0 - t_in} ns before t0", "gateway.jsonl", line)
            if infra == "independent" and slot != "1":
                rep.fail("gateway", "independent_slot2_activity", f"tenant row on slot {slot}", "gateway.jsonl", line)
            if "node" in r and info[a]["node"] is not None and r["node"] != info[a]["node"]:
                rep.fail("gateway", "node_tenant_mismatch", f"tenant of {a} on node {r['node']!r}, assigned {info[a]['node']!r}",
                         "gateway.jsonl", line)
        rep.info("gateway", n_rows=len(gw), n_401=n401, n_401_by_slot=dict(n401_slot), n_health_rows=health,
                 n_untenanted_other=other_null, foreign_tenant_rows_in_margin=margin_foreign,
                 node_field_present=node_field)

    # ledger
    index = LedgerIndex.get(ledger_path) if ledger_path and Path(ledger_path).exists() else None
    if index is not None and isinstance(t0, int) and isinstance(t_end, int):
        index = index.window(t0 - 120 * 10**9, t_end + 120 * 10**9)
    if index is None:
        rep.fail("ledger", "ledger_missing", str(ledger_path), "ledger")
    else:
        for e in index.errors:
            rep.fail("ledger", "ledger_malformed_row", e)
        seen, n_req, n_ok_resp = {}, 0, 0
        for a in ACTORS:
            for ck, att in (facts.get(a) or {}).get("call_keys", []):
                n_req += 1
                fname, line = f"actor-{a}.jsonl", att["req_line"]
                if not isinstance(ck, str) or not ck:
                    rep.fail("ledger", "call_key_missing", "request row has no call_key", fname, line)
                    continue
                if ck in seen:
                    rep.fail("ledger", "duplicate_call_key", ck, fname, line)
                seen[ck] = (a, att)
                res = index.reserve.get(ck, [])
                if len(res) != 1:
                    rep.fail("ledger", "ledger_reservation_missing" if not res else "ledger_duplicate_reservation", ck, fname, line)
                else:
                    r = res[0]
                    turn = att["req"].get("turn")
                    if (r.get("episode"), r.get("actor"), r.get("turn")) != (ep_id, a, turn):
                        rep.fail("ledger", "ledger_field_mismatch", f"{ck}: reserve row says "
                                 f"{r.get('episode')}/{r.get('actor')}/{r.get('turn')}", fname, line)
                    mk = r.get("model")
                    mk = MODEL_STRINGS.get(mk, mk)
                    if mk not in PRICES:
                        rep.fail("ledger", "ledger_unknown_model", f"{ck}: {r.get('model')!r}", fname, line)
                    elif strict_cost and not _close(r.get("usd"), reservation_usd(mk, att["req"].get("request", {}))):
                        rep.fail("ledger", "reservation_usd_mismatch", ck, fname, line)
                resp = att["resp"]
                acts = index.actual.get(ck, [])
                if resp is not None and good_response(resp):
                    n_ok_resp += 1
                    if len(acts) != 1:
                        rep.fail("ledger", "ledger_actual_missing" if not acts else "ledger_duplicate_actual", ck, fname, line)
                    else:
                        usage = (resp.get("response") or {}).get("usage") or {}
                        mk = (res[0].get("model") if res else None)
                        mk = MODEL_STRINGS.get(mk, mk)
                        if strict_cost and mk in PRICES and all(isinstance(usage.get(x), int) for x in ("prompt_tokens", "completion_tokens")) \
                                and not _close(acts[0].get("usd"), actual_usd(mk, usage)):
                            rep.fail("ledger", "actual_usd_mismatch", ck, fname, line)
                elif acts:
                    rep.fail("ledger", "ledger_actual_orphan", f"{ck}: actual row but no usable 200 response", fname, line)
        for r in index.episode_rows.get(ep_id, []):
            if r["call_key"] not in seen:
                rep.fail("ledger", "ledger_row_orphan", f"{r['kind']} row {r['call_key']} has no request row")
        # rows of other episodes using our call_keys
        for ck in seen:
            for r in index.reserve.get(ck, []) + index.actual.get(ck, []):
                if r.get("episode") != ep_id:
                    rep.fail("ledger", "call_key_other_episode", f"{ck} is ledgered under {r.get('episode')!r}")
        rep.info("ledger", n_request_rows=n_req, n_200_responses=n_ok_resp,
                 reserved_usd=sum(r["usd"] for r in index.episode_rows.get(ep_id, []) if r["kind"] == "reserve"
                                  and isinstance(r.get("usd"), (int, float))),
                 actual_usd=sum(r["usd"] for r in index.episode_rows.get(ep_id, []) if r["kind"] == "actual"
                                and isinstance(r.get("usd"), (int, float))))

    # secrets
    needles = []
    for s in secrets:
        if s and len(s) >= 4:
            needles.append(s.encode())
            esc = json.dumps(s)[1:-1]
            if esc != s:
                needles.append(esc.encode())
    for p in sorted(d.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(d))
        data = p.read_bytes()
        if any(n in data or n in rel.encode() for n in needles):
            rep.fail("secrets", "secret_found", f"a secret string appears in {rel}", rel)
        if p.name.endswith((".tar.gz", ".tgz")):
            try:
                for name, content in tar_members(p):
                    if any(n in content or n in name.encode() for n in needles):
                        rep.fail("secrets", "secret_found", f"a secret string appears in {rel}:{name}", rel)
            except (tarfile.TarError, OSError, EOFError) as e:
                rep.fail("secrets", "archive_unreadable", f"{rel}: {e}", rel)

    # episode.json consistency
    reasons = {a: (facts[a]["stop"] or {}).get("reason") for a in facts}
    for a in ACTORS:
        if a not in reasons:
            continue
        if ep_stops.get(a) is None:
            rep.fail("episode", "episode_json_field_missing", f"stop reason for {a}", "episode.json")
        elif ep_stops[a] != reasons[a]:
            rep.fail("episode", "stop_reason_mismatch", f"{a}: episode.json {ep_stops[a]!r} vs log {reasons[a]!r}", "episode.json")
    present = [r for r in reasons.values()]
    if len(present) == 2:
        if status == "complete" and not all(r in OK_STOPS for r in present):
            rep.fail("episode", "status_stop_inconsistent", f"complete but stop reasons {present}", "episode.json")
        if status == "provider_failure" and "provider_failure" not in present:
            rep.fail("episode", "status_stop_inconsistent", f"provider_failure but stop reasons {present}", "episode.json")
        if status == "infra_failure" and "infra_failure" not in present:
            rep.fail("episode", "status_stop_inconsistent", f"infra_failure but stop reasons {present}", "episode.json")
        if status == "budget_stop" and not any(str(r).startswith("budget") for r in present):
            rep.fail("episode", "status_stop_inconsistent", f"budget_stop but stop reasons {present}", "episode.json")
    counts = call_counts_of(ep)
    for a, f in facts.items():
        stop = f["stop"] or {}
        reason = stop.get("reason")
        n_att, n_turn = len(f["attempts"]), f["last_good_turn"]
        n_good = sum(1 for k in range(1, f["turns"] + 1) if good_turn(logs[a], k))
        n_exec = len(f["tool_rows"])
        c = counts.get(a)
        if c is None:
            rep.fail("episode", "episode_json_field_missing", f"call counts for {a}", "episode.json")
        elif "any" in c:
            if c["any"] not in (n_att, n_good):
                rep.fail("episode", "call_count_mismatch", f"{a}: episode.json {c['any']} vs {n_att} requests / {n_good} turns", "episode.json")
        else:
            for key, want in (("model_calls", n_good), ("attempts", n_att), ("tool_calls", n_exec)):
                if key in c and c[key] != want:
                    rep.fail("episode", "call_count_mismatch", f"{a} {key}: episode.json {c[key]} vs log {want}", "episode.json")
        if f["turns"] > max_turns:
            rep.fail("episode", "too_many_turns", f"{a}: {f['turns']} turns", f"actor-{a}.jsonl")
        last = f["turns"] and f["last_good_turn"] == f["turns"]
        if reason == "turns" and f["last_good_turn"] != max_turns:
            rep.fail("episode", "stop_turns_inconsistent", f"{a}: turns stop with {f['last_good_turn']} good turns", f"actor-{a}.jsonl")
        if reason == "wall" and t0 is not None and (t_end - t0) / 1e9 < wall_s - 2 * clock_tolerance_s:
            rep.fail("episode", "stop_wall_inconsistent", f"{a}: wall stop after {(t_end - t0) / 1e9:.1f}s", "episode.json")
        if reason == "finish":
            tl = {}
            if last:
                msg = final_message(logs[a], f["last_good_turn"])
                tl = next((c for c in (msg.get("tool_calls") or []) if (c.get("function") or {}).get("name") == "finish"), None)
            if not tl:
                rep.fail("episode", "finish_without_finish_call", f"{a}: no finish tool call in the last response", f"actor-{a}.jsonl")
            elif stop.get("finish_args") != (tool_call_args(tl["function"].get("arguments"))
                                              or {"_unparsed": tl["function"].get("arguments")}):
                rep.fail("episode", "finish_args_mismatch", a, f"actor-{a}.jsonl")
        if reason == "provider_failure" and last:
            rep.fail("episode", "stop_provider_inconsistent", f"{a}: last turn has a usable response", f"actor-{a}.jsonl")
        if reason in OK_STOPS and f["attempts"] and not last:
            rep.fail("episode", "trailing_failed_call", f"{a}: last turn has no usable response but stop is {reason}", f"actor-{a}.jsonl")
    if index is not None:
        rows = index.episode_rows.get(ep_id, [])
        res_rows = {r["call_key"]: r for r in rows if r["kind"] == "reserve"}
        act_rows = {r["call_key"]: r for r in rows if r["kind"] == "actual"}
        num = lambda r: r["usd"] if isinstance(r.get("usd"), (int, float)) else 0.0  # noqa: E731
        want = {"reserved_usd": {}, "actual_usd": {}}
        for ck, r in res_rows.items():
            a = r.get("actor")
            want["reserved_usd"][a] = want["reserved_usd"].get(a, 0.0) + num(r)
            eff = act_rows.get(ck, r)  # effective cost: last row per call_key
            want["actual_usd"][a] = want["actual_usd"].get(a, 0.0) + num(eff)
        for key in ("reserved_usd", "actual_usd"):
            got = find_key(ep, [key], depth=1)
            if got is None:
                rep.fail("episode", "episode_json_field_missing", key, "episode.json")
            elif isinstance(got, dict):
                parts = {a: want[key].get(a, 0.0) for a in ACTORS}
                parts["total"] = sum(parts.values())
                for a, w in parts.items():
                    if not _close(got.get(a), w):
                        rep.fail("episode", "usd_mismatch", f"{key}[{a}]: episode.json {got.get(a)} vs ledger {w}", "episode.json")
            elif not _close(got, sum(want[key].values())):
                rep.fail("episode", "usd_mismatch", f"{key}: episode.json {got} vs ledger {sum(want[key].values())}", "episode.json")

    # episode.json integrity fields written by the harness (checked when present)
    if complete:
        if ep.get("errors"):
            rep.fail("episode", "episode_errors_on_complete", str(ep["errors"])[:200], "episode.json")
        if ep.get("teardown_ok") is False or (isinstance(ep.get("canary_ok"), dict) and not all(ep["canary_ok"].values())):
            rep.fail("episode", "teardown_or_canary_failed", "complete episode with failed teardown or canary", "episode.json")
    ws = ep.get("workspace_sha256")
    if isinstance(ws, dict):
        for a, h in ws.items():
            f = d / f"workspace-{a}.tar.gz"
            if isinstance(h, dict) and h.get("node") is not None and h.get("node") != h.get("controller"):
                rep.fail("episode", "workspace_hash_mismatch", f"{a}: node and controller hashes differ", "episode.json")
            if isinstance(h, dict) and f.exists() and h.get("controller") != hashlib.sha256(f.read_bytes()).hexdigest():
                rep.fail("episode", "workspace_hash_mismatch", f"{a}: file does not match recorded hash", f.name)
    return finish_report()


def good_turn(rows, turn):
    return any(r.get("kind") == "response" and r.get("turn") == turn and good_response(r) for _, r in rows)


def final_message(rows, turn):
    last = None
    for _, r in rows:
        if r.get("kind") == "response" and r.get("turn") == turn and good_response(r):
            last = r["response"]["choices"][0]["message"]
    return last or {}


def verify_all(data_dir, ledger_path, secrets=(), **kw):
    out = {}
    for d in discover_episodes(data_dir):
        try:
            r = verify(d, ledger_path, secrets, **kw)
        except Exception as e:  # an auditor crash is a failure, never a pass
            r = {"episode_id": d.name, "study": None, "status": None, "ok": False, "checks": {},
                 "failures": [{"section": "audit", "code": "auditor_exception", "detail": f"{type(e).__name__}: {e}",
                               "file": None, "line": None}]}
        key = r["episode_id"] if r["episode_id"] not in out else f"{r['episode_id']}#{d.parent.name}"
        out[key] = r
    owners = collections.defaultdict(set)
    for d in discover_episodes(data_dir):
        for a in ACTORS:
            rows, _ = read_jsonl(d / f"actor-{a}.jsonl")
            for _, row in rows:
                if row.get("kind") == "request" and isinstance(row.get("call_key"), str):
                    owners[row["call_key"]].add(d)
    for ck, dirs in owners.items():
        if len(dirs) > 1:
            for d in dirs:
                for r in out.values():
                    if r.get("failures") is not None and Path(d).name == r["episode_id"].split("#")[0]:
                        r["failures"].append({"section": "ledger", "code": "duplicate_call_key_across_episodes",
                                              "detail": ck, "file": None, "line": None})
                        r["ok"] = False
                        if "ledger" in r["checks"]:
                            r["checks"]["ledger"]["passed"] = False
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("data_dir", type=Path)
    ap.add_argument("--ledger", type=Path, default=BC_DIR / "ledger" / "hf-ledger.jsonl")
    ap.add_argument("--secret", action="append", default=[])
    ap.add_argument("--secrets-file", type=Path)
    ap.add_argument("--hf-token", action="store_true", help="also scan for the HF token (via harness/models.py, no network)")
    ap.add_argument("--out", type=Path, default=AUDIT_DIR / "verification.json")
    ap.add_argument("--clock-tolerance-s", type=float, default=2.0)
    args = ap.parse_args()
    secrets = list(args.secret)
    if args.secrets_file:
        secrets += [l.strip() for l in args.secrets_file.read_text().splitlines() if l.strip()]
    if args.hf_token:
        sys.path.insert(0, str(BC_DIR / "harness"))
        import models  # noqa: E402
        secrets.append(models.load_token())
    if not secrets:
        print("warning: no secrets supplied, so the secret scan checks nothing", file=sys.stderr)
    res = verify_all(args.data_dir, args.ledger, secrets, clock_tolerance_s=args.clock_tolerance_s)
    doc = {"generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "data_dir": str(args.data_dir),
           "ledger": str(args.ledger), "n_episodes": len(res), "n_ok": sum(r["ok"] for r in res.values()),
           "episodes": res}
    args.out.write_text(json.dumps(doc, indent=2))
    for k, r in res.items():
        codes = sorted({f["code"] for f in r["failures"]})
        print(f"{k}: {'PASS' if r['ok'] else 'FAIL ' + ','.join(codes)}")
    print(f"{doc['n_ok']}/{doc['n_episodes']} episodes pass -> {args.out}")
    sys.exit(0 if doc["n_ok"] == doc["n_episodes"] else 1)


if __name__ == "__main__":
    main()
