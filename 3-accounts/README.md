# 3. Accounts

Each sandbox gets its own Artifactory account with Read on its own repository only, and credentials reach the client by stdin. Separate repositories stop a sender from touching a receiver's state, but a client that holds a copy of a peer's password can read the peer's repository. This section has the identity test, the grant behaviour of Read-only and Read + Deploy/Cache accounts, and the credential-handling checks. Paper Section 4.2 is the main result.

| Folder | Question | Result |
| --- | --- | --- |
| [J-identity-origin-binding](J-identity-origin-binding/) | Does binding each sandbox's network origin to one account stop a copied peer password? | Without binding, 16 of 16 copied-credential reads succeeded. With binding, 16 of 16 were rejected with 403 before forwarding, and 16 paired ordinary downloads returned identical bytes. |
| [account-grants](account-grants/) | What can a Read-only account do compared with Read + Deploy/Cache? | Read-only: uncached fetch 403, MKCOL 403. Read + Deploy/Cache: fetch 200, MKCOL 201 in the cache, DELETE 403. |
| [credential-handling](credential-handling/) | How do credentials get into clients, and how was the exported evidence checked? | stdin delivery in J. Scans of exported evidence found no known credential: J 2,118 files, H 460 files, K 43 files. |

The identity test assumes a trusted host, static addresses and unprivileged clients. It does not cover NAT, address reuse or privileged clients.
