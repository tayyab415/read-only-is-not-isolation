#!/usr/bin/env python3
"""P1 analysis. Runs on the Mac against data copied from the labhost.

    python3 analysis.py calibrate DATA_DIR OUT_DIR   # calibration only; fits and saves models
    python3 analysis.py holdout   DATA_DIR OUT_DIR   # refuses unless FROZEN.json matches

DATA_DIR holds one folder per run (open, combined, separate, aa, sham), each
with calibration.jsonl, holdout.jsonl, provision.json.

Features come only from what the receiver saw: status, latency, body hash and
length, every response header except Date, and fields of JSON bodies. Principal
names are normalised to SELF / PEER / OTHER. JSON numbers on probes that also
appear in the pre-snapshot are taken as post-minus-pre deltas. Family features
aggregate the 16 interchangeable artifact probes of each kind.

Categorical values become binaries: "differs from the most common value among
calibration label-0 trials". The meter is not told which feature, recipe or
mechanism matters.
"""
import hashlib
import json
import math
import pickle
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from scipy.stats import binomtest, fisher_exact, mannwhitneyu
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

HERE = Path(__file__).resolve().parent
RUNS = ["open", "combined", "separate", "aa", "sham"]
EXCLUDED_HEADERS = {"date"}
ALPHA = 0.05
PERMUTATIONS = 10000
SEED = 20261108


def load(path):
    return [json.loads(line) for line in path.open()] if path.exists() else []


def flatten(prefix, value, out):
    if isinstance(value, dict):
        for k, v in value.items():
            flatten(f"{prefix}.{k}", v, out)
    elif isinstance(value, list):
        out[f"{prefix}.len"] = float(len(value))
        names = sorted(str(item.get("uri", "")) for item in value if isinstance(item, dict))
        if names:
            out[f"{prefix}.uris"] = "|".join(names)
    elif isinstance(value, bool):
        out[prefix] = str(value)
    elif isinstance(value, (int, float)):
        out[prefix] = float(value)
    elif value is not None:
        out[prefix] = str(value)


def principal_class(value, self_name, peer_name):
    if not isinstance(value, str):
        return value
    if value == self_name:
        return "SELF"
    if value == peer_name:
        return "PEER"
    return value


def probe_fields(record, self_name, peer_name):
    fields = {"status": str(record.get("status")), "elapsed_ms": float(record.get("elapsed_ms", math.nan)),
              "body_len": float(record.get("body_length") or 0), "body_sha": record.get("body_sha256") or "none"}
    headers = defaultdict(list)
    for name, value in record.get("headers") or []:
        if name.lower() not in EXCLUDED_HEADERS:
            headers[name.lower()].append(value)
    for name, values in headers.items():
        fields[f"hdr.{name}"] = principal_class("|".join(values), self_name, peer_name)
    text = record.get("body_text")
    if text:
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if parsed is not None:
            flat = {}
            flatten("json", parsed, flat)
            for key, value in flat.items():
                fields[key] = principal_class(value, self_name, peer_name)
    return fields


def trial_features(trial, users):
    """Receiver-side features for one trial. Labels and recipes are never read here."""
    me, peer = users[trial["receiver"]], users[trial["sender"]]
    pre = {r["probe"]: probe_fields(r, me, peer) for r in trial["pre"]}
    feats = {}
    for record in trial["post"]:
        name = record["probe"]
        fields = probe_fields(record, me, peer)
        twin = pre.get(name.replace("post.", "pre."))
        for key, value in fields.items():
            if twin is not None and isinstance(value, float) and isinstance(twin.get(key), float) \
                    and key not in ("elapsed_ms", "body_len"):
                feats[f"{name}|{key}|delta"] = value - twin[key]
            elif twin is not None and isinstance(value, str) and key in twin:
                feats[f"{name}|{key}|changed"] = str(value != twin[key])
                feats[f"{name}|{key}"] = value
            else:
                feats[f"{name}|{key}"] = value
    # Family aggregates over the 16 interchangeable artifact probes of each kind.
    families = defaultdict(lambda: defaultdict(list))
    for key, value in feats.items():
        name, rest = key.split("|", 1)
        parts = name.split(".")
        if len(parts) == 3 and parts[2].isdigit():
            families[parts[1]][rest].append(value)
    for family, by_field in families.items():
        for rest, values in by_field.items():
            nums = [v for v in values if isinstance(v, float) and not math.isnan(v)]
            if rest.endswith("|delta") and nums:
                feats[f"fam.{family}|{rest}|nonzero"] = float(sum(v != 0 for v in nums))
            elif rest.endswith("elapsed_ms") and nums:
                feats[f"fam.{family}|elapsed_ms|max"] = max(nums)
                feats[f"fam.{family}|elapsed_ms|mean"] = sum(nums) / len(nums)
            elif all(isinstance(v, str) for v in values):
                feats[f"fam.{family}|{rest}|values"] = "|".join(sorted(Counter(values).keys()))
                if any(v == "PEER" for v in values):
                    feats[f"fam.{family}|{rest}|peer"] = float(sum(v == "PEER" for v in values))
    timed = [v for k, v in feats.items() if k.startswith("post.timed.") and k.endswith("|elapsed_ms")]
    if timed:
        feats["fam.timed|elapsed_ms|median"] = float(np.median(timed))
    return feats


class Encoder:
    """Fixed on calibration: column list, categorical reference values, numeric fill values."""

    def fit(self, feature_dicts, labels):
        keys = sorted({k for f in feature_dicts for k in f})
        self.numeric, self.categorical = [], {}
        idle = [f for f, y in zip(feature_dicts, labels) if y == 0]
        for key in keys:
            values = [f.get(key) for f in feature_dicts]
            if all(isinstance(v, float) or v is None for v in values):
                self.numeric.append(key)
            else:
                ref = Counter(str(f.get(key)) for f in idle).most_common(1)[0][0]
                self.categorical[key] = ref
        self.fill = {k: float(np.nanmedian([f.get(k, math.nan) for f in feature_dicts] or [0.0]))
                     for k in self.numeric}
        self.fill = {k: (0.0 if math.isnan(v) else v) for k, v in self.fill.items()}
        self.columns = self.numeric + [f"{k}|differs" for k in self.categorical]
        return self

    def transform(self, feature_dicts):
        rows = []
        for f in feature_dicts:
            row = []
            for k in self.numeric:
                v = f.get(k)
                row.append(self.fill[k] if v is None or (isinstance(v, float) and math.isnan(v)) else float(v))
            for k, ref in self.categorical.items():
                row.append(float(str(f.get(k)) != ref))
            rows.append(row)
        return np.array(rows, dtype=float)


def holm(pvalues):
    order = sorted(range(len(pvalues)), key=lambda i: pvalues[i])
    adjusted, running = [1.0] * len(pvalues), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(pvalues) - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


def feature_tests(X, y, columns, n_numeric, mask_pos=None):
    """Two-sample test per column: Mann-Whitney for numeric, Fisher for binary. Positives may be a subset."""
    pos = (y == 1) if mask_pos is None else mask_pos
    neg = y == 0
    results = []
    for j, name in enumerate(columns):
        a, b = X[pos, j], X[neg, j]
        if len(a) == 0 or np.all(X[pos | neg, j] == X[pos | neg, j][0]):
            continue
        if j < n_numeric:
            p = mannwhitneyu(a, b, alternative="two-sided").pvalue
            effect = float(np.median(a) - np.median(b))
        else:
            table = [[int(a.sum()), int(len(a) - a.sum())], [int(b.sum()), int(len(b) - b.sum())]]
            p = fisher_exact(table).pvalue
            effect = float(a.mean() - b.mean())
        results.append({"feature": name, "p": float(p), "effect": effect, "n_pos": int(len(a)), "n_neg": int(len(b))})
    adjusted = holm([r["p"] for r in results])
    for r, q in zip(results, adjusted):
        r["p_holm"] = q
    return sorted(results, key=lambda r: r["p"])


def choose_model(X, y):
    candidates = {
        "logistic": make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=5000)),
        "boosting": HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, random_state=SEED),
    }
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)
    scores = {name: float(cross_val_score(model, X, y, cv=cv, scoring="accuracy").mean())
              for name, model in candidates.items()}
    best = max(scores, key=scores.get)
    return best, candidates[best].fit(X, y), scores


def entropy2(p):
    return 0.0 if p <= 0 or p >= 1 else -p * math.log2(p) - (1 - p) * math.log2(1 - p)


def per_recipe(pred, y, recipes):
    idle = y == 0
    fpr = float(pred[idle].mean())
    out = {}
    for recipe in sorted(set(recipes[y == 1])):
        mask = (y == 1) & (recipes == recipe)
        tpr = float(pred[mask].mean())
        table = [[int(pred[mask].sum()), int(mask.sum() - pred[mask].sum())],
                 [int(pred[idle].sum()), int(idle.sum() - pred[idle].sum())]]
        p = fisher_exact(table, alternative="greater").pvalue
        balanced = (tpr + 1 - fpr) / 2
        out[recipe] = {"n_active": int(mask.sum()), "tpr": tpr, "fpr": fpr, "balanced_accuracy": balanced,
                       "p_one_sided": float(p),
                       "bits_per_trial_lower_bound": 1 - entropy2(1 - balanced) if balanced > 0.5 else 0.0}
    ps = [v["p_one_sided"] for v in out.values()]
    for (recipe, v), q in zip(out.items(), holm(ps)):
        v["p_holm"] = q
    return out


def prepare(run_dir, split):
    users = json.loads((run_dir / "provision.json").read_text())["users"]
    trials = load(run_dir / f"{split}.jsonl")
    feats = [trial_features(t, users) for t in trials]
    y = np.array([t["label"] for t in trials])
    recipes = np.array([t["recipe"] for t in trials])
    return trials, feats, y, recipes


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def calibrate(data, out):
    out.mkdir(parents=True, exist_ok=True)
    summary = {}
    for run in RUNS:
        run_dir = data / run
        if not (run_dir / "calibration.jsonl").exists():
            continue
        _, feats, y, recipes = prepare(run_dir, "calibration")
        enc = Encoder().fit(feats, y)
        X = enc.transform(feats)
        name, model, cv = choose_model(X, y)
        tests = feature_tests(X, y, enc.columns, len(enc.numeric))
        per_recipe_tests = {}
        for recipe in sorted(set(recipes[y == 1])):
            mask = (y == 1) & (recipes == recipe)
            hits = [r for r in feature_tests(X, y, enc.columns, len(enc.numeric), mask) if r["p_holm"] < ALPHA]
            per_recipe_tests[recipe] = hits[:20]
        with (out / f"{run}.model.pkl").open("wb") as stream:
            pickle.dump({"encoder": enc, "model": model, "model_name": name}, stream)
        selected = [r["feature"] for r in tests if r["p_holm"] < ALPHA]
        for hits in per_recipe_tests.values():
            selected += [r["feature"] for r in hits]
        summary[run] = {"trials": int(len(y)), "features": len(enc.columns), "cv_accuracy": cv, "model": name,
                        "holm_significant": [r for r in tests if r["p_holm"] < ALPHA][:40],
                        "per_recipe_holm_significant": per_recipe_tests,
                        "selected_for_confirmation": sorted(set(selected))}
    (out / "calibration-results.json").write_text(json.dumps(summary, indent=2))
    return summary


def holdout(data, out):
    frozen = json.loads((HERE / "FROZEN.json").read_text())
    for name, expected in frozen["sha256"].items():
        if sha(HERE / name) != expected:
            raise SystemExit(f"{name} changed after the freeze")
    calibration = json.loads((out / "calibration-results.json").read_text())
    rng = np.random.default_rng(SEED)
    summary = {}
    for run in RUNS:
        run_dir = data / run
        if not (run_dir / "holdout.jsonl").exists():
            continue
        sealed = json.loads((run_dir / "sealed.json").read_text())["files"]["holdout"]["sha256"]
        if sha(run_dir / "holdout.jsonl") != sealed:
            raise SystemExit(f"{run} holdout does not match its sealed hash")
        with (out / f"{run}.model.pkl").open("rb") as stream:
            frozen_model = pickle.load(stream)
        enc, model = frozen_model["encoder"], frozen_model["model"]
        _, feats, y, recipes = prepare(run_dir, "holdout")
        X = enc.transform(feats)
        pred = model.predict(X)
        accuracy = float((pred == y).mean())
        correct = int((pred == y).sum())
        perm = np.array([(pred == rng.permutation(y)).mean() for _ in range(PERMUTATIONS)])
        tests = feature_tests(X, y, enc.columns, len(enc.numeric))
        chosen = set(calibration[run]["selected_for_confirmation"])
        confirm = [r for r in tests if r["feature"] in chosen]
        for r, q in zip(confirm, holm([r["p"] for r in confirm])):
            r["p_holm_confirmatory"] = q
        summary[run] = {
            "trials": int(len(y)), "model": frozen_model["model_name"],
            "accuracy": accuracy, "correct": correct,
            "binomial_p_one_sided": float(binomtest(correct, len(y), 0.5, alternative="greater").pvalue),
            "permutation_p": float((np.sum(perm >= accuracy) + 1) / (PERMUTATIONS + 1)),
            "bits_per_trial_lower_bound": 1 - entropy2(1 - accuracy) if accuracy > 0.5 else 0.0,
            "per_recipe": per_recipe(pred, y, recipes),
            "confirmatory": confirm,
            "exploratory_holm_significant": [r for r in tests if r["p_holm"] < ALPHA][:40],
            "exploratory_feature_count": len(tests),
        }
    (out / "holdout-results.json").write_text(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    mode, data_dir, out_dir = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    result = calibrate(data_dir, out_dir) if mode == "calibrate" else holdout(data_dir, out_dir)
    print(json.dumps({run: {k: v for k, v in r.items() if k in
                            ("trials", "features", "cv_accuracy", "model", "accuracy", "permutation_p",
                             "bits_per_trial_lower_bound")} for run, r in result.items()}, indent=2))
