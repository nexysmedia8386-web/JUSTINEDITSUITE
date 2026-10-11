"""Script-driven take selection for Nexys rough cuts.

Input : nxcut work dir (_nexys_work/transcripts/*.json, media.json, shake.json) + a script
Output: plan.json compatible with `nxcut.py build`, plus an alignment report.

Pipeline per script line (dialogue):
  1. syllable similarity matrix line x clip-tokens (diacritic / Southern-accent tolerant)
  2. cheap window search -> Smith-Waterman local alignment inside each window
  3. repeat with used tokens masked -> every attempt (take) of that line in every clip
  4. per attempt: coverage, exactness, fillers, stutters/restarts, ad-libs, shake overlap,
     director cues right after it ("làm lại" = NG, "ok được rồi" = keeper)
  5. global greedy assignment (no token reused by two lines) + context pass for short lines
  6. if no attempt is complete, try stitching a prefix attempt + suffix attempt (flagged)
"""
import argparse
import glob
import json
import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from .script_parser import parse_script
from .textnorm import syllables, syl_sim, is_filler, strip_diacritics, content, numeral_edits, STOPWORDS, _PHRASES

# ----------------------------------------------------------------------------- config
DEFAULT_CFG = {
    "min_coverage": 0.55,        # attempt must match >= 55% of the line's syllables
    "min_coverage_short": 0.75,  # for lines <= 4 syllables
    "pad_in": 0.12, "pad_out": 0.20,
    "adaptive_ripple": True,     # Q/W dynamic trim: eliminate breath gasp & dead air
    "weights": {"match": 0.40, "fluency": 0.20, "stability": 0.15, "complete": 0.15, "recency": 0.05, "context": 0.05},
    "director_ng_penalty": 0.20, "director_ok_bonus": 0.10, "cue_window": 4.0, "cue_syllables": 6,
    "stitch_min_gain": 0.20,
    "soft_min": 0.30, "soft_min_hits": 2,      # bag-of-words match for questions / actions
    "capture_gap": 2.5, "tail_gap": 10.0,       # keep the unscripted reply after a question
    "block_bonus": 0.25,                       # prefer one clip for a whole dialogue block
    "action_min": 0.5, "action_min_hits": 3,   # actions describe visuals: only match clearly spoken ones
    "soft_dialogue_penalty": 0.10,             # paraphrased dialogue ranks below verbatim takes
    "day_bonus": 0.20,                         # prefer takes from the majority shooting day (same person / look)
    "capture_max_s": 30.0, "capture_max_syl": 120,   # an unscripted reply never swallows the rest of the clip
    "sw_floor": 0.35,                          # SW score per script syllable below which no further take can pass
}

NG_CUES = ["làm lại", "diễn lại", "quay lại", "lại nha", "lại nhé", "lại đi", "lại lần nữa", "sai rồi", "hư rồi", "cắt cắt", "khoan"]
OK_CUES = ["được rồi", "ok rồi", "okay rồi", "ổn rồi", "tốt lắm", "hay lắm", "qua cảnh", "xong rồi", "ok cắt", "được đó"]
BOUNDARY_CUES = ["cắt", "action", "bắt đầu", "máy chạy", "diễn", "ba hai một", "3 2 1"]


# ----------------------------------------------------------------------------- data
@dataclass
class Tok:
    syl: str
    t0: float
    t1: float
    seg: int
    filler: bool
    raw: str = ""     # original whisper word (for highlight matching in nxcut)


@dataclass
class Attempt:
    line: str
    file: str
    i0: int
    i1: int
    t_in: float
    t_out: float
    pairs: list                 # [(script_idx, tok_idx, sim)]
    coverage: float
    exact: float
    first_ok: bool
    last_ok: bool
    fillers: int = 0
    stutters: int = 0
    adlibs: int = 0
    exclude: list = field(default_factory=list)
    shake: float = 0.0
    director: str = ""
    take_no: int = 1
    score: float = 0.0
    parts: list = None          # for stitched attempts: [(file, in, out)]
    flags: list = field(default_factory=list)

    def brief(self):
        return {"file": self.file, "in": round(self.t_in, 2), "out": round(self.t_out, 2), "score": round(self.score, 3),
                "coverage": round(self.coverage, 2), "take": self.take_no, "director": self.director}


# ----------------------------------------------------------------------------- loading
def load_tokens(tpath, speech=None):
    """speech: {file: [label per segment]} (nxcore.speech) — "junk" segments (hallucinations, loops, counting) are dropped."""
    d = json.load(open(tpath, encoding="utf-8"))
    labels = (speech or {}).get(d.get("file") or os.path.basename(tpath))
    toks = []
    for si, s in enumerate(d.get("segments", [])):
        if labels and si < len(labels) and labels[si] == "junk":
            continue
        for w in s.get("words", []):
            t0, t1, txt = w[0], w[1], w[2]
            syls = syllables(txt)
            if not syls:
                continue
            step = (t1 - t0) / len(syls)
            for k, sy in enumerate(syls):
                toks.append(Tok(sy, round(t0 + k * step, 3), round(t0 + (k + 1) * step, 3), si, is_filler(sy), txt.strip()))
    return d.get("file") or os.path.basename(tpath), _merge_phrases(toks)


def _merge_phrases(toks):
    """Apply multi-word phrase normalisation across whisper word boundaries ('ki lô mét' -> 'cây số'),
    then the spoken-number canon of textnorm ('hai mươi mốt' / 'hai mốt' → same tokens) — Whisper gives one word
    per token, so syllables() alone never sees the neighbours."""
    return _numerals(_merge_phrases_only(toks))


def _numerals(toks):
    ed = dict(numeral_edits([t.syl for t in toks]))
    if not ed:
        return toks
    out = []
    for i, t in enumerate(toks):
        new = ed.get(i, t.syl)
        if new is None:            # dropped 'mươi': its time goes to the tens digit before it
            if out:
                out[-1] = Tok(out[-1].syl, out[-1].t0, t.t1, out[-1].seg, out[-1].filler, out[-1].raw)
            continue
        out.append(t if new == t.syl else Tok(new, t.t0, t.t1, t.seg, t.filler, t.raw))
    return out


def _merge_phrases_only(toks):
    pats = [(a.split(), b.split()) for a, b in _PHRASES if " " in a]
    out, i = [], 0
    while i < len(toks):
        for a, b in pats:
            if [t.syl for t in toks[i:i + len(a)]] == a:
                t0, t1 = toks[i].t0, toks[i + len(a) - 1].t1
                step = (t1 - t0) / len(b)
                raw = " ".join(dict.fromkeys(t.raw for t in toks[i:i + len(a)]))
                out += [Tok(sy, round(t0 + k * step, 3), round(t0 + (k + 1) * step, 3), toks[i].seg, False, raw) for k, sy in enumerate(b)]
                i += len(a)
                break
        else:
            out.append(toks[i])
            i += 1
    return out


def merged_bad(src, shake):
    """Shake ranges (nxcut) + FFmpeg QC ranges (blur/black/freeze/dark/bright) in one 'unusable picture' map."""
    from .qc import bad_ranges, classify_freeze
    qc = _jload(os.path.join(src, "_nexys_work", "qc.json"), {})
    media = _jload(os.path.join(src, "_nexys_work", "media.json"), {})
    for f, r in qc.items():
        if isinstance(r, dict) and r.get("freeze"):
            classify_freeze(r, (media.get(f) or {}).get("dur", 0))   # long continuous take ≠ freeze
    out = {}
    for f in set(shake) | set(qc):
        rs = [list(r[:2]) for r in shake.get(f, {}).get("ranges", [])] + bad_ranges(qc.get(f))
        out[f] = {"ranges": sorted(rs)}
    return out, qc


def load_work(src, report=None):
    """report: list that receives a note per dropped transcript (clip excluded by triage / deleted since transcribe)."""
    W = os.path.join(src, "_nexys_work")
    clips = {}
    from .speech import load as speech_load
    sp = speech_load(src)
    mp = os.path.join(W, "media.json")
    media = _jload(mp, {})
    # media.json = the clips triage kept: a transcript of any other clip must not give a take (the build has no
    # media info for it and the editor removed it on purpose). Without media.json (bare tests / old work dirs)
    # every transcript stays.
    check = os.path.exists(mp) and isinstance(media, dict)
    dropped = []
    for tp in sorted(glob.glob(os.path.join(W, "transcripts", "*.json"))):
        if tp.endswith(".v2.json"):
            continue
        f, toks = load_tokens(tp, sp)
        if not toks:
            continue
        if check and f not in media:
            from .media import resolve
            k = resolve(f, media)                 # older transcripts keyed by basename
            if not k or k in clips:
                dropped.append(f)
                continue
            f = k
        clips[f] = toks
    if dropped and report is not None:
        report.append(f"Bỏ {len(dropped)} transcript không có trong media.json (đã loại khi triage / không còn file): "
                      + ", ".join(dropped[:8]) + (" …" if len(dropped) > 8 else ""))
    shake = _jload(os.path.join(W, "shake.json"), {})
    return clips, media, shake


def _jload(p, d):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return d


# ----------------------------------------------------------------------------- similarity
class SimCache:
    def __init__(self):
        self.c = {}

    def __call__(self, a, b):
        k = (a, b)
        v = self.c.get(k)
        if v is None:
            v = self.c[k] = syl_sim(a, b)
        return v


class VocabSim:
    """Syllable similarity against the whole source at once.

    Every clip is stored as an int array of vocabulary ids; a script syllable is compared with each UNIQUE source
    syllable once (exact / no-diacritics / phonetic via hash lookups, fuzzy only for similar-length candidates sharing
    the first letter) and cached. A line × clip matrix is then a numpy gather: M[:, clip_ids] — no Python loops per clip."""

    def __init__(self, clips):
        from .textnorm import strip_diacritics, phon_key
        self.vocab, self.ids = {}, {}
        for f, toks in clips.items():
            self.ids[f] = np.fromiter((self.vocab.setdefault(t.syl, len(self.vocab)) for t in toks), np.int32, len(toks))
        V = list(self.vocab)
        self.n = len(V)
        self.by_strip, self.by_phon, self.by_first = {}, {}, {}
        self.strip = [strip_diacritics(v) for v in V]
        for k, v in enumerate(V):
            st = self.strip[k]
            self.by_strip.setdefault(st, []).append(k)
            self.by_phon.setdefault(phon_key(v), []).append(k)
            if len(st) >= 3:
                self.by_first.setdefault(st[0], []).append(k)
        self.rows = {}

    def row(self, s):
        r = self.rows.get(s)
        if r is not None:
            return r
        from .textnorm import strip_diacritics, phon_key
        from difflib import SequenceMatcher
        r = np.zeros(self.n, np.float32)
        ss = strip_diacritics(s)
        if len(ss) >= 3:                                   # fuzzy (loanwords, typos): 0.45
            for k in self.by_first.get(ss[0], ()):
                t = self.strip[k]
                if 2 * min(len(t), len(ss)) >= 0.75 * (len(t) + len(ss)):
                    sm = SequenceMatcher(None, ss, t)
                    if sm.quick_ratio() >= 0.75 and sm.ratio() >= 0.75:
                        r[k] = 0.45
        for k in self.by_phon.get(phon_key(s), ()):         # phonetic (Southern merges): 0.65
            r[k] = 0.65
        for k in self.by_strip.get(ss, ()):                 # tone mark errors: 0.8
            r[k] = 0.8
        k = self.vocab.get(s)
        if k is not None:
            r[k] = 1.0
        self.rows[s] = r
        return r

    def line(self, syls):
        return np.stack([self.row(s) for s in syls]) if syls else np.zeros((0, self.n), np.float32)

    def matrix(self, M, f):
        return M[:, self.ids[f]]


def sim_matrix(line_syl, toks, sim):
    uniq = {}
    for j, t in enumerate(toks):
        uniq.setdefault(t.syl, []).append(j)
    S = np.zeros((len(line_syl), len(toks)), np.float32)
    for i, s in enumerate(line_syl):
        for u, idx in uniq.items():
            v = sim(s, u)
            if v:
                S[i, idx] = v
    return S


def content_rows(syls):
    """Script rows that carry meaning; None when fewer than 2 (then every row counts)."""
    r = [i for i, x in enumerate(syls) if x not in STOPWORDS and not is_filler(x)]
    return r if len(r) >= 2 else None


def candidate_windows(S, m, rows=None):
    """Token windows worth a Smith-Waterman pass. rows: content syllables of the line — stop words ('em', 'là',
    'có') occur in every sentence, so counting their hits turned almost all speech into windows (most of align time)."""
    H = S[rows] if rows is not None else S
    mc = H.shape[0]
    hits = (H.max(axis=0) >= 0.6).astype(np.float32) if mc else np.zeros(S.shape[1], np.float32)
    n = len(hits)
    if n == 0:
        return []
    w = int(m * 1.6) + 4
    need = mc if mc <= 3 else max(2, math.ceil(0.45 * mc))
    rs = np.convolve(hits, np.ones(w), "full")[w - 1: w - 1 + n]  # rs[j] = hits[j..j+w)
    wins = []
    for j in np.where(rs >= need)[0]:
        a, b = max(0, j - m), min(n, j + w + m)
        if wins and a <= wins[-1][1]:
            wins[-1][1] = max(wins[-1][1], b)
        else:
            wins.append([a, b])
    return wins


# ----------------------------------------------------------------------------- Smith-Waterman
GAP_TOK, GAP_TOK_FILLER, GAP_SCRIPT = -0.6, -0.1, -0.8
STITCH_TOPK = 8          # chains kept per (syllable, depth) state in try_stitch


def gap_costs(toks):
    return np.array([GAP_TOK_FILLER if t.filler else GAP_TOK for t in toks], np.float32)


def smith_waterman(S, toks, a, b):
    """Local alignment of all script syllables vs toks[a:b]. Returns (score, pairs)."""
    sc, pairs = _sw(S[:, a:b], gap_costs(toks[a:b]))
    return sc, [(i, a + j, v) for i, j, v in pairs]


def _sw(Sw, g):
    """Smith-Waterman on a window matrix Sw (script × window tokens) with per-token gap costs g.
    Returns (score, pairs) with window-relative token indices.

    Row-vectorised: diagonal / up moves are elementwise; the left move (extra spoken token,
    cost g_j) is a prefix scan  H[j] = G[j] + max_{k<=j}(X[k] - G[k]),  G = cumsum(g).
    """
    m, L = Sw.shape
    G = np.concatenate([[0.0], np.cumsum(g)]).astype(np.float32)          # G[0..L]
    H = np.zeros((m + 1, L + 1), np.float32)
    P = np.zeros((m + 1, L + 1), np.int8)
    for i in range(1, m + 1):
        s = Sw[i - 1]
        d = H[i - 1, :-1] + np.where(s > 0, 2 * s - 1, -1.0)
        u = H[i - 1, 1:] + GAP_SCRIPT
        X = np.maximum(0.0, np.maximum(d, u))                               # best without a left move, j=1..L
        Xf = np.concatenate([[0.0], X])                                     # j=0..L
        Hrow = G + np.maximum.accumulate(Xf - G)
        H[i] = Hrow
        Pi = np.where(Hrow[1:] > X + 1e-6, 3, np.where(X <= 0, 0, np.where(X == d, 1, 2)))
        P[i, 1:] = Pi
    i, j = np.unravel_index(int(np.argmax(H)), H.shape)
    score = float(H[i, j])
    pairs = []
    while i > 0 and j > 0 and P[i, j] != 0:
        p = P[i, j]
        if p == 1:
            sv = float(Sw[i - 1, j - 1])
            if sv > 0:
                pairs.append((int(i - 1), int(j - 1), sv))
            i, j = i - 1, j - 1
        elif p == 2:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return score, pairs


# ----------------------------------------------------------------------------- attempt analysis
def analyse(line, f, toks, pairs, cfg):
    m = len(line.syl)
    matched = [p for p in pairs if p[2] >= 0.45]
    if not matched:
        return None
    cov = len({p[0] for p in matched}) / m
    minc = cfg["min_coverage_short"] if m <= 4 else cfg["min_coverage"]
    if cov < minc:
        return None
    i0, i1 = matched[0][1], matched[-1][1]
    exact = float(np.mean([p[2] for p in matched]))
    first_ok = matched[0][0] <= max(0, int(0.15 * m) - 0) if m > 6 else matched[0][0] == 0
    last_ok = matched[-1][0] >= m - 1 - (int(0.15 * m) if m > 6 else 0)
    at = Attempt(line.id, f, i0, i1, 0, 0, matched, cov, exact, first_ok, last_ok)
    # insertions inside the span: fillers / stutters (restart of the following words) / ad-libs
    aligned = {p[1] for p in matched}
    j = i0
    while j <= i1:
        if j in aligned:
            j += 1
            continue
        k = j
        while k <= i1 and k not in aligned:
            k += 1
        run = toks[j:k]
        nxt = [t.syl for t in toks[k:k + len(run)]]
        runk = [strip_diacritics(t.syl) for t in run if not t.filler]
        if all(t.filler for t in run):
            at.fillers += len(run)
            at.exclude.append([run[0].t0, run[-1].t1])
        elif runk and runk == [strip_diacritics(x) for x in nxt[:len(runk)]]:
            at.stutters += 1
            at.exclude.append([run[0].t0, run[-1].t1])
        else:
            # restart: run ends with a repeat of the words aligned just before it ("em tên là... em tên là")
            p0 = max(i0, j - len(runk))
            prev = [strip_diacritics(t.syl) for t in toks[p0:j]]
            if runk and prev and runk[-len(prev):] == prev[-len(runk):]:
                at.stutters += 1
                # cut only the abandoned first copy (+ any filler before the repeat); the repeat flows into the
                # words aligned after it, so it is the copy to keep
                q, n_ = len(run), 0
                while q > 0 and n_ < len(prev):
                    q -= 1
                    n_ += not run[q].filler
                end = run[q - 1].t1 if q > 0 else toks[j - 1].t1
                at.exclude.append([toks[p0].t0, end])
            else:
                at.adlibs += len(run)
        j = k
    at.exclude = _merge(at.exclude)
    return at


def _merge(rs):
    rs = sorted(rs)
    out = []
    for a, b in rs:
        if out and a <= out[-1][1] + 0.05:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([round(a, 3), round(b, 3)])
    return out


def set_times(at, toks, cfg):
    """Set attempt in/out with adaptive Q/W ripple trim:
    - Q ripple trim: clean dead air & breath sounds before first syllable (snap to 0.06s-0.08s ~ 2 frames)
    - W ripple trim: eliminate trailing dead air after last syllable (snap to 0.08s-0.10s ~ 3 frames)
    - Boundary safety: protect consonant onsets and adjacent word boundaries."""
    gap_before = (toks[at.i0].t0 - toks[at.i0 - 1].t1) if at.i0 > 0 else toks[at.i0].t0
    gap_after = (toks[at.i1 + 1].t0 - toks[at.i1].t1) if at.i1 + 1 < len(toks) else 999.0

    if cfg.get("adaptive_ripple", True) and gap_before >= 0.20:
        pad_in = min(cfg.get("pad_in", 0.12), max(0.06, min(0.08, gap_before * 0.3)))
    else:
        pad_in = cfg.get("pad_in", 0.12)

    if cfg.get("adaptive_ripple", True) and gap_after >= 0.25:
        pad_out = min(cfg.get("pad_out", 0.20), max(0.08, min(0.12, gap_after * 0.35)))
    else:
        pad_out = cfg.get("pad_out", 0.20)

    t_in = toks[at.i0].t0 - pad_in
    t_out = toks[at.i1].t1 + pad_out
    if at.i0 > 0:
        t_in = max(t_in, toks[at.i0 - 1].t1 + 0.02)
    if at.i1 + 1 < len(toks):
        t_out = min(t_out, toks[at.i1 + 1].t0 - 0.02)
    at.t_in, at.t_out = round(max(0.0, t_in), 3), round(t_out, 3)


def director_cue(toks, i1, cfg):
    """Look at what is said right after the attempt (usually the director)."""
    if i1 + 1 >= len(toks):
        return ""
    t_end = toks[i1].t1
    # only the utterance right after the take (first ~6 syllables within cue_window s)
    after = " ".join(t.syl for t in toks[i1 + 1:i1 + 1 + cfg["cue_syllables"]] if t.t0 - t_end <= cfg["cue_window"])
    if not after:
        return ""
    for c in NG_CUES:
        if c in after:
            return "NG:" + c
    for c in OK_CUES:
        if c in after:
            return "OK:" + c
    return ""


def shake_overlap(ranges, a, b):
    if not ranges or b <= a:
        return 0.0
    ov = sum(max(0.0, min(b, r[1]) - max(a, r[0])) for r in ranges)
    return min(1.0, ov / (b - a))


def complete_take(at):
    """A full clean take: starts and ends like the script, ≥ 85 % of it said, not called NG, not a paraphrase."""
    return (at.first_ok and at.last_ok and at.coverage >= 0.85 and not at.director.startswith("NG")
            and not getattr(at, "_soft", False) and not at.parts)


def _number_takes(atts):
    """Take numbering per file in time order + recency (later takes are usually better).
    'Last take wins' only compares COMPLETE non-NG takes: an aborted attempt after the good one (camera stopped
    mid-line, director called NG) is not the take the director settled on."""
    by_file = {}
    for at in atts:
        by_file.setdefault(at.file, []).append(at)
    for f, lst in by_file.items():
        lst.sort(key=lambda x: x.t_in)
        n = len(lst)
        full = [a for a in lst if complete_take(a)]
        for k, at in enumerate(lst, 1):
            at.take_no = k
            at._total_takes = n
            at._recency = k / n
            at.is_last_take = len(full) >= 2 and at is full[-1]
            at.is_old_take = len(full) >= 2 and any(at is x for x in full[:-1])


def score_attempt(at, line, cfg):
    W = cfg["weights"]
    m = len(line.syl)
    match = at.coverage * at.exact
    fluency = max(0.0, 1 - (at.fillers * 0.5 + at.stutters * 1.0 + at.adlibs * 0.35) / max(4, m))
    stability = 1 - at.shake
    complete = (0.5 if at.first_ok else 0) + (0.5 if at.last_ok else 0)
    s = W["match"] * match + W["fluency"] * fluency + W["stability"] * stability + W["complete"] * complete
    s += W["recency"] * at._recency

    # Knowledge Base: Last Take Priority (Lựa chọn A - Khuyến nghị thực tế)
    # Take cuối cùng thường là take đạt chuẩn nhất; take cũ bị diễn lại được hạ điểm
    # (only among complete non-NG takes of the same clip — see _number_takes)
    if getattr(at, "is_last_take", False):
        s += cfg.get("last_take_bonus", 0.18)
        at.flags.append(f"take cuối ({at.take_no}/{at._total_takes}) - ưu tiên")
    elif getattr(at, "is_old_take", False):
        s -= cfg.get("old_take_penalty", 0.12)
        at.flags.append(f"take cũ {at.take_no}")

    if at.director.startswith("NG"):
        s -= cfg["director_ng_penalty"]
    elif at.director.startswith("OK"):
        s += cfg["director_ok_bonus"]
    at.score = round(s, 4)
    return at.score


# ----------------------------------------------------------------------------- main alignment
def find_attempts(line, clips, shake, cfg, sim, vs=None):
    out = []
    m = len(line.syl)
    minc = cfg["min_coverage_short"] if m <= 4 else cfg["min_coverage"]
    floor = float(cfg.get("sw_floor", 0.35)) * m
    rows = content_rows(line.syl)
    M = vs.line(line.syl) if vs is not None else None
    for f, toks in clips.items():
        if M is not None:
            ids = vs.ids[f]
            if not len(ids) or float(M[:, ids].max(initial=0.0)) < 0.6:
                continue                                   # no syllable of the line is even close in this clip
            S = M[:, ids]
        else:
            S = sim_matrix(line.syl, toks, sim)
        g = None
        for a, b in candidate_windows(S, m, rows):
            Sw = S[:, a:b].copy()                          # takes found are masked inside this window only
            if g is None:
                g = gap_costs(toks)
            # every take of the line inside the window: SW scores only fall as found takes are masked, so the loop
            # ends at the score floor (no fixed cap — a long session can hold 10+ retakes in one window)
            while True:
                # upper bound: rows that still have a usable match in the window — too few → no further take can pass
                if (Sw >= 0.45).any(axis=1).sum() < minc * m:
                    break
                sc, pairs = _sw(Sw, g[a:b])
                if sc <= 0 or not pairs or sc < floor:
                    break
                lo, hi = pairs[0][1], pairs[-1][1]
                Sw[:, lo:hi + 1] = 0
                pairs = [(i, a + j, v) for i, j, v in pairs]
                at = analyse(line, f, toks, pairs, cfg)
                if at is None:
                    continue
                set_times(at, toks, cfg)
                at.director = director_cue(toks, at.i1, cfg)
                at.shake = shake_overlap(shake.get(f, {}).get("ranges", []), at.t_in, at.t_out)
                if at.shake > 0.05:
                    at.flags.append(f"hình lỗi (rung/nhòe/đen) {at.shake:.0%} thời lượng take")
                out.append(at)
    _number_takes(out)
    for at in out:
        score_attempt(at, line, cfg)
    out.sort(key=lambda x: -x.score)
    return out


def segments_of(toks):
    segs, cur = [], None
    for i, t in enumerate(toks):
        if cur and t.seg == cur[0]:
            cur[2] = i
        else:
            cur = [t.seg, i, i]
            segs.append(cur)
    return [(a, b) for _, a, b in segs]


def soft_attempts(line, clips, shake, cfg, sim, vs=None):
    """Paraphrase-tolerant match: share of the line's content syllables found in 1–2 consecutive whisper segments.
    Hits are a (content syllable × token) boolean matrix per clip (VocabSim rows ≥ 0.65 ≡ syl_sim ≥ 0.65); a cumulative
    sum gives any window's hits in O(1), so each segment / window is evaluated once instead of re-scanning words."""
    C = list(dict.fromkeys(content(line.syl)))
    if len(C) < 2:
        return []
    min_cov, min_hits = ((cfg["action_min"], cfg["action_min_hits"]) if line.type == "action"
                         else (cfg["soft_min"], cfg["soft_min_hits"]))
    out = []
    R = vs.line(C) >= 0.65 if vs is not None else None
    for f, toks in clips.items():
        if not toks:
            continue
        if R is not None:
            H = R[:, vs.ids[f]]
        else:
            uniq = {}
            for j, t in enumerate(toks):
                uniq.setdefault(t.syl, []).append(j)
            H = np.zeros((len(C), len(toks)), bool)
            for i, c in enumerate(C):
                for w, idx in uniq.items():
                    if sim(c, w) >= 0.65:
                        H[i, idx] = True
        if not H.any():
            continue
        cs = np.concatenate([np.zeros((len(C), 1), np.int32), np.cumsum(H, axis=1, dtype=np.int32)], axis=1)
        segs = segments_of(toks)
        memo = {}

        def eval_(a, b):
            r = memo.get((a, b))
            if r is None:
                hit = (cs[:, b + 1] - cs[:, a]) > 0
                hits = [C[i] for i in np.flatnonzero(hit)]
                cov = len(hits) / len(C)
                if line.type != "action" and len(hits) >= 3:
                    wc = set(content([t.syl for t in toks[a:b + 1]]))
                    prec = len(hits) / max(1, len(wc))
                    cov = max(cov, 2 * cov * prec / (cov + prec))   # short paraphrase of a long script line
                r = memo[(a, b)] = (hits, cov, len(hits) >= min_hits and cov >= min_cov)
            return r
        single_ok = [eval_(a, b)[2] for a, b in segs]
        wins = [seg for seg, ok in zip(segs, single_ok) if ok]
        # a 2-segment window only when neither half matches alone (line spoken across a pause)
        wins += [(segs[k][0], segs[k + 1][1]) for k in range(len(segs) - 1)
                 if not single_ok[k] and not single_ok[k + 1]
                 and toks[segs[k + 1][0]].t0 - toks[segs[k][1]].t1 < 1.5]
        for a, b in wins:
            hits, cov, ok = eval_(a, b)
            if not ok:
                continue
            at = Attempt(line.id, f, a, b, 0, 0, [], cov, 1.0, True, True)
            at.flags.append(f"soft match {len(hits)}/{len(C)}: {' '.join(hits)}")
            at._soft = True
            set_times(at, toks, cfg)
            at.director = director_cue(toks, at.i1, cfg)
            at.shake = shake_overlap(shake.get(f, {}).get("ranges", []), at.t_in, at.t_out)
            out.append(at)
    # keep the best window per (file, start segment) region
    out.sort(key=lambda x: (-x.coverage, x.i1 - x.i0))
    kept = []
    for at in out:
        if not any(k.file == at.file and not (at.i1 < k.i0 or at.i0 > k.i1) for k in kept):
            kept.append(at)
    return kept


def block_coherence(lines, cands, cfg):
    """Lines of one dialogue block should come from the same clip (same take, same person, same framing)."""
    blocks = {}
    for ln in lines:
        if ln.type in ("dialogue", "question") and cands.get(ln.id):
            blocks.setdefault(ln.block, []).append(ln)
    for blk, lns in blocks.items():
        if len(lns) < 2:
            continue
        per_file = {}
        for ln in lns:
            best = {}
            for a in cands[ln.id]:
                best[a.file] = max(best.get(a.file, 0), a.score)
            for f, v in best.items():
                per_file.setdefault(f, []).append(v)
        f_best, vals = max(per_file.items(), key=lambda kv: (sum(kv[1]), len(kv[1])))
        if len(vals) / len(lns) < 0.6:
            continue
        for ln in lns:
            for a in cands[ln.id]:
                if a.file == f_best:
                    a.score = round(a.score + cfg["block_bonus"], 4)
                    if "block: cùng clip" not in a.flags:
                        a.flags.append("block: cùng clip với cả đoạn")
            cands[ln.id].sort(key=lambda x: -x.score)


def _day(media, f):
    return (media.get(f, {}).get("created") or "")[:10]


def day_coherence(lines, cands, chosen, media, cfg):
    """Boost takes shot on the same day as most of the first-pass picks (same candidate, same look)."""
    scene_of = {ln.id: ln.scene for ln in lines}
    by_scene = {}
    for lid, a in chosen.items():
        if _day(media, a.file):
            by_scene.setdefault(scene_of[lid], []).append(_day(media, a.file))
    for lid, lst in cands.items():
        days = by_scene.get(scene_of[lid])
        if not days:
            continue
        major = Counter(days).most_common(1)[0][0]      # ties → first seen (max(set(...)) was hash-order dependent)
        for a in lst:
            if _day(media, a.file) == major:
                a.score = round(a.score + cfg["day_bonus"], 4)
        lst.sort(key=lambda x: -x.score)


_CUES = None


def _cue_seqs():
    global _CUES
    if _CUES is None:
        _CUES = sorted({tuple(syllables(c)) for c in NG_CUES + OK_CUES + BOUNDARY_CUES} - {()}, key=len, reverse=True)
    return _CUES


def cue_marks(toks, a, b, iso_gap=0.3):
    """Indices in toks[a..b] spoken as a director cue (NG / OK / slate: 'làm lại', 'được rồi', 'cắt', 'action' …).
    One-syllable cues ('cắt', 'diễn', 'khoan') are everyday words too: they count only when set off by a pause or a
    segment edge, the way a director calls them."""
    marks = set()
    b = min(b, len(toks) - 1)
    for j in range(max(0, a), b + 1):
        for seq in _cue_seqs():
            n = len(seq)
            if j + n - 1 > b or tuple(t.syl for t in toks[j:j + n]) != seq:
                continue
            if n == 1:
                gb = toks[j].t0 - toks[j - 1].t1 if j > 0 and toks[j - 1].seg == toks[j].seg else 9.0
                ga = toks[j + 1].t0 - toks[j].t1 if j + 1 < len(toks) and toks[j + 1].seg == toks[j].seg else 9.0
                if max(gb, ga) < iso_gap:
                    continue
            marks.update(range(j, j + n))
            break
    return marks


def _restarts(toks, j, i0, i1, k=2):
    """toks[j:j+k] repeats k consecutive syllables already inside toks[i0..i1] (the actor starting the line again)."""
    if j + k > len(toks):
        return False
    probe = [strip_diacritics(t.syl) for t in toks[j:j + k]]
    seen = [strip_diacritics(t.syl) for t in toks[i0:i1 + 1]]
    return any(seen[p:p + k] == probe for p in range(len(seen) - k + 1))


def capture_replies(lines, chosen, cands, clips, cfg):
    """Extend question / action picks through the unscripted reply that follows — up to capture_max_s seconds /
    capture_max_syl syllables, and never through a director cue (NG / OK / slate)."""
    def stops_for(lid):
        st = {}
        for ln in lines:
            if ln.id == lid:
                continue
            if ln.id in chosen:
                for f, i0, *_ in spans(chosen[ln.id]):
                    if i0 is not None:
                        st.setdefault(f, set()).add(i0)
            else:  # unassigned line with a strong match: don't swallow it
                for a in cands.get(ln.id, []):
                    if a.score >= 0.8:
                        st.setdefault(a.file, set()).add(a.i0)
        return st

    max_s, max_n = float(cfg.get("capture_max_s", 30.0)), int(cfg.get("capture_max_syl", 120))
    cap_lines = [ln for ln in lines if (ln.capture or ln.type == "action") and ln.id in chosen]
    last_id = cap_lines[-1].id if cap_lines else None
    for ln in cap_lines:
        at = chosen[ln.id]
        if at.parts:
            continue                  # stitched picks play their parts only
        toks = clips[at.file]
        gap_max = cfg["tail_gap"] if ln.id == last_id else cfg["capture_gap"]
        stops = stops_for(ln.id).get(at.file, set())
        t_end = toks[at.i1].t1
        cues = cue_marks(toks, at.i1 + 1, min(len(toks) - 1, at.i1 + max_n + 4))
        j = at.i1
        while (j + 1 < len(toks) and toks[j + 1].t0 - toks[j].t1 <= gap_max and (j + 1) not in stops
               and (j + 1) not in cues and toks[j + 1].t1 - t_end <= max_s and j + 1 - at.i1 <= max_n):
            j += 1
        if j > at.i1:
            at.flags.append(f"+ trả lời/tiếp nối {toks[at.i1].t1:.1f}→{toks[j].t1:.1f}s")
            at.i1 = j
            set_times(at, toks, cfg)


def snap_to_utterance(chosen, clips, cfg, max_tail=12, max_head=4):
    """Never cut an actor mid-utterance: extend picks to the whisper segment edges (short tails/heads only).
    The extension stops at a director cue ('làm lại', 'ok được rồi', 'cắt') and at a restart of words the pick
    already holds ('… hai mươi à em hai mươi tuổi'): those belong to the next attempt, not to this utterance."""
    occ = {}
    for a in chosen.values():
        for f, i0, i1, *_ in spans(a):
            if i0 is not None:
                occ.setdefault(f, []).append((i0, i1, a.line))
    for a in chosen.values():
        if a.parts:
            continue
        toks = clips[a.file]
        others = [s for s in occ.get(a.file, []) if s[2] != a.line]
        taken = lambda j: any(s0 <= j <= s1 for s0, s1, _ in others)
        seg_end = a.i1
        while seg_end + 1 < len(toks) and toks[seg_end + 1].seg == toks[a.i1].seg:
            seg_end += 1
        seg_start = a.i0
        while seg_start - 1 >= 0 and toks[seg_start - 1].seg == toks[a.i0].seg:
            seg_start -= 1
        cues = cue_marks(toks, seg_start, seg_end)
        end = a.i1
        for j in range(a.i1 + 1, seg_end + 1):
            if j in cues:
                break
            if _restarts(toks, j, a.i0, a.i1, 3 if a.i1 - a.i0 >= 7 else 2):   # longer pick: 2-word repeats are chance
                end = a.i1                # the rest of the segment is a new attempt → keep the pick as aligned
                break
            end = j
        if 0 < end - a.i1 <= max_tail and not any(taken(j) for j in range(a.i1 + 1, end + 1)):
            a.flags.append(f"snap: giữ hết câu nói đến {toks[end].t1:.2f}s")
            a.i1 = end
        start = a.i0
        for j in range(a.i0 - 1, seg_start - 1, -1):
            if j in cues:
                break
            start = j
        if 0 < a.i0 - start <= max_head and not any(taken(j) for j in range(start, a.i0)):
            a.i0 = start
        set_times(a, toks, cfg)


def try_stitch(line, atts, cfg, clips=None):
    """No complete take? Build the line from 2–3 partial takes (Head -> [Middle] -> Tail).

    Each take attempt is an edge from script syllable i to j. chains(s, depth) = the best (≤ STITCH_TOPK) chains
    that finish the line after syllable s, memoised on (s, depth) only — the old key also held every span already
    visited, which made the search exponential in the number of takes. Callers drop chains that collide in time
    with takes already in their own chain."""
    m = len(line.syl)
    if m < 4 or not atts:
        return None
    if any(complete_take(a) for a in atts):
        return None                   # a full clean take exists: a stitched line (jump cut) is never better
    best = atts[0]
    if best.first_ok and best.last_ok and best.coverage >= 0.85:
        return None
    valid_atts = [a for a in atts if a.pairs and len(a.pairs) >= 2 and not a.parts]
    if not valid_atts:
        return None
    max_depth = int(cfg.get("stitch_max_depth", 3))  # up to 3 takes in a chain (Head, Middle, Tail)
    end_at = m - 1 - (int(0.12 * m) if m > 6 else 0)
    first_p = {id(a): min(p[0] for p in a.pairs) for a in valid_atts}

    def collide(x, y):
        return x.file == y.file and min(x.t_out, y.t_out) - max(x.t_in, y.t_in) > 0.1

    memo = {}

    def chains(s0, depth):
        if depth > max_depth:
            return []
        key = (s0, depth)
        if key in memo:
            return memo[key]
        res = []
        for a in valid_atts:
            pa = [p for p in a.pairs if p[0] > s0]
            if not pa or first_p[id(a)] > s0 + 1:      # must advance; at most 1 missing syllable at the joint
                continue
            max_p = max(p[0] for p in pa)
            step = float(np.mean([p[2] for p in pa])) * (len(pa) / m) - 0.04   # accuracy × length − cut penalty
            rng = (a.file, pa[0][1], pa[-1][1])
            if max_p >= end_at:
                res.append((step, [a], pa, [rng]))
            else:
                for sc, ch, prs, rgs in chains(max_p, depth + 1):
                    if not any(collide(a, c) for c in ch):
                        res.append((step + sc, [a] + ch, pa + prs, [rng] + rgs))
        res.sort(key=lambda r: -r[0])
        memo[key] = res = res[:STITCH_TOPK]
        return res

    # Explore from all starting takes (first_ok or starts near 0)
    heads = [a for a in valid_atts if a.first_ok or first_p[id(a)] <= 1]
    best_overall = None
    for h in heads:
        cut_points = sorted({p[0] for p in h.pairs if p[0] >= 1}, reverse=True)[:3]
        for s0 in cut_points:
            sub = next((c for c in chains(s0, 1) if not any(collide(h, x) for x in c[1])), None)
            if sub is None:
                continue
            total_chain = [h] + sub[1]
            head_pairs = [p for p in h.pairs if p[0] <= s0]
            all_pairs = head_pairs + sub[2]
            cov = len({p[0] for p in all_pairs}) / m
            if cov >= 0.88:
                avg_score = (h.score + sum(c.score for c in sub[1])) / len(total_chain) + 0.15 * cov
                h_tok_end = head_pairs[-1][1] if head_pairs else h.pairs[-1][1]
                ranges = [(h.file, h.pairs[0][1], h_tok_end)] + sub[3]
                if best_overall is None or avg_score > best_overall[0]:
                    best_overall = (avg_score, total_chain, all_pairs, cov, ranges)
    if not best_overall:
        return None

    sc, chain, all_pairs, st_cov, ranges = best_overall
    if best.first_ok and best.last_ok or st_cov < best.coverage + cfg.get("stitch_min_gain", 0.20):
        return None
    sc = max(sc, best.score + 0.01)

    # clean slice per part; token range kept per part (parts may come from different clips)
    parts = []
    pad_in, pad_out = cfg.get("pad_in", 0.12), cfg.get("pad_out", 0.20)
    for k, (c, (f, tok_i0, tok_i1)) in enumerate(zip(chain, ranges)):
        toks = (clips or {}).get(f)
        if toks and tok_i0 < len(toks) and tok_i1 < len(toks):
            t0 = max(0.0, toks[tok_i0].t0 - (pad_in if k == 0 else 0.06))
            t1 = toks[tok_i1].t1 + (pad_out if k == len(chain) - 1 else 0.06)
        else:
            t0, t1 = c.t_in, c.t_out
        excl = [e for e in c.exclude if t0 <= e[0] <= t1]
        parts.append({"file": f, "in": round(t0, 3), "out": round(max(t1, t0), 3), "exclude": excl, "i0": int(tok_i0),
                      "i1": int(tok_i1), "label_suffix": f" [{chr(65 + k)}]", "take": c.take_no})

    # the Attempt itself stands for its head clip: in/out and token range = span of the parts in that clip
    # (a later part from an EARLIER moment, or another clip, must never give t_in > t_out / foreign token indices)
    h = chain[0]
    own = [p for p in parts if p["file"] == h.file]
    exact = float(np.mean([p[2] for p in all_pairs]))
    st = Attempt(line.id, h.file, min(p["i0"] for p in own), max(p["i1"] for p in own),
                 min(p["in"] for p in own), max(p["out"] for p in own), all_pairs, min(1.0, st_cov), exact, True, True)
    st.score = round(sc, 4)
    st.parts = parts
    st._tail = chain[-1]
    st._chain = chain
    take_names = " + ".join(f"{c.file} take{c.take_no}" for c in chain)
    st.flags.append(f"STITCH đệ quy ({len(chain)} đoạn): {take_names} → cần B-roll che jump cut")
    st.exclude = [e for p in parts for e in p["exclude"]]
    return st


def spans(at):
    """[(file, tok_i0, tok_i1, t_in, t_out)] of the audio a pick puts on the timeline — one per stitched part."""
    if at.parts and isinstance(at.parts[0], dict):
        return [(p["file"], p.get("i0"), p.get("i1"), p["in"], p["out"]) for p in at.parts]
    return [(at.file, at.i0, at.i1, at.t_in, at.t_out)]


def overlaps(a, b):
    a_spans = [(p["file"], p["in"], p["out"]) for p in a.parts] if (getattr(a, "parts", None) and isinstance(a.parts, list) and a.parts and isinstance(a.parts[0], dict)) else [(a.file, a.t_in, a.t_out)]
    b_spans = [(p["file"], p["in"], p["out"]) for p in b.parts] if (getattr(b, "parts", None) and isinstance(b.parts, list) and b.parts and isinstance(b.parts[0], dict)) else [(b.file, b.t_in, b.t_out)]
    for f_a, in_a, out_a in a_spans:
        for f_b, in_b, out_b in b_spans:
            if f_a == f_b and min(out_a, out_b) - max(in_a, in_b) > 0.2:
                return True
    return False


def assign(lines, cands, cfg):
    """Greedy global assignment: best (line, attempt) pairs first, no reused audio."""
    block_of = {ln.id: ln.block for ln in lines}
    pool = [(at.score, ln.id, at) for ln in lines for at in cands.get(ln.id, [])]
    pool.sort(key=lambda x: -x[0])
    chosen, used = {}, []
    for sc, lid, at in pool:
        if lid in chosen or any(overlaps(at, u) for u in used):   # one piece of audio serves one script line only
            continue
        chosen[lid] = at
        used.append(at)
    # context pass: short lines prefer the attempt right after the previous line's pick
    order = [ln for ln in lines if ln.type in ("dialogue", "question")]
    for k, ln in enumerate(order):
        if len(ln.syl) > 4 or k == 0 or ln.id not in chosen:
            continue
        prev = chosen.get(order[k - 1].id)
        if not prev:
            continue
        near = [a for a in cands[ln.id] if a.file == prev.file and 0 <= a.t_in - prev.t_out <= 10
                and not any(overlaps(a, u) for u in used if u is not chosen[ln.id])]
        if near and near[0].score >= chosen[ln.id].score - 0.15 and near[0] is not chosen[ln.id]:
            used.remove(chosen[ln.id])
            chosen[ln.id] = near[0]
            used.append(near[0])
            near[0].flags.append("context: chọn take liền sau câu trước")
    return chosen


def align(script_path, src, cfg=None, progress=print, lines=None):
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    lines = lines if lines is not None else parse_script(script_path)
    notes = []
    clips, media, shake = load_work(src, notes)
    for n_ in notes:
        progress("⚠ " + n_)
    shake_raw = shake
    has_dialogue = any(ln.type in ("dialogue", "question") for ln in lines)
    w_dir = os.path.join(src, "_nexys_work")
    has_transcripts = os.path.exists(os.path.join(w_dir, "transcripts")) or os.path.exists(os.path.join(w_dir, "speech.json"))
    if not clips:
        if has_dialogue and not has_transcripts:
            raise SystemExit("Chưa phân tích source — chạy `nxcut.py probe` + `transcribe` trước.")
        elif has_dialogue:
            progress("⚠ Source không có câu thoại nào được nhận diện — các câu thoại sẽ được chừa chỗ trống trên timeline.")
        else:
            progress("• Kịch bản dạng montage hình ảnh / B-roll (không có thoại) — tiến hành xếp timeline theo cảnh và nguồn video.")
    sim = SimCache()
    vs = VocabSim(clips)
    cands = {}
    for ln in lines:
        if ln.type not in ("dialogue", "question", "action"):
            continue
        atts = find_attempts(ln, clips, shake, cfg, sim, vs) if ln.type != "action" else []
        weak = not atts or max(a.coverage for a in atts) < 0.7
        if ln.type == "action" and not ln.speaker:
            cands[ln.id] = []          # visual-only coverage note -> b-roll request
            continue
        if ln.type in ("question", "action") or weak:
            soft = soft_attempts(ln, clips, shake, cfg, sim, vs)
            _number_takes(soft)
            for a in soft:
                score_attempt(a, ln, cfg)
                if ln.type == "dialogue":
                    a.score = round(a.score - cfg["soft_dialogue_penalty"], 4)
            atts = sorted(atts + [a for a in soft if not any(overlaps(a, b) for b in atts)], key=lambda x: -x.score)
        if ln.type == "dialogue":
            st = try_stitch(ln, atts, cfg, clips=clips)
            if st:
                atts.insert(0, st)
        cands[ln.id] = atts
        progress(f"{ln.id} {len(atts):2d} take  {ln.text[:50]}")
    # near-tie between takes of the same line → prefer the steadier / better-framed take (nxcore.stability v2)
    for lid, atts in cands.items():
        for at in atts:
            q = (shake_raw.get(at.file) or {}).get("quality")
            if q is not None and (shake_raw.get(at.file) or {}).get("v", 0) >= 2:
                at.score = round(at.score + 0.04 * (float(q) - 60) / 40.0, 4)
                if q < 45:
                    at.flags.append(f"take rung/bố cục yếu (chất lượng {q})")
        atts.sort(key=lambda a: -a.score)
    # script pins ("Nguồn: FILE @ a–b" written by the AI script / editor) → that take wins near-ties
    from .media import resolve_loose
    byid = {ln.id: ln for ln in lines}
    for lid, atts in cands.items():
        ln = byid.get(lid)
        pins = [(resolve_loose(p.get("file"), media), p.get("t")) for p in (getattr(ln, "src", None) or [])]
        pins = [(f, t) for f, t in pins if f]
        if not pins:
            continue
        for at in atts:
            for f, t in pins:
                if at.file == f:
                    bonus = cfg.get("pin_bonus", 0.15)
                    if t and min(at.t_out, t[1] + 1) - max(at.t_in, t[0] - 1) > 0:
                        bonus += 0.05
                    at.score = round(at.score + bonus, 4)
                    at.flags.append("đúng nguồn kịch bản")
                    break
        atts.sort(key=lambda a: -a.score)
    chosen = assign(lines, cands, cfg)              # first pass: which shooting day dominates each scene
    day_coherence(lines, cands, chosen, media, cfg)
    block_coherence(lines, cands, cfg)              # then keep each dialogue block inside one clip
    chosen = assign(lines, cands, cfg)
    capture_replies(lines, chosen, cands, clips, cfg)
    snap_to_utterance(chosen, clips, cfg)
    return lines, cands, chosen, clips, media


# ----------------------------------------------------------------------------- plan + report
def build_plan(lines, cands, chosen, clips, media, src, base=None, name=None):
    base = dict(base or {})
    srcn = src.replace("\\", "/").rstrip("/")
    folder = os.path.basename(srcn).replace(" ", "_")
    P = {
        "name": name or base.get("name") or f"{folder}_SCRIPT_9x16_v1",
        "bin": base.get("bin", folder), "src": srcn,
        "out": base.get("out", srcn + "/_nexys_out"),
        "seq": base.get("seq", {"w": 1080, "h": 1920, "fps": 29.97}),
        "silence": base.get("silence", {"on": True, "gap": 0.35, "pad_in": 0.08, "pad_out": 0.15, "min_piece": 0.4}),
        "shake": base.get("shake", {"on": True}),
        "subs": base.get("subs", {"on": True, "max_chars": 28, "replace": {}}),
        "sfx": base.get("sfx", {"on": False}),
        "highlights_on": base.get("highlights_on", True),
        "scene_names": base.get("scene_names", {}),
        "end_slate": base.get("end_slate", 0),
        "segments": [], "overlays": base.get("overlays", []), "highlights": [],
        "missing": [], "broll_requests": [], "vo_lines": [],
    }
    for ln in lines:
        P["scene_names"].setdefault(ln.scene, ln.scene_title)

    def add_seg(d):
        """Split a segment around its exclude ranges (fillers/stutters) → hard cuts nxcut understands."""
        ex = [e for e in d.pop("exclude", []) if d["in"] < e[1] and e[0] < d["out"]]
        cur, first = d["in"], True
        for a, b in ex + [[d["out"], d["out"]]]:
            if a - cur >= 0.15:
                seg = dict(d, **{"in": round(cur, 3), "out": round(a, 3)})
                if not first:
                    seg["label"] = "↳ cut vấp/filler"
                P["segments"].append(seg)
                first = False
            cur = max(cur, b)

    for ln in lines:
        if ln.type == "broll":
            P["broll_requests"].append({"line": ln.id, "scene": ln.scene, "desc": ln.text})
            continue
        if ln.type == "vo":
            P["vo_lines"].append({"line": ln.id, "scene": ln.scene, "text": ln.text})
            continue
        at = chosen.get(ln.id)
        if not at:
            P["missing"].append({"line": ln.id, "scene": ln.scene, "text": ln.text})
            continue
        label = (f"{ln.speaker}: " if ln.speaker else "") + ln.text
        common = {"scene": ln.scene, "script_line": ln.id, "score": at.score, "flags": at.flags,
                  "candidates": [a.brief() for a in cands[ln.id][:4]]}
        if at.parts:  # stitched
            seg_first = len(P["segments"])
            if isinstance(at.parts[0], dict):
                for k, pt in enumerate(at.parts):
                    suffix = pt.get("label_suffix", f" [{chr(65 + k)}]")
                    pt_label = (label + suffix) if k == 0 else f"↳ {suffix.strip()}"
                    pt_flags = list(at.flags)
                    pt_flags.append(f"stitch đoạn {k + 1}/{len(at.parts)} ({pt['file']} take{pt.get('take', 1)})")
                    pt_common = dict(common, flags=pt_flags)
                    add_seg({"file": pt["file"], "in": pt["in"], "out": pt["out"], "label": pt_label,
                             "exclude": pt.get("exclude", []), **pt_common})
            else:
                h, t = at.parts[0], at._tail
                toks = clips[t.file]
                t_in2 = round(max(toks[at.parts[1][3]].t0 - 0.08, 0), 3)
                add_seg({"file": h[0], "in": h[1], "out": h[2], "label": label + " [A]",
                         "exclude": [e for e in at.exclude if h[1] <= e[0] <= h[2]], **common})
                add_seg({"file": t.file, "in": t_in2, "out": t.t_out, "label": "↳ [B]",
                         "exclude": [e for e in t.exclude if e[0] >= t_in2], **common})
        else:
            seg_first = len(P["segments"])
            add_seg({"file": at.file, "in": at.t_in, "out": at.t_out, "label": label,
                     "exclude": at.exclude, **common})
        seg_idx = seg_first
        for kw in ln.keywords:
            P["highlights"].append({"seg": seg_idx, "match": kw, "text": kw.upper(), "style": "yellow", "dur": 1.6})
    return P


def write_report(P, lines, cands, path):
    by, pieces = {}, {}
    for s in P["segments"]:
        by.setdefault(s["script_line"], s)
        pieces.setdefault(s["script_line"], []).append(s)
    with open(path, "w", encoding="utf-8") as fh:
        n_d = sum(1 for l in lines if l.type == "dialogue")
        fh.write(f"# Align report — {P['name']}\n\n")
        fh.write(f"Dialogue lines: {n_d} · matched: {n_d - len(P['missing'])} · missing: {len(P['missing'])} · "
                 f"b-roll requests: {len(P['broll_requests'])} · VO: {len(P['vo_lines'])}\n\n")
        fh.write("| Line | Scene | Text | Pick | Score | Takes | Notes |\n|---|---|---|---|---|---|---|\n")
        for ln in lines:
            if ln.type != "dialogue":
                continue
            s = by.get(ln.id)
            txt = ln.text[:48].replace("|", "/")
            if not s:
                fh.write(f"| {ln.id} | {ln.scene} | {txt} | — | — | {len(cands.get(ln.id, []))} | ❌ MISSING |\n")
                continue
            notes = []
            ps = pieces[ln.id]
            cuts = sum(1 for x in ps if x["label"].startswith("↳ cut"))
            if cuts:
                notes.append(f"cắt {cuts} vấp/filler")
            notes += s.get("flags", [])
            c0 = s["candidates"][0] if s["candidates"] else {}
            if c0.get("director"):
                notes.append(c0["director"])
            spans = []
            for x in ps:
                if spans and x["label"].startswith("↳ cut"):
                    spans[-1][2] = x["out"]
                else:
                    spans.append([x["file"], x["in"], x["out"]])
            pick = " + ".join("%s %.2f–%.2f" % tuple(sp) for sp in spans)
            if s["score"] < 0.6:
                notes.append("⚠ điểm thấp — review")
            fh.write(f"| {ln.id} | {ln.scene} | {txt} | {pick} | {s['score']:.2f} | "
                     f"{len(cands[ln.id])} | {'; '.join(notes)} |\n")
        if P["broll_requests"]:
            fh.write("\n## B-roll cần tìm / generate\n" + "\n".join(f"- {b['line']} ({b['scene']}): {b['desc']}" for b in P["broll_requests"]) + "\n")


def main():
    ap = argparse.ArgumentParser(description="Script-driven take selection → nxcut plan.json")
    ap.add_argument("--src", required=True, help="folder source (đã chạy nxcut probe + transcribe [+ shake])")
    ap.add_argument("--script", required=True, help=".docx / .txt / .md")
    ap.add_argument("--plan", required=True, help="plan.json output")
    ap.add_argument("--base", default="", help="plan mẫu để copy seq/subs/sfx/scene_names")
    ap.add_argument("--name", default="")
    ap.add_argument("--cfg", default="", help="JSON override DEFAULT_CFG")
    a = ap.parse_args()
    cfg = json.load(open(a.cfg, encoding="utf-8")) if a.cfg else None
    lines, cands, chosen, clips, media = align(a.script, a.src, cfg, progress=lambda *_: None)
    base = json.load(open(a.base, encoding="utf-8")) if a.base else None
    P = build_plan(lines, cands, chosen, clips, media, a.src, base, a.name or None)
    os.makedirs(os.path.dirname(os.path.abspath(a.plan)), exist_ok=True)
    json.dump(P, open(a.plan, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    rp = os.path.splitext(a.plan)[0] + "_align.md"
    write_report(P, lines, cands, rp)
    print(open(rp, encoding="utf-8").read())
    print("plan:", a.plan)


if __name__ == "__main__":
    main()
