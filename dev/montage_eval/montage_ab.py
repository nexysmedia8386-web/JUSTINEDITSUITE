"""Full beat-cut montage on the user's GoPro folder, old scoring vs new (same music / settings as v17), into scratch.
Reports: empty slots, how much of the clip's action the chosen windows hold (handling taken out), shots changed."""
import json, os, subprocess, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "ab"); os.makedirs(OUT, exist_ok=True)
SRC = r"G:/PQ/SOURCE/video Gopro"
S = json.load(open(os.path.join(os.environ["APPDATA"], "JustinEditSuite", "settings.json"), encoding="utf-8"))
PY = S.get("python") or "python"
env = dict(os.environ, JES_NO_STYLE="1", PYTHONIOENCODING="utf-8")

def run(tag, cfg):
    cf = os.path.join(OUT, tag + "_cfg.json"); json.dump(cfg, open(cf, "w"))
    plan = os.path.join(OUT, "plan_" + tag + ".json")
    r = subprocess.run([PY, "-m", "nxcore.montage", "--src", SRC, "--preset", "beat_cut", "--plan", plan, "--name", "AB_" + tag, "--out", OUT,
                        "--seq", "1920x1080@29.97:fill", "--music", "G:/PQ/INTRO 1.mp3", "--cfg", cf],
                       cwd=r"G:\JustinEditSuite\engine", env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode: print(r.stdout[-1500:], r.stderr[-1500:]); sys.exit(1)
    return json.load(open(plan, encoding="utf-8")), r.stdout

shake = json.load(open(os.path.join(SRC, "_nexys_work", "shake.json"), encoding="utf-8"))
def clean_act(f):                    # the same handling rule as Material.moment, for an honest metric
    sh = shake.get(f) or {}; a = np.asarray(sh.get("act") or [], float); hz = float(sh.get("act_hz") or 4)
    if len(a) < 4: return a, hz
    t = np.arange(len(a)) / hz; dur = len(a) / hz
    a = np.where(((t < 2.5) | (t > dur - 1.5)) & (a > 100), np.minimum(a, 10), a)
    return np.minimum(a, 160), hz
def stats(P):
    caps, gaps = [], 0
    for s in P["segments"]:
        if s.get("gap") or not s.get("file"): gaps += 1; continue
        a, hz = clean_act(s["file"])
        if len(a) < 4: continue
        i0, i1 = float(s["in"]), float(s["out"]); L = max(1, int(round((i1 - i0) * hz)))
        u = [r[:2] for r in (shake.get(s["file"]) or {}).get("usable") or []] or [[0, len(a) / hz]]
        best = max([a[k:k + L].mean() for ua, ub in u for k in range(int(ua * hz), max(int(ua * hz) + 1, int(ub * hz) - L + 1)) if k + L <= len(a)] or [0])
        if best >= 15:                # clips with a real action only (raw act ≥ 3 % moving): calm scenery has no "moment"
            caps.append(a[int(i0 * hz):int(i0 * hz) + L].mean() / best)
    caps = np.array(caps)
    return gaps, len(caps), float(np.median(caps)), float(np.mean(caps < 0.5))

old, _ = run("old", {"speech_moments": 0, "energy_pace": True, "w_moment": 0.0, "w_expo": 0.0, "pick_more": False})
mid, _ = run("mid", {"speech_moments": 0, "energy_pace": True, "w_moment": 0.0, "w_expo": 0.0})
new, log = run("new", {"speech_moments": 0, "energy_pace": True})
for tag, P in (("cũ (code cũ)", old), ("chỉ sửa ô trống", mid), ("mới (khoảnh khắc + ô trống)", new)):
    g, n, med, low = stats(P)
    print(f"{tag}: {len(P['segments'])} shot · THIẾU HÌNH {g} · shot có hành động {n}: giữ trung vị {med:.0%} hành động, {low:.0%} giữ < 50%")
same = sum(1 for a, b in zip(old["segments"], new["segments"]) if a.get("file") == b.get("file") and abs(float(a.get("in", 0)) - float(b.get("in", 0))) < 0.3)
print(f"shot giống hệt: {same}/{len(new['segments'])}")
print("ghi chú bản mới:", [l for l in log.splitlines() if "slow-mo" in l or "THIẾU" in l or "trống" in l][:6])
