"""Build engine/nxcore/capcut_tpl.json from real CapCut drafts on this machine (dev only, read only on the drafts).

One example of every object the CapCut writer emits — draft skeleton, track, video / photo / audio / text segment +
material, the per-segment helper materials (speed, canvas, …), a transition, draft_meta_info / index entry — taken
from the newest drafts that hold one, then scrubbed: local paths, request ids, device / disk / MAC hashes and user
content are blanked so the file can ship. The writer fills every field it needs per clip.

    python dev/capcut/make_tpl.py ["F:/CapCut/CapCut Drafts"]
"""
import copy, json, os, sys

ROOT = sys.argv[1] if len(sys.argv) > 1 else "F:/CapCut/CapCut Drafts"
OUT = os.path.join(os.path.dirname(__file__), "..", "..", "engine", "nxcore", "capcut_tpl.json")
VERSION_MIN = "171.0.0"          # CapCut 8.7 — only drafts this new feed the templates


def drafts():
    ents = [e for e in os.scandir(ROOT) if e.is_dir() and os.path.isfile(os.path.join(e.path, "draft_content.json"))]
    for e in sorted(ents, key=lambda e: e.stat().st_mtime, reverse=True):
        try:
            d = json.load(open(os.path.join(e.path, "draft_content.json"), encoding="utf-8"))
        except Exception:
            continue
        yield e.path, d


def index(d):
    r = {}
    for k, v in d["materials"].items():
        if isinstance(v, list):
            for x in v:
                if isinstance(x, dict) and "id" in x:
                    r[x["id"]] = (k, x)
    return r


SCRUB_KEYS = {"request_id", "device_id", "hard_disk_id", "mac_address", "local_material_id", "beats_url", "beats_path",
              "melody_url", "melody_path", "music_id", "category_id", "category_name", "name", "material_name",
              "path", "media_path", "file_Path", "extra_info", "draft_fold_path", "draft_root_path", "draft_cover",
              "draft_json_file", "draft_name", "draft_id", "id", "resource_id_unused"}


def scrub(o, keep=()):
    """blank every identifying string (paths, ids, hashes, names); numbers / bools / structure stay"""
    if isinstance(o, dict):
        return {k: (scrub(v, keep) if (k not in SCRUB_KEYS or k in keep or not isinstance(v, str)) else "") for k, v in o.items()}
    if isinstance(o, list):
        return [scrub(x, keep) for x in o]
    return o


def find_seg(pred):
    for p, d in drafts():
        if d.get("new_version", "0") < VERSION_MIN:
            continue
        I = index(d)
        for ti, t in enumerate(d["tracks"]):
            for s in t["segments"]:
                k, m = I.get(s["material_id"], (None, {}))
                if pred(t, s, k, m, I):
                    return p, d, t, s, m, [I.get(r) for r in s["extra_material_refs"]]
    raise SystemExit("no example for a template — open a draft with one in CapCut 8.7 first")


def extras(ex, drop=()):
    return [[k, scrub(x)] for k, x in (e for e in ex if e) if k not in drop]


tpl = {"made_from": ""}
p, d, t, s, m, ex = find_seg(lambda t, s, k, m, I: k == "videos" and m.get("type") == "video" and t["type"] == "video")
tpl["made_from"] = f"CapCut {d['platform']['app_version']} (new_version {d['new_version']})"
draft = copy.deepcopy(d)
draft["materials"] = {k: ([] if isinstance(v, list) else v) for k, v in d["materials"].items()}
draft["tracks"] = []
for k in ("platform", "last_modified_platform"):
    draft[k] = scrub(draft[k])
draft["config"]["video_mute"] = False
draft.update({"id": "", "name": "", "path": "", "duration": 0, "cover": None, "retouch_cover": None, "static_cover_image_path": "",
              "create_time": 0, "update_time": 0, "keyframe_graph_list": [], "relationships": [], "time_marks": None})
draft["keyframes"] = {k: [] for k in draft["keyframes"]}
tpl["draft"] = draft
tpl["track"] = scrub({k: v for k, v in t.items() if k != "segments"})
tpl["video_seg"] = scrub(s)
tpl["video_mat"] = scrub(m)
tpl["video_extras"] = extras(ex, drop=("transitions",))

p, d, t, s, m, ex = find_seg(lambda t, s, k, m, I: any((I.get(r) or ("",))[0] == "transitions" for r in s["extra_material_refs"]))
tr = [x for k, x in (e for e in ex if e) if k == "transitions"][0]
tpl["transition"] = scrub(tr, keep=("name", "category_name", "category_id"))

p, d, t, s, m, ex = find_seg(lambda t, s, k, m, I: k == "audios" and m.get("type") == "extract_music")
tpl["audio_seg"] = scrub(s)
tpl["audio_mat"] = scrub(m)
tpl["audio_extras"] = extras(ex, drop=("vocal_beautifys",))
p, d, t, s, m, ex = find_seg(lambda t, s, k, m, I: k == "audios" and m.get("type") == "video_original_sound")
tpl["audio_mat_vos"] = scrub(m)

p, d, t, s, m, ex = find_seg(lambda t, s, k, m, I: k == "texts" and not m.get("is_rich_text") and len(json.loads(m["content"]).get("styles", [])) == 1)
tpl["text_track"] = scrub({k: v for k, v in t.items() if k != "segments"})
tpl["text_seg"] = scrub(s)
mt = scrub(m)
mt.update({"content": "", "font_path": "", "text_color": "#ffffff", "has_shadow": False, "shadow_color": "", "fonts": []})
tpl["text_mat"] = mt
tpl["text_extras"] = [[k, dict(x, animations=[])] for k, x in extras(ex)]

# draft_meta_info.json + one media-pool entry + the root index entry, from the newest 8.7 draft
for p, d in drafts():
    if d.get("new_version", "0") >= VERSION_MIN and os.path.isfile(os.path.join(p, "draft_meta_info.json")):
        meta = json.load(open(os.path.join(p, "draft_meta_info.json"), encoding="utf-8"))
        media = [v for g in meta["draft_materials"] for v in g["value"] if g["type"] == 0 and v.get("metetype") == "video"]
        tpl["meta_media"] = scrub(media[0]) if media else None
        meta["draft_materials"] = [{"type": g["type"], "value": []} for g in meta["draft_materials"]]
        tpl["meta"] = scrub(meta)
        proj = json.load(open(os.path.join(p, "Timelines", "project.json"), encoding="utf-8"))
        tpl["project"] = scrub(proj)
        tpl["biz_config"] = {"timeline_settings": {"": {"linkage_enabled": False}}}
        tpl["layout"] = scrub(json.load(open(os.path.join(p, "timeline_layout.json"), encoding="utf-8")))
        break
IDX = os.path.join(os.environ["LOCALAPPDATA"], "CapCut", "User Data", "Projects", "com.lveditor.draft", "root_meta_info.json")
idx = json.load(open(IDX, encoding="utf-8"))
tpl["index_entry"] = scrub(idx["all_draft_store"][0])

s_ = json.dumps(tpl, ensure_ascii=False, indent=1)
for bad in ("C:/Users", "C:\\\\Users", "D:/", "F:/", "AppData"):
    assert bad not in s_, f"template still holds a local path: {bad}"
open(OUT, "w", encoding="utf-8").write(s_)
print("wrote", os.path.normpath(OUT), len(s_), "bytes ·", tpl["made_from"])
