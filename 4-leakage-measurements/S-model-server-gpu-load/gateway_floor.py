#!/usr/bin/env python3
"""S2 tenant gateway: the S1 gateway plus a minimum response time (floor).

Copied from the frozen S1 gateway.py; the only functional change is --floor-ms.
Every response is held until floor-ms have passed since the request arrived.
Responses that take longer than the floor are not changed.

S1 docstring follows.
S1 tenant gateway in front of an owned vLLM server.

One process per policy, fixed at startup:
  shared      strip any client cache_salt; inject nothing (one cache for all tenants)
  salted      strip any client cache_salt; inject HMAC(secret, tenant) (server-enforced)
  passthrough forward the client's cache_salt unchanged (client-chosen salt)

Tenants authenticate with a bearer key. The gateway removes prompt_tokens_details from
client responses, so cached-token counts stay in the trusted log and timing is the only
receiver-visible signal. Each request is logged as one JSON line.
Usage: gateway.py --policy salted --port 9002 --upstream http://127.0.0.1:8000
"""
import argparse, asyncio, hashlib, hmac, json, os, threading, time, uuid
import httpx, uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

ap = argparse.ArgumentParser()
ap.add_argument("--policy", required=True, choices=["shared", "salted", "passthrough"])
ap.add_argument("--port", type=int, required=True)
ap.add_argument("--upstream", default="http://127.0.0.1:8000")
ap.add_argument("--keys", default=os.path.expanduser("~/s1/tenant-keys.json"))
ap.add_argument("--secret", default=os.path.expanduser("~/s1/gateway-secret"))
ap.add_argument("--log", required=True)
ap.add_argument("--floor-ms", type=float, required=True)
args = ap.parse_args()

KEYS = json.load(open(args.keys))          # {"<key>": "<tenant>"}
SECRET = open(args.secret, "rb").read().strip()
LOG_LOCK = threading.Lock()
app = FastAPI()
client = httpx.AsyncClient(base_url=args.upstream, timeout=120)


def tenant_salt(tenant):
    return hmac.new(SECRET, tenant.encode(), hashlib.sha256).hexdigest()


def log(rec):
    with LOG_LOCK, open(args.log, "a") as f:
        f.write(json.dumps(rec) + "\n")


@app.post("/v1/chat/completions")
async def chat(request: Request):
    t_in = time.time_ns()
    auth = request.headers.get("authorization", "")
    tenant = KEYS.get(auth.removeprefix("Bearer ").strip())
    rid = str(uuid.uuid4())
    if tenant is None:
        log({"rid": rid, "t_in": t_in, "status": 401, "policy": args.policy})
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    client_salt = body.pop("cache_salt", None)
    if args.policy == "salted":
        body["cache_salt"] = tenant_salt(tenant)
    elif args.policy == "passthrough" and client_salt is not None:
        body["cache_salt"] = client_salt
    raw = json.dumps(body, sort_keys=True).encode()
    t_up = time.time_ns()
    r = await client.post("/v1/chat/completions", content=raw, headers={"content-type": "application/json"})
    t_down = time.time_ns()
    data = r.json()
    wait_s = args.floor_ms / 1000 - (time.time_ns() - t_in) / 1e9
    if wait_s > 0:
        await asyncio.sleep(wait_s)
    t_out = time.time_ns()
    usage = data.get("usage") or {}
    details = usage.pop("prompt_tokens_details", None) or {}
    log({"rid": rid, "t_in": t_in, "t_up": t_up, "t_down": t_down, "status": r.status_code,
         "policy": args.policy, "tenant": tenant, "trial": request.headers.get("x-trial-id"),
         "role": request.headers.get("x-role"), "client_salt_present": client_salt is not None,
         "salt_sha256_12": hashlib.sha256(body["cache_salt"].encode()).hexdigest()[:12] if "cache_salt" in body else None,
         "body_sha256": hashlib.sha256(raw).hexdigest(), "prompt_tokens": usage.get("prompt_tokens"),
         "cached_tokens": details.get("cached_tokens"), "completion_tokens": usage.get("completion_tokens"),
         "vllm_id": data.get("id"), "floor_ms": args.floor_ms, "t_out": t_out,
         "padded_ms": max(0.0, wait_s * 1000)})
    return JSONResponse(data, status_code=r.status_code)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
