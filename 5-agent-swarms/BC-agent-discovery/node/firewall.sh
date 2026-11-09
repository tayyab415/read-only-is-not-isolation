#!/bin/bash
# BC host firewall: a slot network may reach only its own gateway port on the host.
# Idempotent; run at boot after docker (bc-firewall.service) and by provision/fleet_init.
set -eu
for s in 1 2; do
  ip -4 addr show bcbr$s | grep -q "10.201.$s.1" || { echo "bcbr$s has no gateway IP" >&2; exit 1; }
done
iptables -C INPUT -s 10.201.0.0/16 -j DROP 2>/dev/null || {
  iptables -I INPUT 1 -s 10.201.0.0/16 -j DROP
  iptables -I INPUT 1 -s 10.201.2.0/24 -d 10.201.2.1 -p tcp --dport 9000 -j ACCEPT
  iptables -I INPUT 1 -s 10.201.1.0/24 -d 10.201.1.1 -p tcp --dport 9000 -j ACCEPT
}
iptables -C DOCKER-USER -s 10.201.0.0/16 -j DROP 2>/dev/null || iptables -I DOCKER-USER 1 -s 10.201.0.0/16 -j DROP
iptables -S INPUT | grep 10.201; iptables -S DOCKER-USER | grep 10.201
