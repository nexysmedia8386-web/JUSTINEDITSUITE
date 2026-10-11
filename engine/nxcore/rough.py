# -*- coding: utf-8 -*-
"""Rough cut theo giờ quay — toàn bộ đoạn ĐẠT CHUẨN của source, không cần kịch bản — Justin EDIT Suite.

  python -m nxcore.rough SRC --plan OUT/plan_X_ROUGH.json [--name X_ROUGH] [--out OUT] [--seq 1920x1080@25:fill]
                             [--min 1.0] [--q 45] [--keep-shaky]

Nguồn đoạn (đã có sau bước Phân tích, không decode lại):
  · segments.json  clip dài → nhiều đoạn dùng được (bỏ rung mạnh / đen / mờ / đứng hình, không cắt giữa câu thoại)
  · shake.json     clip ngắn không có trong segments.json → vùng usable
  · qc.json        đen / mờ / đứng hình / tối / cháy sáng bị trừ khỏi vùng usable (fallback)
  · digest.json    trạng thái clip (LOẠI bị bỏ) · media.json giờ quay, fps, thư mục · roles.json A/B-roll
Xếp theo giờ quay (created_ts), tốc độ 100%, giữ âm thanh gốc (thoại A1, tiếng B-roll A2).
Màu clip trên timeline theo vai trò (segment "color" → <labels><label2> trong XML), marker đầu mỗi buổi / thư mục.
"""
import argparse
import collections
import json
import os
import sys

from . import jsonio

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

# vai trò → (màu label Premiere, tên hiển thị) — cùng bảng màu với label clip trong Project (tab Kịch bản)
ROLES = collections.OrderedDict([
    ("dialogue", ("Mango", "Thoại")),
    ("broll", ("Cerulean", "B-roll")),
    ("slowmo", ("Teal", "B-roll 50/60p+ (slow-mo được)")),
    ("handheld", ("Lavender", "Cầm tay / có chuyển động máy")),
    ("static", ("Yellow", "Máy đặt cố định / timelapse")),
])
BAD_QC = ("black", "blur", "freeze", "dark", "bright")


def _j(p, d=None):
    return jsonio.load(p, d)


def _sub(rs, cut):
    out = []
    for a, b in rs:
        segs = [[a, b]]
        for c0, c1 in sorted(cut):
            nxt = []
            for x0, x1 in segs:
                if c1 <= x0 or c0 >= x1:
                    nxt.append([x0, x1]); continue
                if c0 > x0: nxt.append([x0, c0])
                if c1 < x1: nxt.append([c1, x1])
            segs = nxt
        out += segs
    return out


def daypart(cl):
    try:
        h = int(str(cl)[11:13])
    except (TypeError, ValueError):
        return ""
    return "Sáng" if h < 11 else "Trưa" if h < 14 else "Chiều" if h < 18 else "Tối"


def scene_of(f, m):
    """buổi quay (ngày + buổi) và thư mục con → tên cảnh (marker trên timeline)"""
    folder = f.split("/")[0] if "/" in f else ""
    cl = str(m.get("created_local") or "")
    day = cl[8:10] + "/" + cl[5:7] if len(cl) >= 10 else ""
    parts = [x for x in (folder, (day + " " + daypart(cl)).strip()) if x]
    return " · ".join(parts) or "Source"


def clip_pieces(f, m, seg_entry, shake, qc, min_len, q_min, keep_shaky):
    """→ [(a, b, kind, speech_frac, q)] đoạn đạt chuẩn của một clip, theo thứ tự thời gian trong clip"""
    dur = float(m.get("dur") or 0)
    out = []
    segs = (seg_entry or {}).get("segs") or []
    if segs:
        for s in segs:
            a, b, kind = float(s["a"]), float(s["b"]), s.get("kind") or "stable"
            if kind == "shaky" and not keep_shaky:
                continue
            if s.get("q") is not None and float(s["q"]) < q_min and float(s.get("speech") or 0) < 0.3:
                continue
            if b - a >= min_len:
                out.append([a, b, kind, float(s.get("speech") or 0), s.get("q")])
        return out
    sh = shake.get(f) or {}
    rs = [list(r[:2]) for r in (sh.get("usable") or []) + (sh.get("usable_slowmo") or [])]
    rs = sorted(r for r in rs if r[1] - r[0] > 0.3)
    merged = []
    for r in rs:
        if merged and r[0] <= merged[-1][1] + 0.05:
            merged[-1][1] = max(merged[-1][1], r[1])
        else:
            merged.append(list(r))
    rs = merged or ([[0.0, dur]] if dur else [])
    q = qc.get(f) or {}
    bad = [list(r[:2]) for k in BAD_QC for r in (q.get(k) or [])]
    for a, b in _sub(rs, bad):
        if b - a >= min_len:
            out.append([a, b, "stable", 0.0, sh.get("quality")])
    return out


def role_of(kind, speech, m, role):
    if speech >= 0.3 or (role == "a_roll" and speech > 0.05):
        return "dialogue"
    if kind == "static":
        return "static"
    if kind == "handheld":
        return "handheld"
    if float(m.get("fps") or 0) >= 47:
        return "slowmo"
    return "broll"


def seg_desc(ad, f, a, b, fallback):
    c = (ad or {}).get(f) or {}
    best, bo = "", 0.0
    for k, v in (c.get("segs") or {}).items():
        try:
            x0, x1 = [float(x) for x in str(k).replace("–", "-").split("-")[:2]]
        except ValueError:
            continue
        o = min(b, x1) - max(a, x0)
        if o > bo and (v or {}).get("desc"):
            best, bo = v["desc"], o
    return best or c.get("desc") or fallback or ""


def build(src, name="", out="", seq=None, min_len=1.0, q_min=45, keep_shaky=False):
    from .montage import base_plan, default_seq
    W = os.path.join(src, "_nexys_work")
    media = _j(os.path.join(W, "media.json"), {}) or {}
    if not media:
        raise SystemExit("✖ chưa có media.json — chạy bước 1 · Phân tích trước")
    D = _j(os.path.join(W, "digest.json"), {}) or {}
    dclips = {c.get("file"): c for c in (D.get("clips") or {}).values()}
    segj = (_j(os.path.join(W, "segments.json"), {}) or {}).get("clips") or {}
    shake = _j(os.path.join(W, "shake.json"), {}) or {}
    qc = _j(os.path.join(W, "qc.json"), {}) or {}
    qc = qc.get("clips", qc) if isinstance(qc, dict) else {}
    roles = _j(os.path.join(W, "roles.json"), {}) or {}
    ad = (_j(os.path.join(W, "auto_desc.json"), {}) or {}).get("clips") or {}
    C = {"transitions": {"on": False, "fade_in": 0, "fade_out": 0}}
    P = base_plan(src, name or (os.path.basename(src.rstrip("/\\")).replace(" ", "_") + "_ROUGH"), out, seq or default_seq(media, {}), C, "rough")
    P.update({"autofix": {"slowmo": False, "rotate": True}, "seg_markers": False, "flag_markers": False, "chrono": True,
              "broll_audio": {"on": True, "gain_db": -6.0}, "subs": {"on": False}})
    order = sorted(media, key=lambda f: (float(media[f].get("created_ts") or 0), f))
    t, n_dlg, st = 0.0, 0, collections.Counter()
    src_total = used_total = 0.0
    skipped = collections.Counter()
    last_scene = None
    for f in order:
        m = media[f]
        dur = float(m.get("dur") or 0)
        src_total += dur
        if m.get("status") not in (None, "", "ok"):
            skipped["lỗi đọc"] += 1; continue
        dc = dclips.get(f) or {}
        if str(dc.get("status") or "") == "LOẠI":
            skipped["LOẠI"] += 1; continue
        if (roles.get(f) or {}).get("role") in ("junk", "audio_master", "screen"):
            skipped[(roles.get(f) or {}).get("role")] += 1; continue
        pcs = clip_pieces(f, m, segj.get(f), shake, qc, min_len, q_min, keep_shaky)
        if not pcs:
            skipped["không có đoạn đạt"] += 1; continue
        role = (roles.get(f) or {}).get("role") or m.get("role") or ""
        scene = scene_of(f, m)
        # gộp đoạn liền nhau cùng vai trò → bớt điểm cắt vô nghĩa
        merged = []
        for a, b, kind, sp, q in pcs:
            r = role_of(kind, sp, m, role)
            if merged and merged[-1][2] == r and a - merged[-1][1] <= 0.05:
                merged[-1][1] = b
            else:
                merged.append([a, b, r])
        for a, b, r in merged:
            L = b - a
            color, rname = ROLES[r]
            d = seg_desc(ad, f, a, b, dc.get("desc"))
            P["segments"].append({
                "file": f, "in": round(a, 3), "out": round(b, 3), "tl": [round(t, 3), round(t + L, 3)],
                "scene": scene, "label": (rname + " · " + d)[:110], "audio": r == "dialogue",
                "silence": False, "subs": False, "color": color, "role": r, "flags": [], "exclude": [], "script_line": "",
                "rotate_fix": m.get("rotate_fix") or 0})
            if scene != last_scene:
                P["scene_names"][scene] = scene; last_scene = scene
            t += L; used_total += L; st[r] += 1
            n_dlg += r == "dialogue"
    P["duration"] = round(t, 2)
    P["report_notes"] = [
        "Rough cut theo giờ quay: %d đoạn · %s / %s source (%.0f%%)" % (len(P["segments"]), mmss(t), mmss(src_total), 100.0 * used_total / max(src_total, 1)),
        "Vai trò: " + " · ".join("%s %d (%s)" % (ROLES[k][1], st[k], ROLES[k][0]) for k in ROLES if st[k]),
    ] + (["Bỏ qua: " + " · ".join("%s %d" % kv for kv in skipped.items())] if skipped else [])
    return P, {"segments": len(P["segments"]), "total": round(t, 2), "source": round(src_total, 2), "roles": dict(st),
               "skipped": dict(skipped), "legend": {k: ROLES[k] for k in ROLES}}


def mmss(x):
    x = int(round(x)); h, r = divmod(x, 3600); mi, s = divmod(r, 60)
    return "%d:%02d:%02d" % (h, mi, s) if h else "%d:%02d" % (mi, s)


def parse_seq(s):
    """'1920x1080@25:fill' → {w, h, fps, fit}"""
    if not s:
        return None
    wh, _, rest = s.partition("@")
    fps, _, fit = rest.partition(":")
    w, h = wh.lower().split("x")
    return {"w": int(w), "h": int(h), "fps": float(fps or 25), "fit": fit or "fill"}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nxcore.rough", description="Rough cut theo giờ quay — mọi đoạn đạt chuẩn của source")
    ap.add_argument("src")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--seq", default="")
    ap.add_argument("--min", type=float, default=1.0, help="đoạn ngắn hơn (s) bị bỏ")
    ap.add_argument("--q", type=float, default=45, help="điểm chất lượng tối thiểu của đoạn (không áp cho đoạn có thoại)")
    ap.add_argument("--keep-shaky", action="store_true")
    a = ap.parse_args(argv)
    P, rep = build(a.src, a.name, a.out or os.path.dirname(os.path.abspath(a.plan)), parse_seq(a.seq), a.min, a.q, a.keep_shaky)
    os.makedirs(os.path.dirname(os.path.abspath(a.plan)), exist_ok=True)
    jsonio.save_atomic(a.plan, P)
    jsonio.save_atomic(os.path.splitext(a.plan)[0] + ".rough.json", rep)
    for n in P["report_notes"]:
        print("✔ " + n if n.startswith("Rough") else "  " + n)
    print("plan: " + a.plan.replace("\\", "/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
