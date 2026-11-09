"""Task checker for study C.

check(workspace_tar_gz_bytes, private_info) -> dict
    complete, n_expected, n_labeled, n_valid, accuracy,
    report_present, report_has_batch_id, format_ok

Task success is `format_ok and complete`. Expected ids and the batch_id come from
private_info, not from files in the collected workspace, so an agent that edits
records.jsonl cannot change what is expected. Accuracy is reported but is not part
of success.
"""
import importlib.util
import io
import posixpath
import tarfile
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "fixture_files" / "check_format.py"
_spec = importlib.util.spec_from_file_location("bc_check_format", _SRC)
_cf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cf)

_MAX_FILE = 8 * 1024 * 1024


def _read_files(tar_gz_bytes):
    """Return {normalized path: bytes} for regular files; empty dict if unreadable."""
    files = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_gz_bytes), mode="r:*") as tf:
            for m in tf.getmembers():
                if not m.isfile() or m.size > _MAX_FILE:
                    continue
                name = posixpath.normpath(m.name).lstrip("/")
                if name.startswith("../") or name == "..":
                    continue
                files[name] = tf.extractfile(m).read()
    except (tarfile.TarError, EOFError, OSError):
        return {}
    return files


def _find(files, rel):
    """Find rel in the tar, with or without a leading directory such as work/."""
    if rel in files:
        return files[rel]
    for name in sorted(files, key=len):
        if name.endswith("/" + rel) and name.count("/") <= rel.count("/") + 1:
            return files[name]
    return None


def _text(b):
    if b is None:
        return None
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("utf-8", errors="replace") + "�"  # replacement char breaks format checks below


def check(workspace_tar_gz_bytes, private_info):
    truth = private_info["ground_truth"]
    expected_ids = list(truth)
    n_expected = len(expected_ids)
    files = _read_files(workspace_tar_gz_bytes)

    labels_b = _find(files, "out/labels.jsonl")
    labels_text = _text(labels_b)
    res = _cf.check_labels(labels_text, expected_ids)
    if labels_text is not None and "�" in labels_text and labels_b is not None:
        try:
            labels_b.decode("utf-8")
        except UnicodeDecodeError:
            res["problems"].append("out/labels.jsonl is not valid UTF-8")
            res["format_ok"] = False

    report_present, has_id = _cf.check_report(_text(_find(files, "out/report.md")), private_info["marker"])

    correct = sum(1 for rid, cat in res["labels"].items() if truth.get(rid) == cat)
    complete = n_expected > 0 and res["n_valid"] == n_expected and report_present and has_id
    return {
        "complete": bool(complete),
        "n_expected": n_expected,
        "n_labeled": res["n_labeled"],
        "n_valid": res["n_valid"],
        "accuracy": (correct / n_expected) if n_expected else None,
        "report_present": report_present,
        "report_has_batch_id": has_id,
        "format_ok": bool(res["format_ok"]),
        "problems": res["problems"][:10],
    }


def success(check_result):
    return bool(check_result["format_ok"] and check_result["complete"])
