"""Fixed workload plans. No provisioning, analysis, or network access."""
import random
from urllib.parse import urlsplit

RECIPES = ("get", "head", "cond_get", "range_get", "fresh_get",
           "repeated_get", "folder_html", "stats")
CONFIGURATIONS = ("separate", "sham", "aa", "immutable")
SEED = 2026110807
WINDOW_S = 2.0
SENDER_REQUESTS = 128


def schedule(configuration, split, count):
    if configuration not in CONFIGURATIONS or split not in ("calibration", "holdout"):
        raise ValueError("Invalid configuration or split")
    if not isinstance(count, int) or count <= 0 or count % 4:
        raise ValueError("Trial count must be a positive multiple of four")
    rng = random.Random(f"{SEED}:{configuration}:{split}")
    # Pair active and idle trials within each recipe and sender direction.
    # Recipe/direction identity cannot predict a label by unequal counts.
    cells = [(recipe, sender) for recipe in RECIPES for sender in ("alice", "bob")]
    rng.shuffle(cells)
    pairs, remainder = divmod(count // 2, len(cells))
    rows = []
    for index, (recipe, sender) in enumerate(cells):
        for _ in range(pairs + (index < remainder)):
            for label in (0, 1):
                rows.append({"label": label, "recipe": recipe, "sender": sender,
                             "receiver": "bob" if sender == "alice" else "alice",
                             "token": f"{rng.getrandbits(64):016x}"})
    rng.shuffle(rows)
    for index, row in enumerate(rows):
        row.update(index=index, split=split, trial_id=f"{split[0]}{index:04d}")
    return rows


def repository(state, actor):
    if actor not in ("alice", "bob"):
        raise ValueError("Invalid actor")
    return state["repositories"][actor]


def marker(run_id, trial, actor, suffix):
    # Retain the existing trusted backend log collector's marker prefix.
    return f"p1/{run_id}/{trial['trial_id']}/{actor}/{suffix}"


def object_path(state, actor, index):
    if state.get("kind") == "immutable":
        return state["object_paths"][str(index)]
    return f"/{repository(state, actor)}/pkg-{index:02d}.bin"


def folder_path(state, actor):
    return "/simple/" if state.get("kind") == "immutable" else f"/{repository(state, actor)}-cache/"


def missing_path(state, actor, trial, name):
    root = "/missing" if state.get("kind") == "immutable" else f"/{repository(state, actor)}/fresh"
    return f"{root}/{trial['trial_id']}-{trial['token']}-{name}.bin"


def stats_path(state, actor):
    root = "snapshot" if state.get("kind") == "immutable" else repository(state, actor) + "-cache"
    return f"/api/storage/{root}/pkg-15.bin?stats"


def sender_jobs(state, run_id, trial, configuration):
    if trial["label"] == 0 or configuration == "aa":
        return []
    actor, recipe = trial["sender"], trial["recipe"]
    cached = object_path(state, actor, 0)
    methods_paths_headers = {
        "get": ("GET", cached, {}),
        "head": ("HEAD", cached, {}),
        "cond_get": ("GET", cached, {"If-None-Match": state["fixture_etag"]}),
        "range_get": ("GET", cached, {"Range": "bytes=0-0"}),
        "fresh_get": ("GET", None, {}),
        "repeated_get": ("GET", missing_path(state, actor, trial, "repeat"), {}),
        "folder_html": ("GET", folder_path(state, actor), {}),
        "stats": ("GET", stats_path(state, actor), {}),
    }
    method, path, headers = methods_paths_headers[recipe]
    return [{"method": method, "path": path or missing_path(state, actor, trial, f"unique-{index:03d}"),
             "headers": dict(headers), "offset_s": (index // 8) * 0.125,
             "marker": marker(run_id, trial, actor, f"s-{index:03d}"),
             "probe": f"sender.{recipe}"}
            for index in range(SENDER_REQUESTS)]


def receiver_jobs(state, run_id, trial):
    actor = trial["receiver"]
    cached = object_path(state, actor, 15)
    jobs = []

    def add(probe, method, path, offset):
        jobs.append({"method": method, "path": path, "headers": {}, "offset_s": offset,
                     "probe": probe, "marker": marker(run_id, trial, actor, probe)})

    for index in range(15):
        add(f"post.timed.{index:02d}", "GET" if index % 2 == 0 else "HEAD",
            cached, 0.05 + 0.125 * index)
    for index, (probe, method, path) in enumerate([
        ("post.after.head", "HEAD", cached),
        ("post.after.folder", "GET", folder_path(state, actor)),
        ("post.after.stats", "GET", stats_path(state, actor)),
        ("post.after.repeat", "GET", missing_path(state, actor, trial, "r-repeat")),
        ("post.after.fresh", "GET", missing_path(state, actor, trial, "r-unique")),
        ("post.after.cached", "GET", cached),
    ]):
        add(probe, method, path, 2.20 + 0.10 * index)
    return jobs


def scoped(path, state, actor):
    """Fail before dispatch if a generated target leaves its fixture."""
    parsed = urlsplit(path)
    if state.get("kind") == "immutable":
        return parsed.path.startswith(("/files/", "/fixture/", "/simple/", "/missing/",
                                       "/api/storage/snapshot/"))
    repo = repository(state, actor)
    return any(parsed.path == root or parsed.path.startswith(root + "/")
               for root in [f"/{repo}", f"/{repo}-cache", f"/api/storage/{repo}-cache"])
