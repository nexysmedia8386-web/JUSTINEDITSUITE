"""python -m nxcore.ai  → which optional AI features this machine can use."""
import json
import sys

from . import status

for s_ in (sys.stdout, sys.stderr):
    try:
        s_.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

st = status()
for k, (ok, detail) in st.items():
    print(f"  {'✔' if ok else '·'} {k:10s} {detail}")
print("AI " + json.dumps({k: v[0] for k, v in st.items()}))
