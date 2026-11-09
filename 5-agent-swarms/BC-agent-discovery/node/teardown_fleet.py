#!/usr/bin/env python3
"""Terminate BC nodes and read back that nothing is left. Records stop rows in the cash ledger.

Usage: teardown_fleet.py n02 n03 ...   |   teardown_fleet.py --all [--delete-sg]
Writes teardown-<utc>.json with the instance, volume, security-group and key-pair readback.
"""
import argparse, datetime, json, time
from pathlib import Path
import boto3

ROOT = Path(__file__).resolve().parent
NODES = ROOT / "nodes.json"
CASH = ROOT.parent / "ledger" / "cash-ledger.jsonl"
REGION, KEY = "us-east-1", "swarm-lab-bc"

ap = argparse.ArgumentParser()
ap.add_argument("names", nargs="*")
ap.add_argument("--all", action="store_true")
ap.add_argument("--delete-sg", action="store_true")
a = ap.parse_args()
nodes = json.loads(NODES.read_text())
pick = [n for n in nodes if n.get("state") != "terminated" and (a.all or n["name"] in a.names)]
ec = boto3.client("ec2", region_name=REGION)
ids = [n["instance_id"] for n in pick]
if ids:
    ec.terminate_instances(InstanceIds=ids)
    ec.get_waiter("instance_terminated").wait(InstanceIds=ids, WaiterConfig={"Delay": 5, "MaxAttempts": 120})
now = datetime.datetime.now(datetime.timezone.utc)
desc = {i["InstanceId"]: i for r in ec.describe_instances(InstanceIds=ids)["Reservations"] for i in r["Instances"]} if ids else {}
with CASH.open("a") as f:
    for n in pick:
        launched = datetime.datetime.fromisoformat(n["launched_utc"])
        hours = (now - launched).total_seconds() / 3600
        n.update(state=desc[n["instance_id"]]["State"]["Name"], terminated_utc=now.isoformat())
        f.write(json.dumps({"kind": "stop", "node": n["name"], "instance_id": n["instance_id"], "utc": now.isoformat(),
                            "hours_upper": round(hours, 4), "usd_upper": round(hours * (0.8048 + 120 * 0.08 / 730), 4)}) + "\n")
NODES.write_text(json.dumps(nodes, indent=2) + "\n")
readback = {"utc": now.isoformat(), "terminated": {n["name"]: n["state"] for n in pick}}
alive = [n for n in nodes if n.get("state") != "terminated"]
if not alive and a.delete_sg:
    sg = pick[0]["security_group"] if pick else None
    for _ in range(12):
        try:
            if sg:
                ec.delete_security_group(GroupId=sg)
            break
        except Exception:
            time.sleep(10)
    ec.delete_key_pair(KeyName=KEY)
readback["remaining_tagged_instances"] = [
    {"id": i["InstanceId"], "state": i["State"]["Name"]}
    for r in ec.describe_instances(Filters=[{"Name": "tag:Purpose", "Values": ["swarm-lab-BC-throwaway"]}])["Reservations"]
    for i in r["Instances"] if i["State"]["Name"] != "terminated"]
readback["available_volumes_from_bc"] = [v["VolumeId"] for v in ec.describe_volumes(
    Filters=[{"Name": "status", "Values": ["available"]}])["Volumes"]]
readback["bc_security_groups"] = [g["GroupId"] for g in ec.describe_security_groups(
    Filters=[{"Name": "group-name", "Values": ["swarm-bc-nodes"]}])["SecurityGroups"]]
readback["bc_key_pairs"] = [k["KeyName"] for k in ec.describe_key_pairs(
    Filters=[{"Name": "key-name", "Values": [KEY]}])["KeyPairs"]]
out = ROOT / f"teardown-{now.strftime('%Y%m%dT%H%M%S')}.json"
out.write_text(json.dumps(readback, indent=2) + "\n")
print(json.dumps(readback, indent=2))
