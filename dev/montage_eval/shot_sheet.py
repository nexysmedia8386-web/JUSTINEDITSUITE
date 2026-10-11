"""Contact sheet per used shot: the whole clip at 2 fps, the window the montage took outlined, the activity curve under it.
Shows whether the chosen window holds the moment worth taking."""
import json, os, subprocess, sys, tempfile
import numpy as np
from PIL import Image, ImageDraw, ImageFont

SRC = r"G:/PQ/SOURCE/video Gopro"
PLAN = r"G:\JustinEditSuite\engine\NX_video_Gopro\plan_VIDEO_GOPRO_SCRIPT_v17.json"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sheets")
os.makedirs(OUT, exist_ok=True)
FF = json.load(open(os.path.join(os.environ["APPDATA"], "JustinEditSuite", "settings.json"), encoding="utf-8")).get("ffmpeg") or "ffmpeg"
shake = json.load(open(os.path.join(SRC, "_nexys_work", "shake.json"), encoding="utf-8"))
plan = json.load(open(PLAN, encoding="utf-8"))
try: FONT = ImageFont.truetype("arial.ttf", 13)
except Exception: FONT = ImageFont.load_default()

def sheet(i, s):
    f = s["file"]; a, b = float(s["in"]), float(s["out"])
    path = os.path.join(SRC, f); sh = shake.get(f) or {}
    act = np.asarray(sh.get("act") or [], float); hz = float(sh.get("act_hz") or 4)
    tmp = tempfile.mkdtemp()
    subprocess.run([FF, "-v", "error", "-i", path, "-vf", "fps=2,scale=192:-2", os.path.join(tmp, "%03d.jpg")], check=True)
    fr = sorted(os.listdir(tmp)); n = len(fr)
    cols = 8; rows = (n + cols - 1) // cols; tw, th = 192, 108
    W, H = cols * tw, rows * (th + 16) + 120
    im = Image.new("RGB", (W, H), (24, 24, 28)); d = ImageDraw.Draw(im)
    for k, name in enumerate(fr):
        t = (k + 0.5) / 2.0
        x, y = (k % cols) * tw, (k // cols) * (th + 16)
        im.paste(Image.open(os.path.join(tmp, name)).resize((tw, th)), (x, y + 16))
        on = a - 0.25 <= t <= b + 0.25
        d.text((x + 3, y + 1), f"{t:4.1f}s", fill=(255, 200, 60) if on else (160, 160, 160), font=FONT)
        if on: d.rectangle([x, y + 16, x + tw - 1, y + 16 + th - 1], outline=(255, 200, 60), width=4)
    # activity curve over the clip, chosen window shaded
    y0 = rows * (th + 16) + 10; ch = 90; dur = max(len(act) / hz, 0.1)
    d.rectangle([int(a / dur * W), y0, int(b / dur * W), y0 + ch], fill=(70, 60, 20))
    if len(act):
        mx = max(act.max(), 1)
        pts = [(int(k / hz / dur * W), int(y0 + ch - act[k] / mx * ch)) for k in range(len(act))]
        d.line(pts, fill=(90, 200, 255), width=2)
    d.text((6, y0 + 2), f"#{i} {f}  lấy {a:.2f}–{b:.2f}s  ({s.get('beat')})  act max {act.max() if len(act) else 0:.0f} @ {np.argmax(act) / hz if len(act) else 0:.1f}s", fill=(230, 230, 230), font=FONT)
    p = os.path.join(OUT, f"shot{i:02d}.jpg"); im.save(p, quality=85)
    for x in fr: os.remove(os.path.join(tmp, x))
    return p

ids = [int(x) for x in sys.argv[1:]] or [0, 11, 14, 16, 19, 24]
for i in ids:
    print(sheet(i, plan["segments"][i]))
