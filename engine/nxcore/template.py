"""Template sequence từ video tham khảo (tab Reverse) — Justin EDIT Suite.

  make : reverse.json + reverse_ai.json → template.json (slot = từng shot gốc: thời lượng, cỡ cảnh, máy, tốc độ,
         chuyển cảnh, màu, vai trò thoại/b-roll, cấu trúc, beat) + guide_audio.wav (tiếng gốc làm nhạc dẫn).
  fill : template.json + folder source ĐÃ PHÂN TÍCH (bước 1) → plan.json cho nxcut build (0 token AI):
         mỗi slot lấy đoạn đạt chuẩn khớp nhất (vai trò, chuyển động máy, slow-mo, độ sáng / tông màu, mô tả, chất lượng),
         slot không có hình hợp → thẻ chữ đúng thời lượng (slate) để tự thay. Giữ nguyên nhịp cắt, dissolve, slow-mo,
         marker cấu trúc + ghi chú từng shot, nhạc dẫn.

  python -m nxcore.template make REVDIR
  python -m nxcore.template fill TEMPLATE --plan OUT/plan_X.json [--src SRC] [--name X] [--out OUT] [--seq 1080x1920@30:fill]
                                [--music guide|none|FILE] [--music-db -6] [--no-fx] [--no-text] [--no-sfx]
                                [--sfx-index sfx_index.json] [--sfx-db 0]
  fill còn áp: zoom keyframe / punch scale lên thẻ clip V1, chữ (text/title PNG V4 đúng vị trí + thời điểm gốc),
  SFX thật từ thư viện (A3/A7, đỉnh âm khớp thời điểm đo trên video gốc).
"""
import argparse
import collections
import math
import os
import re
import subprocess
import sys
import unicodedata

from . import jsonio

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

VERSION = 2
ROLE_COLOR = {"dialogue": "Mango", "broll": "Cerulean", "slowmo": "Teal", "gap": "Rose"}


def _j(p, d=None):
    return jsonio.load(p, d)


def log(*a):
    print(*a, flush=True)


# ───────────────────────────────────────────── chuẩn hoá mô tả → token để so khớp
STOP = set("và của các một những là có cho với trong trên dưới ra vào đang được không này kia đó the a an of and to in on at with is are by from".split())


def toks(s):
    s = unicodedata.normalize("NFD", str(s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d")
    return {w for w in re.findall(r"[a-z0-9]{2,}", s) if w not in STOP}


def motion_class(cam_ai, cam_eng):
    """chuyển động máy của slot: static | move | handheld | cgi"""
    t = (str(cam_ai or "") + " " + str(cam_eng or "")).lower()
    if "handheld" in t or "cầm tay" in t or "rung" in t:
        return "handheld"
    if any(k in t for k in ("push", "pull", "dolly", "pan", "tilt", "truck", "track", "orbit", "crane", "zoom", "gimbal", "xoay", "follow")):
        return "move"
    if "không đo chắc" in t or "cgi" in t:
        return "cgi"
    return "static"


def speed_class(sp):
    t = str(sp or "").lower()
    if not t or t.startswith("real"):
        return "real"
    if "slow" in t:
        return "slow"
    if "ramp" in t:
        return "ramp"
    if "time" in t or "lapse" in t:
        return "timelapse"
    return "real"


def xfade_of(tr):
    t = str(tr or "").lower()
    if any(k in t for k in ("dissolve", "fade", "mix", "hoà", "hòa", "mờ")):
        return 0.5
    if any(k in t for k in ("flash", "whip", "dip")):
        return 0.2
    return 0.0


SFX_ROLE = {"whoosh": "whoosh", "swoosh": "whoosh", "swish": "whoosh", "transition": "whoosh", "riser": "riser", "rise": "riser",
            "hit": "hit", "impact": "hit", "boom": "hit", "bass": "hit", "pop": "pop", "click": "pop", "typing": "pop", "type": "pop",
            "ding": "pop", "bubble": "pop", "ui": "pop", "glitch": "glitch", "digital": "glitch", "meme": "meme", "accent": "accent"}
FX_ZOOM_IN = ("push-in", "push in", "zoom in", "zoom-in", "dolly in", "dolly-in", "phóng to", "tiến vào")
FX_ZOOM_OUT = ("pull-out", "pull out", "zoom out", "zoom-out", "dolly out", "thu nhỏ", "lùi ra")
FX_PUNCH = ("punch", "zoom cut", "crop in", "scale up", "cắt cận", "phóng cận")
NOT_TALK = ("màn hình", "screen", "giao diện", "đồ hoạ", "đồ họa", "graphic", "3d", " ui", "slide", "chart", "biểu đồ", "logo", "text")


def sfx_role(kind):
    k = str(kind or "").lower()
    for key, role in SFX_ROLE.items():
        if key in k:
            return role
    return "accent"


def zoom_of(slot_cam, fx):
    t = (" ".join(fx) + " " + str(slot_cam or "")).lower()
    if any(k in t for k in FX_PUNCH):
        return None, 112.0
    if any(k in t for k in FX_ZOOM_IN):
        return [1.0, 1.08], None
    if any(k in t for k in FX_ZOOM_OUT):
        return [1.08, 1.0], None
    return None, None


def slot_texts(a, t0, t1):
    out = []
    for x in (a.get("text") or a.get("texts") or []):
        if isinstance(x, str):
            x = {"content": x}
        c = str(x.get("content") or x.get("text") or "").strip()
        if not c:
            continue
        a0 = float(x.get("t0", t0) if x.get("t0") is not None else t0)
        a1 = float(x.get("t1", a0 + 2.0) if x.get("t1") is not None else a0 + 2.0)
        a0 = min(max(a0, t0), t1 - 0.3); a1 = max(a0 + 0.6, min(a1, t1 + 2.0))
        kind = str(x.get("kind") or x.get("style") or "caption").lower()
        if any(k in kind for k in ("subtitle", "phụ đề", "sub")):     # phụ đề thoại của video gốc ≠ chữ đồ hoạ → bỏ
            continue
        out.append({"content": c[:80], "kind": kind, "pos": str(x.get("pos") or x.get("position") or ""), "at": round(a0 - t0, 2),
                    "dur": round(a1 - a0, 2), "style": "title" if any(k in kind for k in ("title", "tiêu đề", "chapter", "chương", "card")) else "text"})
    return out[:4]


def slot_sfx(a, t0, t1, events):
    """SFX của slot: số đo engine (thời điểm chính xác) + mô tả AI (loại âm) → [{at (giây từ đầu slot), kind, role, dur, src}]"""
    out = []
    for e in events or []:
        if t0 - 0.3 <= float(e["t"]) < t1 - 0.05:
            out.append({"t": float(e["t"]), "kind": e.get("kind") or "hit", "dur": float(e.get("dur") or 0.3), "src": "đo"})
    for x in (a.get("sfx") or []):
        if isinstance(x, str):
            x = {"kind": x}
        tt = x.get("t", x.get("at"))
        try:
            tt = float(tt) if tt is not None else t0
        except (TypeError, ValueError):
            tt = t0
        if tt < t0 - 0.3 or tt >= t1:
            tt = t0
        near = [o for o in out if abs(o["t"] - tt) <= 0.4]
        kind = str(x.get("kind") or x.get("type") or x.get("desc") or "accent")
        if near:
            near[0]["kind"] = kind; near[0]["desc"] = x.get("desc") or kind      # AI gọi tên, engine giữ thời điểm
        else:
            out.append({"t": tt, "kind": kind, "dur": 0.4 if sfx_role(kind) in ("hit", "pop") else 1.2, "src": "AI", "desc": x.get("desc") or ""})
    for o in out:
        o["role"] = sfx_role(o["kind"]); o["at"] = round(max(0.0, o.pop("t") - t0), 2)
    out.sort(key=lambda o: o["at"])
    keep = []
    for o in out:
        if not keep or o["at"] - keep[-1]["at"] >= 0.25:
            keep.append(o)
    return keep[:4]


# ───────────────────────────────────────────── make
def make(revdir):
    R = _j(os.path.join(revdir, "reverse.json"), None)
    A = _j(os.path.join(revdir, "reverse_ai.json"), None)
    if not R or not R.get("shots"):
        raise SystemExit("✖ chưa có reverse.json — Phân tích video trước")
    A = A or {}
    ai = {int(s.get("i")): s for s in (A.get("shots") or []) if s and s.get("i") is not None}
    speech = (R.get("audio") or {}).get("speech") or []
    struct = (A.get("editing") or {}).get("structure") or []
    beats_cut = (R.get("audio") or {}).get("cut_on_beat")
    events = (R.get("audio") or {}).get("sfx_events") or []
    slots = []
    for s in R["shots"]:
        a = ai.get(int(s["i"]), {})
        t0, t1 = float(s["t0"]), float(s["t1"])
        ov = sum(max(0.0, min(t1, b) - max(t0, x)) for x, b, _ in speech)
        talk = any(k in str(a.get("action", "")).lower() + " " + str(a.get("sound", "")).lower()
                   for k in ("nói", "phỏng vấn", "talk", "speak", "say", "thoại", "lip", "kể", "hát"))
        size = str(a.get("size") or "")
        what = (str(a.get("subject", "")) + " " + str(a.get("action", ""))).lower()
        if isinstance(a.get("talking"), bool):                 # AI thấy người nói trước máy (khẩu hình) hay hình minh hoạ
            on_cam = a["talking"]
        else:
            on_cam = talk and bool(re.search(r"\b(ECU|CU|MCU|MS)\b", size)) and not any(k in what for k in NOT_TALK)
        role = "dialogue" if ov >= 0.5 * (t1 - t0) and on_cam else "broll"
        fx = [str(x) for x in (a.get("fx") or []) if str(x).strip()][:6]
        zoom, punch = zoom_of(a.get("camera"), fx)
        part = next((p.get("part") for p in struct if float(p.get("t0", 0)) - 0.05 <= t0 < float(p.get("t1", 0))), "") or ""
        c = s.get("color") or {}
        slots.append({
            "i": int(s["i"]), "t0": round(t0, 3), "t1": round(t1, 3), "dur": round(t1 - t0, 3), "role": role, "part": part,
            "size": size, "angle": a.get("angle") or "", "lens": a.get("lens") or "",
            "camera": a.get("camera") or (s.get("cam") or {}).get("label") or "", "motion": motion_class(a.get("camera"), (s.get("cam") or {}).get("label")),
            "speed": speed_class(a.get("speed")), "subject": a.get("subject") or "", "action": a.get("action") or "", "light": a.get("light") or "",
            "transition_in": a.get("transition_in") or "cut", "xfade": xfade_of(a.get("transition_in")) if int(s["i"]) > 1 else 0.0,
            "sound": a.get("sound") or "",
            "look": {"L": c.get("L"), "warm": c.get("warm"), "sat": c.get("sat"), "key": c.get("key"), "tone": c.get("tone"),
                     "palette": [p[0] for p in (c.get("palette") or [])[:3]]},
            "speech": " ".join(x[2] for x in speech if x[1] > t0 + 0.05 and x[0] < t1 - 0.05)[:200],
            "voice_over": ov >= 0.5 * (t1 - t0) and role == "broll",     # tiếng nói chạy liền qua hình minh hoạ
            "fx": fx, "zoom": zoom, "punch": punch,
            "texts": slot_texts(a, t0, t1), "sfx": slot_sfx(a, t0, t1, events),
        })
    info = R.get("info") or {}
    guide = os.path.join(revdir, "guide_audio.wav")
    if info.get("has_audio") and not os.path.exists(guide):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-i", R["video"], "-vn", "-ac", "2", "-ar", "48000", guide],
                       capture_output=True)
    T = {"v": VERSION, "name": os.path.splitext(R.get("name") or "template")[0], "reference": R.get("video"), "key": R.get("key"),
         "w": info.get("w"), "h": info.get("h"), "ratio": info.get("ratio"), "fps": info.get("fps"), "dur": info.get("dur"),
         "bpm": (R.get("audio") or {}).get("bpm"), "cut_on_beat": beats_cut, "edit": R.get("edit"), "look": R.get("look"),
         "overview": A.get("overview") or {}, "editing": A.get("editing") or {}, "structure": struct,
         "sfx_events": len(events), "graphics": (A.get("editing") or {}).get("text_graphics") or "",
         "guide_audio": guide.replace("\\", "/") if os.path.exists(guide) else "", "slots": slots}
    p = os.path.join(revdir, "template.json")
    jsonio.save_atomic(p, T)
    nd = sum(1 for s in slots if s["role"] == "dialogue")
    log("✔ Template: %d slot (%d thoại · %d b-roll) · %.1fs · %s · %d chữ · %d SFX · %d hiệu ứng" % (
        len(slots), nd, len(slots) - nd, T["dur"] or 0, T["ratio"] or "", sum(len(s["texts"]) for s in slots),
        sum(len(s["sfx"]) for s in slots), sum(1 for s in slots if s["zoom"] or s["punch"] or s["fx"])))
    log("template: " + p.replace("\\", "/"))
    return T


# ───────────────────────────────────────────── kho đoạn hình của source đã phân tích
def clip_look(W, m):
    """độ sáng / ấm-lạnh của clip từ thumbnail (cache theo key clip)"""
    th = m.get("thumb")
    if not th:
        return None
    p = os.path.join(W, th)
    if not os.path.exists(p):
        return None
    try:
        import cv2
        import numpy as np
        im = cv2.imread(p)
        if im is None:
            return None
        im = cv2.resize(im, (96, max(1, int(96 * im.shape[0] / im.shape[1]))))
        lab = cv2.cvtColor(im, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
        hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(np.float32)
        return {"L": float(lab[:, 0].mean() * 100 / 255), "warm": float((lab[:, 2] - 128).mean()), "sat": float(hsv[:, 1].mean() / 255)}
    except Exception:
        return None


def source_pool(src, min_len=0.6, q_min=40):
    from .rough import clip_pieces
    W = os.path.join(src, "_nexys_work")
    media = _j(os.path.join(W, "media.json"), {}) or {}
    if not media:
        raise SystemExit("✖ folder source chưa phân tích (thiếu _nexys_work/media.json) — chạy bước 1 · Phân tích ở tab Chạy")
    D = _j(os.path.join(W, "digest.json"), {}) or {}
    dclips = {c.get("file"): c for c in (D.get("clips") or {}).values()}
    segj = (_j(os.path.join(W, "segments.json"), {}) or {}).get("clips") or {}
    shake = _j(os.path.join(W, "shake.json"), {}) or {}
    qc = _j(os.path.join(W, "qc.json"), {}) or {}
    qc = qc.get("clips", qc) if isinstance(qc, dict) else {}
    roles = _j(os.path.join(W, "roles.json"), {}) or {}
    ad = (_j(os.path.join(W, "auto_desc.json"), {}) or {}).get("clips") or {}
    sai = (_j(os.path.join(W, "source_ai.json"), {}) or {}).get("clips") or {}
    tags = _j(os.path.join(W, "broll_tags.json"), {}) or {}
    pool = []
    for f, m in media.items():
        if m.get("status") not in (None, "", "ok") or str((dclips.get(f) or {}).get("status") or "") == "LOẠI":
            continue
        if (roles.get(f) or {}).get("role") in ("junk", "audio_master", "screen"):
            continue
        pcs = clip_pieces(f, m, segj.get(f), shake, qc, min_len, q_min, False)
        if not pcs:
            continue
        a_ = sai.get(f) or {}
        words = " ".join(str(x) for x in [(ad.get(f) or {}).get("desc"), a_.get("summary"), a_.get("shot"), a_.get("camera"),
                                           " ".join(a_.get("tags") or []), tags.get(f) if isinstance(tags.get(f), str) else " ".join(tags.get(f) or []),
                                           " ".join(m.get("tags") or [])] if x)
        look = clip_look(W, m)
        fps = float(m.get("fps") or 25)
        for a, b, kind, sp, q in pcs:
            seg_d = ""
            for k, v in ((ad.get(f) or {}).get("segs") or {}).items():
                try:
                    x0, x1 = [float(x) for x in str(k).replace("–", "-").split("-")[:2]]
                except ValueError:
                    continue
                if min(b, x1) - max(a, x0) > 0 and (v or {}).get("desc"):
                    seg_d += " " + v["desc"]
            pool.append({"file": f, "a": float(a), "b": float(b), "kind": kind, "speech": float(sp or 0), "q": float(q if q is not None else 60),
                         "fps": fps, "dur": float(m.get("dur") or 0), "audio": bool(m.get("audio", True)), "look": look,
                         "toks": toks(words + " " + seg_d), "desc": (seg_d.strip() or (ad.get(f) or {}).get("desc") or a_.get("summary") or "")[:90]})
    return pool, media


def score(slot, c, need):
    L = c["b"] - c["a"]
    if L < need - 0.02:
        return None
    s = 0.0
    sp = c["speech"]
    if slot["role"] == "dialogue":
        s += 4.0 if sp >= 0.3 else -4.0
    else:
        s += 1.0 if sp < 0.3 else -2.5
    mo, k = slot["motion"], c["kind"]
    s += {("static", "stable"): 2.0, ("static", "static"): 2.5, ("move", "stable"): 1.2, ("move", "handheld"): 0.8,
          ("handheld", "handheld"): 2.0, ("handheld", "stable"): 0.3, ("cgi", "stable"): 0.5}.get((mo, k), 0.0)
    if slot["speed"] == "slow":
        s += 2.0 if c["fps"] >= 47 else -1.0
    lk, cl = slot.get("look") or {}, c.get("look")
    if cl and lk.get("L") is not None:
        s -= abs(float(lk["L"]) - cl["L"]) / 22.0
        if lk.get("warm") is not None:
            s -= abs(float(lk["warm"]) - cl["warm"]) / 18.0
    st = slot.get("_toks") or set()
    if st and c["toks"]:
        s += 3.0 * len(st & c["toks"]) / math.sqrt(len(st) * len(c["toks"]) + 1)
    s += (c["q"] - 60) / 40.0
    s += min(1.0, (L - need) / max(need, 1.0)) * 0.3          # dư đất → chọn được khúc giữa ổn định
    return s


def sfx_index_path(p=""):
    if p:
        return p
    base = os.environ.get("APPDATA") or os.environ.get("HOME") or os.path.expanduser("~")    # = the panel's NX.cfgDir
    return os.path.join(base, "JustinEditSuite", "sound", "sfx_index.json")


def sfx_style_of(T):
    t = (str((T.get("editing") or {}).get("sound") or "") + " " + str(T.get("graphics") or "")).lower()
    if any(k in t for k in ("glitch", "tech", "digital", "công nghệ", "ui")):
        return "tech"
    if any(k in t for k in ("meme", "fun", "vui", "hài")):
        return "fun"
    if any(k in t for k in ("cinematic", "trailer", "epic")):
        return "cinematic"
    return "clean"


def place_sfx(T, slots, idx_path, db=0.0, seed=7):
    """SFX của template → P["sfx_items"]: file thật trong thư viện (sfx_index của tab Âm thanh), đặt sao cho ĐỈNH âm rơi đúng
    thời điểm đo trên video gốc: hit/pop/glitch → đỉnh tại sự kiện · whoosh → đỉnh giữa sự kiện · riser → đỉnh tại cuối (điểm vào cảnh)."""
    evs = [(sl["t0"] + float(e["at"]), e) for sl in slots for e in (sl.get("sfx") or [])]
    if not evs:
        return [], [], "không có SFX trong template"
    idx = _j(idx_path, None) if idx_path and os.path.exists(idx_path) else None
    if not idx or not idx.get("files"):
        mk = [[round(t, 2), "SFX " + e["role"] + ": " + str(e.get("desc") or e["kind"])[:60]] for t, e in evs]
        return [], mk, "chưa có thư viện SFX (tab Âm thanh → quét thư viện) — %d SFX ghi thành marker" % len(evs)
    from .sounddesign import Picker, gain_for, MAX_LEN
    style = sfx_style_of(T)
    pk = Picker(idx, style, seed)
    items, mk, miss = [], [], 0
    for t, e in sorted(evs, key=lambda x: x[0]):
        role, dur = e["role"], float(e.get("dur") or 0.4)
        want_peak = dur if role == "riser" else None
        f = pk.pick(role, want_dur=None if want_peak else dur, want_peak=want_peak) or (pk.pick("accent", want_dur=dur) if role != "accent" else None)
        if not f:
            miss += 1; mk.append([round(t, 2), "SFX " + role + " (thư viện không có): " + str(e.get("desc") or e["kind"])[:50]]); continue
        peak = float(f.get("peak_t") or 0)
        anchor = t + dur if role == "riser" else t + dur / 2.0 if role == "whoosh" else t
        items.append({"file": f["path"], "at": round(anchor - peak, 3), "role": role, "gain_db": round(gain_for(f, f.get("role") or role) + float(db), 1),
                      "max_len": float(MAX_LEN.get(role, 3.0)), "src": e.get("src", ""), "desc": str(e.get("desc") or e["kind"])[:60]})
    note = "SFX: %d đặt từ thư viện (style %s)%s" % (len(items), style, (" · %d không có loại phù hợp → marker" % miss) if miss else "")
    return items, mk, note


def fill(T, src, name, out, seq, music="guide", music_db=-6.0, fx=True, texts=True, sfx=True, sfx_index="", sfx_db=0.0):
    from .montage import base_plan
    slots = T["slots"]
    for s in slots:
        s["_toks"] = toks(" ".join([s.get("subject", ""), s.get("action", ""), s.get("size", ""), s.get("light", "")]))
    pool, media = (source_pool(src) if src else ([], {}))
    if not src:                                     # chỉ khung: slot nào cũng là thẻ chữ để tự thay
        src = os.path.join(out, "_template_stub")
        os.makedirs(os.path.join(src, "_nexys_work"), exist_ok=True)
        jsonio.save_atomic(os.path.join(src, "_nexys_work", "media.json"), {})
    # slot khó trước (thoại, dài, slow-mo) — rồi dựng lại theo thứ tự timeline
    order = sorted(range(len(slots)), key=lambda k: (slots[k]["role"] != "dialogue", -slots[k]["dur"]))
    pick = {}
    for k in order:
        sl = slots[k]
        slow = sl["speed"] == "slow"
        best, bs = None, -1e9
        near = {pick[j]["file"] for j in (k - 2, k - 1, k + 1, k + 2) if j in pick}
        for ci, c in enumerate(pool):
            need = sl["dur"] * (0.5 if slow and c["fps"] >= 47 else 1.0)
            v = score(sl, c, need)
            if v is None:
                continue
            if c["file"] in near:
                v -= 3.0
            if v > bs:
                bs, best = v, (ci, need)
        if best is None or bs < -3.5:
            continue
        ci, need = best
        c = pool[ci]
        L = c["b"] - c["a"]
        a0 = c["a"] if (sl["role"] == "dialogue" or L - need < 0.4) else c["a"] + (L - need) / 2.0
        pick[k] = {"file": c["file"], "in": round(a0, 3), "out": round(a0 + need, 3), "speech": c["speech"], "fps": c["fps"],
                   "score": round(bs, 2), "desc": c["desc"], "audio": c["audio"], "kind": c["kind"]}
        # tiêu thụ đoạn đã dùng: tách phần còn lại thành 2 mẩu
        rest = []
        if a0 - c["a"] >= 0.6:
            rest.append(dict(c, b=a0))
        if c["b"] - (a0 + need) >= 0.6:
            rest.append(dict(c, a=a0 + need))
        pool[ci:ci + 1] = rest
    # plan
    C = {"transitions": {"on": True, "dur": 0.5, "scene": False, "broll_rate": 0, "fade_in": 0, "fade_out": 0, "audio": True}}
    P = base_plan(src, name, out, seq, C, "template")
    P.update({"autofix": {"slowmo": True, "rotate": True}, "seg_markers": True, "flag_markers": False, "chrono": False,
              "broll_audio": {"on": True, "gain_db": -18.0 if music != "none" else -6.0}, "subs": {"on": False},
              "template": {"name": T["name"], "reference": T.get("reference")}})
    first = slots[0] if slots else {}
    if str(first.get("transition_in", "")).lower().startswith("fade"):
        P["transitions"]["fade_in"] = 0.5
    segs, filled, gaps, nfx, ntx = [], 0, 0, 0, 0
    for k, sl in enumerate(slots):
        tl = [sl["t0"], sl["t1"]]
        brief = "#%d %s · %s%s" % (sl["i"], " ".join(x for x in (sl["size"], sl["angle"]) if x) or "shot", sl["camera"] or sl["motion"],
                                   " · " + sl["speed"] if sl["speed"] != "real" else "")
        what = (sl["subject"] + ": " + sl["action"]).strip(": ")
        scene = sl["part"] or "Template"
        if k in pick:
            pk = pick[k]
            slow = sl["speed"] == "slow" and pk["fps"] >= 47
            segs.append({"file": pk["file"], "in": pk["in"], "out": pk["out"], "tl": tl, "scene": scene,
                         "label": (brief + " — " + what)[:110], "audio": sl["role"] == "dialogue" and pk["speech"] >= 0.3,
                         "speed": 50 if slow else 100, "xfade": sl["xfade"], "color": ROLE_COLOR["slowmo" if slow else sl["role"]],
                         "silence": False, "subs": False, "flags": [], "exclude": [], "script_line": "", "rotate_fix": (media.get(pk["file"]) or {}).get("rotate_fix") or 0,
                         "template_slot": sl["i"], "match": pk["score"]})
            filled += 1
            if fx and not slow:
                sg = segs[-1]
                if sl.get("zoom") and pk.get("kind") in ("stable", "static") and sl["dur"] >= 1.5:
                    sg["zoom"] = sl["zoom"]; nfx += 1                  # push-in / pull-out bằng keyframe Scale trên thẻ clip
                elif sl.get("punch"):
                    sg["scale"] = float(sl["punch"]); nfx += 1         # punch-in (crop cận) = Scale tĩnh
                if sl.get("fx"):
                    sg["label"] = (sg["label"] + " · FX: " + ", ".join(sl["fx"][:3]))[:150]
        else:
            txt = "SLOT %d · %.1fs\n%s\n%s" % (sl["i"], sl["dur"], " · ".join(x for x in (sl["size"], sl["camera"], sl["speed"] if sl["speed"] != "real" else "") if x), what[:120])
            segs.append({"gap": True, "tl": tl, "in": 0, "out": sl["dur"], "text": txt, "label": ("⬚ " + brief + " — " + what)[:110],
                         "scene": scene, "color": ROLE_COLOR["gap"], "template_slot": sl["i"]})
            gaps += 1
        if texts and sl.get("texts"):
            segs[-1]["cues"] = [{"kind": "text", "text": x["content"], "offset": x["at"], "dur": x["dur"], "style": x["style"], "pos": x.get("pos", ""),
                                 "line": "T%d.%d" % (sl["i"], n + 1)} for n, x in enumerate(sl["texts"])]
            ntx += len(sl["texts"])
    P["segments"] = segs
    P["scene_names"] = {s["scene"]: s["scene"] for s in segs}
    ed, lk = T.get("editing") or {}, T.get("look") or {}
    notes = [[0.0, "TEMPLATE " + T["name"] + " — LOOK: " + str(ed.get("color") or "")[:100]]]
    if ed.get("lighting"):
        notes.append([0.04, "SÁNG: " + str(ed["lighting"])[:110]])
    if ed.get("sound"):
        notes.append([0.08, "ÂM THANH: " + str(ed["sound"])[:110]])
    sfx_note = "SFX: tắt"
    if sfx:
        items, smk, sfx_note = place_sfx(T, slots, sfx_index_path(sfx_index), sfx_db)
        if items:
            P["sfx_items"] = items
        notes += smk
    P["markers"] = notes
    mf = T.get("guide_audio") if music == "guide" else (music if music not in ("none", "", None) else "")
    if mf and os.path.exists(mf):
        P["music"] = [{"file": mf, "start": 0, "in": 0, "end": slots[-1]["t1"] if slots else None, "gain_db": float(music_db), "fade_out": 0.5}]
    P["report_notes"] = [
        "Template '%s': %d slot · %d lấp từ source · %d slot trống (thẻ chữ) · %.1fs" % (T["name"], len(slots), filled, gaps, slots[-1]["t1"] if slots else 0),
        "Nhạc: " + (os.path.basename(mf) if mf else "không"),
        "Hiệu ứng trên thẻ clip: %d (zoom keyframe / punch scale)%s" % (nfx, "" if fx else " — tắt"),
        "Chữ: %d text/title PNG lên V4%s" % (ntx, "" if texts else " — tắt"),
        sfx_note,
    ]
    rep = {"slots": len(slots), "filled": filled, "gaps": gaps, "music": mf or "", "fx": nfx, "texts": ntx,
           "sfx": len(P.get("sfx_items") or []),
           "picks": [{"slot": slots[k]["i"], "file": pick[k]["file"], "in": pick[k]["in"], "out": pick[k]["out"], "score": pick[k]["score"], "desc": pick[k]["desc"]}
                     for k in sorted(pick)]}
    return P, rep


def parse_seq(s, T):
    if s:
        wh, _, rest = s.partition("@")
        fps, _, fit = rest.partition(":")
        w, h = wh.lower().split("x")
        return {"w": int(w), "h": int(h), "fps": float(fps or 25), "fit": fit or "fill"}
    w, h = int(T.get("w") or 1920), int(T.get("h") or 1080)
    short = 1080
    if w >= h:
        W, H = int(round(short * w / h / 2) * 2), short
    else:
        W, H = short, int(round(short * h / w / 2) * 2)
    f = float(T.get("fps") or 25)
    f = 24 if f >= 47 else 30 if f >= 29 else 25 if f >= 24.5 else 24
    return {"w": W, "h": H, "fps": f, "fit": "fill"}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nxcore.template")
    sp = ap.add_subparsers(dest="cmd", required=True)
    a1 = sp.add_parser("make"); a1.add_argument("revdir")
    a2 = sp.add_parser("fill")
    a2.add_argument("template"); a2.add_argument("--plan", required=True); a2.add_argument("--src", default="")
    a2.add_argument("--name", default=""); a2.add_argument("--out", default=""); a2.add_argument("--seq", default="")
    a2.add_argument("--music", default="guide"); a2.add_argument("--music-db", type=float, default=-6.0)
    a2.add_argument("--no-fx", action="store_true"); a2.add_argument("--no-text", action="store_true"); a2.add_argument("--no-sfx", action="store_true")
    a2.add_argument("--sfx-index", default=""); a2.add_argument("--sfx-db", type=float, default=0.0)
    a = ap.parse_args(argv)
    if a.cmd == "make":
        make(a.revdir); return 0
    T = _j(a.template, None)
    if not T or not T.get("slots"):
        log("✖ template trống / không đọc được: " + a.template); return 2
    out = a.out or os.path.dirname(os.path.abspath(a.plan))
    name = a.name or ("TPL_" + re.sub(r"[^\w\-]+", "_", T["name"])[:40])
    P, rep = fill(T, a.src, name, out, parse_seq(a.seq, T), a.music, a.music_db,
                  fx=not a.no_fx, texts=not a.no_text, sfx=not a.no_sfx, sfx_index=a.sfx_index, sfx_db=a.sfx_db)
    os.makedirs(os.path.dirname(os.path.abspath(a.plan)), exist_ok=True)
    jsonio.save_atomic(a.plan, P)
    jsonio.save_atomic(os.path.splitext(a.plan)[0] + ".template.json", rep)
    for n in P["report_notes"]:
        log("✔ " + n if n.startswith("Template") else "  " + n)
    log("plan: " + a.plan.replace("\\", "/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
