"""Shot quality (technical + aesthetic) from the SigLIP2 index — zero-shot, CLIP-IQA style, no extra decode.

Why not DOVER: its code/weights are under the S-Lab License 1.0 (non-commercial only) and pin torch 1.13. Here each
shot's existing SigLIP2 picture vector is compared with positive / negative descriptions; the score is turned into a
rank inside the folder (0…1), so it ranks THIS footage against itself — what picking B-roll needs.

    scores(src) → {unit index: {"tech": 0..1, "aes": 0..1}} or {} without an index / AI pack
"""
import numpy as np

from . import enabled

PROMPTS = {
    "tech": ("a sharp, clear, well exposed, high quality photo",
             "a blurry, noisy, out of focus, badly exposed, low quality photo"),
    "aes": ("a beautiful cinematic shot with great composition and lighting",
            "a boring, ugly, poorly framed amateur snapshot"),
}


def available():
    from .embed import available as _ea
    return enabled("quality") and _ea()


def scores(src):
    if not available():
        return {}
    from .embed import embed_texts, load_index
    M, Z = load_index(src)
    if not M:
        return {}
    n = len(M["units"])
    mean = np.zeros((n, Z["vec"].shape[1]), np.float32)
    np.add.at(mean, Z["unit"], Z["vec"])
    mean /= np.linalg.norm(mean, axis=1, keepdims=True) + 1e-9
    texts = [t for pair in PROMPTS.values() for t in pair]
    T = embed_texts(texts, M.get("model"))
    S = mean @ T.T                                    # units × prompts
    out = {}
    for j, k in enumerate(PROMPTS):
        d = S[:, 2 * j] - S[:, 2 * j + 1]             # positive − negative similarity
        r = np.argsort(np.argsort(d)) / max(1, n - 1)  # rank in the folder 0..1
        for i in range(n):
            out.setdefault(i, {})[k] = round(float(r[i]), 3)
    return out
