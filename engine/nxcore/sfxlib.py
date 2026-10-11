"""SFX library index: role (whoosh/riser/hit/pop/ambience/glitch/meme/accent), style, duration, loudness and the
time of the sound's peak (so a whoosh can be placed with its peak exactly on the cut).

Roles come from folder names (works with any library laid out by category, e.g. "03. WHOOSHES", "10. IMPACT")
and are refined by the envelope shape (long rising envelope => riser, short transient => pop/hit).

CLI: python -m nxcore.sfxlib "D:/.../20. Sound FX" [more roots…] --out sfx_index.json [--force]
"""
import argparse
import json
import os
import re
import sys
import unicodedata

import numpy as np

from .audiolib import AUDIO_EXT, db, decode, file_key, rms_env

# folder keyword -> (role, style)
FOLDER_MAP = [
    (r"whoosh|swoosh|swish|transition|pass ?by|swipe", "whoosh", "clean"),
    (r"riser|rise|uplift|build ?up|tension", "riser", "cinematic"),
    (r"sub ?drop|subdrop|boom|bass ?drop|downlifter", "hit", "cinematic"),
    (r"impact|hit|punch|slam|thud", "hit", "clean"),
    (r"cinematic|trailer|braam", "hit", "cinematic"),
    (r"stomp|clap", "accent", "hard"),
    (r"bass", "hit", "hard"),
    (r"aggressive|rock|metal", "accent", "hard"),
    (r"percussion|drum|perc", "accent", "clean"),
    (r"click|button|tap|ui|interface|simple|bubble|pop|notification|ding|bell|chime", "pop", "clean"),
    (r"glitch|cyber|data|digital|strobe|tech|electric|static", "glitch", "tech"),
    (r"cartoon|game|meme|funny|comic|8.?bit|arcade", "meme", "fun"),
    (r"ambien|ambience|atmos|room ?tone|nature|crowd|city|cafe|coffee|rain|wind|forest|street", "ambience", "natural"),
    (r"corporate|logo|intro|sting", "accent", "clean"),
]

TARGET_DB = {  # target short-term RMS (dBFS) when placed; ambience sits far under dialogue/music
    "whoosh": -24, "riser": -23, "hit": -18, "pop": -26, "glitch": -26, "meme": -22, "accent": -22, "ambience": -36,
}


def _fold(s):
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d")


def folder_role(relpath):
    parts = [_fold(p) for p in re.split(r"[\\/]", relpath)]
    for part in reversed(parts[:-1] or parts):
        for pat, role, style in FOLDER_MAP:
            if re.search(pat, part):
                return role, style
    name = parts[-1]
    for pat, role, style in FOLDER_MAP:
        if re.search(pat, name):
            return role, style
    return "accent", "clean"


def describe(path, max_sec=30):
    x = decode(path, max_sec=max_sec)
    dur_decoded = len(x) / 22050.0
    env = rms_env(x, win_ms=10)                      # 100 Hz envelope
    if env.max() <= 1e-6:
        return None
    e_db = db(env)
    pk = int(np.argmax(env))
    active = env > env.max() * 0.1
    act_idx = np.nonzero(active)[0]
    start = int(act_idx[0]) if len(act_idx) else 0
    end = int(act_idx[-1]) if len(act_idx) else len(env) - 1
    attack = (pk - start) / 100.0
    decay = (end - pk) / 100.0
    loud = float(db(np.sqrt(np.mean(env[active] ** 2)))) if active.any() else float(e_db.max())
    return {
        "dur": round(dur_decoded, 3), "peak_t": round(pk / 100.0, 3), "start_t": round(start / 100.0, 3),
        "end_t": round(end / 100.0, 3), "attack": round(attack, 3), "decay": round(decay, 3),
        "loud_db": round(loud, 1), "peak_db": round(float(e_db.max()), 1),
    }


def refine_role(role, d):
    if d is None:
        return role
    if role in ("whoosh", "hit", "accent", "glitch") and d["attack"] >= 1.2 and d["peak_t"] >= 0.6 * d["dur"]:
        return "riser"                              # long build-up that peaks late
    if role == "hit" and d["dur"] < 0.5:
        return "pop"
    if role == "ambience" and d["dur"] < 2.0:
        return "accent"
    return role


def index(roots, out, force=False, progress=print):
    idx = {}
    if os.path.exists(out) and not force:
        try:
            idx = json.load(open(out, encoding="utf-8")).get("files", {})
        except Exception:
            idx = {}
    files = []
    for root in roots:
        for d, _, fs in os.walk(root):
            for f in fs:
                if f.lower().endswith(AUDIO_EXT):
                    full = os.path.join(d, f)
                    files.append((root, full))
    todo = [(r, f) for r, f in files if idx.get(f, {}).get("key") != file_key(f)]
    progress(json.dumps({"stage": "sfx", "total": len(todo), "cached": len(files) - len(todo)}))
    for k, (root, f) in enumerate(todo, 1):
        rel = os.path.relpath(f, root)
        role, style = folder_role(rel)
        try:
            d = describe(f)
        except Exception as e:
            progress(f"[{k}/{len(todo)}] {rel} lỗi: {e}")
            continue
        if d is None:
            continue
        role = refine_role(role, d)
        nm = _fold(os.path.basename(f))
        if re.search(r"glitch|cyber|data|digital|static|stutter", nm):
            style = "tech"
        elif re.search(r"cartoon|funny|comic|meme|boing", nm):
            style = "fun"
        elif re.search(r"cinematic|trailer|braam|epic", nm):
            style = "cinematic"
        idx[f] = dict(d, key=file_key(f), rel=rel.replace("\\", "/"), category=re.split(r"[\\/]", rel)[0] if os.sep in rel or "/" in rel else "",
                      role=role, style=style)
        if k % 25 == 0 or k == len(todo):
            progress(f"[{k}/{len(todo)}] {rel} → {role}/{style}")
            _save(idx, roots, out)
    idx = {k: v for k, v in idx.items() if os.path.exists(k)}
    return _save(idx, roots, out)


def _save(idx, roots, out):
    roles = {}
    for v in idx.values():
        roles[v["role"]] = roles.get(v["role"], 0) + 1
    data = {"roots": [os.path.abspath(r) for r in roots], "count": len(idx), "roles": roles, "files": idx}
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    json.dump(data, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    return data


def main():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    data = index(a.roots, a.out, a.force, progress=lambda m: print(m, flush=True))
    print("sfx index: %d file · %s" % (data["count"], ", ".join(f"{k} {v}" for k, v in sorted(data["roles"].items(), key=lambda x: -x[1]))))
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
