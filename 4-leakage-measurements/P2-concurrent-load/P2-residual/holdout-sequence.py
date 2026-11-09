import json,os,subprocess,sys,time
from pathlib import Path
p=Path.home()/"workspaces/artifactory-lab/next/P2-residual"
status=p/"holdout-sequence-status.json"
env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"}
results=[]
try:
 for config in ["separate","sham","aa","immutable"]:
  status.write_text(json.dumps({"stage":"collecting_holdout","configuration":config,"finished":results,"utc_ns":time.time_ns()}))
  with (p/"holdout-sequence.log").open("a") as stream:
   subprocess.run([sys.executable,"collect.py","holdout",config],cwd=p,env=env,stdout=stream,stderr=subprocess.STDOUT,check=True)
  results.append(config)
 with (p/"holdout-sequence.log").open("a") as stream:
  subprocess.run([sys.executable,"audit.py",str(p/"data/p2-m1"),"holdout"],cwd=p,env=env,stdout=stream,stderr=subprocess.STDOUT,check=True)
 status.write_text(json.dumps({"stage":"holdout_audited","finished":results,"utc_ns":time.time_ns()}))
except BaseException as error:
 status.write_text(json.dumps({"stage":"failed","finished":results,"failure_type":type(error).__name__,"utc_ns":time.time_ns()}))
 raise
