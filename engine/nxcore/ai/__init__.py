"""Optional AI pack (PyTorch models) — Justin EDIT Suite.

Every feature here is OPTIONAL: when torch / a model / a package is missing the caller keeps its classic numpy/ffmpeg
path, so the suite still runs on any machine. `python -m nxcore.ai` prints what is available.

  beats      beat_this (CPJKU, 2024)            beats + downbeats                 → nxcore.music
  structure  allin1 (mir-aidj)                   sections intro/verse/chorus…     → nxcore.music (if installable)
  embed      SigLIP2 (Google)                    text → footage search, near-dups → nxcore.ai.embed
  quality    SigLIP2 zero-shot (CLIP-IQA style)  technical + aesthetic rank       → nxcore.montage
             (DOVER was evaluated: S-Lab non-commercial license + torch 1.13 pin → not used)
  shots      TransNetV2                          shot boundaries (incl. dissolves) → nxcore.segments
  align      wav2vec2 Vietnamese CTC             word timings re-aligned           → nxcore transcribe
  decode     PyNvVideoCodec (NVIDIA)             NVDEC frame access, 3× faster     → nxcore.ai.frames

Disable everything with NX_AI=0, one feature with NX_AI_<NAME>=0 (e.g. NX_AI_BEATS=0).
Models download once to the Hugging Face cache (HF_HOME) or ~/.cache/justin_ai.
"""
import importlib.util
import os
import threading

CACHE = os.environ.get("NX_AI_CACHE") or os.path.join(os.path.expanduser("~"), ".cache", "justin_ai")

_lock = threading.Lock()
_models = {}


def enabled(name):
    if os.environ.get("NX_AI", "1") == "0":
        return False
    return os.environ.get(f"NX_AI_{name.upper()}", "1") != "0"


def has(mod):
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def torch_ok():
    return has("torch")


def device(prefer_gpu=True):
    """'cuda' | 'mps' | 'cpu'."""
    if not torch_ok():
        return "cpu"
    import torch
    if prefer_gpu and torch.cuda.is_available():
        return "cuda"
    if prefer_gpu and getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def cached(key, factory):
    """One model instance per process (loaded lazily, thread-safe)."""
    with _lock:
        if key not in _models:
            _models[key] = factory()
        return _models[key]


def free(key=None):
    """Drop cached models (and the CUDA cache) — call before handing the GPU to whisper / Ollama."""
    with _lock:
        if key is None:
            _models.clear()
        else:
            _models.pop(key, None)
    if torch_ok():
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def status():
    """→ {feature: (available, detail)} — what this machine can use right now."""
    t = torch_ok()
    dev = device() if t else "-"
    out = {
        "torch": (t, f"device {dev}" if t else "pip install torch (xem README AI)"),
        "beats": (t and has("beat_this"), "beat_this"),
        "structure": (t and has("allin1"), "allin1 (cần madmom + NATTEN)"),
        "embed": (t and has("transformers"), "SigLIP2 qua transformers"),
        "quality": (t and has("transformers"), "SigLIP2 zero-shot (DOVER: giấy phép phi thương mại → không dùng)"),
        "shots": (t and has("transnetv2_pytorch"), "TransNetV2"),
        "align": (t and has("transformers"), "wav2vec2 tiếng Việt"),
        "decode": (has("PyNvVideoCodec"), "PyNvVideoCodec (NVDEC, lấy khung nhanh 3×)"),
    }
    return {k: (bool(ok) and (k == "torch" or enabled(k)), d) for k, (ok, d) in out.items()}
