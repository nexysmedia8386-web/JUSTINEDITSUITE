"""Parse a shooting script (.txt / .md / .docx) into alignable lines.

Two styles are understood, and can be mixed:

A. Screenplay style
  # CẢNH 1 – Phỏng vấn | PHÂN CẢNH 2 — ... | SCENE 3 | [C4]   -> scene header
  HR: Chị thấy em hợp đó.                                     -> speaker + dialogue
  [B-ROLL] makeup cận  |  [VO] Nexys tuyển idol...             -> b-roll / voice-over
  (cười)                                                      -> stage direction, dropped
  **tiềm năng** / bold run in Word                             -> highlight keyword
  Word table with 2 columns (Hình | Lời)                       -> b-roll + dialogue

B. Outline / brief style (typical Nexys brief)
  HR:                      <- speaker alone on a line ...
  “Chị nghĩ em có thể về được rồi...”   <- ... then the quoted line = dialogue
  * Em đã có kinh nghiệm ... chưa?      -> interview question (soft match + capture the answer)
  * Quay quá trình Idol được makeup...  -> action / coverage note (soft match on speech, else b-roll)
  Bối cảnh: Phòng phỏng vấn.            -> note
  Lưu ý edit: → Đưa đoạn diễn xuất này lên đầu video làm opening hook   -> previous dialogue block = HOOK
  → Khi edit có thể chuyển đổi giữa hai source ...                       -> scene is multicam
  Kết video: Có thể chốt bằng cảnh Idol rời khỏi công ty + logo/CTA      -> end b-roll + end slate
"""
import os
import re
from dataclasses import dataclass, field, asdict

from .textnorm import nfc, syllables

SCENE_RE = re.compile(r"^\s*(?:#+\s*|\[)?\s*((?:phân\s+)?cảnh|scene|c)\s*(\d+[\w\.]*)\s*\]?\s*[:\-–—]?\s*(.*)$", re.I)
SPEAKER_RE = re.compile(r"^\s*([^\W\d_][\w ]{0,20}?)\s*[:：]\s*(.+)$", re.U)
BROLL_RE = re.compile(r"^\s*[\[\(]\s*b[\s\-]?roll\s*[:\]\)]?\s*(.*?)\]?\s*$", re.I)
VO_RE = re.compile(r"^\s*[\[\(]\s*vo\s*[:\]\)]\s*(.*)$", re.I)
# VO_TEXT_PATCH: on-screen text. [TEXT] … / [TITLE] … / [CHỮ] … / Text: … / Title: …
TEXT_RE = re.compile(r"^\s*(?:[\[\(]\s*(text|title|chữ|tiêu đề)\s*[\]\)]\s*[:：]?|(text|title)\s*[:：])\s*(.+)$", re.I)
DIRECTION_RE = re.compile(r"\([^)]*\)")
BOLD_MD_RE = re.compile(r"\*\*(.+?)\*\*")
SENT_RE = re.compile(r"(?<=[\.\?\!…])\s+")
QUOTE_RE = re.compile(r"[“\"«](.+?)[”\"»]")
SOURCE_RE = re.compile(r"^\s*(?:[>↳└\-\*•]\s*)?(?:nguồn|nguon|source|src)\s*[:：]\s*(.+)$", re.I)
SRC_FILE_RE = re.compile(r"([\w\-()./\\ ]*?[\w\-()]+\.(?:mov|mp4|mxf|mts|m2ts|m4v|avi|mkv|insv))(?:\s*[@\[(]\s*(\d+(?:[.,]\d+)?)\s*(?:s)?\s*[-–—~→]\s*(\d+(?:[.,]\d+)?)\s*s?\s*[\])]?)?", re.I)
MISSING_WORDS = ("thiếu", "không có source", "không có clip", "chưa có source", "không đạt", "không đủ", "cần quay", "quay bổ sung", "missing", "⚠")
HR_RE = re.compile(r"^\s*([-*_—–=])(?:\s*\1){2,}\s*$")          # markdown rule "---" / "***" / "___"
# a non-scene heading whose content is editor notes, not shots ("### GHI CHÚ BIÊN TẬP (EDIT NOTES)")
NOTE_HEAD_WORDS = ("ghi chú", "ghi chu", "note", "lưu ý", "luu y", "hướng dẫn", "huong dan", "brief", "thông tin",
                   "thong tin", "tổng quan", "tong quan", "mục tiêu", "muc tieu", "ý tưởng", "y tuong", "định hướng",
                   "dinh huong", "yêu cầu", "yeu cau")
BULLET_RE = re.compile(r"^\s*(?:[\*\-•·▪◦]|\d+[\.\)])\s*(.*)$")

KNOWN_SPEAKERS = {"hr", "idol", "mc", "host", "ứng viên", "candidate", "vo", "đạo diễn", "khách", "nam", "nữ",
                  "mẹ", "bố", "ba", "má", "bé", "ông", "bà", "cô", "chú", "dì", "con", "chồng", "vợ", "bạn", "người dẫn",
                  "hướng dẫn viên", "nhân viên", "chủ quán", "barista", "khách hàng", "giám khảo", "phóng viên", "nhân vật"}
NOTE_LABELS = ("bối cảnh", "lưu ý", "ghi chú", "note", "kết video", "tạo điểm nhấn", "mục tiêu", "địa điểm", "thời lượng", "đạo cụ")
HOOK_WORDS = ("hook", "lên đầu video", "đầu video", "mở đầu video", "opening")
MULTICAM_WORDS = ("hai source", "2 source", "chuyển đổi giữa", "multicam", "hai góc", "2 góc")
END_WORDS = ("kết video",)


@dataclass
class ScriptLine:
    id: str
    scene: str
    type: str  # dialogue | question | action | broll | vo
    text: str
    speaker: str = ""
    scene_title: str = ""
    para: int = 0
    block: int = 0            # consecutive dialogue lines share a block (continuity)
    hook: bool = False        # move to the opening of the video
    capture: bool = False     # also keep the (unscripted) reply that follows
    keywords: list = field(default_factory=list)
    syl: list = field(default_factory=list)
    src: list = field(default_factory=list)   # [{"file": "DSC_1.MOV", "t": [a, b] | None}] from "Nguồn:" notes
    src_note: str = ""        # "Nguồn: ⚠ THIẾU …" → no source good enough (reason)

    def to_dict(self):
        return asdict(self)


@dataclass
class ScriptMeta:
    title: str = ""
    scene_titles: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)      # (scene, text)
    multicam_scenes: list = field(default_factory=list)
    hook_files: list = field(default_factory=list)  # clip names the script asks to open with
    end_note: str = ""
    sources: list = field(default_factory=list)     # (line ids, files, missing reason) per "Nguồn:" note


def _read_paragraphs(path):
    """Yield (text, bold_runs, table_cells_or_None)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        import docx  # python-docx
        d = docx.Document(path)
        for child in d.element.body.iterchildren():
            tag = child.tag.split("}")[-1]
            if tag == "p":
                p = docx.text.paragraph.Paragraph(child, d)
                bold = [r.text for r in p.runs if r.bold and r.text.strip()]
                yield p.text, bold, None
            elif tag == "tbl":
                t = docx.table.Table(child, d)
                for ri, row in enumerate(t.rows):
                    cells = [c.text.strip() for c in row.cells]
                    if ri == 0 and any(h.lower() in ("hình", "hình ảnh", "visual", "lời", "thoại", "audio") for h in cells):
                        continue
                    bold = [r.text for c in row.cells for p in c.paragraphs for r in p.runs if r.bold and r.text.strip()]
                    yield "", bold, cells
    else:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.rstrip("\n")
                yield BOLD_MD_RE.sub(r"\1", raw), BOLD_MD_RE.findall(raw), None


def _split_sentences(text):
    parts = [p.strip() for p in SENT_RE.split(text) if p.strip()]
    out = []
    for p in parts:
        if out and len(syllables(p)) < 3:
            out[-1] += " " + p
        else:
            out.append(p)
    return out


def _is_speaker(name, known, quoted=False):
    """"HR", "MC", "BÉ ĐẬU", and named characters built on a role / kinship word ("Bé Đậu", "Mẹ Lan", "Ông ngoại");
    with a quote after the colon also a short capitalised name ("Minh Anh: “…”")."""
    n = name.strip().lower()
    if n in known:
        return True
    w = name.strip().split()
    if not w or any(n.startswith(l) for l in NOTE_LABELS):
        return False
    if len(w) <= 2 and name.strip().isupper():
        return True
    if len(w) <= 3 and w[0].lower() in known:
        return True
    return quoted and len(w) <= 3 and all(x[:1].isupper() for x in w)


def parse_script_meta(path, min_syl=2):
    lines, meta = [], ScriptMeta()
    scene, para, n, block = "S1", 0, 0, 0
    known = set(KNOWN_SPEAKERS)
    pending_speaker = ""      # "HR:" alone on a line -> next quote belongs to HR
    context_speaker = ""      # "HR tiến hành phỏng vấn..." -> bullets with '?' are HR questions
    last_kind = ""            # to decide when a dialogue block ends
    note_mode = ""            # inside "Lưu ý edit:" / "Kết video:" paragraphs
    note_section = False      # under a "### GHI CHÚ …" heading: every line is a note until the next scene

    def add(kind, text, speaker="", bold=(), capture=False):
        nonlocal n
        text = nfc(DIRECTION_RE.sub(" ", text)).strip().strip("“”\"").strip()
        if not text:
            return
        if lines and lines[-1].type != kind and {kind, lines[-1].type} <= {"dialogue", "question"}:
            new_block()
        chunks = _split_sentences(text) if kind == "dialogue" else [text]
        for ch in chunks:
            syl = syllables(ch)
            if kind in ("dialogue", "question") and len(syl) < min_syl:
                continue
            kws = [b.strip() for b in bold if b.strip() and b.strip().lower() in ch.lower()]
            n += 1
            lines.append(ScriptLine(f"L{n:03d}", scene, kind, ch, speaker, meta.scene_titles.get(scene, scene),
                                    para, block, False, capture, kws, syl))

    def new_block():
        nonlocal block
        block += 1

    def handle_note(text):
        low = text.lower()
        meta.notes.append((scene, text))
        if low.startswith(("ghi chú nguồn", "ghi chu nguon")):
            return                          # source summary written by the panel — informational only
        if any(w in low for w in HOOK_WORDS):
            for fn in re.findall(r"[\w\-()]+\.(?:mov|mp4|mxf|avi|mkv|m4v)", text, flags=re.I):
                if fn not in meta.hook_files:
                    meta.hook_files.append(fn)
            # the most recent dialogue block becomes the hook
            blk = next((l.block for l in reversed(lines) if l.type == "dialogue"), None)
            for l in lines:
                if l.block == blk and l.type == "dialogue":
                    l.hook = True
        if any(w in low for w in MULTICAM_WORDS) and scene not in meta.multicam_scenes:
            meta.multicam_scenes.append(scene)

    def handle_source(body):
        """'Nguồn: DSC_9041.MOV @ 12.5–16 · ổn định 82' → pin to the previous item (whole dialogue block)."""
        files = []
        for m in SRC_FILE_RE.finditer(body):
            fn = m.group(1).strip().strip("`*\"' ").replace("\\", "/")
            t = [float(m.group(2).replace(",", ".")), float(m.group(3).replace(",", "."))] if m.group(2) else None
            files.append({"file": fn, "t": t})
        low = body.lower()
        missing = ""
        if any(w in low for w in MISSING_WORDS):
            missing = body.strip()
            files = []          # clips named in a "thiếu" note are the rejected ones, not pins
        if not lines:
            meta.sources.append(([], files, missing))
            return
        last = lines[-1]
        # a dialogue block gets the note on every line since the previous "Nguồn:" (one note per quote also works)
        tgt = [l for l in lines if l.block == last.block and l.type == last.type == "dialogue" and not l.src and not l.src_note] or [last]
        for l in tgt:
            if files:
                l.src = files
            if missing:
                l.src_note = missing
        meta.sources.append(([l.id for l in tgt], files, missing))

    first = True
    for text, bold, cells in _read_paragraphs(path):
        if cells is not None:
            para += 1
            new_block()
            vis, aud = (cells + ["", ""])[:2] if len(cells) >= 2 else ("", cells[0])
            if vis:
                add("broll", vis)
            if aud:
                m = SPEAKER_RE.match(aud)
                sp = m.group(1).strip() if m and _is_speaker(m.group(1), known) else ""
                add("dialogue", m.group(2) if sp else aud, sp, bold)
            continue
        t = text.strip()
        if not t:
            note_mode = ""
            continue
        para += 1
        low = t.lower()

        # title (first line, all caps, before any scene)
        if first and scene == "S1" and t.isupper() and not SCENE_RE.match(t):
            meta.title = t
            first = False
            continue
        first = False

        if HR_RE.match(t):                  # "---" separators are layout, never a shot ("--" became a B-roll line)
            continue
        m = SCENE_RE.match(t)
        if m and (t.startswith("#") or t.startswith("[") or m.group(1).lower() != "c"):
            scene = f"C{m.group(2)}"
            meta.scene_titles[scene] = t.lstrip("#[ ").rstrip("] ")
            pending_speaker = context_speaker = note_mode = ""
            note_section = False
            new_block()
            last_kind = "scene"
            continue
        if t.startswith("#"):              # other headings are structure; a notes heading opens a notes section
            note_section = any(w in t.lstrip("# ").lower() for w in NOTE_HEAD_WORDS)
            continue
        if note_section:
            b_ = BULLET_RE.match(t)
            handle_note(BOLD_MD_RE.sub(r"\1", b_.group(1) if b_ else t).strip())
            continue

        m = SOURCE_RE.match(t)
        if m:
            handle_source(m.group(1))
            continue
        m = BROLL_RE.match(t)
        if m:
            add("broll", m.group(1))
            continue
        m = VO_RE.match(t)
        if m:
            add("vo", m.group(1), "VO", bold)
            continue
        m = TEXT_RE.match(t)
        if m:
            tag = (m.group(1) or m.group(2) or "").lower()
            add("text", m.group(3), "TITLE" if tag in ("title", "tiêu đề") else "TEXT")
            continue

        # "Label:" alone
        if t.endswith((":", "：")) and len(t) < 60:
            label = t.rstrip(":：").strip()
            first_word = label.split()[0] if label.split() else ""
            if _is_speaker(label, known):
                known.add(label.lower())
                pending_speaker = label
                note_mode = ""
                continue
            if any(label.lower().startswith(l) for l in NOTE_LABELS):
                note_mode = label.lower()
                if any(label.lower().startswith(e) for e in END_WORDS):
                    note_mode = "end"
                continue
            # narrative like "HR giới thiệu:" / "Sau khi kết thúc phân cảnh:"
            if first_word.lower() in known:
                pending_speaker = first_word
                context_speaker = first_word
            else:
                pending_speaker = ""
            if last_kind == "dialogue":
                new_block()
            last_kind = "narrative"
            note_mode = ""
            continue

        # arrows / note paragraphs
        if t.startswith(("→", "->", "=>")) or (note_mode and not BULLET_RE.match(t)):
            body = t.lstrip("→->= ").strip()
            if note_mode == "end":
                meta.end_note = body
                add("broll", body)
            else:
                handle_note(body)
            note_mode = ""
            continue

        # quoted dialogue
        q = QUOTE_RE.findall(t)
        m = SPEAKER_RE.match(t)
        if q and (pending_speaker or not m):
            add("dialogue", " ".join(q), pending_speaker, bold)
            last_kind = "dialogue"
            continue

        # bullets
        b = BULLET_RE.match(t)
        if b and not t.startswith("**"):
            body = b.group(1).strip()
            if not body:
                continue
            if body.rstrip().endswith("?"):
                add("question", body, context_speaker or pending_speaker, bold, capture=True)
            else:
                add("action", body, context_speaker)   # speaker set => spoken action (e.g. "HR trao đổi với ứng viên")
            last_kind = "bullet"
            continue

        # "Name: text" on one line
        if m:
            name, rest = m.group(1).strip(), m.group(2).strip()
            if any(name.lower().startswith(l) for l in NOTE_LABELS):
                if name.lower().startswith(END_WORDS):
                    meta.end_note = rest
                    add("broll", rest)
                else:
                    handle_note(f"{name}: {rest}")
                continue
            if _is_speaker(name, known, quoted=bool(QUOTE_RE.search(rest))):
                known.add(name.lower())
                if last_kind not in ("dialogue", "speaker"):
                    new_block()
                add("dialogue", QUOTE_RE.sub(r"\1", rest), name, bold)
                last_kind = "dialogue"
                continue

        # plain narrative line
        fw = low.split()[0] if low.split() else ""
        if fw in known:
            context_speaker = t.split()[0]
        if last_kind == "dialogue":
            new_block()
        last_kind = "narrative"
        # a narrative line that is actually dialogue in screenplay style (no speaker)
        if not any(low.startswith(l) for l in NOTE_LABELS) and t.endswith((".", "!", "?", "…")) and fw not in known \
                and not q and len(syllables(t)) >= 4 and _looks_spoken(t):
            add("dialogue", t, "", bold)
            last_kind = "dialogue"
    # a dialogue question that closes its block is answered off-script -> keep the reply
    for k, l in enumerate(lines):
        nxt = lines[k + 1] if k + 1 < len(lines) else None
        if l.type == "dialogue" and l.text.rstrip().endswith("?") and (nxt is None or nxt.block != l.block):
            l.capture = True
    return lines, meta


def _looks_spoken(t):
    """Heuristic: first/second person pronouns => spoken line, otherwise stage description."""
    w = set(syllables(t))
    return bool(w & {"em", "chị", "anh", "mình", "tôi", "bạn", "tụi", "chúng"})


def parse_script(path, min_syl=2):
    return parse_script_meta(path, min_syl)[0]


def to_text(path):
    """Any script file → editable markdown text (the panel's manual-script box): Word bold runs → **…**,
    2-column Word tables (Hình | Lời) → "[B-ROLL] hình" + the spoken line."""
    out = []
    for text, bold, cells in _read_paragraphs(path):
        if cells is not None:
            if len(cells) >= 2:
                if cells[0]:
                    out.append(f"[B-ROLL] {cells[0]}")
                if cells[1]:
                    out.append(cells[1])
            else:
                out.extend(c for c in cells if c)
            continue
        t = text
        for b in sorted(set(bold), key=len, reverse=True):
            if b.strip() and f"**{b}**" not in t:
                t = t.replace(b, f"**{b}**", 1)
        out.append(t)
    return "\n".join(out).strip() + "\n"


def check(path):
    """How Align will read the script — counts per kind + what usually goes wrong, for the panel's "Kiểm tra"."""
    lines, meta = parse_script_meta(path)
    kinds = {}
    for l in lines:
        kinds[l.type] = kinds.get(l.type, 0) + 1
    scenes = sorted({l.scene for l in lines if l.scene})
    warn = []
    spoken = kinds.get("dialogue", 0) + kinds.get("question", 0)
    if not lines:
        warn.append("không đọc được dòng nào — xem lại cú pháp (ví dụ ở ô hướng dẫn)")
    elif not spoken:
        warn.append("không có câu thoại nào — Align ghép lời nói trong source theo câu thoại; kịch bản chỉ B-roll / VO "
                    "nên dùng preset montage")
    if lines and len(scenes) <= 1 and len(lines) > 25:
        warn.append("chỉ có 1 cảnh — chia cảnh bằng dòng '# CẢNH 2 – tên cảnh' giúp chuyển cảnh và chương đúng chỗ")
    return {"lines": len(lines), "scenes": len(scenes), "dialogue": kinds.get("dialogue", 0), "question": kinds.get("question", 0),
            "broll": kinds.get("broll", 0), "vo": kinds.get("vo", 0), "action": kinds.get("action", 0),
            "hooks": sum(1 for l in lines if l.hook), "keywords": sorted({k for l in lines for k in l.keywords})[:20],
            "title": meta.title, "warn": warn,
            "sample": [{"type": l.type, "speaker": l.speaker, "text": l.text[:80]} for l in lines[:6]]}


if __name__ == "__main__":
    import json
    import sys
    for s_ in (sys.stdout, sys.stderr):
        try:
            s_.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    cmd, p = (sys.argv[1:3] + ["", ""])[:2]
    try:
        res = {"text": to_text(p)} if cmd == "totext" else check(p) if cmd == "check" else {"error": "lệnh: totext | check FILE"}
    except Exception as e:                                 # noqa: BLE001
        res = {"error": str(e)[:300]}
    print("SCRIPT " + json.dumps(res, ensure_ascii=False))
