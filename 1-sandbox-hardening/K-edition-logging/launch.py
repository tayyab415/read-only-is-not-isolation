#!/usr/bin/env python3
"""Create only the dedicated version-test instance; persist IDs before waits."""
import datetime
import json
from pathlib import Path
import urllib.request
import boto3

ROOT = Path(__file__).resolve().parent
ROOT.mkdir(parents=True, exist_ok=True)
STATE = ROOT / "instance.json"
assert not STATE.exists(), "Inspect the saved instance before another launch"
ec = boto3.client("ec2", region_name="ap-south-1")
lab_host = ec.describe_instances(InstanceIds=["<LAB_HOST_INSTANCE_ID>"])["Reservations"][0]["Instances"][0]
with urllib.request.urlopen("https://checkip.amazonaws.com", timeout=15) as response:
    ip = response.read().decode().strip()
name = "swarm-k-versions-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
sg = ec.create_security_group(GroupName=name, Description="Temporary owned version study; SSH from controller only",
                              VpcId=lab_host["VpcId"])["GroupId"]
state = {"region": "ap-south-1", "security_group": sg, "name": name}
STATE.write_text(json.dumps(state, indent=2))
try:
    ec.authorize_security_group_ingress(GroupId=sg, IpPermissions=[{
        "IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
        "IpRanges": [{"CidrIp": ip + "/32", "Description": "Experiment controller"}],
    }])
    images = ec.describe_images(Owners=["099720109477"], Filters=[
        {"Name": "name", "Values": ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]},
        {"Name": "state", "Values": ["available"]},
    ])["Images"]
    ami = max(images, key=lambda x: x["CreationDate"])["ImageId"]
    userdata = """#!/bin/bash
set -eu
shutdown -h +60
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2
usermod -aG docker ubuntu
mkdir -p /home/ubuntu/k-versions
chown ubuntu:ubuntu /home/ubuntu/k-versions
touch /home/ubuntu/k-versions/BOOTSTRAP_READY
"""
    result = ec.run_instances(
        ImageId=ami, InstanceType="m6a.large", MinCount=1, MaxCount=1,
        KeyName=lab_host["KeyName"], SubnetId=lab_host["SubnetId"], SecurityGroupIds=[sg],
        MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled"},
        InstanceInitiatedShutdownBehavior="terminate",
        BlockDeviceMappings=[{"DeviceName": "/dev/sda1", "Ebs": {
            "VolumeSize": 40, "VolumeType": "gp3", "DeleteOnTermination": True}}],
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name", "Value": name}, {"Key": "Purpose", "Value": "owned-version-logging-study"}]}],
        UserData=userdata, ClientToken=name,
    )["Instances"][0]
    state.update(instance_id=result["InstanceId"], image_id=ami,
                 launched_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    STATE.write_text(json.dumps(state, indent=2))
except BaseException:
    if "instance_id" not in state:
        ec.delete_security_group(GroupId=sg)
    raise
ec.get_waiter("instance_running").wait(InstanceIds=[state["instance_id"]],
                                     WaiterConfig={"Delay": 5, "MaxAttempts": 36})
instance = ec.describe_instances(InstanceIds=[state["instance_id"]])["Reservations"][0]["Instances"][0]
state.update(public_ip=instance["PublicIpAddress"], no_iam_role="IamInstanceProfile" not in instance,
             shutdown_behavior=ec.describe_instance_attribute(
                 InstanceId=state["instance_id"], Attribute="instanceInitiatedShutdownBehavior"
             )["InstanceInitiatedShutdownBehavior"]["Value"])
STATE.write_text(json.dumps(state, indent=2))
print(json.dumps(state, indent=2))
