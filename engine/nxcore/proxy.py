"""Lightweight analysis proxies for AI video understanding — Justin EDIT Suite.

Each source clip -> <src>/_nexys_work/proxy/<name>.mp4 : short side 360 px, 10 fps, H.264 CRF 30,
AAC mono 64 kbps (speech/music/noise stay audible for the model), faststart. ~1 MB per 10 s.
Parallel, CUDA decode when available, cached by source size+mtime in proxy/manifest.json.
Built only on request (analyze --proxy / panel button) — the regular scan never makes proxies.

CLI: python -m nxcore.proxy "F:/NEXIS/HR 2509" [--workers 4] [--no-cuda] [--force] [--height 360]
"""
import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import gpu as _gpu
from . import jsonio


def _key(path):
    st = os.stat(path)
    return f"{st.st_size}:{int(st.st_mtime)}"


X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-g", "20"]
NVENC = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "32", "-b:v", "0", "-g", "20"]
VTENC = ["-c:v", "h264_videotoolbox", "-b:v", "700k", "-g", "20"]


def attempts(cuda, rot=0, short_side=360):
    """→ [(label, input args, -vf, encoder args)] best first. NVIDIA: decode + scale + encode all on the GPU (CPU nearly
    idle → the rest of the scan keeps running); Mac: VideoToolbox both ways; else hw decode (if any) + libx264; CPU last."""
    # scale so the SHORT side = short_side (works for 9:16 and 16:9; ffmpeg auto-rotates phone footage)
    # fps first: frames that are dropped anyway are not scaled (4K 30p → 2/3 less scaling work)
    wh = f"'if(gt(iw,ih),-2,{short_side})':'if(gt(iw,ih),{short_side},-2)'"
    vf = f"fps=10,scale={wh}:flags=bilinear,format=yuv420p"
    a = _gpu.accel() if cuda else ""
    try:
        rotated = int(float(rot or 0)) % 360 != 0
    except (TypeError, ValueError):
        rotated = False
    out = []
    if a == "cuda" and _gpu._ff_has("encoders", "h264_nvenc"):
        if not rotated:                       # rotated phone clips: auto-transpose needs frames in RAM
            out.append(("cuda+nvenc", ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"],
                        f"fps=10,scale_cuda={wh}:format=nv12", NVENC))
        out.append(("cuda+nvenc", ["-hwaccel", "cuda"], vf, NVENC))
    elif a == "videotoolbox" and _gpu._ff_has("encoders", "h264_videotoolbox"):
        out.append(("videotoolbox", ["-hwaccel", "videotoolbox"], vf, VTENC))
    if a:
        out.append((a, ["-hwaccel", a], vf, X264))
    out.append(("cpu", _gpu.cpu_args(), vf, X264))
    return out


def build_cmd(src, dst, has_audio, hw, vf, enc):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + hw
    cmd += ["-i", src, "-map", "0:v:0", "-vf", vf] + enc
    if has_audio:
        cmd += ["-map", "0:a:0?", "-c:a", "aac", "-ac", "1", "-ar", "32000", "-b:a", "64k"]
    else:
        cmd += ["-an"]
    return cmd + ["-movflags", "+faststart", dst]


def make(src, dst, has_audio, cuda, rot=0):
    err = ""
    for label, hw, vf, enc in attempts(cuda, rot):
        p = subprocess.run(build_cmd(src, dst, has_audio, hw, vf, enc), capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode == 0:
            return label
        err = p.stderr[-300:]
    raise RuntimeError(err)


def _safe_dump(data, path):
    try:
        jsonio.save_atomic(path, data)
    except Exception:
        pass


def run(src, workers=4, cuda=True, force=False, files=None, progress=print):
    W = os.path.join(src, "_nexys_work")
    mp_media = os.path.join(W, "media.json")
    try:
        media = json.load(open(mp_media, encoding="utf-8"))
    except Exception as e:
        progress(f"media.json lỗi ({e}).")
        return {}
    pdir = os.path.join(W, "proxy")
    os.makedirs(pdir, exist_ok=True)
    mp = os.path.join(pdir, "manifest.json")
    man = {}
    if not force and os.path.exists(mp) and os.path.getsize(mp) > 0:
        try:
            with open(mp, encoding="utf-8") as fp:
                man = json.load(fp)
        except Exception as e:
            progress(f"manifest.json lỗi ({e}) -> làm mới cache.")
            man = {}
    todo = []
    for f, info in media.items():
        if files and f not in files:
            continue
        s = os.path.join(src, f)
        if not os.path.exists(s):
            continue
        k = _key(s)
        from .media import stem
        dst = os.path.join(pdir, stem(f) + ".mp4")
        if man.get(f, {}).get("key") == k and os.path.exists(dst):
            continue
        todo.append((f, s, dst, info, k))
    progress(json.dumps({"stage": "proxy", "total": len(todo), "cached": len(media) - len(todo)}))
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(make, s, dst, info.get("audio", True), cuda, info.get("rot", 0)): (f, dst, info, k)
                for f, s, dst, info, k in todo}
        for fu in as_completed(futs):
            f, dst, info, k = futs[fu]
            done += 1
            try:
                dec = fu.result()
                man[f] = {"key": k, "proxy": os.path.basename(dst), "dur": info.get("dur"), "bytes": os.path.getsize(dst), "audio": info.get("audio", True)}
                progress(f"[{done}/{len(todo)}] {f} → {os.path.basename(dst)} {man[f]['bytes'] / 1e6:.1f}MB ({dec})")
            except Exception as e:
                try:
                    os.remove(dst)  # drop partial output so the next run redoes it
                except OSError:
                    pass
                progress(f"[{done}/{len(todo)}] {f} proxy lỗi: {e}")
            _safe_dump(man, mp)
    total = sum(v.get("bytes", 0) for v in man.values())
    progress(f"proxy: {len(man)} clip · {total / 1e6:.1f} MB")
    return man


def main():
    ap = argparse.ArgumentParser(description="Make 360p analysis proxies (video + audio) for Gemini video understanding")
    ap.add_argument("src")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-cuda", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--files", nargs="*")
    a = ap.parse_args()
    for st in (sys.stdout, sys.stderr):  # Windows console/redirect defaults to cp1252 -> crash on "→"/Vietnamese
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    run(a.src, a.workers, not a.no_cuda, a.force, a.files, progress=lambda m: print(m, flush=True))
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
