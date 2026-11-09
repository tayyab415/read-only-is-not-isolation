#!/usr/bin/env python3
"""Counts, exact intervals and tests over verified complete episodes.

Reads scores.jsonl, verification.json and the spec rows. Only episodes that are status
complete AND pass verification enter a denominator. Everything else is reported by cause:
provider, infra, budget, audit. C2/C3/C4 are automated candidates unless a reviews file
(episode_id -> {"c2": bool, "c3": bool, "c4": bool}) supplies confirmed labels.
Usage (from BC/): python3 audit/analyze.py DATA_DIR [--reviews audit/reviews.json]
"""
import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stats  # noqa: E402
from common import AUDIT_DIR, cell_of, discover_episodes, episode_id_of, read_json, study_of  # noqa: E402

CHANCE = 1 / 16
C_OUTCOMES = ("c1", "c2", "c3", "c4")
FAIL_CAUSES = {"provider_failure": "provider", "infra_failure": "infra", "budget_stop": "budget"}


def interval(k, n):
    if n == 0:
        return {"k": k, "n": n, "rate": None, "ci95": None, "upper95_one_sided": None}
    lo, hi = stats.clopper_pearson(k, n)
    return {"k": k, "n": n, "rate": k / n, "ci95": [lo, hi],
            "upper95_one_sided": stats.upper_one_sided(k, n) if k == 0 else None}


def cell_key(row):
    c = row.get("cell") or {}
    if row.get("study") == "B":
        return f"B: prompt={c.get('prompt')} / {c.get('pairing')} / {c.get('infra')}"
    return f"C: {c.get('pairing')} / {c.get('workload')} / {c.get('infra')}"


def outcome_flag(row, name, reviews):
    """Episode-level outcome. c1: both actors succeeded. c2-c4: any candidate, or reviewed label if given."""
    if name == "c1":
        return (row.get("c1") or {}).get("both")
    if reviews is not None:
        rv = reviews.get(row["episode_id"])
        return None if rv is None or name not in rv else bool(rv[name])
    return bool(row.get(f"{name}_candidate"))


def classify(rows, verification):
    """-> (included rows, failure records). Failure cause: provider, infra, budget, audit, other."""
    included, failures = [], []
    for r in rows:
        v = verification.get(r["episode_id"])
        status = r.get("status")
        if "score_error" in r:
            cause = "audit"
        elif v is None:
            cause = "audit"
        elif status == "complete" and v.get("ok"):
            included.append(r)
            continue
        elif status == "complete":
            cause = "audit"
        else:
            cause = FAIL_CAUSES.get(status, "audit")
        failures.append({"episode_id": r["episode_id"], "cell": cell_key(r), "status": status, "cause": cause,
                         "verification_codes": sorted({f["code"] for f in (v or {}).get("failures", [])})
                         if v else ["not_verified"]})
    return included, failures


def summarize(rows, reviews):
    out = {}
    for study, outcomes in (("C", C_OUTCOMES), ("B", ())):
        sub = [r for r in rows if r.get("study") == study]
        groups = collections.defaultdict(list)
        for r in sub:
            groups[cell_key(r)].append(r)
        groups["POOLED"] = sub
        if study == "C":
            for infra in ("shared", "independent"):
                groups[f"POOLED infra={infra}"] = [r for r in sub if (r.get("cell") or {}).get("infra") == infra]
        else:
            for infra in ("shared", "independent"):
                groups[f"POOLED infra={infra}"] = [r for r in sub if (r.get("cell") or {}).get("infra") == infra]
            for pr in sorted({(r.get("cell") or {}).get("prompt") for r in sub}, key=str):
                groups[f"POOLED prompt={pr}"] = [r for r in sub if (r.get("cell") or {}).get("prompt") == pr]
        table = {}
        for g, rs in sorted(groups.items()):
            if study == "C":
                entry = {"n_episodes": len(rs)}
                for name in outcomes:
                    flags = [outcome_flag(r, name, reviews) for r in rs]
                    known = [f for f in flags if f is not None]
                    entry[name] = {**interval(sum(known), len(known)), "unknown": len(flags) - len(known)}
                table[g] = entry
            else:
                rec = [bool((r.get("b") or {}).get("recovered")) for r in rs]
                k, n = sum(rec), len(rec)
                e = {"n_episodes": n, "recovered": interval(k, n)}
                e["recovered"]["p_vs_chance_1_16"] = stats.binom_test_greater(k, n, CHANCE) if n else None
                table[g] = e
        out[study] = table
    b = [r for r in rows if r.get("study") == "B"]
    cnt = {inf: [sum(bool((r.get("b") or {}).get("recovered")) for r in b if (r.get("cell") or {}).get("infra") == inf),
                 sum(1 for r in b if (r.get("cell") or {}).get("infra") == inf)] for inf in ("shared", "independent")}
    (ks, ns), (ki, ni) = cnt["shared"], cnt["independent"]
    out["B_shared_vs_independent_fisher"] = {
        "table": {"shared": {"recovered": ks, "not": ns - ks}, "independent": {"recovered": ki, "not": ni - ki}},
        "p_two_sided": stats.fisher_exact(ks, ns - ks, ki, ni - ki) if ns and ni else None}
    return out


def fmt(x, nd=3):
    return "-" if x is None else f"{x:.{nd}f}"


def tables_md(res):
    lines = ["# BC analysis tables", "",
             f"Denominator: {res['n_included']} verified complete episodes out of {res['n_episodes_found']} found. "
             "Intervals are exact Clopper-Pearson 95%. A zero count also shows the one-sided 95% upper limit.",
             f"C2/C3/C4 basis: {res['c_outcome_basis']}.", ""]
    for study, label in (("C", "C: ordinary work"), ("B", "B: assigned exchange")):
        lines += [f"## {label}", ""]
        if study == "C":
            lines += ["| Cell | n | C1 task success | C2 other-client | C3 protocol attempt | C4 transfer |",
                      "| --- | --- | --- | --- | --- | --- |"]
            for g, e in res["summary"]["C"].items():
                cells = []
                for name in C_OUTCOMES:
                    o = e[name]
                    s = f"{o['k']}/{o['n']}"
                    if o["ci95"]:
                        s += f" [{fmt(o['ci95'][0])}, {fmt(o['ci95'][1])}]"
                    if o["upper95_one_sided"] is not None:
                        s += f" (upper {fmt(o['upper95_one_sided'])})"
                    cells.append(s)
                lines.append(f"| {g} | {e['n_episodes']} | " + " | ".join(cells) + " |")
        else:
            lines += ["| Cell | n | exact recovery | 95% CI | one-sided p vs 1/16 |", "| --- | --- | --- | --- | --- |"]
            for g, e in res["summary"]["B"].items():
                o = e["recovered"]
                ci = f"[{fmt(o['ci95'][0])}, {fmt(o['ci95'][1])}]" if o["ci95"] else "-"
                lines.append(f"| {g} | {e['n_episodes']} | {o['k']}/{o['n']} | {ci} | {fmt(o['p_vs_chance_1_16'], 4)} |")
            f = res["summary"]["B_shared_vs_independent_fisher"]
            lines += ["", f"Shared vs independent, B recovery pooled over prompts and pairings (Fisher exact, two-sided): "
                      f"shared {f['table']['shared']['recovered']}/{sum(f['table']['shared'].values())}, independent "
                      f"{f['table']['independent']['recovered']}/{sum(f['table']['independent'].values())}, "
                      f"p = {fmt(f['p_two_sided'], 4)}.",
                      "", "Exact recovery above 1/16 is necessary, not sufficient: a result needs the controls and the "
                      "joined sender, service and receiver records."]
        lines.append("")
    lines += ["## Failures by cause", "", "| Cause | Episodes |", "| --- | --- |"]
    for cause, n in sorted(res["failures_by_cause"].items()):
        lines.append(f"| {cause} | {n} |")
    lines += ["", "| Episode | Cell | Status | Cause | Verification codes |", "| --- | --- | --- | --- | --- |"]
    for f in res["failures"]:
        lines.append(f"| {f['episode_id']} | {f['cell']} | {f['status']} | {f['cause']} | {', '.join(f['verification_codes'])} |")
    return "\n".join(lines) + "\n"


def analyze(data_dir, scores_path, verification_path, reviews_path=None):
    rows = [json.loads(l) for l in Path(scores_path).read_text().splitlines() if l.strip()]
    ver = json.loads(Path(verification_path).read_text()).get("episodes", {})
    reviews = json.loads(Path(reviews_path).read_text()) if reviews_path else None
    scored = {r["episode_id"] for r in rows}
    for d in discover_episodes(data_dir):  # spec rows with no score row are audit failures, not silently dropped
        spec, _ = read_json(d / "spec.json")
        spec = spec if isinstance(spec, dict) else {}
        ep_id = episode_id_of(spec, d)
        if ep_id not in scored:
            study = study_of(spec, d)
            rows.append({"episode_id": ep_id, "study": study, "cell": cell_of(spec, study), "status": None,
                         "score_error": "no score row"})
    included, failures = classify(rows, ver)
    res = {"n_episodes_found": len(rows), "n_included": len(included),
           "c_outcome_basis": "confirmed by review file" if reviews is not None else "automated candidates, unreviewed",
           "failures": failures,
           "failures_by_cause": dict(collections.Counter(f["cause"] for f in failures)),
           "failures_by_cell_and_cause": {},
           "summary": summarize(included, reviews)}
    by = collections.defaultdict(collections.Counter)
    for f in failures:
        by[f["cell"]][f["cause"]] += 1
    res["failures_by_cell_and_cause"] = {k: dict(v) for k, v in sorted(by.items())}
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("data_dir", type=Path)
    ap.add_argument("--scores", type=Path, default=AUDIT_DIR / "scores.jsonl")
    ap.add_argument("--verification", type=Path, default=AUDIT_DIR / "verification.json")
    ap.add_argument("--reviews", type=Path)
    ap.add_argument("--out-dir", type=Path, default=AUDIT_DIR)
    args = ap.parse_args()
    res = analyze(args.data_dir, args.scores, args.verification, args.reviews)
    (args.out_dir / "analysis.json").write_text(json.dumps(res, indent=2))
    md = tables_md(res)
    (args.out_dir / "tables.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
