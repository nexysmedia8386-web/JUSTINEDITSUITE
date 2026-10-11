/* Justin EDIT Suite — tab Chữ → ✦ Đồ họa: motion graphics library.
   Templates (js/motion_tpl.js: lower third, logo, counter, callout, chapter title, subscribe) are drawn on a canvas
   frame by frame and piped to ffmpeg as ProRes 4444 WITH ALPHA — the same path JIZURA's text uses — then placed at the
   playhead on a video track above the edit. Nothing to install: no After Effects, no headless browser.
   Defaults come from the brand colours here and from the style book (📒 mg_scale / mg_dur); AI Assist can place one
   too (tool "motion_graphic" → JESMotion.place). */
(function () {
  "use strict";
  var NX = window.NX, M = window.JESMotionTpl;
  if (!NX || !M) return;
  var fs = require("fs"), path = require("path"), cp = require("child_process"), Buf = require("buffer").Buffer;
  function $(id) { return document.getElementById(id); }
  function esc(s) { return NX.esc ? NX.esc(s) : String(s == null ? "" : s); }
  function S() {
    var s = NX.S.motion;
    if (!s) s = NX.S.motion = {};
    s.vals = s.vals || {}; s.brand = s.brand || {};
    if (!M.TEMPLATES[s.tpl]) s.tpl = "lower_third";
    return s;
  }
  function rule(k) { return window.JESStyle ? window.JESStyle.param(k) : undefined; }
  var seq = { w: 1920, h: 1080, fps: 25, playhead: 0, name: "", live: false };
  var job = null, started = false;

  // ------------------------------------------------------------------ fonts + logo images
  var fontP = null;
  function fonts() {
    if (fontP) return fontP;
    if (!document.querySelector('link[data-jes-font]')) {
      var l = document.createElement("link"); l.rel = "stylesheet"; l.setAttribute("data-jes-font", "1");
      l.href = "https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@500;700;800;900&display=swap";
      document.head.appendChild(l);
    }
    var load = Promise.all(["500", "700", "800", "900"].map(function (w) { return document.fonts.load(w + ' 40px "Be Vietnam Pro"'); }));
    // offline: Segoe UI (Windows) / Helvetica (macOS) also carry every Vietnamese diacritic — never block longer than 4 s
    fontP = Promise.race([load, new Promise(function (r) { setTimeout(r, 4000); })]).then(function () {}, function () {});
    return fontP;
  }
  var imgs = {};
  function logoImg(file, cb) {
    if (!file) return null;
    var c = imgs[file];
    if (!c) {
      c = imgs[file] = { img: new Image(), ok: false, wait: [] };
      try {
        var ext = (path.extname(file).slice(1) || "png").toLowerCase();
        var mime = /jpe?g/.test(ext) ? "image/jpeg" : ext === "svg" ? "image/svg+xml" : ext === "webp" ? "image/webp" : "image/png";
        c.img.onload = function () { c.ok = true; c.wait.splice(0).forEach(function (f) { f(); }); };
        c.img.onerror = function () { c.err = "không đọc được ảnh"; c.wait.splice(0).forEach(function (f) { f(); }); };
        // a data: URL keeps the canvas clean (a file:// image would taint it and getImageData would refuse to render)
        c.img.src = "data:" + mime + ";base64," + fs.readFileSync(file).toString("base64");
      } catch (e) { c.err = e.message; }
    }
    if (cb) { if (c.ok || c.err) cb(); else c.wait.push(cb); }
    return c.ok ? c.img : null;
  }
  function waitLogo(file) { return new Promise(function (r) { if (!file) return r(); logoImg(file, r); }); }

  // ------------------------------------------------------------------ parameters of the current template
  function paramsFor(id, extra) {
    var T = M.TEMPLATES[id], v = Object.assign({}, S().vals[id] || {}, extra || {}), over = Object.assign({}, S().brand, v);
    if (over.dur === undefined || over.dur === "") over.dur = rule("mg_dur") || T.dur;
    if (over.scale === undefined || over.scale === "") over.scale = rule("mg_scale") || 1;
    var p = M.params(id, over);
    if (id === "logo_pop" && p.logo) p._img = logoImg(p.logo);
    return p;
  }
  function readSeq() {
    return NX.evalHost("jesSequence(1)").then(function (r) {
      var j = JSON.parse(r); if (!j.ok) throw new Error(j.error || "chưa mở sequence");
      seq = { w: j.w, h: j.h, fps: j.fps || 25, playhead: +j.playhead || 0, name: j.name, live: true };
      return seq;
    }).catch(function (e) { seq.live = false; throw e; });
  }

  // ------------------------------------------------------------------ UI
  function gallery() {
    var g = $("mgGal"); if (!g) return;
    g.innerHTML = Object.keys(M.TEMPLATES).map(function (id) {
      return '<button class="mg-tile' + (id === S().tpl ? " on" : "") + '" data-id="' + id + '"><canvas width="192" height="108"></canvas><span>' + esc(M.TEMPLATES[id].name) + "</span></button>";
    }).join("");
    Array.prototype.forEach.call(g.querySelectorAll(".mg-tile"), function (b) {
      var id = b.dataset.id, p = paramsFor(id), c = b.querySelector("canvas");
      M.draw(c.getContext("2d"), id, Math.min(p.dur - 0.3, 1.6), p, 192, 108);
      b.onclick = function () { S().tpl = id; NX.save(); gallery(); form(); restart(); };
    });
  }
  function field(f, v, id) {
    var key = id + "." + f.k, val = v[f.k] !== undefined ? v[f.k] : f.def;
    if (f.type === "select") return '<label>' + esc(f.label) + '<select data-k="' + f.k + '">' + f.opts.map(function (o) {
      return '<option value="' + o[0] + '"' + (String(val) === o[0] ? " selected" : "") + ">" + esc(o[1]) + "</option>"; }).join("") + "</select></label>";
    if (f.type === "color") return '<label>' + esc(f.label) + '<input type="color" data-k="' + f.k + '" value="' + esc(val) + '"></label>';
    if (f.type === "file") return '<label class="mg-wide">' + esc(f.label) + '<div class="row" style="gap:4px"><input data-k="' + f.k + '" value="' + esc(val) +
      '" placeholder="(trống = huy hiệu chữ)" style="flex:1"><button class="btn chip" data-pick="' + f.k + '">Chọn…</button></div></label>';
    var short = f.type === "num" || /^(value|prefix|suffix|kicker|done)$/.test(f.k);     // short fields share a row
    return '<label' + (short ? "" : ' class="mg-wide"') + ">" + esc(f.label) + '<input data-k="' + f.k + '"' + (f.type === "num" ? ' type="number" step="1"' : "") +
      ' value="' + esc(val) + '" data-key="' + esc(key) + '"></label>';
  }
  function form() {
    var el = $("mgForm"); if (!el) return;
    var id = S().tpl, T = M.TEMPLATES[id], v = S().vals[id] || {};
    var dDur = rule("mg_dur") || T.dur, dSc = rule("mg_scale") || 1;
    el.innerHTML = T.fields.map(function (f) { return field(f, v, id); }).join("") +
      '<label>Thời lượng (s)<input type="number" step="0.5" min="1.2" data-k="dur" value="' + esc(v.dur != null ? v.dur : "") + '" placeholder="' + dDur + (rule("mg_dur") ? " · Sổ luật" : "") + '"></label>' +
      '<label>Cỡ (×)<input type="number" step="0.1" min="0.4" max="2" data-k="scale" value="' + esc(v.scale != null ? v.scale : "") + '" placeholder="' + dSc + (rule("mg_scale") ? " · Sổ luật" : "") + '"></label>';
    Array.prototype.forEach.call(el.querySelectorAll("[data-k]"), function (inp) {
      inp.oninput = function () {
        var vals = S().vals[id] = S().vals[id] || {}, k = inp.dataset.k, x = inp.value;
        if ((k === "dur" || k === "scale") && x === "") delete vals[k]; else vals[k] = x;
        saveSoon(); if (k === "dur") restart();
      };
    });
    Array.prototype.forEach.call(el.querySelectorAll("[data-pick]"), function (b) {
      b.onclick = function () {
        var r = window.cep.fs.showOpenDialogEx(false, false, "Chọn logo (PNG nền trong suốt)", "", ["png", "webp", "jpg", "jpeg", "svg"]);
        if (!r || !r.data || !r.data.length) return;
        var f = r.data[0].replace(/\\/g, "/"), vals = S().vals[id] = S().vals[id] || {};
        vals[b.dataset.pick] = f; NX.save(); form(); logoImg(f, function () { gallery(); });
      };
    });
  }
  function brand() {
    var el = $("mgBrand"); if (!el) return;
    var b = Object.assign({}, M.BRAND, S().brand);
    el.innerHTML = [["primary", "Màu hộp"], ["accent", "Màu nhấn"], ["ink", "Màu chữ"]].map(function (x) {
      return '<label>' + x[1] + '<input type="color" data-b="' + x[0] + '" value="' + esc(b[x[0]]) + '"></label>';
    }).join("") + '<button class="btn chip" id="mgBrandReset" title="Về màu mặc định">↺</button>';
    Array.prototype.forEach.call(el.querySelectorAll("[data-b]"), function (inp) {
      inp.oninput = function () { S().brand[inp.dataset.b] = inp.value; saveSoon(); };
      inp.onchange = function () { gallery(); };
    });
    $("mgBrandReset").onclick = function () { S().brand = {}; NX.save(); brand(); gallery(); };
  }
  var saveT = 0;
  function saveSoon() { clearTimeout(saveT); saveT = setTimeout(function () { NX.save(); }, 400); }

  // live preview (loops while the view is visible)
  var raf = 0, t0 = 0, paused = false, scrub = 0;
  function stage() {
    var c = $("mgPrev"); if (!c) return null;
    var vert = seq.h > seq.w, W = vert ? 360 : 640, H = Math.round(W * seq.h / seq.w);
    if (c.width !== W || c.height !== H) { c.width = W; c.height = H; }
    c.parentNode.classList.toggle("vert", vert);
    return c;
  }
  function frame(now) {
    raf = 0;
    if (!visible()) return;
    var c = stage(), id = S().tpl, p = paramsFor(id), loop = p.dur + 0.7;
    var t = paused ? scrub : ((now - t0) / 1000) % loop;
    M.draw(c.getContext("2d"), id, Math.min(t, p.dur), p, c.width, c.height);
    if (!paused) { $("mgT").value = String(Math.round(Math.min(t, p.dur) / p.dur * 1000)); }
    $("mgTime").textContent = Math.min(t, p.dur).toFixed(1) + " / " + p.dur.toFixed(1) + "s";
    raf = requestAnimationFrame(frame);
  }
  function restart() { t0 = performance.now(); if (!raf && visible()) raf = requestAnimationFrame(frame); }
  function visible() { var c = $("mgCard"), hl = $("hl"); return !!(c && hl && hl.classList.contains("on") && c.style.display !== "none"); }

  // ------------------------------------------------------------------ render → ProRes 4444 + alpha
  function rate(fps) { return Math.abs(fps - 29.97) < 0.02 ? "30000/1001" : Math.abs(fps - 23.976) < 0.02 ? "24000/1001" : Math.abs(fps - 59.94) < 0.02 ? "60000/1001" : String(fps); }
  function renderClip(id, p, file, onFrame, cancelled) {
    var fps = seq.fps || 25, n = Math.max(1, Math.round(p.dur * fps));
    var k = S().res === "fast" ? Math.min(1, 1080 / Math.min(seq.w, seq.h)) : 1;
    var rw = Math.round(seq.w * k / 2) * 2, rh = Math.round(seq.h * k / 2) * 2;
    var vf = (rw !== seq.w || rh !== seq.h ? "scale=" + seq.w + ":" + seq.h + ":flags=lanczos," : "") + "format=yuva444p10le";
    var args = ["-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", rw + "x" + rh, "-r", rate(fps), "-i", "-",
                "-vf", vf, "-c:v", "prores_ks", "-profile:v", "4", "-vendor", "apl0", "-an", file];
    var c = document.createElement("canvas"); c.width = rw; c.height = rh;
    var ctx = c.getContext("2d", { willReadFrequently: true });
    return new Promise(function (res, rej) {
      var pr = cp.spawn(NX.S.ffmpeg || "ffmpeg", args, { windowsHide: true }), err = "", i = 0, done = false;
      if (NX.trackChild) NX.trackChild(pr);
      try { pr.stderr.setEncoding("utf8"); } catch (e) {}
      pr.stderr.on("data", function (d) { err += d; if (err.length > 8000) err = err.slice(-4000); });
      pr.on("error", function (e) { if (!done) { done = true; rej(new Error("ffmpeg: " + e.message)); } });
      pr.on("close", function (code) { if (done) return; done = true; code === 0 ? res(file) : rej(new Error("ffmpeg " + code + ": " + err.slice(-200))); });
      pr.stdin.on("error", function () {});
      var step = function () {
        if (done) return;
        if (cancelled()) { done = true; try { pr.kill(); } catch (e) {} return rej(new Error("Đã huỷ")); }
        var ts = performance.now();
        while (i < n && performance.now() - ts < 30) {             // ~30 ms of work per slice → the panel stays responsive
          M.draw(ctx, id, i / fps, p, rw, rh);
          i++; onFrame(i, n);
          if (!pr.stdin.write(Buf.from(ctx.getImageData(0, 0, rw, rh).data.buffer))) { pr.stdin.once("drain", function () { setTimeout(step, 0); }); return; }
        }
        if (i >= n) { pr.stdin.end(); return; }
        setTimeout(step, 0);
      };
      step();
    });
  }
  function outDir() {
    var base = NX.S.src && NX.outDir ? NX.outDir() : path.join(NX.cfgDir, "out");
    var d = path.join(base, "motion");
    try { fs.mkdirSync(d, { recursive: true }); } catch (e) {}
    return d;
  }
  function prog(f, label) {
    var b = $("mgProg"); if (!b) return;
    if (f < 0) { b.style.display = "none"; return; }
    b.style.display = "flex"; b.querySelector("i").style.width = Math.round(f * 100) + "%"; b.querySelector("span").textContent = label || "";
  }
  function bar() {
    if ($("mgPlace")) $("mgPlace").disabled = $("mgSave").disabled = !!job;
    if ($("mgCancel")) $("mgCancel").style.display = job ? "" : "none";
  }
  /* opts: {template, fields, at (s; default playhead), dur, track (0-based), place (default true), noLock (caller holds the lock)}
     → {file, placed, at, track} */
  function place(opts) {
    opts = opts || {};
    var id = opts.template || S().tpl;
    if (!M.TEMPLATES[id]) return Promise.reject(new Error("không có mẫu đồ họa " + id));
    if (job) return Promise.reject(new Error("đang render đồ họa khác"));
    if (!opts.noLock && NX.tryLock && !NX.tryLock("motion", "Đồ họa → timeline")) return Promise.reject(new Error("Đang chạy: " + NX.busyLabel()));
    var extra = Object.assign({}, opts.fields || {}); if (opts.dur) extra.dur = opts.dur;
    var p = paramsFor(id, extra), cancelled = false, doPlace = opts.place !== false, t0 = Date.now();
    var track = opts.track != null ? +opts.track : +(S().track != null ? S().track : 3), file;
    job = { cancel: function () { cancelled = true; } }; bar();
    return Promise.all([fonts(), waitLogo(p.logo), readSeq().catch(function (e) { if (doPlace) throw e; return seq; })]).then(function () {
      if (id === "logo_pop" && p.logo) p._img = logoImg(p.logo);
      var stamp = new Date().toTimeString().slice(0, 8).replace(/:/g, "");
      file = path.join(outDir(), id + "_" + stamp + ".mov").replace(/\\/g, "/");
      NX.log("▶ Đồ họa " + M.TEMPLATES[id].name + " · " + p.dur.toFixed(1) + "s · " + seq.w + "×" + seq.h + " @" + seq.fps + (doPlace ? " → V" + (track + 1) : ""), "#CCDC42");
      return renderClip(id, p, file, function (i, n) { prog(i / n, "Render " + i + "/" + n + " khung"); }, function () { return cancelled; });
    }).then(function () {
      if (!doPlace) return { file: file, placed: 0 };
      var at = opts.at != null && isFinite(+opts.at) ? +opts.at : seq.playhead;
      prog(1, "Đặt lên timeline…");
      return NX.evalHost("jesTextFxPlace(" + JSON.stringify(JSON.stringify([{ path: file, at: at }])) + "," + track + "," + JSON.stringify("JES_Motion") + ")").then(function (r) {
        var j = JSON.parse(r); if (!j.ok) throw new Error(j.error);
        if (j.errors && j.errors.length) throw new Error(j.errors[0]);
        return { file: file, placed: j.placed, at: at, track: j.track };
      });
    }).then(function (r) {
      NX.log("✔ Đồ họa: " + (r.placed ? "đặt ở " + r.track + " · " + r.at.toFixed(2) + "s" : "đã lưu") + " · " + Math.round((Date.now() - t0) / 100) / 10 + "s · " + file, "#6fd08c");
      return r;
    }).then(function (r) { finish(); return r; }, function (e) { finish(); NX.log("✖ Đồ họa: " + (e && e.message || e), "#e5534b"); throw e; });
    function finish() { job = null; if (!opts.noLock && NX.unlock) NX.unlock("motion"); prog(-1); bar(); }
  }

  // ------------------------------------------------------------------ view switch inside tab Chữ
  function setView(v) {
    S().view = v; NX.save();
    Array.prototype.forEach.call(document.querySelectorAll("#hlMode button"), function (b) { b.classList.toggle("on", b.dataset.m === v); });
    if ($("tfxCard")) $("tfxCard").style.display = v === "mg" ? "none" : "";
    if ($("mgCard")) $("mgCard").style.display = v === "mg" ? "" : "none";
    if (v === "mg") open(); else try { window.dispatchEvent(new Event("resize")); } catch (e) {}     // JIZURA iframe re-fits
  }
  function open() {
    if (!started) {
      started = true;
      fonts().then(function () { gallery(); });
      gallery(); form(); brand();
      $("mgTrack").value = String(S().track != null ? S().track : 3);
      $("mgRes").value = S().res || "seq";
      $("mgTrack").onchange = function () { S().track = +$("mgTrack").value; NX.save(); };
      $("mgRes").onchange = function () { S().res = $("mgRes").value; NX.save(); };
      $("mgPlace").onclick = function () { place({}).catch(function () {}); };
      $("mgSave").onclick = function () { place({ place: false }).catch(function () {}); };
      $("mgCancel").onclick = function () { if (job) job.cancel(); };
      $("mgPlay").onclick = function () { paused = !paused; $("mgPlay").textContent = paused ? "▶" : "⏸"; if (!paused) { t0 = performance.now() - scrub * 1000; restart(); } };
      $("mgT").oninput = function () { paused = true; $("mgPlay").textContent = "▶"; scrub = +$("mgT").value / 1000 * paramsFor(S().tpl).dur; };
      document.addEventListener("jes:stylebook", function () { if (started) { form(); gallery(); } });
      bar();
    }
    readSeq().then(function () { $("mgSeq").textContent = seq.name + " · " + seq.w + "×" + seq.h + " @" + seq.fps; }, function () {
      $("mgSeq").textContent = "chưa mở sequence — xem trước theo 16:9";
    }).then(restart);
    restart();
  }
  function init() {
    if (!$("mgCard")) return;            // 2.5.1+: the Studio (js/studio.js) owns tab Chữ; place() below still serves AI Assist
    Array.prototype.forEach.call(document.querySelectorAll("#hlMode button"), function (b) { b.onclick = function () { setView(b.dataset.m); }; });
    document.addEventListener("nxtab", function (e) { if (e.detail === "hl" && S().view === "mg") setView("mg"); });
    if (S().view === "mg") setView("mg");
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
  window.JESMotion = { place: place, setView: setView, templates: function () { return Object.keys(M.TEMPLATES); } };
})();
