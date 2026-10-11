"""Vietnamese text normalisation for script <-> transcript alignment.

Whisper on Vietnamese footage mostly fails in three ways:
  1. wrong tone mark (hỏi/ngã, sắc/nặng)            -> compare without diacritics
  2. Southern-accent consonant merges (tr/ch, s/x,
     d/gi/r/v, final n/ng, t/c)                      -> compare a phonetic key
  3. English loanwords spelled phonetically          -> fuzzy ASCII ratio
Each level gets a lower similarity score so exact matches still win.
"""
import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


@lru_cache(maxsize=262144)
def strip_diacritics(s: str) -> str:
    s = s.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


_DIG = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]
_UNITS = {"km": "cây số", "kg": "ki lô gam", "cm": "xen ti mét", "m": "mét", "k": "nghìn", "tr": "triệu", "h": "giờ", "%": "phần trăm"}


def num_words(n: int) -> str:
    """Vietnamese reading of 0..999999 (Southern-neutral: 'mốt', 'lăm', 'linh')."""
    if n < 10:
        return _DIG[n]
    if n < 100:
        t, u = divmod(n, 10)
        head = "mười" if t == 1 else _DIG[t] + " mươi"
        if u == 0:
            return head
        tail = "mốt" if (u == 1 and t > 1) else ("lăm" if u == 5 else _DIG[u])
        return head + " " + tail
    if n < 1000:
        h, r = divmod(n, 100)
        s = _DIG[h] + " trăm"
        if r == 0:
            return s
        return s + (" linh " + _DIG[r] if r < 10 else " " + num_words(r))
    th, r = divmod(n, 1000)
    s = num_words(th) + " nghìn"
    if r == 0:
        return s
    if r < 100:
        return s + " không trăm " + (("linh " + _DIG[r]) if r < 10 else num_words(r))
    return s + " " + num_words(r)


_NUMTOK = re.compile(r"(\d+)([a-zđ%]*)")


_PHRASES = [("ki lô mét", "cây số"), ("ki-lô-mét", "cây số"), ("kilomet", "cây số"), ("make up", "makeup"), ("xì tai lít", "stylist"),
            # domain synonyms (Nexys casting / live): script wording <-> how people actually say it
            ("stylist", "trang phục"), ("phối đồ", "phối trang phục"), ("outfit", "trang phục"), ("test line", "test live")]


# Southern / spoken number forms → one canonical reading, applied to the script AND the transcript (both go through
# syllables(); align applies numeral_edits() again across Whisper word boundaries):
#   ngàn = nghìn (same word, regional) · "trăm lẻ năm" = "trăm linh năm" ('lẻ' only after trăm/nghìn: alone it is
#   "odd / retail" and would merge with the name Linh) · "hai mốt" = "hai mươi mốt": mốt / tư / lăm only ever follow a
#   tens digit, so "X mươi mốt|tư|lăm" is shortened to "X mốt|tư|lăm" (and "mươi bốn" read as "mươi tư" first).
#   "hai ba" is NOT expanded: it also means "two or three".
_NUM_ALIAS = {"ngàn": "nghìn"}
_TENS_DIG = {"hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"}
_UNIT_MARK = {"mốt", "tư", "lăm"}


def numeral_edits(syls):
    """[(index, new syllable | None = drop)] that bring spoken numbers to the canonical form (see above)."""
    out, s = [], list(syls)
    for i, x in enumerate(s):
        if x in _NUM_ALIAS:
            s[i] = _NUM_ALIAS[x]
            out.append((i, s[i]))
    for i, x in enumerate(s):
        if x == "lẻ" and i and s[i - 1] in ("trăm", "nghìn") and i + 1 < len(s) and s[i + 1] in _DIG:
            s[i] = "linh"
            out.append((i, "linh"))
        elif x == "bốn" and i and s[i - 1] == "mươi":
            s[i] = "tư"
            out.append((i, "tư"))
    for i in range(1, len(s) - 1):
        if s[i] == "mươi" and s[i - 1] in _TENS_DIG and s[i + 1] in _UNIT_MARK:
            out.append((i, None))
    return out


def apply_numeral_edits(syls):
    ed = dict(numeral_edits(syls))
    return [ed.get(i, x) for i, x in enumerate(syls) if not (i in ed and ed[i] is None)]


def syllables(text: str) -> list[str]:
    """Lower-cased NFC syllables, punctuation removed; numbers (and 20km-style units) read out in words."""
    t = nfc(text).lower().replace("%", " % ")
    for a, b in _PHRASES:
        t = t.replace(a, b)
    t = re.sub(r"(?<=\d)[\.,](?=\d{3}\b)", "", t)  # 1.000 -> 1000
    t = _PUNCT.sub(lambda m: m.group(0) if m.group(0) == "%" else " ", t)
    out = []
    for x in _WS.split(t):
        if not x:
            continue
        m = _NUMTOK.fullmatch(x)
        if m and int(m.group(1)) < 1_000_000:
            out += num_words(int(m.group(1))).split()
            unit = m.group(2)
            if unit:
                out += _UNITS.get(unit, unit).split()
        elif x == "%":
            out += ["phần", "trăm"]
        else:
            out.append(x)
    return apply_numeral_edits(out)


# --- phonetic key (Southern accent tolerant) ---------------------------------
_INITIALS = [("ngh", "ng"), ("gh", "g"), ("gi", "z"), ("tr", "ch"), ("kh", "k"), ("ph", "f"),
             ("th", "t"), ("qu", "w"), ("d", "z"), ("r", "z"), ("v", "z"), ("s", "x"), ("c", "k")]
_FINALS = [("nh", "n"), ("ng", "n"), ("ch", "t"), ("c", "t"), ("y", "i")]


# Southern vowel reductions, ONLY where they do not merge unrelated common words:
#   iêu → iu (nhiều/nhìu, hiểu/hỉu, yêu/iu), ươu → ưu (rượu/rựu)  — the reduced forms are (near) non-words.
# Deliberately NOT applied: general iê → i (tiền/tin/tiếng/tính), iêm → im (tiệm/tìm, chiếm/chim),
# ươi/uôi → ưi/ui (tuổi/tui, mười/mùi, muối/mùi), uôn → un (muốn/mụn, buồn/bùn): those collapse everyday words.
_VOWEL_RED = (("ieu", "iu"), ("yeu", "iu"), ("uou", "uu"))


@lru_cache(maxsize=262144)
def phon_key(syl: str) -> str:
    a = strip_diacritics(syl.lower())
    for src, dst in _VOWEL_RED:
        if src in a:
            a = a.replace(src, dst)
            break
    for src, dst in _INITIALS:
        if a.startswith(src):
            a = dst + a[len(src):]
            break
    for src, dst in _FINALS:
        if a.endswith(src) and len(a) > len(src):
            a = a[: -len(src)] + dst
            break
    return a


FILLERS = {"ờ", "ừ", "ừm", "ờm", "ơ", "ư", "hmm", "hm", "uhm", "um", "ừa", "ờ ờ", "ê"}


def is_filler(syl: str) -> bool:
    return syl in FILLERS


def syl_sim(a: str, b: str) -> float:
    """Similarity of two syllables in [0,1]."""
    if a == b:
        return 1.0
    aa, bb = strip_diacritics(a), strip_diacritics(b)
    if aa == bb:
        return 0.8
    if phon_key(a) == phon_key(b):
        return 0.65
    la, lb = len(aa), len(bb)
    # fuzzy only for loanwords / typos with the same first letter ('phôn'~'phone'); on short syllables with different
    # initials ('thôi'~'hỏi', 'xong'~'uống') the ratio was pure noise and created false take matches
    if la >= 3 and lb >= 3 and aa[0] == bb[0] and 2 * min(la, lb) >= 0.75 * (la + lb):
        sm = SequenceMatcher(None, aa, bb)
        if sm.quick_ratio() >= 0.75 and sm.ratio() >= 0.75:
            return 0.45
    return 0.0


STOPWORDS = set("""em chị anh là có của và thì mà nha nhé nhá ạ dạ gì không này đó cho với được đã sẽ đang rồi
cái các những một hai ba bạn mình tụi bên á à ơi vậy thế nào ra vào lại khi để như nên cũng rất""".split())


def content(syls):
    return [s for s in syls if s not in STOPWORDS and not is_filler(s)]
