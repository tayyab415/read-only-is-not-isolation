import sys
from pathlib import Path

AUDIT = Path(__file__).resolve().parents[1]
for p in (AUDIT, AUDIT / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
