"""Assemble an nxcut plan.json from alignment results + script meta + b-roll tags.

Order of the sequence:
  HOOK (lines the script asks to move to the opening)
  → per scene, in script order: spoken picks (merged when consecutive in one clip),
    then b-roll for the scene's visual notes (matched by tags), multicam base + cutaways
  → end slate.

B-roll tags live in <src>/_nexys_work/broll_tags.json (written by a vision pass — Gemini / Claude — or by hand):
  {"C0131.MP4": {"tags": ["makeup", "cận"]},
   "Record_x.mp4": {"tags": ["màn hình live", "nhảy"], "role": "master", "use": [24, 54]},
   "C0145.MP4": {"tags": ["góc thực tế", "nhảy"], "role": "angle"},
   "IMG_7524.MOV": {"tags": ["đến công ty"], "place": "intro", "use": [2.1, 7.0]}}
"""
import json
import os
import re
from collections import Counter

from .textnorm import syllables, content, syl_sim

ASM_CFG = {
    "broll_dur": 2.5, "broll_dur_static": 2.0, "broll_dur_max": 3.5, "broll_min_clip": 1.2, "broll_min_score": 0.14,
    "broll_per_line": 1, "auto_slowmo": True, "slowmo_speed": 50, "second_shot_ratio": 0.75, "dup_window_s": 150, "dup_sim": 0.4,
    # long takes split into segments (nxcore.segments): one clip may give several shots
    "broll_segments": True, "broll_from_used_clips": True, "seconds_per_use": 30.0, "max_uses_per_clip": 4,
    "seg_gap_s": 2.0, "seg_dup_sim": 0.8,
    # shooting-time order (nxcore.chrono): "auto" = on for the vlog_travel / event_recap presets
    "chrono": "auto", "chrono_weight": 0.2, "chrono_lambda": 0.5, "chrono_topk": 12,
    "broll_max_per_scene": 12, "multicam_base": 30.0, "angle_dur": 2.5,
    "merge_gap": 0.35, "hook_title": "HOOK – diễn xuất", "intro_same_day": True, "end_slate": 2.0,
    "keep_hook_in_scene": True,  # opening hook is an excerpt; also keep the line in its story scene
    "pin_max": 8.0,          # a script-pinned shot ("Nguồn: X @ a–b") keeps its range up to this length
    "fill_to_target": 0.9,   # below this share of the brief duration, b-roll shots are lengthened inside their usable range
    "gap_placeholders": True,  # missing footage → a text card with reserved duration on the timeline (build)
    "fill_max": 6.0,         # … but never beyond this length per shot (keeps the pace)
    "punch_in": True,        # Ctrl+K jump-cut smoothing: alternate 112% scale on consecutive takes of same shot
    "punch_in_scale": 112.0, # 112% scale punch-in
    "j_l_cut": True,         # Rolling edit (N): L-cut extends dialogue audio under B-roll, J-cut leads dialogue
    "l_cut_s": 0.35,         # L-cut audio extension (s)
    "j_cut_s": 0.30,         # J-cut audio lead (s)
    "micro_crossfade": True, # Ctrl+Shift+D: 2-frame audio crossfades at dialogue cuts
    "jump_cover": True,      # stitched / filler-cut jump cuts get a short B-roll cutaway when the scene has spare B-roll
    "jump_cover_min": 1.5, "jump_cover_max": 2.5,
}


def _jload(p, d):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return d


def stable_window(dur, L, ranges, use=None):
    """Pick [a, a+L] inside the clip (or its `use` range) that avoids shake, closest to the middle.
    Vectorized with numpy for 15x-20x acceleration.
    `use` is clamped to the clip: a pin past the end / an inverted or < 0.5 s range falls back to the whole clip
    (minus 0.3 s edges) — callers report such pins (assemble: report_notes)."""
    import numpy as np
    dur = float(dur or 0)
    lo, hi = (use if use else (0.3, dur - 0.3))
    lo, hi = max(0.0, float(lo)), min(dur, float(hi))
    if hi - lo < 0.5:
        lo, hi = 0.3, dur - 0.3
        if hi - lo < 0.5:
            lo, hi = 0.0, dur
    L = min(float(L), max(0.5, hi - lo))
    if hi - lo <= L + 1e-4:
        return round(lo, 2), round(min(float(dur), lo + L), 2)
    mid = (lo + hi - L) / 2
    rs = [r for r in ranges if r[1] > lo and r[0] < hi]
    if not rs:
        return round(mid, 2), round(mid + L, 2)
    step = max(0.25, (hi - lo - L) / 300.0)
    ks = np.arange(lo, hi - L + 1e-6, step, dtype=np.float32)
    if len(ks) == 0:
        return round(lo, 2), round(lo + L, 2)
    total_bad = np.zeros(len(ks), dtype=np.float32)
    for r in rs:                 # shake.json ranges carry a 3rd value (score): [a, b, s]
        ov = np.maximum(0.0, np.minimum(ks + L, float(r[1])) - np.maximum(ks, float(r[0])))
        total_bad += ov
    cost = np.round(total_bad, 2) * 10000.0 + np.abs(ks - mid)
    best_idx = int(np.argmin(cost))
    a = float(ks[best_idx])
    return round(a, 2), round(a + L, 2)


_SPEED_KW = re.compile(r"(?:slow[\s\-]*(?:mo(?:tion)?)?|tốc\s*độ|speed|·)\s*[:=]?\s*(?:x\s*)?(\d{1,4}(?:[.,]\d+)?)\s*%")


def _pin_speed(text):
    """Speed written on a strict B-roll line: "· 50%", "slow 25%", "tốc độ 200%", "timelapse ×121".
    A bare percentage is NOT a speed ("giảm 50% giá", "100% cotton") → it needs one of those keywords before it.
    Clamped to 5–10000 % (Premiere's range)."""
    tl = (text or "").lower()
    m_ = re.search(r"timelapse\s*[×x]\s*([\d.,]+)", tl)
    if m_:
        sp = 100.0 * float(m_.group(1).replace(",", "."))
    else:
        m_ = _SPEED_KW.search(tl)
        sp = float(m_.group(1).replace(",", ".")) if m_ else 100.0
    return round(min(10000.0, max(5.0, sp)), 2)


def _talk(d):
    """Piece with live dialogue on screen (or a card holding the place of missing dialogue)."""
    return (bool(d.get("audio", True)) and not d.get("gap")) or d.get("gap_kind") == "thoại"


def _tag_score(tags, text_syl):
    ts = set(text_syl)
    sc = 0
    for tg in tags:
        tc = content(syllables(tg)) or syllables(tg)
        if tc and all(any(syl_sim(c, t) >= 0.8 for t in ts) for c in tc):
            sc += len(tc)
    return sc


def _raw_phrase(toks, i0, i1):
    out = []
    for t in toks[i0:i1 + 1]:
        if not out or out[-1] != t.raw:
            out.append(t.raw)
    return " ".join(out)


def find_keyword(toks, i0, i1, kw):
    """Locate keyword syllables inside toks[i0..i1]; returns (tok_start, tok_end) or None."""
    ks = syllables(kw)
    for j in range(i0, i1 - len(ks) + 2):
        if all(syl_sim(ks[k], toks[j + k].syl) >= 0.8 for k in range(len(ks))):
            return j, j + len(ks) - 1
    return None


def build_plan(lines, meta, cands, chosen, clips, media, shake, src, base=None, name=None,
               tags=None, keywords=(), cfg=None):
    learned = {}
    if (cfg or {}).get("learn", True) and not os.environ.get("JES_NO_LEARN"):
        try:
            from . import prefs
            learned = {k: v for k, v in prefs.asm_overrides().items() if k in ASM_CFG or k in ("quality_weight", "min_quality")}
        except Exception:
            learned = {}

    preset_data = {}
    preset_p = os.path.join(src, "_nexys_work", "preset.json")
    if (cfg or {}).get("preset"):
        try:
            from .presets import get_preset
            preset_data = get_preset(cfg["preset"])
        except Exception:
            pass
    elif os.path.exists(preset_p):
        preset_data = _jload(preset_p, {})

    # presets / learned prefs speak "auto_slowmo_60p"; the engine reads "auto_slowmo"
    preset_data = dict(preset_data or {})
    if "auto_slowmo_60p" in preset_data and "auto_slowmo" not in preset_data:
        preset_data["auto_slowmo"] = bool(preset_data["auto_slowmo_60p"])
    # the genre preset is a deliberate choice for THIS video: learned habits only fill keys the preset leaves open
    learned = {k: v for k, v in learned.items() if k not in preset_data and k not in (cfg or {})}
    # the editor's style book (rules they approved) is stronger than the preset; this run's own settings still win
    style, style_tr = {}, {}
    if (cfg or {}).get("learn", True):
        try:
            from . import stylebook
            pid = (cfg or {}).get("preset") or preset_data.get("id") or ""
            style = {k: v for k, v in stylebook.params("assemble", pid).items() if k not in (cfg or {})}
            style_tr = stylebook.params("transition", pid)
        except Exception:
            style, style_tr = {}, {}
    C = {**ASM_CFG, **learned, **preset_data, **style, **(cfg or {})}
    base = dict(base or {})
    srcn = src.replace("\\", "/").rstrip("/")
    folder = os.path.basename(srcn).replace(" ", "_")
    tags = tags if tags is not None else _jload(os.path.join(src, "_nexys_work", "broll_tags.json"), {})
    P = {
        "name": name or base.get("name") or f"{folder}_SCRIPT_9x16_v1",
        "bin": base.get("bin", folder), "src": srcn, "out": base.get("out", srcn + "/_nexys_out"),
        "seq": base.get("seq", preset_data.get("seq", {"w": 1080, "h": 1920, "fps": 29.97})),
        "silence": base.get("silence", preset_data.get("silence", {"on": True, "gap": 0.25, "pad_in": 0.06, "pad_out": 0.08, "min_piece": 0.25})),
        "shake": base.get("shake", {"on": True}),
        "subs": base.get("subs", preset_data.get("subs", {"on": True, "max_chars": 28, "replace": {}})),
        "sfx": base.get("sfx", {"on": False}), "highlights_on": True,
        "punch_in": C.get("punch_in", True), "punch_in_scale": C.get("punch_in_scale", 112.0),
        "broll_audio": {"on": True, "gain_db": preset_data.get("broll_nat_gain_db", -12.0)},
        "bgm_ducking_db": preset_data.get("bgm_ducking_db", -22.0),
        "chapters": bool(preset_data.get("chapters", False)),       # YouTube chapters file at build (long-form presets)
        "scene_names": {}, "end_slate": C["end_slate"] if meta.end_note else base.get("end_slate", 0),
        "segments": [], "overlays": [], "highlights": [],
        "missing": [], "broll_requests": [], "vo_lines": [], "unused_speech": [], "report_notes": [],
        "source_gaps": [{"line": ln.id, "scene": ln.scene, "text": ln.text, "note": ln.src_note}
                        for ln in lines if getattr(ln, "src_note", "")],
    }
    day = lambda f: (media.get(f, {}).get("created") or "")[:10]
    from .align import spans as _spans
    from .media import resolve_loose
    # STRICT_PIN_PATCH: "Nguồn: FILE @ a–b" is an instruction, not a hint → that file, that range, the speed written
    # on the line ("· 50%", "slow 25%", "timelapse ×121"), in script order. Picker / dedupe / fill / shake-shift never touch it.
    strict, pin_range_bad = {}, []
    for ln in lines:
        for p_ in (getattr(ln, "src", None) or []):
            f_ = resolve_loose(p_.get("file"), media)
            t_ = p_.get("t")
            if not (f_ and t_):
                continue
            d_ = float(media[f_].get("dur") or 0)
            t0_, t1_ = float(t_[0]), float(t_[1])
            if d_ and (t0_ >= d_ - 0.1 or t1_ <= t0_):
                pin_range_bad.append(f"{ln.id}: Nguồn {f_} @ {t0_:g}–{t1_:g}s nằm ngoài clip ({d_:.1f}s) → bỏ in–out ghi, "
                                     "lấy đoạn ổn định trong clip")
            elif d_ and t1_ > d_ + 0.5:
                pin_range_bad.append(f"{ln.id}: Nguồn {f_} @ {t0_:g}–{t1_:g}s vượt độ dài clip ({d_:.1f}s) → cắt còn {t0_:g}–{d_:.1f}s")
            if ln.type in ("broll", "action", "dialogue", "question") and ln.id not in strict:
                b_ = min(t1_, d_ or t1_)
                if b_ - t0_ >= 0.1:
                    strict[ln.id] = (f_, round(t0_, 3), round(b_, 3), id(ln.src))
    strict_files = {v[0] for v in strict.values()}
    byid = {ln.id: ln for ln in lines}
    # picks that really go on the timeline (a strict line plays its pinned range instead, except as hook excerpt)
    live = {lid: a for lid, a in chosen.items() if lid not in strict or getattr(byid.get(lid), "hook", False)}
    used_files = {f for a in live.values() for f, *_ in _spans(a)}
    kw_hits = []  # (file, tok_i0, tok_i1, text, style)

    pending_cues = []   # VO_TEXT_PATCH: [VO] / [TEXT] lines waiting for the next shot to anchor on
    vo_state = {"debt": 0.0, "text": "", "line": "vo"}   # debt = s of VO still talking past the last piece
    pk_ref = [None]     # b-roll Picker (built after the hook pieces)

    def vo_cover(scene_key):
        """VO needs picture WITHOUT live dialogue under it. Called when the next piece talks (or the scene ends)
        while VO is pending / still running: B-roll matching the narration first, a card for whatever is left."""
        vo = [c for c in pending_cues if c["kind"] == "vo"]
        start = vo_state["debt"] + (0.2 if vo_state["debt"] > 0.05 else 0.0)   # build never lets VO overlap VO
        for c in vo:                         # place the pending VO on the cover, one after the other
            c["offset"] = round(max(float(c.get("offset") or 0), start), 2)
            start = c["offset"] + float(c["dur"]) + 0.2
        if vo:
            vo_state["text"] = " ".join(c["text"] for c in vo)
            vo_state["line"] = "+".join(c["line"] for c in vo)
        need = max(vo_state["debt"], start - 0.2 if vo else 0.0)
        text = vo_state["text"]
        for _ in range(4):
            if need <= 0.05:
                return
            got = pk_ref[0].cover(text, min(need + 0.1, float(C.get("fill_max", 6.0)))) if pk_ref[0] and text else None
            if not got:
                break
            v, a, b, sc = got
            slow = v.get("_slowmo") and C.get("auto_slowmo", True)
            piece({"file": v["file"], "in": a, "out": b, "scene": scene_key, "label": "B-roll (VO): " + text[:80],
                   "script_line": vo_state["line"], "score": sc, "audio": False, "silence": False, "subs": False,
                   "exclude": [], "flags": [f"B-roll phủ VO (khớp {sc:.2f}) — VO không đè thoại"],
                   **({"speed": C["slowmo_speed"]} if slow else {}), **({"seg": v["seg"]} if v.get("seg") else {})})
            need = vo_state["debt"]
        if need > 0.05:
            piece({"file": "", "gap": True, "gap_kind": "vo", "in": 0.0, "out": round(need, 2), "scene": scene_key,
                   "text": "VO — cần B-roll\n" + text, "label": ("VO chưa có hình: " + text)[:120],
                   "script_line": vo_state["line"], "score": 0, "audio": False, "silence": False, "subs": False,
                   "exclude": [], "flags": [f"chừa {need:.1f}s hình cho VO (không để VO đè thoại)"],
                   "suggest": pk_ref[0].suggest(text, 3) if pk_ref[0] and text else []})

    def piece(d):
        """Append a segment; merge with the previous one when it continues the same clip.
        VO cues ride only on pieces without live dialogue (B-roll / cards); see vo_cover()."""
        talk = _talk(d)
        if talk and (vo_state["debt"] > 0.05 or any(c["kind"] == "vo" for c in pending_cues)):
            vo_cover(d["scene"])
        if pending_cues:
            mine = [c for c in pending_cues if not talk or c["kind"] != "vo"]
            d.setdefault("cues", []).extend(mine)
            pending_cues[:] = [c for c in pending_cues if all(c is not x for x in mine)]
        if not talk:
            L = _seg_len(d)
            vo_state["debt"] = max([0.0, vo_state["debt"] - L] + [float(c.get("offset") or 0) + float(c["dur"]) - L
                                                                  for c in d.get("cues", []) if c["kind"] == "vo"])
            for c in d.get("cues", []):
                if c["kind"] == "vo":
                    vo_state["text"], vo_state["line"] = c["text"], c["line"]
        prev = P["segments"][-1] if P["segments"] else None
        if (prev and not d.get("gap") and not prev.get("gap") and prev["file"] == d["file"] and prev.get("audio", True) == d.get("audio", True)
                and -2.0 <= d["in"] - prev["out"] <= C["merge_gap"] and prev["scene"] == d["scene"]
                and float(prev.get("speed") or 100) == float(d.get("speed") or 100) and bool(prev.get("pinned")) == bool(d.get("pinned"))
                and (not d.get("pinned") or d["in"] >= prev["out"] - 0.05)):
            prev["out"] = max(prev["out"], d["out"])
            prev["exclude"] = prev.get("exclude", []) + d.get("exclude", [])
            prev["script_line"] += "+" + d["script_line"]
            prev["flags"] = list(dict.fromkeys(prev.get("flags", []) + d.get("flags", [])))
            for c_ in d.get("cues", []):     # cue now sits inside the merged piece: shift by where d starts in it
                c_["offset"] = round(float(c_.get("offset") or 0) + (d["in"] - prev["in"]) * 100.0 / float(prev.get("speed") or 100), 2)
                prev.setdefault("cues", []).append(c_)
            return
        P["segments"].append(d)

    def gap(ln, scene_key, kind, secs, why="", suggest=None):
        """Placeholder for footage the source does not have: build puts a text card of `secs` on V1.
        `suggest` = recommended replacement sources (Review buttons swap the card for one of them)."""
        if not C.get("gap_placeholders", True):
            return
        who = f"{ln.speaker}: " if ln.speaker and kind == "thoại" else ""
        txt = f"⚠ THIẾU {kind.upper()}\n{who}{ln.text}" + (f"\n({why})" if why else "")
        piece({"file": "", "gap": True, "gap_kind": kind, "in": 0.0, "out": round(secs, 2), "scene": scene_key, "text": txt,
               "label": f"⚠ THIẾU {kind}: {who}{ln.text}"[:120], "script_line": ln.id, "score": 0, "audio": False,
               "silence": False, "subs": False, "exclude": [], "flags": [f"chừa {secs:.1f}s — cần quay bổ sung / thay hình"],
               "suggest": suggest or []})

    def spoken(ln, scene_key):
        at = chosen.get(ln.id)
        if not at:
            if ln.type != "action":
                P["missing"].append({"line": ln.id, "scene": ln.scene, "text": ln.text})
                from .speech import classify
                lab = classify({"text": ln.text, "start": 0.0, "end": max(1.0, 0.3 * len(ln.syl))}, 0.0)[0]
                if lab == "junk":                     # the script quoted a Whisper hallucination / counting → drop, no card
                    P["report_notes"].append(f"{ln.id}: câu trong kịch bản là thoại rác (Whisper bịa / đếm số) — bỏ, không chừa chỗ")
                    return False
                sug = [dict(a.brief(), audio=True) for a in (cands.get(ln.id) or []) if getattr(a, "coverage", 0) >= 0.35][:3]
                gap(ln, scene_key, "thoại", max(2.0, min(8.0, 0.3 * len(ln.syl) + 0.6)),
                    getattr(ln, "src_note", "") and "kịch bản ghi thiếu nguồn" or "không có trong source", sug)
            return False
        label = (f"{ln.speaker}: " if ln.speaker and ln.type != "action" else "") + ln.text
        if getattr(at, "parts", None) and isinstance(at.parts, list) and at.parts and isinstance(at.parts[0], dict):
            for k, pt in enumerate(at.parts):
                suffix = pt.get("label_suffix", f" [{chr(65 + k)}]")
                seg_lbl = (label + suffix) if k == 0 else f"↳ {suffix.strip()}"
                pt_flags = list(at.flags)
                pt_flags.append(f"stitch đoạn {k + 1}/{len(at.parts)} ({pt['file']} take{pt.get('take', 1)})")
                piece({"file": pt["file"], "in": pt["in"], "out": pt["out"], "scene": scene_key,
                       "label": seg_lbl, "script_line": ln.id, "score": at.score,
                       "flags": pt_flags, "exclude": list(pt.get("exclude", [])),
                       "candidates": [a.brief() for a in cands.get(ln.id, [])[:4]]})
        else:
            piece({"file": at.file, "in": at.t_in, "out": at.t_out, "scene": scene_key, "label": label,
                   "script_line": ln.id, "score": at.score, "flags": list(at.flags), "exclude": list(at.exclude),
                   "candidates": [a.brief() for a in cands.get(ln.id, [])[:4]]})
        # keywords / numbers are searched part by part: a stitched pick's parts may come from different clips, so
        # its token range only means something inside each part's own clip
        kws = list(ln.keywords) + list(keywords)
        for f_, i0, i1, *_ in _spans(at):
            toks = clips.get(f_)
            if not toks or i0 is None or i1 is None:
                continue
            i0, i1 = max(0, i0), min(len(toks) - 1, i1)
            for kw in list(kws):
                loc = find_keyword(toks, i0, i1, kw)
                if loc:
                    kws.remove(kw)                    # one highlight per keyword and line
                    kw_hits.append((f_, loc[0], loc[1], kw.upper() + "!", "pink" if scene_key != "HOOK" else "red"))
            # numbers said in the pick (20km, 1 đến 2 ngày) -> highlight the phrase around them
            dig = [j for j in range(i0, i1 + 1) if re.search(r"\d", toks[j].raw)]
            if dig:
                j0 = max(i0, dig[0] - (1 if len(dig) > 1 else 2))
                j1 = min(i1, dig[-1] + 1)
                while j1 > dig[-1] and toks[j1].syl in ("đến", "nữa", "đây", "nha", "nhé", "rồi", "để"):
                    j1 -= 1
                phrase = _raw_phrase(toks, j0, j1)
                kw_hits.append((f_, j0, j1, phrase.upper(), "red" if scene_key == "HOOK" else "yellow"))
        return True

    # ---- HOOK
    hook = [ln for ln in lines if ln.hook]
    if hook:
        P["scene_names"]["HOOK"] = C["hook_title"]
        for ln in hook:
            spoken(ln, "HOOK")

    scenes = list(dict.fromkeys(ln.scene for ln in lines))
    scene_day = {}
    for sc in scenes:
        ds = [day(chosen[ln.id].file) for ln in lines if ln.scene == sc and ln.id in chosen]
        scene_day[sc] = Counter(ds).most_common(1)[0][0] if ds else ""     # ties → first seen (deterministic)
    # ---- b-roll v2: every visual line gets its own best shot; each clip and each near-duplicate group used once
    from .brollpick import Picker
    ai_clips = _jload(os.path.join(src, "_nexys_work", "source_ai.json"), {}).get("clips", {})
    # local vision description (nxcore.describe, runs after the scan) fills clips the cloud AI pass never saw
    from .describe import load as _auto_desc, load_segs as _auto_seg_desc
    from .segments import load as _load_segs, seg_key
    seg_desc = _auto_seg_desc(src)
    segs = {}
    if C.get("broll_segments", True):
        for f, sl in _load_segs(src).items():
            if len(sl) >= 2 and f in media:
                d_ = seg_desc.get(f, {})
                segs[f] = [dict(s, desc=d_.get(seg_key(s), "")) for s in sl]
    for f, d in _auto_desc(src).items():
        # split clips carry their own per-segment words; a clip-level line would make every segment look alike
        if f not in segs and not (ai_clips.get(f) or {}).get("summary"):
            ai_clips[f] = dict(ai_clips.get(f) or {}, summary=d)
    busy = {}
    for a_ in live.values():
        for f_, _, _, t0_, t1_ in _spans(a_):
            busy.setdefault(f_, []).append([t0_, t1_])
    for f_, a_, b_, _ in strict.values():          # script-pinned ranges are on the timeline too: never B-roll again
        busy.setdefault(f_, []).append([a_, b_])
    tags = {f: t for f, t in tags.items() if isinstance(t, dict) and not f.startswith("_")}
    visual = {sc: [ln for ln in lines if ln.scene == sc and not ln.hook and ((ln.type == "action" and ln.id not in chosen) or ln.type == "broll")]
              for sc in scenes}
    scene_text = {sc: [s for ln in visual[sc] for s in syllables(ln.text)] for sc in scenes}
    masters = {f for f, t in tags.items() if t.get("role") == "master" and f in media and f not in used_files}
    master_for = {sc: [] for sc in scenes}
    angle_for = {sc: [] for sc in scenes}
    if masters:   # live-screen/multicam material keeps the v1 scene logic (base + angle cutaways)
        for f, t in tags.items():
            if f in media and f not in used_files and t.get("role") in ("master", "angle"):
                best = max(scenes, key=lambda sc: _tag_score(t.get("tags", []), scene_text[sc]))
                if _tag_score(t.get("tags", []), scene_text[best]) > 0:
                    (master_for if t["role"] == "master" else angle_for)[best].append(f)
    multicam_files = {f for v in list(master_for.values()) + list(angle_for.values()) for f in v}
    intro = [f for f, t in sorted(tags.items(), key=lambda kv: media.get(kv[0], {}).get("created") or "")
             if t.get("place") == "intro" and f in media and f not in used_files]
    stab = _jload(os.path.join(src, "_nexys_work", "shake.json"), {})
    pins, pin_bad = {}, []
    for ln in lines:
        for p_ in (getattr(ln, "src", None) or []):
            f = resolve_loose(p_.get("file"), media)
            if not f:
                pin_bad.append(f"{ln.id} nguồn '{p_.get('file')}' không có trong source (tên sai hoặc đã loại khi triage)")
            elif ln.type in ("broll", "action") and ln.id not in strict:   # strict lines never go through the Picker
                pins.setdefault(ln.id, []).append((f, p_.get("t")))
    P["report_notes"].extend(pin_bad + pin_range_bad)
    strict_done = set()
    if strict:
        P["report_notes"].append(f"Dựng đúng kịch bản: {len(strict)} dòng có Nguồn @ in–out → giữ nguyên file, đoạn cắt, tốc độ và thứ tự")

    def strict_piece(ln, scene_key):
        if ln.id not in strict:
            return False
        f_, a_, b_, gid = strict[ln.id]
        if gid in strict_done:            # one quote split into sentences: its first line already carries the whole range
            return True
        strict_done.add(gid)
        grp = [l2 for l2 in lines if l2.id in strict and strict[l2.id][3] == gid]
        text_ = " ".join(l2.text for l2 in grp)
        talk = ln.type in ("dialogue", "question") or (ln.type == "action" and bool(ln.speaker))   # spoken action keeps its sound
        d = {"file": f_, "in": a_, "out": b_, "scene": scene_key, "pinned": True, "script_line": "+".join(l2.id for l2 in grp),
             "label": ((f"{ln.speaker}: " if ln.speaker else "") + text_) if talk else "B-roll: " + text_,
             "score": 1.0, "flags": ["đúng nguồn kịch bản (strict)"], "exclude": [], "silence": False}
        if talk:
            d["audio"] = True
        else:
            d.update(audio=False, subs=False)
            sp_ = _pin_speed(ln.text)
            if abs(sp_ - 100.0) > 0.01:
                d["speed"] = sp_
                d["flags"].append(f"tốc độ {sp_:g}% theo kịch bản")
        piece(d)
        return True
    pinned_files = {f for v in pins.values() for f, _ in v}
    # strict files count as used (busy = their pinned range): a short clip is not offered again, a long split take
    # only lends segments away from the pinned stretch
    pk = Picker(tags, ai_clips, media, shake, (used_files - pinned_files) | multicam_files | set(intro) | strict_files, C, stab=stab,
                force=pinned_files - strict_files,
                segs={f: s for f, s in segs.items() if f not in multicam_files and f not in intro}, busy=busy)
    pk_ref[0] = pk
    # script-named hook clips ("→ Đưa đoạn DSC_8955.MOV lên đầu video")
    # (when the note belongs to a dialogue block, that dialogue already IS the hook → don't add the clip again)
    hook_files = [] if any(ln.hook for ln in lines) else [f for f in getattr(meta, "hook_files", []) if f in media]
    hook_files = [f for f in hook_files if f not in strict_files]   # the script already places that shot itself
    if hook_files:
        P["scene_names"]["HOOK"] = C["hook_title"]
        for f in hook_files:
            v = pk.items.get(f) or {"file": f, "desc": "", "use": tags.get(f, {}).get("use"), "dur": media[f]["dur"]}
            a, b = pk.window(v) if f in pk.items else stable_window(media[f]["dur"], C["broll_dur_max"], shake.get(f, {}).get("ranges", []), v["use"])
            P["segments"].insert(0 if not P["segments"] else len([x for x in P["segments"] if x["scene"] == "HOOK"]),
                                 {"file": f, "in": a, "out": b, "scene": "HOOK", "label": "Hook (theo kịch bản): " + (ai_clips.get(f, {}).get("summary") or f),
                                  "script_line": "hook", "score": 1, "flags": ["hook theo ghi chú kịch bản"], "exclude": [], "audio": False, "silence": False, "subs": False})
    covered_by_hook = set()
    for f in hook_files:           # a script line describing the very shot used as hook is already on screen
        if f in pk.items:
            for sc in scenes:
                for ln in visual[sc]:
                    if pk.score(ln.text, pk.items[f]) >= 0.4:
                        covered_by_hook.add(ln.id)
                        P["report_notes"].append(f"{ln.id} '{ln.text[:50]}' trùng nội dung hook {f} → không lặp lại")
    want = [(ln.id, ln.text) for sc in scenes for ln in visual[sc] if ln.id not in covered_by_hook and ln.id not in strict]
    per_line = max(1, int(C["broll_per_line"]))
    if C.get("target_dur") and want:           # brief duration → how many shots each visual line may get
        spoken_s = sum(max(0.0, t1_ - t0_) for a in live.values() for *_, t0_, t1_ in _spans(a))
        need = max(0.0, float(C["target_dur"]) - spoken_s)
        per_line = int(min(3, max(per_line, round(need / (len(want) * C["broll_dur"]) + 0.2))))
    # ---- shooting-time order (vlog / event recap): estimate WHEN in the shoot each visual line belongs
    from . import chrono as _chrono
    chrono_exp = {}
    P["chrono"] = _chrono.enabled(C, src)          # Review flags back-jumps only when the story follows the shoot
    if P["chrono"] and want:
        anchors = {}
        for ln in lines:
            a_ = chosen.get(ln.id)
            t0 = _chrono.capture_ts(a_.file, media.get(a_.file)) if a_ else None
            if t0:
                anchors[ln.id] = t0 + a_.t_in
        want_ids = {lid for lid, _ in want}
        order_ = [ln.id for ln in lines if not ln.hook and (ln.id in anchors or ln.id in want_ids)]
        cand_ids = [i for i, v in pk.items.items() if v.get("t_abs") and v["file"] not in set(hook_files)]
        bs = pk.batch_scores(want, cand_ids) if cand_ids else {}
        opts = {}
        for lid, _ in want:
            row = sorted(((bs.get((lid, i), 0.0), pk.items[i]["t_abs"]) for i in cand_ids), reverse=True)
            opts[lid] = [x for x in row[:int(C.get("chrono_topk", 12))] if x[0] >= C["broll_min_score"]]
        chrono_exp = _chrono.expected_times(order_, anchors, opts, lam=float(C.get("chrono_lambda", 0.5)))
    picks = pk.assign(want, per_line=per_line, exclude=set(hook_files), pins=pins, chrono=chrono_exp or None)
    if chrono_exp:
        seq_t = [v.get("t_abs") for lid, _ in want for v in picks.get(lid, [])[:1]]
        back = _chrono.backward_jumps(seq_t)
        known = [t for t in seq_t if t]
        P["report_notes"].append(
            f"Mạch thời gian quay (VLOG): B-roll đi theo trình tự quay {_chrono.fmt(min(known)) if known else '?'} → "
            f"{_chrono.fmt(max(known)) if known else '?'}" + (f" · {back} chỗ lùi thời gian (do nội dung khớp hơn hẳn)" if back else " · không lùi"))
    for lid, pl in pins.items():
        got = {v["file"] for v in picks.get(lid, [])}
        for f, _ in pl:
            if f not in got:
                why = "đã dùng ở dòng khác" if f in pk.used_files else "không đạt lọc (quá ngắn/lỗi)"
                P["report_notes"].append(f"{lid}: nguồn kịch bản {f} không dùng được — {why}")
    if per_line > 1:
        P["report_notes"].append(f"Mỗi dòng hình tối đa {per_line} shot (khác nhóm take) để gần độ dài brief {C.get('target_dur')}s")

    for si, sc in enumerate(scenes):
        P["scene_names"][sc] = meta.scene_titles.get(sc, sc)
        if si == 0:
            for f in intro:
                if C["intro_same_day"] and scene_day.get(sc) and day(f) != scene_day[sc]:
                    P["report_notes"].append(f"Intro {f} bỏ qua: khác ngày quay với phần còn lại của {sc} (khác ứng viên)")
                    continue
                a, b = tags[f].get("use", [0.3, min(5.0, media[f]["dur"])])
                piece({"file": f, "in": a, "out": b, "scene": sc, "label": "Intro: " + ", ".join(tags[f].get("tags", [])),
                       "script_line": "intro", "score": 0, "flags": ["intro (ngoài kịch bản)"], "exclude": []})
        vis_ids = {ln.id for ln in visual[sc]}
        has_speech = False
        for ln in lines:
            if ln.scene != sc:
                continue
            if ln.hook and not C.get("keep_hook_in_scene", True):
                continue
            if strict_piece(ln, sc):
                has_speech = has_speech or ln.type in ("dialogue", "question")
                continue
            if ln.type in ("dialogue", "question") or (ln.type == "action" and ln.id not in vis_ids):
                has_speech = spoken(ln, sc) or has_speech
            elif ln.type in ("vo", "text"):
                cue = {"kind": ln.type, "line": ln.id, "scene": sc, "text": ln.text, "offset": float(C.get("vo_lead", 0.3)) if ln.type == "vo" else 0.0,
                       "style": "title" if ln.speaker == "TITLE" else "text",
                       "dur": round(len(ln.syl) / float(C.get("vo_syl_per_s", 4.0)) + 0.4, 2) if ln.type == "vo" else float(C.get("text_dur", 2.5))}
                pending_cues.append(cue)
                (P["vo_lines"] if ln.type == "vo" else P.setdefault("text_lines", [])).append(cue)
            if ln.id in vis_ids:
                got = picks.get(ln.id) or []
                if getattr(ln, "src_note", "") and got and not any(v.get("_pinned") for v in got):
                    # the script (AI saw every clip) says no clip is good enough → keep the time as a card,
                    # name the closest shot so the editor can drop it in if acceptable
                    alt = ", ".join(f"{v['file']} ({v['score']:.2f})" for v in got[:2])
                    P["report_notes"].append(f"{ln.id}: kịch bản ghi THIẾU → chừa thẻ chữ; gợi ý thay tạm: {alt}")
                    P["broll_requests"].append({"line": ln.id, "scene": sc, "desc": ln.text})
                    note = ln.src_note.replace("Nguồn:", "").replace("⚠", "").replace("THIẾU", "").strip(" —-")
                    sug = [{"file": v["file"], "in": w[0], "out": w[1], "score": v["score"], "q": v.get("q")}
                           for v in got for w in [pk.window(v, ln.text)]]
                    sug += [x for x in pk.suggest(ln.text, 3, exclude=used_files, when=chrono_exp.get(ln.id))
                            if x["file"] not in {y["file"] for y in sug}]
                    gap(ln, sc, "hình", float(C.get("broll_dur", 2.5)) + 0.5, (note[:60] + " · " if note else "") + "gợi ý tạm: " + got[0]["file"], sug[:3])
                    got = []
                    continue
                for v in got:
                    a, b = pk.window(v, ln.text)
                    if v.get("_slowmo") and C.get("auto_slowmo", True):
                        b = round(a + (b - a) * 0.6, 2)          # shorter source window → ~1.2× on the timeline at 50 %
                    piece({"file": v["file"], "in": a, "out": b, "scene": sc, "label": "B-roll: " + ln.text,
                           "script_line": ln.id, "score": v["score"], "audio": False, "silence": False, "subs": False, "exclude": [],
                           "flags": [f"khớp {v['score']:.2f}"] + (["đúng nguồn kịch bản"] if v.get("_pinned") else [])
                                    + ([f"⚠ kịch bản ghi thiếu source đạt chuẩn — dùng tạm"] if ln.src_note else [])
                                    + (["take dự phòng BTS"] if v.get("use_as") == "dialogue" else [])
                                    + ([f"đoạn {v['seg'][0]:.0f}–{v['seg'][1]:.0f}s / clip {float(v['dur']) / 60:.1f} phút"] if v.get("seg") else [])
                                    + ([f"slow-mo {C['slowmo_speed']}% (rung nhẹ khi chạy thường)"] if v.get("_slowmo") and C.get("auto_slowmo", True) else []),
                           **({"speed": C["slowmo_speed"]} if v.get("_slowmo") and C.get("auto_slowmo", True) else {}),
                           **({"seg": v["seg"]} if v.get("seg") else {}),
                           "candidates": [dict(x, audio=False) for x in pk.suggest(ln.text, 4, exclude=used_files | {v["file"]},
                                                                               when=chrono_exp.get(ln.id))]})
                if not got and ln.id in covered_by_hook:
                    pass
                elif not got:
                    if ln.type == "action" and has_speech:
                        P["report_notes"].append(f"{ln.id} ({sc}) '{ln.text}' — không khớp riêng, có thể đã nằm trong đoạn thoại liền của cảnh")
                    else:
                        P["broll_requests"].append({"line": ln.id, "scene": sc, "desc": ln.text})
                        note = (getattr(ln, "src_note", "") or "").replace("Nguồn:", "").replace("⚠", "").replace("THIẾU", "").strip(" —-")
                        gap(ln, sc, "hình", float(C.get("broll_dur", 2.5)) + 0.5, note[:80] or "không có clip đạt chuẩn",
                            pk.suggest(ln.text, 3, exclude=used_files, min_ratio=0.6, when=chrono_exp.get(ln.id)))
        # VO written after the scene's last shot (or still talking at its end) gets its own picture inside the scene:
        # "after" the last shot would run over whatever comes next — often the next scene's dialogue
        if vo_state["debt"] > 0.05 or any(c["kind"] == "vo" for c in pending_cues):
            vo_cover(sc)
        if pending_cues:      # TEXT written after the scene's last shot → starts where that shot ends
            last_ = next((s_ for s_ in reversed(P["segments"]) if s_.get("scene") == sc), None)
            if last_ is not None:
                for c_ in pending_cues:
                    c_["after"] = True
                last_.setdefault("cues", []).extend(pending_cues)
                pending_cues.clear()
        # multicam base + angle cutaways (live screen material)
        if master_for[sc]:
            f = master_for[sc][0]
            use = tags[f].get("use", [0, media[f]["dur"]])
            a = use[0]
            b = min(use[1], a + C["multicam_base"])
            P["segments"].append({"file": f, "in": a, "out": b, "scene": sc, "label": "Source 1: " + ", ".join(tags[f]["tags"][:2]),
                                  "script_line": "multicam", "score": 0, "flags": [], "exclude": [], "silence": False, "subs": False})
            anchor = len(P["segments"]) - 1
            P["segments"][anchor]["_anchor"] = anchor
            n = len(angle_for[sc])
            for k, g in enumerate(angle_for[sc]):
                off = round((b - a) * (k + 1) / (n + 1) - C["angle_dur"] / 2, 2)
                ia, ib = stable_window(media[g]["dur"], C["angle_dur"], shake.get(g, {}).get("ranges", []), tags[g].get("use"))
                P["overlays"].append({"file": g, "anchor": anchor, "offset": off, "dur": round(ib - ia, 2), "in": ia, "muted": True})
    for f2, f1, a2, a1 in pk.swaps:
        P["report_notes"].append(f"Chọn take {f2} thay {f1} (cùng nhóm): ổn định {a2.get('stability')} vs {a1.get('stability')}, "
                                 f"bố cục {a2.get('comp')} vs {a1.get('comp')}")
    for s_ in P["segments"]:
        rf = (stab.get(s_["file"], {}).get("composition") or {}).get("rotate_fix")
        if rf:
            s_["rotate_fix"] = rf
            s_["flags"] = list(s_.get("flags", [])) + [f"nghiêng — tự xoay {rf:+.1f}° khi build"]
    left = pk.leftovers()
    if left:
        P["report_notes"].append("B-roll tốt chưa dùng (1 take/nhóm): " + "; ".join(
            f"{v['file']} ({', '.join(v['tags'][:2]) or v['desc'][:40]})" for v in left))
    dup = {}
    for v in pk.items.values():
        g_ = dup.setdefault(v["group"], [])
        if v["file"] not in g_:                       # segments of one take = one entry
            g_.append(v["file"])
    groups = [g for g in dup.values() if len(g) > 1]
    if groups:
        P["report_notes"].append(f"Gộp {sum(len(g) for g in groups)} clip thành {len(groups)} nhóm take trùng — mỗi nhóm chỉ dùng 1: " +
                                 "; ".join("/".join(g[:4]) for g in groups[:8]))

    for s_ in P["segments"]:                         # shooting time of every shot (Review shows it; vlog order checks)
        if not s_.get("gap") and s_.get("file") in media:
            t0 = _chrono.capture_ts(s_["file"], media[s_["file"]])
            if t0:
                s_["shot_at"] = round(t0 + float(s_.get("in") or 0), 1)

    # unused speech clips (off-script material the editor may want)
    for f, toks in clips.items():
        if f not in used_files and f not in tags and toks and len(toks) > 3:
            P["unused_speech"].append({"file": f, "text": " ".join(t.syl for t in toks)[:120]})

    # transitions (built by nxcut build): learned dissolve length / share of montage cuts, else house defaults
    try:
        from . import prefs as _pf
        _r = _pf.rules()
    except Exception:
        _r = {}
    P["transitions"] = dict({"on": True, "dur": round(min(1.2, max(0.4, float(_r.get("dissolve_s", 0.8)))), 2), "scene": True,
                             "broll_rate": round(float(_r.get("dissolve_rate", 0.3)), 2), "fade_in": 0.8, "fade_out": 1.0, "audio": True},
                            **(base.get("transitions") or {}))
    if "dissolve_s" in style_tr:                        # an approved rule beats the learned length and the base plan
        P["transitions"]["dur"] = float(style_tr["dissolve_s"])
    if learned:
        P["report_notes"].append("Áp thói quen đã học: " + ", ".join(f"{k}={v}" for k, v in learned.items()))
    if style or style_tr:
        from . import stylebook
        P["report_notes"].append("Áp Sổ luật phong cách: " + stylebook.describe({**style, **style_tr}))
    try:
        from . import prefs
        ar = prefs.rules().get("auto_rotate")
        if ar is not None:
            P["autofix"] = {**P.get("autofix", {"slowmo": True, "rotate": True}), "rotate": bool(ar)}
    except Exception:
        pass
    _dedupe_overlaps(P)
    _fill_to_target(P, C, stab, media, shake)
    _vo_guard(P)
    _split_excludes(P)
    _place_highlights(P, kw_hits, clips)
    if C.get("jump_cover", True):
        words = {sc: " ".join([meta.scene_titles.get(sc, "")] + [ln.text for ln in lines if ln.scene == sc and ln.type in ("broll", "action")])
                 for sc in scenes}
        _cover_jump_cuts(P, C, pk, words, {ln.id: ln.text for ln in lines})
    _apply_jump_cut_punch_in(P, C)
    if C.get("j_l_cut", True):
        _apply_jl_cuts(P, C, media, clips)
    return P


def _seg_len(s):
    return (s["out"] - s["in"]) * 100.0 / float(s.get("speed") or 100)


def _fill_to_target(P, C, stab, media, bad):
    """Brief says N seconds but the cut is much shorter → lengthen b-roll shots inside their usable range (no overlaps,
    no shake), longest-shortfall first, up to fill_max per shot. Always reports cut length vs brief."""
    tgt = float(C.get("target_dur") or 0)
    total = sum(_seg_len(s) for s in P["segments"])
    if not tgt:
        return
    if total < C["fill_to_target"] * tgt:
        need = tgt - total
        used = {}
        for s in P["segments"]:
            used.setdefault(s["file"], []).append([s["in"], s["out"]])
        cand = [s for s in P["segments"] if not s.get("audio", True) and s.get("scene") != "HOOK" and not s.get("gap") and not s.get("pinned")]
        for rnd in range(3):                                  # spread growth: +≤2 s per shot per round
            for s in sorted(cand, key=lambda x: _seg_len(x)):
                if need <= 0.05:
                    break
                f = s["file"]; dur = float((media.get(f) or {}).get("dur") or 0)
                us = (stab.get(f) or {}).get("usable") or [[0.3, max(0.3, dur - 0.3)]]
                box = next((r for r in us if r[0] - 0.05 <= s["in"] and s["out"] <= r[1] + 0.05), None)
                if not box:
                    continue
                room_max = C["fill_max"] - (s["out"] - s["in"])
                if room_max <= 0.1:
                    continue
                others = [r for r in used[f] if not (abs(r[0] - s["in"]) < 1e-6 and abs(r[1] - s["out"]) < 1e-6)]
                hi = min([box[1]] + [r[0] for r in others if r[0] >= s["out"] - 1e-6])
                lo = max([box[0]] + [r[1] for r in others if r[1] <= s["in"] + 1e-6])
                shakes = [r for r in (bad.get(f) or {}).get("ranges", []) if r[1] > lo and r[0] < hi]
                grow = min(2.0, room_max, need * float(s.get("speed") or 100) / 100.0)
                a, b = s["in"], s["out"]
                add_r = min(grow, max(0.0, hi - b)); add_l = min(grow - add_r, max(0.0, a - lo))
                na, nb = round(a - add_l, 2), round(b + add_r, 2)
                if any(min(nb, r[1]) - max(na, r[0]) > 0.2 for r in shakes):
                    continue
                if nb - na > b - a + 0.05:
                    for r in used[f]:
                        if abs(r[0] - a) < 1e-6 and abs(r[1] - b) < 1e-6:
                            r[0], r[1] = na, nb
                    s["in"], s["out"] = na, nb
                    need -= (nb - na - (b - a)) * 100.0 / float(s.get("speed") or 100)
                    s["flags"] = list(dict.fromkeys(list(s.get("flags", [])) + ["kéo dài cho đủ thời lượng brief"]))
        total = sum(_seg_len(s) for s in P["segments"])
    P["duration"] = {"cut_s": round(total, 1), "target_s": round(tgt, 1)}
    msg = f"Thời lượng bản dựng ≈ {total:.0f}s / brief {tgt:.0f}s"
    if total < 0.8 * tgt:
        msg += " — còn thiếu hình: kịch bản cần thêm shot/cảnh (hoặc giảm thời lượng brief)"
    P["report_notes"].insert(0, msg)


def _dedupe_overlaps(P, tol=0.3, min_piece=0.4):
    """No source moment may play twice: a later segment that re-uses audio/picture already on the timeline
    (typical after consecutive picks were merged into one long piece) is trimmed, split around the used part, or dropped."""
    kept, taken, carry = [], {}, []     # carry: VO/TEXT cues of a dropped segment move to the next kept one
    for s in P["segments"]:             # script-pinned ranges are fixed: registered first, nothing else may reuse them
        if s.get("pinned") and not s.get("gap") and s.get("file"):
            taken.setdefault(s["file"], []).append((s["in"], s["out"], s["script_line"]))
    for s in P["segments"]:
        if s.get("script_line") == "multicam" or s.get("gap") or s.get("scene") == "HOOK" or s.get("pinned"):
            if carry:
                s["cues"] = list(carry) + s.get("cues", []); carry.clear()
            kept.append(s); continue
        rs = taken.setdefault(s["file"], [])
        parts = [(s["in"], s["out"])]
        hit_by = []
        for (x, y, who) in sorted(rs):
            nxt = []
            for a, b in parts:
                if min(b, y) - max(a, x) <= tol:
                    nxt.append((a, b)); continue
                hit_by.append(who)
                if a < x:
                    nxt.append((a, x))
                if y < b:
                    nxt.append((y, b))
            parts = nxt
        parts = [(a, b) for a, b in parts if b - a >= min_piece]
        if not parts:
            P["report_notes"].append(f"Bỏ đoạn trùng: {s['file']} {s['in']:.2f}–{s['out']:.2f} [{s['script_line']}] đã có trong [{', '.join(dict.fromkeys(hit_by))}]")
            carry.extend(s.get("cues", []))
            continue
        base_flags, base_excl = list(s.get("flags", [])), list(s.get("exclude", []))
        for k, (a, b) in enumerate(parts):
            seg = s if k == 0 else dict(s)
            if k:
                seg.pop("cues", None)
            elif carry:
                seg["cues"] = list(carry) + seg.get("cues", []); carry.clear()
            seg["flags"] = list(base_flags)
            if (a, b) != (s["in"], s["out"]) or len(parts) > 1:
                seg["flags"] = list(seg.get("flags", [])) + [f"bỏ phần trùng với [{', '.join(dict.fromkeys(hit_by))}] → {a:.2f}–{b:.2f}"]
            seg["exclude"] = [e for e in base_excl if a <= e[0] < b]
            seg["in"], seg["out"] = round(a, 3), round(b, 3)
            rs.append((seg["in"], seg["out"], seg["script_line"]))
            kept.append(seg)
    # overlays anchor by index → remap after drops
    if [id(s) for s in kept] != [id(s) for s in P["segments"]]:
        idx = {}
        for i, s in enumerate(kept):
            idx.setdefault(id(s), i)
        old = P["segments"]
        remap = {i: idx.get(id(s)) for i, s in enumerate(old)}
        P["overlays"] = [dict(o, anchor=remap[o["anchor"]]) for o in P.get("overlays", []) if remap.get(o["anchor"]) is not None]
        P["highlights"] = [dict(h, seg=remap[h["seg"]]) for h in P.get("highlights", []) if remap.get(h.get("seg")) is not None]
        for i, s in enumerate(kept):     # _split_excludes maps overlay anchors through _anchor: keep it = new index
            if "_anchor" in s and idx.get(id(s)) == i:
                s["_anchor"] = i
        P["segments"] = kept


def _split_excludes(P):
    out = []
    for d in P["segments"]:
        ex = sorted(e for e in d.pop("exclude", []) if d["in"] < e[1] and e[0] < d["out"])
        cur, first = d["in"], True
        for a, b in ex + [[d["out"], d["out"]]]:
            if a - cur >= 0.15:
                seg = dict(d, **{"in": round(cur, 3), "out": round(a, 3)})
                if not first:
                    seg["label"] = "↳ cut vấp/filler"
                    seg.pop("cues", None)
                out.append(seg)
                first = False
            cur = max(cur, b)
    # overlay anchors must follow the split
    remap = {}
    for i, d in enumerate(out):
        if "_anchor" in d:
            remap.setdefault(d["_anchor"], i)
    for d in out:
        d.pop("_anchor", None)
    for ov in P["overlays"]:
        ov["anchor"] = remap.get(ov["anchor"], ov["anchor"])
    P["segments"] = out


def _place_highlights(P, kw_hits, clips):
    seen = set()
    for f, j0, j1, text, style in kw_hits:
        toks = clips[f]
        t = toks[j0].t0
        for si, s in enumerate(P["segments"]):
            if s["file"] == f and s["in"] - 0.05 <= t <= s["out"] and s.get("audio", True):
                key = (si, text)
                if key in seen:
                    break
                seen.add(key)
                P["highlights"].append({"seg": si, "match": _raw_phrase(toks, j0, j1), "text": text[:40], "style": style, "dur": 1.6})
                break


def _apply_jump_cut_punch_in(P, C):
    """Detect consecutive cuts from the same camera/file without intervening visual change (jump cut).
    Automatically alternate between Base (100%) and Punch-in (112% scale) to create an artificial 2-camera look
    (Premiere Add Edit / Razor + Scale technique)."""
    if not C.get("punch_in", True):
        return
    scale_punch = float(C.get("punch_in_scale", 112.0))
    punched = False
    for k in range(len(P["segments"])):
        cur = P["segments"][k]
        if cur.get("gap") or not cur.get("audio", True) or cur.get("pinned"):   # strict pins play exactly as written
            punched = False
            continue
        if k > 0:
            prev = P["segments"][k - 1]
            if (prev.get("audio", True) and not prev.get("gap") and not prev.get("pinned")
                    and prev.get("file") == cur.get("file") and prev.get("scene") == cur.get("scene")):
                if not punched:
                    cur["scale"] = scale_punch
                    cur["flags"] = list(dict.fromkeys(list(cur.get("flags", [])) + [f"punch-in {scale_punch:.0f}% (chống jump cut)"]))
                    punched = True
                else:
                    punched = False
            else:
                punched = False


def _timeline(P):
    """Timeline start of every segment + the intervals where VO talks (same rules as the build: offset from the
    segment start, 'after' = from its end, a VO never overlaps the previous one — it is pushed 0.2 s after it)."""
    starts, vo, t, vo_end = [], [], 0.0, None
    for s in P["segments"]:
        starts.append(t)
        L = _seg_len(s)
        for c in s.get("cues", []):
            if c.get("kind") == "vo":
                st = t + (L if c.get("after") else float(c.get("offset") or 0))
                if vo_end is not None:
                    st = max(st, vo_end + 0.2)
                vo_end = st + float(c.get("dur") or 2.5)
                vo.append((st, vo_end))
        t += L
    return starts, vo


def _vo_guard(P):
    """Last check after dedupe / fill: no VO may play over a piece with live dialogue. VO handed to such a piece
    (its B-roll was dropped as a duplicate) moves onto a card in front of it; VO still talking when such a piece
    starts gets a card for the rest of its time. Cards keep the edit honest — Review swaps in a shot."""
    out, t, vo_end, last = [], 0.0, None, {"text": "", "line": "vo"}

    def add(s):
        nonlocal t, vo_end
        L = _seg_len(s)
        for c in s.get("cues", []):
            if c.get("kind") == "vo":
                st = t + (L if c.get("after") else float(c.get("offset") or 0))
                if vo_end is not None:
                    st = max(st, vo_end + 0.2)
                vo_end = st + float(c.get("dur") or 2.5)
                last.update(text=c.get("text", ""), line=c.get("line", "vo"))
        out.append(s)
        t += L

    def card(s, secs, cues):
        return {"file": "", "gap": True, "gap_kind": "vo", "in": 0.0, "out": round(secs, 2), "scene": s.get("scene"),
                "text": "VO — cần B-roll\n" + last["text"], "label": ("VO chưa có hình: " + last["text"])[:120],
                "script_line": last["line"], "score": 0, "audio": False, "silence": False, "subs": False, "exclude": [],
                "flags": [f"chừa {secs:.1f}s hình cho VO (không để VO đè thoại)"], "cues": cues}

    for s in P["segments"]:
        if _talk(s):
            own = [c for c in s.get("cues", []) if c.get("kind") == "vo"]
            if own:
                s["cues"] = [c for c in s["cues"] if c.get("kind") != "vo"]
                for c in own:
                    c.pop("after", None)
                last.update(text=" ".join(c.get("text", "") for c in own), line=own[0].get("line", "vo"))
                add(card(s, max(float(c.get("offset") or 0) + float(c.get("dur") or 2.5) for c in own), own))
            if vo_end is not None and vo_end > t + 0.05:
                add(card(s, vo_end - t, []))
        add(s)
    P["segments"] = out


def _cover_jump_cuts(P, C, pk, scene_words, line_text):
    """Stitched takes / filler cuts leave a jump cut inside one shot. When the scene still has relevant unused
    B-roll, a short muted cutaway (jump_cover_min…max s, V2 overlay — the multicam mechanism) starts exactly on the
    cut and hides it. Never over a pinned (strict) piece, a card, or another overlay."""
    if pk is None:
        return
    segs = P["segments"]
    starts, _ = _timeline(P)
    lo_d, hi_d = float(C.get("jump_cover_min", 1.5)), float(C.get("jump_cover_max", 2.5))
    occupied = [(starts[o["anchor"]] + float(o.get("offset") or 0), starts[o["anchor"]] + float(o.get("offset") or 0) + float(o["dur"]))
                for o in P.get("overlays", []) if o.get("anchor") is not None and o["anchor"] < len(segs)]
    cuts = []
    for k in range(1, len(segs)):
        cur, prev = segs[k], segs[k - 1]
        if not str(cur.get("label", "")).startswith("↳") or not _talk(cur) or not _talk(prev) or cur.get("gap") or prev.get("gap"):
            continue
        if cur.get("pinned") or prev.get("pinned"):
            continue
        stitch = any("cần B-roll che jump cut" in f for f in cur.get("flags", []))
        cuts.append((0 if stitch else 1, k))
    for _, k in sorted(cuts):
        T = starts[k]
        if any(a < T + hi_d and T < b for a, b in occupied):
            continue
        # room: the cutaway may run over the following pieces only while they are plain dialogue
        room, j = 0.0, k
        while j < len(segs) and room < hi_d and _talk(segs[j]) and not segs[j].get("gap") and not segs[j].get("pinned"):
            room += _seg_len(segs[j])
            j += 1
        if room < lo_d:
            continue
        cur = segs[k]
        sl = str(cur.get("script_line", "")).split("+")[0]
        text = (scene_words.get(cur.get("scene"), "") + " " + line_text.get(sl, "")).strip()
        got = pk.cover(text, min(hi_d, room), min_len=lo_d, slowmo_ok=False) if text else None
        if not got:
            continue
        v, a, b, sc = got
        dur = round(min(b - a, hi_d, room), 2)
        P["overlays"].append({"file": v["file"], "anchor": k, "offset": 0.0, "dur": dur, "in": a, "muted": True,
                              "kind": "jumpcut", "score": sc})
        occupied.append((T, T + dur))
        cur["flags"] = list(dict.fromkeys(list(cur.get("flags", [])) + [f"B-roll che jump cut: {v['file']} {dur:.1f}s"]))


def _apply_jl_cuts(P, C, media, clips=None):
    """Implement Rolling Edit (N) J-Cut and L-Cut split edits:
    - L-cut: Dialogue audio extends under incoming B-roll by l_cut_s (0.35s)
    - J-cut: Spoken audio starts early by j_cut_s (0.30s) while previous B-roll is still on screen.
    The extension is clamped to the silence around the piece in its source (next / previous spoken token — a word or
    the director's 'cắt' must not leak in), to half the B-roll's timeline length (speed-aware), and to the time before
    / after a VO on that B-roll. Strict (pinned) pieces are never extended."""
    import bisect
    segs = P["segments"]
    l_s = float(C.get("l_cut_s", 0.35))
    j_s = float(C.get("j_cut_s", 0.30))
    starts, vo = _timeline(P)
    tk = {}

    def tokens(f):
        if f not in tk:
            ts = (clips or {}).get(f) or []
            tk[f] = ([t.t0 for t in ts], sorted(t.t1 for t in ts))
        return tk[f]

    def room_after(f, out):
        t0s, _ = tokens(f)
        i = bisect.bisect_left(t0s, out - 0.02)
        return (t0s[i] - out - 0.05) if i < len(t0s) else 99.0

    def room_before(f, tin):
        _, t1s = tokens(f)
        i = bisect.bisect_right(t1s, tin + 0.02)
        return (tin - t1s[i - 1] - 0.05) if i > 0 else tin

    for k in range(len(segs)):
        cur = segs[k]
        if not cur.get("audio", True) or cur.get("gap") or cur.get("pinned"):
            continue
        T0, T1 = starts[k], starts[k] + _seg_len(cur)
        # L-cut: dialogue -> B-roll
        if k + 1 < len(segs):
            nxt = segs[k + 1]
            if not nxt.get("audio", True) and not nxt.get("gap") and not nxt.get("pinned"):
                f = cur.get("file")
                dur = float((media.get(f) or {}).get("dur") or 0)
                actual_l = min(l_s, _seg_len(nxt) * 0.5, room_after(f, cur["out"]))
                for a, b in vo:                   # VO starting on that B-roll keeps a clean entry
                    if b > T1 and a < T1 + actual_l:
                        actual_l = min(actual_l, a - T1 - 0.05)
                if dur - cur["out"] >= actual_l and actual_l >= 0.15:
                    cur["l_cut"] = round(actual_l, 2)
                    cur["flags"] = list(dict.fromkeys(list(cur.get("flags", [])) + [f"L-cut thoại lồng dưới B-roll +{actual_l:.2f}s"]))
        # J-cut: B-roll -> dialogue
        if k > 0:
            prev = segs[k - 1]
            if not prev.get("audio", True) and not prev.get("gap") and not prev.get("pinned"):
                actual_j = min(j_s, _seg_len(prev) * 0.5, room_before(cur.get("file"), cur["in"]))
                for a, b in vo:
                    if a < T0 and b > T0 - actual_j:
                        actual_j = min(actual_j, T0 - b - 0.05)
                if cur["in"] >= actual_j and actual_j >= 0.15:
                    cur["j_cut"] = round(actual_j, 2)
                    cur["flags"] = list(dict.fromkeys(list(cur.get("flags", [])) + [f"J-cut thoại đi trước hình -{actual_j:.2f}s"]))


def write_report(P, lines, cands, path):
    with open(path, "w", encoding="utf-8") as fh:
        spoken = [l for l in lines if l.type in ("dialogue", "question")]
        fh.write(f"# Align report — {P['name']}\n\n")
        fh.write(f"Thoại/câu hỏi: {len(spoken)} · thiếu: {len(P['missing'])} · segments: {len(P['segments'])} · "
                 f"overlays: {len(P['overlays'])} · highlights: {len(P['highlights'])}\n\n")
        fh.write("| # | Scene | File | In–Out | Label | Score | Notes |\n|---|---|---|---|---|---|---|\n")
        for i, s in enumerate(P["segments"]):
            notes = "; ".join(s.get("flags", []))[:160]
            fh.write(f"| {i} | {s['scene']} | {s['file']} | {s['in']:.2f}–{s['out']:.2f} | {s['label'][:60].replace('|', '/')} | "
                     f"{s.get('score', 0):.2f} | {notes} |\n")
        if P["overlays"]:
            fh.write("\n## Cutaway (multicam / che jump cut)\n" + "\n".join(
                f"- seg {o['anchor']} +{o['offset']}s: {o['file']} {o['in']}–{o['in'] + o['dur']:.2f}" + (" (che jump cut)" if o.get("kind") == "jumpcut" else "")
                for o in P["overlays"]) + "\n")
        if P["missing"]:
            fh.write("\n## Câu kịch bản không có trong source\n" + "\n".join(f"- {m['line']} ({m['scene']}): {m['text']}" for m in P["missing"]) + "\n")
        if P.get("source_gaps"):
            fh.write("\n## ⚠ Thiếu source đạt chuẩn (theo kịch bản)\n" + "\n".join(
                f"- {g['line']} ({g['scene']}): {g['text'][:70]} — {g['note']}" for g in P["source_gaps"]) + "\n")
        gaps = [s for s in P["segments"] if s.get("gap")]
        if gaps:
            fh.write(f"\n## ⚠ Đoạn chừa trống trên timeline ({len(gaps)} thẻ chữ · {sum(g['out'] for g in gaps):.0f}s)\n" +
                     "\n".join(f"- {g['script_line']} ({g['scene']}) {g['out']:.1f}s: {g['label']}" for g in gaps) + "\n")
        if P["broll_requests"]:
            fh.write("\n## B-roll cần tìm / generate\n" + "\n".join(f"- {b['line']} ({b['scene']}): {b['desc']}" for b in P["broll_requests"]) + "\n")
        if P["unused_speech"]:
            fh.write("\n## Clip có thoại chưa dùng\n" + "\n".join(f"- {u['file']}: {u['text']}" for u in P["unused_speech"]) + "\n")
        if P["report_notes"]:
            fh.write("\n## Ghi chú\n" + "\n".join(f"- {n}" for n in P["report_notes"]) + "\n")
