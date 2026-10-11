"""Small frame thumbnails at exact times (segment frames in the dashboard, In-point frames in Review) — Justin EDIT Suite.

  python -m nxcore.thumbs SRC < requests.json        # [["clip.mp4", 12.3], …]  (or --req '[...]')

Writes  _nexys_work/fthumbs/<stem>_<t×10>.jpg  (192 px wide, 8-bit 4:2:0), skips files that exist.
Prints  "THUMB <file>|<t>|<abs path>"  per finished thumbnail (the panel swaps it in live), then "THUMBS done N".
"""
import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from .media import stem

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        sys.stdin.reconfigure(encoding="utf-8")
    except Exception:
        pass

WIDTH = 192


def path_for(src, f, t):
    return os.path.join(src, "_nexys_work", "fthumbs", f"{stem(f)}_{int(round(float(t) * 10))}.jpg")


def make_one(src, f, t, width=WIDTH):
    dst = path_for(src, f, t)
    if os.path.exists(dst) and os.path.getsize(dst) > 200:
        return dst
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp.jpg"
    p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-ss", f"{max(0.0, float(t)):.2f}",
                        "-i", os.path.join(src, f), "-frames:v", "1",
                        "-vf", f"scale={width}:-2:flags=bicubic,format=yuvj420p", "-q:v", "4", tmp],
                       capture_output=True, timeout=60)
    if p.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 200:
        os.replace(tmp, dst)
        return dst
    try:
        os.remove(tmp)
    except OSError:
        pass
    return None


def run(src, reqs, workers=6, out=print):
    seen, todo = set(), []
    for f, t in reqs:
        k = (f, int(round(float(t) * 10)))
        if k not in seen:
            seen.add(k); todo.append((f, float(t)))
    n = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(make_one, src, f, t): (f, t) for f, t in todo}
        for fu in as_completed(futs):
            f, t = futs[fu]
            try:
                p = fu.result()
            except Exception:
                p = None
            if p:
                n += 1
                out(f"THUMB {f}|{round(t, 1)}|{p}")
    out(f"THUMBS done {n}")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--req", default="")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    reqs = json.loads(a.req or sys.stdin.read() or "[]")
    run(a.src, reqs, a.workers, out=lambda s: print(s, flush=True))


if __name__ == "__main__":
    main()
