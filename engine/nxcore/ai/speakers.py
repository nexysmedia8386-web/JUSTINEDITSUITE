"""C3 — who speaks when (speaker diarization), across the WHOLE source folder: the same person gets the same label
in every clip (S1, S2, …).

Why not pyannote: its pipelines are gated (accept terms + an access token per user). Here: WeSpeaker ResNet34-LM
(VoxCeleb, CC-BY-4.0, ungated, ONNX 26 MB, CPU) for voice embeddings + the speech regions the tool already has
(word timestamps aligned by wav2vec2) instead of a separate VAD + spectral clustering that finds the number of
people itself (NME-SC, Park et al. 2019).

  diarize(src)        → _nexys_work/speakers.json {"speakers": {S1: {secs, clips, sample}}, "clips": {file: [[a, b, "S1"]…]}}
  speaker_at(src)     → function (file, t) → "S1" | None   (for transcripts / LLM context)
  python -m nxcore.ai.speakers SRC [--thr 0.55]
"""
import os
import sys

import numpy as np

from .. import jsonio

SPK_V = 1
REPO, FILE = "Wespeaker/wespeaker-voxceleb-resnet34-LM", "voxceleb_resnet34_LM.onnx"
SR = 16000
WIN, HOP = 1.5, 0.75
# Measured on stitched conversations of known voices (narrator, presenter near / far from a phone in a reverberant
# showroom, Windows vi-VN TTS): spectral clustering (NME-SC) found 3/3, 2/2, 1/1 people, 100 % of windows right;
# no fixed AHC threshold got all three (0.75–0.80 split the lone presenter, 0.85 merged the 2-person talk).
MIN_SPECTRAL = 20              # fewer windows than this (< ~15 s of speech) → conservative AHC
THR = 0.85                     # AHC cosine distance for that fallback
MAX_CLUSTER = 2000             # NME-SC is O(n³): cluster a sample, assign the rest to the nearest centroid


def _session():
    from . import cached

    def build():
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        p = hf_hub_download(REPO, FILE)
        return ort.InferenceSession(p, providers=["CPUExecutionProvider"])
    return cached(("wespeaker", REPO), build)


def available():
    try:
        import onnxruntime  # noqa: F401
        import torchaudio  # noqa: F401
        return True
    except Exception:
        return False


def fbank(x):
    """16 kHz float waveform → (frames × 80) WeSpeaker features (Kaldi fbank, Hamming, int16 scale, mean-normalised)."""
    import torch
    import torchaudio.compliance.kaldi as kaldi
    w = torch.from_numpy(np.asarray(x, np.float32) * (1 << 15)).unsqueeze(0)
    f = kaldi.fbank(w, num_mel_bins=80, frame_length=25, frame_shift=10, dither=0.0, sample_frequency=SR,
                    window_type="hamming", use_energy=False)
    return (f - f.mean(dim=0, keepdim=True)).numpy()


def embed(chunks, batch=32):
    """[waveform chunk] (equal length) → (n × 256) L2-normalised voice embeddings."""
    s = _session()
    feats = [fbank(c) for c in chunks]
    out = []
    for i in range(0, len(feats), batch):
        F = np.stack(feats[i:i + batch]).astype(np.float32)
        out.append(s.run(None, {"feats": F})[0])
    E = np.concatenate(out) if out else np.zeros((0, 256), np.float32)
    return E / (np.linalg.norm(E, axis=1, keepdims=True) + 1e-9)


def speech_regions(words, gap=0.5, min_len=0.6):
    """Aligned words → [(a, b)] stretches of continuous speech."""
    out = []
    for w in sorted(words, key=lambda w: float(w[0])):
        a, b = float(w[0]), float(w[1])
        if out and a - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out if b - a >= min_len]


def windows(regions):
    """[(a, b)] → window starts covering each region (a short region gets one window centred on it)."""
    out = []
    for a, b in regions:
        if b - a <= WIN:
            out.append(max(0.0, (a + b - WIN) / 2))
        else:
            out += list(np.arange(a, b - WIN + 1e-6, HOP))
            if b - WIN - out[-1] > HOP / 2:
                out.append(b - WIN)
    return out


def cluster(E, thr=THR, min_share=0.03):
    """Agglomerative clustering (average linkage, cosine distance) → labels 0..k-1 by total size; clusters holding
    < min_share of the windows are merged into the nearest big one (stray coughs / music / cross-talk)."""
    if len(E) == 0:
        return np.zeros(0, int)
    if len(E) == 1:
        return np.zeros(1, int)
    from scipy.cluster.hierarchy import fcluster, linkage
    Z = linkage(E, method="average", metric="cosine")
    lab = fcluster(Z, t=thr, criterion="distance") - 1
    ids, cnt = np.unique(lab, return_counts=True)
    big = [i for i, c in zip(ids, cnt) if c >= max(2, min_share * len(E))] or [ids[np.argmax(cnt)]]
    cent = {i: E[lab == i].mean(0) for i in big}
    for i in ids:
        if i not in big:
            j = max(big, key=lambda b: float(cent[b] @ E[lab == i].mean(0)))
            lab[lab == i] = j
    order = sorted(set(lab.tolist()), key=lambda i: -(lab == i).sum())
    remap = {o: n for n, o in enumerate(order)}
    return np.array([remap[i] for i in lab])


def cluster_spectral(E, k_max=10):
    """Spectral clustering with the speaker count from the eigengap of a p-nearest-neighbour affinity, p chosen by
    NME-SC (Park et al. 2019, "Auto-tuning spectral clustering for speaker diarization"): no threshold to tune —
    a fixed cosine threshold split one presenter (phone mic, reverberant room) into 2–9 "people"."""
    n = len(E)
    if n < 4:
        return np.zeros(n, int)
    from scipy.linalg import eigh
    from sklearn.cluster import KMeans
    S_ = np.clip(E @ E.T, -1, 1)
    best = None
    for p in sorted({max(2, int(n * q)) for q in (0.02, 0.04, 0.06, 0.08, 0.1, 0.13, 0.16, 0.2, 0.25)}):
        if p >= n:
            continue
        A_ = np.zeros_like(S_)
        idx = np.argsort(-S_, axis=1)[:, :p]
        np.put_along_axis(A_, idx, 1.0, axis=1)
        A_ = (A_ + A_.T) / 2
        Lp = np.diag(A_.sum(1)) - A_
        lam = eigh(Lp, eigvals_only=True)
        km = min(k_max, n - 1)
        gaps = np.diff(lam[:km + 1])
        k = int(np.argmax(gaps)) + 1
        g = gaps[k - 1] / max(lam[-1], 1e-9)
        r = (p / n) / max(g, 1e-9)
        if best is None or r < best[0]:
            best = (r, p, k, A_)
    _, p, k, A_ = best
    if k == 1:
        return np.zeros(n, int)
    D = np.diag(1.0 / np.sqrt(A_.sum(1) + 1e-9))
    Ls = np.eye(n) - D @ A_ @ D
    _, V = eigh(Ls)
    U = V[:, :k]
    U = U / (np.linalg.norm(U, axis=1, keepdims=True) + 1e-9)
    lab = KMeans(n_clusters=k, n_init=10, random_state=0).fit_predict(U)
    order = sorted(set(lab.tolist()), key=lambda i: -(lab == i).sum())
    remap = {o: m for m, o in enumerate(order)}
    return np.array([remap[i] for i in lab])


def assign(E, thr=THR):
    """Speaker label per window: NME-SC on (a sample of) the windows, AHC when there are too few."""
    n = len(E)
    if n < MIN_SPECTRAL:
        return cluster(E, thr)
    if n <= MAX_CLUSTER:
        return cluster_spectral(E)
    sel = np.sort(np.random.default_rng(0).choice(n, MAX_CLUSTER, replace=False))
    lab_s = cluster_spectral(E[sel])
    C = np.stack([E[sel][lab_s == k].mean(0) for k in range(lab_s.max() + 1)])
    return np.argmax(E @ C.T, axis=1)


def turns(starts, labels, regions, min_turn=1.0):
    """Window labels → speaker turns [[a, b, k]] over the speech regions; a turn < min_turn s between two turns of
    the same speaker is absorbed (a window straddling a change flips briefly)."""
    out = []
    centers = np.asarray(starts, float) + WIN / 2
    for a, b in regions:
        sel = [i for i in np.argsort(centers) if a - WIN / 2 <= centers[i] <= b + WIN / 2]
        for n, i in enumerate(sel):                     # each window owns ±HOP/2 around its centre; ends stretch
            t0 = a if n == 0 else max(a, centers[i] - HOP / 2)
            t1 = b if n == len(sel) - 1 else min(b, centers[i] + HOP / 2)
            k = int(labels[i])
            if n and out[-1][2] == k:
                out[-1][1] = t1
            else:
                out.append([t0, t1, k])
    i = 1
    while i < len(out) - 1:
        if out[i][1] - out[i][0] < min_turn and out[i - 1][2] == out[i + 1][2]:
            out[i - 1][1] = out[i + 1][1]
            del out[i:i + 2]
        else:
            i += 1
    return [[round(a, 2), round(b, 2), k] for a, b, k in out if b > a]


def diarize(src, thr=THR, progress=print, force=False):
    from ..aiedit import _words
    from ..montage import decode_16k_many
    media = jsonio.load(os.path.join(src, "_nexys_work", "media.json"), {}) or {}
    out_p = os.path.join(src, "_nexys_work", "speakers.json")
    starts, owner, chunks, regs = [], [], [], {}
    talk = {}
    for f, m in media.items():
        if m.get("status", "ok") == "ok":
            R = speech_regions(_words(src, f))
            if R:
                talk[f] = R
    names = list(talk)
    for k, (_, x) in enumerate(decode_16k_many([os.path.join(src, f) for f in names])):
        f, R = names[k], talk[names[k]]
        if isinstance(x, Exception):
            progress(f"speakers: {f} lỗi đọc âm thanh ({x})")
            continue
        regs[f] = R
        L = int(WIN * SR)
        for s in windows(R):
            c = x[int(s * SR):int(s * SR) + L]
            if len(c) < L:
                c = np.pad(c, (0, L - len(c)))
            starts.append(s); owner.append(f); chunks.append(c)
    if not chunks:
        progress("speakers: không có đoạn thoại nào (cần transcript)")
        jsonio.save_atomic(out_p, {"v": SPK_V, "speakers": {}, "clips": {}})
        return {}
    progress(f"speakers: {len(chunks)} cửa sổ giọng trong {len(regs)} clip")
    E = embed(chunks)
    lab = assign(E, thr)
    owner = np.array(owner)
    starts = np.array(starts)
    clips, spk = {}, {}
    for f, R in regs.items():
        sel = owner == f
        T = turns(starts[sel], lab[sel], R)
        T = [[float(a), float(b), int(k)] for a, b, k in T]
        clips[f] = [[a, b, f"S{k + 1}"] for a, b, k in T]
        for a, b, k in T:
            s = spk.setdefault(f"S{k + 1}", {"secs": 0.0, "clips": set(), "sample": None, "_best": 0.0})
            s["secs"] += b - a
            s["clips"].add(f)
            if b - a > s["_best"]:
                s["_best"], s["sample"] = b - a, [f, a, b]
    speakers = {k: {"secs": round(v["secs"], 1), "clips": len(v["clips"]), "sample": v["sample"]}
                for k, v in sorted(spk.items(), key=lambda z: int(z[0][1:]))}
    jsonio.save_atomic(out_p, {"v": SPK_V, "model": REPO, "thr": thr, "speakers": speakers, "clips": clips,
                               "keys": {f: (media.get(f) or {}).get("key") for f in clips}})
    progress(f"speakers: {len(speakers)} người nói · " + ", ".join(f"{k} {v['secs']:.0f}s/{v['clips']} clip" for k, v in speakers.items()))
    return speakers


def speaker_at(src):
    """→ f(file, t) → "S1" | None, from speakers.json (None when the folder was not diarized)."""
    d = jsonio.load(os.path.join(src, "_nexys_work", "speakers.json"), {}) or {}
    C = d.get("clips") or {}

    def f(file, t):
        for a, b, k in C.get(file) or ():
            if a - 0.2 <= t <= b + 0.2:
                return k
        return None
    return f


def main():
    import argparse
    for s_ in (sys.stdout, sys.stderr):
        try:
            s_.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Ai đang nói lúc nào (diarization) cho cả thư mục source")
    ap.add_argument("src")
    ap.add_argument("--thr", type=float, default=THR)
    a = ap.parse_args()
    diarize(a.src, a.thr, progress=lambda m: print(m, flush=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
