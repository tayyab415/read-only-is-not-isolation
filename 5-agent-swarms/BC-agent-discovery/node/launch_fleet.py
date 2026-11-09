#!/usr/bin/env python3
"""Launch and provision BC nodes (g6.xlarge, us-east-1). Adapted from S-serving/VM/launch.py.

No IAM role; SSH from this Mac only; each node powers off (and terminates) after
--ttl-min minutes. State: nodes.json (one row per node), ssh_config (Host bc-nNN),
../ledger/cash-ledger.jsonl (launch rows; teardown_fleet.py adds stop rows).
Usage: launch_fleet.py --count 2 [--first 1] [--ttl-min 600] [--no-provision]
       launch_fleet.py --provision-only n01 n02
"""
import argparse, datetime, json, subprocess, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import boto3

ROOT = Path(__file__).resolve().parent
NODES = ROOT / "nodes.json"
SSH_CONFIG = ROOT / "ssh_config"
CASH = ROOT.parent / "ledger" / "cash-ledger.jsonl"
REGION, ITYPE, KEY = "us-east-1", "g6.xlarge", "swarm-lab-bc"
USD_PER_HOUR = 0.8048 + 120 * 0.08 / 730  # on-demand list price + 120 GB gp3
FILES = ["gateway_bc.py", "node_ctl.py", "node_ctl.sh", "constraints.txt", "actor.Dockerfile", "provision.sh",
         "firewall.sh", "fleet_init.sh"]
SSH_OPTS = ["-F", str(SSH_CONFIG), "-o", "ConnectTimeout=10", "-o", "BatchMode=yes"]


def load_nodes():
    return json.loads(NODES.read_text()) if NODES.exists() else []


def save_nodes(nodes):
    NODES.write_text(json.dumps(nodes, indent=2) + "\n")
    lines = []
    for n in nodes:
        if n.get("state") in ("terminated",):
            continue
        lines += [f"Host bc-{n['name']}", f"  HostName {n.get('public_ip', '0.0.0.0')}", "  User ubuntu",
                  f"  IdentityFile {Path.home() / '.ssh/aws_lab'}", "  IdentitiesOnly yes",
                  f"  UserKnownHostsFile {ROOT / 'known_hosts'}", "  StrictHostKeyChecking accept-new",
                  "  ServerAliveInterval 15", "  ControlMaster auto", "  ControlPersist 30m",
                  f"  ControlPath /tmp/bc-cm-{n['name']}", ""]
    SSH_CONFIG.write_text("\n".join(lines))


def cash(row):
    CASH.parent.mkdir(exist_ok=True)
    with CASH.open("a") as f:
        f.write(json.dumps(row) + "\n")


def security_group(ec, ip):
    vpc = ec.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    found = ec.describe_security_groups(Filters=[{"Name": "group-name", "Values": ["swarm-bc-nodes"]}])["SecurityGroups"]
    if found:
        sg = found[0]["GroupId"]
    else:
        sg = ec.create_security_group(GroupName="swarm-bc-nodes", VpcId=vpc,
                                      Description="BC throwaway model-server nodes; SSH from controller only")["GroupId"]
    perms = ec.describe_security_groups(GroupIds=[sg])["SecurityGroups"][0]["IpPermissions"]
    cidrs = {r["CidrIp"] for p in perms for r in p.get("IpRanges", [])}
    if ip + "/32" not in cidrs:
        ec.authorize_security_group_ingress(GroupId=sg, IpPermissions=[{
            "IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
            "IpRanges": [{"CidrIp": ip + "/32", "Description": "BC controller"}]}])
    return sg


def launch(count, first, ttl_min, image=None):
    ec = boto3.client("ec2", region_name=REGION)
    ssm = boto3.client("ssm", region_name=REGION)
    if not ec.describe_key_pairs(Filters=[{"Name": "key-name", "Values": [KEY]}])["KeyPairs"]:
        ec.import_key_pair(KeyName=KEY, PublicKeyMaterial=(Path.home() / ".ssh/aws_lab.pub").read_bytes())
    ip = urllib.request.urlopen("https://checkip.amazonaws.com", timeout=15).read().decode().strip()
    sg = security_group(ec, ip)
    ami = image or ssm.get_parameter(Name="/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id")["Parameter"]["Value"]
    nodes = load_nodes()
    taken = {n["name"] for n in nodes}
    new = []
    for i in range(first, first + count):
        nm = f"n{i:02d}"
        assert nm not in taken, f"{nm} already in nodes.json"
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
        r = ec.run_instances(
            ImageId=ami, InstanceType=ITYPE, MinCount=1, MaxCount=1, KeyName=KEY, SecurityGroupIds=[sg],
            MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled", "HttpPutResponseHopLimit": 1},
            InstanceInitiatedShutdownBehavior="terminate",
            BlockDeviceMappings=[{"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 120, "VolumeType": "gp3",
                                                                       "DeleteOnTermination": True}}],
            TagSpecifications=[{"ResourceType": "instance", "Tags": [
                {"Key": "Name", "Value": f"swarm-bc-{nm}"}, {"Key": "Purpose", "Value": "swarm-lab-BC-throwaway"}]}],
            UserData=f"#!/bin/bash\nshutdown -h +{ttl_min}\n", ClientToken=f"swarm-bc-{nm}-{stamp}")["Instances"][0]
        row = {"name": nm, "ssh": f"bc-{nm}", "instance_id": r["InstanceId"], "image_id": ami,
               "instance_type": ITYPE, "security_group": sg, "controller_ip": ip, "ttl_min": ttl_min,
               "launched_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "state": "pending"}
        nodes.append(row)
        new.append(row)
        save_nodes(nodes)
        cash({"kind": "launch", "node": nm, "instance_id": r["InstanceId"], "utc": row["launched_utc"],
              "usd_per_hour": USD_PER_HOUR})
    ids = [n["instance_id"] for n in new]
    ec.get_waiter("instance_running").wait(InstanceIds=ids, WaiterConfig={"Delay": 5, "MaxAttempts": 80})
    desc = {i["InstanceId"]: i for r in ec.describe_instances(InstanceIds=ids)["Reservations"] for i in r["Instances"]}
    for n in new:
        inst = desc[n["instance_id"]]
        n.update(public_ip=inst["PublicIpAddress"], no_iam_role="IamInstanceProfile" not in inst,
                 availability_zone=inst["Placement"]["AvailabilityZone"], state="running",
                 shutdown_behavior=ec.describe_instance_attribute(
                     InstanceId=n["instance_id"], Attribute="instanceInitiatedShutdownBehavior"
                 )["InstanceInitiatedShutdownBehavior"]["Value"])
    save_nodes(nodes)
    return [n["name"] for n in new]


def ssh(alias, cmd, timeout=120, data=None):
    return subprocess.run(["ssh", *SSH_OPTS, alias, cmd], input=data, capture_output=True, text=True, timeout=timeout)


def provision(name, script="provision.sh"):
    alias = f"bc-{name}"
    for _ in range(40):
        try:
            if ssh(alias, "true", timeout=20).returncode == 0:
                break
        except subprocess.TimeoutExpired:
            pass
        time.sleep(6)
    else:
        return name, "ssh never came up"
    subprocess.run(["ssh", *SSH_OPTS, alias, "rm -rf /tmp/bc-src && mkdir -p /tmp/bc-src"], check=True)
    subprocess.run(["scp", "-q", *SSH_OPTS, *[str(ROOT / f) for f in FILES], f"{alias}:/tmp/bc-src/"], check=True)
    r = ssh(alias, f"sudo nohup bash /tmp/bc-src/{script} > /tmp/provision.log 2>&1 < /dev/null & echo started")
    return name, r.stdout.strip() or r.stderr.strip()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int)
    ap.add_argument("--first", type=int, default=1)
    ap.add_argument("--ttl-min", type=int, default=600)
    ap.add_argument("--no-provision", action="store_true")
    ap.add_argument("--provision-only", nargs="*")
    ap.add_argument("--image", help="BC machine image; nodes then run fleet_init.sh")
    a = ap.parse_args()
    names = a.provision_only if a.provision_only is not None else launch(a.count, a.first, a.ttl_min, a.image)
    print(json.dumps({"launched_or_selected": names}))
    if not a.no_provision:
        with ThreadPoolExecutor(max_workers=16) as pool:
            script = "fleet_init.sh" if a.image else "provision.sh"
            for nm, msg in pool.map(lambda n: provision(n, script), names):
                print(nm, msg, flush=True)
