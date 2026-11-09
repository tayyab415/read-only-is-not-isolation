#!/usr/bin/env python3
import concurrent.futures
import json
import statistics
import time
from common import OUT, ok, req

root = "/github-remote-cache/zz_A_bulk"
ok(req("bulk-parent", "MKCOL", root, "a-cache"))
start = time.perf_counter()
def create(i):
    r = req(f"bulk-create-{i:04}", "MKCOL", root + f"/entry_{i:04}", "a-cache")
    return r.status_code if r is not None else 0
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    statuses = list(pool.map(create, range(3000)))
result = {"folder_count_attempted": 3000, "workers": 4,
          "creation_wall_s": time.perf_counter() - start,
          "statuses": {str(s): statuses.count(s) for s in set(statuses)},
          "listings": []}
for endpoint, method, path, headers, body in [
    ("html", "GET", root + "/", {}, None),
    ("native-html", "GET", "/list" + root + "/", {}, None),
    ("folder-info", "GET", "/api/storage" + root, {}, None),
    ("file-list", "GET", "/api/storage" + root + "?list&deep=0&listFolders=1", {}, None),
    ("webdav", "PROPFIND", root + "/", {"Depth": "1", "Content-Type": "application/xml"},
     '<D:propfind xmlns:D="DAV:"><D:allprop/></D:propfind>'),
]:
    for i in range(3):
        label = f"bulk-list-{endpoint}-{i}"
        r = req(label, method, path, "a-read", headers=headers, data=body)
        if r is not None:
            result["listings"].append({"id": label, "endpoint": endpoint,
                "status": r.status_code, "response_bytes": len(r.content),
                "time_to_headers_s": r.elapsed.total_seconds()})
(OUT / "bulk-summary.json").write_text(json.dumps(result, indent=2))
print("BULK DONE", result["statuses"], result["creation_wall_s"], flush=True)
