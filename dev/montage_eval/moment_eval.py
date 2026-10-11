"""Which window does montage take inside a clip, vs. the window a person would take?
14 shots of the user's GoPro beat cut, labelled by looking at every frame (contact sheets): ideal start ranges.
The clips are re-analysed with the NEW stability pass (v8: + luma) once (cached), then best_sub runs per weight set."""
import json, os, pickle, sys
sys.path.insert(0, r"G:\JustinEditSuite\engine")
os.environ["JES_NO_STYLE"] = "1"
from nxcore import montage, stability

SRC = r"G:/PQ/SOURCE/video Gopro"
HERE = os.path.dirname(os.path.abspath(__file__))
# file, source window length (s), slow-mo?, acceptable START ranges, the start the v17 plan took, verdict on it
LAB = [
    ("GX019712.MP4", 1.81, False, [[0.2, 2.6]], 5.8, "bad: chỉ còn chân, mất đá + biển"),
    ("GX019715.MP4", 2.25, False, [[5.5, 6.76]], 3.5, "bad: bãi cỏ trống, người chạy tới sau đó"),
    ("GX019736.MP4", 1.34, False, [[2.3, 3.4]], 6.9, "bad: đứng yên sau khi leo"),
    ("20260523_174053.mp4", 1.80, False, [[4.9, 5.27]], 2.9, "bad: tối, máy chưa chỉnh sáng"),
    ("GX019664.MP4", 0.88, False, [[0.5, 2.5]], 6.1, "bad: cúi người, bỏ lúc dang tay"),
    ("GX019729.MP4", 0.90, False, [[4.0, 7.2]], 10.9, "bad: đứng yên, bỏ lúc đi trên sống đá"),
    ("GX019666.MP4", 2.54, True, [[2.4, 3.9]], 3.3, "ok"),
    ("GX019681.MP4", 1.82, False, [[0.8, 3.6], [15.8, 17.2]], 16.3, "ok"),
    ("20260523_083511.mp4", 3.66, False, [[0.9, 4.2]], 1.6, "ok"),
    ("GX019705.MP4", 1.82, False, [[2.0, 8.0]], 4.5, "ok"),   # widened after review: 2.2–4.0 is the tilt-up reveal of the vista
    ("GX019707.MP4", 0.90, False, [[3.8, 13.6]], 7.6, "ok"),
    ("GX019746.MP4", 1.35, True, [[0.4, 8.6], [13.6, 17.4]], 3.8, "ok"),   # 13.6–17.4: turns and looks at the camera
    ("GX019744.MP4", 1.37, False, [[2.4, 5.0], [6.4, 9.6]], 7.2, "ok"),
    ("GX019727.MP4", 1.84, False, [[0.5, 10.5]], 8.8, "ok"),
]

def fresh_stab():
    cache = os.path.join(HERE, "stab_v8.pkl")
    if os.path.exists(cache):
        return pickle.load(open(cache, "rb"))
    media = json.load(open(os.path.join(SRC, "_nexys_work", "media.json"), encoding="utf-8"))
    out = {}
    for f, *_ in LAB:
        r = stability.analyze_clip(os.path.join(SRC, f), media[f], cuda=True)
        r.pop("_qc", None)
        out[f] = r
        print(f"  quét lại {f}: luma {len(r.get('luma') or [])} mẫu, act {len(r.get('act') or [])}, usable {r.get('usable')}", flush=True)
    pickle.dump(out, open(cache, "wb"))
    return out

def run(C, stab, verbose=False):
    mat = montage.Material(SRC, C)
    for f, s in stab.items():
        mat.stab[f] = s
    mat._prof.clear()
    mat.cands = mat._windows() if hasattr(mat, "_windows") else mat.cands
    hits_bad = hits_ok = 0
    lines = []
    for f, D, slow, ideal, cur, verdict in LAB:
        cs = [c for c in mat.cands if c["file"] == f and c["b"] - c["a"] >= D - 1e-6 and (not c["slow_only"] or slow)]
        best = None
        for c in cs:
            got = mat.best_sub(c, D, slow, (), want_mid=True, speech_w=0.35, accents=[(0.0, 1.0)])
            if got and (best is None or got[1] > -9):
                # several candidates (segments) of one clip: take the one whose window scores best — same rule pick() uses
                # at equal clip-level terms (stability of the window)
                if best is None or got[1] > best[1] + 0.05 or (abs(got[1] - best[1]) <= 0.05 and c is cs[0]):
                    best = got
        s0 = best[0] if best else None
        hit = s0 is not None and any(a - 0.15 <= s0 <= b + 0.15 for a, b in ideal)
        if verdict.startswith("bad"): hits_bad += hit
        else: hits_ok += hit
        lines.append(f"  {'✔' if hit else '✖'} {f:22} D {D:4.2f}{' slow' if slow else '     '}  lấy {s0 if s0 is None else round(s0, 2)!s:>6}  (đúng {ideal})  · cũ {cur} — {verdict}")
    if verbose:
        print("\n".join(lines))
    return hits_bad, hits_ok

if __name__ == "__main__":
    stab = fresh_stab()
    base = montage.config("beat_cut")
    grid = [("hiện tại", 0.0, 0.0, 0.5, 0.0)] + [(f"moment {m} · expo 0.6 · in {ki} · forgive {fg}", m, 0.6, ki, fg)
                                                  for m in (0.35, 0.5) for ki in (0.5, 0.25) for fg in (0.0, 0.5, 0.8)]
    for name, m, e, ki, fg in grid:
        C = dict(base, w_moment=m, w_expo=e, w_action_in=ki, moment_forgive=fg)
        b, o = run(C, stab)
        print(f"{name:44}  sai→đúng {b}/6 · ổn giữ được {o}/8")
    if len(sys.argv) > 4:
        C = dict(base, w_moment=float(sys.argv[1]), w_expo=float(sys.argv[2]), w_action_in=float(sys.argv[3]), moment_forgive=float(sys.argv[4]))
        print("\nchi tiết", sys.argv[1:]); run(C, stab, verbose=True)
