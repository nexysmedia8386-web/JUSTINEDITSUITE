"""C2 — find footage by its SOUND (tiếng cười, vỗ tay, sóng biển, xe máy, chim hót, mưa, nhạc…).

Model: PANNs Cnn14_16k (Kong et al. 2020, AudioSet 527 classes, mAP 0.438). Code MIT (architecture below follows
qiuqiangkong/audioset_tagging_cnn), weights CC-BY-4.0 (Zenodo 3987831) — commercial use allowed with attribution.
CLAP (free-text audio search) was not used: LAION-CLAP's checkpoints carry no licence and were trained partly on
research-only Freesound / NC clips; Microsoft CLAP states no weight licence either.

Free text still works: the query is matched to the 527 English class names with the multilingual SigLIP2 text tower
(already loaded for picture search) plus a small Vietnamese dictionary for the sounds editors ask for most.

  index(src)               _nexys_work/sounds.npz: per 2 s window (1 s hop) the 527 class probabilities (uint8)
  search(src, q, k)        → [{"file", "a", "b", "score", "labels"}]  (adjacent matching windows merged)
  python -m nxcore.ai.sounds index|search SRC ["query"]
"""
import csv
import json
import os
import sys

import numpy as np

from .. import jsonio
from . import CACHE, cached, device, torch_ok

SND_V = 1
SR = 16000
WIN, HOP = 2.0, 1.0
WEIGHTS = os.path.join(CACHE, "panns", "Cnn14_16k_mAP=0.438.pth")
WEIGHTS_URL = "https://zenodo.org/records/3987831/files/Cnn14_16k_mAP%3D0.438.pth?download=1"
_LAB = os.path.join(os.path.dirname(__file__), "audioset_labels.csv")

# Vietnamese → AudioSet class names (what editors search for most; SigLIP handles the rest)
VI = {
    "cười": ["Laughter", "Giggle", "Chuckle, chortle", "Belly laugh"], "vỗ tay": ["Applause", "Clapping"],
    "hò reo": ["Cheering", "Crowd", "Applause"], "cổ vũ": ["Cheering", "Crowd"], "đám đông": ["Crowd", "Hubbub, speech noise, speech babble"],
    "sóng": ["Waves, surf", "Ocean"], "biển": ["Waves, surf", "Ocean"], "gió": ["Wind", "Wind noise (microphone)"],
    "mưa": ["Rain", "Raindrop", "Rain on surface"], "sấm": ["Thunder", "Thunderstorm"], "nước chảy": ["Stream", "Waterfall", "Water"],
    "thác": ["Waterfall"], "chim": ["Bird", "Bird vocalization, bird call, song", "Chirp, tweet"], "chó": ["Dog", "Bark"],
    "mèo": ["Cat", "Meow"], "gà": ["Chicken, rooster", "Crowing, cock-a-doodle-doo"], "xe máy": ["Motorcycle", "Engine"],
    "động cơ": ["Engine", "Idling", "Motor vehicle (road)", "Accelerating, revving, vroom"], "xe": ["Car", "Motor vehicle (road)", "Traffic noise, roadway noise"],
    "ô tô": ["Car", "Vehicle", "Engine"], "xe hơi": ["Car", "Vehicle"], "còi xe": ["Vehicle horn, car horn, honking"],
    "máy bay": ["Aircraft", "Fixed-wing aircraft, airplane"], "flycam": ["Aircraft", "Buzz"], "tàu": ["Boat, Water vehicle", "Motorboat, speedboat"],
    "thuyền": ["Boat, Water vehicle", "Motorboat, speedboat"], "nhạc": ["Music"], "hát": ["Singing", "Music"],
    "trống": ["Drum", "Drum kit"], "guitar": ["Guitar", "Acoustic guitar"], "piano": ["Piano"], "pháo hoa": ["Fireworks", "Firecracker"],
    "pháo": ["Firecracker", "Fireworks"], "nói chuyện": ["Speech", "Conversation"], "trẻ em": ["Child speech, kid speaking", "Children playing"],
    "em bé": ["Baby cry, infant cry", "Babbling"], "khóc": ["Crying, sobbing", "Baby cry, infant cry"], "la hét": ["Screaming", "Shout"],
    "bước chân": ["Walk, footsteps"], "cửa": ["Door", "Slam"], "chuông": ["Bell", "Church bell", "Doorbell"],
    "điện thoại": ["Telephone", "Ringtone"], "nấu ăn": ["Frying (food)", "Sizzle", "Dishes, pots, and pans"], "chiên": ["Frying (food)", "Sizzle"],
    "côn trùng": ["Insect", "Cricket"], "ve": ["Insect", "Cricket"], "lửa": ["Fire", "Crackle"], "tiếng ồn": ["Noise", "Hubbub, speech noise, speech babble"],
    "im lặng": ["Silence"], "huýt sáo": ["Whistling"], "đàn": ["Musical instrument", "Plucked string instrument"],
}


def labels():
    return cached(("audioset_labels",), lambda: [r["display_name"] for r in csv.DictReader(open(_LAB, encoding="utf-8"))])


def available():
    return torch_ok() and os.path.exists(_LAB)


# ---------------------------------------------------------------- model (PANNs Cnn14, MIT)
def _build():
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from torchlibrosa.stft import LogmelFilterBank, Spectrogram

    class ConvBlock(nn.Module):
        def __init__(self, cin, cout):
            super().__init__()
            self.conv1 = nn.Conv2d(cin, cout, 3, 1, 1, bias=False)
            self.conv2 = nn.Conv2d(cout, cout, 3, 1, 1, bias=False)
            self.bn1, self.bn2 = nn.BatchNorm2d(cout), nn.BatchNorm2d(cout)

        def forward(self, x, pool=(2, 2)):
            x = F.relu_(self.bn1(self.conv1(x)))
            x = F.relu_(self.bn2(self.conv2(x)))
            return F.avg_pool2d(x, kernel_size=pool)

    class Cnn14(nn.Module):
        def __init__(self, sr=SR, win=512, hop=160, mels=64, fmin=50, fmax=8000, classes=527):
            super().__init__()
            self.spectrogram_extractor = Spectrogram(n_fft=win, hop_length=hop, win_length=win, window="hann", center=True,
                                                     pad_mode="reflect", freeze_parameters=True)
            self.logmel_extractor = LogmelFilterBank(sr=sr, n_fft=win, n_mels=mels, fmin=fmin, fmax=fmax, ref=1.0, amin=1e-10,
                                                     top_db=None, freeze_parameters=True)
            self.bn0 = nn.BatchNorm2d(64)
            chans = [1, 64, 128, 256, 512, 1024, 2048]
            for i in range(6):
                setattr(self, f"conv_block{i + 1}", ConvBlock(chans[i], chans[i + 1]))
            self.fc1 = nn.Linear(2048, 2048)
            self.fc_audioset = nn.Linear(2048, classes)

        def forward(self, wav):                                  # (batch, samples) → (batch, 527) probabilities
            x = self.logmel_extractor(self.spectrogram_extractor(wav))
            x = self.bn0(x.transpose(1, 3)).transpose(1, 3)
            for i in range(1, 6):
                x = getattr(self, f"conv_block{i}")(x, (2, 2))
            x = self.conv_block6(x, (1, 1))
            x = torch.mean(x, dim=3)
            x = torch.max(x, dim=2)[0] + torch.mean(x, dim=2)
            x = F.relu_(self.fc1(x))
            return torch.sigmoid(self.fc_audioset(x))

    if not os.path.exists(WEIGHTS):
        _download()
    m = Cnn14()
    sd = torch.load(WEIGHTS, map_location="cpu", weights_only=False)
    sd = sd.get("model", sd)
    missing, unexpected = m.load_state_dict(sd, strict=False)
    real = [k for k in missing if not k.startswith(("spectrogram_extractor", "logmel_extractor"))]
    if real:
        raise RuntimeError(f"PANNs: thiếu trọng số {real[:3]}")
    dev = device()
    return m.to(dev).eval(), dev


def _download(progress=print):
    import urllib.request
    os.makedirs(os.path.dirname(WEIGHTS), exist_ok=True)
    progress("sounds: tải PANNs Cnn14_16k (~360 MB, CC-BY-4.0)…")
    tmp = WEIGHTS + ".part"
    with urllib.request.urlopen(WEIGHTS_URL, timeout=120) as r, open(tmp, "wb") as fh:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            fh.write(b)
    os.replace(tmp, WEIGHTS)


def _model():
    return cached(("panns", WEIGHTS), _build)


def tag_audio(x, batch=64):
    """16 kHz mono float32 → (windows × 527) probabilities, window i covers [i·HOP, i·HOP + WIN]."""
    import torch
    m, dev = _model()
    L, H = int(WIN * SR), int(HOP * SR)
    if len(x) < L:
        x = np.pad(x, (0, L - len(x)))
    n = 1 + (len(x) - L) // H
    idx = np.arange(n)[:, None] * H + np.arange(L)[None, :]
    out = []
    with torch.inference_mode():
        for i in range(0, n, batch):
            w = torch.from_numpy(np.ascontiguousarray(x[idx[i:i + batch]], dtype=np.float32)).to(dev)
            out.append(m(w).float().cpu().numpy())
    return np.concatenate(out) if out else np.zeros((0, 527), np.float32)


# ---------------------------------------------------------------- index
def paths(src):
    W = os.path.join(src, "_nexys_work")
    return os.path.join(W, "sounds.npz"), os.path.join(W, "sounds.json")


def load_index(src):
    npz, man = paths(src)
    if not (os.path.exists(npz) and os.path.exists(man)):
        return None, None
    try:
        Z = np.load(npz)
        return jsonio.load(man, None), {"p": Z["p"], "clip": Z["clip"], "t0": Z["t0"]}
    except Exception:
        return None, None


def index(src, force=False, progress=print):
    if not available():
        progress("sounds: chưa có gói AI (torch) → bỏ qua")
        return None
    from ..montage import decode_16k_many
    media = jsonio.load(os.path.join(src, "_nexys_work", "media.json"), {}) or {}
    files = [f for f, m in media.items() if m.get("status", "ok") == "ok" and m.get("audio", True) and float(m.get("dur") or 0) > 0.5]
    M, Z = (None, None) if force else load_index(src)
    old = {}
    if M and Z is not None and M.get("v") == SND_V:
        for ci, f in enumerate(M["files"]):
            if (M["clips"].get(f) or {}).get("key") == (media.get(f) or {}).get("key"):
                sel = Z["clip"] == ci
                old[f] = (Z["p"][sel], Z["t0"][sel])
    todo = [f for f in files if f not in old]
    progress(json.dumps({"stage": "sounds", "total": len(todo), "cached": len(files) - len(todo)}))
    # ffmpeg decodes the next clips in background threads while the GPU tags this one (was strictly one after the
    # other: 14.8 s decode + 6.7 s GPU for 340 clips)
    for k, (p_, x) in enumerate(decode_16k_many([os.path.join(src, f) for f in todo]), 1):
        f = todo[k - 1]
        if isinstance(x, Exception):
            progress(f"[{k}/{len(todo)}] {f} lỗi đọc âm thanh ({x})")
            continue
        if x is None or len(x) < SR // 2:
            continue
        p = np.round(tag_audio(np.asarray(x, np.float32)) * 255).astype(np.uint8)
        old[f] = (p, (np.arange(len(p)) * HOP).astype(np.float32))
        progress(f"[{k}/{len(todo)}] {f} · {len(p)} cửa sổ")
    P, C, T, names, clips = [], [], [], [], {}
    for f in files:
        if f not in old:
            continue
        p, t0 = old[f]
        ci = len(names)
        names.append(f)
        clips[f] = {"key": (media.get(f) or {}).get("key"), "n": int(len(p))}
        P.append(p); C.append(np.full(len(p), ci, np.int32)); T.append(t0)
    if not P:
        return None
    npz, man = paths(src)
    np.savez_compressed(npz, p=np.concatenate(P), clip=np.concatenate(C), t0=np.concatenate(T))
    jsonio.save_atomic(man, {"v": SND_V, "model": "panns-cnn14-16k", "win": WIN, "hop": HOP, "files": names, "clips": clips})
    progress(f"sounds: {len(names)} clip · {sum(len(p) for p in P)} cửa sổ trong chỉ mục")
    return len(names)


# ---------------------------------------------------------------- query → classes
def _norm(s):
    return " ".join(str(s).lower().split())


def query_classes(q, top=4):
    """→ {class index: weight} for a free-text query (Vietnamese or English)."""
    labs = labels()
    pos = {l: i for i, l in enumerate(labs)}
    qn = _norm(q)
    w = {}
    for key, cls in VI.items():                           # dictionary hits first (exact Vietnamese words)
        if key in qn:
            for r, c in enumerate(cls):
                if c in pos:
                    w[pos[c]] = max(w.get(pos[c], 0.0), 1.0 - 0.15 * r)
    for i, l in enumerate(labs):                          # English class name typed directly
        if len(l) > 3 and _norm(l) in qn:
            w[i] = max(w.get(i, 0.0), 1.0)
    try:                                                  # multilingual SigLIP2 text tower for everything else
        from .embed import available as _ea, embed_texts
        if _ea():
            T = embed_texts([f"the sound of {l}" for l in labs])
            v = embed_texts([q])[0]
            s = T @ v
            # when the dictionary already understood the query, SigLIP only adds its 2 closest classes, at half weight:
            # it reads words, not sounds ("sóng" → "Sine wave", "gió thổi" → "Breathing")
            hit = bool(w)
            order = np.argsort(-s)[:2 if hit else top]
            for r, i in enumerate(order):
                if s[i] >= s[order[0]] - (0.015 if hit else 0.03):
                    w[int(i)] = max(w.get(int(i), 0.0), (0.5 if hit else 1.0) - 0.1 * r)
    except Exception:                                     # noqa: BLE001
        pass
    tot = sum(w.values()) or 1.0
    return {k: v / tot for k, v in sorted(w.items(), key=lambda z: -z[1])[:6]}


def search(src, q, k=24, thr=0.15):
    """Windows scored by the query's classes → merged into moments per clip → best k."""
    M, Z = load_index(src)
    if not M:
        return []
    W = query_classes(q)
    if not W:
        return []
    cls = np.array(list(W.keys()))
    wt = np.array(list(W.values()), np.float32)
    P = Z["p"][:, cls].astype(np.float32) / 255.0
    s = (P * wt[None, :]).sum(axis=1) / wt.sum() if len(wt) > 1 else P[:, 0]
    # one strongly present class is enough — but only in proportion to how much the query means that class (a
    # low-weight SigLIP extra like "Whistling" for "tiếng gió" must not win on its own)
    s = np.maximum(s, (P * (wt / wt.max())[None, :]).max(axis=1) * 0.8)
    labs = labels()
    out = []
    hit = np.nonzero(s >= thr)[0]
    i = 0
    while i < len(hit):                                   # merge consecutive windows of one clip
        j = i
        while j + 1 < len(hit) and hit[j + 1] == hit[j] + 1 and Z["clip"][hit[j + 1]] == Z["clip"][hit[i]]:
            j += 1
        span = hit[i:j + 1]
        f = M["files"][int(Z["clip"][span[0]])]
        best = span[int(np.argmax(s[span]))]
        pres = [labs[c] for c in cls if Z["p"][best, c] >= 0.15 * 255]
        out.append({"file": f, "a": round(float(Z["t0"][span[0]]), 2), "b": round(float(Z["t0"][span[-1]] + WIN), 2),
                    "score": round(float(s[best]), 3), "t": round(float(Z["t0"][best] + WIN / 2), 2), "labels": pres})
        i = j + 1
    out.sort(key=lambda h: -h["score"])
    return out[:k]


def main():
    import argparse
    for s_ in (sys.stdout, sys.stderr):
        try:
            s_.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Tìm footage theo âm thanh (PANNs AudioSet)")
    ap.add_argument("cmd", choices=("index", "search", "classes"))
    ap.add_argument("src", nargs="?", default="")
    ap.add_argument("query", nargs="?", default="")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.cmd == "index":
        index(a.src, a.force, progress=lambda m: print(m, flush=True))
    elif a.cmd == "classes":
        labs = labels()
        print({labs[c]: round(w, 2) for c, w in query_classes(a.query or a.src).items()})
    else:
        for h in search(a.src, a.query, 12):
            print(f"{h['score']:.2f} {h['file']} {h['a']}-{h['b']} · {', '.join(h['labels'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
