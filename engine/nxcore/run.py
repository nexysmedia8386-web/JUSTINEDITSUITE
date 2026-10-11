"""One-shot CLI: script + nxcut work dir -> plan.json (+ report), ready for `nxcut.py build`.

python -m nxcore.run --src "F:/NEXIS/HR 2509" --script kich_ban.docx --plan out/plan.json \
    --base plan_example_casting.json --name CASTING_v4 --keywords "tiềm năng,nhóm diễn"
"""
import argparse
import json
import os
import sys

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from .align import align, load_work, merged_bad
from .assemble import build_plan, write_report
from .script_parser import parse_script_meta


def parse_seq(spec, cur=None):
    """'1920x1080@25:fit' → {"w": 1920, "h": 1080, "fps": 25, "fit": "fit"} (missing parts keep the current value)."""
    import re
    seq = dict(cur or {"w": 1080, "h": 1920, "fps": 29.97})
    m = re.match(r"^\s*(\d+)\s*[x×]\s*(\d+)\s*(?:@\s*([\d.]+))?\s*(?::\s*(fit|fill))?\s*$", spec, re.I)
    if not m:
        raise SystemExit(f"--seq sai định dạng: {spec} (vd 1920x1080@25:fill)")
    seq["w"], seq["h"] = int(m.group(1)), int(m.group(2))
    if m.group(3):
        seq["fps"] = float(m.group(3))
    if m.group(4):
        seq["fit"] = m.group(4).lower()
    return seq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--script", default="", help="kịch bản; bỏ trống với preset montage (beat / vo / story) → nxcore.montage")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--base", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--out", default="", help="thư mục output của nxcut build (XML/SRT/PNG)")
    ap.add_argument("--keywords", default="", help="từ khóa highlight, phân tách bằng dấu phẩy")
    ap.add_argument("--cfg", default="", help="JSON override cho align/assemble config")
    ap.add_argument("--seq", default="", help="khung sequence WxH[@fps][:fit|fill], vd 1920x1080@25:fill")
    ap.add_argument("--broll-audio-db", type=float, default=None, help="mức âm thanh gốc của B-roll (dB, mặc định -9); -99 = tắt tiếng nhưng vẫn kèm")
    ap.add_argument("--preset", default="", help="preset thể loại video")
    ap.add_argument("--music", action="append", default=[], help="(preset montage) file nhạc")
    ap.add_argument("--vo", action="append", default=[], help="(preset montage vo) file voice-over")
    a = ap.parse_args()
    mode = ""
    if a.preset:
        try:
            from .presets import get_preset
            mode = get_preset(a.preset).get("mode") or ""
        except Exception:
            mode = ""
    if not a.script:
        if mode not in ("beat", "vo", "story"):
            raise SystemExit("--script là bắt buộc (trừ preset montage: beat_cut, wedding_*, event_recap, explainer_vo)")
        from . import montage
        argv = ["--src", a.src, "--preset", a.preset, "--plan", a.plan, "--name", a.name, "--out", a.out]
        if a.seq:
            argv += ["--seq", a.seq]
        if a.cfg:
            argv += ["--cfg", a.cfg]
        for m in a.music:
            argv += ["--music", m]
        for v in a.vo:
            argv += ["--vo", v]
        montage.main(argv)
        return
    cfg = json.load(open(a.cfg, encoding="utf-8")) if a.cfg else {}
    if a.preset:
        cfg["preset"] = a.preset
    lines, meta = parse_script_meta(a.script)
    lines, cands, chosen, clips, media = align(a.script, a.src, cfg.get("align"), lines=lines)
    load_notes = []
    _, _, shake = load_work(a.src, load_notes)
    shake, qc = merged_bad(a.src, shake)
    base = json.load(open(a.base, encoding="utf-8")) if a.base else {}
    if a.out:
        base["out"] = a.out.replace("\\", "/")
    if a.seq:
        base["seq"] = parse_seq(a.seq, base.get("seq"))
    kws = [k.strip() for k in a.keywords.split(",") if k.strip()]
    asm_cfg = dict(cfg.get("assemble") or {})
    if a.preset:
        asm_cfg["preset"] = a.preset
    P = build_plan(lines, meta, cands, chosen, clips, media, shake, a.src, base, a.name or None, keywords=kws, cfg=asm_cfg)
    P["report_notes"][:0] = load_notes          # transcripts dropped because their clip is not in media.json
    # audio QC notes on the clips actually used
    for f in sorted({s_["file"] for s_ in P["segments"] if s_.get("audio", True)}):
        q = qc.get(f) or {}
        if q.get("true_peak") is not None and q["true_peak"] > -0.5:
            P["report_notes"].append(f"{f}: âm thanh có thể vỡ (true peak {q['true_peak']} dBFS)")
        if q.get("lufs") is not None and q["lufs"] < -35:
            P["report_notes"].append(f"{f}: thoại nhỏ ({q['lufs']} LUFS) — cần gain")
    if a.broll_audio_db is not None:
        P["broll_audio"] = {"on": True, "gain_db": a.broll_audio_db}
    noaud = sorted({s_["file"] for s_ in P["segments"] if not media.get(s_["file"], {}).get("audio", True)})
    if noaud:
        P["report_notes"].append(f"{len(noaud)} clip không có track âm thanh (không kèm tiếng được): " + ", ".join(noaud[:10]))
    os.makedirs(os.path.dirname(os.path.abspath(a.plan)), exist_ok=True)
    json.dump(P, open(a.plan, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    rp = os.path.splitext(a.plan)[0] + "_align.md"
    write_report(P, lines, cands, rp)
    print(open(rp, encoding="utf-8").read())
    print("plan:", a.plan)


if __name__ == "__main__":
    main()
