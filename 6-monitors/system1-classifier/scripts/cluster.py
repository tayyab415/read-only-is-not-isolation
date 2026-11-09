"""Near-duplicate clusters over struct views, for group-aware splits.

Exact struct duplicates share a cluster; MinHash LSH (Jaccard >= THRESH on struct 3-gram shingles)
merges near-duplicates with union-find. Splits must keep a cluster on one side.
"""
import hashlib

from datasketch import MinHash, MinHashLSH

THRESH = 0.8
PERMS = 64


def _shingles(s: str, n: int = 3):
    toks = s.split()
    if len(toks) < n:
        return {" ".join(toks)}
    return {" ".join(toks[i : i + n]) for i in range(len(toks) - n + 1)}


def clusters(structs: list[str], prefix: str) -> list[str]:
    parent = list(range(len(structs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    first_by_hash: dict[str, int] = {}
    reps = []
    for i, s in enumerate(structs):
        h = hashlib.sha1(s.encode()).hexdigest()
        if h in first_by_hash:
            union(first_by_hash[h], i)
        else:
            first_by_hash[h] = i
            reps.append(i)

    lsh = MinHashLSH(threshold=THRESH, num_perm=PERMS)
    for i in reps:
        m = MinHash(num_perm=PERMS)
        for sh in _shingles(structs[i]):
            m.update(sh.encode())
        for j in lsh.query(m):
            union(i, int(j))
        lsh.insert(str(i), m)

    return [f"{prefix}{find(i)}" for i in range(len(structs))]
