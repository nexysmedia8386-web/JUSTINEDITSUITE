"""Atomic JSON files for _nexys_work — Justin EDIT Suite.

save_atomic(path, obj): write to a unique temp file next to the target, then os.replace() it in one step, so a reader
(the panel, another engine process) never sees half a file and a killed process never leaves a truncated one.
On Windows os.replace() fails with PermissionError while another process has the target open (panel reading it,
antivirus / Dropbox scanning it) → retried with a short back-off; as a last resort the file is written in place
(never silently dropped).

load(path, default, retries=0): parsed JSON or `default` (missing / empty / invalid file). retries > 0 re-reads a file
that fails to parse — the panel writes some files with a plain (non-atomic) writeFileSync, so a read can catch it
half-written.
"""
import json
import os
import threading
import time

REPLACE_TRIES = 12           # ~3 s total back-off
_MISSING = object()


def _tmp_name(path):
    return f"{path}.{os.getpid()}.{threading.get_ident() % 100000}.tmp"


def save_atomic(path, obj, indent=1, ensure_ascii=False, **kw):
    """Write obj as JSON to path atomically. Returns path. Raises only when even the in-place fallback fails."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = _tmp_name(path)
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=ensure_ascii, indent=indent, **kw)
            fh.flush()
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    err = None
    for i in range(REPLACE_TRIES):
        try:
            os.replace(tmp, path)
            return path
        except PermissionError as e:           # Windows: target (or tmp) briefly locked by a reader / scanner
            err = e
            time.sleep(min(0.5, 0.02 * (2 ** i)))
        except OSError as e:
            err = e
            break
    try:                                        # last resort: in place (non-atomic) beats losing the data
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=ensure_ascii, indent=indent, **kw)
        return path
    except OSError:
        raise err
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def load(path, default=None, retries=0, delay=0.15):
    """Parsed JSON from path, or `default` when the file is missing, empty or not valid JSON."""
    for i in range(max(0, retries) + 1):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return default
        except (ValueError, OSError):
            if i < retries:
                time.sleep(delay)
    return default


def load_strict(path, retries=3, delay=0.15):
    """Like load(), but tells apart 'missing' (→ {}) from 'exists but unreadable' (→ None) so a caller about to
    rewrite the file can refuse instead of replacing an editor's data with an empty dict."""
    if not os.path.exists(path):
        return {}
    v = load(path, _MISSING, retries=retries, delay=delay)
    if v is _MISSING:
        try:
            if os.path.getsize(path) == 0:
                return {}
        except OSError:
            pass
        return None
    return v
