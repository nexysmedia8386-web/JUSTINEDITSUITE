"""Music analysis for editing: tempo, beats, downbeats (bars), sections (intro/build/drop/break/outro), energy curve.

numpy + ffmpeg only. Designed for "cut to the music structure": the editor/tool assigns pacing per section,
snaps montage cuts to beats, and places hits/risers on section changes.

CLI:
  python -m nxcore.music analyze "song.mp3" [--out song.music.json]
  python -m nxcore.music index "D:/Music" --out music_index.json      # analyze a folder (cached by size+mtime)
"""
import argparse
import json
import os
import sys

import numpy as np

from .audiolib import AUDIO_EXT, HOP, SR, band_edges, db, decode, file_key, stft_mag

FPS_ENV = SR / HOP  # onset-envelope frames per second (~43.07)


# ------------------------------------------------------------------ onset envelope
def onset_envelope(S):
    """Band-wise log spectral flux: each of 12 log-spaced bands weighs equally, so kick/bass count as much as
    broadband hats (plain bin-sum flux locks onto hi-hats and puts beats between the kicks)."""
    edges = band_edges(nb=12)
    B = np.stack([S[:, a:b].mean(axis=1) for a, b in edges], axis=1)
    L = np.log1p(100.0 * B / (B.max(axis=0, keepdims=True) + 1e-9))
    flux = np.maximum(0.0, np.diff(L, axis=0))
    flux = flux / (np.percentile(flux, 99, axis=0, keepdims=True) + 1e-9)
    w = np.linspace(1.5, 0.7, flux.shape[1])            # slight low-frequency emphasis
    flux = (flux * w).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    k = np.hanning(5); k /= k.sum()
    flux = np.convolve(flux, k, "same")
    flux -= np.convolve(flux, np.ones(int(FPS_ENV)) / int(FPS_ENV), "same")   # remove slow trend
    flux = np.maximum(flux, 0)
    return flux / (flux.max() + 1e-9)


def low_onset(S):
    edges = band_edges(nb=12)
    lo = S[:, edges[0][0]: edges[3][1]].mean(axis=1)
    on = np.maximum(0, np.diff(np.log1p(100 * lo / (lo.max() + 1e-9)), prepend=0))
    return on / (on.max() + 1e-9)


# ------------------------------------------------------------------ tempo
def estimate_tempo(env, lo=60.0, hi=200.0, prior=118.0):
    e = env - env.mean()
    ac = np.correlate(e, e, "full")[len(e) - 1:]
    ac /= ac[0] + 1e-9
    lags = np.arange(len(ac))
    bpm = 60.0 * FPS_ENV / np.maximum(lags, 1)
    ok = (bpm >= lo) & (bpm <= hi)
    w = np.exp(-0.5 * (np.log2(np.maximum(bpm, 1e-3) / prior) / 0.9) ** 2)
    score = np.where(ok, ac * w, -1)
    # reinforce with harmonics (lag*2) so the true beat period beats its half
    score2 = score.copy()
    for i in np.nonzero(ok)[0]:
        if 2 * i < len(ac):
            score2[i] += 0.5 * ac[2 * i]
    best = int(np.argmax(score2))
    # parabolic refine
    if 1 <= best < len(ac) - 1:
        a, b, c = ac[best - 1], ac[best], ac[best + 1]
        off = 0.5 * (a - c) / (a - 2 * b + c + 1e-9)
        lag = best + float(np.clip(off, -0.5, 0.5))
    else:
        lag = float(best)
    return 60.0 * FPS_ENV / lag, lag


# ------------------------------------------------------------------ beat tracking (Ellis 2007 DP)
def track_beats(env, period, tightness=100.0):
    n = len(env)
    score = env.copy().astype(np.float64)
    back = -np.ones(n, np.int64)
    lo, hi = int(round(period / 2)), int(round(period * 2))
    offs = np.arange(-hi, -lo + 1)
    pen = -tightness * (np.log(-offs / period)) ** 2
    for i in range(hi, n):
        cand = score[i + offs] + pen
        j = int(np.argmax(cand))
        score[i] = env[i] + cand[j]
        back[i] = i + offs[j]
    # start from best score in the last period
    tail = max(0, n - int(period))
    i = tail + int(np.argmax(score[tail:]))
    beats = []
    while i >= 0:
        beats.append(i)
        i = back[i]
    beats = np.array(beats[::-1])
    return beats


def refine_phase(beats, env, low, period):
    """Shift the whole grid (±half a beat) to where kick/low onsets + overall onsets line up best."""
    best, best_s = 0, -1.0
    half = int(period // 2)
    comb = 0.6 * low + 0.4 * env
    for sh in range(-half, half + 1):
        b = beats + sh
        b = b[(b >= 0) & (b < len(comb))]
        sc = comb[b].mean() if len(b) else 0
        if sc > best_s:
            best, best_s = sh, sc
    return np.clip(beats + best, 0, len(env) - 1)


def trim_lead(beats, env):
    thr = 0.05
    while len(beats) > 4 and env[max(0, beats[0] - 2): beats[0] + 3].max() < thr:
        beats = beats[1:]
    return beats


# ------------------------------------------------------------------ bars / downbeats
def downbeat_phase(beats, S):
    lo_on = low_onset(S)
    best, best_s = 0, -1
    for ph in range(4):
        b = beats[ph::4]
        s = lo_on[np.clip(b, 0, len(lo_on) - 1)].mean() if len(b) else 0
        if s > best_s:
            best, best_s = ph, s
    return best


# ------------------------------------------------------------------ sections
def bar_features(S, bars, n_frames):
    edges = band_edges(nb=12)
    B = np.stack([S[:, a:b].mean(axis=1) for a, b in edges], axis=1)   # frames x 12
    rms = np.sqrt((S ** 2).mean(axis=1))
    feats, energy = [], []
    bounds = list(bars) + [n_frames]
    for i in range(len(bars)):
        a, b = bounds[i], max(bounds[i + 1], bounds[i] + 1)
        v = np.log1p(B[a:b].mean(axis=0))
        feats.append(v / (np.linalg.norm(v) + 1e-9))
        energy.append(float(db(rms[a:b].mean())))
    return np.array(feats), np.array(energy)


def novelty(F, k=4):
    n = len(F)
    if n < 2 * k + 1:
        return np.zeros(n)
    SSM = F @ F.T
    g = np.outer(np.hanning(2 * k + 2)[1:-1], np.hanning(2 * k + 2)[1:-1])
    sign = np.ones((2 * k, 2 * k)); sign[:k, k:] = -1; sign[k:, :k] = -1
    K = g * sign
    nov = np.zeros(n)
    P = np.pad(SSM, k, mode="edge")
    for i in range(n):
        nov[i] = (P[i:i + 2 * k, i:i + 2 * k] * K).sum()
    nov = np.maximum(nov, 0)
    return nov / (nov.max() + 1e-9)


def segment(F, energy, min_bars=4, group=4):
    n = len(F)
    nov = novelty(F, k=min(4, max(2, n // 8)))
    # energy step: mean of next 2 bars vs previous 2 bars, robust-normalised (a silent tail must not dominate)
    E = np.asarray(energy, float)
    step = np.zeros(n)
    for i in range(1, n):
        a, b = E[max(0, i - 2):i], E[i:i + 2]
        step[i] = abs(b.mean() - a.mean())
    ref = np.percentile(step[step > 0], 90) if (step > 0).any() else 1.0
    step = np.clip(step / (ref + 1e-9), 0, 1.5) / 1.5
    score = 0.45 * nov + 0.55 * step
    grid = np.zeros(n); grid[::group] = 0.15; grid[::2 * group] = 0.2   # phrase grid (4/8 bars) bonus
    s = score + grid
    cuts = [0]
    for i in np.argsort(-s):
        if s[i] < 0.32:
            break
        if all(abs(i - c) >= min_bars for c in cuts) and n - i >= min(min_bars, 2):
            cuts.append(int(i))
    return sorted(cuts)


def refine_boundaries(secs, beats_f, S, t):
    """Bar grids can be a beat off; move each boundary (±4 beats) to the beat with the largest energy step."""
    if len(secs) < 2 or len(beats_f) < 16:
        return secs
    rms = np.sqrt((S ** 2).mean(axis=1))
    bounds = list(beats_f) + [len(rms)]
    be = np.array([db(rms[bounds[i]:max(bounds[i + 1], bounds[i] + 1)].mean()) for i in range(len(beats_f))])
    bt = np.array([t(b) for b in beats_f])
    for k in range(1, len(secs)):
        bi = int(np.argmin(np.abs(bt - secs[k]["start"])))
        best, best_s = bi, -1
        for d in range(-4, 5):
            j = bi + d
            if j < 4 or j + 4 > len(be):
                continue
            sc = abs(be[j:j + 4].mean() - be[j - 4:j].mean())
            if sc > best_s + 0.3:             # prefer the original grid unless clearly better
                best, best_s = j, sc
        nt = float(bt[best])
        if secs[k - 1]["start"] + 1.0 < nt < secs[k]["end"] - 1.0:
            secs[k - 1]["end"] = nt; secs[k]["start"] = nt
    return secs


def label_sections(secs, total_dur):
    """Label by loudness RELATIVE to the song's body (median, duration-weighted), in dB:
    drop = clear peak (>= +2.5 dB over body and >= +3 dB over the previous section), main = the body,
    intro/outro = quieter edges, break = quiet dip in the middle, build = the section leading into a drop/main from below."""
    if not secs:
        return secs
    E = np.array([s["energy_db"] for s in secs], float)
    w = np.array([max(0.1, s["end"] - s["start"]) for s in secs])
    order = np.argsort(E)
    cw = np.cumsum(w[order]) / w.sum()
    body = float(E[order][np.searchsorted(cw, 0.5)])
    top = E.max()
    n = len(secs)
    for i, s in enumerate(secs):
        rel = s["energy_db"] - body
        s["rel_db"] = round(float(rel), 1)
        s["energy"] = float(np.clip((s["energy_db"] - (top - 15.0)) / 15.0, 0, 1))
        prev = secs[i - 1]["energy_db"] if i else -99
        if rel >= 2.5 and s["energy_db"] - prev >= 3.0:
            s["label"] = "drop"
        elif rel >= 2.5 and i and secs[i - 1].get("label") == "drop":
            s["label"] = "drop"
        elif i == 0 and rel <= -2.0:
            s["label"] = "intro"
        elif i == n - 1 and rel <= -2.0:
            s["label"] = "outro"
        elif rel <= -3.0:
            s["label"] = "break"
        else:
            s["label"] = "main"
    for i in range(n - 1):   # build: quieter section right before a drop, or rising section before the body
        nxt = secs[i + 1]
        if secs[i]["label"] in ("main", "intro") and i > 0 and nxt["label"] == "drop":
            secs[i]["label"] = "build"
        elif secs[i]["label"] == "main" and nxt["label"] == "main" and secs[i]["rising"] and secs[i]["rel_db"] <= -1.0:
            secs[i]["label"] = "build"
    # trailing quiet sections are all outro (a song often fades in 2 steps)
    for i in range(n - 1, 0, -1):
        if secs[i]["label"] == "outro" and secs[i - 1]["label"] == "break" and i - 1 > n * 0.6:
            secs[i - 1]["label"] = "outro"
        else:
            break
    return secs


def phrases(secs, downbeats, bars_per_phrase=8):
    """Split long sections into 8-bar phrases (sub-markers for pacing / phrase-change SFX)."""
    out = []
    db_ = np.array(downbeats)
    for s in secs:
        inside = db_[(db_ > s["start"] + 0.05) & (db_ < s["end"] - 0.05)]
        cuts = [s["start"]] + [float(x) for k, x in enumerate(inside, 1) if k % bars_per_phrase == 0] + [s["end"]]
        cuts = [c for i, c in enumerate(cuts) if i == 0 or c - cuts[i - 1] > 2.0]
        for k in range(len(cuts) - 1):
            out.append({"start": round(cuts[k], 3), "end": round(cuts[k + 1], 3), "label": s["label"], "part": k + 1, "parts": len(cuts) - 1})
    return out


# ------------------------------------------------------------------ main analysis
def analyze(path, max_sec=900):
    x = decode(path, max_sec=max_sec)
    dur = len(x) / SR
    if dur < 3:
        raise RuntimeError("file quá ngắn để phân tích nhạc")
    S = stft_mag(x)
    env = onset_envelope(S)
    off = (1024 - 256) / SR                                  # frame index -> onset time (centre of hop)
    t = lambda f: round(float(f) / FPS_ENV + off, 3)
    to_f = lambda s: np.clip(np.round((np.asarray(s, float) - off) * FPS_ENV).astype(np.int64), 0, len(env) - 1)
    engine = "dsp"
    nn = None
    try:                                                     # neural beats + downbeats (nxcore.ai.beats) when installed
        from .ai import beats as _nb
        nn = _nb.track(x, SR)
    except Exception:
        nn = None
    if nn:
        beats, downs = nn
        bpm = 60.0 / float(np.median(np.diff(beats)))
        beats_f = np.unique(to_f(beats))
        bars_f = np.unique(to_f(downs)) if len(downs) >= 4 else beats_f[::4]
        engine = "beat_this"
    else:
        bpm, period = estimate_tempo(env)
        low = low_onset(S)
        beats_f = trim_lead(refine_phase(track_beats(env, period), env, low, period), env)
        beats = [t(b) for b in beats_f]
        ph = downbeat_phase(beats_f, S)
        bars_f = beats_f[ph::4]
    if len(bars_f) < 4:                                   # very short/ambient: fall back to fixed 2 s blocks
        bars_f = np.arange(0, len(env), int(2 * FPS_ENV))
    F, energy = bar_features(S, bars_f, len(env))
    cuts = segment(F, energy)
    func = None
    try:                                                     # functional sections (allin1) → its boundaries, snapped to bars
        from .ai import structure as _st
        func = _st.sections(path)
    except Exception:
        func = None
    if func:
        bt = np.array([t(b) for b in bars_f])
        cuts = sorted({0} | {int(np.argmin(np.abs(bt - s["start"]))) for s in func[1:]})
        cuts = [c for i, c in enumerate(cuts) if i == 0 or c - cuts[i - 1] >= 1]
    secs = []
    bounds = cuts + [len(bars_f)]
    for i in range(len(cuts)):
        a, b = bounds[i], bounds[i + 1]
        st = t(bars_f[a]) if i else 0.0
        en = t(bars_f[b]) if b < len(bars_f) else round(dur, 3)
        e = energy[a:b]
        rising = bool(len(e) >= 2 and np.polyfit(np.arange(len(e)), e, 1)[0] > 0.4)
        secs.append({"start": st, "end": en, "bars": int(b - a), "energy_db": round(float(e.mean()), 1), "rising": rising})
    if not func:                                             # allin1 boundaries are already musically placed
        secs = refine_boundaries(secs, beats_f, S, t)
    secs = label_sections(secs, dur)
    if func:
        for s in secs:                                       # functional label of the allin1 section covering the middle
            mid = (s["start"] + s["end"]) / 2
            s["func"] = next((f["func"] for f in func if f["start"] <= mid < f["end"]), "")
    # strongest accents (for hit SFX / flash cuts)
    pk = [i for i in range(1, len(env) - 1) if env[i] >= env[i - 1] and env[i] > env[i + 1] and env[i] > 0.5]
    pk = sorted(pk, key=lambda i: -env[i])[:24]
    rms = np.sqrt((S ** 2).mean(axis=1))
    curve = [round(float(v), 1) for v in db(np.array([rms[i:i + int(FPS_ENV)].mean() for i in range(0, len(rms), int(FPS_ENV))]))]
    return {
        "file": os.path.abspath(path), "name": os.path.basename(path), "duration": round(dur, 3),
        "bpm": round(float(bpm), 1), "beat_period": round(60.0 / bpm, 4),
        "engine": engine + ("+allin1" if func else ""),
        "beats": beats, "downbeats": [t(b) for b in bars_f], "sections": secs,
        "phrases": phrases(secs, [t(b) for b in bars_f]),
        "accents": sorted(t(i) for i in pk), "energy_curve_1s": curve,
        "mood": mood_guess(bpm, secs),
    }


def engine_now():
    """Engine analyze() would use right now — a cached analysis made by a weaker engine is redone."""
    try:
        from .ai import beats as _nb
        return "beat_this" if _nb.available() else "dsp"
    except Exception:
        return "dsp"


def stale(A):
    return (A.get("engine") or "dsp").split("+")[0] != engine_now()


def mood_guess(bpm, secs):
    rels = [s.get("rel_db", 0) for s in secs if s["label"] not in ("outro",)]
    dyn = (max(rels) - min(rels)) / 10.0 if rels else 0
    tempo = "slow" if bpm < 90 else "mid" if bpm < 125 else "fast"
    shape = "dynamic" if dyn > 0.55 else "steady"
    return f"{tempo}-{shape}"


def fit_to_length(A, target, fade=2.0):
    """Choose where to end the song so it lands on a section/bar boundary close to `target` seconds."""
    cands = [s["end"] for s in A["sections"]] + A["downbeats"]
    cands = [c for c in cands if c >= min(target, A["duration"]) * 0.85]
    if not cands:
        return {"end": min(target, A["duration"]), "fade": fade}
    best = min(cands, key=lambda c: (abs(c - target) + (0 if c in [s["end"] for s in A["sections"]] else 0.75)))
    return {"end": round(min(best, A["duration"]), 3), "fade": fade}


def index_folder(root, out, force=False, progress=print):
    idx = {}
    if os.path.exists(out) and not force:
        try:
            idx = json.load(open(out, encoding="utf-8"))
        except Exception:
            idx = {}
    files = []
    for d, _, fs in os.walk(root):
        for f in fs:
            if f.lower().endswith(AUDIO_EXT):
                files.append(os.path.join(d, f))
    todo = [f for f in files if idx.get(f, {}).get("key") != file_key(f) or stale(idx[f])]
    progress(json.dumps({"stage": "music", "total": len(todo), "cached": len(files) - len(todo)}))
    for k, f in enumerate(todo, 1):
        try:
            A = analyze(f)
            A["key"] = file_key(f)
            A.pop("beats", None)          # keep the index light; full analysis is recomputed on demand
            idx[f] = A
            progress(f"[{k}/{len(todo)}] {os.path.basename(f)} · {A['bpm']} BPM · {A['mood']} · {len(A['sections'])} đoạn")
        except Exception as e:
            progress(f"[{k}/{len(todo)}] {os.path.basename(f)} lỗi: {e}")
        if k % 5 == 0 or k == len(todo):
            json.dump(idx, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    idx = {k: v for k, v in idx.items() if os.path.exists(k)}
    json.dump(idx, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    return idx


def main():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    a1 = sp.add_parser("analyze"); a1.add_argument("file"); a1.add_argument("--out"); a1.add_argument("--fit", type=float)
    a2 = sp.add_parser("index"); a2.add_argument("root"); a2.add_argument("--out", required=True); a2.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.cmd == "analyze":
        A = analyze(a.file)
        if a.fit:
            A["fit"] = fit_to_length(A, a.fit)
        out = a.out or os.path.splitext(a.file)[0] + ".music.json"
        json.dump(A, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"{A['name']}: {A['bpm']} BPM · {A['mood']} · " + " | ".join(f"{s['label']} {s['start']:.1f}-{s['end']:.1f}" for s in A["sections"]))
        print("OUT", out)
    else:
        idx = index_folder(a.root, a.out, a.force, progress=lambda m: print(m, flush=True))
        print(f"music index: {len(idx)} bài → {a.out}")
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
