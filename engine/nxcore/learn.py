"""Learn the editor's style from finished work (→ nxcore.prefs profile).

  python -m nxcore.learn xml "cut.xml" [--src SOURCE]         # any FCP7 XML exported from Premiere (past edits)
  python -m nxcore.learn sequence --plan plan.json --seq seq.json   # built plan vs. the editor's edited sequence
  python -m nxcore.learn show

What is learned: b-roll/dialogue shot length, slow-motion habit on 50/60p (rate + speed), manual tilt fixes,
dissolve use/length, which shots survive (quality kept vs removed), trims. Rules derived in prefs.derive().
"""
import argparse
import json
import math
import os
import sys
import urllib.parse
import xml.etree.ElementTree as ET

from . import prefs


def _resolve_times(items):
    """FCP XML: start/end = -1 next to transitions → take the transition centre (or its edge for start/end-black)."""
    out = []
    for k, c in enumerate(items):
        if c.tag != "clipitem":
            continue
        s, e = int(c.findtext("start") or 0), int(c.findtext("end") or 0)
        if s < 0 and k > 0 and items[k - 1].tag == "transitionitem":
            t = items[k - 1]; a, b = int(t.findtext("start")), int(t.findtext("end"))
            s = a if t.findtext("alignment") == "start-black" else (a + b) // 2
        if e < 0 and k + 1 < len(items) and items[k + 1].tag == "transitionitem":
            t = items[k + 1]; a, b = int(t.findtext("start")), int(t.findtext("end"))
            e = b if t.findtext("alignment") == "end-black" else (a + b) // 2
        out.append((c, s, e))
    return out


def _param(c, effectid, pid):
    for fl in c.findall("filter"):
        e = fl.find("effect")
        if e is not None and e.findtext("effectid") == effectid:
            for p in e.findall("parameter"):
                if (p.findtext("parameterid") or p.findtext("name")) == pid:
                    return p.findtext("value")
    return None


def learn_xml(xml_path, src=None):
    root = ET.parse(xml_path).getroot()
    seq = root.find("sequence") if root.tag == "xmeml" else root
    fps = int(seq.findtext("rate/timebase") or 30)
    rates = {}
    for f in root.iter("file"):
        if f.get("id") and f.findtext("rate/timebase"):
            rates[f.get("id")] = int(f.findtext("rate/timebase"))
    vtracks = seq.findall("media/video/track")
    atracks = seq.findall("media/audio/track")
    if not vtracks:
        raise SystemExit("XML không có video track")
    # audio clips per start frame (to tell dialogue from b-roll): a V1 clip with a linked/co-starting A1-A4 clip of the same file
    aud = {}
    for tr in atracks[:4]:
        for c, s, e in _resolve_times([x for x in tr if x.tag in ("clipitem", "transitionitem")]):
            aud.setdefault(c.findtext("name"), []).append((s, e))
    P = prefs.load()
    st = P.setdefault("stats", {})
    n_b = n_t = slowed = seen60 = rot = diss = 0
    items = [x for x in vtracks[0] if x.tag in ("clipitem", "transitionitem")]
    for t in items:
        if t.tag == "transitionitem":
            d = (int(t.findtext("end")) - int(t.findtext("start"))) / fps
            if 0.1 < d < 4 and t.findtext("alignment") == "center":
                prefs.ema(st.setdefault("dissolve", {}), d); diss += 1
    for c, s, e in _resolve_times(items):
        if s < 0 or e <= s:
            continue
        L = (e - s) / fps
        name = c.findtext("name") or ""
        fe = c.find("file")
        frate = rates.get(fe.get("id")) if fe is not None else None
        spd = _param(c, "timeremap", "speed")
        spd = float(spd) if spd not in (None, "") else 100.0
        talk = any(min(e, b) - max(s, a) > 0.5 * (e - s) for a, b in aud.get(name, []))
        if talk:
            prefs.ema(st.setdefault("talk_len", {}), L); n_t += 1
        else:
            prefs.ema(st.setdefault("broll_len", {}), L); n_b += 1
            if frate and frate >= 47:
                seen60 += 1
                sm = st.setdefault("slowmo", {})
                sm["seen"] = sm.get("seen", 0) + 1
                if spd < 95:
                    sm["slowed"] = sm.get("slowed", 0) + 1; slowed += 1
                    sm["speed_sum"] = sm.get("speed_sum", 0) + spd; sm["speed_n"] = sm.get("speed_n", 0) + 1
        r = _param(c, "basic", "rotation")
        if r not in (None, ""):
            dev = ((float(r) + 45) % 90) - 45                  # deviation from the nearest 90°
            if 0.3 <= abs(dev) <= 5:
                prefs.bump(P, "rotate", "manual"); rot += 1
    cuts = max(0, n_b + n_t - 1 - diss)
    st.setdefault("cuts", {})["n"] = st.get("cuts", {}).get("n", 0) + cuts
    msg = (f"học từ XML {os.path.basename(xml_path)}: {n_b} shot B-roll (TB {st.get('broll_len', {}).get('mean', 0):.1f}s), {n_t} đoạn thoại, "
           f"slow-mo {slowed}/{seen60} shot 50/60p, {rot} lần chỉnh nghiêng, {diss} dissolve")
    prefs.note(P, "xml", msg)
    prefs.save(P)
    return msg


def learn_sequence(plan, seq, stab=None):
    """plan = the plan.json the tool built; seq = jesSequence() of the SAME sequence after the editor worked on it."""
    stab = stab or {}
    P = prefs.load()
    st = P.setdefault("stats", {})
    norm = lambda p: str(p or "").replace("\\", "/").lower()
    src = norm(plan.get("src", "")).rstrip("/")
    clips = [c for tr in seq.get("video", [])[:2] for c in tr.get("clips", [])]
    used = [False] * len(clips)
    kept = removed = trimmed = added = 0
    for s in plan.get("segments", []):
        if s.get("gap") or not s.get("file"):
            continue                                   # placeholder card, not footage
        full = src + "/" + norm(s["file"])
        best, bi = 0.0, -1
        for i, c in enumerate(clips):
            if norm(c.get("media")) != full:
                continue
            ov = min(c["out"], s["out"]) - max(c["in"], s["in"])
            if ov > best:
                best, bi = ov, i
        q = (stab.get(s["file"]) or {}).get("quality")
        qs = st.setdefault("quality", {})
        if bi < 0:
            removed += 1
            if q is not None:
                qs["removed_sum"] = qs.get("removed_sum", 0) + q; qs["removed_n"] = qs.get("removed_n", 0) + 1
            continue
        used[bi] = True
        c = clips[bi]
        kept += 1
        if q is not None:
            qs["kept_sum"] = qs.get("kept_sum", 0) + q; qs["kept_n"] = qs.get("kept_n", 0) + 1
        L = c["end"] - c["start"]
        if abs(L - (s["out"] - s["in"]) * 100.0 / float(s.get("speed") or 100)) > 0.25:
            trimmed += 1
        prefs.ema(st.setdefault("talk_len" if s.get("audio", True) else "broll_len", {}), L)
        spd = c.get("speed")
        if spd is not None and not s.get("audio", True):
            sm = st.setdefault("slowmo", {})
            fps = (c.get("fps") or 0)
            if s.get("speed") or spd < 0.95:
                sm["seen"] = sm.get("seen", 0) + 1
                if spd < 0.95:
                    sm["slowed"] = sm.get("slowed", 0) + 1; sm["speed_sum"] = sm.get("speed_sum", 0) + spd * 100; sm["speed_n"] = sm.get("speed_n", 0) + 1
        if s.get("rotate_fix") and c.get("rotation") is not None:
            dev = ((float(c["rotation"]) + 45) % 90) - 45
            prefs.bump(P, "rotate", "kept" if abs(dev) >= 0.3 else "reverted")
    for i, c in enumerate(clips):
        if not used[i] and c.get("media"):
            added += 1
            prefs.ema(st.setdefault("broll_len", {}), c["end"] - c["start"])
    msg = f"học từ sequence {seq.get('name')}: giữ {kept}, bỏ {removed}, sửa độ dài {trimmed}, thêm {added} clip"
    prefs.note(P, "sequence", msg)
    prefs.save(P)
    return msg


def main():
    for s_ in (sys.stdout, sys.stderr):
        try:
            s_.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    a1 = sp.add_parser("xml"); a1.add_argument("xml"); a1.add_argument("--src")
    a2 = sp.add_parser("sequence"); a2.add_argument("--plan", required=True); a2.add_argument("--seq", required=True)
    sp.add_parser("show")
    a = ap.parse_args()
    if a.cmd == "xml":
        print(learn_xml(a.xml, a.src))
    elif a.cmd == "sequence":
        plan = json.load(open(a.plan, encoding="utf-8"))
        seq = json.load(open(a.seq, encoding="utf-8"))
        stab = {}
        try:
            stab = json.load(open(os.path.join(plan["src"], "_nexys_work", "shake.json"), encoding="utf-8"))
        except Exception:
            pass
        print(learn_sequence(plan, seq, stab))
    P = prefs.load()
    print("RULES " + json.dumps(prefs.derive(P), ensure_ascii=False))
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
