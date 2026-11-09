#!/usr/bin/env bash
# Isolation tests for the d-agentN sandboxes. Run on the lab host: bash isolation-test.sh [agent-number]
# Each BLOCKED test passes when the probe fails. Positive controls (section 0) show the probes can succeed.
# Output: one block per test with the exact command, exit code, and trimmed output. Summary at the end.
set -u
A=${1:-1}
SB=d-agent$A
PASS=0; FAIL=0
HUBIP=$(hostname -I | awk '{print $1}')
ARTC_IP=$(docker inspect art-c --format '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' 2>/dev/null | awk '{print $1}')
OTHER=$(( A % 3 + 1 ))
OTHER_IP=172.30.1$OTHER.1

inbox() {
  # Abort if the sandbox is down: a dead container makes every "blocked" test pass for the wrong reason.
  [ "$(docker inspect -f '{{.State.Running}}' "$SB" 2>/dev/null)" = true ] || { echo "ABORT: $SB is not running" >&2; exit 3; }
  docker exec "$SB" bash -c "$1" 2>&1
}

# blocked NAME CMD : CMD runs inside the sandbox; it must fail (non-zero rc) to pass.
blocked() {
  local name=$1 cmd=$2 out rc
  out=$(inbox "$cmd"); rc=$?
  if [ $rc -ne 0 ]; then r=PASS; PASS=$((PASS+1)); else r=FAIL; FAIL=$((FAIL+1)); fi
  printf '[%s] %s\n  cmd: %s\n  rc=%s\n  out: %s\n' "$r" "$name" "$cmd" "$rc" "$(printf '%s' "$out" | head -c 300 | tr '\n' ' ')"
}
# expect NAME CMD GREP : CMD runs inside the sandbox; output must match GREP.
expect() {
  local name=$1 cmd=$2 pat=$3 out rc
  out=$(inbox "$cmd"); rc=$?
  if printf '%s' "$out" | grep -qE "$pat"; then r=PASS; PASS=$((PASS+1)); else r=FAIL; FAIL=$((FAIL+1)); fi
  printf '[%s] %s\n  cmd: %s\n  rc=%s\n  out: %s\n' "$r" "$name" "$cmd" "$rc" "$(printf '%s' "$out" | head -c 300 | tr '\n' ' ')"
}

echo "=== isolation test for $SB at $(date -u +%FT%TZ) ==="
echo "lab host ip=$HUBIP art-c ip=${ARTC_IP:-none} other sandbox ip=$OTHER_IP"
echo "lab host listeners:"; ss -ltn | awk 'NR>1{print "  "$4}' | sort -u

echo; echo "=== 0. positive controls (a leak WOULD show up) ==="
docker network create --internal d-ctl-int >/dev/null 2>&1
CTLIP=$(docker network inspect d-ctl-int --format '{{(index .IPAM.Config 0).Gateway}}')
docker run -d --rm --name d-ctl-a --network bridge --user 20001:20001 d-sandbox:1 sleep 60 >/dev/null
docker run -d --rm --name d-ctl-b --network d-ctl-int --user 20001:20001 d-sandbox:1 sleep 60 >/dev/null
sleep 1
for c in "bridge: internet HTTP|docker exec d-ctl-a curl -s -m 6 -o /dev/null -w %{http_code} https://example.com" \
         "bridge: external DNS|docker exec d-ctl-a dig +short +time=3 +tries=1 example.com @8.8.8.8" \
         "bridge: IMDSv2 token|docker exec d-ctl-a curl -s -m 4 -X PUT http://169.254.169.254/latest/api/token -H X-aws-ec2-metadata-token-ttl-seconds:5 -o /dev/null -w %{http_code}" \
         "plain --internal: lab host sshd via bridge gateway $CTLIP:22|docker exec d-ctl-b nc -zv -w 3 $CTLIP 22" ; do
  n=${c%%|*}; cmd=${c#*|}; out=$(bash -c "$cmd" 2>&1); printf 'CONTROL %s\n  cmd: %s\n  out: %s\n' "$n" "$cmd" "$(printf '%s' "$out" | head -c 200 | tr '\n' ' ')"
done
docker rm -f d-ctl-a d-ctl-b >/dev/null 2>&1; docker network rm d-ctl-int >/dev/null 2>&1

echo; echo "=== 1. internet ==="
blocked "HTTPS to example.com by name"      "curl -sS -m 6 https://example.com"
blocked "HTTP to public IP 1.1.1.1"         "curl -sS -m 6 http://1.1.1.1"
blocked "TCP 443 to 8.8.8.8"                "nc -zv -w 4 8.8.8.8 443"
blocked "ICMP to 8.8.8.8"                   "ping -c1 -W3 8.8.8.8"
blocked "pypi.org via pip"                  "pip download --no-deps -d /tmp/x --timeout 4 --retries 0 six 2>&1 | tail -2; test -f /tmp/x/*.whl"
blocked "DNS: A record for example.com (default resolver)"       "dig +short +time=3 +tries=1 example.com | grep -E '^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+\$'"
blocked "DNS: A record via @8.8.8.8 (direct UDP 53)" "dig +short +time=3 +tries=1 example.com @8.8.8.8 | grep -E '^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+\$'"
blocked "DNS: A record via @1.1.1.1 over TCP" "dig +tcp +short +time=3 +tries=1 example.com @1.1.1.1 | grep -E '^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+\$'"
blocked "DNS: unique-name lookup gets NOERROR/NXDOMAIN from a real resolver (exfil channel)" "dig +time=3 +tries=1 TXT probe-$(date +%s).example.com | grep -E 'status: (NOERROR|NXDOMAIN)'"
expect  "DNS: default resolver raw status (informational)" "dig +time=3 +tries=1 example.com | grep -E 'status:|connection timed out|no servers|communications error' | head -2" "."
expect  "no default route"                   "ip route" "^172\.30\.1$A\.0/24 dev eth0"
blocked "no IPv6 global address"             "ip -6 addr show scope global | grep -q inet6"

echo; echo "=== 2. EC2 metadata service ==="
blocked "IMDSv2 token PUT"      "curl -sS -m 4 -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 5'"
blocked "IMDSv1 GET"            "curl -sS -m 4 http://169.254.169.254/latest/meta-data/"
blocked "IMDS TCP 80"           "nc -zv -w 3 169.254.169.254 80"
blocked "IMDS IPv6 endpoint"    "curl -sS -m 4 -g 'http://[fd00:ec2::254]/latest/meta-data/'"

echo; echo "=== 3. lab host services and published ports ==="
blocked "lab host private IP $HUBIP:22 (sshd)"          "nc -zv -w 3 $HUBIP 22"
for p in 3773 49374 43207 38767; do
  blocked "lab host private IP $HUBIP:$p (listens on loopback only)" "nc -zv -w 3 $HUBIP $p"
done
blocked "own loopback 127.0.0.1:3773 (T3 port on host loopback)"    "nc -zv -w 3 127.0.0.1 3773"
blocked "own loopback 127.0.0.1:18581 (art-c published port)"        "nc -zv -w 3 127.0.0.1 18581"
blocked "docker0 gateway 172.17.0.1:22"            "nc -zv -w 3 172.17.0.1 22"
blocked "artnet-c gateway 172.28.0.1:22"           "nc -zv -w 3 172.28.0.1 22"
blocked "same-subnet .254 (no host IP on bridge)"  "nc -zv -w 3 172.30.1$A.254 22"
blocked "same-subnet .2 (nothing there)"           "nc -zv -w 3 172.30.1$A.2 22"
[ -n "$ARTC_IP" ] && blocked "art-c container $ARTC_IP:8081 (another workstream, other network)" "curl -sS -m 4 http://$ARTC_IP:8081/"
blocked "host.docker.internal resolves"            "getent hosts host.docker.internal"
blocked "host.docker.internal:22 by name"          "nc -zv -w 3 host.docker.internal 22"

echo; echo "=== 4. docker socket ==="
blocked "/var/run/docker.sock exists"              "test -e /var/run/docker.sock"
blocked "/run/docker.sock exists"                  "test -e /run/docker.sock"
blocked "docker API over unix socket"              "curl -sS -m 3 --unix-socket /var/run/docker.sock http://localhost/version"
blocked "docker API over tcp 2375 on lab host IP"       "curl -sS -m 3 http://$HUBIP:2375/version"
blocked "no docker binary"                         "command -v docker"
blocked "no socket-like file anywhere under /"     "find / -xdev -type s 2>/dev/null | grep -q ."

echo; echo "=== 5. other sandboxes ==="
for o in 1 2 3; do
  [ "$o" = "$A" ] && continue
  blocked "ICMP to d-agent$o (172.30.1$o.1)"       "ping -c1 -W2 172.30.1$o.1"
  blocked "TCP to d-agent$o 172.30.1$o.1:22/8080"  "nc -zv -w 2 172.30.1$o.1 22; nc -zv -w 2 172.30.1$o.1 8080"
  blocked "name lookup d-agent$o"                  "getent hosts d-agent$o"
done
blocked "arp/neighbor table lists no other sandbox" "ip neigh | grep -E '172\.30\.1[0-9]\.' | grep -v 172.30.1$A."

echo; echo "=== 6. lab host files and host namespace ==="
blocked "/home/<user> visible"                 "test -e /home/<user>"
blocked "~/.aws visible"                           "find / -xdev -name .aws -o -name credentials 2>/dev/null | grep -q ."
blocked "no host-looking mounts (bind from /home,/root,/etc)" "grep -E ' /(home/<user>|root|host|mnt|labhost) ' /proc/self/mountinfo"
expect  "only docker's own 3 file mounts reference /var/lib/docker" "awk '/var\\/lib\\/docker/{print \$5}' /proc/self/mountinfo | sort | tr '\\n' ' '" "^/etc/hostname /etc/hosts /etc/resolv.conf $"
blocked "write to rootfs"                          "touch /usr/x 2>/dev/null"
expect  "rootfs is read-only"                      "grep -E ' / ' /proc/self/mountinfo" " ro[, ]"
expect  "only expected tmpfs mounts"               "awk '\$9==\"tmpfs\"{print \$5}' /proc/self/mountinfo | sort | tr '\n' ' '" "/home/agent"
blocked "lab host hostname / pid namespace: sees PID of dockerd" "pgrep -x dockerd"
blocked "can see host processes (cloudflared, opencode, node)" "pgrep -x 'cloudflared|opencode.exe|node-MainThread'"
blocked "can read /proc/1/root of host"            "test -e /proc/1/root/home/<user>"

echo; echo "=== 7. privileges and limits ==="
expect  "runs as uid 20001"                        "id -u" "^20001$"
blocked "sudo present"                             "command -v sudo"
blocked "su to root with empty password"           "echo | su -c id root"
expect  "CapEff all zero"                          "grep CapEff /proc/self/status" "0000000000000000"
expect  "CapBnd all zero"                          "grep CapBnd /proc/self/status" "0000000000000000"
expect  "NoNewPrivs=1"                             "grep NoNewPrivs /proc/self/status" "1"
expect  "Seccomp mode 2 (filter)"                  "grep ^Seccomp: /proc/self/status" "2"
blocked "create network interface"                 "ip link add dummy0 type dummy"
blocked "mount a filesystem"                       "mount -t tmpfs none /mnt"
blocked "unshare user namespace + net"             "unshare -Urn true"
blocked "chroot"                                   "chroot / true"
blocked "ptrace PID 1"                             "python3 -c \"import ctypes,sys;l=ctypes.CDLL(None);sys.exit(0 if l.ptrace(16,1,0,0)==0 else 1)\""
expect  "memory limit 2 GiB"                       "cat /sys/fs/cgroup/memory.max" "^2147483648$"
expect  "swap limit 0 (memswap = mem)"             "cat /sys/fs/cgroup/memory.swap.max" "^0$"
expect  "cpu limit 1 CPU"                          "cat /sys/fs/cgroup/cpu.max" "^100000 100000$"
expect  "pids limit 256"                           "cat /sys/fs/cgroup/pids.max" "^256$"
blocked "memory bomb 3 GiB survives"               "python3 -c 'b=bytearray(3*1024**3); b[::4096]=b\"x\"*len(b[::4096]); print(len(b))'"
blocked "fork bomb of 400 sleeps survives"         "for i in \$(seq 400); do sleep 20 & done 2>&1 | head -1; test \$(pgrep -c sleep) -ge 300"
inbox "pkill -f 'sleep 20'" >/dev/null
blocked "write 2 GiB to home (tmpfs 1g)"           "dd if=/dev/zero of=/home/agent/big bs=1M count=2048 2>&1; test \$(stat -c %s /home/agent/big) -ge 2147483648"
inbox "rm -f /home/agent/big" >/dev/null

echo; echo "=== summary: $SB PASS=$PASS FAIL=$FAIL ==="
echo "--- docker inspect HostConfig for $SB"
docker inspect "$SB" --format 'Privileged={{.HostConfig.Privileged}} CapDrop={{.HostConfig.CapDrop}} CapAdd={{.HostConfig.CapAdd}} SecurityOpt={{.HostConfig.SecurityOpt}} ReadonlyRootfs={{.HostConfig.ReadonlyRootfs}} Mem={{.HostConfig.Memory}} MemSwap={{.HostConfig.MemorySwap}} NanoCpus={{.HostConfig.NanoCpus}} PidsLimit={{.HostConfig.PidsLimit}} User={{.Config.User}} NetworkMode={{.HostConfig.NetworkMode}} Binds={{.HostConfig.Binds}} PidMode={{.HostConfig.PidMode}} IpcMode={{.HostConfig.IpcMode}}'
[ $FAIL -eq 0 ]
