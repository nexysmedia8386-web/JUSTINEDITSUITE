"""GPU selection shared by the engine — Windows/Linux: NVIDIA CUDA · macOS: Apple VideoToolbox (decode) + MLX (Whisper).

The panel's "GPU" setting arrives as the legacy `cuda` flag everywhere; on a Mac it means "use the Apple GPU":
  • ffmpeg decode   → -hwaccel videotoolbox (H.264/HEVC incl. 10-bit 4:2:2 on Apple Silicon), CPU fallback as before
  • Whisper         → mlx-whisper on the Metal GPU (faster-whisper/CTranslate2 has no Metal backend → CPU only on Mac)
"""
import os
import platform
import shutil
import subprocess
import sys
import threading
from collections import deque

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
IS_APPLE_SILICON = IS_MAC and platform.machine() in ("arm64", "aarch64")
CPUS = os.cpu_count() or 4

_cache = {}


def _once(key, fn):
    if key not in _cache:
        try:
            _cache[key] = fn()
        except Exception:
            _cache[key] = None
    return _cache[key]


def has_nvidia():
    """An NVIDIA card + driver is present (nvidia-smi lists a GPU). Cached per process. NX_HWACCEL=cpu|d3d11va|cuda overrides."""
    forced = os.environ.get("NX_HWACCEL", "").lower()
    if forced:
        return forced == "cuda"

    def probe():
        exe = shutil.which("nvidia-smi")
        if not exe:
            return False
        p = subprocess.run([exe, "-L"], capture_output=True, text=True, timeout=10)
        return p.returncode == 0 and "GPU" in (p.stdout or "")
    return bool(_once("nvidia", probe))


def vram():
    """{"total", "used", "free"} MB of GPU 0 right now (nvidia-smi), or None without an NVIDIA driver. Not cached:
    Premiere, browsers and Ollama change it all the time. On Windows (WDDM) per-process numbers are not available, so
    "used" is everything on the card."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        p = subprocess.run([exe, "--query-gpu=memory.total,memory.used,memory.free", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        t, u, f = (int(float(x)) for x in p.stdout.strip().splitlines()[0].split(","))
        return {"total": t, "used": u, "free": f}
    except Exception:
        return None


def _ff_has(kind, name_):
    def probe():
        p = subprocess.run(["ffmpeg", "-hide_banner", f"-{kind}"], capture_output=True, text=True, timeout=15)
        return p.stdout or ""
    return name_ in (_once("ff_" + kind, probe) or "")


def accel():
    """The hardware decoder this machine can actually use: cuda (NVIDIA) · videotoolbox (Mac) · d3d11va (Windows, any
    AMD / Intel / NVIDIA-less GPU) · "" (none). NX_HWACCEL=<name|cpu> forces one."""
    forced = os.environ.get("NX_HWACCEL", "").lower()
    if forced:
        return "" if forced in ("cpu", "none", "0") else forced
    if IS_MAC:
        return "videotoolbox"
    if has_nvidia():
        return "cuda"
    if IS_WIN and _ff_has("hwaccels", "d3d11va"):
        return "d3d11va"
    return ""


def name():
    return accel() or "cpu"


def hwaccel_args(use_gpu):
    """ffmpeg input args for hardware decode (frames come back to RAM); [] when no usable GPU decoder exists, so
    machines without NVIDIA no longer pay a failing "-hwaccel cuda" attempt per clip."""
    a = accel() if use_gpu else ""
    return ["-hwaccel", a] if a else []


def cpu_args():
    """Input args for a CPU decode: caps ffmpeg's threads (NX_FF_THREADS, set by analyze from the worker count) so N
    parallel decoders don't each spawn one thread per core and thrash."""
    t = os.environ.get("NX_FF_THREADS", "")
    return ["-threads", t] if t.isdigit() and int(t) > 0 else []


def auto_workers(use_gpu):
    """(gpu_workers, cpu_workers) for the decode-heavy stages (QC, stability).
    NVIDIA: NVDEC decodes ~8–10 realtime 4K streams; the per-clip CPU analysis (filters, optical flow) is the other half
    of the pipeline → 3–6 GPU workers. Remaining cores run extra CPU-decode workers on the light clips.
    Mac (VideoToolbox, unified memory): more parallel decodes. No GPU: all CPU, half the cores (each ffmpeg ≥ 2 threads).
    NX_GPU_WORKERS / NX_CPU_WORKERS override."""
    a = accel() if use_gpu else ""
    if a == "cuda":                           # measured (RTX 3070, 4K HEVC 10-bit): 4 GPU workers beat 6 — NVDEC saturates
        g = max(2, min(4, CPUS // 6))
        c = max(0, CPUS // 8 - 1)
    elif a == "videotoolbox":
        g, c = max(2, min(8, CPUS // 2)), 0
    elif a:                                   # d3d11va: one shared video engine, frames come back to RAM
        g = max(2, min(4, CPUS // 4))
        c = max(0, (CPUS - 4 * g) // 6)
    else:
        g, c = 0, max(2, min(8, CPUS // 2))
    g = int(os.environ.get("NX_GPU_WORKERS", g))
    c = int(os.environ.get("NX_CPU_WORKERS", c))
    if g + c < 1:
        c = 1
    return g, c


def ff_threads(workers):
    return max(2, CPUS // max(1, workers))


def run_hybrid(items, fn, gpu_workers, cpu_workers, cost=None):
    """Run fn(item, use_gpu) over items with GPU and CPU workers side by side; yields (item, result | Exception).
    GPU workers take the heaviest clips first, CPU workers the lightest from the other end of the same queue, so the
    slow CPU decodes never end up as the tail. A CPU worker stops taking clips once only the last few heavy ones are
    left (it would finish after the GPU)."""
    q = deque(sorted(items, key=cost or (lambda _: 0), reverse=True))
    lock = threading.Lock()
    out = deque()
    ready = threading.Condition(lock)
    tail = max(2, gpu_workers * 2)
    alive = [0]

    def worker(gpu):
        while True:
            with lock:
                if not q or (not gpu and gpu_workers and len(q) <= tail):
                    alive[0] -= 1
                    ready.notify_all()
                    return
                it = q.popleft() if gpu else q.pop()
            try:
                r = fn(it, gpu)
            except Exception as e:            # noqa: BLE001 — reported per clip by the caller
                r = e
            with lock:
                out.append((it, r))
                ready.notify_all()

    ths = [threading.Thread(target=worker, args=(True,), daemon=True) for _ in range(gpu_workers)]
    ths += [threading.Thread(target=worker, args=(False,), daemon=True) for _ in range(cpu_workers)]
    alive[0] = len(ths)
    for t in ths:
        t.start()
    while True:
        with lock:
            while not out and alive[0] > 0:
                ready.wait(1.0)
            if out:
                it, r = out.popleft()
            elif alive[0] <= 0:
                break
            else:
                continue
        yield it, r


# Whisper model ids → MLX conversions on the Hugging Face hub (mlx-community)
MLX_REPOS = {
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "turbo": "mlx-community/whisper-large-v3-turbo",
    "medium": "mlx-community/whisper-medium-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "base": "mlx-community/whisper-base-mlx",
}


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class MLXWhisper:
    """Apple Silicon Whisper (mlx-whisper) behind the faster-whisper call shape used by nxcut transcribe:
    .transcribe(audio, language=, vad_filter=, vad_parameters=, word_timestamps=, initial_prompt=, …) → (segments, info)
    with segment.start/end/text/avg_logprob/no_speech_prob/compression_ratio/words[word,start,end,probability]."""

    def __init__(self, model):
        import mlx_whisper                      # pip install mlx-whisper (Apple Silicon only)
        self._mw = mlx_whisper
        self.repo = MLX_REPOS.get(model, model if "/" in str(model) else MLX_REPOS["large-v3-turbo"])

    def transcribe(self, audio, language=None, vad_filter=False, vad_parameters=None, condition_on_previous_text=False,
                   word_timestamps=True, initial_prompt=None, beam_size=None, batch_size=None, **_):
        clip = "0"
        if vad_filter:                          # same Silero VAD as on Windows → only speech goes to the GPU
            try:
                from faster_whisper.vad import get_speech_timestamps, VadOptions
                vp = dict(vad_parameters or {})
                ts = get_speech_timestamps(audio, VadOptions(**vp))
                if not ts:
                    return [], None
                clip = [x for t in ts for x in (round(t["start"] / 16000.0, 3), round(t["end"] / 16000.0, 3))]
            except Exception:
                clip = "0"
        # beam search is not implemented in mlx-whisper (greedy + temperature fallback) — beam_size is ignored
        r = self._mw.transcribe(audio, path_or_hf_repo=self.repo, language=language, word_timestamps=word_timestamps,
                                condition_on_previous_text=condition_on_previous_text, initial_prompt=initial_prompt,
                                clip_timestamps=clip, verbose=None)
        segs = []
        for s in r.get("segments") or []:
            words = [_Obj(start=float(w.get("start", 0)), end=float(w.get("end", 0)), word=str(w.get("word", "")),
                          probability=float(w.get("probability", 1.0))) for w in (s.get("words") or [])]
            segs.append(_Obj(start=float(s.get("start", 0)), end=float(s.get("end", 0)), text=str(s.get("text", "")),
                             avg_logprob=float(s.get("avg_logprob", 0.0)), no_speech_prob=float(s.get("no_speech_prob", 0.0)),
                             compression_ratio=float(s.get("compression_ratio", 1.0)), words=words))
        return segs, None
