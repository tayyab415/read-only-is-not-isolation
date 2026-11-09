"""Shared helpers for the BC audit. Python 3.11 stdlib only."""
import hashlib
import io
import json
import tarfile
from pathlib import Path

AUDIT_DIR = Path(__file__).resolve().parent
BC_DIR = AUDIT_DIR.parent

CONTINUE_MSG = "Continue using the tools. Call finish when you are done."
TIME_UP_MSG = "Time is almost up. Call finish now."


def sha256_text(s):
    return hashlib.sha256(s.encode()).hexdigest()


def read_jsonl(path):
    """-> (rows, errors). rows are (lineno, obj) with 1-based line numbers. Bad lines are errors."""
    rows, errors = [], []
    p = Path(path)
    if not p.exists():
        return rows, [f"missing file {p.name}"]
    with p.open(encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                errors.append(f"{p.name}:{n} bad json ({e.msg})")
                continue
            if not isinstance(obj, dict):
                errors.append(f"{p.name}:{n} row is not an object")
                continue
            rows.append((n, obj))
    return rows, errors


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, f"missing file {Path(path).name}"
    except json.JSONDecodeError as e:
        return None, f"{Path(path).name} bad json ({e.msg})"


def to_ns(v):
    """Episode times may be ns ints or epoch-second floats."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(v) if v > 1e14 else int(v * 1e9)


def find_key(obj, aliases, depth=3):
    """First value found under any alias, searching obj then nested dicts (breadth first)."""
    level = [obj]
    for _ in range(depth + 1):
        nxt = []
        for o in level:
            if not isinstance(o, dict):
                continue
            for a in aliases:
                if a in o and o[a] is not None:
                    return o[a]
            nxt.extend(v for v in o.values() if isinstance(v, dict))
        level = nxt
    return None


def actors_of(spec):
    """spec.json -> {"A": {...}, "B": {...}} (empty dict when the shape is not recognised)."""
    a = find_key(spec, ["actors"], depth=1)
    if isinstance(a, list):
        a = {str(x.get("actor") or x.get("id") or x.get("name")): x for x in a if isinstance(x, dict)}
    return a if isinstance(a, dict) else {}


def actor_info(spec, actor):
    a = actors_of(spec).get(actor, {})
    private = a.get("private") if isinstance(a.get("private"), dict) else a.get("private_info")
    private = private if isinstance(private, dict) else {}
    slot = find_key(a, ["slot"])
    return {
        "tenant": find_key(a, ["tenant", "tenant_id"]),
        "slot": None if slot is None else str(slot),
        "node": find_key(a, ["node", "host"]),
        "model": find_key(a, ["model", "model_key"]),
        "role": find_key(a, ["role"]),
        "marker": find_key(private, ["marker", "workload_marker"]) or find_key(a, ["marker", "private_marker"]),
        "value": find_key(private, ["value", "private_value", "sender_value"])
        if find_key(private, ["value", "private_value", "sender_value"]) is not None
        else find_key(a, ["private_value", "sender_value"]),
        "container": find_key(a, ["container"]),
    }


def episode_id_of(spec, dirpath):
    return find_key(spec or {}, ["episode_id", "episode"], depth=1) or Path(dirpath).name


def norm_pairing(p):
    if not isinstance(p, str):
        return p
    parts = sorted(p.replace("_", "-").lower().split("-"))
    return "-".join(parts)


def cell_of(spec, study):
    infra = find_key(spec, ["infra", "infrastructure"])
    pairing = find_key(spec, ["pairing"])
    if pairing is None:
        models = sorted({str(actor_info(spec, a)["model"]) for a in actors_of(spec)})
        if models:
            pairing = f"{models[0]}-{models[0]}" if len(models) == 1 else "-".join(models)
    pairing = norm_pairing(pairing)
    if study == "B":
        return {"prompt": find_key(spec, ["prompt", "prompt_condition"]), "pairing": pairing, "infra": infra}
    return {"pairing": pairing, "workload": find_key(spec, ["workload"]), "infra": infra}


def study_of(spec, dirpath):
    s = find_key(spec or {}, ["study"], depth=1)
    return s if s in ("B", "C") else Path(dirpath).parent.name.upper()[:1]


def discover_episodes(data_dir):
    """Episode directories (contain spec.json or episode.json) under data_dir, depth <= 2."""
    root = Path(data_dir)
    found = []
    for p in sorted(root.rglob("*")):
        if p.is_dir() and (p / "spec.json").exists() or p.is_dir() and (p / "episode.json").exists():
            if len(p.relative_to(root).parts) <= 2:
                found.append(p)
    if (root / "spec.json").exists() or (root / "episode.json").exists():
        found.append(root)
    return found


def tar_members(path):
    """Yield (member_name, bytes) for each regular file in a tar.gz; raise on corrupt archives."""
    with tarfile.open(path) as tf:
        for m in tf.getmembers():
            f = tf.extractfile(m) if m.isfile() else None
            yield m.name, (f.read() if f is not None else b"")


def message_text(msg):
    c = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(p.get("text", "") for p in c if isinstance(p, dict))
    return ""


def good_response(resp_row):
    r = (resp_row or {}).get("response")
    return bool(resp_row and resp_row.get("http_status") == 200 and isinstance(r, dict)
                and isinstance(r.get("choices"), list) and r["choices"]
                and isinstance(r["choices"][0].get("message"), dict))


def parse_actor_log(rows):
    """Group an actor log into turns of attempts. Pure parsing, no judgement.

    -> {"turns": {k: {"attempts":[{"req","req_line","resp","resp_line"}], "tools":[(line,row)]}},
        "stops": [(line,row)], "order_errors": [(code, detail, line)]}"""
    turns, stops, errs = {}, [], []
    cur, pending = 0, None
    for line, row in rows:
        kind, k = row.get("kind"), row.get("turn")
        if stops and kind != "stop":
            errs.append(("row_after_stop", f"{kind} row after stop", line))
        if kind == "request":
            t = turns.setdefault(k, {"attempts": [], "tools": []})
            t["attempts"].append({"req": row, "req_line": line, "resp": None, "resp_line": None})
            pending = t["attempts"][-1]
            cur = k if isinstance(k, int) else cur
        elif kind == "response":
            if pending is None or pending["resp"] is not None or pending["req"].get("turn") != k:
                errs.append(("orphan_response", f"response for turn {k} has no open request", line))
            else:
                pending["resp"], pending["resp_line"] = row, line
        elif kind == "tool":
            if k in turns:
                turns[k]["tools"].append((line, row))
            else:
                errs.append(("orphan_tool", f"tool row for unknown turn {k}", line))
        elif kind == "stop":
            stops.append((line, row))
        else:
            errs.append(("unknown_row_kind", f"kind={kind!r}", line))
    return {"turns": turns, "stops": stops, "order_errors": errs}
