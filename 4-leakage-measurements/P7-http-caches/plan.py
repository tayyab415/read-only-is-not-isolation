"""Prospective balanced schedule for two configured cache implementations."""
import random
import re

SEED = 2026110807
PRODUCTS = ("nginx", "squid")
DELAYS = (20, 150)
ARMS = ("shared", "tenant-keyed", "private", "disabled")


def schedule(product, delay, arm, split):
    if product not in PRODUCTS or delay not in DELAYS \
            or arm not in ARMS + ("no-sender",) or split not in ("calibration", "holdout"):
        raise ValueError("Unknown prospective cache cohort")
    if split == "calibration" and arm != "shared":
        raise ValueError("Only shared positive-control calibration is fitted")
    count = 12 if split == "calibration" else 16 if arm == "no-sender" else 32
    rows = [{"label": label, "sender": sender, "receiver": "bob" if sender == "alice" else "alice"}
            for label in (0, 1) for sender in ("alice", "bob") for _ in range(count // 4)]
    random.Random(f"{SEED}/{product}/{delay}/{arm}/{split}").shuffle(rows)
    for i, row in enumerate(rows):
        token = f"g2-{product}-{delay}-{arm}-{split}-t{i:03d}"
        row.update(trial_id=f"t{i:03d}", product=product, delay_ms=delay, arm=arm, split=split,
                   active=row["label"] == 1 and arm != "no-sender", path=f"/missing/{delay}/{token}")
    return rows


def arm_order(product, delay):
    if product not in PRODUCTS or delay not in DELAYS:
        raise ValueError("Unknown configured cache")
    arms = [*ARMS, "no-sender"]
    random.Random(f"{SEED}/{product}/{delay}/arm-order").shuffle(arms)
    return arms


def marker(run_id, row, actor, kind):
    if not re.fullmatch(r"p7-[a-z0-9-]+", run_id) or actor not in ("alice", "bob") or kind not in ("sender", "receiver"):
        raise ValueError("Invalid owned request marker")
    return f"p7/{run_id}/{row['split']}/{row['trial_id']}/{actor}/{kind}"


def planned_primary_requests():
    rows = [row for product in PRODUCTS for delay in DELAYS
            for arm, split in [("shared", "calibration")] + [(arm, "holdout") for arm in (*ARMS, "no-sender")]
            for row in schedule(product, delay, arm, split)]
    return {"trials": len(rows), "receiver_requests": len(rows),
            "sender_requests": sum(row["active"] for row in rows),
            "http_requests": len(rows) + sum(row["active"] for row in rows),
            "reserved_client_plus_origin_upper_bound": 2 * (len(rows) + sum(row["active"] for row in rows))}
