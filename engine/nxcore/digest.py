"""Compact source digest for long-form editing — Justin EDIT Suite.

Why: for a 10–30 min video the AI writer does not need the full analysis JSON + every transcript word. It needs one
line per clip and one short line per spoken block, each with an ID it can reference. The panel later expands those IDs
back to exact quotes and time ranges locally (0 tokens), so the cloud model never re-types dialogue.

  python -m nxcore.digest SRC [--ollama qwen3-vl:8b] [--describe] [--words 14] [--min-quality 45]

Outputs (in _nexys_work):
  digest.txt   what the writer reads, e.g.
      K12 kỳ cf/DSC_1.MOV | 45s | Q78 ĐẠT | cận · barista rót sữa tạo hình
        K12.1 3.2–14.8 "chào mọi người hôm nay mình … tới Hội An" (38 từ)
  digest.json  {"clips": {"K12": {"file", "dur", ...}}, "utts": {"K12.1": {"file", "t0", "t1", "text"}}, "tokens": N}
  digest_cache.json  local Ollama results (per clip key / utterance hash) — reused across runs

Local AI (Ollama, free, optional):
  --describe : clips with no AI description get "≤15 từ" from one mid frame (vision model)
  --ollama   : long spoken blocks (> 2×words) get a ≤15-word gist instead of head/tail words
"""
import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys

from .media import stem

GAP_S = 1.0          # silence that splits two spoken blocks
MAX_BLOCK_S = 25.0   # a block never gets longer than this (keeps IDs precise)


def _j(p, d):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return d


def blocks(segments, labels=None):
    """Whisper segments → spoken blocks [(t0, t1, text)] split at pauses / max length.
    labels (nxcore.speech): only "ok" lines are kept; a junk/fragment line also ends the current block."""
    out, cur = [], None
    for si, s in enumerate(segments or []):
        a, b, t = float(s.get("start", 0)), float(s.get("end", 0)), str(s.get("text", "")).strip()
        if labels and si < len(labels) and labels[si] != "ok":
            if cur:
                out.append(cur); cur = None
            continue
        if not t:
            continue
        if cur and (a - cur[1] <= GAP_S) and (b - cur[0] <= MAX_BLOCK_S):
            cur[1] = b
            cur[2] += " " + t
        else:
            if cur:
                out.append(cur)
            cur = [a, b, t]
    if cur:
        out.append(cur)
    return [(round(a, 2), round(b, 2), re.sub(r"\s+", " ", t)) for a, b, t in out]


def _ollama(model, prompt, images=None, host=None, timeout=120):
    from .ollama import chat
    return chat(model, prompt, images=images, keep_alive="10m", timeout=timeout, host=host).strip()


def _frame_b64(path, t):
    p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                        "-vf", "scale=448:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-"], capture_output=True)
    return base64.b64encode(p.stdout).decode("ascii") if p.returncode == 0 and p.stdout else None


def _short(text, words):
    w = text.split()
    if len(w) <= words * 2:
        return text
    return " ".join(w[:words]) + " … " + " ".join(w[-max(4, words // 2):])


def build(src, ollama=None, describe=False, words=14, min_quality=45, progress=print):
    W = os.path.join(src, "_nexys_work")
    media = _j(os.path.join(W, "media.json"), {})
    stab = _j(os.path.join(W, "shake.json"), {})
    ai = (_j(os.path.join(W, "source_ai.json"), {}) or {}).get("clips", {})
    tags = _j(os.path.join(W, "broll_tags.json"), {})
    from .describe import load as _auto_desc
    auto = _auto_desc(src)                       # local vision descriptions written after the scan
    cp = os.path.join(W, "digest_cache.json")
    cache = _j(cp, {"desc": {}, "gist": {}})
    from .chrono import capture_ts, fmt as _tfmt
    # shooting order (DJI file-name time wins over drifting metadata) — the writer is told the list is chronological
    keys = sorted(media, key=lambda f: (capture_ts(f, media[f]) or 0, f))
    last_day = None
    from . import speech as speech_mod
    speech_mod.analyze(src, ollama, progress=progress)            # junk / fragment filter (cached, optional local LLM check)
    sp = speech_mod.load(src)
    D = {"clips": {}, "utts": {}}
    lines = ["# DIGEST SOURCE — xếp theo THỜI GIAN QUAY; mỗi clip 1 dòng: ID(Kn) giờ quay file | thời lượng | chất lượng + trạng thái | đoạn dùng được | mô tả",
             "# thoại (chỉ câu có nghĩa, đã lọc rác): ID(Kn.m) t0–t1 \"trích\" (số từ) hoặc ~ ý chính — dùng ID để tham chiếu, KHÔNG chép lại lời thoại"]
    n_desc = n_gist = 0
    from difflib import SequenceMatcher
    raw_utts = []
    for f in keys:
        tr = _j(os.path.join(W, "transcripts", stem(f) + ".json"), {})
        lab = sp.get(f) or []
        for t0, t1, text in blocks(tr.get("segments"), lab):
            raw_utts.append({"file": f, "t0": t0, "t1": t1, "text": text})

    # Deduplicate takes: if similarity >= 0.70 within nearby window, mark the last one
    take_tags = {}
    for i in range(len(raw_utts)):
        ti = raw_utts[i]["text"].lower().strip()
        if len(ti.split()) < 3:
            continue
        for j in range(i + 1, min(len(raw_utts), i + 10)):
            tj = raw_utts[j]["text"].lower().strip()
            sim = SequenceMatcher(None, ti, tj).ratio()
            if sim >= 0.70:
                take_tags[i] = "[Take cũ - bỏ qua]"
                take_tags[j] = "[Take CUỐI - ĐẠT]"

    utt_counter = 0
    for i, f in enumerate(keys, 1):
        m, sb = media[f], stab.get(f, {}) or {}
        cid = f"K{i}"
        q = sb.get("quality")
        us = sb.get("usable") or []
        sl = sb.get("usable_slowmo") or []
        longest = max([b - a for a, b in us] + [0])
        st = "ĐẠT"
        if q is not None and q < min_quality:
            st = "LOẠI"
        elif sb.get("usable") is not None and longest < 1.0:
            st = "ĐẠT(slow-mo)" if max([b - a for a, b in sl] + [0]) >= 1.0 else "LOẠI"
        elif q is not None and q < min_quality + 10:
            st = "YẾU"
        a = ai.get(f) or {}
        t = tags.get(f) or {}
        note = (t.get("desc") or "") if isinstance(t, dict) else ""    # editor's own note (panel) wins
        desc = " · ".join(x for x in [a.get("shot", ""), note or a.get("summary", "") or auto.get(f, "") or ", ".join(t.get("tags", []) if isinstance(t, dict) else [])] if x)
        key = f"{f}:{m.get('dur')}:{m.get('size', '')}"
        if not desc and describe and ollama:
            if key in cache["desc"]:
                desc = cache["desc"][key]
            else:
                img = _frame_b64(os.path.join(src, f), float(m.get("dur") or 2) / 2)
                if img:
                    try:
                        desc = _ollama(ollama, "Mô tả khung hình video này bằng tiếng Việt, tối đa 15 từ: cỡ cảnh (toàn/trung/cận), "
                                               "ai/cái gì, đang làm gì, ở đâu. Chỉ trả câu mô tả.", [img])
                        desc = re.sub(r"\s+", " ", desc).strip(" .\"")[:120]
                        cache["desc"][key] = desc
                        n_desc += 1
                    except Exception as e:
                        progress(f"  ⚠ Ollama mô tả {f}: {e}")
            progress(f"[{i}/{len(keys)}] {f} · {desc[:50]}")
        rs = [r for r in us if r[1] - r[0] >= 1.0] or ([r for r in sl if r[1] - r[0] >= 1.0] if st.startswith("ĐẠT(") else [])
        use = (" | dùng " + ",".join(f"{a:.0f}–{b:.0f}" for a, b in sorted(rs, key=lambda r: r[0] - r[1])[:3]) + "s") if rs and st != "LOẠI" else ""
        ts_ = capture_ts(f, m)
        when = _tfmt(ts_) if ts_ else ""
        if when and when[:5] != last_day:            # day separator: vlog story follows the shooting order
            last_day = when[:5]
            lines.append(f"## NGÀY {last_day} (quay theo thứ tự thời gian)")
        lines.append(f"{cid} {when[6:] + ' ' if when else ''}{f} | {float(m.get('dur') or 0):.0f}s | Q{q if q is not None else '?'} {st}{use} | {desc or '(chưa mô tả)'}")
        D["clips"][cid] = {
            "file": f, "dur": m.get("dur"), "quality": q, "status": st, "desc": desc,
            "created": m.get("created"), "created_local": m.get("created_local"), "created_ts": m.get("created_ts", 0),
            "cam": m.get("cam"), "w": m.get("w"), "h": m.get("h"), "fps": m.get("fps"),
            "is_duplicate": m.get("is_duplicate", False)
        }
        tr = _j(os.path.join(W, "transcripts", stem(f) + ".json"), {})
        lab = sp.get(f) or []
        if lab and "ok" not in lab and any(x == "frag" for x in lab):
            lines[-1] += " · có tiếng nói nền"
        for k, (t0, t1, text) in enumerate(blocks(tr.get("segments"), lab), 1):
            uid = f"{cid}.{k}"
            nw = len(text.split())
            ttag = take_tags.get(utt_counter, "")
            utt_counter += 1
            ttag_str = f" {ttag}" if ttag else ""
            D["utts"][uid] = {"file": f, "t0": t0, "t1": t1, "text": text, "take_tag": ttag}
            if ollama and nw > words * 2:
                h = hashlib.md5((ollama + text).encode("utf-8")).hexdigest()[:16]
                g = cache["gist"].get(h)
                if not g:
                    try:
                        g = _ollama(ollama, "Tóm ý chính đoạn lời nói sau trong tối đa 15 từ tiếng Việt, chỉ trả câu tóm tắt:\n" + text)
                        g = re.sub(r"\s+", " ", g).strip(" .\"")[:140]
                        cache["gist"][h] = g
                        n_gist += 1
                    except Exception:
                        g = None
                lines.append(f"  {uid} {t0:.1f}–{t1:.1f} ~ {g} ({nw} từ){ttag_str}" if g else f"  {uid} {t0:.1f}–{t1:.1f} \"{_short(text, words)}\" ({nw} từ){ttag_str}")
            else:
                lines.append(f"  {uid} {t0:.1f}–{t1:.1f} \"{_short(text, words)}\" ({nw} từ){ttag_str}")
    txt = "\n".join(lines) + "\n"
    D["tokens"] = int(len(txt) / 2.6)          # Vietnamese ≈ 2.5–3 chars / token
    D["n_clips"], D["n_utts"] = len(D["clips"]), len(D["utts"])
    open(os.path.join(W, "digest.txt"), "w", encoding="utf-8").write(txt)
    json.dump(D, open(os.path.join(W, "digest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if n_desc or n_gist:
        json.dump(cache, open(cp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    progress(f"digest: {D['n_clips']} clip · {D['n_utts']} đoạn thoại · ~{D['tokens']} token"
             + (f" · Ollama mô tả {n_desc} clip, tóm {n_gist} đoạn (cache)" if n_desc or n_gist else ""))
    return D


def main():
    for s_ in (sys.stdout, sys.stderr):
        try:
            s_.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--ollama", default="")
    ap.add_argument("--describe", action="store_true")
    ap.add_argument("--words", type=int, default=14)
    ap.add_argument("--min-quality", type=int, default=45)
    a = ap.parse_args()
    D = build(a.src, a.ollama or None, a.describe, a.words, a.min_quality, progress=lambda m: print(m, flush=True))
    print("DIGEST " + json.dumps({"tokens": D["tokens"], "clips": D["n_clips"], "utts": D["n_utts"]}))
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
