"""One-shot parallel source analysis — Justin EDIT Suite.

  probe (recursive, cached)  →  ┌ transcribe (GPU whisper, audio decode runs ahead) → speech filter   [not with --no-audio]
                                ├ QC FFmpeg → stability/composition (CPU + NVDEC)                       [not with --audio-only]
                                ├ contact sheets (keyframe grabs, own lane — starts right after probe)  [not with --audio-only]
                                └ proxy 360p for AI video analysis (only with --proxy)
  --audio-only adds the sound index + who-speaks-when to the audio lane (a pictures-only folder skipped them).
Child tools keep their own caches (size+mtime), so re-running only touches new/changed clips.

Output for the panel:
  "@<stage> i/n <detail>"   per-stage progress (dashboard)
  "[D/T] <stage> · detail"  overall progress (progress bar)

CLI: python -m nxcore.analyze "F:/NEXIS/HR 2509" [--cuda] [--model large-v3] [--proxy] [--no-sheets]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # tools dir (nxcut.py + nxcore/)
PROG = re.compile(r"^\[(\d+)/(\d+)\]\s*(.*)$")


class Board:
    def __init__(self):
        self.lock = threading.Lock()
        self.st = {}          # stage -> [done, total]
        self.t0 = time.time()

    def total(self, stage, n):
        with self.lock:
            self.st.setdefault(stage, [0, 0])[1] = n
            self.emit(stage, "")

    def tick(self, stage, i, n, detail):
        with self.lock:
            s = self.st.setdefault(stage, [0, 0])
            s[0], s[1] = i, max(n, s[1])
            self.emit(stage, detail)

    def emit(self, stage, detail):
        d, n = self.st[stage]
        print(f"@{stage} {d}/{n} {detail}", flush=True)
        D = sum(v[0] for v in self.st.values()); T = sum(v[1] for v in self.st.values())
        if T:
            print(f"[{D}/{T}] {stage} · {detail}"[:220], flush=True)


def child(board, stage, args, env):
    p = subprocess.Popen([sys.executable] + args, cwd=HERE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace", bufsize=1)
    for line in p.stdout:
        line = line.rstrip()
        if not line:
            continue
        if line.startswith("{") and '"total"' in line:
            try:
                board.total(stage, int(json.loads(line)["total"]))
                continue
            except Exception:
                pass
        m = PROG.match(line)
        if m:
            board.tick(stage, int(m.group(1)), int(m.group(2)), m.group(3))
        elif line != "DONE":
            print(f"  {stage}: {line}"[:240], flush=True)
    p.wait()
    board.st.setdefault(stage, [0, 0])
    print(f"@{stage} done {'ok' if p.returncode == 0 else 'error ' + str(p.returncode)} {time.time() - board.t0:.0f}s", flush=True)
    return p.returncode


def main():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--cuda", action="store_true")
    ap.add_argument("--model", default="large-v3-turbo")
    ap.add_argument("--beam", type=int, default=1)
    ap.add_argument("--proxy", action="store_true")
    ap.add_argument("--no-sheets", action="store_true")
    ap.add_argument("--workers", type=int, default=0, help="0 = auto")
    ap.add_argument("--preset", default="", help="talking_head | interview_casting | vlog_travel | shortform_9x16 | event_recap")
    ap.add_argument("--no-audio", action="store_true",
                    help="chỉ hình: bỏ nhận dạng lời nói (Whisper) — montage nhạc / B-roll; QC âm thanh vẫn chạy cùng lần giải mã")
    ap.add_argument("--audio-only", action="store_true",
                    help="chỉ phần âm thanh (lời nói + lọc thoại) — cho folder đã phân tích hình với --no-audio")
    a = ap.parse_args()
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
    sys.path.insert(0, HERE)
    from nxcore import gpu as _gpu
    gw, cw = _gpu.auto_workers(a.cuda) if not a.workers else (a.workers, 0)
    w = max(2, gw + cw)
    env["NX_FF_THREADS"] = str(_gpu.ff_threads(w))   # CPU decodes: ffmpeg threads split between parallel workers
    print(f"máy: {_gpu.CPUS} luồng CPU · GPU giải mã: {_gpu.name()} · {gw} luồng GPU + {cw} luồng CPU song song", flush=True)
    board = Board()

    W = os.path.join(a.src, "_nexys_work")
    os.makedirs(W, exist_ok=True)
    if a.preset:
        try:
            from nxcore.presets import get_preset
            preset_data = get_preset(a.preset)
            from nxcore.jsonio import save_atomic
            save_atomic(os.path.join(W, "preset.json"), preset_data, indent=2)
            print(f"preset: Kích hoạt '{preset_data.get('name', a.preset)}'", flush=True)
        except Exception:
            pass

    from nxcore.media import probe_all
    t = time.time()
    board.total("probe", 0)

    def prog(m):
        mm = PROG.match(m)
        if m.startswith("{"):
            try:
                board.total("probe", int(json.loads(m)["total"])); return
            except Exception:
                pass
        if mm:
            board.tick("probe", int(mm.group(1)), int(mm.group(2)), mm.group(3))
        else:
            print("  " + m, flush=True)
    media = probe_all(a.src, workers=max(4, w * 2), progress=prog)
    print(f"@probe done ok {time.time() - t:.0f}s", flush=True)
    if not media:
        print("Không thấy clip video nào (đã quét cả thư mục con).", flush=True)
        return 2

    preset_args = ["--preset", a.preset] if a.preset else []
    tr = ["nxcut.py", "transcribe", a.src, "--model", a.model, "--beam", str(a.beam), "--batch-size", "16"] + preset_args + (["--cuda"] if a.cuda else [])
    # the folder's audio choice (panel asks once): <src>/_nexys_work/audio.json — background steps (sound index,
    # who-speaks-when in nxcore.describe) read it too
    from nxcore.jsonio import save_atomic as _save
    if a.no_audio or a.audio_only:
        _save(os.path.join(W, "audio.json"), {"on": not a.no_audio, "by": "analyze", "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    # lanes run in parallel; each lane = [(stage, child args)] run one after the other
    jobs = []
    if not a.no_audio:
        jobs.append(("transcribe", [("transcribe", tr), ("speech", ["-m", "nxcore.speech", a.src])]))   # speech: junk filter after whisper
    else:
        print("analyze: chỉ hình — bỏ nhận dạng lời nói (bấm 🎙 Âm thanh khi cần)", flush=True)
    cuda_flag = [] if a.cuda else ["--no-cuda"]
    wk = ["--workers", str(a.workers)] if a.workers else []       # none → each tool picks GPU + CPU workers itself
    if not a.audio_only:
        # stability decodes each clip once and runs the QC detectors on the same frames (--with-qc);
        # qc afterwards only handles what is left (clips stability skips by preset, failed / incomplete decodes)
        jobs.append(("qc+stability", [("stability", ["-m", "nxcore.stability", a.src, "--with-qc"] + wk + preset_args + cuda_flag),
                                      ("qc", ["-m", "nxcore.qc", a.src] + wk + cuda_flag)]))
        if not a.no_sheets:          # cheap keyframe grabs: no reason to wait for the GPU lanes
            jobs.append(("sheets", [("sheets", ["nxcut.py", "sheets", a.src])]))
        if a.proxy:                  # proxies are built only on request (--proxy)
            jobs.append(("proxy", [("proxy", ["-m", "nxcore.proxy", a.src, "--workers", "2"] + cuda_flag)]))
    else:
        # the sound index + who-speaks-when come with the audio, right away (a pictures-only folder skipped them)
        jobs[0][1].extend([("sounds", ["-m", "nxcore.ai.sounds", "index", a.src]), ("speakers", ["-m", "nxcore.ai.speakers", a.src])])
    rc = {}

    def lane(name, cmds):
        for stage, c in cmds:
            rc[stage] = child(board, stage, c, env)
    th = [threading.Thread(target=lane, args=j, daemon=True) for j in jobs]
    from nxcore import gpulock
    with gpulock.hold(a.src, "gpu_busy"):          # background describe (Ollama) backs off while whisper/QC run
        for x in th:
            x.start()
        for x in th:
            x.join()
    bad = {k: v for k, v in rc.items() if v}
    print(f"analyze: {len(media)} clip · {time.time() - board.t0:.0f}s" + (f" · lỗi: {', '.join(bad)}" if bad else ""), flush=True)
    return 1 if bad.get("transcribe") else 0


if __name__ == "__main__":
    sys.exit(main())
