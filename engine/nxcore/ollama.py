"""One client for the local Ollama server — every engine module talks to it through here.

Self-healing: a runner loaded while VRAM was crowded can sit half in Windows' shared GPU memory — Ollama still reports
"100% GPU" but reads prompts ~25× slower (measured 92 vs 2 350 tokens/s on an 8 GB card). The first slow answer of a
model unloads it once, so the next call loads it fresh into the VRAM that is free now. Before this module only the
footage re-rank had that check; clip descriptions (hundreds of calls per folder) did not.
"""
import json
import os
import sys
import urllib.request

HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
if not HOST.startswith("http"):
    HOST = "http://" + HOST
SLOW_TOKS = 300                 # prompt tokens / s below this (for ≥ 400 tokens) = degraded runner
_HEALED = set()


def post(path, body, timeout=180, host=None):
    req = urllib.request.Request((host or HOST) + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def tags(host=None, timeout=4):
    """Installed model names (raises when Ollama is not running)."""
    with urllib.request.urlopen((host or HOST) + "/api/tags", timeout=timeout) as r:
        return [m.get("name", "") for m in json.loads(r.read().decode("utf-8")).get("models", [])]


def ps(host=None, timeout=4):
    """Loaded models: [{"name", "size", "size_vram"}] in bytes ([] when none / Ollama is not running)."""
    try:
        with urllib.request.urlopen((host or HOST) + "/api/ps", timeout=timeout) as r:
            ms = json.loads(r.read().decode("utf-8")).get("models") or []
    except Exception:
        return []
    return [{"name": m.get("name") or m.get("model"), "size": int(m.get("size") or 0), "size_vram": int(m.get("size_vram") or 0)} for m in ms]


def placement(model, host=None):
    """→ (share of the model in VRAM 0..1, MB left on the CPU) for a loaded model, or None. Ollama loads into the
    VRAM free at that moment and puts the rest of the layers on the CPU without a word (seen: 35/37 layers,
    5.3 of 5.9 GB while Premiere held the card) — answers then come several times slower."""
    for m in ps(host):
        if m["name"] == model and m["size"]:
            return m["size_vram"] / m["size"], (m["size"] - m["size_vram"]) // 2 ** 20
    return None


_WARNED = set()


def _warn_cpu(model, host):
    if model in _WARNED:
        return
    _WARNED.add(model)
    pl = placement(model, host)
    if pl and pl[0] < 0.97:
        sys.stderr.write(f"⚠ Ollama: {model} chỉ {pl[0] * 100:.0f}% nằm trên GPU ({pl[1]} MB chạy bằng CPU — chậm hơn nhiều). "
                         "VRAM trống không đủ: đóng bớt ứng dụng dùng GPU (Premiere, trình duyệt, game) rồi chạy lại.\n")
        sys.stderr.flush()


def unload(model, host=None):
    try:
        post("/api/generate", {"model": model, "keep_alive": 0}, 30, host)
    except Exception:
        pass


def _heal(model, r, host):
    n, dt = r.get("prompt_eval_count") or 0, (r.get("prompt_eval_duration") or 0) / 1e9
    if n >= 400 and dt > 0 and n / dt < SLOW_TOKS and model not in _HEALED:
        _HEALED.add(model)
        unload(model, host)


def chat(model, content, images=None, system=None, fmt=None, num_ctx=4096, temperature=0.2, num_predict=None,
         keep_alive="5m", timeout=180, host=None, raw=False):
    """→ the answer text (or the whole reply dict with raw=True). fmt: "json" or a JSON schema."""
    msgs = ([{"role": "system", "content": system}] if system else []) + \
           [dict({"role": "user", "content": content}, **({"images": images} if images else {}))]
    opts = {"num_ctx": num_ctx, "temperature": temperature}
    if num_predict is not None:
        opts["num_predict"] = num_predict
    body = {"model": model, "stream": False, "think": False, "keep_alive": keep_alive, "options": opts, "messages": msgs}
    if fmt is not None:
        body["format"] = fmt
    r = post("/api/chat", body, timeout, host)
    _heal(model, r, host)
    _warn_cpu(model, host)
    return r if raw else r.get("message", {}).get("content", "")
