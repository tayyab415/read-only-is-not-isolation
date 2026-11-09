
import subprocess, gzip, hashlib, json, sys, datetime
root='/var/opt/jfrog/artifactory/log/'
list_script='cd '+root+' && for f in artifactory-request.log router-request.log archived/artifactory-request*.log archived/artifactory-request*.log.gz archived/router-request*.log archived/router-request*.log.gz; do [ ! -f "$f" ] || printf "%s\\n" "$f"; done'
files=sorted(set(subprocess.check_output(['docker','exec','art-a','sh','-c',list_script],text=True).splitlines()))
assert len(files)<=20 and all(f.startswith(('artifactory-request.log','router-request.log','archived/artifactory-request','archived/router-request')) for f in files)
needles=tuple(('p1/m1-'+r+'/').encode() for r in ['open','aa','sham','combined','separate'])
with gzip.GzipFile(fileobj=sys.stdout.buffer,mode='wb',mtime=0) as out:
 def emit(r):out.write((json.dumps(r,separators=(',',':'))+'\n').encode())
 emit({'kind':'begin','utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'files':files,'scope':'retained P1 main request/router rows only; read-only recovery'})
 for f in files:
  proc=subprocess.Popen(['docker','exec','art-a','gzip' if f.endswith('.gz') else 'cat', *(['-dc'] if f.endswith('.gz') else []),root+f],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  digest=hashlib.sha256();total=matched=byte_count=0
  for raw in proc.stdout:
   total+=1;byte_count+=len(raw);digest.update(raw)
   if any(n in raw for n in needles):
    matched+=1;emit({'kind':'row','source':f,'line':total,'raw':raw.decode().rstrip('\n')})
  err=proc.stderr.read();rc=proc.wait()
  if rc:raise RuntimeError('native file recovery failed: '+f)
  emit({'kind':'source','source':f,'decoded_bytes':byte_count,'decoded_lines':total,'decoded_sha256':digest.hexdigest(),'matched_rows':matched})
 emit({'kind':'complete','utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source_count':len(files)})
