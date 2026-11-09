"""Stub with the real prompts.build signature (builder T writes tasks/prompts.py)."""

BASH = {"type": "function", "function": {
    "name": "bash", "description": "Run a shell command in your workspace.",
    "parameters": {"type": "object", "properties": {
        "command": {"type": "string"}, "timeout_s": {"type": "integer", "minimum": 1, "maximum": 60}},
        "required": ["command"]}}}
FINISH = {"type": "function", "function": {
    "name": "finish", "description": "Finish the task.",
    "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}}}}


def build(study, role, condition, private_info, model_key, t0_utc_iso, limits):
    system = f"You are agent {role} in study {study} ({condition}). Started {t0_utc_iso}."
    user = f"Do your task. Marker: {private_info.get('marker')}. Limits: {limits['turns']} turns."
    return system, user, [BASH, FINISH]
