from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sys
import time
import unittest

from run import Bridge
import schedule


SERVER = r'''
from concurrent.futures import ThreadPoolExecutor
import json,sys,threading,time
lock=threading.Lock()
def emit(value):
 with lock: print(json.dumps(value),flush=True)
setup=json.loads(sys.stdin.readline())
emit({"ready":True,"public":{"users":{"alice":"a","bob":"b"}},"routes":{"alice":{},"bob":{}}})
def work(job):
 if job["actor"]=="alice": time.sleep(.4)
 emit({"id":job["id"],"actor":job["actor"],"complete":True,"records":[]})
with ThreadPoolExecutor(max_workers=2) as pool:
 for line in sys.stdin:
  message=json.loads(line)
  if message["op"]=="close": break
  pool.submit(work,message)
emit({"closed":True,"status":{"complete":True,"cleanup_absence_proved":True}})
'''


class BridgeTests(unittest.TestCase):
    def test_fast_client_does_not_wait_for_slow_peer(self):
        bridge = Bridge({"run_id": "p4-s1-open", "configuration": "open"}, smoke=True,
                        command=[sys.executable, "-u", "-c", SERVER])
        try:
            start = time.monotonic()
            with ThreadPoolExecutor(max_workers=2) as pool:
                slow = pool.submit(bridge.batch, "alice", [])
                fast = pool.submit(bridge.batch, "bob", [])
                self.assertEqual(fast.result(timeout=1)["actor"], "bob")
                self.assertFalse(slow.done())
                self.assertLess(time.monotonic() - start, .35)
                self.assertEqual(slow.result(timeout=2)["actor"], "alice")
            self.assertTrue(bridge.close()["status"]["complete"])
        finally:
            if bridge.process.poll() is None:
                bridge.force_stop()

    def test_failed_setup_closes_process(self):
        code = 'import sys,json;sys.stdin.readline();print(json.dumps({"ready":False}),flush=True)'
        with self.assertRaises(RuntimeError):
            Bridge({"run_id": "p4-s1-open", "configuration": "open"}, smoke=True,
                   command=[sys.executable, "-u", "-c", code])

    def test_plan_has_all_subject_configuration_cells(self):
        rows = schedule.assigned()
        self.assertEqual(len(rows), 6)
        self.assertEqual({(row["model"], row["configuration"]) for row in rows},
                         {(model, configuration) for model in schedule.MODELS
                          for configuration in schedule.CONFIGURATIONS})
        self.assertEqual(rows, schedule.assigned())
        self.assertEqual(len({row["run_id"] for row in rows}), 6)
        self.assertEqual(set(schedule.MODELS), {"luna", "oss"})
        self.assertFalse(schedule.WITHDRAWAL["included_in_denominator"])
        self.assertTrue(all(set(row["values"]) == {"alice", "bob"}
                            and all(0 <= value <= 15 for value in row["values"].values())
                            for row in rows))

    def test_withdrawal_preserves_surviving_assignments_and_order(self):
        digest = hashlib.sha256(json.dumps(schedule.assigned(), sort_keys=True).encode()).hexdigest()
        self.assertEqual(digest, "d2538f468173b2a33fae61d9d038cf13b54e3c4b4f83fa18911940f8bdb0d8f2")


if __name__ == "__main__":
    unittest.main()
