"""Source classification and role triage for Justin EDIT Suite.

Classifies every footage file into a functional role:
  - a_roll: Main spoken dialogue / host / interview / talking head (usually tripod, voice > -32dB)
  - b_roll: Cutaways, visuals, landscape, inserts (usually shorter, high FPS 50/60p, or ambient audio)
  - audio_master: Dedicated sound recording (mic, lavalier, zoom, bgm, voiceover)
  - screen: Screen recording / slide presentation
  - junk: Corrupted, test shot (< 1.0s), or lens cap black

Priority:
  1. Folder name / directory structure (e.g. A_Roll/, B_Roll/, CamA/, Audio/)
  2. Metadata analysis (duration, FPS, audio presence) + Fast 2.5s speech RMS energy test (-32dB threshold)
"""
import json
import os
import re
import subprocess
import unicodedata
from concurrent.futures import ThreadPoolExecutor

from . import jsonio

AROLL_DIR = re.compile(r"(^|[/\\])(a[_-]?roll|cam[_-]?a|master|main|host|interview|thoại|thoai|nói|noi|diễn|dien|phỏng[_-]?vấn|phong[_-]?van|talking)($|[/\\])", re.I)
BROLL_DIR = re.compile(r"(^|[/\\])(b[_-]?roll|cutaway|insert|cảnh|canh|drone|gimbal|b-roll)($|[/\\])", re.I)
AUDIO_DIR = re.compile(r"(^|[/\\])(audio|mic|sound|voice|vo|thu[_-]?âm|thu[_-]?am|bgm|music)($|[/\\])", re.I)
SCREEN_DIR = re.compile(r"(^|[/\\])(screen|capture|slide|obs|recording)($|[/\\])", re.I)

AROLL_FILE = re.compile(r"^(talk|host|interview|thoai|noi|main|master|cam_?a)", re.I)
BROLL_FILE = re.compile(r"^(broll|cutaway|insert|drone|gimbal|b_)", re.I)


# bump when classification logic changes → cached roles.json entries are re-classified
# v2: measure_audio_rms fixed (was always -99 dB → every clip b_roll)
# v3: speech decided by a full-clip Silero VAD (nxcore.vad) instead of one 2.5 s RMS slice + the ≥47 fps rule
ROLE_CACHE_V = 3


def measure_audio_rms(path, dur):
    """Fast ffmpeg volumedetect on a 2.5s slice in the first 25% of the clip.
    Returns mean_volume in dB (e.g. -24.5) or -99.0 on silence/error."""
    if dur <= 0:
        return -99.0
    ss = max(0.5, min(dur * 0.2, 5.0))
    t = min(2.5, max(0.5, dur - ss))
    cmd = [
        # volumedetect prints mean_volume at INFO level — "-v error" would hide it and every clip reads -99 dB
        "ffmpeg", "-hide_banner", "-v", "info", "-nostdin",
        "-ss", f"{ss:.2f}", "-t", f"{t:.2f}",
        "-i", path,
        "-vn", "-af", "volumedetect",
        "-f", "null", "-"
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12)
        m = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", proc.stderr or "")
        if m:
            return float(m.group(1))
    except Exception:
        pass
    return -99.0


def classify_file(f, meta, full_path, min_speech_rms=-32.0, vad_entry=None):
    """Determine role: 'a_roll' | 'b_roll' | 'audio_master' | 'screen' | 'junk'."""
    dur = float(meta.get("dur") or 0)
    fps = float(meta.get("fps") or 0)
    has_audio = bool(meta.get("audio"))
    folder = (meta.get("folder") or os.path.dirname(f)).replace("\\", "/")
    base = os.path.basename(f)

    # 0. Check junk / invalid
    if meta.get("status") in ("broken", "skip") or dur < 1.0:
        return "junk", "File quá ngắn hoặc lỗi"

    # 1. Priority 1: Folder name
    if AROLL_DIR.search(folder) or AROLL_DIR.search(f):
        return "a_roll", "Thư mục A-Roll/Thoại"
    if BROLL_DIR.search(folder) or BROLL_DIR.search(f):
        return "b_roll", "Thư mục B-Roll/Cắt cảnh"
    if AUDIO_DIR.search(folder) or AUDIO_DIR.search(f):
        return "audio_master", "Thư mục Audio/Mic"
    if SCREEN_DIR.search(folder) or SCREEN_DIR.search(f):
        return "screen", "Thư mục quay màn hình"

    # 2. Filename hints
    if AROLL_FILE.search(base):
        return "a_roll", "Tên file A-Roll"
    if BROLL_FILE.search(base):
        return "b_roll", "Tên file B-Roll"

    # 3. Priority 2: Metadata rules
    if not has_audio:
        return "b_roll", "Không có âm thanh (B-roll hình ảnh)"

    # 3b. Full-clip VAD (nxcore.vad) — measured speech beats every guess below (60p vlog cams talk too)
    if vad_entry:
        from nxcore.vad import role_hint
        hint = role_hint(vad_entry, dur)
        if hint: return hint
        sp = float(vad_entry.get("speech") or 0)
        return "b_roll", f"VAD: chỉ có {sp:.1f}s giọng nói / {dur:.0f}s (tiếng nền, câu lẻ)"

    # High frame rate >= 47fps (50p, 60p, 120p) is typically shot for slow motion B-roll
    if fps >= 47.0:
        return "b_roll", f"FPS cao ({fps:.0f}p) cho slow-motion"

    # 4. Audio speech energy test (RMS threshold -32dB)
    mean_db = measure_audio_rms(full_path, dur)
    has_voice = (mean_db > min_speech_rms)

    if dur >= 8.0:
        if has_voice:
            return "a_roll", f"Thời lượng dài ({dur:.1f}s) & âm lượng giọng nói rõ ({mean_db:.1f} dB > {min_speech_rms} dB)"
        else:
            return "b_roll", f"Thời lượng {dur:.1f}s nhưng chỉ có tiếng ồn nền ({mean_db:.1f} dB <= {min_speech_rms} dB)"
    else:
        # Clips < 8s are almost always B-roll or short cutaways
        if has_voice and dur >= 5.0:
            return "a_roll", f"Clip ngắn ({dur:.1f}s) nhưng có thoại rõ ({mean_db:.1f} dB)"
        return "b_roll", f"Clip ngắn ({dur:.1f}s) B-roll"


COMMON_SCENE_KEYWORDS = {
    # Food & Drink
    "cafe": "Quán cafe", "ca phe": "Cà phê", "coffee": "Cà phê", "tra": "Trà", "milk tea": "Trà sữa",
    "tra sua": "Trà sữa", "nuoc": "Đồ uống", "drink": "Đồ uống", "cocktail": "Cocktail", "bar": "Quầy bar",
    "pha che": "Pha chế", "bartender": "Pha chế", "barista": "Barista",
    "an": "Ăn uống", "mon an": "Món ăn", "food": "Món ăn", "dish": "Món ăn", "am thuc": "Ẩm thực",
    "nha hang": "Nhà hàng", "quan an": "Quán ăn", "bep": "Nhà bếp", "nau": "Nấu nướng", "cook": "Nấu ăn",

    # Nature & Landscape
    "hoang hon": "Hoàng hôn", "sunset": "Hoàng hôn", "binh minh": "Bình minh", "sunrise": "Bình minh",
    "bien": "Biển", "beach": "Bãi biển", "sea": "Biển", "song": "Bờ sông", "river": "Bờ sông",
    "ho": "Hồ nước", "lake": "Hồ nước", "nui": "Núi đồi", "mountain": "Núi đồi", "rung": "Rừng cây",
    "forest": "Rừng cây", "cay": "Cây cối", "troi": "Bầu trời", "sky": "Bầu trời", "may": "Mây trời",
    "mua": "Mưa", "nang": "Nắng",

    # Travel & Urban
    "du lich": "Du lịch", "travel": "Du lịch", "trip": "Chuyến đi", "checkin": "Check-in",
    "duong pho": "Đường phố", "street": "Đường phố", "pho": "Phố xá", "xe": "Xe cộ", "o to": "Ô tô",
    "xe may": "Xe máy", "traffic": "Giao thông", "thanh pho": "Thành phố", "city": "Đô thị",
    "toa nha": "Tòa nhà", "building": "Kiến trúc",

    # People & Action
    "phong van": "Phỏng vấn", "interview": "Phỏng vấn", "talk": "Trò chuyện", "noi chuyen": "Trò chuyện",
    "cuoi": "Nụ cười", "smile": "Nụ cười", "di bo": "Đi bộ", "walk": "Đi dạo", "chay": "Chạy bộ",
    "tay": "Cận cảnh tay", "hand": "Bàn tay", "chan dung": "Chân dung", "portrait": "Chân dung",
    "khach": "Khách hàng", "dien vien": "Diễn viên", "idol": "Idol",

    # Camera / Tech / Event
    "flycam": "Flycam", "drone": "Flycam", "gimbal": "Gimbal", "gopro": "Action Cam",
    "san khau": "Sân khấu", "stage": "Sân khấu", "event": "Sự kiện", "su kien": "Sự kiện",
    "khan gia": "Khán giả", "crowd": "Đám đông", "anh sang": "Ánh sáng", "light": "Ánh sáng",
    "am nhac": "Âm nhạc", "music": "Âm nhạc", "dance": "Nhảy / Múa", "bieu dien": "Biểu diễn",
    "man hinh": "Màn hình", "screen": "Màn hình", "laptop": "Laptop", "pc": "Máy tính", "code": "Lập trình"
}



# Vietnamese spelling of the ASCII keywords above. Plain ASCII "an" / "ho" / "song" / "may" are other words too
# ("Hội An", "Hồ Chí Minh", "song ca", "May 2026"): keywords shorter than 3 letters only match through their
# diacritic word ("ăn", "hồ"); longer ones match either form. Always whole words, only in the FILE NAME.
KEYWORD_VI = {
    "ca phe": "cà phê", "tra": "trà", "tra sua": "trà sữa", "nuoc": "nước", "pha che": "pha chế", "an": "ăn",
    "mon an": "món ăn", "am thuc": "ẩm thực", "nha hang": "nhà hàng", "quan an": "quán ăn", "bep": "bếp", "nau": "nấu",
    "hoang hon": "hoàng hôn", "binh minh": "bình minh", "bien": "biển", "song": "sông", "ho": "hồ", "nui": "núi",
    "rung": "rừng", "cay": "cây", "troi": "trời", "may": "mây", "mua": "mưa", "nang": "nắng", "du lich": "du lịch",
    "duong pho": "đường phố", "pho": "phố", "o to": "ô tô", "xe may": "xe máy", "thanh pho": "thành phố",
    "toa nha": "tòa nhà", "phong van": "phỏng vấn", "noi chuyen": "nói chuyện", "cuoi": "cười", "di bo": "đi bộ",
    "chay": "chạy", "chan dung": "chân dung", "khach": "khách", "dien vien": "diễn viên", "san khau": "sân khấu",
    "su kien": "sự kiện", "khan gia": "khán giả", "anh sang": "ánh sáng", "am nhac": "âm nhạc", "bieu dien": "biểu diễn",
    "man hinh": "màn hình",
}
KW_MIN_ASCII = 3


def _name_text(f):
    """File name (no folder, no extension) → lowercase NFC words separated by single spaces."""
    stem_name = os.path.splitext(os.path.basename(str(f).replace("\\", "/")))[0]
    t = unicodedata.normalize("NFC", stem_name).lower()
    return " ".join(re.split(r"[\s_\-.,()\[\]+]+", t)).strip()


def _has_word(text, phrase):
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text) is not None


def keyword_tags(f):
    """Scene tags from the file name: whole words only; ASCII keywords < 3 letters need their Vietnamese spelling.
    Longer phrases win: "xe may" is Xe máy, not also "may" (Mây trời)."""
    text = _name_text(f)
    if not text:
        return []
    order = {v: i for i, v in enumerate(dict.fromkeys(COMMON_SCENE_KEYWORDS.values()))}
    forms = []
    for k, v in COMMON_SCENE_KEYWORDS.items():
        if len(k.replace(" ", "")) >= KW_MIN_ASCII:
            forms.append((k, v))
        if k in KEYWORD_VI:
            forms.append((KEYWORD_VI[k], v))
    hit = set()
    for x, v in sorted(forms, key=lambda kv: -len(kv[0])):
        pat = re.compile(r"(?<!\w)" + re.escape(x) + r"(?!\w)")
        if pat.search(text):
            hit.add(v)
            text = pat.sub(" # ", text)          # consumed: shorter keywords can't match inside this phrase
    return sorted(hit, key=lambda v: order.get(v, 999))


def extract_clip_tags(f, meta, role):
    tags = []
    # 1. Role tag
    if role == "a_roll":
        tags.append("A-Roll (Thoại)")
    elif role == "b_roll":
        tags.append("B-Roll")
    elif role == "screen":
        tags.append("Màn hình")
    elif role == "audio_master":
        tags.append("Audio Mic")
    elif role == "junk":
        tags.append("Lỗi / Loại")

    # 2. Semantic matching on the file name (folders become their own tags below)
    for v in keyword_tags(f):
        if v not in tags:
            tags.append(v)

    # Clean folder names as category tags
    folder = (meta.get("folder") or os.path.dirname(f)).replace("\\", "/")
    if folder and folder not in (".", "", "(gốc)"):
        parts = [p.strip() for p in folder.split("/") if p.strip()]
        for p in parts:
            clean_p = re.sub(r"^\d+[\s_-]*", "", p).replace("_", " ").replace("-", " ").strip()
            if clean_p and len(clean_p) >= 3 and not re.match(r"^(dcim|\d+msdcf|clip|clips|video|videos|source|sources|footage|raw|cam[a-z]?)$", clean_p, re.I):
                cap_tag = " ".join(w.capitalize() for w in clean_p.split())
                if cap_tag not in tags and not any(cap_tag.lower() in t.lower() for t in tags):
                    tags.append(cap_tag)

    # Meaningful name tokens from filename
    IGNORE_NAME_WORDS = {"clip", "clips", "video", "videos", "source", "sources", "footage", "raw",
                         "dcim", "100msdcf", "cama", "camb", "camc", "cam1", "cam2", "take", "mov", "mp4",
                         "data", "nx", "lower", "rec", "shot", "broll", "aroll"}
    base = os.path.basename(f)
    stem_name = os.path.splitext(base)[0]
    tokens = re.split(r"[\s_\-]+", stem_name)
    for t in tokens:
        t_clean = re.sub(r"^\d+|\d+$", "", t).strip()
        if len(t_clean) >= 3 and t_clean.lower() not in IGNORE_NAME_WORDS and not re.match(r"^(c\d+|img|dji|dsc|gopr)$", t_clean, re.I):
            cap_n = t_clean.capitalize()
            if cap_n not in tags and not any(cap_n.lower() in x.lower() for x in tags):
                tags.append(cap_n)

    # 3. Technical tags
    fps = float(meta.get("fps") or 0)
    if fps >= 95.0:
        tags.append("100/120p Slow-mo")
    elif fps >= 47.0:
        tags.append("50/60p Slow-mo")

    res = meta.get("res_label", "")
    if "4K" in res:
        tags.append("4K UHD")
    elif "2.7K" in res:
        tags.append("2.7K")

    orient = meta.get("orient")
    if orient == "dọc":
        tags.append("Dọc 9:16")
    elif orient == "vuông":
        tags.append("Vuông 1:1")

    cam = meta.get("cam")
    if cam and cam not in ("Khác", "?") and cam not in tags:
        tags.append(cam)

    if not meta.get("audio"):
        tags.append("Không tiếng")

    return tags


TAG_JUNK = ("clip", "clips", "video", "videos", "footage", "raw", "cama", "camb")


def _merge_tags(cur_tags, computed):
    """Fold tag_all's output into the broll_tags dict IN PLACE. tag_all owns only the auto tags and the creation of
    new entries; every field already present (desc, segs_mark, pins, role, use, …) is the editor's and is kept."""
    updated = 0
    for f, (role, new_tags, dur) in computed.items():
        e = cur_tags.get(f)
        if isinstance(e, dict):
            existing = [t for t in list(e.get("tags") or []) if str(t).lower() not in TAG_JUNK]
            for t in new_tags:
                if t not in existing:
                    existing.append(t)
            e["tags"] = existing
            if "role" not in e:
                e["role"] = role
        else:
            cur_tags[f] = {
                "role": role,
                "tags": list(new_tags),
                "auto": True,
                "use": [round(max(0.5, dur * 0.1), 1), round(min(dur - 0.2, dur * 0.9), 1)]
            }
            updated += 1
    return updated


def tag_all(src, media, roles=None, progress=print):
    """Automatically tag all clips into broll_tags.json and enrich media dict.
    broll_tags.json is also edited live by the panel (descriptions, segment pins/bans) → it is re-read right before
    writing and only the auto tags are merged in; an unreadable file is never overwritten."""
    W = os.path.join(src, "_nexys_work")
    os.makedirs(W, exist_ok=True)
    tp = os.path.join(W, "broll_tags.json")

    roles = roles or {}
    computed = {}
    for f, m in media.items():
        if m.get("status") != "ok":
            continue
        role = m.get("role") or roles.get(f, {}).get("role") or "b_roll"
        computed[f] = (role, extract_clip_tags(f, m, role), float(m.get("dur") or 0))

    cur_tags = jsonio.load_strict(tp)              # right before writing: the editor's latest state
    if cur_tags is None or not isinstance(cur_tags, dict):
        progress("tags: ⚠ broll_tags.json đang bị ghi dở / lỗi JSON → không ghi đè, giữ nguyên quyết định của editor")
        for f, (role, new_tags, dur) in computed.items():
            media[f]["tags"] = list(new_tags)
        return {}
    _merge_tags(cur_tags, computed)
    for f in computed:
        media[f]["tags"] = cur_tags[f].get("tags") or []
    jsonio.save_atomic(tp, cur_tags)
    progress(f"@tags {len(media)}/{len(media)} done")
    progress(f"tags: Đã gắn nhãn cho {len(media)} clip vào _nexys_work/broll_tags.json")
    return cur_tags


def classify_all(src, media, workers=4, min_speech_rms=-32.0, progress=print):
    """Classifies all clips in media dict and updates roles.
    Saves _nexys_work/roles.json and enriches media dict."""
    W = os.path.join(src, "_nexys_work")
    os.makedirs(W, exist_ok=True)
    rp = os.path.join(W, "roles.json")
    roles = jsonio.load(rp, {}) or {}

    vad = {}
    try:                                    # one cheap CPU pass (audio → numpy → Silero), cached per media key
        from nxcore import vad as _vad
        def vprog(msg):                     # keep the probe progress bar clean: one summary line every 50 clips
            if msg.startswith("{"): return
            if msg.startswith("@vad "):
                i, n = msg.split()[1].split("/")
                if i == n or int(i) % 50 == 0: progress(f"triage: quét giọng nói (VAD) {i}/{n}")
                return
            progress(msg)
        vad = _vad.scan(src, {f: m for f, m in media.items() if m.get("status") == "ok"}, workers=workers, progress=vprog)
    except Exception as e:
        progress(f"triage: VAD không chạy được ({str(e)[:120]}) → dùng cách đo âm lượng cũ")

    todo = []
    for f, m in media.items():
        if m.get("status") == "ok":
            k = m.get("key")
            if f in roles and roles[f].get("key") == k and "role" in roles[f] and roles[f].get("v") == ROLE_CACHE_V:
                m["role"] = roles[f]["role"]
                m["role_reason"] = roles[f].get("reason", "")
            else:
                todo.append((f, m))

    if todo:
        progress(json.dumps({"stage": "triage", "total": len(todo)}))
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            futs = {ex.submit(classify_file, f, m, os.path.join(src, f), min_speech_rms, vad.get(f)): (f, m) for f, m in todo}
            for fu in futs:
                f, m = futs[fu]
                try:
                    role, reason = fu.result()
                except Exception as e:
                    role, reason = "b_roll", f"Lỗi phân loại: {e}"
                m["role"] = role
                m["role_reason"] = reason
                roles[f] = {"role": role, "reason": reason, "key": m.get("key"), "v": ROLE_CACHE_V}

        jsonio.save_atomic(rp, roles)

    counts = {}
    for m in media.values():
        r = m.get("role", "b_roll")
        counts[r] = counts.get(r, 0) + 1

    summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
    progress(f"triage: Phân loại source xong ({summary})")
    return roles
