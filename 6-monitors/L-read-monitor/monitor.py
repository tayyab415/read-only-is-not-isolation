#!/usr/bin/env python3
"""Offline associations for read effects; completed server records only."""
from collections import defaultdict
from urllib.parse import parse_qs, urlsplit


class ReadMonitor:
    def __init__(self, window=600, fast_miss_threshold_s=0.08, repo_aliases=None):
        self.window = window
        self.fast_miss_threshold_s = fast_miss_threshold_s
        # Explicit trusted repository topology; never infer aliases from labels.
        self.repo_aliases = dict(repo_aliases or {})
        self.downloads = defaultdict(list)
        self.misses = defaultdict(list)
        self.last_completion = float("-inf")

    def process(self, event):
        allowed = {"event_id", "principal", "method", "path", "start", "duration", "status", "bytes"}
        if set(event)-allowed:
            raise ValueError("Only projected server fields are accepted")
        completion = event["start"]+event["duration"]
        if completion < self.last_completion:
            raise ValueError("Process completed records in completion order")
        self.last_completion = completion
        parsed = urlsplit(event["path"])
        path = parsed.path.removeprefix("/artifactory/")
        metadata = path.startswith("api/storage/")
        if metadata:
            target = path.removeprefix("api/storage/")
        elif path.startswith(("api/", "ui/")):
            return []
        else:
            target = path
        repo, _, artifact = target.partition("/")
        if not repo or not artifact:
            return []
        repo = self.repo_aliases.get(repo, repo)
        stats = metadata and "stats" in parse_qs(parsed.query, keep_blank_values=True)
        alerts = []

        def emit(rule, **extra):
            alerts.append({"event_id": event["event_id"], "rule": rule, "repo": repo,
                           "artifact": artifact, "principal": event["principal"],
                           "available_at_completion": completion,
                           "communication_verified": False, **extra})

        def prior(collection):
            return [old for old in collection[(repo, artifact)]
                    if old["completion"] <= event["start"]
                    and event["start"]-old["completion"] <= self.window
                    and old["principal"] and event["principal"]
                    and old["principal"] != event["principal"]]

        success = 200 <= event["status"] < 300
        if event["method"] == "GET" and stats:
            emit("statistics_observation", outcome="success" if success else "denied_or_failed")
            if success:
                for old in prior(self.downloads):
                    emit("possible_download_statistics_exposure", producer_event_id=old["event_id"])
        if event["method"] in ("GET", "HEAD") and not metadata:
            if event["status"] == 404 and event["duration"] < self.fast_miss_threshold_s:
                emit("fast_failed_artifact_request", duration_s=event["duration"],
                     threshold_s=self.fast_miss_threshold_s)
            for old in prior(self.misses):
                emit("possible_shared_miss_state", producer_event_id=old["event_id"],
                     current_status=event["status"],
                     state_change_verified=False)
            if event["status"] in (403, 404):
                self.misses[(repo, artifact)].append({
                    "completion": completion, "principal": event["principal"], "event_id": event["event_id"]})
            # Successful artifact GET may change stats; HEAD is deliberately not assumed to do so.
            if event["method"] == "GET" and success:
                self.downloads[(repo, artifact)].append({
                    "completion": completion, "principal": event["principal"], "event_id": event["event_id"]})
        return alerts
