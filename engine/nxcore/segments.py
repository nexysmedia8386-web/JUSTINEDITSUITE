"""Split long clips into usable shots ("đoạn") so one long take can feed several B-roll picks — Justin EDIT Suite.

  python -m nxcore.segments SRC [--force] [--workers 4]

Signals (almost all already computed by the analysis — no extra full decode):
  · shake.json   usable runs (stable / handheld); fast pans + shaky stretches are natural boundaries
  · keyframes    one keyframe-only decode at 64×36 gray → scene changes inside a continuous take + a 64-bit
                 perceptual hash per segment (nxcore.describe reuses it to skip near-identical segments)
  · qc.json      black / blur / freeze / dark / bright ranges are cut out
  · transcripts  never cut inside a spoken block; segments that are mostly speech are marked speech=True
Rules: clips < LONG_S stay one segment (their usable hull). Long clips → segments of MIN_S…MAX_S (≈ TARGET_S),
split where the picture changes most.
Output  _nexys_work/segments.json  {"v", "clips": {file: {"sig", "dur", "segs": [{"a","b","kind","speech","q","hash","cut"}]}}}
cached per clip by a signature of (file key, shake/qc versions, transcript mtime).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

from . import jsonio
from .media import stem
from .gpu import hwaccel_args as _hwargs   # CUDA on Windows/Linux, VideoToolbox on macOS

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

SEG_V = 2            # v2: static long takes (timelapse / locked-off) keep only a few evenly spaced segments
LONG_S = 20.0        # shorter clips keep one segment
MIN_S = 3.0
MAX_S = 15.0
TARGET_S = 8.0
EDGE_S = 0.3         # camera settling at clip start / end
KW, KH = 64, 36      # keyframe thumbnail size


def _j(p, d):
    return jsonio.load(p, d)


def _save_atomic(p, obj):
    jsonio.save_atomic(p, obj)


# ---------- keyframes: scene change + perceptual hash ----------
# nxcore.describe needs the same keyframes as 448-px JPEGs right after segmenting. While it runs segments it sets
# share_keyframes({paths}): those clips are decoded ONCE (keyframe_jpegs), the gray thumbnails are derived from the
# JPEGs and the JPEG list is kept for describe (bounded by KF_SHARE_BUDGET bytes; over budget → describe decodes again).
KF_SHARE_BUDGET = 256 * 1024 * 1024
_KF_SHARE = None
_KF_LOCK = threading.Lock()


def share_keyframes(paths, budget=KF_SHARE_BUDGET):
    """Start keeping keyframe JPEGs for `paths` (absolute). Returns the store {path: [(t, jpeg)]} to pop from."""
    global _KF_SHARE
    with _KF_LOCK:
        _KF_SHARE = {"want": set(paths), "store": {}, "used": 0, "budget": budget}
        return _KF_SHARE["store"]


def stop_sharing():
    global _KF_SHARE
    with _KF_LOCK:
        _KF_SHARE = None


def gray_from_jpegs(kj):
    """[(t, jpeg)] → (times[n], frames[n,KH,KW] uint8 gray), same scale as keyframes()."""
    import cv2
    ts, frs = [], []
    for t, b in kj:
        img = cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        frs.append(cv2.resize(img, (KW, KH), interpolation=cv2.INTER_AREA))
        ts.append(float(t))
    if not frs:
        return np.zeros(0), np.zeros((0, KH, KW), np.uint8)
    return np.asarray(ts), np.stack(frs)


def _shared_keyframes(path, hw, timeout):
    sh = _KF_SHARE
    if sh is None or path not in sh["want"]:
        return None
    from .describe import keyframe_jpegs
    kj = keyframe_jpegs(path, hw=hw, timeout=max(timeout, 900))
    if not kj:
        return None
    ts, fr = gray_from_jpegs(kj)
    if not len(fr):
        return None
    size = sum(len(b) for _, b in kj)
    with _KF_LOCK:
        if _KF_SHARE is sh and sh["used"] + size <= sh["budget"]:
            sh["store"][path] = kj
            sh["used"] += size
    return ts, fr


def keyframes(path, hw=True, timeout=600):
    """Keyframe-only decode → (times[n], frames[n,KH,KW] uint8 gray). Empty arrays on failure."""
    shared = _shared_keyframes(path, hw, timeout)
    if shared is not None:
        return shared
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "info"] + _hwargs(hw) + \
          ["-skip_frame", "nokey", "-i", path, "-an", "-sn", "-dn",
           "-vf", f"scale={KW}:{KH}:flags=area,format=gray,showinfo", "-fps_mode", "passthrough", "-f", "rawvideo", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except Exception:
        return np.zeros(0), np.zeros((0, KH, KW), np.uint8)
    n = len(p.stdout) // (KW * KH)
    ts = [float(x) for x in re.findall(rb"pts_time:\s*(-?[\d.]+)", p.stderr)]
    if (p.returncode != 0 or not n) and hw:
        return keyframes(path, hw=False, timeout=timeout)
    m = min(n, len(ts))
    fr = np.frombuffer(p.stdout[: m * KW * KH], np.uint8).reshape(m, KH, KW)
    return np.asarray(ts[:m]), fr


def dhash(img):
    """64-bit difference hash of a gray image (int)."""
    import cv2
    s = cv2.resize(img, (9, 8), interpolation=cv2.INTER_AREA).astype(np.int16)
    bits = (s[:, 1:] > s[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a, b):
    return bin(int(a) ^ int(b)).count("1")


def change_curve(frames):
    """Picture change between consecutive keyframes (brightness-normalised mean abs diff, 0…~60)."""
    if len(frames) < 2:
        return np.zeros(len(frames))
    f = frames.astype(np.float32)
    f -= f.mean(axis=(1, 2), keepdims=True)
    d = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2))
    return np.concatenate([[0.0], d])        # d[i] = change arriving at keyframe i


def scene_cuts(ts, d):
    """Keyframe times where the content changes clearly more than this clip's normal motion."""
    if len(d) < 3:
        return []
    base = float(np.median(d[1:])) + 1e-3
    return [float(t) for t, x in zip(ts, d) if x >= 14.0 and x >= 3.0 * base]


# ---------- intervals ----------
def _merge(rs, gap=0.6):
    out = []
    for a, b in sorted([list(r[:2]) for r in rs if r[1] > r[0]]):
        if out and a - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _subtract(rs, bad):
    out = []
    for a, b in rs:
        cur = [[a, b]]
        for x, y in bad:
            nxt = []
            for c, e in cur:
                if y <= c or x >= e:
                    nxt.append([c, e]); continue
                if x > c:
                    nxt.append([c, x])
                if y < e:
                    nxt.append([y, e])
            cur = nxt
        out += cur
    return [r for r in out if r[1] - r[0] > 0.05]


def qc_bad(q):
    rs = []
    for k in ("black", "blur", "freeze", "dark", "bright"):
        rs += [r[:2] for r in (q.get(k) or []) if len(r) >= 2]
    return _merge(rs, 0.3)


def speech_blocks(W, f):
    tr = _j(os.path.join(W, "transcripts", stem(f) + ".json"), {}) or {}
    rs = [[float(s.get("start", 0)), float(s.get("end", 0))] for s in tr.get("segments", []) if str(s.get("text", "")).strip()]
    return _merge(rs, 0.8)


def _speech_at(t, sp):
    return next(((a, b) for a, b in sp if a < t < b), None)


def _snap(t, sp, lo, hi):
    """Move a cut out of a spoken block when the neighbouring pieces stay long enough."""
    blk = _speech_at(t, sp)
    if not blk:
        return t
    for c in sorted(blk, key=lambda x: abs(x - t)):
        if lo + MIN_S <= c <= hi - MIN_S:
            return c
    return t


def split_long(a, b, ts, d, sp):
    """[a,b] longer than MAX_S → split recursively at the strongest picture change near the middle."""
    L = b - a
    if L <= MAX_S:
        return [[a, b]]
    mid = (a + b) / 2
    idx = np.where((ts > a + MIN_S) & (ts < b - MIN_S))[0] if len(ts) else np.zeros(0, int)
    if len(idx):
        w = d[idx] * (1.0 - 0.6 * np.abs(ts[idx] - mid) / (L / 2)) + 1e-6 * (1 - np.abs(ts[idx] - mid) / L)
        t = float(ts[idx[int(np.argmax(w))]])
    else:
        t = mid
    t = _snap(t, sp, a, b)
    return split_long(a, t, ts, d, sp) + split_long(t, b, ts, d, sp)


# Static long take (timelapse, locked-off wide): the picture barely changes between keyframes and there is no scene
# cut — tiling it into 8-second shots gave 119 near-identical segments for one 20-min timelapse (C0008, Hội An):
# 119 vision calls and 119 interchangeable B-roll candidates. Keep a few shots spread over the take instead
# (start … end, so a timelapse still offers its different light), each up to MAX_S.
# Calibrated on Hội An keyframe change (median / p90): timelapse 0.22 / 0.26 · action-cam walk 4.25 / 6.6 · gimbal 49 / 65.
STATIC_MED, STATIC_P90 = 1.5, 3.0


def is_static(d, cuts):
    if len(d) < 8 or cuts:
        return False
    x = d[1:]
    return float(np.median(x)) < STATIC_MED and float(np.percentile(x, 90)) < STATIC_P90


def static_n(dur):
    return int(min(6, max(2, round(dur / 180.0))))


def spread(pieces, n):
    """n pieces evenly spaced over the take (first and last included)."""
    if len(pieces) <= n:
        return pieces
    idx = sorted({int(round(i * (len(pieces) - 1) / (n - 1))) for i in range(n)})
    return [pieces[i] for i in idx]


def build_clip(dur, sh, q, sp, kf, extra_cuts=None):
    """→ list of segments for one clip. No shake entry → the whole clip; a shake entry with nothing usable (fully
    shaky / fast) → no segment at all (a 50/60p clip that is only steady in slow motion uses its slow-mo stretches).
    extra_cuts: frame-accurate shot starts from TransNetV2 (nxcore.ai.shots), merged with the keyframe cuts."""
    usable = None
    if sh and isinstance(sh.get("usable"), list):
        usable = sh["usable"] or sh.get("usable_slowmo") or []
        if not usable:
            return []
    if not usable:
        usable = [[EDGE_S, max(EDGE_S, dur - EDGE_S)]]
    rs = _subtract(_merge(usable, 0.6), qc_bad(q or {}))
    rs = [[max(a, EDGE_S), min(b, dur - EDGE_S)] for a, b in rs]
    rs = [r for r in rs if r[1] - r[0] > 0.05]
    ts, fr = kf if kf else (np.zeros(0), np.zeros((0, KH, KW), np.uint8))
    d = change_curve(fr)
    cuts = scene_cuts(ts, d)
    if extra_cuts:                             # neural cuts win; a keyframe cut within 1 s of one is the same cut
        nn = sorted(float(c) for c in extra_cuts)
        cuts = sorted(nn + [c for c in cuts if all(abs(c - x) > 1.0 for x in nn)])
    if dur < LONG_S or not rs:
        pieces = [[rs[0][0], rs[-1][1]]] if rs else [[EDGE_S, max(EDGE_S + 0.1, dur - EDGE_S)]]
    else:
        pieces = []
        for a, b in rs:
            edges = [a] + [c for c in cuts if a + 1.0 < c < b - 1.0] + [b]
            for x, y in zip(edges, edges[1:]):
                pieces += split_long(x, y, ts, d, sp)
        merged = []
        for p in pieces:                       # glue short neighbours of the same shot (touching, no scene cut between)
            if merged and p[0] - merged[-1][1] <= 0.7 and p[1] - merged[-1][0] <= MAX_S \
                    and (p[1] - p[0] < MIN_S or merged[-1][1] - merged[-1][0] < MIN_S) \
                    and not any(abs(c - p[0]) < 0.5 for c in cuts):
                merged[-1][1] = p[1]
            else:
                merged.append(list(p))
        pieces = [p for p in merged if p[1] - p[0] >= 1.5]
        if is_static(d, cuts) and len(pieces) > 2:
            pieces = spread(pieces, static_n(dur))
    states = (sh or {}).get("segments") or []
    static = dur >= LONG_S and is_static(d, cuts)
    out = []
    for a, b in pieces:
        acc, jw, jt = {}, 0.0, 0.0
        for s in states:
            o = min(b, s["end"]) - max(a, s["start"])
            if o > 0:
                acc[s["state"]] = acc.get(s["state"], 0.0) + o
                if s.get("jit") is not None:
                    jw += o; jt += o * float(s["jit"])
        L = max(b - a, 1e-3)
        h = None
        if len(ts):
            i = int(np.argmin(np.abs(ts - (a + b) / 2)))
            if abs(ts[i] - (a + b) / 2) <= max(2.0, L / 2):
                h = format(dhash(fr[i]), "016x")
        out.append({"a": round(a, 2), "b": round(b, 2), "kind": max(acc, key=acc.get) if acc else "",
                    "speech": round(sum(max(0.0, min(b, y) - max(a, x)) for x, y in sp) / L, 2),
                    "q": round(100 * max(0.0, 1 - (jt / jw) / 0.6)) if jw else None,
                    "hash": h, "cut": any(abs(c - a) < 0.5 for c in cuts), **({"static": True} if static else {})})
    return out


def seg_key(s):
    return f"{float(s['a']):.1f}-{float(s['b']):.1f}"


def seg_id(f, s):
    return f"{f}@{seg_key(s)}"


def _sig(W, f, m, sh, q):
    try:
        tm = int(os.path.getmtime(os.path.join(W, "transcripts", stem(f) + ".json")))
    except OSError:
        tm = 0
    return f"{m.get('key')}|{(sh or {}).get('v')}|{(sh or {}).get('key')}|{(q or {}).get('qc_v')}|{tm}|{SEG_V}|{_shots_engine(m)}"


def _shots_engine(m):
    """'tn' when TransNetV2 applies to this clip — edited files segmented without it are redone once it is installed."""
    try:
        from .ai import shots as _sh
        return "tn" if _sh.available() and _sh.wanted(m) else ""
    except Exception:
        return ""


def run(src, force=False, workers=4, progress=print, files=None):
    W = os.path.join(src, "_nexys_work")
    media = _j(os.path.join(W, "media.json"), {})
    if not media:
        progress("@segments done skip chưa quét source")
        return {}
    shake, qc = _j(os.path.join(W, "shake.json"), {}), _j(os.path.join(W, "qc.json"), {})
    op = os.path.join(W, "segments.json")
    store = _j(op, {}) or {}
    if store.get("v") != SEG_V:
        store = {}
    clips = store.setdefault("clips", {})
    store["v"] = SEG_V
    for f in [f for f in clips if f not in media]:
        clips.pop(f, None)
    for c in clips.values():                   # canonical key for the panel (JS toFixed rounds .x5 differently)
        for s in c.get("segs") or []:
            s.setdefault("k", seg_key(s))
    todo = []
    for f, m in media.items():
        if m.get("status", "ok") != "ok" or (files and f not in files):
            continue
        sg = _sig(W, f, m, shake.get(f), qc.get(f))
        if force or (clips.get(f) or {}).get("sig") != sg:
            todo.append((f, m, sg))
    progress(json.dumps({"stage": "segments", "total": len(todo), "cached": len(media) - len(todo)}))

    def job(item):
        f, m, sg = item
        dur = float(m.get("dur") or 0)
        kf = keyframes(os.path.join(src, f)) if dur >= LONG_S else None
        nn = None
        if dur >= LONG_S and _shots_engine(m):
            try:
                from .ai import shots as _sh
                nn = _sh.cuts(os.path.join(src, f), m.get("fps"))
            except Exception:
                nn = None
        segs = build_clip(dur, shake.get(f), qc.get(f), speech_blocks(W, f), kf, nn)
        for s in segs:
            s["k"] = seg_key(s)
        return f, {"sig": sg, "dur": round(dur, 2), "segs": segs}

    # longest first: the slow keyframe scans start early and overlap with the many short clips
    todo.sort(key=lambda x: -float(x[1].get("dur") or 0))
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = [ex.submit(job, it) for it in todo]
        for fu in as_completed(futs):
            done += 1
            try:
                f, rec = fu.result()
                clips[f] = rec
                progress(f"@segments {done}/{len(todo)} {f}")
                if len(rec["segs"]) > 1:
                    progress(f"[{done}/{len(todo)}] {f} · {len(rec['segs'])} đoạn")
            except Exception as e:
                progress(f"@segments {done}/{len(todo)} ?")
                progress(f"segments: lỗi {e}")
            if done % 10 == 0 or done == len(todo):
                _save_atomic(op, store)
    if not todo:
        _save_atomic(op, store)
    n_long = sum(1 for c in clips.values() if len(c.get("segs") or []) > 1)
    n_seg = sum(len(c.get("segs") or []) for c in clips.values())
    if todo:
        progress(f"segments: {n_seg} đoạn từ {len(clips)} clip ({n_long} clip dài được tách)")
    progress(f"@segments done ok {n_seg}")
    return clips


def load(src):
    """file → [segments] (only clips split into ≥ 2 parts are interesting to callers, but all are returned)."""
    st = _j(os.path.join(src, "_nexys_work", "segments.json"), {}) or {}
    return {f: c.get("segs") or [] for f, c in (st.get("clips") or {}).items()}


def main():
    ap = argparse.ArgumentParser(description="Tách clip dài thành nhiều đoạn dùng được")
    ap.add_argument("src")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--files", nargs="*", default=None)
    a = ap.parse_args()
    run(a.src, a.force, a.workers, progress=lambda s: print(s, flush=True), files=a.files)


if __name__ == "__main__":
    main()
