"""CapCut probe draft JES_KF_PROBE — settles what nxcore.capcut can only guess, by looking (stage 0.3).

Every section shows its own instruction on screen; the user reports what they saw:

  1  0–6 s    background zooms in?                     → KFTypeScaleX works (uniform)
     0–6 s    music silent from 2 s to 4 s?            → KFTypeVolume works
     0–6 s    small picture top-right grows?           → UNIFORM_SCALE works
  2–5 6–30 s  four clips, in-point 5 s, speed 2×, black until a step keyframe; the picture must appear exactly when
              the line "HÌNH PHẢI HIỆN LÚC NÀY" appears (3 s into the clip). Each clip encodes the step with one
              hypothesis of keyframe time_offset:
                H1 material (source) time  = s0 + rel × speed        (what capcut.py does now)
                H2 segment time            = rel
                H3 segment source time     = rel × speed
                H4 timeline time           = t0 + rel
  6  30–36 s  "🔥🔥 TRẮNG | VÀNG": only VÀNG yellow?      → styles[].range counts UTF-16 units

    python dev/capcut/kf_probe.py            (CapCut closed; index backed up by nxcore.capcut.write)
"""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "engine"))
from nxcore import capcut as cc

SRC = "G:/PQ/SOURCE/video Gopro/GX019681.MP4"         # 4K59.94 GoPro, 20 s
MUSIC = "G:/PQ/INTRO 1.mp3"
FPS = 30.0
US = 1_000_000


def clip(path, t0, t1, in_s, speed=100.0, scale=None):
    f = lambda s: int(round(s * FPS))
    return {"path": path, "name": os.path.basename(path), "start": f(t0), "end": f(t1), "in_": int(round(in_s * FPS * 100.0 / speed)),
            "out_": 0, "speed": speed, "scale": scale, "scale_kf": [], "rot": 0.0, "opacity": 1.0, "level": None, "level_kf": [],
            "fade_in": 0, "fade_out": 0, "tr_out": None, "track_idx": 1}


def text(D, a, b, s, tri, font, y=0.0, size=None, styles=None):
    seg = D.subtitle(a, b, s, tri, font)
    m = next(x for x in D.mats["texts"] if x["id"] == seg["material_id"])
    c = json.loads(m["content"])
    if size:
        for st in c["styles"]:
            st["size"] = size
        m["font_size"] = size
    if styles:                                    # [(start, end, rgb)] in UTF-16 units
        base = c["styles"][0]
        c["styles"] = [dict(json.loads(json.dumps(base)), range=[a_, b_], fill={"content": {"render_type": "solid", "solid": {"color": rgb}}})
                       for a_, b_, rgb in styles]
    m["content"] = json.dumps(c, ensure_ascii=False)
    seg["clip"]["transform"]["y"] = y
    return seg


def main():
    tpl = json.load(open(cc.TPL_PATH, encoding="utf-8"))
    D = cc.Draft(tpl, 1920, 1080, FPS)
    font = cc.system_font()
    v1, v2, tx, tx2 = [], [], [], []
    # 1 · scale X keyframes on the background (in 0, speed 1, t0 0: every time hypothesis agrees here)
    s = D.video(clip(SRC, 0, 6, 0.0), 0, 0.0)
    s["common_keyframes"] = D._kf("KFTypeScaleX", [(0, 1.0), (6 * US, 1.8)])
    v1.append(s)
    # 1 · UNIFORM_SCALE keyframes on a small picture top-right
    s = D.video(clip(SRC, 0, 6, 0.0), 1, 0.0)
    s["clip"]["scale"] = {"x": 0.3, "y": 0.3}
    s["clip"]["transform"] = {"x": 0.6, "y": 0.55}
    s["common_keyframes"] = D._kf("UNIFORM_SCALE", [(0, 0.3), (6 * US, 0.6)])
    v2.append(s)
    tx.append(text(D, 0, 6, "1) Nền phóng to dần? · Ô nhỏ góc phải to dần? · Nhạc TẮT từ giây 2 đến 4?", 2, font, y=0.85))
    # 2–5 · time-base hypotheses: in-point 5 s, speed 2×, picture steps 0 → 1 at 3 s into the clip
    hyp = [("H1", lambda t0, s0, sp, r: s0 + r * sp), ("H2", lambda t0, s0, sp, r: r),
           ("H3", lambda t0, s0, sp, r: r * sp), ("H4", lambda t0, s0, sp, r: t0 + r)]
    for k, (name, enc) in enumerate(hyp):
        a = 6 + 6 * k
        s = D.video(clip(SRC, a, a + 6, 5.0, speed=200.0), 0, 0.0)
        t0, s0, sp = s["target_timerange"]["start"], s["source_timerange"]["start"], s["speed"]
        pts = [(enc(t0, s0, sp, r * US), v) for r, v in ((0.0, 0.0), (2.95, 0.0), (3.0, 1.0))]
        s["common_keyframes"] = D._kf("KFTypeAlpha", pts)
        v1.append(s)
        tx.append(text(D, a, a + 6, f"{k + 2}) Clip {name}: hình phải hiện ĐÚNG lúc dòng chữ giữa màn hình xuất hiện", 2, font, y=0.85))
        tx2.append(text(D, a + 3, a + 6, "HÌNH PHẢI HIỆN LÚC NÀY", 3, font, y=0.0, size=14.0))
    # 6 · UTF-16 style ranges
    v1.append(D.video(clip(SRC, 30, 36, 2.0), 0, 0.0))
    line = "🔥🔥 TRẮNG | VÀNG"
    u = lambda x: len(x.encode("utf-16-le")) // 2
    k = u(line[:line.index("VÀNG")])
    tx.append(text(D, 30, 36, "6) Chỉ đúng chữ VÀNG có màu vàng?", 2, font, y=0.85))
    tx2.append(text(D, 30, 36, line, 3, font, y=0.0, size=14.0, styles=[(0, k, [1, 1, 1]), (k, u(line), [1.0, 0.85, 0.0])]))
    # music: KFTypeVolume — silent from 2 s to 4 s (in 0, t0 0)
    m = D.audio(clip(MUSIC, 0, 36, 0.0), 4)
    m["volume"] = 1.0
    m["common_keyframes"] = D._kf("KFTypeVolume", [(0, 1.0), (1.95 * US, 1.0), (2.0 * US, 0.0), (3.95 * US, 0.0), (4.0 * US, 1.0)])
    D.add_track("video", v1)
    D.add_track("video", v2)
    D.add_track("text", tx, flag=1)
    D.add_track("text", tx2, flag=1)
    D.add_track("audio", [m])
    for t in D.d["tracks"]:                    # track_render_index = position in the track list
        for sg in t["segments"]:
            sg["track_render_index"] = D.d["tracks"].index(t)
    D.finish("JES_KF_PROBE")
    print(json.dumps(cc.write(D, "JES_KF_PROBE", replace=True, src=__file__), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
