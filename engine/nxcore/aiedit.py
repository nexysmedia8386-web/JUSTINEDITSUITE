"""AI editing of a built plan — Justin EDIT Suite.

Every kind of AI edit becomes a CHANGE SET on the plan (list of ops) → the panel previews it (before / after thumbs,
reason, tick boxes) → `apply` writes the next plan version (…_vN+1) → nxcut build makes the new sequence. Nothing
touches the old plan or the open timeline, so every edit can be undone by opening the previous version.

  review      AI rà bản dựng: look-alike / repeated shots, cut in the middle of an action, shaky or defective source,
              talking inside a music montage, jump cuts, missing footage → each issue comes with its fix op
  context     compact text of the edit for an LLM ("ra lệnh bằng lời"); the LLM answers with ops (see OPS below)
  resolve     ops → concrete changes: a "query" replacement is searched with SigLIP2 + the montage scoring rules
  apply       changes → plan_<name>_vN+1.json
  transcript  the words of the edit, per segment (sửa bằng transcript); `fillers` = filler words / stutters / pauses
  post        per timeline piece: reframe path (subject tracking), colour match (Lumetri), stabilise → host.jsx

OPS (indices = segment index in the plan):
  {"kind":"replace","seg":i,"query":"…"}  or  {…,"file","in","out"}    new picture for a slot
  {"kind":"slide","seg":i,"by":±s}  or {…,"in":s}                       same clip, other moment
  {"kind":"trim","seg":i,"start":±s,"end":±s}                           (free-timeline plans)
  {"kind":"remove","seg":i}   {"kind":"move","seg":i,"to":j}   {"kind":"swap","seg":i,"with":j}
  {"kind":"speed","seg":i,"speed":50}   {"kind":"scale","seg":i,"scale":112}
  {"kind":"cut_words","seg":i,"t0":s,"t1":s}                            (source seconds; transcript edits)
  {"kind":"replan","cfg":{"pace":0.8,…}}                                 (beat montage: re-cut with new settings)
CLI: python -m nxcore.aiedit <cmd> PLAN [--ops ops.json] [--out new_plan.json]   → JSON on the last line "AIEDIT {...}"
"""
import argparse
import copy
import json
import math
import os
import re
import sys

import numpy as np

from . import jsonio

FILLERS = {"ừ", "ừm", "ờ", "ờm", "ơ", "à", "ừa", "hừm", "ầy", "um", "uh", "uhm", "ah", "eh", "erm", "hmm"}
VERSION_RE = re.compile(r"^(.*?)(?:_v(\d+))?$")


# ====================================================================== plan helpers
def load_plan(path):
    P = jsonio.load(path, None)
    if not isinstance(P, dict) or "segments" not in P:
        raise SystemExit(f"plan lỗi / không có: {path}")
    return P


def is_grid(P):
    """Montage plans place every segment on a fixed timeline span ("tl", cuts on the beat)."""
    return any(s.get("tl") for s in P["segments"])


def seg_dur(s):
    if s.get("tl"):
        return float(s["tl"][1]) - float(s["tl"][0])
    return (float(s["out"]) - float(s["in"])) * 100.0 / float(s.get("speed") or 100.0)


def timeline(P):
    """→ [(t0, t1)] per segment (tl when present, else cumulative)."""
    out, t = [], 0.0
    for s in P["segments"]:
        if s.get("tl"):
            a, b = float(s["tl"][0]), float(s["tl"][1])
        else:
            a, b = t, t + (seg_dur(s) if not s.get("gap") else float(s.get("out", 1)) - float(s.get("in", 0)))
        out.append((a, b))
        t = b
    return out


def next_name(name):
    m = VERSION_RE.match(name or "PLAN")
    base, v = m.group(1), int(m.group(2) or 1)
    return f"{base}_v{v + 1}"


def mmss(t):
    t = max(0.0, float(t))
    return f"{int(t // 60)}:{t % 60:04.1f}"


# ====================================================================== material (montage scoring reused)
# Files Material reads: a change in any of them (new scan, re-index, describe…) rebuilds it.
_MAT_FILES = ("media.json", "shake.json", "qc.json", "segments.json", "broll_tags.json", "auto_desc.json", "speech.json",
              os.path.join("embed", "siglip.npz"))
_MAT_CACHE = {}          # (src, preset) → (signature, Material) — kept across commands by the AI worker process


def _mat_sig(src):
    W = os.path.join(src, "_nexys_work")
    sig = []
    for f in _MAT_FILES:
        try:
            st = os.stat(os.path.join(W, f))
            sig.append((f, st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append((f, 0, 0))
    return tuple(sig)


def material(src, preset, C, quiet=True):
    """Material of a source folder, reused while its analysis files are unchanged (one Material per worker)."""
    from . import montage as M
    key, sig = (os.path.normcase(os.path.abspath(src)), preset or ""), _mat_sig(src)
    hit = _MAT_CACHE.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    if quiet:
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            mat = M.Material(src, C)
    else:
        mat = M.Material(src, C)
    _MAT_CACHE[key] = (sig, mat)
    return mat


class Ctx:
    """Footage model of the plan's source folder + the montage assigner primed with the plan's current shots, so
    every replacement obeys the same rules as the original cut (stability, look-alike spread, action, budgets)."""

    def __init__(self, P, quiet=True):
        from . import montage as M
        self.M, self.P = M, P
        self.src = P["src"]
        self.C = M.config(P.get("preset") or "", {})
        self.mat = material(self.src, P.get("preset"), self.C, quiet)
        self.by_file = {}
        for c in self.mat.cands:
            self.by_file.setdefault(c["file"], []).append(c)
        self.tl = timeline(P)
        self.total = self.tl[-1][1] if self.tl else 1.0

    def cand(self, s):
        """Candidate window holding a plan segment (synthetic one when the window was not a candidate)."""
        f = s.get("file")
        if not f:
            return None
        mid = (float(s["in"]) + float(s["out"])) / 2
        cs = self.by_file.get(f) or []
        hit = [c for c in cs if c["a"] - 0.05 <= mid <= c["b"] + 0.05]
        if hit:
            return hit[0]
        m = self.mat.media.get(f) or {}
        return {"file": f, "a": float(s["in"]), "b": float(s["out"]), "slow_only": False, "fps": float(m.get("fps") or 30),
                "dur": float(m.get("dur") or 0), "q": 0.5, "comp": 0.5, "rot": 0.0, "hash": None, "pin": False, "desc": "",
                "cam": m.get("cam", ""), "t_abs": None, "vec": None, "id": f"{f}@{s['in']}"}

    def assigner(self, skip=()):
        """Assigner seeded with every plan shot except the `skip` indices (the slots being refilled)."""
        asg = self.M.Assigner(self.mat, self.C, chrono=False, log=[])
        for i, s in enumerate(self.P["segments"]):
            if i in skip or not s.get("file") or s.get("gap"):
                continue
            c = self.cand(s)
            asg.placed[i] = c
            asg.used.setdefault(s["file"], []).append([float(s["in"]), float(s["out"])])
            asg.uses[s["file"]] = asg.uses.get(s["file"], 0) + 1
        return asg

    def slot(self, i):
        s = self.P["segments"][i]
        lab = s.get("beat") or "main"
        return {"t0": self.tl[i][0], "t1": self.tl[i][1], "label": lab, "energy": 0.85 if lab == "drop" else 0.6,
                "slow": float(s.get("speed") or 100) != 100 or lab in ("intro", "break", "outro")}

    def neigh(self, i, asg):
        out = []
        for d in (1, 2):
            for j, side in ((i - d, "L"), (i + d, "R")):
                if j in asg.placed:
                    out.append((d, dict(asg.placed[j], _side=side)))
        return out

    def ok_window(self, f, s0, Dsrc):
        """A replacement must not bring back the defects the review hunts: mid-action cut-out, shaky / bad source."""
        e, _ = self.mat.edge_action(f, s0, Dsrc)
        if e >= 0.75:
            return False
        bad = (self.mat.bad.get(f) or {}).get("ranges", [])
        over = sum(max(0.0, min(r[1], s0 + Dsrc) - max(r[0], s0)) for r in bad)
        return over / max(0.1, Dsrc) < 0.2

    def best_replacement(self, i, only=None, exclude_file=True, tries=6):
        """→ op dict for the best new picture in slot i (or None). A pick that fails ok_window is excluded and the
        next best is tried, so a fix never trades one defect for the same defect."""
        s = self.P["segments"][i]
        asg = self.assigner(skip={i})
        D = self.tl[i][1] - self.tl[i][0]
        cur = s.get("file")
        banned = set()
        flt = (lambda c: (not exclude_file or c["file"] != cur) and id(c) not in banned and (only is None or only(c)))
        for _ in range(tries):
            best = None
            for ms in (float(self.C.get("min_stab", 0.45)), 0.3, 0.0):
                best = asg.pick(D, self.slot(i), self.neigh(i, asg), x=(self.tl[i][0] + self.tl[i][1]) / 2 / self.total,
                                min_stab=ms, only=flt, k=i)
                if best:
                    break
            if not best:
                return None
            sc, c, s0, sp, st, Dsrc = best
            if self.ok_window(c["file"], s0, Dsrc):
                return {"kind": "replace", "seg": i, "file": c["file"], "in": round(s0, 3), "out": round(s0 + Dsrc, 3),
                        "speed": sp, "stab": round(st, 2), "desc": c.get("desc") or ""}
            banned.add(id(c))
        return None

    def best_slide(self, i):
        """Same clip, better moment (steadier / action finished) → op or None."""
        s = self.P["segments"][i]
        c = self.cand(s)
        sp = float(s.get("speed") or 100)
        Dsrc = float(s["out"]) - float(s["in"])
        others = [[float(x["in"]), float(x["out"])] for k, x in enumerate(self.P["segments"])
                  if k != i and x.get("file") == s["file"]]
        got = self.mat.best_sub(c, Dsrc, sp != 100, others, speech_w=float(self.C.get("w_speech", 0.35)), gap=0.5)
        if not got:
            return None
        s0 = got[0]
        if abs(s0 - float(s["in"])) < 0.2:
            return None
        return {"kind": "slide", "seg": i, "in": round(s0, 3), "out": round(s0 + Dsrc, 3), "stab": round(got[1], 2)}


def virt(X, op):
    """Write a computed fix into the context's working plan, so the NEXT suggestion sees it (no two slots get the
    same replacement shot, no fix lands next to a look-alike chosen a moment ago)."""
    if not op or op.get("seg") is None:
        return op
    s = X.P["segments"][op["seg"]]
    for k in ("file", "in", "out"):
        if op.get(k) is not None:
            s[k] = op[k]
    if op.get("kind") == "scale":
        s["scale"] = op["scale"]
    s.pop("gap", None)
    return op


# ====================================================================== review
def review(P, max_issues=60):
    """→ [{"seg", "type", "sev" 1-3, "msg", "fix": op | None}] most severe first."""
    X = Ctx(copy.deepcopy(P))
    P = X.P
    mat, segs = X.mat, P["segments"]
    grid = is_grid(P)
    vids = [i for i, s in enumerate(segs) if s.get("file") and not s.get("gap")]
    cand = {i: X.cand(segs[i]) for i in vids}
    issues = []

    def add(i, typ, sev, msg, fix):
        issues.append({"seg": i, "type": typ, "sev": sev, "msg": msg, "fix": fix, "t": round(X.tl[i][0], 2)})

    flagged = set()
    # 1. repeated / look-alike shots close together (re-takes of one scene read as "lặp source")
    for a_, i in enumerate(vids):
        for j in vids[a_ + 1:a_ + 5]:
            si, sj = segs[i], segs[j]
            la = mat.look_alike(cand[i], cand[j])
            same = si["file"] == sj["file"] and abs(float(si["in"]) - float(sj["in"])) < 15
            if (la >= 0.8 or same) and j not in flagged and not sj.get("audio"):
                flagged.add(j)
                add(j, "repeat", 3, f"giống shot #{i} ({'cùng clip' if same else f'{la:.0%} giống'}) chỉ cách {j - i} shot",
                    virt(X, X.best_replacement(j, only=lambda c, ci=cand[i]: mat.look_alike(c, ci) < 0.5)))
    # 2. cut-out in the middle of an action
    for i in vids:
        s = segs[i]
        if s.get("audio") or i in flagged:
            continue
        e, _ = mat.edge_action(s["file"], float(s["in"]), float(s["out"]) - float(s["in"]))
        if e >= 0.75:
            fix = X.best_slide(i)
            if fix:
                e2, _ = mat.edge_action(s["file"], fix["in"], fix["out"] - fix["in"])
                if e2 > e - 0.25:
                    fix = None
            add(i, "action", 2, "cắt khi chủ thể còn đang chuyển động (hành động chưa xong)", virt(X, fix or X.best_replacement(i)))
            flagged.add(i)
    # 3. shaky / defective source inside the used window
    for i in vids:
        s = segs[i]
        if i in flagged:
            continue
        bad = [r for r in (mat.bad.get(s["file"]) or {}).get("ranges", []) if r[1] > float(s["in"]) and r[0] < float(s["out"])]
        over = sum(min(r[1], float(s["out"])) - max(r[0], float(s["in"])) for r in bad)
        L = max(0.1, float(s["out"]) - float(s["in"]))
        if over / L >= 0.2:
            fix = X.best_slide(i) if not s.get("audio") else None
            add(i, "shaky", 3 if over / L > 0.5 else 2, f"{over / L:.0%} thời lượng shot bị rung / mờ / đen",
                virt(X, fix or (None if s.get("audio") else X.best_replacement(i))))
            flagged.add(i)
    # 4. someone talking on camera inside a music montage
    if grid:
        for i in vids:
            s = segs[i]
            if s.get("audio") or i in flagged:
                continue
            tk = mat.talk_mask(s["file"])
            if len(tk) < 2:
                continue
            a, b = int(float(s["in"]) * X.M.HZ), int(float(s["out"]) * X.M.HZ)
            a, b = max(0, min(a, len(tk) - 1)), max(0, min(b, len(tk) - 1))
            share = (tk[b] - tk[a]) / max(1, b - a)
            if share >= 0.35:
                add(i, "speech", 2, f"có người nói trong {share:.0%} shot (montage nhạc)", virt(X, X.best_replacement(i)))
                flagged.add(i)
    # 5. jump cuts between two takes of the same speaker (free-timeline plans)
    if not grid:
        for i, j in zip(vids, vids[1:]):
            a, b = segs[i], segs[j]
            if j == i + 1 and a.get("audio") and b.get("audio") and a["file"] == b["file"] \
                    and 0 < float(b["in"]) - float(a["out"]) < 2.0 and not b.get("scale"):
                add(j, "jump", 1, "jump cut (cùng góc máy, bỏ một đoạn ngắn) → punch-in 112% che nhảy hình",
                    {"kind": "scale", "seg": j, "scale": 112})
    # 6. missing footage cards
    for i, s in enumerate(segs):
        if s.get("gap") and grid:
            add(i, "gap", 3, "đang là thẻ THIẾU HÌNH", virt(X, X.best_replacement(i, exclude_file=False)))
    # 7. C1: what the channel's real audience leaves on (YouTube Analytics lessons, ≥ 2 videos, lift ≥ 1.5×)
    try:
        from .ytanalytics import FEAT_TEXT, learned_risks, segment_features
        risk = learned_risks()
    except Exception:
        risk = {}
    if risk:
        for i in vids:
            if i in flagged:
                continue
            f = [k for k, v in segment_features(P, i, X.tl).items() if v and k in risk]
            if not f:
                continue
            k = max(f, key=lambda k: risk[k])
            L = X.tl[i][1] - X.tl[i][0]
            fix = None
            if k == "long" and not grid and L > 5.0:
                s = segs[i]
                if s.get("audio"):                            # talking: never cut words off — a punch-in changes the picture
                    fix = None if s.get("scale") else {"kind": "scale", "seg": i, "scale": 112}
                else:                                          # b-roll: keep the best 4 s
                    fix = {"kind": "trim", "seg": i, "start": 0.0, "end": -round(L - 4.0, 2)}
            add(i, "retention", 1, f"khán giả kênh của anh hay rời đi ở {FEAT_TEXT[k]} (×{risk[k]})", fix)
            flagged.add(i)
    issues.sort(key=lambda x: (-x["sev"], x["seg"]))
    return issues[:max_issues]


# ====================================================================== context for an LLM
TAGS = ["biển", "bãi biển", "sóng", "thuyền", "đồi cát", "sa mạc", "núi", "đồi", "rừng", "cây cối", "đồng ruộng", "sông", "hồ",
        "thác nước", "bầu trời", "mây", "hoàng hôn", "bình minh", "ban đêm", "đèn đường", "thành phố", "đường phố", "con đường",
        "xe hơi", "xe máy", "cầu", "nhà cửa", "khách sạn", "hồ bơi", "nhà hàng", "món ăn", "đồ uống", "chợ", "cửa hàng",
        "người đàn ông", "người phụ nữ", "trẻ em", "nhóm người", "đám đông", "khuôn mặt cận cảnh", "người đi bộ", "người chạy",
        "người ngồi", "selfie", "chụp ảnh", "nhảy múa", "biểu diễn", "sân khấu", "đám cưới", "cô dâu", "hoa", "động vật", "chó",
        "mèo", "chim", "flycam trên cao", "trong xe", "nội thất", "văn phòng", "máy tính", "điện thoại", "chữ viết / biển hiệu"]
_TAGV = {}


def shot_tags(X, c, k=3):
    """Top SigLIP2 labels of a shot (Vietnamese) — gives the LLM eyes when no vision description exists."""
    if c is None or c.get("vec") is None:
        return []
    if "v" not in _TAGV:
        try:
            from .ai.embed import embed_texts
            _TAGV["v"] = embed_texts([f"một cảnh quay có {t}" for t in TAGS])
        except Exception:
            _TAGV["v"] = None
    V = _TAGV["v"]
    if V is None:
        return []
    s = V @ c["vec"]
    order = np.argsort(-s)[:k]
    return [TAGS[j] for j in order if s[j] >= s[order[0]] - 0.02]


def context(P, max_lines=400):
    X = Ctx(P)
    segs = P["segments"]
    lines = []
    spk = _speaker_fn(P)
    for i, s in enumerate(segs[:max_lines]):
        a, b = X.tl[i]
        if s.get("gap"):
            lines.append(f"#{i} {mmss(a)}–{mmss(b)} THIẾU HÌNH")
            continue
        c = X.cand(s)
        desc = (c.get("desc") or "").replace("\n", " ")[:90]
        tg = shot_tags(X, c)
        desc = (desc + (" · " if desc and tg else "") + (", ".join(tg) if tg else "")) or s.get("label", "")
        sp = f" slow{int(float(s['speed']))}%" if float(s.get("speed") or 100) != 100 else ""
        tag = s.get("beat") or ("thoại" if s.get("audio") else "b-roll")
        if spk and s.get("audio"):                      # C3: who is talking in this shot
            who = spk(s["file"], (float(s["in"]) + float(s["out"])) / 2)
            tag += f" {who}" if who else ""
        # compact on purpose: a local 8B model on an 8 GB card must fit the whole edit in a small context window
        lines.append(f"#{i} {mmss(a)} {b - a:.1f}s {tag}{sp} · {desc}")
    head = {"name": P.get("name"), "mode": "montage theo nhạc (cắt đúng beat)" if is_grid(P) else "dựng tự do",
            "segments": len(segs), "duration": round(X.total, 1), "music": (P.get("montage") or {}).get("music"),
            "bpm": (P.get("montage") or {}).get("bpm"), "index": X.mat.has_vec}
    return head, "\n".join(lines)


# ====================================================================== resolve ops → concrete changes
def _query_filter(X, text, top=60, verify=0):
    """Candidates whose picture matches `text` best (SigLIP2) → predicate, or None without the AI pack / index.
    verify = N (B2): the local vision model also LOOKS at the N best and only those it judges a match stay allowed
    (none matched / no Ollama → the SigLIP set as before). The predicate carries .vlm {id(cand): score 0..1}."""
    if not X.mat.has_vec:
        return None
    try:
        from .ai.embed import embed_texts
        q = embed_texts([text])[0]
    except Exception:
        return None
    scored = [(float(np.dot(c["vec"], q)), id(c), c) for c in X.mat.cands if c.get("vec") is not None]
    if not scored:
        return None
    scored.sort(key=lambda z: -z[0])
    keep = {k for _, k, _ in scored[:top]}
    vlm = {}
    if verify:
        try:
            from .ai import rerank as RR
            name, _ = RR.model_for("auto")
            if name:
                cs = [c for _, _, c in scored[:int(verify)]]
                ims = RR.frames_rgb(X.src, [(c["file"], (c["a"] + c["b"]) / 2) for c in cs], X.mat.media)
                for c, im in zip(cs, ims):
                    s, _ = RR.score_one(name, RR.DEF_HOST, text, RR.jpeg_b64(im)) if im is not None else (None, "")
                    if s is not None:
                        vlm[id(c)] = s
                good = {k for k, s in vlm.items() if s >= RR.MATCH}
                if good:
                    keep = good
        except Exception:                               # noqa: BLE001 — verification is a bonus, never a blocker
            pass

    def flt(c):
        return id(c) in flt.keep
    flt.keep, flt.vlm = keep, vlm
    flt.sig = {k for _, k, _ in scored[:top]}            # fallback when the montage rules reject every verified shot
    return flt


def resolve(P, ops):
    """→ list of concrete changes (each with before / after) — ready for preview + apply."""
    X = None
    segs = P["segments"]
    out = []

    def ctx():
        nonlocal X
        if X is None:
            X = Ctx(copy.deepcopy(P))        # working copy: each resolved change is visible to the next one
        return X

    def snap(s):
        return {"file": s.get("file"), "in": s.get("in"), "out": s.get("out"), "speed": s.get("speed", 100),
                "scale": s.get("scale", 100), "label": s.get("label", "")}

    def feats(v):
        """taste features of one side of a change (B1: learned from what the editor keeps / unticks)."""
        if not v or not v.get("file"):
            return None
        try:
            X_ = ctx()
            return X_.mat.taste_feats(X_.cand(v), float(v["in"]), float(v["out"]) - float(v["in"]), float(v.get("speed") or 100))
        except Exception:                               # noqa: BLE001 — learning is optional, the change is not
            return None

    n = 0
    grid = is_grid(P)
    for op in ops:
        kind = op.get("kind")
        if grid and kind == "move":            # beat grid: slots keep their timing → moving a picture = swapping two slots
            kind = "swap"
        idxs = op.get("seg")
        idxs = idxs if isinstance(idxs, list) else [idxs] if idxs is not None else [None]
        for i in idxs:
            n += 1
            ch = {"id": op.get("id") or f"c{n}", "kind": kind, "seg": i, "why": op.get("why") or op.get("msg") or "",
                  "ok": True, "err": ""}
            try:
                if kind != "replan" and (not isinstance(i, int) or not 0 <= i < len(segs)):
                    raise ValueError(f"không có shot #{i}")
                s = segs[i] if isinstance(i, int) else None
                if s is not None:
                    ch["before"] = snap(s)
                    ch["t"] = round(timeline(P)[i][0], 2)
                if kind == "replace":
                    if op.get("file"):
                        new = {k: op[k] for k in ("file", "in", "out") if k in op}
                        new["speed"] = op.get("speed", s.get("speed", 100))
                        ch["stab"], ch["desc"] = op.get("stab"), op.get("desc", "")
                    else:
                        only = _query_filter(ctx(), op["query"], verify=8 if op.get("verify") else 0) if op.get("query") else None
                        if op.get("query") and only is None:
                            raise ValueError("tìm cảnh theo mô tả cần gói AI + chỉ mục SigLIP2 (Tìm footage → Lập chỉ mục)")
                        got = ctx().best_replacement(i, only=only)
                        if not got and only is not None and only.keep is not only.sig:
                            only.keep = only.sig
                            got = ctx().best_replacement(i, only=only)
                        if not got:
                            raise ValueError("không tìm được cảnh thay phù hợp")
                        if only is not None and only.vlm:              # B2: what the vision model said about the pick
                            hit = next((s for c in ctx().by_file.get(got["file"], []) for k, s in only.vlm.items()
                                        if id(c) == k and c["a"] - 0.05 <= got["in"] <= c["b"]), None)
                            ch["vlm"] = round(hit, 2) if hit is not None else None
                        new = {k: got[k] for k in ("file", "in", "out", "speed")}
                        virt(ctx(), got)
                        ch["stab"] = got.get("stab")
                        ch["desc"] = got.get("desc", "")
                    ch["after"] = dict(ch["before"], **new)
                elif kind == "slide":
                    L = float(s["out"]) - float(s["in"])
                    a = float(op["in"]) if "in" in op else float(s["in"]) + float(op.get("by", 0))
                    dur = float((ctx().mat.media.get(s["file"]) or {}).get("dur") or 1e9)
                    a = max(0.0, min(a, dur - L - 0.05))
                    ch["after"] = dict(ch["before"], **{"in": round(a, 3), "out": round(a + L, 3)})
                elif kind == "trim":
                    if s.get("tl"):
                        raise ValueError("montage theo beat: dùng slide (thời lượng shot cố định theo nhịp)")
                    a, b = float(s["in"]) + float(op.get("start", 0)), float(s["out"]) + float(op.get("end", 0))
                    if b - a < 0.2:
                        raise ValueError("shot quá ngắn sau khi trim")
                    ch["after"] = dict(ch["before"], **{"in": round(max(0.0, a), 3), "out": round(b, 3)})
                elif kind in ("speed", "scale"):
                    v = float(op[kind])
                    ch["after"] = dict(ch["before"], **{kind: v})
                elif kind == "remove":
                    if s.get("tl") and i == 0:
                        raise ValueError("không xóa shot đầu tiên của montage (thay hình thay vì xóa)")
                    ch["after"] = None
                elif kind in ("move", "swap"):
                    j = int(op.get("to", op.get("with")))
                    if not 0 <= j < len(segs):
                        raise ValueError(f"không có vị trí #{j}")
                    ch["to"] = j
                    ch["after"] = snap(segs[j]) if kind == "swap" else ch["before"]
                elif kind == "cut_words":
                    if s.get("tl"):
                        raise ValueError("montage theo beat: không cắt chữ (thời lượng shot theo nhịp)")
                    t0, t1 = float(op["t0"]), float(op["t1"])
                    if not (float(s["in"]) - 0.05 <= t0 < t1 <= float(s["out"]) + 0.05):
                        raise ValueError("đoạn chữ nằm ngoài shot")
                    ch.update(t0=round(t0, 3), t1=round(t1, 3), text=op.get("text", ""))
                    ch["after"] = ch["before"]
                elif kind == "replan":
                    if not is_grid(P):
                        raise ValueError("replan chỉ dùng cho montage theo nhạc")
                    ch["cfg"] = {k: v for k, v in (op.get("cfg") or {}).items()
                                 if k in ("pace", "min_shot", "max_shot", "min_stab", "shuffle_seed", "w_motion", "auto_slowmo")}
                    if not ch["cfg"]:
                        raise ValueError("replan không có thông số hợp lệ")
                else:
                    raise ValueError(f"thao tác lạ: {kind}")
            except Exception as e:                          # noqa: BLE001 — reported in the preview
                ch["ok"], ch["err"] = False, str(e)[:200]
            if ch["ok"] and kind in ("replace", "slide"):
                ch["src"] = "query" if op.get("query") else "review" if op.get("issue") else "cmd"
                ch["pf"] = {"before": feats(ch.get("before")), "after": feats(ch.get("after"))}
            out.append(ch)
    return out


def thumbs(P, changes):
    """before / after thumbnail per change (cached JPEGs) — what the preview shows."""
    from .ai.embed import thumb
    src = P["src"]
    for ch in changes:
        for side in ("before", "after"):
            v = ch.get(side)
            if v and v.get("file"):
                try:
                    v["thumb"] = thumb(src, {"file": v["file"], "a": float(v["in"]), "b": float(v["out"])})
                except Exception:
                    v["thumb"] = ""
    return changes


# ====================================================================== apply
def apply(P, changes, plan_path):
    """Changes (only those with ok and not excluded) → new plan dict + its path (next version)."""
    Q = copy.deepcopy(P)
    segs = Q["segments"]
    done, notes = [], []
    keep = [c for c in changes if c.get("ok", True) and not c.get("skip")]
    # field edits first (indices stable), then structural edits from the end backwards
    for c in keep:
        i, k = c.get("seg"), c["kind"]
        if k in ("replace", "slide", "trim", "speed", "scale") and isinstance(i, int):
            s, a = segs[i], c["after"]
            for f_ in ("file", "in", "out"):
                if a.get(f_) is not None:
                    s[f_] = a[f_]
            if k in ("replace", "speed") and a.get("speed") is not None:
                if float(a["speed"]) == 100:
                    s.pop("speed", None)
                else:
                    s["speed"] = float(a["speed"])
            if k == "scale":
                s["scale"] = float(a["scale"])
            if k == "replace":
                s.pop("gap", None)
                s.pop("text", None)
                s["label"] = (s.get("label", "").split(" · ")[0] + " · " + (c.get("desc") or "AI thay")[:60]).strip(" ·")
            if s.get("tl") and s.get("file"):          # beat slot: the timeline span decides the source length
                s["out"] = round(float(s["in"]) + (float(s["tl"][1]) - float(s["tl"][0])) * float(s.get("speed") or 100) / 100.0, 3)
            s.setdefault("flags", []).append(f"AI sửa: {k}" + (f" — {c['why'][:60]}" if c.get("why") else ""))
            done.append(c)
    for c in sorted([c for c in keep if c["kind"] == "swap"], key=lambda c: -c["seg"]):
        i, j = c["seg"], c["to"]
        for f_ in ("file", "in", "speed", "label", "rotate_fix"):
            segs[i][f_], segs[j][f_] = segs[j].get(f_), segs[i].get(f_)
        for s in (segs[i], segs[j]):
            for f_ in [x for x, v in list(s.items()) if v is None]:
                s.pop(f_)
            if s.get("tl"):
                s["out"] = round(float(s["in"]) + (float(s["tl"][1]) - float(s["tl"][0])) * float(s.get("speed") or 100) / 100.0, 3)
            else:
                s["out"] = round(float(s["in"]) + seg_dur(s), 3)
        done.append(c)
    struct = sorted([c for c in keep if c["kind"] in ("cut_words", "remove", "move")],
                    key=lambda c: (-c["seg"], -(c.get("t0") or 0)))
    for c in struct:
        i = c["seg"]
        if i >= len(segs):
            continue
        s = segs[i]
        if c["kind"] == "cut_words":
            # cut exactly on the (acoustically aligned) word boundaries: a padding made short words like "ừm" (60 ms)
            # survive half in each piece
            left = dict(copy.deepcopy(s), out=round(max(float(s["in"]), c["t0"]), 3))
            right = dict(copy.deepcopy(s), **{"in": round(min(float(s["out"]), max(c["t1"], c["t0"])), 3)})
            parts = [p for p in (left, right) if float(p["out"]) - float(p["in"]) >= 0.15]
            segs[i:i + 1] = parts
            notes.append(f"xóa “{c.get('text', '')[:40]}” ở shot #{i}")
        elif c["kind"] == "remove":
            if s.get("tl"):                          # beat grid: the previous shot holds over the removed slot
                p = segs[i - 1]
                p["tl"] = [p["tl"][0], s["tl"][1]]
                if p.get("file"):
                    p["out"] = round(float(p["in"]) + (float(p["tl"][1]) - float(p["tl"][0])) * float(p.get("speed") or 100) / 100.0, 3)
            del segs[i]
        elif c["kind"] == "move":
            j = c["to"]
            item = segs.pop(i)
            segs.insert(min(j, len(segs)), item)
        done.append(c)
    replan = next((c for c in keep if c["kind"] == "replan"), None)
    name = next_name(P.get("name"))
    Q["name"] = name
    Q.setdefault("ai_edits", []).append({"from": P.get("name"), "changes": len(done) + (1 if replan else 0),
                                         "kinds": sorted({c["kind"] for c in done})})
    Q.setdefault("report_notes", []).append(f"AI sửa {len(done)} thay đổi từ bản {P.get('name')}" + (": " + "; ".join(notes[:5]) if notes else ""))
    new_path = os.path.join(os.path.dirname(os.path.abspath(plan_path)), f"plan_{name}.json")
    return Q, new_path, replan


def do_replan(P, cfg, name, path):
    """Re-cut a music montage with new settings (pace, min/max shot, stability…), same music / length / frame."""
    import tempfile
    from . import montage as M
    mon = P.get("montage") or {}
    music = [m.get("file") for m in P.get("music") or [] if m.get("file")]
    seq = P.get("seq") or {}
    args = ["--src", P["src"], "--preset", P.get("preset") or "beat_cut", "--plan", path, "--name", name, "--out", P["out"]]
    for m in music[:1]:
        args += ["--music", m]
    if mon.get("end") and mon.get("music_dur") and float(mon["end"]) < float(mon["music_dur"]) - 0.5:
        args += ["--target", str(round(float(mon["end"]), 2))]
    if seq.get("w"):
        args += ["--seq", f"{seq['w']}x{seq['h']}@{seq.get('fps', 29.97)}:{seq.get('fit', 'fill')}"]
    fd, cf = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)
    try:
        M.main(args + ["--cfg", cf])
    finally:
        os.remove(cf)
    return path


# ====================================================================== transcript editing
def _words(src, f):
    from .media import stem
    d = jsonio.load(os.path.join(src, "_nexys_work", "transcripts", stem(f) + ".json"), {}) or {}
    return [w for s in d.get("segments") or [] for w in (s.get("words") or []) if len(w) >= 3 and str(w[2]).strip()]


def _speaker_fn(P):
    """C3: f(file, t) → "S1"… from the folder's diarization (speakers.json), or None when it was not run."""
    try:
        from .ai.speakers import speaker_at
        if os.path.exists(os.path.join(P["src"], "_nexys_work", "speakers.json")):
            return speaker_at(P["src"])
    except Exception:
        pass
    return None


def transcript(P):
    """Edit blocks; speech words are [start, end, text] — plus the speaker ("S1"…) as a 4th item once diarized."""
    out, cache = [], {}
    spk = _speaker_fn(P)
    for i, s in enumerate(P["segments"]):
        if s.get("gap") or not s.get("file"):
            out.append({"seg": i, "kind": "gap", "label": s.get("label", "")})
            continue
        if not s.get("audio"):
            out.append({"seg": i, "kind": "broll", "file": s["file"], "label": s.get("label", "")})
            continue
        f = s["file"]
        if f not in cache:
            cache[f] = _words(P["src"], f)
        a, b = float(s["in"]), float(s["out"])
        ws = [[round(float(w[0]), 2), round(float(w[1]), 2), str(w[2]).strip()] for w in cache[f]
              if float(w[1]) > a + 0.02 and float(w[0]) < b - 0.02]
        if spk:
            for w in ws:
                w.append(spk(f, (w[0] + w[1]) / 2) or "")
        out.append({"seg": i, "kind": "speech", "file": f, "in": a, "out": b, "words": ws})
    return out


# ====================================================================== B4: YouTube chapters from the dialogue
CH_MIN = 10.0                  # YouTube: first chapter at 0:00, ≥ 3 chapters, each ≥ 10 s


def sentences(P, gap=0.7, max_words=28):
    """Spoken sentences on the EDITED timeline → [{"t", "end", "text"}] (timeline seconds). A sentence ends at
    . ? ! …, at a pause ≥ gap s, or after max_words; a cut between two shots also ends it."""
    tl = timeline(P)
    out = []
    for blk in transcript(P):
        if blk["kind"] != "speech" or not blk["words"]:
            continue
        i = blk["seg"]
        sp = float(P["segments"][i].get("speed") or 100) / 100.0
        t0, a = tl[i][0], blk["in"]
        to_tl = (lambda x: round(t0 + max(0.0, x - a) / sp, 2))
        cur = []
        for w in blk["words"] + [None]:
            if cur and (w is None or w[0] - cur[-1][1] >= gap or len(cur) >= max_words or re.search(r"[.?!…]$", cur[-1][2])):
                out.append({"t": to_tl(cur[0][0]), "end": to_tl(cur[-1][1]), "text": " ".join(x[2] for x in cur), "seg": i})
                cur = []
            if w is not None:
                cur.append(w)
    return sorted(out, key=lambda s: s["t"])


def _tc(t):
    t = int(round(max(0.0, t)))
    h, m, s = t // 3600, t % 3600 // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def chapters_ctx(P, block_s=12.0, max_chars=14000, speakers=False):
    """What the LLM reads: the dialogue merged into ≥ block_s blocks, one "[m:ss] text" line each (the timestamps are
    the only places a chapter may start). A cut to another shot opens a new block too (once the block has ≥ 4 s): in a
    talking edit a new shot is where a new thought most often starts. Long videos: block texts shortened to fit."""
    S = sentences(P)
    tl = timeline(P)
    total = tl[-1][1] if tl else 0.0
    # speakers=False by default: with "S2:/S4:" tags the LLM chapters by SPEAKER, not topic — 3.0/8 topic changes
    # found vs 4.5/8 without (6 runs each, showroom test where presenter and staff alternate)
    who = None
    if speakers:                                       # C3 speaker of each block (first word's), when diarized
        tr = {blk["seg"]: blk for blk in transcript(P) if blk["kind"] == "speech"}
        tl_ = timeline(P)

        def who(seg, t):
            blk = tr.get(seg)
            if not blk:
                return ""
            sp = float(P["segments"][seg].get("speed") or 100) / 100.0
            src_t = blk["in"] + (t - tl_[seg][0]) * sp
            w = min(blk["words"], key=lambda w: abs(w[0] - src_t))
            return w[3] if len(w) > 3 else ""
    blocks = []
    for s in S:
        age = s["t"] - blocks[-1]["t"] if blocks else 1e9
        if blocks and age < block_s and not (s["seg"] != blocks[-1]["seg"] and age >= 4.0):
            blocks[-1]["text"] += " " + s["text"]
            blocks[-1]["seg"] = s["seg"]
        else:
            blocks.append({"t": s["t"], "text": s["text"], "seg": s["seg"], "spk": who(s["seg"], s["t"]) if who else ""})
    if not blocks:
        return {"text": "", "dur": round(total, 1), "n": 0}
    per = max(60, int(max_chars / len(blocks)) - 10)
    lines = [f"[{_tc(b['t'])}] " + (f"{b['spk']}: " if b.get("spk") else "") +
             (b["text"] if len(b["text"]) <= per else b["text"][:per].rsplit(" ", 1)[0] + "…") for b in blocks]
    return {"text": "\n".join(lines), "dur": round(total, 1), "n": len(blocks), "dur_tc": _tc(total)}


def _parse_tc(v):
    if isinstance(v, (int, float)):
        return float(v)
    p = [x for x in re.split(r"[:.]", str(v).strip("[] ")) if x.strip().isdigit()]
    if not p:
        return None
    n = [int(x) for x in p[-3:]]
    return float(n[0] * 3600 + n[1] * 60 + n[2]) if len(n) == 3 else float(n[0] * 60 + n[1]) if len(n) == 2 else float(n[0])


def fix_chapters(P, items):
    """LLM chapters → YouTube-valid list: snapped to the nearest sentence start (≤ 8 s away), first at 0:00, each ≥
    CH_MIN s, none in the last CH_MIN s. → {"chapters": [{"t", "tc", "title"}], "text", "ok", "warn"}."""
    S = sentences(P)
    tl = timeline(P)
    total = tl[-1][1] if tl else 0.0
    starts = np.array([s["t"] for s in S]) if S else np.zeros(0)
    got = []
    for it in items or []:
        t = _parse_tc(it.get("t") if isinstance(it, dict) else None)
        title = re.sub(r"\s+", " ", str((it or {}).get("title") or "")).strip(" -–:·\"'")[:60]
        if t is None or not title:
            continue
        if len(starts):
            j = int(np.argmin(np.abs(starts - t)))
            if abs(starts[j] - t) <= 8.0:
                t = float(starts[j])
        got.append([max(0.0, t), title])
    got.sort(key=lambda x: x[0])
    if got:
        got[0][0] = 0.0                                 # YouTube: the list must start at 0:00
    out = []
    for t, title in got:
        if out and t - out[-1][0] < CH_MIN:
            continue
        if out and total - t < CH_MIN:
            break
        out.append([t, title])
    warn = ""
    if len(out) < 3:
        warn = "YouTube cần ít nhất 3 chương, mỗi chương ≥ 10 giây" + (" — video quá ngắn" if total < 3 * CH_MIN else "")
    ch = [{"t": round(t, 2), "tc": _tc(t), "title": title} for t, title in out]
    return {"chapters": ch, "text": "\n".join(f"{c['tc']} {c['title']}" for c in ch), "ok": not warn, "warn": warn,
            "dur": round(total, 1)}


def _norm_word(w):
    return re.sub(r"[^\w]", "", str(w).lower())


def _frames_voicing(x, sr=16000, win=0.03, hop=0.01):
    """Per 10 ms frame: RMS and periodicity (max normalised autocorrelation over 80–400 Hz pitch lags)."""
    n, h = int(win * sr), int(hop * sr)
    if len(x) < n:
        return np.zeros(0), np.zeros(0)
    idx = np.arange(0, len(x) - n, h)
    F = np.stack([x[i:i + n] for i in idx]).astype(np.float32)
    F -= F.mean(axis=1, keepdims=True)
    rms = np.sqrt((F ** 2).mean(axis=1))
    lo, hi = int(sr / 400), int(sr / 80)
    spec = np.fft.rfft(F, n=2 * n, axis=1)
    ac = np.fft.irfft(np.abs(spec) ** 2, axis=1)[:, :n]                 # autocorrelation per frame
    per = ac[:, lo:hi].max(axis=1) / np.maximum(ac[:, 0], 1e-9)
    return rms, per


def missed_vocalizations(audio, words, a, b, sr=16000, min_run=0.10, min_pause=0.30, edge=0.12):
    """Voiced sound inside the PAUSES between aligned words that the transcript does not contain — Whisper is trained
    to drop fillers ("ừ", "ờ", "à") and false starts, so they leave a voiced pause. Only real pauses count
    (≥ min_pause between two words, `edge` s trimmed at both ends: CTC word spans are narrower than the spoken word,
    the trimmed tails still belong to it). A run counts when ≥ min_run s of consecutive frames are loud enough vs.
    this shot's speech AND periodic (fillers are voiced; breaths / wind are not).
    Measured (TTS fillers over real outdoor noise, −20 dB): thresholds 0.30·speech / 0.45 periodicity found 1/4 —
    the fillers sat at 0.24–0.34 of the speech level, periodicity 0.43–0.54.  → [(t0, t1)] in source seconds."""
    x = audio[int(max(0.0, a) * sr):int(b * sr)]
    rms, per = _frames_voicing(x, sr)
    if not len(rms):
        return []
    t = a + np.arange(len(rms)) * 0.01 + 0.015
    inw = np.zeros(len(t), bool)
    for w in words:
        inw |= (t >= float(w[0])) & (t <= float(w[1]))
    if inw.sum() < 10:
        return []
    ws = sorted((float(w[0]), float(w[1])) for w in words)
    pause = np.zeros(len(t), bool)
    for (_, e0), (s1, _) in zip(ws, ws[1:]):
        if s1 - e0 >= min_pause:
            pause |= (t >= e0 + edge) & (t <= s1 - edge)
    speech_lvl = float(np.median(rms[inw]))
    floor = float(np.percentile(rms, 10))
    voiced = (rms > max(3.0 * floor, 0.15 * speech_lvl)) & (per > 0.35) & pause
    out, i, n = [], 0, len(voiced)
    while i < n:
        if voiced[i]:
            j = i
            while j < n and voiced[j]:
                j += 1
            if (j - i) * 0.01 >= min_run:
                out.append((round(float(t[i]) - 0.03, 2), round(float(t[j - 1]) + 0.03, 2)))
            i = j
        else:
            i += 1
    return out


def fillers(P, pause_s=0.7):
    """Filler words, stutters (word repeated right away) and long pauses inside spoken shots → cut_words ops."""
    ops = []
    silence_on = bool((P.get("silence") or {}).get("on"))
    for blk in transcript(P):
        if blk["kind"] != "speech":
            continue
        ws = blk["words"]
        for k, w in enumerate(ws):
            nw = _norm_word(w[2])
            prev_gap = w[0] - ws[k - 1][1] if k else 1.0
            next_gap = ws[k + 1][0] - w[1] if k + 1 < len(ws) else 1.0
            if nw in FILLERS and (prev_gap > 0.12 or next_gap > 0.12 or len(ws) < 3):
                ops.append({"kind": "cut_words", "seg": blk["seg"], "t0": w[0], "t1": w[1], "text": w[2],
                            "why": f"từ đệm “{w[2]}”"})
            elif k + 1 < len(ws) and nw and nw == _norm_word(ws[k + 1][2]) and ws[k + 1][0] - w[1] < 0.45 and len(nw) > 1:
                ops.append({"kind": "cut_words", "seg": blk["seg"], "t0": w[0], "t1": ws[k + 1][0], "text": w[2],
                            "why": f"lặp từ “{w[2]} {ws[k + 1][2]}”"})
            if not silence_on and k + 1 < len(ws) and next_gap >= pause_s:
                ops.append({"kind": "cut_words", "seg": blk["seg"], "t0": round(w[1] + 0.12, 2), "t1": round(ws[k + 1][0] - 0.12, 2),
                            "text": f"(im lặng {next_gap:.1f}s)", "why": "khoảng lặng dài"})
        # A4: fillers Whisper left out of the transcript (voiced sound between two aligned words)
        if len(ws) >= 2:
            try:
                audio = _clip_audio(P["src"], blk["file"])
            except Exception:
                audio = None
            if audio is not None:
                taken = [(o["t0"], o["t1"]) for o in ops if o["seg"] == blk["seg"]]
                for v0, v1 in missed_vocalizations(audio, ws, blk["in"], blk["out"]):
                    if any(v0 < b and v1 > a for a, b in taken):
                        continue
                    ops.append({"kind": "cut_words", "seg": blk["seg"], "t0": v0, "t1": v1, "text": "(tiếng ừ/ờ không có chữ)",
                                "why": "tiếng đệm Whisper bỏ sót (có giọng nói giữa hai từ)"})
    return ops


_AUDIO = {}


def _clip_audio(src, f):
    if (src, f) not in _AUDIO:
        from .vad import pcm
        if len(_AUDIO) > 8:                                     # bounded: one talking-head folder at a time
            _AUDIO.clear()
        _AUDIO[(src, f)] = pcm(os.path.join(src, f))
    return _AUDIO[(src, f)]


# ====================================================================== post-production (Premiere)
def _pieces(P):
    p = os.path.join(P["out"], f"{P['name']}.pieces.json")
    d = jsonio.load(p, None)
    if not d:
        raise SystemExit(f"chưa có {p} — Build bản dựng trước")
    return d


def post(P, want=("reframe", "color", "stabilize")):
    """Per V1 piece of the built sequence: reframe keyframes, Lumetri corrections, stabilise → host.jsx jesApplyPost."""
    import cv2
    from .ai import frames as _fr
    from . import stability as _st
    d = _pieces(P)
    src = P["src"]
    media = jsonio.load(os.path.join(src, "_nexys_work", "media.json"), {}) or {}
    SW, SH = float(P["seq"]["w"]), float(P["seq"]["h"])
    seq_ar = SW / SH
    items, stats = [], []
    shake = jsonio.load(os.path.join(src, "_nexys_work", "shake.json"), {}) or {}
    for p in d.get("pieces", []):
        if p.get("track", "V1") != "V1" or not p.get("file") or p["file"] not in media:
            continue
        m = media[p["file"]]
        path = os.path.join(src, p["file"])
        a, b = float(p["in"]), float(p["out"])
        it = {"tl": float(p["tl_start"]), "file": p["file"], "in": a, "out": b}
        src_ar = float(m.get("w") or 16) / float(m.get("h") or 9)
        n = max(2, min(10, int((b - a) / 0.5) + 1))
        ts = list(np.linspace(a + 0.05, max(a + 0.1, b - 0.05), n))
        need_frames = ("reframe" in want and abs(src_ar - seq_ar) > 0.15 and (P["seq"].get("fit") or "fill") == "fill") or "color" in want
        frs = _fr.full(path, ts, 320) if need_frames else []
        if "reframe" in want and abs(src_ar - seq_ar) > 0.15 and (P["seq"].get("fit") or "fill") == "fill":
            xs, ys = [], []
            for im in frs:
                if im is None:
                    continue
                g = cv2.cvtColor(im, cv2.COLOR_RGB2GRAY)
                faces = _st._faces(g)
                h_, w_ = g.shape
                if faces:
                    x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
                    xs.append((x + fw / 2) / w_); ys.append((y + fh * 0.6) / h_)
                else:
                    cx, cy = _st._saliency_centroid(g)
                    xs.append(cx); ys.append(cy)
            if xs:
                xs = np.convolve(np.pad(xs, 2, mode="edge"), np.ones(5) / 5, "valid")      # calm the virtual camera
                ys = np.convolve(np.pad(ys, 2, mode="edge"), np.ones(5) / 5, "valid")
                vmax = 0.25 * (ts[1] - ts[0]) if len(ts) > 1 else 0.0                    # ≤ 25 % of the frame per second
                for k_ in range(1, len(xs)):
                    xs[k_] = xs[k_ - 1] + max(-vmax, min(vmax, xs[k_] - xs[k_ - 1]))
                    ys[k_] = ys[k_ - 1] + max(-vmax, min(vmax, ys[k_] - ys[k_ - 1]))
                # clip scaled to fill the sequence: its size in sequence pixels
                sc = max(SW / float(m["w"]), SH / float(m["h"]))
                cw, ch_ = float(m["w"]) * sc / SW, float(m["h"]) * sc / SH                  # clip size / sequence size
                kf = []
                for t_, u, v in zip(ts, xs, ys):
                    px = 0.5 + (0.5 - float(u)) * cw                                          # put source point u at the centre
                    py = 0.5 + (0.5 - float(v)) * ch_
                    lim_x, lim_y = max(0.0, (cw - 1) / 2), max(0.0, (ch_ - 1) / 2)          # never show past the clip edge
                    px = min(0.5 + lim_x, max(0.5 - lim_x, px))
                    py = min(0.5 + lim_y, max(0.5 - lim_y, py))
                    kf.append([round(float(t_), 3), round(px, 4), round(py, 4)])
                if max(k[1] for k in kf) - min(k[1] for k in kf) < 0.01:
                    kf = [kf[0]]                                                             # static framing
                it["reframe"] = kf
        if "color" in want:
            mid = [im for im in frs if im is not None]
            if mid:
                im = mid[len(mid) // 2]
                lab = cv2.cvtColor(im, cv2.COLOR_RGB2LAB).astype(np.float32)
                hsv = cv2.cvtColor(im, cv2.COLOR_RGB2HSV).astype(np.float32)
                stats.append((len(items), float(lab[..., 0].mean()), float(lab[..., 1].mean()) - 128, float(lab[..., 2].mean()) - 128,
                              float(hsv[..., 1].mean())))
        if "stabilize" in want:
            st = None
            sh = shake.get(p["file"]) or {}
            bad = [r for r in sh.get("ranges", []) if r[1] > a and r[0] < b]
            if bad:
                over = sum(min(r[1], b) - max(r[0], a) for r in bad) / max(0.1, b - a)
                st = over >= 0.15
            if st:
                it["stabilize"] = True
        items.append(it)
    if stats:
        L_ref = float(np.median([s[1] for s in stats])); a_ref = float(np.median([s[2] for s in stats]))
        b_ref = float(np.median([s[3] for s in stats])); S_ref = float(np.median([s[4] for s in stats]))
        # Match only MODERATE drifts between clips (white balance / exposure of different cameras or moments).
        # A big difference is the content itself — a sunset is meant to be warm, a night street dark — and is kept.
        K = 0.6                                                     # apply 60 % of the measured correction
        for k, L, a_, b_, S in stats:
            fix = {}
            ev = math.log2(max(L_ref, 1) / max(L, 1)) * 1.6         # L* ratio → stops (approx.)
            if 30 <= L <= 85 and 0.2 <= abs(ev) <= 1.2:
                fix["Exposure"] = round(ev * K, 2)
            db, da = b_ref - b_, a_ref - a_
            if 3 <= abs(db) <= 12:
                fix["Temperature"] = round(max(-15, min(15, db * 2.0 * K)), 1)
            if 3 <= abs(da) <= 10:
                fix["Tint"] = round(max(-10, min(10, da * 2.0 * K)), 1)
            ratio = S_ref / max(S, 1)
            if 0.8 <= ratio <= 1.25 and abs(ratio - 1) >= 0.08:
                fix["Saturation"] = round(100 * (1 + (ratio - 1) * K), 0)
            if fix:
                items[k]["color"] = fix
    return [it for it in items if any(k in it for k in ("reframe", "color", "stabilize"))]


# ====================================================================== CLI
def main():
    for s_ in (sys.stdout, sys.stderr):
        try:
            s_.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("review", "context", "resolve", "apply", "transcript", "fillers", "post",
                                    "chapters_ctx", "chapters", "thumbs", "speakers"))
    ap.add_argument("plan")
    ap.add_argument("--ops", default="", help="JSON file: ops (resolve) or changes (apply)")
    ap.add_argument("--thumbs", action="store_true")
    ap.add_argument("--want", default="reframe,color,stabilize")
    a = ap.parse_args()
    ops = None
    if a.ops:
        ops = jsonio.load(a.ops, []) or []
    res = run_cmd(a.cmd, a.plan, ops, a.thumbs, a.want, log=lambda m: print(m, flush=True))
    print("AIEDIT " + json.dumps(res, ensure_ascii=False), flush=True)
    return 0


def run_cmd(cmd, plan, ops=None, with_thumbs=False, want="reframe,color,stabilize", log=print):
    """One AI-edit command → result dict. Shared by the CLI (fallback) and the long-lived AI worker
    (nxcore.ai.worker), which keeps the source folder's Material between commands."""
    P = load_plan(plan)
    if isinstance(ops, dict):
        ops = ops.get("ops") or ops.get("changes") or []
    ops = ops or []
    if cmd == "review":
        iss = review(P)
        fixes = [dict(x["fix"], why=x["msg"], issue=x["type"], sev=x["sev"]) for x in iss if x.get("fix")]
        ch = resolve(P, fixes)
        if with_thumbs:
            thumbs(P, ch)
        log(f"aiedit: {len(iss)} vấn đề · {sum(1 for c in ch if c['ok'])} cách sửa")
        return {"issues": iss, "changes": ch}
    if cmd == "context":
        head, text = context(P)
        return {"head": head, "text": text}
    if cmd == "resolve":
        ch = resolve(P, ops)
        if with_thumbs:
            thumbs(P, ch)
        return {"changes": ch}
    if cmd == "apply":
        try:                                            # B1: every kept / unticked shot change teaches the taste model
            from . import prefs
            Pf = prefs.load()
            n = prefs.record_taste(Pf, ops, P.get("preset"))
            if n:
                prefs.save(Pf)
                log(f"aiedit: ghi {n} lựa chọn vào gu chọn shot")
        except Exception as e:                          # noqa: BLE001
            log(f"aiedit: không ghi được gu chọn shot ({e})")
        Q, path, replan = apply(P, ops, plan)
        if replan:
            # a re-cut renumbers every shot → it runs alone (other changes of the same set would target stale indices)
            path = do_replan(P, replan["cfg"], Q["name"], path)
            res = {"plan": path.replace("\\", "/"), "name": Q["name"], "replan": replan["cfg"],
                   "note": "đã dựng lại theo thông số mới — các thay đổi khác trong cùng lượt bị bỏ qua"}
        else:
            jsonio.save_atomic(path, Q, indent=1)
            res = {"plan": path.replace("\\", "/"), "name": Q["name"], "replan": None}
        log(f"aiedit: đã lưu {path}")
        return res
    if cmd == "transcript":
        return {"blocks": transcript(P)}
    if cmd == "fillers":
        return {"changes": resolve(P, fillers(P))}
    if cmd == "post":
        return {"items": post(P, tuple(x for x in str(want).split(",") if x))}
    if cmd == "chapters_ctx":
        return chapters_ctx(P)
    if cmd == "chapters":
        return fix_chapters(P, ops)
    if cmd == "speakers":                               # C3: diarize the source folder, then the transcript with speakers
        from .ai import speakers as SPK
        sp = SPK.diarize(P["src"], progress=log)
        return {"speakers": sp, "blocks": transcript(P)}
    if cmd == "thumbs":                                 # B5: thumbnail candidates, saved next to the plan
        from .ai import thumbpick
        out = os.path.join(os.path.dirname(os.path.abspath(plan)), f"thumbs_{P.get('name') or 'edit'}")
        return {"thumbs": thumbpick.pick(P, 8, out, log=log), "dir": out.replace("\\", "/")}
    raise ValueError(f"lệnh aiedit lạ: {cmd}")


if __name__ == "__main__":
    sys.exit(main())
