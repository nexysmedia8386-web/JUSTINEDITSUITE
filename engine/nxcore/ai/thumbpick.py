"""B5 — thumbnail candidates for the EDITED video.

YouTube itself offers frames picked by a quality model; creators who upload their own pick a frame that is sharp (no
motion blur), shows a clear face with an expression when there is one, has strong colour and reads at phone size.
Here: 1 frame / s over the edit (≤ MAX_SAMPLES), each scored on
  sharp   variance of the Laplacian (log, ranked in this video)      — motion blur is the #1 bad-thumbnail cause
  face    SigLIP2 zero-shot "close-up of a face with a clear expression" vs "scenery, no people" (OpenCV 5 has no
          Haar cascades any more; this needs no extra model download)
  aes     SigLIP2 zero-shot "beautiful cinematic shot" vs "boring amateur snapshot" (nxcore.ai.quality prompts)
  tech    SigLIP2 zero-shot "sharp, well exposed" vs "blurry, badly exposed"
  colour  Hasler–Süsstrunk colourfulness            expo  mean luma inside 0.2–0.8 and enough contrast
→ the best K, kept apart (different shot, ≥ 3 s, SigLIP cosine < 0.92), saved next to the plan at the sequence's
shape (1280×720, or 720×1280 for a vertical edit), cropped where the picture has the most detail.

  pick(P, k=8) → [{"t", "file", "src_t", "score", "why", "path"}]
  python -m nxcore.ai.thumbpick PLAN.json [--k 8]
"""
import os

import numpy as np

from .. import jsonio

MAX_SAMPLES = 600
W_ = {"aes": 0.30, "tech": 0.15, "sharp": 0.25, "face": 0.15, "colour": 0.10, "expo": 0.05}


def samples(P, fps=1.0):
    """[(timeline t, file, source t, rot)] — 1 / s over every picture shot of the edit (gaps / missing skipped)."""
    from ..aiedit import timeline
    tl = timeline(P)
    media = jsonio.load(os.path.join(P["src"], "_nexys_work", "media.json"), {}) or {}
    out = []
    for i, s in enumerate(P["segments"]):
        if s.get("gap") or not s.get("file"):
            continue
        a, b = tl[i]
        sp = float(s.get("speed") or 100) / 100.0
        n = max(1, int((b - a) * fps))
        for j in range(n):
            t = a + (j + 0.5) * (b - a) / n
            out.append((round(t, 2), s["file"], round(float(s["in"]) + (t - a) * sp, 3), (media.get(s["file"]) or {}).get("rot")))
    if len(out) > MAX_SAMPLES:
        out = [out[int(k)] for k in np.linspace(0, len(out) - 1, MAX_SAMPLES)]
    return out


def measures(img):
    """RGB frame (≈ 448 px wide) → raw technical measures."""
    import cv2
    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    sharp = float(np.log1p(cv2.Laplacian(g, cv2.CV_64F).var()))
    r, gg, b = (img[..., k].astype(np.float32) for k in range(3))
    rg, yb = r - gg, 0.5 * (r + gg) - b
    colour = float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)) / 100.0
    luma = g.astype(np.float32) / 255.0
    m, sd = float(luma.mean()), float(luma.std())
    expo = float(np.clip(1.0 - abs(m - 0.5) / 0.3, 0, 1) * np.clip(sd / 0.18, 0, 1))
    # OpenCV 5 dropped the Haar cascades → the face score comes from SigLIP2 in pick() (no extra model download)
    return {"sharp": sharp, "colour": colour, "expo": expo}


FACE_PROMPTS = ("a close-up photo of a person's face with a clear expression", "a photo of scenery with no people")


def target_size(P):
    """Thumbnail size from the edit's sequence: vertical sequence → 720×1280 (Shorts), otherwise 1280×720."""
    seq = P.get("seq") or {}
    try:
        return (720, 1280) if float(seq.get("h") or 0) > float(seq.get("w") or 1) else (1280, 720)
    except (TypeError, ValueError):
        return 1280, 720


def fit_crop(img, W, H):
    """Scale to cover W×H, then crop along the long axis where the picture has the most DETAIL (gradient energy, mild
    pull to the centre) — a vertical phone frame cut to 16:9 keeps the people, not the plain ceiling above them
    (a centre cut did exactly that on the showroom test)."""
    import cv2
    h, w = img.shape[:2]
    s = max(W / w, H / h)
    img = cv2.resize(img, (max(W, round(w * s)), max(H, round(h * s))), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    h, w = img.shape[:2]
    if (h, w) == (H, W):
        return img
    g = cv2.cvtColor(cv2.resize(img, (max(1, w // 4), max(1, h // 4))), cv2.COLOR_RGB2GRAY).astype(np.float32)
    e = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0)) + np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1))
    vertical = h - H > w - W
    prof = e.sum(axis=1) if vertical else e.sum(axis=0)     # detail per row (tall image) / per column (wide image)
    L = (H if vertical else W) // 4
    span = len(prof) - L
    if span <= 0:
        off = 0
    else:
        cs = np.concatenate([[0.0], np.cumsum(prof)])
        band = cs[L:L + span + 1] - cs[:span + 1]
        pos = np.arange(span + 1)
        band = band * (1.0 - 0.25 * np.abs(pos - span / 2) / max(span / 2, 1))
        off = int(np.argmax(band)) * 4
    off = max(0, min(off, (h - H) if vertical else (w - W)))      # the /4 profile can overshoot by ≤ 3 px
    return img[off:off + H, :W] if vertical else img[:H, off:off + W]


def _rank(x):
    x = np.asarray(x, float)
    return np.argsort(np.argsort(x)) / max(1, len(x) - 1)


def pick(P, k=8, out_dir=None, log=print, stop=None):
    from . import frames as _fr
    from .quality import PROMPTS
    S = samples(P)
    if not S:
        raise RuntimeError("bản dựng không có shot hình nào")
    log(f"thumbs: {len(S)} khung (1/giây)")
    by = {}
    for n, (t, f, st, rot) in enumerate(S):
        by.setdefault((f, rot), []).append((n, st))
    imgs = [None] * len(S)

    def grab(item, workers=4):
        (f, rot), its = item
        if stop and stop():
            return
        for (n, _), im in zip(its, _fr.full(os.path.join(P["src"], f), [st for _, st in its], 448, workers=workers, rot=rot)):
            imgs[n] = im
    # clips with many frames: one NVDEC decoder each, in turn; a montage's 1–2 frames per clip: ffmpeg seeks, 4 clips
    # at a time (sequential seeks were 157 s for a 158-shot montage)
    many = [it for it in by.items() if len(it[1]) > 3]
    few = [it for it in by.items() if len(it[1]) <= 3]
    for it in many:
        grab(it)
    if few:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(lambda it: grab(it, 1), few))
    ok = [n for n, im in enumerate(imgs) if im is not None]
    if not ok:
        raise RuntimeError("không đọc được khung hình nào")
    meas = [measures(imgs[n]) for n in ok]
    sc = {key: _rank([m[key] for m in meas]) for key in ("sharp", "colour")}
    sc["face"] = np.zeros(len(meas))
    sc["expo"] = np.array([m["expo"] for m in meas])
    V = None
    try:                                                   # SigLIP2 zero-shot quality (AI pack); skipped without it
        from .embed import available, embed_images, embed_texts
        if available():
            V = embed_images([imgs[n] for n in ok])
            T = embed_texts([p for pair in PROMPTS.values() for p in pair])
            Q = V @ T.T
            for j, key in enumerate(PROMPTS):
                sc[key] = _rank(Q[:, 2 * j] - Q[:, 2 * j + 1])
            Fq = V @ embed_texts(list(FACE_PROMPTS)).T
            d = Fq[:, 0] - Fq[:, 1]
            sc["face"] = np.clip((d - np.percentile(d, 50)) / max(1e-6, np.percentile(d, 95) - np.percentile(d, 50)), 0, 1)
    except Exception as e:                                 # noqa: BLE001
        log(f"thumbs: bỏ qua chấm SigLIP ({e})")
    tot = sum(W_[key] * sc[key] for key in W_ if key in sc) / sum(W_[key] for key in W_ if key in sc)
    # sharpness is a gate as well as a score: a blurred frame never makes it, however pretty
    tot = np.where(sc["sharp"] < 0.35, tot * 0.5, tot)
    order = np.argsort(-tot)
    chosen = []
    for j in order:
        n = ok[j]
        t, f = S[n][0], S[n][1]
        if any(abs(t - S[ok[c]][0]) < 3.0 for c in chosen):
            continue
        if V is not None and any(float(V[j] @ V[c]) > 0.92 for c in chosen):
            continue
        chosen.append(j)
        if len(chosen) >= k:
            break
    out_dir = out_dir or os.path.join(P.get("out") or os.path.dirname(P.get("_path", "")) or ".", f"thumbs_{P.get('name', 'edit')}")
    os.makedirs(out_dir, exist_ok=True)
    res = []
    import cv2
    W, H = target_size(P)
    for r, j in enumerate(chosen, 1):
        n = ok[j]
        t, f, st, rot = S[n]
        sh, sw = imgs[n].shape[:2]
        width = int(min(3840, max(W, np.ceil(H * sw / sh))))       # enough pixels to COVER the target, never upscale
        big = _fr.full(os.path.join(P["src"], f), [st], width, rot=rot)[0]
        if big is None:
            continue
        big = fit_crop(big, W, H)
        name = f"thumb_{r:02d}_{int(t // 60)}m{int(t % 60):02d}s.jpg"
        p = os.path.join(out_dir, name)
        cv2.imwrite(p, cv2.cvtColor(big, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])
        why = [lab for key, lab in (("face", "có mặt người rõ"), ("sharp", "nét"), ("aes", "đẹp"), ("colour", "màu rực"))
               if key in sc and sc[key][j] >= (0.5 if key == "face" else 0.8)]
        res.append({"t": t, "file": f, "src_t": st, "score": round(float(tot[j]), 3), "why": ", ".join(why),
                    "path": p.replace("\\", "/")})
    log(f"thumbs: {len(res)} khung → {out_dir}")
    return res


def main():
    import argparse
    import json
    import sys
    for s_ in (sys.stdout, sys.stderr):
        try:
            s_.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Gợi ý khung thumbnail cho bản dựng")
    ap.add_argument("plan")
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    P = jsonio.load(a.plan, {})
    P["_path"] = a.plan
    res = pick(P, a.k, a.out or None)
    print("THUMBS " + json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
