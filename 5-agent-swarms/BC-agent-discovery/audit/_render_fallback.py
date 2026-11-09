"""Local copy of the SPEC rule for model-visible tool results.

Used only when ../harness/render.py is missing. tests/test_render_agree.py checks that the
two agree whenever the harness file exists. A timed-out result reports exec_json["timeout_s"]
when present, otherwise the measured duration rounded to whole seconds.
"""

LIMIT = 4000
KEEP_HEAD = 2500
KEEP_TAIL = 1000


def _header(exec_json):
    if exec_json.get("timed_out"):
        t = exec_json.get("timeout_s")
        if t is None:
            t = round((exec_json.get("t_end_ns", 0) - exec_json.get("t_start_ns", 0)) / 1e9)
        return f"[timed out after {t}s]"
    return f"[exit {exec_json.get('rc')}]"


def render_tool_result(exec_json):
    text = (f"{_header(exec_json)}\nSTDOUT:\n{exec_json.get('stdout', '')}"
            f"\nSTDERR:\n{exec_json.get('stderr', '')}")
    if len(text) <= LIMIT:
        return text
    omitted = len(text) - KEEP_HEAD - KEEP_TAIL
    return f"{text[:KEEP_HEAD]}\n...[{omitted} chars omitted]...\n{text[-KEEP_TAIL:]}"
