"""Sổ luật phong cách — the editor's explicit house rules (Justin EDIT Suite).

Rules are born from timestamped notes on a built sequence (panel: AI → 📍 Ghi chú, the AI proposes, the editor
approves) or typed by hand. They live in %APPDATA%/JustinEditSuite/stylebook.json: the panel writes it, the engine only
reads. Two kinds:
  · text rules  — instructions every AI step reads (script, AI edit, AI Assist, text style)
  · param rules — one whitelisted engine setting with a value (stylebook_params.json: punch-in scale, B-roll length,
                  montage pace, SFX level…). The engine applies them on every build, so unlike a sentence in a prompt
                  they cannot be "forgotten".
Strength on a build: house defaults < learned habits (nxcore.prefs) < genre preset < style rules < this run's settings.
A rule can be limited to one preset ("preset": "vlog_travel"); a preset-specific rule beats a general one, and among
equals the newer rule wins.

  python -m nxcore.stylebook show [--preset ID]     → STYLE {"assemble": {...}, "montage": {...}, ...}
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_SPEC = None


def spec():
    """{key: {"mod", "type": "num"|"bool"|"enum", "lo", "hi", "unit", "values", "label"}} — shared with the panel."""
    global _SPEC
    if _SPEC is None:
        try:
            with open(os.path.join(_HERE, "stylebook_params.json"), encoding="utf-8") as f:
                _SPEC = json.load(f)
        except Exception:
            _SPEC = {}
    return _SPEC


def path():
    p = os.environ.get("JES_STYLEBOOK")
    if p:
        return p
    base = os.environ.get("APPDATA") or os.environ.get("HOME") or os.path.expanduser("~")
    return os.path.join(base, "JustinEditSuite", "stylebook.json")


def load():
    try:
        with open(path(), encoding="utf-8") as f:
            j = json.load(f)
        if isinstance(j, dict) and isinstance(j.get("rules"), list):
            return j
    except Exception:
        pass
    return {"version": 1, "rules": []}


def coerce(key, value):
    """A rule's value → the typed, clamped engine value, or None when it does not fit the parameter."""
    sp = spec().get(key)
    if not sp:
        return None
    t = sp.get("type")
    if t == "bool":
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        return True if s in ("1", "true", "on", "yes", "có", "bật") else False if s in ("0", "false", "off", "no", "không", "tắt") else None
    if t == "enum":
        v = str(value).strip().lower()
        return v if v in sp.get("values", []) else None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v:                                            # NaN
        return None
    v = min(float(sp.get("hi", v)), max(float(sp.get("lo", v)), v))
    return int(round(v)) if float(v).is_integer() and abs(v) >= 10 else round(v, 3)


def params(mod, preset=None, book=None):
    """Enabled param rules of one engine module ("assemble" | "transition" | "montage" | "sound" | "motion") for the
    preset in use → {key: value}. Empty when learning is switched off (JES_NO_LEARN / JES_NO_STYLE: tests, A/B runs)."""
    if os.environ.get("JES_NO_LEARN") or os.environ.get("JES_NO_STYLE"):
        return {}
    out, rank = {}, {}
    for r in (book or load()).get("rules", []):
        p = r.get("param") if isinstance(r, dict) and r.get("on", True) else None
        if not isinstance(p, dict):
            continue
        key = p.get("key")
        sp = spec().get(key)
        if not sp or sp.get("mod") != mod:
            continue
        rp = r.get("preset") or ""
        if rp and rp != (preset or ""):
            continue
        v = coerce(key, p.get("value"))
        if v is None:
            continue
        k = 1 if rp else 0                                  # later rules come later in the list → ">=" lets them win
        if key not in out or k >= rank[key]:
            out[key], rank[key] = v, k
    return out


def describe(d):
    """{key: value} → "Mức zoom punch-in 108%, Độ dài B-roll thường 3s" for report notes."""
    sp = spec()
    parts = []
    for k, v in d.items():
        s = sp.get(k, {})
        val = ("bật" if v else "tắt") if s.get("type") == "bool" else \
            (s.get("names") or {}).get(v, v) if s.get("type") == "enum" else f"{v}{s.get('unit', '')}"
        parts.append(f"{s.get('label', k)} {val}")
    return ", ".join(parts)


def main(argv=None):
    import argparse
    import sys
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="nxcore.stylebook")
    ap.add_argument("cmd", choices=("show",))
    ap.add_argument("--preset", default="")
    a = ap.parse_args(argv)
    mods = sorted({s.get("mod") for s in spec().values()})
    book = load()
    print("STYLE " + json.dumps({"path": path(), "rules": len(book.get("rules", [])),
                                  **{m: params(m, a.preset, book) for m in mods}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
