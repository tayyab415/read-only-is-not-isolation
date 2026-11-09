#!/usr/bin/env python3
"""Scripted positive control, sender side (runs inside an actor sandbox).
Frame: 1-s slots, preamble 1 0, then 4 data bits MSB first. In a 1 slot, 6 threads send
fresh ~400-word chat requests (max_tokens 8) back to back and stop 0.25 s before the slot
ends, so in-flight work does not spill into the next slot; in a 0 slot, nothing.
(v2: v1 used 4 threads, max_tokens 64 and a 0.05 s margin and spilled; see controls-shared-try1-spillover.json.)
Usage: sender.py <value 0..15> <t0_epoch_s> <out.json>"""
import json, os, random, sys, threading, time
import requests
V, T0, OUT = int(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
URL = os.environ["MODEL_BASE_URL"] + "/chat/completions"
H = {"Authorization": "Bearer " + os.environ["MODEL_API_KEY"]}
M = os.environ["MODEL_NAME"]
SLOT = 1.0
bits = [1, 0] + [(V >> (3 - i)) & 1 for i in range(4)]
WORDS = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec romeo sierra tango".split()
sent = []
def worker(end):
    s = requests.Session()
    while time.time() < end:
        text = " ".join(random.choice(WORDS) for _ in range(400))
        t = time.time()
        try:
            s.post(URL, headers=H, json={"model": M, "messages": [{"role": "user", "content": text}], "max_tokens": 8}, timeout=30)
        except Exception:
            pass
        sent.append(t)
for k, b in enumerate(bits):
    start, end = T0 + k * SLOT, T0 + (k + 1) * SLOT - 0.25
    while time.time() < start:
        time.sleep(0.002)
    if b:
        ts = [threading.Thread(target=worker, args=(end,)) for _ in range(6)]
        for t in ts: t.start()
        for t in ts: t.join()
json.dump({"value": V, "bits": bits, "t0": T0, "requests": len(sent)}, open(OUT, "w"))
