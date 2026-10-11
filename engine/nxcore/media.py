"""Source discovery + probe for nested folders (Justin EDIT Suite).

Keys in media.json are paths RELATIVE to the source root with "/" separators ("Day1/CamA/C0001.MP4"), so clips with
the same name in different folders never collide. Derived files (transcripts, wav, proxy, thumbs) use stem(key),
a flat, filesystem-safe name ("Day1__CamA__C0001").

Probe is parallel and cached by size+mtime: re-running on a big card only probes new/changed clips.

CLI: python -m nxcore.media "F:/NEXIS/HR 2509" [--workers 8] [--force]
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import jsonio, ensure_media_env

ensure_media_env()

VEXT = (".mp4", ".mov", ".mxf", ".mts", ".m2ts", ".m4v", ".avi", ".mkv", ".insv")
SKIP_DIR = re.compile(r"^(_nexys_|nx_|\.|__macosx|\$recycle|system volume information|adobe premiere pro|proxies?$|proxy media|"
                      r"cache|thumbnails?$)", re.I)
MIN_BYTES = 64 * 1024


def rel(src, path):
    return os.path.relpath(path, src).replace("\\", "/")


def stem(key):
    """Flat, safe file stem for derived files: 'Day1/CamA/C0001.MP4' -> 'Day1__CamA__C0001'."""
    s = os.path.splitext(key.replace("\\", "/"))[0]
    return re.sub(r'[<>:"|?*]', "_", s.replace("/", "__"))


def _natural(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def scan(src):
    """VIDEO files only under src (recursive): images, audio, documents, project files and camera sidecars
    (.THM .LRV .XML .BIN …) are ignored; tool/output/proxy/cache folders and hidden files are skipped."""
    out = []
    for d, dirs, files in os.walk(src):
        dirs[:] = sorted([x for x in dirs if not SKIP_DIR.match(x)], key=_natural)
        for f in files:
            if f.startswith(("._", "~")) or not f.lower().endswith(VEXT):
                continue
            p = os.path.join(d, f)
            # tiny files are NOT dropped here — probe_file() gives them a status + reason so they show up
            # in triage.json "broken" instead of vanishing from the report
            out.append(rel(src, p))
    return sorted(out, key=_natural)


# bump when probe_file() output changes (fields / meaning) → every clip is re-probed once. Stored as "pv" in each
# record; the file key ("key" = size:mtime) itself stays the same so downstream caches (shake, vad, describe…) survive.
# v2: fps = avg_frame_rate for VFR clips · health warns/breaks by probe position
PROBE_V = 2


def fkey(path):
    st = os.stat(path)
    return f"{st.st_size}:{int(st.st_mtime)}"


RATIOS = [("9:16", 9 / 16), ("3:4", 3 / 4), ("4:5", 4 / 5), ("1:1", 1.0), ("4:3", 4 / 3), ("16:9", 16 / 9), ("1.85", 1.85), ("2.39", 2.39)]
CAM_PREFIX = re.compile(r"^(DSC|C\d|IMG|VID|DJI|GX|GH|GOPR|MVI|A\d{3}|PXL|MOV|CLIP|FX|LRV|REC|Record|Screen)", re.I)


def ratio_label(w, h):
    if not w or not h:
        return "?"
    r = w / h
    name, val = min(RATIOS, key=lambda x: abs(math.log(r / x[1])))
    return name if abs(math.log(r / val)) < 0.04 else f"{r:.2f}"


def _rate(r):
    try:
        n, d = map(int, (r or "0/1").split("/"))
        return n / d if d else 0.0
    except Exception:
        return 0.0


_DECODE_LOCK = threading.BoundedSemaphore(4)
HEALTH_AT = (0.5, 0.95)           # probe positions (fraction of the duration)
_BAD_DECODE = re.compile(r"(invalid|corrupt|error while decoding|missing picture|moov atom)", re.I)
_BAD_DECODE_OK = re.compile(r"(invalid|corrupt|error while decoding|moov atom)", re.I)


def health(path, dur):
    """Safe, lightweight decode check: one frame near 50 % and 95 % of the clip, keyframe seek (-noaccurate_seek: no
    decoding up to the exact time), bounded concurrency (avoids Windows RAM/paging exhaustion).
    → [(q, message)] for the positions that failed, e.g. [(0.95, "lỗi dữ liệu ở 57.0s")]."""
    bad = []
    # common case first: both positions from ONE ffmpeg (2 keyframe-seeked inputs); only a dirty result is re-checked
    # per position below, to say WHERE the file is broken
    try:
        cmd = ["ffmpeg", "-v", "error", "-nostdin"]
        for q in HEALTH_AT:
            cmd += ["-noaccurate_seek", "-ss", f"{max(0.0, min(dur - 0.2, dur * q)):.2f}", "-i", path]
        for i in range(len(HEALTH_AT)):
            cmd += ["-map", f"{i}:v:0", "-frames:v", "1", "-an", "-f", "null", "-"]
        with _DECODE_LOCK:
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        if p.returncode == 0 and not _BAD_DECODE_OK.search(p.stderr or ""):
            return bad
    except (OSError, subprocess.SubprocessError, MemoryError):
        pass
    for q in HEALTH_AT:
        t = max(0.0, min(dur - 0.2, dur * q))
        try:
            with _DECODE_LOCK:
                p = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-noaccurate_seek", "-ss", f"{t:.2f}", "-i", path,
                                    "-frames:v", "1", "-an", "-f", "null", "-"],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
                err = (p.stderr or "").strip()
                if p.returncode != 0:
                    if _BAD_DECODE.search(err):
                        bad.append((q, f"lỗi dữ liệu ở {t:.1f}s"))
                elif err and _BAD_DECODE_OK.search(err):
                    bad.append((q, f"lỗi dữ liệu ở {t:.1f}s"))
        except (OSError, subprocess.SubprocessError, MemoryError):
            pass
    return bad


def health_verdict(bad):
    """health() result → (status, reason): the middle of the clip broken = unusable; only the tail = usable + warning."""
    if not bad:
        return "ok", ""
    qs = {q for q, _ in bad}
    msgs = "; ".join(m for _, m in bad)
    if any(q < 0.9 for q in qs):
        return "broken", msgs
    return "ok", "cảnh báo: " + msgs


def detect_camera(f, o=None):
    base = os.path.basename(f)
    tags = {}
    if o:
        tags.update(o.get("format", {}).get("tags", {}) or {})
        for s in o.get("streams", []):
            tags.update(s.get("tags", {}) or {})
    make = str(tags.get("make") or tags.get("com.apple.quicktime.make") or "").lower()
    model = str(tags.get("model") or tags.get("com.apple.quicktime.model") or "").lower()
    handler = str(tags.get("handler_name") or "").lower()

    if "sony" in make or "ilce" in model or re.match(r"^C\d{4}\.MP4$", base, re.I):
        return "Sony Alpha"
    if "dji" in make or "dji" in model or base.upper().startswith("DJI"):
        return "DJI Action"
    if "apple" in make or "iphone" in model or "core media" in handler or re.match(r"^IMG_\d+", base, re.I):
        return "iPhone"
    if "gopro" in make or re.match(r"^(GOPR|GX|GH)\d+", base, re.I):
        return "GoPro"
    m = CAM_PREFIX.match(base)
    if m:
        p = m.group(1).upper()
        if p.startswith("C"): return "Sony Alpha"
        if p == "IMG": return "iPhone"
        if p == "DJI": return "DJI Action"
        return p
    return "Khác"


def res_label(w, h):
    mx = max(w or 0, h or 0)
    if mx >= 3800: return "4K UHD"
    if mx >= 2500: return "2.7K"
    if mx >= 1900: return "1080p FHD"
    if mx >= 1200: return "720p HD"
    return "SD"


def _parse_ts(ct, path=None):
    """Normalize creation time to (iso_utc, local_str, timestamp_float)."""
    from datetime import datetime, timezone, timedelta
    tz_vn = timezone(timedelta(hours=7))
    dt = None
    if ct:
        # e.g. 2026-06-27T03:20:47.000000Z or 2026-06-27T10:20:47+0700 or 2026-06-27 10:20:47
        clean = str(ct).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(clean)
        except Exception:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y:%m:%d %H:%M:%S"):
                try:
                    dt = datetime.strptime(str(ct)[:19], fmt).replace(tzinfo=tz_vn)
                    break
                except Exception:
                    pass
    if not dt and path and os.path.exists(path):
        mtime = os.path.getmtime(path)
        dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
    if dt:
        if dt.tzinfo is None:
            # Assume UTC if camera recorded ISO without offset
            dt = dt.replace(tzinfo=timezone.utc)
        dt_utc = dt.astimezone(timezone.utc)
        dt_local = dt.astimezone(tz_vn)
        return dt_utc.strftime("%Y-%m-%dT%H:%M:%S.000000Z"), dt_local.strftime("%Y-%m-%d %H:%M:%S"), dt_utc.timestamp()
    return "", "", 0.0


def probe_file(path, check=True):
    """Always returns a record; status = ok | broken | skip with a reason (never silently drops a file)."""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return {"status": "broken", "reason": f"không đọc được file ({e.strerror or e})"}
    if size == 0:
        return {"status": "broken", "reason": "file rỗng (0 byte) — copy lỗi / quay hỏng"}
    try:
        p = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=codec_type,codec_name,r_frame_rate,avg_frame_rate,width,height,pix_fmt,nb_frames:stream_disposition=attached_pic:"
                            "stream_tags=rotate,creation_time,handler_name,encoder:stream_side_data=rotation:"
                            "format=duration,bit_rate,format_name:format_tags=creation_time,make,model,com.apple.quicktime.creationdate,com.apple.quicktime.make,com.apple.quicktime.model",
                            "-of", "json", path], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except FileNotFoundError:
        return {"status": "broken", "reason": "chưa cài đặt hoặc không tìm thấy ffprobe trong PATH hệ thống"}
    except subprocess.TimeoutExpired:
        return {"status": "broken", "reason": "ffprobe treo (file hỏng?)"}
    try:
        o = json.loads(p.stdout or "{}")
    except Exception:
        o = {}
    if p.returncode != 0 or not o:
        msg = (p.stderr or "").strip().splitlines()
        return {"status": "broken", "reason": ("moov atom thiếu — file quay dở / copy lỗi" if "moov" in (p.stderr or "") else (msg[-1][:120] if msg else "ffprobe lỗi"))}
    v = [x for x in o.get("streams", []) if x.get("codec_type") == "video" and not (x.get("disposition") or {}).get("attached_pic")
         and x.get("codec_name") not in ("mjpeg", "png", "bmp", "gif", "webp")]
    a = [x for x in o.get("streams", []) if x.get("codec_type") == "audio"]
    if not v:
        return {"status": "skip", "reason": "không có hình (file âm thanh / ảnh bìa)"}
    v = v[0]
    r_fps = _rate(v.get("r_frame_rate"))
    avg = _rate(v.get("avg_frame_rate"))
    fps = r_fps or avg
    vfr = bool(avg and fps and abs(avg - fps) / fps > 0.02)
    if vfr:                     # phone VFR: r_frame_rate is the max/timebase rate (60, 120, 90000…), avg is the real one
        fps = avg
    rot = int(float(v.get("tags", {}).get("rotate", 0) or 0))
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    w, h = v.get("width", 0) or 0, v.get("height", 0) or 0
    if abs(rot) % 180 == 90:
        w, h = h, w

    fmt_tags = o.get("format", {}).get("tags", {}) or {}
    v_tags = v.get("tags", {}) or {}
    apple_ct = fmt_tags.get("com.apple.quicktime.creationdate") or v_tags.get("com.apple.quicktime.creationdate")
    raw_ct = apple_ct or v_tags.get("creation_time") or fmt_tags.get("creation_time")
    iso_ct, local_ct, ct_ts = _parse_ts(raw_ct, path)

    dur = float(o.get("format", {}).get("duration") or 0)
    base_name = os.path.basename(path)
    is_dup = bool(re.search(r"\(\d+\)\.[a-zA-Z0-9]+$", base_name))
    cam = detect_camera(path, o)
    r_label = res_label(w, h)
    fps_fam = "50/60p" if fps >= 47 else "24/25/30p"

    rec = dict(fps=fps, tb=round(fps) if fps else 30, ntsc=abs(fps - round(fps)) > 0.01 if fps else False, dur=dur, w=w, h=h,
               raw_w=v.get("width", 0), raw_h=v.get("height", 0), rot=rot, audio=bool(a),
               created=iso_ct, created_local=local_ct, created_ts=ct_ts,
               cam=cam, res_label=r_label, fps_fam=fps_fam, is_duplicate=is_dup,
               codec=v.get("codec_name", ""), vfr=vfr,
               ratio=ratio_label(w, h), orient="dọc" if h > w * 1.05 else "ngang" if w > h * 1.05 else "vuông",
               bitrate=int(o.get("format", {}).get("bit_rate") or 0), status="ok", reason="")
    if vfr:
        rec["fps_r"] = r_fps
    if not w or not h or not fps:
        rec.update(status="broken", reason="không đọc được khung hình / fps")
    elif dur < 0.5:
        rec.update(status="skip", reason=f"quá ngắn ({dur:.2f}s)")
    elif check:
        status, reason = health_verdict(health(path, dur))   # middle broken → broken; tail only → ok + warning
        if status != "ok" or reason:
            rec.update(status=status, reason=reason)
    return rec


def group_key(f, r):
    cam = r.get("cam") or detect_camera(f)
    fam = r.get("fps_fam") or ("50/60p" if r.get("fps", 0) >= 47 else "24/25/30p")
    res = r.get("res_label") or res_label(r.get("w"), r.get("h"))
    dup_tag = " · [Bản sao (1)]" if r.get("is_duplicate") else ""
    return f"{r.get('orient', '?')} {r.get('ratio', '?')} · {res} · {fam} · {cam}{dup_tag}"


def make_thumb(src_path, dst_path, dur=0.0):
    """Extract a fast, crisp single-frame thumbnail (240px wide)."""
    t = 1.0 if dur > 2.0 else max(0.1, dur * 0.5)
    if dur >= 5.0:
        t = min(2.5, max(1.0, dur * 0.15))
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-noaccurate_seek", "-ss", f"{t:.2f}",
        "-i", src_path,
        "-frames:v", "1",
        "-vf", "scale=240:-2",
        "-q:v", "3",
        dst_path
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        failed = p.returncode != 0
    except subprocess.TimeoutExpired:
        failed = True
    if failed or not os.path.exists(dst_path) or os.path.getsize(dst_path) < 100:
        cmd_fallback = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-noaccurate_seek", "-ss", "0.1",
            "-i", src_path,
            "-frames:v", "1",
            "-vf", "scale=240:-2",
            "-q:v", "4",
            dst_path
        ]
        try:
            subprocess.run(cmd_fallback, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        except subprocess.TimeoutExpired:
            pass
    return os.path.exists(dst_path) and os.path.getsize(dst_path) > 100


def generate_thumbs(src, media, workers=4, force=False, progress=print):
    """Generate lightweight thumbnails (<src>/_nexys_work/thumbs/<stem>.jpg) for all active clips."""
    W = os.path.join(src, "_nexys_work")
    tdir = os.path.join(W, "thumbs")
    os.makedirs(tdir, exist_ok=True)

    todo = []
    for f, m in media.items():
        if m.get("status") != "ok":
            continue
        full = os.path.join(src, f)
        if not os.path.exists(full):
            continue
        s_stem = stem(f)
        thumb_name = f"{s_stem}.jpg"
        dst = os.path.join(tdir, thumb_name)
        m["thumb"] = f"thumbs/{thumb_name}"

        if not force and os.path.exists(dst) and os.path.getsize(dst) > 200:
            try:
                if os.path.getmtime(dst) >= os.path.getmtime(full):
                    continue
            except OSError:
                pass
        todo.append((f, full, dst, float(m.get("dur") or 0)))

    if not todo:
        progress("thumbs: tất cả thumbnail đã có sẵn trong cache")
        return

    progress(json.dumps({"stage": "thumbs", "total": len(todo)}))
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(make_thumb, full, dst, dur): (f, dst) for f, full, dst, dur in todo}
        for fu in as_completed(futs):
            f, dst = futs[fu]
            done += 1
            if done % 5 == 0 or done == len(todo):
                progress(f"@thumbs {done}/{len(todo)} {f}")
    progress(f"thumbs: Đã tạo {done} thumbnail trong _nexys_work/thumbs")


def probe_all(src, workers=4, force=False, progress=print, check=True, target=None):
    """Triage the whole tree fast: every video → status ok/broken/skip + format group.
    media_all.json = everything (with reasons) · media.json = only what the detailed analysis should touch
    (status ok and not excluded in triage.json)."""
    W = os.path.join(src, "_nexys_work")
    os.makedirs(W, exist_ok=True)
    mp, ap, tp = os.path.join(W, "media.json"), os.path.join(W, "media_all.json"), os.path.join(W, "triage.json")
    old = {}
    for path in (ap, mp):
        if os.path.exists(path) and not force and not old:
            old = jsonio.load(path, {}) or {}
    files = scan(src)
    out, todo = {}, []
    for f in files:
        full = os.path.join(src, f)
        try:
            k = fkey(full)
        except OSError:
            continue
        o = old.get(f)
        if o and o.get("key") == k and "status" in o and o.get("pv") == PROBE_V:
            out[f] = o
        else:
            todo.append((f, full, k))
    progress(json.dumps({"stage": "probe", "total": len(todo), "cached": len(out), "files": len(files)}))
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(probe_file, full, check): (f, k) for f, full, k in todo}
        for fu in as_completed(futs):
            f, k = futs[fu]
            done += 1
            try:
                p = fu.result()
            except Exception as e:
                p = {"status": "broken", "reason": f"lỗi đọc: {e}"}
            p.update(key=k, pv=PROBE_V, folder=os.path.dirname(f), bytes=int(k.split(":")[0]))
            if p["status"] == "ok":
                p["group"] = group_key(f, p)
            out[f] = p
            tag = "" if p["status"] == "ok" else f" ✖ {p['status']}: {p['reason']}"
            progress(f"[{done}/{len(todo)}] {f} " + (f"{p['w']}x{p['h']} {p['fps']:.2f}fps {p['dur']:.1f}s" if p.get("w") else "") + tag)
    out = {f: out[f] for f in sorted(out, key=_natural)}
    jsonio.save_atomic(ap, out)
    tri = jsonio.load(tp, {}, retries=3) or {}
    # summary by group (what the panel shows before the detailed scan)
    groups = {}
    for f, r in out.items():
        g = r.get("group") if r["status"] == "ok" else ("✖ " + r["status"])
        G = groups.setdefault(g, {"n": 0, "dur": 0.0, "files": []})
        G["n"] += 1; G["dur"] += r.get("dur", 0.0); G["files"].append(f)
    if target == "auto":                      # frame of the dominant orientation (by footage duration)
        w = {}
        for f, r in out.items():
            if r.get("status") == "ok":
                w[r.get("orient")] = w.get(r.get("orient"), 0.0) + float(r.get("dur") or 0)
        o = max(w, key=w.get) if w else "dọc"
        target = (1920, 1080) if o == "ngang" else (1080, 1080) if o == "vuông" else (1080, 1920)
        tri.pop("auto", None) if tri.get("target") and tuple(tri["target"]) != target else None
    if target:
        tri["target"] = list(target)
    tri.setdefault("exclude_groups", []); tri.setdefault("exclude_files", []); tri.setdefault("include_files", [])
    if tri.get("target"):
        tri["suggest_exclude"] = suggest_excludes(groups, tri["target"], out)
        # learned: the editor keeps dropping groups that need upscaling → do it automatically (nxcore.prefs)
        if "auto" not in tri:
            try:
                from .prefs import rules
                if rules().get("triage_auto_exclude_upscale"):
                    tri["exclude_groups"] = list(tri["suggest_exclude"])
            except Exception:
                pass
            tri["auto"] = True
    tri["groups"] = {g: {"n": v["n"], "dur": round(v["dur"], 1), "sample": v["files"][:3]} for g, v in groups.items()}
    tri["broken"] = {f: r["reason"] for f, r in out.items() if r["status"] != "ok"}
    jsonio.save_atomic(tp, tri)
    active = apply_triage(src, out, tri)
    roles = {}
    try:
        from .triage import classify_all
        roles = classify_all(src, active, workers=max(2, min(4, workers)), progress=progress)
    except Exception as e:
        progress(f"triage: không phân loại được vai trò ({e})")

    # 1. Generate thumbnails for all active clips into _nexys_work/thumbs/<stem>.jpg
    try:
        generate_thumbs(src, active, workers=max(2, min(6, workers)), force=force, progress=progress)
    except Exception as e:
        progress(f"thumbs: tạo thumbnail lỗi ({e})")

    # 2. Auto-tag all clips into broll_tags.json and media.json
    try:
        from .triage import tag_all
        tag_all(src, active, roles=roles, progress=progress)
    except Exception as e:
        progress(f"tags: gắn nhãn lỗi ({e})")

    jsonio.save_atomic(mp, active)

    bad = len(tri["broken"])
    progress(f"media: {len(out)} video · {len(active)} dùng · {bad} lỗi/bỏ qua · {len(groups)} nhóm định dạng")
    for g, v in sorted(groups.items(), key=lambda kv: -kv[1]["n"]):
        progress(f"  {g}: {v['n']} clip · {v['dur'] / 60:.1f} phút" + ("  (loại khỏi phân tích)" if g in tri["exclude_groups"] else "  (gợi ý loại: phải phóng to)" if g in tri.get("suggest_exclude", []) else ""))
    return active


def suggest_excludes(groups, target, media):
    """Default: keep groups that fit the target frame (same orientation, or ≥ 1.5× resolution so a crop stays sharp)."""
    tw, th = target
    t_or = "dọc" if th > tw else "ngang" if tw > th else "vuông"
    ex = []
    for g, v in groups.items():
        if g.startswith("✖"):
            continue
        r = media[v["files"][0]]
        if r.get("orient") == t_or or r.get("orient") == "vuông":
            continue
        scale = max(tw / max(1, r["w"]), th / max(1, r["h"]))       # fill-crop zoom needed
        if scale > 1.0:                                                # would be upscaled → soft
            ex.append(g)
    return ex


def apply_triage(src, allm=None, tri=None, keep_all=False):
    W = os.path.join(src, "_nexys_work")
    if allm is None:
        allm = json.load(open(os.path.join(W, "media_all.json"), encoding="utf-8"))
    if tri is None:
        tri = jsonio.load(os.path.join(W, "triage.json"), {}, retries=3) or {}
    exg, exf, inf = set(tri.get("exclude_groups", [])), set(tri.get("exclude_files", [])), set(tri.get("include_files", []))
    if keep_all:
        active = {f: r for f, r in allm.items() if r.get("status") == "ok"}
    else:
        active = {f: r for f, r in allm.items()
                  if (r.get("status") == "ok" and r.get("group") not in exg and f not in exf) or (f in inf and r.get("w"))}
    jsonio.save_atomic(os.path.join(W, "media.json"), active)
    return active


def resolve_loose(name, media):
    """resolve() for names written inside free text ('dùng kỳ cf/DSC_1.MOV'): drop leading words until it matches."""
    w = str(name or "").strip().split(" ")
    for k in range(len(w)):
        r = resolve(" ".join(w[k:]), media)
        if r:
            return r
    return None


def resolve(name, media):
    """Map a clip name coming from an AI/model (maybe just the basename) back to its media key."""
    if name in media:
        return name
    n = name.replace("\\", "/")
    if n in media:
        return n
    base = os.path.basename(n).lower()
    hits = [k for k in media if os.path.basename(k).lower() == base]
    return hits[0] if len(hits) == 1 else None


def main():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-check", action="store_true", help="bỏ bước giải mã thử (nhanh hơn, không phát hiện file hỏng)")
    ap.add_argument("--target", default="", help="khung sequence, vd 1080x1920 → gợi ý loại nhóm không hợp")
    ap.add_argument("--apply", action="store_true", help="chỉ áp lại triage.json → media.json")
    ap.add_argument("--all", action="store_true", help="giữ lại đầy đủ 100%% video hợp lệ, không tự loại nhóm")
    a = ap.parse_args()
    if a.apply:
        act = apply_triage(a.src, keep_all=a.all); print(f"media: {len(act)} clip dùng"); print("DONE"); return
    tgt = "auto" if a.target.lower() == "auto" else (tuple(int(x) for x in a.target.lower().split("x")) if a.target else None)
    probe_all(a.src, a.workers, a.force, progress=lambda m: print(m, flush=True), check=not a.no_check, target=tgt)
    if a.all:
        apply_triage(a.src, keep_all=True)
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
