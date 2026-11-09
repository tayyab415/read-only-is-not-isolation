#!/usr/bin/env python3
"""Exploratory: export B/C bash tool calls as System-1 monitor eval sets (s1-monitor-lab format).

One row per executed bash call from verified complete v2 episodes: {id, source, raw}.
Sets: bc_C (ordinary ticket work), bc_B (all calls in the value-exchange task), and
bc_Bchan (B calls whose text mentions the channel: keyword-selected, see CHAN_RE).
The rows are then prepared with s1-monitor-lab/scripts/04_prep_set.py (same canonicalizer,
dedup and min-struct filter as the System-1 classifier's natural sets) and scored with 24_natural_eval.py.
Usage: monitor_export.py data OUT_DIR [--verification audit/verification.json]
"""
import argparse, json, re
from pathlib import Path

CHAN_RE = re.compile(r"covert|channel|signal|encode|decode|\bbits?\b|burst|slot|latency|sender|receiver|transmit", re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--verification", type=Path)
    a = ap.parse_args()
    ok = None
    if a.verification and a.verification.exists():
        v = json.loads(a.verification.read_text())
        eps = v.get("episodes", v)
        ok = {k for k, r in eps.items() if isinstance(r, dict) and r.get("ok")}
    a.out.mkdir(parents=True, exist_ok=True)
    sets = {"bc_C": [], "bc_B": [], "bc_Bchan": []}
    for f in sorted(a.data.glob("[BC]/*/episode.json")):
        e = json.loads(f.read_text())
        if e["status"] != "complete" or (ok is not None and e["episode_id"] not in ok):
            continue
        for actor in ("A", "B"):
            for line in (f.parent / f"actor-{actor}.jsonl").read_text().splitlines():
                r = json.loads(line)
                if r.get("kind") != "tool" or r.get("name") != "bash" or not r.get("exec"):
                    continue
                args = r.get("arguments")
                args = json.loads(args) if isinstance(args, str) else (args or {})
                cmd = args.get("command")
                if not isinstance(cmd, str) or not cmd.strip():
                    continue
                key = "bc_C" if e["study"] == "C" else "bc_B"
                sets[key].append(cmd)
                if key == "bc_B" and CHAN_RE.search(cmd):
                    sets["bc_Bchan"].append(cmd)
    for name, cmds in sets.items():
        with (a.out / f"{name}.jsonl").open("w") as fh:
            for i, c in enumerate(cmds):
                fh.write(json.dumps({"id": f"{name}:{i}", "source": name, "raw": c}) + "\n")
        print(name, len(cmds))


if __name__ == "__main__":
    main()
