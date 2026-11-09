"""Model-visible rendering of a node exec result (SPEC "Episode loop").

Pure and deterministic. The audit imports render_tool_result to rebuild the
exact tool messages a model saw.
"""

LIMIT = 4000
KEEP_HEAD = 2500
KEEP_TAIL = 1000


def _header(exec_json):
    if exec_json.get("timed_out"):
        t = exec_json.get("timeout_s")
        if t is None:
            # Older rows without the clipped timeout: use the measured duration.
            t = round((exec_json.get("t_end_ns", 0) - exec_json.get("t_start_ns", 0)) / 1e9)
        return f"[timed out after {t}s]"
    return f"[exit {exec_json.get('rc')}]"


def clip(text, limit=LIMIT, head=KEEP_HEAD, tail=KEEP_TAIL):
    if len(text) <= limit:
        return text
    omitted = len(text) - head - tail
    return f"{text[:head]}\n...[{omitted} chars omitted]...\n{text[-tail:]}"


def render_tool_result(exec_json):
    text = (f"{_header(exec_json)}\nSTDOUT:\n{exec_json.get('stdout', '')}"
            f"\nSTDERR:\n{exec_json.get('stderr', '')}")
    return clip(text)
