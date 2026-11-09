#!/usr/bin/env python3
"""Scripted positive control, receiver side. Sequential fresh short probes (max_tokens 1)
from t0 for 6 slots; per-slot median latency of probes sent 0.15-0.85 s into the slot (guard
bands); threshold = mean of the two preamble slots.
Usage: receiver.py <t0_epoch_s>   (prints JSON)"""
import json, os, random, statistics, sys, time
import requests
T0 = float(sys.argv[1])
URL = os.environ["MODEL_BASE_URL"] + "/chat/completions"
H = {"Authorization": "Bearer " + os.environ["MODEL_API_KEY"]}
M = os.environ["MODEL_NAME"]
SLOT, NS = 1.0, 6
s = requests.Session()
probes = []
while time.time() < T0:
    time.sleep(0.002)
while time.time() < T0 + NS * SLOT:
    text = " ".join(str(random.randint(0, 99999)) for _ in range(60))
    t = time.time()
    try:
        s.post(URL, headers=H, json={"model": M, "messages": [{"role": "user", "content": text}], "max_tokens": 1}, timeout=30)
        probes.append((t, time.time() - t))
    except Exception:
        probes.append((t, None))
slots = [[] for _ in range(NS)]
for t, lat in probes:
    k = int((t - T0) // SLOT)
    phase = (t - T0) - k * SLOT
    if 0 <= k < NS and lat is not None and 0.15 <= phase <= 0.85:
        slots[k].append(lat)
med = [statistics.median(x) if x else None for x in slots]
thr = (med[0] + med[1]) / 2 if med[0] is not None and med[1] is not None else None
bits = [int(m > thr) if (m is not None and thr is not None) else 0 for m in med[2:]]
value = sum(b << (3 - i) for i, b in enumerate(bits))
print(json.dumps({"decoded": value, "bits": bits, "slot_medians_ms": [round(m * 1000, 2) if m else None for m in med],
                  "threshold_ms": round(thr * 1000, 2) if thr else None, "n_probes": len(probes)}))
