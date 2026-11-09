"""Explicit owned cache configurations. No deployment on import."""
import ipaddress
import re

PRODUCTS = ("nginx", "squid")
ARMS = ("shared", "tenant-keyed", "private", "disabled")


def validate(product, arm, origins, origin_host):
    if product not in PRODUCTS or arm not in ARMS:
        raise ValueError("Unknown cache comparison")
    if not origins or set(origins.values()) - {"alice", "bob"}:
        raise ValueError("Missing trusted client origins")
    for address in origins:
        if ipaddress.ip_address(address).version != 4:
            raise ValueError("This fixture uses IPv4")
    if not re.fullmatch(r"p7-[a-z0-9-]+-origin", origin_host):
        raise ValueError("Origin is not an owned P7 fixture")


def nginx(arm, origins, origin_host):
    validate("nginx", arm, origins, origin_host)
    mapping = "\n".join(f"    {address} {actor};" for address, actor in origins.items())
    allowed = "\n".join(f"    allow {address};" for address in origins)
    key = "p7:$tenant:$scheme:$proxy_host:$request_uri" if arm == "tenant-keyed" \
        else "p7:$scheme:$proxy_host:$request_uri"
    cache = "off" if arm == "disabled" else "fixture"
    return f"""worker_processes 1;
pid /tmp/nginx.pid;
error_log /tmp/nginx-error.log info;
events {{ worker_connections 128; }}
http {{
  client_body_temp_path /tmp/client-body;
  proxy_temp_path /tmp/proxy-temp;
  proxy_cache_path /tmp/cache levels=1:2 keys_zone=fixture:1m inactive=120s max_size=16m;
  map $remote_addr $tenant {{
    default "";
{mapping}
  }}
  log_format evidence escape=json '{{"end_epoch_s":$msec,"client_ip":"$remote_addr","actor":"$tenant","method":"$request_method","path":"$request_uri","marker":"$http_user_agent","status":$status,"body_bytes":$body_bytes_sent,"cache_status":"$upstream_cache_status","request_time_s":"$request_time","upstream_time_s":"$upstream_response_time","upstream_status":"$upstream_status"}}';
  access_log /tmp/cache-access.jsonl evidence;
  server {{
    listen 8000;
{allowed}
    deny all;
    location / {{
      proxy_pass http://{origin_host}:8000;
      proxy_http_version 1.0;
      proxy_set_header Connection close;
      proxy_set_header Host {origin_host}:8000;
      proxy_set_header User-Agent $http_user_agent;
      proxy_cache {cache};
      proxy_cache_key "{key}";
      proxy_cache_valid 200 60s;
      proxy_cache_valid 404 60s;
      proxy_cache_min_uses 1;
      proxy_cache_lock off;
      proxy_cache_background_update off;
      proxy_cache_use_stale off;
      proxy_cache_revalidate off;
      proxy_cache_convert_head on;
      proxy_force_ranges on;
      proxy_next_upstream off;
      proxy_connect_timeout 3s;
      proxy_read_timeout 3s;
      keepalive_timeout 0;
    }}
  }}
}}
"""


def squid(arm, origins, origin_host):
    validate("squid", arm, origins, origin_host)
    addresses = " ".join(origins)
    helper = ""
    if arm == "tenant-keyed":
        helper = """store_id_program /usr/local/bin/python3 /store_id.py /config/origins.json /tmp/store-id.jsonl
store_id_extras "%>a"
store_id_children 1 startup=1 idle=1 concurrency=0
store_id_bypass off
store_id_access allow owned_clients
store_id_access deny all
"""
    cache_policy = "deny" if arm == "disabled" else "allow"
    return f"""http_port 8000
visible_hostname p7-owned-cache
pid_filename /tmp/squid.pid
coredump_dir /tmp
cache_effective_user research
cache_effective_group research
workers 1
acl owned_clients src {addresses}
acl owned_origin dstdomain {origin_host}
acl owned_port port 8000
acl permitted_methods method GET HEAD
http_access allow owned_clients owned_origin owned_port permitted_methods
http_access deny all
cache {cache_policy} all
cache_mem 4 MB
maximum_object_size_in_memory 64 KB
cache_dir ufs /tmp/cache 16 4 16
maximum_object_size 64 KB
negative_ttl 60 seconds
collapsed_forwarding off
offline_mode off
client_persistent_connections off
server_persistent_connections off
forward_max_tries 1
retry_on_error off
uri_whitespace deny
refresh_pattern . 0 0% 0
range_offset_limit none
shutdown_lifetime 0 seconds
cache_log /tmp/squid-cache.log
cache_store_log none
logformat evidence %ts.%03tu %>a %rm %ru %>Hs %Ss %tr %{{User-Agent}}>h
access_log stdio:/tmp/cache-access.txt evidence
buffered_logs off
{helper}"""
