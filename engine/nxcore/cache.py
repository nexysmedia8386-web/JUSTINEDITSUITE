"""Cache manager for a source folder's _nexys_work (+ the panel's global caches).

  python -m nxcore.cache SRC                      # list categories + sizes (JSON line "CACHE {...}")
  python -m nxcore.cache SRC --clear heavy        # proxy + thumbs + audio (safe, rebuilt on demand)
  python -m nxcore.cache SRC --clear analysis,ai  # comma list; "all" = everything except editor decisions
  python -m nxcore.cache SRC --clear all --decisions   # also AI scripts, b-roll tags, triage choices

Editor decisions (broll_tags.json, triage.json, auto_script_*.md) are never removed unless --decisions is given.
Outputs in _nexys_out (plans, XML) are never touched.
"""
import argparse
import glob
import json
import os
import shutil
import sys

CATS = {
    "proxy":    {"label": "Proxy 360p (AI xem video)", "paths": ["proxy"], "heavy": True},
    "thumbs":   {"label": "Contact sheet / thumbnail", "paths": ["thumbs", "preview", "fthumbs"], "heavy": True},
    "audio":    {"label": "WAV tạm / sync", "paths": ["audio", "sync", "*.wav"], "heavy": True},
    "analysis": {"label": "Kết quả quét (probe, QC, rung, transcript, VAD, vai trò, lọc thoại, tách đoạn)",
                 "paths": ["media.json", "media_all.json", "qc.json", "shake.json", "shake_v*_backup.json", "sync.json",
                           "transcripts", "transcripts_all.txt", "vad.json", "roles.json", "speech.json", "segments.json"]},
    "ai":       {"label": "Phân tích AI + upload Gemini", "paths": ["source_ai.json", "gemini_files.json", "source_check.json", "auto_desc.json",
                                                              "speech_llm_cache.json"]},
    "decisions": {"label": "Quyết định của editor (tag B-roll, triage, kịch bản AI)",
                  "paths": ["broll_tags.json", "triage.json", "auto_script_*.md", "auto_script_*.md.bak"], "keep": True},
}
GROUPS = {"heavy": ["proxy", "thumbs", "audio"], "all": ["proxy", "thumbs", "audio", "analysis", "ai"]}


def work(src):
    return os.path.join(src, "_nexys_work")


def _size(p):
    if os.path.isfile(p):
        return os.path.getsize(p)
    t = 0
    for r, _, fs in os.walk(p):
        for f in fs:
            try:
                t += os.path.getsize(os.path.join(r, f))
            except OSError:
                pass
    return t


def _match(src, cat):
    w = work(src)
    out = []
    for pat in CATS[cat]["paths"]:
        out += [p for p in glob.glob(os.path.join(w, pat)) if os.path.exists(p)]
    return sorted(set(out))


def listing(src):
    res, total = {}, 0
    for c, d in CATS.items():
        ps = _match(src, c)
        sz = sum(_size(p) for p in ps)
        res[c] = {"label": d["label"], "bytes": sz, "items": len(ps), "heavy": bool(d.get("heavy")), "keep": bool(d.get("keep"))}
        total += sz
    return {"src": src, "total": total, "cats": res}


def clear(src, cats, decisions=False):
    want = []
    for c in cats:
        want += GROUPS.get(c, [c])
    if decisions:
        want.append("decisions")
    freed, removed = 0, []
    for c in dict.fromkeys(want):
        if c not in CATS or (CATS[c].get("keep") and not decisions):
            continue
        for p in _match(src, c):
            sz = _size(p)
            try:
                shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
                freed += sz
                removed.append(os.path.relpath(p, work(src)))
            except OSError as e:
                print(f"  ⚠ không xóa được {p}: {e}")
    return freed, removed


def fmt(b):
    for u in ("B", "KB", "MB", "GB"):
        if b < 1024 or u == "GB":
            return f"{b:.0f} {u}" if u in ("B", "KB") else f"{b:.1f} {u}"
        b /= 1024.0


def main():
    for s_ in (sys.stdout, sys.stderr):
        try:
            s_.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--clear", default="")
    ap.add_argument("--decisions", action="store_true")
    a = ap.parse_args()
    if not os.path.isdir(work(a.src)):
        print("CACHE " + json.dumps({"src": a.src, "total": 0, "cats": {}}, ensure_ascii=False))
        print("DONE")
        return
    if a.clear:
        freed, removed = clear(a.src, [c.strip() for c in a.clear.split(",") if c.strip()], a.decisions)
        print(f"✔ Đã xóa {len(removed)} mục · giải phóng {fmt(freed)}")
    L = listing(a.src)
    for c, d in L["cats"].items():
        if d["items"]:
            print(f"  {c:9s} {fmt(d['bytes']):>9s}  {d['label']}")
    print("CACHE " + json.dumps(L, ensure_ascii=False))
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
