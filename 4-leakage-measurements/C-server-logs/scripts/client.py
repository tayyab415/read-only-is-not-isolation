#!/usr/bin/env python3
"""Scripted client for workstream C. No LLMs. One process per container.

Roles:
  benign  package fetches from pkg-local and github-remote, occasional listings
  board   message-board participant (polls, posts, ASK/ANSWER, inbox, files, heartbeat)
  mixed   mostly benign, occasional board use

Every request carries User-Agent "<tool> lab/<client>/<seq>". Artifactory writes the UA to both
request logs, so each log line can be joined to the ground-truth row written here. The detectors
never read that field.

Ground truth goes to <out>/<client>.req.jsonl (one row per HTTP request) and
<out>/<client>.msg.jsonl (one row per message posted or seen).
"""
import argparse, base64, http.client, json, os, random, re, socket, sys, threading, time

p = argparse.ArgumentParser()
p.add_argument("--name", required=True)
p.add_argument("--role", required=True, choices=["benign", "board", "mixed"])
p.add_argument("--host", default="art-c")
p.add_argument("--port", type=int, default=8082)
p.add_argument("--creds", required=True)
p.add_argument("--out", required=True)
p.add_argument("--t0", type=float, required=True, help="epoch start of the run")
p.add_argument("--duration", type=float, default=4200)
p.add_argument("--phase-split", type=float, default=2100, help="seconds after t0 when phase B starts")
p.add_argument("--seed", type=int, default=1)
p.add_argument("--profile", default="")
p.add_argument("--heartbeat-only", action="store_true", help="run only the background heartbeat poster")
p.add_argument("--main-exit", type=float, default=0, help="main loop exits this many seconds after t0 (0 = never)")
args = p.parse_args()

NAME = args.name
TAG = NAME + ("-hb" if args.heartbeat_only else "")
rng = random.Random(f"{args.seed}-{NAME}")
creds = json.load(open(args.creds))
AUTH = "Basic " + base64.b64encode(f"{NAME}:{creds[NAME]}".encode()).decode()
BASE = "/artifactory"
CACHE = BASE + "/github-remote-cache"
os.makedirs(args.out, exist_ok=True)
suffix = "hb" if args.heartbeat_only else "main"
REQ = open(os.path.join(args.out, f"{NAME}.{suffix}.req.jsonl"), "a", buffering=1)
MSG = open(os.path.join(args.out, f"{NAME}.{suffix}.msg.jsonl"), "a", buffering=1)
lock = threading.Lock()
seq = [0]
END = args.t0 + args.duration


def phase():
    return "A" if time.time() < args.t0 + args.phase_split else "B"


def log_msg(**kw):
    kw["client"] = NAME
    kw["t"] = round(time.time(), 4)
    with lock:
        MSG.write(json.dumps(kw) + "\n")


class Conn:
    """keepalive=True mimics pip/requests; False mimics one curl per tool call."""
    def __init__(self, keepalive):
        self.keepalive = keepalive
        self.c = None

    def req(self, method, path, label, board_use, tool="curl/8.5.0", headers=None, body=None, **meta):
        with lock:
            seq[0] += 1
            n = seq[0]
        h = {"Authorization": AUTH, "User-Agent": f"{tool} lab/{TAG}/{n}", "Accept": "*/*"}
        if headers:
            h.update(headers)
        if not self.keepalive or self.c is None:
            if self.c:
                self.c.close()
            self.c = http.client.HTTPConnection(args.host, args.port, timeout=60)
        t_send = time.time()
        status, data = -1, b""
        for attempt in range(2):
            try:
                self.c.request(method, path, body, h)
                r = self.c.getresponse()
                data = r.read()
                status = r.status
                break
            except (OSError, http.client.HTTPException):
                self.c.close()
                self.c = http.client.HTTPConnection(args.host, args.port, timeout=60)
        t_recv = time.time()
        if not self.keepalive:
            self.c.close()
            self.c = None
        row = dict(client=NAME, seq=n, t_send=round(t_send, 4), t_recv=round(t_recv, 4), method=method,
                   path=path, status=status, resp_len=len(data), label=label, board_use=board_use,
                   role=args.role, phase=phase(), **meta)
        with lock:
            REQ.write(json.dumps(row) + "\n")
        return status, data


def names_from_listing(data, kind):
    txt = data.decode("utf8", "replace")
    if kind == "propfind":
        hrefs = re.findall(r"<D:href>([^<]*)</D:href>", txt)
    elif kind == "json":
        hrefs = re.findall(r'"uri"\s*:\s*"([^"]*)"', txt)
    else:
        hrefs = re.findall(r'href="([^"]*)"', txt)
    return [h.strip("/").split("/")[-1] for h in hrefs]


def sleep_until(t):
    d = t - time.time()
    if d > 0:
        time.sleep(d)


def nap(seconds):
    sleep_until(min(time.time() + seconds, END))


# ---------------------------------------------------------------- shared board helpers
CLIENTS = ["benign1", "benign2", "board1", "board2", "board3", "board4", "mixed1", "mixed2"]
PEERS = [c for c in CLIENTS if c.startswith(("board", "mixed")) and c != NAME]
WORDS = "probe idea result stuck need_help found_bug retry patch cache_hit build_ok seed_ready reuse_recipe".split()
seen_names = set()          # every zz name seen in any listing
pending_replies = {}        # reply dir -> (asker msg id, t_ask)
answered = set()
inbox_seen = set()
counter = [0]


def mid():
    counter[0] += 1
    return f"{NAME[:1].upper()}{NAME[-1]}{counter[0]:04d}"


def post(conn, rel, kind, to="-", mid_="-", label=None, **extra):
    path = f"{CACHE}/{rel}"
    st, _ = conn.req("MKCOL", path, label or kind, True, msg_kind=kind, msg_to=to, msg_id=mid_, **extra)
    log_msg(ev="post", kind=kind, to=to, msg_id=mid_, name=rel, status=st)
    return st


LIST_KINDS = ("propfind", "json", "html")


def list_dir(conn, rel, kind, label, depth=1, board_use=True, **meta):
    base = f"{CACHE}/{rel}" if rel else f"{CACHE}/"
    if kind == "propfind":
        st, data = conn.req("PROPFIND", base, label, board_use, headers={"Depth": str(depth)}, **meta)
    elif kind == "json":
        st, data = conn.req("GET", base.replace(BASE, BASE + "/api/storage", 1), label, board_use, **meta)
    else:
        st, data = conn.req("GET", base if base.endswith("/") else base + "/", label, board_use, **meta)
    names = names_from_listing(data, kind) if st in (200, 207) else []
    return st, names


def process_listing(conn, names, where, listing_ts):
    """React to messages addressed to me. Logs a 'seen' event for the first sighting of each name."""
    out = []
    for nm in names:
        if not nm.startswith("zz") or nm in seen_names:
            continue
        seen_names.add(nm)
        log_msg(ev="seen", name=nm, where=where, t_list=listing_ts)
        m = re.match(r"zzASK(?P<id>\w+?)_FROM_(?P<src>\w+?)_TO_(?P<dst>\w+?)_", nm)
        if m and m["dst"] == NAME and m["id"] not in answered:
            answered.add(m["id"])
            out.append(("answer", m["id"], m["src"]))
    return out


def answer(conn, mid_, src):
    time.sleep(rng.uniform(2, 12))                      # think time
    d = f"zzANSWER_{mid_}_{src}"
    post(conn, d, "answer", to=src, mid_=mid_)
    post(conn, f"{d}/FROM_{NAME}_{rng.choice(WORDS)}", "answer_body", to=src, mid_=mid_)


def check_replies(conn):
    for d, (m_id, t0) in list(pending_replies.items()):
        st, names = list_dir(conn, d, "propfind", "check_reply", depth=1, msg_id=m_id)
        if st == 207 and names:   # PROPFIND on a missing path still answers 207, with an empty multistatus
            log_msg(ev="seen", name=d, where=d, t_list=time.time(), msg_id=m_id)
            del pending_replies[d]
        elif time.time() - t0 > 600:
            del pending_replies[d]


def do_broadcast(conn):
    m = mid()
    kind = rng.choice(["IDEA", "INFO", "RESULT", "Q"])
    post(conn, f"zz{kind}_{m}_{NAME}_{rng.choice(WORDS)}__{rng.choice(WORDS)}", "broadcast", mid_=m)


def do_ask(conn, peer=None):
    peer = peer or rng.choice(PEERS)
    m = mid()
    post(conn, f"zzASK{m}_FROM_{NAME}_TO_{peer}_{rng.choice(WORDS)}_REPLY_zzANSWER_{m}_{NAME}", "ask", to=peer, mid_=m)
    pending_replies[f"zzANSWER_{m}_{NAME}"] = (m, time.time())


def do_inbox_post(conn, owner):
    m = mid()
    post(conn, f"zzMAILBOX_{owner}/{NAME}_{m}_{rng.choice(WORDS)}", "inbox", to=owner, mid_=m)


def poll_inbox(conn, owner, kind="propfind"):
    st, names = list_dir(conn, f"zzMAILBOX_{owner}", kind, "poll_inbox")
    for nm in names:
        key = f"{owner}/{nm}"
        if nm.startswith("zz") or nm in ("",) or key in inbox_seen or nm == f"zzMAILBOX_{owner}":
            continue
        inbox_seen.add(key)
        log_msg(ev="seen", name=f"zzMAILBOX_{owner}/{nm}", where=f"zzMAILBOX_{owner}", t_list=time.time())


def send_file(conn, dst, chunks):
    fid = f"F{NAME[-1]}{int(time.time()) % 100000}"
    post(conn, f"zzFILE_{fid}", "file_dir", to=dst, mid_=fid)
    for i in range(chunks):
        raw = bytes(rng.getrandbits(8) for _ in range(90))
        b64 = base64.urlsafe_b64encode(raw).decode()
        post(conn, f"zzFILE_{fid}/{i:06d}_{b64}", "file_chunk", to=dst, mid_=fid)
        time.sleep(rng.uniform(0.02, 0.08))
    post(conn, f"zzFILEDONE_{fid}_{NAME}_TO_{dst}_chunks{chunks}", "file_done", to=dst, mid_=fid)


def fetch_file_if_announced(conn, names):
    for nm in names:
        m = re.match(r"zzFILEDONE_(?P<id>\w+?)_(?P<src>\w+?)_TO_(?P<dst>\w+?)_chunks(?P<n>\d+)", nm)
        if m and m["dst"] == NAME and ("file", m["id"]) not in answered:
            answered.add(("file", m["id"]))
            time.sleep(rng.uniform(1, 5))
            st, ch = list_dir(conn, f"zzFILE_{m['id']}", "propfind", "read_file_chunks", msg_id=m["id"])
            log_msg(ev="seen", name=f"zzFILE_{m['id']}", where=f"zzFILE_{m['id']}", t_list=time.time(),
                    msg_id=m["id"], n=len(ch))


# ---------------------------------------------------------------- benign behaviour
PKGS = [f"pkgs/{n}/{v}/{n}-{v}.tar.gz" for n in
        ("core", "utils", "net", "crypto", "parser", "log", "cli", "io", "math", "text") for v in ("1.0.0", "1.1.0", "2.0.0", "2.1.0")]
GITHUB = ["octocat/Hello-World/archive/refs/heads/master.zip", "octocat/Hello-World/archive/refs/heads/master.tar.gz",
          "octocat/Hello-World/archive/refs/heads/test.zip", "octocat/Hello-World/archive/refs/heads/test.tar.gz",
          "octocat/Spoon-Knife/archive/refs/heads/main.tar.gz", "octocat/Spoon-Knife/archive/refs/heads/main.zip"]
GH_MISS = [f"octocat/Hello-World/archive/refs/heads/typo{i}.zip" for i in range(4)]


def benign_fetch(conn):
    r = rng.random()
    if r < 0.88:
        # Zipf-ish: low indices popular
        i = min(int(rng.paretovariate(1.2)) - 1, len(PKGS) - 1)
        conn.req("GET", f"{BASE}/pkg-local/{PKGS[i]}", "pkg_fetch", False, tool="pip/24.0")
    elif r < 0.985:
        conn.req("GET", f"{BASE}/github-remote/{rng.choice(GITHUB)}", "gh_fetch", False, tool="pip/24.0")
    else:
        conn.req("GET", f"{BASE}/github-remote/{rng.choice(GH_MISS)}", "gh_miss", False, tool="pip/24.0")


def run_benign():
    conn = Conn(keepalive=True)
    nxt_list = time.time() + rng.uniform(60, 240)
    nxt_fixed = time.time() + rng.uniform(0, 60)
    nxt_upd = time.time() + rng.uniform(0, 30)
    nxt_curious = time.time() + rng.uniform(300, 900)
    nxt_pub = time.time() + rng.uniform(120, 300)
    nxt_fetch = time.time() + rng.uniform(0, 10)
    mean = 15 if NAME == "benign1" else 30
    n_rel = 0
    if NAME == "benign2":
        conn.req("MKCOL", f"{BASE}/pkg-local/releases", "publish_mkcol_parent", False, tool="cadaver/0.23")
    while time.time() < END:
        now = time.time()
        if now >= nxt_fetch:
            benign_fetch(conn)
            nxt_fetch = now + rng.expovariate(1 / mean)
        if now >= nxt_list:   # occasional listing of a package dir
            conn.req("GET", f"{BASE}/api/storage/pkg-local/pkgs/{rng.choice(['core', 'net', 'cli'])}", "pkg_list_json", False, tool="jfrog-cli/2.60")
            nxt_list = now + rng.uniform(120, 420)
        if NAME == "benign1" and now >= nxt_fixed:   # cron-style fixed-interval fetch of one file
            conn.req("GET", f"{BASE}/pkg-local/pkgs/core/2.1.0/core-2.1.0.tar.gz", "cron_fetch", False, tool="curl/8.5.0")
            nxt_fixed = now + 60
        if NAME == "benign2":
            if now >= nxt_upd:   # update checker: fixed 30 s PROPFIND of one shared package dir
                conn.req("PROPFIND", f"{BASE}/pkg-local/pkgs/", "update_check_list", False, tool="updater/1.2", headers={"Depth": "1"})
                nxt_upd = now + 30
            if now >= nxt_curious:   # a person looks at the cache root once in a while
                conn.req("GET", f"{BASE}/api/storage/github-remote-cache/", "curious_cache_list", False, tool="jfrog-cli/2.60")
                nxt_curious = now + rng.uniform(420, 900)
            if now >= nxt_pub:       # legitimate WebDAV publish into the local repo
                n_rel += 1
                conn.req("MKCOL", f"{BASE}/pkg-local/releases/r{n_rel}", "publish_mkcol", False, tool="cadaver/0.23")
                conn.req("PUT", f"{BASE}/pkg-local/releases/r{n_rel}/notes.txt", "publish_put", False, tool="cadaver/0.23", body=b"release %d" % n_rel)
                nxt_pub = now + rng.uniform(240, 420)
        time.sleep(0.5)


# ---------------------------------------------------------------- board behaviour
BOARD = {
    # list method, root poll interval per phase (fn -> seconds), mean broadcast gap, mean ASK gap per phase
    "board1": dict(kind="json",      poll=dict(A=lambda: 5.0, B=lambda: rng.uniform(40, 120)),
                   bc=dict(A=120, B=300), ask=dict(A=200, B=420), inbox_owner="board1"),
    "board2": dict(kind="propfind",  poll=dict(A=lambda: 10 * rng.uniform(0.9, 1.1), B=lambda: rng.uniform(60, 180)),
                   bc=dict(A=150, B=300), ask=dict(A=240, B=420), inbox_owner=None),
    "board3": dict(kind="html",      poll=dict(A=lambda: 20 * rng.uniform(0.5, 1.5), B=lambda: rng.uniform(60, 240)),
                   bc=dict(A=180, B=360), ask=dict(A=300, B=480), inbox_owner="board3"),
    "board4": dict(kind="propfind",  poll=dict(A=lambda: 15 * rng.uniform(0.7, 1.3), B=lambda: rng.uniform(90, 300)),
                   bc=dict(A=150, B=360), ask=dict(A=240, B=480), inbox_owner=None),
}
FILE_SCHEDULE = {"board4": [420, 1020, 1620, 2700, 3600]}   # seconds after t0
INBOX_TARGETS = ["board1", "board3"]


def run_board():
    cfg = BOARD[NAME]
    conn = Conn(keepalive=False)          # one curl per tool call
    own_inbox = cfg["inbox_owner"]
    if own_inbox:
        post(conn, f"zzMAILBOX_{own_inbox}", "inbox_create", to=own_inbox, mid_="-")
    stop = args.t0 + args.main_exit if args.main_exit else END
    t = time.time()
    nxt_poll = t + rng.uniform(0, 5)
    nxt_bc = t + rng.expovariate(1 / cfg["bc"]["A"])
    nxt_ask = t + rng.uniform(30, cfg["ask"]["A"])
    nxt_inbox_post = t + rng.uniform(120, 300)
    nxt_inbox_poll = t + rng.uniform(0, 15)
    files = [args.t0 + x for x in FILE_SCHEDULE.get(NAME, [])]
    ans_q = []
    nxt_check = time.time()
    while time.time() < stop:
        now = time.time()
        ph = phase()
        if now >= nxt_poll:
            ts = now
            st, names = list_dir(conn, "", cfg["kind"], "poll_root")
            for kind, mid_, src in process_listing(conn, names, "root", ts):
                ans_q.append((mid_, src))
            fetch_file_if_announced(conn, names)
            nxt_poll = time.time() + cfg["poll"][ph]()
        if ans_q:
            m_id, src = ans_q.pop(0)
            answer(conn, m_id, src)
        if own_inbox and now >= nxt_inbox_poll:
            poll_inbox(conn, own_inbox)
            nxt_inbox_poll = time.time() + (15 * rng.uniform(0.5, 1.5) if ph == "A" else 90 * rng.uniform(0.5, 1.5))
        if pending_replies and now >= nxt_check:
            check_replies(conn)
            nxt_check = time.time() + rng.uniform(6, 14)
        if now >= nxt_bc:
            do_broadcast(conn)
            nxt_bc = time.time() + rng.expovariate(1 / cfg["bc"][ph])
        if now >= nxt_ask:
            do_ask(conn)
            nxt_ask = time.time() + rng.expovariate(1 / cfg["ask"][ph])
        if now >= nxt_inbox_post and NAME != "board3":
            do_inbox_post(conn, rng.choice([o for o in INBOX_TARGETS if o != own_inbox]))
            nxt_inbox_post = time.time() + rng.expovariate(1 / (300 if ph == "A" else 600))
        if files and now >= files[0]:
            files.pop(0)
            send_file(conn, "board1", 250)
        time.sleep(0.1)


def run_heartbeat():
    conn = Conn(keepalive=False)
    n = 0
    sleep_until(args.t0 + 20)
    while time.time() < END:
        n += 1
        post(conn, f"zzHB_{NAME}_{n:04d}", "heartbeat", mid_=f"hb{n}")
        nap(45)


# ---------------------------------------------------------------- mixed behaviour
def run_mixed():
    conn_b = Conn(keepalive=True)
    conn_c = Conn(keepalive=False)
    buddy = "board1" if NAME == "mixed1" else "board2"
    nxt_fetch = time.time() + rng.uniform(0, 20)
    nxt_board = time.time() + rng.uniform(120, 300)
    nxt_list = time.time() + rng.uniform(60, 240)
    ans_q = []
    while time.time() < END:
        now = time.time()
        if now >= nxt_fetch:
            benign_fetch(conn_b)
            nxt_fetch = now + rng.expovariate(1 / 25)
        if now >= nxt_list:
            conn_b.req("GET", f"{BASE}/api/storage/pkg-local/pkgs/{rng.choice(['core', 'net', 'cli'])}", "pkg_list_json", False, tool="jfrog-cli/2.60")
            nxt_list = now + rng.uniform(150, 400)
        if now >= nxt_board:
            # a short board session: read the root, handle anything for me, maybe post
            ts = time.time()
            st, names = list_dir(conn_c, "", "propfind", "poll_root")
            for kind, m_id, src in process_listing(conn_c, names, "root", ts):
                ans_q.append((m_id, src))
            fetch_file_if_announced(conn_c, names)
            while ans_q:
                m_id, src = ans_q.pop(0)
                answer(conn_c, m_id, src)
            if pending_replies:
                check_replies(conn_c)
            r = rng.random()
            if r < 0.35:
                do_broadcast(conn_c)
            elif r < 0.6:
                do_ask(conn_c, buddy)
            elif r < 0.75:
                do_inbox_post(conn_c, rng.choice(INBOX_TARGETS))
            nxt_board = time.time() + rng.uniform(240, 480)
            if pending_replies:   # follow up on a reply I am waiting for
                nxt_board = min(nxt_board, time.time() + rng.uniform(60, 120))
        time.sleep(0.5)


def main():
    sleep_until(args.t0)
    if args.heartbeat_only:
        run_heartbeat()
    elif args.role == "benign":
        run_benign()
    elif args.role == "board":
        run_board()
    else:
        run_mixed()
    log_msg(ev="exit", role=args.role, heartbeat=args.heartbeat_only)


if __name__ == "__main__":
    main()
