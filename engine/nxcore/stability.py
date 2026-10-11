"""Camera stability + usable ranges + composition per clip — Justin EDIT Suite (replaces the old phase-correlation shake scan).

Per clip (decoded once at ≈15 fps — an even divisor of the source rate: 24→12, 25→12.5, 30/60→15 — 480 px wide,
grayscale, CUDA decode with frames kept on the GPU when available, CPU fallback):
  • global camera motion per frame: KLT features + RANSAC similarity transform → subject movement is rejected as outliers
    (the old whole-frame phase correlation mixed people walking with camera shake)
  • camera path = cumulative motion; intended move = path low-passed (σ 0.6 s); SHAKE = high-frequency residual,
    normalised by the frame diagonal (%) so 4K/1080/phone footage share one scale; rotation jitter in degrees
  • per-frame state: stable | handheld (ok) | shaky | slowmo (50/60p: shaky in real time, fine at ≤50 % speed) |
                     fast (whip pan / fast move) | lowconf (texture-less, not judged)
  • usable ranges  = stable/handheld runs ≥ 0.8 s (0.25 s trimmed at clip start/end, camera settling)
  • best window    = the most stable stretch (≥ 1.5 s, prefers not the first/last second)
  • composition    = sharpness, exposure/clipping, subject placement (spectral-residual saliency + faces, thirds/centre),
                     horizon tilt (Hough) → suggested rotation fix in degrees
  • stability/composition/quality scores 0–100 → used to rank near-duplicate takes (brollpick) and in the panel dashboard.

shake.json keeps the old keys (ranges = unusable [start, end, score]) so align/assemble work unchanged.
Slow-mo-only stretches (50/60p, steady at ≤ 50 % speed) are NOT in "ranges" but in "ranges_slowmo" (v5).
Jitter values (jit, jit_median, p90) are expressed in 15-fps-equivalent units whatever the analysis rate.

CLI: python -m nxcore.stability "F:/NEXIS/HR 2509" [--workers 3] [--force] [--files a.mov b.mov]
"""
import argparse
import json
import math
import os
import subprocess
import sys
import threading
from fractions import Fraction

import numpy as np

from . import jsonio

FPS = 15            # nominal analysis rate; the real one is analysis_fps(src_fps) (an integer divisor of the source rate)
WIDTH = 480
COMP_V = 2          # composition v2: faces from YuNet (OpenCV 5 has no Haar cascades → v1 found no face, ever). Bumping it
                    # refreshes only the face part of cached clips (3 frames each), not the whole analysis
STAB_V = 8          # v8: + "luma" brightness curve (4 Hz) · v7: + "pan" camera direction (4 Hz) · v6: + "act" subject-activity curve (4 Hz) · v5: analysis fps = src/round(src/15), slow-mo out of "ranges"
ACT_HZ = 4
CFG = {
    "stable_pct": 0.12,     # shake RMS (% of diagonal) below → stable (tripod/gimbal)
    "ok_pct": 0.40,         # below → acceptable handheld (after motion compensation, see jit_eff)
    "rot_ok_deg": 0.35,     # rotation jitter above → shaky
    "fast_pct_s": 45.0,     # intended camera speed above (% diagonal / s) → fast move / whip
    "speed_comp": 12.0,     # moving shots: parallax/rolling-shutter noise grows with speed → jitter / (1 + speed/12)
    "lowconf": 0.15,
    "lowconf_max_s": 0.5,   # longer texture-less stretches are not judged → not usable
    "min_usable": 0.8, "trim_edge": 0.25, "best_min": 1.5,
}

# CUDA decode keeps frames on the GPU; the fps filter drops frames there, scale_cuda shrinks them to 2× the analysis
# width (and converts 10-bit p010 → nv12) on the GPU, the CPU "area" scale finishes. A bare
# "hwdownload,format=nv12|p010le|…" picks nv12 for 10-bit HEVC → error → every 10-bit clip silently ran on CPU.
# Fallback chain: GPU frames → plain "-hwaccel cuda" → CPU (codec / chroma NVDEC can't do, e.g. HEVC 4:2:2).
# Set NX_HW_FRAMES=0 (or HW_FRAMES = False) to skip the GPU-frames step.
HW_FRAMES = os.environ.get("NX_HW_FRAMES", "1") != "0"
HW_PRE = "scale_cuda=w=min(960\\,iw):h=-2:format=nv12,hwdownload,format=nv12,"


def analysis_fps(src_fps):
    """Even sampling of the source frames: 24→12, 25→12.5, 30→15, 50→16.7, 60→15 (24p resampled to 15 fps repeats
    frames in a 3:2-like cadence that reads as camera judder)."""
    try:
        s = float(src_fps or 0)
    except (TypeError, ValueError):
        s = 0.0
    if not s or s <= 0 or not math.isfinite(s):
        return float(FPS)
    return s / max(1, round(s / FPS))


def _fps_expr(fps):
    fr = Fraction(float(fps)).limit_denominator(1001)
    return f"{fr.numerator}/{fr.denominator}"


def decode_attempts(cuda, rot=0):
    """→ [(decoder, input args, filter prefix placed right after fps=)] in order of preference; CPU always last.
    Same chain as QC (nxcore.qc.hw_attempts): CUDA / VideoToolbox / D3D11VA depending on the machine."""
    from .qc import hw_attempts
    return hw_attempts(cuda, rot, frames_pre=HW_PRE if HW_FRAMES else "")


def decode_timeout(dur):
    """Watchdog for one decode: generous (CPU 4K HEVC with other jobs running), but never hangs forever."""
    dur = float(dur or 0)
    return 3600.0 if dur <= 0 else max(180.0, 90.0 + 4.0 * dur)


class _Tail:
    """Drains a pipe in a thread (stderr must never fill up and block ffmpeg) and keeps the last `keep` bytes."""

    def __init__(self, stream, keep=8192):          # keep=None: whole log (fused QC parses every metadata line)
        self.buf = b""
        self.keep = keep
        self.th = threading.Thread(target=self._run, args=(stream,), daemon=True)
        self.th.start()

    def _run(self, s):
        rd = getattr(s, "read1", s.read)
        parts = []
        try:
            while True:
                b = rd(65536)
                if not b:
                    break
                if self.keep is None:
                    parts.append(b)
                else:
                    self.buf = (self.buf + b)[-self.keep:]
        except Exception:
            pass
        if parts:
            self.buf = b"".join(parts)

    def text(self):
        self.th.join(30 if self.keep is None else 5)
        return self.buf.decode("utf-8", "replace")


class FFStream:
    """ffmpeg → raw frames on stdout, stderr drained in a thread, killed by a watchdog after `timeout` s."""

    def __init__(self, cmd, frame_bytes, timeout=None, full_log=False):
        self.fb = frame_bytes
        self.timed_out = False
        self.rc = None
        self.err = ""
        self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     bufsize=frame_bytes * 4)
        self._tail = _Tail(self.proc.stderr, None if full_log else 8192)
        self._timer = None
        if timeout:
            self._timer = threading.Timer(timeout, self._expire)
            self._timer.daemon = True
            self._timer.start()

    def _expire(self):
        self.timed_out = True
        self.kill()

    def kill(self):
        try:
            self.proc.kill()
        except Exception:
            pass

    def read(self):
        """next frame (bytes) or None at EOF / short read."""
        try:
            b = self.proc.stdout.read(self.fb)
        except (OSError, ValueError):
            return None
        return b if b and len(b) == self.fb else None

    def close(self, kill=False):
        if kill:
            self.kill()
        if self._timer:
            self._timer.cancel()
        try:
            self.proc.stdout.close()
        except Exception:
            pass
        try:
            self.rc = self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.kill()
            self.rc = self.proc.wait()
        self.err = self._tail.text()
        try:
            self.proc.stderr.close()
        except Exception:
            pass
        return self.rc


def _gray_cmd(hw_args, pre, path, fps, W, H, qc_audio=None):
    """qc_audio None → gray frames only; True/False (clip has audio) → the same decode also runs every QC detector
    (nxcore.qc) on a split of the frames + the audio, results in stderr: one decode instead of two per clip."""
    if qc_audio is None:
        vf = f"fps={_fps_expr(fps)},{pre}scale={W}:{H}:flags=area,format=gray"
        return ["ffmpeg", "-v", "error", "-nostdin"] + hw_args + ["-i", path, "-an", "-sn", "-dn", "-vf", vf, "-f", "rawvideo", "-"]
    from .qc import qc_graph
    g, maps = qc_graph("[qv]", "", qc_audio)
    graph = f"[0:v]fps={_fps_expr(fps)},{pre}split=2[sv0][qv];[sv0]scale={W}:{H}:flags=area,format=gray[sv];" + g
    return (["ffmpeg", "-hide_banner", "-nostats", "-nostdin", "-loglevel", "info"] + hw_args +
            ["-i", path, "-filter_complex", graph, "-map", "[sv]", "-f", "rawvideo", "pipe:1"] + maps + ["-f", "null", "-"])


def _out_h(width, w_disp, h_disp):
    return int(round(width * (h_disp or 9) / max(1, (w_disp or 16)) / 2)) * 2


def decode_gray(path, w_disp, h_disp, cuda=True, fps=FPS, width=WIDTH, max_frames=None, rot=0, dur_hint=0.0):
    W = width
    H = _out_h(width, w_disp, h_disp)
    frame_bytes = W * H
    last_err = ""
    for _dec, hw, pre in decode_attempts(cuda, rot):
        st = FFStream(_gray_cmd(hw, pre, path, fps, W, H), frame_bytes, decode_timeout(dur_hint))
        chunks = []
        try:
            while True:
                chunk = st.read()
                if chunk is None:
                    break
                chunks.append(chunk)
                if max_frames and len(chunks) >= max_frames:
                    break
        finally:
            st.close(kill=bool(max_frames and len(chunks) >= max_frames))
        complete = max_frames and len(chunks) >= max_frames
        if chunks and (complete or (st.rc == 0 and not st.timed_out)):
            return np.frombuffer(b"".join(chunks), np.uint8).reshape(len(chunks), H, W)
        last_err = st.err or ("timeout" if st.timed_out else last_err)
    raise RuntimeError(last_err[-200:] if last_err else "Failed to decode frames")


def _motion_pass(st, W, H, sample_step):
    """Camera motion on the fly from an FFStream: O(1) frames in memory + ≤160 sampled frames for composition."""
    import cv2
    chunk = st.read()
    if chunk is None:
        return None
    prev = np.frombuffer(chunk, np.uint8).reshape(H, W)
    samples = {0: prev.copy()}
    dx_l, dy_l, da_l, ds_l, conf_l = [0.0], [0.0], [0.0], [1.0], [1.0]
    act_l = [0.0]           # subject activity: share of pixels still changing once the camera move is compensated
    mg = max(4, W // 40)    # border left out (warp edges, rolling-shutter skew at the frame edge)
    luma_l = [float(prev[mg:-mg, mg:-mg].mean())]   # brightness: dark / blown stretches + auto-exposure ramps (montage)
    hanning_win = cv2.createHanningWindow((W, H), cv2.CV_32F)
    frame_idx = 0
    while True:
        chunk = st.read()
        if chunk is None:
            break
        frame_idx += 1
        cur = np.frombuffer(chunk, np.uint8).reshape(H, W)
        if frame_idx % sample_step == 0 and len(samples) < 160:
            samples[frame_idx] = cur.copy()
        luma_l.append(float(cur[mg:-mg, mg:-mg].mean()))

        p0 = cv2.goodFeaturesToTrack(prev, maxCorners=220, qualityLevel=0.01, minDistance=10, blockSize=7)
        ok = False
        if p0 is not None and len(p0) >= 12:
            p1, stt, _ = cv2.calcOpticalFlowPyrLK(prev, cur, p0, None, winSize=(21, 21), maxLevel=3,
                                                  criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03))
            g = stt.reshape(-1) == 1
            if g.sum() >= 10:
                M, inl = cv2.estimateAffinePartial2D(p0[g], p1[g], method=cv2.RANSAC, ransacReprojThreshold=1.5, maxIters=500)
                if M is not None:
                    r = float(inl.sum()) / max(1, g.sum())
                    dx_l.append(M[0, 2])
                    dy_l.append(M[1, 2])
                    da_l.append(math.degrees(math.atan2(M[1, 0], M[0, 0])))
                    ds_l.append(math.hypot(M[0, 0], M[1, 0]))
                    conf_l.append(r if g.sum() >= 25 else r * 0.6)
                    warped = cv2.warpAffine(prev, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
                    diff = cv2.absdiff(cur[mg:-mg, mg:-mg], warped[mg:-mg, mg:-mg])
                    act_l.append(float((diff > 18).mean()))
                    ok = True
        if not ok:
            (x, y), resp = cv2.phaseCorrelate(prev.astype(np.float32) * hanning_win, cur.astype(np.float32) * hanning_win)
            dx_l.append(x)
            dy_l.append(y)
            da_l.append(0.0)
            ds_l.append(1.0)
            conf_l.append(min(0.3, resp))
            act_l.append(act_l[-1])          # no reliable camera model: carry the last value
        prev = cur
    return np.array(dx_l), np.array(dy_l), np.array(da_l), np.array(ds_l), np.array(conf_l), samples, np.array(act_l), np.array(luma_l)


def stream_camera_motion_and_samples(path, w_disp, h_disp, cuda=True, fps=FPS, width=WIDTH, dur_hint=0.0, rot=0, info_out=None,
                                     qc_audio=None):
    """Stream-decode video at `fps` and compute camera motion on the fly.
    Keeps only O(1) memory: current + previous frame (250 KB) and ~100 candidate sample frames (<= 15 MB)
    for composition, completely avoiding giant pipe buffers and MemoryError.
    A decode that fails (rc ≠ 0, watchdog timeout) or stops early (< 90 % of the expected frames) is redone on the CPU;
    when every attempt is short, the longest one is used (damaged / truncated file)."""
    W = width
    H = _out_h(width, w_disp, h_disp)
    frame_bytes = W * H

    expected_frames = int(dur_hint * fps) if dur_hint else 0
    sample_step = max(int(round(fps * 1.5)), expected_frames // 120) if expected_frames > 0 else int(round(fps * 2.0))

    from .qc import err_line
    last_err = ""
    gpu_err = ""
    best = None
    for dec, hw, pre in decode_attempts(cuda, rot):
        st = FFStream(_gray_cmd(hw, pre, path, fps, W, H, qc_audio), frame_bytes, decode_timeout(dur_hint),
                      full_log=qc_audio is not None)
        try:
            res = _motion_pass(st, W, H, sample_step)
        except Exception as e:
            st.close(kill=True)
            last_err = str(e)
            continue
        st.close()
        n = len(res[0]) if res else 0
        complete = bool(res) and st.rc == 0 and not st.timed_out and (not expected_frames or n >= 0.9 * expected_frames)
        if st.timed_out:
            last_err = f"{dec}: quá thời gian giải mã ({decode_timeout(dur_hint):.0f}s)"
        elif st.rc != 0:
            last_err = st.err[-2000:]
        if dec != "cpu" and not complete:
            gpu_err = gpu_err or f"{dec}: {err_line(last_err) or 'giải mã thiếu khung'}"
        if res and (best is None or n > best[1]):
            best = (res, n, dec, st.err if complete else "", complete)
        if complete:
            break                                  # complete decode
    if best is None or best[1] < 2:
        raise RuntimeError(f"Decode failed: {last_err[-200:]}")
    (dx, dy, da, ds, conf, samples, act, luma), n, dec, log, complete = best
    if info_out is not None:
        info_out.update(decoder=dec, frames=n, expected=expected_frames, complete=complete,
                        gpu_err=gpu_err if dec == "cpu" else "", log=log, act=act, luma=luma)
    return dx, dy, da, ds, conf, W, H, n, samples


def camera_motion(frames):
    import cv2
    n = len(frames)
    dx = np.zeros(n); dy = np.zeros(n); da = np.zeros(n); ds = np.ones(n); conf = np.ones(n)
    prev = frames[0]
    for i in range(1, n):
        cur = frames[i]
        p0 = cv2.goodFeaturesToTrack(prev, maxCorners=220, qualityLevel=0.01, minDistance=10, blockSize=7)
        ok = False
        if p0 is not None and len(p0) >= 12:
            p1, st, _ = cv2.calcOpticalFlowPyrLK(prev, cur, p0, None, winSize=(21, 21), maxLevel=3,
                                                 criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03))
            g = st.reshape(-1) == 1
            if g.sum() >= 10:
                M, inl = cv2.estimateAffinePartial2D(p0[g], p1[g], method=cv2.RANSAC, ransacReprojThreshold=1.5, maxIters=500)
                if M is not None:
                    r = float(inl.sum()) / max(1, g.sum())
                    dx[i], dy[i] = M[0, 2], M[1, 2]
                    da[i] = math.degrees(math.atan2(M[1, 0], M[0, 0]))
                    ds[i] = math.hypot(M[0, 0], M[1, 0])
                    conf[i] = r if g.sum() >= 25 else r * 0.6
                    ok = True
        if not ok:                                    # texture-less frame: fall back to phase correlation, low confidence
            win = cv2.createHanningWindow(prev.shape[::-1], cv2.CV_32F)
            (x, y), resp = cv2.phaseCorrelate(prev.astype(np.float32) * win, cur.astype(np.float32) * win)
            dx[i], dy[i], conf[i] = x, y, min(0.3, resp)
        prev = cur
    return dx, dy, da, ds, conf


def _gauss(x, sigma):
    if sigma <= 0 or len(x) < 3:
        return x.copy()
    r = int(3 * sigma)
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2); k /= k.sum()
    xp = np.pad(x, r, mode="edge")
    return np.convolve(xp, k, "valid")


def _runs(mask):
    out, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            out.append((i, j)); i = j
        else:
            i += 1
    return out


def _med3(x):
    if len(x) < 3:
        return x.copy()
    y = x.copy()
    y[1:-1] = np.median(np.stack([x[:-2], x[1:-1], x[2:]]), axis=0)
    return y


def analyze_motion_vectors(dx, dy, da, ds, conf, W, H, cfg=CFG, fps=FPS, src_fps=30.0):
    n = len(dx)
    D = math.hypot(W, H)
    js = float(fps) / FPS                                    # jitter in 15-fps-equivalent units (thresholds scale with fps)
    dx, dy, da = _med3(dx), _med3(dy), _med3(da)          # single-frame tracking glitches
    sv = 0.2 * fps
    vx, vy, va = _gauss(dx, sv), _gauss(dy, sv), _gauss(da, sv)
    rx, ry, ra = dx - vx, dy - vy, da - va
    win = max(3, int(0.5 * fps))
    k = np.ones(win) / win
    jit = np.sqrt(np.convolve(rx ** 2 + ry ** 2, k, "same")) / D * 100.0 * js     # % of diagonal per frame (15 fps eq.)
    rjit = np.sqrt(np.convolve(ra ** 2, k, "same")) * js                          # degrees per frame (15 fps eq.)
    gx, gy = _gauss(dx, 0.5 * fps), _gauss(dy, 0.5 * fps)
    speed = np.hypot(gx, gy) * fps / D * 100.0             # intended move, % diag / s
    pan_x, pan_y = gx * fps / W * 100.0, gy * fps / H * 100.0   # signed intended move, % of frame width / height per s
    zoom = np.abs(_gauss(np.log(np.maximum(ds, 1e-6)), 0.5 * fps)) * fps * 100
    cf = np.convolve(conf, k, "same")
    jit = jit / (1.0 + speed / cfg["speed_comp"])          # judge shake relative to the intended move
    state = np.full(n, "handheld", dtype=object)
    state[(jit < cfg["stable_pct"]) & (rjit < cfg["rot_ok_deg"] * 0.5)] = "stable"
    state[(jit >= cfg["ok_pct"]) | (rjit >= cfg["rot_ok_deg"])] = "shaky"
    state[speed >= cfg["fast_pct_s"]] = "fast"
    # texture-less frames are "not judged" — but never hide a measured shake / fast move
    state[(cf < cfg["lowconf"]) & (jit >= cfg["stable_pct"]) & (state == "handheld")] = "lowconf"
    if src_fps >= 48:     # 50/60p: shake that halves at ≤50 % speed is fine as slow-motion (editor's usual move)
        state[(state == "shaky") & (jit < 2 * cfg["ok_pct"]) & (rjit < 2 * cfg["rot_ok_deg"])] = "slowmo"
    return {"jit": jit, "rjit": rjit, "speed": speed, "zoom": zoom, "conf": cf, "state": state, "D": D, "fps": float(fps),
            "pan_x": pan_x, "pan_y": pan_y}


def analyze_motion(frames, cfg=CFG, fps=FPS, src_fps=30.0):
    """Shake = high-frequency part of the camera VELOCITY (a pan's start/stop is a single step and barely counts,
    unlike a path-residual metric that paints ±1–2 s around every intended move as 'shaky')."""
    H, W = frames[0].shape
    dx, dy, da, ds, conf = camera_motion(frames)
    return analyze_motion_vectors(dx, dy, da, ds, conf, W, H, cfg=cfg, fps=fps, src_fps=src_fps)


def ranges_from(M, dur, cfg=CFG, fps=FPS):
    st = M["state"]; n = len(st)
    t = lambda i: round(min(dur, i / fps), 2)
    # merge short state flickers (< 0.3 s) into neighbours
    lab = st.copy()
    runs = []
    i = 0
    while i < n:
        j = i
        while j < n and lab[j] == lab[i]:
            j += 1
        runs.append([i, j, lab[i]]); i = j
    for r in runs:
        if r[1] - r[0] < 0.3 * fps and len(runs) > 1:
            r[2] = None
    last = None
    for r in runs:
        if r[2] is None:
            r[2] = last or "handheld"
        last = r[2]
    merged = []
    for a, b, s in runs:
        if merged and merged[-1][2] == s:
            merged[-1][1] = b
        else:
            merged.append([a, b, s])
    segs = []
    for a, b, s in merged:
        seg = {"start": t(a), "end": t(b), "state": s, "jit": round(float(M["jit"][a:b].mean()), 3),
               "speed": round(float(M["speed"][a:b].mean()), 1)}
        segs.append(seg)
    long_lc = np.zeros(n, bool)                  # lowconf = not judged: a short blip is fine, a long stretch is not usable
    for a, b in _runs(st == "lowconf"):
        if (b - a) / fps > cfg.get("lowconf_max_s", 0.5):
            long_lc[a:b] = True
    good = np.isin(st, ["stable", "handheld", "lowconf"]) & ~long_lc
    e = int(cfg["trim_edge"] * fps)
    if e:
        good[:e] = False; good[-e:] = False
    usable = [[t(a), t(b)] for a, b in _runs(good) if (b - a) / fps >= cfg["min_usable"]]
    slow = np.isin(st, ["stable", "handheld", "lowconf", "slowmo"]) & ~long_lc
    if e:
        slow[:e] = False; slow[-e:] = False
    usable_slowmo = [[t(a), t(b)] for a, b in _runs(slow) if (b - a) / fps >= cfg["min_usable"] and (st[a:b] == "slowmo").any()]
    # "ranges" = unusable at any speed (avoid lists downstream); slow-mo-only stretches go to "ranges_slowmo"
    # (shaky in real time, good at ≤ 50 % — b-roll pickers must not avoid them)
    bad, bad_slow = [], []
    for s in segs:
        if s["state"] in ("shaky", "fast", "slowmo"):
            r = [s["start"], s["end"], round(max(s["jit"] / CFG["ok_pct"], s["speed"] / CFG["fast_pct_s"]), 2)]
            (bad_slow if s["state"] == "slowmo" else bad).append(r)
    # best window: highest stability, not glued to the clip edges
    best = None
    L = max(int(cfg["best_min"] * fps), 1)
    if n > L:
        score = -(M["jit"] + 0.5 * M["rjit"] * 0.3) - np.where(np.isin(st, ["shaky", "fast"]), 5.0, 0.0)
        edge = np.zeros(n); edge[: int(1.0 * fps)] -= 0.08; edge[-int(1.0 * fps):] -= 0.08
        c = np.convolve(score + edge, np.ones(L) / L, "valid")
        a = int(np.argmax(c))
        best = [t(a), t(a + L)]
        # extend while it stays usable
        for u in usable:
            if u[0] <= best[0] and best[1] <= u[1]:
                best = [u[0], min(u[1], u[0] + max(4.0, cfg["best_min"]))] if u[1] - u[0] <= 4.0 else best
                break
    return segs, usable, bad, best, usable_slowmo, bad_slow


# ------------------------------------------------------------------ composition
FACE_REPO, FACE_FILE = "opencv/face_detection_yunet", "face_detection_yunet_2023mar.onnx"   # OpenCV Zoo, MIT, 227 KB
FACE_SCORE = 0.7
_FACE_SRC = None                 # ("yn", model path) | ("haar", cascade path) | False — decided once per process
_FACE_LOCK = threading.Lock()
_FACE_TL = threading.local()     # one detector per thread: FaceDetectorYN keeps per-call state (input size)


def _face_source():
    """YuNet through cv2.FaceDetectorYN (OpenCV ≥ 4.5.4); the model downloads once into the Hugging Face cache.
    OpenCV 5 dropped the Haar cascades the suite used before — _faces() then returned [] on every frame without
    a word, so composition and reframe ran blind. Haar stays as the fallback on an OpenCV 4 without the model."""
    global _FACE_SRC
    with _FACE_LOCK:
        if _FACE_SRC is None:
            _FACE_SRC = False
            import cv2
            if hasattr(cv2, "FaceDetectorYN"):
                try:
                    from huggingface_hub import hf_hub_download, try_to_load_from_cache
                    p = try_to_load_from_cache(FACE_REPO, FACE_FILE)
                    if not isinstance(p, str) or not os.path.isfile(p):
                        p = hf_hub_download(FACE_REPO, FACE_FILE)
                    _FACE_SRC = ("yn", p)
                except Exception as e:
                    print(f"stability: không nạp được YuNet ({str(e)[:80]}) — bố cục không thấy khuôn mặt", flush=True)
            if not _FACE_SRC and hasattr(cv2, "CascadeClassifier"):
                hp = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
                if os.path.isfile(hp):
                    _FACE_SRC = ("haar", hp)
    return _FACE_SRC


def _faces(img):
    """[(x, y, w, h)] of the faces in a gray or BGR frame (empty when no detector is available)."""
    import cv2
    src = _face_source()
    if not src:
        return []
    det = getattr(_FACE_TL, "det", None)
    if det is None:
        det = (cv2.FaceDetectorYN.create(src[1], "", (320, 320), FACE_SCORE, 0.3, 50) if src[0] == "yn"
               else cv2.CascadeClassifier(src[1]))
        _FACE_TL.det = det
    h, w = img.shape[:2]
    if src[0] == "haar":
        g = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        f = det.detectMultiScale(g, scaleFactor=1.15, minNeighbors=5, minSize=(max(18, w // 25),) * 2)
        return [] if f is None else [tuple(map(int, x)) for x in f]
    im = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if img.ndim == 2 else img
    det.setInputSize((w, h))
    _, f = det.detect(np.ascontiguousarray(im))
    if f is None:
        return []
    return [tuple(int(round(v)) for v in r[:4]) for r in f if r[2] >= max(10, w // 48)]


def _saliency_centroid(img):
    """Spectral-residual saliency (Hou & Zhang 2007) on a 64-px thumbnail → weighted centroid (0..1)."""
    import cv2
    s = cv2.resize(img, (64, int(64 * img.shape[0] / img.shape[1])), interpolation=cv2.INTER_AREA).astype(np.float32)
    F = np.fft.fft2(s)
    la = np.log(np.abs(F) + 1e-6)
    sr = la - cv2.blur(la, (3, 3))
    sal = np.abs(np.fft.ifft2(np.exp(sr + 1j * np.angle(F)))) ** 2
    sal = cv2.GaussianBlur(sal, (0, 0), 2.5)
    sal = np.maximum(sal - np.percentile(sal, 70), 0)
    tot = sal.sum() + 1e-9
    ys, xs = np.mgrid[0:sal.shape[0], 0:sal.shape[1]]
    return float((sal * xs).sum() / tot / sal.shape[1]), float((sal * ys).sum() / tot / sal.shape[0])


def _tilt(img):
    import cv2
    e = cv2.Canny(img, 60, 160)
    L = cv2.HoughLinesP(e, 1, np.pi / 360, threshold=40, minLineLength=img.shape[1] // 5, maxLineGap=6)
    if L is None:
        return None
    ang, wts = [], []
    for x1, y1, x2, y2 in np.asarray(L).reshape(-1, 4):
        a = math.degrees(math.atan2(y2 - y1, x2 - x1))
        a = ((a + 90) % 180) - 90                     # -90..90
        for ref in (0, 90, -90):
            d = a - ref
            if abs(d) <= 8:
                ang.append(d); wts.append(math.hypot(x2 - x1, y2 - y1))
    if len(ang) < 5:
        return None
    o = np.argsort(ang); ang = np.array(ang)[o]; wts = np.array(wts)[o]
    c = np.cumsum(wts) / wts.sum()
    q1, med, q3 = (float(ang[np.searchsorted(c, q)]) for q in (0.25, 0.5, 0.75))
    if q3 - q1 > 1.2:            # lines disagree (close-up, organic shapes) → no reliable horizon
        return None
    return round(med, 2)


def _placement(img):
    """→ (subject placement 0..1, faces found): the largest face (else the saliency centroid) near a third / the
    centre, and a head near the top 14 % when the face is not a close-up"""
    H, W = img.shape[:2]
    faces = _faces(img)
    if faces:
        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
        cx, cy = (x + w / 2) / W, (y + h / 2) / H
        head_ok = 1.0 - min(1.0, abs(y / H - 0.14) / 0.2) if h / H < 0.45 else 0.8
    else:
        cx, cy = _saliency_centroid(img)
        head_ok = 1.0
    pts = [(1 / 3, 1 / 3), (2 / 3, 1 / 3), (1 / 3, 2 / 3), (2 / 3, 2 / 3), (0.5, 0.5), (0.5, 0.42)]
    d = min(math.hypot(cx - px, cy - py) for px, py in pts)
    return max(0.0, 1.0 - d / 0.35) * (0.6 + 0.4 * head_ok), len(faces)


def _comp_score(c):
    tilt_ok = 1.0 if c.get("tilt_deg") is None else max(0.0, 1.0 - max(0.0, abs(c["tilt_deg"]) - 0.5) / 3.0)
    return round(100 * (0.35 * c.get("sharp", 50) / 100 + 0.25 * c.get("exposure", 50) / 100
                        + 0.25 * c.get("placement", 50) / 100 + 0.15 * tilt_ok))


def refresh_faces(path, r, dur=None, width=WIDTH):
    """Cached v1 composition (no faces) → placement / faces / score / quality from 3 fresh frames of the same
    stretch; sharpness, exposure and tilt keep their cached values (same as a full re-analysis would give)."""
    import cv2
    from .ai import frames as _fr
    comp = dict(r.get("composition") or {})
    if not comp:
        return False
    dur = float(dur or 0) or max([b for _, b in (r.get("usable") or [[0, 1]])])
    a, b = (r.get("best") or ((r.get("usable") or [[0, dur]])[0]))
    ims = [im for im in _fr.full(path, list(np.linspace(a + 0.1, max(a + 0.2, b - 0.1), 3)), width) if im is not None]
    if not ims:
        return False
    pf = [_placement(cv2.cvtColor(im, cv2.COLOR_RGB2GRAY)) for im in ims]
    comp["placement"] = round(float(np.mean([p for p, _ in pf])) * 100)
    comp["faces"] = int(max(n for _, n in pf))
    comp["score"] = _comp_score(comp)
    r["composition"] = comp
    r["quality"] = round(0.55 * float(r.get("stability", 50)) + 0.45 * comp["score"])
    r["comp_v"] = COMP_V
    return True


def composition(frames_sel):
    import cv2
    if not len(frames_sel):
        return {}
    sh, ex, pl, tl, fc = [], [], [], [], []
    for img in frames_sel:
        H, W = img.shape
        lap = cv2.Laplacian(img, cv2.CV_64F).var()
        sh.append(min(1.0, max(0.0, (math.log10(lap + 1) - 1.0) / 2.0)))            # ~10 → 0, ~1000 → 1
        m = img.mean() / 255.0
        clip = float(((img > 250) | (img < 5)).mean())
        ex.append(max(0.0, 1.0 - abs(m - 0.47) * 2.2) * (1.0 - min(1.0, clip * 4)))
        p_, n_ = _placement(img)
        pl.append(p_)
        fc.append(n_)
        t = _tilt(img)
        if t is not None:
            tl.append(t)
    tilt = round(float(np.median(tl)), 2) if tl else None
    tilt_conf = bool(len(tl) >= 2 and (max(tl) - min(tl)) <= 0.6)     # the same horizon angle in ≥2 of 3 frames
    tilt_ok = 1.0 if tilt is None else max(0.0, 1.0 - max(0.0, abs(tilt) - 0.5) / 3.0)
    sharp, expo, place = float(np.mean(sh)), float(np.mean(ex)), float(np.mean(pl))
    score = 100 * (0.35 * sharp + 0.25 * expo + 0.25 * place + 0.15 * tilt_ok)
    return {"sharp": round(sharp * 100), "exposure": round(expo * 100), "placement": round(place * 100),
            "tilt_deg": tilt, "tilt_conf": tilt_conf,
            "rotate_fix": (round(-tilt, 1) if tilt is not None and tilt_conf and 0.6 <= abs(tilt) <= 4 else 0),
            "faces": int(max(fc) if fc else 0), "score": round(score)}


def act_curve(act, afps):
    """Per-frame subject activity → ACT_HZ samples, 0…255 (= 0…51 % of the picture still moving after camera
    compensation). nxcore.montage keeps cuts out of the middle of an action with it."""
    if act is None or not len(act):
        return []
    a = np.asarray(act, float)
    step = afps / ACT_HZ
    n = int(len(a) / step)
    out = [a[int(i * step):max(int(i * step) + 1, int((i + 1) * step))].mean() for i in range(n)]
    return [int(min(255, round(v * 500))) for v in out]


def luma_curve(luma, afps):
    """Per-frame mean brightness of the analysis frames → ACT_HZ samples, 0…255. nxcore.montage keeps shots out of
    dark / blown stretches and out of the camera's auto-exposure ramps (a sunset clip that is black for 5 s, then right)."""
    if luma is None or not len(luma):
        return []
    a = np.asarray(luma, float)
    step = afps / ACT_HZ
    n = int(len(a) / step)
    return [int(round(a[int(i * step):max(int(i * step) + 1, int((i + 1) * step))].mean())) for i in range(n)]


def pan_curve(px, py, afps):
    """Intended camera move → ACT_HZ samples [x, y] in % of the frame per second (int, ±127). nxcore.montage keeps
    two neighbouring shots from panning in opposite directions (screen-direction continuity)."""
    if px is None or not len(px):
        return []
    step = afps / ACT_HZ
    n = int(len(px) / step)
    out = []
    for i in range(n):
        a, b = int(i * step), max(int(i * step) + 1, int((i + 1) * step))
        out.append([int(max(-127, min(127, round(float(np.mean(px[a:b])))))),
                    int(max(-127, min(127, round(float(np.mean(py[a:b]))))))])
    return out


def _fused_qc(dinfo, info, path):
    """QC result from the fused decode's log (None when that decode was incomplete → nxcore.qc redoes the clip)."""
    if not dinfo.get("complete") or not dinfo.get("log"):
        return None
    from . import qc as _qc
    dur = float(info.get("dur") or 0)
    r = _qc.parse(dinfo["log"], dur)
    r["decoder"] = dinfo.get("decoder", "")
    if dinfo.get("gpu_err"):
        r["gpu_err"] = dinfo["gpu_err"]
    return _qc.classify_freeze(r, dur, path, r["decoder"] != "cpu")


def analyze_clip(path, info, cuda=True, cfg=CFG, with_qc=False):
    """with_qc: the same decode also produces the clip's QC entry (returned under "_qc", popped by analyze_all)."""
    dur_hint = float(info.get("dur") or 0.0)
    src_fps = float(info.get("fps") or 30)
    afps = analysis_fps(src_fps)
    dinfo = {}
    dx, dy, da, ds, conf, W, H, n_frames, samples = stream_camera_motion_and_samples(
        path, info.get("w"), info.get("h"), cuda=cuda, fps=afps, width=WIDTH, dur_hint=dur_hint,
        rot=info.get("rot", 0), info_out=dinfo, qc_audio=bool(info.get("audio", True)) if with_qc else None)
    qc_r = _fused_qc(dinfo, info, path) if with_qc else None
    dinfo.pop("log", None)
    dur = float(info.get("dur") or n_frames / afps)
    if n_frames < 4:
        return {"ranges": [], "ranges_slowmo": [], "usable": [[0, round(dur, 2)]] if dur > 0.5 else [], "usable_slowmo": [],
                "segments": [], "best": None, "stability": 50, "composition": {}, "quality": 50, "note": "clip quá ngắn",
                "v": STAB_V, "comp_v": COMP_V, "_qc": qc_r}
    M = analyze_motion_vectors(dx, dy, da, ds, conf, W, H, cfg, fps=afps, src_fps=src_fps)
    segs, usable, bad, best, usable_slowmo, bad_slow = ranges_from(M, dur, cfg, fps=afps)
    st = M["state"]
    frac = {k: round(float((st == k).mean()), 3) for k in ("stable", "handheld", "shaky", "slowmo", "fast", "lowconf")}
    usable_s = sum(b - a for a, b in usable)
    jit_med = float(np.median(M["jit"]))
    stab = 100 * (0.55 * max(0.0, 1 - jit_med / (cfg["ok_pct"] * 1.6)) + 0.30 * (usable_s / max(dur, 0.1)) + 0.15 * (frac["stable"]))
    # composition sampled inside the best (or longest usable) stretch
    a, b = (best or (usable[0] if usable else [0, dur]))
    ts_list = np.linspace(a + 0.1, max(a + 0.2, b - 0.1), 3)
    if samples:
        comp_frames = [samples[min(samples.keys(), key=lambda k: abs(k - int(round(t * afps))))] for t in ts_list]
    else:
        comp_frames = []
    comp = composition(comp_frames)
    quality = round(0.55 * stab + 0.45 * comp.get("score", 50))
    code = {"stable": "s", "handheld": "h", "shaky": "y", "slowmo": "m", "fast": "f", "lowconf": "l"}
    strip = [code[st[min(len(st) - 1, int(i * afps))]] for i in np.arange(0, dur, 0.5)]   # 2 Hz state strip for the UI
    return {"ranges": bad, "ranges_slowmo": bad_slow, "usable": usable, "usable_slowmo": usable_slowmo, "segments": segs,
            "best": best, "frac": frac,
            "jit_median": round(jit_med, 3), "p90": round(float(np.percentile(M["jit"], 90)), 3),
            "median": round(jit_med, 3), "stability": round(stab), "composition": comp, "quality": quality,
            "strip": "".join(strip), "afps": round(afps, 3), "decoder": dinfo.get("decoder", ""), "v": STAB_V, "comp_v": COMP_V,
            "gpu_err": dinfo.get("gpu_err", ""), "_qc": qc_r, "act": act_curve(dinfo.get("act"), afps), "act_hz": ACT_HZ,
            "pan": pan_curve(M.get("pan_x"), M.get("pan_y"), afps), "luma": luma_curve(dinfo.get("luma"), afps)}


def _skipped_entry(m, role):
    dur = float(m.get("dur") or 0)
    return {
        "ranges": [], "ranges_slowmo": [], "usable": [[0.0, dur]], "usable_slowmo": [], "segments": [],
        "best": [0.0, min(dur, 4.0)],
        "frac": {"stable": 1.0, "handheld": 0.0, "shaky": 0.0, "slowmo": 0.0, "fast": 0.0, "lowconf": 0.0},
        "jit_median": 0.05, "stability": 95,
        "composition": {"score": 85, "sharpness": 80, "rotate_fix": 0.0}, "quality": 90,
        "v": STAB_V, "key": m.get("key"), "skipped": True, "note": f"bỏ qua quét rung (role {role} theo preset)"
    }


def analyze_all(src, workers=0, cuda=True, force=False, files=None, progress=print, preset=None, with_qc=False):
    """with_qc: clips whose qc.json entry is missing / stale get QC from the same decode (written to qc.json);
    nxcore.qc run afterwards only has the leftovers (clips skipped here, failed or incomplete decodes)."""
    W = os.path.join(src, "_nexys_work")
    media = jsonio.load(os.path.join(W, "media.json"), None)
    if media is None:
        raise RuntimeError("media.json chưa có / lỗi — chạy probe trước")
    sp = os.path.join(W, "shake.json")
    res = jsonio.load(sp, {}) or {}

    skip_roles = set()
    if not preset and os.path.exists(os.path.join(W, "preset.json")):
        preset = (jsonio.load(os.path.join(W, "preset.json"), {}) or {}).get("id")
    if preset:
        try:
            from .presets import get_preset
            cfg = get_preset(preset)
            skip_roles = set(cfg.get("skip_shake_roles", []))
        except Exception:
            pass

    todo, recomp = [], []
    skipped_count = 0
    for f in (files or list(media)):
        if f not in media:
            continue
        m = media[f]
        role = m.get("role", "b_roll")
        if role in skip_roles:
            res[f] = _skipped_entry(m, role)       # marked "skipped" → re-analysed once the preset/role no longer skips it
            skipped_count += 1
            continue
        cur = res.get(f) or {}
        if force or cur.get("v") != STAB_V or cur.get("key") != m.get("key") or cur.get("skipped"):
            todo.append(f)
        elif cur.get("comp_v", 1) != COMP_V and cur.get("composition"):
            recomp.append(f)

    if skipped_count:
        progress(f"stability: Bỏ qua {skipped_count} clip ({', '.join(skip_roles)}) theo preset — gán nhãn tripod cố định")
    if recomp:                                  # composition v1 saw no face: refresh that part only (3 frames / clip)
        progress(f"bố cục: tìm khuôn mặt lại cho {len(recomp)} clip đã phân tích (3 khung mỗi clip, không giải mã lại cả clip)")
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as ex:
            ok = list(ex.map(lambda f: refresh_faces(os.path.join(src, f), res[f], media[f].get("dur")), recomp))
        progress(f"bố cục: xong {sum(ok)}/{len(recomp)} clip · có mặt người: {sum(1 for f in recomp if (res[f].get('composition') or {}).get('faces'))}")
        jsonio.save_atomic(sp, res, indent=None)
    progress(json.dumps({"stage": "stability", "total": len(todo), "cached": len(media) - len(todo) - skipped_count}))
    from . import gpu as _gpu
    from . import qc as _qc
    qp = os.path.join(W, "qc.json")
    qcd, need_qc = {}, set()
    if with_qc and todo:
        qcd = jsonio.load(qp, {}) or {}
        for f in todo:
            try:
                if force or (qcd.get(f) or {}).get("key") != _qc._key(os.path.join(src, f)):
                    need_qc.add(f)
            except OSError:
                pass
        if need_qc:
            progress(f"{len(need_qc)} clip làm QC luôn trong cùng lần giải mã (1 lần đọc file thay vì 2)")
    if todo:
        g, c = _qc.resolve_workers(workers, cuda)
        progress(f"{g} luồng GPU ({_gpu.name()}) + {c} luồng CPU song song")
        if g + c > 1:
            try:                                   # threads run in parallel already → OpenCV must not fan out per call too
                import cv2
                cv2.setNumThreads(1)
            except Exception:
                pass

    def one(f, on_gpu):
        return analyze_clip(os.path.join(src, f), media[f], cuda and on_gpu, with_qc=f in need_qc)

    fresh, qc_new = [], 0
    k = 0
    for f, r in (_gpu.run_hybrid(todo, one, g, c, cost=lambda f: _qc.clip_cost(media[f])) if todo else []):
        k += 1
        if isinstance(r, Exception):
            progress(f"[{k}/{len(todo)}] {f} lỗi: {r}")
            continue
        q = r.pop("_qc", None)
        if q is not None:
            q["key"] = _qc._key(os.path.join(src, f))
            qcd[f] = q
            qc_new += 1
        r["key"] = media[f].get("key")
        if not r.get("gpu_err"):
            r.pop("gpu_err", None)
        res[f] = r
        fresh.append(r)
        cmp_ = r.get("composition", {})
        progress(f"[{k}/{len(todo)}] {f} · ổn định {r['stability']} · bố cục {cmp_.get('score', '-')}"
                 + (f" · xoay {cmp_['rotate_fix']:+.1f}°" if cmp_.get("rotate_fix") else "")
                 + (f" · rung {len(r['ranges'])} đoạn" if r["ranges"] else "")
                 + (" · +QC" if q is not None else ""))
        if k % 5 == 0 or k == len(todo):
            jsonio.save_atomic(sp, res, indent=None)
            if qc_new:
                jsonio.save_atomic(qp, qcd, indent=None)
    w = _qc.fallback_warning(fresh, len(fresh), "Stability")
    if w:
        progress(w)
    res = {f: v for f, v in res.items() if f in media}
    jsonio.save_atomic(sp, res, indent=None)
    if qc_new:
        jsonio.save_atomic(qp, qcd, indent=None)
    return res


def compare_takes(keys, res):
    """Rank near-duplicate takes: stability 55% + composition 45% (= quality)."""
    rows = [(res.get(k, {}).get("quality", 0), k) for k in keys]
    rows.sort(reverse=True)
    return [k for _, k in rows]


def main():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--workers", type=int, default=0, help="0 = tự chọn theo máy (GPU + CPU song song)")
    ap.add_argument("--no-cuda", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--preset", default="", help="talking_head | interview_casting | vlog_travel | shortform_9x16 | event_recap")
    ap.add_argument("--files", nargs="*")
    ap.add_argument("--with-qc", action="store_true", help="làm QC trong cùng lần giải mã (ghi qc.json)")
    a = ap.parse_args()
    analyze_all(a.src, a.workers, not a.no_cuda, a.force, a.files, progress=lambda m: print(m, flush=True), preset=a.preset,
                with_qc=a.with_qc)
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
