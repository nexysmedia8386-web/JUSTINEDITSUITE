"""Speech quality — which transcript lines are real, meaningful dialogue.

Whisper on vlog / event footage produces three kinds of lines the editor never wants as dialogue:
  junk  : hallucinations ("Các bạn có thể nhận thêm những bài hát…", "hãy subscribe…"), repetition loops
          ("đây, đây, đây…", "tờ tờ tờ"), number counting ("78, 03, 88…"), a whole-clip sentence over music/noise,
          very low confidence
  frag  : fragments / fillers ("Rồi", "Dạ", "Hả?", "Cái gì?") — fine as natural sound, not as a dialogue beat
  ok    : meaningful speech worth cutting to

  python -m nxcore.speech SRC [--ollama gemma4:12b-it-qat] [--min-words 3]

Writes _nexys_work/speech.json  {file: {"seg": [["ok"|"frag"|"junk", score, reason], … per segment], "key": …}}
Used by: digest (only "ok" lines reach the AI), align (junk never matched), build (no subtitles for junk),
panel transcripts (AI prompts).  Optional local LLM pass (Ollama, cached) re-checks lines that look like speech
but may be nonsense ("Ai dò phôn xô").
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys

from . import jsonio
from .textnorm import strip_diacritics

V = 3          # v3: hallucination phrases matched on whole words + only when they are most of the line / low confidence
HALLU = [  # normalised (no diacritics, lowercase) fragments of known Whisper hallucinations on Vietnamese audio
    "cac ban co the nhan them nhung bai hat", "hay subscribe", "dang ky kenh", "dang ki kenh", "cam on cac ban da theo doi",
    "cam on cac ban da xem", "hen gap lai cac ban", "ghien mi go", "like va share", "subtitle", "vietsub", "phu de",
    "thank you for watching", "thanks for watching", "nhac nen", "la la school", "lalaschool", "theo doi va", "bam chuong",
    "video tiep theo", "kenh cua minh", "nhan nut like", "amara org", "cac ban hay", "xin chao cac ban va hen gap lai",
    "subscribe", "hen gap lai",
]
# subtitle-credit / channel-name hallucinations: never real dialogue → junk wherever they appear (still whole words)
HALLU_ALWAYS = {"ghien mi go", "la la school", "lalaschool", "amara org", "vietsub"}
HALLU_COVER = 0.6          # phrase(s) ≥ 60 % of the line's syllables → junk
_HALLU_RE = [(h, re.compile(r"(?<![a-z0-9])" + re.escape(h) + r"(?![a-z0-9])")) for h in HALLU]
FILLER = set("a à ạ ừ ờ ơ ồ ừm hả hở ha hà dạ vâng ừ ờ rồi ok okay oke ơi nè ne đó đây kìa chưa có không hông ủa ui ối trời à ừa ờ ha á".split())
NUM = re.compile(r"^\d+([.,:]\d+)?$")


def _norm(t):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", strip_diacritics(t.lower()))).strip()


def hallu_match(text, lp=None, ns=None):
    """→ (is_junk, coverage, phrases). A known hallucination phrase is junk only when it is most of the line
    (≥ 60 % of the syllables) or Whisper itself was unsure (avg logprob < -1.0 or no_speech_prob > 0.5):
    "các bạn hãy xem kìa…", "kênh của mình có…", "theo dõi vào…" in real speech stay."""
    n = _norm(text)
    toks = n.split()
    if not toks:
        return False, 0.0, []
    covered = [False] * len(toks)
    found, always = [], False
    for h, rx in _HALLU_RE:
        for m in rx.finditer(n):
            a = len(n[:m.start()].split())
            b = a + len(h.split())
            for i in range(a, min(b, len(toks))):
                covered[i] = True
            found.append(h)
            always = always or h in HALLU_ALWAYS
    if not found:
        return False, 0.0, []
    cover = sum(covered) / len(toks)
    unsure = (lp is not None and lp < -1.0) or (ns is not None and ns > 0.5)
    return bool(always or cover >= HALLU_COVER or unsure), round(cover, 2), found


def classify(seg, clip_dur=0.0, min_words=3):
    """→ (label, score 0..1, reason)."""
    t = str(seg.get("text", "")).strip()
    words = [w for w in re.split(r"[\s,.!?…;:]+", t.lower()) if w]
    nw = len(words)
    dur = max(0.05, float(seg.get("end", 0)) - float(seg.get("start", 0)))
    if not words:
        return "junk", 0.0, "rỗng"
    lp, ns = seg.get("lp"), seg.get("ns")
    if hallu_match(t, lp, ns)[0]:
        return "junk", 0.0, "câu Whisper hay bịa"
    if ns is not None and ns > 0.6:          # (nxcut used to drop these at transcribe time; now kept + flagged here)
        return "junk", 0.05, "gần như không có tiếng nói"
    uniq = len(set(words)) / nw
    run = max_run(words)
    if run >= 4 or (nw >= 6 and uniq < 0.35):
        return "junk", 0.05, "lặp từ"
    nums = sum(1 for w in words if NUM.match(w))
    if (nums >= 3 and nums / nw >= 0.4) or (nums >= 2 and nums / nw >= 0.3):
        return "junk", 0.1, "đọc số liên tục"
    if nw >= 4 and nw % 2 == 0 and words[:nw // 2] == words[nw // 2:]:
        return "frag", 0.3, "lặp lại câu"
    wps = nw / dur
    probs = [w[3] for w in seg.get("words", []) if len(w) > 3]
    wp = sum(probs) / len(probs) if probs else None
    if ns is not None and lp is not None and ns > 0.5 and lp < -0.9:
        return "junk", 0.1, "gần như không có tiếng nói"
    if wp is not None and wp < 0.35:
        return "junk", 0.15, "nghe không rõ"
    # a long fluent sentence stretched over most of a clip with slow delivery = typical hallucination over music
    if float(seg.get("start", 0)) <= 0.3 and dur >= 6 and wps < 1.8 and clip_dur and dur >= 0.6 * clip_dur:
        return "junk", 0.15, "câu phủ cả clip (nghi bịa trên nhạc/ồn)"
    if nw >= 5 and (wps > 7.5 or wps < 0.7):
        return "junk", 0.2, "tốc độ nói bất thường"
    content = [w for w in words if w not in FILLER]
    if nw < min_words or len(content) < 2:
        return "frag", 0.35, "câu vụn / từ đệm"
    score = 0.6 + 0.4 * min(1.0, len(content) / 8.0)
    if wp is not None:
        score = score * (0.5 + 0.5 * min(1.0, wp / 0.8))
    if lp is not None and lp < -1.0:
        score *= 0.75
    if score < 0.45:
        return "frag", round(score, 2), "độ tin cậy thấp"
    return "ok", round(score, 2), ""


def max_run(words):
    best = cur = 1
    for a, b in zip(words, words[1:]):
        cur = cur + 1 if a == b else 1
        best = max(best, cur)
    return best


def _ollama(model, prompt, timeout=120):
    from .ollama import chat
    txt = chat(model, prompt, temperature=0.1, num_predict=400, keep_alive="10m", timeout=timeout)
    m = re.search(r"\{.*\}", txt, re.S)                    # tolerate prose / code fences around the JSON
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    nums = re.findall(r"\b[0-2]\b", txt)                   # last resort: a bare list of scores
    return {"s": [int(x) for x in nums]} if nums else {}


LLM_PROMPT = ("Đây là các câu Whisper nhận dạng từ tiếng nói trong 1 clip video tiếng Việt (vlog/phỏng vấn/sự kiện, có thể sai chính tả). "
              "Câu đời thường ngắn nhưng rõ nghĩa (vd 'Cho con thu tiền', 'Đậu ơi ăn gì đây?') vẫn là 2. Với MỖI câu, chấm 0–2: "
              "2 = câu có nghĩa, dùng làm thoại trong video được; 1 = vụn/nói chuyện phụ, chỉ để làm tiếng nền; "
              "0 = vô nghĩa / nhận dạng sai / lẫn tạp âm. TRẢ JSON {\"s\":[điểm,…]} theo đúng thứ tự.\n")


def analyze(src, ollama=None, min_words=3, force=False, progress=print):
    W = os.path.join(src, "_nexys_work")
    media = {}
    try:
        media = json.load(open(os.path.join(W, "media.json"), encoding="utf-8"))
    except Exception:
        pass
    sp = os.path.join(W, "speech.json")
    old = {} if force else _j(sp)
    cache = _j(os.path.join(W, "speech_llm_cache.json"))
    out, stats, n_llm = {}, {"ok": 0, "frag": 0, "junk": 0}, 0
    for tp in sorted(glob.glob(os.path.join(W, "transcripts", "*.json"))):
        if tp.endswith(".v2.json"):
            continue
        d = _j(tp)
        f = d.get("file") or os.path.basename(tp)
        segs = d.get("segments", [])
        key = f"{V}:{os.path.getsize(tp)}:{int(os.path.getmtime(tp))}:{min_words}:{bool(ollama)}"
        if old.get(f, {}).get("key") == key:
            out[f] = old[f]
        else:
            dur = float((media.get(f) or {}).get("dur") or 0)
            res = [list(classify(s, dur, min_words)) for s in segs]
            # a clip where much of the talk is junk (counting, loops, noise) is background chatter: keep only strong lines
            if len(res) >= 4 and sum(r[0] == "junk" for r in res) / len(res) >= 0.25:
                for i, r in enumerate(res):
                    if r[0] == "ok" and len(str(segs[i].get("text", "")).split()) < 6:
                        res[i] = ["frag", 0.4, "clip nói chuyện lộn xộn"]
            if ollama:
                idx = [i for i, r in enumerate(res) if r[0] == "ok"]
                if idx:
                    lines = [segs[i]["text"] for i in idx]
                    h = hashlib.md5((ollama + "\n".join(lines)).encode("utf-8")).hexdigest()[:16]
                    sc = cache.get(h)
                    if sc is None:
                        try:
                            sc = _ollama(ollama, LLM_PROMPT + "\n".join(f"{k + 1}. {t}" for k, t in enumerate(lines))).get("s")
                            if isinstance(sc, list) and len(sc) == len(lines):
                                cache[h] = sc; n_llm += 1
                            else:
                                sc = None
                        except Exception as e:
                            progress(f"  ⚠ Ollama kiểm tra thoại {f}: {e}")
                            sc = None
                    for i, v in zip(idx, sc or []):
                        try:
                            v = int(v)
                        except Exception:
                            continue
                        # local LLM can only demote to "frag" (kept as natural sound + still matchable by a
                        # hand-written script); only the deterministic rules above can call a line junk
                        if v <= 0:
                            res[i] = ["frag", 0.25, "AI local: có thể vô nghĩa / nhận dạng sai"]
                        elif v == 1:
                            res[i] = ["frag", 0.4, "AI local: nói chuyện phụ"]
            out[f] = {"seg": res, "key": key}
        for r in out[f]["seg"]:
            stats[r[0]] += 1
    jsonio.save_atomic(sp, out, indent=None)
    if n_llm:
        jsonio.save_atomic(os.path.join(W, "speech_llm_cache.json"), cache, indent=None)
    progress(f"speech: {stats['ok']} câu có nghĩa · {stats['frag']} câu vụn (tiếng nền) · {stats['junk']} câu rác bị loại"
             + (f" · AI local kiểm {n_llm} clip" if n_llm else ""))
    return out


def load(src):
    """{file: [label per segment]} — empty dict when speech.json is missing (callers then keep everything)."""
    d = _j(os.path.join(src, "_nexys_work", "speech.json"))
    return {f: [r[0] for r in v.get("seg", [])] for f, v in d.items()}


def _j(p):
    v = jsonio.load(p, {})
    return v if isinstance(v, dict) else {}


def main():
    for s_ in (sys.stdout, sys.stderr):
        try:
            s_.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--ollama", default="")
    ap.add_argument("--min-words", type=int, default=3)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    analyze(a.src, a.ollama or None, a.min_words, a.force, progress=lambda m: print(m, flush=True))
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
