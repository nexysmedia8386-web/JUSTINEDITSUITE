/* Justin EDIT Suite — Review v1.7
   · compact table + thumbnail of each shot's In frame, grouped by script line (↳ = more cuts of the same line)
   · 1-click shot change: popover with every alternative (other takes / B-roll / other moments of long takes) as thumbnails
   · direct edits on the open sequence (backup once per session): swap / trim / fill a missing card → no Rebuild needed
   · two-way sync: what you trim / delete / add on the timeline flows back into the plan (✎ sửa tay, ⛔ không còn trên timeline) */
(function () {
  "use strict";
  var fs = require("fs"), path = require("path");
  var NX = window.NX, $ = function (id) { return document.getElementById(id); };
  var st = { shots: null, seqName: "", syncedAt: 0, extras: [], backedUp: {}, busy: false, pop: null, data: null, dataAt: 0 };

  function S() { return NX.S; }
  function P() { return NX.getPlan(); }
  function esc(s) { return NX.esc(String(s == null ? "" : s)); }
  function base(f) { return String(f || "").split(/[\\/]/).pop(); }
  function full(f) { return (P().src + "/" + f).replace(/\\/g, "/"); }
  function norm(p) { return String(p || "").replace(/\\/g, "/").toLowerCase(); }
  function r2(x) { return Math.round(Number(x) * 100) / 100; }
  function fmt(x) { return NX.formatTime ? NX.formatTime(x) : (+x).toFixed(1); }
  function host(expr) {
    return NX.evalHost(expr).then(function (r) {
      var j; try { j = JSON.parse(r); } catch (e) { throw new Error(String(r || "host không trả lời").slice(0, 200)); }
      if (j && j.ok === false) throw new Error(j.error || "lỗi host");
      return j;
    });
  }
  function rj(p, d) { try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) { return d; } }

  // ── descriptions of any (file, time) — editor note > AI summary > local vision (segment) > local vision (clip) ──
  function data() {
    var p = P(); if (!p) return {};
    if (st.data && Date.now() - st.dataAt < 3000 && st.data._src === p.src) return st.data;
    var W = path.join(p.src, "_nexys_work");
    st.data = { _src: p.src, ai: (rj(path.join(W, "source_ai.json"), {}) || {}).clips || {}, auto: (rj(path.join(W, "auto_desc.json"), {}) || {}).clips || {},
                segs: (rj(path.join(W, "segments.json"), {}) || {}).clips || {}, tags: rj(path.join(W, "broll_tags.json"), {}) || {},
                media: rj(path.join(W, "media.json"), {}) || {}, shake: rj(path.join(W, "shake.json"), {}) || {} };
    st.dataAt = Date.now();
    return st.data;
  }
  function descAt(f, t) {
    var D = data(), note = (D.tags[f] || {}).desc;
    if (note) return "✏️ " + note;
    var a = D.ai[f] || {};
    if (a.summary) return "✨ " + a.summary;
    var au = D.auto[f] || {}, sg = (D.segs[f] || {}).segs || [];
    for (var i = 0; i < sg.length; i++) {
      var s = sg[i], k = s.k || (s.a.toFixed(1) + "-" + s.b.toFixed(1));
      if (t >= s.a - 0.5 && t <= s.b + 0.5 && au.segs && au.segs[k] && au.segs[k].desc) return "🤖 " + au.segs[k].desc;
    }
    return au.desc ? "🤖 " + au.desc : "";
  }

  // ── timeline ↔ plan ─────────────────────────────────────────────────────────────────────────────────────────
  function seqMatches(name) {
    // exact build name (case-insensitive) — a prefix test would let plan "_v2" claim sequence "_v21"
    var p = P(); if (!p || !p.name || !name) return false;
    var a = norm(name).replace(/\s+/g, " ").trim(), b = norm(p.name).replace(/\s+/g, " ").trim();
    return a === b || (a.indexOf(b) === 0 && /^[\s_-]*(copy|bản sao)?\s*$/.test(a.slice(b.length)));
  }
  /* <out>/<name>.pieces.json (written by Build): every timeline clip of the build → its plan segment.
     A speech segment is SEVERAL clips after the silence cut; the sidecar says which clips belong together and
     where each one started in the source, so a sync never mistakes one piece for the whole segment. */
  var pcCache = { p: "", m: 0, v: null };
  function readPieces(p) {
    var fp = path.join(p.out || NX.outDir(), p.name + ".pieces.json"), s0;
    try { s0 = fs.statSync(fp); } catch (e) { return null; }
    if (pcCache.p === fp && pcCache.m === s0.mtimeMs) return pcCache.v;
    var j = rj(fp, null);
    if (!j || !Array.isArray(j.pieces) || (j.name && norm(j.name) !== norm(p.name))) j = null;
    pcCache = { p: fp, m: s0.mtimeMs, v: j };
    return j;
  }
  function silPad(p, s) {              // how far the build may have moved a clip edge from the words (silence pad)
    var sil = p.silence || {}, cut = !!sil.on && s.audio !== false && s.silence !== false;
    return { cut: cut, a: cut ? Math.max(0.05, +sil.pad_in || 0) : 0.05, b: cut ? Math.max(0.05, +sil.pad_out || 0) : 0.05 };
  }
  var IMG = /\.(png|jpe?g|tiff?|psd|gif|bmp|ai|svg|webp)$/i;
  function isCard(x) { return x.track === 0 && (!x.media || /\/gaps\/[^\/]*_gap\d+\.png$/.test(x.media)); }
  function isToolOutput(x, outs) {    // highlight PNGs, JIZURA text clips, gap cards, titles … — not "added by hand"
    var m = x.media || "";
    if (!m) return true;
    for (var i = 0; i < outs.length; i++) if (outs[i] && m.indexOf(outs[i]) === 0) return true;
    if (/\/(textfx|highlights|gaps|titles)\//.test(m) || /\/jizura_[^\/]*\.mov$/.test(m)) return true;
    return x.track >= 2 && IMG.test(m);                                 // V3+ graphics
  }
  // match the timeline clips to plan segments → { bySeg: {i: [shot]}, used: {k: 1}, orig: {i: [a, b]} }
  function matchShots(p, shots) {
    var pcs = readPieces(p), segP = {}, used = {}, bySeg = {}, orig = {}, nGood = 0, nBad = 0;
    (pcs ? pcs.pieces : []).forEach(function (q) {
      var s = p.segments[q.seg]; if (!s || q.seg == null) { nBad++; return; }
      var gapPiece = !!q.gap || !q.file || /\/gaps\//.test(String(q.file).replace(/\\/g, "/"));
      if (gapPiece ? !s.gap : (s.gap || norm(q.file) !== norm(s.file))) { nBad++; return; }   // plan changed since this build
      (segP[q.seg] = segP[q.seg] || []).push(q); nGood++;
    });
    if (nBad > nGood) segP = {};                                          // sidecar of another plan version → heuristics only
    var add = function (i, k) { used[k] = 1; (bySeg[i] = bySeg[i] || []).push(shots[k]); };
    // 0) cutaways of the plan (V2+ overlays) are claimed first so they never count as a segment's clip
    var ovl = pcs && Array.isArray(pcs.overlays) && pcs.overlays.length ? pcs.overlays.map(function (o) { return { file: o.file, "in": +o["in"], dur: +o.out - +o["in"] }; }) : (p.overlays || []);
    ovl.forEach(function (o) {
      if (!o || !o.file) return;
      var m = norm(full(o.file)), a = +o["in"] || 0, b = a + (+o.dur || 0);
      for (var k = 0; k < shots.length; k++) {
        var x = shots[k];
        if (!used[k] && x.track >= 1 && x.media === m && Math.min(x.out, b + 0.1) - Math.max(x["in"], a - 0.1) > 0) { used[k] = 1; break; }
      }
    });
    // 1) missing-footage cards (V1 PNG from <out>/gaps/ or a text card): in order, same length
    var cards = [], ci = 0;
    shots.forEach(function (x, k) { if (isCard(x)) cards.push(k); });
    p.segments.forEach(function (s, i) {
      if (!s.gap) return;
      var gp = segP[i] && segP[i][0], want = gp ? (+gp.tl_end - +gp.tl_start) : Math.max(0.5, s.out - s["in"]);
      for (var c = ci; c < cards.length; c++) {
        var x = shots[cards[c]];
        if (Math.abs((x.end - x.start) - want) <= 0.15) { add(i, cards[c]); ci = c + 1; break; }
      }
    });
    // 2) media clips → the piece (or segment) whose source range they overlap most
    var targets = [];
    p.segments.forEach(function (s, i) {
      if (s.gap || !s.file) return;
      var media = norm(full(s.file)), pad = silPad(p, s), ps = segP[i];
      if (ps && ps.length) {
        ps.forEach(function (q) {
          var tr = /^V(\d+)$/i.exec(String(q.track || ""));
          targets.push({ seg: i, a: +q["in"], b: +q.out, media: media, t: +q.tl_start, pa: pad.a, pb: pad.b, track: tr ? +tr[1] - 1 : null });
        });
        orig[i] = [Math.min.apply(null, ps.map(function (q) { return +q["in"]; })), Math.max.apply(null, ps.map(function (q) { return +q.out; }))];
        if (ps.some(function (q) { return q.speed != null && Math.abs(+q.speed - 100) > 0.5; })) orig[i].speed = true;
      }
      // the plan's own range too: without a sidecar it is the only target (a silence-cut segment = several clips INSIDE
      // [in, out] → every clip of the same media touching [in − pad, out + pad]); with one, it catches a shot changed
      // in Review after the build (direct swap / trim)
      // (Build lays every plan segment on V1)
      targets.push({ seg: i, a: s["in"], b: s.out, media: media, t: s._tl_start, pa: Math.max(pad.a, 0.25), pb: Math.max(pad.b, 0.25), loose: true, track: 0 });
    });
    shots.forEach(function (x, k) {
      if (used[k] || !x.media) return;
      var best = null, bestKey = null;
      targets.forEach(function (t) {
        if (t.media !== x.media) return;
        if (Math.min(x.out, t.b + t.pb) - Math.max(x["in"], t.a - t.pa) < 0) return;     // does not touch the (padded) range
        var key = [t.track == null || t.track === x.track ? 1 : 0,                          // same track as built
                   Math.round((Math.min(x.out, t.b) - Math.max(x["in"], t.a)) * 100),      // most source overlap
                   -(t.t != null && !isNaN(t.t) ? Math.abs(x.start - t.t) : 0)];          // nearest timeline position
        if (!bestKey || key[0] > bestKey[0] || (key[0] === bestKey[0] && (key[1] > bestKey[1] || (key[1] === bestKey[1] && key[2] > bestKey[2])))) { best = t; bestKey = key; }
      });
      if (best && !bestKey[0] && bestKey[1] <= 0) best = null;      // other track + no real overlap = a clip the editor added (cutaway)
      if (best) add(best.seg, k);
    });
    return { bySeg: bySeg, used: used, orig: orig, sidecar: !!pcs && nGood > 0 && nBad <= nGood };
  }
  function sync(quiet) {
    var p0 = P(); if (!p0 || !p0.segments) return Promise.resolve();
    if (NX.isBusy && NX.isBusy()) {          // a Build / timeline job is running: the plan and the sequence are about to change
      if (!quiet) NX.log("• Đang chạy: " + NX.busyLabel() + " — đồng bộ sau khi xong", "#e0a82e");
      return Promise.resolve();
    }
    return host("jesRvShots()").then(function (j) {
      var p = P(); if (!p || !p.segments) return;                       // plan switched while Premiere answered
      st.seqName = j.name; st.syncedAt = Date.now();
      if (!seqMatches(j.name)) {
        st.shots = null; st.extras = [];
        p.segments.forEach(function (s) { delete s._shot; delete s._shots; delete s._gone; });
        if (!quiet) NX.log("• Sequence đang mở (" + j.name + ") không phải bản dựng của plan " + p.name + " — Review sửa plan, bấm Rebuild", "#e0a82e");
        render(); return;
      }
      var shots = (j.shots || []).filter(function (x) { return x.enabled !== false; })
        .sort(function (a, b) { return a.start - b.start || a.track - b.track; });
      var M = matchShots(p, shots), changed = 0, gone = 0;
      p.segments.forEach(function (s, i) {
        delete s._shot; delete s._shots; delete s._gone;
        var xs = (M.bySeg[i] || []).slice().sort(function (a, b) { return a.start - b.start; });
        if (!xs.length) { if (!s.gap) { s._gone = true; gone++; } return; }
        s._shot = xs[0]; s._shots = xs;
        if (s.gap) return;
        // in/out change only when the OUTER edges of the segment's clips moved beyond the silence pad:
        // inner cuts are the build's silence cut (re-made on every Rebuild), not an edit
        var a = Math.min.apply(null, xs.map(function (x) { return x["in"]; })), b = Math.max.apply(null, xs.map(function (x) { return x.out; }));
        var pad = silPad(p, s), o = M.orig[i], na = s["in"], nb = s.out;
        if (o) {
          if (o.speed) return;                                          // slow-mo: timeline in/out are not source seconds
          if (Math.abs(a - o[0]) > pad.a) na = a;
          if (Math.abs(b - o[1]) > pad.b) nb = b;
        } else {
          if (s.speed && s.speed !== 1 && s.speed !== 100) return;
          if (pad.cut) { if (a < s["in"] - pad.a) na = a; if (b > s.out + pad.b) nb = b; }        // only extensions are visible without the sidecar
          else { if (Math.abs(a - s["in"]) > pad.a) na = a; if (Math.abs(b - s.out) > pad.b) nb = b; }
        }
        na = r2(na); nb = r2(nb);
        if (nb - na >= 0.2 && (Math.abs(na - s["in"]) > 0.01 || Math.abs(nb - s.out) > 0.01)) { s["in"] = na; s.out = nb; s.manual = true; delete s.tl; changed++; }   // (montage beat span: the edit wins)
      });
      var ov = {}; (p.overlays || []).forEach(function (o) { if (o && o.file) ov[norm(full(o.file))] = 1; });
      var outs = [p.out ? norm(p.out).replace(/\/+$/, "") + "/" : "", NX.outDir ? norm(NX.outDir()).replace(/\/+$/, "") + "/" : ""];
      st.extras = shots.filter(function (x, k) { return !M.used[k] && x.media && !ov[x.media] && !isToolOutput(x, outs); });
      st.shots = shots;
      if (changed) NX.savePlan({ noRender: true });                     // one paint below, not two
      if (!quiet || changed || gone) NX.log("↻ Đồng bộ " + j.name + ": " + (changed ? changed + " đoạn bạn đã sửa trên timeline → cập nhật plan · " : "") +
        (gone ? gone + " đoạn không còn trên timeline · " : "") + (st.extras.length ? st.extras.length + " clip thêm tay" : "khớp " + (p.segments.length - gone) + " đoạn") +
        (M.sidecar ? "" : " (chưa có .pieces.json của bản Build — khớp theo source)"),
        changed || gone ? "#6fd08c" : "#8b9099");
      render();
    }).catch(function (e) { st.shots = null; if (!quiet) NX.log("✖ Đồng bộ timeline: " + e.message, "#e5534b"); render(); });
  }
  function directOk(s) {
    return S().reviewDirect !== false && st.shots && s && s._shot && !s._gone && !(s.speed && s.speed !== 1 && s.speed !== 100);
  }
  // trim on the timeline replaces ONE clip — a segment split by the silence cut is trimmed in the plan (Rebuild) instead
  function directTrimOk(s) { return directOk(s) && (!s._shots || s._shots.length <= 1); }
  function backupOnce() {
    var k = st.seqName;
    if (st.backedUp[k]) return Promise.resolve();
    var d = new Date(), hh = ("0" + d.getHours()).slice(-2) + ("0" + d.getMinutes()).slice(-2);
    return host('jesCloneSequence("_bak_' + hh + '")').then(function (r) {
      st.backedUp[k] = true; if (r.backup) NX.log("🛟 Đã backup sequence → " + r.backup + " (trước khi sửa thẳng)", "#8b9099");
    });
  }
  // timeline edits take the panel-wide lock (no Build / sound / text placement / voice job runs meanwhile)
  function lock(fn) {
    if (st.busy) { NX.log("• Đang sửa timeline — chờ thao tác trước xong", "#e0a82e"); return Promise.resolve(); }
    if (NX.tryLock && !NX.tryLock("review", "Review sửa timeline")) {
      NX.log("• Đang chạy: " + NX.busyLabel() + " — sửa timeline sau khi xong", "#e0a82e"); return Promise.resolve();
    }
    st.busy = true; status();
    return Promise.resolve().then(fn).catch(function (e) { NX.log("✖ " + (e && e.message || e), "#e5534b"); })
      .then(function () { st.busy = false; if (NX.unlock) NX.unlock("review"); return sync(true); });
  }
  // one timeline op: replace the clip x (a synced shot) with file[a,b] (audio kept only for dialogue)
  function tlReplace(x, file, a, b, audio) {
    return host("jesRvReplace(" + [x.start, JSON.stringify(x.media || ""), JSON.stringify(full(file)),
      r2(a), r2(b), audio ? "true" : "false"].join(",") + ")");
  }
  function tlRemove(x) { return host("jesRvRemove(" + x.start + "," + JSON.stringify(x.media || "") + ")"); }

  // ── edits (plan always; timeline too when the open sequence is this plan's build) ──────────────────────────
  function subPieces(i) {      // "↳" cuts that belong to segment i's take
    var p = P(), s = p.segments[i], out = [];
    for (var j = i + 1; j < p.segments.length && p.segments[j].script_line === s.script_line && String(p.segments[j].label).indexOf("↳") === 0; j++) out.push(j);
    return out;
  }
  function dropSegs(idx) {     // remove plan segments (desc order) and fix highlight / overlay indexes
    var p = P();
    idx.slice().sort(function (a, b) { return b - a; }).forEach(function (j) {
      p.segments.splice(j, 1);
      p.highlights = (p.highlights || []).filter(function (h) { return h.seg !== j; }).map(function (h) { if (h.seg > j) h.seg -= 1; return h; });
      (p.overlays || []).forEach(function (o) { if (o.anchor > j) o.anchor -= 1; });
    });
  }
  function swapTo(i, c, why) {
    var p = P(), s = p.segments[i]; if (!s || !c) return;
    var wasGap = !!s.gap, subs = wasGap ? [] : subPieces(i);
    var audio = wasGap ? !!c.audio : (c.audio !== false && s.audio !== false);
    var direct = directOk(s);
    var apply = function (dur) {
      var old = wasGap ? null : { file: s.file, "in": s["in"], out: s.out, score: s.score };
      if (dur == null && s.tl && s.tl.length === 2) dur = (s.tl[1] - s.tl[0]) * (+s.speed || 100) / 100;   // montage: same beat slot
      if (subs.length) dropSegs(subs);
      s.file = c.file; s["in"] = r2(c["in"]); s.out = r2(dur != null ? c["in"] + dur : c.out); if (c.score != null) s.score = c.score;
      if (wasGap) {
        var txt = String(s.label || "").replace(/^⚠ THIẾU (hình|thoại):\s*/, "");
        s.label = (audio ? "" : "B-roll: ") + txt; s.audio = audio; if (!audio) { s.silence = false; s.subs = false; }
        s.candidates = (s.suggest || []).filter(function (x) { return x !== c; });
        delete s.gap; delete s.text; delete s.suggest;
      } else {
        s.candidates = [old].concat((s.candidates || []).filter(function (x) { return x !== c; }));
      }
      if (c.seg) s.seg = c.seg; else delete s.seg;
      var m_ = data().media[c.file];                       // shooting time follows the new shot (vlog order check)
      if (m_ && m_.created_ts) s.shot_at = Math.round((m_.created_ts + +c["in"]) * 10) / 10; else delete s.shot_at;
      s.flags = (s.flags || []).filter(function (f) { return !/^(đổi take|thay thẻ|chọn clip)/.test(f); }).concat([why || "đổi shot trong Review"]);
      learn(old, c);
    };
    if (!direct) {
      apply(null); NX.savePlan();
      NX.log("• #" + i + " → " + base(c.file) + " " + (+c["in"]).toFixed(1) + "–" + (+c.out).toFixed(1) + "s — đã sửa plan" +
        (st.shots ? "" : " (sequence đang mở không khớp plan)") + " · bấm ▶ Rebuild", "#e0a82e");
      return Promise.resolve();
    }
    return lock(function () {
      return backupOnce().then(function () {
        // the old take's other clips go first — "↳" segments and the silence-cut pieces of this segment —
        // latest first, so the clips still to be removed / replaced keep their timeline start
        var gone = (s._shots || [s._shot]).slice(1);
        subs.forEach(function (j) { var sj = P().segments[j]; ((sj && (sj._shots || (sj._shot ? [sj._shot] : []))) || []).forEach(function (x) { gone.push(x); }); });
        gone.sort(function (a, b) { return b.start - a.start; });
        var chain = Promise.resolve();
        gone.forEach(function (x) { chain = chain.then(function () { return tlRemove(x); }); });
        return chain;
      }).then(function () { return tlReplace(s._shot, c.file, c["in"], c.out, audio); })
        .then(function (r) {
          apply(r.dur); NX.savePlan({ noRender: true });     // lock() re-syncs + paints once
          NX.log("✔ #" + i + " → " + base(c.file) + " " + (+c["in"]).toFixed(1) + "–" + (+s.out).toFixed(1) + "s · đã thay thẳng trên sequence" +
            (Math.abs(r.shift || 0) > 0.04 ? " (các shot sau dời " + (r.shift > 0 ? "+" : "") + r.shift.toFixed(2) + "s)" : "") +
            (subs.length ? " · bỏ " + subs.length + " đoạn ↳ của take cũ" : ""), "#6fd08c");
        });
    });
  }
  function trim(i, side, delta) {
    var s = P().segments[i]; if (!s || s.gap) return;
    var a = s["in"], b = s.out;
    if (side === "in") a = Math.max(0, r2(a + delta)); else b = r2(b + delta);
    if (b - a < 0.2) return;
    if (!directTrimOk(s)) {
      if (directOk(s)) NX.log("• #" + i + " gồm " + s._shots.length + " clip (cắt im lặng) — trim vào plan, bấm ▶ Rebuild", "#e0a82e");
      NX.nudgeSeg(i, side, delta); return;
    }
    lock(function () {
      return backupOnce().then(function () { return tlReplace(s._shot, s.file, a, b, s.audio !== false); }).then(function (r) {
        s["in"] = a; s.out = r2(a + r.dur); delete s.tl; NX.savePlan({ noRender: true });
        NX.log("✔ #" + i + " " + side.toUpperCase() + (delta > 0 ? " +" : " ") + delta + "s → " + s["in"].toFixed(2) + "–" + s.out.toFixed(2) + "s (đã sửa thẳng timeline)", "#6fd08c");
      });
    });
  }
  function learn(old, c) {
    try {
      var sk = data().shake, qo = old && (sk[old.file] || {}).quality, qn = (sk[c.file] || {}).quality;
      if (window.JESLearn && qo != null && qn != null && Math.abs(qn - qo) >= 5) window.JESLearn.bump("take", qn > qo ? "stab_win" : "stab_loss");
    } catch (e) {}
  }
  function jump(s) {
    var t = s._shot ? s._shot.start : s._tl_start;
    if (t != null) host("jesSetPlayhead(" + t + ")").catch(function (e) { NX.log("✖ " + e.message, "#e5534b"); });
  }
  function openSource(f, a, b) {
    host("nxOpenInSource(" + JSON.stringify(full(f)) + "," + a + "," + b + ")").catch(function (e) { NX.log("✖ " + e.message, "#e5534b"); });
  }

  // ── alternatives for a segment ──────────────────────────────────────────────────────────────────────────────
  function alts(s) {
    if (s.gap) return (s.suggest || []).slice();
    return (s.candidates || []).filter(function (c) { return c && c.file && !(c.file === s.file && Math.abs(c["in"] - s["in"]) < 0.3); });
  }
  function closePop() {
    if (st.pop) { st.pop.remove(); st.pop = null; }
    document.removeEventListener("mousedown", outside, true);
    document.removeEventListener("keydown", escKey, true);
  }
  function outside(ev) { if (st.pop && !st.pop.contains(ev.target)) closePop(); }
  function escKey(ev) { if (ev.key === "Escape") closePop(); }
  function optRow(c, k, extra) {
    var dur = (+c.out - +c["in"]);
    var d = descAt(c.file, (+c["in"] + +c.out) / 2);
    return '<div class="rv-opt" data-k="' + k + '"' + (extra || "") + ">" +
      '<div class="rv-oth">' + JESThumbs.img(c.file, +c["in"] + Math.min(0.4, dur / 3)) + "</div>" +
      '<div class="rv-om"><div class="rv-ot"><b>' + esc(base(c.file)) + "</b> " + (+c["in"]).toFixed(1) + "–" + (+c.out).toFixed(1) + "s · " + dur.toFixed(1) + "s" +
        (c.score != null ? ' <span class="rv-pct">' + Math.round(c.score * 100) + "%</span>" : "") +
        (c.seg ? ' <span class="bdg segbtn" title="Một đoạn trong clip dài">đoạn</span>' : "") + "</div>" +
        (d ? '<div class="rv-od">' + esc(d) + "</div>" : "") +
        (c.why ? '<div class="rv-ow">' + esc(c.why) + "</div>" : "") + "</div></div>";
  }
  function openPop(i, anchor) {
    closePop();
    var p = P(), s = p.segments[i]; if (!s) return;
    var list = alts(s);
    var el = document.createElement("div");
    el.className = "rv-pop";
    var head = '<div class="rv-ph"><b>#' + i + " · " + esc(s.script_line || "") + "</b> " + esc(String(s.label || "").slice(0, 70)) +
      '<button class="btn rv-x" title="Đóng (Esc)">✕</button></div>' +
      '<div class="rv-pnote">' + (directOk(s) ? "🔗 Bấm 1 lựa chọn → thay thẳng trên sequence" : "📝 Bấm 1 lựa chọn → sửa plan (bấm ▶ Rebuild sau)") + "</div>";
    var cur = s.gap ? '<div class="rv-cur">▲ Thẻ thiếu ' + (s.out - s["in"]).toFixed(1) + "s</div>"
      : '<div class="rv-cur">' + optRow({ file: s.file, "in": s["in"], out: s.out, score: s.score, seg: s.seg }, -1, ' data-cur="1"') + "</div>";
    el.innerHTML = head + cur + '<div class="rv-list">' + (list.length ? list.map(function (c, k) { return optRow(c, k); }).join("")
      : '<div class="muted" style="padding:8px">Chưa có lựa chọn gợi ý cho đoạn này.</div>') + "</div>" +
      '<div class="rv-pf"><input class="rv-q" placeholder="🔍 Tìm clip khác trong source (tên, mô tả)…"><div class="rv-more"></div></div>';
    document.body.appendChild(el); st.pop = el;
    var r = anchor.getBoundingClientRect(), w = Math.min(400, window.innerWidth - 16);
    el.style.width = w + "px";
    el.style.left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8)) + "px";
    var top = r.bottom + 4, h = Math.min(window.innerHeight * 0.7, 520);
    if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 4);
    el.style.top = top + "px"; el.style.maxHeight = h + "px";
    el.querySelector(".rv-x").onclick = closePop;
    Array.prototype.forEach.call(el.querySelectorAll(".rv-list .rv-opt"), function (o) {
      o.onclick = function () { var c = list[+o.getAttribute("data-k")]; closePop(); swapTo(i, c, s.gap ? "thay thẻ thiếu trong Review" : "đổi shot trong Review"); };
    });
    var cu = el.querySelector(".rv-cur .rv-opt");
    if (cu) cu.onclick = function () { openSource(s.file, s["in"], s.out); };
    var q = el.querySelector(".rv-q"), t = null;
    q.oninput = function () { clearTimeout(t); t = setTimeout(function () { searchOther(i, q.value, el.querySelector(".rv-more")); }, 180); };
    JESThumbs.fill(el);
    document.addEventListener("mousedown", outside, true);
    document.addEventListener("keydown", escKey, true);
  }
  // any clip of the source (long takes → their segments), ranked by text match on name + descriptions
  function searchOther(i, query, box) {
    var D = data(), s = P().segments[i], q = String(query || "").trim().toLowerCase();
    if (!q) { box.innerHTML = ""; return; }
    var want = Math.max(1.5, Math.min(6, s.out - s["in"] || 3)), rows = [];
    Object.keys(D.media).forEach(function (f) {
      var m = D.media[f] || {}, sg = (D.segs[f] || {}).segs || [], au = D.auto[f] || {};
      var hay = (f + " " + ((D.ai[f] || {}).summary || "") + " " + (au.desc || "") + " " + ((D.tags[f] || {}).tags || []).join(" ") + " " + ((D.tags[f] || {}).desc || "")).toLowerCase();
      if (sg.length >= 2) {
        sg.forEach(function (x) {
          var k = x.k || (x.a.toFixed(1) + "-" + x.b.toFixed(1)), d = ((au.segs || {})[k] || {}).desc || "";
          if ((hay + " " + d.toLowerCase()).indexOf(q) < 0) return;
          var a = x.a + Math.max(0, (x.b - x.a - want) / 2);
          rows.push({ file: f, "in": r2(a), out: r2(Math.min(x.b, a + want)), seg: [x.a, x.b], audio: false });
        });
      } else if (hay.indexOf(q) >= 0) {
        var us = ((D.shake[f] || {}).usable || []).slice().sort(function (u, v) { return (v[1] - v[0]) - (u[1] - u[0]); })[0] || [0.3, m.dur || want];
        var a2 = us[0] + Math.max(0, (us[1] - us[0] - want) / 2);
        rows.push({ file: f, "in": r2(a2), out: r2(Math.min(us[1], a2 + want, m.dur || 1e9)), audio: !!(s.audio !== false && !s.gap && m.audio) });
      }
    });
    rows = rows.slice(0, 30);
    box.innerHTML = rows.length ? rows.map(function (c, k) { return optRow(c, k); }).join("") : '<div class="muted" style="padding:6px">Không có clip nào khớp “' + esc(query) + "”.</div>";
    Array.prototype.forEach.call(box.querySelectorAll(".rv-opt"), function (o) {
      o.onclick = function () { var c = rows[+o.getAttribute("data-k")]; closePop(); swapTo(i, c, "chọn clip khác trong Review: " + base(c.file)); };
    });
    JESThumbs.fill(box);
  }

  // ── table ───────────────────────────────────────────────────────────────────────────────────────────────────
  function status() {
    var el = $("rvSync"); if (!el) return;
    var on = S().reviewDirect !== false;
    el.className = "rv-mode " + (st.busy ? "busy" : st.shots && on ? "live" : "plan");
    el.textContent = st.busy ? "⏳ Đang sửa timeline…" :
      st.shots && on ? "🔗 Sửa thẳng: " + st.seqName + " · đồng bộ " + new Date(st.syncedAt).toTimeString().slice(0, 8) :
      st.shots ? "📝 Đang tắt sửa thẳng — thay đổi chỉ vào plan" :
      st.seqName ? "📝 Sequence đang mở (" + st.seqName + ") không phải bản dựng của plan này — sửa plan, bấm ▶ Rebuild" :
      "📝 Chưa đọc timeline — bấm ↻ Đồng bộ";
  }
  function shotAt(ts) {
    var d = new Date(ts * 1000), p = function (x) { return ("0" + x).slice(-2); };
    return p(d.getDate()) + "/" + p(d.getMonth() + 1) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
  }
  function markBack(segs) {        // shooting-time order: flag shots filmed > 5 min BEFORE the previous shot (hook excluded)
    var prev = null;
    segs.forEach(function (s) {
      delete s._back;
      if (s.gap || !s.shot_at || s.scene === "HOOK" || s.script_line === "hook") return;
      if (prev != null && s.shot_at < prev - 300) {
        var m = Math.round((prev - s.shot_at) / 60);
        s._back = m >= 120 ? Math.round(m / 60) + " giờ" : m + " phút";
      }
      prev = Math.max(prev || 0, s.shot_at);
    });
  }
  function issueOf(s) {
    return !!s.gap || !!s._gone || (s.score && s.score < 0.65) ||
      (s.flags || []).some(function (f) { return /lỗi|rung|STITCH|soft|thủ công|THIẾU|chừa/i.test(f); });
  }
  function rowHtml(s, i, first) {
    var gap = !!s.gap, speech = !gap && s.audio !== false && !/b-roll|montage/i.test(s.label || ""), issue = issueOf(s);
    var stIcon = gap ? '<span class="rv-st gap" title="Thiếu hình / thoại">▲</span>' :
      s._gone ? '<span class="rv-st gone" title="Không còn trên timeline">⛔</span>' :
      issue ? '<span class="rv-st low" title="Cần xem: điểm thấp / có cảnh báo">⚠</span>' : '<span class="rv-st ok" title="Đã có shot">✔</span>';
    var lineCell = first ? '<div class="rv-lid" title="' + esc(s.scene || "") + '">' + esc(s.script_line || "–") + "</div>" + stIcon
      : '<div class="rv-sub" title="Cùng câu kịch bản">↳</div>' + stIcon;
    var nAlt = alts(s).length, dur = s.out - s["in"];
    var th = gap ? '<div class="rv-th gapbox" data-i="' + i + '" title="Bấm để chọn shot lấp thẻ thiếu (' + nAlt + ' gợi ý)">▲<span class="rv-n">' + nAlt + "</span></div>"
      : '<div class="rv-th" data-i="' + i + '" title="Bấm để xem & đổi shot (' + nAlt + ' lựa chọn)">' + JESThumbs.img(s.file, +s["in"] + Math.min(0.3, dur / 3)) +
        '<span class="rv-n">' + (nAlt ? "⇄" + nAlt : "⇄") + "</span></div>";
    var clip = gap ? '<b style="color:var(--warn)">THẺ THIẾU</b><div class="muted">chừa ' + dur.toFixed(1) + "s</div>"
      : '<div class="rv-fn" title="' + esc(s.file) + '">' + esc(base(s.file)) + "</div>" +
        '<div class="rv-io">' + (+s["in"]).toFixed(2) + "–" + (+s.out).toFixed(2) + ' <span class="muted">· ' + dur.toFixed(1) + "s</span></div>" +
        (s.shot_at ? '<div class="rv-at' + (s._back ? " back" : "") + '" title="' + (s._back ? "Shot này quay SỚM hơn shot trước nó " + s._back + " — mạch thời gian bị lùi" : "Giờ quay") + '">' +
          (s._back ? "↩ " : "🕒 ") + shotAt(s.shot_at) + "</div>" : "") +
        '<div class="rv-nd" title="Trim ±0.5s">In<button class="nb" data-i="' + i + '" data-side="in" data-d="-0.5" title="Mở rộng đầu 0.5s">◀</button>' +
        '<button class="nb" data-i="' + i + '" data-side="in" data-d="0.5" title="Cắt bớt đầu 0.5s">▶</button> Out' +
        '<button class="nb" data-i="' + i + '" data-side="out" data-d="-0.5" title="Cắt bớt đuôi 0.5s">◀</button>' +
        '<button class="nb" data-i="' + i + '" data-side="out" data-d="0.5" title="Mở rộng đuôi 0.5s">▶</button></div>';
    var chip = gap ? '<span class="seg-chip chip-gap">▲ THIẾU</span>' :
      speech ? '<span class="seg-chip chip-speech">THOẠI</span>' : '<span class="seg-chip chip-broll">B-ROLL</span>';
    if (s.manual) chip += ' <span class="bdg" style="color:var(--acc)" title="Bạn đã sửa đoạn này trên timeline — plan đã cập nhật theo">✎ sửa tay</span>';
    if (s._gone) chip += ' <span class="bdg r" title="Không thấy đoạn này trên sequence đang mở (đã xóa tay?)">⛔ không còn trên timeline</span>';
    var d = gap ? "" : descAt(s.file, (+s["in"] + +s.out) / 2), fl = s.flags || [];
    var content = chip + ' <span class="rv-lb">' + esc(String(s.label || "").replace(/^B-roll:\s*/, "")) + "</span>" +
      (d ? '<div class="rv-desc">' + esc(d) + "</div>" : "") +
      (fl.length ? '<div class="flag" title="' + esc(fl.join(" · ")) + '">' + esc(fl.slice(0, 2).join(" · ")) + (fl.length > 2 ? " +" + (fl.length - 2) : "") + "</div>" : "");
    var sc = s.score != null && !gap ? '<span class="score-pill ' + (s.score >= 0.75 ? "score-high" : s.score >= 0.6 ? "score-mid" : "score-low") + '">' + Math.round(s.score * 100) + "%</span>" : "";
    return '<tr class="seg rv' + (issue ? " has-issue" : "") + (gap ? " gap" : "") + (s._gone ? " gone" : "") + (first ? " grp" : "") + '" data-i="' + i + '">' +
      '<td class="c-l">' + lineCell + "</td><td class=\"c-t\">" + th + '</td><td class="c-c">' + clip + "</td><td>" + content + "</td>" +
      '<td class="c-s">' + sc + '<button class="btn-nav tl rv-jump" data-i="' + i + '" title="Nhảy playhead tới đoạn này (' + fmt(s._shot ? s._shot.start : s._tl_start) + ')">📍</button></td></tr>';
  }
  function visible() { var r = $("review"); return !!(r && r.classList.contains("on")); }
  function render() {
    var p = P(), tb = $("segTable"); status();
    if (!p || !p.segments || !tb) return;
    if (!visible()) { st.dirty = true; return; }          // hidden tab: paint when it is opened (show)
    st.dirty = false;
    closePop();
    var wrap = $("reviewWrap"), keep = wrap ? wrap.scrollTop : 0;
    var q = (($("reviewSearch") || {}).value || "").trim().toLowerCase();
    var scv = ($("reviewSceneFilter") || {}).value || "", ty = ($("reviewTypeFilter") || {}).value || "all", only = $("onlyIssues") && $("onlyIssues").checked;
    var h = ['<thead><tr><th class="c-l">Câu</th><th class="c-t">Hình</th><th class="c-c">Clip · In–Out</th><th>Nội dung</th><th class="c-s">Điểm</th></tr></thead><tbody>'];
    var n = 0, prev = null;
    if (p.chrono) markBack(p.segments); else p.segments.forEach(function (s) { delete s._back; });   // order check only when chrono is on
    p.segments.forEach(function (s, i) {
      var gap = !!s.gap, speech = !gap && s.audio !== false && !/b-roll|montage/i.test(s.label || ""), issue = issueOf(s);
      var first = s.script_line !== prev; prev = s.script_line;
      if (only && !issue) return;
      if (scv && (s.scene || "Chung") !== scv) return;
      if (ty === "speech" && !speech) return;
      if (ty === "broll" && (gap || speech)) return;
      if (ty === "gaps" && !gap) return;
      if (ty === "issues" && !issue) return;
      if (q && (String(s.label || "") + " " + (s.file || "") + " " + (s.scene || "") + " " + (s.script_line || "") + " " + (s.flags || []).join(" ")).toLowerCase().indexOf(q) < 0) return;
      n++; h.push(rowHtml(s, i, first));
    });
    if (!n) h.push('<tr><td colspan="5" style="text-align:center;padding:18px;color:var(--dim)">Không có đoạn nào khớp bộ lọc.</td></tr>');
    if (st.extras.length) {
      var srcN = norm(p.src) + "/";
      h.push('<tr class="rv-exh"><td colspan="5">＋ ' + st.extras.length + " clip bạn thêm tay trên timeline (không có trong plan)</td></tr>");
      st.extras.forEach(function (x) {
        var rel = x.media.indexOf(srcN) === 0 ? x.media.slice(srcN.length) : "";
        h.push('<tr class="rv-extra"><td class="c-l"><span class="rv-st">＋</span></td><td class="c-t">' +
          (rel ? '<div class="rv-th ro">' + JESThumbs.img(rel, x["in"] + 0.2) + "</div>" : "") + '</td><td class="c-c"><div class="rv-fn">' + esc(x.name) + "</div>" +
          '<div class="rv-io">' + x["in"].toFixed(2) + "–" + x.out.toFixed(2) + '</div></td><td class="muted">V' + (x.track + 1) + " @ " + fmt(x.start) +
          (rel ? " · " + esc(descAt(rel, (x["in"] + x.out) / 2)) : "") + '</td><td class="c-s"><button class="btn-nav tl rv-xjump" data-t="' + x.start + '">📍</button></td></tr>');
      });
    }
    h.push("</tbody>");
    tb.innerHTML = h.join("");
    st.rendered = p; st.renderedFilter = filterKey();
    if (wrap && keep) wrap.scrollTop = keep;
    Array.prototype.forEach.call(tb.querySelectorAll(".rv-th[data-i]"), function (el) {
      el.onclick = function (ev) { ev.stopPropagation(); openPop(+el.getAttribute("data-i"), el); };
    });
    Array.prototype.forEach.call(tb.querySelectorAll(".nb"), function (b) {
      b.onclick = function (ev) { ev.stopPropagation(); trim(+b.getAttribute("data-i"), b.getAttribute("data-side"), +b.getAttribute("data-d")); };
    });
    Array.prototype.forEach.call(tb.querySelectorAll(".rv-jump"), function (b) {
      b.onclick = function (ev) { ev.stopPropagation(); jump(P().segments[+b.getAttribute("data-i")]); };
    });
    Array.prototype.forEach.call(tb.querySelectorAll(".rv-xjump"), function (b) {
      b.onclick = function (ev) { ev.stopPropagation(); host("jesSetPlayhead(" + b.getAttribute("data-t") + ")").catch(function () {}); };
    });
    Array.prototype.forEach.call(tb.querySelectorAll("tr.seg"), function (tr) {
      tr.onclick = function (ev) {
        if (/BUTTON|INPUT|SELECT|IMG/.test(ev.target.tagName)) return;
        Array.prototype.forEach.call(tb.querySelectorAll("tr.sel"), function (x) { x.classList.remove("sel"); });
        tr.classList.add("sel");
        var s = P().segments[+tr.getAttribute("data-i")]; if (!s) return;
        if (!s.gap) openSource(s.file, s["in"], s.out);
        jump(s);
      };
    });
    JESThumbs.fill(tb);
  }

  function init() {
    var info = $("planInfo"), bar = info && info.parentNode;
    if (bar && !$("rvSyncBox")) {
      var d = document.createElement("div");
      d.id = "rvSyncBox"; d.className = "rv-syncbox";
      d.innerHTML = '<span id="rvSync" class="rv-mode plan"></span>' +
        '<button class="btn" id="bRvSync" title="Đọc lại sequence đang mở: những gì bạn đã trim / xóa / thêm trên timeline được cập nhật vào plan">↻ Đồng bộ</button>' +
        '<label class="chk" title="Đổi shot / trim / lấp thẻ thiếu trong Review sẽ sửa thẳng sequence đang mở (tự backup 1 lần trước khi sửa)">' +
        '<input type="checkbox" id="rvDirect" style="width:auto"> Sửa thẳng sequence</label>';
      bar.parentNode.insertBefore(d, bar.nextSibling);
      $("rvDirect").checked = S().reviewDirect !== false;
      $("rvDirect").onchange = function () { S().reviewDirect = $("rvDirect").checked; NX.save(); status(); };
      $("bRvSync").onclick = function () { sync(false); };
    }

    window.addEventListener("resize", closePop);
    status();
    if (P()) render();                                     // paints now if Review is the open tab, else marks it dirty
  }

  function filterKey() {
    return [($("reviewSearch") || {}).value, ($("reviewSceneFilter") || {}).value, ($("reviewTypeFilter") || {}).value, ($("onlyIssues") || {}).checked, st.extras.length].join("|");
  }
  // tab opened: repaint only when the plan object / filters changed; re-read the timeline at most every 10 s, after paint
  function show() {
    var p = P(); if (!p) return;
    if (st.dirty || st.rendered !== p || st.renderedFilter !== filterKey()) render(); else status();
    if (Date.now() - st.syncedAt > 10000 && !st.busy && !(NX.isBusy && NX.isBusy())) setTimeout(function () { sync(true); }, 120);
  }
  // plan switched (other source folder / session): forget the previous timeline match
  function reset() {
    closePop();
    st.shots = null; st.extras = []; st.seqName = ""; st.syncedAt = 0; st.rendered = null; st.dirty = true;
    status();
  }
  window.JESReview = { render: render, show: show, sync: sync, swapTo: swapTo, alts: alts, reset: reset };
  init();
})();
