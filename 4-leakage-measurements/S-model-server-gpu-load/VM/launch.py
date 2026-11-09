#!/usr/bin/env python3
"""Launch one throwaway GPU VM for the owned model-server study (S1).

No IAM role; SSH from this Mac only; self-terminates after 8 hours.
us-east-1 because ap-south-1 has 0 G-instance vCPU quota (checked 2026-11-09).
"""
import datetime, json, urllib.request
from pathlib import Path
import boto3

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "instance.json"
assert not STATE.exists(), "instance.json exists; inspect before launching again"
REGION, ITYPE, KEY = "us-east-1", "g6.xlarge", "swarm-lab-s1"
ec = boto3.client("ec2", region_name=REGION)
ssm = boto3.client("ssm", region_name=REGION)
if not ec.describe_key_pairs(Filters=[{"Name": "key-name", "Values": [KEY]}])["KeyPairs"]:
    ec.import_key_pair(KeyName=KEY, PublicKeyMaterial=Path.home().joinpath(".ssh/aws_lab.pub").read_bytes())
ip = urllib.request.urlopen("https://checkip.amazonaws.com", timeout=15).read().decode().strip()
vpc = ec.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
ami = ssm.get_parameter(Name="/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id")["Parameter"]["Value"]
name = "swarm-s1-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
sg = ec.create_security_group(GroupName=name, Description="Temporary owned model-server VM; SSH from controller only",
                              VpcId=vpc)["GroupId"]
state = {"region": REGION, "security_group": sg, "name": name, "controller_ip": ip, "key_name": KEY}
STATE.write_text(json.dumps(state, indent=2))
userdata = """#!/bin/bash
set -eux
shutdown -h +480
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq tmux jq
mkdir -p /home/ubuntu/s1 /home/ubuntu/logs
sudo -u ubuntu bash -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh'
sudo -u ubuntu bash -lc '~/.local/bin/uv venv -p 3.12 ~/venv && ~/.local/bin/uv pip install -p ~/venv/bin/python vllm fastapi uvicorn httpx numpy > ~/logs/pip.log 2>&1'
sudo -u ubuntu bash -lc '~/venv/bin/hf download Qwen/Qwen2.5-1.5B-Instruct > ~/logs/hf.log 2>&1 || ~/venv/bin/huggingface-cli download Qwen/Qwen2.5-1.5B-Instruct >> ~/logs/hf.log 2>&1'
chown -R ubuntu:ubuntu /home/ubuntu/s1 /home/ubuntu/logs
touch /home/ubuntu/BOOTSTRAP_READY
"""
try:
    ec.authorize_security_group_ingress(GroupId=sg, IpPermissions=[{
        "IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
        "IpRanges": [{"CidrIp": ip + "/32", "Description": "Experiment controller"}]}])
    r = ec.run_instances(
        ImageId=ami, InstanceType=ITYPE, MinCount=1, MaxCount=1, KeyName=KEY, SecurityGroupIds=[sg],
        MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled"},
        InstanceInitiatedShutdownBehavior="terminate",
        BlockDeviceMappings=[{"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 120, "VolumeType": "gp3", "DeleteOnTermination": True}}],
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name", "Value": name}, {"Key": "Purpose", "Value": "swarm-lab-S1-model-server-throwaway"}]}],
        UserData=userdata, ClientToken=name)["Instances"][0]
    state.update(instance_id=r["InstanceId"], image_id=ami, instance_type=ITYPE,
                 launched_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 auto_terminate_after_minutes=480, on_demand_usd_per_hour=0.8048)
    STATE.write_text(json.dumps(state, indent=2))
except BaseException:
    if "instance_id" not in state:
        ec.delete_security_group(GroupId=sg)
    raise
ec.get_waiter("instance_running").wait(InstanceIds=[state["instance_id"]], WaiterConfig={"Delay": 5, "MaxAttempts": 60})
inst = ec.describe_instances(InstanceIds=[state["instance_id"]])["Reservations"][0]["Instances"][0]
state.update(public_ip=inst["PublicIpAddress"], no_iam_role="IamInstanceProfile" not in inst,
             shutdown_behavior=ec.describe_instance_attribute(InstanceId=state["instance_id"],
                 Attribute="instanceInitiatedShutdownBehavior")["InstanceInitiatedShutdownBehavior"]["Value"])
STATE.write_text(json.dumps(state, indent=2))
print(json.dumps(state, indent=2))
