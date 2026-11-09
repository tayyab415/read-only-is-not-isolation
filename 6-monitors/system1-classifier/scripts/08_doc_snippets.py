"""Extract fenced code blocks from Markdown docs as benign one-shot snippets.

Keeps js/ts/python/bash blocks of 40-4000 chars. Marks `hf_api` when the block calls a huggingface.co
endpoint or the HF client with a token (the hard-negative subset).
Usage: python 08_doc_snippets.py ROOT_DIR OUT.jsonl SOURCE_NAME REPO_COMMIT_JSON
"""
import json
import pathlib
import re
import sys

root, out, source, commits = sys.argv[1], sys.argv[2], sys.argv[3], json.loads(sys.argv[4])
FENCE = re.compile(r"```(\w+)[^\n]*\n(.*?)```", re.S)
LANG = {"js": "js", "javascript": "js", "ts": "js", "typescript": "js", "py": "py", "python": "py", "bash": "sh", "sh": "sh", "shell": "sh", "html": "html"}
HF_API = re.compile(r"huggingface\.co/api|hf\.co/api|router\.huggingface|api-inference|HfApi\(|hf_hub_download|upload_file|create_repo|@huggingface/hub|Authorization|HF_TOKEN|hf_[A-Za-z]{4,}")
n = 0
with open(out, "w") as f:
    for p in sorted(pathlib.Path(root).rglob("*.md*")):
        rel = str(p.relative_to(root))
        repo = rel.split("/")[0]
        for i, (lang, code) in enumerate(FENCE.findall(p.read_text(errors="ignore"))):
            lang = LANG.get(lang.lower())
            code = code.strip()
            if not lang or not (40 <= len(code) <= 4000):
                continue
            if lang == "sh":  # drop prompt markers and pure install lines
                code = re.sub(r"^\$ ", "", code, flags=re.M)
                if re.fullmatch(r"(?:(?:pip|npm|pnpm|yarn|brew|uv) (?:install|add)[^\n]*\n?)+", code):
                    continue
            f.write(json.dumps({"id": f"{rel}#{i}", "source": source, "lang": lang, "raw": code, "hf_api": bool(HF_API.search(code)), "repo_commit": commits.get(repo)}) + "\n")
            n += 1
print(n, "blocks")
