"""Squid StoreID helper using its observed source address, not an HTTP header."""
import hashlib
import ipaddress
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit


def store_id(line, origins):
    parts = line.strip().split()
    if len(parts) != 2:
        raise ValueError("The trusted helper input format changed")
    url, address = parts
    ipaddress.ip_address(address)
    actor = origins.get(address)
    parts = urlsplit(url)
    if actor not in ("alice", "bob") or parts.scheme != "http" or parts.port != 8000 \
            or not parts.hostname or not parts.hostname.startswith("p7-") or not parts.hostname.endswith("-origin") \
            or parts.username or parts.password or parts.fragment:
        raise ValueError("Helper input leaves its trusted fixture")
    digest = hashlib.sha256(url.encode()).hexdigest()
    return {"url": url, "client_ip": address, "actor": actor,
            "store_id": f"http://p7-key.invalid/{actor}/{digest}"}


def main():
    origins = json.loads(Path(sys.argv[1]).read_text())
    path = Path(sys.argv[2])
    with path.open("x") as stream:
        for line in sys.stdin:
            try:
                row = store_id(line, origins)
            except Exception as exc:
                stream.write(json.dumps({"input": line.strip(), "failure_type": type(exc).__name__}) + "\n")
                stream.flush()
                # Never return ERR's original-URL fallback. This failure stops
                # the cohort; no isolation result may use requests after it.
                print("BH message=trusted_mapping_failed", flush=True)
                raise SystemExit(2)
            stream.write(json.dumps(row) + "\n")
            stream.flush()
            print('OK store-id="' + row["store_id"] + '"', flush=True)


if __name__ == "__main__":
    main()
