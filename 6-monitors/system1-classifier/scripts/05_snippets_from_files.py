"""Cut real source files into statement-sized snippets (one top-level statement or function each).

Used for: local benign JS from installed node packages (smoke test and a held-out benign source).
Usage: python 05_snippets_from_files.py ROOT_DIR OUT.jsonl SOURCE_NAME [--glob '*.js'] [--max 20000]
"""
import argparse, json, pathlib, random, re

ap = argparse.ArgumentParser()
ap.add_argument("root"); ap.add_argument("out"); ap.add_argument("source")
ap.add_argument("--glob", default="*.js"); ap.add_argument("--max", type=int, default=20000)
ap.add_argument("--seed", type=int, default=0)
a = ap.parse_args()
random.seed(a.seed)
files = [p for p in pathlib.Path(a.root).rglob(a.glob) if p.is_file() and p.stat().st_size < 400_000 and ".min." not in p.name]
random.shuffle(files)
n = 0
with open(a.out, "w") as f:
    for p in files:
        try:
            text = p.read_text(errors="ignore")
        except Exception:
            continue
        # split at blank lines that precede a top-level line (column 0), keep 40-2000 char chunks
        chunks = re.split(r"\n\s*\n(?=\S)", text)
        for i, c in enumerate(chunks):
            c = c.strip()
            if 40 <= len(c) <= 2000 and not c.startswith(("/*", "//", "*")):
                f.write(json.dumps({"id": f"{p.name}:{i}", "file": str(p), "source": a.source, "raw": c}) + "\n")
                n += 1
                if n >= a.max:
                    break
        if n >= a.max:
            break
print(n, "snippets from", len(files), "files")
