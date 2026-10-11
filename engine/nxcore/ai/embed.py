"""Search footage by meaning with SigLIP2 (Google, Apache-2.0, multilingual — Vietnamese queries work).

Each shot (segments.json, else the whole clip) → up to 3 frames → SigLIP2 image embeddings, cached in
_nexys_work/embed/ by clip key. A text query is embedded once and ranked by cosine similarity (best frame per shot).
~100× faster than describing every shot with a vision LLM, runs on CPU too (slower), no Ollama / VRAM juggling.

CLI:
  python -m nxcore.ai.embed index SRC [--force] [--model google/siglip2-base-patch16-224]
  python -m nxcore.ai.embed search SRC "hoàng hôn trên biển" [--k 20]       → lines "HIT {json}"
  python -m nxcore.ai.embed dups SRC [--thr 0.95]                             → near-identical shots (takes)
Python: search(src, query, k) → [{"file","a","b","score"}]; score_texts(src, [query…]) for nxcore.brollpick.
"""
import argparse
import json
import os
import sys

import numpy as np

from .. import jsonio
from . import cached, device, enabled, has, torch_ok

MODEL = os.environ.get("NX_SIGLIP", "google/siglip2-base-patch16-224")
EMB_V = 1
SIZE = 384                      # frames grabbed at 384 px; the processor resizes to the model's input


def available():
    return enabled("embed") and torch_ok() and has("transformers")


def _load(model=MODEL):
    import torch
    from transformers import AutoModel, AutoProcessor
    dev = device()
    dt = torch.float16 if dev == "cuda" else torch.float32
    m = AutoModel.from_pretrained(model, torch_dtype=dt).to(dev).eval()
    p = AutoProcessor.from_pretrained(model)
    return m, p, dev, dt


def _get(model=MODEL):
    return cached(("siglip", model), lambda: _load(model))


def _vec(o):
    """transformers ≥ 5 returns a ModelOutput from get_*_features (pooled vector in pooler_output); 4.x a tensor."""
    return o if hasattr(o, "float") else (getattr(o, "pooler_output", None) if getattr(o, "pooler_output", None) is not None else o[0])


def embed_images(imgs, model=MODEL, batch=32):
    import torch
    m, p, dev, dt = _get(model)
    out = []
    for i in range(0, len(imgs), batch):
        x = p(images=list(imgs[i:i + batch]), return_tensors="pt")
        with torch.no_grad():
            v = m.get_image_features(pixel_values=x["pixel_values"].to(dev, dt))
        v = torch.nn.functional.normalize(_vec(v).float(), dim=-1)
        out.append(v.cpu().numpy())
    return np.concatenate(out) if out else np.zeros((0, 768), np.float32)


def _embed_texts_model(texts, model):
    import torch
    m, p, dev, dt = _get(model)
    # SigLIP2 was trained on lower-cased text padded to 64 tokens — same here, or similarities are off
    x = p(text=[t.lower() for t in texts], padding="max_length", max_length=64, truncation=True, return_tensors="pt")
    with torch.no_grad():
        v = m.get_text_features(**{k: v.to(dev) for k, v in x.items()})
    return torch.nn.functional.normalize(_vec(v).float(), dim=-1).cpu().numpy()


# Text vectors are cached on disk: fixed prompts (quality, shot tags) and repeated queries never load the model again.
# Loading transformers + SigLIP2 costs ~8 s per process — it was most of the 17 s a panel AI command took to start.
_TXT = {}


def _txt_cache_path(model):
    from . import CACHE
    return os.path.join(CACHE, "siglip_text_" + model.replace("/", "_") + ".npz")


def _txt_load(model):
    if model in _TXT:
        return _TXT[model]
    d = {}
    try:
        z = np.load(_txt_cache_path(model), allow_pickle=False)
        d = {str(k): v.astype(np.float32) for k, v in zip(z["keys"], z["vecs"])}
    except Exception:
        d = {}
    _TXT[model] = d
    return d


def _txt_save(model):
    d = _TXT.get(model) or {}
    if not d:
        return
    p = _txt_cache_path(model)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        keys = list(d)
        tmp = p + ".tmp.npz"
        np.savez(tmp, keys=np.array(keys), vecs=np.stack([d[k] for k in keys]).astype(np.float16))
        os.replace(tmp, p)
    except Exception:
        pass


def embed_texts(texts, model=MODEL):
    """Unit text vectors (n × dim) — served from the disk cache when every text was embedded before."""
    d = _txt_load(model)
    keys = [t.lower().strip() for t in texts]
    miss = [k for k in dict.fromkeys(keys) if k not in d]
    if miss:
        for k, v in zip(miss, _embed_texts_model(miss, model)):
            d[k] = v.astype(np.float32)
        if len(d) <= 5000:                                  # bounded: typed queries accumulate
            _txt_save(model)
    return np.stack([d[k] for k in keys]).astype(np.float32)


def _units(src, media):
    """{file: [(a, b), …]} — shots from segments.json when present, else the whole usable clip."""
    segs = {}
    try:
        from ..segments import load as _seg_load
        segs = _seg_load(src) or {}             # {file: [{"a","b",…}]}
    except Exception:
        segs = {}
    out = {}
    for f, m in media.items():
        if m.get("status", "ok") != "ok":
            continue
        ss = [(float(s["a"]), float(s["b"])) for s in (segs.get(f) or []) if float(s["b"]) - float(s["a"]) > 0.3]
        dur = float(m.get("dur") or 0)
        out[f] = ss or ([(0.0, dur)] if dur > 0.3 else [])
    return out


def _times(a, b):
    L = b - a
    if L < 2.0:
        return [a + L / 2]
    return [a + L * q for q in (0.2, 0.5, 0.8)]


def paths(src):
    d = os.path.join(src, "_nexys_work", "embed")
    return d, os.path.join(d, "siglip.npz"), os.path.join(d, "siglip.json")


def load_index(src):
    d, npz, man = paths(src)
    if not (os.path.exists(npz) and os.path.exists(man)):
        return None, None
    M = jsonio.load(man, None)
    try:
        Z = np.load(npz)
        return M, {"vec": Z["vec"].astype(np.float32), "unit": Z["unit"]}
    except Exception:
        return None, None


def index(src, force=False, model=MODEL, progress=print):
    if not available():
        progress("embed: chưa có torch + transformers → bỏ qua (pip install torch transformers)")
        return None
    from . import frames as _fr
    W = os.path.join(src, "_nexys_work")
    media = jsonio.load(os.path.join(W, "media.json"), {}) or {}
    units = _units(src, media)
    d, npz, man = paths(src)
    os.makedirs(d, exist_ok=True)
    M, Z = (None, None) if force else load_index(src)
    if M and (M.get("model") != model or M.get("v") != EMB_V):
        M, Z = None, None
    old = {}                                   # file → (units, vecs, unit idx) reused when key + shots are unchanged
    if M and Z is not None:
        for f, e in M.get("clips", {}).items():
            idx = [i for i, u in enumerate(M["units"]) if u[0] == f]
            if idx:
                sel = np.isin(Z["unit"], idx)
                old[f] = (e, [M["units"][i] for i in idx], Z["vec"][sel], Z["unit"][sel], idx)
    todo = [f for f, us in units.items() if us and not (f in old and old[f][0].get("key") == media[f].get("key")
                                                        and [tuple(u[1:3]) for u in old[f][1]] == [tuple(map(lambda x: round(x, 2), u)) for u in us])]
    progress(json.dumps({"stage": "embed", "total": len(todo), "cached": len(units) - len(todo)}))
    all_units, vecs, uidx, clips = [], [], [], {}

    def add(f, us, V, U_local):
        base = len(all_units)
        all_units.extend([[f, round(a, 2), round(b, 2)] for a, b in us])
        vecs.append(V)
        uidx.append(np.asarray(U_local, np.int32) + base)
        clips[f] = {"key": media[f].get("key")}

    for f, (e, us_old, V, U, idx) in old.items():
        if f in units and f not in todo:
            remap = {g: k for k, g in enumerate(idx)}
            add(f, [(u[1], u[2]) for u in us_old], V, [remap[int(x)] for x in U])
    for k, f in enumerate(todo, 1):
        us = units[f]
        ts, owner = [], []
        for j, (a, b) in enumerate(us):
            for t in _times(a, b):
                ts.append(t); owner.append(j)
        imgs = []
        for i in range(0, len(ts), 12):                  # ≤ 12 seeks per ffmpeg call
            imgs += _fr.at(os.path.join(src, f), ts[i:i + 12], SIZE) or []
        if len(imgs) != len(ts):
            progress(f"[{k}/{len(todo)}] {f} lỗi đọc khung — bỏ qua")
            continue
        V = embed_images(imgs, model)
        add(f, us, V, owner)
        progress(f"[{k}/{len(todo)}] {f} · {len(us)} đoạn · {len(imgs)} khung")
    if not vecs:
        return None
    vec = np.concatenate(vecs).astype(np.float16)
    unit = np.concatenate(uidx)
    np.savez(npz, vec=vec, unit=unit)
    jsonio.save_atomic(man, {"v": EMB_V, "model": model, "units": all_units, "clips": clips})
    progress(f"embed: {len(all_units)} đoạn · {len(vec)} khung trong chỉ mục")
    return len(all_units)


def _unit_scores(Z, qv, n_units):
    s = Z["vec"] @ qv                               # frames × queries
    best = np.full((n_units, qv.shape[1]), -1.0, np.float32)
    np.maximum.at(best, Z["unit"], s)
    return best


def score_texts(src, texts, model=None):
    """→ (units [[file, a, b]], scores units × texts) or (None, None) without an index / AI pack."""
    if not available():
        return None, None
    M, Z = load_index(src)
    if not M:
        return None, None
    qv = embed_texts(texts, model or M.get("model", MODEL)).T
    return M["units"], _unit_scores(Z, qv, len(M["units"]))


def search(src, query, k=20):
    units, S = score_texts(src, [query])
    if units is None:
        return []
    order = np.argsort(-S[:, 0])[:k]
    return [{"file": units[i][0], "a": units[i][1], "b": units[i][2], "score": round(float(S[i, 0]), 4)} for i in order]


def thumb(src, hit, size=224):
    """Cached JPEG of a hit's middle frame (_nexys_work/embed/thumbs) → absolute path or ''."""
    import hashlib
    d = os.path.join(src, "_nexys_work", "embed", "thumbs")
    os.makedirs(d, exist_ok=True)
    t = round((float(hit["a"]) + float(hit["b"])) / 2, 1)
    p = os.path.join(d, hashlib.md5(f"{hit['file']}@{t}".encode("utf-8")).hexdigest()[:16] + ".jpg")
    if not os.path.exists(p):
        from . import frames as _fr
        im = _fr.at(os.path.join(src, hit["file"]), [t], size)
        if not im:
            return ""
        import cv2
        cv2.imwrite(p, cv2.cvtColor(im[0], cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 82])
    return p.replace("\\", "/")


def dups(src, thr=0.95):
    """Near-identical shots across clips (re-takes): mean embedding per shot, cosine ≥ thr."""
    M, Z = load_index(src)
    if not M:
        return []
    n = len(M["units"])
    mean = np.zeros((n, Z["vec"].shape[1]), np.float32)
    np.add.at(mean, Z["unit"], Z["vec"])
    mean /= np.linalg.norm(mean, axis=1, keepdims=True) + 1e-9
    S = mean @ mean.T
    out = []
    for i in range(n):
        for j in np.nonzero(S[i, i + 1:] >= thr)[0] + i + 1:
            if M["units"][i][0] != M["units"][j][0]:
                out.append({"a": M["units"][i], "b": M["units"][int(j)], "sim": round(float(S[i, j]), 4)})
    return sorted(out, key=lambda r: -r["sim"])


def serve():
    """Long-lived search worker for the panel: the model loads ONCE, then each query costs milliseconds.
    stdin: one JSON per line {"id", "src", "q" | "dups", "k", "thr"} → stdout: "HIT/DUP {json}" lines then "END {id}".
    The index is re-read when its file changes (a new 'Lập chỉ mục' run)."""
    cache = {}

    def idx(src):
        _, npz, _ = paths(src)
        mt = os.path.getmtime(npz) if os.path.exists(npz) else 0
        if cache.get(src, (None, -1))[1] != mt:
            cache[src] = (load_index(src), mt)
        return cache[src][0]

    try:                                           # load the model before READY: the first (uncached) query is fast too
        _get()
    except Exception as e:                         # noqa: BLE001
        print("ERR " + json.dumps({"id": "", "msg": f"không nạp được SigLIP2: {str(e)[:160]}"}, ensure_ascii=False), flush=True)
    print("READY", flush=True)
    for line in sys.stdin:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        rid, src = r.get("id", ""), r.get("src", "")
        if r.get("embed"):                         # pre-embed texts for another process (aiedit resolve reads the disk cache)
            try:
                embed_texts([str(t) for t in r["embed"]])
            except Exception as e:                 # noqa: BLE001
                print("ERR " + json.dumps({"id": rid, "msg": str(e)[:200]}, ensure_ascii=False), flush=True)
            print("END " + json.dumps({"id": rid}), flush=True)
            continue
        try:
            M, Z = idx(src)
            if not M:
                print("ERR " + json.dumps({"id": rid, "msg": "chưa có chỉ mục — bấm Lập chỉ mục"}, ensure_ascii=False), flush=True)
            elif r.get("dups"):
                for d in dups(src, float(r.get("thr", 0.95)))[:int(r.get("k", 40))]:
                    d["thumb_a"] = thumb(src, {"file": d["a"][0], "a": d["a"][1], "b": d["a"][2]})
                    d["thumb_b"] = thumb(src, {"file": d["b"][0], "a": d["b"][1], "b": d["b"][2]})
                    print("DUP " + json.dumps(d, ensure_ascii=False), flush=True)
            else:
                qv = embed_texts([r.get("q", "")], M.get("model", MODEL)).T
                S = _unit_scores(Z, qv, len(M["units"]))[:, 0]
                for i in np.argsort(-S)[:int(r.get("k", 24))]:
                    h = {"file": M["units"][i][0], "a": M["units"][i][1], "b": M["units"][i][2], "score": round(float(S[i]), 4)}
                    h["thumb"] = thumb(src, h)
                    print("HIT " + json.dumps(h, ensure_ascii=False), flush=True)
        except Exception as e:                     # noqa: BLE001 — keep serving
            print("ERR " + json.dumps({"id": rid, "msg": str(e)[:200]}, ensure_ascii=False), flush=True)
        print("END " + json.dumps({"id": rid}), flush=True)
    return 0


def main():
    for s_ in (sys.stdout, sys.stderr):
        try:
            s_.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("index"); a.add_argument("src"); a.add_argument("--force", action="store_true"); a.add_argument("--model", default=MODEL)
    b = sp.add_parser("search"); b.add_argument("src"); b.add_argument("query"); b.add_argument("--k", type=int, default=20)
    b.add_argument("--thumbs", action="store_true", help="kèm ảnh JPEG khung giữa của mỗi kết quả")
    c = sp.add_parser("dups"); c.add_argument("src"); c.add_argument("--thr", type=float, default=0.95)
    c.add_argument("--k", type=int, default=40); c.add_argument("--thumbs", action="store_true")
    sp.add_parser("serve")
    x = ap.parse_args()
    if x.cmd == "serve":
        return serve()
    if x.cmd == "index":
        index(x.src, x.force, x.model, progress=lambda m: print(m, flush=True))
    elif x.cmd == "search":
        M, _ = load_index(x.src)
        if not M:
            print("embed: chưa có chỉ mục — bấm 'Lập chỉ mục' (hoặc chạy Mô tả tự động) trước", flush=True)
        for h in search(x.src, x.query, x.k):
            if x.thumbs:
                h["thumb"] = thumb(x.src, h)
            print("HIT " + json.dumps(h, ensure_ascii=False), flush=True)
    else:
        for r in dups(x.src, x.thr)[:x.k]:
            if x.thumbs:
                r["thumb_a"] = thumb(x.src, {"file": r["a"][0], "a": r["a"][1], "b": r["a"][2]})
                r["thumb_b"] = thumb(x.src, {"file": r["b"][0], "a": r["b"][1], "b": r["b"][2]})
            print("DUP " + json.dumps(r, ensure_ascii=False), flush=True)
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
