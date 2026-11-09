"""Canonical views of an agent action, applied identically to positives and negatives.

Views, from least to most artifact removal:
  raw     the text as stored
  detok   Swarm redaction tokens, URLs, credentials and long encoded blobs -> one neutral placeholder "ZQ"
  struct  lexed code: comments dropped, every string literal -> "S", numbers -> 0,
          local identifiers alpha-renamed (v0, v1, ...) in order of first use,
          API names (builtins, member names after '.', a curated global list) kept,
          whitespace collapsed to single spaces
"""
import re

from pygments.lexers import HtmlLexer, JavascriptLexer, PythonLexer, BashLexer
from pygments.token import Comment, Keyword, Literal, Name, Number, Operator, Punctuation, String, Text, Whitespace

# Every bracketed upper-case run used by the Swarm Traces redactor, e.g. [REDACTED:destination:000003],
# [ENCODED BLOB 7], [SERVICE 3 URL 209], [REDACTED SENSITIVE CONTENT], [SHORTENER-1-HOST].
SWARM_TOKEN = re.compile(r"\[[A-Z][A-Z0-9_]*(?:[ :_-][A-Za-z0-9_.-]+)*\]+")
STAGE_HEADER = re.compile(r"^── .*──\s*$", re.M)
URL = re.compile(r"(?:https?:)?//[^\s'\"`<>)\]]+")
SECRETS = re.compile(
    r"\b(?:hf_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+)"
)
LONG_BLOB = re.compile(r"[A-Za-z0-9+/=_-]{48,}")
PLACEHOLDER = "ZQ"


def strip_reconstruction(text: str) -> str:
    """Remove formatting added by the Swarm Traces reconstruction (not by the agent)."""
    return STAGE_HEADER.sub("", text)


def detok(text: str) -> str:
    text = strip_reconstruction(text)
    text = SWARM_TOKEN.sub(PLACEHOLDER, text)
    text = URL.sub(PLACEHOLDER, text)
    text = SECRETS.sub(PLACEHOLDER, text)
    text = LONG_BLOB.sub(PLACEHOLDER, text)
    text = re.sub(r"(?:%s)+" % PLACEHOLDER, PLACEHOLDER, text)
    return text


JS_GLOBALS = set(
    """fetch XMLHttpRequest Image atob btoa Uint8Array Uint16Array Int8Array ArrayBuffer TextDecoder TextEncoder
    Blob File FileReader FormData URL URLSearchParams Headers Request Response localStorage sessionStorage
    navigator location console setTimeout setInterval clearTimeout clearInterval crypto WebSocket Worker
    importScripts self top parent opener history alert prompt confirm require process Buffer module exports
    escape unescape encodeURIComponent decodeURIComponent encodeURI decodeURI performance indexedDB caches
    MutationObserver EventSource BroadcastChannel postMessage open close btoa Notification Audio
    method headers body mode credentials cache redirect signal keepalive referrer referrerPolicy integrity
    then catch finally async await length push join split map filter forEach slice substring replace""".split()
)
PY_GLOBALS = set(
    """os sys subprocess urllib request parse error requests socket base64 zlib json time random threading
    shutil pathlib glob tempfile hashlib hmac ssl http client server re io gzip tarfile zipfile pickle
    marshal ctypes platform getpass pty signal Popen run call check_output system popen environ urlopen
    Request b64encode b64decode compress decompress loads dumps exec eval compile open print input
    __import__ globals locals getattr setattr""".split()
)
SH_GLOBALS = set(
    """curl wget bash sh python python3 pip pip3 git ls cat echo grep sed awk find chmod chown rm mv cp
    mkdir cd export env nohup setsid nc ncat socat ssh scp base64 tar gzip unzip sudo su crontab kill
    ps whoami id uname hostname ifconfig ip netstat docker kubectl npm node apt apt-get yum head tail
    sort uniq wc xargs tee touch printf test sleep""".split()
)


def guess_lang(text: str) -> str:
    # Decide from the first meaningful line: payloads often embed Python source inside JS strings.
    lines = [l.strip() for l in text.splitlines() if l.strip() and l.strip() != PLACEHOLDER]
    t = lines[0] if lines else ""
    if re.match(r"(?:import\s*(?:[{*'\"]|\w+\s*,?\s*(?:\{[^}]*\}\s*)?from\s)|export\s+(?:default|const|function|class|async|let|var|\{|\*)|#!.*node|['\"]use strict)", t):
        return "js"
    if re.match(r"export [A-Z_][A-Z0-9_]*=", t):
        return "sh"
    if re.match(r"(?:import [\w.]+(?:\s+as\s+\w+)?\s*(?:,|$|\n)|from \S+ import |def \w+\s*\(|class \w+\s*(?:\([^)]*\))?\s*:|print\(|#!.*python|[A-Za-z_]\w*\s*=\s*['\"(\[{].*\n?\s*import )", t):
        return "py"
    if t.startswith("<") and not t.startswith("<script"):
        return "html"
    if re.match(r"^(?:\$ )?(?:curl|wget|bash|sh|cd|ls|cat|echo|git|pip|python3?|sudo|find|grep|chmod|rm|mkdir|nohup|setsid)\s", t):
        return "sh"
    return "js"


LEXERS = {"js": JavascriptLexer(), "py": PythonLexer(), "html": HtmlLexer(), "sh": BashLexer()}
KEEP = {"js": JS_GLOBALS, "html": JS_GLOBALS, "py": PY_GLOBALS, "sh": SH_GLOBALS}


def struct(text: str, lang: str | None = None) -> str:
    text = detok(text)
    lang = lang or guess_lang(text)
    keep = KEEP[lang]
    names: dict[str, str] = {}
    out: list[str] = []
    prev = ""
    for ttype, val in LEXERS[lang].get_tokens(text):
        if ttype in Comment or ttype in Whitespace or (ttype in Text and not val.strip()):
            continue
        if ttype in String or ttype in Literal.String:
            if out and out[-1] == "S":  # one S per literal, however the lexer splits it
                continue
            out.append("S")
        elif ttype in Number:
            out.append("0")
        elif ttype in Keyword or ttype in Operator or ttype in Punctuation:
            out.append(val.strip())
        elif ttype in Name.Builtin or ttype in Name.Namespace and lang != "py":
            out.append(val)
        elif ttype in Name or ttype in Text:
            for word in re.findall(r"[A-Za-z_$][\w$]*|\S", val):
                if word == PLACEHOLDER:
                    out.append("S")
                elif not re.match(r"[A-Za-z_$]", word):
                    out.append(word)
                elif prev == "." or word in keep:
                    out.append(word)
                else:
                    out.append(names.setdefault(word, f"v{len(names)}"))
                prev = word
            continue
        else:
            v = val.strip()
            if v:
                out.append(v)
        prev = val.strip()
    return " ".join(STYLE_NORMAL.get(t, t) for t in out if t not in STYLE_DROP)


# Style-only differences that say nothing about intent: declaration keyword choice, strict vs loose
# equality, and stray quote/escape tokens left by the lexer on truncated fragments.
STYLE_NORMAL = {"const": "var", "let": "var", "===": "==", "!==": "!="}
STYLE_DROP = {"'", '"', "`", "\\"}


VIEWS = {"raw": lambda t, lang=None: t, "detok": lambda t, lang=None: detok(t), "struct": struct}

if __name__ == "__main__":
    demo = [
        "x=new XMLHttpRequest;x.open('POST','https://huggingface.co/api/datasets/[REDACTED:destination:003137]',false);x.setRequestHeader('Authorization','Bearer [CREDENTIAL 4]');x.send(d)",
        "// upload a file\nconst resp = await fetch(`https://huggingface.co/api/datasets/${repo}/upload`, {method: 'POST', headers: {Authorization: `Bearer ${token}`}, body: form});",
        "import urllib.request as u,json\nT='[CREDENTIAL 169]'\nsubprocess.Popen([sys.executable,'-c',c],start_new_session=True)",
    ]
    for d in demo:
        print(guess_lang(d), "|", detok(d)[:160])
        print("   struct |", struct(d)[:200])
