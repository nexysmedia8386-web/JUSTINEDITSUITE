# -*- coding: utf-8 -*-
"""specscript — kịch bản "ghim nguồn" (quy trình skill nx-script-from-probe) cho Justin EDIT Suite.

AI chỉ chọn shot (clip ID + in/out + tốc độ, câu thoại theo ID + trích dẫn, VO/text/title);
module này dựng file NX chạy thẳng Align → Build và tự sửa những gì AI hay sai:
  · thoại: cắt đúng câu theo timestamp TỪNG TỪ (không bao giờ lấy cả block ASR dài)
  · b-roll: kẹp vào đoạn "dùng được" (shake.json), tốc độ hợp fps (24/30p không slow-mo)
  · không dùng lại cùng đoạn hình, b-roll không chồng lên thoại cùng clip
  · khớp thời lượng từng chương + tổng, TC liền mạch
  · kiểm tra bằng script_parser thật của engine

python -m nxcore.specscript build SPEC.json --src SRC --out OUT.md [--target 600]
→ OUT.md + OUT.report.json (+ dòng tóm tắt ra stdout)
"""
import os, re, sys, json, argparse, difflib, unicodedata, collections

MIN_OUT = 1.2          # giây output tối thiểu cho 1 shot b-roll
MAX_OUT = 8.0          # giây output tối đa khi kéo dài b-roll để khớp thời lượng
SYL_PER_S = 4.0        # tốc độ đọc VO tiếng Việt


def flat_stem(f):
    """'Day1/CamA/C0001.MP4' -> 'Day1__CamA__C0001' (cùng quy ước nxcore.media.stem)"""
    try:
        from nxcore.media import stem as _st
        return _st(f)
    except Exception:
        s = os.path.splitext(str(f).replace("\\", "/"))[0]
        return re.sub(r'[<>:"|?*]', "_", s.replace("/", "__"))


def jl(p, d=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return d


def norm(w):
    return re.sub(r"[^\w]", "", unicodedata.normalize("NFC", str(w).lower()))


def tc(x):
    m, s = divmod(max(0.0, x), 60)
    return "%d:%04.1f" % (m, s) if abs(s - round(s)) > 0.05 else "%d:%02d" % (m, round(s) if round(s) < 60 else 59)


def g(v):
    return "%g" % round(v, 2)


# ------------------------------------------------------------------ source data
class Source:
    def __init__(self, src):
        self.src = src
        w = os.path.join(src, "_nexys_work")
        self.work = w
        self.D = jl(os.path.join(w, "digest.json"), {}) or {}
        self.media = jl(os.path.join(w, "media.json"), {}) or {}
        self.shake = jl(os.path.join(w, "shake.json"), {}) or {}
        self.clips = self.D.get("clips", {}) or {}
        self.utts = self.D.get("utts", {}) or {}
        self.byfile = {c.get("file"): k for k, c in self.clips.items()}
        self._words = {}

    def file_of(self, ref):
        ref = str(ref or "").strip()
        if ref in self.clips:
            return self.clips[ref].get("file")
        def key(x):
            return unicodedata.normalize("NFC", str(x).replace("\\", "/")).lower()
        allf = [f for f in list(self.media.keys()) + list(self.byfile.keys()) if f]
        want = key(ref)
        for f in allf:                       # đường dẫn đầy đủ: Chiều/NT_4427.MP4
            if key(f) == want:
                return f
        base = key(os.path.basename(want))   # chỉ tên file: dùng khi không trùng giữa các thư mục con
        hits = sorted(set(f for f in allf if key(os.path.basename(key(f))) == base))
        return hits[0] if len(hits) == 1 else None

    def info(self, f):
        c = self.clips.get(self.byfile.get(f), {}) or {}
        m = self.media.get(f, {}) or {}
        dur = float(c.get("dur") or m.get("dur") or 0)
        fps = float(c.get("fps") or m.get("fps") or 30)
        return dur, fps, c.get("status", "")

    def ranges(self, f, slow):
        s = self.shake.get(f, {}) or {}
        rs = [list(r[:2]) for r in (s.get("usable") or [])]
        if slow:
            rs += [list(r[:2]) for r in (s.get("usable_slowmo") or [])]
        rs = [r for r in rs if r[1] - r[0] > 0.3]
        if not rs:
            dur = self.info(f)[0]
            rs = [[0.0, dur]] if dur else []
        return sorted(rs)

    def words(self, f):
        if f not in self._words:
            d = jl(os.path.join(self.work, "transcripts", flat_stem(f) + ".json"), {}) or {}
            ws = []
            for s in d.get("segments", []):
                for w in s.get("words", []) or []:
                    if len(w) >= 3 and str(w[2]).strip():
                        ws.append((float(w[0]), float(w[1]), str(w[2]).strip()))
            self._words[f] = ws
        return self._words[f]


# ------------------------------------------------------------------ speed
def parse_rate(sp):
    s = str(sp or "").lower().replace(",", ".")
    m = re.search(r"(?:timelapse|time-lapse|tl)\s*[×x*]?\s*(\d+(?:\.\d+)?)", s)
    if m:
        return max(1.0, float(m.group(1))), True
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
    if m:
        return float(m.group(1)) / 100.0, False
    return None, False


def rate_label(rate, tl):
    return ("timelapse ×%g" % round(rate, 1)) if tl else ("%d%%" % round(rate * 100))


def fix_rate(req, fps, fixes, where):
    rate, tl = parse_rate(req)
    if tl:
        return rate, True
    floor = 1.0 if fps < 47 else (0.25 if fps >= 100 else 0.5)
    if rate is None:
        rate = 0.5 if fps >= 47 else 1.0
    if rate < floor - 1e-6:
        fixes.append("%s: %s fps %.0f không slow-mo được → %s" % (where, rate_label(rate, False), fps, rate_label(floor, False)))
        rate = floor
    if rate > 1.0:
        rate = 1.0
    return rate, False


# ------------------------------------------------------------------ dialogue snapping
def snap_quote(S, uid, quote, fixes, warns, f=None, a=None, b=None):
    u = S.utts.get(uid) if uid else None
    if u:
        f, t0, t1 = u["file"], float(u["t0"]), float(u["t1"])
    elif f and a is not None and b is not None:      # câu không có trong digest: file + khoảng + trích dẫn
        u, t0, t1 = {"text": quote or ""}, float(a) - 1.0, float(b) + 1.0
        uid = "%s@%s" % (os.path.basename(f), g(float(a)))
    else:
        warns.append("thoại %s không có trong digest — bỏ shot" % uid)
        return None
    ws = [w for w in S.words(f) if w[0] >= t0 - 0.3 and w[1] <= t1 + 0.3]
    if not ws:
        return {"file": f, "a": max(0.0, t0 - 0.1), "b": t1 + 0.15, "q": u.get("text", "").strip()}
    span = (0, len(ws) - 1)
    qt = [norm(x) for x in str(quote or "").split() if norm(x)]
    if qt:
        wt = [norm(w[2]) for w in ws]
        sm = difflib.SequenceMatcher(None, wt, qt, autojunk=False)
        blocks = [b for b in sm.get_matching_blocks() if b.size]
        hit = sum(b.size for b in blocks)
        if blocks and hit >= max(1, 0.5 * len(qt)):
            span = (blocks[0].a, blocks[-1].a + blocks[-1].size - 1)
        else:
            warns.append("%s: trích dẫn không khớp transcript → dùng cả câu" % uid)
    elif t1 - t0 > 12:
        warns.append("%s: câu ASR dài %.0fs mà AI không trích phần cần dùng" % (uid, t1 - t0))
    i, j = span
    allw = S.words(f)
    before = [w[1] for w in allw if w[1] < ws[i][0] - 0.01]
    after = [w[0] for w in allw if w[0] > ws[j][1] + 0.01]
    a = max(0.0, ws[i][0] - 0.25, (before[-1] + 0.02) if before else 0.0)
    b = min(ws[j][1] + 0.35, after[0] - 0.02) if after else ws[j][1] + 0.35
    if t1 - t0 > (b - a) + 1.0:
        fixes.append("%s: cắt %.1fs → %.1fs theo timestamp từng từ" % (uid, t1 - t0, b - a))
    return {"file": f, "a": a, "b": max(b, a + 0.4), "q": " ".join(w[2] for w in ws[i:j + 1])}


# ------------------------------------------------------------------ b-roll placement
def place(a, b, ranges, dur):
    if dur:
        a, b = max(0.0, min(a, dur)), max(0.0, min(b, dur))
    if b <= a:
        b = a + 3.0
    if not ranges:
        return a, b, None
    L = b - a
    best = max(ranges, key=lambda r: (min(b, r[1]) - max(a, r[0]), r[1] - r[0]))
    if a >= best[0] - 0.05 and b <= best[1] + 0.05:
        return a, b, best
    L = min(L, best[1] - best[0])
    na = min(max(a, best[0]), best[1] - L)
    return na, na + L, best


def overlaps(a, b, used):
    return [u for u in used if min(b, u[1]) - max(a, u[0]) > 0.15]


def free_slot(L, rng, used):
    """vị trí đầu tiên trong rng dài L không chồng lên used"""
    cuts = sorted([u for u in used if u[1] > rng[0] and u[0] < rng[1]])
    cur = rng[0]
    for u in cuts:
        if u[0] - cur >= L:
            return cur
        cur = max(cur, u[1])
    return cur if rng[1] - cur >= L else None


# ------------------------------------------------------------------ build
def build(spec, S, target=None, strict=False):
    fixes, warns = [], []
    chapters = []
    for ci, ch in enumerate(spec.get("chapters", []), 1):
        shots = []
        for si, sh in enumerate(ch.get("shots", []), 1):
            where = "C%d.%d" % (ci, si)
            extra = {k: (str(sh.get(k)).strip() if sh.get(k) else None) for k in ("vo", "text", "title")}
            qf = S.file_of(sh.get("id") or sh.get("f")) if (sh.get("quote") or sh.get("q")) else None
            if sh.get("utt") or sh.get("q_id") or qf:
                r = snap_quote(S, sh.get("utt") or sh.get("q_id"), sh.get("quote") or sh.get("q"), fixes, warns,
                               f=qf, a=sh.get("a"), b=sh.get("b"))
                if not r:
                    continue
                shots.append(dict(kind="dlg", file=r["file"], a=r["a"], b=r["b"], rate=1.0, tl=False, q=r["q"],
                                  d=sh.get("d") or "", **extra))
                continue
            f = S.file_of(sh.get("id") or sh.get("f"))
            if not f:
                warns.append("%s: clip %s không có trong source — bỏ shot" % (where, sh.get("id") or sh.get("f")))
                continue
            dur, fps, st = S.info(f)
            if st == "LOẠI":
                warns.append("%s: %s bị đánh LOẠI ở bước phân tích" % (where, f))
            rate, tl = fix_rate(sh.get("sp"), fps, fixes, where)
            try:
                a, b = float(sh.get("a", 0)), float(sh.get("b", 0))
            except Exception:
                a, b = 0.0, 0.0
            rs = S.ranges(f, rate < 1.0)
            na, nb, rng = place(a, b, rs, dur)
            if abs(na - a) > 0.6 or abs(nb - b) > 0.6:
                fixes.append("%s: %s @ %s–%s ngoài đoạn dùng được → %s–%s" % (where, f, g(a), g(b), g(na), g(nb)))
            shots.append(dict(kind="broll", file=f, a=na, b=nb, rate=rate, tl=tl, rng=rng or [0.0, dur or nb],
                              d=sh.get("d") or "", **extra))
        chapters.append({"name": str(ch.get("name") or ch.get("title") or "CHƯƠNG %d" % ci).upper(),
                         "dur": float(ch.get("dur_s") or (float(ch.get("tout") or 0) - float(ch.get("tin") or 0)) or 0), "shots": shots})

    # -- used ranges: thoại trước, rồi b-roll theo thứ tự xuất hiện
    used = collections.defaultdict(list)
    for ch in chapters:
        for s in ch["shots"]:
            if s["kind"] == "dlg":
                used[s["file"]].append([s["a"], s["b"]])

    def out_len(s):
        return (s["b"] - s["a"]) / s["rate"]

    # -- khớp thời lượng: chuẩn hóa chương theo target rồi co/giãn b-roll
    total_req = sum(c["dur"] for c in chapters)
    if target and total_req <= 0:   # spec không ghi thời lượng chương → chia theo độ dài hiện có
        for c in chapters:
            c["dur"] = sum(out_len(s) for s in c["shots"])
        total_req = sum(c["dur"] for c in chapters)
    if target and total_req > 0:
        k = target / total_req
        for c in chapters:
            c["dur"] *= k
    for ci, c in enumerate(chapters, 1):
        if not c["dur"]:
            continue
        br = [s for s in c["shots"] if s["kind"] == "broll"]
        dl = sum(out_len(s) for s in c["shots"] if s["kind"] == "dlg")
        need = c["dur"] - dl
        if not br:
            continue
        for _ in range(3):
            have = sum(out_len(s) for s in br)
            if have <= 0 or abs(need - have) < 0.25:
                break
            f = need / have
            if f < 1.0 and not strict:   # thời lượng là tham khảo: không cắt ngắn hình đã chọn
                break
            for s in br:
                lo, hi = s["rng"]
                L = (s["b"] - s["a"]) * f
                if s["tl"]:
                    L = min(L, hi - lo)
                else:
                    L = max(MIN_OUT * s["rate"], min(L, max(MAX_OUT, out_len(s)) * s["rate"], hi - lo))
                a = min(max(s["a"], lo), hi - L)
                s["a"], s["b"] = max(lo, a), max(lo, a) + L
        diff = sum(out_len(s) for s in br) - need
        if abs(diff) > 1.0 and (strict or diff < 0):
            warns.append("chương %d '%s': lệch %+.1fs so với đích %.0fs (%s)" %
                         (ci, c["name"], diff, c["dur"], "thiếu hình — thêm shot" if diff < 0 else "thừa — bớt shot"))

    # -- không dùng lại đoạn hình / không chồng thoại
    for ci, c in enumerate(chapters, 1):
        for si, s in enumerate(c["shots"], 1):
            if s["kind"] != "broll":
                continue
            u = used[s["file"]]
            if overlaps(s["a"], s["b"], u):
                L = s["b"] - s["a"]
                pos = free_slot(L, s["rng"], u)
                if pos is None:
                    for r in S.ranges(s["file"], s["rate"] < 1.0):
                        pos = free_slot(L, r, u)
                        if pos is not None:
                            s["rng"] = r
                            break
                if pos is not None:
                    fixes.append("C%d.%d: %s @ %s–%s trùng đoạn đã dùng → %s–%s" %
                                 (ci, si, s["file"], g(s["a"]), g(s["b"]), g(pos), g(pos + L)))
                    s["a"], s["b"] = pos, pos + L
                else:
                    warns.append("C%d.%d: %s @ %s–%s dùng lại hình đã dùng" % (ci, si, s["file"], g(s["a"]), g(s["b"])))
            u.append([s["a"], s["b"]])

    # -- timeline + VO fit
    t = 0.0
    vo_list = []
    for c in chapters:
        c["tin"] = t
        for s in c["shots"]:
            s["t0"] = t
            t += out_len(s)
            s["t1"] = t
            if s.get("vo"):
                vo_list.append(s)
        c["tout"] = t
    for i, s in enumerate(vo_list):
        nxt = vo_list[i + 1]["t0"] if i + 1 < len(vo_list) else t
        need = len(s["vo"].split()) / SYL_PER_S + 0.4
        if need > nxt - s["t0"] + 0.05:
            warns.append("VO @%s dài %.1fs nhưng chỉ có %.1fs trước câu VO sau" % (tc(s["t0"]), need, nxt - s["t0"]))
    return chapters, t, fixes, warns


def to_md(spec, chapters, cta):
    out = [str(spec.get("title") or "KỊCH BẢN").upper()]
    for n in spec.get("notes") or []:
        n = str(n).strip()
        if n:
            out.append(n if re.match(r"^(thời lượng|ghi chú)\s*:", n, re.I) else "Ghi chú: " + n)
    out.append("")
    for i, c in enumerate(chapters, 1):
        out.append("PHÂN CẢNH %d — %s (%s–%s)" % (i, c["name"], tc(c["tin"]), tc(c["tout"])))
        for s in c["shots"]:
            if s.get("vo"):
                out.append("[VO] " + s["vo"])
            if s.get("title"):
                out.append("[TITLE] " + s["title"])
            if s.get("text"):
                out.append("[TEXT] " + s["text"])
            if s["kind"] == "dlg":
                out.append("THOẠI: “%s”" % s["q"].replace("“", "").replace("”", "").replace('"', ""))
            else:
                d = re.sub(r"\s*·\s*\d+\s*%.*$", "", s.get("d") or "cảnh").strip() or "cảnh"
                out.append("[B-ROLL] %s · %s" % (d, rate_label(s["rate"], s["tl"])))
            out.append("Nguồn: %s @ %s–%s" % (s["file"], g(s["a"]), g(s["b"])))
        out.append("")
    if cta:
        out += ["Kết video:", "→ " + cta]
    return "\n".join(out).rstrip() + "\n"


# ------------------------------------------------------------------ kho source chưa dùng (không bỏ sót đoạn đạt chuẩn)
GOOD = ("ĐẠT", "ĐẠT(slow-mo)")
MIN_LEFT = 1.0


def _sub(rs, cut):
    out = []
    for a, b in rs:
        segs = [[a, b]]
        for c0, c1 in sorted(cut):
            nxt = []
            for x0, x1 in segs:
                if c1 <= x0 or c0 >= x1:
                    nxt.append([x0, x1])
                    continue
                if c0 > x0:
                    nxt.append([x0, c0])
                if c1 < x1:
                    nxt.append([c1, x1])
            segs = nxt
        out += segs
    return out


def _len(rs):
    return sum(max(0.0, b - a) for a, b in rs)


def _desc(S, f, a, b, c):
    if not hasattr(S, "_ad"):
        S._ad = (jl(os.path.join(S.work, "auto_desc.json"), {}) or {}).get("clips", {})
    ad = S._ad.get(f) or {}
    best, bo = "", 0.0
    for k, v in (ad.get("segs") or {}).items():
        m = re.findall(r"[\d.]+", str(k))
        if len(m) >= 2:
            o = min(b, float(m[1])) - max(a, float(m[0]))
            if o > bo and v.get("desc"):
                best, bo = v["desc"], o
    d = best or ad.get("desc") or c.get("desc") or "cảnh"
    return "cảnh" if re.match(r"^\s*b-roll\s*,", d, re.I) else d


def leftovers(S, chapters, min_len=MIN_LEFT):
    used = collections.defaultdict(list)
    for c in chapters:
        for s in c["shots"]:
            used[s["file"]].append([s["a"], s["b"]])
    utts = collections.defaultdict(list)
    for uid, u in S.utts.items():
        if u.get("file") and str(u.get("text") or "").strip():
            utts[u["file"]].append((float(u["t0"]), float(u["t1"]), str(u["text"]).strip(), uid))
    clips = sorted(S.clips.values(), key=lambda c: (c.get("created_ts") or 0, c.get("file") or ""))
    days = collections.OrderedDict()
    st = {"good_s": 0.0, "used_s": 0.0, "left_s": 0.0, "good_clips": 0, "untouched_clips": 0,
          "left_dialogue": 0, "left_broll": 0, "weak_clips": []}
    for c in clips:
        f, status = c.get("file"), str(c.get("status") or "")
        if not f:
            continue
        if status not in GOOD:
            if status == "YẾU":
                st["weak_clips"].append(f)
            continue
        dur = float(c.get("dur") or 0)
        sh = S.shake.get(f, {}) or {}
        good = [list(r[:2]) for r in (sh.get("usable") or []) + (sh.get("usable_slowmo") or [])]
        good = sorted(r for r in good if r[1] - r[0] > 0.3) or ([[0.0, dur]] if dur else [])
        merged = []
        for r in good:
            if merged and r[0] <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], r[1])
            else:
                merged.append(list(r))
        good = merged
        st["good_clips"] += 1
        st["good_s"] += _len(good)
        U = used.get(f, [])
        st["used_s"] += _len(good) - _len(_sub(good, U))
        if not U:
            st["untouched_clips"] += 1
        items = []
        speech = []
        for t0, t1, txt, uid in utts.get(f, []):
            ov = _len(_sub([[t0, t1]], [[0, t0]] + [[t1, 1e9]]) ) - _len(_sub([[t0, t1]], U))
            if ov < 0.5 * (t1 - t0):
                ws = [w for w in S.words(f) if w[0] >= t0 - 0.05 and w[1] <= t1 + 0.05]
                q = " ".join(w[2] for w in ws) or txt
                items.append(dict(kind="dlg", file=f, a=round(t0, 2), b=round(t1, 2), q=q, d=""))
            speech.append([t0 - 0.2, t1 + 0.2])
        for a, b in _sub(_sub(good, U), speech):
            if b - a >= min_len:
                items.append(dict(kind="broll", file=f, a=round(a, 2), b=round(b, 2), q="", d=_desc(S, f, a, b, c)))
        if not items:
            continue
        items.sort(key=lambda s: s["a"])
        day = (c.get("created_local") or "")[:10] or "không rõ ngày"
        days.setdefault(day, []).extend(items)
        for s in items:
            st["left_s"] += s["b"] - s["a"]
            st["left_dialogue" if s["kind"] == "dlg" else "left_broll"] += 1
    return days, st


def leftovers_md(title, days, st):
    out = ["KHO SOURCE CHƯA DÙNG — " + str(title or "").upper(),
           "Ghi chú: mọi đoạn đạt chuẩn (status ĐẠT, đoạn usable, câu thoại có nghĩa) chưa có trong kịch bản chính — xếp theo giờ quay, 100%, để dựng tay.",
           "Ghi chú: %d đoạn thoại · %d đoạn hình · %s" % (st["left_dialogue"], st["left_broll"], tc(st["left_s"])), ""]
    t, n = 0.0, 0
    for day, items in days.items():
        d0 = t
        body = []
        for s in items:
            n += 1
            if s["kind"] == "dlg":
                body.append("THOẠI: “%s”" % s["q"].replace("“", "").replace("”", "").replace('"', ""))
            else:
                d = re.sub(r"[·%]", " ", s["d"]).strip()[:70] or "cảnh"
                body.append("[B-ROLL] %s · 100%%" % d)
            body.append("Nguồn: %s @ %s–%s" % (s["file"], g(s["a"]), g(s["b"])))
            t += s["b"] - s["a"]
        try:
            y, m, dd = day.split("-")
            name = "NGÀY %s/%s — CHƯA DÙNG" % (dd, m)
        except Exception:
            name = day.upper() + " — CHƯA DÙNG"
        sc = sum(1 for x in out if x.startswith("PHÂN CẢNH ")) + 1
        out.append("PHÂN CẢNH %d — %s (%s–%s)" % (sc, name, tc(d0), tc(t)))
        out += body + [""]
    return "\n".join(out).rstrip() + "\n"


def shot_list(chapters):
    out = []
    for ci, c in enumerate(chapters, 1):
        for si, s in enumerate(c["shots"], 1):
            out.append({"id": "C%d.%d" % (ci, si), "ch": ci, "chapter": c["name"], "kind": s["kind"], "file": s["file"],
                        "a": round(s["a"], 2), "b": round(s["b"], 2), "speed": rate_label(s["rate"], s["tl"]),
                        "t0": round(s["t0"], 2), "t1": round(s["t1"], 2), "d": s.get("d") or "",
                        "q": s.get("q") or "", "vo": s.get("vo") or "", "text": s.get("text") or "", "title": s.get("title") or ""})
    return out


# ------------------------------------------------------------------ contact sheet: 1 khung giữa mỗi shot, có nhãn
def sheets(rep, src, outdir, per=12, cols=4, width=400, only=None):
    import subprocess, concurrent.futures
    from PIL import Image, ImageDraw, ImageFont
    os.makedirs(outdir, exist_ok=True)
    shots = [s for s in rep.get("shot_list", []) if not only or s["id"] in only]
    try:
        font = ImageFont.truetype("arial.ttf", 15)
    except Exception:
        font = ImageFont.load_default()

    def grab(i_s):
        i, s = i_s
        fp = os.path.join(outdir, "_f%03d.jpg" % i)
        t = (s["a"] + s["b"]) / 2.0
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "%.2f" % t, "-i", os.path.join(src, s["file"]),
                        "-frames:v", "1", "-vf", "scale=%d:-2" % width, fp], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return i, fp

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        frames = dict(ex.map(grab, list(enumerate(shots))))
    paths, legend = [], []
    for k in range(0, len(shots), per):
        chunk = list(range(k, min(k + per, len(shots))))
        tiles = []
        for i in chunk:
            s = shots[i]
            try:
                im = Image.open(frames[i]).convert("RGB")
            except Exception:
                im = Image.new("RGB", (width, width * 9 // 16), (40, 40, 40))
            dr = ImageDraw.Draw(im)
            dr.rectangle([0, 0, im.width, 22], fill=(0, 0, 0))
            dr.text((5, 3), "%s  %s @%s" % (s["id"], os.path.basename(s["file"]), g(s["a"])), fill=(255, 230, 0), font=font)
            tiles.append(im)
            legend.append("%s · %s @ %s–%s · %s%s" % (s["id"], s["file"], g(s["a"]), g(s["b"]),
                                                       "THOẠI: " + s["q"] if s["kind"] == "dlg" else s["d"],
                                                       (" · TEXT: " + s["text"]) if s.get("text") else ""))
        h = max(t.height for t in tiles)
        rows = (len(tiles) + cols - 1) // cols
        sh = Image.new("RGB", (width * cols, h * rows), (18, 18, 18))
        for j, t in enumerate(tiles):
            sh.paste(t, ((j % cols) * width, (j // cols) * h))
        fp = os.path.join(outdir, "sheet_%02d.jpg" % (k // per + 1))
        sh.save(fp, quality=82)
        paths.append(fp)
    for f in frames.values():
        try:
            os.remove(f)
        except Exception:
            pass
    return paths, legend


# ------------------------------------------------------------------ publish: file NX ra gốc source + bảng đọc
def slug(t):
    t = unicodedata.normalize("NFD", str(t or "KichBan"))
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn").replace("đ", "d").replace("Đ", "D")
    t = re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_")
    return (t[:40] or "KichBan")


def table_md(rep, title, md_name):
    L = rep.get("shot_list", [])
    out = ["# " + str(title or "Kịch bản"), "",
           "File NX: `%s` · %s · %d shot (%d thoại, %d b-roll) · %d VO · %d text" % (
               md_name, tc(rep.get("total") or 0), rep.get("shots", 0), rep.get("dialogue", 0), rep.get("broll", 0),
               rep.get("vo", 0), rep.get("text", 0)), ""]
    cur = None
    for s in L:
        if s["ch"] != cur:
            cur = s["ch"]
            c = (rep.get("chapters") or [{}] * cur)[cur - 1] if len(rep.get("chapters") or []) >= cur else {}
            out += ["", "## %d · %s (%s–%s)" % (cur, s["chapter"], tc(c.get("tin", s["t0"])), tc(c.get("tout", s["t1"]))), "",
                    "| TC | Hình | Thoại / VO | Text |", "| --- | --- | --- | --- |"]
        hinh = "%s @ %s–%s · %s. %s" % (s["file"], g(s["a"]), g(s["b"]), s["speed"], s["d"])
        au = " / ".join(x for x in (["“%s”" % s["q"]] if s["q"] else []) + (["VO: " + s["vo"]] if s["vo"] else [])) or "—"
        tx = " · ".join(x for x in (["TITLE: " + s["title"]] if s["title"] else []) + ([s["text"]] if s["text"] else [])) or "—"
        out.append("| %s–%s | %s | %s | %s |" % (tc(s["t0"]), tc(s["t1"]), hinh.replace("|", "/"), au.replace("|", "/"), tx.replace("|", "/")))
    if rep.get("fixes"):
        out += ["", "## Engine tự sửa", ""] + ["- " + x for x in rep["fixes"]]
    if rep.get("warnings"):
        out += ["", "## Cần xem", ""] + ["- [ ] " + x for x in rep["warnings"]]
    vis = rep.get("vision") or {}
    if vis.get("issues"):
        out += ["", "## Kiểm tra hình (AI vision)", ""] + ["- [ ] %s: %s" % (i.get("shot"), i.get("problem")) for i in vis["issues"]]
    return "\n".join(out).rstrip() + "\n"


def publish(md_path, src, title=None):
    rep = jl(os.path.splitext(md_path)[0] + ".report.json", {}) or {}
    base = "KichBan_" + slug(title or open(md_path, encoding="utf-8").readline().split("—")[0])
    v = 1
    while os.path.exists(os.path.join(src, "%s_v%d.md" % (base, v))):
        v += 1
    dst = os.path.join(src, "%s_v%d.md" % (base, v))
    shutil_copy(md_path, dst)
    tbl = os.path.join(src, "%s_v%d_BANG.md" % (base, v))
    with open(tbl, "w", encoding="utf-8") as f:
        f.write(table_md(rep, title or base, os.path.basename(dst)))
    return dst, tbl


def shutil_copy(a, b):
    import shutil
    shutil.copyfile(a, b)


def parser_check(md_path):
    try:
        from nxcore.script_parser import parse_script_meta
    except Exception:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from nxcore.script_parser import parse_script_meta
    L, M = parse_script_meta(md_path)
    return {"scenes": len(M.scene_titles), "types": dict(collections.Counter(l.type for l in L)),
            "missing": [l.text[:50] for l in L if l.type in ("broll", "dialogue") and not l.src],
            "hook_files": list(M.hook_files or [])}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nxcore.specscript")
    sub = ap.add_subparsers(dest="cmd")
    b = sub.add_parser("build")
    b.add_argument("spec")
    b.add_argument("--src", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--target", type=float, default=0)
    b.add_argument("--strict-duration", action="store_true", help="ép đúng thời lượng (co cả shot dài)")
    b.add_argument("--no-leftovers", action="store_true")
    sh = sub.add_parser("sheets", help="contact sheet 1 khung/shot từ .report.json")
    sh.add_argument("md")
    sh.add_argument("--src", required=True)
    sh.add_argument("--out", default="")
    sh.add_argument("--only", default="", help="C1.1,C5.3 …")
    pb = sub.add_parser("publish", help="chép file NX ra gốc source (KichBan_<tên>_vN.md) + bảng đọc _BANG.md")
    pb.add_argument("md")
    pb.add_argument("--src", required=True)
    pb.add_argument("--title", default="")
    a = ap.parse_args(argv)
    if a.cmd == "sheets":
        rp = jl(os.path.splitext(a.md)[0] + ".report.json", {}) or {}
        if not rp.get("shot_list"):
            print("✖ report chưa có shot_list — build lại kịch bản")
            return 1
        outdir = a.out or os.path.splitext(a.md)[0] + "_sheets"
        paths, legend = sheets(rp, a.src, outdir, only=[x.strip() for x in a.only.split(",") if x.strip()] or None)
        with open(os.path.join(outdir, "sheets.json"), "w", encoding="utf-8") as f:
            json.dump({"sheets": paths, "legend": legend}, f, ensure_ascii=False, indent=1)
        print("✔ %d contact sheet · %d shot → %s" % (len(paths), len(legend), outdir))
        return 0
    if a.cmd == "publish":
        dst, tbl = publish(a.md, a.src, a.title or None)
        print("✔ " + dst)
        print("✔ " + tbl)
        return 0
    if a.cmd != "build":
        ap.print_help()
        return 2
    spec = jl(a.spec)
    if not spec or not spec.get("chapters"):
        print("✖ spec rỗng hoặc sai định dạng")
        return 1
    S = Source(a.src)
    if not S.clips:
        print("✖ chưa có digest.json — chạy digest trước")
        return 1
    chapters, total, fixes, warns = build(spec, S, a.target or spec.get("target") or None, strict=a.strict_duration)
    cta = str(spec.get("cta") or "").strip()
    md = to_md(spec, chapters, cta)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(md)
    chk = parser_check(a.out)
    if chk["hook_files"]:
        warns.append("parser thấy hook_files %s — có thể bị chèn hook trùng" % chk["hook_files"])
    shots = [s for c in chapters for s in c["shots"]]
    rep = {"md": a.out, "total": round(total, 2), "target": a.target or None,
           "shots": len(shots), "dialogue": sum(s["kind"] == "dlg" for s in shots),
           "broll": sum(s["kind"] == "broll" for s in shots),
           "vo": sum(bool(s.get("vo")) for s in shots), "text": sum(bool(s.get("text") or s.get("title")) for s in shots),
           "chapters": [{"name": c["name"], "tin": round(c["tin"], 2), "tout": round(c["tout"], 2), "target": round(c["dur"], 2)} for c in chapters],
           "fixes": fixes, "warnings": warns, "parser": chk, "shot_list": shot_list(chapters)}
    if not a.no_leftovers:
        days, lst = leftovers(S, chapters)
        lmd = os.path.splitext(a.out)[0] + "_CHUADUNG.md"
        with open(lmd, "w", encoding="utf-8") as f:
            f.write(leftovers_md(spec.get("title"), days, lst))
        lst["md"] = lmd
        lst["coverage_pct"] = round(100.0 * lst["used_s"] / lst["good_s"], 1) if lst["good_s"] else 100.0
        lst["parser"] = parser_check(lmd)
        lst["by_day"] = {d: [dict(kind=s["kind"], file=s["file"], a=s["a"], b=s["b"], q=s["q"]) for s in it] for d, it in days.items()}
        rep["leftovers"] = lst
    with open(os.path.splitext(a.out)[0] + ".report.json", "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print("✔ Kịch bản ghim nguồn: %s · %d phân cảnh · %d shot (%d thoại, %d b-roll) · %d VO · %d text" %
          (tc(total), len(chapters), rep["shots"], rep["dialogue"], rep["broll"], rep["vo"], rep["text"]))
    print("  Parser: %d phân cảnh · %s · thiếu nguồn %d" % (chk["scenes"], chk["types"], len(chk["missing"])))
    if fixes:
        print("  Tự sửa %d chỗ:" % len(fixes))
        for x in fixes[:12]:
            print("   · " + x)
        if len(fixes) > 12:
            print("   · … +%d (xem .report.json)" % (len(fixes) - 12))
    for x in warns[:12]:
        print("  ⚠ " + x)
    L = rep.get("leftovers")
    if L:
        print("✔ Kho chưa dùng: %s · %d thoại + %d hình (%s) · kịch bản chính phủ %.0f%% hình đạt chuẩn · %d clip ĐẠT chưa đụng tới" %
              (os.path.basename(L["md"]), L["left_dialogue"], L["left_broll"], tc(L["left_s"]), L["coverage_pct"], L["untouched_clips"]))
        if L["weak_clips"]:
            print("  ⓘ %d clip YẾU không đưa vào kho: %s" % (len(L["weak_clips"]), ", ".join(L["weak_clips"][:8])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
