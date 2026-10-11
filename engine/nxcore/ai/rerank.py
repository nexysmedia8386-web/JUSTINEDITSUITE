"""B2 — two-stage footage search (coarse → fine, as in UniVTG-style moment retrieval).

SigLIP2 ranks every indexed shot in milliseconds but reads a picture as a bag of things ("biển", "người", "đồi cát"):
"người chạy trên đồi cát" and a person standing on a dune look the same to it. A local vision LLM (Ollama, Qwen3-VL)
reads who does what, where — too slow for 900 shots, fine for the best dozen. So: SigLIP shortlist → the VLM looks at
the frame of each shot that matched best → re-ordered by its 0–10 score; shots it judges unrelated are flagged.

  rerank(src, query, hits)            hits = SigLIP rows {"file", "a", "b", "score"} → same rows + "vlm" 0..1, "why"
  python -m nxcore.ai.rerank SRC "query" [--k 12]
"""
import base64
import json
import os
import time

import numpy as np

from .. import jsonio

from ..ollama import HOST as DEF_HOST    # noqa: E402 — one place parses OLLAMA_HOST
WIDTH = 448                      # NOT a token saving: Ollama ≥ 0.40 runs Qwen3-VL with --image-min-tokens 1024, so every
                                 # image costs ~1 080 prompt tokens whatever its size (measured 2026-10-11: 1280 px → 1 077,
                                 # 640 px → 1 077). A bigger frame would cost the same tokens with more detail — A/B it on B2 first.
MATCH = 0.6                      # vlm ≥ this = "khớp"; below 0.35 = unrelated (flagged in the panel)


def best_times(M, Z, qv, units):
    """unit index → time (s) of its frame that matches the query best (frames of a unit are stored in _times order)."""
    from .embed import _times
    out = {}
    for u in units:
        rows = np.nonzero(Z["unit"] == u)[0]
        _, a, b = M["units"][u]
        ts = _times(float(a), float(b))
        if len(rows) == len(ts) and len(rows):
            out[u] = ts[int(np.argmax(Z["vec"][rows] @ qv))]
        else:
            out[u] = (float(a) + float(b)) / 2
    return out


def jpeg_b64(img, q=85):
    import cv2
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, q])
    return base64.b64encode(buf.tobytes()).decode("ascii") if ok else None


def frames_rgb(src, items, media, width=WIDTH):
    """items [(file, t)] → [RGB frame | None], whole frames (aspect kept, as Premiere shows them), grouped per clip."""
    from . import frames as _fr
    by = {}
    for i, (f, t) in enumerate(items):
        by.setdefault(f, []).append((i, t))
    out = [None] * len(items)
    for f, its in by.items():
        rot = (media.get(f) or {}).get("rot", None)
        for (i, _), im in zip(its, _fr.full(os.path.join(src, f), [t for _, t in its], width, rot=rot)):
            out[i] = im
    return out


def frames_b64(src, items, media, width=WIDTH):
    return [jpeg_b64(im) if im is not None else None for im in frames_rgb(src, items, media, width)]


PROMPT = ("Người dựng phim đang tìm cảnh: “{q}”.\n"
          "Ảnh này khớp yêu cầu tới mức nào? Chấm 0–10:\n"
          "0–2 = không liên quan · 3–5 = đúng bối cảnh nhưng sai chủ thể hoặc hành động · "
          "6–8 = khớp phần lớn · 9–10 = khớp hoàn toàn.\n"
          'Chỉ trả JSON: {{"score": <số 0-10>, "why": "<lý do, tối đa 8 từ tiếng Việt>"}}')


def _post(host, path, body, timeout):
    from ..ollama import post
    return post(path, body, timeout, host)


def _chat(model, host, content, images, timeout=180, num_predict=60, fmt="json", num_ctx=2048):
    """One deterministic JSON chat call (shared client nxcore.ollama — it unloads a degraded runner once)."""
    from ..ollama import chat
    return chat(model, content, images=images, fmt=fmt, num_ctx=num_ctx, temperature=0.0, num_predict=num_predict,
                keep_alive="5m", timeout=timeout, host=host)


def score_one(model, host, query, img_b64):
    """→ (score 0..1, why) for one frame; (None, error) when the model answers nonsense."""
    try:
        txt = _chat(model, host, PROMPT.format(q=query), [img_b64])
        d = json.loads(txt)
        s = float(d.get("score"))
        return max(0.0, min(1.0, s / 10.0)), str(d.get("why") or "")[:80]
    except Exception as e:                       # noqa: BLE001 — one bad answer must not sink the whole search
        return None, str(e)[:80]


SHEET_PROMPT = ("Ảnh ghép gồm {n} ô, mỗi ô là một cảnh quay, đánh số 1–{n} (số vàng ở góc trên bên trái ô).\n"
                "Người dựng phim đang tìm cảnh: “{q}”.\n"
                "Chấm TỪNG ô 0–10: 0–2 = không liên quan · 3–5 = đúng bối cảnh nhưng sai chủ thể hoặc hành động · "
                "6–8 = khớp phần lớn · 9–10 = khớp hoàn toàn.\n"
                'Chỉ trả JSON dạng {{"1": điểm, "2": điểm, …, "{n}": điểm}}.')
TILE = (336, 189)


def contact_sheet(imgs_rgb, cols=4, tile=TILE):
    """[RGB frame] → one numbered grid image (RGB). The VLM sees every candidate in ONE image: Ollama turns any image
    into the same ~1 050 visual tokens, so a grid of 12 costs one encode instead of twelve."""
    import cv2
    w, h = tile
    rows = (len(imgs_rgb) + cols - 1) // cols
    sheet = np.zeros((rows * h, cols * w, 3), np.uint8)
    for i, im in enumerate(imgs_rgb):
        r, c = divmod(i, cols)
        t = cv2.resize(im, (w, h), interpolation=cv2.INTER_AREA) if im is not None else np.zeros((h, w, 3), np.uint8)
        t = t.copy()
        cv2.rectangle(t, (0, 0), (40 if i < 9 else 54, 30), (0, 0, 0), -1)
        cv2.putText(t, str(i + 1), (5, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 230, 0), 2, cv2.LINE_AA)
        cv2.rectangle(t, (0, 0), (w - 1, h - 1), (0, 0, 0), 2)
        sheet[r * h:(r + 1) * h, c * w:(c + 1) * w] = t
    return sheet


def score_sheet(model, host, query, imgs_rgb, cols=4, tile=TILE):
    """→ [score 0..1 | None] for every frame, from ONE call on a numbered grid."""
    n = len(imgs_rgb)
    schema = {"type": "object", "properties": {str(i): {"type": "integer"} for i in range(1, n + 1)},
              "required": [str(i) for i in range(1, n + 1)]}
    try:
        txt = _chat(model, host, SHEET_PROMPT.format(n=n, q=query), [jpeg_b64(contact_sheet(imgs_rgb, cols, tile), 88)],
                    num_predict=12 * n + 20, fmt=schema)
        d = json.loads(txt)
    except Exception:                            # noqa: BLE001
        return [None] * n
    out = []
    for i in range(1, n + 1):
        try:
            out.append(max(0.0, min(1.0, float(d.get(str(i))) / 10.0)))
        except (TypeError, ValueError):
            out.append(None)
    return out


def model_for(model="auto", host=DEF_HOST):
    """Vision model to use (the panel's / describe's choice) → (name, "") or (None, reason)."""
    from ..describe import resolve_model
    return resolve_model(host, model or "auto")


MODE = "one"


def rerank(src, query, hits, model="auto", host=DEF_HOST, k=12, log=print, on_row=None, stop=None, mode=None):
    """Re-score the first k SigLIP hits with the vision LLM. Rows beyond k keep their SigLIP order after them.
    on_row(hit) is called as each scored row is ready (the panel updates tiles live); stop() → True ends early
    (rows not scored yet keep their SigLIP place). mode "one" = a call per frame, "sheet4" = 2×2 numbered grids."""
    mode = mode or MODE
    stop = stop or (lambda: False)
    name, why = model_for(model, host)
    if not name:
        raise RuntimeError(why)
    from .embed import embed_texts, load_index
    M, Z = load_index(src)
    if not M:
        raise RuntimeError("chưa có chỉ mục — bấm Lập chỉ mục")
    media = jsonio.load(os.path.join(src, "_nexys_work", "media.json"), {}) or {}
    head = hits[:k]
    key = {(u[0], float(u[1]), float(u[2])): i for i, u in enumerate(M["units"])}
    units = [key.get((h["file"], float(h["a"]), float(h["b"]))) for h in head]
    qv = embed_texts([query], M.get("model"))[0]
    bt = best_times(M, Z, qv, [u for u in units if u is not None])
    items = [(h["file"], bt.get(u, (float(h["a"]) + float(h["b"])) / 2)) for h, u in zip(head, units)]
    t0 = time.time()
    ims = frames_rgb(src, items, media)
    log(f"rerank: {len(head)} khung · {time.time() - t0:.1f}s · model {name} · {mode}")
    t1 = time.time()
    for h, (f, t) in zip(head, items):
        h["t"], h["vlm"], h["why"] = round(t, 2), None, ""
    step = 4 if mode == "sheet4" else 1
    for c0 in range(0, len(head), step):
        if stop():
            log("rerank: dừng (có yêu cầu mới)")
            break
        part = ims[c0:c0 + step]
        if mode == "sheet4":
            got = [(s, "") for s in score_sheet(name, host, query, part, 2, (448, 252))]
        else:
            got = [score_one(name, host, query, jpeg_b64(part[0])) if part[0] is not None else (None, "không đọc được khung")]
        for h, (s, w) in zip(head[c0:c0 + step], got):
            h["vlm"], h["why"] = (round(s, 2) if s is not None else None), w
            if on_row:
                on_row(h)
    log(f"rerank: chấm xong {time.time() - t1:.1f}s")
    n = len(head)
    # VLM score decides; SigLIP order breaks ties (and places rows the VLM could not score)
    order = sorted(range(n), key=lambda i: (-(head[i]["vlm"] if head[i]["vlm"] is not None else 0.45), i))
    return [head[i] for i in order] + hits[k:]


def main():
    import argparse
    import sys
    for s_ in (sys.stdout, sys.stderr):
        try:
            s_.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Tìm footage hai tầng: SigLIP2 lọc → vision LLM (Ollama) xếp hạng lại")
    ap.add_argument("src")
    ap.add_argument("query")
    ap.add_argument("--k", type=int, default=12)
    ap.add_argument("--model", default="auto")
    a = ap.parse_args()
    from .embed import search
    hits = search(a.src, a.query, k=max(a.k, 24))
    for h in rerank(a.src, a.query, hits, a.model, k=a.k)[:a.k]:
        print(f"{h.get('vlm')!s:>5} {h['score']:.3f} {h['file']} {h['a']}-{h['b']} · {h.get('why', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
