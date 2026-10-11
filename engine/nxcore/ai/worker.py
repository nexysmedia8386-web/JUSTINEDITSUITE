"""Shared AI worker for the panel — ONE long-lived Python process for every interactive AI request.

Before: footage search had its own worker and every AI-edit command started a new Python process (≈ 0.3 s start-up
each, Material rebuilt each time, a second SigLIP2 copy in VRAM whenever a command needed the model). Now:
  · SigLIP2 is loaded once (in the background right after start, so READY is immediate)
  · the source folder's Material (nxcore.aiedit.material) stays in memory until its analysis files change
  · search / look-alike / pre-embedding / every nxcore.aiedit command go through the same JSON-lines channel

Protocol (stdin → stdout, one JSON object per line):
  request   {"id": "r7", "op": "search"|"sounds"|"dups"|"rank"|"embed"|"aiedit"|"ping", …}
            {"op": "cancel", "target": "r7"}          stops a running / queued request (handled at once)
  replies   "READY"                                  once, at start
            "LOG {"id", "msg"}"                       progress / anything a handler prints
            "HIT {…}" / "DUP {…}" / "RANK {…}"        streamed rows (search / dups / vision re-rank)
            "RES {"id", "data"}"                      the result object (aiedit, ping, rank)
            "ERR {"id", "msg"}"                       the request failed (the worker keeps running)
            "END {"id"}"                              always last for a request
Requests are handled one at a time, in order; a reader thread keeps listening so a cancel reaches a long request
(a vision re-rank takes ~5 s per shot on an 8 GB card).

  python -m nxcore.ai.worker
"""
import io
import json
import os
import sys
import threading
import traceback

import numpy as np

_OUT = sys.stdout
_LOCK = threading.Lock()


def emit(kind, obj=None):
    line = kind if obj is None else kind + " " + json.dumps(obj, ensure_ascii=False)
    with _LOCK:
        _OUT.write(line + "\n")
        _OUT.flush()


class _LogStream(io.TextIOBase):
    """While a request runs, anything printed (engine progress, montage output…) becomes LOG lines of that request,
    so it can never corrupt the protocol."""

    def __init__(self, rid):
        self.rid, self.buf = rid, ""

    def write(self, s):
        self.buf += str(s)
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                emit("LOG", {"id": self.rid, "msg": line[:500]})
        return len(s)

    def flush(self):
        pass


# ---------------------------------------------------------------- handlers
def _index(src, cache={}):
    from .embed import load_index, paths
    _, npz, _ = paths(src)
    mt = os.path.getmtime(npz) if os.path.exists(npz) else 0
    if cache.get(src, (None, -1))[1] != mt:
        cache[src] = (load_index(src), mt)
    return cache[src][0]


def h_search(r):
    from . import embed as E
    if not E.available():
        raise RuntimeError("chưa có gói AI (Cài đặt → Cài gói AI)")
    src = r["src"]
    M, Z = _index(src)
    if not M:
        raise RuntimeError("chưa có chỉ mục — bấm Lập chỉ mục")
    qv = E.embed_texts([r.get("q", "")], M.get("model", E.MODEL)).T
    S = E._unit_scores(Z, qv, len(M["units"]))[:, 0]
    for i in np.argsort(-S)[:int(r.get("k", 24))]:
        h = {"file": M["units"][i][0], "a": M["units"][i][1], "b": M["units"][i][2], "score": round(float(S[i]), 4)}
        h["thumb"] = E.thumb(src, h)
        emit("HIT", h)


def h_sounds(r):
    """C2: search by sound (PANNs AudioSet tags) — HIT rows like a picture search, plus the sound labels found."""
    from . import embed as E, sounds as SND
    src = r["src"]
    if not SND.load_index(src)[0]:
        raise RuntimeError("chưa có chỉ mục âm thanh — bấm Lập chỉ mục")
    for h in SND.search(src, r.get("q", ""), int(r.get("k", 24))):
        h["thumb"] = E.thumb(src, {"file": h["file"], "a": h["t"], "b": h["t"]})
        emit("HIT", h)


def h_dups(r):
    from . import embed as E
    src = r["src"]
    if not _index(src)[0]:
        raise RuntimeError("chưa có chỉ mục — bấm Lập chỉ mục")
    for d in E.dups(src, float(r.get("thr", 0.95)))[:int(r.get("k", 40))]:
        d["thumb_a"] = E.thumb(src, {"file": d["a"][0], "a": d["a"][1], "b": d["a"][2]})
        d["thumb_b"] = E.thumb(src, {"file": d["b"][0], "a": d["b"][1], "b": d["b"][2]})
        emit("DUP", d)


_CANCEL = set()


def cancelled(rid):
    return rid in _CANCEL


def h_rank(r):
    """B2: the vision LLM looks at the best SigLIP hits of a search and re-orders them. RANK rows stream as each
    shot is scored; RES = the new order. Stops between shots when cancelled (a new search was typed)."""
    from . import rerank as RR
    rid = r.get("id", "")
    try:                                         # SigLIP keeps its weights, but hand the cached VRAM to Ollama
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    hits = [dict(h) for h in r.get("hits") or []]
    t0 = __import__("time").time()
    out = RR.rerank(r["src"], r.get("q", ""), hits, r.get("model") or "auto", k=int(r.get("k", 12)),
                    log=print, on_row=lambda h: emit("RANK", h), stop=lambda: cancelled(rid))
    return {"order": [[h["file"], h["a"], h["b"]] for h in out], "secs": round(__import__("time").time() - t0, 1),
            "cancelled": cancelled(rid)}


def h_embed(r):
    from .embed import embed_texts
    embed_texts([str(t) for t in r.get("texts") or []])         # fills the shared on-disk text cache


def h_aiedit(r):
    from .. import aiedit
    return aiedit.run_cmd(r["cmd"], r["plan"], r.get("ops"), bool(r.get("thumbs")), r.get("want") or "reframe,color,stabilize",
                          log=print)


def h_ping(r):
    from . import embed as E
    from . import _models
    return {"pid": os.getpid(), "model_loaded": any(isinstance(k, tuple) and k[0] == "siglip" for k in _models),
            "ai": E.available()}


HANDLERS = {"search": h_search, "sounds": h_sounds, "dups": h_dups, "rank": h_rank, "embed": h_embed, "aiedit": h_aiedit,
            "ping": h_ping}


def _warm():
    """Load SigLIP2 in the background: READY goes out at once, the first search does not wait ~8 s."""
    try:
        from . import embed as E
        if E.available():
            E._get()
    except Exception:
        pass


def main():
    global _OUT
    for s_ in (sys.stdout, sys.stderr):
        try:
            s_.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    _OUT = sys.stdout
    threading.Thread(target=_warm, daemon=True).start()
    import queue
    inbox = queue.Queue()

    def reader():                                # cancels must get through while a long request runs
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("op") == "cancel":
                _CANCEL.add(str(r.get("target", "")))
            else:
                inbox.put(r)
        inbox.put(None)

    threading.Thread(target=reader, daemon=True).start()
    emit("READY")
    while True:
        r = inbox.get()
        if r is None:
            break
        rid = r.get("id", "")
        if rid in _CANCEL:                       # cancelled while still queued
            emit("END", {"id": rid})
            continue
        fn = HANDLERS.get(r.get("op"))
        old = sys.stdout
        sys.stdout = _LogStream(rid)
        try:
            if fn is None:
                raise ValueError(f"op lạ: {r.get('op')}")
            data = fn(r)
            if data is not None:
                emit("RES", {"id": rid, "data": data})
        except Exception as e:                       # noqa: BLE001 — reported, the worker keeps serving
            emit("ERR", {"id": rid, "msg": str(e)[:300] or e.__class__.__name__,
                         "trace": traceback.format_exc(limit=3)[-600:]})
        finally:
            sys.stdout.flush()
            sys.stdout = old
            emit("END", {"id": rid})
    return 0


if __name__ == "__main__":
    sys.exit(main())
