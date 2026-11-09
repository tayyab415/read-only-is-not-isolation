#!/usr/bin/env python3
"""Terminate only the saved, tagged K instance and remove its dedicated SG."""
import datetime
import json
from pathlib import Path
import time
import boto3
from botocore.exceptions import ClientError

root = Path(__file__).resolve().parent
state = json.loads((root / "instance.json").read_text())
ec = boto3.client("ec2", region_name=state["region"])
instance = ec.describe_instances(InstanceIds=[state["instance_id"]])["Reservations"][0]["Instances"][0]
tags = {t["Key"]: t["Value"] for t in instance["Tags"]}
assert tags["Name"] == state["name"]
assert tags["Purpose"] == "owned-version-logging-study"
assert instance["InstanceId"] != "<LAB_HOST_INSTANCE_ID>"
assert [s["GroupId"] for s in instance["SecurityGroups"]] == [state["security_group"]]
volumes = [m["Ebs"]["VolumeId"] for m in instance.get("BlockDeviceMappings", [])]
assert all(m["Ebs"]["DeleteOnTermination"] for m in instance["BlockDeviceMappings"])
report = {"instance_id": state["instance_id"], "security_group": state["security_group"],
          "volumes": volumes, "requested_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
          "ownership_checks_passed": True}
ec.terminate_instances(InstanceIds=[state["instance_id"]])
(root / "cleanup.json").write_text(json.dumps(report, indent=2))
for _ in range(90):
    observed = ec.describe_instances(InstanceIds=[state["instance_id"]])["Reservations"][0]["Instances"][0]
    if observed["State"]["Name"] == "terminated":
        break
    time.sleep(2)
else:
    raise RuntimeError("Termination not yet verified; saved IDs remain")
for _ in range(30):
    try:
        ec.delete_security_group(GroupId=state["security_group"])
        break
    except ClientError as error:
        if error.response["Error"]["Code"] != "DependencyViolation":
            raise
        time.sleep(2)
else:
    raise RuntimeError("Security group deletion pending")
report["final_state"] = observed["State"]["Name"]
report["security_group_deleted"] = True
report["volume_states"] = {}
for volume in volumes:
    try:
        result = ec.describe_volumes(VolumeIds=[volume])["Volumes"]
        report["volume_states"][volume] = result[0]["State"] if result else "absent"
    except ClientError as error:
        if error.response["Error"]["Code"] != "InvalidVolume.NotFound":
            raise
        report["volume_states"][volume] = "absent"
report["verified_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
assert all(s in ("deleted", "absent") for s in report["volume_states"].values()), report["volume_states"]
(root / "cleanup.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
