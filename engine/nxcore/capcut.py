"""CapCut desktop draft writer — Premiere / FCP7 XML (xmeml) → a CapCut project folder.

`nxcut.py build` already turns a plan into an xmeml timeline (V1 cuts with speed + scale, V2 cutaways, V3/V4 PNG cards,
music / VO / SFX with gain, duck keyframes and fades). This module converts that XML — or any xmeml exported from
Premiere / Resolve / FCP7 — into a CapCut draft: draft_content.json (+ the CapCut 8.7 Timelines/ copy),
draft_meta_info.json, a cover, and an entry in CapCut's draft index so the project shows on CapCut's home screen.

Object shapes come from capcut_tpl.json (scrubbed examples of real CapCut 8.7 drafts, made by dev/capcut/make_tpl.py);
only the fields that carry the edit are filled in here. CapCut keeps time in microseconds; xmeml in frames.

    python -m nxcore.capcut info
    python -m nxcore.capcut from-xml TIMELINE.xml [--srt X.srt] [--name N] [--root DIR] [--replace] [--no-register]

Safety: CapCut must be closed (it rewrites its index on exit); the index is backed up before every change; --replace
only ever overwrites a folder this module made (it carries jes_capcut.json).
"""
import argparse, collections, copy, datetime, json, os, re, shutil, subprocess, sys, time, urllib.parse, uuid
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
TPL_PATH = os.path.join(HERE, "capcut_tpl.json")
CC_HOME = os.path.join(os.environ.get("LOCALAPPDATA", ""), "CapCut")
INDEX = os.path.join(CC_HOME, "User Data", "Projects", "com.lveditor.draft", "root_meta_info.json")
MARK = "jes_capcut.json"                     # every draft we write carries it: --replace never touches anything else
STILL = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff")
PHOTO_US = 10800000000                       # CapCut gives a still a 3 h "duration"
TPS = 254016000000                           # Premiere ticks per second (cutPointTicks)
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def gid():
    """GUID text the way CapCut writes it: upper case, third group lower ("15EC194B-1E4F-4ad4-900B-9D7007EF0ACD")"""
    h = uuid.uuid4().hex.upper()
    return f"{h[:8]}-{h[8:12]}-{h[12:16].lower()}-{h[16:20]}-{h[20:]}"


def now_us():
    return int(time.time() * 1_000_000)


def fwd(p):
    return str(p).replace("\\", "/")


# ---------------------------------------------------------------- CapCut on this machine
def _vkey(v):
    return tuple(int(x) for x in re.findall(r"\d+", v))


def installed_version():
    apps = os.path.join(CC_HOME, "Apps")
    try:
        vs = [d for d in os.listdir(apps) if re.match(r"^\d+(\.\d+)+$", d) and os.path.isdir(os.path.join(apps, d))]
    except OSError:
        return ""
    return max(vs, key=_vkey) if vs else ""


def system_font():
    """CapCut's default caption font (Resources/Font/SystemFont/en.ttf of the newest installed build) — has Vietnamese"""
    v = installed_version()
    p = os.path.join(CC_HOME, "Apps", v, "Resources", "Font", "SystemFont", "en.ttf") if v else ""
    return fwd(p) if p and os.path.isfile(p) else ""


def running():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq CapCut.exe", "/NH"], capture_output=True, text=True,
                             creationflags=NOWIN, timeout=15).stdout
    except Exception:
        return False
    return "capcut.exe" in out.lower()


def load_index():
    with open(INDEX, encoding="utf-8") as fh:
        return json.load(fh)


def draft_root():
    """where CapCut keeps drafts: the root most index entries point at (the user may have moved it off C:)"""
    try:
        roots = collections.Counter(fwd(e.get("draft_root_path") or "") for e in load_index().get("all_draft_store", []))
        roots.pop("", None)
        if roots:
            return roots.most_common(1)[0][0]
    except Exception:
        pass
    return fwd(os.path.join(CC_HOME, "User Data", "Projects", "com.lveditor.draft"))


def info():
    return {"installed": bool(installed_version()), "version": installed_version(), "running": running(),
            "root": draft_root(), "index": os.path.isfile(INDEX), "font": system_font(),
            "template": json.load(open(TPL_PATH, encoding="utf-8")).get("made_from", "")}


# ---------------------------------------------------------------- media probe
_PROBE = {}


def probe(path):
    """{"dur" s, "w", "h" (display orientation), "video", "audio", "still", "size" bytes}"""
    if path in _PROBE:
        return _PROBE[path]
    r = {"dur": 0.0, "w": 0, "h": 0, "video": False, "audio": False, "still": path.lower().endswith(STILL),
         "size": os.path.getsize(path) if os.path.isfile(path) else 0}
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                              "stream=codec_type,width,height,duration:stream_tags=rotate:stream_side_data=rotation:format=duration",
                              "-of", "json", path], capture_output=True, text=True, encoding="utf-8", errors="replace",
                             creationflags=NOWIN, timeout=60).stdout
        j = json.loads(out or "{}")
        r["dur"] = float((j.get("format") or {}).get("duration") or 0)
        for s in j.get("streams") or []:
            if s.get("codec_type") == "audio":
                r["audio"] = True
            elif s.get("codec_type") == "video" and not r["video"]:
                r["video"] = True
                w, h = int(s.get("width") or 0), int(s.get("height") or 0)
                rot = (s.get("tags") or {}).get("rotate") or next((d.get("rotation") for d in s.get("side_data_list") or [] if "rotation" in d), 0)
                if abs(int(float(rot or 0))) % 180 == 90:
                    w, h = h, w
                r["w"], r["h"] = w, h
                r["dur"] = r["dur"] or float(s.get("duration") or 0)
    except Exception:
        pass
    _PROBE[path] = r
    return r


# ---------------------------------------------------------------- xmeml
def _path_of(url):
    u = urllib.parse.unquote(url or "")
    u = re.sub(r"^file://localhost/", "", u)
    u = re.sub(r"^file:///?", "", u)
    if re.match(r"^/[A-Za-z]:", u):
        u = u[1:]
    return fwd(u)


def _num(el, tag, d=-1):
    t = el.findtext(tag)
    try:
        return int(round(float(t)))
    except (TypeError, ValueError):
        return d


def _filters(it):
    """Basic Motion scale / rotation (+ scale keyframes), Time Remap speed, Audio Levels (+ keyframes), Opacity"""
    f = {"scale": None, "scale_kf": [], "rot": 0.0, "speed": 100.0, "level": None, "level_kf": [], "opacity": 1.0}
    for eff in it.iter("effect"):
        eid = (eff.findtext("effectid") or "").lower()
        for prm in eff.findall("parameter"):
            pid = (prm.findtext("parameterid") or "").lower()
            try:
                val = float(prm.findtext("value"))
            except (TypeError, ValueError):
                val = None
            kfs = []
            for k in prm.findall("keyframe"):
                try:
                    kfs.append((float(k.findtext("when")), float(k.findtext("value"))))
                except (TypeError, ValueError):
                    pass
            if val is None and not kfs:
                continue
            if eid == "basic" and pid == "scale":
                f["scale"], f["scale_kf"] = val, kfs
            elif eid == "basic" and pid == "rotation":
                f["rot"] = val or 0.0
            elif eid == "timeremap" and pid == "speed":
                f["speed"] = val or 100.0
            elif eid == "audiolevels" and pid == "level":
                f["level"], f["level_kf"] = val, kfs
            elif eid == "opacity" and pid == "opacity" and val is not None:
                f["opacity"] = max(0.0, min(1.0, val / 100.0))
    return f


def _tr(el, fps):
    ts, te = _num(el, "start"), _num(el, "end")
    al = (el.findtext("alignment") or "center").lower()
    try:
        cut = ts + float(el.findtext("cutPointTicks")) / TPS * fps
    except (TypeError, ValueError):
        cut = None
    if al == "start" or al == "start-black":
        cut = ts
    elif al == "end" or al == "end-black":
        cut = te
    elif cut is None or not (ts <= cut <= te):
        cut = (ts + te) / 2.0
    return {"start": ts, "end": te, "align": al, "cut": int(round(cut)),
            "name": eff.findtext("name") if (eff := el.find("effect")) is not None else ""}


def parse_xmeml(path):
    """→ {"name", "w", "h", "fps", "video": [[clip…]…], "audio": [[clip…]…], "notes": […]}; clip times in frames.
    A clip under a transition has start / end = -1 in xmeml and its in / out reach under the transition:
    here every clip is cut back to the cut point (CapCut overlaps the handles itself) and the transition is kept
    as clip["tr_out"] (dissolve to the next clip) or a fade (start-black / end-black)."""
    root = ET.parse(path).getroot()
    seq = root.find(".//sequence")
    if seq is None:
        raise ValueError("không thấy <sequence> trong XML")
    tb = _num(seq, "rate/timebase", 30)
    ntsc = (seq.findtext("rate/ntsc") or "").upper() == "TRUE"
    fps = tb * 1000 / 1001 if ntsc else float(tb)
    fmt = seq.find("media/video/format/samplecharacteristics")
    W = _num(fmt, "width", 1920) if fmt is not None else 1920
    H = _num(fmt, "height", 1080) if fmt is not None else 1080
    files = {}
    for f in root.iter("file"):
        if f.get("id") and f.findtext("pathurl"):
            files[f.get("id")] = _path_of(f.findtext("pathurl"))
    notes = []

    def track(tr_el, kind):
        items = list(tr_el)
        out = []
        for k, it in enumerate(items):
            if it.tag == "generatoritem":
                notes.append(f"{kind}: bỏ qua generator '{it.findtext('name')}' (CapCut không có tương đương)")
                continue
            if it.tag != "clipitem" or (it.findtext("enabled") or "TRUE").upper() == "FALSE":
                continue
            fe = it.find("file")
            p = files.get(fe.get("id")) if fe is not None else None
            if not p:
                notes.append(f"{kind}: clip '{it.findtext('name')}' không có đường dẫn file — bỏ qua")
                continue
            st, en, i0, o0 = _num(it, "start"), _num(it, "end"), _num(it, "in", 0), _num(it, "out", 0)
            prev = items[k - 1] if k > 0 and items[k - 1].tag == "transitionitem" else None
            nxt = items[k + 1] if k + 1 < len(items) and items[k + 1].tag == "transitionitem" else None
            c = {"path": p, "name": it.findtext("name") or os.path.basename(p), "fade_in": 0, "fade_out": 0, "tr_out": None,
                 "track_idx": _num(it, "sourcetrack/trackindex", 1), **_filters(it)}
            if st < 0:
                if prev is not None:
                    t = _tr(prev, fps)
                    if t["align"] == "start-black":
                        st, c["fade_in"] = t["start"], t["end"] - t["start"]
                    else:                       # the in point sits at the transition start: move it to the cut
                        i0 += t["cut"] - t["start"]
                        st = t["cut"]
                else:
                    st = out[-1]["end"] if out else 0
            if en < 0:
                if nxt is not None:
                    t = _tr(nxt, fps)
                    if t["align"] == "end-black":
                        en, c["fade_out"] = t["end"], t["end"] - t["start"]
                    else:
                        o0 -= t["end"] - t["cut"]
                        en = t["cut"]
                        c["tr_out"] = t
                else:
                    en = st + max(1, o0 - i0)
            elif nxt is not None:              # a fade sitting after a clip with a real end
                t = _tr(nxt, fps)
                if t["align"] == "end-black":
                    c["fade_out"] = t["end"] - t["start"]
            if prev is not None and c["fade_in"] == 0 and _tr(prev, fps)["align"] == "start-black":
                c["fade_in"] = _tr(prev, fps)["end"] - _tr(prev, fps)["start"]
            if en - st < 1:
                continue
            c.update(start=st, end=en, in_=i0, out_=o0)
            out.append(c)
        return out

    media = seq.find("media")
    V = [track(t, f"V{n + 1}") for n, t in enumerate(media.find("video").findall("track"))] if media.find("video") is not None else []
    A = [track(t, f"A{n + 1}") for n, t in enumerate(media.find("audio").findall("track"))] if media.find("audio") is not None else []
    return {"name": seq.findtext("name") or os.path.splitext(os.path.basename(path))[0], "w": W, "h": H, "fps": fps,
            "video": V, "audio": A, "notes": notes, "markers": len(seq.findall("marker"))}


def parse_srt(path):
    cues = []
    if not path or not os.path.isfile(path):
        return cues
    txt = open(path, encoding="utf-8-sig").read().replace("\r\n", "\n")
    ts = lambda s: sum(float(x) * m for x, m in zip(re.split(r"[:]", s.replace(",", ".")), (3600, 60, 1)))
    for blk in re.split(r"\n\s*\n", txt.strip()):
        ln = blk.strip().split("\n")
        k = next((i for i, l in enumerate(ln) if "-->" in l), -1)
        if k < 0:
            continue
        a, b = [x.strip() for x in ln[k].split("-->")[:2]]
        tx = "\n".join(ln[k + 1:]).strip()
        if tx:
            cues.append((ts(a), ts(b.split(" ")[0]), tx))
    return cues


# ---------------------------------------------------------------- draft builder
class Draft:
    def __init__(self, tpl, W, H, fps):
        self.T = tpl
        self.d = copy.deepcopy(tpl["draft"])
        self.mats = self.d["materials"]
        self.W, self.H, self.fps = W, H, fps
        self.d["id"] = gid()
        self.d["canvas_config"].update(width=W, height=H, ratio="original")
        self.d["fps"] = float(round(fps))
        t = now_us()
        self.d["create_time"], self.d["update_time"] = t // 1_000_000, t // 1_000_000
        self.tracks = []                         # [(type, [segment…], attribute, flag)]
        self.media = collections.OrderedDict()   # path → pool entry (draft_meta_info)
        self.notes = []

    def us(self, frames):
        return int(round(frames / self.fps * 1_000_000))

    def _add(self, kind, obj):
        self.mats.setdefault(kind, []).append(obj)
        return obj["id"]

    def _extras(self, lst, **over):
        ids = []
        for kind, x in lst:
            o = copy.deepcopy(x)
            o["id"] = gid()
            for k, v in over.get(kind, {}).items():
                o[k] = v
            ids.append(self._add(kind, o))
        return ids

    def _pool(self, path, pr, metetype):
        """one media-pool entry per file (CapCut's media panel); materials point at it through local_material_id"""
        if path not in self.media:
            e = copy.deepcopy(self.T["meta_media"])
            t = int(time.time())
            e.update(id=str(uuid.uuid4()), file_Path=path, extra_info=os.path.basename(path), metetype=metetype,
                     duration=PHOTO_US if pr["still"] else int(pr["dur"] * 1_000_000), width=pr["w"], height=pr["h"],
                     create_time=t, import_time=t, import_time_ms=now_us(),
                     roughcut_time_range={"start": 0, "duration": PHOTO_US if pr["still"] else int(pr["dur"] * 1_000_000)})
            self.media[path] = e
        return self.media[path]["id"]

    def _kf(self, prop, pts):
        if not pts:
            return []
        return [{"id": gid(), "keyframe_list": [{"curveType": "Line", "graphID": "", "id": gid(), "left_control": {"x": 0.0, "y": 0.0},
                                                 "right_control": {"x": 0.0, "y": 0.0}, "string_value": "", "time_offset": int(t), "values": [v]}
                                                for t, v in pts],
                 "material_id": "", "property_type": prop}]

    def fit_scale(self, pr, prem_scale):
        """Premiere scale % is of the source pixels; CapCut 1.0 = the whole picture fitted inside the canvas"""
        if not pr["w"] or not pr["h"]:
            return 1.0
        fit = min(self.W / pr["w"], self.H / pr["h"])
        return (prem_scale if prem_scale is not None else 100.0) / 100.0 / fit

    def video(self, c, tri, volume):
        pr = probe(c["path"])
        if not os.path.isfile(c["path"]):
            self.notes.append(f"thiếu file: {c['path']}")
        photo = pr["still"]
        sp = abs(c["speed"] or 100.0) / 100.0
        if (c["speed"] or 100) < 0:
            self.notes.append(f"{c['name']}: tua ngược chưa hỗ trợ — để chạy xuôi")
        t0, td = self.us(c["start"]), self.us(c["end"]) - self.us(c["start"])     # end − start in µs: cuts stay flush
        if photo:
            s0, sd, sp = 0, td, 1.0
        else:
            s0 = int(round(c["in_"] / self.fps * sp * 1_000_000))
            sd = int(round(td * sp))
            dur = int(pr["dur"] * 1_000_000)
            if dur and s0 + sd > dur:            # never past the end of the file
                s0 = max(0, dur - sd)
                sd = min(sd, dur - s0)
        m = copy.deepcopy(self.T["video_mat"])
        m.update(id=gid(), type="photo" if photo else "video", path=c["path"], material_name=os.path.basename(c["path"]),
                 duration=PHOTO_US if photo else int(pr["dur"] * 1_000_000), width=pr["w"], height=pr["h"],
                 has_audio=bool(pr["audio"]) and not photo, category_name="local",
                 local_material_id=self._pool(c["path"], pr, "photo" if photo else "video"))
        mid = self._add("videos", m)
        refs = self._extras([e for e in self.T["video_extras"] if e[0] in ("speeds", "placeholder_infos")],
                            speeds={"speed": sp})
        if c.get("tr_out"):
            tr = copy.deepcopy(self.T["transition"])
            tr.update(id=gid(), duration=self.us(c["tr_out"]["end"] - c["tr_out"]["start"]), path=_effect_path(tr.get("effect_id")))
            refs.append(self._add("transitions", tr))
        refs += self._extras([e for e in self.T["video_extras"] if e[0] not in ("speeds", "placeholder_infos")])
        sc = self.fit_scale(pr, c["scale"])
        kf = []
        if c["scale_kf"] and not photo:
            kf = self._kf("KFTypeScaleX", [(w / self.fps * sp * 1_000_000, self.fit_scale(pr, v)) for w, v in c["scale_kf"]])
        # keyframe time = the clip's own (source) time, as CapCut stores it; fades from / to black = opacity keys
        op = c.get("opacity", 1.0)
        fi = min(self.us(c["fade_in"]), td // 2) * sp
        fo = min(self.us(c["fade_out"]), td // 2) * sp
        al = ([(s0, 0.0), (s0 + fi, op)] if fi else []) + ([(s0 + sd - fo, op), (s0 + sd, 0.0)] if fo else [])
        kf += self._kf("KFTypeAlpha", al)
        s = copy.deepcopy(self.T["video_seg"])
        s.update(id=gid(), material_id=mid, extra_material_refs=refs, source_timerange={"start": s0, "duration": sd},
                 target_timerange={"start": t0, "duration": td}, speed=sp, volume=volume, last_nonzero_volume=1.0,
                 render_index=tri, track_render_index=tri, track_attribute=0, common_keyframes=kf, keyframe_refs=[],
                 visible=True, reverse=False, is_placeholder=False)
        s["clip"] = {"alpha": c.get("opacity", 1.0), "flip": {"horizontal": False, "vertical": False}, "rotation": float(c["rot"] or 0.0),
                     "scale": {"x": sc, "y": sc}, "transform": {"x": 0.0, "y": 0.0}}
        s["uniform_scale"] = {"on": True, "value": 1.0}
        return s

    def audio(self, c, tri):
        pr = probe(c["path"])
        if not os.path.isfile(c["path"]):
            self.notes.append(f"thiếu file: {c['path']}")
        sp = abs(c["speed"] or 100.0) / 100.0
        t0, td = self.us(c["start"]), self.us(c["end"]) - self.us(c["start"])
        s0 = int(round(c["in_"] / self.fps * sp * 1_000_000))
        sd = int(round(td * sp))
        dur = int(pr["dur"] * 1_000_000)
        if dur and s0 + sd > dur:
            sd = max(0, dur - s0)
            td = int(round(sd / sp))
        vos = pr["video"] and not pr["still"]
        m = copy.deepcopy(self.T["audio_mat_vos" if vos else "audio_mat"])
        stem = os.path.splitext(os.path.basename(c["path"]))[0]
        m.update(id=gid(), path=c["path"], name=stem if vos else os.path.basename(c["path"]), duration=dur,
                 local_material_id=self._pool(c["path"], pr, "video" if vos else "music"))
        mid = self._add("audios", m)
        ext = self.T["audio_extras"]
        refs = self._extras([e for e in ext if e[0] in ("speeds", "placeholder_infos")], speeds={"speed": sp})
        if c["fade_in"] or c["fade_out"]:
            refs.append(self._add("audio_fades", {"id": gid(), "type": "audio_fade", "fade_type": 0,
                                                   "fade_in_duration": self.us(c["fade_in"]), "fade_out_duration": self.us(c["fade_out"])}))
        refs += self._extras([e for e in ext if e[0] not in ("speeds", "placeholder_infos")])
        lv = c["level"] if c["level"] is not None else 1.0
        kf = self._kf("KFTypeVolume", [(w / self.fps * sp * 1_000_000, v) for w, v in c["level_kf"]]) if c["level_kf"] else []
        s = copy.deepcopy(self.T["audio_seg"])
        s.update(id=gid(), material_id=mid, extra_material_refs=refs, source_timerange={"start": s0, "duration": sd},
                 target_timerange={"start": t0, "duration": td}, speed=sp, volume=lv, last_nonzero_volume=1.0,
                 render_index=0, track_render_index=tri, track_attribute=0, common_keyframes=kf, keyframe_refs=[],
                 clip=None, uniform_scale=None, visible=True, reverse=False)
        return s

    def subtitle(self, a, b, text, tri, font):
        port = self.H > self.W
        size = 10.0 if port else 7.0               # CapCut's own caption size for this canvas
        text = text.replace("\r", "")
        st = {"fill": {"content": {"render_type": "solid", "solid": {"color": [1, 1, 1]}}},
              "font": {"path": font, "id": ""}, "size": size, "useLetterColor": True,
              "strokes": [{"content": {"render_type": "solid", "solid": {"color": [0, 0, 0]}}, "width": 0.06}],
              "range": [0, len(text.encode("utf-16-le")) // 2]}   # CapCut counts UTF-16 units (emoji = 2)
        m = copy.deepcopy(self.T["text_mat"])
        m.update(id=gid(), type="subtitle", add_type=1, content=json.dumps({"text": text, "styles": [st]}, ensure_ascii=False),
                 font_path=font, font_size=size, text_color="#ffffff", border_color="#000000", border_width=0.06, border_alpha=1.0,
                 alignment=1, is_rich_text=False, check_flag=7 if not m.get("check_flag") else m["check_flag"])
        mid = self._add("texts", m)
        refs = self._extras(self.T["text_extras"])
        s = copy.deepcopy(self.T["text_seg"])
        s.update(id=gid(), material_id=mid, extra_material_refs=refs, source_timerange=None,
                 target_timerange={"start": int(a * 1_000_000), "duration": max(100_000, int((b - a) * 1_000_000))},
                 speed=1.0, volume=1.0, render_index=14000, track_render_index=tri, track_attribute=0,
                 common_keyframes=[], keyframe_refs=[], visible=True)
        s["clip"] = {"alpha": 1.0, "flip": {"horizontal": False, "vertical": False}, "rotation": 0.0,
                     "scale": {"x": 1.0, "y": 1.0}, "transform": {"x": 0.0, "y": -0.65 if port else -0.73}}
        return s

    def add_track(self, ttype, segs, flag=0):
        segs = sorted(segs, key=lambda s: s["target_timerange"]["start"])
        for a, b in zip(segs, segs[1:]):           # CapCut forbids overlap on one track: trim the earlier clip
            ea = a["target_timerange"]["start"] + a["target_timerange"]["duration"]
            if ea > b["target_timerange"]["start"]:
                cut = b["target_timerange"]["start"] - a["target_timerange"]["start"]
                if a.get("source_timerange"):
                    a["source_timerange"]["duration"] = int(round(cut * a.get("speed", 1.0)))
                a["target_timerange"]["duration"] = cut
        t = copy.deepcopy(self.T["text_track" if ttype == "text" else "track"])
        t.update(id=gid(), type=ttype, attribute=0, flag=flag, segments=[s for s in segs if s["target_timerange"]["duration"] > 0])
        self.d["tracks"].append(t)
        return t

    def finish(self, name):
        end = 0
        for t in self.d["tracks"]:
            for s in t["segments"]:
                end = max(end, s["target_timerange"]["start"] + s["target_timerange"]["duration"])
        self.d["duration"] = end
        self.d["name"] = name
        v1 = next((t for t in self.d["tracks"] if t["type"] == "video"), None)
        if v1 is not None:                         # a magnetic main track would close gaps the edit means to keep
            pos, gap = 0, False
            for s in v1["segments"]:
                gap |= s["target_timerange"]["start"] > pos + 1000
                pos = s["target_timerange"]["start"] + s["target_timerange"]["duration"]
            self.d["config"]["maintrack_adsorb"] = not gap
        return self.d


def _effect_path(effect_id):
    """an effect CapCut already downloaded (transition) — else blank, CapCut fetches it by id"""
    d = os.path.join(CC_HOME, "User Data", "Cache", "effect", str(effect_id or ""))
    try:
        subs = [os.path.join(d, x) for x in os.listdir(d) if os.path.isdir(os.path.join(d, x))]
        return fwd(max(subs, key=os.path.getmtime)) if subs else ""
    except OSError:
        return ""


def build(xml_path, srt_path=None):
    """xmeml (+ srt) → (draft_content dict, Draft) — nothing written"""
    tl = parse_xmeml(xml_path)
    tpl = json.load(open(TPL_PATH, encoding="utf-8"))
    D = Draft(tpl, tl["w"], tl["h"], tl["fps"])
    D.notes += tl["notes"]
    # the clip's own sound: an audio clip of the same file at the same timeline span as a video clip is that clip's
    # audio (Premiere links them); CapCut plays it from the video segment, so it becomes that segment's volume
    linked, used = {}, set()
    for tr in tl["audio"]:
        for c in tr:
            k = (c["path"].lower(), c["start"], c["end"])
            if k not in linked:
                linked[k] = c
    vol_of = {}
    for tr in tl["video"]:
        for c in tr:
            k = (c["path"].lower(), c["start"], c["end"])
            if k in linked and not c["path"].lower().endswith(STILL):
                a = linked[k]
                vol_of[id(c)] = a["level"] if a["level"] is not None else 1.0
                used.add(k)
                if a["level_kf"]:
                    D.notes.append(f"{c['name']}: keyframe âm lượng của tiếng gốc chưa chuyển (dùng mức tĩnh)")
    tri = 0
    for n, tr in enumerate(tl["video"]):
        if not tr:
            continue
        D.add_track("video", [D.video(c, tri, vol_of.get(id(c), 0.0)) for c in tr])
        tri += 1
    cues = parse_srt(srt_path)
    if cues:
        font = system_font()
        D.add_track("text", [D.subtitle(a, b, tx, tri, font) for a, b, tx in cues], flag=1)
        tri += 1
    seen = set()
    for n, tr in enumerate(tl["audio"]):
        segs = []
        for c in tr:
            k = (c["path"].lower(), c["start"], c["end"])
            if k in used:
                continue
            k2 = k + (c["in_"],)
            if k2 in seen:                         # stereo pair split over two tracks: one CapCut segment
                continue
            seen.add(k2)
            segs.append(D.audio(c, tri))
        if segs:
            D.add_track("audio", segs)
            tri += 1
    return D.finish(tl["name"]), D, tl


# ---------------------------------------------------------------- write the folder + register it
def _dump(path, obj, compact=True, indent=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".jes_tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":") if compact else None, indent=indent))
    os.replace(tmp, path)


def _cover(D, out):
    v = next((t for t in D.d["tracks"] if t["type"] == "video" and t["segments"]), None)
    if not v:
        return False
    s = v["segments"][0]
    m = next(x for x in D.mats["videos"] if x["id"] == s["material_id"])
    at = (s["source_timerange"]["start"] if m["type"] == "video" else 0) / 1_000_000 + 0.2
    W, H = (480, int(480 * D.H / D.W)) if D.W >= D.H else (int(480 * D.W / D.H), 480)
    vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    cmd = ["ffmpeg", "-v", "error", "-y"] + (["-ss", f"{at:.3f}"] if m["type"] == "video" else []) + ["-i", m["path"], "-frames:v", "1", "-vf", vf, out]
    try:
        subprocess.run(cmd, capture_output=True, creationflags=NOWIN, timeout=60)
    except Exception:
        return False
    return os.path.isfile(out)


def write(D, name, root=None, replace=False, register=True, src=""):
    root = fwd(root or draft_root())
    name = re.sub(r'[<>:"/\\|?*]+', "_", name).strip(" .") or "JES"
    fold = f"{root}/{name}"
    if os.path.exists(fold):
        if not replace:
            raise FileExistsError(f"đã có dự án '{name}' trong {root} — đổi tên hoặc dùng --replace")
        if not os.path.isfile(os.path.join(fold, MARK)):
            raise PermissionError(f"'{fold}' không phải dự án do Justin EDIT Suite tạo — không ghi đè")
    if register and running():
        raise RuntimeError("CapCut đang mở — đóng CapCut rồi chạy lại (CapCut ghi đè danh sách dự án khi thoát)")
    if os.path.exists(fold):
        shutil.rmtree(fold)
    content = D.d
    content["name"] = ""                          # CapCut leaves it blank; the project name lives in the meta / index
    tid = content["id"]
    t_us = now_us()
    _dump(f"{fold}/draft_content.json", content)
    _dump(f"{fold}/Timelines/{tid}/draft_content.json", content)
    proj = copy.deepcopy(D.T["project"])
    proj.update(id=gid(), main_timeline_id=tid, create_time=t_us, update_time=t_us,
                timelines=[{"create_time": t_us, "id": tid, "is_marked_delete": False, "name": "Timeline 01", "update_time": t_us}])
    _dump(f"{fold}/Timelines/project.json", proj)
    _dump(f"{fold}/draft_biz_config.json", {"timeline_settings": {tid: {"linkage_enabled": False}}}, compact=False, indent=4)
    _dump(f"{fold}/timeline_layout.json", {"dockItems": [{"dockIndex": 0, "ratio": 1, "timelineIds": [tid], "timelineNames": ["Timeline 01"]}],
                                           "layoutOrientation": 1})
    cover = _cover(D, f"{fold}/draft_cover.jpg")
    size = sum(probe(p)["size"] for p in D.media)
    did = gid()
    meta = copy.deepcopy(D.T["meta"])
    removable = _removable(root)
    meta.update(draft_fold_path=fold, draft_id=did, draft_name=name, draft_root_path=root.replace("/", "\\"),
                draft_cover="draft_cover.jpg" if cover else "", draft_removable_storage_device=removable,
                draft_timeline_materials_size_=size, tm_draft_create=t_us, tm_draft_modified=t_us, tm_duration=content["duration"])
    for g in meta["draft_materials"]:
        if g["type"] == 0:
            g["value"] = list(D.media.values())
    _dump(f"{fold}/draft_meta_info.json", meta)
    _dump(f"{fold}/{MARK}", {"made_by": "Justin EDIT Suite", "source": fwd(src), "created": datetime.datetime.now().isoformat(timespec="seconds"),
                              "draft_id": did, "timeline_id": tid}, compact=False, indent=1)
    res = {"folder": fold, "name": name, "draft_id": did, "duration_s": round(content["duration"] / 1e6, 2), "cover": cover,
           "registered": False, "backup": ""}
    if register:
        res.update(_register(fold, name, did, root, size, t_us, content["duration"], cover))
    return res


def _removable(root):
    """CapCut notes the drive letter of drafts kept off the system drive — copy what its own drafts here say"""
    try:
        for e in os.scandir(root):
            mp = os.path.join(e.path, "draft_meta_info.json")
            if e.is_dir() and os.path.isfile(mp) and not os.path.isfile(os.path.join(e.path, MARK)):
                return json.load(open(mp, encoding="utf-8")).get("draft_removable_storage_device", "")
    except OSError:
        pass
    return ""


def _register(fold, name, did, root, size, t_us, dur, cover):
    if not os.path.isfile(INDEX):
        return {"registered": False, "note": "không thấy root_meta_info.json — mở CapCut, dự án sẽ được quét trong thư mục nháp"}
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = INDEX + f".jes_bak_{stamp}"
    shutil.copy2(INDEX, bak)
    olds = sorted(x for x in os.listdir(os.path.dirname(INDEX)) if x.startswith("root_meta_info.json.jes_bak_"))
    for x in olds[:-5]:                           # keep the five newest backups
        try:
            os.remove(os.path.join(os.path.dirname(INDEX), x))
        except OSError:
            pass
    idx = load_index()
    tpl = json.load(open(TPL_PATH, encoding="utf-8"))
    store = [e for e in idx.get("all_draft_store", []) if fwd(e.get("draft_fold_path", "")).rstrip("/") != fold]
    e = copy.deepcopy(tpl["index_entry"])
    e.update(draft_cover=(fold + "\\draft_cover.jpg") if cover else "", draft_fold_path=fold, draft_id=did,
             draft_json_file=fold + "\\draft_content.json", draft_name=name, draft_root_path=root.replace("/", "\\"),
             draft_timeline_materials_size=size, tm_draft_create=t_us, tm_draft_modified=t_us, tm_duration=dur)
    store.insert(0, e)
    idx["all_draft_store"] = store
    if isinstance(idx.get("draft_ids"), int):
        idx["draft_ids"] += 1
    _dump(INDEX, idx)
    return {"registered": True, "backup": fwd(bak)}


def summary(D, tl):
    by = collections.Counter(t["type"] for t in D.d["tracks"])
    n = collections.Counter(t["type"] for t in D.d["tracks"] for _ in t["segments"])
    return {"canvas": f"{D.W}x{D.H}", "fps": round(D.fps, 3), "tracks": dict(by), "segments": dict(n),
            "slowmo": sum(1 for t in D.d["tracks"] for s in t["segments"] if t["type"] == "video" and abs(s["speed"] - 1) > 1e-6),
            "transitions": len(D.mats.get("transitions", [])), "media": len(D.media),
            "missing": sorted({p for p in D.media if not os.path.isfile(p)}),
            "notes": list(dict.fromkeys(D.notes))[:40], "markers_skipped": tl.get("markers", 0)}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nxcore.capcut")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("info")
    b = sp.add_parser("from-xml")
    b.add_argument("xml")
    b.add_argument("--srt", default=None, help="phụ đề → track chữ CapCut (mặc định: <xml>.srt nếu có)")
    b.add_argument("--name", default="")
    b.add_argument("--root", default="")
    b.add_argument("--replace", action="store_true")
    b.add_argument("--no-register", action="store_true")
    b.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "info":
        print(json.dumps(info(), ensure_ascii=False, indent=1))
        return 0
    srt = a.srt if a.srt is not None else (os.path.splitext(a.xml)[0] + ".srt")
    content, D, tl = build(a.xml, srt if srt and os.path.isfile(srt) else None)
    rep = summary(D, tl)
    if not a.dry_run:
        rep.update(write(D, a.name or tl["name"], a.root or None, replace=a.replace, register=not a.no_register, src=a.xml))
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (FileExistsError, PermissionError, RuntimeError, ValueError) as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(2)
