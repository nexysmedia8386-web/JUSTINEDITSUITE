"""Frame grabbing for the AI models.

  at(path, times, size)          → [H×W×3 uint8 RGB] square center crops near given seconds (SigLIP frames)
  full(path, times, width)       → whole frames, aspect kept (reframe / colour analysis)
  stream(path, fps, w, h)        → iterator of H×W×3 uint8 RGB frames at `fps` (TransNetV2: 48×27)

NVIDIA + PyNvVideoCodec → NVDEC random access in-process (frames to RAM, cv2 crop); otherwise ffmpeg with the machine's
hwaccel (nxcore.gpu): one keyframe-only pass per clip, or a seek per frame for 1–2 frames.
"""
import subprocess

import numpy as np

from .. import gpu as _gpu
from . import enabled, has


def _nv_ok():
    return enabled("decode") and has("PyNvVideoCodec") and _gpu.accel() == "cuda"


def _crop(img, size):
    import cv2
    h, w = img.shape[:2]
    s = size / min(h, w)
    img = cv2.resize(img, (max(size, round(w * s)), max(size, round(h * s))), interpolation=cv2.INTER_AREA)
    h, w = img.shape[:2]
    y, x = (h - size) // 2, (w - size) // 2
    return np.ascontiguousarray(img[y:y + size, x:x + size])


def _nv_at(path, times, size):
    """NVDEC random access (PyNvVideoCodec). Frames come back to RAM and are cropped with cv2: only the video
    decoder engine is used, not the CUDA cores Premiere / Ollama keep busy (GPU-side torch resize measured the same
    speed but depends on them)."""
    import PyNvVideoCodec as nvc
    dec = nvc.SimpleDecoder(path, gpu_id=0, use_device_memory=False, output_color_type=nvc.OutputColorType.RGB)
    meta = dec.get_stream_metadata()
    fps = float(getattr(meta, "average_fps", 0) or 30.0)
    n = int(getattr(meta, "num_frames", 0) or len(dec))
    return [_crop(np.from_dlpack(dec[int(min(max(0, round(t * fps)), n - 1))]), size) for t in times]


def _ff_one(path, t, size):
    """One keyframe-seeked frame on the CPU (a GPU context per single frame costs more than it saves).
    A multi-input ffmpeg (12 seeks → vstack) hung on 4K HEVC: framesync buffers full-res frames of every input."""
    vf = f"scale={size}:{size}:force_original_aspect_ratio=increase:flags=area,crop={size}:{size},format=rgb24"
    cmd = ["ffmpeg", "-v", "error", "-nostdin"] + _gpu.cpu_args() + ["-noaccurate_seek", "-ss", f"{t:.3f}", "-i", path,
           "-an", "-sn", "-dn", "-frames:v", "1", "-vf", vf, "-f", "rawvideo", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        return None
    fb = size * size * 3
    if len(p.stdout) < fb:
        return None
    return np.frombuffer(p.stdout[:fb], np.uint8).reshape(size, size, 3)


def keyframe_times(path):
    """Keyframe timestamps from the packet list (demux only, no decoding) — ~1 s even for a 20-min clip."""
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", path]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return []
    out = []
    for line in p.stdout.splitlines():
        t, _, fl = line.partition(",")
        if "K" in fl:
            try:
                out.append(float(t))
            except ValueError:
                pass
    return sorted(set(out))


def _kf_at(path, times, size, max_gap=2.5):
    """One keyframe-only decode for all `times`: each time takes its nearest keyframe (a picture to *describe* a
    shot, not an exact frame). Only the picked keyframes are scaled (on the GPU) and downloaded.
    → list (same length as times, None where no keyframe is within max_gap s) or None when unusable."""
    kts = keyframe_times(path)
    if not kts:
        return None
    ka = np.asarray(kts)
    pick = []
    for t in times:
        j = int(np.argmin(np.abs(ka - t)))
        pick.append(kts[j] if abs(kts[j] - t) <= max_gap else None)
    want = sorted({k for k in pick if k is not None})
    if not want:
        return None
    sel = "+".join(f"lt(abs(t-{k:.4f}),0.002)" for k in want)
    crop = f"scale={size}:{size}:force_original_aspect_ratio=increase:flags=area,crop={size}:{size},format=rgb24"
    tries = []
    if _gpu.accel() == "cuda":                     # select on GPU frames, shrink on the GPU, download only the picks
        tries.append((["-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-skip_frame", "nokey"],
                      f"select='{sel}',scale_cuda=w=-2:h={size * 2}:format=nv12,hwdownload,format=nv12,{crop}"))
    if _gpu.hwaccel_args(True):
        tries.append((_gpu.hwaccel_args(True) + ["-skip_frame", "nokey"], f"select='{sel}',{crop}"))
    tries.append((_gpu.cpu_args() + ["-skip_frame", "nokey"], f"select='{sel}',{crop}"))
    fb = size * size * 3
    for hw, vf in tries:
        cmd = ["ffmpeg", "-v", "error", "-nostdin"] + hw + ["-i", path, "-an", "-sn", "-dn", "-vf", vf,
                                                             "-fps_mode", "passthrough", "-f", "rawvideo", "-"]
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=600)
        except subprocess.TimeoutExpired:
            continue
        n = len(p.stdout) // fb
        if p.returncode == 0 and n == len(want):
            a = np.frombuffer(p.stdout[:n * fb], np.uint8).reshape(n, size, size, 3)
            got = {k: a[i] for i, k in enumerate(want)}
            return [got[k] if k is not None else None for k in pick]
    return None


def at(path, times, size=384, workers=4):
    """RGB center crops (size×size) at / near `times` seconds.
    Measured interleaved, 247 frames over 24 clips of every length, GPU 20–40 % busy: PyNvVideoCodec 16 s · keyframe
    pass 52 s · ffmpeg seek per frame 214 s (one 6-min clip: 5 s · 39 s · 92 s; one thumbnail: 0.05 s vs 0.84 s).
    With Premiere holding the GPU at 100 % PyNvVideoCodec could stall (an earlier run), hence the fallbacks:
    NVIDIA → PyNvVideoCodec · else keyframe pass (> 2 frames) · else one ffmpeg seek per frame.
    Missing frames are dropped (caller checks the count)."""
    times = [max(0.0, float(t)) for t in times]
    if not times:
        return []
    if _nv_ok():
        try:
            return _nv_at(path, times, size)
        except Exception:
            pass
    # a keyframe pass decodes every keyframe of the clip (~5–8 s for 6 min of DJI 4K): worth it for many frames,
    # not for a single thumbnail — 1–2 frames go straight to a seek (~0.5 s each)
    kf = None
    if len(times) > 2:
        try:
            kf = _kf_at(path, times, size)
        except Exception:
            kf = None
    if kf is not None and all(x is not None for x in kf):
        return kf
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(times)))) as ex:
        res = list(ex.map(lambda t: _ff_one(path, t, size), times))
    return [r for r in res if r is not None]


def full(path, times, width=320, workers=4, rot=None):
    """Whole frames (aspect kept, `width` px wide, RGB) at `times` seconds — reframe / colour analysis, and what a
    vision LLM looks at (a square crop can cut the subject off). Missing frames come back as None (same length).
    rot = the clip's display rotation (media.json "rot") when the caller knows it → NVDEC random access for > 3 frames
    of one clip (NVDEC ignores rotation metadata: the frame is turned here, np.rot90 k = rot / 90 — checked against
    ffmpeg's auto-rotation on iPhone −90° clips). 1–3 frames: an ffmpeg seek each is faster than opening a 4K decoder
    (2 frames: 0.78 s vs 2.78 s). Unknown rotation → ffmpeg."""
    if rot is not None and int(round(float(rot))) % 90 == 0 and len(times) > 3 and _nv_ok():
        try:
            import cv2
            import PyNvVideoCodec as nvc
            dec = nvc.SimpleDecoder(path, gpu_id=0, use_device_memory=False, output_color_type=nvc.OutputColorType.RGB)
            meta = dec.get_stream_metadata()
            fps = float(getattr(meta, "average_fps", 0) or 30.0)
            n = int(getattr(meta, "num_frames", 0) or len(dec))
            out = []
            for t in times:
                im = np.from_dlpack(dec[int(min(max(0, round(float(t) * fps)), n - 1))])
                k = int(round(float(rot) / 90.0)) % 4
                if k:
                    im = np.ascontiguousarray(np.rot90(im, k))      # cv2 wants contiguous memory
                h, w = im.shape[:2]
                out.append(np.ascontiguousarray(cv2.resize(im, (width, max(2, round(h * width / w / 2) * 2)),
                                                           interpolation=cv2.INTER_AREA)))
            return out
        except Exception:
            pass

    def one(t):
        vf = f"scale={width}:-2:flags=area,format=rgb24"
        cmd = ["ffmpeg", "-v", "error", "-nostdin"] + _gpu.cpu_args() + ["-noaccurate_seek", "-ss", f"{max(0.0, t):.3f}", "-i", path,
               "-an", "-sn", "-dn", "-frames:v", "1", "-vf", vf, "-f", "image2pipe", "-vcodec", "png", "-"]
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            return None
        if not p.stdout:
            return None
        import cv2
        im = cv2.imdecode(np.frombuffer(p.stdout, np.uint8), cv2.IMREAD_COLOR)
        return None if im is None else cv2.cvtColor(im, cv2.COLOR_BGR2RGB)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(times) or 1))) as ex:
        return list(ex.map(one, [float(t) for t in times]))


def stream(path, fps=None, w=48, h=27, timeout=None):
    """RGB frames w×h (no aspect kept — TransNetV2 expects exactly 48×27) at `fps` (None = every frame)."""
    vf = (f"fps={fps}," if fps else "") + f"scale={w}:{h}:flags=area,format=rgb24"
    fb = w * h * 3
    tries = []
    if _gpu.accel() == "cuda":                       # shrink on the GPU: every 4K frame is decoded, only 192 px leave the card
        tries.append((["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"],
                      (f"fps={fps}," if fps else "") + f"scale_cuda=w=192:h=-2:format=nv12,hwdownload,format=nv12,"
                      f"scale={w}:{h}:flags=area,format=rgb24"))
    if _gpu.hwaccel_args(True):
        tries.append((_gpu.hwaccel_args(True), vf))
    tries.append(([], vf))
    for hw, vf in tries:
        cmd = ["ffmpeg", "-v", "error", "-nostdin"] + hw + ["-i", path, "-an", "-sn", "-dn", "-vf", vf, "-f", "rawvideo", "-"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=fb * 64)
        got = 0
        try:
            while True:
                b = p.stdout.read(fb)
                if not b or len(b) < fb:
                    break
                got += 1
                yield np.frombuffer(b, np.uint8).reshape(h, w, 3)
        finally:
            p.stdout.close()
            rc = p.wait()
        if got or rc == 0:
            return
