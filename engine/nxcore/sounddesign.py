"""Auto sound design for any timeline: soundtrack fit + section markers + SFX placement.

Input timeline = the JSON the panel gets from Premiere (jesSequence) — so it works on sequences built by the tool
AND on timelines the editor cut by hand. Music analysis comes from nxcore.music, SFX from nxcore.sfxlib.

Rules (density light|medium|heavy, style clean|cinematic|tech|fun|hard):
  • section change in the music      → whoosh on the nearest cut (riser → hit into every drop)
  • cuts                               → whoosh (medium: spaced, heavy: every cut; glitch/meme mixed in by style)
  • text/graphic appears (upper tracks)→ pop
  • drop accents (heavy)               → hit
  • ambience bed (optional)            → looped under everything, far below dialogue
Every SFX is aligned by its PEAK (a whoosh peaks on the cut, a riser peaks on the drop), level-matched from its
measured loudness, never repeats one of the last 4 files of its role, and keeps a minimum gap per density.

CLI:
  python -m nxcore.sounddesign --timeline seq.json --sfx sfx_index.json [--music song.music.json --music-start 0]
         [--density medium] [--style clean] [--layers whoosh,riser,hit,pop,ambience,glitch] [--out sd.json]
"""
import argparse
import json
import os
import random
import sys

from .sfxlib import TARGET_DB

DENSITY = {  # min gap between SFX, whoosh probability on ordinary cuts, min shot length for a cut whoosh
    "light":  {"gap": 2.5, "cut_p": 0.0, "min_shot": 99, "pop": False, "accent_hits": False},
    "medium": {"gap": 1.2, "cut_p": 0.45, "min_shot": 1.4, "pop": True, "accent_hits": False},
    "heavy":  {"gap": 0.45, "cut_p": 1.0, "min_shot": 0.35, "pop": True, "accent_hits": True},
}
STYLE_POOL = {
    "clean": {"clean", "cinematic", "natural"}, "cinematic": {"cinematic", "clean", "natural"},
    "tech": {"tech", "clean"}, "fun": {"fun", "clean"}, "hard": {"hard", "cinematic", "clean"},
}
MAX_LEN = {"whoosh": 2.5, "hit": 3.5, "pop": 1.2, "glitch": 1.5, "meme": 2.0, "accent": 2.5, "riser": 8.0}
LABEL_COLOR = {"intro": 3, "main": 4, "verse": 4, "build": 5, "drop": 1, "break": 6, "outro": 3}   # Premiere marker colour index


class Picker:
    def __init__(self, sfx, style, seed):
        self.files = [dict(v, path=k) for k, v in sfx["files"].items()]
        self.pool = STYLE_POOL.get(style, STYLE_POOL["clean"])
        self.rng = random.Random(seed)
        self.recent = {}

    def pick(self, role, want_dur=None, want_peak=None):
        c = [f for f in self.files if f["role"] == role and f["style"] in self.pool]
        if not c and role not in ("riser", "hit"):      # never drop a glitchy riser/hit into a clean edit
            c = [f for f in self.files if f["role"] == role]
        if not c:
            return None
        rec = self.recent.setdefault(role, [])
        fresh = [f for f in c if f["path"] not in rec] or c
        if want_peak is not None:                       # risers: peak time close to the lead-in we want
            fresh.sort(key=lambda f: abs(f["peak_t"] - want_peak))
            fresh = fresh[:max(3, len(fresh) // 4)]
        elif want_dur is not None:
            fresh.sort(key=lambda f: abs(min(f["dur"], MAX_LEN.get(role, 3)) - want_dur))
            fresh = fresh[:max(4, len(fresh) // 3)]
        f = self.rng.choice(fresh)
        rec.append(f["path"]); del rec[:-4]
        return f


def gain_for(f, role, boost=0.0):
    g = TARGET_DB.get(role, -22) - f.get("loud_db", -20) + boost
    return round(max(-24.0, min(6.0, g)), 1)


def cuts_from(tl, main_tracks=(0, 1)):
    """Cut points on the picture: starts of clips on the main + cutaway tracks (first clip excluded)."""
    pts = []
    for tr in tl.get("video", []):
        if tr.get("track") not in main_tracks:
            continue
        for c in tr.get("clips", []):
            pts.append((round(c["start"], 3), round(c["end"] - c["start"], 3), tr["track"]))
    pts.sort()
    out, last = [], None
    for t, d, trk in pts:
        if t <= 0.05:
            continue
        if last is not None and abs(t - last) < 0.1:
            continue
        out.append({"t": t, "shot": d, "track": trk}); last = t
    return out


def text_starts(tl, min_track=2):
    ts = []
    for tr in tl.get("video", []):
        if tr.get("track", 0) >= min_track:
            ts += [round(c["start"], 3) for c in tr.get("clips", []) if c["end"] - c["start"] < 30 and c["start"] > 0.3]  # long = watermark
    return sorted(set(ts))


def dialogue_ranges(tl, tracks=(0, 1)):
    rs = []
    for tr in tl.get("audio", []):
        if tr.get("track") in tracks and not tr.get("muted"):
            rs += [(c["start"], c["end"]) for c in tr.get("clips", []) if c.get("enabled", True)]
    rs.sort()
    merged = []
    for s, e in rs:
        if merged and s <= merged[-1][1] + 0.3:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def in_ranges(t, rs):
    return any(s - 0.1 <= t <= e + 0.1 for s, e in rs)


def design(tl, sfx, music=None, music_start=0.0, music_in=0.0, density="medium", style="clean",
           layers=("whoosh", "riser", "hit", "pop"), seed=7, duck_music=True, fit_music=True):
    D = DENSITY.get(density, DENSITY["medium"])
    P = Picker(sfx, style, seed)
    dur = float(tl.get("dur") or 0) or max([c["end"] for tr in tl.get("video", []) for c in tr.get("clips", [])] + [0])
    cuts = cuts_from(tl)
    texts = text_starts(tl)
    dlg = dialogue_ranges(tl)
    events, markers, used_t = [], [], []
    layers = set(layers)
    notes = []
    # SFX already on the timeline (e.g. from Build) → keep, don't stack another one on top
    lib = set(sfx.get("files", {}))
    lib_names = {os.path.basename(k).lower() for k in lib}
    existing = []
    for tr in tl.get("audio", []):
        for c in tr.get("clips", []):
            m = (c.get("media") or "")
            if m in lib or os.path.basename(m).lower() in lib_names or os.path.basename(c.get("name", "")).lower() in lib_names:
                existing.append(round(c["start"], 3))
    if existing:
        notes.append(f"giữ {len(existing)} SFX đã có trên timeline, không đặt chồng")
    # structure from Build markers: "■ <scene>" ; "■ HOOK…" marks the opening hook
    scene_marks, hook_end = [], None
    mk = sorted(tl.get("markers", []), key=lambda m: m["t"])
    for m in mk:
        if str(m.get("name", "")).startswith("■"):
            scene_marks.append({"t": m["t"], "name": m["name"][1:].strip()})
    for i, m in enumerate(scene_marks):
        if "hook" in m["name"].lower() and i + 1 < len(scene_marks):
            hook_end = scene_marks[i + 1]["t"]
    dlg_cover = sum(e - s for s, e in dlg) / max(dur, 1.0)
    talky = dlg_cover > 0.45

    def free(t, gap=None):
        g = D["gap"] if gap is None else gap
        return all(abs(t - u) >= g for u in used_t) and all(abs(t - u) >= 0.6 for u in existing)

    def place(role, t_peak, reason, boost=0.0, want_dur=None, want_peak=None, force=False):
        if role not in layers and not (role in ("glitch", "meme") and "whoosh" in layers):
            return False
        if not force and not free(t_peak):
            return False
        f = P.pick(role, want_dur=want_dur, want_peak=want_peak)
        if not f:
            return False
        start = t_peak - f["peak_t"]
        trim_in = 0.0
        if start < 0:
            trim_in, start = -start, 0.0
        length = min(f["dur"] - trim_in, MAX_LEN.get(role, 3.0), max(0.2, dur - start))
        if length <= 0.1:
            return False
        b = boost - (4.0 if in_ranges(t_peak, dlg) and role in ("hit", "whoosh", "glitch", "meme") else 0.0)
        events.append({"t": round(start, 3), "peak": round(t_peak, 3), "in": round(trim_in, 3), "dur": round(length, 3),
                       "path": f["path"], "name": os.path.basename(f["path"]), "role": role,
                       "gain_db": gain_for(f, role, b), "reason": reason})
        used_t.append(t_peak)
        return True

    # ---- music structure on the timeline
    secs = []
    mus = None
    if music:
        for s in music["sections"]:
            a, b = s["start"] - music_in + music_start, s["end"] - music_in + music_start
            if b <= 0 or a >= dur:
                continue
            secs.append(dict(s, tl_start=round(max(0.0, a), 3), tl_end=round(min(dur, b), 3)))
        for s in secs:
            markers.append({"t": s["tl_start"], "name": "♪ " + s["label"].upper(),
                            "comment": f"{music['name']} · {s['label']} · energy {s['energy']}", "color": LABEL_COLOR.get(s["label"], 4)})
        mus = {"path": music["file"], "start": music_start, "in": music_in, "bpm": music["bpm"]}
        if fit_music:
            from .music import fit_to_length
            ft = fit_to_length(music, dur - music_start + music_in)
            mus["end_in_song"] = ft["end"]
            mus["dur"] = round(min(ft["end"] - music_in, dur - music_start), 3)
            mus["fade_out"] = ft["fade"]
        if duck_music and dlg:
            mus["duck"] = [[round(s, 3), round(e, 3)] for s, e in dlg]

    def nearest_cut(t, win):
        c = [x for x in cuts if abs(x["t"] - t) <= win]
        return min(c, key=lambda x: abs(x["t"] - t)) if c else None

    # phrase markers inside long sections (pacing guide)
    if music:
        for ph in music.get("phrases", []):
            if ph["part"] > 1:
                t = ph["start"] - music_in + music_start
                if 0 < t < dur:
                    markers.append({"t": round(t, 3), "name": "· " + ph["label"] + f" {ph['part']}/{ph['parts']}", "comment": "phrase 8 bar", "color": 7})
    # entries = drops; a steady song without drops gets its first "main" after intro/build as the entry
    entries = [s for s in secs if s["label"] == "drop"]
    if not entries:
        for i, s in enumerate(secs):
            if s["label"] == "main" and i > 0 and secs[i - 1]["label"] in ("intro", "build"):
                entries = [s]; break
    # 1) entries: riser into + hit on it (cut-snapped when a cut is close)
    for s in entries:
        if s["tl_start"] < 0.5:
            continue
        c = nearest_cut(s["tl_start"], 0.35)
        t = c["t"] if c else s["tl_start"]
        if "riser" in layers:
            lead = min(4.0, max(1.5, (t - (secs[secs.index(s) - 1]["tl_start"] if secs.index(s) else 0)) * 0.6))
            place("riser", t, f"riser vào {s['label']} @{t:.2f}s", want_peak=lead, force=True)
        place("hit", t, f"hit đầu {s['label']}", boost=2.0 if density == "heavy" else 0.0, force=True)
    # 2) other section changes → whoosh on the nearest cut
    for s in secs:
        if s in entries or s["tl_start"] < 0.5:
            continue
        c = nearest_cut(s["tl_start"], 0.6)
        if c:
            place("whoosh", c["t"], f"đổi đoạn nhạc → {s['label']}", want_dur=1.2)
    # 2b) structure without music (or in addition): hook end, scene changes, ending
    def near_cut(t, win=0.5):
        c = nearest_cut(t, win)
        return c["t"] if c else t
    if hook_end and hook_end < dur - 1:
        t = near_cut(hook_end)
        if not secs and "riser" in layers and hook_end > 3:
            place("riser", t, "riser hết hook", want_peak=min(3.0, hook_end * 0.6), force=True)
        place("hit", t, "hết hook → vào nội dung", force=True)
    for m in scene_marks:
        if m["t"] < 0.5 or (hook_end and abs(m["t"] - hook_end) < 0.3) or "hook" in m["name"].lower():
            continue
        t = near_cut(m["t"])
        if place("whoosh", t, "đổi cảnh: " + m["name"][:40], want_dur=1.4, force=True) and density != "light" and not secs:
            f = P.pick("hit")
            if f and "hit" in layers:     # soft low hit layered under the scene-change whoosh
                events.append({"t": round(max(0.0, t - f["peak_t"]), 3), "peak": round(t, 3), "in": 0.0,
                               "dur": round(min(f["dur"], 2.0), 3), "path": f["path"], "name": os.path.basename(f["path"]),
                               "role": "hit", "gain_db": gain_for(f, "hit", -8.0), "reason": "lớp trầm dưới whoosh đổi cảnh"})
    if not secs and dur > 8 and "hit" in layers:
        last_cut = max([c["t"] for c in cuts if c["t"] < dur - 1.5] or [0])
        if dur - last_cut < 8:
            place("hit", last_cut, "vào cảnh kết", boost=-3.0)
    if not secs and not scene_marks and not hook_end and cuts and "hit" in layers:
        first = next((c for c in cuts if c["t"] >= 1.5), None)   # plain montage: mark the first real cut
        if first:
            place("hit", first["t"], "mở nhịp montage", boost=-3.0)

    # 3) ordinary cuts
    rng = random.Random(seed + 1)
    def sec_at(t):
        for s in secs:
            if s["tl_start"] <= t < s["tl_end"]:
                return s
        return None
    for c in cuts:
        s = sec_at(c["t"])
        p = D["cut_p"] * (1.3 if s and s["label"] in ("drop", "build") else 0.7 if s and s["label"] in ("intro", "outro", "break") else 1.0)
        if c["shot"] < D["min_shot"] or rng.random() > min(1.0, p):
            continue
        if talky and density != "heavy" and in_ranges(c["t"], dlg):
            continue          # never whoosh between two sentences of a conversation
        role = "whoosh"
        if style == "tech" and rng.random() < 0.5:
            role = "glitch"
        elif style == "fun" and rng.random() < 0.3:
            role = "meme"
        place(role, c["t"], "cut", want_dur=0.8 if density == "heavy" else 1.2)
    # 4) text pops
    if D["pop"] and "pop" in layers:
        for t in texts:
            place("pop", t, "text xuất hiện", force=False)
    # 5) accent hits inside drops (heavy)
    if D["accent_hits"] and music and "hit" in layers:
        for a in music.get("accents", []):
            t = a - music_in + music_start
            s = sec_at(t)
            if s and s["label"] == "drop":
                place("hit", t, "accent nhạc", boost=-4.0)
    # 6) ambience bed
    amb = []
    if "ambience" in layers:
        t = 0.0
        while t < dur - 0.5:
            f = P.pick("ambience", want_dur=dur - t)
            if not f:
                break
            length = min(f["dur"], dur - t)
            amb.append({"t": round(t, 3), "in": 0.0, "dur": round(length, 3), "path": f["path"], "name": os.path.basename(f["path"]),
                        "role": "ambience", "gain_db": gain_for(f, "ambience"), "reason": "ambience nền"})
            t += max(1.0, length - 0.5)
    events.sort(key=lambda e: e["t"])
    stats = {}
    for e in events + amb:
        stats[e["role"]] = stats.get(e["role"], 0) + 1
    if not music:
        notes.append("chưa chọn nhạc → không có riser/hit theo drop; chọn soundtrack để SFX bám cấu trúc nhạc")
    if talky:
        notes.append(f"nội dung nhiều thoại ({int(dlg_cover * 100)}%) → không đặt whoosh giữa các câu thoại")
    return {"notes": notes, "structure": {"scenes": len(scene_marks), "hook_end": hook_end, "talky": talky},
            "duration": round(dur, 3), "density": density, "style": style, "cuts": len(cuts), "texts": len(texts),
            "sfx": events, "ambience": amb, "markers": markers, "music": mus, "stats": stats}


def main():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeline", required=True)
    ap.add_argument("--sfx", required=True)
    ap.add_argument("--music")
    ap.add_argument("--music-start", type=float, default=0.0)
    ap.add_argument("--music-in", type=float, default=0.0)
    ap.add_argument("--density", default="medium", choices=list(DENSITY))
    ap.add_argument("--style", default="clean", choices=list(STYLE_POOL))
    ap.add_argument("--layers", default="whoosh,riser,hit,pop")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-duck", action="store_true")
    ap.add_argument("--no-fit", action="store_true")
    ap.add_argument("--gain-db", type=float, default=0.0, help="added to every SFX / ambience level (Sổ luật: sfx_gain_db)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    tl = json.load(open(a.timeline, encoding="utf-8"))
    sfx = json.load(open(a.sfx, encoding="utf-8"))
    music = json.load(open(a.music, encoding="utf-8")) if a.music else None
    R = design(tl, sfx, music, a.music_start, a.music_in, a.density, a.style, [x for x in a.layers.split(",") if x], a.seed, not a.no_duck, not a.no_fit)
    if a.gain_db:
        for e in R["sfx"] + R["ambience"]:
            e["gain_db"] = round(float(e["gain_db"]) + a.gain_db, 1)
        R.setdefault("notes", []).append(f"Sổ luật: SFX {a.gain_db:+.1f} dB so với mức tự chọn")
    json.dump(R, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"sound design: {len(R['sfx'])} SFX + {len(R['ambience'])} ambience · markers {len(R['markers'])} · " +
          ", ".join(f"{k} {v}" for k, v in R["stats"].items()))
    for e in R["sfx"][:60]:
        print(f"  {e['peak']:7.2f}s {e['role']:8} {e['gain_db']:+5.1f} dB  {e['name']}  ({e['reason']})")
    print("OUT", a.out)
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
