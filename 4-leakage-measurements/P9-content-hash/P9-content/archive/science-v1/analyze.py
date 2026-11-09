#!/usr/bin/env python3
"""P2b analysis. Runs on the Mac.

    python3 analyze.py freeze  DATA   # calibration only; writes DECODERS-FROZEN.json
    python3 analyze.py holdout DATA   # refuses unless the freeze and seals match

Statistic S = median elapsed_ms of the receiver's 15 timed probes (post.timed.*).
Threshold = midpoint of the two calibration class medians; direction from their order.
"""
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ARMS = ("equal", "distinct", "aa-equal", "aa-distinct")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    with Path(path).open() as stream:
        return [json.loads(line) for line in stream]


def statistic(trial):
    timed = [r["elapsed_ms"] for r in trial["post"] if str(r.get("probe", "")).startswith("post.timed.")]
    if len(timed) != 15 or any(v is None for v in timed):
        raise ValueError(f"{trial['trial_id']}: expected 15 timed probes")
    return statistics.median(timed)


def h2(p):
    return 0.0 if p <= 0 or p >= 1 else -p * math.log2(p) - (1 - p) * math.log2(1 - p)


def holm(ps):
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    out, running = [1.0] * len(ps), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(ps) - rank) * ps[i]))
        out[i] = running
    return out


def freeze(data):
    check_sources()
    if (HERE / "DECODERS-FROZEN.json").exists() \
            or any((data / arm / "holdout.jsonl").exists() for arm in ARMS):
        raise RuntimeError("Do not fit or refreeze after a decoder freeze or holdout collection")
    inputs = check_split_audit(data, "calibration")
    decoders = {}
    for arm in ARMS:
        path = data / arm / "calibration.jsonl"
        trials = load(path)
        if len(trials) != 100 or any(sum(t["label"] == label for t in trials) != 50 for label in (0, 1)):
            raise RuntimeError("P2b needs all 100 balanced calibration trials per arm")
        s1 = [statistic(t) for t in trials if t["label"] == 1]
        s0 = [statistic(t) for t in trials if t["label"] == 0]
        m1, m0 = statistics.median(s1), statistics.median(s0)
        decoders[arm] = {"threshold_ms": (m1 + m0) / 2, "active_is_below": m1 < m0,
                         "calibration_median_active_ms": m1, "calibration_median_idle_ms": m0,
                         "calibration_trials": len(trials), "calibration_sha256": sha(path)}
    out = {"frozen_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
           "decoders": decoders, "sha256": source_hashes(), "calibration_sha256": inputs,
           "family": list(ARMS), "family_alpha": .05, "correction": "Holm",
           "primary_test": "exact balanced-sender-direction conditional assignment tail"}
    with (HERE / "DECODERS-FROZEN.json").open("x") as stream:
        json.dump(out, stream, indent=2)
    return out


def source_hashes():
    import p2b
    return {name: sha(HERE / name) for name in sorted(p2b.SOURCE_NAMES | {"SOURCES-SHA256.txt"})}


def check_sources():
    import p2b
    p2b.check_sources()


def check_split_audit(data, split):
    import p2b
    path = data / f"audit-{split}.json"
    audit = json.loads(path.read_text())
    if audit.get("passed") is not True or audit.get("audit_source_sha256") != sha(HERE / "audit.py") \
            or audit.get("source_manifest_sha256") != sha(HERE / "SOURCES-SHA256.txt") \
            or len(audit.get("runs", [])) != 4 \
            or {r.get("arm") for r in audit["runs"]} != set(ARMS) \
            or any(r.get("passed") is not True or r.get("split") != split
                   or r.get("trials") != p2b.SIZES[split] for r in audit["runs"]):
        raise RuntimeError("All four complete splits need the current native audit")
    inputs = {f"audit-{split}.json": sha(path)}
    for arm in ARMS:
        folder = data / arm
        seal_path = folder / f"{split}-sealed.json"
        seal = json.loads(seal_path.read_text())
        reported = next(r for r in audit["runs"] if r["arm"] == arm)
        if reported.get("seal_sha256") != sha(seal_path) or seal.get("complete") is not True \
                or seal.get("cleanup_absence_proved") is not True \
                or seal.get("trials") != p2b.SIZES[split] \
                or seal.get("source_manifest_sha256") != sha(HERE / "SOURCES-SHA256.txt"):
            raise RuntimeError("Audited P2b seal changed or is incomplete")
        wanted = {f"{split}-{suffix}" for suffix in (
            "provision.json", "schedule.json", "server.json", "server-log-lines.txt", "cleanup.json", "native-before.json", "native-after.json")}
        if set(seal.get("evidence_sha256", {})) != wanted:
            raise RuntimeError("P2b seal omits required evidence")
        hashes = {**seal["evidence_sha256"], f"{split}.jsonl": seal["sha256"]}
        for name, expected in hashes.items():
            if sha(folder / name) != expected:
                raise RuntimeError("Audited P2b input changed")
            inputs[f"{arm}/{name}"] = expected
        inputs[f"{arm}/{split}-sealed.json"] = sha(seal_path)
    return inputs


def verify_freeze(data):
    check_sources()
    frozen = json.loads((HERE / "DECODERS-FROZEN.json").read_text())
    if frozen.get("sha256") != source_hashes() or set(frozen.get("decoders", {})) != set(ARMS):
        raise RuntimeError("P2b source changed or the decoder family is incomplete")
    if frozen.get("calibration_sha256") != check_split_audit(data, "calibration") \
            or any(d.get("calibration_trials") != 100 for d in frozen["decoders"].values()):
        raise RuntimeError("P2b frozen calibration changed")
    return frozen


def decode(dec, s):
    return int(s < dec["threshold_ms"]) if dec["active_is_below"] else int(s > dec["threshold_ms"])


def holdout(data):
    from scipy.stats import fisher_exact
    frozen = verify_freeze(data)
    check_split_audit(data, "holdout")
    result, ps = {}, []
    for arm in ARMS:
        path = data / arm / "holdout.jsonl"
        sealed = json.loads((data / arm / "holdout-sealed.json").read_text())
        if not sealed.get("complete") or sealed["sha256"] != sha(path):
            raise SystemExit(f"{arm}: holdout does not match its seal")
        trials = load(path)
        dec = frozen["decoders"][arm]
        y = [t["label"] for t in trials]
        s = [statistic(t) for t in trials]
        pred = [decode(dec, v) for v in s]
        correct = sum(int(a == b) for a, b in zip(pred, y))
        n = len(trials)
        acc = correct / n
        p = conditional_pvalue(trials, pred)
        ps.append(p)
        probes = [r["elapsed_ms"] for t in trials for r in t["post"] if str(r.get("probe", "")).startswith("post.timed.")]
        probes.sort()
        result[arm] = {"trials": n, "correct": correct, "accuracy": acc, "p_one_sided": p,
                       "median_S_active_ms": statistics.median([v for v, l in zip(s, y) if l == 1]),
                       "median_S_idle_ms": statistics.median([v for v, l in zip(s, y) if l == 0]),
                       "probe_latency_median_ms": probes[len(probes) // 2],
                       "probe_latency_p95_ms": probes[int(0.95 * (len(probes) - 1))],
                       "failed_trials": sum(1 for t in trials if t["failed_request_markers"])}
    for arm, q in zip(ARMS, holm(ps)):
        result[arm]["p_holm"] = q
    comparisons = {}
    a, b = result["equal"], result["distinct"]
    table = [[a["correct"], a["trials"] - a["correct"]],
             [b["correct"], b["trials"] - b["correct"]]]
    comparisons["equal_vs_distinct"] = {
        "fisher_p_two_sided": float(fisher_exact(table).pvalue),
        "accuracy_difference": a["accuracy"] - b["accuracy"]}
    out = {"arms": result, "comparisons": comparisons, "freeze_sha256": sha(HERE / "DECODERS-FROZEN.json"),
           "audit_sha256": sha(data / "audit-holdout.json"),
           "primary_family": "Exact balanced-sender-direction conditional assignment tails, four-arm Holm",
           "null_gate_passed": all(result[arm]["p_one_sided"] >= .05
                                   and result[arm]["p_holm"] >= .05
                                   for arm in ("aa-equal", "aa-distinct")),
           "scope": "Fixed-decoder content intervention on one shared host; no capacity or unique internal cause."}
    with (HERE / "results.json").open("x") as stream:
        json.dump(out, stream, indent=2)
    return out


def conditional_pvalue(trials, predictions):
    """Hypergeometric overlap probabilities, convolved across sender directions."""
    from scipy.stats import hypergeom
    distribution = {0: 1.0}
    for sender in ("alice", "bob"):
        group = [(row["label"], prediction)
                 for row, prediction in zip(trials, predictions) if row["sender"] == sender]
        n = len(group)
        positives = sum(label for label, _ in group)
        predicted = sum(prediction for _, prediction in group)
        if not n or positives * 2 != n:
            raise RuntimeError("Exact assignment test requires balanced sender-direction labels")
        support = {
            n - positives - predicted + 2 * overlap:
                float(hypergeom.pmf(overlap, n, positives, predicted))
            for overlap in range(max(0, positives - (n - predicted)),
                                 min(positives, predicted) + 1)}
        combined = {}
        for left, p_left in distribution.items():
            for right, p_right in support.items():
                combined[left + right] = combined.get(left + right, 0.0) + p_left * p_right
        distribution = combined
    if not math.isclose(sum(distribution.values()), 1.0, rel_tol=1e-12, abs_tol=1e-12):
        raise RuntimeError("Conditional assignment probabilities do not sum to one")
    observed = sum(row["label"] == prediction for row, prediction in zip(trials, predictions))
    return min(1.0, sum(p for correct, p in distribution.items() if correct >= observed))


if __name__ == "__main__":
    mode, data = sys.argv[1], Path(sys.argv[2])
    print(json.dumps(freeze(data) if mode == "freeze" else holdout(data), indent=2))
