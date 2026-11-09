"""Curate Swarm Traces payload rows into agent-action positives.

Keep a payload row when, after removing reconstruction headers and redaction tokens, it still holds
at least MIN_RESIDUAL characters of agent-written text and at least one code marker.
Drops chunked-transport fragments such as  x+='[ENCODED BLOB 1]';n('[REDACTED:...]')  that carry
no content once redacted.

Usage: python 02_build_positives.py data/swarm-traces.jsonl.gz data/positives.parquet results/02_positives.json
"""
import collections
import gzip
import json
import re
import sys

import pandas as pd

from canon import PLACEHOLDER, detok, guess_lang, struct
from cluster import clusters

src, out_parquet, out_json = sys.argv[1:4]
MIN_RESIDUAL = 40
MIN_STRUCT_TOKENS = 12
CODE_MARKER = re.compile(
    r"fetch\s*\(|XMLHttpRequest|document\.|new Image|^\s*import |subprocess|eval\s*\(|atob\s*\(|btoa\s*\("
    r"|<script|<iframe|<img|\.src\s*=|location\.|window\.|os\.system|\bcurl |\bwget |requests\.|urllib"
    r"|WebSocket|\bawait\b|=>|function\s*\w*\s*\(|navigator\.|localStorage|\.innerHTML",
    re.M,
)

rows, reasons = [], collections.Counter()
with gzip.open(src, "rt") as f:
    for line in f:
        r = json.loads(line)
        if r["kind"] != "payload":
            continue
        raw = r["text"] or ""
        d = detok(raw)
        residual = re.sub(r"\s+", "", d.replace(PLACEHOLDER, ""))
        if len(residual) < MIN_RESIDUAL:
            reasons["residual_too_short"] += 1
            continue
        if not CODE_MARKER.search(d):
            reasons["no_code_marker"] += 1
            continue
        lang = guess_lang(d)
        s = struct(raw, lang)
        if len(s.split()) < MIN_STRUCT_TOKENS:
            reasons["struct_too_short"] += 1
            continue
        reasons["kept"] += 1
        rows.append({"id": r["id"], "cite": r["cite"], "lang": lang, "raw": raw, "detok": d, "struct": s})

df = pd.DataFrame(rows)
df["label"] = 1
df["source"] = "swarm_traces"
df["cluster"] = clusters(df["struct"].tolist(), "swarm:")
df.to_parquet(out_parquet, index=False)

sizes = df["cluster"].value_counts()
summary = {
    "payload_rows": sum(reasons.values()),
    "reasons": reasons,
    "kept_by_lang": df["lang"].value_counts().to_dict(),
    "distinct_raw": int(df["raw"].nunique()),
    "distinct_detok": int(df["detok"].nunique()),
    "distinct_struct": int(df["struct"].nunique()),
    "clusters": int(len(sizes)),
    "largest_clusters": sizes.head(10).to_dict(),
    "singleton_clusters": int((sizes == 1).sum()),
    "raw_len_quantiles": df["raw"].str.len().quantile([0.1, 0.5, 0.9]).to_dict(),
}
json.dump(summary, open(out_json, "w"), indent=1, default=dict)
print(json.dumps(summary, indent=1, default=dict))
