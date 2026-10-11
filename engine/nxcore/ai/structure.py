"""Functional music structure with allin1 (mir-aidj "All-In-One Metrical and Functional Structure Analysis",
WASPAA 2023, MIT): section boundaries + labels intro / verse / chorus / bridge / inst / solo / break / outro.

nxcore.music keeps its own energy labels (drop / build / main / break …, used for pacing) and takes the section
BOUNDARIES from allin1 when it is installed, adding its label as "func" on each section.

    segs = sections(path)    # [{"start", "end", "func"}] or None (allin1 missing / failed)

allin1 needs madmom (git) + NATTEN: on Windows NATTEN has no wheels, so this is mostly a Linux/WSL/Mac feature.
"""
from . import enabled, has, torch_ok

ENGINE = "allin1"


_IMPORT_OK = None


def available():
    """A real import, once: allin1 installs fine on Windows but its madmom dependency needs MSVC build tools."""
    global _IMPORT_OK
    if not (enabled("structure") and torch_ok() and has("allin1")):
        return False
    if _IMPORT_OK is None:
        try:
            import allin1  # noqa: F401
            _IMPORT_OK = True
        except Exception:
            _IMPORT_OK = False
    return _IMPORT_OK


def sections(path):
    if not available():
        return None
    try:
        import allin1
        from . import device
        r = allin1.analyze(path, device=device(), keep_byproducts=False)
    except Exception:
        return None
    out = []
    for s in getattr(r, "segments", None) or []:
        a, b = float(s.start), float(s.end)
        if b - a > 0.5:
            out.append({"start": round(a, 3), "end": round(b, 3), "func": str(s.label)})
    return out or None
