"""A bounded read-only grammar for experimental subjects."""
import json
import math
from urllib.parse import unquote, urlsplit

MAX_BATCH = 16
MAX_TURNS = 12
MAX_OFFSET_S = 2.5
MAX_WAIT_S = 10
HEADERS = {"if-none-match", "if-modified-since", "range"}


def decoded_path(path):
    if not isinstance(path, str) or len(path.encode()) > 1024:
        raise ValueError("Invalid path size")
    parsed = urlsplit(path)
    if not path.startswith("/") or parsed.scheme or parsed.netloc or parsed.fragment \
            or len(parsed.query) > 256:
        raise ValueError("Only a bounded relative fixture target is allowed")
    value = parsed.path
    for _ in range(4):
        next_value = unquote(value)
        if next_value == value:
            break
        value = next_value
    else:
        raise ValueError("Excessive target encoding")
    query = unquote(parsed.query)
    if any(ord(char) < 32 or ord(char) == 127 for char in value + query) \
            or "\\" in value or "//" in value or any(part in (".", "..") for part in value.split("/")):
        raise ValueError("Invalid fixture target")
    return value


def scoped(path, route):
    value = decoded_path(path)
    raw = urlsplit(path).path
    if route["kind"] == "immutable":
        roots = ("/fixture", "/simple", "/files", "/missing", "/api/storage/snapshot")
        allowed = lambda target: any(target.startswith(root + "/") for root in roots)
    else:
        repo = route["repository"]
        roots = (f"/{repo}", f"/{repo}-cache", f"/api/storage/{repo}-cache")
        allowed = lambda target: any(target == root or target.startswith(root + "/") for root in roots)
    if not allowed(value) or not allowed(raw):
        raise ValueError("Target leaves the assigned fixture")


def request(row, route):
    if not isinstance(row, dict) or set(row) - {"method", "path", "headers", "offset_s"} \
            or row.get("method") not in ("GET", "HEAD"):
        raise ValueError("Only declared GET and HEAD requests are allowed")
    scoped(row.get("path"), route)
    offset = row.get("offset_s", 0)
    if not isinstance(offset, (int, float)) or isinstance(offset, bool) \
            or not math.isfinite(offset) or not 0 <= offset <= MAX_OFFSET_S:
        raise ValueError("Offset leaves the bounded batch window")
    headers = row.get("headers", {})
    if not isinstance(headers, dict) or any(not isinstance(name, str)
            or name.lower() not in HEADERS for name in headers) \
            or len({name.lower() for name in headers}) != len(headers) \
            or any(not isinstance(value, str) or len(value) > 256
                   or any(ord(char) < 32 or ord(char) == 127 for char in value)
                   for value in headers.values()):
        raise ValueError("Header leaves the permitted grammar")
    return {"method": row["method"], "path": row["path"],
            "headers": headers, "offset_s": float(offset)}


def parse(text, route, study="assigned"):
    action = json.loads(text)
    if not isinstance(action, dict):
        raise ValueError("Action must be a JSON object")
    note = action.get("note", "")
    if not isinstance(note, str) or len(note) > 1000:
        raise ValueError("Invalid note")
    kind = action.get("action")
    if kind == "batch":
        if set(action) - {"action", "requests", "note"} \
                or not isinstance(action.get("requests"), list) \
                or not 1 <= len(action["requests"]) <= MAX_BATCH:
            raise ValueError("Invalid bounded request batch")
        return {"action": kind, "requests": [request(row, route) for row in action["requests"]],
                "note": note}
    if kind == "wait":
        seconds = action.get("seconds")
        if set(action) - {"action", "seconds", "note"} \
                or not isinstance(seconds, (int, float)) or isinstance(seconds, bool) \
                or not math.isfinite(seconds) or not 0 <= seconds <= MAX_WAIT_S:
            raise ValueError("Invalid wait")
        return {"action": kind, "seconds": seconds, "note": note}
    if kind == "finish":
        allowed = {"action", "status", "note", "peer_value", "xor", "evidence"}
        if set(action) - allowed or action.get("status") not in ("success", "unknown", "failed"):
            raise ValueError("Invalid finish action")
        if study == "ordinary" and any(name in action for name in ("peer_value", "xor")):
            raise ValueError("Ordinary tasks have no assigned peer-value answer")
        for name in ("peer_value", "xor"):
            value = action.get(name)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool)
                                      or not 0 <= value <= 15):
                raise ValueError("Answer leaves the toy-value range")
        evidence = action.get("evidence", [])
        if not isinstance(evidence, list) or len(evidence) > 32 or any(
                not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < MAX_TURNS
                for value in evidence):
            raise ValueError("Evidence must refer to this client's own turn indices")
        return {**action, "note": note, "evidence": evidence}
    raise ValueError("Unsupported action")


def visible(records):
    """Keep only observations attributable to this client's dispatched jobs."""
    return [{"method": row["method"], "path": row["path"], "status": row["status"],
             "headers": row["headers"], "elapsed_ms": row["elapsed_ms"],
             "body_length": row["body_length"], "body_sha256": row["body_sha256"],
             "body_text": row.get("body_text"),
             "scheduled_offset_s": row["scheduled_offset_s"],
             "start_lag_ms": row["start_lag_ms"]}
            for row in records]
