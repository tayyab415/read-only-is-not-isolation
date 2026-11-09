"""Inventory redaction-token formats and basic shape of Swarm Traces payload rows.

Usage: python 01_inventory.py data/swarm-traces.jsonl.gz results/01_inventory.json
"""
import collections
import gzip
import json
import re
import sys

src, out = sys.argv[1], sys.argv[2]

# Any bracketed run that starts with an upper-case word: [REDACTED:kind:001], [ENCODED BLOB 7], [SERVICE 3 URL 209]
TOKEN = re.compile(r"\[[A-Z][A-Z0-9_]*(?:[ :_-][A-Za-z0-9_.-]+)*\]")

kinds = collections.Counter()
token_types = collections.Counter()
rows_with_type = collections.Counter()
examples = {}
lengths = []
n_payload = 0
n_with_token = 0
first_chars = collections.Counter()

with gzip.open(src, "rt") as f:
    for line in f:
        r = json.loads(line)
        kinds[r["kind"]] += 1
        if r["kind"] != "payload":
            continue
        n_payload += 1
        text = r["text"] or ""
        lengths.append(len(text))
        first_chars[text[:12]] += 1
        found = TOKEN.findall(text)
        if found:
            n_with_token += 1
        seen = set()
        for tok in found:
            t = re.sub(r"\d+", "#", tok)
            token_types[t] += 1
            seen.add(t)
            examples.setdefault(t, tok)
        for t in seen:
            rows_with_type[t] += 1

lengths.sort()
q = lambda p: lengths[int(p * (len(lengths) - 1))]
res = {
    "kinds": kinds,
    "n_payload": n_payload,
    "n_payload_with_any_token": n_with_token,
    "length_quantiles": {p: q(p) for p in (0.01, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99)},
    "token_types_by_rows": rows_with_type.most_common(60),
    "token_types_total": token_types.most_common(60),
    "token_examples": examples,
    "top_prefixes": first_chars.most_common(25),
}
json.dump(res, open(out, "w"), indent=1, default=dict)
print(json.dumps({k: res[k] for k in ("kinds", "n_payload", "n_payload_with_any_token", "length_quantiles")}, default=dict))
for t, c in rows_with_type.most_common(40):
    print(f"{c:7d}  {t}")
print("--- top prefixes")
for p, c in first_chars.most_common(25):
    print(f"{c:7d}  {p!r}")
