#!/usr/bin/env python3
"""S1 utility workload: what each cache policy costs ordinary agent traffic.

Two tenants x 4 agents x 12 requests. Every request = a 600-word (about 1,500-token) scaffold system
prompt plus a fresh 40-word user turn, 32 output tokens, one thread per agent.
  distinct: each tenant has its own scaffold (cross-tenant reuse impossible anyway)
  common:   both tenants use the same scaffold (the case where salting gives up reuse)
Cached-token totals come from the trusted gateway log, joined on x-trial-id.
Usage: utility.py --phase cache_on|cache_off
"""
import argparse, json, random, threading, time
from pathlib import Path
import httpx
from trials import DATA, KEY_OF, MODEL, words

AGENTS, REQUESTS, SCAFFOLD_WORDS, TURN_WORDS = 4, 12, 600, 40
CONFIGS = {"cache_on": {"shared": 9001, "salted": 9002}, "cache_off": {"nocache": 9001}}
REPS = 3


def agent_run(port, tenant, scaffold, tag, agent, out, lock):
    c = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=120)
    for j in range(REQUESTS):
        body = {"model": MODEL, "max_tokens": 32, "temperature": 0, "ignore_eos": True,
                "messages": [{"role": "system", "content": scaffold},
                             {"role": "user", "content": words(f"{tag}-{tenant}-{agent}-{j}", TURN_WORDS)}]}
        t0 = time.perf_counter_ns()
        r = c.post("/v1/chat/completions", json=body, headers={
            "authorization": f"Bearer {KEY_OF[tenant]}", "x-trial-id": f"{tag}", "x-role": "utility"})
        t1 = time.perf_counter_ns()
        with lock:
            out.append({"tenant": tenant, "agent": agent, "j": j, "status": r.status_code, "ms": (t1 - t0) / 1e6,
                        "completion_tokens": (r.json().get("usage") or {}).get("completion_tokens")})


def main(phase):
    rows, lock = [], threading.Lock()
    order = [(cfg, wl, rep) for cfg in CONFIGS[phase] for wl in ("distinct", "common") for rep in range(REPS)]
    random.Random(f"S1-utility-{phase}").shuffle(order)
    for cfg, wl, rep in order:
        port = CONFIGS[phase][cfg]
        tag = f"util-{phase}-{cfg}-{wl}-{rep}"
        common = words(f"{tag}-scaffold", SCAFFOLD_WORDS)
        scaff = {t: (common if wl == "common" else words(f"{tag}-scaffold-{t}", SCAFFOLD_WORDS))
                 for t in ("tenant-a", "tenant-b")}
        out = []
        threads = [threading.Thread(target=agent_run, args=(port, t, scaff[t], tag, a, out, lock))
                   for t in ("tenant-a", "tenant-b") for a in range(AGENTS)]
        t0 = time.perf_counter()
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        wall = time.perf_counter() - t0
        rows.append({"phase": phase, "config": cfg, "workload": wl, "rep": rep, "tag": tag, "wall_s": wall,
                     "requests": out})
        print(tag, f"{wall:.2f}s", sum(r["status"] == 200 for r in out), "ok", flush=True)
        time.sleep(1)
    (DATA / f"utility-{phase}.json").write_text(json.dumps(rows))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", required=True, choices=list(CONFIGS))
    main(ap.parse_args().phase)
