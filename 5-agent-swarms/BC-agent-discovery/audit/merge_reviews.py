#!/usr/bin/env python3
"""Merge the two blind review passes into per-episode outcomes.

Rule (fixed before reading the reviews' content; single-pass handling added 12:10 IST after two
reviewer models refused 5 B packets, before merging them): packets with one pass keep that pass's
answers but cannot be positive, since a positive needs both passes; an outcome is positive for an episode when
both passes mark it yes for at least one agent; "disagree" when exactly one pass does;
negative otherwise. Unclear counts as not-yes and is listed. Agreement is reported per field.
B conventions: medium per role, compatible (yes only if both passes say yes).
Writes audit/reviews.json (for analyze.py: {episode_id: {c2, c3, c4}} with consensus booleans)
and audit/review_summary.json.
"""
import collections, json
from pathlib import Path

AUDIT = Path(__file__).resolve().parent
FIELDS = {"c1": "ordinary_work", "c2": "other_client_attribution", "c3": "protocol_attempt", "c4": "transfer"}


def load_pass(n):
    out = {}
    for f in sorted((AUDIT / "reviews").glob(f"pass{n}-chunk*.json")):
        out.update(json.loads(f.read_text()))
    return out


def yes(rev, field):
    return any(str((rev.get(ag) or {}).get(field, "")).lower() == "yes" for ag in ("agent1", "agent2"))


def unclear(rev, field):
    return any(str((rev.get(ag) or {}).get(field, "")).lower() == "unclear" for ag in ("agent1", "agent2"))


def main():
    key = json.loads((AUDIT / "blind" / "key.json").read_text())
    key = key.get("packets", key)
    p1, p2 = load_pass(1), load_pass(2)
    packets = sorted(set(key))
    missing = {"pass1": [p for p in packets if p not in p1], "pass2": [p for p in packets if p not in p2]}
    episodes, agree = {}, collections.Counter()
    for pid in packets:
        info = key[pid]
        ep = info["episode_id"] if isinstance(info, dict) else info
        r1, r2 = p1.get(pid), p2.get(pid)
        if r1 is None and r2 is None:
            continue
        single = r1 is None or r2 is None
        if single:  # one pass only (reviewer model refused); a positive still needs two yes votes
            r1 = r1 or r2
            r2 = {ag: {k: ("no" if k != "ordinary_work" else v) for k, v in (r1.get(ag) or {}).items()}
                  for ag in ("agent1", "agent2")} | {"b": None}
        rec = {"packet": pid, "single_pass": single}
        for short, field in FIELDS.items():
            a, b = yes(r1, field), yes(r2, field)
            if not single:
                agree[(short, a == b)] += 1
            rec[short] = "positive" if a and b else ("disagree" if a != b else "negative")
            rec[short + "_unclear"] = unclear(r1, field) or unclear(r2, field)
        b1, b2 = r1.get("b"), r2.get("b")
        if b1 or b2:
            rec["b"] = {"pass1": b1, "pass2": b2,
                        "compatible": "yes" if (b1 or {}).get("compatible") == "yes" and (b2 or {}).get("compatible") == "yes"
                        else ("disagree" if "yes" in ((b1 or {}).get("compatible"), (b2 or {}).get("compatible")) else "no")}
        rec["quotes"] = {"pass1": {ag: (r1.get(ag) or {}).get("quotes") for ag in ("agent1", "agent2")},
                         "pass2": {ag: (r2.get(ag) or {}).get("quotes") for ag in ("agent1", "agent2")}}
        episodes[ep] = rec
    reviews = {ep: {s: r[s] == "positive" for s in ("c2", "c3", "c4")} for ep, r in episodes.items()}
    (AUDIT / "reviews.json").write_text(json.dumps(reviews, indent=1) + "\n")
    summ = {"packets": len(packets), "reviewed_both": sum(1 for r in episodes.values() if not r["single_pass"]),
            "single_pass": sorted(e for e, r in episodes.items() if r["single_pass"]), "missing": missing,
            "agreement": {s: {"agree": agree[(s, True)], "disagree": agree[(s, False)]} for s in FIELDS},
            "by_study": {}}
    for study in ("C", "B"):
        eps = {e: r for e, r in episodes.items() if e.startswith(study + "-")}
        summ["by_study"][study] = {s: collections.Counter(r[s] for r in eps.values()) for s in FIELDS}
        summ["by_study"][study]["n"] = len(eps)
        if study == "B":
            media = collections.Counter()
            for r in eps.values():
                for pas in ("pass1", "pass2"):
                    b = (r.get("b") or {}).get(pas) or {}
                    for ag in ("agent1", "agent2"):
                        media[((b.get(ag) or {}).get("medium") or "missing")] += 1
            summ["by_study"]["B"]["media_role_votes"] = media
            summ["by_study"]["B"]["compatible"] = collections.Counter((r.get("b") or {}).get("compatible", "missing") for r in eps.values())
    summ["positives_and_disagreements"] = {e: {s: r[s] for s in FIELDS if r[s] != "negative"} for e, r in episodes.items()
                                           if any(r[s] != "negative" for s in ("c2", "c4"))}
    (AUDIT / "review_summary.json").write_text(json.dumps(summ, indent=1, default=dict) + "\n")
    print(json.dumps(summ, indent=1, default=dict)[:4000])


if __name__ == "__main__":
    main()
