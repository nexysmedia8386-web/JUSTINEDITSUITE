"""Shooting-time order for VLOG / event recap — Justin EDIT Suite.

A vlog's story usually follows the order things were filmed. Given the script lines in order, this module
estimates WHEN in the shoot each visual line should come from, so B-roll picks follow the day instead of jumping
back and forth between mornings and evenings that merely look alike.

1. capture_ts(file, media)  absolute start time of a clip. DJI file names carry the true start (DJI_YYYYMMDD_HHMMSS):
                            when the metadata disagrees by > 2 min the name wins (re-written files keep a later date).
2. expected_times(...)      monotonic alignment (dynamic programming) of the script's lines onto the shoot timeline:
                            dialogue takes already chosen are fixed anchors; every visual line may take one of its
                            top-K content matches or "no opinion"; going BACK in time costs a penalty that grows with
                            how far back (a few minutes of shuffle is free). Hook / end-slate lines are left out —
                            a vlog hook is deliberately taken from later in the day.
3. fit(t, line)             −1 … +1 agreement of a candidate's time with its line's expected time (used as a score term).
"""
import math
import re
from datetime import datetime

DJI_NAME = re.compile(r"DJI_(\d{8})_(\d{6})")
CHRONO_PRESETS = ("vlog_travel", "event_recap", "wedding_highlight", "wedding_full")
FREE_BACK_S = 300.0        # going back less than this (same place, same hour) is free
FULL_BACK_S = 3 * 3600.0   # going back this far costs the whole penalty
MIN_SIGMA_S = 600.0


def capture_ts(f, m):
    """Seconds since epoch of the clip START, or None."""
    ts = float((m or {}).get("created_ts") or 0) or None
    mm = DJI_NAME.search(str(f).replace("\\", "/").rsplit("/", 1)[-1])
    if mm:
        try:
            loc = datetime.strptime(mm.group(1) + mm.group(2), "%Y%m%d%H%M%S")
            lm = (m or {}).get("created_local")
            if lm and ts:
                meta_loc = datetime.strptime(lm[:19], "%Y-%m-%d %H:%M:%S")
                drift = (meta_loc - loc).total_seconds()
                if abs(drift) > 120:
                    ts -= drift                      # trust the name (true start), keep the metadata's timezone
            elif not ts:
                ts = loc.timestamp()
        except Exception:
            pass
    return ts


def folder_choice(src):
    """The editor's answer stored by the panel (asked before scanning for non-vlog presets): {"on", "preset"} or {}."""
    if not src:
        return {}
    try:
        import json
        import os
        with open(os.path.join(src, "_nexys_work", "chrono.json"), encoding="utf-8") as fh:
            c = json.load(fh) or {}
        return c if isinstance(c.get("on"), bool) else {}
    except Exception:
        return {}


def enabled(cfg, src=None):
    """cfg chrono True/False forces; "auto" → the folder's own choice when made with the SAME preset, else the preset
    default (on for vlog_travel / event_recap)."""
    c = (cfg or {}).get("chrono", "auto")
    if c in (True, "on", 1):
        return True
    if c in (False, "off", 0, None):
        return False
    preset = str((cfg or {}).get("preset") or (cfg or {}).get("id") or "")
    fc = folder_choice(src)
    if fc and str(fc.get("preset") or "") == preset:
        return bool(fc["on"])
    return preset in CHRONO_PRESETS


def back_penalty(t_prev, t, lam):
    if t_prev is None or t is None or t >= t_prev - FREE_BACK_S:
        return 0.0
    return lam * min(1.0, (t_prev - FREE_BACK_S - t) / (FULL_BACK_S - FREE_BACK_S) + 0.25)


def expected_times(order, anchors, options, lam=0.5, free_score=0.0):
    """order   : [line_id] in script order (hook / slate lines already removed)
    anchors : {line_id: t}            fixed times (chosen dialogue takes)
    options : {line_id: [(score, t)]} content candidates of visual lines (top-K)
    → {line_id: (t_expected, sigma)} for the visual lines (None where nothing can be said)."""
    states = []                              # per line: [(gain, t)] ; t None = "no opinion"
    for lid in order:
        if lid in anchors:
            states.append([(0.0, anchors[lid])])
        else:
            st = [(float(s), float(t)) for s, t in options.get(lid, []) if t is not None]
            st.append((free_score, None))
            states.append(st)
    if not states:
        return {}
    # best[i][k] = (total, back pointer, last known time)
    best = [[(g, -1, t) for g, t in states[0]]]
    for i in range(1, len(states)):
        row = []
        for g, t in states[i]:
            cand = None
            for k, (tot, _, tl) in enumerate(best[i - 1]):
                v = tot + g - back_penalty(tl, t, lam)
                if cand is None or v > cand[0]:
                    cand = (v, k, t if t is not None else tl)
            row.append(cand)
        best.append(row)
    k = max(range(len(best[-1])), key=lambda j: best[-1][j][0])
    pick = [None] * len(states)
    for i in range(len(states) - 1, -1, -1):
        pick[i] = states[i][k][1]
        k = best[i][k][1]
    # lines with "no opinion" → interpolate between their known neighbours
    known = [(i, t) for i, t in enumerate(pick) if t is not None]
    out = {}
    for i, lid in enumerate(order):
        if lid in anchors:
            continue
        t = pick[i]
        prv = next((kt for kt in reversed(known) if kt[0] < i), None)
        nxt = next((kt for kt in known if kt[0] > i), None)
        if t is None:
            if prv and nxt:
                t = prv[1] + (nxt[1] - prv[1]) * (i - prv[0]) / (nxt[0] - prv[0])
            elif prv or nxt:
                t = (prv or nxt)[1]
            else:
                out[lid] = None
                continue
        span = abs((nxt[1] if nxt else t) - (prv[1] if prv else t))
        out[lid] = (t, max(MIN_SIGMA_S, span / 2.0))
    return out


def fit(t, exp):
    """+1 when t sits at the expected time, 0 at ~1.2 σ, → −1 far away (other part of the day / another day)."""
    if t is None or not exp:
        return 0.0
    te, sg = exp
    return 2.0 * math.exp(-0.5 * ((t - te) / sg) ** 2) - 1.0


def backward_jumps(times):
    """How many consecutive picks go back in shooting time by more than FREE_BACK_S."""
    ts = [t for t in times if t is not None]
    return sum(1 for a, b in zip(ts, ts[1:]) if b < a - FREE_BACK_S)


def fmt(ts):
    try:
        return datetime.fromtimestamp(ts).strftime("%d/%m %H:%M")
    except Exception:
        return "?"
