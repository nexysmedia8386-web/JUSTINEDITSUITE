/* Justin EDIT Suite — tab Chữ → 🎬 Studio chữ & đồ họa (UI). Model + rendering: js/studio_core.js.
   One editor for everything that goes over the picture: text (JIZURA's library, pinned per item), graphics
   (js/motion_tpl.js) and logos — on a mini timeline that mirrors the open sequence, a preview where items are
   dragged / scaled / rotated, a property panel, a library picker with thumbnails, a brand kit and undo.
   ▶ Đặt renders each item as ProRes 4444 + alpha and lays it at its own time on track (base + lane). */
(function () {
  "use strict";
  var NX = window.NX, C = window.JESStudioCore, M = window.JESMotionTpl;
  if (!NX || !C || !M) return;
  var fs = require("fs"), path = require("path"), cp = require("child_process");
  function $(id) { return document.getElementById(id); }
  function esc(s) { return NX.esc ? NX.esc(s) : String(s == null ? "" : s); }
  function S() { var s = NX.S.studio; if (!s) s = NX.S.studio = {}; return s; }
  var ST = { sel: null, t: 0, playing: false, pps: 40, boxes: [], ready: false, job: null, hist: [], redo: [] };
  var TAGS = [["", "Tất cả"], ["calm", "Nhẹ nhàng"], ["pop", "Sôi động"], ["glitch", "Glitch"], ["graphic", "Đồ họa"], ["editorial", "Biên tập"], ["emotional", "Cảm xúc"]];
  var GROUP_VI = { layout: "Bố cục chữ", enter: "Hiệu ứng vào", hold: "Khi đứng yên", exit: "Hiệu ứng ra", treat: "Xử lý chữ", decor: "Trang trí", cam: "Chuyển động khung", style: "Bảng màu" };
  var KIND_VI = { text: "Chữ", graphic: "Đồ họa", logo: "Logo" };
  function pretty(k) { return String(k).replace(/([a-z])([A-Z])/g, "$1 $2").replace(/_/g, " ").toLowerCase(); }
  function fmt(t) { t = Math.max(0, +t || 0); var m = Math.floor(t / 60), s = t - m * 60; return m + ":" + (s < 10 ? "0" : "") + s.toFixed(1); }
  function doc() { return C.doc(); }
  function items() { return doc() ? doc().items : []; }
  function sel() { return items().filter(function (x) { return x.id === ST.sel; })[0] || null; }

  // ------------------------------------------------------------------ undo
  function snap() { if (!doc()) return; ST.hist.push(JSON.stringify(doc().items)); if (ST.hist.length > 60) ST.hist.shift(); ST.redo = []; }
  function restore(json) { doc().items = JSON.parse(json); C.saveDoc(); if (!sel()) ST.sel = null; refresh(); }
  function undo() { if (!ST.hist.length) return; ST.redo.push(JSON.stringify(doc().items)); restore(ST.hist.pop()); }
  function redo() { if (!ST.redo.length) return; ST.hist.push(JSON.stringify(doc().items)); restore(ST.redo.pop()); }
  function changed(full) { C.saveDoc(); if (full) refresh(); else { draw(); timeline(); } }

  // ------------------------------------------------------------------ layout of the view
  function build() {
    var root = $("stRoot"); if (!root || root.dataset.built) return; root.dataset.built = "1";
    root.innerHTML =
      '<div class="card st-card">' +
        '<div class="st-bar"><button class="btn main" data-add="text" title="Thêm chữ tại thời điểm đang xem">＋ Chữ</button>' +
          '<button class="btn" data-add="graphic" title="Chọn mẫu đồ họa">＋ Đồ họa</button><button class="btn" data-add="logo">＋ Logo</button>' +
          '<button class="btn chip" id="stImpSub" title="Mỗi câu thoại của bản Build → một mục chữ kiểu phụ đề (làn 0)">⤓ Thoại</button>' +
          '<button class="btn chip" id="stImpHl" title="Highlight của plan → chữ nhấn theo ý nghĩa câu (làn 1)">⤓ Highlight</button>' +
          '<button class="btn chip" id="stBrandBtn">🎨 Brand kit</button>' +
          '<span class="st-seq muted" id="stSeq">…</span></div>' +
        '<div class="st-stage" id="stStage"><div class="st-frame" id="stFrame"><img id="stBg" alt=""><canvas id="stCv"></canvas><canvas id="stOv"></canvas></div></div>' +
        '<div class="row st-play"><button class="btn chip" id="stPlay" title="Phát / dừng (Space)">▶</button><span id="stTime" class="st-time">0:00.0</span>' +
          '<button class="btn chip" id="stSync" title="Nhảy tới vị trí playhead của Premiere">⇄ Playhead</button>' +
          '<label class="chk" title="Khung hình thật của clip V1 tại thời điểm đang xem (lấy từ file gốc)"><input type="checkbox" id="stShowBg"> Hình nền thật</label>' +
          '<span class="st-undo"><button class="btn chip" id="stUndo" title="Hoàn tác (Ctrl+Z)">↶</button><button class="btn chip" id="stRedo" title="Làm lại (Ctrl+Y)">↷</button></span></div>' +
        '<div class="st-tl" id="stTl"><div class="st-ruler" id="stRuler"></div><div class="st-lanes" id="stLanes"></div><div class="st-ph" id="stPh"></div></div>' +
        '<div class="row st-zoom"><span class="muted">Thu phóng</span><input type="range" id="stZoom" min="4" max="200" value="40"><button class="btn chip" id="stFit">Vừa khung</button>' +
          '<span class="muted" id="stCount"></span></div>' +
        '<div id="stInsp" class="st-insp"></div>' +
        '<div class="row st-foot"><label class="st-lbl">Track gốc <select id="stBase"><option value="2">V3</option><option value="3">V4</option><option value="4">V5</option><option value="5">V6</option></select></label>' +
          '<select id="stRes" title="Chất lượng render"><option value="seq">Nét (theo sequence)</option><option value="fast">Nhanh (1080p)</option></select>' +
          '<button class="btn" id="stPlaceSel">▶ Đặt mục chọn</button><button class="btn main" id="stPlaceAll">▶ Đặt tất cả</button>' +
          '<button class="btn" id="stCancel" style="display:none">Huỷ</button></div>' +
        '<div id="stProg" class="tfx-prog" style="display:none"><div class="bar"><i></i></div><span></span></div>' +
        '<div class="hint">Mỗi mục render thành clip ProRes 4444 nền trong suốt, đặt đúng thời điểm trên track gốc + làn (bin JES_Studio). ' +
          'Kéo trên khung xem trước để di chuyển, góc phải dưới để phóng, chấm trên để xoay. Phím: Space phát · Delete xóa · Ctrl+Z hoàn tác · mũi tên dịch.</div>' +
      '</div>' +
      '<div class="st-pick" id="stPick" style="display:none"></div>' +
      '<div class="st-pick" id="stBrand" style="display:none"></div>';
    Array.prototype.forEach.call(root.querySelectorAll("[data-add]"), function (b) { b.onclick = function () { add(b.dataset.add); }; });
    $("stImpSub").onclick = function () { importCues("sub"); };
    $("stImpHl").onclick = function () { importCues("hl"); };
    $("stBrandBtn").onclick = brandKit;
    $("stPlay").onclick = togglePlay;
    $("stSync").onclick = syncPlayhead;
    $("stUndo").onclick = undo; $("stRedo").onclick = redo;
    $("stShowBg").checked = !!S().showBg;
    $("stShowBg").onchange = function () { S().showBg = $("stShowBg").checked; NX.save(); bgFrame(); };
    $("stZoom").oninput = function () { ST.pps = +$("stZoom").value; S().pps = ST.pps; timeline(); };
    $("stFit").onclick = fitZoom;
    $("stBase").value = String(S().base != null ? S().base : 2); $("stBase").onchange = function () { S().base = +$("stBase").value; NX.save(); };
    $("stRes").value = S().res || "seq"; $("stRes").onchange = function () { S().res = $("stRes").value; NX.save(); };
    $("stPlaceSel").onclick = function () { var it = sel(); if (it) placeItems([it]); else NX.log("• Chọn một mục trên timeline trước", "#e0a82e"); };
    $("stPlaceAll").onclick = function () { if (items().length) placeItems(items().slice()); };
    $("stCancel").onclick = function () { if (ST.job) ST.job.cancel = true; };
    overlayEvents(); timelineEvents(); keys();
    window.addEventListener("resize", function () { if (visible()) { sizeStage(); draw(); timeline(); } });
  }
  function visible() { var hl = $("hl"), r = $("stRoot"); return !!(hl && hl.classList.contains("on") && r && r.style.display !== "none"); }

  // ------------------------------------------------------------------ open / sequence / document
  function open() {
    build();
    $("stSeq").textContent = "Đang nạp JIZURA…";
    return C.engine().then(function () { return C.readSeq(true).catch(function () { return C.seq(); }); }).then(function (q) {
      var name = q.live ? q.name : "chưa mở sequence";
      if (!doc() || doc().seq !== name) { C.loadDoc(name); ST.sel = null; ST.hist = []; ST.redo = []; }
      $("stSeq").textContent = q.live ? q.name + " · " + q.w + "×" + q.h + " @" + q.fps + " · " + fmt(q.dur) : "Chưa mở sequence — xem trước theo 16:9, mở sequence rồi bấm ⇄";
      if (!ST.ready) { ST.ready = true; ST.pps = S().pps || 0; if (!ST.pps) fitZoom(); else $("stZoom").value = String(ST.pps); }
      C.onFonts(draw);
      sizeStage(); refresh(); bgFrame();
    }).catch(function (e) { $("stSeq").textContent = "✖ " + e.message; });
  }
  function refresh() { draw(); timeline(); inspector(); $("stCount").textContent = items().length ? items().length + " mục" : ""; }
  function sizeStage() {
    var q = C.seq(), fr = $("stFrame"), st = $("stStage"); if (!fr) return;
    var maxW = st.clientWidth - 2, maxH = Math.max(160, Math.min(window.innerHeight * 0.42, 520));
    var w = maxW, h = w * q.h / q.w; if (h > maxH) { h = maxH; w = h * q.w / q.h; }
    fr.style.width = Math.round(w) + "px"; fr.style.height = Math.round(h) + "px";
    var dpr = Math.min(2, window.devicePixelRatio || 1);
    [$("stCv"), $("stOv")].forEach(function (c) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); });
  }

  // ------------------------------------------------------------------ preview
  var drawQ = 0;
  function draw() {
    if (drawQ) return;
    drawQ = requestAnimationFrame(function () {
      drawQ = 0;
      var cv = $("stCv"); if (!cv || !C.J()) return;
      ST.boxes = C.drawFrame(cv.getContext("2d"), items(), ST.t, cv.width, cv.height);
      overlay();
      $("stTime").textContent = fmt(ST.t) + " / " + fmt(C.seq().dur);
      var ph = $("stPh"); if (ph) ph.style.left = Math.round(ST.t * ST.pps) + "px";
    });
  }
  function overlay() {
    var ov = $("stOv"), x = ov.getContext("2d"); x.clearRect(0, 0, ov.width, ov.height);
    var b = ST.boxes.filter(function (q) { return q.id === ST.sel; })[0]; if (!b) return;
    var k = ov.width / $("stFrame").clientWidth;
    x.save(); x.translate(b.cx, b.cy); x.rotate(b.rot);
    x.strokeStyle = "#8ea9ff"; x.lineWidth = 1.5 * k; x.setLineDash([5 * k, 4 * k]); x.strokeRect(-b.w / 2, -b.h / 2, b.w, b.h); x.setLineDash([]);
    x.fillStyle = "#8ea9ff"; x.fillRect(b.w / 2 - 5 * k, b.h / 2 - 5 * k, 10 * k, 10 * k);                       // scale handle
    x.beginPath(); x.moveTo(0, -b.h / 2); x.lineTo(0, -b.h / 2 - 18 * k); x.stroke();
    x.beginPath(); x.arc(0, -b.h / 2 - 22 * k, 5 * k, 0, Math.PI * 2); x.fill();                                     // rotate handle
    x.restore();
  }
  function local(b, px, py) {             // point → box-local coordinates (undo the box rotation)
    var dx = px - b.cx, dy = py - b.cy, c = Math.cos(-b.rot), s = Math.sin(-b.rot);
    return { x: dx * c - dy * s, y: dx * s + dy * c };
  }
  function overlayEvents() {
    var ov = $("stOv"), drag = null;
    function pt(e) { var r = ov.getBoundingClientRect(), k = ov.width / r.width; return { x: (e.clientX - r.left) * k, y: (e.clientY - r.top) * k, k: k }; }
    ov.addEventListener("pointerdown", function (e) {
      var p = pt(e), it = sel(), b = ST.boxes.filter(function (q) { return q.id === ST.sel; })[0], mode = null;
      if (b && it) {
        var l = local(b, p.x, p.y);
        if (Math.abs(l.x - b.w / 2) < 9 * p.k && Math.abs(l.y - b.h / 2) < 9 * p.k) mode = "scale";
        else if (Math.abs(l.x) < 9 * p.k && Math.abs(l.y - (-b.h / 2 - 22 * p.k)) < 9 * p.k) mode = "rot";
      }
      if (!mode) {                                     // pick the top-most item under the pointer
        var hit = null;
        for (var i = ST.boxes.length - 1; i >= 0 && !hit; i--) { var q = ST.boxes[i], l2 = local(q, p.x, p.y); if (Math.abs(l2.x) <= q.w / 2 + 4 && Math.abs(l2.y) <= q.h / 2 + 4) hit = q; }
        if (!hit) { select(null); return; }
        if (hit.id !== ST.sel) select(hit.id);
        it = sel(); b = hit; mode = "move";
      }
      snap();
      drag = { mode: mode, p0: p, x0: +it.x || 0, y0: +it.y || 0, s0: +it.s || 1, r0: +it.rot || 0, b: b, it: it };
      ov.setPointerCapture(e.pointerId); e.preventDefault();
    });
    ov.addEventListener("pointermove", function (e) {
      if (!drag) return;
      var p = pt(e), it = drag.it, W = ov.width, H = ov.height;
      if (drag.mode === "move") {
        it.x = Math.round((drag.x0 + (p.x - drag.p0.x) / W) * 1000) / 1000; it.y = Math.round((drag.y0 + (p.y - drag.p0.y) / H) * 1000) / 1000;
        if (!e.altKey) { if (Math.abs(it.x) < 0.006) it.x = 0; if (Math.abs(it.y) < 0.006) it.y = 0; }      // snaps back to the template's own spot
      } else if (drag.mode === "scale") {
        var d0 = Math.hypot(drag.p0.x - drag.b.cx, drag.p0.y - drag.b.cy), d1 = Math.hypot(p.x - drag.b.cx, p.y - drag.b.cy);
        it.s = Math.round(Math.max(0.1, Math.min(6, drag.s0 * d1 / Math.max(1, d0))) * 100) / 100;
      } else {
        var a = Math.atan2(p.y - drag.b.cy, p.x - drag.b.cx) * 180 / Math.PI + 90;
        it.rot = e.shiftKey ? Math.round(a / 15) * 15 : Math.round(a);
        if (it.rot > 180) it.rot -= 360;
      }
      draw(); inspSync();
    });
    ov.addEventListener("pointerup", function () { if (drag) { drag = null; C.saveDoc(); inspector(); } });
  }

  // ------------------------------------------------------------------ real picture under the preview (V1 at time t)
  var bgT = 0, bgCache = {};
  function bgFrame() {
    var img = $("stBg"); if (!img) return;
    if (!S().showBg) { img.style.display = "none"; return; }
    clearTimeout(bgT);
    bgT = setTimeout(function () {
      var q = C.seq(), clip = null;
      ((q.video || [])[0] || { clips: [] }).clips.forEach(function (c) { if (ST.t >= c.start && ST.t < c.end && c.media && c.enabled !== false) clip = c; });
      if (!clip) { img.style.display = "none"; return; }
      var at = Math.max(0, clip["in"] + (ST.t - clip.start)), key = clip.media + "@" + at.toFixed(1);
      if (bgCache[key]) { img.src = bgCache[key]; img.style.display = ""; return; }
      var out = path.join(require("os").tmpdir(), "jes_studio_bg.jpg");
      cp.execFile(NX.S.ffmpeg || "ffmpeg", ["-y", "-v", "error", "-ss", at.toFixed(3), "-i", clip.media, "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "4", out],
        { windowsHide: true, timeout: 15000 }, function (err) {
          if (err) { img.style.display = "none"; return; }
          try { bgCache[key] = "data:image/jpeg;base64," + fs.readFileSync(out).toString("base64"); img.src = bgCache[key]; img.style.display = ""; } catch (e) {}
          var ks = Object.keys(bgCache); if (ks.length > 40) delete bgCache[ks[0]];
        });
    }, ST.playing ? 1e9 : 220);
  }

  // ------------------------------------------------------------------ playback + time
  var raf = 0, last = 0;
  function setTime(t) { ST.t = Math.max(0, Math.min(C.seq().dur, t)); draw(); if (!ST.playing) bgFrame(); }
  function togglePlay() {
    ST.playing = !ST.playing; $("stPlay").textContent = ST.playing ? "⏸" : "▶";
    if (ST.playing) { last = performance.now(); raf = requestAnimationFrame(tick); } else { cancelAnimationFrame(raf); bgFrame(); }
  }
  function tick(now) {
    if (!ST.playing || !visible()) { ST.playing = false; $("stPlay").textContent = "▶"; return; }
    ST.t += (now - last) / 1000; last = now;
    if (ST.t >= C.seq().dur) ST.t = 0;
    draw(); raf = requestAnimationFrame(tick);
  }
  function syncPlayhead() { C.readSeq(true).then(function (q) { setTime(q.playhead); $("stSeq").textContent = q.name + " · " + q.w + "×" + q.h + " @" + q.fps + " · " + fmt(q.dur); }, function (e) { NX.log("✖ " + e.message, "#e5534b"); }); }

  // ------------------------------------------------------------------ mini timeline
  function fitZoom() {
    var tl = $("stTl"); if (!tl) return;
    var end = Math.max(C.seq().dur, items().reduce(function (m, x) { return Math.max(m, x.t0 + x.dur); }, 0) + 2);
    ST.pps = Math.max(4, Math.min(200, (tl.clientWidth - 20) / end)); $("stZoom").value = String(Math.round(ST.pps)); S().pps = ST.pps; timeline();
  }
  function timeline() {
    var lanesEl = $("stLanes"), ru = $("stRuler"); if (!lanesEl) return;
    var q = C.seq(), end = Math.max(q.dur, items().reduce(function (m, x) { return Math.max(m, x.t0 + x.dur); }, 0)) + 4, W = Math.ceil(end * ST.pps);
    var nl = Math.max(2, items().reduce(function (m, x) { return Math.max(m, (x.lane | 0) + 2); }, 0));
    var step = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300].filter(function (s) { return s * ST.pps >= 46; })[0] || 600, tk = [];
    for (var s = 0; s <= end; s += step) tk.push('<i style="left:' + Math.round(s * ST.pps) + 'px">' + fmt(s).replace(/\.0$/, "") + "</i>");
    ru.innerHTML = tk.join(""); ru.style.width = W + "px";
    var html = [];
    for (var l = 0; l < nl; l++) html.push('<div class="st-lane" data-l="' + l + '" style="width:' + W + 'px"><span class="st-ln">V' + ((+S().base || 2) + 1 + l) + "</span></div>");
    lanesEl.innerHTML = html.join(""); lanesEl.style.width = W + "px";
    items().forEach(function (it) {
      var lane = lanesEl.children[it.lane | 0]; if (!lane) return;
      var d = document.createElement("div");
      d.className = "st-item k-" + it.kind + (it.id === ST.sel ? " on" : "") + (it.placed ? " placed" : "");
      d.dataset.id = it.id; d.style.left = Math.round(it.t0 * ST.pps) + "px"; d.style.width = Math.max(6, Math.round(it.dur * ST.pps)) + "px";
      d.title = C.itemLabel(it) + " · " + fmt(it.t0) + " → " + fmt(it.t0 + it.dur) + (it.placed ? " · đã đặt " + it.placed.when.slice(11, 16) : "");
      d.innerHTML = '<b class="h l"></b><span>' + esc(C.itemLabel(it)) + '</span><b class="h r"></b>';
      lane.appendChild(d);
    });
    $("stPh").style.left = Math.round(ST.t * ST.pps) + "px";
  }
  function timelineEvents() {
    var tl = $("stTl"), drag = null;
    tl.addEventListener("pointerdown", function (e) {
      var lanesEl = $("stLanes"), r = lanesEl.getBoundingClientRect(), x = e.clientX - r.left;
      var el = e.target.closest(".st-item");
      if (!el) { setTime(x / ST.pps); return; }                          // empty space / ruler → move the time
      var it = items().filter(function (q) { return q.id === el.dataset.id; })[0]; if (!it) return;
      if (it.id !== ST.sel) select(it.id);
      var mode = e.target.classList.contains("l") ? "trimL" : e.target.classList.contains("r") ? "trimR" : "move";
      snap();
      drag = { it: it, mode: mode, x0: e.clientX, y0: e.clientY, t0: it.t0, d0: it.dur, l0: it.lane | 0, moved: false };
      tl.setPointerCapture(e.pointerId); e.preventDefault();
    });
    tl.addEventListener("pointermove", function (e) {
      if (!drag) return;
      var it = drag.it, dt = (e.clientX - drag.x0) / ST.pps;
      if (Math.abs(e.clientX - drag.x0) + Math.abs(e.clientY - drag.y0) > 3) drag.moved = true;
      if (!drag.moved) return;
      var snapT = function (t) {                      // magnet: playhead + other items' edges within 6 px, else 0.1 s
        var best = Math.round(t * 10) / 10, bd = 6 / ST.pps;
        [ST.t].concat(items().filter(function (o) { return o !== it; }).reduce(function (a, o) { a.push(o.t0, o.t0 + o.dur); return a; }, [])).forEach(function (c) {
          if (Math.abs(c - t) < bd) { bd = Math.abs(c - t); best = c; }
        });
        return Math.max(0, best);
      };
      if (drag.mode === "move") {
        var t0 = snapT(drag.t0 + dt), e0 = snapT(drag.t0 + dt + it.dur) - it.dur;
        it.t0 = Math.abs(e0 - (drag.t0 + dt)) < Math.abs(t0 - (drag.t0 + dt)) ? Math.max(0, e0) : t0;
        it.lane = Math.max(0, Math.min(11, drag.l0 + Math.round((e.clientY - drag.y0) / 26)));
      } else if (drag.mode === "trimL") {
        var nt = Math.min(drag.t0 + drag.d0 - 0.3, snapT(drag.t0 + dt)); it.dur = Math.round((drag.t0 + drag.d0 - nt) * 100) / 100; it.t0 = nt;
      } else it.dur = Math.max(0.3, Math.round((snapT(drag.t0 + drag.d0 + dt) - drag.t0) * 100) / 100);
      timeline(); draw();
    });
    tl.addEventListener("pointerup", function () {
      if (!drag) return;
      var d = drag; drag = null;
      if (!d.moved) { ST.hist.pop(); return; }
      var it = d.it, clash = items().some(function (o) { return o !== it && o.lane === it.lane && o.t0 < it.t0 + it.dur - 1e-3 && it.t0 < o.t0 + o.dur - 1e-3; });
      if (clash) it.lane = C.freeLane(it.t0, it.dur, it.id);     // no two items overlap on one track
      changed(true);
    });
    tl.addEventListener("dblclick", function (e) {
      var el = e.target.closest(".st-item"); var it = el && items().filter(function (q) { return q.id === el.dataset.id; })[0];
      if (it) NX.evalHost("jesSetPlayhead(" + it.t0 + ")");
    });
  }

  // ------------------------------------------------------------------ add / import / select / delete
  function add(kind) {
    if (!doc()) return;
    if (kind === "graphic") return pickGraphic();
    var it = C.newItem(kind, { t0: Math.round(ST.t * 10) / 10 });
    it.lane = C.freeLane(it.t0, it.dur);
    snap(); doc().items.push(it); select(it.id); changed(true);
    if (kind === "text") setTimeout(function () { var ta = $("stTxt"); if (ta) { ta.focus(); ta.select(); } }, 0);
  }
  function addGraphic(tpl, preset) {
    var it = preset ? Object.assign(JSON.parse(JSON.stringify(preset.item)), { id: C.newItem("text").id, t0: Math.round(ST.t * 10) / 10 }) : C.newItem("graphic", { tpl: tpl, t0: Math.round(ST.t * 10) / 10 });
    it.dur = it.dur || (M.TEMPLATES[tpl] ? M.TEMPLATES[tpl].dur : 4);
    delete it.placed; it.lane = C.freeLane(it.t0, it.dur);
    snap(); doc().items.push(it); select(it.id); changed(true);
  }
  function importCues(kind) {
    var list = C.fromCues(kind);
    if (!list.length) { NX.log(kind === "sub" ? "• Chưa có phụ đề — bấm Build (bước 3) trước" : "• Chưa có highlight có timing — Build lại để có file .text.json", "#e0a82e"); return; }
    snap();
    list.forEach(function (it) { if (items().some(function (o) { return o.lane === it.lane && o.t0 < it.t0 + it.dur && it.t0 < o.t0 + o.dur; })) it.lane = C.freeLane(it.t0, it.dur); doc().items.push(it); });
    NX.log("✔ Studio: " + list.length + (kind === "sub" ? " câu thoại" : " highlight") + " thành mục chữ — chỉnh từng mục hoặc đặt tất cả", "#6fd08c");
    fitZoom(); changed(true);
  }
  function select(id) { ST.sel = id; timeline(); inspector(); draw(); }
  function remove(it) { snap(); doc().items = items().filter(function (x) { return x !== it; }); ST.sel = null; changed(true); }
  function duplicate(it) {
    var c = JSON.parse(JSON.stringify(it)); c.id = C.newItem("text").id; delete c.placed; c.t0 = Math.round((it.t0 + it.dur) * 10) / 10;
    c.lane = C.freeLane(c.t0, c.dur); snap(); doc().items.push(c); select(c.id); changed(true);
  }
  function keys() {
    document.addEventListener("keydown", function (e) {
      if (!visible() || /^(INPUT|TEXTAREA|SELECT)$/.test((e.target || {}).tagName) || (e.target && e.target.isContentEditable)) return;
      if ($("stPick").style.display !== "none" || $("stBrand").style.display !== "none") return;
      var it = sel();
      if (e.key === " ") { e.preventDefault(); togglePlay(); }
      else if ((e.key === "Delete" || e.key === "Backspace") && it) { e.preventDefault(); remove(it); }
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); }
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "y") { e.preventDefault(); redo(); }
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "d" && it) { e.preventDefault(); duplicate(it); }
      else if (it && /^Arrow/.test(e.key)) {
        e.preventDefault(); snap(); var st = e.shiftKey ? 0.02 : 0.004;
        if (e.key === "ArrowLeft") it.x = +(it.x - st).toFixed(3); if (e.key === "ArrowRight") it.x = +(it.x + st).toFixed(3);
        if (e.key === "ArrowUp") it.y = +(it.y - st).toFixed(3); if (e.key === "ArrowDown") it.y = +(it.y + st).toFixed(3);
        changed(); inspSync();
      }
    });
  }

  // ------------------------------------------------------------------ inspector
  var TABS = { text: [["content", "Nội dung"], ["look", "Kiểu"], ["motion", "Chuyển động"], ["place", "Vị trí"]],
               graphic: [["content", "Nội dung"], ["place", "Vị trí"]], logo: [["content", "Logo"], ["place", "Vị trí"]] };
  function inspector() {
    var el = $("stInsp"); if (!el) return;
    var it = sel();
    if (!it) { el.innerHTML = '<div class="muted st-empty">' + (items().length ? "Bấm một mục trên timeline hoặc trên khung xem trước để chỉnh." :
      "Chưa có mục nào — bấm ＋ Chữ / ＋ Đồ họa / ＋ Logo, hoặc ⤓ Thoại / ⤓ Highlight để lấy từ bản Build.") + "</div>"; return; }
    var tab = S().tab && TABS[it.kind].some(function (t) { return t[0] === S().tab; }) ? S().tab : TABS[it.kind][0][0];
    el.innerHTML = '<div class="st-ih"><span class="st-kind k-' + it.kind + '">' + KIND_VI[it.kind] + '</span><b>' + esc(C.itemLabel(it)) + "</b>" +
      '<span class="st-ia"><button class="btn chip" data-a="dup" title="Nhân bản (Ctrl+D)">⧉</button><button class="btn chip" data-a="preset" title="Lưu làm mẫu của tôi">☆ Mẫu</button>' +
      '<button class="btn chip" data-a="place" title="Render + đặt riêng mục này">▶</button><button class="btn chip" data-a="del" title="Xóa (Delete)">✕</button></span></div>' +
      '<div class="sc-seg st-tabs">' + TABS[it.kind].map(function (t) { return '<button data-t="' + t[0] + '"' + (t[0] === tab ? ' class="on"' : "") + ">" + t[1] + "</button>"; }).join("") + "</div>" +
      '<div class="st-pane">' + pane(it, tab) + "</div>" +
      '<div class="row st-time-row"><label>Bắt đầu (s)<input type="number" step="0.1" min="0" data-k="t0" value="' + it.t0 + '"></label>' +
      '<label>Dài (s)<input type="number" step="0.1" min="0.3" data-k="dur" value="' + it.dur + '"></label>' +
      '<label>Làn<input type="number" step="1" min="0" max="11" data-k="lane" value="' + (it.lane | 0) + '"></label></div>';
    Array.prototype.forEach.call(el.querySelectorAll(".st-tabs button"), function (b) { b.onclick = function () { S().tab = b.dataset.t; inspector(); }; });
    Array.prototype.forEach.call(el.querySelectorAll("[data-a]"), function (b) {
      b.onclick = function () {
        var a = b.dataset.a;
        if (a === "dup") duplicate(it); else if (a === "del") remove(it); else if (a === "place") placeItems([it]); else if (a === "preset") savePreset(it);
      };
    });
    bindInputs(el, it);
  }
  function num(k, label, v, step, min, max) {
    return '<label>' + label + '<input type="number" data-k="' + k + '" value="' + v + '" step="' + step + '"' + (min != null ? ' min="' + min + '"' : "") + (max != null ? ' max="' + max + '"' : "") + "></label>";
  }
  function range(k, label, v, min, max, step) {
    return '<label class="st-rng">' + label + ' <output>' + v + '</output><input type="range" data-k="' + k + '" value="' + v + '" min="' + min + '" max="' + max + '" step="' + step + '"></label>';
  }
  function pickBtn(group, label, val) {
    return '<label>' + label + '<button class="btn st-pickb" data-pick="' + group + '">' + esc(val && val.length !== 0 ? (Array.isArray(val) ? val.map(pretty).join(", ") : pretty(val)) : "—") + " ▾</button></label>";
  }
  function colorIn(k, label, v, def) {
    return '<label class="st-col">' + label + '<span class="row"><input type="color" data-k="' + k + '" value="' + esc(v || def) + '">' +
      '<button class="btn chip" data-clear="' + k + '" title="Về màu của bảng màu">↺</button>' + (v ? "" : '<i class="muted">theo bảng màu</i>') + "</span></label>";
  }
  function pane(it, tab) {
    var J = C.J();
    if (tab === "place") {
      var A = ["tl", "tc", "tr", "cl", "c", "cr", "bl", "bc", "br"];
      return '<div class="st-grid3">' + A.map(function (a) { return '<button class="btn chip" data-anchor="' + a + '" title="Đưa tới ' + a + '">' + { tl: "↖", tc: "↑", tr: "↗", cl: "←", c: "•", cr: "→", bl: "↙", bc: "↓", br: "↘" }[a] + "</button>"; }).join("") + "</div>" +
        '<div class="st-form">' + num("x", "Ngang (% khung)", Math.round(it.x * 1000) / 10, 0.5) + num("y", "Dọc (% khung)", Math.round(it.y * 1000) / 10, 0.5) +
        num("s", "Cỡ (×)", it.s, 0.05, 0.1, 6) + num("rot", "Xoay (°)", it.rot, 1, -180, 180) + range("op", "Độ đậm", it.op, 0, 1, 0.05) + "</div>" +
        '<div class="row"><button class="btn chip" data-reset="pos">Về vị trí gốc</button></div>';
    }
    if (it.kind === "logo") {
      return '<div class="st-form"><label class="st-wide">File logo (PNG nền trong / SVG)<span class="row"><input data-k="file" value="' + esc(it.file) + '" style="flex:1"><button class="btn chip" data-file="file">Chọn…</button></span></label>' +
        '<label>Góc<select data-k="anchor">' + Object.keys(C.ANCHORS).map(function (a) { return '<option value="' + a + '"' + (a === it.anchor ? " selected" : "") + ">" + { tl: "Trên trái", tc: "Trên giữa", tr: "Trên phải", cl: "Giữa trái", c: "Giữa", cr: "Giữa phải", bl: "Dưới trái", bc: "Dưới giữa", br: "Dưới phải" }[a] + "</option>"; }).join("") + "</select></label>" +
        range("size", "Cỡ (% cạnh ngắn)", it.size, 0.04, 0.6, 0.01) +
        sel3("ain", "Vào", it.ain, [["pop", "Bật lên"], ["fade", "Hiện dần"], ["slide", "Trượt vào"], ["spin", "Xoay vào"], ["zoom", "Phóng vào"]]) +
        sel3("idle", "Khi đứng", it.idle, [["none", "Đứng yên"], ["float", "Bồng bềnh"], ["pulse", "Nhịp đập"], ["shine", "Vệt sáng"]]) +
        sel3("aout", "Ra", it.aout, [["fade", "Mờ dần"], ["shrink", "Thu nhỏ"], ["slide", "Trượt ra"], ["none", "Cắt"]]) +
        '<label class="st-wide">Chữ dưới logo<input data-k="caption" value="' + esc(it.caption) + '"></label></div>';
    }
    if (it.kind === "graphic") {
      var T = M.TEMPLATES[it.tpl], f = it.fields || {};
      return '<div class="st-form"><label class="st-wide">Mẫu<button class="btn st-pickb" data-pick="graphic">' + esc(T ? T.name : it.tpl) + " ▾</button></label>" +
        T.fields.map(function (fd) {
          var v = f[fd.k] != null ? f[fd.k] : fd.def;
          if (fd.type === "select") return '<label>' + esc(fd.label) + '<select data-f="' + fd.k + '">' + fd.opts.map(function (o) { return '<option value="' + o[0] + '"' + (String(v) === o[0] ? " selected" : "") + ">" + esc(o[1]) + "</option>"; }).join("") + "</select></label>";
          if (fd.type === "color") return '<label>' + esc(fd.label) + '<input type="color" data-f="' + fd.k + '" value="' + esc(v) + '"></label>';
          if (fd.type === "file") return '<label class="st-wide">' + esc(fd.label) + '<span class="row"><input data-f="' + fd.k + '" value="' + esc(v) + '" style="flex:1"><button class="btn chip" data-ffile="' + fd.k + '">Chọn…</button></span></label>';
          return '<label' + (fd.type === "num" || /^(value|prefix|suffix|kicker|done)$/.test(fd.k) ? "" : ' class="st-wide"') + ">" + esc(fd.label) + '<input data-f="' + fd.k + '"' + (fd.type === "num" ? ' type="number"' : "") + ' value="' + esc(v) + '"></label>';
        }).join("") + range("gscale", "Cỡ mẫu (×)", it.gscale || 1, 0.4, 2, 0.05) +
        colorIn("f_primary", "Màu hộp", f.primary, C.brand().primary) + colorIn("f_accent", "Màu nhấn", f.accent, C.brand().accent) + colorIn("f_ink", "Màu chữ", f.ink, C.brand().ink) + "</div>";
    }
    // ---- text
    if (tab === "content") {
      var words = String(it.text || "").split(/\s+/).filter(Boolean);
      return '<textarea id="stTxt" data-k="text" rows="2">' + esc(it.text) + "</textarea>" +
        '<div class="st-words" title="Bấm chữ để nhấn mạnh (JIZURA tô màu / phóng to từ được nhấn)">' + words.map(function (w) {
          var on = (it.emph || []).indexOf(w) >= 0; return '<button class="btn chip' + (on ? " on" : "") + '" data-w="' + esc(w) + '">' + esc(w) + "</button>"; }).join("") + "</div>" +
        '<label class="chk"><input type="checkbox" data-k="impact"' + (it.impact ? " checked" : "") + "> Câu nhấn mạnh (impact: đậm hơn, hiệu ứng mạnh hơn)</label>" +
        '<div class="st-form">' + pickBtn("layout", "Bố cục", it.layout) + pickBtn("treat", "Xử lý chữ", it.treat) + pickBtn("decor", "Trang trí", it.decor) + "</div>";
    }
    if (tab === "look") {
      var faces = (J && J.LANG_FACES && J.LANG_FACES.vi && J.LANG_FACES.vi.map) || {};
      return '<div class="st-form">' + pickBtn("style", "Bảng màu", it.style) +
        '<label>Font<select data-k="font"><option value="">Theo bảng màu</option>' + Object.keys(faces).map(function (k) {
          return '<option value="' + k + '"' + (k === it.font ? " selected" : "") + ">" + esc(faces[k].family + " " + faces[k].weight) + "</option>"; }).join("") + "</select></label>" +
        colorIn("fg", "Màu chữ", it.fg, "#ffffff") + colorIn("accent", "Màu nhấn", it.accent, "#ffc83d") + colorIn("box", "Màu nền hộp", it.box, "#111111") +
        range("chroma", "Lệch màu (ghost)", it.chroma, 0, 1, 0.05) + range("glitch", "Glitch", it.glitch, 0, 1, 0.05) +
        '<label class="st-wide">Đổi biến thể<button class="btn chip" data-reseed="1" title="Cùng lựa chọn, khác chi tiết ngẫu nhiên (vị trí phụ, decor…)">🎲 Ngẫu nhiên lại</button></label></div>';
    }
    // motion
    return '<div class="st-form">' + pickBtn("enter", "Vào", it.enter) + pickBtn("hold", "Khi đứng", it.hold) + pickBtn("exit", "Ra", it.exit) + pickBtn("cam", "Chuyển động khung", it.cam) +
      num("inDur", "Thời gian vào (s, 0 = tự)", it.inDur || 0, 0.05, 0, 3) + num("outDur", "Thời gian ra (s, 0 = tự)", it.outDur || 0, 0.05, 0, 3) +
      range("motion", "Biên độ chuyển động", it.motion, 0, 1, 0.05) +
      '<label class="chk st-wide"><input type="checkbox" data-k="koma"' + (+it.koma ? " checked" : "") + "> Giật kiểu hoạt hình (12 hình/giây)</label></div>";
  }
  function sel3(k, label, v, opts) {
    return '<label>' + label + '<select data-k="' + k + '">' + opts.map(function (o) { return '<option value="' + o[0] + '"' + (o[0] === v ? " selected" : "") + ">" + o[1] + "</option>"; }).join("") + "</select></label>";
  }
  function bindInputs(el, it) {
    var commit = function (full) { C.saveDoc(); if (full) refresh(); else { draw(); timeline(); } };
    Array.prototype.forEach.call(el.querySelectorAll("[data-k]"), function (inp) {
      var k = inp.dataset.k, first = true;
      var apply = function (fin) {
        if (first) { snap(); first = false; }
        var v = inp.type === "checkbox" ? inp.checked : inp.value;
        if (k === "koma") v = inp.checked ? 12 : 0;
        else if (k === "impact") v = !!inp.checked;
        else if (/^(t0|dur|lane|s|rot|op|size|motion|glitch|chroma|inDur|outDur|gscale)$/.test(k)) v = +v || 0;
        else if (k === "x" || k === "y") v = (+v || 0) / 100;
        if (k === "lane") v = Math.max(0, Math.min(11, v | 0));
        if (k === "dur") v = Math.max(0.3, v);
        if (/^f_/.test(k)) { it.fields = it.fields || {}; it.fields[k.slice(2)] = v; }
        else it[k] = v;
        var o = inp.parentNode.querySelector("output"); if (o) o.textContent = v;
        if (k === "text" && fin) { it.emph = (it.emph || []).filter(function (w) { return String(it.text).indexOf(w) >= 0; }); }
        commit(fin && (k === "text" || k === "lane"));
      };
      inp.addEventListener("input", function () { apply(false); });
      inp.addEventListener("change", function () { apply(true); first = true; });
    });
    Array.prototype.forEach.call(el.querySelectorAll("[data-f]"), function (inp) {
      var first = true;
      inp.addEventListener("input", function () { if (first) { snap(); first = false; } it.fields = it.fields || {}; it.fields[inp.dataset.f] = inp.value; commit(false); });
      inp.addEventListener("change", function () { first = true; });
    });
    Array.prototype.forEach.call(el.querySelectorAll("[data-clear]"), function (b) {
      b.onclick = function () { snap(); var k = b.dataset.clear; if (/^f_/.test(k)) { if (it.fields) delete it.fields[k.slice(2)]; } else it[k] = ""; commit(true); };
    });
    Array.prototype.forEach.call(el.querySelectorAll("[data-w]"), function (b) {
      b.onclick = function () { snap(); var w = b.dataset.w, e = it.emph = it.emph || [], i = e.indexOf(w); if (i >= 0) e.splice(i, 1); else e.push(w); commit(true); };
    });
    Array.prototype.forEach.call(el.querySelectorAll("[data-pick]"), function (b) { b.onclick = function () { picker(b.dataset.pick, it); }; });
    Array.prototype.forEach.call(el.querySelectorAll("[data-anchor]"), function (b) { b.onclick = function () { snap(); anchorTo(it, b.dataset.anchor); commit(true); }; });
    Array.prototype.forEach.call(el.querySelectorAll("[data-reset]"), function (b) { b.onclick = function () { snap(); it.x = 0; it.y = 0; it.s = 1; it.rot = 0; commit(true); }; });
    Array.prototype.forEach.call(el.querySelectorAll("[data-reseed]"), function (b) { b.onclick = function () { snap(); it.seed = (Math.random() * 1e9) | 0; commit(true); }; });
    Array.prototype.forEach.call(el.querySelectorAll("[data-file],[data-ffile]"), function (b) {
      b.onclick = function () {
        var r = window.cep.fs.showOpenDialogEx(false, false, "Chọn logo / ảnh", "", ["png", "webp", "jpg", "jpeg", "svg"]);
        if (!r || !r.data || !r.data.length) return;
        snap(); var f = r.data[0].replace(/\\/g, "/");
        if (b.dataset.file) it[b.dataset.file] = f; else { it.fields = it.fields || {}; it.fields[b.dataset.ffile] = f; }
        C.image(f, function () { draw(); }); commit(true);
      };
    });
  }
  function inspSync() {                                     // live numbers while dragging on the preview
    var it = sel(), el = $("stInsp"); if (!it || !el) return;
    [["x", Math.round(it.x * 1000) / 10], ["y", Math.round(it.y * 1000) / 10], ["s", it.s], ["rot", it.rot]].forEach(function (p) {
      var inp = el.querySelector('[data-k="' + p[0] + '"]'); if (inp && document.activeElement !== inp) inp.value = p[1];
    });
  }
  /* put the item's drawn box at a frame anchor (5 % safe margin), keeping its scale */
  function anchorTo(it, a) {
    var bb = C.bbox(it), w = (bb.x1 - bb.x0) * (it.s || 1), h = (bb.y1 - bb.y0) * (it.s || 1), A = C.ANCHORS[a], m = 0.05;
    var tx = m + w / 2 + A[0] * (1 - 2 * m - w), ty = m + h / 2 + A[1] * (1 - 2 * m - h);
    it.x = Math.round((tx - bb.cx) * 1000) / 1000; it.y = Math.round((ty - bb.cy) * 1000) / 1000;
  }

  // ------------------------------------------------------------------ library picker (JIZURA groups + graphics + presets)
  var thumbQ = [], thumbBusy = false;
  function thumbs() {
    if (thumbBusy) return; thumbBusy = true;
    (function run() {
      var t0 = performance.now();
      while (thumbQ.length && performance.now() - t0 < 24) { var j = thumbQ.shift(); try { j(); } catch (e) {} }
      if (thumbQ.length) requestAnimationFrame(run); else thumbBusy = false;
    })();
  }
  function renderThumb(cv, item, t) {
    var q = C.seq(), w = 192, h = Math.max(1, Math.round(192 * q.h / q.w));
    var L = C.mkCanvas(w, h);
    C.drawLayer(item, t != null ? t : C.restTime(item), L);
    cv.width = w; cv.height = h; cv.getContext("2d").drawImage(L, 0, 0);
  }
  function picker(group, it) {
    var J = C.J(), box = $("stPick"), multi = group === "decor", anim = /^(enter|hold|exit|cam)$/.test(group);
    var keysOf = group === "style" ? Object.keys(J.STYLES) : group === "graphic" ? Object.keys(M.TEMPLATES) : J.order(group);
    var reg = group === "style" ? J.STYLES : group === "graphic" ? M.TEMPLATES : J.registry(group);
    var cur = group === "graphic" ? it.tpl : it[group], tag = "", q = "";
    box.innerHTML = '<div class="st-pk"><div class="st-pkh"><b>' + (group === "graphic" ? "Mẫu đồ họa" : GROUP_VI[group]) + '</b><span class="muted"> · ' + keysOf.length + " lựa chọn" + (multi ? " · chọn nhiều" : "") + "</span>" +
      '<input id="stPkQ" placeholder="Tìm…"><button class="btn main" id="stPkDone">Xong</button></div>' +
      (group === "graphic" || group === "style" ? "" : '<div class="st-tags">' + TAGS.map(function (t) { return '<button class="btn chip' + (t[0] === "" ? " on" : "") + '" data-tag="' + t[0] + '">' + t[1] + "</button>"; }).join("") + "</div>") +
      (anim ? '<div class="st-pkprev"><canvas id="stPkCv"></canvas><span class="muted" id="stPkName">Rê chuột lên hiệu ứng để xem trước</span></div>' : "") +
      '<div class="st-pkgrid" id="stPkGrid"></div></div>';
    box.style.display = "";
    var grid = $("stPkGrid"), pv = $("stPkCv"), pvRaf = 0, pvKey = null, pvT0 = 0;
    function apply(k) {
      snap();
      if (group === "graphic") { it.tpl = k; it.fields = {}; it.dur = it.dur || M.TEMPLATES[k].dur; }
      else if (multi) { var a = it.decor = (it.decor || []).slice(), i = a.indexOf(k); if (i >= 0) a.splice(i, 1); else a.push(k); }
      else it[group] = k;
      cur = group === "graphic" ? it.tpl : it[group];
      Array.prototype.forEach.call(grid.querySelectorAll(".st-tile"), function (t) { t.classList.toggle("on", multi ? (it.decor || []).indexOf(t.dataset.k) >= 0 : t.dataset.k === cur); });
      C.saveDoc(); draw();
    }
    function preview(k) {                               // animated preview of an effect on THIS item
      if (!pv) return;
      pvKey = k; pvT0 = performance.now(); $("stPkName").textContent = pretty(k);
      if (pvRaf) return;
      var probe = JSON.parse(JSON.stringify(it)); probe.t0 = 0;
      (function loop(now) {
        if (box.style.display === "none") { pvRaf = 0; return; }
        probe[group] = pvKey; probe.dur = Math.max(2.2, it.dur);
        var q2 = C.seq(), w = pv.clientWidth || 320, h = Math.round(w * q2.h / q2.w);
        if (pv.width !== w) { pv.width = w; pv.height = h; }
        var lt = ((now - pvT0) / 1000) % (probe.dur + 0.4);
        pv.getContext("2d").clearRect(0, 0, w, h);
        try { C.composite(pv.getContext("2d"), probe, lt, w, h, "pk"); } catch (e) {}
        pvRaf = requestAnimationFrame(loop);
      })(performance.now());
    }
    function list() {
      thumbQ.length = 0;
      var ks = keysOf.filter(function (k) {
        var d = reg[k] || {};
        if (tag && !(d.tags || []).some(function (x) { return x === tag; })) return false;
        return !q || (k + " " + pretty(k) + " " + (d.name || "")).toLowerCase().indexOf(q) >= 0;
      });
      grid.innerHTML = ks.map(function (k) {
        var on = multi ? (it.decor || []).indexOf(k) >= 0 : k === cur, d = reg[k] || {};
        var label = group === "graphic" ? d.name : group === "style" ? pretty(k) : pretty(k);
        var sw = group === "style" ? '<span class="st-sw">' + (d.schemes || []).slice(0, 3).map(function (s) { return '<i style="background:' + s.bg + ';color:' + s.fg + '">A<em style="background:' + s.accent + '"></em></i>'; }).join("") + "</span>" : "";
        return '<button class="st-tile' + (on ? " on" : "") + (anim ? " txt" : "") + '" data-k="' + k + '" title="' + esc(k + (d.tags ? " · " + d.tags.join(", ") : "")) + '">' +
          (anim || group === "style" ? "" : "<canvas></canvas>") + sw + "<span>" + esc(label) + "</span></button>";
      }).join("") || '<div class="muted">Không có lựa chọn khớp.</div>';
      Array.prototype.forEach.call(grid.querySelectorAll(".st-tile"), function (t) {
        var k = t.dataset.k;
        t.onclick = function () { apply(k); if (!multi && !anim) { /* stays open for comparing; Xong closes */ } };
        if (anim) t.onmouseenter = function () { preview(k); };
        var cv = t.querySelector("canvas");
        if (cv) thumbQ.push(function () {
          var probe = JSON.parse(JSON.stringify(it)); probe.t0 = 0;
          if (group === "graphic") { probe = C.newItem("graphic", { tpl: k, dur: M.TEMPLATES[k].dur }); }
          else if (group === "decor") probe.decor = [k]; else probe[group] = k;
          if (group === "layout" || group === "treat" || group === "decor") probe.dur = 3;
          renderThumb(cv, probe);
        });
      });
      thumbs();
      if (anim && cur) preview(cur);
    }
    Array.prototype.forEach.call(box.querySelectorAll("[data-tag]"), function (b) {
      b.onclick = function () { tag = b.dataset.tag; Array.prototype.forEach.call(box.querySelectorAll("[data-tag]"), function (x) { x.classList.toggle("on", x === b); }); list(); };
    });
    $("stPkQ").oninput = function () { q = $("stPkQ").value.trim().toLowerCase(); list(); };
    $("stPkDone").onclick = function () { box.style.display = "none"; thumbQ.length = 0; inspector(); timeline(); };
    list();
  }
  /* ＋ Đồ họa: the template gallery + "Mẫu của tôi" (saved presets of any kind) */
  function pickGraphic() {
    var box = $("stPick"), P = C.brand().presets || [];
    box.innerHTML = '<div class="st-pk"><div class="st-pkh"><b>Thêm đồ họa</b><span class="muted"> · bấm để thêm tại ' + fmt(ST.t) + '</span><button class="btn" id="stPkDone">Đóng</button></div>' +
      '<div class="st-pkgrid" id="stPkGrid">' + Object.keys(M.TEMPLATES).map(function (k) { return '<button class="st-tile" data-g="' + k + '"><canvas></canvas><span>' + esc(M.TEMPLATES[k].name) + "</span></button>"; }).join("") + "</div>" +
      (P.length ? '<div class="st-pkh"><b>☆ Mẫu của tôi</b></div><div class="st-pkgrid" id="stPkMine">' + P.map(function (p, i) {
        return '<button class="st-tile" data-p="' + i + '"><canvas></canvas><span>' + esc(p.name) + '</span><i class="st-tdel" data-del="' + i + '" title="Xóa mẫu">✕</i></button>'; }).join("") + "</div>" : "") + "</div>";
    box.style.display = "";
    $("stPkDone").onclick = function () { box.style.display = "none"; };
    Array.prototype.forEach.call(box.querySelectorAll("[data-g]"), function (t) {
      t.onclick = function () { box.style.display = "none"; addGraphic(t.dataset.g); };
      var cv = t.querySelector("canvas"), k = t.dataset.g; thumbQ.push(function () { renderThumb(cv, C.newItem("graphic", { tpl: k, dur: M.TEMPLATES[k].dur })); });
    });
    Array.prototype.forEach.call(box.querySelectorAll("[data-p]"), function (t) {
      var p = P[+t.dataset.p];
      t.onclick = function (e) {
        if (e.target.dataset.del != null) { e.stopPropagation(); P.splice(+e.target.dataset.del, 1); C.saveBrand({ presets: P }); pickGraphic(); return; }
        box.style.display = "none"; addGraphic(p.item.tpl, p);
      };
      var cv = t.querySelector("canvas"); thumbQ.push(function () { var pr = JSON.parse(JSON.stringify(p.item)); pr.t0 = 0; renderThumb(cv, pr); });
    });
    thumbs();
  }
  function savePreset(it) {
    NX.ask("Tên mẫu (dùng lại cho mọi video, lưu trong brand kit):", [], { text: true, placeholder: C.itemLabel(it) }).then(function (name) {
      if (!name) return;
      var item = JSON.parse(JSON.stringify(it)); delete item.placed; item.t0 = 0; item.lane = 0;
      var P = (C.brand().presets || []).slice(); P.push({ name: name, kind: it.kind, item: item, at: new Date().toISOString() });
      C.saveBrand({ presets: P }); NX.log("☆ Đã lưu mẫu: " + name + " (＋ Đồ họa → Mẫu của tôi)", "#6fd08c");
    });
  }

  // ------------------------------------------------------------------ brand kit
  function brandKit() {
    var box = $("stBrand"), b = C.brand(), J = C.J(), faces = (J && J.LANG_FACES && J.LANG_FACES.vi && J.LANG_FACES.vi.map) || {};
    box.innerHTML = '<div class="st-pk"><div class="st-pkh"><b>🎨 Brand kit</b><span class="muted"> · mặc định cho mọi mục mới + đồ họa</span><button class="btn main" id="stBkDone">Xong</button></div>' +
      '<div class="st-form">' +
      '<label>Màu hộp<input type="color" data-b="primary" value="' + esc(b.primary) + '"></label><label>Màu nhấn<input type="color" data-b="accent" value="' + esc(b.accent) + '"></label>' +
      '<label>Màu chữ<input type="color" data-b="ink" value="' + esc(b.ink) + '"></label>' +
      '<label>Bảng màu chữ<select data-b="textStyle">' + Object.keys(J.STYLES).map(function (k) { return '<option value="' + k + '"' + (k === b.textStyle ? " selected" : "") + ">" + pretty(k) + "</option>"; }).join("") + "</select></label>" +
      '<label>Font chữ<select data-b="font"><option value="">Theo bảng màu</option>' + Object.keys(faces).map(function (k) { return '<option value="' + k + '"' + (k === b.font ? " selected" : "") + ">" + esc(faces[k].family + " " + faces[k].weight) + "</option>"; }).join("") + "</select></label>" +
      '<label>Xử lý chữ<select data-b="treat">' + J.order("treat").map(function (k) { return '<option value="' + k + '"' + (k === b.treat ? " selected" : "") + ">" + pretty(k) + "</option>"; }).join("") + "</select></label>" +
      '<label class="st-wide">Logo kênh<span class="row"><input data-b="logo" value="' + esc(b.logo) + '" style="flex:1"><button class="btn chip" id="stBkLogo">Chọn…</button></span></label>' +
      '<label>Góc logo<select data-b="logoAnchor">' + Object.keys(C.ANCHORS).map(function (a) { return '<option value="' + a + '"' + (a === b.logoAnchor ? " selected" : "") + ">" + a + "</option>"; }).join("") + "</select></label>" +
      "</div>" +
      '<div class="row" style="gap:6px;margin-top:8px"><button class="btn chip" id="stBkExp">Xuất brand kit…</button><button class="btn chip" id="stBkImp">Nhập brand kit…</button>' +
      '<span class="muted">' + (b.presets || []).length + " mẫu của tôi · file " + esc(path.join(NX.cfgDir, "brandkit.json")) + "</span></div></div>";
    box.style.display = "";
    Array.prototype.forEach.call(box.querySelectorAll("[data-b]"), function (inp) { inp.onchange = function () { var p = {}; p[inp.dataset.b] = inp.value; C.saveBrand(p); draw(); }; });
    $("stBkLogo").onclick = function () {
      var r = window.cep.fs.showOpenDialogEx(false, false, "Chọn logo kênh", "", ["png", "webp", "svg", "jpg", "jpeg"]);
      if (r && r.data && r.data.length) { C.saveBrand({ logo: r.data[0].replace(/\\/g, "/") }); brandKit(); }
    };
    $("stBkExp").onclick = function () {
      var r = window.cep.fs.showSaveDialogEx("Xuất brand kit", "", ["json"], "brandkit.json");
      if (r && r.data) { fs.writeFileSync(r.data, JSON.stringify(C.brand(), null, 1), "utf8"); NX.log("✔ Đã xuất brand kit: " + r.data, "#6fd08c"); }
    };
    $("stBkImp").onclick = function () {
      var r = window.cep.fs.showOpenDialogEx(false, false, "Nhập brand kit", "", ["json"]);
      if (!r || !r.data || !r.data.length) return;
      try { var j = JSON.parse(fs.readFileSync(r.data[0], "utf8")); C.saveBrand(j); brandKit(); draw(); NX.log("✔ Đã nhập brand kit", "#6fd08c"); }
      catch (e) { NX.log("✖ File brand kit lỗi: " + e.message, "#e5534b"); }
    };
    $("stBkDone").onclick = function () { box.style.display = "none"; };
  }

  // ------------------------------------------------------------------ render + place
  function prog(f, label) {
    var b = $("stProg"); if (!b) return;
    if (f < 0) { b.style.display = "none"; return; }
    b.style.display = "flex"; b.querySelector("i").style.width = Math.round(f * 100) + "%"; b.querySelector("span").textContent = label || "";
  }
  function placeItems(list) {
    if (ST.job) return;
    var job = ST.job = { cancel: false };
    $("stCancel").style.display = ""; $("stPlaceAll").disabled = $("stPlaceSel").disabled = true;
    C.place(list, { base: +$("stBase").value, fast: $("stRes").value === "fast", onProgress: prog, cancelled: function () { return job.cancel; } })
      .then(function () { C.saveDoc(true); timeline(); }, function (e) { NX.log("✖ Studio: " + (e && e.message || e), "#e5534b"); })
      .then(function () { ST.job = null; prog(-1); $("stCancel").style.display = "none"; $("stPlaceAll").disabled = $("stPlaceSel").disabled = false; });
  }

  // ------------------------------------------------------------------ view switch inside tab Chữ
  function setView(v) {
    S().view = v; NX.save();
    Array.prototype.forEach.call(document.querySelectorAll("#hlMode button"), function (b) { b.classList.toggle("on", b.dataset.m === v); });
    $("stRoot").style.display = v === "studio" ? "" : "none";
    $("tfxCard").style.display = v === "studio" ? "none" : "";
    if (v === "studio") open(); else try { window.dispatchEvent(new Event("resize")); } catch (e) {}     // JIZURA iframe re-fits
  }
  function init() {
    if (!$("stRoot") || !$("hlMode")) return;
    Array.prototype.forEach.call(document.querySelectorAll("#hlMode button"), function (b) { b.onclick = function () { setView(b.dataset.m); }; });
    document.addEventListener("nxtab", function (e) { if (e.detail === "hl") setView(S().view === "jizura" ? "jizura" : "studio"); else if (ST.playing) togglePlay(); });
    document.addEventListener("jes:brand", function () { if (ST.ready) draw(); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
  window.JESStudio = { open: open, setView: setView, add: add, select: select, items: items, setTime: setTime, refresh: refresh, placeItems: placeItems };
})();
