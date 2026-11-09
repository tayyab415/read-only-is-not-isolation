"""Read native storage mapping for one already completed owned P4 fixture."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE.parent
PROVISION = EVIDENCE / "P4-models/remote-data/assigned-m1/p4-m1-luna-separate/provision.json"

REMOTE = """
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,re,subprocess,sys
config=json.loads(sys.stdin.read())
repos=config['cache_repositories']
assert len(repos)==2 and all(re.fullmatch(r'p1-p4-m1-luna-separate-(alice|bob)-cache',r) for r in repos)
names=subprocess.check_output(['docker','ps','--format','{{.Names}}'],text=True).splitlines()
assert set(names)=={'art-a','art-a-db'}
quoted=','.join("'"+r+"'" for r in repos)
sql=("BEGIN READ ONLY; SELECT coalesce(json_agg(row_to_json(n) ORDER BY repo,node_path,node_name),'[]'::json) "
     "FROM (SELECT node_id,node_type,repo,node_path,node_name,bin_length,sha1_actual,sha256 "
     "FROM public.nodes WHERE repo IN ("+quoted+") AND node_name ~ '^pkg-[0-9]{2}\\\\.bin$') n; COMMIT;")
command=['docker','exec','art-a-db','psql','-U','artifactory','-d','artifactory','-qAt','-c',sql]
query=subprocess.run(command,capture_output=True,text=True,check=True)
nodes=json.loads(query.stdout)
assert len(nodes)==32
assert {(r['repo'],r['node_name']) for r in nodes}=={(repo,f'pkg-{i:02d}.bin') for repo in repos for i in range(16)}
assert all(r['bin_length']==31 and r['sha256']==config['payload_sha256'] for r in nodes)
hashes={r['sha1_actual'] for r in nodes};assert len(hashes)==1
checksum=hashes.pop();assert re.fullmatch('[0-9a-f]{40}',checksum)
candidates=[
 '/var/opt/jfrog/artifactory/data/artifactory/filestore/'+checksum[:2]+'/'+checksum,
 '/var/opt/jfrog/artifactory/filestore/'+checksum[:2]+'/'+checksum]
found=[]
for path in candidates:
 result=subprocess.run(['docker','exec','art-a','test','-f',path],capture_output=True,text=True)
 if result.returncode==0:found.append(path)
 elif result.returncode!=1:raise RuntimeError('File existence command failed')
assert len(found)==1
path=found[0]
file_sha1=subprocess.check_output(['docker','exec','art-a','sha1sum',path],text=True).split()[0]
file_sha256=subprocess.check_output(['docker','exec','art-a','sha256sum',path],text=True).split()[0]
stat=subprocess.check_output(['docker','exec','art-a','stat','--printf=%d|%i|%s|%h|%F',path],text=True)
device,inode,size,links,kind=stat.split('|')
assert file_sha1==checksum and file_sha256==config['payload_sha256'] and int(size)==31 and kind=='regular file'
image=subprocess.check_output(['docker','inspect','art-a','--format','{{.Image}}'],text=True).strip()
print(json.dumps({'utc':datetime.now(timezone.utc).isoformat(),'running_containers':names,
 'sql':sql,'query_command':command,'native_nodes':nodes,'candidate_blob_paths':candidates,
 'existing_canonical_blob_path':path,'blob_sha1':file_sha1,'blob_sha256':file_sha256,
 'blob_stat':{'device':device,'inode':inode,'bytes':int(size),'hardlinks':int(links),'kind':kind},
 'artifactory_image':image,'fixture_or_service_mutations':0,
 'scope':'Current mapping for exactly 32 completed owned P4 cache nodes and their canonical checksum blob; no timing or global filestore claim'}))
"""


def main():
    destination = HERE / "RESULT.json"
    if destination.exists():
        raise RuntimeError("Preserve the existing native readback")
    verifier = json.loads((EVIDENCE / "P2b-verification/RESULT.json").read_text())
    review = json.loads((EVIDENCE / "P2b-recovery/HELD-REVIEW.json").read_text())
    assert verifier["passed"] and review["passed"] and verifier["primary_aa_raw_nominal_pass"]
    provision = json.loads(PROVISION.read_text())
    assert provision["run_id"] == "p4-m1-luna-separate" and provision["prewarm_count"] == 32
    repositories = [provision["repositories"][actor] + "-cache"
                    for actor in ("alice", "bob")]
    assert all(re.fullmatch(r"p1-p4-m1-luna-separate-(alice|bob)-cache", r)
               for r in repositories)
    config = {
        "cache_repositories": repositories,
        "payload_sha256": "85c7795747cf1f3ac1e7d7f53d3faa6b9d9f3dc961aa9b692aaaddb943ace870",
    }
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "lab2", "python3", "-c", shlex.quote(REMOTE)],
        input=json.dumps(config), capture_output=True, text=True, timeout=45)
    record = {
        "utc": datetime.now(timezone.utc).isoformat(),
        "returncode": result.returncode, "passed": result.returncode == 0,
        "provision_sha256": hashlib.sha256(PROVISION.read_bytes()).hexdigest(),
        "p2b_verification_sha256": hashlib.sha256(
            (EVIDENCE / "P2b-verification/RESULT.json").read_bytes()).hexdigest(),
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "configuration": config,
        "native": json.loads(result.stdout) if result.returncode == 0 else None,
        "stdout_on_failure": result.stdout if result.returncode else None,
        "stderr": result.stderr,
        "new_fixture_created": False, "model_calls": 0,
        "limits": [
            "The readback concerns completed P4 fixtures on lab2, not the earlier lab host P2 runtime.",
            "A common checksum reference and canonical blob do not identify page-cache residency or a timing cause.",
            "The scoped read does not inventory the full filestore or prove there are no other copies.",
        ],
    }
    destination.open("x").write(json.dumps(record, indent=2) + "\n")
    print(json.dumps({
        "passed": record["passed"], "returncode": result.returncode,
        "native_nodes": len(record["native"]["native_nodes"]) if record["native"] else None,
        "error": result.stderr if result.returncode else None,
    }))
    result.check_returncode()


if __name__ == "__main__":
    main()
