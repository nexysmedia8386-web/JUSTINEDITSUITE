"""Editor profile — what Justin EDIT Suite has learned from the editor's own decisions.

Stored at %APPDATA%/JustinEditSuite/profile.json (override: env JES_PROFILE), shared by the panel (JS writes events)
and the engine (Python derives rules). Only aggregated counters + a short history are kept.

stats (all incremental, exponential moving averages where it makes sense):
  broll_len   — shot length of b-roll the editor keeps (from finished timelines / trims)
  talk_len    — length of dialogue pieces kept
  slowmo      — 50/60p b-roll shots seen vs slowed, mean speed used
  rotate      — tilt fixes kept vs reverted / applied by hand
  take        — when two takes compete: steadier/better-framed one kept vs the other
  triage      — groups needing upscale / wrong orientation: excluded vs kept
  quality     — quality of shots the editor removes vs keeps (sets the minimum)
  sfx         — density picked
  dissolve    — cuts vs dissolves, typical dissolve length
rules (derived, used as defaults; each needs enough evidence):
  broll_dur, broll_dur_static, auto_slowmo_60p, slowmo_speed, auto_rotate, quality_weight, min_quality,
  triage_auto_exclude_upscale, sfx_density, dissolve_s
"""
import json
import os
import time

MIN_N = 3
# B1 shot taste — features of montage.Material.taste_feats, in this order (label = how the panel explains it)
TASTE = [("stab", "shot ổn định"), ("quality", "hình đẹp / bố cục"), ("motion", "máy chuyển động nhiều"),
         ("calm_end", "cắt khi hành động đã xong"), ("slow", "slow-motion"), ("talk", "có người nói trong shot")]
# guard rails: taste may only STRENGTHEN these. Unticking "fix this shaky shot" means the editor liked that shot's
# content, not shakiness — and the features are correlated (busy camera ↔ cut mid-action), so an unconstrained fit
# learned "calm_end < 0" from a pure motion taste and doubled mid-action cuts (10 → 19 on 340 clips)
NONNEG = {"stab", "quality", "calm_end"}
MAX_PAIRS = 400
TASTE_SCALE = 0.05        # 1 logit of preference → 0.05 montage score (w_stab is 0.45 per unit of stability)
TASTE_CAP = 0.3


def path():
    p = os.environ.get("JES_PROFILE")
    if p:
        return p
    # same folder as the panel's NX.cfgDir (APPDATA on Windows, else HOME) — learn.js writes this file too; it used
    # ~/.config on macOS, so the Mac panel and engine each kept their own profile
    base = os.environ.get("APPDATA") or os.environ.get("HOME") or os.path.expanduser("~")
    return os.path.join(base, "JustinEditSuite", "profile.json")


def load():
    try:
        return json.load(open(path(), encoding="utf-8"))
    except Exception:
        return {"version": 1, "stats": {}, "history": []}


def save(P):
    P["rules"] = derive(P)
    P["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(os.path.dirname(path()), exist_ok=True)
    tmp = path() + ".tmp"
    json.dump(P, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, path())
    return P


def ema(stat, x, alpha=0.2):
    """Running mean that follows the editor's recent habits (and a plain count)."""
    n = stat.get("n", 0)
    stat["mean"] = x if n == 0 else stat["mean"] * (1 - alpha) + x * alpha
    stat["n"] = n + 1
    return stat


def bump(P, key, field, by=1):
    s = P.setdefault("stats", {}).setdefault(key, {})
    s[field] = s.get(field, 0) + by
    return s


def note(P, kind, text):
    P.setdefault("history", []).append({"t": time.strftime("%Y-%m-%d %H:%M"), "kind": kind, "text": text})
    P["history"] = P["history"][-200:]


def derive(P):
    s = P.get("stats", {})
    r = {}
    bl = s.get("broll_len", {})
    if bl.get("n", 0) >= MIN_N:
        r["broll_dur"] = round(min(4.5, max(1.0, bl["mean"])), 2)
        r["broll_dur_static"] = round(max(0.9, r["broll_dur"] * 0.8), 2)
    sm = s.get("slowmo", {})
    if sm.get("seen", 0) >= 5:
        rate = sm.get("slowed", 0) / max(1, sm["seen"])
        r["auto_slowmo_60p"] = rate >= 0.35
        r["slowmo_rate"] = round(rate, 2)
        if sm.get("speed_n", 0) >= MIN_N:
            r["slowmo_speed"] = int(round(max(20, min(80, sm["speed_sum"] / sm["speed_n"])) / 5) * 5)
    ro = s.get("rotate", {})
    if ro.get("kept", 0) + ro.get("reverted", 0) + ro.get("manual", 0) >= MIN_N:
        r["auto_rotate"] = ro.get("reverted", 0) <= 0.3 * (ro.get("kept", 0) + ro.get("manual", 0) + ro.get("reverted", 0))
    tk = s.get("take", {})
    if tk.get("stab_win", 0) + tk.get("stab_loss", 0) >= MIN_N:
        w = tk["stab_win"] / (tk["stab_win"] + tk["stab_loss"])
        r["quality_weight"] = round(0.04 + 0.2 * w, 3)          # 0.04 (ignores steadiness) … 0.24 (always the steadier take)
    q = s.get("quality", {})
    if q.get("removed_n", 0) >= MIN_N and q.get("kept_n", 0) >= MIN_N:
        rm, kp = q["removed_sum"] / q["removed_n"], q["kept_sum"] / q["kept_n"]
        if kp - rm >= 8:
            r["min_quality"] = int(round((rm + kp) / 2))
    tr = s.get("triage", {})
    if tr.get("upscale_excluded", 0) + tr.get("upscale_kept", 0) >= 2:
        r["triage_auto_exclude_upscale"] = tr.get("upscale_excluded", 0) >= 2 * tr.get("upscale_kept", 0)
    sf = s.get("sfx", {})
    if sum(sf.get(k, 0) for k in ("light", "medium", "heavy")) >= MIN_N:
        r["sfx_density"] = max(("light", "medium", "heavy"), key=lambda k: sf.get(k, 0))
    ds = s.get("dissolve", {})
    if ds.get("n", 0) >= MIN_N:
        r["dissolve_s"] = round(ds["mean"], 2)
        r["dissolve_rate"] = round(ds.get("n", 0) / max(1, ds.get("n", 0) + s.get("cuts", {}).get("n", 0)), 2)
    tt = taste_text(fit_taste(P.get("taste_pairs") or [])["w"])
    if tt:
        r["shot_taste"] = tt
    return r


def rules():
    return derive(load())


# ---------------------------------------------------------------------- B1: shot taste (Bradley–Terry)
def record_taste(P, changes, preset):
    """Kept / unticked AI shot changes → preference pairs. A change carries "pf" = {"before": φ, "after": φ} (set by
    aiedit.resolve). Kept = after ≻ before, unticked = before ≻ after. Changes the editor asked for by description
    ("query") say nothing about taste — the description chose the shot — so they are not learned from."""
    pairs = P.setdefault("taste_pairs", [])
    n = 0
    for c in changes or []:
        pf = c.get("pf") or {}
        a, b = pf.get("after"), pf.get("before")
        if c.get("ok") is False or c.get("src") == "query" or not a or not b or len(a) != len(TASTE) or len(b) != len(TASTE):
            continue
        d = [round(float(x) - float(y), 3) for x, y in zip(a, b)]
        if max(abs(x) for x in d) < 0.02:                  # nothing to compare on
            continue
        pairs.append({"t": time.strftime("%Y-%m-%d %H:%M"), "p": preset or "", "k": c.get("kind", ""),
                      "d": d, "y": 0 if c.get("skip") else 1})
        n += 1
    P["taste_pairs"] = pairs[-MAX_PAIRS:]
    return n


def trust(n_kept, n_unticked):
    """How much of the learned taste to use, 0..1, from how much evidence there is.
    n_kept / n_unticked: AI shot changes the editor kept / unticked (all presets). The fitted weights are multiplied
    by this before montage uses them; 0 → montage behaves exactly as without learning."""
    # simulated editor: 10 pairs → direction right only sometimes (cos 0.35–0.86), 30 → 0.51–0.76, 100 → 0.78–0.93.
    # Unticks weigh double: an editor who keeps everything only "confirms" the review's own rules, and without ≥ 2
    # unticks there is no contrast to learn from at all. 20 kept + 10 unticked → 0.5, 70 + 30 → 0.76.
    n_eff = n_kept + 2.0 * n_unticked
    if n_unticked < 2 or n_eff < 12:
        return 0.0
    return n_eff / (n_eff + 40.0)


def fit_taste(pairs, preset=None, lam=2.0):
    """L2-regularised Bradley–Terry (logistic regression on feature differences, no intercept, mirrored pairs so
    "the AI suggested it" carries no bias by itself). Recent pairs and pairs of the same preset weigh more.
    → {"w": {feature: montage-score weight}, "n", "kept", "unticked", "trust"}."""
    import numpy as np
    k = len(TASTE)
    if not pairs:
        return {"w": {}, "n": 0, "kept": 0, "unticked": 0, "trust": 0.0}
    D = np.array([p["d"] for p in pairs], float)
    y = np.array([p["y"] for p in pairs], float)
    age = np.arange(len(pairs))[::-1]                       # 0 = newest
    wt = 0.5 ** (age / 150.0) * np.array([1.0 if (not preset or p.get("p") == preset) else 0.5 for p in pairs])
    X, Y, W = np.vstack([D, -D]), np.concatenate([y, 1 - y]), np.concatenate([wt, wt])
    act = np.ones(k, bool)                                  # features still free (guard rails that went < 0 are pinned at 0)
    for _ in range(k):
        w = np.zeros(k)
        Xa = X[:, act]
        wa = np.zeros(int(act.sum()))
        for _ in range(30):                                 # Newton steps (≤ 6×6 solve)
            p = 1.0 / (1.0 + np.exp(-np.clip(Xa @ wa, -30, 30)))
            g = Xa.T @ (W * (p - Y)) + lam * wa
            H = (Xa * (W * p * (1 - p))[:, None]).T @ Xa + lam * np.eye(len(wa))
            step = np.linalg.solve(H, g)
            wa -= step
            if np.abs(step).max() < 1e-6:
                break
        w[act] = wa
        neg = [j for j in range(k) if act[j] and TASTE[j][0] in NONNEG and w[j] < 0]
        if not neg:
            break
        act[neg] = False
    kept, unt = int(y.sum()), int(len(y) - y.sum())
    t = float(min(1.0, max(0.0, trust(kept, unt))))
    sw = np.clip(w * TASTE_SCALE * t, -TASTE_CAP, TASTE_CAP)
    return {"w": {TASTE[j][0]: round(float(sw[j]), 4) for j in range(k) if abs(sw[j]) >= 0.005},
            "n": len(pairs), "kept": kept, "unticked": unt, "trust": round(t, 2)}


_TASTE_CACHE = {}


def shot_taste(preset=None):
    """Learned taste weights for montage scoring, cached until the profile changes. {} = nothing learned yet."""
    try:
        mt = os.path.getmtime(path())
    except OSError:
        return {}
    key = (preset or "", mt)
    if key not in _TASTE_CACHE:
        _TASTE_CACHE.clear()
        _TASTE_CACHE[key] = fit_taste(load().get("taste_pairs") or [], preset)["w"]
    return _TASTE_CACHE[key]


def taste_text(w):
    """Weights → short Vietnamese summary for the "Tool đã học" chips ("" when nothing stands out)."""
    lab = dict(TASTE)
    top = sorted(((v, k) for k, v in (w or {}).items() if abs(v) >= 0.03), key=lambda z: -abs(z[0]))[:3]
    return ", ".join(("thích " if v > 0 else "tránh ") + lab[k] for v, k in top)


def asm_overrides():
    """Rules translated into assemble/brollpick config keys (only confident ones)."""
    r = rules()
    out = {}
    for k in ("broll_dur", "broll_dur_static", "slowmo_speed", "quality_weight", "min_quality"):
        if k in r:
            out[k] = r[k]
    if "auto_slowmo_60p" in r:              # rule name (panel) → the key assemble actually reads
        out["auto_slowmo"] = r["auto_slowmo_60p"]
    if "auto_rotate" in r:
        out["auto_rotate"] = r["auto_rotate"]
    return out
