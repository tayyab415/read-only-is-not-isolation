"""Stub fixtures: callable returning (tar_gz_bytes, private_info)."""
import io
import secrets
import tarfile


def make_workspace(spec_row, actor, role):
    marker = secrets.token_hex(4)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = f"records for {actor} {marker}\n".encode()
        info = tarfile.TarInfo("records.txt")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue(), {"marker": marker, "actor": actor}
