"""Public fixed actions supplied to participants; no assignment generator."""
import hashlib

OFFSETS = tuple(round(.1 + .1 * index, 2) for index in range(22))
PAYLOAD = b"p1-meter fixture package bytes\n"
FILE_BYTES = (b"private-working-file\n" * 51 + b"x" * 1024)[:1024]
CPU_BYTES = (b"p8-cpu-fixture\n" * 4096 + b"x" * 65536)[:65536]


def parse_cpu(line):
    fields = line.strip().split()
    if len(fields) < 9 or fields[0] != "cpu":
        raise ValueError("No aggregate CPU row")
    values = [int(value) for value in fields[1:]]
    if any(value < 0 for value in values):
        raise ValueError("Negative CPU counter")
    return values
