"""Auto-describe every clip — and every SEGMENT of long clips — with a LOCAL vision model (Ollama). Justin EDIT Suite.

  python -m nxcore.describe SRC [--model auto] [--force] [--files a.mp4 b.mp4] [--workers 6] [--no-segments]

1. nxcore.segments splits long clips into usable shots (cached; keyframe-only CUDA scan, ~9 s for 20 min of 4K HEVC).
2. Frames: short clips → 3 seeks (20/50/80 %); long clips → ONE keyframe-only decode gives every segment's frames
   (shared with step 1: clips segmented in this run keep their keyframe JPEGs for step 2, so they decode once).
   Frame extraction runs in a thread pool AHEAD of the model, so total time ≈ model time (~0.6 s / item).
3. Near-identical segments (same perceptual hash on every frame) reuse a description instead of calling the model.
Output  _nexys_work/auto_desc.json
  {"clips": {file: {"desc", "key", "model", "at", "segs": {"12.0-21.5": {"desc", "dup"?}}}}, "_model"}
  · clip "desc" of a split clip = its longest segment's description
  · cached by file key (+ segment range) → only new / changed material is sent again. Switching the model alone does
    NOT re-describe (descriptions stay valid; "model" is recorded per clip for the panel) — use --force for that.
  · written atomically every ~1 s → the panel fills in live; a kill loses nothing
Progress: "@describe i/n file", final "@describe done ok N" (or "done skip <why>"). Never fails the scan.
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from . import jsonio
from .gpu import hwaccel_args as _hwargs   # CUDA on Windows/Linux, VideoToolbox on macOS

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DEF_MODEL = "auto"
# "auto" = first installed of these. Qwen is the default analysis model (editor's choice, 2026-10):
# qwen3-vl:8b-instruct = the NON-thinking build (6.1 GB, leaves room for whisper on a 12 GB card).
# Plain qwen3-vl:8b is the thinking build: Ollama ignores think:false and it returns an empty answer → last.
PREFERRED = ("qwen3-vl:8b-instruct", "qwen2.5vl:7b", "gemma4:12b-it-qat", "gemma4:12b", "gemma3:12b", "llava:13b", "qwen3-vl:8b")
from .ollama import HOST as DEF_HOST     # noqa: E402 — one place parses OLLAMA_HOST
FRAME_AT = (0.2, 0.5, 0.8)
FRAME_W = 448
DUP_BITS = 5            # max hamming distance (of 64) per frame to call two segments "the same picture"


def prompt(n):
    return (f"Đây là {n} khung hình theo thứ tự thời gian của CÙNG MỘT cảnh quay video. "
            "Viết đúng 1 câu tiếng Việt, tối đa 18 từ, mô tả cảnh cho người dựng phim tìm cảnh: "
            "cỡ cảnh (toàn/trung/cận/đặc tả), chủ thể là ai/cái gì, đang làm gì, ở đâu; "
            "nếu máy lia, đi theo hoặc bay flycam thì nói rõ. "
            "Không mở đầu bằng 'Đây là', 'Clip', 'Video', 'Cảnh quay'; không liệt kê; không giải thích; chỉ trả câu mô tả.")


PROMPT = prompt(3)


def _j(p, d):
    return jsonio.load(p, d)


def _save_atomic(p, obj):
    jsonio.save_atomic(p, obj)


def resolve_model(host, model):
    """-> (model, '') when usable, else (None, short reason). model 'auto' picks from PREFERRED."""
    from .ollama import tags
    try:
        names = tags(host)
    except Exception:
        return None, "Ollama không chạy"
    if model == "auto":
        hit = next((p for p in PREFERRED if p in names), None)
        return (hit, "") if hit else (None, f"chưa có model vision (ollama pull {PREFERRED[0]})")
    if model in names or any(n.split(":")[0] == model for n in names if ":" not in model):
        return model, ""
    return None, f"chưa có model {model} (ollama pull {model})"


# 10-bit / 4:2:2 camera files (Sony XAVC, DJI) give 4:2:2 JPEGs Ollama can't decode → force 8-bit 4:2:0
_VF = f"scale={FRAME_W}:-2:flags=bicubic,format=yuvj420p"


def frame_b64(path, t, width=FRAME_W):
    """One frame near t (keyframe seek — the model describes the shot, the exact frame doesn't matter) as base64 JPEG."""
    try:
        p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-noaccurate_seek",
                            "-ss", f"{max(0.0, t):.2f}", "-i", path,
                            "-frames:v", "1", "-vf", _VF if width == FRAME_W else f"scale={width}:-2:flags=bicubic,format=yuvj420p",
                            "-q:v", "3", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                           capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        return None
    return base64.b64encode(p.stdout).decode("ascii") if p.returncode == 0 and p.stdout else None


def keyframe_jpegs(path, hw=True, timeout=900):
    """ONE keyframe-only decode → [(t, jpeg_bytes)] at FRAME_W. CUDA first, CPU fallback."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "info"] + _hwargs(hw) + \
          ["-skip_frame", "nokey", "-i", path, "-an", "-sn", "-dn", "-vf", _VF + ",showinfo", "-fps_mode", "passthrough",
           "-q:v", "3", "-f", "image2pipe", "-vcodec", "mjpeg", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except Exception:
        return []
    ts = [float(x) for x in re.findall(rb"pts_time:\s*(-?[\d.]+)", p.stderr)]
    jp, i = [], 0
    data = p.stdout
    while True:                                   # split the MJPEG stream on SOI … EOI
        s = data.find(b"\xff\xd8", i)
        if s < 0:
            break
        e = data.find(b"\xff\xd9", s + 2)
        if e < 0:
            break
        jp.append(data[s:e + 2]); i = e + 2
    if (p.returncode != 0 or not jp) and hw:
        return keyframe_jpegs(path, hw=False, timeout=timeout)
    return list(zip(ts, jp))


def jpeg_hash(b):
    try:
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            return None
        from .segments import dhash
        return dhash(img)
    except Exception:
        return None


def clean(text):
    t = re.sub(r"<think>.*?</think>", " ", text or "", flags=re.S)
    t = next((x for x in (l.strip() for l in t.splitlines()) if x), "")
    t = re.sub(r"^(mô tả|description)\s*:\s*", "", t, flags=re.I)
    t = t.strip(" \"'“”*-•").strip()
    w = t.split()
    if len(w) > 30:
        t = " ".join(w[:30]).rstrip(",;") + "…"
    return t


def ask(model, host, imgs_b64, timeout=180):
    from .ollama import chat                     # shared client: unloads a degraded (shared-memory) runner once
    out = chat(model, prompt(len(imgs_b64)), images=imgs_b64, num_predict=80, keep_alive="2m",   # frees VRAM soon after
               timeout=timeout, host=host)                                                         # (whisper needs it)
    d = clean(out)
    if not d:
        raise RuntimeError("model trả về rỗng (model 'thinking'? dùng --model gemma4:12b-it-qat)")
    return d


def describe_clip(path, dur, model, host, timeout=180):
    """Back-compat single-clip call (3 seeks)."""
    ts = [dur * a for a in FRAME_AT] if dur and dur > 0.6 else [0.0]
    imgs = [b for b in (frame_b64(path, t) for t in ts) if b]
    if not imgs:
        raise RuntimeError("không lấy được khung hình")
    return ask(model, host, imgs, timeout)


def _frame_times(a, b):
    L = b - a
    if L < 4:
        return [(a + b) / 2]
    if L < 9:
        return [a + 0.3 * L, a + 0.7 * L]
    return [a + 0.2 * L, a + 0.5 * L, a + 0.8 * L]


def _frames_for(path, items, kf_store=None):
    """items [(key, a, b, times)] → [(key, a, b, [b64], [hash])]. Many frames → one keyframe pass (reused from the
    segmenting pass when it kept them), else seeks."""
    need = sum(len(it[3]) for it in items)
    kf = kf_store.pop(path, None) if kf_store is not None else None
    if kf is None:
        kf = keyframe_jpegs(path) if need >= 4 else []
    out = []
    for key, a, b, times in items:
        imgs, hs = [], []
        for t in times:
            jb = None
            if kf:
                k = min(kf, key=lambda x: abs(x[0] - t))
                if abs(k[0] - t) <= 1.6 and a - 0.5 <= k[0] <= b + 0.5:
                    jb = k[1]
            if jb is None:
                b64 = frame_b64(path, t)
                jb = base64.b64decode(b64) if b64 else None
            if jb:
                imgs.append(base64.b64encode(jb).decode("ascii")); hs.append(jpeg_hash(jb))
        out.append((key, a, b, imgs, hs))
    return out


def _same(h1, h2):
    from .segments import hamming
    return h1 and h2 and len(h1) == len(h2) and all(x is not None and y is not None and hamming(x, y) <= DUP_BITS
                                                    for x, y in zip(h1, h2))


def run(src, model=DEF_MODEL, host=DEF_HOST, force=False, files=None, workers=6, progress=print, segments=True):
    from . import gpulock
    if gpulock.active(src, "describe"):          # one describe per folder (panel reloads used to leave a 2nd one running)
        progress("@describe done skip đang_chạy"); return 0
    if gpulock.active(src, "gpu_busy"):          # analyze (whisper/QC) owns the GPU → come back when it is idle
        progress("@describe done skip gpu_busy"); return 0
    with gpulock.hold(src, "describe"):
        return _run(src, model, host, force, files, workers, progress, segments)


def unload(model, host=DEF_HOST):
    try:
        from .ollama import unload as _unload
        _unload(model, host)
    except Exception:
        pass


def _run(src, model, host, force, files, workers, progress, segments):
    from . import gpulock
    W = os.path.join(src, "_nexys_work")
    media = _j(os.path.join(W, "media.json"), {})
    if not media:
        progress("@describe done skip chưa quét source")
        return 0
    model, why = resolve_model(host, model)
    op = os.path.join(W, "auto_desc.json")
    store = _j(op, {}) or {}
    clips = store.setdefault("clips", {})
    seg_clips = {}
    kf_store = None
    if segments:
        from . import segments as segmod
        if not why:              # long clips still to describe: segmenting keeps their keyframe JPEGs (one decode)
            want = {os.path.join(src, f) for f, m in media.items()
                    if m.get("status", "ok") == "ok" and (not files or f in files)
                    and float(m.get("dur") or 0) >= segmod.LONG_S
                    and (force or (clips.get(f) or {}).get("key") != m.get("key") or not (clips.get(f) or {}).get("desc"))}
            kf_store = segmod.share_keyframes(want) if want else None
        try:
            seg_clips = segmod.run(src, workers=4, progress=progress, files=files)
        except Exception as e:
            progress(f"segments: lỗi tách đoạn ({e}) — mô tả theo clip")
        finally:
            segmod.stop_sharing()
    try:                         # SigLIP2 shot index (search by meaning, look-alike shots, quality) — no Ollama needed
        from .ai import embed as _emb
        if _emb.available():
            _emb.index(src, force=force, progress=progress)
    except Exception as e:
        progress(f"embed: lỗi chỉ mục SigLIP ({str(e)[:120]}) — bỏ qua")
    audio_on = (_j(os.path.join(src, "_nexys_work", "audio.json"), {}) or {}).get("on", True) is not False
    if not audio_on:             # the editor chose "pictures only" for this folder (panel asks before analysing)
        progress("sounds/speakers: folder này chọn chỉ hình — bỏ qua âm thanh")
    try:                         # C2: sound index (search by sound) — PANNs, ~1 min per 300 clips on a GPU
        from .ai import sounds as _snd
        if audio_on and _snd.available() and os.path.exists(_snd.WEIGHTS):     # weights come with "Tải trước model"
            _snd.index(src, force=force, progress=progress)
    except Exception as e:
        progress(f"sounds: lỗi chỉ mục âm thanh ({str(e)[:120]}) — bỏ qua")
    try:                         # C3: who speaks when — needs the transcripts, ~10 s per 400 voice windows (CPU)
        from .ai import speakers as _spk
        tdir = os.path.join(src, "_nexys_work", "transcripts")
        sp_p = os.path.join(src, "_nexys_work", "speakers.json")
        newest = max((os.path.getmtime(os.path.join(tdir, x)) for x in os.listdir(tdir)), default=0) if os.path.isdir(tdir) else 0
        if audio_on and _spk.available() and newest and (force or not os.path.exists(sp_p) or os.path.getmtime(sp_p) < newest):
            _spk.diarize(src, progress=progress)
    except Exception as e:
        progress(f"speakers: lỗi nhận diện người nói ({str(e)[:120]}) — bỏ qua")
    try:
        from .ai import free as _free
        _free()                  # hand the VRAM back before the vision LLM loads
    except Exception:
        pass
    from .segments import seg_key
    if why:
        progress(f"describe: bỏ qua mô tả tự động — {why}")
        progress(f"@describe done skip {why.replace(' ', '_')}")
        return 0
    for f in [f for f in clips if f not in media]:
        clips.pop(f, None)
    plan = []
    for f, m in media.items():
        if m.get("status", "ok") != "ok" or (files and f not in files):
            continue
        c = clips.get(f) or {}
        fresh = not force and c.get("key") == m.get("key")
        dur = float(m.get("dur") or 0)
        segs = (seg_clips.get(f) or {}).get("segs") or []
        if len(segs) >= 2:
            have = (c.get("segs") or {}) if fresh else {}
            keys = {seg_key(s) for s in segs}
            if fresh and set(have) - keys:                       # re-split → forget ranges that no longer exist
                c["segs"] = {k: v for k, v in have.items() if k in keys}
            items = [(seg_key(s), s["a"], s["b"], _frame_times(s["a"], s["b"])) for s in segs
                     if not (have.get(seg_key(s)) or {}).get("desc")]
        else:
            if fresh and c.get("desc"):
                continue
            items = [(None, 0.0, dur, [dur * x for x in FRAME_AT] if dur > 0.6 else [0.0])]
        if items:
            plan.append((f, m, items, segs))
    total = sum(len(p[2]) for p in plan)
    progress(json.dumps({"stage": "describe", "total": total, "clips": len(plan), "cached": len(media) - len(plan)}))
    store["_model"] = model
    if not total:
        _save_atomic(op, store)
        progress(f"@describe done ok {len(clips)}")
        return 0
    plan.sort(key=lambda p: float(p[1].get("dur") or 0))    # short clips first: the model starts at once while long clips decode
    done = fails = dups = 0
    last_save = 0.0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = [ex.submit(_frames_for, os.path.join(src, f), items, kf_store) for f, m, items, segs in plan]
        for fu, (f, m, items, segs) in zip(futs, plan):
            c = clips.get(f) or {}
            if not (not force and c.get("key") == m.get("key")):
                c = {}
            clips[f] = c
            try:
                res = fu.result()
            except Exception as e:
                res = []
                progress(f"[{done}/{total}] {f} ✖ {e}")
            if gpulock.active(src, "gpu_busy"):      # a scan started meanwhile → free VRAM for whisper, resume later
                _save_atomic(op, store); unload(model, host)
                progress(f"describe: tạm dừng ở {done}/{total} — đang quét, nhường GPU cho whisper")
                progress("@describe done skip gpu_busy"); return 0
            seen = []
            for key, a, b, imgs, hs in res:
                done += 1
                tag = f"{f}" + (f" @{key}s" if key else "")
                try:
                    if not imgs:
                        raise RuntimeError("không lấy được khung hình")
                    d = next((d2 for h2, d2 in seen if _same(hs, h2)), None)
                    dup = d is not None
                    if dup:
                        dups += 1
                    else:
                        d = ask(model, host, imgs)
                    seen.append((hs, d))
                    if key is None:
                        c["desc"] = d
                    else:
                        c.setdefault("segs", {})[key] = dict({"desc": d}, **({"dup": True} if dup else {}))
                    c.update(key=m.get("key"), model=model, at=time.strftime("%Y-%m-%d %H:%M"))
                    progress(f"@describe {done}/{total} {f}")
                    progress(f"[{done}/{total}] {tag} · {d}" + (" (trùng hình → dùng lại)" if dup else ""))
                except Exception as e:
                    fails += 1
                    progress(f"@describe {done}/{total} {f}")
                    progress(f"[{done}/{total}] {tag} ✖ {e}")
                if time.time() - last_save > 1.0:
                    _save_atomic(op, store); last_save = time.time()
            if len(segs) >= 2 and c.get("segs"):             # clip-level line = its longest described segment
                best = max((s for s in segs if (c["segs"].get(seg_key(s)) or {}).get("desc")),
                           key=lambda s: s["b"] - s["a"], default=None)
                if best:
                    c["desc"] = c["segs"][seg_key(best)]["desc"]
                    c.update(key=m.get("key"), model=model)
    _save_atomic(op, store)
    progress(f"describe: {done - fails}/{total} mục đã có mô tả tự động ({model})"
             + (f" · {dups} đoạn trùng hình dùng lại mô tả" if dups else "") + (f" · {fails} lỗi" if fails else ""))
    progress(f"@describe done {'ok' if done - fails else 'fail'} {len(clips)}")
    return 0


def load(src):
    """file -> clip-level auto description (for assemble / digest)."""
    st = _j(os.path.join(src, "_nexys_work", "auto_desc.json"), {}) or {}
    return {f: c["desc"] for f, c in (st.get("clips") or {}).items() if isinstance(c, dict) and c.get("desc")}


def load_segs(src):
    """file -> {"a-b": desc} for split clips."""
    st = _j(os.path.join(src, "_nexys_work", "auto_desc.json"), {}) or {}
    return {f: {k: v.get("desc", "") for k, v in (c.get("segs") or {}).items() if v.get("desc")}
            for f, c in (st.get("clips") or {}).items() if isinstance(c, dict) and c.get("segs")}


def main():
    ap = argparse.ArgumentParser(description="Mô tả tự động từng clip / đoạn bằng vision model local (Ollama)")
    ap.add_argument("src")
    ap.add_argument("--model", default=DEF_MODEL)
    ap.add_argument("--host", default=DEF_HOST)
    ap.add_argument("--force", action="store_true", help="mô tả lại tất cả, bỏ qua cache")
    ap.add_argument("--files", nargs="*", default=None)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--no-segments", action="store_true", help="chỉ mô tả cấp clip, không tách đoạn")
    a = ap.parse_args()
    sys.exit(run(a.src, a.model, a.host.rstrip("/"), a.force, a.files, a.workers,
                 progress=lambda s: print(s, flush=True), segments=not a.no_segments))


if __name__ == "__main__":
    main()
