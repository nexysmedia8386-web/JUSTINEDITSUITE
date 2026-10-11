"""Heartbeat lock files in _nexys_work/ — cross-process "who owns the GPU / is describe already running".

A lock is a file touched every few seconds by its owner; it counts as held while its mtime is younger than
STALE_S, so a crashed / killed owner frees it by itself (no PID checks — os.kill(pid, 0) terminates on Windows).

    with gpulock.hold(src, "gpu_busy"):      # analyze: whisper + QC own the GPU
        ...
    gpulock.active(src, "gpu_busy")          # describe: back off while true
"""
import os, threading, time
from contextlib import contextmanager

STALE_S = 30.0
BEAT_S = 5.0


def path(src, name): return os.path.join(src, "_nexys_work", "." + name + ".lock")


def active(src, name, stale=STALE_S):
    try:
        return time.time() - os.path.getmtime(path(src, name)) < stale
    except OSError:
        return False


@contextmanager
def hold(src, name):
    p = path(src, name); os.makedirs(os.path.dirname(p), exist_ok=True)
    stop = threading.Event()

    def beat():
        while not stop.is_set():
            try:
                with open(p, "w") as fh: fh.write(f"{os.getpid()} {time.time():.0f}")
            except OSError:
                pass
            stop.wait(BEAT_S)
    th = threading.Thread(target=beat, daemon=True); th.start()
    try:
        yield p
    finally:
        stop.set(); th.join(1)
        try: os.remove(p)
        except OSError: pass
