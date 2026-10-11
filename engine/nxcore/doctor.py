"""System doctor — checks everything the suite needs and INSTALLS what is missing (no more "thiếu cái này cái kia").

  python -m nxcore.doctor                 # check only → human lines + "DOCTOR {json}"
  python -m nxcore.doctor --fix           # install what is missing (pip packages, ffmpeg, CUDA libs, AI pack, models)
       [--ai auto|yes|no] [--models] [--ollama auto|yes|no] [--quiet]

Used by install.ps1 / install_mac.sh right after Python exists, and by the panel (Cài đặt → Kiểm tra hệ thống →
Tự sửa) so a machine that loses a package later heals itself too. Everything is per-user (no admin rights).

Hardware decides the extras:
  NVIDIA        → CUDA libs for Whisper, PyTorch cu128, AI pack, Ollama when VRAM ≥ 8 GB
  Apple Silicon → mlx-whisper, PyTorch (MPS), AI pack
  other / CPU   → core only (AI pack on request: --ai yes)
"""
import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

IS_WIN, IS_MAC = sys.platform == "win32", sys.platform == "darwin"
ARM_MAC = IS_MAC and platform.machine() in ("arm64", "aarch64")
HOME_DIR = os.environ.get("NX_HOME") or (os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "JustinEditSuite") if IS_WIN
            else os.path.join(os.path.expanduser("~"), "Library", "Application Support", "JustinEditSuite") if IS_MAC
            else os.path.join(os.path.expanduser("~"), ".local", "share", "JustinEditSuite"))
FFMPEG_DIR = os.path.join(HOME_DIR, "ffmpeg", "bin")

# (import name, pip spec, why) — the suite does not start without these
CORE = [
    ("numpy", "numpy>=1.24", "tính toán"),
    ("cv2", "opencv-python>=4.8", "phân tích hình (rung, bố cục)"),
    ("PIL", "pillow>=9.5", "contact sheet, ảnh"),
    ("faster_whisper", "faster-whisper>=1.0", "nhận diện giọng nói"),
    ("docx", "python-docx>=0.8.11", "đọc kịch bản .docx"),
]
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"
AI_PKGS = [
    ("transformers", "transformers>=4.49", "SigLIP2 tìm footage + căn chỉnh từ"),
    ("sentencepiece", "sentencepiece", "tokenizer SigLIP2"),
    ("google.protobuf", "protobuf", "tokenizer"),
    ("beat_this", "beat_this @ https://github.com/CPJKU/beat_this/archive/main.zip", "bắt beat bằng AI"),
    ("transnetv2_pytorch", "transnetv2-pytorch", "tách cảnh trong video đã dựng"),
    ("torchlibrosa", "torchlibrosa==0.1.0", "tìm footage theo âm thanh (PANNs)"),
    ("scipy", "scipy", "nhận diện người nói, phân tích retention"),
    ("sklearn", "scikit-learn", "nhận diện người nói (phân cụm)"),
]
FFMPEG_WIN_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
FFMPEG_MAC_URLS = {"ffmpeg": "https://evermeet.cx/ffmpeg/getrelease/zip", "ffprobe": "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip"}
OLLAMA_MODEL = "qwen3-vl:8b-instruct"

QUIET = False


def say(msg):
    if not QUIET:
        print(msg, flush=True)


def has(mod):
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def run(cmd, timeout=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:                       # noqa: BLE001
        return -1, str(e)


# ------------------------------------------------------------------ hardware
def nvidia():
    """→ (name, vram_gb) or None."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    rc, out = run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"], 15)
    if rc != 0 or not out.strip():
        return None
    name, _, mem = out.strip().splitlines()[0].partition(",")
    try:
        return name.strip(), round(float(mem) / 1024, 1)
    except ValueError:
        return name.strip(), 0.0


def free_gb(path):
    try:
        return shutil.disk_usage(path).free / 1e9
    except OSError:
        return 0.0


# ------------------------------------------------------------------ pip
def _pip_user_flag():
    """--user when the interpreter's site-packages is not writable (Python in Program Files, no admin)."""
    import site
    try:
        sp = site.getsitepackages()[0]
    except Exception:
        return []
    return [] if os.access(sp, os.W_OK) else ["--user"]


def pip_install(specs, extra=(), label=""):
    if not specs:
        return True
    say(f"  ↓ pip install {' '.join(s.split(' @ ')[0] for s in specs)} {label}".rstrip())
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location",
           "--progress-bar", "off"] + _pip_user_flag() + list(extra) + list(specs)
    rc, out = run(cmd, 3600)
    if rc != 0:
        tail = [l for l in out.splitlines() if l.strip()][-3:]
        say("  ✗ pip lỗi: " + " | ".join(tail)[:400])
        return False
    importlib.invalidate_caches()
    return True


def ensure_pip():
    rc, _ = run([sys.executable, "-m", "pip", "--version"], 60)
    if rc == 0:
        return True
    say("  ↓ cài pip (ensurepip)")
    rc, _ = run([sys.executable, "-m", "ensurepip", "--upgrade"], 600)
    return rc == 0


# ------------------------------------------------------------------ ffmpeg
def ffmpeg_ok():
    try:
        import nxcore  # noqa: F401  (ensure_media_env adds the portable / winget / registry dirs to PATH)
    except Exception:
        pass
    if os.path.isdir(FFMPEG_DIR) and FFMPEG_DIR not in os.environ.get("PATH", ""):
        os.environ["PATH"] = FFMPEG_DIR + os.pathsep + os.environ.get("PATH", "")
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _download(url, dst, label):
    say(f"  ↓ tải {label} …")
    req = urllib.request.Request(url, headers={"User-Agent": "JustinEditSuite-doctor"})
    t0, last = time.time(), 0
    with urllib.request.urlopen(req, timeout=60) as r, open(dst, "wb") as fh:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            fh.write(b)
            got += len(b)
            if time.time() - last > 3:
                last = time.time()
                say(f"    {got / 1e6:.0f}/{total / 1e6:.0f} MB" if total else f"    {got / 1e6:.0f} MB")
    say(f"    xong {os.path.getsize(dst) / 1e6:.0f} MB trong {time.time() - t0:.0f}s")


def install_ffmpeg():
    os.makedirs(FFMPEG_DIR, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="jes_ff_")
    try:
        if IS_WIN:
            z = os.path.join(tmp, "ff.zip")
            _download(FFMPEG_WIN_URL, z, "ffmpeg (gyan.dev, ~100 MB)")
            with zipfile.ZipFile(z) as zf:
                for n in zf.namelist():
                    base = os.path.basename(n)
                    if n.replace("\\", "/").split("/")[-2:-1] == ["bin"] and base.lower() in ("ffmpeg.exe", "ffprobe.exe"):
                        with zf.open(n) as src, open(os.path.join(FFMPEG_DIR, base), "wb") as out:
                            shutil.copyfileobj(src, out)
        elif IS_MAC:
            if shutil.which("brew"):
                say("  ↓ brew install ffmpeg")
                rc, _ = run(["brew", "install", "ffmpeg"], 3600)
                if rc == 0:
                    return ffmpeg_ok()
            for name, url in FFMPEG_MAC_URLS.items():
                z = os.path.join(tmp, name + ".zip")
                _download(url, z, name)
                with zipfile.ZipFile(z) as zf:
                    zf.extract(name, FFMPEG_DIR)
                os.chmod(os.path.join(FFMPEG_DIR, name), 0o755)
        else:
            say("  ✗ Linux: cài ffmpeg bằng trình quản lý gói (sudo apt install ffmpeg)")
            return False
    except Exception as e:                       # noqa: BLE001
        say(f"  ✗ tải ffmpeg lỗi: {e}")
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return ffmpeg_ok()


# ------------------------------------------------------------------ CUDA for faster-whisper
def cuda_libs_ok():
    """cuBLAS 12 + cuDNN 9 reachable for CTranslate2 (pip wheels nvidia-cublas-cu12 / nvidia-cudnn-cu12)."""
    import site
    roots = list(site.getsitepackages()) + [site.getusersitepackages()]
    want = ("cublas64_12.dll", "cudnn64_9.dll") if IS_WIN else ("libcublas.so.12", "libcudnn.so.9")
    seen = set()
    for r in roots:
        for d, _, fs in os.walk(os.path.join(r, "nvidia")):
            seen |= {f for f in fs if f in want}
    return all(w in seen for w in want)


# ------------------------------------------------------------------ torch / AI pack
def torch_state():
    """→ (installed, cuda_build, version)."""
    if not has("torch"):
        return False, False, ""
    rc, out = run([sys.executable, "-c", "import torch;print(torch.__version__, bool(torch.version.cuda))"], 300)
    if rc != 0:
        return False, False, ""
    v, _, c = out.strip().splitlines()[-1].partition(" ")
    return True, c.strip() == "True", v


def want_ai(mode, gpu):
    if mode == "yes":
        return True
    if mode == "no":
        return False
    return bool(gpu or ARM_MAC) and free_gb(HOME_DIR if os.path.isdir(HOME_DIR) else os.path.expanduser("~")) >= 8


# ------------------------------------------------------------------ models (download once, so the first run is not a stall)
def prefetch_models(ai_on, whisper="large-v3-turbo"):
    code = [
        "import os",
        "from faster_whisper.utils import download_model",
        f"download_model({whisper!r})",
    ]
    if ai_on:
        code += [
            "from transformers import AutoModel, AutoProcessor, AutoModelForCTC",
            "AutoModel.from_pretrained('google/siglip2-base-patch16-224'); AutoProcessor.from_pretrained('google/siglip2-base-patch16-224')",
            "AutoModelForCTC.from_pretrained('dragonSwing/wav2vec2-base-vietnamese'); AutoProcessor.from_pretrained('dragonSwing/wav2vec2-base-vietnamese')",
            "import torch; torch.hub.load_state_dict_from_url('https://cloud.cp.jku.at/public.php/dav/files/7ik4RrBKTS273gp/final0.ckpt', file_name='beat_this-final0.ckpt', map_location='cpu')",
            # PANNs Cnn14_16k (CC-BY-4.0) for search by sound — nxcore.ai.sounds downloads into the AI cache
            f"import sys; sys.path.insert(0, {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))!r})",
            "from nxcore.ai import sounds as _s",
            "os.path.exists(_s.WEIGHTS) or _s._download()",
            # WeSpeaker ResNet34-LM ONNX (CC-BY-4.0, ungated) for who-speaks-when
            "from huggingface_hub import hf_hub_download; from nxcore.ai import speakers as _k; hf_hub_download(_k.REPO, _k.FILE)",
        ]
    say("  ↓ tải trước model (Whisper" + (", SigLIP2, wav2vec2, beat_this, PANNs, WeSpeaker" if ai_on else "") + ") — chỉ lần đầu")
    rc, out = run([sys.executable, "-c", "\n".join(code)], 7200)
    if rc != 0:
        say("  ✗ tải model lỗi: " + " | ".join([l for l in out.splitlines() if l.strip()][-2:])[:300])
    return rc == 0


# ------------------------------------------------------------------ Ollama (local vision LLM for clip descriptions)
def ollama_state():
    exe = shutil.which("ollama")
    if not exe and IS_WIN:
        cand = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
        exe = cand if os.path.exists(cand) else None
    if not exe:
        return None, False
    rc, out = run([exe, "list"], 30)
    return exe, rc == 0 and OLLAMA_MODEL.split(":")[0] in out


def install_ollama():
    if IS_WIN and shutil.which("winget"):
        say("  ↓ winget install Ollama.Ollama")
        run(["winget", "install", "-e", "--id", "Ollama.Ollama", "--silent", "--accept-package-agreements",
             "--accept-source-agreements"], 3600)
    elif IS_MAC and shutil.which("brew"):
        say("  ↓ brew install ollama")
        run(["brew", "install", "ollama"], 3600)
    exe, _ = ollama_state()
    return exe


# ------------------------------------------------------------------ GPU memory / face detector
OLLAMA_NEED_MB = 6600            # qwen3-vl:8b-instruct with its 4k context, all layers on the GPU (measured 6.2 GB)


def vram_state():
    """VRAM split: what other apps hold (Premiere, browsers…), what Ollama holds, what an AI model could get."""
    try:
        from . import gpu as _gpu, ollama as _ol
    except ImportError:
        return None
    v = _gpu.vram()
    if not v:
        return None
    loaded = _ol.ps()
    ol_mb = sum(m["size_vram"] for m in loaded) // 2 ** 20
    others = max(0, v["used"] - ol_mb)
    return {"total_mb": v["total"], "used_mb": v["used"], "free_mb": v["free"], "others_mb": others, "ai_mb": v["total"] - others,
            "ollama": [{"name": m["name"], "gpu_pct": round(100 * m["size_vram"] / m["size"]) if m["size"] else 0,
                        "cpu_mb": (m["size"] - m["size_vram"]) // 2 ** 20} for m in loaded]}


def face_state(download=False):
    """→ (ok, why): YuNet face model usable by nxcore.stability (why: "cv2" | "model" | error text)."""
    try:
        import cv2
    except ImportError:
        return False, "cv2"
    if not hasattr(cv2, "FaceDetectorYN"):
        return False, "cv2"
    try:
        from huggingface_hub import hf_hub_download, try_to_load_from_cache
        from .stability import FACE_REPO, FACE_FILE
        p = try_to_load_from_cache(FACE_REPO, FACE_FILE)
        if isinstance(p, str) and os.path.isfile(p):
            return True, ""
        if download:
            say("  ↓ tải model khuôn mặt YuNet (227 KB, MIT)")
            return os.path.isfile(hf_hub_download(FACE_REPO, FACE_FILE)), ""
        return False, "model"
    except Exception as e:
        return False, str(e)[:120]


# ------------------------------------------------------------------ main
def check_and_fix(fix=False, ai="auto", models=False, ollama="auto", whisper="large-v3-turbo"):
    rep = {"python": sys.version.split()[0], "exe": sys.executable, "items": {}, "fixed": [], "failed": []}
    items = rep["items"]

    def item(key, ok, detail, required=True):
        items[key] = {"ok": bool(ok), "detail": detail, "required": required}
        say(f"  {'✔' if ok else ('✗' if required else '·')} {key:14s} {detail}")

    say(f"Python {rep['python']} · {sys.executable}")
    if sys.version_info < (3, 10):
        item("python", False, "cần Python ≥ 3.10 (bộ cài tự cài bản 3.12)")
        return rep
    item("python", True, rep["python"])
    gpu = nvidia()
    rep["gpu"] = {"nvidia": gpu[0] if gpu else None, "vram_gb": gpu[1] if gpu else 0, "apple_silicon": ARM_MAC}
    say(f"  · GPU: {gpu[0] + f' ({gpu[1]} GB)' if gpu else ('Apple Silicon' if ARM_MAC else 'không có GPU tăng tốc')}")

    if fix and not ensure_pip():
        rep["failed"].append("pip")

    # core packages
    missing = [(m, spec, why) for m, spec, why in CORE if not has(m)]
    if ARM_MAC and not has("mlx_whisper"):
        missing.append(("mlx_whisper", "mlx-whisper>=0.4.1", "Whisper trên GPU Apple"))
    if fix and missing:
        if pip_install([s for _, s, _ in missing], label="(thư viện chính)"):
            rep["fixed"] += [m for m, _, _ in missing if has(m)]
    for m, spec, why in CORE + ([("mlx_whisper", "", "Whisper trên GPU Apple")] if ARM_MAC else []):
        item(m, has(m), why)

    # ffmpeg
    ok = ffmpeg_ok()
    if not ok and fix:
        ok = install_ffmpeg()
        if ok:
            rep["fixed"].append("ffmpeg")
    item("ffmpeg", ok, shutil.which("ffmpeg") or "chưa có (giải mã / cắt video)")

    # CUDA libs for whisper
    if gpu and IS_WIN:
        ok = cuda_libs_ok()
        if not ok and fix and pip_install(["nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*"], label="(CUDA cho Whisper, ~1 GB)"):
            ok = cuda_libs_ok()
            if ok:
                rep["fixed"].append("cuda_libs")
        item("cuda_libs", ok, "cuBLAS + cuDNN cho Whisper GPU" if ok else "thiếu → Whisper chạy CPU (chậm ~10×)", required=False)

    # AI pack
    ai_on = want_ai(ai, gpu)
    t_ok, t_cuda, t_ver = torch_state()
    if fix and ai_on:
        if not t_ok or (gpu and not t_cuda):
            extra = ["--index-url", TORCH_INDEX] if gpu else []
            if t_ok and gpu and not t_cuda:
                extra += ["--force-reinstall"]          # CPU-only torch on an NVIDIA machine → CUDA build
            if pip_install(["torch", "torchvision", "torchaudio"], extra, "(PyTorch, ~2.5 GB)"):
                rep["fixed"].append("torch")
            t_ok, t_cuda, t_ver = torch_state()
        if t_ok:
            pk = [(m, s) for m, s, _ in AI_PKGS if not has(m)]
            if gpu and not has("PyNvVideoCodec"):        # NVDEC frame access: 3× faster SigLIP indexing (measured)
                pk.append(("PyNvVideoCodec", "PyNvVideoCodec"))
            if pk and pip_install([s for _, s in pk], label="(gói AI)"):
                rep["fixed"] += [m for m, _ in pk if has(m)]
    why_off = ("" if ai_on else " (đã chọn không cài)" if ai == "no" else
               " (máy không có GPU tăng tốc hoặc ổ đĩa < 8 GB trống — bấm 'Cài gói AI' để cài vẫn được)")
    item("torch", t_ok, (f"{t_ver} · {'CUDA' if t_cuda else 'CPU/MPS'}" if t_ok else "chưa có — gói AI tắt" + why_off),
         required=False)
    for m, _, why in AI_PKGS:
        item(m.split(".")[0] if m != "google.protobuf" else "protobuf", has(m), why, required=False)

    # models
    if fix and models:
        if prefetch_models(t_ok and ai_on and has("transformers"), whisper):
            rep["fixed"].append("models")

    # Ollama
    exe, has_model = ollama_state()
    want_ol = ollama == "yes" or (ollama == "auto" and gpu and gpu[1] >= 8 and free_gb(os.path.expanduser("~")) >= 15)
    if fix and want_ol:
        if not exe:
            exe = install_ollama()
            if exe:
                rep["fixed"].append("ollama")
        if exe and not has_model:
            say(f"  ↓ ollama pull {OLLAMA_MODEL} (~6 GB)")
            rc, _ = run([exe, "pull", OLLAMA_MODEL], 7200)
            exe, has_model = ollama_state()
            if has_model:
                rep["fixed"].append("ollama_model")
    item("ollama", bool(exe and has_model), (f"{OLLAMA_MODEL} sẵn sàng" if exe and has_model else
                                             "có Ollama, chưa có model" if exe else "chưa cài (mô tả clip bằng AI local)"),
         required=False)

    # face detector (YuNet, 227 KB, MIT) — OpenCV 5 has no Haar cascades any more
    ok_f, why_f = face_state()
    if not ok_f and fix and why_f == "model":
        ok_f, why_f = face_state(download=True)
        if ok_f:
            rep["fixed"].append("face")
    item("face", ok_f, "YuNet (OpenCV) — bố cục / reframe thấy khuôn mặt" if ok_f else
         {"cv2": "OpenCV thiếu FaceDetectorYN — bố cục chỉ dùng saliency",
          "model": "chưa tải model YuNet 227 KB (bấm Tự sửa)"}.get(why_f, why_f), required=False)

    # GPU memory right now: Ollama loads into what is free and quietly runs the remaining layers on the CPU
    if gpu and not ARM_MAC:
        st = vram_state()
        if st:
            rep["vram"] = st
            item("vram", st["ai_mb"] >= OLLAMA_NEED_MB,
                 f"ứng dụng khác giữ {st['others_mb'] / 1024:.1f} GB / {st['total_mb'] / 1024:.0f} GB → còn {st['ai_mb'] / 1024:.1f} GB cho AI local"
                 + ("" if st["ai_mb"] >= OLLAMA_NEED_MB else
                    f" (< {OLLAMA_NEED_MB / 1024:.1f} GB: Ollama sẽ chạy một phần trên CPU — đóng bớt Premiere / trình duyệt khi chạy AI)"),
                 required=False)
            for m in st["ollama"]:
                item("ollama_gpu", m["gpu_pct"] >= 97, f"{m['name']}: {m['gpu_pct']}% trên GPU"
                     + ("" if m["gpu_pct"] >= 97 else f", {m['cpu_mb']} MB chạy CPU (chậm) — nạp lại khi VRAM trống hơn"),
                     required=False)
            if st["ai_mb"] < OLLAMA_NEED_MB:
                say("  · Mẹo: NVIDIA Control Panel → Manage 3D settings → Program Settings → ollama.exe / python.exe → "
                    "'CUDA - Sysmem Fallback Policy' = 'Prefer No Sysmem Fallback': tràn VRAM sẽ báo lỗi ngay thay vì chậm ~25 lần")

    rep["failed"] += [k for k, v in items.items() if v["required"] and not v["ok"]]
    rep["ok"] = not rep["failed"]
    rep["ai"] = {"wanted": ai_on, "torch": t_ok, "cuda": t_cuda}
    return rep


def write_settings(engine_dir, gpu_ok):
    """Point the panel at this engine + this Python (merging, never dropping the user's other settings)."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    p = os.path.join(base, "JustinEditSuite", "settings.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    cur = {}
    try:
        with open(p, encoding="utf-8") as fh:
            cur = json.load(fh) or {}
    except Exception:
        cur = {}
    cur["tools"] = os.path.abspath(engine_dir).replace("\\", "/")
    cur["python"] = sys.executable.replace("\\", "/")
    cur.setdefault("cuda", bool(gpu_ok))
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cur, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    say(f"  ✔ settings.json → engine {cur['tools']} · python {cur['python']}")


def main():
    global QUIET
    for s_ in (sys.stdout, sys.stderr):
        try:
            s_.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--ai", default="auto", choices=("auto", "yes", "no"))
    ap.add_argument("--models", action="store_true", help="tải trước model Whisper / AI")
    ap.add_argument("--ollama", default="no", choices=("auto", "yes", "no"))
    ap.add_argument("--whisper", default="large-v3-turbo")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--settings", default="", help="ghi settings.json của panel: engine = thư mục này, python = python đang chạy")
    a = ap.parse_args()
    QUIET = a.quiet
    rep = check_and_fix(a.fix, a.ai, a.models, a.ollama, a.whisper)
    if a.settings:
        write_settings(a.settings, bool(rep.get("gpu", {}).get("nvidia") or ARM_MAC))
    say("✔ Đủ thành phần bắt buộc" if rep.get("ok") else "✗ Còn thiếu: " + ", ".join(rep.get("failed") or []))
    print("DOCTOR " + json.dumps(rep, ensure_ascii=False), flush=True)
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
