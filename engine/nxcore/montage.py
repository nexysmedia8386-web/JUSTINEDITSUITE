"""Music / voice-over / shooting-time driven edits — no script needed. Justin EDIT Suite.

Modes (preset "mode"):
  beat   cut ON the music: shot length follows the song's sections (intro / build / drop / break / outro), every cut
         lands on a beat (long shots on bars), only the steadiest windows of the footage are used. Optional spoken
         moments (wedding vows / speeches) play over the montage, the music ducks under them and the first shot of each
         moment shows the speaker in sync.      presets: beat_cut, wedding_highlight, wedding_teaser, event_recap
  vo     a recorded voice-over is transcribed → sentences → each sentence gets the B-roll that matches it best
         (B-roll Picker: content match × stability), cuts on sentence starts, subtitles from the VO words.
                                                 preset: explainer_vo
  story  a long film in shooting order: meaningful speech kept (ceremony, speeches, toasts), multi-cam duplicates of
         the same moment dropped, B-roll bridges of the steadiest shots between them, optional songs under the bridges
         (cuts on their beats).                  preset: wedding_full

  python -m nxcore.montage --src SRC --preset beat_cut --music song.mp3 --plan OUT/plan_X.json --name X --out OUT
         [--seq 1920x1080@25:fill] [--target 60] [--vo voice.wav] [--music b.mp3 …] [--cfg cfg.json] [--cuda]
         [--shuffle SEED [--shuffle-rhythm]]   (beat: bản xáo shot có seed — cùng seed ra cùng bản)

Plan extras read by `nxcut.py build`:
  segment "tl": [t0, t1]        exact timeline span (s) — the build derives frames from it, so cuts stay on the beat
  "music":  [{file, start, in, end, gain_db, fade_in, fade_out, duck: [[s, e]], duck_db}]   → A5
  "audio_inserts": [{file, in, out, at, gain_db, text}]                                   → A1 (dialogue over B-roll)
  "vo_track": [{file, at, in, dur, gain_db}]                                              → A4
  "sub_cues": [[s, e, text]]    subtitles computed here (VO / vows) — written to the SRT when subs are on
  "markers":  [[t, name]]
"""
import argparse
import bisect
import hashlib
import math
import os
import random
import re
import sys

import numpy as np

from . import jsonio

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HZ = 10                      # stability profile resolution (samples / s)
LABEL_VI = {"intro": "Intro", "build": "Build", "main": "Main", "drop": "Drop", "break": "Break", "outro": "Outro",
            "verse": "Verse"}
# target shot length per music section (s) before the preset's pace factor; n beats ∈ {1, 2, 4, 8, 16} nearest to it
SHOT_S = {"intro": 3.2, "build": 2.0, "main": 1.8, "verse": 1.8, "drop": 0.95, "break": 3.0, "outro": 3.4}
SLOW_LABELS = ("intro", "break", "outro")
PRIORITY = ("drop", "build", "main", "verse", "intro", "break", "outro")

DEFAULTS = {
    "pace": 1.0, "min_shot": 0.45, "max_shot": 5.0,
    "min_stab": 0.45,            # windows below this stability score are left out (relaxed step by step when short)
    "w_stab": 0.45, "w_quality": 0.25, "w_motion": 0.10, "w_speech": 0.35, "w_chrono": 0.0,
    "w_cover": 4.0, "cover_sat": 0.5,   # B3 saturated coverage (Assigner.cover_gains): show every scene of the shoot
    # WHICH seconds of a clip: reward the window holding the clip's moment (subject action, camera handling taken out —
    # Material.moment) and a settled exposure (Material.expo, stability v8 "luma"). Tuned on 14 hand-labelled GoPro
    # shots of a real beat cut: wrong moment 6 → 2, already-right shots kept 8/8 (0.35–0.5 / 0.6 equally good).
    "w_moment": 0.4, "w_expo": 0.6,
    "auto_slowmo": True, "slowmo_speed": 50,
    "skip_roles": ["junk", "audio_master", "screen"],
    "music_gain_db": -4.0, "music_fade_out": 2.0, "duck_db": -16.0,
    "speech_moments": 0,         # beat mode: how many spoken moments (vows / speeches) ride over the montage
    "speaker_shot_s": 3.5,       # … the first this-many seconds show the speaker in sync
    "nat_sound": False, "broll_nat_gain_db": -14.0,
    "xfade_slow": False,         # beat mode: dissolve (instead of a cut) between shots of slow sections
    "vo_lead": 0.5, "vo_tail": 1.0, "vo_shot_max": 5.0, "vo_shot_min": 1.6, "vo_music_db": -20.0,
    "story_scene_gap_min": 20.0, "story_bridge_shots": 3, "story_shot_s": 3.5, "story_speech_share": 0.7,
}

# words that make a spoken line a "moment" (vows, thanks, family, promises) — normalised, no diacritics
MOMENT_WORDS = ("yeu", "thuong", "hua", "mai mai", "suot doi", "tron doi", "hanh phuc", "cam on", "bo me", "ba me",
                "vo chong", "chong", "vo", "gia dinh", "ben nhau", "cuoc doi", "trai tim", "nho", "biet on", "xin loi",
                "love", "promise", "forever", "always", "thank", "family", "together", "heart", "life", "marry",
                "dong y", "nguyen", "chuc", "con", "em", "anh")


def _j(p, d=None):
    v = jsonio.load(p, d)
    return d if v is None else v


def _strip(t):
    try:
        from .textnorm import strip_diacritics
        return strip_diacritics(str(t).lower())
    except Exception:
        return str(t).lower()


def file_hash(path):
    try:
        st = os.stat(path)
        return hashlib.md5(f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}".encode("utf-8")).hexdigest()[:12]
    except OSError:
        return hashlib.md5(path.encode("utf-8")).hexdigest()[:12]


# ====================================================================== material: every steady window of the footage
def stab_score(jit):
    """camera jitter (RMS % of diagonal after motion compensation) → 0..1. ≤ 0.12 tripod/gimbal, 0.40 = limit of
    acceptable handheld (nxcore.stability CFG), ≥ 0.8 unusable."""
    return np.interp(jit, [0.0, 0.06, 0.12, 0.40, 0.80], [1.0, 0.97, 0.9, 0.4, 0.0])


def _sub(rs, bad, margin=0.0):
    out = []
    for a, b in rs:
        cur = [[a, b]]
        for x, y in bad:
            x, y = x - margin, y + margin
            nxt = []
            for p, q in cur:
                if y <= p or x >= q:
                    nxt.append([p, q])
                    continue
                if x > p:
                    nxt.append([p, x])
                if y < q:
                    nxt.append([y, q])
            cur = nxt
        out += cur
    return out


def profile_arrays(sh, dur):
    """shake.json entry → 10 Hz (score at 100 %, score at ≤ 50 % slow motion, camera speed % diag / s)."""
    n = max(1, int(math.ceil(dur * HZ)) + 1)
    segs = sh.get("segments") or []
    if sh.get("skipped") or not segs:      # no scan (role skipped by the preset / no entry): assume a usable tripod-ish shot
        base = float(sh.get("stability", 70) or 70) / 100.0
        jit = np.full(n, 0.12 if base >= 0.9 else 0.25)
        spd = np.zeros(n)
        state = np.full(n, "h")
    else:
        jit = np.full(n, float(sh.get("jit_median") or 0.3))
        spd = np.zeros(n)
        state = np.full(n, "h")
        code = {"stable": "s", "handheld": "h", "shaky": "y", "slowmo": "m", "fast": "f", "lowconf": "l"}
        for s in segs:
            a, b = int(float(s["start"]) * HZ), int(math.ceil(float(s["end"]) * HZ))
            jit[a:b] = float(s.get("jit") or 0.3)
            spd[a:b] = float(s.get("speed") or 0)
            state[a:b] = code.get(s.get("state"), "h")
    sc = stab_score(jit)
    sc = np.where(state == "s", np.maximum(sc, 0.9), sc)
    sc = np.where(np.isin(state, ["y", "f"]), 0.0, sc)
    sc = np.where(state == "l", np.minimum(sc, 0.5), sc)
    slow = np.where(state == "m", stab_score(jit * 0.5), sc)       # steady once played at half speed
    sc = np.where(state == "m", 0.0, sc)
    return sc, slow, spd


def steadiest_start(arr, lo, hi, L, prefer=None, bad=(), gain=0.05, bonus=None):
    """Start of the steadiest L-second window in [lo, hi] on a 10 Hz score array (0.7·mean + 0.3·worst moment,
    windows touching `bad` ranges excluded). With `prefer` (a start already chosen) it only moves when the window
    is clearly steadier (≥ gain) — None when nothing fits. `bonus` (10 Hz, same length): added as its window mean —
    the moment / exposure terms (moment_curve, expo_curve), so the steadiest seconds are not simply the emptiest."""
    Ls = max(1, int(round(L * HZ)))
    i0, i1 = int(math.ceil(lo * HZ)), min(int(math.floor(hi * HZ)) - Ls, len(arr) - Ls)
    if i1 < i0:
        return None
    starts = np.arange(i0, i1 + 1)
    if len(starts) > 150:
        starts = np.unique(np.linspace(i0, i1, 150).astype(int))
    cs = np.concatenate([[0.0], np.cumsum(arr)])
    mean = (cs[starts + Ls] - cs[starts]) / Ls
    win = np.lib.stride_tricks.sliding_window_view(arr[i0:i1 + Ls], Ls)
    val = 0.7 * mean + 0.3 * win[starts - i0].min(axis=1)
    if bonus is not None and len(bonus) >= len(arr):
        cb = np.concatenate([[0.0], np.cumsum(np.asarray(bonus, float)[:len(arr)])])
        val = val + (cb[starts + Ls] - cb[starts]) / Ls
    for x, y in bad:
        val = np.where((starts / HZ < float(y)) & ((starts + Ls) / HZ > float(x)), -9.0, val)
    if prefer is not None:
        val = val - 0.03 * np.abs(starts / HZ - prefer) / max(hi - lo, 1.0)
        j0 = int(np.argmin(np.abs(starts / HZ - prefer)))
        j = int(np.argmax(val))
        return round(float(starts[j] / HZ), 2) if val[j] >= val[j0] + gain else round(float(prefer), 2)
    j = int(np.argmax(val))
    return round(float(starts[j] / HZ), 2) if val[j] > -1 else None


def moment_curve(sh, n, C):
    """10 Hz "something happens here" 0..1.2 (the moment reward of a window): the stability pass's subject-activity
    curve ("act", v6+) with the camera's own handling taken out. Picking the camera up / setting it down / grass against
    the lens moves 25-40 % of the picture at the very start or end of a clip — far more than a person running in from
    afar (5-15 %) — and would otherwise BE the clip's moment. Normalised on the cleaned curve, so the real action is the
    peak. Empty when the clip has no activity curve."""
    a = np.asarray(sh.get("act") or [], float)
    hz = float(sh.get("act_hz") or 4)
    if len(a) < 4 or n == 0:
        return np.zeros(0)
    t = np.arange(len(a)) / hz
    dur = len(a) / hz
    edge = (t < float(C.get("handling_head", 2.5))) | (t > dur - float(C.get("handling_tail", 1.5)))
    a = np.where(edge & (a > float(C.get("handling_act", 100.0))), np.minimum(a, 10.0), a)   # handling / lens clutter
    a = np.minimum(a, 160.0)                                                                # whole-frame change (light)
    x = np.interp(np.arange(n) / HZ, t, a)
    lo, hi = np.percentile(a, 30), np.percentile(a, 90)
    return np.clip((x - lo) / max(hi - lo, 6.0), 0.0, 1.2)


def expo_curve(sh, n, C):
    """10 Hz exposure quality 0..1 from the stability pass "luma" (v8). Only two things count:
    · an exposure STEP with the camera still (the camera / phone jumps ≥ 40 levels within ~1 s, e.g. a sunset clip black
      for 5 s, then right): stretches further from mid-grey than the clip's best stretch lose, and so does the ramp;
    · extremes (near black, blown out).
    A brightness that drifts slowly, or jumps while the camera moves (tilting from bright rock to a darker vista), is
    framing, not exposure: no penalty. Empty when the clip has no luma curve."""
    a = np.asarray(sh.get("luma") or [], float)
    hz = float(sh.get("act_hz") or 4)
    if len(a) < 4 or n == 0:
        return np.zeros(0)
    y = np.maximum(np.interp(np.arange(n) / HZ, np.arange(len(a)) / hz, a), 1.0)
    q = np.ones(n)
    q = np.where(y < 28.0, np.clip(y / 28.0, 0.0, 1.0) ** 1.5, q)                  # near black
    q = np.where(y > 232.0, np.clip((252.0 - y) / 20.0, 0.0, 1.0), q)              # blown out
    w = int(HZ)                                                                     # ±0.5 s → a 1 s step
    d = np.abs(y[2 * w:] - y[:-2 * w]) if n > 2 * w else np.zeros(0)
    pan = np.asarray(sh.get("pan") or [], float)
    if len(d) and pan.ndim == 2 and len(pan) >= 2:
        src_t = np.arange(len(pan)) / hz
        mv = np.hypot(np.interp(np.arange(n) / HZ, src_t, pan[:, 0]), np.interp(np.arange(n) / HZ, src_t, pan[:, 1]))
        mvw = np.lib.stride_tricks.sliding_window_view(mv, 2 * w + 1).max(axis=1)[:len(d)]
        d = np.where(mvw < float(C.get("expo_still", 8.0)), d, 0.0)                # sunset clip |pan| <= 2, tilt 40+
    else:
        d = np.zeros(0)                                                             # no camera-move data: no step rule
    if len(d) and d.max() >= float(C.get("expo_step", 40.0)):
        dist = np.abs(np.log(y / 128.0))
        best = float(np.percentile(dist, 15))                                       # the clip's best-exposed stretch
        q = q * np.clip(1.0 - (dist - best - 0.15) / 0.45, 0.0, 1.0)
        ramp = np.zeros(n)
        ramp[w:w + len(d)] = np.clip((d - 25.0) / 40.0, 0.0, 1.0)
        q = q * (1.0 - 0.8 * np.convolve(ramp, np.ones(3) / 3, "same"))
    return q


class Material:
    """The footage of a source folder as candidate windows: usable (steady) stretches of every clip, split at the
    shots of long takes (nxcore.segments), defects (QC + shake) removed, each with a 10 Hz stability profile."""

    def __init__(self, src, cfg):
        self.src, self.C = src, cfg
        W = os.path.join(src, "_nexys_work")
        self.W = W
        self.media = _j(os.path.join(W, "media.json"), {}) or {}
        self.stab = _j(os.path.join(W, "shake.json"), {}) or {}
        tags = _j(os.path.join(W, "broll_tags.json"), {}) or {}
        self.tags = {f: t for f, t in tags.items() if isinstance(t, dict) and not f.startswith("_")}
        try:
            from .segments import load as _ls
            self.segs = _ls(src)
        except Exception:
            self.segs = {}
        try:
            from .describe import load as _ad, load_segs as _sd
            self.desc, self.sdesc = _ad(src), _sd(src)
        except Exception:
            self.desc, self.sdesc = {}, {}
        try:
            from .align import merged_bad
            self.bad, self.qc = merged_bad(src, self.stab)
        except Exception:
            self.bad, self.qc = {}, {}
        try:
            from .speech import load as _sl
            self.speech_lab = _sl(src)
        except Exception:
            self.speech_lab = {}
        self._prof, self._tx, self._spk, self._vad = {}, {}, {}, None
        self.notes = []
        self.cands = self._windows()
        self._attach_vectors()

    # -------------------------------------------------------------- look-alike detection
    def _attach_vectors(self):
        """SigLIP2 picture vector per candidate (nxcore.ai.embed index) → look-alike shots from different clips
        (re-takes of one scene) are kept apart on the timeline. No index → 64-bit dhash (near match, not equality)."""
        self.has_vec = False
        try:
            from .ai.embed import load_index
            M, Z = load_index(self.src)
        except Exception:
            M, Z = None, None
        if not M:
            return
        n = len(M["units"])
        mean = np.zeros((n, Z["vec"].shape[1]), np.float32)
        np.add.at(mean, Z["unit"], Z["vec"])
        mean /= np.linalg.norm(mean, axis=1, keepdims=True) + 1e-9
        by_file = {}
        for i, (f, a, b) in enumerate(M["units"]):
            by_file.setdefault(f, []).append((float(a), float(b), i))
        try:                                  # zero-shot technical + aesthetic rank of every shot (nxcore.ai.quality)
            from .ai.quality import scores as _qs
            QS = _qs(self.src)
        except Exception:
            QS = {}
        w_ai = float(self.C.get("w_ai_quality", 0.35))
        for c in self.cands:
            us = by_file.get(c["file"])
            if not us:
                continue
            mid = (c["a"] + c["b"]) / 2
            a, b, i = min(us, key=lambda u: 0 if u[0] <= mid <= u[1] else min(abs(u[0] - mid), abs(u[1] - mid)))
            c["vec"] = mean[i]
            self.has_vec = True
            if i in QS and w_ai:              # blend into the existing quality / composition scores
                c["q"] = (1 - w_ai) * c["q"] + w_ai * QS[i]["tech"]
                c["comp"] = (1 - w_ai) * c["comp"] + w_ai * QS[i]["aes"]

    @staticmethod
    def look_alike(c, o):
        """0..1 how much two candidates show the same picture."""
        if c.get("vec") is not None and o.get("vec") is not None:
            s = float(np.dot(c["vec"], o["vec"]))
            return max(0.0, min(1.0, (s - 0.80) / 0.15))      # SigLIP cosine 0.80 → 0 … 0.95+ → 1
        h1, h2 = c.get("hash"), o.get("hash")
        if h1 and h2:
            d = bin(int(h1, 16) ^ int(h2, 16)).count("1")
            return max(0.0, min(1.0, (14 - d) / 10.0))         # dhash Hamming ≤ 4 → 1 … ≥ 14 → 0
        return 0.0

    # -------------------------------------------------------------- transcripts / speech
    def transcript(self, f):
        if f not in self._tx:
            from .media import stem
            d = _j(os.path.join(self.W, "transcripts", stem(f) + ".json"), {}) or {}
            segs = [s for s in (d.get("segments") or []) if str(s.get("text", "")).strip()]
            lab = self.speech_lab.get(f)
            if lab is None or len(lab) != len(d.get("segments") or []):
                try:
                    from .speech import classify
                    lab = [classify(s, float((self.media.get(f) or {}).get("dur") or 0))[0] for s in segs]
                except Exception:
                    lab = ["ok"] * len(segs)
            else:
                full = [s for s in (d.get("segments") or [])]
                lab = [lab[i] for i, s in enumerate(full) if str(s.get("text", "")).strip()]
            self._tx[f] = [dict(s, label=l) for s, l in zip(segs, lab)]
        return self._tx[f]

    def speech_blocks(self, f):
        """spoken stretches of a clip (any non-junk transcript line) — on-camera talking is avoided in a montage."""
        if f not in self._spk:
            rs = sorted([float(s["start"]), float(s["end"])] for s in self.transcript(f) if s.get("label") != "junk")
            if not rs:                       # not transcribed (preset skips Whisper): Silero VAD speech map
                if self._vad is None:
                    self._vad = _j(os.path.join(self.W, "vad.json"), {}) or {}
                rs = sorted([float(x[0]), float(x[1])] for x in ((self._vad.get(f) or {}).get("ts") or []) if len(x) >= 2)
            m = []
            for a, b in rs:
                if m and a - m[-1][1] < 0.8:
                    m[-1][1] = max(m[-1][1], b)
                else:
                    m.append([a, b])
            self._spk[f] = m
        return self._spk[f]

    # -------------------------------------------------------------- stability profile
    def profile(self, f):
        """10 Hz arrays: score at 100 % speed, score at slow motion (≤ 50 %), camera speed (% diag / s)."""
        p = self._prof.get(f)
        if p is None:
            p = self._prof[f] = profile_arrays(self.stab.get(f) or {}, float((self.media.get(f) or {}).get("dur") or 0))
        return p

    # -------------------------------------------------------------- candidate windows
    def _windows(self):
        C, out = self.C, []
        skip = set(C.get("skip_roles") or [])
        from .chrono import capture_ts
        from .segments import seg_key
        for f in sorted(self.media):
            m = self.media[f]
            t = self.tags.get(f, {})
            role = t.get("role") or m.get("role") or "b_roll"
            if role in skip or m.get("status") in ("broken", "skip") or t.get("ban") or t.get("use") == "skip":
                continue
            dur = float(m.get("dur") or 0)
            fps = float(m.get("fps") or 0)
            if dur < 1.0 or fps < 10:
                continue
            sh = self.stab.get(f)
            if sh and isinstance(sh.get("usable"), list):
                norm = [list(r[:2]) for r in sh.get("usable") or []]
                slow = _sub([list(r[:2]) for r in sh.get("usable_slowmo") or []], norm) if fps >= 47 else []
            else:
                norm, slow = [[0.3, max(0.3, dur - 0.3)]], []
            use = t.get("use")
            if isinstance(use, list) and len(use) == 2:
                ni = [[max(a, use[0]), min(b, use[1])] for a, b in norm if min(b, use[1]) - max(a, use[0]) >= 1.0]
                norm = ni or norm
            bad = [list(r[:2]) for r in (self.bad.get(f) or {}).get("ranges", [])]
            norm, slow = _sub(norm, bad), _sub(slow, bad)
            segs = self.segs.get(f) or []
            marks = t.get("segs_mark") or {}
            pieces = []
            for kind, rs in (("n", norm), ("m", slow)):
                for a, b in rs:
                    if segs:
                        for s in segs:
                            k = s.get("k") or seg_key(s)
                            if marks.get(k) == "ban":
                                continue
                            x, y = max(a, float(s["a"])), min(b, float(s["b"]))
                            if y - x >= 0.5:
                                pieces.append((x, y, kind, s, marks.get(k) == "pin"))
                    elif b - a >= 0.5:
                        pieces.append((a, b, kind, None, False))
            t0 = capture_ts(f, m)
            comp = (sh or {}).get("composition") or {}
            q = float((sh or {}).get("quality", 60) or 60) / 100.0
            for a, b, kind, s, pin in pieces:
                k = (s.get("k") or seg_key(s)) if s else ""
                desc = (self.sdesc.get(f) or {}).get(k) or self.desc.get(f) or t.get("desc") or ""
                out.append({"id": f"{f}@{a:.2f}-{b:.2f}", "file": f, "a": round(a, 2), "b": round(b, 2),
                            "slow_only": kind == "m", "fps": fps, "dur": dur, "role": role,
                            "t_abs": (t0 + (a + b) / 2) if t0 else None, "t0": t0,
                            "q": q if s is None or s.get("q") is None else 0.5 * q + 0.5 * float(s["q"]) / 100.0,
                            "comp": float(comp.get("score", 60) or 60) / 100.0,
                            "rot": float(comp.get("rotate_fix") or 0.0), "hash": (s or {}).get("hash"),
                            "pin": pin, "desc": desc, "cam": m.get("cam", ""),
                            "seg_cut": bool((s or {}).get("cut"))})     # piece starts on a real scene change
        return out

    # -------------------------------------------------------------- best sub-window of a candidate
    def talk_mask(self, f):
        k = ("talk", f)
        if k not in self._prof:
            n = len(self.profile(f)[0])
            m = np.zeros(n)
            for a, b in self.speech_blocks(f):
                m[int(a * HZ):int(math.ceil(b * HZ))] = 1.0
            self._prof[k] = np.concatenate([[0.0], np.cumsum(m)])
        return self._prof[k]

    def _cum(self, key, arr):
        """[0, cumsum(arr)…] cached per clip (key in the profile cache)."""
        v = self._prof.get(key)
        if v is None:
            v = self._prof[key] = np.concatenate([[0.0], np.cumsum(arr)])
        return v

    def best_sub(self, c, D, slow, used=(), want_mid=True, speech_w=0.0, gap=0.5, accents=None):
        """→ (start, stability 0..1, mean camera speed, speech overlap 0..1) for a D-second source window inside the
        candidate, avoiding `used` ranges (± 0.5 s) of the same clip; None when nothing fits."""
        a, b = c["a"], c["b"]
        if b - a < D - 1e-6:
            return None
        sc, scs, spd = self.profile(c["file"])
        arr = scs if slow else sc
        L = max(1, int(round(D * HZ)))
        i0, i1 = int(math.ceil(a * HZ)), int(math.floor(b * HZ)) - L
        i1 = min(i1, len(arr) - L)
        if i1 < i0:
            if b - a >= D - 1e-6 and i0 <= len(arr) - L:
                i1 = i0
            else:
                return None
        starts = np.arange(i0, i1 + 1)
        if len(starts) > 80:
            starts = np.unique(np.linspace(i0, i1, 80).astype(int))
        f = c["file"]
        # per-clip prefix sums are cached (they were rebuilt over the WHOLE clip on each of ~17 k calls per montage);
        # np.minimum/np.maximum instead of np.clip on these tiny arrays (np.clip's Python wrapper cost more than the
        # maths: 185 k calls) — same values, the edit is bit-identical
        cs = self._cum(("cs", f, bool(slow)), arr)
        mean = (cs[starts + L] - cs[starts]) / L
        # worst moment inside counts too: one shaky second spoils a shot that is steady on average
        sub = np.ascontiguousarray(arr[i0:i1 + L])
        win = np.lib.stride_tricks.as_strided(sub, shape=(len(sub) - L + 1, L), strides=(sub.strides[0], sub.strides[0]),
                                              writeable=False)
        mins = win[starts - i0].min(axis=1)
        val = 0.7 * mean + 0.3 * mins
        ok = np.ones(len(starts), bool)
        for x, y in used:
            ok &= ~((starts / HZ < y + gap) & ((starts + L) / HZ > x - gap))
        if not ok.any():
            return None
        if want_mid and b - a > D:
            mid = (a + b - D) / 2
            val = val - 0.04 * np.abs(starts / HZ - mid) / max((b - a - D) / 2, 0.1)
        if speech_w:                          # someone talking on camera inside the window
            tk = self.talk_mask(c["file"])
            val = val - speech_w * (tk[np.minimum(starts + L, len(tk) - 1)] - tk[starts]) / L
        ar = self.act_rel(c["file"])
        w_act = float(self.C.get("w_action", 0.35))
        w_mom = float(self.C.get("w_moment", 0.0))
        if len(ar) and (w_act or w_mom):      # don't cut out in the middle of an action (and less so cut in)
            ca = self._cum(("ca", f), ar)
            n_ar = len(ar)

            def _mean(lo, hi):
                lo, hi = np.minimum(np.maximum(lo, 0), n_ar), np.minimum(np.maximum(hi, 0), n_ar)
                return (ca[hi] - ca[lo]) / np.maximum(hi - lo, 1)
            end_a = _mean(starts + L - 3, starts + L + 2)
            beg_a = _mean(starts - 1, starts + 3)
            inside = _mean(starts, starts + L)
            # an action that peaks inside and settles by the cut reads as complete → small bonus
            k_in = float(self.C.get("w_action_in", 0.5))         # cutting IN mid-action ("in medias res") hurts less than out
            val = val - w_act * (end_a + k_in * beg_a) + 0.15 * w_act * np.minimum(np.maximum(inside - end_a, 0), 1)
            # the MOMENT: the window should hold what happens in the clip (someone runs in, climbs, raises the arms),
            # not the calm before / after it — without this the steadiest, emptiest seconds won
            mo = self.moment(f) if w_mom else np.zeros(0)
            if len(mo):
                cm = self._cum(("cm", f), mo)
                n_mo = len(mo)
                lo_, hi_ = np.minimum(starts, n_mo), np.minimum(starts + L, n_mo)
                m_in = (cm[hi_] - cm[lo_]) / np.maximum(hi_ - lo_, 1)
                val = val + w_mom * m_in
                # an action longer than the shot can't end calm: when the window IS the moment, ending mid-action is
                # forgiven in part (hold_actions later stretches the shot to the next beat when that settles it)
                k_fg = float(self.C.get("moment_forgive", 0.0))
                if k_fg:
                    val = val + w_act * k_fg * np.minimum(m_in, 1.0) * end_a
        w_exp = float(self.C.get("w_expo", 0.0))
        ex = self.expo(c["file"]) if w_exp else np.zeros(0)
        if len(ex) >= i1 + L:                 # well exposed, not mid auto-exposure ramp (a sunset clip black for 5 s)
            sub_e = np.ascontiguousarray(ex[i0:i1 + L])
            win_e = np.lib.stride_tricks.as_strided(sub_e, shape=(len(sub_e) - L + 1, L), strides=(sub_e.strides[0], sub_e.strides[0]),
                                                    writeable=False)[starts - i0]
            val = val - w_exp * (1.0 - (0.7 * win_e.mean(axis=1) + 0.3 * win_e.min(axis=1)))
        w_vb = float(self.C.get("w_visual_beat", 0.12))
        on = self.onset(c["file"]) if (accents and w_vb) else np.zeros(0)
        if len(on):
            # visual beats (A2): a movement rising ON the cut and on the beats inside the shot — accents are source-time
            # offsets from the window start [(offset s, weight)], the cut itself first
            tot = sum(w for _, w in accents)
            hit = np.zeros(len(starts))
            n_on = len(on) - 1
            for off, w in accents:
                ii = np.minimum(np.maximum(starts + int(round(off * HZ)), 0), n_on)
                hit += w * on[ii]
            val = val + w_vb * hit / max(tot, 1e-6)
        val = np.where(ok, val, -9)
        j = int(np.argmax(val))
        s0 = starts[j] / HZ
        st = float(0.7 * mean[j] + 0.3 * mins[j])
        cs2 = self._cum(("cs2", f), spd)
        v = float((cs2[starts[j] + L] - cs2[starts[j]]) / L)
        tk = self.talk_mask(c["file"])
        i_ = starts[j]
        sp = float(tk[min(i_ + L, len(tk) - 1)] - tk[i_]) / L
        return round(s0, 2), st, v, min(1.0, sp)

    def act_rel(self, f):
        """10 Hz subject activity relative to the clip itself (0 = its calm moments, 1 = its busy ones), from the
        stability pass ("act", 4 Hz). Empty array when the clip was analysed before v6 → no action rules."""
        k = ("act", f)
        if k not in self._prof:
            sh = self.stab.get(f) or {}
            a = np.asarray(sh.get("act") or [], float)
            hz = float(sh.get("act_hz") or 4)
            n = len(self.profile(f)[0])
            if len(a) < 4 or n == 0:
                self._prof[k] = np.zeros(0)
            else:
                x = np.interp(np.arange(n) / HZ, np.arange(len(a)) / hz, a)
                lo, hi = np.percentile(a, 30), np.percentile(a, 90)
                self._prof[k] = np.clip((x - lo) / max(hi - lo, 4.0), 0.0, 1.5)
        return self._prof[k]

    def moment(self, f):
        """10 Hz moment curve of a clip (moment_curve), cached."""
        k = ("moment", f)
        if k not in self._prof:
            self._prof[k] = moment_curve(self.stab.get(f) or {}, len(self.profile(f)[0]), self.C)
        return self._prof[k]

    def expo(self, f):
        """10 Hz exposure quality of a clip (expo_curve), cached."""
        k = ("expo", f)
        if k not in self._prof:
            self._prof[k] = expo_curve(self.stab.get(f) or {}, len(self.profile(f)[0]), self.C)
        return self._prof[k]

    def onset(self, f):
        """10 Hz visual onset strength 0..1 — the subject's movement suddenly rising ("visual beat", Davis &
        Agrawala). Positive part of the smoothed activity derivative, normalised by the clip's own 95th percentile."""
        k = ("onset", f)
        if k not in self._prof:
            ar = self.act_rel(f)
            if len(ar) < 5:
                self._prof[k] = np.zeros(0)
            else:
                sm = np.convolve(ar, np.ones(3) / 3, "same")
                d = np.maximum(0.0, sm[2:] - sm[:-2])               # rise over 0.2 s
                d = np.concatenate([[0.0], d, [0.0]])
                ref = float(np.percentile(d, 95)) if np.any(d > 0) else 0.0
                o = np.clip(d / ref, 0.0, 1.0) if ref > 1e-6 else np.zeros_like(d)
                # ±1 sample tolerance (±0.1 s) folded in once: a beat "hits" a rise that starts just before / after it
                self._prof[k] = np.maximum(o, np.maximum(np.roll(o, 1), np.roll(o, -1)))
        return self._prof[k]

    def pan(self, f):
        """10 Hz intended camera move (x, y) in % of the frame per second (stability v7 "pan"); empty when absent."""
        k = ("pan", f)
        if k not in self._prof:
            sh = self.stab.get(f) or {}
            a = np.asarray(sh.get("pan") or [], float)
            n = len(self.profile(f)[0])
            if a.ndim != 2 or len(a) < 2 or n == 0:
                self._prof[k] = np.zeros((0, 2))
            else:
                hz = float(sh.get("act_hz") or 4)
                t = np.arange(n) / HZ
                src_t = np.arange(len(a)) / hz
                self._prof[k] = np.stack([np.interp(t, src_t, a[:, 0]), np.interp(t, src_t, a[:, 1])], axis=1)
        return self._prof[k]

    def pan_at(self, f, a, b):
        """Mean camera move over source [a, b] → np.array([x, y]) or None."""
        p = self.pan(f)
        if not len(p):
            return None
        i0, i1 = max(0, int(a * HZ)), min(len(p), max(int(a * HZ) + 1, int(round(b * HZ))))
        return p[i0:i1].mean(axis=0) if i1 > i0 else None

    def edge_action(self, f, s0, D):
        """(activity right at the cut-out, at the cut-in) of a source window — high = cut lands mid-action."""
        ar = self.act_rel(f)
        if not len(ar):
            return 0.0, 0.0
        i0, i1 = int(round(s0 * HZ)), int(round((s0 + D) * HZ))
        e = ar[max(0, i1 - 3):min(len(ar), i1 + 2)]
        b = ar[max(0, i0 - 1):min(len(ar), i0 + 3)]
        return float(e.mean()) if len(e) else 0.0, float(b.mean()) if len(b) else 0.0

    def taste_feats(self, c, s0, D, speed=100.0):
        """Shot-taste features of a source window, each ≈ 0..1, in the order of prefs.TASTE (B1): what the editor's
        kept / unticked AI changes are compared on. One function for recording AND scoring, so both see the same."""
        f = c["file"]
        slow = float(speed) != 100.0
        sc, scs, spd = self.profile(f)
        arr = scs if slow else sc
        i0 = max(0, min(int(round(s0 * HZ)), len(arr) - 1))
        i1 = max(i0 + 1, min(int(round((s0 + D) * HZ)), len(arr)))
        st = float(0.7 * arr[i0:i1].mean() + 0.3 * arr[i0:i1].min()) if len(arr) else 0.5
        v = float(spd[i0:min(i1, len(spd))].mean()) if i0 < len(spd) else 0.0
        tk = self.talk_mask(f)
        talk = float(tk[min(i1, len(tk) - 1)] - tk[min(i0, len(tk) - 1)]) / max(1, i1 - i0) if len(tk) else 0.0
        e_end, _ = self.edge_action(f, s0, D)
        return [round(st, 3), round(0.6 * c["q"] + 0.4 * c["comp"], 3),
                round(min(1.0, math.log((max(v, 0.0) + 2.0) / 2.0) / math.log(16.0)), 3),
                round(1.0 - min(1.0, e_end), 3), 1.0 if slow else 0.0, round(min(1.0, max(0.0, talk)), 3)]

    def best_stab(self, c):
        """cheap upper bound used to rank candidates before the exact window search."""
        if "_bs" not in c:
            sc, scs, _ = self.profile(c["file"])
            arr = scs if c["slow_only"] else sc
            i0, i1 = int(c["a"] * HZ), int(c["b"] * HZ)
            c["_bs"] = float(arr[i0:max(i0 + 1, i1)].mean()) if i1 > i0 else 0.0
        return c["_bs"]


# ====================================================================== beat grid
def load_music(path, W):
    """nxcore.music analysis, cached in _nexys_work/music/<hash>.music.json."""
    from . import music as _mu
    d = os.path.join(W, "music")
    os.makedirs(d, exist_ok=True)
    cp = os.path.join(d, file_hash(path) + ".music.json")
    A = _j(cp, None)
    if not (isinstance(A, dict) and A.get("beats")) or _mu.stale(A):     # stale: made before beat_this was installed
        print(f"♪ phân tích nhạc {os.path.basename(path)}…", flush=True)
        A = _mu.analyze(path)
        jsonio.save_atomic(cp, A)
    return A


def _pick_n(period, tgt, min_shot, max_shot):
    opts = [n for n in (1, 2, 4, 8, 16) if min_shot - 1e-6 <= n * period <= max_shot + 1e-6]
    if not opts:
        opts = [n for n in (1, 2, 4, 8, 16) if n * period >= min_shot][:1] or [1]
    return min(opts, key=lambda n: abs(math.log(n * period / tgt)))


def beat_slots(A, end, C):
    """Cut points on the beat grid. Long shots start on bars (sections start on downbeats); a build accelerates
    (2n → n beats); the cut before every section is kept so the picture changes WITH the music."""
    pace = float(C.get("pace", 1.0))
    min_shot, max_shot = float(C.get("min_shot", 0.45)), float(C.get("max_shot", 5.0))
    shot_s = dict(SHOT_S, **(C.get("shot_s") or {}))
    beats = [float(b) for b in (A.get("beats") or []) if b < end - 0.05]
    period = float(A.get("beat_period") or (60.0 / float(A.get("bpm") or 120)))
    secs = [dict(s) for s in (A.get("sections") or []) if float(s["start"]) < end - 0.2]
    if not secs:
        secs = [{"start": 0.0, "end": end, "label": "main", "energy": 0.6}]
    secs[-1]["end"] = end
    if not beats:                                    # no beat found: fixed 120 BPM grid
        period = 0.5
        beats = list(np.arange(0.0, end, period))
    slots = []
    cuts = [0.0]
    lab_of = []
    # shuffle nhịp (shuffle_seed + shuffle_rhythm): mỗi bước cắt có thể giãn ×2 / dồn ½ — vẫn đúng beat, đổi đoạn nhạc vẫn cắt
    seed = int(C.get("shuffle_seed") or 0)
    rr = random.Random(seed * 7919 + 1) if (seed and C.get("shuffle_rhythm")) else None
    amt = max(0.0, min(1.0, float(C.get("shuffle_amount", 0.6))))
    # năng lượng cục bộ (energy_curve_1s) so với cả bài: cao → cắt nhanh gấp đôi, trầm → shot dài gấp đôi
    ec = [float(x) for x in (A.get("energy_curve_1s") or [])]
    e_hi = e_lo = None
    if C.get("energy_pace", True) and len(ec) >= 8:
        srt = sorted(x for x in ec[:int(end) + 1] if x > -60)
        if len(srt) >= 8:
            e_lo, e_hi = srt[int(0.25 * (len(srt) - 1))], srt[int(0.75 * (len(srt) - 1))]

    def local_e(t, L):
        i0, i1 = int(max(0.0, t)), int(max(0.0, t + max(1.0, L))) + 1
        w = ec[i0:i1] or ec[-1:]
        return sum(w) / len(w)
    for si, s in enumerate(secs):
        st, en = max(0.0, float(s["start"])), min(end, float(s["end"]))
        if en - st < 0.2:
            continue
        lab = s.get("label") or "main"
        n = _pick_n(period, shot_s.get(lab, 1.8) * pace, min_shot, max_shot)
        if st > cuts[-1] + 0.5 * min_shot:
            cuts.append(st)
            lab_of.append(None)
        k = bisect.bisect_left(beats, st - 0.12)       # first beat of the section (≈ the cut just placed)
        half = st + (en - st) / 2
        j = k
        while True:
            t_here = beats[j] if j < len(beats) else en
            step = n
            if lab == "build" and t_here < half and (2 * n) * period <= max_shot:
                step = 2 * n                          # slower first half, faster into the drop
            elif e_hi is not None and lab not in ("build", "drop"):
                e = local_e(t_here, n * period)
                if e >= e_hi and n >= 2 and (n // 2) * period >= min_shot - 1e-6:
                    step = n // 2                     # đoạn bài lên cao: cắt dày hơn
                elif e <= e_lo and lab not in SLOW_LABELS and (2 * n) * period <= max_shot + 1e-6:
                    step = 2 * n                      # đoạn trầm: để shot thở
            if rr is not None and not (lab == "build" and t_here >= half):     # giữ đà dồn vào drop
                u = rr.random()
                if u < 0.22 * amt and step >= 2 and (step // 2) * period >= min_shot - 1e-6:
                    step //= 2
                elif u > 1 - 0.22 * amt and (2 * step) * period <= max_shot + 1e-6:
                    step *= 2
            j += step
            # stop when the rest of the section would be a stub (< half a step): it joins the last shot
            if j >= len(beats) or beats[j] > en - max(0.6 * min_shot, 0.5 * step * period):
                break
            if beats[j] - cuts[-1] >= min_shot - 1e-6:
                cuts.append(beats[j])
        secs[si]["_n"] = n
    cuts.append(end)
    if len(cuts) > 2 and cuts[-1] - cuts[-2] < min_shot:
        cuts.pop(-2)
    # label every slot by the section its middle falls in
    for t0, t1 in zip(cuts, cuts[1:]):
        if t1 - t0 < 1e-3:
            continue
        mid = (t0 + t1) / 2
        s = next((x for x in secs if float(x["start"]) <= mid < float(x["end"]) + 1e-6), secs[-1])
        lab = s.get("label") or "main"
        e = float(s.get("energy", 0.6))
        inner = [round(b - t0, 3) for b in beats if t0 + 0.15 < b < t1 - 0.15]      # beats inside the shot (visual beats)
        slots.append({"t0": round(t0, 3), "t1": round(t1, 3), "label": lab, "energy": e,
                      "sec": secs.index(s), "beats": round((t1 - t0) / period, 1),
                      "slow": lab in SLOW_LABELS or e < 0.35, "inner_beats": inner})
    return slots, secs, period


# ====================================================================== shot assignment
class Assigner:
    def __init__(self, mat, C, chrono=False, log=None):
        self.mat, self.C, self.chrono = mat, C, chrono
        self.used = {}          # file → [[a, b]] source ranges already on the timeline
        self.notes = log if log is not None else []
        self.uses = {}
        ts = sorted(c["t_abs"] for c in mat.cands if c.get("t_abs"))
        self.ts = ts
        # shuffle: nhiễu có seed vào điểm chọn shot → bản dựng khác nhưng vẫn trong nhóm đoạn đạt chuẩn (ổn định, chất lượng)
        seed = int(C.get("shuffle_seed") or 0)
        self.rng = random.Random(seed) if seed else None
        self.amt = max(0.0, min(1.0, float(C.get("shuffle_amount", 0.6))))
        self.avoid = {}                       # bản trước: t0 (0.1 s) → file → tránh lặp lại đúng chỗ
        ap = C.get("shuffle_avoid_plan")
        if seed and ap and os.path.exists(ap):
            for sg in (_j(ap, {}) or {}).get("segments") or []:
                if sg.get("tl") and sg.get("file") and not sg.get("gap"):
                    self.avoid[round(float(sg["tl"][0]), 1)] = sg["file"]
        self.sigma = max(600.0, (ts[-1] - ts[0]) / 12.0) if len(ts) > 1 else 600.0
        self.placed = {}                      # slot index → candidate on the timeline (whole-timeline variety)
        self.extra = 0                        # extra uses per clip granted each time the pool runs dry
        self.w_reuse = float(C.get("w_reuse", 0.3))
        self.gap_s = float(C.get("reuse_gap_s", 4.0))   # a 2nd shot from one clip starts ≥ this far from the 1st
        # B1: the editor's learned shot taste (prefs.fit_taste, from kept / unticked AI changes) — residual weights on
        # Material.taste_feats added to every candidate's score; None until something has been learned
        self.taste_w = None
        if C.get("learn_taste", True):
            try:
                from . import prefs
                tw = prefs.shot_taste(C.get("id") or "")
                if tw:
                    self.taste_w = [float(tw.get(k, 0.0)) for k, _ in prefs.TASTE]
            except Exception:
                self.taste_w = None

    def max_uses(self, f):
        base = self.__dict__.setdefault("_mu", {}).get(f)
        if base is None:                         # per file once (was recomputed ~600 k times per montage)
            dur = float((self.mat.media.get(f) or {}).get("dur") or 0)
            base = self._mu[f] = int(max(1, min(int(self.C.get("max_uses_per_clip", 4)), round(dur / float(self.C.get("seconds_per_use", 30.0))))))
        return base + self.extra                 # pool exhausted → every clip gets ONE more use (not unlimited)

    def _static(self):
        """Per-candidate arrays that never change during an Assigner's life (rough ranking in pick, vectorised)."""
        S = self.__dict__.get("_st")
        if S is not None and S["n"] == len(self.mat.cands):
            return S
        cs = self.mat.cands
        if getattr(self, "_CA", None) is None or self._CA["file"].shape[0] != len(cs):
            self._CA = self._arrays(cs)
            self._cidx = {id(c): i for i, c in enumerate(cs)}
        codes = self._CA["file"]
        files = [None] * (int(codes.max()) + 1 if len(codes) else 0)
        for c, fc in zip(cs, codes):
            files[fc] = c["file"]
        S = self._st = {
            "n": len(cs), "code": codes, "files": files,
            "bs": np.array([self.mat.best_stab(c) for c in cs], float),
            "qc": np.array([0.6 * c["q"] + 0.4 * c["comp"] for c in cs], float),
            "slow_only": np.array([bool(c["slow_only"]) for c in cs]),
            "hfr": np.array([c["fps"] >= 47 for c in cs]),
            "len": np.array([c["b"] - c["a"] for c in cs], float),
            "pin": np.array([bool(c.get("pin")) for c in cs]),
            "t_abs": [i for i, c in enumerate(cs) if c.get("t_abs")],
        }
        return S

    def _spread_vec(self, k):
        if k is None or not self.placed:
            return None
        if getattr(self, "_sp_key", None) != (k, self._placed_ver):
            self._sp_arr = self._spread_all(k)
            self._sp_key = (k, self._placed_ver)
        return self._sp_arr

    # ---------------------------------------------------------------- variety penalty, vectorised
    # spread_penalty was called ~1 M times per edit in pure Python (every candidate × every placed shot, every slot):
    # 7.5 s of a 10 s review. Now one pass per pick computes it for ALL candidates with numpy (matrix product for
    # SigLIP similarity, bitwise_count for dhash) over the placed shots that can still matter (|Δslot| ≤ REP_SPAN).
    REP_SPAN = 25                             # exp(-(25-1)/6) ≈ 0.02 — farther shots no longer change the score

    def _code(self, table, v):
        return table.setdefault(v, len(table))

    def _arrays(self, cs):
        fc = self.__dict__.setdefault("_fcode", {})
        cc = self.__dict__.setdefault("_ccode", {})
        dim = next((len(c["vec"]) for c in self.mat.cands if c.get("vec") is not None), 1)
        n = len(cs)
        A = {"file": np.array([self._code(fc, c["file"]) for c in cs], np.int64),
             "cam": np.array([self._code(cc, c.get("cam") or "") for c in cs], np.int64),
             "t": np.array([float(c["t_abs"]) if c.get("t_abs") else np.nan for c in cs], np.float64),
             "vec": np.zeros((n, dim), np.float32), "hv": np.zeros(n, bool),
             "hash": np.zeros(n, np.uint64), "hh": np.zeros(n, bool)}
        for i, c in enumerate(cs):
            if c.get("vec") is not None and len(c["vec"]) == dim:
                A["vec"][i] = c["vec"]; A["hv"][i] = True
            if c.get("hash"):
                A["hash"][i] = np.uint64(int(c["hash"], 16)); A["hh"][i] = True
        return A

    def _spread_all(self, k):
        """→ penalty for every candidate of mat.cands at slot k (array)."""
        if getattr(self, "_CA", None) is None or self._CA["file"].shape[0] != len(self.mat.cands):
            self._CA = self._arrays(self.mat.cands)
            self._cidx = {id(c): i for i, c in enumerate(self.mat.cands)}
        CA = self._CA
        near = [(j, o) for j, o in self.placed.items() if j != k and abs(j - k) <= self.REP_SPAN]
        N = CA["file"].shape[0]
        if not near:
            return np.zeros(N)
        PA = self._arrays([o for _, o in near])
        dk = np.abs(np.array([j for j, _ in near], np.float64) - k)
        fall = np.exp(-(dk - 1) / float(self.C.get("rep_window", 6.0)))
        w_file, w_look, w_take = (float(self.C.get(x, d)) for x, d in (("w_rep_file", 0.9), ("w_rep_look", 0.8), ("w_rep_take", 0.35)))
        same = CA["file"][:, None] == PA["file"][None, :]
        la = np.zeros((N, len(near)), np.float32)
        vb = CA["hv"][:, None] & PA["hv"][None, :]
        if vb.any():
            la = np.where(vb, np.clip((CA["vec"] @ PA["vec"].T - 0.80) / 0.15, 0.0, 1.0), 0.0)
        hb = ~vb & CA["hh"][:, None] & PA["hh"][None, :]
        if hb.any():
            d = np.bitwise_count(CA["hash"][:, None] ^ PA["hash"][None, :]).astype(np.float32)
            la = np.where(hb, np.clip((14.0 - d) / 10.0, 0.0, 1.0), la)
        with np.errstate(invalid="ignore"):
            take = (~same) & (la <= 0) & (CA["cam"][:, None] == PA["cam"][None, :]) & \
                   (np.abs(CA["t"][:, None] - PA["t"][None, :]) < 60)
        pen = np.where(same, w_file, w_look * la + w_take * take) * fall[None, :]
        return np.minimum(pen.sum(axis=1), 2.0)

    def spread_penalty(self, c, k):
        """Variety over the WHOLE timeline (not only the 2 neighbours): the same clip, a look-alike shot (SigLIP / dhash)
        or the same take (same camera, shot < 60 s apart) placed near slot k costs score, fading with the distance.
        Candidates of mat.cands are served from one vectorised pass per (slot, placed shots); others (synthetic
        windows from nxcore.aiedit) take the reference loop below."""
        if k is None or not self.placed:
            return 0.0
        if getattr(self, "_sp_key", None) != (k, self._placed_ver):
            self._sp_arr = self._spread_all(k)
            self._sp_key = (k, self._placed_ver)
        i = self._cidx.get(id(c))
        if i is not None:
            return float(self._sp_arr[i])
        return self._spread_one(c, k)

    @property
    def _placed_ver(self):
        return (len(self.placed), self.__dict__.get("_pv", 0))

    # ---------------------------------------------------------------- B3: coverage (facility location)
    COV_LO = 0.6                              # SigLIP cosine below this = "does not stand for that footage at all"

    def cover_gains(self, cs, k=None):
        """B3 — how much footage each candidate of `cs` would newly REPRESENT: the facility-location marginal gain of
        Gong et al. 2014, Σ_i w_i·max(0, cov(i, c) − cur_i) / Σ_i w_i over every candidate window i (w = its seconds,
        cov = SigLIP cosine mapped COV_LO → 0, 1 → 1, cur_i = how well the shots placed so far — slot k excluded —
        already stand for i). Spread only stops repeats; this pulls in scenes nobody has shown yet (montage measured
        LESS representative than a random pick: it piles shots onto the steadiest scenes). → array, 0 without vectors.
        Saturated (Lin & Bilmes 2011): coverage counts only up to `cover_sat` — footage a placed shot already stands
        for gives nothing more, so with scarce footage (every scene shown early) the term switches itself off instead
        of pulling "typical" look-alike shots together (unsaturated: look-alike pairs 1 → 4–7 on 40 clips)."""
        out = np.zeros(len(cs), np.float32)
        sat = float(self.C.get("cover_sat", 0.5))
        if getattr(self, "_CA", None) is None or self._CA["file"].shape[0] != len(self.mat.cands):
            self._CA = self._arrays(self.mat.cands)
            self._cidx = {id(c): i for i, c in enumerate(self.mat.cands)}
        CA = self._CA
        if not CA["hv"].any():
            return out
        lo = self.COV_LO
        if self.__dict__.get("_covM") is None:
            # coverage of every window by every window, once (1 013 windows → 4 MB); rows are then looked up — the
            # first version re-multiplied N × placed × 768 per pick (6 s of an 8 s montage)
            V = CA["vec"][CA["hv"]]
            row = np.full(len(self.mat.cands), -1, np.int64)
            row[CA["hv"]] = np.arange(len(V))
            M_ = np.clip((V @ V.T - lo) / (1 - lo), 0, sat).astype(np.float32) if len(V) <= 6000 else None
            w = np.array([c["b"] - c["a"] for c, h in zip(self.mat.cands, CA["hv"]) if h], np.float32)
            self._covM = (V, w, row, M_)
        V, w, row, M_ = self._covM

        def strength(c):                              # coverage row of one candidate (synthetic ones: computed)
            i = self._cidx.get(id(c))
            r = row[i] if i is not None else -1
            if r >= 0 and M_ is not None:
                return M_[r]
            v = c.get("vec")
            if v is None or len(v) != V.shape[1]:
                return None
            return np.clip((V @ np.asarray(v, np.float32) - lo) / (1 - lo), 0, sat)

        key = (self._placed_ver, k)
        if self.__dict__.get("_cov_key") != key:
            rows = [s for s in (strength(o) for j, o in self.placed.items() if j != k) if s is not None]
            self._cov_cur, self._cov_key = (np.max(np.stack(rows), axis=0) if rows else np.zeros(len(V), np.float32)), key
        cur = self._cov_cur
        Wt = max(float(w.sum()), 1e-6)
        for n, c in enumerate(cs):
            s = strength(c)
            if s is not None:
                out[n] = float(np.maximum(s - cur, 0) @ w) / Wt
        return out

    def _spread_one(self, c, k):
        pen = 0.0
        w_file, w_look, w_take = (float(self.C.get(x, d)) for x, d in (("w_rep_file", 0.9), ("w_rep_look", 0.8), ("w_rep_take", 0.35)))
        for j, o in self.placed.items():
            dk = abs(j - k)
            if dk == 0:
                continue
            fall = math.exp(-(dk - 1) / float(self.C.get("rep_window", 6.0)))
            if o["file"] == c["file"]:
                pen += w_file * fall
                continue
            la = Material.look_alike(c, o)
            if la > 0:
                pen += w_look * la * fall
            elif c.get("t_abs") and o.get("t_abs") and c.get("cam") == o.get("cam") and abs(c["t_abs"] - o["t_abs"]) < 60:
                pen += w_take * fall
        return min(pen, 2.0)

    def expected(self, x):
        """timeline fraction 0..1 → expected shooting time (quantile of the material's capture times)."""
        if not self.ts:
            return None
        i = min(len(self.ts) - 1, max(0, int(round(x * (len(self.ts) - 1)))))
        return self.ts[i]

    def pick(self, D_tl, slot, neigh, x=None, min_stab=None, want_motion=None, speech_w=None, prefer=None, only=None, k=None):
        """Best (candidate, start, speed, stability) for a D_tl-second shot. neigh: [(distance, cand)] already
        placed nearby on the timeline (variety rules)."""
        C, mat = self.C, self.mat
        min_stab = float(C.get("min_stab", 0.45)) if min_stab is None else min_stab
        speech_w = float(C.get("w_speech", 0.35)) if speech_w is None else speech_w
        slow_ok = bool(C.get("auto_slowmo", True)) and slot.get("slow")
        exp = self.expected(x) if (self.chrono and x is not None) else None
        e = float(slot.get("energy", 0.6))
        v_pref = 3.0 + 14.0 * e if want_motion is None else want_motion
        # ---- rough ranking of every candidate, vectorised (the per-candidate Python loop was ~3 s of a 7 s montage);
        # same arithmetic in the same order, stable sort, same rng draws → the very same edit as before
        cands = mat.cands
        S = self._static()
        code = S["code"]
        uses_f = np.array([self.uses.get(f, 0) for f in S["files"]], float)
        mu_f = np.array([self.max_uses(f) for f in S["files"]], float)
        uses_c = uses_f[code]
        ok = uses_c < mu_f[code]
        if only:
            ok &= np.fromiter((bool(only(c)) for c in cands), bool, len(cands))
        slow_v = S["slow_only"] | (bool(slow_ok) & S["hfr"])
        if not slow_ok:
            ok &= ~S["slow_only"]
        sp_v = np.where(slow_v, float(C.get("slowmo_speed", 50)), 100.0)
        D_v = D_tl * sp_v / 100.0
        ok &= ~(S["len"] < D_v)
        ok &= ~(S["bs"] < min_stab - 0.15)
        spread = self._spread_vec(k)
        r_v = C["w_stab"] * S["bs"] + C["w_quality"] * S["qc"] - self.w_reuse * uses_c
        if spread is not None:
            r_v = r_v - spread
        if exp is not None:
            for i in S["t_abs"]:
                r_v[i] += C["w_chrono"] * math.exp(-0.5 * ((cands[i]["t_abs"] - exp) / self.sigma) ** 2)
        if prefer:
            r_v = r_v + 0.3 * np.fromiter((bool(prefer(c)) for c in cands), bool, len(cands))
        r_v = r_v + 0.15 * S["pin"]
        idx = np.nonzero(ok)[0]
        r_sel = r_v[idx]
        if self.rng is not None:
            r_sel = r_sel + np.array([self.rng.gauss(0.0, 0.14 * self.amt) for _ in range(len(idx))])
        ranked = idx[np.argsort(-r_sel, kind="stable")]
        best = None
        # the 48 best-ranked candidates first (as always); when every one of them is used up at this length — late
        # slots (Break, Outro) after the Main / Drop slots took the favourite clips — the next 48, and so on, instead
        # of leaving the slot empty ("THIẾU HÌNH") while lower-ranked clips still have unused footage
        for c0 in range(0, len(ranked), 48):
            if best is not None or (c0 and not C.get("pick_more", True)):
                break
            best = self._pick_chunk(ranked[c0:c0 + 48], r_v, slow_v, sp_v, D_v, slot, neigh, k, exp, v_pref, speech_w, min_stab, prefer)
        return best

    def _pick_chunk(self, top, r_v, slow_v, sp_v, D_v, slot, neigh, k, exp, v_pref, speech_w, min_stab, prefer):
        C, mat, cands = self.C, self.mat, self.mat.cands
        rough = [(float(r_v[i]), cands[i], bool(slow_v[i]), float(sp_v[i]), float(D_v[i])) for i in top]
        best = None
        w_cov = float(C.get("w_cover", 0.0))
        cg = self.cover_gains([z[1] for z in rough[:48]], k) if w_cov else None
        for n_, (r, c, slow, sp, D) in enumerate(rough[:48]):
            # the cut (weight 1) + the beats inside the shot (0.5 each), in SOURCE seconds (slow-mo stretches them)
            acc = [(0.0, 1.0)] + [(float(b) * sp / 100.0, 0.5) for b in (slot.get("inner_beats") or [])]
            got = mat.best_sub(c, D, slow, self.used.get(c["file"], ()), speech_w=speech_w, gap=self.gap_s,
                               want_mid=self.rng is None or self.rng.random() < 1 - 0.6 * self.amt, accents=acc)
            if not got:
                continue
            s0, st, v, spk = got
            if st < min_stab:
                continue
            sc = (C["w_stab"] * st + C["w_quality"] * (0.6 * c["q"] + 0.4 * c["comp"])
                  + C["w_motion"] * math.exp(-abs(math.log((v + 2.0) / (v_pref + 2.0))))
                  - speech_w * spk - self.w_reuse * self.uses.get(c["file"], 0) - self.spread_penalty(c, k))
            w_pan = float(C.get("w_pan", 0.15))
            if w_pan and k is not None and self.__dict__.get("win"):
                sc += w_pan * self.pan_continuity(k, c["file"], s0, D, sp)
            if self.taste_w is not None:
                sc += sum(w * x for w, x in zip(self.taste_w, mat.taste_feats(c, s0, D, sp)))
            if cg is not None:
                sc += w_cov * float(cg[n_])
            if exp is not None and c.get("t_abs"):
                sc += C["w_chrono"] * math.exp(-0.5 * ((c["t_abs"] - exp) / self.sigma) ** 2)
            if prefer and prefer(c):
                sc += 0.3
            if c.get("pin"):
                sc += 0.15
            for dist, o in neigh:
                if o is None:
                    continue
                if o["file"] == c["file"]:
                    sc -= 0.6 if dist == 1 else 0.3
                elif c.get("hash") and o.get("hash") == c["hash"]:
                    sc -= 0.4
                elif c.get("t_abs") and o.get("t_abs") and abs(c["t_abs"] - o["t_abs"]) < 20 and c.get("cam") == o.get("cam"):
                    sc -= 0.2 if dist == 1 else 0.1
                if self.chrono and dist == 1 and c.get("t_abs") and o.get("t_abs") and o.get("_side") == "L" \
                        and c["t_abs"] < o["t_abs"] - 300:
                    sc -= 0.25                  # jumps back in the day right after a later shot
            if self.rng is not None:
                sc += self.rng.gauss(0.0, 0.09 * self.amt)
                if self.avoid.get(round(float(slot.get("t0", -1)), 1)) == c["file"]:
                    sc -= 0.35 * self.amt + 0.1          # bản shuffle trước đặt đúng clip này ở đây
            if best is None or sc > best[0]:
                best = (sc, c, s0, sp, st, D)
        return best

    def pan_continuity(self, k, f, s0, D, speed=100.0):
        """A3 — screen-direction continuity with the shots on both sides of slot k: two neighbouring shots panning in
        OPPOSITE directions read as a jolt (penalty). Penalty only by default: a bonus for the SAME direction pulled
        re-takes of one scene (filmed with the same move) next to each other — look-alike pairs 3 → 6 on 40 clips,
        1 without it. Uses the move at the end of the left shot / start of the right shot. → value in [-2, 2·bonus]."""
        mat = self.mat
        t = 0.0
        sp = speed / 100.0
        bonus = float(self.C.get("pan_bonus", 0.0))
        # the neighbours' moves don't depend on the candidate: once per (slot, placed shots) instead of per candidate
        key = (k, self._placed_ver)
        if self.__dict__.get("_pan_key") != key:
            W_ = self.__dict__.get("win", {})
            left, right = W_.get(k - 1), W_.get(k + 1)
            vl = vr = None
            if left:
                v = mat.pan_at(left[0], left[1] + left[2] - 0.5, left[1] + left[2])
                vl = None if v is None else v * left[3]
            if right:
                v = mat.pan_at(right[0], right[1], right[1] + 0.5)
                vr = None if v is None else v * right[3]
            self._pan_nb, self._pan_key = (bool(left), vl, bool(right), vr), key
        has_l, vl, has_r, vr = self._pan_nb
        if not has_l and not has_r:
            return 0.0
        vs = mat.pan_at(f, s0, s0 + 0.5) if has_l else None
        ve = mat.pan_at(f, s0 + D - 0.5, s0 + D) if has_r else None

        def cont(a, b):
            if a is None or b is None:
                return 0.0
            ma, mb = float(np.hypot(*a)), float(np.hypot(*b))
            if min(ma, mb) < 3.0:                       # one of them is (almost) still: no direction to match
                return 0.0
            cos = float(np.dot(a, b)) / (ma * mb)
            strength = min(1.0, min(ma, mb) / 12.0)
            return strength * (cos if cos < 0 else bonus * cos)
        if has_l and vs is not None:
            t += cont(vl, vs * sp)
        if has_r and ve is not None:
            t += cont(ve * sp, vr)
        return t

    def take(self, c, s0, D, k=None, speed=100.0):
        if k is not None:
            self.placed[k] = c
            self.__dict__.setdefault("win", {})[k] = (c["file"], s0, D, speed / 100.0)
            self._pv = self.__dict__.get("_pv", 0) + 1       # invalidates the cached spread penalties
        self.used.setdefault(c["file"], []).append([s0, s0 + D])
        self.uses[c["file"]] = self.uses.get(c["file"], 0) + 1

    def release(self, k, c, s0, D):
        """Undo take(): slot k is empty again (its source range and its use are given back)."""
        if self.placed.get(k) is c:
            del self.placed[k]
            self.__dict__.get("win", {}).pop(k, None)
            self._pv = self.__dict__.get("_pv", 0) + 1
        rs = self.used.get(c["file"], [])
        for i, r in enumerate(rs):
            if abs(r[0] - s0) < 1e-6 and abs(r[1] - (s0 + D)) < 1e-6:
                del rs[i]
                break
        self.uses[c["file"]] = max(0, self.uses.get(c["file"], 1) - 1)


def hold_limit(mat, c):
    """How far (source seconds) a shot taken from candidate `c` may be held to finish an action.

    `c["b"]` is where this candidate ends — but long takes are split into ~8 s pieces by nxcore.segments, so the
    action often continues in the NEXT piece of the same file. mat.cands holds every piece: same "file", contiguous
    when next["a"] ≈ c["b"]; next["seg_cut"] is True when that piece starts on a real scene change (camera turned to
    something else) and False when the split was only for length. Pieces of the SAME clip are consecutive in
    mat.cands (sorted by file, then time)."""
    by_file = getattr(mat, "_pieces", None)
    if by_file is None:                       # file → its pieces sorted by start (built once per Material)
        by_file = {}
        for x in mat.cands:
            by_file.setdefault(x["file"], []).append(x)
        for v in by_file.values():
            v.sort(key=lambda x: x["a"])
        mat._pieces = by_file
    end = c["b"]
    for nxt in by_file.get(c["file"], ()):
        if nxt["a"] <= c["a"] + 1e-6:
            continue                          # this piece or an earlier one
        if abs(nxt["a"] - end) > 0.15:        # a gap = a shaky / blurred / black stretch was cut out → never hold over it
            if nxt["a"] > end:
                break
            continue
        if nxt.get("seg_cut") or nxt.get("slow_only") != c.get("slow_only"):
            break                             # real scene change, or only usable at another speed
        end = nxt["b"]                        # length-only split of one continuous take → keep going
    return end


def hold_actions(slots, picked, mat, asg, C, period):
    """A shot whose cut-out lands in the middle of an action (subject still moving, nxcore.stability "act") is held
    over the NEXT slot when that settles the action: the cut moves to the next slot's beat (still on the grid, never
    across a music-section change, never past hold_max). The swallowed slot's shot is released. → number of holds."""
    thr = float(C.get("hold_thr", 0.6))
    cap = float(C.get("hold_max", float(C.get("max_shot", 5.0)) + 1.5))
    held, k = 0, 0
    while k < len(slots) - 1:
        p, q = picked[k], picked[k + 1]
        a, b = slots[k], slots[k + 1]
        if not p or a.get("fixed") or b.get("fixed") or a["sec"] != b["sec"]:
            k += 1
            continue
        c = p["c"]
        sp = float(p.get("speed") or 100.0)
        end_a, _ = mat.edge_action(c["file"], p["in"], p["src_len"])
        newD = b["t1"] - a["t0"]
        new_src = newD * sp / 100.0
        others = [r for r in asg.used.get(c["file"], []) if abs(r[0] - p["in"]) > 1e-3]
        fits = p["in"] + new_src <= hold_limit(mat, c) + 1e-6 and \
            not any(r[0] < p["in"] + new_src and r[1] > p["in"] for r in others)
        if end_a < thr or newD > cap or not fits:
            k += 1
            continue
        end_b, _ = mat.edge_action(c["file"], p["in"], new_src)
        if end_b > end_a - 0.25:              # still mid-action one slot later: holding doesn't help
            k += 1
            continue
        for r in asg.used.get(c["file"], []):  # extend this shot's source range
            if abs(r[0] - p["in"]) < 1e-3:
                r[1] = p["in"] + new_src
        if q:                                  # release the swallowed shot
            f2 = q["c"]["file"]
            asg.used[f2] = [r for r in asg.used.get(f2, []) if abs(r[0] - q["in"]) > 1e-3]
            asg.uses[f2] = max(0, asg.uses.get(f2, 1) - 1)
        p["src_len"] = new_src
        slots[k] = dict(a, t1=b["t1"], beats=round(newD / period, 1), held=True)
        del slots[k + 1]
        del picked[k + 1]
        held += 1
    return held


def fill_slots(slots, mat, C, chrono, notes, order=None, prefer=None):
    """Assign a shot to every slot (most important slots first, variety checked against both neighbours)."""
    asg = Assigner(mat, C, chrono, notes)
    N = len(slots)
    total = slots[-1]["t1"] if slots else 1.0
    if order is None:
        rank = {lab: i for i, lab in enumerate(PRIORITY)}
        rk = asg.rng.random if asg.rng is not None else (lambda: 0.0)
        order = list(range(min(2, N))) + sorted(range(2, N), key=lambda k: (rank.get(slots[k]["label"], 9), rk(), slots[k]["t0"]))
    picked = [None] * N
    relaxed = 0
    for k in order:
        sl = slots[k]
        if sl.get("fixed"):
            continue
        D = sl["t1"] - sl["t0"]
        neigh = []
        for d in (1, 2):
            for j, side in ((k - d, "L"), (k + d, "R")):
                if 0 <= j < N and picked[j]:
                    neigh.append((d, dict(picked[j]["c"], _side=side)))
        ms = float(C.get("min_stab", 0.45))
        best = None
        for step in range(4):
            best = asg.pick(D, sl, neigh, x=(sl["t0"] + sl["t1"]) / 2 / total, min_stab=ms - 0.15 * step, prefer=prefer, k=k)
            if best:
                relaxed = max(relaxed, step)
                break
        if not best and sl.get("slow") and C.get("pick_more", True):   # no steady slow-mo left: this slot plays at normal speed
            for step in range(4):
                best = asg.pick(D, dict(sl, slow=False), neigh, x=(sl["t0"] + sl["t1"]) / 2 / total, min_stab=ms - 0.15 * step,
                                prefer=prefer, k=k)
                if best:
                    notes.append(f"{sl['t0']:.1f}s: hết đoạn slow-mo đủ ổn định → shot này chạy tốc độ thường")
                    break
        for _ in range(3):                   # pool exhausted: ONE more use per clip at a time (spread rules still apply)
            if best:
                break
            asg.extra += 1
            best = asg.pick(D, sl, neigh, x=(sl["t0"] + sl["t1"]) / 2 / total, min_stab=0.0, prefer=prefer, k=k)
            if best:
                notes.append(f"{sl['t0']:.1f}s: hết đoạn ổn định chưa dùng → cho mỗi clip thêm 1 lần dùng (lần {asg.extra})")
        if not best:
            notes.append(f"{sl['t0']:.1f}–{sl['t1']:.1f}s: không còn đoạn nào đủ dài ({D:.1f}s) → để trống (card)")
            continue
        sc, c, s0, sp, st, Dsrc = best
        asg.take(c, s0, Dsrc, k, sp)
        picked[k] = {"c": c, "in": s0, "speed": sp, "stab": st, "score": sc, "src_len": Dsrc}
    if relaxed:
        notes.append(f"Không đủ đoạn ổn định ≥ {C.get('min_stab')} → hạ ngưỡng {0.15 * relaxed:.2f} cho một số shot")
    n_ch = refine_assignment(slots, picked, asg, C, total, prefer)
    if n_ch:
        notes.append(f"Tối ưu toàn cục: chọn lại {n_ch} shot khi đã thấy cả hai phía (bản dựng tổng thể tốt hơn)")
    return picked, asg


def split_gaps(slots, picked, asg, C, notes):
    """A slot still empty after every relaxation (each free stretch of footage is shorter than it — Break / Outro
    slots of 3-4 s come last, when the long stretches are taken) → two shots cut at its middle inner beat: still on
    the music's grid, and two 1.6 s windows exist where one 3.2 s window no longer does. A half that stays empty is
    split again; a slot that can't be split any more gets one more use per clip (fill_slots' last resort) before it
    is left empty. In place on slots / picked (they stay parallel)."""
    if not C.get("pick_more", True):
        return 0
    total = slots[-1]["t1"] if slots else 1.0
    ms0 = float(C.get("min_stab", 0.45))

    def fill(j):
        h = slots[j]
        for ms in (ms0, ms0 - 0.15, ms0 - 0.3, 0.0):
            best = asg.pick(h["t1"] - h["t0"], h, _neigh(slots, picked, j), x=(h["t0"] + h["t1"]) / 2 / total, min_stab=ms, k=j)
            if best:
                sc, c, s0, sp, st, Dsrc = best
                asg.take(c, s0, Dsrc, j, sp)
                picked[j] = {"c": c, "in": s0, "speed": sp, "stab": st, "score": sc, "src_len": Dsrc}
                return True
        return False

    k = n_split = 0
    while k < len(slots):
        sl = slots[k]
        if picked[k] is not None or sl.get("fixed"):
            k += 1
            continue
        inner = sl.get("inner_beats") or []
        half = (sl["t1"] - sl["t0"]) / 2
        b = min(inner, key=lambda x: abs(x - half)) if inner else None
        tm = round(sl["t0"] + b, 3) if b is not None else None
        if tm is not None and tm - sl["t0"] >= 0.5 and sl["t1"] - tm >= 0.5:
            A = dict(sl, t1=tm, inner_beats=[x for x in inner if x < b - 0.15])
            B = dict(sl, t0=tm, inner_beats=[round(x - b, 3) for x in inner if x > b + 0.15])
            slots[k:k + 1] = [A, B]
            picked[k:k + 1] = [None, None]
            fill(k); fill(k + 1)
            n_split += 1
            stale = f"{sl['t0']:.1f}–{sl['t1']:.1f}s: không còn đoạn nào đủ dài"      # fill_slots' "left empty" note
            notes[:] = [x for x in notes if not x.startswith(stale)]
            notes.append(f"{sl['t0']:.1f}–{sl['t1']:.1f}s: không còn đoạn liền {sl['t1'] - sl['t0']:.1f}s → tách đôi tại beat {tm:.2f}s")
            continue                       # revisit k: a half still empty is split again (halves shrink → ends)
        for _ in range(3):                 # can't split: ONE more use per clip at a time, as fill_slots does
            asg.extra += 1
            if fill(k):
                notes.append(f"{sl['t0']:.1f}s: hết đoạn chưa dùng → cho mỗi clip thêm 1 lần dùng (lần {asg.extra})")
                break
        if picked[k] is None and k > 0 and picked[k - 1] and not slots[k - 1].get("fixed"):
            # last resort: the shot before simply plays on (its clip continues past the cut, unused) — the natural
            # long last shot of an outro whose music rings out after the final beat
            p, prev = picked[k - 1], slots[k - 1]
            c, sp = p["c"], float(p["speed"])
            new_len = round((sl["t1"] - prev["t0"]) * sp / 100.0, 3)
            end = p["in"] + new_len
            mine = (p["in"], p["in"] + p["src_len"])
            others = [r for r in asg.used.get(c["file"], []) if not (abs(r[0] - mine[0]) < 1e-6 and abs(r[1] - mine[1]) < 1e-6)]
            if end <= c["b"] + 1e-6 and not any(r[0] < end + 0.3 and mine[0] < r[1] + 0.3 for r in others):
                asg.release(k - 1, c, p["in"], p["src_len"])
                asg.take(c, p["in"], new_len, k - 1, sp)
                p["src_len"] = new_len
                gap0 = round(sl["t0"] - prev["t0"], 3)
                slots[k - 1] = dict(prev, t1=sl["t1"], inner_beats=(prev.get("inner_beats") or []) + [gap0] +
                                    [round(gap0 + x, 3) for x in (sl.get("inner_beats") or [])])
                del slots[k]
                del picked[k]
                notes.append(f"{sl['t0']:.1f}–{sl['t1']:.1f}s: hết hình → giữ shot trước thêm {sl['t1'] - sl['t0']:.1f}s")
                continue
        if picked[k] is None:
            notes.append(f"{sl['t0']:.1f}–{sl['t1']:.1f}s: không còn đoạn nào đủ dài ({sl['t1'] - sl['t0']:.1f}s) → để trống (card)")
        k += 1
    return n_split


def _neigh(slots, picked, k):
    N = len(slots)
    return [(d, dict(picked[j]["c"], _side=side)) for d in (1, 2) for j, side in ((k - d, "L"), (k + d, "R"))
            if 0 <= j < N and picked[j]]


def _rescore(asg, slots, picked, k, total, prefer=None, only=None, min_stab=0.0):
    sl = slots[k]
    return asg.pick(sl["t1"] - sl["t0"], sl, _neigh(slots, picked, k), x=(sl["t0"] + sl["t1"]) / 2 / total,
                    min_stab=min_stab, prefer=prefer, only=only, k=k)


def refine_assignment(slots, picked, asg, C, total, prefer=None):
    """Global improvement after the greedy fill (A1 in docs/LO_TRINH_THUAT_TOAN.md).

    The greedy pass fills the most important slots first, each one seeing only the shots chosen BEFORE it: early
    drops take the best footage blind to what the later slots will need, and the variety rules only ever look one
    way. Here every slot is re-decided once all others are placed (iterated conditional modes): release it, score
    its current shot (best window of the same candidate) and the best alternative with BOTH neighbours visible,
    keep the better. Each accepted move raises the total score, so the loop converges; it stops when a pass
    changes nothing or after refine_passes passes. → number of slots that changed."""
    passes = int(C.get("refine_passes", 3))
    if passes <= 0:
        return 0
    margin = float(C.get("refine_margin", 0.02))       # ignore ties / shuffle noise
    ms = float(C.get("min_stab", 0.45))
    changed_any = set()
    for _ in range(passes):
        changed = 0
        for k in range(len(slots)):
            cur = picked[k]
            if not cur or slots[k].get("fixed"):
                continue
            asg.release(k, cur["c"], cur["in"], cur["src_len"])
            stay = _rescore(asg, slots, picked, k, total, prefer, only=lambda c, cc=cur["c"]: c is cc)
            alt = _rescore(asg, slots, picked, k, total, prefer, min_stab=min(ms, float(cur.get("stab") or ms)))
            best = stay
            if alt and alt[1] is not cur["c"] and (not stay or alt[0] > stay[0] + margin):
                best = alt
                changed += 1
                changed_any.add(k)
            if best is None:                               # current window no longer valid on its own: keep it as it was
                asg.take(cur["c"], cur["in"], cur["src_len"], k, cur.get("speed") or 100.0)
                continue
            sc, c, s0, sp, st, Dsrc = best
            asg.take(c, s0, Dsrc, k, sp)
            picked[k] = {"c": c, "in": s0, "speed": sp, "stab": st, "score": sc, "src_len": Dsrc}
        if not changed:
            break
    return len(changed_any)


def visual_beat_hits(slots, picked, mat, thr=0.5):
    """Share of shots whose subject movement rises ON a musical accent (the cut or a beat inside the shot)."""
    n = hit = 0
    for sl, p in zip(slots, picked):
        if not p:
            continue
        on = mat.onset(p["c"]["file"])
        if not len(on):
            continue
        n += 1
        sp = float(p.get("speed") or 100) / 100.0
        offs = [0.0] + [float(b) * sp for b in (sl.get("inner_beats") or [])]
        i0 = int(round(p["in"] * HZ))
        if any(on[min(len(on) - 1, max(0, i0 + int(round(o * HZ))))] >= thr for o in offs):
            hit += 1
    return f"{hit}/{n}" if n else "-"


def opposite_pans(picked, mat, thr=3.0):
    """Neighbouring shots that pan in opposite directions (both moving ≥ thr % of the frame / s, cos < -0.5)."""
    n = 0
    for a, b in zip(picked, picked[1:]):
        if not a or not b:
            continue
        va = mat.pan_at(a["c"]["file"], a["in"] + a["src_len"] - 0.5, a["in"] + a["src_len"])
        vb = mat.pan_at(b["c"]["file"], b["in"], b["in"] + 0.5)
        if va is None or vb is None:
            continue
        va, vb = va * float(a.get("speed") or 100) / 100, vb * float(b.get("speed") or 100) / 100
        ma, mb = float(np.hypot(*va)), float(np.hypot(*vb))
        if min(ma, mb) >= thr and float(np.dot(va, vb)) / (ma * mb) < -0.5:
            n += 1
    return n


def assignment_score(slots, picked, asg, total):
    """Total score of an assignment, every slot scored with BOTH neighbours visible (the objective A1 maximises)."""
    s = 0.0
    for k, cur in enumerate(picked):
        if not cur or slots[k].get("fixed"):
            continue
        asg.release(k, cur["c"], cur["in"], cur["src_len"])
        r = _rescore(asg, slots, picked, k, total, only=lambda c, cc=cur["c"]: c is cc)
        asg.take(cur["c"], cur["in"], cur["src_len"], k, cur.get("speed") or 100.0)
        s += r[0] if r else 0.0
    return s


# ====================================================================== spoken moments (vows / speeches)
def utterances(mat, max_len=12.0, gap=0.7):
    """meaningful speech lines of the whole folder, merged per clip into utterances ≤ max_len s."""
    out = []
    for f in sorted(mat.media):
        m = mat.media[f]
        if not m.get("audio", True):
            continue
        cur = None
        for s in mat.transcript(f):
            if s.get("label") != "ok":
                if cur:
                    out.append(cur)
                cur = None
                continue
            a, b = float(s["start"]), float(s["end"])
            if cur and a - cur["b"] <= gap and b - cur["a"] <= max_len:
                cur["b"] = b
                cur["text"] += " " + s["text"].strip()
                cur["lp"].append(float(s.get("lp") or -0.5))
                cur["words"] += [w for w in s.get("words") or [] if len(w) >= 3]
            else:
                if cur:
                    out.append(cur)
                cur = {"file": f, "a": a, "b": b, "text": s["text"].strip(), "lp": [float(s.get("lp") or -0.5)],
                       "words": [w for w in s.get("words") or [] if len(w) >= 3]}
        if cur:
            out.append(cur)
    from .chrono import capture_ts
    for u in out:
        t0 = capture_ts(u["file"], mat.media.get(u["file"]))
        u["t_abs"] = (t0 + u["a"]) if t0 else None
    return out


def moment_score(u, mat):
    L = u["b"] - u["a"]
    n = _strip(u["text"])
    words = len(n.split())
    hits = sum(1 for w in MOMENT_WORDS if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", n))
    conf = math.exp(max(-2.0, sum(u["lp"]) / max(1, len(u["lp"]))))
    q = mat.qc.get(u["file"]) or {}
    lufs = q.get("lufs")
    audio = 1.0 if lufs is None else float(np.interp(lufs, [-45, -32, -24, -14, -8], [0.0, 0.5, 1.0, 1.0, 0.6]))
    if q.get("true_peak") is not None and q["true_peak"] > -0.5:
        audio *= 0.7
    length = float(np.interp(L, [1.5, 3.0, 5.0, 9.0, 13.0], [0.0, 0.6, 1.0, 1.0, 0.5]))
    dens = min(1.0, words / max(L * 2.2, 1.0))
    return round(0.9 * min(1.0, hits / 3.0) + 0.6 * conf + 0.6 * audio + 0.5 * length + 0.2 * dens, 3)


DIRECTION = ("nhin may", "nhin qua", "nhin day", "nhin len", "nhin nhau", "nhin vo", "nhin xuong", "chu oi", "co oi", "bac oi",
             "anh oi", "chi oi", "em oi", "dung roi", "ok", "chup", "may chup", "nho may", "dung sat", "sat vo", "xich", "xiu",
             "1 2 3", "2 3", "hai ba", "mot hai", "cuoi len", "quay qua", "dung day", "giu lai", "tu tu", "chuan bi",
             "quay nguoi", "dung chat", "dung xit", "nhu the nay", "nam tay", "dua tay", "tay ben", "lui lai", "buoc len")
# từ làm nên một "khoảnh khắc" thật (lời thề, cảm ơn, chúc) — bỏ các đại từ chung chung của MOMENT_WORDS
STRONG_WORDS = ("yeu", "thuong", "hua", "mai mai", "suot doi", "tron doi", "hanh phuc", "cam on", "bo me", "ba me",
                "vo chong", "gia dinh", "ben nhau", "cuoc doi", "trai tim", "biet on", "chuc", "ngay vui", "tinh yeu",
                "love", "promise", "forever", "thank", "family", "together", "heart", "marry")


def is_direction(text):
    """lời chỉ đạo / gọi nhau / đếm số — không phải khoảnh khắc (ASR wedding đầy loại này)."""
    n = " " + re.sub(r"[^a-z0-9 ]", " ", _strip(text)) + " "
    n = re.sub(r"\s+", " ", n)
    hits = sum(n.count(" " + w + " ") for w in DIRECTION)
    ws = n.split()
    words = max(1, len(ws))
    grams = [" ".join(ws[i:i + 3]) for i in range(len(ws) - 2)]
    looped = len(grams) >= 4 and len(set(grams)) <= 0.6 * len(grams)          # ASR lặp câu
    return hits >= 2 or hits / words > 0.12 or looped or bool(re.search(r"\b(\w+ oi)\b.*\b\1\b", n))


def is_moment(text):
    n = " " + re.sub(r"[^a-z0-9 ]", " ", _strip(text)) + " "
    n = re.sub(r"\s+", " ", n)
    return any(" " + w + " " in n for w in STRONG_WORDS)


def pick_moments(mat, k, chrono=True):
    if k <= 0:
        return []
    us = [u for u in utterances(mat) if 2.5 <= u["b"] - u["a"] <= 13.0 and len(_strip(u["text"]).split()) >= 5
          and not is_direction(u["text"]) and is_moment(u["text"])]
    for u in us:
        u["score"] = moment_score(u, mat)
    us.sort(key=lambda u: -u["score"])
    out = []
    for u in us:
        if any(o["file"] == u["file"] and abs(o["a"] - u["a"]) < 30 for o in out):
            continue
        if u.get("t_abs") and any(o.get("t_abs") and abs(o["t_abs"] - u["t_abs"]) < 45 for o in out):
            continue                          # the same moment filmed by a second camera
        out.append(u)
        if len(out) >= k:
            break
    if chrono:
        out.sort(key=lambda u: (u.get("t_abs") or 0, u["file"], u["a"]))
    return out


def sub_chunks(words, offset, max_chars=32):
    """words [[s, e, w, …]] → subtitle cues [[s, e, text]] (timeline = offset + word time)."""
    cues, cur = [], []
    for w in words:
        if cur and (len(" ".join(x[2] for x in cur + [w])) > max_chars or w[0] - cur[-1][1] > 0.6):
            cues.append([offset + cur[0][0], offset + cur[-1][1], " ".join(x[2].strip() for x in cur)])
            cur = []
        cur.append(w)
    if cur:
        cues.append([offset + cur[0][0], offset + cur[-1][1], " ".join(x[2].strip() for x in cur)])
    for c1, c2 in zip(cues, cues[1:]):
        c1[1] = min(c1[1] + 0.25, c2[0])
    return [[round(a, 3), round(b, 3), t] for a, b, t in cues]


# ====================================================================== plan helpers
def default_seq(media, C):
    """preset frame, else the footage's: dominant orientation (by duration) at 1080p; 50p/25p → 25, else 29.97."""
    if C.get("seq"):
        return dict(C["seq"])
    w = {"h": 0.0, "v": 0.0, "s": 0.0}
    fp = {}
    for m in media.values():
        a_ = float(m.get("w") or 1) / float(m.get("h") or 1)
        d = float(m.get("dur") or 1)
        w["h" if a_ > 1.15 else "v" if a_ < 0.87 else "s"] += d
        r = float(m.get("fps") or 0)
        k = 25 if (47 <= r < 55 or 24.5 <= r < 26) else 23.976 if 23 < r < 24.5 else 29.97
        fp[k] = fp.get(k, 0.0) + d
    o = max(w, key=w.get) if any(w.values()) else "h"
    W_, H_ = {"h": (1920, 1080), "v": (1080, 1920), "s": (1080, 1080)}[o]
    return {"w": W_, "h": H_, "fps": max(fp, key=fp.get) if fp else 25, "fit": "fill"}


def base_plan(src, name, out, seq, C, mode):
    srcn = src.replace("\\", "/").rstrip("/")
    folder = os.path.basename(srcn).replace(" ", "_")
    nat = bool(C.get("nat_sound", False))
    return {
        "name": name or f"{folder}_{mode.upper()}_v1", "bin": folder, "src": srcn,
        "out": (out or srcn + "/_nexys_out").replace("\\", "/"), "mode": mode, "preset": C.get("id", ""),
        "seq": seq or C.get("seq") or {"w": 1920, "h": 1080, "fps": 25, "fit": "fill"},   # (main passes default_seq)
        "silence": {"on": False}, "shake": {"on": False},
        "subs": dict(C.get("subs") or {"on": False}),
        "sfx": {"on": False}, "highlights_on": False, "punch_in": False,
        "broll_audio": {"on": nat, "gain_db": float(C.get("broll_nat_gain_db", -14.0))} if nat else {"on": False},
        "transitions": dict({"on": True, "dur": 0.6, "scene": False, "broll_rate": 0, "fade_in": 0.5, "fade_out": 1.5,
                             "audio": True}, **(C.get("transitions") or {})),
        "autofix": {"slowmo": True, "rotate": True}, "end_slate": 0,
        "scene_names": {}, "segments": [], "overlays": [], "highlights": [],
        "music": [], "audio_inserts": [], "vo_track": [], "sub_cues": [], "markers": [],
        "missing": [], "broll_requests": [], "vo_lines": [], "unused_speech": [], "report_notes": [],
    }


def seg_from_pick(p, t0, t1, scene, label, flags=()):
    c = p["c"]
    d = {"file": c["file"], "in": p["in"], "out": round(p["in"] + (t1 - t0) * p["speed"] / 100.0, 3),
         "tl": [round(t0, 3), round(t1, 3)], "scene": scene, "label": label, "audio": False, "silence": False,
         "subs": False, "score": round(p["score"], 3), "stab": round(p["stab"], 2), "flags": list(flags),
         "exclude": [], "script_line": ""}
    if p["speed"] != 100:
        d["speed"] = p["speed"]
        d["flags"].append(f"slow-mo {p['speed']:g}%")
    if abs(c.get("rot") or 0) >= 0.5:
        d["rotate_fix"] = c["rot"]
    return d


def gap_seg(t0, t1, scene, text):
    return {"file": "", "gap": True, "in": 0.0, "out": round(t1 - t0, 3), "tl": [round(t0, 3), round(t1, 3)],
            "scene": scene, "text": text, "label": text[:100], "audio": False, "silence": False, "subs": False,
            "score": 0, "flags": ["thiếu hình"], "exclude": [], "script_line": ""}


def music_item(path, start, end, C, duck=(), fade_out=None, fade_in=0.0):
    return {"file": os.path.abspath(path).replace("\\", "/"), "name": os.path.basename(path), "start": round(start, 3),
            "in": 0.0, "end": round(end, 3), "gain_db": float(C.get("music_gain_db", -4.0)),
            "fade_in": round(float(fade_in), 2),
            "fade_out": round(float(C.get("music_fade_out", 2.0) if fade_out is None else fade_out), 2),
            "duck": [[round(a, 3), round(b, 3)] for a, b in duck], "duck_db": float(C.get("duck_db", -16.0))}


def _mmss(t):
    return f"{int(t // 60)}:{int(t % 60):02d}"


# ====================================================================== mode: beat
def plan_beat(mat, C, P, music, target, chrono, notes):
    if music:
        A = load_music(music[0], mat.W)
        from .music import fit_to_length
        fit = fit_to_length(A, target, fade=float(C.get("music_fade_out", 2.0))) if target else \
            {"end": A["duration"], "fade": float(C.get("music_fade_out", 2.0))}
        end = float(fit["end"])
        natural_end = abs(end - A["duration"]) < 0.5
        P["montage"] = {"music": A["name"], "bpm": A["bpm"], "mood": A.get("mood"), "music_dur": A["duration"], "end": end}
    else:
        end = float(target or 60.0)
        A = {"beats": [], "beat_period": 0.5, "bpm": 120, "sections": [
            {"start": 0.0, "end": min(4.0, end / 6), "label": "intro", "energy": 0.3},
            {"start": min(4.0, end / 6), "end": end * 0.85, "label": "main", "energy": 0.6},
            {"start": end * 0.85, "end": end, "label": "outro", "energy": 0.3}]}
        natural_end = False
        notes.append("Chưa chọn nhạc → cắt theo lưới 120 BPM; chọn nhạc ở tab Âm thanh rồi Align lại để cắt đúng beat")
        P["montage"] = {"music": None, "bpm": 120, "end": end}
    slots, secs, period = beat_slots(A, end, C)
    if int(C.get("shuffle_seed") or 0):
        P["montage"]["shuffle"] = {"seed": int(C["shuffle_seed"]), "amount": float(C.get("shuffle_amount", 0.6)),
                                   "rhythm": bool(C.get("shuffle_rhythm"))}
        notes.append("🎲 Shuffle #%d · mức %.2f · %s" % (int(C["shuffle_seed"]), float(C.get("shuffle_amount", 0.6)),
                     "đổi hình + nhịp cắt" if C.get("shuffle_rhythm") else "đổi hình, giữ nhịp cắt"))
    # ---- spoken moments: audio over the montage, the first slot(s) show the speaker in sync
    moments = pick_moments(mat, int(C.get("speech_moments", 0)), chrono=True)
    inserts = []
    if moments:
        # candidate starts: song start (after one bar) + every quieter section start + bars inside breaks / intros;
        # moments are spread over the film (cold open, then evenly), preferring break / build / intro over main
        pref = {"intro": 0.0, "break": 0.0, "build": 0.3, "outro": 0.5, "main": 0.8, "verse": 0.8}
        cands = [(max(2.0, period * 4), 0.0)]
        for s in secs:
            lab = s.get("label")
            if lab in pref:
                cands.append((float(s["start"]), pref[lab]))
                if lab in ("break", "intro"):
                    cands += [(float(x), pref[lab] + 0.1) for x in (A.get("downbeats") or [])
                              if float(s["start"]) + 1.0 < float(x) < float(s["end"]) - 3.0]
        cands = sorted({(round(x, 2), w) for x, w in cands if 1.0 <= x < end - 6.0})
        n_m = len(moments)
        want = [cands[0][0] if cands else 2.0] + [end * (0.12 + 0.76 * i / max(1, n_m - 1)) for i in range(1, n_m)]
        busy_until = -1.0
        for u, w_t in zip(moments, want):
            L = u["b"] - u["a"]
            ok_c = [(x, w) for x, w in cands if x >= busy_until + 2.0 and x + L < end - 2.0]
            if not ok_c:
                notes.append(f"Bỏ câu nói '{u['text'][:40]}…' — không còn chỗ trong nhạc")
                continue
            spot = min(ok_c, key=lambda xw: abs(xw[0] - w_t) / max(end, 1.0) * 4 + xw[1])[0]
            bts = [x for x in (A.get("beats") or []) if x >= spot - 0.02]
            at = float(bts[0]) if bts and bts[0] < spot + 2 * period else spot     # speech starts on a beat
            k = max(i for i in range(len(slots)) if slots[i]["t0"] <= at + 1e-6)
            lead = at - slots[k]["t0"]                   # speaker on screen a moment before the first word
            if u["a"] - lead < 0.05:
                at, lead = slots[k]["t0"], 0.0
            inserts.append({"u": u, "at": round(at, 3), "k": k, "lead": lead})
            busy_until = at + L
    for ins in inserts:            # speaker shot: merge the slots covering the first speaker_shot_s seconds
        u, k = ins["u"], ins["k"]
        lim = ins["at"] + min(float(C.get("speaker_shot_s", 3.5)), u["b"] - u["a"])
        t_pic = slots[k]["t0"]
        j = k
        cap = ins["lead"] + float(C.get("speaker_shot_s", 3.5)) + 1.5
        while j + 1 < len(slots) and slots[j + 1]["t0"] < lim - 0.3 and not slots[j + 1].get("fixed") \
                and slots[j + 1]["t1"] - t_pic <= cap:
            j += 1
        t1 = slots[j]["t1"]
        f = u["file"]
        src_in = u["a"] - ins["lead"]                   # picture and words stay in sync
        dur = float(mat.media[f].get("dur") or 0)
        ok = src_in >= 0 and src_in + (t1 - t_pic) <= dur - 0.05
        if ok:
            sc, _, _ = mat.profile(f)
            i0, i1 = int(src_in * HZ), int((src_in + t1 - t_pic) * HZ)
            st = float(sc[i0:max(i0 + 1, i1)].mean()) if i1 > i0 else 0.0
            ok = st >= 0.25
        if ok:
            new = dict(slots[k], t1=t1, fixed={"file": f, "in": round(src_in, 3), "stab": st, "text": u["text"]})
            slots[k:j + 1] = [new]
            for o in inserts:
                if o["k"] > k:
                    o["k"] -= j - k
        ins["speaker_ok"] = ok
    # ---- shots
    pk_notes = []
    picked, asg = fill_slots(slots, mat, C, chrono, pk_notes)
    split_gaps(slots, picked, asg, C, pk_notes)              # long empty slots → two shots on the middle beat
    held = hold_actions(slots, picked, mat, asg, C, period)
    if held:
        pk_notes.append(f"Giữ {held} shot thêm tới beat sau vì hành động chưa xong (cắt vẫn đúng beat)")
    notes += pk_notes
    scene_of = {}
    for i, s in enumerate(secs):
        sc = f"M{i + 1:02d}"
        scene_of[i] = sc
        P["scene_names"][sc] = f"♪ {LABEL_VI.get(s.get('label'), s.get('label'))} {_mmss(float(s['start']))}"
    nfix = 0
    for k, sl in enumerate(slots):
        sc = scene_of.get(sl["sec"], "M01")
        if sl.get("fixed"):
            fx = sl["fixed"]
            seg = {"file": fx["file"], "in": fx["in"], "out": round(fx["in"] + sl["t1"] - sl["t0"], 3),
                   "tl": [sl["t0"], sl["t1"]], "scene": sc, "label": "🎙 " + fx["text"][:90], "audio": False,
                   "silence": False, "subs": False, "score": 1.0, "stab": round(fx["stab"], 2),
                   "flags": ["người nói (khớp tiếng)"], "exclude": [], "script_line": "moment", "beat": sl["label"]}
            P["segments"].append(seg)
            nfix += 1
            continue
        p = picked[k]
        if not p:
            P["segments"].append(gap_seg(sl["t0"], sl["t1"], sc, "THIẾU HÌNH — " + LABEL_VI.get(sl["label"], sl["label"])))
            continue
        desc = (p["c"].get("desc") or "")[:70]
        seg = seg_from_pick(p, sl["t0"], sl["t1"], sc, f"♪ {LABEL_VI.get(sl['label'], sl['label'])} · {desc}")
        seg["beat"] = sl["label"]
        if C.get("xfade_slow") and sl["slow"] and k and slots[k - 1]["slow"]:
            seg["xfade"] = round(min(0.8, 0.3 * (sl["t1"] - sl["t0"])), 2)
        P["segments"].append(seg)
    # ---- audio: inserts on A1, music ducked under them
    duck = []
    max_chars = int((C.get("subs") or {}).get("max_chars", 32))
    for ins in inserts:
        u = ins["u"]
        pre = min(0.1, u["a"], ins["at"])
        P["audio_inserts"].append({"file": u["file"], "in": round(u["a"] - pre, 3), "out": round(u["b"] + 0.25, 3),
                                   "at": round(ins["at"] - pre, 3), "gain_db": 0.0, "text": u["text"]})
        duck.append([ins["at"] - 0.35, ins["at"] + (u["b"] - u["a"]) + 0.5])
        P["sub_cues"] += sub_chunks([w for w in u["words"]], ins["at"] - u["a"], max_chars)
        P["markers"].append([round(ins["at"], 3), "🎙 " + u["text"][:80]])
        P["vo_lines"].append({"line": "moment", "text": u["text"], "file": u["file"], "at": round(ins["at"], 2),
                              "score": u.get("score")})
        if not ins.get("speaker_ok"):
            notes.append(f"Câu '{u['text'][:40]}…': hình người nói không ổn định → chỉ dùng tiếng trên B-roll")
    if music:
        P["music"].append(music_item(music[0], 0.0, end, C, duck=duck,
                                     fade_out=1.0 if natural_end else float(C.get("music_fade_out", 2.0))))
    P["report_notes"] += notes
    return slots, picked, secs, period, inserts


# ====================================================================== mode: vo
def decode_16k(path):
    import subprocess
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                       capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"không đọc được {path}: {r.stderr.decode('utf-8', 'replace')[:200]}")
    return np.frombuffer(r.stdout, np.float32).copy()


def decode_16k_many(paths, workers=4, ahead=8):
    """Yield (path, audio | Exception) in order while the next clips decode in background threads — whoever consumes
    the audio (PANNs on the GPU, speaker embeddings) no longer waits for ffmpeg: sound index 28 s → see sounds.index."""
    from concurrent.futures import ThreadPoolExecutor
    paths = list(paths)

    def one(p):
        try:
            return decode_16k(p)
        except Exception as e:                       # noqa: BLE001 — handed to the caller with its path
            return e
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = [ex.submit(one, p) for p in paths[:ahead]]
        for i, p in enumerate(paths):
            if i + ahead < len(paths):
                futs.append(ex.submit(one, paths[i + ahead]))
            yield p, futs[i].result()


def transcribe_vo(path, W, model="large-v3-turbo", cuda=False, lang="vi"):
    """→ (words [[s, e, w, p]], duration). Cached by file size + mtime."""
    d = os.path.join(W, "vo_tx")
    os.makedirs(d, exist_ok=True)
    cp = os.path.join(d, file_hash(path) + ".json")
    c = _j(cp, None)
    if isinstance(c, dict) and c.get("words") is not None:
        return c["words"], float(c["dur"])
    audio = decode_16k(path)
    dur = len(audio) / 16000.0
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    import nxcut
    m, dev = nxcut._whisper(model, cuda)
    print(f"🎙 transcribe VO {os.path.basename(path)} ({dur:.0f}s, {dev})", flush=True)
    segs, _ = m.transcribe(audio, language=lang or None, word_timestamps=True, vad_filter=True,
                           condition_on_previous_text=False, beam_size=5)
    words = []
    for s in segs:
        for w in (getattr(s, "words", None) or []):
            t = str(w.word).strip()
            if t:
                words.append([round(float(w.start), 3), round(float(w.end), 3), t, round(float(w.probability), 3)])
    jsonio.save_atomic(cp, {"file": path, "dur": dur, "words": words, "model": model})
    return words, dur


def vo_shots(words, C):
    """sentences (punctuation / pauses ≥ 0.7 s) → shots of vo_shot_min … vo_shot_max seconds."""
    mx, mn = float(C.get("vo_shot_max", 5.0)), float(C.get("vo_shot_min", 1.6))
    sents, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if re.search(r"[.!?…]$", w[2]) or nxt is None or nxt[0] - w[1] >= 0.7:
            sents.append(cur)
            cur = []
    if cur:
        sents.append(cur)

    def split(ws):
        L = ws[-1][1] - ws[0][0]
        if L <= mx or len(ws) < 4:
            return [ws]
        mid = ws[0][0] + L / 2
        best, bv = None, -1e9
        for j in range(2, len(ws) - 1):
            t = ws[j][0]
            v = (ws[j][0] - ws[j - 1][1]) * 4 + (1.5 if re.search(r"[,;:]$", ws[j - 1][2]) else 0) - abs(t - mid) / L
            if t - ws[0][0] >= mn and ws[-1][1] - t >= mn and v > bv:
                best, bv = j, v
        if best is None:
            return [ws]
        return split(ws[:best]) + split(ws[best:])

    shots = [p for s in sents for p in split(s)]
    merged = []
    for s in shots:
        if merged and (s[-1][1] - s[0][0] < mn or merged[-1][-1][1] - merged[-1][0][0] < mn) \
                and s[-1][1] - merged[-1][0][0] <= mx * 1.2:
            merged[-1] = merged[-1] + s
        else:
            merged.append(s)
    return merged


def plan_vo(mat, C, P, vo_files, music, notes, model="large-v3-turbo", cuda=False, lang="vi"):
    lead, tail = float(C.get("vo_lead", 0.5)), float(C.get("vo_tail", 1.0))
    words, at = [], lead
    for vf in vo_files:
        ws, dur = transcribe_vo(vf, mat.W, model, cuda, lang)
        P["vo_track"].append({"file": os.path.abspath(vf).replace("\\", "/"), "at": round(at, 3), "in": 0.0,
                              "dur": round(dur, 3), "gain_db": 0.0, "name": os.path.basename(vf)})
        words += [[w[0] + at, w[1] + at, w[2], w[3] if len(w) > 3 else 1.0] for w in ws]
        at += dur + 0.4
    end = at - 0.4 + tail
    if not words:
        raise SystemExit("VO không có lời (transcribe rỗng) — kiểm tra file / ngôn ngữ")
    shots = vo_shots(words, C)
    cuts = [0.0] + [max(0.0, s[0][0] - 0.15) for s in shots[1:]] + [end]
    lines = []
    mx = float(C.get("vo_shot_max", 5.0))
    for i, s in enumerate(shots):
        t0, t1 = cuts[i], cuts[i + 1]
        n = max(1, int(math.ceil((t1 - t0) / (mx * 1.15))))        # a pause in the VO still changes picture every ≤ mx s
        text = " ".join(w[2] for w in s)
        for j in range(n):
            lines.append({"id": f"V{i + 1:02d}" + (chr(97 + j) if n > 1 else ""), "text": text,
                          "t0": round(t0 + (t1 - t0) * j / n, 3), "t1": round(t0 + (t1 - t0) * (j + 1) / n, 3)})
    # B-roll by content: the script pipeline's Picker (TF-IDF over AI descriptions × stability)
    from .assemble import ASM_CFG
    from .brollpick import Picker
    pc = dict(ASM_CFG, **{k: v for k, v in C.items() if k in ASM_CFG})
    sa = _j(os.path.join(mat.W, "source_ai.json"), {})
    ai_clips = dict((sa.get("clips") or {}) if isinstance(sa, dict) else {})
    segs = {}
    from .segments import seg_key
    for f, sl in mat.segs.items():
        if len(sl) >= 2 and f in mat.media:
            d_ = mat.sdesc.get(f, {})
            segs[f] = [dict(s, desc=d_.get(seg_key(s), "")) for s in sl]
    for f, d in mat.desc.items():
        if f not in segs and not (ai_clips.get(f) or {}).get("summary"):
            ai_clips[f] = dict(ai_clips.get(f) or {}, summary=d)
    pk = Picker(mat.tags, ai_clips, mat.media, mat.bad, set(), pc, stab=mat.stab, segs=segs)
    asg = Assigner(mat, C, False, notes)
    P["scene_names"]["VO"] = "Explainer — VO"
    prev = None
    nmatch = nfall = 0
    for li, ln in enumerate(lines):
        D = ln["t1"] - ln["t0"]
        got = pk.cover(ln["text"], D + 0.2, min_len=D, slowmo_ok=True)
        seg = None
        if got:
            v, a, b, sc = got
            slow = bool(v.get("_slowmo"))
            sp = float(C.get("slowmo_speed", 50)) if slow else 100.0
            seg = {"file": v["file"], "in": round(a, 3), "out": round(a + D * sp / 100.0, 3), "tl": [ln["t0"], ln["t1"]],
                   "scene": "VO", "label": f"{ln['id']} · {ln['text'][:90]}", "audio": False, "silence": False,
                   "subs": False, "score": sc, "flags": [f"khớp nội dung {sc:.2f}"], "exclude": [], "script_line": ln["id"]}
            if sp != 100:
                seg["speed"] = sp
            asg.take({"file": v["file"]}, a, D * sp / 100.0, li)
            nmatch += 1
        else:
            best = asg.pick(D, {"energy": 0.5, "slow": False}, [(1, prev)] if prev else [], k=li)
            if best:
                sc, c, s0, sp, st, Dsrc = best
                asg.take(c, s0, Dsrc, li)
                if not hasattr(pk, "used_ids"):
                    pk.used_ids, pk.used_groups, pk._uses, pk.used_files = set(), set(), {}, set()
                for i_, v_ in list(pk.items.items()):    # the Picker must not offer this stretch again
                    sg = v_.get("seg")
                    if v_["file"] == c["file"] and (not sg or (sg[0] < s0 + Dsrc and sg[1] > s0)) and i_ not in pk.used_ids:
                        pk._take(i_)
                seg = seg_from_pick({"c": c, "in": s0, "speed": sp, "stab": st, "score": sc}, ln["t0"], ln["t1"], "VO",
                                    f"{ln['id']} · {ln['text'][:90]}", ["không khớp nội dung → B-roll ổn định nhất còn lại"])
                seg["script_line"] = ln["id"]
                P["broll_requests"].append({"line": ln["id"], "text": ln["text"]})
                nfall += 1
        if seg is None:
            seg = gap_seg(ln["t0"], ln["t1"], "VO", "VO — cần B-roll\n" + ln["text"])
            seg["script_line"] = ln["id"]
        prev = {"file": seg.get("file")}
        P["segments"].append(seg)
    P["vo_lines"] = [{"line": ln["id"], "text": ln["text"], "at": round(ln["t0"], 2)} for ln in lines]
    P["sub_cues"] = sub_chunks(words, 0.0, int((C.get("subs") or {}).get("max_chars", 32)))
    if music:
        spans = []
        for w in words:
            if spans and w[0] - spans[-1][1] < 1.5:
                spans[-1][1] = w[1]
            else:
                spans.append([w[0], w[1]])
        mi = music_item(music[0], 0.0, end, C, duck=[[a - 0.3, b + 0.4] for a, b in spans])
        mi["duck_db"] = float(C.get("vo_music_db", -20.0)) - float(C.get("music_gain_db", -4.0))
        P["music"].append(mi)
    P["montage"] = {"vo": [os.path.basename(v) for v in vo_files], "vo_dur": round(end, 2), "shots": len(lines),
                    "matched": nmatch}
    ngap = len(lines) - nmatch - nfall
    notes.append(f"VO {end:.1f}s → {len(lines)} shot, {nmatch} khớp nội dung, {nfall} lấy B-roll ổn định"
                 + (f", {ngap} thẻ thiếu hình" if ngap else ""))
    P["report_notes"] += notes
    return lines


# ====================================================================== mode: story (long film in shooting order)
def plan_story(mat, C, P, music, target, notes):
    T = float(target or C.get("target_dur") or 1200.0)
    us = [u for u in utterances(mat, max_len=60.0, gap=1.2) if u["b"] - u["a"] >= 2.0]
    for u in us:
        u["score"] = moment_score(u, mat)
    # one camera per moment: overlapping capture time (multi-cam) → keep the better-sounding / steadier one
    us.sort(key=lambda u: (u.get("t_abs") or 0, u["file"], u["a"]))
    keep = []
    for u in us:
        dup = next((o for o in keep if u.get("t_abs") and o.get("t_abs") and
                    min(o["t_abs"] + o["b"] - o["a"], u["t_abs"] + u["b"] - u["a"]) - max(o["t_abs"], u["t_abs"])
                    > 0.5 * min(o["b"] - o["a"], u["b"] - u["a"])), None)
        if dup is None:
            keep.append(u)
        elif u["score"] > dup["score"] + 0.1:
            keep[keep.index(dup)] = u
    speech_budget = float(C.get("story_speech_share", 0.7)) * T
    tot = sum(u["b"] - u["a"] for u in keep)
    if tot > speech_budget:
        ranked = sorted(keep, key=lambda u: -u["score"])
        sel, acc = [], 0.0
        for u in ranked:
            if acc + (u["b"] - u["a"]) <= speech_budget:
                sel.append(u)
                acc += u["b"] - u["a"]
        dropped = len(keep) - len(sel)
        keep = sorted(sel, key=lambda u: (u.get("t_abs") or 0, u["file"], u["a"]))
        notes.append(f"Thoại {tot / 60:.1f} phút > {speech_budget / 60:.1f} phút → giữ {len(keep)} đoạn nói hay nhất, bỏ {dropped}")
        tot = acc
    # scenes = gaps in shooting time
    items = [("speech", u.get("t_abs") or 0.0, u) for u in keep]
    cands_t = [c for c in mat.cands if c.get("t_abs")]
    all_t = sorted([x[1] for x in items if x[1]] + [c["t_abs"] for c in cands_t])
    if not all_t:
        raise SystemExit("Không có thời gian quay (created) trong media.json → không dựng được theo trình tự")
    gap = float(C.get("story_scene_gap_min", 20.0)) * 60.0
    bounds = [all_t[0]]
    for a, b in zip(all_t, all_t[1:]):
        if b - a > gap:
            bounds.append(b)
    bounds.append(all_t[-1] + 1.0)
    scenes = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
    broll_budget = max(30.0, T - tot)
    shot_s = float(C.get("story_shot_s", 3.5)) * float(C.get("pace", 1.0))
    # B-roll shots per scene ∝ the scene's steady material (at least a short establishing run)
    mat_s = [sum(c["b"] - c["a"] for c in cands_t if a <= c["t_abs"] < b) for a, b in scenes]
    tot_m = sum(mat_s) or 1.0
    asg = Assigner(mat, dict(C, w_chrono=0.0), False, notes)
    t = 0.0
    from datetime import datetime
    for si, (a, b) in enumerate(scenes):
        sc = f"S{si + 1:02d}"
        P["scene_names"][sc] = f"{sc} · {datetime.fromtimestamp(a).strftime('%H:%M')}"
        sp_items = [u for u in keep if a <= (u.get("t_abs") or -1) < b]
        n_b = max(int(C.get("story_bridge_shots", 3)), int(round(broll_budget * mat_s[si] / tot_m / shot_s)))
        pool = sorted([c for c in cands_t if a <= c["t_abs"] < b], key=lambda c: c["t_abs"])
        if not pool and not sp_items:
            continue
        # spread the B-roll over the scene's time; each speech block goes where it happened
        anchors = sorted([("speech", u["t_abs"], u) for u in sp_items] +
                         [("broll", a + (b - a) * (i + 0.5) / max(n_b, 1), None) for i in range(n_b)], key=lambda x: x[1])
        prev = None
        scene_items = []
        for kind, tt, u in anchors:
            if kind == "speech":
                d = u["b"] - u["a"]
                scene_items.append((u.get("t_abs") or tt, {"file": u["file"], "in": round(max(0.0, u["a"] - 0.15), 3),
                                    "out": round(u["b"] + 0.3, 3), "scene": sc, "label": u["text"][:100],
                                    "audio": True, "silence": True, "subs": True, "score": u["score"],
                                    "flags": [], "exclude": [], "script_line": "speech"}))
                t += d + 0.45
                prev = {"file": u["file"], "t_abs": u.get("t_abs")}
                continue
            near = lambda c, tt=tt: abs(c["t_abs"] - tt) < max(300.0, (b - a) / max(n_b, 1))
            inside = lambda c, a=a, b=b: bool(c.get("t_abs")) and a <= c["t_abs"] < b
            kk = len(asg.placed)
            best = asg.pick(shot_s, {"energy": 0.4, "slow": True}, [(1, prev)] if prev else [], prefer=near, only=inside, k=kk)
            if not best:
                continue
            scr, c, s0, spd, st, Dsrc = best
            asg.take(c, s0, Dsrc, kk)
            seg = seg_from_pick({"c": c, "in": s0, "speed": spd, "stab": st, "score": scr}, t, t + shot_s, sc,
                                "B-roll · " + (c.get("desc") or "")[:80])
            seg.pop("tl", None)                   # story timeline is not on a grid
            scene_items.append(((c.get("t0") or 0) + s0 if c.get("t0") else tt, seg))
            t += shot_s
            prev = c
        scene_items.sort(key=lambda x: x[0])     # what happened first comes first
        lead = [x for x in scene_items if not x[1].get("audio")][:1]
        if lead and scene_items[0][1].get("audio"):   # a scene opens on a picture (establishing B-roll), not mid-sentence
            scene_items.remove(lead[0])
            scene_items.insert(0, lead[0])
        P["segments"] += [x[1] for x in scene_items]
    P["subs"] = dict(P.get("subs") or {}, on=bool((C.get("subs") or {}).get("on", True)))
    P["silence"] = dict({"on": True, "gap": 0.6, "pad_in": 0.12, "pad_out": 0.2, "min_piece": 0.6}, **(C.get("silence") or {}))
    if music:
        notes.append("Nhạc nền cho phim dài: đặt ở tab Âm thanh trên sequence sau khi Build (ducking theo thoại)")
    P["montage"] = {"speech_s": round(tot, 1), "target": T, "scenes": len(scenes)}
    notes.append(f"Phim theo trình tự quay: {len(scenes)} cảnh · thoại {tot / 60:.1f} phút · mục tiêu {T / 60:.0f} phút")
    P["report_notes"] += notes


# ====================================================================== report
def write_report(P, path, slots=None, period=None):
    L = [f"# {P['name']} — {P.get('mode')} ({P.get('preset')})", ""]
    m = P.get("montage") or {}
    if m.get("music"):
        L.append(f"Nhạc: **{m['music']}** · {m.get('bpm')} BPM · {m.get('mood', '')} · dùng {m.get('end', 0):.1f}s / {m.get('music_dur', 0):.1f}s")
    segs = [s for s in P["segments"] if not s.get("gap")]
    tl_end = max([s["tl"][1] for s in P["segments"] if s.get("tl")] or [0])
    st = [s.get("stab") for s in segs if s.get("stab") is not None]
    L.append(f"Shot: {len(P['segments'])} ({len(set(s['file'] for s in segs))} clip) · thiếu hình {sum(1 for s in P['segments'] if s.get('gap'))}"
             + (f" · độ ổn định TB {np.mean(st):.2f} (min {min(st):.2f})" if st else "")
             + (f" · dài {tl_end:.1f}s" if tl_end else ""))
    if slots and period:
        from collections import OrderedDict
        by = OrderedDict()
        for s in slots:
            by.setdefault(s["label"], []).append(s["t1"] - s["t0"])
        L.append("Nhịp: " + " · ".join(f"{LABEL_VI.get(k, k)} {np.mean(v):.2f}s ({np.mean(v) / period:.1f} beat) ×{len(v)}"
                                        for k, v in by.items()))
    if P.get("audio_inserts"):
        L += ["", "## Câu nói chèn (A1, nhạc duck)"] + [f"- {i['at']:.1f}s · {i['file']} {i['in']:.1f}–{i['out']:.1f} · {i['text'][:90]}"
                                                       for i in P["audio_inserts"]]
    L += ["", "| TL | Đoạn | File | In–Out | Tốc độ | Ổn định | Ghi chú |", "|---|---|---|---|---|---|---|"]
    for s in P["segments"]:
        tl = s.get("tl")
        L.append(f"| {tl[0]:.2f}–{tl[1]:.2f} | {s.get('beat', s.get('scene', ''))} | {s.get('file') or '—'} | "
                 f"{s.get('in', 0):.2f}–{s.get('out', 0):.2f} | {s.get('speed', 100):g}% | {s.get('stab', '')} | "
                 f"{s.get('label', '')[:60]} |" if tl else
                 f"| — | {s.get('scene', '')} | {s.get('file') or '—'} | {s.get('in', 0):.2f}–{s.get('out', 0):.2f} | "
                 f"{s.get('speed', 100):g}% | {s.get('stab', '')} | {s.get('label', '')[:60]} |")
    L += ["", "## Ghi chú"] + ([f"- {n}" for n in P.get("report_notes") or []] or ["- không"])
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


# ====================================================================== CLI
def config(preset, cfg=None):
    from .presets import get_preset
    pd = dict(get_preset(preset)) if preset else {}
    C = dict(DEFAULTS)
    C.update({k: v for k, v in pd.items()})
    C.update(pd.get("montage") or {})
    # the editor's style book (nxcore.stylebook): stronger than the preset, weaker than this run's settings.
    # Its pace is a multiplier on the preset's own pace (the panel's pace slider is one too, already applied in cfg).
    try:
        from . import stylebook
        st = stylebook.params("montage", preset)
    except Exception:
        st = {}
    if "pace" in st:
        C["pace"] = round(float(C.get("pace") or 1.0) * float(st.pop("pace")), 3)
    C.update(st)
    C.update(cfg or {})
    if "auto_slowmo_60p" in pd and "auto_slowmo" not in (pd.get("montage") or {}):
        C["auto_slowmo"] = bool(pd["auto_slowmo_60p"])
    return C


def chrono_on(C, src):
    try:
        from .chrono import enabled
        return enabled(dict(C, preset=C.get("id", "")), src)
    except Exception:
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(description="Dựng montage theo nhạc / VO / trình tự quay (không cần kịch bản)")
    ap.add_argument("--src", required=True)
    ap.add_argument("--preset", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--seq", default="")
    ap.add_argument("--music", action="append", default=[])
    ap.add_argument("--vo", action="append", default=[])
    ap.add_argument("--target", type=float, default=0.0, help="độ dài mong muốn (s); 0 = theo preset / hết bài")
    ap.add_argument("--cfg", default="", help="JSON override (montage keys)")
    ap.add_argument("--shuffle", type=int, default=0, help="beat: seed xáo shot (0 = tắt) — cùng seed ra cùng bản")
    ap.add_argument("--shuffle-rhythm", action="store_true", help="beat: xáo cả nhịp cắt (×2 / ½ beat)")
    ap.add_argument("--mode", default="", help="beat | vo | story (mặc định theo preset)")
    ap.add_argument("--cuda", action="store_true", help="GPU cho transcribe VO")
    ap.add_argument("--model", default="large-v3-turbo")
    ap.add_argument("--lang", default="vi")
    a = ap.parse_args(argv)
    cfg = _j(a.cfg, {}) if a.cfg else {}
    cfg = (cfg.get("montage") or cfg.get("assemble") or cfg) if isinstance(cfg, dict) else {}
    C = config(a.preset, cfg)
    if a.shuffle:
        C["shuffle_seed"] = a.shuffle
    if a.shuffle_rhythm:
        C["shuffle_rhythm"] = True
    mode = a.mode or C.get("mode") or "beat"
    target = a.target or float(cfg.get("target_dur") or 0) or float(C.get("target_dur") or 0)
    music = [m for m in a.music if m and os.path.exists(m)]
    for m in a.music:
        if m and not os.path.exists(m):
            print(f"⚠ không thấy file nhạc {m}", flush=True)
    seq = None
    if a.seq:
        from .run import parse_seq
        seq = parse_seq(a.seq, C.get("seq"))
    mat = Material(a.src, C)
    if not mat.media:
        raise SystemExit("Chưa có media.json — bấm Phân tích source trước")
    P = base_plan(a.src, a.name, a.out, seq or default_seq(mat.media, C), C, mode)
    notes = []
    chrono = chrono_on(C, a.src)
    if chrono and not float(C.get("w_chrono") or 0):
        C["w_chrono"] = 0.2
    print(f"montage {mode} · preset {a.preset} · {len(mat.cands)} đoạn ứng viên từ {len(mat.media)} clip"
          + (" · theo giờ quay" if chrono else ""), flush=True)
    slots = period = None
    if mode == "beat":
        slots, _, _, period, _ = plan_beat(mat, C, P, music, target, chrono, notes)
    elif mode == "vo":
        vos = [v for v in a.vo if v and os.path.exists(v)]
        if not vos:
            raise SystemExit("Preset VO cần file voice-over: --vo voice.wav (thu sẵn hoặc VoiceStudio)")
        plan_vo(mat, C, P, vos, music, notes, a.model, a.cuda, a.lang)
    elif mode == "story":
        plan_story(mat, C, P, music, target, notes)
    else:
        raise SystemExit(f"mode không hỗ trợ: {mode}")
    os.makedirs(os.path.dirname(os.path.abspath(a.plan)) or ".", exist_ok=True)
    jsonio.save_atomic(a.plan, P)
    rp = os.path.splitext(a.plan)[0] + "_align.md"
    write_report(P, rp, slots, period)
    print(open(rp, encoding="utf-8").read())
    print("plan:", a.plan)
    return P


if __name__ == "__main__":
    main()
