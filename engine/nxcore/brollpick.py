"""B-roll picking v2: one best shot per script line, no near-duplicate takes, best moment inside the clip.

Why: v1 dumped every clip whose tags touched a scene (up to 12) into that scene, so similar takes/static angles
stacked up ("trùng lặp") and specific shot descriptions in the script were ignored.

Pipeline:
  1. pool      = clips with tags or AI description, minus `use_as: skip`, too-short or mostly-bad (shake/QC) clips,
                 minus clips already used for dialogue.
  2. descriptor= tags + AI summary + shot size (+ best_broll/moments text when present).
  3. groups    = near-duplicate takes: shot back-to-back (≤ 150 s apart) AND similar descriptor → one group.
  4. score     = TF-IDF cosine(line text, descriptor) over syllables+bigrams (generic words like "cà phê" weigh
                 little), + shot-size agreement (cận/đặc tả/toàn/trung), + quality, − "dialogue" clips (BTS talk).
  5. assign    = global greedy on (line, clip): each line gets its best clip, each clip AND each group used once.
                 Optional 2nd shot per line (cfg broll_per_line / target duration) from a different group.
  6. window    = inside the AI `use` range (best_broll) when present, length by content (static 2.0 s, action ≤ 3.5 s),
                 avoiding shake/QC ranges.
"""
import math
import re
import unicodedata
from collections import Counter

from .textnorm import strip_diacritics

# Shot size / action / static keywords are matched as WHOLE WORDS on the accented text: substring matching on
# diacritic-free text read "Cần Thơ" as close-up ("can "), "đó/độ/đỏ" as an action ("do "), "tỉnh/tính" as static.
# Text typed without any diacritics falls back to the stripped multi-word forms (single stripped syllables are
# too ambiguous to trust).
SIZE_WORDS = {
    "close": ("cận cảnh", "cận", "đặc tả", "chi tiết", "macro", "close", "close up", "closeup", "extreme close"),
    "wide": ("toàn cảnh", "góc rộng", "quét ngang", "flycam", "wide", "khung cảnh", "drone", "toàn thân"),
    "medium": ("trung cảnh", "medium", "nửa người", "cowboy"),
}
SIZE_LABELS = {"cận": "close", "toàn": "wide", "trung": "medium", "rộng": "wide"}   # one-word shot labels (AI "shot", tags)
ACTION_WORDS = ("chạy", "chảy", "rót", "đổ", "di chuyển", "quét", "pha", "nhấc", "xịt", "phun", "tưới", "gắn", "bấm",
                "tạo hình", "khuấy", "xoay", "pour", "running", "walking")
STATIC_WORDS = ("tĩnh", "tĩnh vật", "tĩnh lặng", "cố định", "đứng yên", "góc máy", "static")
SIZE_TOK = set("can canh dac ta toan trung goc quay shot".split())   # removed 'may' so 'máy pha/máy tính' aren't stripped
STOP = set("va voi cua cho trong tren duoi tu vao ra mot nhung cac la co duoc nay kia do day thi ma se dang rat lai".split())

SYNONYMS = {
    # F&B / Coffee / Drink
    "cafe": "syn_coffee", "ca phe": "syn_coffee", "coffee": "syn_coffee", "espresso": "syn_coffee",
    "latte": "syn_coffee", "cappuccino": "syn_coffee", "barista": "syn_coffee", "pha che": "syn_coffee",
    "pour": "syn_pour",
    "tra": "syn_tea", "tea": "syn_tea", "tra sua": "syn_tea", "milk tea": "syn_tea",
    "do uong": "syn_drink", "thuc uong": "syn_drink", "drink": "syn_drink", "nuoc": "syn_drink",
    "ly": "syn_cup", "tach": "syn_cup", "coc": "syn_cup", "cup": "syn_cup", "glass": "syn_cup",
    "sua": "syn_milk", "milk": "syn_milk", "kem": "syn_milk", "cream": "syn_milk",

    # Food / Dining / Cooking
    "mon an": "syn_food", "am thuc": "syn_food", "food": "syn_food", "dish": "syn_food",
    "dia": "syn_dish", "ban an": "syn_table", "nha hang": "syn_restaurant", "quan an": "syn_restaurant",
    "bep": "syn_cook", "nau": "syn_cook", "che bien": "syn_cook", "cook": "syn_cook", "chef": "syn_cook",

    # Work / Tech / Office
    "may tinh": "syn_pc", "laptop": "syn_pc", "computer": "syn_pc", "pc": "syn_pc",
    "man hinh": "syn_screen", "screen": "syn_screen", "display": "syn_screen",
    "code": "syn_code", "lap trinh": "syn_code", "coding": "syn_code", "developer": "syn_code",
    "dien thoai": "syn_phone", "smartphone": "syn_phone", "phone": "syn_phone",
    "van phong": "syn_office", "office": "syn_office", "cong so": "syn_office", "cong ty": "syn_office",
    "ban lam viec": "syn_desk", "desk": "syn_desk", "hop": "syn_meeting", "meeting": "syn_meeting",

    # Travel / Nature / Landscape
    "du lich": "syn_travel", "travel": "syn_travel", "trip": "syn_travel", "tour": "syn_travel",
    "phong canh": "syn_landscape", "landscape": "syn_landscape", "khung canh": "syn_landscape",
    "bien": "syn_sea", "beach": "syn_sea", "sea": "syn_sea", "ocean": "syn_sea",
    "nui": "syn_mountain", "mountain": "syn_mountain", "doi": "syn_mountain",
    "hoang hon": "syn_sunset", "sunset": "syn_sunset", "binh minh": "syn_sunrise", "sunrise": "syn_sunrise",
    "cay": "syn_tree", "rung": "syn_forest", "forest": "syn_forest", "troi": "syn_sky", "sky": "syn_sky",

    # City / Traffic / Urban
    "duong pho": "syn_street", "pho": "syn_street", "street": "syn_street", "city": "syn_city",
    "do thi": "syn_city", "thanh pho": "syn_city", "toa nha": "syn_building", "building": "syn_building",
    "xe": "syn_vehicle", "o to": "syn_car", "car": "syn_car", "xe hoi": "syn_car",
    "xe may": "syn_moto", "motorbike": "syn_moto", "giao thong": "syn_traffic", "traffic": "syn_traffic",

    # People / Emotions / Action
    "nhan vat": "syn_people", "nguoi": "syn_people", "person": "syn_people", "people": "syn_people",
    "nu cuoi": "syn_smile", "cuoi": "syn_smile", "smile": "syn_smile", "vui": "syn_smile",
    "noi chuyen": "syn_talk", "talk": "syn_talk", "tro chuyen": "syn_talk", "phong van": "syn_interview",
    "interview": "syn_interview", "di bo": "syn_walk", "walk": "syn_walk", "run": "syn_run",
    "tay": "syn_hand", "hand": "syn_hand", "chan dung": "syn_portrait", "portrait": "syn_portrait",

    # Stage / Event
    "su kien": "syn_event", "event": "syn_event", "san khau": "syn_stage", "stage": "syn_stage",
    "khan gia": "syn_crowd", "crowd": "syn_crowd", "hoi truong": "syn_event",
    "anh sang": "syn_light", "light": "syn_light", "am thanh": "syn_sound", "sound": "syn_sound",
}

# Short Vietnamese words that collide once diacritics are stripped (chảy/chạy, rừng/rung, phố/phở, đồi/đội,
# họp/hộp, trà/trả, cây/cay, biển/biến, trời/trôi, tách/tách-rời…). They match ONLY on the accented form;
# ASCII-only text skips them instead of guessing (wrong concept tags pull the wrong B-roll).
SYN_ACCENT = {
    "rót": "syn_pour", "đổ": "syn_pour", "chảy": "syn_pour",
    "chạy": "syn_run",
    "trà": "syn_tea", "tách": "syn_cup", "đĩa": "syn_dish",
    "phố": "syn_street", "đồi": "syn_mountain", "cây": "syn_tree", "rừng": "syn_forest",
    "trời": "syn_sky", "biển": "syn_sea", "tay": "syn_hand", "họp": "syn_meeting",
}
for _k in {strip_diacritics(k) for k in SYN_ACCENT}:
    SYNONYMS.pop(_k, None)
del _k


# stop / shot-size words on the ACCENTED form ('có' is a stop word, 'cổ' in "phố cổ" is not; 'cận' is a shot size,
# 'cần' in "Cần Thơ" is not); a word typed without diacritics falls back to the stripped lists
STOP_ACC = set("và với của cho trong trên dưới từ vào ra một những các là có được này kia đó đây thì mà sẽ đang rất lại".split())
SIZE_TOK_ACC = set("cận cảnh đặc tả toàn trung góc quay shot".split())


def _stop(x):
    if x in STOP_ACC or x in SIZE_TOK_ACC:
        return True
    return strip_diacritics(x) == x and (x in STOP or x in SIZE_TOK)


def _norm_acc(s):
    """Lower-case NFC text with diacritics kept, punctuation → space."""
    return re.sub(r"[^\w ]+", " ", unicodedata.normalize("NFC", str(s)).lower())


def _norm(s):
    return " " + re.sub(r"[^a-z0-9 ]+", " ", strip_diacritics(str(s)).lower()).strip() + " "


def _kw_re(words, strip=False):
    if strip:   # English words + multi-syllable phrases only: 'can', 'do', 'pha', 'tinh' without accents mean too much
        words = [w for w in words if strip_diacritics(w) == w or " " in w]
    ws = sorted({strip_diacritics(w) if strip else w for w in words}, key=len, reverse=True)
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(w) for w in ws) + r")(?!\w)") if ws else None


_SIZE_RE = {k: (_kw_re(v), _kw_re(v, True)) for k, v in SIZE_WORDS.items()}
_ACTION_RE = (_kw_re(ACTION_WORDS), _kw_re(ACTION_WORDS, True))
_STATIC_RE = (_kw_re(STATIC_WORDS), _kw_re(STATIC_WORDS, True))


def _has(res, text):
    """Whole-word keyword test: accented text → accented words; text without any diacritics → stripped phrases."""
    acc = " ".join(_norm_acc(text).split())
    st = strip_diacritics(acc)
    r = res[1] if st == acc else res[0]
    return bool(r and r.search(acc))


def _feats(s):
    """Features for TF-IDF: accented words + bigrams (primary — 'phố' ≠ 'phở'), the same words without diacritics as
    '~'-features (weighted 0.5 in Picker._weight: tolerant of text typed without accents), concept tags ×2."""
    norm_txt = _norm(s)
    raw_words = norm_txt.split()
    acc = [x for x in _norm_acc(s).split() if not _stop(x)]
    w = [strip_diacritics(x) for x in acc]
    bigrams = [a + "_" + b for a, b in zip(acc, acc[1:])]
    loose = ["~" + x for x in w] + ["~" + a + "_" + b for a, b in zip(w, w[1:])]

    # Semantic concept tags expansion
    syn_tags = []
    for bg in [a + " " + b for a, b in zip(raw_words, raw_words[1:])]:
        if bg in SYNONYMS:
            syn_tags.append(SYNONYMS[bg])
    for tok in w:
        if tok in SYNONYMS:
            syn_tags.append(SYNONYMS[tok])
    for tok in _norm_acc(s).split():
        if tok in SYN_ACCENT:
            syn_tags.append(SYN_ACCENT[tok])

    return Counter(acc + bigrams + loose + syn_tags * 2)


def _size(s):
    for k, res in _SIZE_RE.items():
        if _has(res, s):
            return k
    return None


def _size_of(shot, tags):
    """Shot size of a clip: the AI's one-word label / a one-word tag first ('cận', 'toàn'), else words in them."""
    for x in [shot] + list(tags or []):
        lab = " ".join(_norm_acc(x or "").split())
        if lab in SIZE_LABELS:
            return SIZE_LABELS[lab]
    return _size((shot or "") + " " + " ".join(tags or []))


class Picker:
    def __init__(self, tags, ai_clips, media, bad, used_files, cfg, stab=None, force=(), segs=None, busy=None):
        """segs: {file: [{"a","b","desc","speech","q"}…]} from nxcore.segments (+ describe) — a clip split into ≥ 2
        segments becomes one item PER SEGMENT (key "file@a-b"), so a long take can serve several lines.
        busy: {file: [[t_in, t_out]…]} ranges already used as dialogue; segments of those clips stay usable as
        B-roll only when silent and ≥ 1 s away from the dialogue."""
        self.cfg = cfg
        self._lc = {}
        force = set(force or ())
        self.stab = stab or {}
        self.media, self.bad = media, bad
        self.items = {}
        segs, busy = segs or {}, busy or {}
        for f in sorted(set(tags) | set(ai_clips) | force | {g for g, s in segs.items() if len(s) >= 2}):   # sorted: run-to-run identical
            split = len(segs.get(f) or []) >= 2 and cfg.get("broll_segments", True)
            if f.startswith("_") or f not in media or (f in used_files and not (split and cfg.get("broll_from_used_clips", True))):
                continue
            pinned = f in force                 # named by the script ("Nguồn: …") → keep even if thin on tags
            t, a = tags.get(f, {}), ai_clips.get(f, {})
            if a.get("use_as") == "skip" and not pinned:
                continue
            dur = float(media[f].get("dur") or 0)
            if dur < (0.5 if pinned else cfg["broll_min_clip"]):
                continue
            badsum = sum(max(0, r[1] - r[0]) for r in bad.get(f, {}).get("ranges", []))
            if dur and badsum / dur > 0.6 and not pinned:
                continue
            mq = cfg.get("min_quality")
            if mq and not pinned and f in self.stab and float(self.stab[f].get("quality", 100) or 100) < mq - 10:
                continue                         # clearly below what this editor keeps (learned)
            moments_raw = a.get("moments") or []
            if isinstance(moments_raw, list):
                extra = " ".join(str(m.get("desc") or m.get("what") or m) for m in moments_raw[:6] if m)
            else:
                extra = ""
                moments_raw = []
            # t["desc"] = editor's own note (typed in the panel dashboard) — most specific, goes first
            desc = " ".join([str(t.get("desc") or ""), " ".join(t.get("tags", [])), a.get("summary", ""), a.get("shot", ""), str(a.get("best_broll", "")), extra])
            if not desc.strip() and not split:
                if not pinned:
                    continue
                desc = f.rsplit("/", 1)[-1]
            v = {"id": f, "file": f, "desc": desc, "tags": t.get("tags", []), "use": t.get("use"), "role": t.get("role"),
                             "shot": _size_of(a.get("shot", ""), t.get("tags", [])), "dur": dur,
                             "fps": float(media[f].get("fps") or 0), "raw_w": int(media[f].get("raw_w") or 0),
                             "created": media[f].get("created") or "", "use_as": a.get("use_as", ""),
                             "quality": a.get("quality", ""), "bad": badsum,
                             "q": float(self.stab.get(f, {}).get("quality", 60) or 60),
                             "stability": self.stab.get(f, {}).get("stability"),
                             "comp": (self.stab.get(f, {}).get("composition") or {}).get("score"),
                             "usable": self.stab.get(f, {}).get("usable"), "best": self.stab.get(f, {}).get("best"),
                             "rotate_fix": (self.stab.get(f, {}).get("composition") or {}).get("rotate_fix", 0),
                             "moments": moments_raw}
            if not split:
                if f in used_files:
                    continue
                self.items[f] = v
                continue
            busy_f = busy.get(f, []) if f in used_files else []
            marks = t.get("segs_mark") or {}            # editor's per-segment choice in the dashboard: "pin" | "ban"
            for s in segs[f]:
                a_, b_ = float(s["a"]), float(s["b"])
                mk = marks.get(s.get("k") or f"{a_:.1f}-{b_:.1f}")
                if mk == "ban":
                    continue
                if busy_f and (float(s.get("speech") or 0) > 0.3 or
                               any(min(b_, y + 1.0) > max(a_, x - 1.0) for x, y in busy_f)):
                    continue                      # this stretch is (next to) dialogue already on the timeline
                sd = str(s.get("desc") or "").strip()
                if not (sd or desc.strip()):
                    continue
                sid = f"{f}@{a_:.1f}-{b_:.1f}"
                bad_s = sum(max(0.0, min(r[1], b_) - max(r[0], a_)) for r in bad.get(f, {}).get("ranges", []))
                us = [[max(x, a_), min(y, b_)] for x, y in (v["usable"] or [[a_, b_]]) if min(y, b_) - max(x, a_) > 0.3]
                self.items[sid] = dict(v, id=sid, seg=[a_, b_], seg_len=round(b_ - a_, 2), use=[a_, b_], _sdesc=sd,
                                       _segpin=(mk == "pin"),
                                       desc=(sd + " " + sd + " " + desc) if sd else desc,   # the segment's own words weigh double
                                       usable=us or None, bad=bad_s,
                                       best=v["best"] if v["best"] and a_ <= v["best"][0] and v["best"][1] <= b_ else None,
                                       q=float(s["q"]) if s.get("q") is not None else v["q"],
                                       # a talking clip's silent stretches are real B-roll → no "dialogue clip" penalty there
                                       use_as="" if v["use_as"] == "dialogue" and float(s.get("speech") or 0) < 0.3 else v["use_as"],
                                       role=None if v.get("role") == "dialogue" and float(s.get("speech") or 0) < 0.3 else v.get("role"),
                                       moments=[m for m in moments_raw if isinstance(m, dict)
                                                and a_ <= float(m.get("t0", -1) or -1) <= b_])
        from .chrono import capture_ts                 # absolute shooting time of every item (chronological vlogs)
        for v in self.items.values():
            t0 = capture_ts(v["file"], media.get(v["file"]))
            mid = (v["seg"][0] + v["seg"][1]) / 2 if v.get("seg") else float(v["dur"]) / 2
            v["t_abs"] = (t0 + mid) if t0 else None
        docs = [_feats(v["desc"]) for v in self.items.values()]
        n = max(1, len(docs))
        df = Counter(k for d in docs for k in d)
        self.idf = lambda k: math.log((n + 1) / (df.get(k, 0) + 0.5))
        for v, d in zip(self.items.values(), docs):
            v["vec"] = self._weight(d)
            if v.get("_sdesc"):
                v["svec"] = self._weight(_feats(v["_sdesc"]))   # segment-only words (clip tags are shared by all segments)
        self._group()

    def _weight(self, c):
        v = {k: (1 + math.log(x)) * self.idf(k) * (0.5 if k[0] == "~" else 1.0) for k, x in c.items()}
        nrm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {k: x / nrm for k, x in v.items()}

    @staticmethod
    def cos(a, b):
        if len(a) > len(b):
            a, b = b, a
        return sum(x * b.get(k, 0.0) for k, x in a.items())

    def _group(self):
        """Near-duplicate takes → same group (consecutive in time + similar content)."""
        from datetime import datetime

        def ts(s):
            try:
                return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
            except Exception:
                return None
        order = sorted(self.items.values(), key=lambda v: (v["created"], v["file"], (v.get("seg") or [0])[0]))
        gid, prev = 0, None
        for v in order:
            if prev is not None:
                if prev["file"] == v["file"] and prev.get("seg") and v.get("seg"):
                    # neighbouring segments of one take: same group only if they SHOW the same thing
                    a, b = prev.get("svec"), v.get("svec")
                    same = bool(a and b) and self.cos(a, b) >= self.cfg.get("seg_dup_sim", 0.8)
                else:
                    t0, t1 = ts(prev["created"]), ts(v["created"])
                    close = t0 is not None and t1 is not None and abs(t1 - t0) <= self.cfg["dup_window_s"]
                    same = close and self.cos(prev["vec"], v["vec"]) >= self.cfg["dup_sim"]
                if not same:
                    gid += 1
            v["group"] = gid
            prev = v
        self.members = {}
        for v in self.items.values():
            self.members.setdefault(v["group"], []).append(v["id"])
        self.swaps = []

    def _line(self, text):
        """Line vector + shot size, computed once per script line (score() runs lines × clips times)."""
        c = self._lc.get(text)
        if c is None:
            c = self._lc[text] = (self._weight(_feats(text)), _size(text))
        return c

    def score(self, line_text, v):
        lv, ls = self._line(line_text)
        tag_match = max(0.0, min(1.0, self.cos(lv, v["vec"])))
        stab = (v["stability"] if v.get("stability") is not None else v.get("q", 60)) / 100.0
        comp = (v["comp"] if v.get("comp") is not None else 70.0) / 100.0
        fps = float(v.get("fps") or 0)
        raw_w = int(v.get("raw_w") or 0)
        res_fps = 1.0 if (fps >= 47.0 or raw_w >= 3840) else 0.8

        # 1. Relevance Gate: Semantic relevance is an absolute prerequisite.
        # If there is no content match at all, never let technical stability fake a good score!
        if tag_match < 0.08:
            return 0.0

        # 2. Multiplicative Quality Modulation: Content is 65%, modulated by technical quality (35%)
        quality = 0.50 * stab + 0.35 * comp + 0.15 * res_fps
        s = 0.65 * tag_match + 0.35 * (tag_match * quality)

        # 3. Shot size agreement
        if ls and v.get("shot"):
            s += 0.08 if ls == v["shot"] else -0.06

        # 4. Heavy penalty for dialogue clips when used as visual B-roll
        if v.get("use_as") == "dialogue" or v.get("role") == "dialogue":
            s -= 0.35

        if v.get("use_as") == "hook":
            s += 0.03
        if v.get("_segpin"):
            s += 0.15                                   # editor pinned this moment of a long take

        # 5. Defect penalty (shaky / out of focus / bad ranges)
        s -= min(0.15, v["bad"] / max(v.get("seg_len") or v["dur"], 1) * 0.3)
        return max(0.0, round(s, 4))

    def window(self, v, line_text="", L=None):
        """L: wanted length (VO cover / jump-cut cutaway) instead of the content-based one."""
        from .assemble import stable_window
        t = v["desc"] + " " + line_text
        fixed = L is not None
        if not fixed:
            L = self.cfg["broll_dur"]
            if _has(_STATIC_RE, t) and not _has(_ACTION_RE, line_text):
                L = self.cfg["broll_dur_static"]
        use = v.get("_pin_t") or v["use"]
        if v.get("_pin_t"):                       # the script chose this exact range → keep it (up to pin_max)
            L = min(self.cfg.get("pin_max", 8.0), max(1.0, use[1] - use[0]))
        if use and not fixed and _has(_ACTION_RE, t):
            L = min(self.cfg["broll_dur_max"], max(L, use[1] - use[0]))

        # Moment-Aware Targeting: check if any AI moment specifically matches the requested action/text
        moments = v.get("moments") or []
        if moments and line_text:
            lv, _ = self._line(line_text)
            best_m, best_sim = None, 0.0
            for m in moments:
                m_desc = str(m.get("desc") or m.get("what") or "")
                if not m_desc:
                    continue
                mv = self._weight(_feats(m_desc))
                sim = self.cos(lv, mv)
                if sim > best_sim and (m.get("t1", 0) > m.get("t0", 0)):
                    best_sim = sim
                    best_m = m
            # If a moment has significant semantic match (sim >= 0.20), lock onto that moment's window!
            if best_m and best_sim >= 0.20:
                m_t0, m_t1 = float(best_m["t0"]), float(best_m["t1"])
                use = [max(0.0, m_t0 - 0.2), min(float(v["dur"]), m_t1 + 0.2)]
                L = min(L, max(1.0, use[1] - use[0]))

        # Avoid pre-roll camera shake on longer clips when no explicit range is set
        if not use and v["dur"] >= 4.0:
            use = [1.0, v["dur"] - 0.5]

        # keep the window inside a stable, usable stretch: AI range ∩ usable (else the most stable window)
        us = v.get("usable") or []
        slow = self.stab.get(v["file"], {}).get("usable_slowmo") or []
        if slow and (not us or max(b - a for a, b in us) < min(L, 1.0)):
            us = slow; v["_slowmo"] = True          # only steady enough at ≤50 % speed
        if us:
            if use:
                inter = [[max(a, use[0]), min(b, use[1])] for a, b in us if min(b, use[1]) - max(a, use[0]) >= min(L, 1.0)]
                use = max(inter, key=lambda r: r[1] - r[0]) if inter else use
            else:
                big = max(us, key=lambda r: r[1] - r[0])
                use = big if big[1] - big[0] >= min(L, 1.0) else (v.get("best") or None)
        elif v.get("best"):
            use = use or v["best"]
        a, b = stable_window(v["dur"], L, self.bad.get(v["file"], {}).get("ranges", []), use)
        return self._steadier(v, a, b, use)

    def _steadier(self, v, a, b, use):
        """Inside the range already chosen (AI moment / usable stretch), move the window to a clearly steadier
        moment of the take (stability profile from shake.json: mean + worst moment) — a pinned range stays."""
        f = v["file"]
        sh = self.stab.get(f) or {}
        if v.get("_pin_t") or not sh.get("segments") or sh.get("skipped"):
            return a, b
        dur = float(v["dur"] or 0)
        lo, hi = (use if use else (0.3, dur - 0.3))
        lo, hi = max(0.0, float(lo)), min(dur, float(hi))
        if hi - lo <= (b - a) + 0.2:
            return a, b
        try:
            import numpy as np
            from .montage import profile_arrays, steadiest_start, moment_curve, expo_curve, DEFAULTS
            prof = getattr(self, "_profs", None)
            if prof is None:
                prof = self._profs = {}
            if f not in prof:
                prof[f] = profile_arrays(sh, dur)
            sc, slow, _ = prof[f]
            arr = slow if v.get("_slowmo") else sc
            # same "which seconds" rule as the montage: the take's moment + a settled exposure, not just the calmest bit
            n = len(arr)
            bonus = np.zeros(n)
            mo, ex = moment_curve(sh, n, DEFAULTS), expo_curve(sh, n, DEFAULTS)
            if len(mo) == n:
                bonus = bonus + float(DEFAULTS["w_moment"]) * mo
            if len(ex) == n:
                bonus = bonus - float(DEFAULTS["w_expo"]) * (1.0 - ex)
            s0 = steadiest_start(arr, lo, hi, b - a, prefer=a, bad=[r[:2] for r in self.bad.get(f, {}).get("ranges", [])],
                                 bonus=bonus if bonus.any() else None)
        except Exception:
            return a, b
        if s0 is None:
            return a, b
        return round(s0, 2), round(s0 + (b - a), 2)

    def batch_scores(self, lines, files=None):
        """score() for every (line, item) pair at once — the SAME formula (relevance gate, content × technical
        quality, shot size, dialogue / hook / segment-pin terms, defect penalty), vectorised: cosine as one matrix
        product over the lines' own terms (other terms add 0 to a dot product). {(line_id, item_id): score}."""
        import numpy as np
        ids = list(files if files is not None else self.items.keys())
        if not lines or not ids:
            return {}
        lf = [self._line(txt) for _, txt in lines]
        vocab = {}
        for lv, _ in lf:
            for t in lv:
                vocab.setdefault(t, len(vocab))
        M, N = len(lines), len(ids)
        if not vocab:
            return {(lid, i): 0.0 for lid, _ in lines for i in ids}
        line_mat = np.zeros((M, len(vocab)))
        for r, (lv, _) in enumerate(lf):
            for t, w in lv.items():
                line_mat[r, vocab[t]] = w
        item_mat = np.zeros((len(vocab), N))
        quality, flat, shot = np.zeros(N), np.zeros(N), []
        for j, i in enumerate(ids):
            v = self.items[i]
            for t, w in v["vec"].items():
                k = vocab.get(t)
                if k is not None:
                    item_mat[k, j] = w
            stab = (v["stability"] if v.get("stability") is not None else v.get("q", 60)) / 100.0
            comp = (v["comp"] if v.get("comp") is not None else 70.0) / 100.0
            res_fps = 1.0 if (float(v.get("fps") or 0) >= 47.0 or int(v.get("raw_w") or 0) >= 3840) else 0.8
            quality[j] = 0.50 * stab + 0.35 * comp + 0.15 * res_fps
            b = 0.0
            if v.get("use_as") == "dialogue" or v.get("role") == "dialogue":
                b -= 0.35
            if v.get("use_as") == "hook":
                b += 0.03
            if v.get("_segpin"):
                b += 0.15
            b -= min(0.15, v["bad"] / max(v.get("seg_len") or v["dur"], 1) * 0.3)
            flat[j] = b
            shot.append(v.get("shot"))
        tm = np.clip(line_mat @ item_mat, 0.0, 1.0)
        sc = 0.65 * tm + 0.35 * tm * quality[None, :] + flat[None, :]
        for r, (_, ls) in enumerate(lf):
            if ls:
                sc[r] += np.array([0.0 if not x else (0.08 if x == ls else -0.06) for x in shot])
        sc = np.where(tm < 0.08, 0.0, np.maximum(0.0, sc))
        return {(lid, i): round(float(sc[r, j]), 4) for r, (lid, _) in enumerate(lines) for j, i in enumerate(ids)}

    # ---- reuse rules for split (long) clips ----
    def max_uses(self, f):
        """How many shots one clip may give: 1 for normal clips; long split takes ≈ 1 per `seconds_per_use` (≤ max)."""
        mu = getattr(self, "_maxu", None)
        if mu is None:
            per = float(self.cfg.get("seconds_per_use", 30.0))
            cap = int(self.cfg.get("max_uses_per_clip", 4))
            mu = self._maxu = {}
            for x in self.items.values():
                if x.get("seg"):
                    mu[x["file"]] = int(max(1, min(cap, round(float(x["dur"]) / per))))
        return mu.get(f, 1)

    def _free(self, i, k=None):
        """Item i can still be used (k = index of the script line it would serve, for the no-repeat-next-door rule)."""
        v = self.items[i]
        f = v["file"]
        u = self._uses.get(f, [])
        if i in self.used_ids or v["group"] in self.used_groups:
            return False
        if not u:
            return True
        if not v.get("seg") or len(u) >= self.max_uses(f):
            return False
        a, b = v["seg"]
        # (seg_gap_s is not enforced on segment ranges: segments are abutting tiles of one take and a neighbouring line
        #  may use the next tile — the shot window sits mid-segment, so the two moments are far apart anyway)
        for x, y, kk, j in u:
            if min(b, y) > max(a, x) + 1e-6:                # overlapping source ranges
                return False
            near_tl = k is not None and kk is not None and abs(kk - k) <= 1
            if near_tl and self.cos(v.get("svec") or v["vec"], self.items[j].get("svec") or self.items[j]["vec"]) > 0.3:
                return False                                # same-looking shot from the same take right next door
        return True

    def _take(self, i, k=None):
        v = self.items[i]
        a, b = v.get("seg") or (0.0, float(v["dur"]))
        self.used_ids.add(i)
        self.used_groups.add(v["group"])
        self._uses.setdefault(v["file"], []).append((a, b, k, i))
        self.used_files = set(self._uses)

    def chrono_bonus(self, i, exp):
        """Score term for shooting-time order: cfg chrono_weight × fit (−1 … +1). exp = (t_expected, sigma) or None."""
        if not exp:
            return 0.0
        from .chrono import fit
        return float(self.cfg.get("chrono_weight", 0.2)) * fit(self.items[i].get("t_abs"), exp)

    def assign(self, lines, per_line=1, exclude=(), pins=None, chrono=None):
        """lines: [(line_id, text)] → {line_id: [item,…]}; greedy global best match.
        Each item (clip, or segment of a long clip) and each near-duplicate group is used once; a long split clip may
        serve up to max_uses() lines with non-overlapping segments, never the same look back-to-back.
        pins: {line_id: [(file, [t0, t1] | None)]} from the script's "Nguồn:" notes → those clips win their line."""
        pins = pins or {}
        order ={lid: k for k, (lid, _) in enumerate(lines)}
        exclude = set(exclude)
        self.used_ids, self.used_groups, self._uses, self.used_files = set(), set(), {}, set()
        for i, v in self.items.items():                    # e.g. the hook's clip(s)
            if v["file"] in exclude:
                self.used_ids.add(i); self.used_groups.add(v["group"])
        pairs = []
        pin_t = {}
        thr = self.cfg["broll_min_score"]
        cand = [i for i, v in self.items.items() if v["file"] not in exclude]
        batch_sc = self.batch_scores(lines, cand)        # == score(); used for ranking AND for the group swap below
        for lid, text in lines:
            pf = {}
            for f, t in pins.get(lid, []):
                pf.setdefault(f, t)
            for i in cand:
                v = self.items[i]
                sc = rank = batch_sc[(lid, i)]
                f = v["file"]
                if f in pf and (not pf[f] or not v.get("seg") or
                                min(v["seg"][1], pf[f][1]) > max(v["seg"][0], pf[f][0])):
                    sc = rank = max(sc, thr) + 0.5
                    pin_t[(lid, i)] = pf[f]
                elif sc < thr:
                    continue                               # content first: below the relevance threshold never competes
                if chrono:                                 # … then shooting-time order only RANKS the relevant shots
                    rank = sc + self.chrono_bonus(i, chrono.get(lid))
                pairs.append((rank, lid, i, sc))
        pairs.sort(key=lambda x: (-x[0], order.get(x[1], 0), x[2]))
        out = {lid: [] for lid, _ in lines}
        for rnd in range(per_line):
            for rank, lid, i, sc in pairs:
                if len(out[lid]) > rnd or not self._free(i, order[lid]):
                    continue
                if rnd and out[lid]:
                    if sc < self.cfg["second_shot_ratio"] * out[lid][0]["score"]:
                        continue
                    it = self.items[i]   # extra shot must add something: other shot size or clearly different content
                    if any(it["shot"] == o["shot"] and self.cos(it["vec"], o["vec"]) > 0.5 for o in out[lid]):
                        continue
                # same take group: swap to a steadier / better framed take when it matches (almost) as well — only to
                # a take that is itself free (max uses, overlap, seg gap, look-alike neighbour rules)
                g = self.items[i]["group"]
                alts = [(batch_sc.get((lid, h), 0.0), h) for h in self.members.get(g, [])
                        if h != i and self.items[h]["file"] not in exclude and self._free(h, order[lid])]
                better = [] if (lid, i) in pin_t else \
                    [x for x in alts if x[0] >= sc * 0.9 and self.items[x[1]]["q"] >= self.items[i]["q"] + 8]
                if better:
                    sc2, i2 = max(better, key=lambda x: (self.items[x[1]]["q"], x[0]))
                    self.swaps.append((self.items[i2]["file"], self.items[i]["file"], self.items[i2], self.items[i]))
                    i, sc = i2, sc2
                v = dict(self.items[i], score=round(sc, 3))
                if (lid, i) in pin_t:
                    v["_pinned"] = True
                    v["score"] = round(sc - 0.5, 3)
                    if pin_t[(lid, i)]:
                        v["_pin_t"] = pin_t[(lid, i)]
                out[lid].append(v)
                self._take(i, order[lid])
        return out

    def cover(self, text, L, min_len=0.0, when=None, slowmo_ok=True):
        """Best still-free shot for `text` (VO narration / a scene to cut away to), window ≈ L s and ≥ min_len long
        → (item, a, b, score) or None. The item is marked used, so later picks and suggestions skip it."""
        if not hasattr(self, "used_ids"):
            self.used_ids, self.used_groups, self._uses, self.used_files = set(), set(), {}, set()
        free = [i for i in self.items if self._free(i)]
        if not free or not text:
            return None
        thr = self.cfg["broll_min_score"]
        sc = self.batch_scores([("_", text)], free)
        ranked = sorted(((s + self.chrono_bonus(i, when), i, s) for (_, i), s in sc.items() if s >= thr),
                        key=lambda x: (-x[0], x[1]))
        for _, i, s in ranked[:12]:
            v = dict(self.items[i])
            a, b = self.window(v, text, L=L)
            if b - a < min_len - 1e-6 or (v.get("_slowmo") and not slowmo_ok):
                continue
            self._take(i)
            return v, a, b, round(s, 3)
        return None

    def explain_match(self, text, v):
        """Explain why this clip was suggested (matched keywords, stability, shot size)."""
        lv, ls = self._line(text)
        reasons = []
        overlap = [k for k in lv if k in v["vec"] and not k.startswith("syn_")]
        if overlap:
            reasons.append(f"Khớp '{', '.join(overlap[:2])}'")
        elif any(k in v["vec"] for k in lv if k.startswith("syn_")):
            reasons.append("Khớp chủ đề")
        if ls and v.get("shot") and ls == v["shot"]:
            reasons.append("Đúng cỡ cảnh")
        q = v.get("q")
        if q and q >= 70:
            reasons.append(f"Ổn định {round(q)}%")
        return " · ".join(reasons) if reasons else "Gợi ý tương đồng"

    def suggest(self, text, k=3, exclude=(), min_ratio=0.6, when=None):
        """Best alternative shots for a line (not yet on the timeline) → [{"file","in","out","score","why","seg"?}].
        exclude = files to leave out; segments of a long take stay eligible (other moments of the same clip).
        Used for the Review "thay source khuyến nghị" buttons (gap cards and b-roll swaps)."""
        exclude = set(exclude)
        if not hasattr(self, "used_ids"):
            self.used_ids, self.used_groups, self._uses, self.used_files = set(), set(), {}, set()
        # Strictly require minimum threshold of at least 0.20 to filter out zero-relevance clips
        thr = max(0.20, self.cfg["broll_min_score"] * min_ratio)
        ids = [i for i, v in self.items.items() if not (v["file"] in exclude and not v.get("seg")) and self._free(i)]
        scored = []
        for (_, i), sc in self.batch_scores([("_", text)], ids).items():
            if sc >= thr:
                scored.append((max(0.0, sc + self.chrono_bonus(i, when)), i))   # when = (t, sigma) of the line (vlog order)
        ranked = sorted(scored, key=lambda x: (-x[0], x[1]))
        out, groups = [], set()
        for sc, i in ranked:
            if len(out) >= k:
                break
            v = self.items[i]
            if v["group"] in groups:
                continue                          # one take per near-duplicate group
            groups.add(v["group"])
            a, b = self.window(dict(v), text)
            out.append(dict({"file": v["file"], "in": a, "out": b, "score": round(sc, 3), "q": v.get("q"),
                             "why": self.explain_match(text, v)}, **({"seg": v["seg"]} if v.get("seg") else {})))
        return out

    def leftovers(self, limit=12):
        rest = [v for i, v in self.items.items() if self._free(i)]
        best = {}
        for v in rest:
            g = v["group"]
            if g not in best or (v.get("seg_len") or v["dur"]) > (best[g].get("seg_len") or best[g]["dur"]):
                best[g] = v
        return sorted(best.values(), key=lambda v: (v["created"], (v.get("seg") or [0])[0]))[:limit]
