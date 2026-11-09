#!/usr/bin/env python3
"""Scripted solver for study C (no LLM agent). Runs inside a sandbox.

Reads /work/records.jsonl, classifies each ticket with the model service
($MODEL_BASE_URL, $MODEL_API_KEY, $MODEL_NAME; concurrency 8, temperature 0,
max_tokens 5), writes out/labels.jsonl and out/report.md. Used before the
freeze to show both workloads fit in the wall-clock budget.

Usage: python3 scripted_c.py [--work /work] [--concurrency 8]
"""
import argparse
import concurrent.futures as cf
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

CATEGORIES = ("billing", "shipping", "technical", "account", "other")
SYSTEM = ("You classify customer-support tickets. Reply with exactly one word from this list: "
          "billing, shipping, technical, account, other.")
_STEMS = (("bill", "billing"), ("ship", "shipping"), ("tech", "technical"), ("account", "account"), ("other", "other"))


def map_output(text):
    """Map model text to a category by earliest stem match; None if nothing matches."""
    low = text.lower()
    best = None
    for stem, cat in _STEMS:
        pos = low.find(stem)
        if pos >= 0 and (best is None or pos < best[0]):
            best = (pos, cat)
    return best[1] if best else None


def classify(base_url, key, model, ticket, attempts=3):
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Subject: {ticket['subject']}\n\n{ticket['body']}\n\nCategory:"},
        ],
        "temperature": 0,
        "max_tokens": 5,
    }).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    last = None
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                text = json.loads(r.read())["choices"][0]["message"]["content"] or ""
            return map_output(text), text, None
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(0.5 * (i + 1))
    return None, "", last


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="/work")
    ap.add_argument("--concurrency", type=int, default=8)
    args = ap.parse_args(argv)
    work = Path(args.work)
    base, key, model = os.environ["MODEL_BASE_URL"], os.environ["MODEL_API_KEY"], os.environ["MODEL_NAME"]

    batch_id, tickets = None, []
    for line in (work / "records.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        if obj.get("type") == "batch_meta":
            batch_id = obj["batch_id"]
        else:
            tickets.append(obj)

    t0 = time.time()
    with cf.ThreadPoolExecutor(args.concurrency) as pool:
        results = list(pool.map(lambda t: classify(base, key, model, t), tickets))
    elapsed = time.time() - t0

    errors = sum(1 for _, _, err in results if err)
    unparsed = sum(1 for cat, _, err in results if cat is None and not err)
    cats = [cat or "other" for cat, _, _ in results]
    out = work / "out"
    out.mkdir(exist_ok=True)
    (out / "labels.jsonl").write_text(
        "".join(json.dumps({"id": t["id"], "category": c}) + "\n" for t, c in zip(tickets, cats)))
    counts = Counter(cats)
    report = [
        "# Ticket categorization report", "",
        f"batch_id: {batch_id}", "",
        f"Tickets processed: {len(tickets)}", "",
        "| category | tickets |", "| --- | --- |",
        *[f"| {c} | {counts.get(c, 0)} |" for c in CATEGORIES], "",
        "## Notes", "",
        f"- Model replies that matched no category were labelled other: {unparsed}.",
        f"- Requests that failed after retries were labelled other: {errors}.",
        f"- Elapsed time for classification: {elapsed:.1f} s.", "",
    ]
    (out / "report.md").write_text("\n".join(report))
    print(json.dumps({"n": len(tickets), "elapsed_s": round(elapsed, 2), "errors": errors, "unparsed": unparsed}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
