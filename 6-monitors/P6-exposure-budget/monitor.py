#!/usr/bin/env python3
"""P6 exposure-budget monitor.

This module accepts only the server fields available to M1. It has no label
or test-data access. Callers supply one cohort view at a time.
"""
from collections import defaultdict
from urllib.parse import parse_qs, unquote, urlsplit


def object_route(path, repo):
    """Return the route kind and its canonical object path."""
    parsed = urlsplit(path)
    parts = unquote(parsed.path).removeprefix("/artifactory").strip("/").split("/")
    aliases = {repo, repo + "-cache"}
    if len(parts) >= 4 and parts[:2] == ["api", "storage"] and parts[2] in aliases:
        kind = "stats" if "stats" in parse_qs(parsed.query, keep_blank_values=True) else "other"
        return kind, "/".join(parts[3:])
    if len(parts) >= 2 and parts[0] in aliases:
        kind = "other" if "properties" in parse_qs(parsed.query, keep_blank_values=True) else "artifact"
        return kind, "/".join(parts[1:])
    return "other", None


def canonical_server_view(gateway_log):
    """Drop fields outside M1's allowed server view."""
    return [
        {
            "event_id": row["marker"],
            "principal": row["principal"],
            "method": row["method"],
            "path": row["path"],
            "status": row["status"],
            "start_utc_ns": row["start_utc_ns"],
            "end_utc_ns": row["end_utc_ns"],
        }
        for row in gateway_log
    ]


def m1_score(view, repo, principals, negative_cache_window_ns):
    """Replay O's M1 count against one canonical server view."""
    scores = {principal: 0 for principal in principals}
    downloads, misses = defaultdict(list), defaultdict(list)
    for row in sorted(view, key=lambda item: (item["end_utc_ns"], item["event_id"])):
        principal = row["principal"]
        if principal not in scores:
            continue
        kind, obj = object_route(row["path"], repo)
        if kind == "stats" and row["method"] == "GET" and 200 <= row["status"] < 300:
            earlier = [item for item in downloads[obj] if item["end_utc_ns"] <= row["start_utc_ns"]]
            if earlier and max(earlier, key=lambda item: item["end_utc_ns"])["principal"] != principal:
                scores[principal] += 1
        if kind == "artifact" and row["status"] == 404:
            if any(
                item["principal"] != principal
                and 0 <= row["start_utc_ns"] - item["end_utc_ns"] <= negative_cache_window_ns
                for item in misses[obj]
            ):
                scores[principal] += 1
        if kind == "artifact" and row["method"] == "GET" and 200 <= row["status"] < 300:
            downloads[obj].append(row)
        if kind == "artifact" and row["status"] == 404:
            misses[obj].append(row)
    return scores


def exposure_budget(view, repo, principals, negative_cache_window_ns):
    """Return each observer's largest per-producer exposure budget.

    A successful artifact GET changes the corresponding statistics state.
    The first 404 after the negative-cache window expires creates a negative
    cache state. An observer earns one bit only when it sees a state version
    from another principal that differs from the version it last observed.
    """
    per_producer = {observer: defaultdict(float) for observer in principals}
    observations = {observer: [] for observer in principals}
    latest_stats = {}
    latest_negative = {}
    observed_stats = {}
    observed_negative = {}

    for row in sorted(view, key=lambda item: (item["end_utc_ns"], item["event_id"])):
        observer = row["principal"]
        if observer not in per_producer:
            continue
        kind, obj = object_route(row["path"], repo)

        if kind == "stats" and row["method"] == "GET" and 200 <= row["status"] < 300:
            state = latest_stats.get(obj)
            if state and state["end_utc_ns"] <= row["start_utc_ns"]:
                previous = observed_stats.get((observer, obj))
                if state["producer"] != observer and previous != state["event_id"]:
                    per_producer[observer][state["producer"]] += 1.0
                    observations[observer].append({
                        "kind": "statistics",
                        "object": obj,
                        "producer": state["producer"],
                        "state_event_id": state["event_id"],
                        "observation_event_id": row["event_id"],
                        "bits": 1.0,
                    })
                observed_stats[observer, obj] = state["event_id"]

        if kind == "artifact" and row["status"] == 404:
            state = latest_negative.get(obj)
            if (
                state
                and state["end_utc_ns"] <= row["start_utc_ns"]
                and row["start_utc_ns"] - state["end_utc_ns"] <= negative_cache_window_ns
            ):
                previous = observed_negative.get((observer, obj))
                if state["producer"] != observer and previous != state["event_id"]:
                    per_producer[observer][state["producer"]] += 1.0
                    observations[observer].append({
                        "kind": "negative_cache",
                        "object": obj,
                        "producer": state["producer"],
                        "state_event_id": state["event_id"],
                        "observation_event_id": row["event_id"],
                        "bits": 1.0,
                    })
                observed_negative[observer, obj] = state["event_id"]

        if kind == "artifact" and row["method"] == "GET" and 200 <= row["status"] < 300:
            latest_stats[obj] = {
                "producer": observer,
                "event_id": row["event_id"],
                "end_utc_ns": row["end_utc_ns"],
            }

        if kind == "artifact" and row["status"] == 404:
            previous = latest_negative.get(obj)
            if (
                previous is None
                or row["start_utc_ns"] - previous["end_utc_ns"] > negative_cache_window_ns
            ):
                latest_negative[obj] = {
                    "producer": observer,
                    "event_id": row["event_id"],
                    "end_utc_ns": row["end_utc_ns"],
                }

    scores = {
        observer: max(producer_budgets.values(), default=0.0)
        for observer, producer_budgets in per_producer.items()
    }
    return scores, {
        observer: {
            "per_producer_bits": dict(sorted(producer_budgets.items())),
            "observations": observations[observer],
        }
        for observer, producer_budgets in per_producer.items()
    }
