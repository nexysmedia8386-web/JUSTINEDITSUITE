"""Reverse prompt — đo một video tham khảo để AI viết lại ngôn ngữ dựng + prompt Seedance — Justin EDIT Suite.

Engine chỉ ĐO (0 token): cắt shot, khung hình, chuyển động máy, màu, nhịp cắt, BPM / cắt theo beat, thoại.
AI (panel, tab "Phân tích video") đọc reverse.json + xem video (Gemini) hoặc contact sheet (Ollama) rồi viết.

  python -m nxcore.reverse VIDEO --out DIR [--thresh 0.32] [--no-tx] [--cuda] [--force]

DIR/
  reverse.json      số đo đầy đủ (shots, edit, audio, transcript, color)
  frames/s003_f.jpg  khung đầu shot 3 · s003_m.jpg khung giữa (480 px)
  sheet_01.jpg …    contact sheet 4×4 khung giữa, có số shot + giờ (cho model local)
  proxy.mp4         480p có tiếng cho Gemini Files API
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys

import numpy as np

from . import jsonio
from .gpu import hwaccel_args

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

SAMPLE_FPS = 6.0          # khung phân tích / giây
AW = 480                  # bề ngang khung phân tích
MIN_SHOT = 0.35           # shot ngắn hơn (s) gộp vào shot trước (flash / lỗi detect)
VERSION = 5


def log(*a):
    print(*a, flush=True)


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, **kw)


def probe(path):
    p = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path])
    j = json.loads(p.stdout.decode("utf-8", "replace") or "{}")
    v = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in j.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not v:
        raise RuntimeError("không thấy luồng video trong file")
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    rot = 0
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    rot = rot or int(float((v.get("tags") or {}).get("rotate", 0) or 0))
    if abs(rot) % 180 == 90:
        w, h = h, w
    fr = v.get("avg_frame_rate") or v.get("r_frame_rate") or "25/1"
    try:
        n, d = fr.split("/")
        fps = float(n) / float(d) if float(d) else 25.0
    except Exception:
        fps = 25.0
    dur = float((j.get("format") or {}).get("duration") or v.get("duration") or 0)
    return {"w": w, "h": h, "fps": round(fps, 3), "dur": round(dur, 3), "codec": v.get("codec_name"),
            "has_audio": a is not None, "audio_codec": (a or {}).get("codec_name"), "bytes": int((j.get("format") or {}).get("size") or 0)}


def ratio_name(w, h):
    r = w / float(h or 1)
    for name, v in (("9:16", 9 / 16), ("4:5", 0.8), ("1:1", 1.0), ("4:3", 4 / 3), ("16:9", 16 / 9), ("1.85:1", 1.85), ("2.39:1", 2.39)):
        if abs(r - v) / v < 0.04:
            return name
    return "%.2f:1" % r


# ---------------------------------------------------------------- shot detection (ffmpeg scene score, full frame rate)
def detect_cuts(path, thresh, cuda):
    vf = "scale=240:-2,select='gt(scene\\,%s)',metadata=print:file=-" % thresh
    for gpu in ((True, False) if cuda else (False,)):
        p = _run(["ffmpeg", "-hide_banner", "-nostats", "-nostdin"] + hwaccel_args(gpu) + ["-i", path, "-an", "-sn", "-vf", vf, "-f", "null", "-"])
        if p.returncode == 0:
            out = p.stdout.decode("utf-8", "replace")
            ts, sc = [], []
            for m in re.finditer(r"pts_time:([\d.]+)[^\n]*\n[^\n]*scene_score=([\d.]+)", out):
                ts.append(float(m.group(1))); sc.append(float(m.group(2)))
            return ts, sc
    raise RuntimeError("ffmpeg detect cắt lỗi: " + p.stderr.decode("utf-8", "replace")[-300:])


def make_shots(cuts, scores, dur):
    pts = [0.0] + [t for t in cuts if 0.05 < t < dur - 0.05] + [dur]
    sc = [1.0] + scores
    shots = []
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        if shots and b - a < MIN_SHOT:          # flash frame → gộp vào shot trước
            shots[-1]["t1"] = b
            continue
        shots.append({"t0": a, "t1": b, "cut_score": round(sc[i] if i < len(sc) else 1.0, 3)})
    if len(shots) > 1 and shots[0]["t1"] - shots[0]["t0"] < MIN_SHOT:
        shots[1]["t0"] = 0.0; shots.pop(0)
    for i, s in enumerate(shots):
        s["i"] = i + 1
        s["t0"], s["t1"] = round(s["t0"], 3), round(s["t1"], 3)
        s["dur"] = round(s["t1"] - s["t0"], 3)
    return shots


# ---------------------------------------------------------------- per-frame stream (6 fps, 480 px)
def frame_stream(path, cuda):
    vf = "fps=%g,scale=%d:-2" % (SAMPLE_FPS, AW)
    p0 = _run(["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-frames:v", "1", "-vf", vf, "-f", "image2pipe", "-vcodec", "png", "-"])
    import cv2
    im = cv2.imdecode(np.frombuffer(p0.stdout, np.uint8), cv2.IMREAD_COLOR) if p0.stdout else None
    if im is None:
        raise RuntimeError("không đọc được khung hình đầu: " + p0.stderr.decode("utf-8", "replace")[-200:])
    h, w = im.shape[:2]
    cmd = ["ffmpeg", "-v", "error", "-nostdin"] + hwaccel_args(cuda) + ["-i", path, "-an", "-vf", vf, "-pix_fmt", "bgr24", "-f", "rawvideo", "-"]
    pr = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=w * h * 3 * 4)
    n, k = w * h * 3, 0
    while True:
        buf = pr.stdout.read(n)
        if not buf or len(buf) < n:
            break
        yield k / SAMPLE_FPS, np.frombuffer(buf, np.uint8).reshape(h, w, 3)
        k += 1
    pr.wait()
    if k == 0 and cuda:                       # CUDA không decode được (4:2:2 10-bit…) → CPU
        for x in frame_stream(path, False):
            yield x


class Motion:
    """global affine giữa 2 khung liên tiếp (ORB + RANSAC; phase correlation khi ít feature)."""

    def __init__(self):
        import cv2
        self.cv2 = cv2
        self.orb = cv2.ORB_create(600)
        self.bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)

    def step(self, g0, g1):
        cv2 = self.cv2
        h, w = g0.shape
        k0, d0 = self.orb.detectAndCompute(g0, None)
        k1, d1 = self.orb.detectAndCompute(g1, None)
        M, conf = None, 0.0
        if d0 is not None and d1 is not None and len(k0) > 25 and len(k1) > 25:
            m = self.bf.match(d0, d1)
            if len(m) > 20:
                a = np.float32([k0[x.queryIdx].pt for x in m]); b = np.float32([k1[x.trainIdx].pt for x in m])
                M, inl = cv2.estimateAffinePartial2D(a, b, method=cv2.RANSAC, ransacReprojThreshold=2.5)
                if M is not None and (inl is None or inl.sum() < 12):
                    M = None
                elif M is not None:
                    conf = float(inl.sum()) / len(m)     # tỉ lệ điểm theo cùng 1 chuyển động máy (cảnh cứng → cao)
        if M is None:
            (dx, dy), resp = cv2.phaseCorrelate(np.float32(g0), np.float32(g1))
            M = np.float32([[1, 0, dx], [0, 1, dy]])
            conf = min(0.5, float(resp))
        s = math.hypot(M[0, 0], M[1, 0]); rot = math.degrees(math.atan2(M[1, 0], M[0, 0]))
        # dịch chuyển của TÂM khung (bỏ phần do zoom quanh gốc toạ độ)
        cx, cy = w / 2.0, h / 2.0
        tx = (M[0, 0] * cx + M[0, 1] * cy + M[0, 2]) - cx
        ty = (M[1, 0] * cx + M[1, 1] * cy + M[1, 2]) - cy
        warped = cv2.warpAffine(g0, M, (w, h))
        res = float(np.mean(cv2.absdiff(warped[8:-8, 8:-8], g1[8:-8, 8:-8]))) / 255.0
        return tx / w, ty / w, math.log(max(s, 1e-3)), rot, res, conf


def cam_label(st):
    """số đo chuyển động/giây của một shot → nhãn ngôn ngữ quay phim (VN, thuật ngữ EN)."""
    pan, tilt, zoom, roll, jit, res = st["pan"], st["tilt"], st["zoom"], st["roll"], st["jitter"], st["subject"]
    subj = "chủ thể chuyển động mạnh" if res > 0.075 else "chủ thể chuyển động vừa" if res > 0.035 else "ít chuyển động trong khung"
    if st.get("conf", 1) < 0.35:
        return "không đo chắc — CGI / vật thể chuyển động chiếm khung (xem video)", subj
    lab = []
    if abs(zoom) > 0.035:
        lab.append("push-in / zoom in" if zoom > 0 else "pull-out / zoom out")
    if abs(pan) > 0.025:
        lab.append(("pan/truck sang phải" if pan < 0 else "pan/truck sang trái") + (" nhanh" if abs(pan) > 0.15 else ""))
    if abs(tilt) > 0.025:
        lab.append(("tilt/pedestal lên" if tilt > 0 else "tilt/pedestal xuống") + (" nhanh" if abs(tilt) > 0.15 else ""))
    if abs(roll) > 3.0:
        lab.append("roll / xoay khung")
    if jit > 0.015:
        lab.append("handheld rung mạnh" if jit > 0.04 else "handheld")
    if not lab:
        lab.append("locked-off / máy tĩnh" if jit < 0.004 else "gần tĩnh (gimbal/tay rất nhẹ)")
    return " + ".join(lab), subj


def color_stats(img):
    import cv2
    small = cv2.resize(img, (96, int(96 * img.shape[0] / img.shape[1]) or 54), interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(np.float32)
    L = lab[:, 0] * 100 / 255.0
    a, b = lab[:, 1] - 128, lab[:, 2] - 128
    px = small.reshape(-1, 3).astype(np.float32)
    k = 4
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _, lbl, cen = cv2.kmeans(px, k, None, crit, 2, cv2.KMEANS_PP_CENTERS)
    cnt = np.bincount(lbl.ravel(), minlength=k)
    order = np.argsort(-cnt)
    pal = [["#%02x%02x%02x" % (int(cen[i][2]), int(cen[i][1]), int(cen[i][0])), round(float(cnt[i]) / len(lbl), 2)] for i in order]
    Lm, Ls = float(L.mean()), float(L.std())
    key = "low-key" if Lm < 32 else "high-key" if Lm > 68 else "mid-key"
    warm = float(b.mean())
    tone = "ấm (vàng/cam)" if warm > 8 else "lạnh (xanh)" if warm < -4 else "trung tính"
    tint = float(a.mean())
    if tint > 6:
        tone += ", ngả magenta"
    elif tint < -6:
        tone += ", ngả xanh lá/teal"
    sat = float(hsv[:, 1].mean()) / 255.0
    return {"L": round(Lm, 1), "contrast": round(Ls, 1), "sat": round(sat, 2), "warm": round(warm, 1), "tint": round(tint, 1),
            "key": key, "tone": tone, "palette": pal}


def analyze_frames(path, shots, fdir, cuda):
    import cv2
    os.makedirs(fdir, exist_ok=True)
    mot = Motion()
    acc = {s["i"]: {"st": [], "first": None, "mid": None} for s in shots}
    mids = {s["i"]: (s["t0"] + s["t1"]) / 2.0 for s in shots}
    bounds = [s["t0"] for s in shots]
    import bisect
    prev_g, prev_i, n = None, None, 0
    best_mid = {}
    for t, img in frame_stream(path, cuda):
        n += 1
        i = shots[max(0, bisect.bisect_right(bounds, t) - 1)]["i"]
        g = cv2.cvtColor(cv2.resize(img, (240, int(240 * img.shape[0] / img.shape[1]))), cv2.COLOR_BGR2GRAY)
        a = acc[i]
        if a["first"] is None and t >= shots[i - 1]["t0"] + 0.08:
            fp = os.path.join(fdir, "s%03d_f.jpg" % i); cv2.imwrite(fp, img, [cv2.IMWRITE_JPEG_QUALITY, 85]); a["first"] = fp
        d = abs(t - mids[i])
        if i not in best_mid or d < best_mid[i][0]:
            best_mid[i] = (d, img.copy())
        if prev_g is not None and prev_i == i:
            try:
                a["st"].append(mot.step(prev_g, g))
            except Exception:
                pass
        prev_g, prev_i = g, i
        if n % 120 == 0:
            log("  khung %d · %.0fs" % (n, t))
    for s in shots:
        i = s["i"]; a = acc[i]
        if i in best_mid:
            mp = os.path.join(fdir, "s%03d_m.jpg" % i)
            cv2.imwrite(mp, best_mid[i][1], [cv2.IMWRITE_JPEG_QUALITY, 88])
            s["mid"] = mp.replace("\\", "/")
            s["color"] = color_stats(best_mid[i][1])
            if not a["first"]:
                a["first"] = mp
        s["first"] = (a["first"] or "").replace("\\", "/")
        st = np.array(a["st"]) if a["st"] else np.zeros((0, 6))
        conf = float(np.median(st[:, 5])) if len(st) else 0.0
        good = st[st[:, 5] >= 0.35] if len(st) else st          # chỉ bước đo chắc mới tính chuyển động máy
        if len(good) >= 2:
            st = good
        if len(st):
            r = SAMPLE_FPS
            tx, ty = st[:, 0], st[:, 1]
            m = {"pan": float(tx.mean() * r), "tilt": float(ty.mean() * r), "zoom": float(st[:, 2].mean() * r), "roll": float(st[:, 3].mean() * r),
                 # rung = thành phần tần số cao (sai phân bậc 1), không tính pan tăng/giảm tốc đều
                 "jitter": float(np.sqrt((np.var(np.diff(tx)) + np.var(np.diff(ty))) / 2.0)) if len(st) > 2 else 0.0,
                 "subject": float(np.median(st[:, 4])), "conf": conf}
        else:
            m = {"pan": 0.0, "tilt": 0.0, "zoom": 0.0, "roll": 0.0, "jitter": 0.0, "subject": 0.0, "conf": 0.0}
        lab, subj = cam_label(m)
        s["cam"] = dict({k: round(v, 4) for k, v in m.items()}, label=lab, subject_motion=subj)
    return n


def contact_sheets(shots, out, per=16, cols=4):
    import cv2
    paths = []
    tw = 320
    for si in range(0, len(shots), per):
        grp = shots[si:si + per]
        tiles = []
        for s in grp:
            im = cv2.imread(s.get("mid") or "") if s.get("mid") else None
            if im is None:
                continue
            th = int(tw * im.shape[0] / im.shape[1])
            im = cv2.resize(im, (tw, th))
            cv2.rectangle(im, (0, 0), (tw, 22), (0, 0, 0), -1)
            cv2.putText(im, "#%d  %.1f-%.1fs" % (s["i"], s["t0"], s["t1"]), (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
            tiles.append(im)
        if not tiles:
            continue
        th = max(t.shape[0] for t in tiles)
        tiles = [cv2.copyMakeBorder(t, 0, th - t.shape[0], 0, 0, cv2.BORDER_CONSTANT) for t in tiles]
        cols_ = min(cols, len(tiles))
        while len(tiles) % cols_:
            tiles.append(np.zeros_like(tiles[0]))
        rows = [np.hstack(tiles[r:r + cols_]) for r in range(0, len(tiles), cols_)]
        p = os.path.join(out, "sheet_%02d.jpg" % (si // per + 1))
        cv2.imwrite(p, np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 82])
        paths.append(p.replace("\\", "/"))
    return paths


def make_proxy(path, out, info, cuda):
    dst = os.path.join(out, "proxy.mp4")
    vf = "scale='if(gt(iw,ih),-2,480)':'if(gt(iw,ih),480,-2)':flags=bilinear,fps=%g,format=yuv420p" % min(30.0, info["fps"] or 25)
    base = ["-i", path, "-map", "0:v:0", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-g", "30"]
    base += (["-map", "0:a:0?", "-c:a", "aac", "-ac", "2", "-b:a", "96k"] if info["has_audio"] else ["-an"])
    for gpu in ((True, False) if cuda else (False,)):
        p = _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-nostdin"] + hwaccel_args(gpu) + base + ["-movflags", "+faststart", dst])
        if p.returncode == 0:
            return dst.replace("\\", "/")
    log("  ⚠ không tạo được proxy: " + p.stderr.decode("utf-8", "replace")[-200:])
    return ""


def _band_db(x, sr, hop, n, nfft=1024):
    """năng lượng dải (dB) mỗi khung 20 ms: sub (< 100 Hz: boom / hit trầm) · air (> 5 kHz: whoosh / pop / click / glitch)"""
    f = np.fft.rfftfreq(nfft, 1.0 / sr)
    lo, hi = f < 100, f > 5000
    win = np.hanning(nfft).astype(np.float32)
    xp = np.pad(x.astype(np.float32), (nfft // 2, nfft // 2))
    sub, air = np.empty(n, np.float32), np.empty(n, np.float32)
    B = 2048
    for s0 in range(0, n, B):
        idx = np.arange(s0, min(n, s0 + B))
        fr_ = np.stack([xp[i * hop:i * hop + nfft] for i in idx]) * win
        P = np.abs(np.fft.rfft(fr_, axis=1)) ** 2
        sub[idx] = 10 * np.log10(P[:, lo].sum(1) + 1e-10)
        air[idx] = 10 * np.log10(P[:, hi].sum(1) + 1e-10)
    return sub, air


def _rolling_med(v, k=100):
    pad = np.pad(v, (k // 2, k // 2), mode="edge")
    try:
        from numpy.lib.stride_tricks import sliding_window_view
        return np.median(sliding_window_view(pad, k + 1), axis=1)[:len(v)]
    except Exception:
        return np.array([np.median(pad[i:i + k + 1]) for i in range(len(v))])


def sfx_under_voice(x, sr, hop, n, sp, cuts, rms=None, base_rms=None):
    """SFX chạy DƯỚI giọng đọc (YouTube / explainer): giọng ít năng lượng < 100 Hz và > 5 kHz (trừ âm gió s/x ngắn) —
    nên boom / hit trầm nổi ở dải sub, whoosh / pop / click / glitch nổi ở dải air. Chỉ xét khung đang có thoại
    (khung không thoại đã do bộ dò chính lo). Ngưỡng cao để không bắt nhầm âm gió."""
    if not sp.any():
        return []
    sub, air = _band_db(x, sr, hop, n)
    bs, ba = _rolling_med(sub), _rolling_med(air)
    out = []
    loud = (rms > base_rms + 4.0) if rms is not None else np.ones(n, bool)     # SFX thật làm cả mix to lên, âm gió / plosive thì không
    for band, base, rise, kind_s, min_d, max_d in ((sub, bs, 15.0, "hit", 0.1, 2.5), (air, ba, 13.0, "air", 0.22, 3.0)):
        hot = sp & (band > base + rise) & loud
        i = 0
        while i < n:
            if not hot[i]:
                i += 1; continue
            j = i
            while j < n and band[j] > base[j] + rise - 6:
                j += 1
            dur = (j - i) / 50.0
            if min_d <= dur <= max_d:
                pk = int(np.argmax(band[i:j])) / max(1, j - i)
                if kind_s == "hit":
                    kind = "hit"
                else:
                    kind = "riser" if dur > 1.2 and pk > 0.6 else "whoosh" if dur >= 0.3 else "pop"
                t = i / 50.0
                out.append({"t": round(t, 2), "dur": round(dur, 2), "prom": round(float(band[i:j].max() - base[i:j].mean()), 1), "kind": kind,
                            "centroid": 0, "on_cut": any(abs(t - c) <= 0.35 for c in cuts) if cuts else False, "under_voice": True})
            i = max(j, i + 1)
    return out


def sfx_events(path, words, cuts=None, max_sec=1800):
    """SFX nghe được (ngoài lời thoại): đoạn nổi rõ hơn nền ≥ 8 dB trong khoảng không có chữ nào của Whisper.
    → [{t, dur, prom (độ nổi dB), kind: hit|pop|whoosh|riser, centroid, on_cut, under_voice?}] (giây). Nhạc nền đều không lọt vì cần độ nổi;
    SFX nằm dưới giọng đọc bắt riêng theo dải sub / air (sfx_under_voice)."""
    from .audiolib import decode
    sr, hop = 22050, 441                                   # 20 ms
    x = decode(path, sr=sr, max_sec=max_sec)
    n = len(x) // hop
    if n < 50:
        return []
    rms = 20 * np.log10(np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(1)) + 1e-9)
    sp = np.zeros(n, bool)
    for w in words or []:
        sp[max(0, int((w[0] - 0.06) * 50)):int((w[1] + 0.06) * 50) + 1] = True
    k = 100                                                # nền cục bộ 2 s (trung vị)
    pad = np.pad(rms, (k // 2, k // 2), mode="edge")
    try:
        from numpy.lib.stride_tricks import sliding_window_view
        base = np.median(sliding_window_view(pad, k + 1), axis=1)[:n]
    except Exception:
        base = np.array([np.median(pad[i:i + k + 1]) for i in range(n)])
    voice = float(np.median(rms[sp])) if sp.any() else float(np.percentile(rms, 75))
    hot = (~sp) & (rms > base + 8) & (rms > max(-36.0, voice - 14))
    out, i = [], 0
    cuts = sorted(cuts or [])
    while i < n:
        if not hot[i]:
            i += 1; continue
        j = i
        while j < n and not sp[j] and rms[j] > base[j] + 3:
            j += 1
        a0, a1 = max(0, i - 3), min(n, j + 2)              # lấy cả phần đuôi để ước độ dài
        dur = (j - i) / 50.0
        if dur >= 0.04:
            seg = x[i * hop:max(j, i + 3) * hop]
            spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
            f = np.fft.rfftfreq(len(seg), 1.0 / sr)
            cen = float((f * spec).sum() / (spec.sum() + 1e-9))
            pk = int(np.argmax(rms[i:j])) / max(1, j - i)
            kind = "riser" if dur > 1.2 and pk > 0.6 else "whoosh" if dur > 0.25 else ("hit" if cen < 1800 else "pop")
            t = i / 50.0
            oc = any(abs(t - c) <= 0.35 for c in cuts) if cuts else False
            out.append({"t": round(t, 2), "dur": round(dur, 2), "prom": round(float((rms[i:max(j, i + 1)] - base[i:max(j, i + 1)]).max()) + 6.0, 1),
                        "kind": kind, "centroid": int(cen), "on_cut": oc})
        i = max(j, i + 1)
    out += sfx_under_voice(x, sr, hop, n, sp, cuts, rms, base)
    out.sort(key=lambda e: e["t"])
    # gộp sự kiện sát nhau (< 0.15 s), giới hạn ~30 / phút giữ cái nổi nhất
    merged = []
    for e in out:
        if merged and e["t"] - (merged[-1]["t"] + merged[-1]["dur"]) < 0.15:
            m = merged[-1]; m["dur"] = round(e["t"] + e["dur"] - m["t"], 2); m["prom"] = max(m["prom"], e["prom"])
            if m["dur"] > 0.25 and m["kind"] in ("hit", "pop"): m["kind"] = "whoosh"
            m["on_cut"] = m["on_cut"] or e["on_cut"]
        else:
            merged.append(dict(e))
    cap = int(30 * (n / 50.0) / 60.0) + 5
    if len(merged) > cap:
        keep = set(id(e) for e in sorted(merged, key=lambda e: -e["prom"])[:cap])
        merged = [e for e in merged if id(e) in keep]
    return merged


def audio_info(path, cuts, info, tx, W, cuda):
    out = {}
    if not info["has_audio"]:
        return out
    try:
        from . import music
        A = music.analyze(path, max_sec=900)
        beats = A.get("beats") or []
        on = 0
        if beats and cuts:
            import bisect
            for c in cuts:
                k = bisect.bisect_left(beats, c)
                d = min([abs(c - beats[j]) for j in (k - 1, k) if 0 <= j < len(beats)] or [9])
                on += d <= 0.09
        out.update({"bpm": A.get("bpm"), "mood": A.get("mood"), "beats_n": len(beats),
                    "cut_on_beat": round(on / float(len(cuts)), 2) if cuts else None,
                    "sections": [{"t0": s["start"], "t1": s["end"], "label": s.get("label"), "energy_db": s.get("energy_db")} for s in A.get("sections", [])],
                    "energy_curve_1s": A.get("energy_curve_1s", [])[:600]})
    except Exception as e:
        out["music_error"] = str(e)[:160]
    if tx:
        try:
            from . import montage
            words, _ = montage.transcribe_vo(path, W, cuda=cuda, lang=None)
            # nhạc không lời → Whisper hay "nghe" ra 1-2 từ (You / Thank you) — dưới 4 từ hoặc độ tin thấp = không có thoại
            if len(words) < 4 or float(np.mean([w[3] for w in words])) < 0.45:
                words = []
            lines, cur = [], None
            for s, e, w, _p in words:
                if cur and (s - cur[1] > 0.7 or len(cur[2]) > 90 or re.search(r"[.?!…]$", cur[2])):
                    lines.append(cur); cur = None
                cur = [cur[0], e, cur[2] + " " + w] if cur else [s, e, w]
            if cur:
                lines.append(cur)
            out["speech"] = [[round(a, 2), round(b, 2), t.strip()] for a, b, t in lines]
            out["words"] = len(words)
            out["_words"] = words
        except Exception as e:
            out["speech_error"] = str(e)[:160]
    try:
        out["sfx_events"] = sfx_events(path, out.pop("_words", None) or [], cuts)
    except Exception as e:
        out.pop("_words", None)
        out["sfx_error"] = str(e)[:160]
    return out


def edit_stats(shots, dur):
    L = np.array([s["dur"] for s in shots]) if shots else np.zeros(1)
    asl = float(L.mean())
    pacing = "rất nhanh (montage/teaser)" if asl < 1.2 else "nhanh" if asl < 2.5 else "vừa" if asl < 5 else "chậm (long take)"
    # nhịp theo từng đoạn 10% thời lượng: số cắt / phút
    curve = []
    for k in range(10):
        a, b = dur * k / 10, dur * (k + 1) / 10
        c = sum(1 for s in shots[1:] if a <= s["t0"] < b)
        curve.append(round(c / max((b - a) / 60.0, 1e-6), 1))
    longest = sorted(shots, key=lambda s: -s["dur"])[:3]
    return {"shots": len(shots), "asl": round(asl, 2), "median": round(float(np.median(L)), 2), "min": round(float(L.min()), 2),
            "max": round(float(L.max()), 2), "cuts_per_min": round((len(shots) - 1) / max(dur / 60.0, 1e-6), 1),
            "pacing": pacing, "cpm_curve_10": curve, "longest": [[s["i"], s["dur"]] for s in longest]}


def file_key(p):
    st = os.stat(p)
    return "%d:%d" % (st.st_size, int(st.st_mtime))


def run(video, out, thresh=0.32, tx=True, cuda=True, force=False, proxy=True):
    video = os.path.abspath(video)
    os.makedirs(out, exist_ok=True)
    rp = os.path.join(out, "reverse.json")
    key = file_key(video)
    try:
        old = json.load(open(rp, encoding="utf-8"))
        if not force and (not proxy or (old.get("proxy") and os.path.exists(old["proxy"]))) and old.get("key") == key and old.get("thresh") == thresh and old.get("v") == VERSION and (old.get("audio", {}).get("speech") is not None or not tx or not old["info"]["has_audio"]):
            log("✔ Đã phân tích trước đó (dùng lại): %d shot" % len(old["shots"]))
            return old
    except Exception:
        pass
    info = probe(video)
    log("▶ %s · %dx%d %s · %.2f fps · %.1fs%s" % (os.path.basename(video), info["w"], info["h"], ratio_name(info["w"], info["h"]), info["fps"], info["dur"], " · có tiếng" if info["has_audio"] else " · không tiếng"))
    log("▶ Tìm điểm cắt (scene ≥ %s)…" % thresh)
    cuts, scores = detect_cuts(video, thresh, cuda)
    shots = make_shots(cuts, scores, info["dur"])
    log("  %d shot" % len(shots))
    log("▶ Đo chuyển động máy + màu từng shot…")
    analyze_frames(video, shots, os.path.join(out, "frames"), cuda)
    sheets = contact_sheets(shots, out)
    pxy = ""
    if proxy:
        log("▶ Proxy 480p cho Gemini…")
        pxy = make_proxy(video, out, info, cuda)
    W = os.path.join(out, "_work")
    os.makedirs(W, exist_ok=True)
    if info["has_audio"]:
        log("▶ Âm thanh: BPM / beat" + (" + transcribe thoại" if tx else "") + "…")
    au = audio_info(video, [s["t0"] for s in shots[1:]], info, tx, W, cuda)
    ed = edit_stats(shots, info["dur"])
    cols = [s["color"] for s in shots if s.get("color")]
    look = {}
    if cols:
        wts = np.array([s["dur"] for s in shots if s.get("color")])
        avg = lambda k: round(float(np.average([c[k] for c in cols], weights=wts)), 1)
        look = {"L": avg("L"), "contrast": avg("contrast"), "sat": round(float(np.average([c["sat"] for c in cols], weights=wts)), 2),
                "warm": avg("warm"), "tint": avg("tint")}
    R = {"v": VERSION, "key": key, "thresh": thresh, "video": video.replace("\\", "/"), "name": os.path.basename(video),
         "info": dict(info, ratio=ratio_name(info["w"], info["h"])), "edit": ed, "audio": au, "look": look,
         "shots": shots, "sheets": sheets, "proxy": pxy, "frames_dir": os.path.join(out, "frames").replace("\\", "/")}
    jsonio.save_atomic(rp, R)
    log("✔ Phân tích video xong: %d shot · ASL %.2fs · %.1f cắt/phút · %s%s%s" % (
        ed["shots"], ed["asl"], ed["cuts_per_min"], ed["pacing"], (" · %d SFX nghe được" % len(au.get("sfx_events") or [])) if au.get("sfx_events") is not None else "",
        (" · %s BPM, %d%% cắt trúng beat" % (au.get("bpm"), round(100 * (au.get("cut_on_beat") or 0)))) if au.get("bpm") else ""))
    log("reverse: " + rp.replace("\\", "/"))
    return R


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nxcore.reverse", description="Đo video tham khảo cho reverse prompt")
    ap.add_argument("video")
    ap.add_argument("--out", required=True)
    ap.add_argument("--thresh", type=float, default=0.32)
    ap.add_argument("--no-tx", action="store_true", help="không transcribe thoại")
    ap.add_argument("--no-proxy", action="store_true")
    ap.add_argument("--cuda", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    if not os.path.isfile(a.video):
        log("✖ không thấy file: " + a.video); return 2
    run(a.video, a.out, a.thresh, not a.no_tx, a.cuda, a.force, not a.no_proxy)
    return 0


if __name__ == "__main__":
    sys.exit(main())
