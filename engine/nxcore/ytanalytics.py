"""C1 — learn from the channel's REAL audience retention (YouTube Analytics API).

  login            OAuth for an installed app (PKCE + loopback 127.0.0.1): the editor signs in to Google in their own
                   browser; only READ-ONLY scopes; the token stays in %APPDATA%/JustinEditSuite/youtube_token.json.
                   Needs an OAuth client of type "Desktop app" from the editor's own Google Cloud project
                   (APIs: YouTube Data API v3 + YouTube Analytics API) saved as …/JustinEditSuite/google_client.json.
  videos           the channel's uploads (id, title, duration, published)
  suggest PLAN     uploads whose duration matches the edit (± 2 s) — which video is this edit?
  analyze PLAN ID  retention curve → moments where viewers left MORE than the curve's own trend, and rewatched
                   moments, each mapped to the shot / words / kind of segment on the edit's timeline
  lessons          across every linked video: what is on screen where people leave vs. on average

API facts (YouTube Analytics v2, reports.query): dimension elapsedVideoTimeRatio (0.01 … 1.00, 100 points),
metrics audienceWatchRatio (share of viewers still watching, > 1 = rewatching) and relativeRetentionPerformance
(0 … 1 vs. videos of similar length; 0.5 = typical), filter video==ID;audienceType==ORGANIC. One video per query.
Data arrives ~2–3 days after publishing and only for videos with enough views.

  python -m nxcore.ytanalytics login|videos|suggest PLAN|analyze PLAN VIDEO_ID|lessons
"""
import base64
import hashlib
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np

from . import jsonio

SCOPES = ["https://www.googleapis.com/auth/yt-analytics.readonly", "https://www.googleapis.com/auth/youtube.readonly"]
DATA = "https://www.googleapis.com/youtube/v3"
ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"


def home():
    # the panel's NX.cfgDir: APPDATA (Windows) else HOME — the panel writes google_client.json there
    base = os.environ.get("APPDATA") or os.environ.get("HOME") or os.path.expanduser("~")
    d = os.path.join(base, "JustinEditSuite")
    os.makedirs(d, exist_ok=True)
    return d


def client_path():
    return os.path.join(home(), "google_client.json")


def token_path():
    return os.path.join(home(), "youtube_token.json")


def links_path():
    return os.path.join(home(), "youtube_links.json")


def client():
    c = jsonio.load(client_path(), None)
    if not c:
        raise RuntimeError("chưa có OAuth client — tạo loại 'Desktop app' trong Google Cloud Console rồi chọn file .json")
    c = c.get("installed") or c.get("web") or c
    if not c.get("client_id"):
        raise RuntimeError("file OAuth client không hợp lệ (thiếu client_id)")
    return c


# ---------------------------------------------------------------- OAuth (installed app, PKCE, loopback)
def _post_form(url, data):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), headers={"content-type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def auth_url(c, redirect, challenge, state):
    q = {"client_id": c["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": " ".join(SCOPES),
         "code_challenge": challenge, "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent",
         "state": state}
    return (c.get("auth_uri") or "https://accounts.google.com/o/oauth2/auth") + "?" + urllib.parse.urlencode(q)


def login(open_browser=True, timeout=300, log=print):
    """Browser sign-in → refresh token saved. The code arrives on http://127.0.0.1:<free port>/ (never typed in)."""
    import http.server
    import socket
    import threading
    import webbrowser
    c = client()
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    redirect = f"http://127.0.0.1:{port}/"
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] == state:
                got["code"], got["error"] = q.get("code", [""])[0], q.get("error", [""])[0]
            ok = bool(got.get("code"))
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(("<h3>Justin EDIT Suite</h3><p>" + ("Đã kết nối kênh — quay lại Premiere." if ok else
                              "Không cấp quyền — đóng tab này.") + "</p>").encode("utf-8"))

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    url = auth_url(c, redirect, challenge, state)
    log("youtube: mở trình duyệt để đăng nhập Google (chỉ quyền ĐỌC thống kê kênh)…")
    if open_browser:
        webbrowser.open(url)
    else:
        log(url)
    t0 = time.time()
    while "code" not in got and time.time() - t0 < timeout:
        time.sleep(0.3)
    srv.shutdown()
    if not got.get("code"):
        raise RuntimeError("chưa cấp quyền" + (f" ({got.get('error')})" if got.get("error") else " — hết thời gian chờ"))
    tok = _post_form(c.get("token_uri") or "https://oauth2.googleapis.com/token",
                     {"code": got["code"], "client_id": c["client_id"], "client_secret": c.get("client_secret", ""),
                      "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
    tok["expires_at"] = time.time() + float(tok.get("expires_in", 3600)) - 60
    jsonio.save_atomic(token_path(), tok)
    ch = channel()
    log(f"youtube: đã kết nối kênh {ch.get('title')}")
    return ch


def access_token():
    tok = jsonio.load(token_path(), None)
    if not tok:
        raise RuntimeError("chưa kết nối kênh YouTube (bấm Kết nối kênh)")
    if time.time() < float(tok.get("expires_at", 0)):
        return tok["access_token"]
    c = client()
    new = _post_form(c.get("token_uri") or "https://oauth2.googleapis.com/token",
                     {"client_id": c["client_id"], "client_secret": c.get("client_secret", ""),
                      "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})
    tok.update(access_token=new["access_token"], expires_at=time.time() + float(new.get("expires_in", 3600)) - 60)
    jsonio.save_atomic(token_path(), tok)
    return tok["access_token"]


def get(url, params, fetch=None):
    """GET with the bearer token. fetch(url) → dict replaces the network (tests)."""
    full = url + "?" + urllib.parse.urlencode(params)
    if fetch:
        return fetch(full)
    req = urllib.request.Request(full, headers={"authorization": "Bearer " + access_token()})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        msg = (json.loads(body).get("error") or {}).get("message", body[:200]) if body.startswith("{") else body[:200]
        raise RuntimeError(f"YouTube API {e.code}: {msg}")


# ---------------------------------------------------------------- channel data
def channel(fetch=None):
    d = get(DATA + "/channels", {"part": "snippet,contentDetails", "mine": "true"}, fetch)
    it = (d.get("items") or [{}])[0]
    return {"id": it.get("id"), "title": (it.get("snippet") or {}).get("title"),
            "uploads": ((it.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")}


def iso_dur(s):
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0.0
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return float(d * 86400 + h * 3600 + mi * 60 + se)


def videos(n=50, fetch=None):
    ch = channel(fetch)
    ids, page = [], None
    while ch.get("uploads") and len(ids) < n:
        p = {"part": "contentDetails", "playlistId": ch["uploads"], "maxResults": 50}
        if page:
            p["pageToken"] = page
        d = get(DATA + "/playlistItems", p, fetch)
        ids += [(it.get("contentDetails") or {}).get("videoId") for it in d.get("items") or []]
        page = d.get("nextPageToken")
        if not page:
            break
    out = []
    for i in range(0, len(ids[:n]), 50):
        d = get(DATA + "/videos", {"part": "snippet,contentDetails", "id": ",".join(ids[i:i + 50])}, fetch)
        for it in d.get("items") or []:
            out.append({"id": it["id"], "title": it["snippet"]["title"], "published": it["snippet"]["publishedAt"][:10],
                        "dur": iso_dur(it["contentDetails"]["duration"])})
    return out


def retention(vid, published="2005-01-01", fetch=None):
    """→ (ratio[100], audienceWatchRatio[100], relativeRetentionPerformance[100])"""
    d = get(ANALYTICS, {"ids": "channel==MINE", "startDate": published[:10], "endDate": time.strftime("%Y-%m-%d"),
                        "metrics": "audienceWatchRatio,relativeRetentionPerformance", "dimensions": "elapsedVideoTimeRatio",
                        "filters": f"video=={vid};audienceType==ORGANIC", "sort": "elapsedVideoTimeRatio"}, fetch)
    rows = d.get("rows") or []
    if len(rows) < 10:
        raise RuntimeError("YouTube chưa có số liệu retention cho video này (mới đăng < 3 ngày hoặc quá ít lượt xem)")
    R = np.array(rows, float)
    return R[:, 0], R[:, 1], R[:, 2]


# ---------------------------------------------------------------- linking edits ↔ videos
def plan_total(P):
    from .aiedit import timeline
    tl = timeline(P)
    return tl[-1][1] if tl else 0.0


def suggest(P, vids, tol=2.0):
    """Uploads whose duration matches the edit — the exported sequence IS the video (± tol s for encoders / trims)."""
    T = plan_total(P)
    return sorted([dict(v, diff=round(abs(v["dur"] - T), 1)) for v in vids if abs(v["dur"] - T) <= tol], key=lambda v: v["diff"])


def link(plan_path, vid):
    L = jsonio.load(links_path(), {}) or {}
    L[os.path.abspath(plan_path)] = vid
    jsonio.save_atomic(links_path(), L)


# ---------------------------------------------------------------- analysis
def _segment_at(P, tl, t):
    for i, (a, b) in enumerate(tl):
        if a <= t < b:
            return i
    return len(tl) - 1 if tl else None


def describe_moment(P, t, tl=None, words=None):
    """What the viewer saw / heard at edit time t → {"seg", "kind", "label", "text", "shot_len"}."""
    from .aiedit import timeline, transcript
    tl = tl or timeline(P)
    i = _segment_at(P, tl, t)
    if i is None:
        return {}
    s = P["segments"][i]
    kind = "gap" if s.get("gap") else "thoại" if s.get("audio") else "b-roll"
    txt = ""
    blocks = words if words is not None else {b["seg"]: b for b in transcript(P) if b["kind"] == "speech"}
    if i in blocks:
        b = blocks[i]
        sp = float(s.get("speed") or 100) / 100.0
        src_t = float(s["in"]) + (t - tl[i][0]) * sp
        near = [w[2] for w in b["words"] if src_t - 4 <= w[0] <= src_t + 2]
        txt = " ".join(near)[:120]
    return {"seg": i, "kind": kind, "label": (s.get("label") or s.get("file") or "")[:60], "text": txt,
            "shot_len": round(tl[i][1] - tl[i][0], 1), "t_in_shot": round(t - tl[i][0], 1)}


def moments(ratio, watch, rel, dur, k=3.0, min_gap=0.03, size=9, min_lost=0.015):
    """Tuned on 60 random synthetic curves (scratch c1_tune): leaves found 99 % with 0.05 false per video, rewatches
    92 % — min_lost (≥ 1.5 % of viewers beyond the trend) is what removed the false leaves (1.2–3.8 per video
    without it); smaller dips are not worth an editor's attention."""
    """Leaves = steps where the watch ratio falls MORE than its own smooth trend (robust z ≥ k), rewatches = rises.
    The first 3 % (start-up drop every video has) and last 3 % (end screen) are not judged."""
    from scipy.ndimage import median_filter
    w = np.asarray(watch, float)
    n = len(w)
    # A median filter keeps STEPS (people leaving for good) and removes narrow BUMPS (a moment watched again):
    #   leaves   = steps of the filtered curve beyond its own smooth decay (robust z ≥ k, local maxima)
    #   rewatch  = the raw curve standing above the filtered one
    # (judging raw 1 % steps, the fall after a rewatched bump read as people leaving on the synthetic test)
    m = median_filter(w, size=size, mode="nearest")
    dm = -np.diff(m)                                       # level lost between point j and j+1
    res = dm - median_filter(dm, size=11, mode="nearest")
    mad = np.median(np.abs(res - np.median(res))) * 1.4826 + 1e-6
    z = res / mad
    bump = w - m
    mad_b = np.median(np.abs(bump - np.median(bump))) * 1.4826 + 1e-6
    out = []
    for j in range(1, n - 1):
        r = float(ratio[j])
        if r < 0.05 or r > 0.97:                           # every video loses people in its first seconds; end screen
            continue
        if j < n - 2 and z[j] >= k and res[j] >= min_lost and z[j] >= z[j - 1] and z[j] >= z[j + 1]:
            out.append({"type": "leave", "t": round(float(ratio[j + 1]) * dur, 1), "ratio": float(ratio[j + 1]),
                        "lost": round(float(dm[j]), 4), "z": round(float(z[j]), 1)})
        if bump[j] >= max(0.006, 4 * mad_b) and bump[j] >= bump[j - 1] and bump[j] >= bump[j + 1]:
            out.append({"type": "rewatch", "t": round(r * dur, 1), "ratio": r, "gain": round(float(bump[j]), 4)})
    merged = []                                            # one event per neighbourhood (strongest)
    for e in sorted(out, key=lambda e: -(e.get("lost") or e.get("gain"))):
        if all(abs(e["ratio"] - m["ratio"]) >= min_gap or e["type"] != m["type"] for m in merged):
            merged.append(e)
    return sorted(merged, key=lambda e: e["t"])


def analyze(P, vid, published="2005-01-01", dur=None, fetch=None, curve=None):
    """→ {"curve": [[t, watch, rel]…], "events": [...], "summary": {...}}  (curve = (ratio, watch, rel) for tests)"""
    from .aiedit import timeline, transcript
    ratio, watch, rel = curve if curve is not None else retention(vid, published, fetch)
    T = float(dur or plan_total(P))
    tl = timeline(P)
    blocks = {b["seg"]: b for b in transcript(P) if b["kind"] == "speech"}
    ev = moments(ratio, watch, rel, T)
    for e in ev:
        e.update(describe_moment(P, e["t"], tl, blocks))
    at = (lambda sec: float(np.interp(sec / T, ratio, watch)))
    summ = {"dur": round(T, 1), "at30s": round(at(30), 3) if T > 40 else None, "mid": round(at(T / 2), 3),
            "end": round(float(watch[-1]), 3), "rel_mean": round(float(np.mean(rel)), 3),
            "rel_30s": round(float(np.interp(30 / T, ratio, rel)), 3) if T > 40 else None}
    return {"video": vid, "curve": [[round(float(r) * T, 1), round(float(w), 4), round(float(x), 3)] for r, w, x in zip(ratio, watch, rel)],
            "events": ev, "summary": summ}


def segment_features(P, i, tl):
    s = P["segments"][i]
    L = tl[i][1] - tl[i][0]
    return {"broll": not s.get("audio") and not s.get("gap"), "talk": bool(s.get("audio")), "long": L >= 6.0,
            "slow": float(s.get("speed") or 100) != 100, "intro": tl[i][0] < 30.0}


FEAT_TEXT = {"broll": "đoạn b-roll (không lời)", "talk": "đoạn nói", "long": "shot dài ≥ 6 s", "slow": "slow-motion",
             "intro": "30 giây đầu"}


def lessons(results):
    """Across analysed videos: how much more often a kind of segment holds a 'leave' moment than its share of the
    timeline predicts (lift). results = [(P, analysis)] → [{"feat", "text", "lift", "leaves", "videos"}]"""
    from .aiedit import timeline
    tot = {k: 0.0 for k in FEAT_TEXT}
    hit = {k: 0 for k in FEAT_TEXT}
    vids = {k: set() for k in FEAT_TEXT}
    n_leave, T_all = 0, 0.0
    for P, A in results:
        tl = timeline(P)
        T_all += tl[-1][1] if tl else 0.0
        for i in range(len(tl)):
            for k, v in segment_features(P, i, tl).items():
                if v:
                    tot[k] += tl[i][1] - tl[i][0]
        for e in A["events"]:
            if e["type"] != "leave" or e.get("seg") is None:
                continue
            n_leave += 1
            for k, v in segment_features(P, e["seg"], tl).items():
                if v:
                    hit[k] += 1
                    vids[k].add(A["video"])
    out = []
    for k in FEAT_TEXT:
        if not tot[k] or not n_leave:
            continue
        share_t = tot[k] / T_all                            # share of watch time that is this kind
        share_e = hit[k] / n_leave                          # share of leave moments that land on it
        out.append({"feat": k, "text": FEAT_TEXT[k], "lift": round(share_e / max(share_t, 1e-6), 2), "leaves": hit[k],
                    "videos": len(vids[k]), "share_time": round(share_t, 2)})
    return sorted(out, key=lambda r: -r["lift"])


def stored_results():
    """[(plan, analysis)] for every linked edit analysed before (no network)."""
    L = jsonio.load(links_path(), {}) or {}
    out = []
    for pp in L:
        rp = os.path.splitext(pp)[0] + ".retention.json"
        if os.path.exists(pp) and os.path.exists(rp):
            out.append((jsonio.load(pp, {}), jsonio.load(rp, {})))
    return out


def learned_risks(min_lift=1.5, min_leaves=3, min_videos=2):
    """{feature: lift} the channel's audience leaves on clearly more than elsewhere — used by the AI review."""
    try:
        res = stored_results()
        if len(res) < min_videos:
            return {}
        return {x["feat"]: x["lift"] for x in lessons(res)
                if x["lift"] >= min_lift and x["leaves"] >= min_leaves and x["videos"] >= min_videos}
    except Exception:                                      # noqa: BLE001
        return {}


def main():
    import argparse
    for s_ in (sys.stdout, sys.stderr):
        try:
            s_.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Retention YouTube Analytics → bài học cho bản dựng")
    ap.add_argument("cmd", choices=("login", "videos", "suggest", "analyze", "lessons", "status"))
    ap.add_argument("plan", nargs="?", default="")
    ap.add_argument("video", nargs="?", default="")
    a = ap.parse_args()
    log = lambda m: print(m, flush=True)
    try:
        if a.cmd == "status":
            res = {"client": os.path.exists(client_path()), "token": os.path.exists(token_path())}
            if res["token"]:
                res["channel"] = channel()
        elif a.cmd == "login":
            res = login(log=log)
        elif a.cmd == "videos":
            res = videos()
        elif a.cmd == "suggest":
            res = suggest(jsonio.load(a.plan, {}), videos())
        elif a.cmd == "analyze":
            P = jsonio.load(a.plan, {})
            v = next((x for x in videos(200) if x["id"] == a.video), {"published": "2005-01-01", "dur": None})
            res = analyze(P, a.video, v["published"], v.get("dur"))
            link(a.plan, a.video)
            jsonio.save_atomic(os.path.splitext(a.plan)[0] + ".retention.json", res)
        else:
            pairs = stored_results()
            res = {"videos": len(pairs), "lessons": lessons(pairs)}
        print("YT " + json.dumps(res, ensure_ascii=False), flush=True)
        return 0
    except Exception as e:                                 # noqa: BLE001
        print("YT " + json.dumps({"error": str(e)[:300]}, ensure_ascii=False), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
