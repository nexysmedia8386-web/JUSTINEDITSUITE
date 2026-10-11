"""Word timings re-aligned on the audio with a Vietnamese wav2vec2 CTC model (forced alignment, WhisperX-style).

Whisper's word timestamps come from cross-attention and drift by 0.1–0.5 s (more after silences); cutting a talking
head on them clips first/last syllables. A CTC acoustic model aligned to the KNOWN text places every word where its
sounds are. Model: dragonSwing/wav2vec2-base-vietnamese (Apache-2.0 — the more popular nguyenvulebinh/* models are
CC-BY-NC, not usable in a commercial tool). Other languages: NX_ALIGN_MODEL_<LANG>=<hf id> (CTC model).

    refine(audio16k, segments, lang="vi") → segments with "words" re-timed + "aligned": "w2v"
    python -m nxcore.ai.align SRC        → re-align every transcript of a source folder in place
"""
import os
import re
import sys
import unicodedata

import numpy as np

from . import cached, device, enabled, has, torch_ok

MODELS = {"vi": "dragonSwing/wav2vec2-base-vietnamese"}
SR = 16000
PAD = 0.35                 # audio context around each segment (s)
MAX_SHIFT = 1.0            # an aligned word farther than this from whisper's time is distrusted (kept as whisper)
TAG = "w2v"


def model_id(lang):
    return os.environ.get(f"NX_ALIGN_MODEL_{(lang or '').upper()}") or MODELS.get(lang or "")


def available(lang="vi"):
    return enabled("align") and torch_ok() and has("transformers") and bool(model_id(lang))


def _load(mid):
    import torch
    from transformers import AutoModelForCTC, AutoProcessor
    dev = device()
    m = AutoModelForCTC.from_pretrained(mid).to(dev).eval()
    p = AutoProcessor.from_pretrained(mid)
    vocab = p.tokenizer.get_vocab()
    return m, p, dev, vocab, torch


def _norm(w):
    w = unicodedata.normalize("NFC", w.lower())
    return re.sub(r"[^\w]", "", w)


def _viterbi(lp, tokens, blank):
    """CTC forced alignment. lp: T×V log-probs, tokens: target ids → per-token [first_frame, last_frame] or None."""
    T, S = lp.shape[0], 2 * len(tokens) + 1
    if T < len(tokens):
        return None
    ext = np.full(S, blank, np.int64)
    ext[1::2] = tokens
    NEG = -1e30
    dp = np.full((T, S), NEG, np.float64)
    bp = np.zeros((T, S), np.int8)
    dp[0, 0] = lp[0, blank]
    dp[0, 1] = lp[0, ext[1]]
    skip_ok = np.zeros(S, bool)
    skip_ok[3::2] = ext[3::2] != ext[1:-2:2]            # may jump over a blank between two different labels
    emit = lp[:, ext]                                    # T×S
    for t in range(1, T):
        p = dp[t - 1]
        c0 = p
        c1 = np.concatenate([[NEG], p[:-1]])
        c2 = np.where(skip_ok, np.concatenate([[NEG, NEG], p[:-2]]), NEG)
        stack = np.stack([c0, c1, c2])
        k = stack.argmax(0)
        dp[t] = stack[k, np.arange(S)] + emit[t]
        bp[t] = k
    s = S - 1 if dp[-1, S - 1] >= dp[-1, S - 2] else S - 2
    path = np.empty(T, np.int64)
    for t in range(T - 1, -1, -1):
        path[t] = s
        s -= int(bp[t, s])                               # int(): int8 arithmetic would overflow past state 127
    spans = [None] * len(tokens)
    for t, st in enumerate(path):
        if st % 2 == 1:
            i = st // 2
            spans[i] = [t, t] if spans[i] is None else [spans[i][0], t]
    return spans


def refine(audio, segments, lang="vi"):
    """Re-time segment["words"] ([start, end, word, prob]) in place; returns segments."""
    mid = model_id(lang)
    if not segments or not available(lang):
        return segments
    m, p, dev, vocab, torch = cached(("align", mid), lambda: _load(mid))
    blank = vocab.get(p.tokenizer.pad_token, 0)
    sep = vocab.get("|")
    audio = np.asarray(audio, np.float32)
    for seg in segments:
        words = seg.get("words") or []
        if not words:
            continue
        a = max(0.0, float(seg["start"]) - PAD)
        b = min(len(audio) / SR, float(seg["end"]) + PAD)
        x = audio[int(a * SR):int(b * SR)]
        if len(x) < SR // 4:
            continue
        toks, owner = [], []                              # token ids + which word each char belongs to
        for wi, w in enumerate(words):
            n = _norm(str(w[2]))
            ids = [vocab[ch] for ch in n if ch in vocab]
            if not ids:
                continue
            if toks and sep is not None:
                toks.append(sep); owner.append(-1)
            toks += ids; owner += [wi] * len(ids)
        if not toks:
            continue
        inp = p(x, sampling_rate=SR, return_tensors="pt").input_values.to(dev)
        with torch.no_grad():
            lg = m(inp).logits[0].float()
        lp = torch.log_softmax(lg, -1).cpu().numpy()
        spans = _viterbi(lp, np.asarray(toks), blank)
        if spans is None:
            continue
        stride = (b - a) / lp.shape[0]
        got = {}
        for (sp, wi) in zip(spans, owner):
            if wi < 0 or sp is None:
                continue
            g = got.setdefault(wi, [sp[0], sp[1]])
            g[0], g[1] = min(g[0], sp[0]), max(g[1], sp[1])
        moved = 0
        for wi, (f0, f1) in got.items():
            s0, s1 = round(a + f0 * stride, 2), round(a + (f1 + 1) * stride, 2)
            w = words[wi]
            if abs(s0 - float(w[0])) <= MAX_SHIFT and abs(s1 - float(w[1])) <= MAX_SHIFT and s1 > s0:
                w[0], w[1] = s0, s1
                moved += 1
        if moved:
            seg["aligned"] = TAG
    return segments


def main():
    """Re-align all transcripts of a source folder (whisper words → acoustic word times)."""
    for s_ in (sys.stdout, sys.stderr):
        try:
            s_.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    import argparse
    import glob
    import json
    from .. import jsonio
    from ..vad import pcm
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--lang", default="vi")
    ap.add_argument("--force", action="store_true")
    x = ap.parse_args()
    if not available(x.lang):
        print(f"align: chưa dùng được (cần torch + transformers, model cho '{x.lang}')")
        return 1
    td = os.path.join(x.src, "_nexys_work", "transcripts")
    files = [j for j in sorted(glob.glob(os.path.join(td, "*.json"))) if not j.endswith(".v2.json")]
    todo = []
    for j in files:
        d = jsonio.load(j, {}) or {}
        segs = d.get("segments") or []
        if segs and (x.force or not all(s.get("aligned") for s in segs if s.get("words"))):
            todo.append((j, d))
    print(json.dumps({"stage": "align", "total": len(todo)}), flush=True)
    for k, (j, d) in enumerate(todo, 1):
        audio = pcm(os.path.join(x.src, d["file"]))
        refine(audio, d["segments"], x.lang)
        jsonio.save_atomic(j, d)
        n = sum(1 for s in d["segments"] if s.get("aligned"))
        print(f"[{k}/{len(todo)}] {d['file']} · {n}/{len(d['segments'])} câu căn lại", flush=True)
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
