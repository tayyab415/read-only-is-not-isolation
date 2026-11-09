"""Label-independent command lists for an ordinary-task background client."""
import importlib.util
from pathlib import Path
import sys

P4 = Path(__file__).resolve().parent.parent / "P4-models"
sys.path.insert(0, str(P4))
import actions
import deployment
sys.path.insert(0, str(P4))

WAVE_OFFSETS_S = (0, 8, 16, 24)


def jobs(route):
    return [{"method": "GET", "path": f"{route['object_root']}/pkg-{index:02d}.bin",
             "headers": {}, "offset_s": index * 0.1} for index in range(16)]


class Client(deployment.Client):
    def __init__(self, run_id, actor, public, execute_background):
        super().__init__(run_id, actor, public)
        self.execute_background = execute_background

    def batch(self, requests):
        if self.actor != "bob" or self.execute_background:
            return super().batch(requests)
        # The idle background runs the same persistent worker RPC and the same
        # command list. Only the private execute flag changes inside its sandbox.
        import time
        checked = [actions.request(row, self.route) for row in requests]
        commands = [{**row, "probe": f"subject.turn{self.turn:02d}.r{index:02d}",
                     "marker": f"p1/{self.run_id}/a{self.turn:02d}/{self.actor}/r{index:02d}"}
                    for index, row in enumerate(checked)]
        self.turn += 1
        if self.turn > len(WAVE_OFFSETS_S):
            raise RuntimeError("Background wave limit exhausted")
        self.client.start(commands, time.time_ns() + 150_000_000, execute_http=0)
        returned = self.client.finish()
        if returned:
            raise RuntimeError("Idle background unexpectedly made requests")
        return {"complete": True, "records": []}
