# nxcut.py — Nexys rough-cut toolkit (Windows Python: faster_whisper, cv2, numpy, PIL; ffmpeg in PATH)
# Subcommands: probe | sheets | transcribe | shake | sync | build   (see SKILL.md)
import time, os, sys, json, glob, subprocess, math, re, argparse, urllib.parse, unicodedata
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
if "HF_HOME" not in os.environ and os.path.exists("D:\\"):
    os.environ["HF_HOME"] = r"D:\cache\huggingface"
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
import shutil
import numpy as np
from nxcore import jsonio
# Whisper hallucinations are no longer dropped here: every segment is kept and nxcore.speech flags junk
# (whole-word phrase match, only when the phrase is most of the line or Whisper was unsure).
VEXT = (".mp4", ".mov", ".mxf", ".mts")
def run(cmd, **k): return subprocess.run(cmd, capture_output=True, **k)
def jload(p, d=None): return jsonio.load(p, d)
def jsave(p, o): jsonio.save_atomic(p, o)
def work(src): return os.path.join(src, "_nexys_work")
def speech_labels(speech, f, segs, dur=0.0):
    """ok/frag/junk per transcript segment: speech.json when it matches the transcript, else classified on the fly
    (speech.json missing or older than the transcript) so hallucinations never reach subtitles / plans."""
    lab = (speech or {}).get(f)
    if lab is not None and len(lab) == len(segs): return lab
    try:
        from nxcore.speech import classify
        return [classify(s_, dur)[0] for s_ in segs]
    except Exception:
        return []

# ---------- probe ----------
def probe_file(f):
    o = json.loads(run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,r_frame_rate,width,height:stream_tags=rotate,creation_time:stream_side_data=rotation:format=duration:format_tags=creation_time", "-of", "json", f], text=True, encoding="utf-8").stdout)
    v = [s for s in o["streams"] if s["codec_type"] == "video"]
    a = [s for s in o["streams"] if s["codec_type"] == "audio"]
    if not v: return None
    v = v[0]; n, d = map(int, v["r_frame_rate"].split("/")); fps = n / d
    rot = int(float(v.get("tags", {}).get("rotate", 0) or 0))
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd: rot = int(sd["rotation"])
    w, h = v["width"], v["height"]
    if abs(rot) % 180 == 90: w, h = h, w
    ct = v.get("tags", {}).get("creation_time") or o["format"].get("tags", {}).get("creation_time")
    return dict(fps=fps, tb=round(fps), ntsc=abs(fps - round(fps)) > 0.01, dur=float(o["format"]["duration"]), w=w, h=h, raw_w=v["width"], raw_h=v["height"], rot=rot, audio=bool(a), created=ct)
def cmd_probe(a):
    """Recursive (sub-folders), parallel, cached by size+mtime — see nxcore/media.py."""
    from nxcore.media import probe_all
    probe_all(a.src, workers=getattr(a, "workers", 8), force=getattr(a, "force", False), progress=lambda m: print(m, flush=True))

def stem(f):
    from nxcore.media import stem as _s
    return _s(f)

# ---------- contact sheets ----------
def cmd_sheets(a):
    """Contact sheets (4 frames/clip, 10 clips/sheet) — frame grabs in parallel; skipped when media is unchanged."""
    from PIL import Image, ImageDraw
    import io
    from concurrent.futures import ThreadPoolExecutor
    src = a.src; W = work(src); media = jload(os.path.join(W, "media.json")); td = os.path.join(W, "thumbs"); os.makedirs(td, exist_ok=True)
    sig = "|".join(f"{f}:{p.get('key', p.get('dur'))}" for f, p in media.items())
    stamp = os.path.join(td, "_sig.txt")
    if not getattr(a, "force", False) and os.path.exists(stamp) and open(stamp, encoding="utf-8").read() == sig:
        print("sheets: không đổi (cache)"); return
    items = list(media.items())
    print(json.dumps({"stage": "sheets", "total": len(items)}), flush=True)
    QS = (0.1, 0.35, 0.6, 0.85)
    def grab_one_pass(path, dur):
        """All 4 frames from ONE ffmpeg (4 keyframe-seeked inputs → hstack) instead of 4 processes per clip."""
        cmd = ["ffmpeg", "-v", "error", "-nostdin"]
        for q in QS: cmd += ["-noaccurate_seek", "-ss", f"{dur * q:.2f}", "-i", path]
        g = ";".join(f"[{i}:v:0]scale=200:-2,setsar=1,format=rgb24[v{i}]" for i in range(len(QS)))
        g += ";" + "".join(f"[v{i}]" for i in range(len(QS))) + f"hstack=inputs={len(QS)}[o]"
        r = run(cmd + ["-filter_complex", g, "-map", "[o]", "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"], timeout=60)
        if r.returncode != 0 or not r.stdout: return None
        img = Image.open(io.BytesIO(r.stdout)).convert("RGB"); w = img.width // len(QS)
        return [img.crop((i * w, 0, (i + 1) * w, img.height)) for i in range(len(QS))]
    def grab(item):
        f, p = item; fr = []
        try: fr = grab_one_pass(os.path.join(src, f), float(p['dur'])) or []
        except Exception: fr = []
        for q in (() if fr else QS):            # fallback: one process per frame (a broken spot only loses its frame)
            try:   # keyframe seek (-noaccurate_seek): a contact sheet doesn't need the exact frame; never hang on a bad file
                r = run(["ffmpeg", "-v", "error", "-nostdin", "-noaccurate_seek", "-ss", f"{p['dur'] * q:.2f}", "-i", os.path.join(src, f), "-frames:v", "1", "-vf", "scale=200:-2", "-f", "image2pipe", "-vcodec", "png", "-"], timeout=30).stdout
            except subprocess.TimeoutExpired:
                continue
            try: fr.append(Image.open(io.BytesIO(r)).convert("RGB"))
            except Exception: pass
        if not fr: fr = [Image.new("RGB", (200, 112))]
        row = Image.new("RGB", (sum(i.width for i in fr), max(i.height for i in fr) + 22)); d = ImageDraw.Draw(row)
        x = 0
        for i in fr: row.paste(i, (x, 22)); x += i.width
        d.text((4, 4), f"{f}  {p['dur']:.1f}s  {p.get('created') or ''}", fill="yellow")
        return row
    rows = [None] * len(items)
    with ThreadPoolExecutor(max_workers=6) as ex:
        for k, (i, row) in enumerate(zip(range(len(items)), ex.map(grab, items)), 1):
            rows[i] = row; print(f"[{k}/{len(items)}] {items[i][0]}", flush=True)
    for g in glob.glob(os.path.join(td, "sheet*.jpg")): os.remove(g)
    for i in range(0, len(rows), 10):
        chunk = rows[i:i + 10]; S = Image.new("RGB", (max(r.width for r in chunk), sum(r.height for r in chunk))); y = 0
        for r in chunk: S.paste(r, (0, y)); y += r.height
        S.save(os.path.join(td, f"sheet{i // 10}.jpg"), quality=80)
    open(stamp, "w", encoding="utf-8").write(sig)
    print("sheets:", (len(rows) + 9) // 10)

# ---------- transcribe ----------
def _cuda_dll_paths():
    """faster-whisper/ctranslate2 needs cublas64_12 + cudnn9 on Windows. pip wheels (nvidia-cublas-cu12,
    nvidia-cudnn-cu12) put them in site-packages/nvidia/*/bin, which is NOT on the DLL search path -> register."""
    if os.name != "nt":
        return []
    import site
    roots = list(site.getsitepackages()) + [site.getusersitepackages()]
    found = []
    for r in roots:
        for b in glob.glob(os.path.join(r, "nvidia", "*", "bin")):
            if b in found:
                continue
            found.append(b)
            try: os.add_dll_directory(b)
            except Exception: pass
    if found:
        os.environ["PATH"] = os.pathsep.join(found + [os.environ.get("PATH", "")])
    return found

# Short names for fine-tuned CTranslate2 Whisper models (faster-whisper downloads HF repo ids itself)
WHISPER_ALIASES = {
    "erax-turbo": "erax-ai/EraX-WoW-Turbo-V1.1-CT2",   # large-v3-turbo fine-tuned on Vietnamese (MIT)
}

def _whisper(model, cuda):
    model = WHISPER_ALIASES.get(model, model)
    """Load Whisper; CUDA is verified with a real 1 s encode (load alone succeeds even when cublas is missing).
    macOS: faster-whisper has no Metal backend → Apple Silicon uses mlx-whisper on the GPU (nxcore.gpu.MLXWhisper),
    Intel Macs / missing mlx-whisper fall back to faster-whisper CPU int8."""
    from nxcore import gpu as _gpu
    if cuda and _gpu.IS_MAC:
        if _gpu.IS_APPLE_SILICON:
            try:
                m = _gpu.MLXWhisper(model)
                m.transcribe(np.zeros(16000, np.float32), language="vi")      # loads / downloads the MLX weights once
                print(f"whisper: Apple GPU (MLX) · {m.repo}", flush=True)
                return m, "mlx"
            except ImportError:
                print("whisper: chưa có mlx-whisper → CPU. Cài: python3 -m pip install mlx-whisper", flush=True)
            except Exception as e:
                print(f"whisper: MLX không chạy được ({str(e)[:100]}) → CPU", flush=True)
        cuda = False
    from faster_whisper import WhisperModel
    if cuda:
        dirs = _cuda_dll_paths()
        for ct in ("float16", "int8_float16"):
            try:
                m = WhisperModel(model, device="cuda", compute_type=ct)
                segs, _ = m.transcribe(np.zeros(16000, np.float32), language="vi", beam_size=1)
                list(segs)
                print(f"whisper: CUDA {ct} ({len(dirs)} nvidia dll dir)", flush=True)
                return m, "cuda"
            except Exception as e:
                err_str = str(e)
                print(f"whisper: CUDA {ct} không được ({err_str[:80]})", flush=True)
        if "err_str" in locals() and any(term in err_str.lower() for term in ("memory", "malloc", "pagefile")):
            print("whisper: Lỗi tràn bộ nhớ ảo (RAM/VRAM/Pagefile hết dung lượng do ổ C: đầy). Hãy kiểm tra dung lượng ổ C:.", flush=True)
        else:
            print("whisper: CUDA không dùng được -> chuyển CPU int8. "
                  "Cài: python -m pip install --user nvidia-cublas-cu12 nvidia-cudnn-cu12==9.*", flush=True)
    threads = max(1, min(4, (os.cpu_count() or 4) // 4))
    m = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=threads)
    print(f"whisper: CPU int8 ({threads} threads)", flush=True)
    return m, "cpu"

def _silent(qc, f, dur):
    q = (qc or {}).get(f) or {}
    sil = sum(max(0.0, r[1] - r[0]) for r in q.get("silence", []) if len(r) >= 2)
    return dur > 0 and sil / dur >= 0.97

def _free_gpu():
    """Unload Ollama models before whisper: a 7 GB vision model + whisper large-v3 overflow a 12 GB card and
    Windows silently spills to shared RAM (whisper then runs ~10× slower). Describe re-loads it later by itself."""
    import urllib.request
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    if not host.startswith("http"): host = "http://" + host
    try:
        with urllib.request.urlopen(host + "/api/ps", timeout=3) as r:
            models = [x.get("name") or x.get("model") for x in (json.load(r).get("models") or [])]
        for mdl in models:
            req = urllib.request.Request(host + "/api/generate", data=json.dumps({"model": mdl, "keep_alive": 0}).encode(),
                                         headers={"content-type": "application/json"})
            urllib.request.urlopen(req, timeout=20).read()
            print(f"whisper: giải phóng VRAM — unload Ollama {mdl}", flush=True)
    except Exception:
        pass
    try:                                    # VoiceStudio backend (tab Giọng) keeps its TTS model on the GPU — ask it to let go
        req = urllib.request.Request("http://127.0.0.1:3900/system/flush-memory", data=b"{}", headers={"content-type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()
        print("whisper: giải phóng VRAM — VoiceStudio flush-memory", flush=True)
    except Exception:
        pass

def cmd_transcribe(a):
    from concurrent.futures import ThreadPoolExecutor
    src = a.src; W = work(src); media = jload(os.path.join(W, "media.json")); ad = os.path.join(W, "audio"); td = os.path.join(W, "transcripts")
    os.makedirs(ad, exist_ok=True); os.makedirs(td, exist_ok=True)
    qc = jload(os.path.join(W, "qc.json"), {})

    # Adaptive preset rules
    preset_id = getattr(a, "preset", None)
    if not preset_id and os.path.exists(os.path.join(W, "preset.json")):
        try:
            preset_id = jload(os.path.join(W, "preset.json"), {}).get("id")
        except Exception:
            pass
    preset_cfg = {}
    if preset_id:
        try:
            from nxcore.presets import get_preset
            preset_cfg = get_preset(preset_id)
        except Exception:
            pass

    if preset_cfg.get("disable_transcribe"):
        print(f"whisper: Tắt nhận diện giọng nói theo preset '{preset_id}' (Event / Recap dựng theo nhạc)", flush=True)
        for f in media:
            o = os.path.join(td, stem(f) + ".json")
            if not os.path.exists(o):
                jsave(o, {"file": f, "key": media[f].get("key"), "segments": [], "note": f"tắt theo preset {preset_id}"})
        return

    skip_roles = set(preset_cfg.get("skip_transcribe_roles", []))
    # Speech map first (CPU Silero VAD over the whole clip, cached in vad.json — ~25 s for 2 h of footage):
    # whisper only ever sees clips that really contain speech, whatever role triage guessed.
    V = {}
    try:
        from nxcore import vad as _vad
        V = _vad.scan(src, {f: p for f, p in media.items() if not a.files or f in a.files},
                      progress=lambda m: print(m, flush=True))
    except Exception as e:
        _vad = None
        print(f"whisper: VAD không chạy được ({str(e)[:100]}) → dùng QC silence", flush=True)

    def decide(f, p):
        """-> (None, '') = transcribe, else (stub note, reason key)."""
        if not p.get("audio"): return "no audio", "noaudio"
        v = V.get(f)
        if v and _vad and _vad.fresh(v, p):
            sp = float(v.get("speech") or 0)
            if sp < _vad.MIN_SPEECH_S: return f"không có giọng nói (VAD {sp:.1f}s)", "nospeech"
            role = p.get("role", "b_roll")
            if role in skip_roles and sp < _vad.AROLL_SPEECH_S:
                return f"bỏ qua role {role} theo preset (VAD {sp:.1f}s)", "role"
            return None, ""
        if _silent(qc, f, p.get("dur", 0)): return "silent (qc)", "nospeech"
        role = p.get("role", "b_roll")
        if role in skip_roles: return f"bỏ qua role {role} theo preset", "role"
        return None, ""

    todo = []; skipped = {"nospeech": 0, "role": 0, "noaudio": 0}; keys = {f: p.get("key") for f, p in media.items()}
    for f, p in media.items():
        if a.files and f not in a.files: continue
        o = os.path.join(td, stem(f) + ".json")
        note, why = decide(f, p)
        if os.path.exists(o) and not a.force:
            # a stub (empty, with a note) is redone when the clip now needs whisper (VAD found speech / role changed);
            # a transcript of another version of the file (key) or from another model is redone. Old files without
            # key/model are kept (no mass re-transcription after an update).
            old = jload(o, {}) or {}
            stale = old.get("key") not in (None, p.get("key")) or old.get("model") not in (None, a.model)
            if not stale and not (note is None and old.get("note") and not old.get("segments")):
                continue
        if note is not None:
            if why != "noaudio": jsave(o, {"file": f, "key": p.get("key"), "segments": [], "note": note})
            skipped[why] += 1; continue
        todo.append((f, o, float((V.get(f) or {}).get("speech") or 0)))
    if skipped["nospeech"]:
        print(f"whisper: Bỏ qua {skipped['nospeech']} clip không có giọng nói (VAD) — không tốn GPU", flush=True)
    if skipped["role"]:
        print(f"whisper: Bỏ qua {skipped['role']} clip B-roll gần như không thoại theo preset", flush=True)
    print(json.dumps({"stage": "transcribe", "total": len(todo)}), flush=True)
    if todo:
        _free_gpu()
        m, dev = _whisper(a.model, a.cuda)
        batch_pipe = None
        batch_size = getattr(a, "batch_size", 16)
        if dev == "cuda" and batch_size > 1:
            try:
                from faster_whisper import BatchedInferencePipeline
                batch_pipe = BatchedInferencePipeline(model=m)
                print(f"whisper: BatchedInferencePipeline bật (batch_size={batch_size})", flush=True)
            except Exception as e:
                print(f"whisper: không bật được BatchedInferencePipeline ({e})", flush=True)
        from nxcore.vad import pcm
        def load_audio(item):                      # audio-only decode straight into numpy (no WAV on disk)
            return pcm(os.path.join(src, item[0]))
        # decode runs ≤ 4 clips ahead of the GPU (bounded → RAM stays small even on 20-min takes)
        pool = ThreadPoolExecutor(max_workers=4); AHEAD = 4
        futs = {}
        def ensure(j):
            if j < len(todo) and j not in futs: futs[j] = pool.submit(load_audio, todo[j])
        for j in range(AHEAD): ensure(j)
        t_all = time.time(); audio_s = 0.0
        for k, (f, o, sp) in enumerate(todo, 1):
            ensure(k - 1 + AHEAD)
            try:
                audio = futs.pop(k - 1).result()
            except Exception as e:
                print(f"[{k}/{len(todo)}] {f} lỗi tách âm: {e}", flush=True); continue
            if not len(audio):
                jsave(o, {"file": f, "key": keys.get(f), "segments": [], "note": "không đọc được audio"}); continue
            t0 = time.time(); data = []
            def once(vad):
                out = []
                engine = batch_pipe if (batch_pipe is not None and dev == "cuda" and vad) else m
                kw = dict(language=a.lang, vad_filter=vad, condition_on_previous_text=False,
                          word_timestamps=True, beam_size=a.beam, initial_prompt=a.prompt or None)
                if vad:
                    kw["vad_parameters"] = dict(threshold=0.35, min_speech_duration_ms=200)
                if engine is batch_pipe:
                    kw["batch_size"] = batch_size
                segs, _ = engine.transcribe(audio, **kw)
                for s_ in segs:                    # all kept: nxcore.speech flags hallucinations / no-speech as junk
                    t = s_.text.strip()
                    if not t: continue
                    out.append({"start": round(s_.start, 2), "end": round(s_.end, 2), "text": t,
                                "lp": round(s_.avg_logprob, 3), "ns": round(s_.no_speech_prob, 3), "cr": round(s_.compression_ratio, 2),   # confidence → nxcore.speech
                                "words": [[round(w.start, 2), round(w.end, 2), w.word.strip(), round(w.probability, 2)] for w in (s_.words or [])]})
                return out
            passes = (True, False) if getattr(a, "two_pass", False) else (True,)
            for vad in passes:
                try:
                    data = once(vad)
                except RuntimeError as e:
                    err_s = str(e).lower()
                    if dev in ("cuda", "mlx") and ("out of memory" in err_s or "cuda" in err_s or "metal" in err_s):
                        print(f"whisper: lỗi CUDA giữa chừng -> chuyển CPU ({str(e)[:100]})", flush=True)
                        m, dev = _whisper(a.model, False)
                        batch_pipe = None
                        data = once(vad)
                    else:
                        raise
                if data: break
            if data and a.lang:                    # acoustic word times (wav2vec2 CTC) instead of whisper's attention guess
                try:
                    from nxcore.ai import align as _al
                    if _al.available(a.lang): _al.refine(audio, data, a.lang)
                except Exception as e:
                    print(f"whisper: căn chỉnh từ lỗi ({str(e)[:100]}) → giữ thời gian của whisper", flush=True)
            dur = len(audio) / 16000.0; audio_s += dur; dt = time.time() - t0
            jsave(o, {"file": f, "key": keys.get(f), "model": a.model, "segments": data})
            print(f"[{k}/{len(todo)}] {f} · {len(data)} câu · {sp:.0f}s thoại/{dur:.0f}s · {dt:.1f}s", flush=True)
            del audio
        pool.shutdown(wait=False)
        el = time.time() - t_all
        print(f"whisper: {len(todo)} clip · {audio_s / 60:.1f} phút audio trong {el / 60:.1f} phút ({audio_s / max(el, 0.1):.0f}× realtime)", flush=True)
    # readable dump for Claude (hallucinated / no-speech lines left out, as before)
    with open(os.path.join(W, "transcripts_all.txt"), "w", encoding="utf-8") as fh:
        for j in sorted(glob.glob(os.path.join(td, "*.json"))):
            if j.endswith(".v2.json"): continue
            d = jload(j)
            if not d or not d.get("segments"): continue
            lab = speech_labels(None, d.get("file"), d["segments"], float((media.get(d.get("file")) or {}).get("dur") or 0))
            keep = [s_ for k_, s_ in enumerate(d["segments"]) if not (k_ < len(lab) and lab[k_] == "junk")]
            if not keep: continue
            fh.write(f"## {d['file']}\n")
            for s_ in keep: fh.write(f"  {s_['start']:6.2f}-{s_['end']:6.2f} {s_['text']}\n")
    print("DONE")

# ---------- shake ----------
def motion_series(path, fps=15, size=256):
    import cv2
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-vf", f"fps={fps},scale={size}:{size},format=gray", "-f", "rawvideo", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    win = cv2.createHanningWindow((size, size), cv2.CV_32F); d = [(0.0, 0.0)]
    frame_bytes = size * size
    c = proc.stdout.read(frame_bytes)
    if not c or len(c) < frame_bytes:
        proc.stdout.close(); proc.wait()
        return np.array(d)
    prev = np.frombuffer(c, np.uint8).reshape(size, size).astype(np.float32)
    while True:
        c = proc.stdout.read(frame_bytes)
        if not c or len(c) < frame_bytes:
            break
        cur = np.frombuffer(c, np.uint8).reshape(size, size).astype(np.float32)
        (dx, dy), _ = cv2.phaseCorrelate(prev * win, cur * win)
        d.append((dx, dy))
        prev = cur
    proc.stdout.close(); proc.wait()
    return np.array(d)
def shake_ranges(d, fps=15, thr=1.2, win=0.5, min_len=0.4):
    k = max(3, int(fps * win)); ker = np.ones(k) / k
    sm = np.stack([np.convolve(d[:, i], ker, "same") for i in (0, 1)], 1)
    jit = np.linalg.norm(d - sm, axis=1); score = np.sqrt(np.convolve(jit ** 2, ker, "same"))
    bad = score > thr; out = []; i = 0
    while i < len(bad):
        if bad[i]:
            j = i
            while j < len(bad) and bad[j]: j += 1
            if (j - i) / fps >= min_len: out.append([round(i / fps, 2), round(j / fps, 2), round(float(score[i:j].max()), 2)])
            i = j
        else: i += 1
    return out, score
def cmd_shake(a):
    """v2: camera-motion stability + usable ranges + composition (nxcore/stability.py)."""
    from nxcore.stability import analyze_all
    analyze_all(a.src, workers=a.workers, cuda=not getattr(a, "no_cuda", False), force=a.force, files=a.files,
                progress=lambda m: print(m, flush=True), preset=getattr(a, "preset", None))
    print("DONE")

# ---------- sync (audio onset cross-correlation) ----------
def onset(path, sr=8000, hop=80):
    x = np.frombuffer(run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]).stdout, np.float32)
    n = 512; fr = np.lib.stride_tricks.sliding_window_view(x, n)[::hop] * np.hanning(n)
    S = np.log1p(np.abs(np.fft.rfft(fr, axis=1))[:, 5:120] * 50); fl = np.diff(S, axis=0); fl[fl < 0] = 0
    o = fl.sum(1); o = o - np.convolve(o, np.ones(50) / 50, "same"); return (o - o.mean()) / (o.std() + 1e-9), sr / hop
def xcorr(a, b):
    n = 1 << int(np.ceil(np.log2(len(a) + len(b)))); c = np.fft.irfft(np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n)), n)
    c = np.concatenate([c[-(len(b) - 1):], c[:len(a)]]) / len(b); return c
def cmd_sync(a):
    src = a.src; W = work(src); R, fps = onset(os.path.join(src, a.master)); res = jload(os.path.join(W, "sync.json"), {})
    for f in a.files:
        X, _ = onset(os.path.join(src, f)); c = xcorr(R, X); i = int(c.argmax()); lag = (i - (len(X) - 1)) / fps
        z = float((c[i] - c.mean()) / (c.std() + 1e-9))  # peak z-score; music beats create side peaks, so verify visually when z < 12
        res[f] = {"master": a.master, "offset": round(lag, 3), "z": round(z, 1)}
        # visual proof: master frame vs clip frame at the same instant -> Claude must LOOK before trusting the offset
        from PIL import Image
        import io
        md = probe_file(os.path.join(src, f))["dur"]; tc = md / 2; tm = tc + lag; ims = []
        for path, tt in ((os.path.join(src, a.master), tm), (os.path.join(src, f), tc)):
            r = run(["ffmpeg", "-v", "error", "-ss", str(max(0, tt)), "-i", path, "-frames:v", "1", "-vf", "scale=-2:480", "-f", "image2pipe", "-vcodec", "png", "-"]).stdout
            ims.append(Image.open(io.BytesIO(r)).convert("RGB") if r else Image.new("RGB", (270, 480)))
        S = Image.new("RGB", (ims[0].width + ims[1].width, 480)); S.paste(ims[0], (0, 0)); S.paste(ims[1], (ims[0].width, 0))
        os.makedirs(os.path.join(W, "sync"), exist_ok=True); pv = os.path.join(W, "sync", f"{os.path.splitext(f)[0]}_vs_master.jpg"); S.save(pv, quality=80)
        res[f]["preview"] = pv; print(f, res[f]); jsave(os.path.join(W, "sync.json"), res)

# ---------- build: plan.json -> silence cut + shake-avoid + XML (V1/V2/V3, A1/A2/A3) + SRT + highlights + SFX + bridge command ----------
def norm(s): return re.sub(r"[^\w]+", " ", unicodedata.normalize("NFC", s.lower())).strip()
def silence_pieces(words, i, o, cfg):
    ws = [w for w in words if w[1] > i and w[0] < o]
    if not ws: return [[i, o]]
    groups = [[ws[0]]]
    for w in ws[1:]:
        (groups[-1].append(w) if w[0] - groups[-1][-1][1] <= cfg["gap"] else groups.append([w]))
    P = []
    for g in groups:
        a, b = max(i, g[0][0] - cfg["pad_in"]), min(o, g[-1][1] + cfg["pad_out"])
        if P and a <= P[-1][1]: P[-1][1] = b
        else: P.append([a, b])
    keep = [p for p in P if p[1] - p[0] >= cfg["min_piece"]] or P
    return [[round(a, 3), round(b, 3)] for a, b in keep]
def avoid_shake(i, o, ranges, dur):
    L = o - i
    hit = lambda a, b: any(a < r[1] and b > r[0] for r in ranges)
    if not hit(i, o): return i, o, False
    best = None
    for k in range(0, int((dur - L) * 10) + 1):
        a = k / 10
        if not hit(a, a + L) and (best is None or abs(a - i) < abs(best - i)): best = a
    return (best, best + L, False) if best is not None else (i, o, True)
def render_highlight(text, path, W, H, style):
    from PIL import Image, ImageDraw, ImageFont
    palette = {
        "pink": ((236, 72, 153), (255, 255, 255)),
        "yellow": ((250, 204, 21), (20, 20, 20)),
        "black": ((15, 15, 20), (255, 255, 255)),
        "red": ((220, 38, 38), (255, 255, 255)),
        "gold": ((245, 158, 11), (255, 255, 255)),
        "blue": ((37, 99, 235), (255, 255, 255)),
        "cyan": ((6, 182, 212), (20, 20, 20)),
        "green": ((16, 185, 129), (255, 255, 255)),
        "purple": ((139, 92, 246), (255, 255, 255))
    }
    C = palette.get(style, palette["pink"])
    font = None
    for fp in ("C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if os.path.exists(fp): font = ImageFont.truetype(fp, int(W * 0.075)); break
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    text = unicodedata.normalize("NFC", text); bb = d.textbbox((0, 0), text, font=font); tw, th = bb[2] - bb[0], bb[3] - bb[1]
    while tw > W * 0.86: font = ImageFont.truetype(font.path, int(font.size * 0.9)); bb = d.textbbox((0, 0), text, font=font); tw, th = bb[2] - bb[0], bb[3] - bb[1]
    px, py = int(W * 0.045), int(W * 0.03); x, y = (W - tw) // 2, int(H * 0.28)
    d.rounded_rectangle([x - px, y - py, x + tw + px, y + th + py + bb[1]], radius=int(W * 0.03), fill=C[0] + (240,))
    d.text((x, y), text, font=font, fill=C[1]); im.save(path)
def render_gap(text, path, W, H):
    """Full-frame placeholder card: dark slate, amber frame, centred wrapped text (Vietnamese-safe fonts)."""
    from PIL import Image, ImageDraw, ImageFont
    import textwrap
    fp = next((x for x in ("C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf") if os.path.exists(x)), None)
    short = min(W, H); im = Image.new("RGB", (W, H), (22, 22, 26)); d = ImageDraw.Draw(im)
    m = int(short * 0.04); d.rectangle([m, m, W - m, H - m], outline=(245, 166, 35), width=max(4, short // 150))
    text = unicodedata.normalize("NFC", text).replace("⚠", "▲"); size = int(short * 0.06)   # Arial has ▲, not ⚠
    while True:
        font = ImageFont.truetype(fp, size) if fp else ImageFont.load_default()
        cw = max(8, int((W - 4 * m) / (size * 0.55)))
        lines = [l2 for para in text.split("\n") for l2 in (textwrap.wrap(para, cw) or [""])]
        lh = int(size * 1.3); th = lh * len(lines)
        if th <= H - 6 * m or size <= 14: break
        size = int(size * 0.88)
    y = (H - th) // 2
    for k, ln in enumerate(lines):
        f2 = ImageFont.truetype(fp, int(size * 1.15)) if (fp and k == 0) else font
        bb = d.textbbox((0, 0), ln, font=f2); d.text(((W - (bb[2] - bb[0])) // 2, y), ln, font=f2, fill=(245, 166, 35) if k == 0 else (235, 235, 235)); y += lh
    im.save(path)
def render_title(text, path, W, H, style="text", pos=""):
    """VO_TEXT_PATCH: on-screen text PNG from [TEXT]/[TITLE]. title = big, centred; text = lower third, left. White + soft shadow."""
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    import textwrap
    fp = next((x for x in ("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf") if os.path.exists(x)), None)
    short = min(W, H); size = int(short * (0.085 if style == "title" else 0.05)); text = unicodedata.normalize("NFC", text)
    while True:
        font = ImageFont.truetype(fp, size) if fp else ImageFont.load_default()
        cw = max(8, int(W * (0.8 if (style == "title" or H > W) else 0.6) / (size * 0.55)))
        lines = textwrap.wrap(text, cw) or [""]
        if len(lines) <= 3 or size <= 18: break
        size = int(size * 0.9)
    lh = int(size * 1.25); th = lh * len(lines)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); sh = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d, ds = ImageDraw.Draw(im), ImageDraw.Draw(sh)
    pos = str(pos or "").lower()            # template: where the reference put its text (top / center / lower …)
    vc = "top" if "top" in pos or "trên" in pos else "center" if ("center" in pos or "giữa" in pos) else "lower" if pos else ""
    vc = vc or ("center" if style == "title" else "lower")
    y = int(H * 0.10) if vc == "top" else (H - th) // 2 if vc == "center" else int(H * 0.80) - th
    for ln in lines:
        bb = d.textbbox((0, 0), ln, font=font); x = (W - (bb[2] - bb[0])) // 2 if (style == "title" or vc == "center") else int(W * 0.06)
        ds.text((x + 2, y + 3), ln, font=font, fill=(0, 0, 0, 170)); d.text((x, y), ln, font=font, fill=(255, 255, 255, 255)); y += lh
    sh = sh.filter(ImageFilter.GaussianBlur(max(2, size // 12))); Image.alpha_composite(sh, im).save(path)
def pieces_sidecar(name, fps_exact, pieces, ov_placed=()):
    """{"name", "fps", "pieces": [{"seg", "file", "tl_start", "tl_end", "in", "out", "track", "speed", "id"}],
    "overlays": [...]} — seconds (timeline and source), rounded to ms. Gap cards are pieces with "gap": true and
    file null. Overlays (V2 cutaways) are listed apart: they belong to no plan segment of their own."""
    r3 = lambda x: round(float(x), 3)
    out = []
    for k, p in enumerate(pieces):
        q = {"seg": p["seg"], "file": None if p.get("gap") else p["file"], "tl_start": r3(p["st"] / fps_exact),
             "tl_end": r3(p["en"] / fps_exact), "in": r3(p["i"]), "out": r3(p["o"]), "track": "V1",
             "speed": r3(p.get("speed", 100.0)), "id": f"v{k}"}
        if p.get("gap"):
            q["gap"] = True
        out.append(q)
    ovs = [{"overlay": k, "anchor": ov.get("anchor"), "file": ov["file"], "tl_start": r3(st / fps_exact), "tl_end": r3(en / fps_exact),
            "in": r3(ov["in"]), "out": r3(float(ov["in"]) + float(ov["dur"])), "track": "V2", "speed": 100.0, "id": f"o{k}"}
           for k, ov, st, en in ov_placed]
    return {"name": name, "fps": fps_exact, "pieces": out, "overlays": ovs}
def srt_ts(t): ms = int(round(t * 1000)); return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
def cmd_build(a):
    P = jload(a.plan); src = P["src"]; W = work(src); media = jload(os.path.join(W, "media.json")); shake = jload(os.path.join(W, "shake.json"), {})
    out = P["out"]; os.makedirs(os.path.join(out, "highlights"), exist_ok=True)
    SW, SH, FPS = P["seq"]["w"], P["seq"]["h"], P["seq"].get("fps", 29.97); TB = round(FPS); NT = abs(FPS - TB) > 0.01; fps_exact = TB * 1000 / 1001 if NT else TB
    fr = lambda s: int(round(s * fps_exact)); sil = P.get("silence", {"on": False}); sub = P.get("subs", {"on": False}); sfx = P.get("sfx", {"on": False})
    words = {}
    try:
        from nxcore.speech import load as _speech_load
        speech = _speech_load(src)
    except Exception:
        speech = {}
    def W_(f):
        if f not in words:
            d = jload(os.path.join(W, "transcripts", stem(f) + ".json"), {"segments": []}) or {"segments": []}
            segs_ = d.get("segments") or []
            lab = speech_labels(speech, f, segs_, float((media.get(f) or {}).get("dur") or 0))
            words[f] = [w[:3] + [k] for k, s in enumerate(segs_) if not (k < len(lab) and lab[k] == "junk") for w in s.get("words", []) if w[2]]   # no subtitles for junk
        return words[f]
    pieces, flags, t = [], [], 0
    for si, s in enumerate(P["segments"]):
        tl_ = s.get("tl")                # montage: exact timeline span → frames from the span, cuts stay on the beat
        if s.get("gap"):                  # missing footage: text card with the reserved duration (V1), no audio
            d = max(1, fr(float(tl_[1])) - t) if tl_ else fr(max(0.5, s["out"] - s["in"]))
            pieces.append(dict(seg=si, file=None, gap=True, text=s.get("text") or s.get("label", "⚠ THIẾU"), i=0, o=d / fps_exact, st=t, en=t + d,
                               audio=False, label=s.get("label", ""), scene=s.get("scene", ""), first=True, speed=100.0, rot=0.0))
            flags.append(f"seg{si}: ⚠ THIẾU — chừa {d / fps_exact:.1f}s ({s.get('label', '')[:60]})"); t += d
            continue
        f = s["file"]; m = media[f]; i, o = s["in"], s["out"]
        fx = P.get("autofix", {"slowmo": True, "rotate": True})
        sp = float(s.get("speed") or 100) if ((fx.get("slowmo", True) or s.get("pinned")) and not s.get("audio", True)) else 100.0   # slow-mo only on silent b-roll
        if tl_:
            d_tl = max(1, fr(float(tl_[1])) - t)
            o = i + d_tl / fps_exact * sp / 100.0
            if o > float(m["dur"]) - 0.02:                    # never past the end of the clip: slide the window back
                i, o = max(0.0, float(m["dur"]) - 0.02 - (o - i)), float(m["dur"]) - 0.02
        sh_ = shake.get(f) or {}
        # slow-mo-only stretches (shake.json "ranges_slowmo") are steady at ≤ 50 % speed, shaky in real time
        bad_ = list(sh_.get("ranges") or []) + (list(sh_.get("ranges_slowmo") or []) if sp > 50 else [])
        if not s.get("audio", True) and P.get("shake", {}).get("on") and f in shake and not s.get("pinned") and not tl_:   # STRICT_PIN_PATCH: script range is final
            i2, o2, still = avoid_shake(i, o, bad_, m["dur"])
            if (i2, o2) != (i, o): flags.append(f"seg{si} {f}: shake → dời {i:.2f}→{i2:.2f}")
            if still: flags.append(f"seg{si} {f}: SHAKE không tránh được")
            i, o = i2, o2
        elif s.get("audio", True) and f in shake and any(i < r[1] and o > r[0] for r in bad_):
            flags.append(f"seg{si} {f}: thoại có đoạn rung {[r for r in bad_ if i < r[1] and o > r[0]]}")
        ps = silence_pieces(W_(f), i, o, sil) if (sil.get("on") and s.get("audio", True) and s.get("silence", True)) else [[i, o]]
        rot = float(s.get("rotate_fix") or 0) if fx.get("rotate", True) else 0.0
        if sp != 100: flags.append(f"seg{si} {f}: tự áp slow-mo {sp:.0f}%")
        if rot: flags.append(f"seg{si} {f}: tự xoay {rot:+.1f}° (bù nghiêng) + scale bù góc")
        for pi, (a0, b0) in enumerate(ps):
            d = d_tl if (tl_ and len(ps) == 1) else fr((b0 - a0) * 100.0 / sp)
            pieces.append(dict(seg=si, file=f, i=a0, o=b0, st=t, en=t + d, audio=s.get("audio", True),
                               label=s.get("label", ""), scene=s.get("scene", ""), first=pi == 0, color=s.get("color") or "",
                               speed=sp, rot=rot, scale=float(s.get("scale", 100.0)), zoom=s.get("zoom") if len(ps) == 1 else None,
                               xfade=float(s.get("xfade") or 0) if pi == 0 else 0.0,
                               l_cut=float(s.get("l_cut", 0.0)) if pi == len(ps) - 1 else 0.0,
                               j_cut=float(s.get("j_cut", 0.0)) if pi == 0 else 0.0))
            t += d
    seq_end = t + fr(P.get("end_slate", 0))
    def adur(p_):
        return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p_], text=True).stdout.strip() or 0)
    rate = lambda tb, nt: f"<rate><timebase>{tb}</timebase><ntsc>{'TRUE' if nt else 'FALSE'}</ntsc></rate>"
    esc = lambda x: str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    done = set(); _fids = {}
    def fid(f, path=None):
        """unique <file id> per referenced file: "A B.mp4" / "A_B.mp4" / NFC vs NFD spellings never share an id."""
        k = (f, path)
        if k not in _fids:
            slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", unicodedata.normalize("NFKD", os.path.basename(str(f))).encode("ascii", "ignore").decode())[:40]
            _fids[k] = f"file-{len(_fids) + 1}-{slug}"
        return _fids[k]
    def safe_media(path):
        """Premiere báo 'project appears to be damaged' khi import XML có file tên chứa & # % ; (vd nhạc
        'Vietsub&Engsub….mp3'): dùng bản link/copy tên an toàn trong _nexys_work/safe_media."""
        base = os.path.basename(path)
        if not re.search(r"[&#%;]", path):
            return path
        try:
            d = os.path.join(src, "_nexys_work", "safe_media")
            os.makedirs(d, exist_ok=True)
            dst = os.path.join(d, re.sub(r"[&#%;]+", "_", base))
            if not (os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(path)):
                try:
                    if os.path.exists(dst): os.remove(dst)
                    os.link(path, dst)
                except Exception:
                    shutil.copy2(path, dst)
            print(f"  ⚠ tên file có ký tự Premiere không đọc được trong XML → dùng bản an toàn: {dst}")
            return dst.replace("\\", "/")
        except Exception as e:
            print(f"  ⚠ không tạo được bản an toàn cho {base}: {e}")
            return path
    def fref(f, path=None, still=False, w=None, h=None, audio_only=False):
        fi = fid(f, path)
        if fi in done: return f'<file id="{fi}"/>'
        done.add(fi); path = safe_media(path or (src.rstrip("/\\") + "/" + f)); url = "file://localhost/" + urllib.parse.quote(path.replace("\\", "/"))
        if audio_only:
            du = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], text=True).stdout.strip() or 1)
            return f'<file id="{fi}"><name>{esc(os.path.basename(path))}</name><pathurl>{url}</pathurl>{rate(TB, NT)}<duration>{fr(du)}</duration><media><audio><channelcount>2</channelcount></audio></media></file>'
        if still: return f'<file id="{fi}"><name>{esc(f)}</name><pathurl>{url}</pathurl>{rate(TB, NT)}<duration>{fr(3600)}</duration><media><video><samplecharacteristics><width>{w}</width><height>{h}</height></samplecharacteristics></video></media></file>'
        m = media[f]
        return (f'<file id="{fi}"><name>{esc(os.path.basename(f))}</name><pathurl>{url}</pathurl>{rate(m["tb"], m["ntsc"])}<duration>{int(m["dur"] * m["fps"])}</duration>'
                f'<media><video><samplecharacteristics><width>{m["raw_w"]}</width><height>{m["raw_h"]}</height></samplecharacteristics></video><audio><channelcount>1</channelcount></audio></media></file>')
    def motion(sc, rot=0.0, kf=None):
        """Basic Motion: static scale (+ rotation); kf = [(source frame, scale %), …] → scale keyframes (template zoom in/out).
        Keyframe time = source frame (in point + offset), same convention as level_kf."""
        rp = f'<parameter><parameterid>rotation</parameterid><name>Rotation</name><valuemin>-8640</valuemin><valuemax>8640</valuemax><value>{rot:.2f}</value></parameter>' if rot else ""
        kx = "".join(f"<keyframe><when>{w_}</when><value>{v_:.2f}</value></keyframe>" for w_, v_ in (kf or []))
        return f'<filter><effect><name>Basic Motion</name><effectid>basic</effectid><effectcategory>motion</effectcategory><effecttype>motion</effecttype><mediatype>video</mediatype><parameter><parameterid>scale</parameterid><name>Scale</name><valuemin>0</valuemin><valuemax>1000</valuemax><value>{sc:.2f}</value>{kx}</parameter>{rp}</effect></filter>'
    def rot_zoom(rot):
        # a rotated frame must be scaled up so no black corners show: cos θ + sin θ · (long side / short side)
        th = math.radians(abs(rot)); return math.cos(th) + math.sin(th) * max(SW, SH) / min(SW, SH)
    def timeremap(sp):
        prm = lambda pid, v, mn="", mx="": f'<parameter authoringApp="PremierePro"><parameterid>{pid}</parameterid><name>{pid}</name>{mn}{mx}<value>{v}</value></parameter>'
        return ('<filter><effect><name>Time Remap</name><effectid>timeremap</effectid><effectcategory>motion</effectcategory><effecttype>motion</effecttype><mediatype>video</mediatype>'
                + prm("variablespeed", 0, "<valuemin>0</valuemin>", "<valuemax>1</valuemax>") + prm("speed", f"{sp:g}", "<valuemin>-100000</valuemin>", "<valuemax>100000</valuemax>")
                + prm("reverse", "FALSE") + prm("frameblending", "FALSE") + '</effect></filter>')
    def gain(db): return f'<filter><effect><name>Audio Levels</name><effectid>audiolevels</effectid><effectcategory>audiolevels</effectcategory><effecttype>audiolevels</effecttype><mediatype>audio</mediatype><parameter><parameterid>level</parameterid><name>Level</name><valuemin>0</valuemin><valuemax>3.98109</valuemax><value>{10 ** (db / 20):.4f}</value></parameter></effect></filter>'
    FIT = (P["seq"].get("fit") or "fill").lower()
    def fit(f): m = media[f]; return 100.0 * (min if FIT == "fit" else max)(SW / m["w"], SH / m["h"])  # fill = crop to frame, fit = whole picture (letterbox)
    BA = P.get("broll_audio", {"on": True, "gain_db": -9})      # every clip keeps its own sound (b-roll nat sound on A2)
    V1, A1, V2, A2, V3, A3 = [], [], [], [], [], []
    V4, A4 = [], []          # VO_TEXT_PATCH: V4 = [TEXT]/[TITLE] PNG, A4 = VO audio
    LABELS = ("Violet", "Iris", "Caribbean", "Lavender", "Cerulean", "Forest", "Rose", "Mango", "Purple", "Blue", "Teal",
              "Magenta", "Tan", "Green", "Brown", "Yellow")
    def label_xml(cid):
        """màu clip trên timeline (Premiere đọc <labels><label2> khi import XML) — plan segment "color": tên hoặc 0–15"""
        m_ = re.match(r"^[vab](\d+)$", str(cid))
        c_ = pieces[int(m_.group(1))].get("color") if m_ and int(m_.group(1)) < len(pieces) else ""
        if c_ == "" or c_ is None: return ""
        if isinstance(c_, int) or str(c_).isdigit(): c_ = LABELS[int(c_) % 16]
        return f"<labels><label2>{esc(c_)}</label2></labels>"
    def clip(cid, f, st, en, i, o, extra="", sp=100.0, hin=0, hout=0, open_s=False, open_e=False):
        m = media[f]; k = 100.0 / sp; si = fr(i * k); so = si + (en - st); extra = extra + label_xml(cid)
        s_ = "-1" if (hin or open_s) else st; e_ = "-1" if (hout or open_e) else en
        return (f'<clipitem id="{cid}"><name>{esc(os.path.basename(f))}</name><enabled>TRUE</enabled><duration>{fr(m["dur"] * k)}</duration>{rate(TB, NT)}'
                f'<start>{s_}</start><end>{e_}</end><in>{si - hin}</in><out>{so + hout}</out>{fref(f)}{extra}</clipitem>')
    # ---- transitions: cross dissolve at scene changes + part of the b-roll montage cuts, fade from/to black at the ends
    TRN = dict({"on": True, "dur": 0.8, "scene": True, "broll_rate": 0.3, "fade_in": 0.8, "fade_out": 1.0, "audio": True}, **(P.get("transitions") or {}))
    TPS = 254016000000
    def trans(st, en, align, cut_ticks, media_type):
        if media_type == "video":
            eff = ('<effect><name>Cross Dissolve</name><effectid>Cross Dissolve</effectid><effectcategory>Dissolve</effectcategory><effecttype>transition</effecttype>'
                   '<mediatype>video</mediatype><wipecode>0</wipecode><wipeaccuracy>100</wipeaccuracy><startratio>0</startratio><endratio>1</endratio><reverse>FALSE</reverse></effect>')
        else:
            eff = ('<effect><name>Cross Fade (+3dB)</name><effectid>KGAudioTransCrossFade3dB</effectid><effecttype>transition</effecttype>'
                   '<mediatype>audio</mediatype><wipecode>0</wipecode><wipeaccuracy>100</wipeaccuracy><startratio>0</startratio><endratio>1</endratio><reverse>FALSE</reverse></effect>')
        return (f'<transitionitem><start>{st}</start><end>{en}</end><alignment>{align}</alignment><cutPointTicks>{int(cut_ticks)}</cutPointTicks>'
                f'{rate(TB, NT)}{eff}</transitionitem>')
    on_a1 = lambda q: q.get("audio") and not q.get("gap")
    on_a2 = lambda q: (not q.get("audio")) and not q.get("gap") and q.get("speed", 100) == 100 and BA.get("on", True) and media[q["file"]].get("audio", True)
    TV = {}                                   # piece index k → handle frames of the centre dissolve on the cut before it
    TA = {}                                   # piece index k → handle frames of audio crossfade (Ctrl+Shift+D)
    if TRN.get("on", True) and pieces:
        nb, every = 0, (max(1, round(1.0 / float(TRN["broll_rate"]))) if float(TRN.get("broll_rate") or 0) > 0 else 0)
        for k in range(1, len(pieces)):
            pa, pb = pieces[k - 1], pieces[k]
            if pa["en"] != pb["st"] or pa.get("gap") or pb.get("gap") or pa.get("speed", 100) != 100 or pb.get("speed", 100) != 100:
                continue
            scene_change = pb["first"] and pb["scene"] != pa["scene"]
            want = (scene_change and TRN.get("scene", True)) or pb.get("xfade", 0) > 0     # montage: dissolve asked per cut
            if not want and not pa["audio"] and not pb["audio"] and every:
                nb += 1; want = nb % every == 0          # every n-th b-roll → b-roll cut in a montage
            if not want:
                continue
            Lf = min(fr(float(pb.get("xfade") or TRN["dur"])), int(0.4 * min(pa["en"] - pa["st"], pb["en"] - pb["st"])))
            ha = int((float(media[pa["file"]]["dur"]) - pa["o"]) * fps_exact) - 1        # source left after the outgoing clip
            hb = int(pb["i"] * fps_exact) - 1                                          # source before the incoming clip
            h = min(Lf // 2, ha, hb)
            if h >= fr(0.15):
                TV[k] = h
                if TRN.get("audio", True):
                    TA[k] = h
        if TV:
            flags.append(f"chuyển cảnh: {len(TV)} cross dissolve (~{float(TRN['dur']):.1f}s) + fade đầu/cuối")
    # Micro-crossfades on audio cuts (Ctrl+Shift+D Constant Power): eliminate boundary pops/clicks
    if TRN.get("audio", True) and pieces:
        micro_dur = float(P.get("audio_crossfade_s", 0.08))
        micro_fr = max(2, fr(micro_dur))
        for k in range(1, len(pieces)):
            if k in TA:
                continue
            pa, pb = pieces[k - 1], pieces[k]
            if pa["en"] != pb["st"] or pa.get("gap") or pb.get("gap"):
                continue
            if (on_a1(pa) and on_a1(pb)) or (on_a2(pa) and on_a2(pb)):
                ha = int((float(media[pa["file"]]["dur"]) - pa["o"]) * fps_exact)
                hb = int(pb["i"] * fps_exact)
                h = min(micro_fr // 2, ha, hb)
                if h >= 1:
                    TA[k] = h
    FI = fr(float(TRN.get("fade_in") or 0)) if TRN.get("on", True) and pieces and not pieces[0].get("gap") else 0
    FO = fr(float(TRN.get("fade_out") or 0)) if TRN.get("on", True) and pieces and not pieces[-1].get("gap") else 0
    FI = min(FI, (pieces[0]["en"] - pieces[0]["st"]) // 2) if FI else 0
    FO = min(FO, (pieces[-1]["en"] - pieces[-1]["st"]) // 2) if FO else 0
    def pre_items(k, track_audio_of):
        """transition items that go before piece k's clip on a track (video: always; audio: when both sides are on that track)."""
        out_ = []
        if k == 0 and FI:
            out_.append(trans(0, FI, "start-black", 0, "video" if track_audio_of is None else "audio"))
        if track_audio_of is None:
            if k in TV:
                cut = pieces[k]["st"]; h = TV[k]
                out_.append(trans(cut - h, cut + h, "center", h / fps_exact * TPS, "video"))
        else:
            if k in TA and track_audio_of(pieces[k - 1]) and track_audio_of(pieces[k]):
                cut = pieces[k]["st"]; h = TA[k]
                out_.append(trans(cut - h, cut + h, "center", h / fps_exact * TPS, "audio"))
        return out_
    os.makedirs(os.path.join(out, "gaps"), exist_ok=True)
    for k, p in enumerate(pieces):
        if p.get("gap"):
            png = os.path.join(out, "gaps", f"{P['name']}_gap{k:03d}.png"); render_gap(p["text"], png, SW, SH); name = os.path.basename(png)
            V1.append(f'<clipitem id="v{k}"><name>{esc(name)}</name><enabled>TRUE</enabled><duration>{fr(3600)}</duration>{rate(TB, NT)}<start>{p["st"]}</start><end>{p["en"]}</end><in>0</in><out>{p["en"] - p["st"]}</out>{fref(name, png, True, SW, SH)}</clipitem>')
            continue
        link = f'<link><linkclipref>v{k}</linkclipref><mediatype>video</mediatype></link><link><linkclipref>a{k}</linkclipref><mediatype>audio</mediatype></link>' if p["audio"] else ""
        rz = rot_zoom(p["rot"]) if p.get("rot") else 1.0
        sc_mult = float(p.get("scale", 100.0)) / 100.0
        tf = timeremap(p["speed"]) if p.get("speed", 100) != 100 else ""
        hin, hout = TV.get(k, 0), TV.get(k + 1, 0)
        o_s, o_e = (k == 0 and FI > 0), (k == len(pieces) - 1 and FO > 0)
        V1.extend(pre_items(k, None))
        base_sc = fit(p["file"]) * rz * sc_mult; zkf = None
        zm = p.get("zoom")
        if zm and len(zm) == 2 and p.get("speed", 100) == 100 and p["en"] - p["st"] >= 4:   # template: slow push-in / pull-out
            si_ = fr(p["i"]); zkf = [(si_, base_sc * float(zm[0])), (si_ + p["en"] - p["st"], base_sc * float(zm[1]))]
            base_sc *= float(zm[0])
        V1.append(clip(f"v{k}", p["file"], p["st"], p["en"], p["i"], p["o"], motion(base_sc, p.get("rot", 0.0), zkf) + tf + link, sp=p.get("speed", 100),
                       hin=hin, hout=hout, open_s=o_s, open_e=o_e))
        aud = TRN.get("audio", True)
        if p["audio"]:
            if aud: A1.extend(pre_items(k, on_a1))
            nx_on = k + 1 < len(pieces) and on_a1(pieces[k + 1])
            # J-Cut / L-Cut audio extension under adjacent B-roll
            st_a, en_a = p["st"], p["en"]
            i_a, o_a = p["i"], p["o"]
            if p.get("l_cut") and k + 1 < len(pieces) and not on_a1(pieces[k + 1]):
                l_s = float(p["l_cut"])
                ha = float(media[p["file"]]["dur"]) - p["o"]
                actual_l = min(l_s, max(0.0, ha))
                if actual_l > 0.05:
                    en_a += fr(actual_l)
                    o_a += actual_l
            if p.get("j_cut") and k > 0 and not on_a1(pieces[k - 1]):
                j_s = float(p["j_cut"])
                hb = p["i"]
                actual_j = min(j_s, max(0.0, hb))
                if actual_j > 0.05:
                    st_a = max(0, st_a - fr(actual_j))
                    i_a = max(0.0, i_a - actual_j)
            hin_a = TA.get(k, 0) if (aud and k in TA and on_a1(pieces[k - 1])) else 0
            hout_a = TA.get(k + 1, 0) if (aud and (k + 1 in TA) and nx_on) else 0
            A1.append(clip(f"a{k}", p["file"], st_a, en_a, i_a, o_a, "<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>" + link,
                           hin=hin_a, hout=hout_a,
                           open_s=o_s and aud, open_e=o_e and aud))
        elif BA.get("on", True) and media[p["file"]].get("audio", True):
            src_tr = "<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>"
            g = gain(max(-96.0, float(BA.get("gain_db", -9))))
            hin_a2 = TA.get(k, 0) if (aud and k in TA and on_a2(pieces[k - 1])) else 0
            nx_on_a2 = k + 1 < len(pieces) and on_a2(pieces[k + 1])
            hout_a2 = TA.get(k + 1, 0) if (aud and (k + 1 in TA) and nx_on_a2) else 0
            if p.get("speed", 100) == 100:        # linked like a normal import
                lk = f'<link><linkclipref>v{k}</linkclipref><mediatype>video</mediatype></link><link><linkclipref>b{k}</linkclipref><mediatype>audio</mediatype></link>'
                V1[-1] = V1[-1].replace("</clipitem>", lk + "</clipitem>")
                if aud: A2.extend(pre_items(k, on_a2))
                A2.append(clip(f"b{k}", p["file"], p["st"], p["en"], p["i"], p["o"], src_tr + g + lk,
                               hin=hin_a2, hout=hout_a2,
                               open_s=o_s and aud, open_e=o_e and aud))
            else:                                 # slow-mo picture: natural sound at 100 % under it (not stretched)
                m_ = media[p["file"]]; L = (p["en"] - p["st"]) / fps_exact; a0 = max(0.0, min(p["i"], m_["dur"] - L))
                en = p["st"] + fr(min(L, m_["dur"] - a0))
                A2.append(clip(f"b{k}", p["file"], p["st"], en, a0, a0 + L, src_tr + g))
    if FO:
        e_ = pieces[-1]["en"]
        V1.append(trans(e_ - FO, e_, "end-black", FO / fps_exact * TPS, "video"))
        if TRN.get("audio", True):
            last = pieces[-1]
            if last.get("audio"): A1.append(trans(e_ - FO, e_, "end-black", FO / fps_exact * TPS, "audio"))
            elif A2 and A2[-1].startswith(f'<clipitem id="b{len(pieces) - 1}"'): A2.append(trans(e_ - FO, e_, "end-black", FO / fps_exact * TPS, "audio"))
    seg_start = {}
    for p in pieces: seg_start.setdefault(p["seg"], p["st"])
    # VO_TEXT_PATCH: [VO] / [TEXT] / [TITLE] cues anchored on their shot by assemble → timeline frames
    seg_end = {}
    for p in pieces: seg_end[p["seg"]] = p["en"]
    VOC, TTC = [], []
    for si_, s_ in enumerate(P["segments"]):
        for c_ in (s_.get("cues") or []):
            if si_ not in seg_start: continue
            at_ = seg_end[si_] if c_.get("after") else seg_start[si_] + fr(float(c_.get("offset") or 0))
            (VOC if c_.get("kind") == "vo" else TTC).append(dict(c_, st=at_, en=at_ + fr(float(c_.get("dur") or 2.5))))
    VOC.sort(key=lambda c: c["st"]); TTC.sort(key=lambda c: c["st"])
    vo_dirs = [os.path.join(out, "vo"), os.path.join(W, "vo"), os.path.join(src, "VO")]
    for n_, c_ in enumerate(VOC, 1):          # recorded / TTS files: <line id>.wav or VO_01.wav … in out/vo, _nexys_work/vo or <src>/VO
        hits = []
        for d_ in vo_dirs:
            for pat in (c_["line"] + ".*", f"VO_{n_:02d}.*", f"VO{n_:02d}.*", f"{n_:02d}_*.*"):
                hits += [h for h in glob.glob(os.path.join(d_, pat)) if h.lower().endswith((".wav", ".mp3", ".m4a", ".aif", ".aiff", ".flac"))]
        if hits:
            f_ = hits[0]; du_ = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f_], text=True).stdout.strip() or 1)
            c_["en"] = c_["st"] + fr(du_); c_["audio_file"] = f_
            A4.append(f'<clipitem id="vo{n_}"><name>{esc(os.path.basename(f_))}</name><enabled>TRUE</enabled><duration>{fr(du_)}</duration>{rate(TB, NT)}<start>{c_["st"]}</start><end>{c_["en"]}</end><in>0</in><out>{fr(du_)}</out>{fref("vo/" + os.path.basename(f_), f_, audio_only=True)}<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack></clipitem>')
    for a_, b_ in zip(VOC, VOC[1:]):          # VO must not talk over itself: push the next line back (keeps its length)
        if a_["en"] + fr(0.2) > b_["st"]:
            sh_ = a_["en"] + fr(0.2) - b_["st"]; b_["st"] += sh_; b_["en"] += sh_
            flags.append(f"VO {b_['line']} dời {sh_ / fps_exact:.1f}s cho khỏi chồng VO {a_['line']} — kiểm tra lại khớp hình")
    os.makedirs(os.path.join(out, "titles"), exist_ok=True)
    for a_, b_ in zip(TTC, TTC[1:]):          # one V4 track: a text ends where the next one starts
        if a_["en"] > b_["st"]: a_["en"] = max(a_["st"] + 2, b_["st"])
    TTC = [c_ for c_ in TTC if c_["en"] - c_["st"] >= 2]
    for n_, c_ in enumerate(TTC):
        png = os.path.join(out, "titles", f"{P['name']}_t{n_:02d}.png"); render_title(c_["text"], png, SW, SH, c_.get("style", "text"), c_.get("pos", "")); nm_ = os.path.basename(png)
        V4.append(f'<clipitem id="t{n_}"><name>{esc(nm_)}</name><enabled>TRUE</enabled><duration>{fr(3600)}</duration>{rate(TB, NT)}<start>{c_["st"]}</start><end>{c_["en"]}</end><in>0</in><out>{c_["en"] - c_["st"]}</out>{fref(nm_, png, True, SW, SH)}</clipitem>')
    if VOC:
        with open(os.path.join(out, P["name"] + "_VO.srt"), "w", encoding="utf-8") as fh:
            for n_, c_ in enumerate(VOC, 1):
                fh.write(f"{n_}\n{srt_ts(c_['st'] / fps_exact)} --> {srt_ts(c_['en'] / fps_exact)}\n{unicodedata.normalize('NFC', c_['text'])}\n\n")
        jsave(os.path.join(out, P["name"] + ".vo.json"), [{"n": n_, "line": c_["line"], "s": round(c_["st"] / fps_exact, 3), "e": round(c_["en"] / fps_exact, 3),
                                                           "text": c_["text"], "audio": c_.get("audio_file", "")} for n_, c_ in enumerate(VOC, 1)])
    # ---- montage audio: music (A5, ducked by keyframes), spoken inserts over B-roll (A1 / A6), continuous VO (A4 / A6)
    A5, A6 = [], []
    src_tr1 = "<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>"
    def level_kf(base_db, duck, duck_db, st_, en_, in_f):
        """Audio Levels with volume keyframes: base level, −duck_db dB inside every duck range (0.25 s ramps).
        Keyframe time = source frame (in point + offset); montage music always starts at in = 0."""
        lv = lambda db_: f"{10 ** (max(-96.0, db_) / 20):.4f}"
        keys = []
        rp = fr(0.25)
        for a_, b_ in sorted(duck or []):
            s_, e_ = fr(a_) - st_, fr(b_) - st_
            if e_ <= 0 or s_ >= en_ - st_: continue
            s_, e_ = max(rp, s_), min(en_ - st_ - rp, e_)
            if e_ - s_ < 2: continue
            keys += [(s_ - rp, base_db), (s_, base_db + duck_db), (e_, base_db + duck_db), (e_ + rp, base_db)]
        kf = "".join(f"<keyframe><when>{in_f + w_}</when><value>{lv(v_)}</value></keyframe>" for w_, v_ in keys)
        return ('<filter><effect><name>Audio Levels</name><effectid>audiolevels</effectid><effectcategory>audiolevels</effectcategory><effecttype>audiolevels</effecttype><mediatype>audio</mediatype>'
                f'<parameter><parameterid>level</parameterid><name>Level</name><valuemin>0</valuemin><valuemax>3.98109</valuemax><value>{lv(base_db)}</value>{kf}</parameter></effect></filter>')
    MU = P.get("music") or []
    if isinstance(MU, dict): MU = [MU]
    for n_, mu in enumerate(sorted(MU, key=lambda x: float(x.get("start", 0)))):
        f_ = mu.get("file") or ""
        if not os.path.exists(f_): flags.append(f"nhạc không tìm thấy: {f_}"); continue
        du_ = adur(f_)
        in_s = max(0.0, float(mu.get("in", 0))); st_ = fr(float(mu.get("start", 0)))
        en_ = min(fr(float(mu.get("end") or (float(mu.get("start", 0)) + du_ - in_s))), st_ + fr(du_ - in_s))
        if en_ - st_ < 2: continue
        in_f = fr(in_s)
        FIm = min(fr(float(mu.get("fade_in") or 0)), (en_ - st_) // 3); FOm = min(fr(float(mu.get("fade_out") or 0)), (en_ - st_) // 3)
        if FIm: A5.append(trans(st_, st_ + FIm, "start-black", 0, "audio"))
        A5.append(f'<clipitem id="mu{n_}"><name>{esc(os.path.basename(f_))}</name><enabled>TRUE</enabled><duration>{fr(du_)}</duration>{rate(TB, NT)}'
                  f'<start>{"-1" if FIm else st_}</start><end>{"-1" if FOm else en_}</end><in>{in_f}</in><out>{in_f + en_ - st_}</out>'
                  f'{fref("music/" + os.path.basename(f_), f_, audio_only=True)}{src_tr1}'
                  f'{level_kf(float(mu.get("gain_db", -4)), mu.get("duck"), float(mu.get("duck_db", -16)), st_, en_, in_f)}</clipitem>')
        if FOm: A5.append(trans(en_ - FOm, en_, "end-black", FOm / fps_exact * TPS, "audio"))
        seq_end = max(seq_end, en_)
    INS = sorted(P.get("audio_inserts") or [], key=lambda x: float(x.get("at", 0)))
    tgt_ins = A1 if (INS and not any(on_a1(p_) for p_ in pieces)) else A6
    last_e = -1
    for n_, it in enumerate(INS):
        f_ = it.get("file")
        if f_ not in media: flags.append(f"câu nói chèn: không có {f_} trong media.json"); continue
        i0_ = max(0.0, float(it["in"])); o0_ = min(float(media[f_]["dur"]), float(it["out"]))
        st_ = max(fr(float(it["at"])), last_e); en_ = st_ + fr(o0_ - i0_)
        if en_ - st_ < 2: continue
        tgt_ins.append(clip(f"ai{n_}", f_, st_, en_, i0_, o0_, src_tr1 + gain(float(it.get("gain_db", 0)))))
        last_e = en_
    VT = sorted(P.get("vo_track") or [], key=lambda x: float(x.get("at", 0)))
    tgt_vo = A4 if not A4 else A6
    if tgt_vo is A6 and A6 and VT: flags.append("VO liền mạch + câu chèn cùng track A6 — kiểm tra chồng tiếng")
    for n_, vt in enumerate(VT):
        f_ = vt.get("file") or ""
        if not os.path.exists(f_): flags.append(f"VO không tìm thấy: {f_}"); continue
        du_ = adur(f_); in_s = max(0.0, float(vt.get("in", 0))); L_ = min(du_ - in_s, float(vt.get("dur") or du_))
        st_ = fr(float(vt.get("at", 0))); en_ = st_ + fr(L_)
        tgt_vo.append(f'<clipitem id="vt{n_}"><name>{esc(os.path.basename(f_))}</name><enabled>TRUE</enabled><duration>{fr(du_)}</duration>{rate(TB, NT)}'
                      f'<start>{st_}</start><end>{en_}</end><in>{fr(in_s)}</in><out>{fr(in_s) + en_ - st_}</out>'
                      f'{fref("vo/" + os.path.basename(f_), f_, audio_only=True)}{src_tr1}{gain(float(vt.get("gain_db", 0)))}</clipitem>')
        seq_end = max(seq_end, en_)
    VO_JS = [{"s": round(c["st"] / fps_exact, 3), "e": round(c["en"] / fps_exact, 3), "text": c["text"], "line": c["line"]} for c in VOC]
    TT_JS = [{"s": round(c["st"] / fps_exact, 3), "e": round(c["en"] / fps_exact, 3), "text": c["text"], "style": c.get("style", "text"), "line": c.get("line", "")} for c in TTC]
    ov_placed = []
    for k, ov in enumerate(P.get("overlays", [])):
        st = seg_start[ov["anchor"]] + fr(ov.get("offset", 0)); en = st + fr(ov["dur"])
        ov_placed.append((k, ov, st, en))
        V2.append(clip(f"o{k}", ov["file"], st, en, ov["in"], ov["in"] + ov["dur"], motion(fit(ov["file"]))))
        if media[ov["file"]].get("audio", True) and (not ov.get("muted", True) or BA.get("on", True)):   # cutaway keeps its sound (silent when muted)
            A2.append(clip(f"oa{k}", ov["file"], st, en, ov["in"], ov["in"] + ov["dur"], "<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>" + ("" if not ov.get("muted", True) else gain(-96))))
    # subtitles (from kept words, timeline-mapped)
    cues = []
    if sub.get("on"):
        for p in pieces:
            if not p["audio"] or p.get("nosub"): continue
            if P["segments"][p["seg"]].get("subs", True) is False: continue
            ws = [w for w in W_(p["file"]) if w[0] >= p["i"] - 0.05 and w[1] <= p["o"] + 0.05]
            # phrase = whisper segment (split also on pauses > 0.5s); long phrases -> balanced chunks (no orphan words)
            phrases = []
            for w in ws:
                if phrases and w[3] == phrases[-1][-1][3] and w[0] - phrases[-1][-1][1] <= 0.38: phrases[-1].append(w)
                else: phrases.append([w])
            mc = sub.get("max_chars", 28)
            for ph in phrases:
                total = len(" ".join(x[2] for x in ph)); n = max(1, math.ceil(total / mc)); target = total / n; chunk, acc = [], 0
                for j, w in enumerate(ph):
                    chunk.append(w); acc += len(w[2]) + 1
                    if acc >= target and j < len(ph) - 1 and n > 1 and len(ph) - j - 1 >= 2:
                        cues.append([p["st"] / fps_exact + chunk[0][0] - p["i"], p["st"] / fps_exact + chunk[-1][1] - p["i"], " ".join(x[2] for x in chunk)]); chunk, acc = [], 0; n -= 1
                        target = len(" ".join(x[2] for x in ph[j + 1:])) / max(n, 1)
                if chunk: cues.append([p["st"] / fps_exact + chunk[0][0] - p["i"], p["st"] / fps_exact + chunk[-1][1] - p["i"], " ".join(x[2] for x in chunk)])
        for c1, c2 in zip(cues, cues[1:]): c1[1] = min(c1[1] + 0.25, c2[0])
        if P.get("sub_cues"):                     # montage: subtitles of the VO / spoken inserts, already timeline-timed
            cues = sorted(cues + [[float(c_[0]), float(c_[1]), str(c_[2])] for c_ in P["sub_cues"] if len(c_) >= 3])
    srt = os.path.join(out, P["name"] + ".srt")
    with open(srt, "w", encoding="utf-8") as fh:
        rep = sub.get("replace", {})
        for n, (s0, s1, tx) in enumerate(cues, 1):
            for k_, v_ in rep.items(): tx = re.sub(re.escape(k_), v_, tx, flags=re.I)
            fh.write(f"{n}\n{srt_ts(s0)} --> {srt_ts(s1)}\n{unicodedata.normalize('NFC', tx.strip(' ,'))}\n\n")
    # highlights (PNG on V3) + sfx events
    events = []; hl_times = []
    for k, h in enumerate(P.get("highlights", []) if P.get("highlights_on", True) else []):
        ps = [p for p in pieces if p["seg"] == h["seg"] and not p.get("gap")]; at = None; target = norm(h["match"]).split()
        for p in ps:
            ws = [w for w in W_(p["file"]) if w[0] >= p["i"] - 0.05 and w[1] <= p["o"] + 0.05]; toks = [norm(w[2]) for w in ws]
            for j in range(len(toks) - len(target) + 1):
                if toks[j:j + len(target)] == target: at = p["st"] + fr(ws[j][0] - p["i"]); break
            if at is not None: break
        if at is None: at = ps[0]["st"] if ps else 0; flags.append(f"highlight '{h['match']}' không tìm thấy trong seg{h['seg']} → đặt ở đầu seg")
        png = os.path.join(out, "highlights", f"{P['name']}_hl{k:02d}.png"); render_highlight(h["text"], png, SW, SH, h.get("style", "pink"))
        name = os.path.basename(png); en = at + fr(h.get("dur", 1.5))
        V3.append(f'<clipitem id="h{k}"><name>{esc(name)}</name><enabled>TRUE</enabled><duration>{fr(3600)}</duration>{rate(TB, NT)}<start>{at}</start><end>{en}</end><in>0</in><out>{en - at}</out>{fref(name, png, True, SW, SH)}</clipitem>')
        events.append((at, "highlight", h.get("sfx")))
        hl_times.append({"s": round(at / fps_exact, 3), "e": round(en / fps_exact, 3), "text": h["text"], "style": h.get("style", "pink"), "seg": h.get("seg")})
    # sidecar for the panel's tab Chữ (JIZURA kinetic text): timeline-timed cues + highlights, seconds
    try:
        jsave(os.path.join(out, P["name"] + ".text.json"),
              {"name": P["name"], "fps": fps_exact, "w": SW, "h": SH,
               "cues": [{"s": round(c0, 3), "e": round(c1, 3), "text": unicodedata.normalize("NFC", tx.strip(" ,"))} for c0, c1, tx in cues],
               "highlights": hl_times, "titles": TT_JS, "vo": VO_JS})
    except Exception as e:
        flags.append(f"không ghi được .text.json: {e}")
    if sfx.get("on"):
        lib = sfx["lib"]; last = None
        for p in pieces:
            if p["first"] and p["scene"] != last:
                events.append((max(0, p["st"] - fr(0.15)), "hook" if p["st"] == 0 else "scene", None)); last = p["scene"]
        if P.get("end_slate"): events.append((t, "end", None))
        cyc = {}
        for k, (at, kind, override) in enumerate(sorted(events)):
            pat = override or sfx.get(kind)
            if not pat: continue
            cands = sorted(glob.glob(os.path.join(lib, pat))) if any(c in pat for c in "*?") else [os.path.join(lib, pat)]
            if not cands: flags.append(f"SFX không tìm thấy: {pat}"); continue
            n = cyc.get(pat, 0); f = cands[n % len(cands)]; cyc[pat] = n + 1
            key = os.path.relpath(f, lib); ref = fref(key, f, audio_only=True); du = fr(float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f], text=True).stdout.strip() or 1))
            du = min(du, fr(sfx.get("max_len", 2.5)))
            A3.append(f'<clipitem id="s{k}"><name>{esc(os.path.basename(f))}</name><enabled>TRUE</enabled><duration>{du}</duration>{rate(TB, NT)}<start>{at}</start><end>{at + du}</end><in>0</in><out>{du}</out>{ref}<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>{gain(sfx.get("gain_db", -6))}</clipitem>')
    # template / engine-picked SFX: exact file + time + gain (P["sfx_items"]); overlapping ones go to a second lane (A7)
    A7 = []; lane_end = [0, 0]
    for k, it in enumerate(sorted(P.get("sfx_items") or [], key=lambda x: float(x.get("at", 0)))):
        f = str(it.get("file") or "")
        if not os.path.exists(f): flags.append(f"SFX không tìm thấy: {f}"); continue
        try: fdur = adur(f)
        except Exception: fdur = 0
        in_s = max(0.0, float(it.get("in", 0)))
        du = fr(min(max(0.05, fdur - in_s), float(it.get("max_len", 3.0))))
        at = fr(float(it.get("at", 0)))
        if at < 0: in_s -= at / fps_exact; du += at; at = 0
        if du < 2: continue
        ln_ = 0 if lane_end[0] <= at else 1 if lane_end[1] <= at else (0 if lane_end[0] <= lane_end[1] else 1)
        if lane_end[ln_] > at: flags.append(f"SFX {os.path.basename(f)} @{at / fps_exact:.2f}s chồng 2 lớp — bỏ"); continue
        lane_end[ln_] = at + du
        in_f = fr(in_s)
        (A3 if ln_ == 0 else A7).append(
            f'<clipitem id="sx{k}"><name>{esc(os.path.basename(f))}</name><enabled>TRUE</enabled><duration>{fr(fdur or 1)}</duration>{rate(TB, NT)}<start>{at}</start><end>{at + du}</end>'
            f'<in>{in_f}</in><out>{in_f + du}</out>{fref("sfx/" + f.replace(chr(92), "/"), f, audio_only=True)}{src_tr1}{gain(float(it.get("gain_db", -6)))}</clipitem>')
    # markers
    SN = P.get("scene_names", {}); mk = []; last = None
    for p in pieces:
        if p["first"]:
            if p["scene"] != last: mk.append((p["st"], "■ " + SN.get(p["scene"], p["scene"]))); last = p["scene"]
            if P.get("seg_markers", True): mk.append((p["st"], p["label"]))
    if P.get("end_slate"): mk.append((t, "END SLATE – logo + CTA"))
    for c_ in VOC: mk.append((c_["st"], "🎙 VO: " + c_["text"]))
    for m_ in (P.get("markers") or []):
        try: mk.append((fr(float(m_[0])), str(m_[1])))
        except Exception: pass
    for c_ in TTC: mk.append((c_["st"], "T: " + c_["text"]))
    for fl in (flags if P.get("flag_markers", True) else []):
        m_ = re.match(r"seg(\d+)", fl)
        if m_ and int(m_.group(1)) in seg_start: mk.append((seg_start[int(m_.group(1))], "⚠ " + fl))
    mks = "".join(f"<marker><name>{esc(str(n)[:120])}</name><in>{s}</in><out>-1</out></marker>" for s, n in sorted(mk, key=lambda x: x[0]))   # cut, then escape (never split an &amp;)
    tr = lambda items: f"<track>{''.join(items)}</track>"
    fmt = f'<format><samplecharacteristics><width>{SW}</width><height>{SH}</height><pixelaspectratio>square</pixelaspectratio>{rate(TB, NT)}</samplecharacteristics></format>'
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4"><sequence id="{esc(P["name"])}"><name>{esc(P["name"])}</name><duration>{seq_end}</duration>{rate(TB, NT)}'
           f'<media><video>{fmt}{tr(V1)}{tr(V2)}{tr(V3)}{tr(V4)}</video><audio>{tr(A1)}{tr(A2)}{tr(A3)}{tr(A4)}{tr(A5)}{tr(A6) if (A6 or A7) else ""}{tr(A7) if A7 else ""}</audio></media>{mks}</sequence></xmeml>')
    xp = os.path.join(out, P["name"] + ".xml"); open(xp, "w", encoding="utf-8").write(xml)
    # sidecar for the panel's Review sync: every placed V1 piece (after the silence cut) → its plan segment
    try:
        jsave(os.path.join(out, P["name"] + ".pieces.json"), pieces_sidecar(P["name"], fps_exact, pieces, ov_placed))
    except Exception as e:
        flags.append(f"không ghi được .pieces.json: {e}")
    # YouTube chapters (long-form presets): one per scene, first at 0:00, each ≥ 10 s
    chap = []
    if P.get("chapters"):
        SN_ = P.get("scene_names", {}); lastc = None
        for p in pieces:
            if p["first"] and p["scene"] != lastc:
                chap.append([p["st"] / fps_exact, SN_.get(p["scene"], p["scene"])]); lastc = p["scene"]
        kept = []
        for c in chap:                            # YouTube: every chapter ≥ 10 s
            if not kept or c[0] - kept[-1][0] >= 10: kept.append(c)
        chap = kept
        if chap:
            chap[0][0] = 0.0
            hms = lambda x: (f"{int(x // 3600)}:{int(x % 3600 // 60):02d}:{int(x % 60):02d}" if x >= 3600 else f"{int(x // 60):02d}:{int(x % 60):02d}")
            ttl = lambda x: re.sub(r"^[A-Z]+\d+\s*[—·:-]\s*", "", str(x)).strip() or str(x)
            with open(os.path.join(out, P["name"] + "_chapters.txt"), "w", encoding="utf-8") as fh:
                fh.write("".join(hms(c[0]) + " " + ttl(c[1]) + "\n" for c in chap))
            if len(chap) < 3: flags.append(f"chương YouTube: chỉ {len(chap)} chương (YouTube cần ≥ 3, mỗi chương ≥ 10s)")
    # report
    rp = os.path.join(out, P["name"] + "_report.md")
    n_mu = sum(1 for x in A5 if x.startswith("<clipitem"))
    extra_rep = (f" · nhạc {n_mu}" if n_mu else "") + (f" · câu chèn {len(INS)}" if INS else "") + (f" · chương {len(chap)}" if chap else "")
    with open(rp, "w", encoding="utf-8") as fh:
        fh.write(f"# {P['name']}\n\nDuration: {seq_end / fps_exact:.2f}s · pieces {len(pieces)} (from {len(P['segments'])} segments) · subs {len(cues)} · highlights {len(V3)} · sfx {len(A3) + len(A7)} · VO {len(VOC)} (audio {len(A4)}) · text {len(TTC)}{extra_rep}\n\n| TC | Scene | File | In–Out | Label |\n|---|---|---|---|---|\n")
        for p in pieces: fh.write(f"| {p['st'] / fps_exact:6.2f} | {p['scene']} | {p['file']} | {p['i']:.2f}–{p['o']:.2f} | {p['label'] if p['first'] else '↳ silence cut'} |\n")
        fh.write("\n## Flags\n" + ("\n".join(f"- {x}" for x in flags) or "- none") + "\n")
    # bridge command (import XML → bin → open → SRT caption track → save)
    if a.bridge or a.jsx_out:
        js = ("(function(){var R={steps:[]};try{var p=app.project;var bin=null;for(var i=0;i<p.rootItem.children.numItems;i++){if(p.rootItem.children[i].name==BIN){bin=p.rootItem.children[i];}}"
              "if(!bin){bin=p.rootItem.createBin(BIN);}var n0=p.sequences.numSequences;R.steps.push(['import_xml',p.importFiles([XML],true,bin,false)]);var seq=null;"
              "for(var j=0;j<p.sequences.numSequences;j++){if(p.sequences[j].name==SEQ){seq=p.sequences[j];}}if(!seq){R.error='sequence not found';return JSON.stringify(R);}p.openSequence(seq.sequenceID);"
              "if(SRT){p.importFiles([SRT],true,bin,false);var it=null;for(var k=0;k<bin.children.numItems;k++){if(bin.children[k].name==SRTNAME){it=bin.children[k];}}"
              "if(it){try{R.steps.push(['caption_track',seq.createCaptionTrack(it,0,Sequence.CAPTION_FORMAT_SUBTITLE)]);}catch(e){R.steps.push(['caption_track','ERR '+e]);}}}"
              "var v=[],a=[];for(var q=0;q<seq.videoTracks.numTracks;q++){v.push(seq.videoTracks[q].clips.numItems);}for(var q2=0;q2<seq.audioTracks.numTracks;q2++){a.push(seq.audioTracks[q2].clips.numItems);}"
              "R.seq={name:seq.name,end:seq.end/254016000000,v:v,a:a};R.sequences_added=p.sequences.numSequences-n0;p.save();R.saved=true;}catch(e){R.error=String(e);}return JSON.stringify(R);})();")
        js = js.replace("BIN", json.dumps(P.get("bin", "NX_" + P["name"]))).replace("XML", json.dumps(xp)).replace("SEQ", json.dumps(P["name"])).replace("SRTNAME", json.dumps(os.path.basename(srt))).replace("SRT", json.dumps(srt if cues else ""))
        cid = "nx-build-" + re.sub(r"\W", "-", P["name"]).lower()
        if a.jsx_out: open(a.jsx_out, "w", encoding="utf-8").write(js); print("jsx:", a.jsx_out)
        else: jsave(os.path.join(a.bridge, f"command-{cid}.json"), {"id": cid, "script": js}); print("bridge command:", cid)
    print(open(rp, encoding="utf-8").read())

# ---------- autoplan: quick plan (talking-head / vlog / 100% full coverage) ----------
def cmd_autoplan(a):
    src = a.src; W = work(src); media = jload(os.path.join(W, "media.json")); shake = jload(os.path.join(W, "shake.json"), {})
    digest = jload(os.path.join(W, "digest.json"), {})
    try:
        from nxcore.speech import load as _speech_load
        speech = _speech_load(src)
    except Exception:
        speech = {}
    take_all = getattr(a, "all", False)
    
    # Auto-detect target resolution: 4K UHD if dominant is 4K, else 1080p
    seq_w, seq_h, seq_fps = 3840, 2160, 29.97
    if getattr(a, "seq", ""):
        m_sq = re.match(r"^(\d+)[x×](\d+)(?:@([\d.]+))?", a.seq)
        if m_sq:
            seq_w, seq_h = int(m_sq.group(1)), int(m_sq.group(2))
            if m_sq.group(3): seq_fps = float(m_sq.group(3))
    else:
        # Determine from media
        w_counts = {}
        for m_ in media.values():
            ornt = m_.get("orient", "ngang")
            w_counts[ornt] = w_counts.get(ornt, 0) + float(m_.get("dur") or 0)
        dom = max(w_counts, key=w_counts.get) if w_counts else "ngang"
        if dom == "dọc": seq_w, seq_h = 1080, 1920
        elif dom == "vuông": seq_w, seq_h = 1080, 1080
        else: seq_w, seq_h = 3840, 2160

    # Collect and sort chronologically
    clip_list = []
    for f, m in media.items():
        if a.files and f not in a.files: continue
        ct_ts = float(m.get("created_ts") or 0.0)
        cr = m.get("created") or ""
        if not ct_ts and cr:
            try:
                from datetime import datetime
                ct_ts = datetime.fromisoformat(cr.replace("Z", "+00:00")).timestamp()
            except Exception:
                ct_ts = 0.0
        d = jload(os.path.join(W, "transcripts", stem(f) + ".json"), {"segments": []}) or {"segments": []}
        segs_ = d.get("segments") or []
        lab = speech_labels(speech, f, segs_, float(m.get("dur") or 0))
        segs_ = [s_ for k_, s_ in enumerate(segs_) if not (k_ < len(lab) and lab[k_] == "junk")]    # hallucinations never set in/out
        ws = [w for s in segs_ for w in s.get("words", []) if w[2]]
        clip_list.append(dict(file=f, m=m, created=cr, created_ts=ct_ts, words=ws, segments=segs_))

    clip_list.sort(key=lambda x: (x["created_ts"], x["created"], x["file"]))

    # Group into scene clusters
    clusters = []
    curr = []
    for c in clip_list:
        if not curr: curr.append(c)
        else:
            prev = curr[-1]
            gap_mins = (c["created_ts"] - prev["created_ts"]) / 60.0 if (c["created_ts"] and prev["created_ts"]) else 0
            if gap_mins > 45.0:
                clusters.append(curr); curr = [c]
            else: curr.append(c)
    if curr: clusters.append(curr)

    clip_digest = {dinfo["file"]: dinfo for kid, dinfo in digest.get("clips", {}).items()} if digest else {}

    segments = []
    scene_names = {}
    last_cam = None
    punch_flag = False

    for s_idx, cl in enumerate(clusters):
        sc_code = f"S{s_idx + 1:02d}"
        t_start = cl[0]["created"][:10] if cl[0]["created"] else f"Cảnh {s_idx + 1}"
        scene_names[sc_code] = f"{sc_code} — {t_start} ({len(cl)} clips)"

        for c in cl:
            f = c["file"]; m = c["m"]; dur = float(m.get("dur") or 0); fps = float(m.get("fps") or 30)
            ws = c["words"]; dinfo = clip_digest.get(f, {})
            desc = dinfo.get("desc") or "B-roll"
            cam = m.get("cam", "")
            is_dup = "(1)" in f

            if ws:
                t0 = max(0.0, ws[0][0] - 0.20)
                t1 = min(dur, ws[-1][1] + 0.30)
                txt = c["segments"][0].get("text", "")[:70]
                if is_dup: txt = f"[Bản sao (1)] {txt}"
                segments.append({"file": f, "in": round(t0, 2), "out": round(t1, 2), "scene": sc_code,
                                 "label": txt, "audio": True, "speed": 100.0, "scale": 100.0})
                last_cam = cam; punch_flag = False
            elif take_all or getattr(a, "broll", False):
                sh_info = shake.get(f, {}) or {}
                usable = sh_info.get("usable") or []
                in_pt, out_pt = 0.0, min(dur, 4.0)
                target_len = min(dur, 3.5 if dur >= 4.0 else dur)
                if usable:
                    valid_us = [r for r in usable if r[1] - r[0] >= 1.5]
                    if valid_us:
                        best_r = max(valid_us, key=lambda r: (r[1] - r[0]))
                        u_dur = best_r[1] - best_r[0]
                        use_len = min(u_dur, 4.0)
                        in_pt = best_r[0] if not is_dup else max(best_r[0], best_r[1] - use_len)
                        out_pt = min(dur, in_pt + use_len)
                    else:
                        in_pt = min(0.5, dur * 0.1); out_pt = min(dur, in_pt + target_len)
                else:
                    in_pt = min(0.5, dur * 0.1) if not is_dup else max(0.0, dur * 0.4)
                    out_pt = min(dur, in_pt + target_len)

                sp = 50.0 if (fps >= 47.0 and dur >= 2.0) else 100.0
                sc = 100.0
                if cam and cam == last_cam:
                    sc = 112.0 if not punch_flag else 100.0
                    punch_flag = not punch_flag
                else:
                    punch_flag = False

                lbl = f"[Bản sao (1)] {desc}" if is_dup else desc
                segments.append({"file": f, "in": round(in_pt, 2), "out": round(out_pt, 2), "scene": sc_code,
                                 "label": lbl, "audio": False, "speed": sp, "scale": sc})
                last_cam = cam

    # Set J/L Cut on audio transitions
    for i in range(len(segments)):
        if segments[i]["audio"]:
            if i + 1 < len(segments) and not segments[i + 1]["audio"] and segments[i + 1]["scene"] == segments[i]["scene"]:
                segments[i]["l_cut"] = 0.35
            if i > 0 and not segments[i - 1]["audio"] and segments[i - 1]["scene"] == segments[i]["scene"]:
                segments[i]["j_cut"] = 0.30

    base = os.path.basename(os.path.normpath(src)).replace(" ", "_")
    name = a.name or f"{base}_100PCT_v1"
    out_dir = (a.out or os.path.join(os.path.dirname(a.plan), base)).replace("\\", "/")

    P = {
        "name": name, "bin": "NX_" + base, "src": src.replace("\\", "/"), "out": out_dir,
        "seq": {"w": seq_w, "h": seq_h, "fps": seq_fps, "fit": "fill"},
        "silence": {"on": True, "gap": 0.35, "pad_in": 0.08, "pad_out": 0.15, "min_piece": 0.4},
        "shake": {"on": True},
        "subs": {"on": True, "max_chars": 32, "replace": {"Netflix": "Nexys", "cutting": "casting"}},
        "sfx": {"on": True, "lib": "D:/Tai nguyen lam viec (2)/Tài Nguyên làm viêcc/20. Sound FX",
                "hook": "10. IMPACT/Impact 01.wav", "scene": "03. WHOOSHES/Whoosh 0*.wav",
                "highlight": "01. BUBBLES/Bubble 0*.wav", "end": "15. NOTIFICATIONS/Notification 03.wav",
                "gain_db": -9, "max_len": 2.2},
        "broll_audio": {"on": True, "gain_db": -12.0},
        "transitions": {"on": True, "dur": 0.8, "scene": True, "fade_in": 0.8, "fade_out": 1.0, "audio": True},
        "autofix": {"slowmo": True, "rotate": True},
        "punch_in": True, "punch_in_scale": 112.0, "micro_crossfade": True,
        "highlights_on": True, "scene_names": scene_names, "end_slate": 2.5,
        "segments": segments, "overlays": [], "highlights": []
    }
    jsave(a.plan, P)
    n_distinct = len(set(s["file"] for s in segments))
    print(f"autoplan: {len(segments)} segments ({n_distinct}/{len(media)} files - {n_distinct/len(media)*100:.1f}%) -> {a.plan}")
    if getattr(a, "build", False):
        class BuildArgs:
            plan = a.plan; bridge = getattr(a, "bridge", ""); jsx_out = getattr(a, "jsx_out", "")
        cmd_build(BuildArgs())

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    for c in ("probe", "sheets"):
        _p = sp.add_parser(c); _p.add_argument("src"); _p.add_argument("--force", action="store_true"); _p.add_argument("--workers", type=int, default=8)
    t = sp.add_parser("transcribe"); t.add_argument("src"); t.add_argument("--model", default="large-v3-turbo"); t.add_argument("--lang", default="vi"); t.add_argument("--prompt", default=""); t.add_argument("--cuda", action="store_true"); t.add_argument("--force", action="store_true"); t.add_argument("--files", nargs="*"); t.add_argument("--beam", type=int, default=1); t.add_argument("--batch-size", type=int, default=16); t.add_argument("--two-pass", action="store_true"); t.add_argument("--keep-wav", dest="keep_wav", action="store_true"); t.add_argument("--preset", default="")
    s = sp.add_parser("shake"); s.add_argument("src"); s.add_argument("--files", nargs="*"); s.add_argument("--thr", type=float, default=1.2); s.add_argument("--force", action="store_true"); s.add_argument("--workers", type=int, default=0); s.add_argument("--no-cuda", dest="no_cuda", action="store_true"); s.add_argument("--preset", default="")
    y = sp.add_parser("sync"); y.add_argument("src"); y.add_argument("--master", required=True); y.add_argument("--files", nargs="+", required=True)
    b = sp.add_parser("build"); b.add_argument("plan"); b.add_argument("--bridge", default=""); b.add_argument("--jsx-out", dest="jsx_out", default=""); b.add_argument("--preset", default="")
    ap_ = sp.add_parser("autoplan"); ap_.add_argument("src"); ap_.add_argument("--plan", required=True); ap_.add_argument("--out", default=""); ap_.add_argument("--name", default=""); ap_.add_argument("--files", nargs="*"); ap_.add_argument("--broll", action="store_true"); ap_.add_argument("--all", action="store_true", help="lấy đầy đủ 100%% video đã quét"); ap_.add_argument("--seq", default=""); ap_.add_argument("--build", action="store_true"); ap_.add_argument("--bridge", default=""); ap_.add_argument("--jsx-out", dest="jsx_out", default=""); ap_.add_argument("--preset", default="")
    a = ap.parse_args(); globals()["cmd_" + a.cmd](a)
