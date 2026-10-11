"""Speech map for every clip — Silero VAD on CPU (from faster-whisper), cached in _nexys_work/vad.json.

Why: the old speech test was ONE 2.5 s volumedetect slice + "≥47 fps = B-roll". On a real vlog (Hội An, 216 clips)
that missed dialogue in 63 "B-roll" clips (60p action cams talking to camera) and sent 15 silent "A-roll" clips to
whisper (minutes of GPU each for 0 sentences). A full VAD pass is cheap: audio-only decode straight into numpy
(no WAV on disk) + Silero ≈ 23 s for 121 min of audio with 4 threads.

    python -m nxcore.vad SRC [--force] [--files a.mp4 b.mp4]

Entry: vad.json[f] = {"key", "v", "dur", "speech", "ts": [[a, b], ...]}   (seconds; ts chunks ≤ 30 s)
"""
import argparse, json, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

from . import jsonio

VAD_V = 1
SR = 16000
MIN_SPEECH_S = 1.0           # less than this → "no speech": whisper is skipped
AROLL_SPEECH_S = 3.0         # promote to a_roll when speech ≥ 3 s AND (≥ 25 % of the clip OR ≥ 15 s)
AROLL_RATIO = 0.25
AROLL_ABS_S = 15.0
VAD_OPTS = dict(threshold=0.35, min_speech_duration_ms=200, max_speech_duration_s=30, min_silence_duration_ms=160)


def work(src): return os.path.join(src, "_nexys_work")


def pcm(path, ss=None, t=None):
    """Audio track → float32 mono 16 kHz numpy (ffmpeg pipe, nothing written to disk)."""
    import numpy as np
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if ss is not None: cmd += ["-ss", f"{ss:.3f}"]
    if t is not None: cmd += ["-t", f"{t:.3f}"]
    cmd += ["-i", path, "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    return np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0


def detect(audio):
    """numpy audio → [[a, b], ...] seconds."""
    from faster_whisper.vad import get_speech_timestamps, VadOptions
    if audio is None or not len(audio): return []
    ts = get_speech_timestamps(audio, VadOptions(**VAD_OPTS))
    return [[round(x["start"] / SR, 2), round(x["end"] / SR, 2)] for x in ts]


def speech_of(ts): return round(sum(b - a for a, b in ts), 2)


def load(src):
    v = jsonio.load(os.path.join(work(src), "vad.json"), {})
    return v if isinstance(v, dict) else {}


def fresh(entry, m):
    return bool(entry) and entry.get("v") == VAD_V and entry.get("key") == (m or {}).get("key")


def scan(src, media, files=None, workers=4, force=False, progress=print):
    """Fill vad.json for every clip with audio (cached by media key). Returns the vad dict."""
    W = work(src); os.makedirs(W, exist_ok=True)
    vad = load(src)
    todo = [f for f, m in media.items()
            if m.get("audio") and m.get("status", "ok") == "ok" and (not files or f in files)
            and (force or not fresh(vad.get(f), m))]
    if not todo: return vad
    progress(json.dumps({"stage": "vad", "total": len(todo)}))

    def one(f):
        a = pcm(os.path.join(src, f))
        ts = detect(a)
        n = len(a)
        del a                                  # ex.map keeps every result alive until consumed: return the length only
        return f, n, ts

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for k, (f, n, ts) in enumerate(ex.map(one, todo), 1):
            vad[f] = {"key": media[f].get("key"), "v": VAD_V, "dur": round(n / SR, 2), "speech": speech_of(ts), "ts": ts}
            if k % 10 == 0 or k == len(todo): progress(f"@vad {k}/{len(todo)} {f}")
    jsonio.save_atomic(os.path.join(W, "vad.json"), vad, indent=None)
    return vad


def role_hint(entry, dur):
    """VAD verdict for triage → ('a_roll' | 'b_roll', reason) or None (undecided, keep the metadata rule)."""
    if not entry: return None
    sp = float(entry.get("speech") or 0); dur = float(dur or entry.get("dur") or 0)
    if sp < MIN_SPEECH_S:
        return "b_roll", f"VAD: không có giọng nói ({sp:.1f}s / {dur:.0f}s)"
    if sp >= AROLL_SPEECH_S and (sp >= AROLL_ABS_S or (dur > 0 and sp / dur >= AROLL_RATIO)):
        return "a_roll", f"VAD: có thoại {sp:.0f}s / {dur:.0f}s ({100 * sp / max(dur, 0.1):.0f}%)"
    return None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("src"); ap.add_argument("--force", action="store_true")
    ap.add_argument("--files", nargs="*"); ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    media = json.load(open(os.path.join(work(a.src), "media.json"), encoding="utf-8"))
    vad = scan(a.src, media, a.files, a.workers, a.force, progress=lambda m: print(m, flush=True))
    n = sum(1 for f in media if (vad.get(f) or {}).get("speech", 0) >= MIN_SPEECH_S)
    print(f"vad: {n}/{len(media)} clip có giọng nói", flush=True)


if __name__ == "__main__":
    sys.exit(main())
