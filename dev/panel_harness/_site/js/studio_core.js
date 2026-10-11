/* Justin EDIT Suite — Studio chữ & đồ họa: model + rendering (no UI; js/studio.js draws the editor).
   Every item lives on the sequence's own timeline (seconds) and on a lane (lane 0 → the base video track):
     · text    — ONE JIZURA cut built on purpose: the editor's layout / enter / hold / exit / text treatment / decor /
                 camera / palette / font (JIZURA's library: 140+ layouts, 100 entrances, 86 exits, 52 treatments…).
                 The plan is made by J.plan() from a one-line project whose line-0 override pins every choice.
     · graphic — a js/motion_tpl.js template (lower third, counter, callout, title card, subscribe, logo…)
     · logo    — an image with its own in / idle / out animation, anchored to a corner
   Each item is drawn on its own full-frame transparent layer, then composited with the item's transform
   (x, y offset in frame fractions, scale, rotation, opacity) around the centre of what it draws — so moving /
   scaling never touches the template, and keyframes can drive the same transform later.
   Layers are canvases of the JIZURA iframe document: JIZURA loads its web fonts there, and only canvases of that
   document see them (Vietnamese faces come from textfx.js patchVietnamese). */
(function () {
  "use strict";
  var NX = window.NX, M = window.JESMotionTpl;
  if (!NX || !M) return;
  var fs = require("fs"), path = require("path"), cp = require("child_process"), Buf = require("buffer").Buffer;
  var J = null, IW = null, R = null;

  // ------------------------------------------------------------------ engine (the JIZURA iframe of tab Chữ)
  var engineP = null;
  function engine() {
    if (J) return Promise.resolve(J);
    if (engineP) return engineP;
    var T = window.JESTextFx;
    if (!T) return Promise.reject(new Error("module JIZURA chưa tải"));
    try { T.start(); } catch (e) {}
    engineP = new Promise(function (res, rej) {
      var n = 0;
      (function wait() {
        var j = T.J();
        if (j && j.Renderer && j.plan && j.LANG_FACES && j.LANG_FACES.vi) { J = j; IW = T.W(); R = new J.Renderer(); return res(J); }
        if (++n > 400) { engineP = null; return rej(new Error("JIZURA không nạp được (tab Chữ)")); }
        setTimeout(wait, 50);
      })();
    });
    return engineP;
  }
  function mkCanvas(w, h) { var c = (IW ? IW.document : document).createElement("canvas"); c.width = Math.max(1, w | 0); c.height = Math.max(1, h | 0); return c; }
  var pool = {};
  function scratch(key, w, h) {                      // reusable layer canvases (one per purpose + size)
    var c = pool[key];
    if (!c) c = pool[key] = mkCanvas(w, h);
    if (c.width !== (w | 0) || c.height !== (h | 0)) { c.width = Math.max(1, w | 0); c.height = Math.max(1, h | 0); }
    return c;
  }

  // ------------------------------------------------------------------ sequence
  var seq = { name: "", w: 1920, h: 1080, fps: 25, dur: 60, playhead: 0, live: false, video: [] };
  function readSeq(rich) {
    return NX.evalHost("jesSequence(" + (rich ? 400 : 1) + ")").then(function (r) {
      var j = JSON.parse(r); if (!j.ok) throw new Error(j.error || "chưa mở sequence");
      seq = { name: j.name, w: j.w, h: j.h, fps: j.fps || 25, dur: Math.max(1, +j.dur || 60), playhead: +j.playhead || 0, live: true, video: j.video || [] };
      return seq;
    }, function (e) { seq.live = false; throw e; });
  }
  var ASPECTS = ["16:9", "9:16", "1:1", "4:5", "4:3", "21:9"];
  var AR = { "16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1, "4:5": 0.8, "4:3": 4 / 3, "21:9": 21 / 9 };
  function aspectKey(w, h) {
    var r = w / h, best = "16:9", bd = 1e9;
    ASPECTS.forEach(function (a) { var d = Math.abs(Math.log(r / AR[a])); if (d < bd) { bd = d; best = a; } });
    return best;
  }

  // ------------------------------------------------------------------ brand kit (<cfgDir>/brandkit.json)
  var BK_DEF = { primary: "#15161b", accent: "#ffc83d", ink: "#ffffff", textStyle: "noir", font: "", treat: "none",
                 logo: "", logoAnchor: "tr", presets: [] };
  var bk = null;
  function bkPath() { return path.join(NX.cfgDir, "brandkit.json"); }
  function brand() {
    if (bk) return bk;
    var j = null; try { j = JSON.parse(fs.readFileSync(bkPath(), "utf8")); } catch (e) {}
    bk = Object.assign({}, BK_DEF, j || {}); if (!Array.isArray(bk.presets)) bk.presets = [];
    return bk;
  }
  function saveBrand(patch) {
    bk = Object.assign(brand(), patch || {});
    try { fs.mkdirSync(NX.cfgDir, { recursive: true }); fs.writeFileSync(bkPath(), JSON.stringify(bk, null, 1), "utf8"); } catch (e) {}
    try { document.dispatchEvent(new CustomEvent("jes:brand")); } catch (e) {}
    return bk;
  }

  // ------------------------------------------------------------------ items
  function uid() { return "i" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }
  var TEXT_FIELDS = ["text", "emph", "impact", "style", "font", "layout", "enter", "hold", "exit", "treat", "decor", "cam", "inDur", "outDur",
                     "fg", "accent", "box", "motion", "glitch", "chroma", "koma", "seed"];
  function newItem(kind, over) {
    var b = brand(), it = { id: uid(), kind: kind, name: "", t0: 0, dur: 3, lane: 0, x: 0, y: 0, s: 1, rot: 0, op: 1 };
    if (kind === "text") Object.assign(it, { text: "Chữ mới", emph: [], impact: false, style: b.textStyle || "noir", font: b.font || "",
      layout: "center", enter: "blurStagger", hold: "float", exit: "dissolve", treat: b.treat || "none", decor: [], cam: "push",
      inDur: 0, outDur: 0, fg: "", accent: "", box: "", motion: 0.7, glitch: 0.2, chroma: 0.25, koma: 0, seed: (Math.random() * 1e9) | 0 });
    if (kind === "graphic") Object.assign(it, { tpl: "lower_third", fields: {}, gscale: 1, dur: 4 });
    if (kind === "logo") Object.assign(it, { file: b.logo || "", anchor: b.logoAnchor || "tr", size: 0.16, ain: "pop", idle: "float", aout: "fade", caption: "", dur: 4 });
    return Object.assign(it, over || {});
  }
  function itemLabel(it) {
    if (it.name) return it.name;
    if (it.kind === "text") return String(it.text || "").replace(/\s+/g, " ").slice(0, 40) || "(chữ trống)";
    if (it.kind === "graphic") { var T = M.TEMPLATES[it.tpl]; var f = it.fields || {}; return (T ? T.name : it.tpl) + (f.name || f.title || f.text ? ": " + (f.name || f.title || f.text) : ""); }
    return "Logo" + (it.file ? " · " + path.basename(it.file) : "");
  }

  // ------------------------------------------------------------------ text item → one-cut JIZURA plan
  var planCache = new Map();
  function markText(it) {
    var t = String(it.text || " ").replace(/[\r\n]+/g, " ").replace(/[\[\]]/g, function (c) { return c === "[" ? "(" : ")"; }).trim() || " ";
    (it.emph || []).forEach(function (w) { if (w && t.indexOf(w) >= 0 && t.indexOf("*" + w + "*") < 0) t = t.replace(w, "*" + w + "*"); });
    return t + (it.impact ? "!" : "");
  }
  function textKey(it) { var a = [aspectKey(seq.w, seq.h), it.dur, Math.round(seq.fps)]; TEXT_FIELDS.forEach(function (k) { a.push(it[k]); }); return JSON.stringify(a); }
  function textPlan(it) {
    var key = textKey(it), hit = planCache.get(key);
    if (hit) return hit;
    var pr = J.defaultProject();
    pr.lyrics = "[00:00.00]" + markText(it); pr.title = ""; pr.artist = "";
    pr.style = J.STYLES[it.style] ? it.style : "noir"; pr.aspect = aspectKey(seq.w, seq.h); pr.fps = Math.round(seq.fps) || 25;
    pr.lang = "vi"; pr.seed = it.seed | 0; pr.keyBg = "off";
    pr.fx = Object.assign({}, pr.fx, { motion: +it.motion, glitch: +it.glitch, chroma: +it.chroma, koma: +it.koma | 0, hud: "off", bgSwitch: 0, flash: false, texture: 0 });
    var col = { enabled: !!(it.fg || it.box), accentOn: !!it.accent };
    if (it.fg) col.fg = it.fg; if (it.box) col.bg = it.box; if (it.accent) col.accent = it.accent;
    pr.colors = col;
    if (it.font && J.FONTS[it.font]) pr.fonts = { display: it.font, body: it.font, serif: it.font };
    var reg = function (g, k, d) { return J.registry(g)[k] ? k : d; };
    pr.overrides = { 0: { layout: reg("layout", it.layout, "center"), enter: reg("enter", it.enter, "blur"), exit: reg("exit", it.exit, "blur"),
      hold: reg("hold", it.hold, "still"), decor: (it.decor || []).filter(function (d) { return J.DECOR[d]; }), treat: reg("treat", it.treat, "none"),
      bg: "none", cam: reg("cam", it.cam, "push"), lock: true, lockedSeed: it.seed | 0, single: true } };
    var plan = J.plan(pr), D = Math.max(0.3, +it.dur || 3);
    plan.cuts = plan.cuts.filter(function (c) { return c.line === 0 && c.layout !== "interlude"; }).slice(0, 1);
    var c = plan.cuts[0];
    if (c) {
      c.start = 0; c.end = D; c.dur = D; c.index = 0; c.trans = null;
      if (+it.inDur > 0) c.inDur = +it.inDur;
      if (+it.outDur > 0) c.outDur = +it.outDur;
      if (c.inDur + c.outDur > D * 0.92) { var f = D * 0.92 / (c.inDur + c.outDur); c.inDur *= f; c.outDur *= f; }
    }
    plan.duration = D;
    plan.events = plan.events.filter(function (e) { return e.t >= 0 && e.t < D; });
    plan.hud = false; plan.keyBg = null;
    planCache.set(key, plan);
    if (planCache.size > 80) planCache.delete(planCache.keys().next().value);
    fontsFor(plan, it.text);
    return plan;
  }
  var fontsDone = {}, fontWaiters = [];
  function fontsFor(plan, text) {
    var keys = J.fontsOfPlan(plan), k = keys.join(",") + "|" + text;
    if (fontsDone[k]) return fontsDone[k] === true ? Promise.resolve() : fontsDone[k];
    fontsDone[k] = J.ensureFonts(String(text || "") + "AĂÂĐÊÔƠƯaăâđêôơư0123456789", keys).then(function () {
      fontsDone[k] = true; fontWaiters.splice(0).forEach(function (f) { try { f(); } catch (e) {} });
    }, function () { fontsDone[k] = true; });
    return fontsDone[k];
  }
  function onFonts(f) { fontWaiters.push(f); }

  // ------------------------------------------------------------------ logo images (data: URL → canvas stays clean)
  var imgs = {};
  function image(file, cb) {
    if (!file) return null;
    var c = imgs[file];
    if (!c) {
      c = imgs[file] = { img: new Image(), ok: false, wait: [] };
      try {
        var ext = (path.extname(file).slice(1) || "png").toLowerCase();
        var mime = /jpe?g/.test(ext) ? "image/jpeg" : ext === "svg" ? "image/svg+xml" : ext === "webp" ? "image/webp" : "image/png";
        c.img.onload = function () { c.ok = true; c.wait.splice(0).forEach(function (f) { f(); }); };
        c.img.onerror = function () { c.err = "không đọc được ảnh"; c.wait.splice(0).forEach(function (f) { f(); }); };
        c.img.src = "data:" + mime + ";base64," + fs.readFileSync(file).toString("base64");
      } catch (e) { c.err = e.message; }
    }
    if (cb) { if (c.ok || c.err) cb(); else c.wait.push(cb); }
    return c.ok ? c.img : null;
  }
  function waitImage(file) { return new Promise(function (r) { if (!file) return r(); image(file, r); }); }

  // ------------------------------------------------------------------ drawing one item on a full-frame layer
  function clamp(x) { return x < 0 ? 0 : x > 1 ? 1 : x; }
  function eOut(x) { x = clamp(x); return 1 - Math.pow(1 - x, 3); }
  function eIn(x) { x = clamp(x); return x * x * x; }
  function eBack(x) { x = clamp(x); var c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); }
  var ANCHORS = { tl: [0, 0], tc: [0.5, 0], tr: [1, 0], cl: [0, 0.5], c: [0.5, 0.5], cr: [1, 0.5], bl: [0, 1], bc: [0.5, 1], br: [1, 1] };
  function drawText(it, lt, L) {
    var plan = textPlan(it), sc = Math.min(L.width / plan.W, L.height / plan.H);
    var lw = Math.round(plan.W * sc), lh = Math.round(plan.H * sc);
    if (lw === L.width && lh === L.height) { R.frame(L.getContext("2d"), plan, lt, { transparent: true, scale: sc }); return; }
    var T = scratch("textfit", lw, lh);                // design aspect ≠ sequence aspect: centre JIZURA's frame
    R.frame(T.getContext("2d"), plan, lt, { transparent: true, scale: sc });
    var x = L.getContext("2d"); x.clearRect(0, 0, L.width, L.height); x.drawImage(T, (L.width - lw) / 2, (L.height - lh) / 2);
  }
  function drawGraphic(it, lt, L) {
    var b = brand(), over = Object.assign({ primary: b.primary, accent: b.accent, ink: b.ink }, it.fields || {}, { dur: it.dur, scale: it.gscale || 1 });
    var p = M.params(it.tpl, over);
    if (it.tpl === "logo_pop" && p.logo) p._img = image(p.logo);
    M.draw(L.getContext("2d"), it.tpl, lt, p, L.width, L.height);
  }
  function drawLogo(it, lt, L) {
    var x = L.getContext("2d"), W = L.width, H = L.height, u = Math.min(W, H);
    x.save(); x.clearRect(0, 0, W, H);
    var img = image(it.file), S = Math.max(8, (+it.size || 0.16) * u), iw = S, ih = S;
    if (img) { var k = Math.min(S / img.naturalWidth, S / img.naturalHeight); iw = img.naturalWidth * k; ih = img.naturalHeight * k; }
    var a = ANCHORS[it.anchor] || ANCHORS.tr, m = 0.05 * u;
    var cx = m + iw / 2 + a[0] * (W - 2 * m - iw), cy = m + ih / 2 + a[1] * (H - 2 * m - ih);
    var D = it.dur, pin = clamp(lt / 0.55), pout = clamp((lt - (D - 0.45)) / 0.45);
    var sc = 1, al = 1, dx = 0, dy = 0, rot = 0;
    var aIn = it.ain || "pop", aOut = it.aout || "fade", side = a[0] < 0.5 ? -1 : 1;
    if (aIn === "pop") sc *= eBack(pin); else if (aIn === "fade") al *= eOut(pin);
    else if (aIn === "slide") { dx += side * (1 - eOut(pin)) * (iw + m * 2); al *= clamp(pin * 3); }
    else if (aIn === "spin") { sc *= eOut(pin); rot += (1 - eOut(pin)) * Math.PI; }
    else if (aIn === "zoom") { sc *= 1 + (1 - eOut(pin)) * 1.5; al *= eOut(pin); }
    if (aOut === "fade") al *= 1 - eIn(pout); else if (aOut === "shrink") { sc *= 1 - eIn(pout); }
    else if (aOut === "slide") { dx += side * eIn(pout) * (iw + m * 2); }
    var idle = Math.max(0, lt - 0.55);
    if (it.idle === "float") dy += Math.sin(idle * 1.6) * 0.012 * u;
    else if (it.idle === "pulse") sc *= 1 + 0.03 * Math.sin(idle * 3.2);
    if (sc > 0.001 && al > 0.001) {
      x.globalAlpha = al; x.translate(cx + dx, cy + dy); if (rot) x.rotate(rot); x.scale(sc, sc);
      if (img) x.drawImage(img, -iw / 2, -ih / 2, iw, ih);
      else {                                         // no file yet: a neutral badge so the slot is visible
        x.fillStyle = brand().primary; x.beginPath(); x.arc(0, 0, S * 0.45, 0, Math.PI * 2); x.fill();
        x.strokeStyle = brand().accent; x.lineWidth = S * 0.04; x.stroke();
        x.fillStyle = brand().ink; x.font = "800 " + Math.round(S * 0.22) + 'px "Be Vietnam Pro","Segoe UI",Arial'; x.textAlign = "center"; x.textBaseline = "middle"; x.fillText("LOGO", 0, 0);
      }
      if (it.idle === "shine" && img) {                // a light sweep every 3 s, on the logo pixels only
        var ph = (idle % 3) / 0.9;
        if (ph < 1) {
          var Sh = scratch("shine", Math.ceil(iw), Math.ceil(ih)), sx = Sh.getContext("2d");
          sx.globalCompositeOperation = "source-over"; sx.clearRect(0, 0, Sh.width, Sh.height); sx.drawImage(img, 0, 0, iw, ih);
          sx.globalCompositeOperation = "source-atop";
          var bx = -iw * 0.5 + iw * 2 * ph, g = sx.createLinearGradient(bx - iw * 0.25, 0, bx + iw * 0.25, ih * 0.4);
          g.addColorStop(0, "rgba(255,255,255,0)"); g.addColorStop(0.5, "rgba(255,255,255,.55)"); g.addColorStop(1, "rgba(255,255,255,0)");
          sx.fillStyle = g; sx.fillRect(0, 0, iw, ih); x.drawImage(Sh, -iw / 2, -ih / 2);
        }
      }
      if (it.caption) {
        x.fillStyle = brand().ink; x.font = "800 " + Math.round(S * 0.16) + 'px "Be Vietnam Pro","Segoe UI",Arial'; x.textAlign = "center"; x.textBaseline = "top";
        x.shadowColor = "rgba(0,0,0,.4)"; x.shadowBlur = S * 0.06; x.fillText(it.caption, 0, ih / 2 + S * 0.08);
      }
    }
    x.restore();
  }
  function drawLayer(it, lt, L) {
    if (it.kind === "text") drawText(it, lt, L);
    else if (it.kind === "graphic") drawGraphic(it, lt, L);
    else drawLogo(it, lt, L);
  }

  /* where the item draws (fractions of the frame), measured once per look at a "resting" moment: the pivot of
     scale / rotation and the box the editor drags in the preview */
  function restTime(it) {
    if (it.kind === "text") { var c = textPlan(it).cuts[0]; return c ? Math.min(it.dur * 0.95, c.inDur + Math.max(0.05, (c.dur - c.inDur - c.outDur) * 0.4)) : it.dur / 2; }
    return Math.min(it.dur * 0.6, 1.6);
  }
  function lookKey(it) {
    if (it.kind === "text") return "t" + textKey(it);
    if (it.kind === "graphic") return "g" + JSON.stringify([it.tpl, it.fields, it.gscale, it.dur, aspectKey(seq.w, seq.h), brand().primary]);
    return "l" + JSON.stringify([it.file, it.anchor, it.size, it.caption, aspectKey(seq.w, seq.h), !!image(it.file)]);
  }
  function bbox(it) {
    var k = lookKey(it);
    if (it._bb && it._bbKey === k) return it._bb;
    var w = 384, h = Math.max(1, Math.round(384 * seq.h / seq.w)), L = scratch("bb", w, h);
    try { drawLayer(it, restTime(it), L); } catch (e) { return { x0: 0.3, y0: 0.4, x1: 0.7, y1: 0.6, cx: 0.5, cy: 0.5, empty: true }; }
    var d = L.getContext("2d").getImageData(0, 0, w, h).data, x0 = w, y0 = h, x1 = -1, y1 = -1;
    for (var y = 0; y < h; y += 2) for (var x = 0; x < w; x += 2) if (d[(y * w + x) * 4 + 3] > 24) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
    var bb = x1 < 0 ? { x0: 0.3, y0: 0.4, x1: 0.7, y1: 0.6, empty: true } : { x0: x0 / w, y0: y0 / h, x1: (x1 + 2) / w, y1: (y1 + 2) / h };
    bb.cx = (bb.x0 + bb.x1) / 2; bb.cy = (bb.y0 + bb.y1) / 2;
    Object.defineProperty(it, "_bb", { value: bb, writable: true, configurable: true, enumerable: false });
    Object.defineProperty(it, "_bbKey", { value: k, writable: true, configurable: true, enumerable: false });
    return bb;
  }
  /* item → its pixels at sequence time t, composited with its transform onto ctx (W×H). → screen box or null */
  function composite(ctx, it, t, W, H, layerKey) {
    var lt = t - it.t0;
    if (lt < 0 || lt >= it.dur) return null;
    var L = scratch(layerKey || "layer", W, H);
    drawLayer(it, lt, L);
    var bb = bbox(it), px = bb.cx * W, py = bb.cy * H, s = +it.s || 1, r = (+it.rot || 0) * Math.PI / 180;
    ctx.save();
    ctx.globalAlpha = clamp(it.op == null ? 1 : +it.op);
    ctx.translate(px + (+it.x || 0) * W, py + (+it.y || 0) * H);
    if (r) ctx.rotate(r);
    if (s !== 1) ctx.scale(s, s);
    ctx.drawImage(L, -px, -py);
    ctx.restore();
    return { id: it.id, cx: px + (+it.x || 0) * W, cy: py + (+it.y || 0) * H, w: (bb.x1 - bb.x0) * W * s, h: (bb.y1 - bb.y0) * H * s, rot: r };
  }
  /* every item active at t, lane 0 first (= lowest track) → hit boxes for the editor */
  function drawFrame(ctx, items, t, W, H, only) {
    ctx.clearRect(0, 0, W, H);
    var boxes = [];
    items.slice().sort(function (a, b) { return a.lane - b.lane; }).forEach(function (it) {
      if (only && only.indexOf(it.id) < 0) return;
      try { var b = composite(ctx, it, t, W, H); if (b) boxes.push(b); } catch (e) { console.warn("studio item", it.id, e); }
    });
    return boxes;
  }

  // ------------------------------------------------------------------ render one item → ProRes 4444 + alpha
  function rate(fps) { return Math.abs(fps - 29.97) < 0.02 ? "30000/1001" : Math.abs(fps - 23.976) < 0.02 ? "24000/1001" : Math.abs(fps - 59.94) < 0.02 ? "60000/1001" : String(fps); }
  function renderItem(it, file, opt, onFrame, cancelled) {
    var fps = seq.fps || 25, n = Math.max(1, Math.round(it.dur * fps));
    var k = opt.fast ? Math.min(1, 1080 / Math.min(seq.w, seq.h)) : 1;
    var rw = Math.round(seq.w * k / 2) * 2, rh = Math.round(seq.h * k / 2) * 2;
    var vf = (rw !== seq.w || rh !== seq.h ? "scale=" + seq.w + ":" + seq.h + ":flags=lanczos," : "") + "format=yuva444p10le";
    var args = ["-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", rw + "x" + rh, "-r", rate(fps), "-i", "-",
                "-vf", vf, "-c:v", "prores_ks", "-profile:v", "4", "-vendor", "apl0", "-an", file];
    var out = scratch("render", rw, rh), ctx = out.getContext("2d", { willReadFrequently: true });
    var probe = Object.assign({}, it, { t0: 0 });
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
          ctx.clearRect(0, 0, rw, rh);
          composite(ctx, probe, i / fps, rw, rh, "rlayer");
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
    var d = path.join(base, "studio", safe(seq.name || "sequence"));
    try { fs.mkdirSync(d, { recursive: true }); } catch (e) {}
    return d;
  }
  function safe(s) { return String(s || "").replace(/[\\/:*?"<>|]/g, "_").slice(0, 80) || "sequence"; }
  /* items → rendered + placed at their own time on track base+lane. opts: {base, fast, noLock, onProgress, cancelled} */
  function place(items, opts) {
    opts = opts || {};
    if (!opts.noLock && NX.tryLock && !NX.tryLock("studio", "Studio → timeline")) return Promise.reject(new Error("Đang chạy: " + NX.busyLabel()));
    var base = opts.base != null ? +opts.base : 2, t0 = Date.now(), placed = [], cancelled = opts.cancelled || function () { return false; };
    var prog = opts.onProgress || function () {};
    return engine().then(function () { return readSeq(); }).then(function () {
      return Promise.all(items.map(function (it) {
        if (it.kind === "text") return fontsFor(textPlan(it), it.text);
        if (it.kind === "logo") return waitImage(it.file);
        if (it.kind === "graphic" && it.fields && it.fields.logo) return waitImage(it.fields.logo);
      }));
    }).then(function () {
      var dir = outDir(), stamp = new Date().toTimeString().slice(0, 8).replace(/:/g, "");
      NX.log("▶ Studio → timeline: " + items.length + " mục · " + seq.name + " · " + seq.w + "×" + seq.h + " @" + seq.fps, "#CCDC42");
      return items.reduce(function (p, it, k) {
        return p.then(function () {
          var file = path.join(dir, it.kind + "_" + it.id + "_" + stamp + ".mov").replace(/\\/g, "/");
          return renderItem(it, file, opts, function (i, n) { prog((k + i / n) / items.length, "Mục " + (k + 1) + "/" + items.length + " · khung " + i + "/" + n); }, cancelled)
            .then(function () { placed.push({ it: it, path: file, at: it.t0, track: base + (it.lane | 0) }); });
        });
      }, Promise.resolve());
    }).then(function () {
      prog(1, "Đặt lên timeline…");
      var byTrack = {};
      placed.forEach(function (p) { (byTrack[p.track] = byTrack[p.track] || []).push({ path: p.path, at: p.at }); });
      return Object.keys(byTrack).reduce(function (p, tr) {
        return p.then(function () {
          return NX.evalHost("jesTextFxPlace(" + JSON.stringify(JSON.stringify(byTrack[tr])) + "," + tr + "," + JSON.stringify("JES_Studio") + ")").then(function (r) {
            var j = JSON.parse(r); if (!j.ok) throw new Error(j.error);
            if (j.errors && j.errors.length) throw new Error(j.errors[0]);
          });
        });
      }, Promise.resolve());
    }).then(function () {
      placed.forEach(function (p) { p.it.placed = { file: p.path, at: p.at, track: p.track, when: new Date().toISOString() }; });
      NX.log("✔ Studio: " + placed.length + " mục lên timeline (V" + (base + 1) + "…) · " + Math.round((Date.now() - t0) / 100) / 10 + "s", "#6fd08c");
      return placed;
    }).then(function (r) { if (!opts.noLock && NX.unlock) NX.unlock("studio"); return r; },
            function (e) { if (!opts.noLock && NX.unlock) NX.unlock("studio"); throw e; });
  }

  // ------------------------------------------------------------------ document per sequence
  var doc = null, saveT = 0;
  function docPath(name) {
    var base = NX.S.src && NX.outDir ? NX.outDir() : path.join(NX.cfgDir, "out");
    return path.join(base, "studio", safe(name) + ".studio.json");
  }
  function loadDoc(name) {
    var j = null; try { j = JSON.parse(fs.readFileSync(docPath(name), "utf8")); } catch (e) {}
    doc = j && Array.isArray(j.items) ? j : { version: 1, seq: name, base: 2, items: [] };
    doc.seq = name;
    return doc;
  }
  function saveDoc(now) {
    if (!doc) return;
    clearTimeout(saveT);
    var write = function () { try { var p = docPath(doc.seq); fs.mkdirSync(path.dirname(p), { recursive: true }); fs.writeFileSync(p, JSON.stringify(doc, null, 1), "utf8"); } catch (e) {} };
    if (now) write(); else saveT = setTimeout(write, 500);
  }
  /* first lane where [t0, t0+dur) does not overlap another item */
  function freeLane(t0, dur, skipId) {
    for (var lane = 0; lane < 12; lane++) {
      var clash = (doc ? doc.items : []).some(function (o) { return o.id !== skipId && o.lane === lane && o.t0 < t0 + dur - 1e-3 && t0 < o.t0 + o.dur - 1e-3; });
      if (!clash) return lane;
    }
    return 0;
  }

  // ------------------------------------------------------------------ build cues → items (Thoại / Highlight)
  /* dialogue → readable subtitle cards; highlights → the meaning-based roles of js/textfx.js (rules, 0 token) */
  function fromCues(kind) {
    var T = window.JESTextFx, list = T && T.cues ? T.cues(kind) : [];
    if (!list.length) return [];
    var ROLES = T.roles(), used = {};
    function rot(arr) { arr = (arr || []).filter(Boolean); if (!arr.length) return undefined; var k = arr.join(","); used[k] = (used[k] || 0) + 1; return arr[(used[k] - 1) % arr.length]; }
    return list.map(function (c, i) {
      var it = newItem("text", { text: c.text, t0: Math.round(c.s * 100) / 100, dur: Math.max(0.6, Math.round((c.e - c.s) * 100) / 100), lane: kind === "sub" ? 0 : 1 });
      if (kind === "sub") Object.assign(it, { layout: "subtitleBar", enter: "fadeStagger", hold: "still", exit: "dissolve", chroma: 0, glitch: 0, decor: [] });
      else {
        var role = T.ruleRole(c.text, i), Rr = ROLES[role] || {};
        if (Rr.layout) Object.assign(it, { layout: rot(Rr.layout), enter: rot(Rr.enter) || it.enter, exit: rot(Rr.exit) || it.exit, hold: Rr.hold || it.hold,
                                           decor: (Rr.decor || []).slice(0, 1), impact: !!Rr.impact, name: Rr.label + ": " + c.text.slice(0, 30) });
      }
      return it;
    });
  }

  window.JESStudioCore = {
    engine: engine, J: function () { return J; }, seq: function () { return seq; }, readSeq: readSeq, aspectKey: aspectKey,
    brand: brand, saveBrand: saveBrand, newItem: newItem, itemLabel: itemLabel, textPlan: textPlan, onFonts: onFonts, fontsFor: fontsFor,
    image: image, bbox: bbox, composite: composite, drawFrame: drawFrame, drawLayer: drawLayer, mkCanvas: mkCanvas, restTime: restTime,
    place: place, renderItem: renderItem, loadDoc: loadDoc, saveDoc: saveDoc, doc: function () { return doc; }, freeLane: freeLane,
    fromCues: fromCues, ANCHORS: ANCHORS, TEXT_FIELDS: TEXT_FIELDS
  };
})();
