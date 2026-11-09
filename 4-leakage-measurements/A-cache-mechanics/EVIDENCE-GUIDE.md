# A evidence

Tests ran on the owned labhost instance `art-a`, using Artifactory OSS 7.161.26. Times in the transcripts and server logs are UTC.

- `requests.jsonl` records each request's identity, method, prepared URL and path, headers, body, status, response headers, response size, SHA-256, and full response duration. Authorization headers and generated secrets are redacted.
- `bodies/<request-id>.txt` contains the response body. Empty files are intentional for empty HTTP responses.
- `log-matches/<request-id>.json` lists matching lines and their source line numbers in every collected log file. An empty array means no matching line was found in that file.
- `log-match-index.json` provides the compact correlation index.
- `logs/` contains sanitized snapshots of the 71 log files named in `log-inventory.json`. The files came from A's `var/log`, including its `tomcat` subdirectory. No C or D log files were collected.
- `database/` contains the exact read-only PostgreSQL commands and results used to check creator fields, name limits, and missing paths.
- `bulk-summary.json` contains the 3,000-folder measurements. `time_to_headers_s` measures receipt of headers; `full_response_duration_s` includes downloading the response body.
- `chunk-roundtrip.json` compares the original and reconstructed file hashes after transport through four folder names.
- `response-byte-audit.json` verifies response-body hashes. Two early responses initially used the HTTP library's guessed text encoding; their original bytes were recovered and verified against the SHA-256 recorded at request time.
- `secret-audit.json` checks that the generated passwords, tokens, and server keys do not occur in the copied evidence.
- `image.txt`, `container-hostconfig.json`, `java-processes.txt`, `running-state.txt`, and `READY` describe the instance without credentials.
- `scripts/` contains the recorder, installation steps, tests, collection, and audit scripts. `start.sh` preserves the first installation attempt; `database.py` adds PostgreSQL and server keys after that attempt failed. Do not rerun installation scripts against a running instance.

The successful administration interfaces used by this OSS build are `/artifactory/ui/users`, `/artifactory/ui/permissiontargets`, `/artifactory/ui/securityconfig`, and `/artifactory/ui/admin/repositories`. In that UI API, Deploy/Cache is spelled `upload`. Read back the effective permission target after changing it. The legacy administration REST APIs returned a Pro-only error.

The first administrator password change succeeded through `PUT /ui/users/admin` using the built-in default login. `secure-admin-ui` repeats the operation with the changed login and records its response. `old-default-password-denied` records the default login returning 401. Credentials remain only at the path named in `READY` on the labhost, with mode 600.

The requested trace header did not determine the server trace ID. Correlation uses the recorded User-Agent containing the unique request ID to find the server-generated trace in `artifactory-request.log` or `router-request.log`, then follows that trace across the collected files. User-Agent is a lab correlation aid, not an authenticated identity.

The router recorded the encoded-backslash rejection before it reached Artifactory's request logger. All recorded requests have at least a router or Artifactory request-log match.
