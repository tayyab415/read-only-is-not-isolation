#!/usr/bin/env python3
"""BC slot gateway in front of the node's vLLM server.

Adapted from S-serving/gateway.py (S1 "salted" policy), not imported.
One process per slot, bound to that slot's docker bridge IP, so the slot a request
arrives on is fixed by the network. The current tenant for the slot is read from a key
file written by node_ctl.py ({} when the slot is idle). The gateway strips any client
cache_salt and injects HMAC(secret, tenant). It forwards only the normal OpenAI-style
routes, removes prompt_tokens_details from responses (cached-token counts stay in the
trusted log), and writes one JSON line per request.
Usage: gateway_bc.py --slot 1 --bind 10.201.1.1 --keyfile /opt/bc/keys/slot1.json \
         --secret /opt/bc/secret --log /opt/bc/logs/gw-slot1.jsonl
"""
import argparse, hashlib, hmac, json, threading, time, uuid
import httpx, uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

ap = argparse.ArgumentParser()
ap.add_argument("--slot", type=int, required=True)
ap.add_argument("--bind", required=True)
ap.add_argument("--port", type=int, default=9000)
ap.add_argument("--upstream", default="http://127.0.0.1:8000")
ap.add_argument("--keyfile", required=True)
ap.add_argument("--secret", required=True)
ap.add_argument("--log", required=True)
args = ap.parse_args()

SECRET = open(args.secret, "rb").read().strip()
LOG_LOCK = threading.Lock()
FORWARD_POST = {"/v1/chat/completions", "/v1/completions"}
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
client = httpx.AsyncClient(base_url=args.upstream, timeout=httpx.Timeout(180.0))


def slot_owner():
    try:
        with open(args.keyfile) as f:
            return json.load(f) or {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def authorize(request):
    owner = slot_owner()
    got = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    key = owner.get("key", "")
    if key and got and hmac.compare_digest(got.encode(), key.encode()):
        return owner
    return None


def log(rec):
    rec["slot"] = args.slot
    with LOG_LOCK, open(args.log, "a") as f:
        f.write(json.dumps(rec) + "\n")


def scrub(obj):
    if isinstance(obj, dict) and isinstance(obj.get("usage"), dict):
        details = obj["usage"].pop("prompt_tokens_details", None) or {}
        return details.get("cached_tokens"), obj["usage"]
    return None, None


def base_row(request, owner, t_in):
    return {"rid": str(uuid.uuid4()), "t_in": t_in, "method": request.method, "path": request.url.path,
            "tenant": owner.get("tenant") if owner else None,
            "episode": owner.get("episode") if owner else None}


@app.get("/health")
async def health(request: Request):
    row = base_row(request, None, time.time_ns())
    row.update(status=200, t_down=time.time_ns())
    log(row)
    return JSONResponse({"status": "ok"})


@app.get("/v1/models")
async def models(request: Request):
    t_in = time.time_ns()
    owner = authorize(request)
    row = base_row(request, owner, t_in)
    if owner is None:
        row.update(status=401, t_down=time.time_ns())
        log(row)
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    row["t_up"] = time.time_ns()
    r = await client.get("/v1/models")
    row.update(status=r.status_code, t_down=time.time_ns())
    log(row)
    return JSONResponse(r.json(), status_code=r.status_code)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(path: str, request: Request):
    t_in = time.time_ns()
    owner = authorize(request)
    row = base_row(request, owner, t_in)
    route = "/" + path
    if request.method != "POST" or route not in FORWARD_POST:
        row.update(status=404, t_down=time.time_ns())
        log(row)
        return JSONResponse({"error": "not found"}, status_code=404)
    if owner is None:
        row.update(status=401, t_down=time.time_ns())
        log(row)
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
        assert isinstance(body, dict)
    except Exception:
        row.update(status=400, t_down=time.time_ns())
        log(row)
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    body.pop("cache_salt", None)
    body["cache_salt"] = hmac.new(SECRET, owner["tenant"].encode(), hashlib.sha256).hexdigest()
    raw = json.dumps(body, sort_keys=True).encode()
    stream = bool(body.get("stream"))
    row.update(stream=stream, body_sha256=hashlib.sha256(raw).hexdigest(),
               salt_sha256_12=hashlib.sha256(body["cache_salt"].encode()).hexdigest()[:12])
    row["t_up"] = time.time_ns()
    if not stream:
        r = await client.post(route, content=raw, headers={"content-type": "application/json"})
        row["t_first"] = row["t_down"] = time.time_ns()
        try:
            data = r.json()
        except Exception:
            row.update(status=r.status_code)
            log(row)
            return JSONResponse({"error": "upstream error"}, status_code=502)
        cached, usage = scrub(data)
        row.update(status=r.status_code, cached_tokens=cached,
                   prompt_tokens=(usage or {}).get("prompt_tokens"),
                   completion_tokens=(usage or {}).get("completion_tokens"))
        log(row)
        return JSONResponse(data, status_code=r.status_code)

    req = client.build_request("POST", route, content=raw, headers={"content-type": "application/json"})
    upstream = await client.send(req, stream=True)
    row["status"] = upstream.status_code

    async def relay():
        try:
            async for line in upstream.aiter_lines():
                if "t_first" not in row:
                    row["t_first"] = time.time_ns()
                if line.startswith("data: ") and line[6:].strip() not in ("", "[DONE]"):
                    try:
                        obj = json.loads(line[6:])
                        cached, usage = scrub(obj)
                        if usage:
                            row.update(cached_tokens=cached, prompt_tokens=usage.get("prompt_tokens"),
                                       completion_tokens=usage.get("completion_tokens"))
                        line = "data: " + json.dumps(obj)
                    except json.JSONDecodeError:
                        pass
                yield line + "\n"
        finally:
            await upstream.aclose()
            row["t_down"] = time.time_ns()
            log(row)

    return StreamingResponse(relay(), status_code=upstream.status_code,
                             media_type=upstream.headers.get("content-type", "text/event-stream"))


if __name__ == "__main__":
    uvicorn.run(app, host=args.bind, port=args.port, log_level="warning")
