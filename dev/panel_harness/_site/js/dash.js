/* Justin EDIT Suite — source dashboard (folders, clips, stability strips, insights) + pipeline lanes. */
(function () {
  "use strict";
  var fs = require("fs"), path = require("path"), toUrl = require("url").pathToFileURL;
  var NX = window.NX, $ = function (id) { return document.getElementById(id); };
  var open = {}, lanes = {}, renderT = null;

  function S() { return NX.S; }
  function W(f) { return path.join(S().src || "", "_nexys_work", f || ""); }
  function rj(p, d) { try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) { return d; } }
  // v1.8: read-only views (dashboard render) re-parse a JSON only when its mtime/size changed — the dashboard used to
  // re-read ~1 MB of JSON + list transcripts/ on every repaint (tab click, every progress line, every describe tick).
  // Writers keep using rj() (fresh object they can mutate).
  var rjCache = {};
  function rjc(p, d) {
    var st; try { st = fs.statSync(p); } catch (e) { delete rjCache[p]; return d; }
    var c = rjCache[p];
    if (c && c.m === st.mtimeMs && c.z === st.size) return c.v;
    var v; try { v = JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) { return c ? c.v : d; }
    rjCache[p] = { m: st.mtimeMs, z: st.size, v: v }; return v;
  }
  var SIG_FILES = ["media.json", "qc.json", "shake.json", "auto_desc.json", "segments.json", "digest.json", "source_ai.json",
                   "broll_tags.json", "roles.json", "triage.json", "media_all.json", "transcripts", "proxy/manifest.json"];
  function dataSig() {
    return (S().src || "") + "|" + SIG_FILES.map(function (f) { try { var t = fs.statSync(W(f)); return t.mtimeMs + ":" + t.size; } catch (e) { return "-"; } }).join("|");
  }
  var trCache = { dir: "", m: 0, v: {} };
  function esc(s) { return NX.esc(String(s == null ? "" : s)); }
  function stemOf(f) { return String(f).replace(/\\/g, "/").replace(/\.\w+$/, "").replace(/\//g, "__").replace(/[<>:"|?*]/g, "_"); }
  function hms(s) { s = Math.round(s || 0); var h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60; return (h ? h + ":" + ("0" + m).slice(-2) : m) + ":" + ("0" + x).slice(-2); }
  function col(q) { return q == null ? "var(--dim)" : q >= 75 ? "var(--ok)" : q >= 55 ? "var(--acc)" : q >= 40 ? "var(--warn)" : "var(--bad)"; }
  function ts(s) { var t = Date.parse(s || ""); return isNaN(t) ? null : t / 1000; }

  function load() {
    if (!S().src || !fs.existsSync(W("media.json"))) return null;
    var tr = {}, tdir = W("transcripts");
    try {
      var tm = fs.statSync(tdir).mtimeMs;
      if (trCache.dir === tdir && trCache.m === tm) tr = trCache.v;
      else {
        fs.readdirSync(tdir).forEach(function (f) { if (/\.json$/.test(f) && !/\.v2\.json$/.test(f)) tr[f.replace(/\.json$/, "")] = 1; });
        trCache = { dir: tdir, m: tm, v: tr };
      }
    } catch (e) {}
    var dg = {};                                   // file -> auto description from digest (Ollama vision / AI)
    try { var dc = (rjc(W("digest.json"), {}) || {}).clips || {}; Object.keys(dc).forEach(function (k) { var c = dc[k]; if (c && c.file && c.desc) dg[c.file] = c.desc; }); } catch (e) {}
    var ad = (rjc(W("auto_desc.json"), {}) || {}).clips || {};   // local vision descriptions (nxcore.describe)
    var sgp = W("segments.json"), sg = (rjc(sgp, {}) || {}).clips || {};   // long takes split into usable shots (nxcore.segments)
    return {
      digest: dg, autoDesc: ad, segs: sg, hasSegs: fs.existsSync(sgp),
      media: rjc(W("media.json"), {}),
      tr: tr,
      qc: rjc(W("qc.json"), {}),
      stab: rjc(W("shake.json"), {}),
      ai: (rjc(W("source_ai.json"), {}) || {}).clips || {},
      proxy: rjc(W("proxy/manifest.json"), {}),
      brollTags: rjc(W("broll_tags.json"), {}) || {},
      roles: rjc(W("roles.json"), {}) || {}
    };
  }

  function stats(D) {
    var keys = Object.keys(D.media), folders = {}, st = { n: keys.length, dur: 0, audio: 0, tr: 0, qc: 0, stab: 0, vfr: [], shaky: [], tilt: [], slow: [], short: [], noaudio: [], qsum: 0, qn: 0, usable: 0 };
    keys.forEach(function (f) {
      var m = D.media[f], fo = (m.folder != null ? m.folder : (f.indexOf("/") >= 0 ? f.slice(0, f.lastIndexOf("/")) : "")) || "(gốc)";
      (folders[fo] = folders[fo] || []).push(f);
      st.dur += m.dur || 0;
      if (m.audio) { st.audio++; if (D.tr[stemOf(f)]) st.tr++; } else st.noaudio.push(f);
      if (D.qc[f]) st.qc++;
      var s = D.stab[f];
      if (s && s.v >= 2) {
        st.stab++; st.qsum += s.quality || 0; st.qn++;
        (s.usable || []).forEach(function (u) { st.usable += u[1] - u[0]; });
        if ((s.stability || 100) < 50) st.shaky.push(f);
        if (s.composition && s.composition.rotate_fix) st.tilt.push(f);
        if ((s.usable_slowmo || []).length) st.slow.push(f);
      }
      if (m.vfr) st.vfr.push(f);
      if ((m.dur || 0) < 1.2) st.short.push(f);
    });
    st.folders = folders;
    // near-duplicate takes (same folder, shot back-to-back, same frame size) — same rule family as brollpick
    var groups = 0;
    Object.keys(folders).forEach(function (fo) {
      var arr = folders[fo].slice().sort(function (a, b) { return (ts(D.media[a].created) || 0) - (ts(D.media[b].created) || 0); }), run = 1;
      for (var i = 1; i < arr.length; i++) {
        var a = D.media[arr[i - 1]], b = D.media[arr[i]], ta = ts(a.created), tb = ts(b.created);
        if (ta && tb && tb - ta <= 150 && a.w === b.w && a.h === b.h) run++; else { if (run > 1) groups++; run = 1; }
      }
      if (run > 1) groups++;
    });
    st.groups = groups;
    // folders recorded at the same time → multicam candidates
    var span = Object.keys(folders).map(function (fo) {
      var t = folders[fo].map(function (f) { return ts(D.media[f].created); }).filter(Boolean);
      return t.length ? { fo: fo, a: Math.min.apply(null, t), b: Math.max.apply(null, t) } : null;
    }).filter(Boolean), multi = [];
    for (var i = 0; i < span.length; i++) for (var j = i + 1; j < span.length; j++) {
      var ov = Math.min(span[i].b, span[j].b) - Math.max(span[i].a, span[j].a);
      if (ov > 300) multi.push(span[i].fo + " ↔ " + span[j].fo);
    }
    st.multi = multi;
    return st;
  }

  function insights(D, st) {
    var out = [];
    var need = st.n - Math.min(st.qc, st.stab), needTr = st.audio - st.tr;
    if (need > 0 || needTr > 0) out.push(["warn", "⏳", "Chưa phân tích đủ: " + (needTr > 0 ? needTr + " clip chưa transcribe · " : "") + (need > 0 ? need + " clip chưa QC/độ ổn định" : "") + " → bấm <b>1 · Phân tích</b> (chỉ chạy phần còn thiếu)."]);
    if (st.vfr.length) out.push(["warn", "⚠", st.vfr.length + " clip VFR (khung hình thay đổi, thường là điện thoại) — dễ lệch tiếng khi dựng; nên convert CFR."]);
    if (st.shaky.length) out.push(["bad", "〰", st.shaky.length + " clip rung nhiều — tool chỉ lấy đoạn ổn định bên trong (xanh = ổn định, vàng-xanh = cầm tay OK, đỏ = rung, cam = lia nhanh)."]);
    if (st.slow.length) out.push(["", "🐢", st.slow.length + " clip 50/60p rung nhẹ nhưng dùng được khi slow-mo ≤50% (vạch tím) — đúng kiểu anh hay làm."]);
    if (st.tilt.length) out.push(["", "⟲", st.tilt.length + " clip bị nghiêng — plan sẽ gợi ý độ xoay (vd +1.0°) như anh vẫn chỉnh tay."]);
    if (st.groups) out.push(["", "⧉", "~" + st.groups + " nhóm take quay liền nhau — khi dựng, mỗi nhóm lấy 1 take ổn định/bố cục tốt nhất."]);
    if (st.multi.length) out.push(["good", "🎥", "Folder quay cùng lúc (có thể multicam): " + esc(st.multi.slice(0, 3).join(" · "))]);
    if (st.short.length) out.push(["", "✂", st.short.length + " clip < 1.2s — bỏ qua khi chọn B-roll."]);
    if (st.noaudio.length) out.push(["", "🔇", st.noaudio.length + " clip không có tiếng."]);
    if (st.n && !Object.keys(D.ai).length) out.push(["", "✨", "Chưa có phân tích AI (mô tả clip, tag B-roll) — dùng <b>🔍 Phân tích source</b> ở phần kịch bản để chọn shot chính xác hơn."]);
    var qcFix = function (q, dur) {
    q = Object.assign({}, q || {});
    if ((q.qc_v || 1) >= 2 || !(q.freeze || []).length) return q;
    var rs = q.freeze.map(function (r) { return [r[0], r[1]]; }).sort(function (a, b) { return a[0] - b[0]; }), runs = [];
    rs.forEach(function (r) { var L = runs[runs.length - 1]; if (L && r[0] - L[1] <= 1.5) { L[1] = Math.max(L[1], r[1]); L[2]++; } else runs.push([r[0], r[1], 1]); });
    var cover = runs.reduce(function (a, r) { return a + r[1] - r[0]; }, 0), fr = [], st = (q.static || []).slice();
    runs.forEach(function (r) { if ((dur && cover >= 0.4 * dur) || r[1] - r[0] >= Math.max(8, 0.5 * (dur || 0)) || r[2] >= 3) st.push([r[0], r[1]]); else fr.push([r[0], r[1]]); });
    q.freeze = fr; q.static = st;
    return q;
  };
    var stat = Object.keys(D.qc).filter(function (f) { return D.media[f] && (qcFix(D.qc[f], D.media[f].dur).static || []).length; });
    if (stat.length) out.push(["", "▣", stat.length + " clip quay dài liên tục / máy cố định (hình ít thay đổi) — footage bình thường, không tính là lỗi freeze."]);
    var sc = rj(W("source_check.json"), null);
    if (sc && sc.items) out.push([sc.gaps.length ? "warn" : "good", "📎", "Kịch bản AI: " + sc.ok + "/" + sc.items + " mục có source đạt chuẩn" +
      (sc.gaps.length ? " · <b>" + sc.gaps.length + " mục thiếu</b>: " + esc(sc.gaps.slice(0, 3).join(" · ")) + (sc.gaps.length > 3 ? "…" : "") : "")]);
    if (st.qn) out.push(["good", "✓", "Chất lượng trung bình " + Math.round(st.qsum / st.qn) + "/100 · " + hms(st.usable) + " hình dùng được / " + hms(st.dur) + "."]);
    return out;
  }

  // ---- fast overview (nxcore.media triage): format groups the editor can include/exclude + broken files
  function profileBump(key, field) {
    var pp = path.join(NX.cfgDir, "profile.json"), P = rj(pp, null) || { version: 1, stats: {}, history: [] };
    P.stats = P.stats || {}; var st = P.stats[key] = P.stats[key] || {}; st[field] = (st[field] || 0) + 1;
    try { fs.writeFileSync(pp, JSON.stringify(P, null, 1), "utf8"); } catch (e) {}
  }
  function applyTriage(tri) {
    var all = rj(W("media_all.json"), null); if (!all) return;
    var exg = tri.exclude_groups || [], exf = tri.exclude_files || [], inf = tri.include_files || [], act = {};
    Object.keys(all).forEach(function (f) { var r = all[f];
      if ((r.status === "ok" && exg.indexOf(r.group) < 0 && exf.indexOf(f) < 0) || (inf.indexOf(f) >= 0 && r.w)) act[f] = r; });
    fs.writeFileSync(W("media.json"), JSON.stringify(act, null, 1), "utf8");
  }
  function toggleGroup(g) {
    var tri = rj(W("triage.json"), null); if (!tri) return;
    var ex = tri.exclude_groups = tri.exclude_groups || [], i = ex.indexOf(g), sug = (tri.suggest_exclude || []).indexOf(g) >= 0;
    if (i >= 0) ex.splice(i, 1); else ex.push(g);
    if (sug) profileBump("triage", i >= 0 ? "upscale_kept" : "upscale_excluded");    // learns whether to auto-drop upscale groups
    fs.writeFileSync(W("triage.json"), JSON.stringify(tri, null, 1), "utf8");
    applyTriage(tri);
    NX.log((i >= 0 ? "✔ Dùng lại nhóm " : "✔ Loại nhóm ") + g + " — các bước phân tích sau chỉ chạy trên clip đang dùng");
    render();
  }
  function triageHtml() {
    var tri = rj(W("triage.json"), null);
    if (!tri || !tri.groups) return "";
    var ex = tri.exclude_groups || [], sug = tri.suggest_exclude || [], gs = Object.keys(tri.groups).sort(function (a, b) { return tri.groups[b].n - tri.groups[a].n; });
    var broken = Object.keys(tri.broken || {});
    if (gs.length < 2 && !broken.length) return "";
    var h = '<div class="tri"><div class="th">🗂 Tổng quan định dạng' + (tri.target ? ' <span class="muted">· khung đích ' + esc(tri.target) + "</span>" : "") + ' <span class="muted">— bấm để dùng / loại</span></div><div class="tchips">';
    h += gs.map(function (g) {
      var v = tri.groups[g], off = ex.indexOf(g) >= 0, sg = sug.indexOf(g) >= 0;
      return '<button class="tchip' + (off ? " off" : "") + (sg ? " sug" : "") + '" data-g="' + esc(g) + '" title="' + esc((v.sample || []).join(", ")) + '">' +
        (off ? "✕ " : "✓ ") + esc(g) + " <b>" + v.n + "</b> · " + hms(v.dur) + (sg ? " · ⤢ phải phóng to" : "") + "</button>";
    }).join("") + "</div>";
    if (broken.length) h += '<details class="brk"><summary>⛔ ' + broken.length + " file lỗi / bỏ qua (không phân tích)</summary>" +
      broken.slice(0, 80).map(function (f) { return "<div><b>" + esc(f) + "</b> — " + esc(tri.broken[f]) + "</div>"; }).join("") + "</details>";
    return h + "</div>";
  }

  // ── Clip description ─────────────────────────────────────────────────────────
  // Priority: editor note (broll_tags.json[f].desc) > AI summary (source_ai.json) > local vision (auto_desc.json)
  //           > digest auto-desc > transcript opening.
  var trSnipCache = {}, editingDesc = false;
  function transcriptSnippet(f) {
    var p = W(path.join("transcripts", stemOf(f) + ".json")), mt = 0, c = trSnipCache[p];
    if (c && c.sig === lastSig) return c.text;                 // v1.9: no per-row stat while the source data is unchanged
    try { mt = fs.statSync(p).mtimeMs; } catch (e) { trSnipCache[p] = { sig: lastSig, mt: -1, text: "" }; return ""; }
    if (c && c.mt === mt) { c.sig = lastSig; return c.text; }
    var j = rj(p, {}) || {}, txt = "";
    (j.segments || []).some(function (s) { txt += (txt ? " " : "") + String(s.text || "").trim(); return txt.length >= 140; });
    txt = txt.replace(/\s+/g, " ").trim();
    if (txt.split(" ").length < 3) txt = "";          // "ừ ừ", "ok" … — not a description
    if (txt.length > 140) txt = txt.slice(0, 137).replace(/\s+\S*$/, "") + "…";
    trSnipCache[p] = { sig: lastSig, mt: mt, text: txt };
    return txt;
  }
  // digest falls back to "tag1, tag2, …" when it has no real description — that only repeats the badges
  function isTagList(text, D, f) {
    var known = {};
    ((D.media[f] || {}).tags || []).concat(((D.brollTags || {})[f] || {}).tags || [], (D.ai[f] || {}).tags || [])
      .forEach(function (t) { known[String(t).trim().toLowerCase()] = 1; });
    var parts = String(text).split(/\s*[,·]\s*/).filter(Boolean);
    return parts.length > 0 && parts.every(function (p) { return known[p.toLowerCase()]; });
  }
  function descOf(D, f) {
    var t = (D.brollTags && D.brollTags[f]) || {}, a = D.ai[f] || {};
    if (t.desc) return { src: "note", ic: "✏️", text: String(t.desc), tip: "Mô tả của bạn" };
    if (a.summary) {
      var extra = [a.shot, a.people && !/^không rõ$/i.test(a.people) ? a.people : ""].filter(Boolean).join(" · ");
      return { src: "ai", ic: "✨", text: a.summary + (extra ? " — " + extra : ""), tip: "Mô tả từ Phân tích AI" };
    }
    var au = D.autoDesc && D.autoDesc[f];
    if (au && au.desc) return { src: "auto", ic: "🤖", text: au.desc, tip: "Mô tả tự động (" + (au.model || "AI local") + ")" };
    if (D.digest && D.digest[f] && !isTagList(D.digest[f], D, f)) return { src: "auto", ic: "🤖", text: D.digest[f], tip: "Mô tả tự động (digest)" };
    if (D.tr[stemOf(f)]) {
      var sn = transcriptSnippet(f);
      if (sn) return { src: "tr", ic: "💬", text: "“" + sn + "”", tip: "Đoạn đầu lời thoại (transcript)" };
    }
    return null;
  }
  function saveNote(f, text) {
    var tp = W("broll_tags.json"), cur = rj(tp, {}) || {};
    text = String(text || "").replace(/\s+/g, " ").trim();
    if (text) { cur[f] = cur[f] || { tags: [] }; cur[f].desc = text; }
    else if (cur[f]) delete cur[f].desc;
    try { fs.writeFileSync(tp, JSON.stringify(cur, null, 1), "utf8"); return true; }
    catch (e) { NX.log("✖ Không lưu được mô tả: " + e.message, "#e5534b"); return false; }
  }
  function editDesc(el) {
    var f = el.getAttribute("data-file"), D = load(); if (!f || !D) return;
    var t = (D.brollTags[f] || {}).desc || "", d = descOf(D, f);
    var inp = document.createElement("textarea");
    inp.className = "cl-desc-in"; inp.rows = 2; inp.value = t;
    inp.placeholder = (d && d.src !== "note" ? d.text.replace(/^“|”$/g, "") + "  (gõ để thay bằng mô tả của bạn)" : "Mô tả cảnh: ai, làm gì, ở đâu, cỡ cảnh…") ;
    el.replaceWith(inp); inp.focus();
    var done = false; editingDesc = true;
    function finish(save) {
      if (done) return; done = true; editingDesc = false;
      if (save && inp.value.trim() !== t.trim()) {
        if (saveNote(f, inp.value)) NX.log(inp.value.trim() ? "✏️ Đã lưu mô tả: " + path.basename(f) : "✏️ Đã xoá mô tả: " + path.basename(f), "#6fd08c");
      }
      scheduleRender(10);
    }
    inp.onkeydown = function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); finish(true); }
      else if (e.key === "Escape") { e.preventDefault(); finish(false); }
    };
    inp.onblur = function () { finish(true); };
  }

  // ── Segments of long clips (nxcore.segments + per-segment descriptions) ─────────────────────────────────────
  var openSegs = {};
  var KIND = { stable: "tĩnh", handheld: "cầm tay", slowmo: "slow-mo", fast: "lia nhanh", shaky: "rung" };
  function segKey(s) { return s.k || (Number(s.a).toFixed(1) + "-" + Number(s.b).toFixed(1)); }
  function segsOf(D, f) { var c = D.segs && D.segs[f]; return (c && c.segs) || []; }
  function segList(D, f) {
    var sl = segsOf(D, f), ad = (D.autoDesc[f] || {}).segs || {}, marks = ((D.brollTags || {})[f] || {}).segs_mark || {};
    return '<div class="cl-segs">' + sl.map(function (s) {
      var k = segKey(s), d = (ad[k] || {}).desc, mk = marks[k] || "";
      return '<div class="sg' + (mk ? " " + mk : "") + '" data-file="' + esc(f) + '" data-k="' + esc(k) + '" data-a="' + s.a + '" data-b="' + s.b + '">' +
        '<div class="sg-th" title="Bấm để mở đoạn này trong Source Monitor (đã đánh In/Out)">' +
          (window.JESThumbs ? JESThumbs.img(f, (s.a + s.b) / 2) : "") + '<span class="sg-dur">' + (s.b - s.a).toFixed(0) + "s</span></div>" +
        '<div class="sg-main"><div class="sg-top"><b>' + hms(s.a) + "–" + hms(s.b) + "</b>" +
          (KIND[s.kind] ? ' <span class="bdg">' + KIND[s.kind] + "</span>" : "") +
          (s.speech > 0.5 ? ' <span class="bdg t">💬 thoại</span>' : "") +
          (s.cut ? ' <span class="bdg" title="Bắt đầu ở chỗ đổi cảnh trong cùng take">✂ cảnh mới</span>' : "") +
          (mk === "pin" ? ' <span class="bdg g">📌 ưu tiên</span>' : mk === "ban" ? ' <span class="bdg r">🚫 không dùng</span>' : "") +
          (s.q != null ? ' <span class="sg-q" style="color:' + col(s.q) + '" title="Độ ổn định của đoạn">' + s.q + "</span>" : "") + "</div>" +
          '<div class="sg-desc">' + (d ? "🤖 " + esc(d) : '<span class="muted">chưa mô tả — 🤖 sẽ chạy nền</span>') + "</div></div>" +
        '<div class="sg-act"><button class="btn sg-mk' + (mk === "pin" ? " on" : "") + '" data-mk="pin" title="Ưu tiên dùng đoạn này khi tự chọn B-roll">📌</button>' +
          '<button class="btn sg-mk' + (mk === "ban" ? " on" : "") + '" data-mk="ban" title="Không bao giờ dùng đoạn này">🚫</button></div></div>';
    }).join("") + "</div>";
  }
  function saveSegMark(f, k, mk) {
    var tp = W("broll_tags.json"), cur = rj(tp, {}) || {};
    cur[f] = cur[f] || { tags: [] };
    var m = cur[f].segs_mark || {};
    if (m[k] === mk) delete m[k]; else m[k] = mk;
    cur[f].segs_mark = m;
    try { fs.writeFileSync(tp, JSON.stringify(cur, null, 1), "utf8"); }
    catch (e) { NX.log("✖ Không lưu được: " + e.message, "#e5534b"); return; }
    NX.log((m[k] === "pin" ? "📌 Ưu tiên " : m[k] === "ban" ? "🚫 Bỏ " : "↺ Bỏ đánh dấu ") + path.basename(f) + " @" + k + "s", "#6fd08c");
    scheduleRender(10);
  }

  function fileUrl(p) { try { return toUrl(p).href; } catch (e) { return "file:///" + String(p).replace(/\\/g, "/"); } }
  var thCache = { dir: "", m: 0, set: null, n: -1 }, thOther = {}, renderN = 0;
  function hasThumbFile(rel, abs) {
    var r = String(rel).replace(/\\/g, "/");
    if (/^thumbs\/[^\/]+$/.test(r)) {
      var dir = W("thumbs");
      if (!(thCache.dir === dir && thCache.n === renderN && thCache.set)) {      // thumbs/ stat once per repaint
        var mt = -1; try { mt = fs.statSync(dir).mtimeMs; } catch (e) {}
        if (!(thCache.dir === dir && thCache.m === mt && thCache.set)) {         // listing only when the folder changed
          var set = {}; try { fs.readdirSync(dir).forEach(function (n) { set[n.toLowerCase()] = 1; }); } catch (e) {}
          thCache = { dir: dir, m: mt, set: set, n: renderN };
        } else thCache.n = renderN;
      }
      return !!thCache.set[r.slice(7).toLowerCase()];
    }
    var c = thOther[abs];                                     // custom thumb path: checked once per data change
    if (c && c.sig === lastSig) return c.ok;
    var ok = false; try { ok = fs.existsSync(abs); } catch (e) {}
    thOther[abs] = { sig: lastSig, ok: ok };
    return ok;
  }
  function clipRow(D, f) {
    var m = D.media[f] || {};
    var s = D.stab[f] || {}, c = s.composition || {}, ai = D.ai[f] || {};
    var brTag = (D.brollTags && D.brollTags[f]) || {};
    var roleData = (D.roles && D.roles[f]) || {};
    var role = m.role || brTag.role || roleData.role || "b_roll";

    // Thumbnail path (existence from one cached listing of thumbs/ — no fs call per row)
    var thumbRel = m.thumb || ("thumbs/" + stemOf(f) + ".jpg");
    var thumbAbs = W(thumbRel);
    var hasThumb = hasThumbFile(thumbRel, thumbAbs);
    var thumbUrl = hasThumb ? fileUrl(thumbAbs) : "";

    var allTags = [];
    // Role badge
    var roleBadge = "";
    if (role === "a_roll") {
      roleBadge = '<span class="bdg role-a" title="A-Roll (Thoại chính)">🎤 A-Roll</span>';
    } else if (role === "b_roll") {
      roleBadge = '<span class="bdg role-b" title="B-Roll (Cảnh phụ)">🎬 B-Roll</span>';
    } else if (role === "screen") {
      roleBadge = '<span class="bdg role-s" title="Màn hình">💻 Screen</span>';
    } else if (role === "audio_master") {
      roleBadge = '<span class="bdg role-m" title="Audio Mic">🎙 Mic</span>';
    }

    // Audio status
    if (!m.audio) {
      allTags.push('<span class="bdg mute" title="Không có âm thanh">🔇 Mute</span>');
    } else if (D.tr[stemOf(f)]) {
      allTags.push('<span class="bdg t" title="Đã có transcript lời thoại">💬 Thoại</span>');
    }

    // High FPS / Slow-mo
    if ((m.fps || 0) >= 95) {
      allTags.push('<span class="bdg sm" title="FPS 100/120p thích hợp Slow-mo">🐢 120p Slow-mo</span>');
    } else if ((m.fps || 0) >= 47) {
      allTags.push('<span class="bdg sm" title="FPS 50/60p thích hợp Slow-mo">🐢 60p Slow-mo</span>');
    }

    // Resolution badge if 4K
    if (m.res_label && m.res_label.indexOf("4K") >= 0) {
      allTags.push('<span class="bdg res" title="Độ phân giải 4K">4K UHD</span>');
    }

    // VFR warning
    if (m.vfr) {
      allTags.push('<span class="bdg w" title="VFR: Khung hình biến thiên">VFR</span>');
    }

    // Tilt / Rotation fix
    if (c.rotate_fix) {
      allTags.push('<span class="bdg w" title="Gợi ý xoay hình bù nghiêng">xoay ' + (c.rotate_fix > 0 ? "+" : "") + c.rotate_fix + '°</span>');
    }

    // Face detection
    if (c.faces) {
      allTags.push('<span class="bdg g" title="Phát hiện khuôn mặt trong khung hình">👤' + c.faces + '</span>');
    }

    // Shake ranges
    if ((s.ranges || []).length) {
      allTags.push('<span class="bdg r" title="Đoạn bị rung lắc">rung ' + s.ranges.length + '</span>');
    }

    // Semantic tags (from m.tags or brollTags or ai.tags)
    var rawTags = (m.tags || []).concat(brTag.tags || []).concat(ai.tags || []);
    var tagSeen = {};
    rawTags.forEach(function (t) {
      if (!t || typeof t !== "string") return;
      var clean = t.trim();
      var key = clean.toLowerCase();
      if (/^(a-roll|b-roll|màn hình|audio mic|mute|không tiếng|có tiếng|4k uhd|50\/60p slow-mo|100\/120p slow-mo)$/i.test(key)) return;
      if (!tagSeen[key]) {
        tagSeen[key] = 1;
        allTags.push('<span class="bdg tag" title="Nhãn cảnh quay">' + esc(clean) + '</span>');
      }
    });

    if (ai.use_as && !tagSeen[ai.use_as.toLowerCase()]) {
      allTags.push('<span class="bdg tag" title="Gợi ý dùng của AI">' + esc(ai.use_as) + '</span>');
    }

    var q = s.quality, name = f.split("/").pop();
    var tip = (s.stability != null ? "Ổn định " + s.stability + " · bố cục " + (c.score || "?") + " (nét " + (c.sharp || "?") + ", sáng " + (c.exposure || "?") + ", vị trí " + (c.placement || "?") + ")" : "");
    var strip = (s.strip || "").split("").map(function (ch) { return '<i class="s-' + (/^[shymfl]$/.test(ch) ? ch : "l") + '"></i>'; }).join("");

    var thumbHtml = '<div class="cl-thumb-wrap" data-file="' + esc(f) + '" title="Bấm để mở trong Source Monitor">' +
      (hasThumb ? '<img class="cl-thumb" src="' + esc(thumbUrl) + '" loading="lazy" alt="" />' : '<div class="cl-thumb-icon">🎬</div>') +
      '<span class="cl-thumb-dur">' + (m.dur || 0).toFixed(1) + 's</span>' +
      '<div class="cl-thumb-play">▶</div>' +
      '</div>';

    var dsc = descOf(D, f);
    var descHtml = dsc
      ? '<div class="cl-desc d-' + dsc.src + '" data-file="' + esc(f) + '" title="' + esc(dsc.tip + " — bấm để " + (dsc.src === "note" ? "sửa" : "viết mô tả của bạn") + "\n\n" + dsc.text) + '">' +
          '<span class="ic">' + dsc.ic + '</span>' + esc(dsc.text) + '</div>'
      : '<div class="cl-desc empty" data-file="' + esc(f) + '" title="Chưa có mô tả — bấm để tự viết, hoặc chạy ✨ Phân tích AI để tạo tự động">+ Thêm mô tả</div>';

    var nSeg = segsOf(D, f).length;
    var segBadge = nSeg >= 2 ? '<span class="bdg segbtn' + (openSegs[f] ? " on" : "") + '" data-file="' + esc(f) +
      '" title="Clip dài được tách thành ' + nSeg + ' đoạn dùng được — 1 clip có thể cho nhiều shot B-roll. Bấm để xem từng đoạn.">' +
      (openSegs[f] ? "▾ " : "▸ ") + nSeg + " đoạn</span>" : "";
    var tagsHtml = '<div class="cl-tags">' + roleBadge + segBadge + allTags.slice(0, 5).join("") +
      (allTags.length > 5 ? '<span class="bdg tag-more" title="' + esc(rawTags.join(", ")) + '">+' + (allTags.length - 5) + '</span>' : '') +
      '</div>';

    return '<div class="cl" title="' + esc(tip) + '">' +
      thumbHtml +
      '<div class="cl-main">' +
        '<div class="cl-header">' +
          '<div class="cl-title"><span class="nm">' + esc(name) + '</span> <span class="cl-specs">' + (m.dur || 0).toFixed(1) + 's · ' + m.w + '×' + m.h + ' · ' + (m.fps || 0).toFixed(0) + 'p' + (m.cam && m.cam !== "Khác" ? ' · ' + esc(m.cam) : '') + '</span></div>' +
          '<div class="cl-score" style="color:' + col(q) + '">' + (q != null ? q : "–") + '</div>' +
        '</div>' +
        descHtml +
        tagsHtml +
        (strip ? '<div class="strip">' + strip + '</div>' : '') +
      '</div>' +
    '</div>' + (nSeg >= 2 && openSegs[f] ? segList(D, f) : "");
  }

  var dirty = true, lastSig = "";
  function visible() { var r = $("run"); return !!(r && r.classList.contains("on")); }
  function render() {
    var box = $("dash"); if (!box) return;
    if (editingDesc) { scheduleRender(700); return; }      // don't wipe a description being typed
    if (!visible()) { dirty = true; return; }              // v1.8: hidden tab → paint later, when it is opened
    dirty = false; lastSig = dataSig(); renderN++;
    var D = load();
    if (!D) {
      box.innerHTML = '<div class="empty">' + (S().src ? "Folder này chưa quét. Bấm <b>Quét</b> (đọc cả thư mục con, vài giây) hoặc <b>1 · Phân tích</b>." :
        "Chọn folder source rồi bấm <b>Quét</b> — tool đọc cả thư mục con, thống kê clip, thời lượng, độ ổn định, cảnh báo.") + "</div>";
      $("srcMeta").textContent = ""; pipe(null, null); return;
    }
    var st = stats(D);
    var tri0 = rjc(W("triage.json"), null), exN = 0, brN = 0;
    if (tri0) { var all0 = rjc(W("media_all.json"), {}); brN = Object.keys(tri0.broken || {}).length; exN = Math.max(0, Object.keys(all0).length - st.n - brN); }
    $("srcMeta").textContent = st.n + " clip · " + Object.keys(st.folders).length + " folder" + (exN ? " · loại " + exN : "") + (brN ? " · lỗi " + brN : "") +
      (bg.proc && bg.n ? " · 🤖 mô tả " + bg.i + "/" + bg.n : "");
    var pct = function (a, b) { return b ? Math.round(a / b * 100) : 0; };
    var kp = [[st.n, "clip"], [hms(st.dur), "thời lượng"], [Object.keys(st.folders).length, "folder"],
              [pct(st.tr, st.audio) + "%", "đã transcribe", pct(st.tr, st.audio)], [st.qn ? Math.round(st.qsum / st.qn) : "–", "chất lượng TB", st.qn ? st.qsum / st.qn : 0],
              [st.vfr.length + st.shaky.length + st.tilt.length, "cảnh báo"]];
    var html = '<div class="kpis">' + kp.map(function (k) { return '<div class="kpi"><b>' + k[0] + "</b><span>" + k[1] + "</span>" + (k[2] != null ? '<i style="width:' + Math.min(100, k[2]) + '%"></i>' : "") + "</div>"; }).join("") + "</div>";
    html += triageHtml();
    html += insights(D, st).map(function (x) { return '<div class="ins ' + x[0] + '"><span class="ic">' + x[1] + "</span><span>" + x[2] + "</span></div>"; }).join("");
    html += Object.keys(st.folders).map(function (fo) {
      var arr = st.folders[fo], dur = 0, tr = 0, au = 0, qc = 0, sb = 0, qs = 0, qn = 0;
      arr.forEach(function (f) { var m = D.media[f]; dur += m.dur || 0; if (m.audio) { au++; if (D.tr[stemOf(f)]) tr++; } if (D.qc[f]) qc++; var s = D.stab[f]; if (s && s.v >= 2) { sb++; qs += s.quality || 0; qn++; } });
      var n = arr.length, mini = '<span class="mini" title="transcribe · QC · ổn định"><i style="width:' + (au ? tr / au * 33.3 : 33.3) + '%;background:#4fd1c5"></i><i style="width:' + qc / n * 33.3 + '%;background:#a9c94a"></i><i style="width:' + sb / n * 33.4 + '%;background:#6fd08c"></i></span>';
      var q = qn ? Math.round(qs / qn) : null;
      return '<div class="fold' + (open[fo] ? " open" : "") + '" data-fo="' + esc(fo) + '"><div class="fh"><span class="car">▶</span><span class="fn">📂 ' + esc(fo) + '</span><span class="fm">' + n + " clip · " + hms(dur) + "</span>" + mini +
        '<span class="sc" style="color:' + col(q) + ';min-width:22px;text-align:right;font-weight:700">' + (q != null ? q : "–") + '</span></div><div class="clips">' +
        (open[fo] ? arr.map(function (f) { return clipRow(D, f); }).join("") : "") + "</div></div>";
    }).join("");
    var keep = {};                                           // live re-renders must not jump the clip lists back to the top
    Array.prototype.forEach.call(box.querySelectorAll(".fold"), function (fd) { var c = fd.querySelector(".clips"); if (c) keep[fd.getAttribute("data-fo")] = c.scrollTop; });
    box.innerHTML = html;
    Array.prototype.forEach.call(box.querySelectorAll(".fold"), function (fd) { var c = fd.querySelector(".clips"), k = fd.getAttribute("data-fo"); if (c && keep[k]) c.scrollTop = keep[k]; });
    Array.prototype.forEach.call(box.querySelectorAll(".tri .tchip"), function (b) {
      b.onclick = function () { if (NX.isBusy && NX.isBusy()) return NX.log("• Đang chạy — đổi nhóm sau khi xong", "#e0a82e"); toggleGroup(b.getAttribute("data-g")); };
    });
    Array.prototype.forEach.call(box.querySelectorAll(".fold > .fh"), function (h) {
      h.onclick = function () { var fo = h.parentNode.getAttribute("data-fo"); open[fo] = !open[fo]; render(); };
    });
    Array.prototype.forEach.call(box.querySelectorAll(".cl-thumb-wrap"), function (el) {
      el.onclick = function (ev) {
        ev.stopPropagation();
        var relPath = el.getAttribute("data-file");
        if (!relPath) return;
        var full = path.join(S().src || "", relPath).replace(/\\/g, "/");
        if (window.NX && window.NX.evalHost) {
          window.NX.evalHost("nxOpenInSource(" + JSON.stringify(full) + ", 0, 5)").then(function (r) {
            try { var j = JSON.parse(r); if (j.error) NX.log("✖ " + j.error, "#e5534b"); else NX.log("▶ Đã mở source: " + path.basename(relPath), "#6fd08c"); } catch (e) {}
          });
        }
      };
    });
    Array.prototype.forEach.call(box.querySelectorAll(".cl-desc"), function (el) {
      el.onclick = function (ev) { ev.stopPropagation(); editDesc(el); };
    });
    Array.prototype.forEach.call(box.querySelectorAll(".segbtn"), function (el) {
      el.onclick = function (ev) { ev.stopPropagation(); var f = el.getAttribute("data-file"); openSegs[f] = !openSegs[f]; render(); };
    });
    Array.prototype.forEach.call(box.querySelectorAll(".sg .sg-th"), function (el) {
      el.onclick = function (ev) {
        ev.stopPropagation();
        var row = el.parentNode, rel = row.getAttribute("data-file"), a = +row.getAttribute("data-a"), b = +row.getAttribute("data-b");
        var full = path.join(S().src || "", rel).replace(/\\/g, "/");
        NX.evalHost("nxOpenInSource(" + JSON.stringify(full) + ", " + a + ", " + b + ")").then(function (r) {
          try { var j = JSON.parse(r); if (j.error) NX.log("✖ " + j.error, "#e5534b"); else NX.log("▶ Mở " + path.basename(rel) + " @" + hms(a) + "–" + hms(b), "#6fd08c"); } catch (e) {}
        });
      };
    });
    Array.prototype.forEach.call(box.querySelectorAll(".sg .sg-mk"), function (el) {
      el.onclick = function (ev) {
        ev.stopPropagation();
        var row = el.parentNode.parentNode;
        saveSegMark(row.getAttribute("data-file"), row.getAttribute("data-k"), el.getAttribute("data-mk"));
      };
    });
    if (window.JESThumbs) window.JESThumbs.fill(box);
    pipe(D, st);
    if (NX.seqInfo) NX.seqInfo();
  }

  function pipe(D, st) {
    var set = function (id, cls) { var el = $(id); if (el) el.className = "pst" + (cls ? " " + cls : ""); };
    var an = D && st.n && st.qc >= st.n && st.stab >= st.n && st.tr >= st.audio;
    set("pst_an", lanes._running ? "run" : an ? "done" : "");
    var sc = S().scriptMode === "auto" ? (S().script && fs.existsSync(S().script)) : (S().script && fs.existsSync(S().script));
    set("pst_sc", sc ? "done" : "");
    var rp = NX.roughPlan && NX.roughPlan();
    set("pst_rc", rp && fs.existsSync(rp) ? "done" : "");
    var pp = NX.getPlanPath && NX.getPlanPath();
    if (pp && /_ROUGH(_v?\d+)?\.json$/i.test(pp)) pp = NX.outDir() + "/plan_" + NX.currentName() + ".json";   // a rough plan is not an aligned one
    set("pst_al", pp && fs.existsSync(pp) ? "done" : "");
    var xml = NX.outDir && (NX.outDir() + "/" + NX.currentName() + ".xml");
    set("pst_bu", xml && fs.existsSync(xml) ? "done" : "");
    $("pipeMeta").textContent = an ? "source sẵn sàng" : D ? "cần phân tích" : "";
  }

  var LANE = { probe: "Probe", thumbs: "Thumbnail", tags: "Gắn nhãn", triage: "Phân loại", transcribe: "Transcribe", qc: "QC hình/tiếng", stability: "Ổn định/bố cục", proxy: "Proxy AI", sheets: "Contact sheet", speech: "Lọc thoại" };
  function onStage(line) {
    var m = line.match(/^@(\w+) (?:(\d+)\/(\d+)|done (\S+)(?: (\S+))?)\s*(.*)$/);
    if (!m) return false;
    var name = m[1], box = $("lanes");
    if (!lanes[name]) {
      box.style.display = "grid";
      var el = document.createElement("div"); el.className = "lane";
      el.innerHTML = '<span class="lb">' + (LANE[name] || name) + '</span><div class="bar"><i></i></div><span class="n">…</span>';
      box.appendChild(el); lanes[name] = { el: el, t0: Date.now() };
    }
    var L = lanes[name];
    if (m[2] != null) {
      var i = +m[2], n = +m[3], f = n ? i / n : 0;
      L.el.querySelector("i").style.width = (f * 100) + "%";
      var eta = "";
      if (i > 1 && n > i) { var sp = (Date.now() - L.t0) / i; eta = " · ~" + Math.ceil(sp * (n - i) / 1000) + "s"; }
      L.el.querySelector(".n").textContent = n ? i + "/" + n + eta : "…";
      L.el.title = m[6] || "";
    } else {
      L.el.classList.add("done"); L.el.querySelector("i").style.width = "100%";
      L.el.querySelector(".n").textContent = (m[4] === "ok" ? "✓ " : "✖ ") + (m[5] || "");
    }
    if (name === "probe" && m[4]) scheduleRender(50);
    else softRender(1500);                                   // v1.8: repaint only if an output file actually changed
    return true;
  }
  function resetLanes() { lanes = { _running: true }; var b = $("lanes"); if (b) { b.innerHTML = ""; b.style.display = "none"; } pipe(load(), load() ? stats(load()) : null); }
  function endLanes() { lanes._running = false; scheduleRender(10); }
  function scheduleRender(ms) { clearTimeout(renderT); renderT = setTimeout(render, ms || 200); }
  var softT = null;
  function softRender(ms) {
    if (softT) return;
    softT = setTimeout(function () { softT = null; if (!visible()) { dirty = true; return; } if (dataSig() !== lastSig) render(); }, ms || 1500);
  }

  function scan() {
    if (!S().src) return Promise.reject(new Error("chưa chọn folder source"));
    resetLanes();
    var b = S().brief || {}, pf = ((b.platform || "") + " " + (b.notes || "")).toLowerCase(), args = ["-m", "nxcore.media", S().src];
    if (NX.seqSpec && (!S().seq || S().seq.ratio === "auto")) args.push("--target", "auto");        // frame follows the footage
    else if (S().seq && NX.seqSpec) { var q = NX.seqSpec(); args.push("--target", q.w + "x" + q.h); }   // chosen sequence frame
    else if (/9:16|dọc|tiktok|reel|short/.test(pf)) args.push("--target", "1080x1920");
    else if (/16:9|ngang|youtube|landscape/.test(pf)) args.push("--target", "1920x1080");
    else if (/1:1|vuông|square/.test(pf)) args.push("--target", "1080x1080");
    // other presets than vlog / event: ask once per folder whether the story follows the shooting order
    return (NX.askChrono ? NX.askChrono() : Promise.resolve()).then(function () {
      return NX.run(args, "Quét tổng quan source (định dạng · file lỗi · cả thư mục con)");
    }).then(function () { endLanes(); render(); }, function (e) { endLanes(); throw e; });
  }

  // ── Auto description in the background (nxcore.describe, local Ollama vision) ──────────────────────────────
  // Starts by itself whenever the scanned folder has clips without a description (right after Quét / Phân tích,
  // on opening the panel, on switching folder). Never blocks the panel buttons. Pauses while the panel runs a job
  // (whisper/QC need the GPU) and resumes when idle — results are cached per clip, so a pause loses nothing.
  var bg = { proc: null, src: "", i: 0, n: 0, tried: {}, rt: null, skipLogged: {} };
  function autoDescOn() { return S().autoDescribe !== false; }
  function missingDesc(D) {
    if (!D.hasSegs) return Object.keys(D.media).length;          // never segmented → describe also splits long clips
    var n = 0;
    Object.keys(D.media).forEach(function (f) {
      var a = D.autoDesc[f];
      if (!a || !a.desc || a.key !== D.media[f].key) { n++; return; }
      var sl = segsOf(D, f), have = a.segs || {};
      if (sl.length >= 2 && sl.some(function (s) { var k = segKey(s); return !(have[k] && have[k].desc); })) n++;
    });
    return n;
  }
  function descBtn() {
    var b = $("bAutoDesc"); if (!b) return;
    b.className = "btn" + (autoDescOn() ? " on" : "") + (bg.proc ? " run" : "");
    b.title = (autoDescOn() ? "🤖 Mô tả tự động: BẬT" : "🤖 Mô tả tự động: TẮT") +
      " — sau khi quét, AI local (Ollama) xem 3 khung hình mỗi clip và viết 1 câu mô tả. Chạy nền, miễn phí, có cache." +
      (bg.proc ? "\nĐang chạy: " + bg.i + "/" + bg.n : "") + "\nBấm để " + (autoDescOn() ? "tắt" : "bật") + ".";
  }
  function stopAutoDescribe() {
    if (!bg.proc) return;
    // kill the whole tree: on Windows "python" can be a launcher shim — kill() alone left the real describe running
    if (NX.killTree) NX.killTree(bg.proc, false);
    else { try { if (process.platform === "win32") require("child_process").spawn("taskkill", ["/T", "/F", "/PID", String(bg.proc.pid)], { windowsHide: true }); else bg.proc.kill(); }
           catch (e) { try { bg.proc.kill(); } catch (e2) {} } }
    bg.proc = null; descBtn();
  }
  function startAutoDescribe(force) {
    if (!autoDescOn() || bg.proc || !S().src || (NX.isBusy && NX.isBusy()) || editingDesc) return;
    var D = load(); if (!D) return;
    var miss = missingDesc(D), tkey = S().src + "|" + Object.keys(D.media).length + "|" + miss;
    if (!miss || (!force && bg.tried[tkey])) return;
    bg.tried[tkey] = 1;
    var cp = require("child_process"), src = S().src;
    var env = Object.assign({}, process.env, { PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" });
    var p;
    try { p = cp.spawn(S().python || "python", ["-m", "nxcore.describe", src, "--model", S().describeModel || "auto"],
                       { cwd: S().tools, env: env, windowsHide: true }); }
    catch (e) { return; }
    bg.proc = p; bg.src = src; bg.i = 0; bg.n = 0; descBtn();
    if (NX.trackChild) NX.trackChild(p);                      // killed with the panel
    var onLine = function (l) {
      var m;
      if ((m = l.match(/"stage":\s*"describe",\s*"total":\s*(\d+)/))) {
        bg.n = +m[1];
        if (bg.n) NX.log("🤖 Mô tả tự động " + bg.n + " clip — chạy nền, panel vẫn dùng bình thường", "#8b9099");
      } else if ((m = l.match(/^@describe (\d+)\/(\d+)/))) {
        bg.i = +m[1]; bg.n = +m[2]; descBtn();
        if (!bg.rt) bg.rt = setTimeout(function () { bg.rt = null; if (visible()) render(); else dirty = true; }, 5000);      // live fill, throttled
      } else if ((m = l.match(/^@describe done skip (\S+)/))) {
        var why = m[1].replace(/_/g, " ");
        if (/gpu busy|đang chạy/.test(why)) { bg.retry = Date.now() + 20000; return; }   // engine backed off on purpose → retry in 20 s
        if (!bg.skipLogged[why]) { bg.skipLogged[why] = 1; NX.log("🤖 Chưa mô tả tự động được: " + why, "#e0a82e"); }
      } else if (/^describe: /.test(l)) {
        NX.log("🤖 " + l.replace(/^describe: /, ""), "#6fd08c");
      }
    };
    // utf8 decoding on the stream + one carry-over buffer PER stream (stdout / stderr chunks interleave)
    var reader = function (stream) {
      var buf = ""; try { stream.setEncoding("utf8"); } catch (e) {}
      stream.on("data", function (d) { buf += d; var ls = buf.split(/\r\n|\n|\r/); buf = ls.pop(); ls.forEach(onLine); });
      stream.on("end", function () { if (buf) { onLine(buf); buf = ""; } });
    };
    reader(p.stdout); reader(p.stderr);
    p.on("error", function () { bg.proc = null; descBtn(); });
    p.on("close", function () { if (bg.proc === p) bg.proc = null; descBtn(); scheduleRender(50); });
  }
  function autoDescTick() {
    var busy = NX.isBusy && NX.isBusy();
    if (bg.proc && (busy || bg.src !== S().src || !autoDescOn())) {       // a job needs the GPU / folder changed / turned off
      var resume = bg.src === S().src && autoDescOn();
      stopAutoDescribe();
      if (resume) bg.tried = {};                                          // allow the restart once idle
    } else if (!bg.proc && !busy) {
      if (bg.retry && Date.now() > bg.retry) { bg.retry = 0; bg.tried = {}; }
      startAutoDescribe(false);
    }
  }

  function init() {
    if (!$("dash")) return;
    $("bScan").onclick = NX.guard(scan);
    if ($("bAutoDesc")) {
      $("bAutoDesc").onclick = function () {
        S().autoDescribe = !autoDescOn(); NX.save();
        if (autoDescOn()) { NX.log("🤖 Mô tả tự động: BẬT", "#6fd08c"); bg.tried = {}; startAutoDescribe(true); }
        else { stopAutoDescribe(); NX.log("🤖 Mô tả tự động: TẮT (mô tả đã có vẫn giữ)", "#8b9099"); }
        descBtn();
      };
      descBtn();
      setInterval(autoDescTick, 2000);
    }
    var srcEl = $("src"), t = null;
    srcEl.addEventListener("change", function () { scheduleRender(100); });
    srcEl.addEventListener("jes:set", function () { scheduleRender(100); });     // picked with "…" / set by the agent
    srcEl.addEventListener("input", function () { clearTimeout(t); t = setTimeout(render, 600); });
    $("status").addEventListener("dblclick", render);
    document.addEventListener("nxtab", function (e) {      // repaint only if something changed while the tab was hidden
      if (e.detail === "run" && (dirty || dataSig() !== lastSig)) scheduleRender(16);
    });
    render();
    if (NX.seqInfo) setTimeout(NX.seqInfo, 50);
  }

  window.JESDash = { render: render, onStage: onStage, resetLanes: resetLanes, endLanes: endLanes, scan: scan, stats: stats, load: load };
  init();
})();
