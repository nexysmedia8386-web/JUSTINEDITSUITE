"""Single-pass FFmpeg QC for every clip — Justin EDIT Suite.

One decode per clip (downscaled 10 fps gray for video) runs every detector at once:
  video : blurdetect (focus loss), blackdetect (lens cap / black), freezedetect (frozen frame),
          signalstats YAVG (under/over exposure)
  audio : silencedetect, ebur128 (integrated loudness + true peak)
Clips are processed in parallel; results are cached in _nexys_work/qc.json keyed by file size+mtime,
so re-running only analyses new or changed clips.

CLI:  python -m nxcore.qc "F:/NEXIS/HR 2509" [--workers 4] [--no-cuda] [--force]
Output per clip:
  {"blur": [[a,b,score]], "black": [[a,b]], "freeze": [[a,b]], "static": [[a,b]], "dark": [[a,b]], "bright": [[a,b]],
   "silence": [[a,b]], "lufs": -23.1, "true_peak": -1.2, "blur_median": 3.1, "key": "size:mtime"}
"""
import argparse
import json
import os
import re
import subprocess
import sys

from . import jsonio
from . import gpu as _gpu
from .gpu import hwaccel_args as _hwargs   # CUDA / VideoToolbox / D3D11VA — whatever this machine has

VEXT = (".mp4", ".mov", ".mxf", ".mts", ".m4v")

_PTS = re.compile(r"pts_time:([\d.]+)")
_BLUR = re.compile(r"lavfi\.blur=([\d.]+)")
_YAVG = re.compile(r"lavfi\.signalstats\.YAVG=([\d.]+)")
_BLACK = re.compile(r"black_start:([\d.]+)\s+black_end:([\d.]+)")
_FRZ_S = re.compile(r"freeze_start:\s*([\d.]+)")
_FRZ_E = re.compile(r"freeze_end:\s*([\d.]+)")
_SIL_S = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_E = re.compile(r"silence_end:\s*([\d.]+)")
_LUFS = re.compile(r"I:\s+(-?[\d.]+|-inf)\s+LUFS")
_TPK = re.compile(r"Peak:\s+(-?[\d.]+|-inf)\s+dBFS")


def _key(path):
    st = os.stat(path)
    return f"{st.st_size}:{int(st.st_mtime)}"


# CUDA decode keeps frames on the GPU: fps=10 drops frames there, scale_cuda shrinks them (and converts 10-bit p010 →
# nv12) on the GPU, and only the small sampled frames are downloaded. A bare "hwdownload,format=nv12|p010le|…" picks
# nv12 for 10-bit HEVC → "Invalid output format" → every DJI/GoPro 10-bit clip silently fell back to CPU.
# Fallback chain: GPU frames → plain "-hwaccel cuda" (frames in RAM, any pix_fmt) → CPU.
# NX_HW_FRAMES=0 (or HW_FRAMES = False) skips the GPU-frames step.
HW_FRAMES = os.environ.get("NX_HW_FRAMES", "1") != "0"
HW_PRE = "scale_cuda=w=min(640\\,iw):h=-2:format=nv12,hwdownload,format=nv12,"


def hw_attempts(cuda, rot=0, frames_pre=None):
    """→ [(decoder, input args, filter prefix placed right after fps=)], CPU always last.
    The GPU step follows the machine (gpu.accel()): NVIDIA → CUDA, Mac → VideoToolbox, other Windows GPUs → D3D11VA."""
    out = []
    a = _gpu.accel() if cuda else ""
    if a == "cuda":
        try:
            rotated = int(float(rot or 0)) % 360 != 0
        except (TypeError, ValueError):
            rotated = False
        if HW_FRAMES and not rotated:       # rotated clips: ffmpeg's auto-transpose needs frames in RAM
            out.append(("cuda", ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"], HW_PRE if frames_pre is None else frames_pre))
        out.append(("cuda", ["-hwaccel", "cuda"], ""))
    elif a:                                  # videotoolbox / d3d11va: frames come back to RAM
        out.append((a, ["-hwaccel", a], ""))
    out.append(("cpu", _gpu.cpu_args(), ""))
    return out


# Chỉ đọc KEYFRAME (-discard:v nokey bỏ gói ngay ở demuxer, decoder không phải parse): QC video chỉ cần ~1 khung/giây
# cho mờ / đen / phơi sáng / đứng hình (nghi đứng hình được kiểm lại dày 10 fps ở _strict_freeze). Clip 4K 4:2:2 10-bit
# 73 s: 16 s → 1.1 s. NX_QC_KEYFRAMES=0 → giải mã đủ khung như cũ.
QC_KEYFRAMES = os.environ.get("NX_QC_KEYFRAMES", "1") != "0"


def heavy_source(info):
    """Footage nặng decode (máy quay 4K bitrate cao, thường H.264 4:2:2 10-bit — NVDEC đa số card không giải được,
    CPU ~5× realtime) → QC chỉ đọc keyframe. Điện thoại / 1080p decode GPU nhanh → giữ 10 fps như cũ."""
    try:
        px = int(info.get("raw_w") or info.get("w") or 0) * int(info.get("raw_h") or info.get("h") or 0)
        br = float(info.get("bitrate") or 0)
    except (TypeError, ValueError):
        return False
    return px >= 0.9 * 3840 * 2160 and br >= 120e6


def big_frame(info):
    """≥ ~4K: on a machine without a usable GPU decoder QC reads keyframes only (≈10× faster on CPU)."""
    try:
        px = int(info.get("raw_w") or info.get("w") or 0) * int(info.get("raw_h") or info.get("h") or 0)
    except (TypeError, ValueError):
        return False
    return px >= 0.9 * 3840 * 2160


def qc_graph(vin, chain, has_audio):
    """QC detectors on the video stream `vin` (+ [0:a]) → (filter_complex text, -map args). `chain` runs first
    (fps / hwdownload…); the gray 320 px copy feeds blur / black+freeze / exposure. Shared with the fused
    QC+stability decode (nxcore.stability)."""
    v = (f"{vin}{chain}scale=320:-2,format=gray,split=3[a][b][c];"
         f"[a]blurdetect=block_width=32:block_height=32:planes=1,metadata=mode=print:key=lavfi.blur[a1];"
         f"[b]blackdetect=d=0.3:pix_th=0.10,freezedetect=n=-55dB:d=1.2[b1];"
         f"[c]signalstats,metadata=mode=print:key=lavfi.signalstats.YAVG[c1]")
    maps = ["-map", "[a1]", "-map", "[b1]", "-map", "[c1]"]
    if has_audio:
        # silence on a 16 kHz copy (cheap); loudness + true peak at the NATIVE rate (K-weighting / 4× oversampled
        # true peak are only right on the real signal). framelog=quiet: summary only, no per-frame lines.
        v += (";[0:a]asplit=2[sa][la];[sa]aresample=16000,silencedetect=n=-38dB:d=0.4[aout];"
              "[la]ebur128=peak=true:framelog=quiet[lout]")
        maps += ["-map", "[aout]", "-map", "[lout]"]
    return v, maps


def build_cmd(path, has_audio, cuda, fps=10, pre=None, keyframes=None):
    """cuda: True/False (legacy) or the input args list of one hw_attempts() entry; pre: its filter prefix."""
    hw = _hwargs(cuda) if isinstance(cuda, bool) else list(cuda)
    pre = pre or ""
    kf = QC_KEYFRAMES if keyframes is None else keyframes
    if kf:
        hw = hw + ["-discard:v", "nokey"]
    v, maps = qc_graph("[0:v]", f"{'' if kf else f'fps={fps},'}{pre}", has_audio)
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-nostdin", "-loglevel", "info"] + hw
    return cmd + ["-i", path, "-filter_complex", v] + maps + ["-f", "null", "-"]


def qc_timeout(dur):
    dur = float(dur or 0)
    return 7200.0 if dur <= 0 else max(300.0, 120.0 + 6.0 * dur)


def _ranges(series, bad, min_len, fps=10):
    """series: [(t, value)], bad(value)->bool  ->  [[start, end, worst]] runs longer than min_len."""
    out, cur = [], None
    for t, v in series:
        if bad(v):
            if cur is None:
                cur = [t, t, v]
            cur[1] = t + 1.0 / fps
            cur[2] = max(cur[2], v)
        elif cur is not None:
            if cur[1] - cur[0] >= min_len:
                out.append([round(cur[0], 2), round(cur[1], 2), round(cur[2], 2)])
            cur = None
    if cur is not None and cur[1] - cur[0] >= min_len:
        out.append([round(cur[0], 2), round(cur[1], 2), round(cur[2], 2)])
    return out


def parse(stderr, dur):
    blur, yavg, t = [], [], None
    for line in stderr.splitlines():
        m = _PTS.search(line)
        if m:
            t = float(m.group(1))
            continue
        m = _BLUR.search(line)
        if m and t is not None:
            blur.append((t, float(m.group(1))))
            continue
        m = _YAVG.search(line)
        if m and t is not None:
            yavg.append((t, float(m.group(1))))
    # black / freeze / silence intervals
    black = [[round(float(a), 2), round(float(b), 2)] for a, b in _BLACK.findall(stderr)]
    fs, fe = [float(x) for x in _FRZ_S.findall(stderr)], [float(x) for x in _FRZ_E.findall(stderr)]
    freeze = [[round(a, 2), round(fe[i] if i < len(fe) else dur, 2)] for i, a in enumerate(fs)]
    ss, se = [max(0.0, float(x)) for x in _SIL_S.findall(stderr)], [float(x) for x in _SIL_E.findall(stderr)]
    silence = [[round(a, 2), round(se[i] if i < len(se) else dur, 2)] for i, a in enumerate(ss)]
    # nhịp mẫu thật (10 fps, hoặc ~1 khung/giây khi chỉ đọc keyframe) → độ dài mỗi mẫu trong _ranges
    ts_ = sorted(t for t, _ in (blur or yavg))
    gaps = sorted(b - a for a, b in zip(ts_, ts_[1:]) if b > a)
    sfps = 1.0 / gaps[len(gaps) // 2] if gaps else 10.0
    sparse = sfps < 3.0
    if sparse:                               # vài khung đầu ra sát nhau trước keyframe đầu → giữ 1 mẫu / 0.3 s
        def thin(ser):
            out = []
            for t_, v_ in ser:
                if out and t_ - out[-1][0] < 0.3:
                    out[-1] = (out[-1][0], max(out[-1][1], v_))
                else:
                    out.append((t_, v_))
            return out
        blur, yavg = thin(blur), thin(yavg)
    # blur: relative to the clip itself (lens/scene dependent) — robust median + MAD
    res = {"black": black, "freeze": freeze, "silence": silence, "blur": [], "dark": [], "bright": []}
    if blur:
        vals = sorted(v for _, v in blur)
        med = vals[len(vals) // 2]
        mad = sorted(abs(v - med) for v in vals)[len(vals) // 2] or 0.1
        # mẫu thưa (keyframe) làm MAD phình → chặn trần để ngưỡng tương đương bản 10 fps
        thr = max(med * 1.35, med + 3.0 * (min(mad, 0.12 * med) if sparse else mad))
        res["blur"] = _ranges(blur, lambda v: v > thr, 0.5, sfps)
        res["blur_median"] = round(med, 2)
    if yavg:
        res["dark"] = _ranges(yavg, lambda v: v < 28, 0.8, sfps)
        res["bright"] = _ranges(yavg, lambda v: v > 235, 0.8, sfps)
    lu, tp = _LUFS.findall(stderr), _TPK.findall(stderr)
    res["lufs"] = None if not lu or lu[-1] == "-inf" else float(lu[-1])
    res["true_peak"] = None if not tp or tp[-1] == "-inf" else float(tp[-1])
    return res


QC_V = 2
STRICT_N = "-68dB"


def _strict_freeze(path, a, b, cuda):
    """Re-check a suspected freeze at 960 px with a very low noise floor: a locked-off camera still has sensor
    noise (→ not frozen); a real freeze repeats identical frames (recording/decoder glitch)."""
    L = max(0.5, b - a)
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-nostdin"] + _hwargs(cuda) + \
          ["-ss", f"{a:.2f}", "-t", f"{L:.2f}", "-i", path, "-an",
           "-vf", f"fps=10,scale=960:-2,format=gray,freezedetect=n={STRICT_N}:d=1.0", "-f", "null", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120 + 6 * L)
    except subprocess.TimeoutExpired:
        if cuda:
            return _strict_freeze(path, a, b, False)
        return []
    if p.returncode != 0 and cuda:
        return _strict_freeze(path, a, b, False)
    fs, fe = [float(x) for x in _FRZ_S.findall(p.stderr)], [float(x) for x in _FRZ_E.findall(p.stderr)]
    return [[round(a + x, 2), round(a + (fe[i] if i < len(fe) else b - a), 2)] for i, x in enumerate(fs)]


def _merge_runs(rs, gap=1.5):
    """Back-to-back freeze pieces (freezedetect restarts every few seconds on a calm shot) → [a, b, pieces]."""
    out = []
    for a, b in sorted([list(r[:2]) for r in rs]):
        if out and a - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], b); out[-1][2] += 1
        else:
            out.append([a, b, 1])
    return out


def classify_freeze(r, dur, path=None, cuda=True):
    """freeze → only real frozen frames; calm stretches of a continuous take (locked-off camera, slow scene) → "static".
    Rules: freeze covering ≥ 40 % of the clip, a run ≥ max(8 s, 50 % of clip), or ≥ 3 back-to-back pieces = continuous
    take. Short isolated pieces are re-checked strictly (sensor noise → static) when the file is available."""
    if r.get("qc_v", 1) >= QC_V and path is None:
        return r
    raw = list(r.get("freeze", []))
    frz, static = [], list(r.get("static", []))
    runs = _merge_runs(raw)
    cover = sum(b - a for a, b, _ in runs)
    for a, b, n in runs:
        L = b - a
        if (dur and cover >= 0.4 * dur) or L >= max(8.0, 0.5 * (dur or 0)) or n >= 3:
            static.append([round(a, 2), round(b, 2)])
        elif path is not None:
            hit = _strict_freeze(path, a, b, cuda)
            frz += hit
            if not hit:
                static.append([round(a, 2), round(b, 2)])
        else:
            frz.append([round(a, 2), round(b, 2)])
    r["freeze"], r["static"] = frz, sorted(static)
    if path is not None:
        r["qc_v"] = QC_V
    return r


def err_line(stderr):
    """The most telling line of an ffmpeg error log (for the 'GPU fell back to CPU' warning)."""
    lines = [l.strip() for l in (stderr or "").splitlines() if l.strip()]
    for l in lines:
        if re.search(r"(invalid|not supported|unsupported|failed|no device|cannot|error)", l, re.I) and "Conversion failed" not in l:
            return l[:200]
    return (lines[-1] if lines else "")[:200]


def analyse(path, info, cuda=True):
    dur = info.get("dur", 0)
    to = qc_timeout(dur)
    last = ""
    gpu_err = ""
    no_gpu = not (cuda and _gpu.accel())
    kf = QC_KEYFRAMES and (heavy_source(info) or (no_gpu and big_frame(info)))
    for dec, hw, pre in hw_attempts(cuda, info.get("rot", 0)):
        cmd = build_cmd(path, info.get("audio", True), hw, pre=pre, keyframes=kf)
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=to)
        except subprocess.TimeoutExpired:
            last = f"{dec}: quá thời gian QC ({to:.0f}s)"
            gpu_err = gpu_err or (last if dec != "cpu" else "")
            continue
        if p.returncode != 0:                 # no GPU decoder for this codec / chroma format -> next step / CPU
            last = (p.stderr or "")[-400:]
            if dec != "cpu":
                gpu_err = gpu_err or f"{dec}: {err_line(p.stderr)}"
            continue
        r = parse(p.stderr, dur)
        r["decoder"] = dec
        if dec == "cpu" and gpu_err:
            r["gpu_err"] = gpu_err
        return classify_freeze(r, dur, path, dec != "cpu")
    raise RuntimeError(last or "QC lỗi")


def fallback_warning(results, total, stage="QC"):
    """→ a one-line warning when the GPU failed and clips silently decoded on the CPU (the bug that hid for months)."""
    errs = [r.get("gpu_err") for r in results if r.get("gpu_err")]
    if not errs or not total:
        return ""
    common = max(set(errs), key=errs.count)
    return (f"⚠ {stage}: {len(errs)}/{total} clip phải giải mã bằng CPU vì GPU lỗi (chậm hơn nhiều) — {common}. "
            f"Kiểm tra driver / bản ffmpeg; NX_HWACCEL=cpu để tắt hẳn GPU.")


def clip_cost(info):
    """Relative decode cost (duration × pixels) — the hybrid queue gives heavy clips to the GPU, light ones to the CPU."""
    try:
        return float(info.get("dur") or 0) * int(info.get("raw_w") or info.get("w") or 1920) * int(info.get("raw_h") or info.get("h") or 1080)
    except (TypeError, ValueError):
        return 0.0


def resolve_workers(workers, cuda):
    """workers > 0 = explicit total (all on the GPU when there is one); 0 = auto (gpu.auto_workers)."""
    if workers and workers > 0:
        return (workers, 0) if (cuda and _gpu.accel()) else (0, workers)
    return _gpu.auto_workers(cuda)


def bad_ranges(qc_entry, kinds=("blur", "black", "freeze", "dark", "bright")):
    """All unusable-picture intervals of a clip as [[a,b], ...] (used by take scoring and b-roll windows)."""
    out = []
    for k in kinds:
        out += [[r[0], r[1]] for r in (qc_entry or {}).get(k, [])]
    return sorted(out)


def _safe_dump(data, path):
    try:
        jsonio.save_atomic(path, data)
    except Exception:
        pass


def run(src, workers=0, cuda=True, force=False, files=None, progress=print):
    W = os.path.join(src, "_nexys_work")
    mp = os.path.join(W, "media.json")
    try:
        media = json.load(open(mp, encoding="utf-8"))
    except Exception as e:
        progress(f"media.json lỗi ({e}).")
        return {}
    qp = os.path.join(W, "qc.json")
    qc = {}
    if not force and os.path.exists(qp) and os.path.getsize(qp) > 0:
        try:
            with open(qp, encoding="utf-8") as fp:
                qc = json.load(fp)
        except Exception as e:
            progress(f"qc.json lỗi ({e}) -> làm mới cache.")
            qc = {}
    todo = []
    for f, info in media.items():
        if files and f not in files:
            continue
        path = os.path.join(src, f)
        if not os.path.exists(path):
            continue
        k = _key(path)
        if qc.get(f, {}).get("key") == k:
            if qc[f].get("freeze") and qc[f].get("qc_v", 1) < QC_V:
                classify_freeze(qc[f], info.get("dur", 0))       # old cache: long takes are not freezes
            continue
        todo.append((f, path, info, k))
    progress(json.dumps({"stage": "qc", "total": len(todo), "cached": len(media) - len(todo)}))
    if not todo:
        _safe_dump(qc, qp)
    if not todo:
        return qc
    g, c = resolve_workers(workers, cuda)
    progress(f"{g} luồng GPU ({_gpu.name()}) + {c} luồng CPU song song")
    done, fresh = 0, []
    # GPU workers take the heavy clips, CPU workers the light ones from the other end of the queue
    for (f, path, info, k), r in _gpu.run_hybrid(todo, lambda it, on_gpu: analyse(it[1], it[2], cuda and on_gpu),
                                                 g, c, cost=lambda it: clip_cost(it[2])):
        done += 1
        if isinstance(r, Exception):
            progress(f"[{done}/{len(todo)}] {f} QC lỗi: {r}")
            continue
        r["key"] = k
        qc[f] = r
        fresh.append(r)
        flags = [f"{n}:{len(r[n])}" for n in ("blur", "black", "freeze", "dark", "bright") if r.get(n)]
        progress(f"[{done}/{len(todo)}] {f} {r['decoder']} lufs={r['lufs']} peak={r['true_peak']} {' '.join(flags)}")
        if done % 5 == 0 or done == len(todo):
            _safe_dump(qc, qp)  # save as we go atomically (every 5 clips: qc.json can be MBs on big folders)
    w = fallback_warning(fresh, len(fresh))
    if w:
        progress(w)
    _safe_dump(qc, qp)
    return qc


def main():
    ap = argparse.ArgumentParser(description="Single-pass FFmpeg QC (blur/black/freeze/exposure/silence/loudness)")
    ap.add_argument("src")
    ap.add_argument("--workers", type=int, default=0, help="0 = tự chọn theo máy (GPU + CPU song song)")
    ap.add_argument("--no-cuda", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--files", nargs="*")
    a = ap.parse_args()
    run(a.src, a.workers, not a.no_cuda, a.force, a.files, progress=lambda m: print(m, flush=True))
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
