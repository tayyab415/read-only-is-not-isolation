#!/usr/bin/env python3
"""Persistent, scheduled HTTP worker for an isolated experimental client.

Configuration and credentials arrive once on stdin. Each RPC contains only
fixed requests and offsets. Labels and sender recipes never reach a receiver.
"""
from concurrent.futures import ThreadPoolExecutor
import base64
import hashlib
import http.client
import json
import math
import sys
import time
from urllib.parse import unquote, urlsplit

REQUEST_HEADERS = {"if-none-match", "if-modified-since", "range"}
MAX_REQUESTS, MAX_WORKERS = 128, 8
MAX_OFFSET_S = 3.0


def validate_jobs(jobs):
    if not isinstance(jobs, list) or len(jobs) > MAX_REQUESTS:
        raise ValueError("request count exceeds fixed limit")
    for request in jobs:
        if not isinstance(request, dict):
            raise ValueError("invalid request object")
        if request.get("method") not in ("GET", "HEAD"):
            raise ValueError("unsupported method")
        path = request.get("path", "")
        parsed = urlsplit(path)
        decoded = unquote(parsed.path)
        if not path.startswith("/") or parsed.scheme or parsed.netloc or parsed.fragment \
                or any(character in path for character in "\r\n") \
                or any(character in decoded for character in "\r\n\\") \
                or ".." in decoded.split("/"):
            raise ValueError("invalid fixture path")
        offset = request.get("offset_s", 0)
        if not isinstance(offset, (float, int)) or isinstance(offset, bool) \
                or not math.isfinite(offset) or not 0 <= offset <= MAX_OFFSET_S:
            raise ValueError("offset exceeds the fixed window")
        headers = request.get("headers") or {}
        if not isinstance(headers, dict) or any(not isinstance(name, str)
                or name.lower() not in REQUEST_HEADERS for name in headers):
            raise ValueError("header outside the experimental grammar")
        if len({name.lower() for name in headers}) != len(headers):
            raise ValueError("duplicate experimental header")
        if any(not isinstance(value, str) or len(value) > 1024 or "\r" in value or "\n" in value
               for value in headers.values()):
            raise ValueError("invalid experimental header")
        marker = request.get("marker")
        if not isinstance(marker, str) or len(marker) > 256 or "\r" in marker or "\n" in marker:
            raise ValueError("invalid trusted marker")


def run_jobs(config, jobs, authorization, executor, epoch_utc_ns=None, execute_http=1):
    validate_jobs(jobs)
    now_utc, now_mono = time.time_ns(), time.monotonic_ns()
    if epoch_utc_ns is None:
        epoch_utc_ns = now_utc
    if not isinstance(epoch_utc_ns, int) or isinstance(epoch_utc_ns, bool) \
            or not -1_000_000_000 <= epoch_utc_ns - now_utc <= 2_000_000_000:
        raise ValueError("invalid or stale common schedule epoch")
    epoch = now_mono + epoch_utc_ns - now_utc
    if not isinstance(execute_http, int) or isinstance(execute_http, bool) or execute_http not in (0, 1):
        raise ValueError("invalid sender execution flag")
    if execute_http == 0:
        remaining = (epoch + 2_000_000_000 - time.monotonic_ns()) / 1e9
        if remaining > 0:
            time.sleep(remaining)
        return []

    def request(job):
        deadline = epoch + int(job.get("offset_s", 0) * 1e9)
        remaining = (deadline - time.monotonic_ns()) / 1e9
        if remaining > 0:
            time.sleep(remaining)
        started_mono = time.monotonic_ns()
        started_utc = time.time_ns()
        request_headers = dict(job.get("headers") or {})
        wire_headers = {"Authorization": authorization, "User-Agent": job["marker"], **request_headers}
        connection = http.client.HTTPConnection(config["host"], config["port"], timeout=30)
        record = {"method": job["method"], "path": job["path"], "marker": job["marker"],
                  "probe": job.get("probe"), "request_headers": request_headers,
                  "scheduled_offset_s": job.get("offset_s", 0),
                  "schedule_epoch_utc_ns": epoch_utc_ns,
                  "schedule_setup_utc_ns": now_utc,
                  "start_lag_ms": max(0, (started_mono - deadline) / 1e6),
                  "start_utc_ns": started_utc}
        try:
            connection.request(job["method"], job["path"], headers=wire_headers)
            response = connection.getresponse()
            body = response.read()
            headers = response.getheaders()
            content_type = response.getheader("Content-Type") or ""
            record.update(status=response.status, headers=headers, body_length=len(body),
                          body_sha256=hashlib.sha256(body).hexdigest(),
                          body_text=body.decode("utf-8", "replace") if len(body) <= 65536
                          and any(kind in content_type for kind in ["json", "html", "text", "xml"]) else None)
        except Exception as error:
            record.update(status=None, headers=[], body_length=0, body_sha256=None,
                          body_text=None, error=type(error).__name__)
        finally:
            record.update(end_utc_ns=time.time_ns(),
                          elapsed_ms=(time.monotonic_ns() - started_mono) / 1e6)
            connection.close()
        return record

    return list(executor.map(request, jobs))


def main():
    config = json.loads(sys.stdin.readline())
    authorization = "Basic " + base64.b64encode(
        (config.pop("user") + ":" + config.pop("password")).encode()).decode()
    workers = config.pop("workers", MAX_WORKERS)
    if not isinstance(workers, int) or not 1 <= workers <= MAX_WORKERS:
        raise ValueError("worker count exceeds fixed limit")
    with ThreadPoolExecutor(max_workers=workers) as executor:
        print(json.dumps({"ready": True, "workers": workers}), flush=True)
        for line in sys.stdin:
            message = json.loads(line)
            if message["op"] == "exit":
                return
            if message["op"] != "schedule":
                raise ValueError("unsupported RPC")
            print(json.dumps(run_jobs(config, message["jobs"], authorization, executor,
                                      message.get("epoch_utc_ns"), message.get("execute_http", 1))), flush=True)


if __name__ == "__main__":
    main()
