/* Justin EDIT Suite — frame thumbnails at exact times (dashboard segments, Review In-points).
   Usage: html += JESThumbs.img(file, t, "cls", "title");  …after inserting the html: JESThumbs.fill(container).
   Missing frames are batched (120 ms) into one `python -m nxcore.thumbs` run and swapped in as they finish.
   A frame already requested (in flight) is never queued twice; a frame the engine could not make is not retried
   in this session (no endless python runs on a broken clip). */
(function () {
  "use strict";
  var fs = require("fs"), path = require("path"), cp = require("child_process"), toUrl = require("url").pathToFileURL;
  var queue = {}, timer = null, running = false;
  var inflight = {}, failed = {};                          // key = src|file|t

  function S() { return window.NX.S; }
  function esc(s) { return window.NX.esc(String(s == null ? "" : s)); }
  function stemOf(f) { return String(f).replace(/\\/g, "/").replace(/\.\w+$/, "").replace(/\//g, "__").replace(/[<>:"|?*]/g, "_"); }
  function key(t) { return (Math.round(+t * 10) / 10).toFixed(1); }          // one decimal — same file name as the engine
  function pathFor(src, f, t) { return path.join(src, "_nexys_work", "fthumbs", stemOf(f) + "_" + Math.round(+key(t) * 10) + ".jpg"); }
  function url(p) { try { return toUrl(p).href; } catch (e) { return "file:///" + String(p).replace(/\\/g, "/"); } }
  function rk(src, f, t) { return String(src).replace(/\\/g, "/").toLowerCase() + "|" + f + "|" + key(t); }

  function img(f, t, cls, title) {
    return '<img class="fthumb' + (cls ? " " + cls : "") + '" data-f="' + esc(f) + '" data-t="' + key(t) + '" alt=""' +
      (title ? ' title="' + esc(title) + '"' : "") + ">";
  }

  function each(f, t, cb) {
    var k = key(t);
    Array.prototype.forEach.call(document.querySelectorAll('img.fthumb[data-t="' + k + '"]'), function (el) {
      if (el.getAttribute("data-f") === f) cb(el);
    });
  }
  function apply(f, t, p) {
    each(f, t, function (el) { el.src = url(p); el.setAttribute("data-ok", "1"); el.classList.remove("loading"); });
  }
  function markFailed(f, t) {
    each(f, t, function (el) { if (el.getAttribute("data-ok") !== "1") { el.setAttribute("data-ok", "fail"); el.classList.remove("loading"); el.classList.add("failed"); } });
  }

  function fill(root) {
    var src = S().src; if (!src) return;
    Array.prototype.forEach.call((root || document).querySelectorAll("img.fthumb:not([data-ok])"), function (el) {
      var f = el.getAttribute("data-f"), t = el.getAttribute("data-t"), p = pathFor(src, f, t), k = rk(src, f, t);
      if (failed[k]) { el.setAttribute("data-ok", "fail"); el.classList.add("failed"); return; }
      if (inflight[k] || queue[k]) { el.classList.add("loading"); return; }        // already requested — wait for it
      var ok = false;
      try { ok = fs.existsSync(p); } catch (e) {}
      if (ok) { el.src = url(p); el.setAttribute("data-ok", "1"); el.classList.remove("loading"); }
      else { queue[k] = [f, +t]; el.classList.add("loading"); }
    });
    if (Object.keys(queue).length && !timer) timer = setTimeout(flush, 120);
  }

  function flush() {
    timer = null;
    if (running) { timer = setTimeout(flush, 400); return; }
    var src = S().src;
    var pre = String(src || "").replace(/\\/g, "/").toLowerCase() + "|";
    var ks = Object.keys(queue).filter(function (k) { return k.indexOf(pre) === 0; });
    Object.keys(queue).forEach(function (k) { if (ks.indexOf(k) < 0) delete queue[k]; });   // requests of a previous folder
    ks = ks.slice(0, 240);
    if (!ks.length) return;
    var batch = {};
    var reqs = ks.map(function (k) { var r = queue[k]; delete queue[k]; inflight[k] = 1; batch[k] = r; return r; });
    var env = Object.assign({}, process.env, { PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" });
    var p;
    var settle = function () {                         // whatever the engine did not deliver is a failure (not retried)
      Object.keys(batch).forEach(function (k) { delete inflight[k]; failed[k] = 1; markFailed(batch[k][0], batch[k][1]); });
      batch = {};
    };
    try { p = cp.spawn(S().python || "python", ["-m", "nxcore.thumbs", src], { cwd: S().tools, env: env, windowsHide: true }); }
    catch (e) { settle(); return; }
    running = true;
    if (window.NX && window.NX.trackChild) window.NX.trackChild(p);
    var buf = "";
    try { p.stdout.setEncoding("utf8"); } catch (e) {}          // Vietnamese file names never split mid-character
    p.stdout.on("data", function (d) {
      buf += d;
      var ls = buf.split(/\r\n|\n|\r/); buf = ls.pop();
      ls.forEach(function (l) {
        var m = l.match(/^THUMB (.+)\|([\d.]+)\|(.+)$/);
        if (!m) return;
        var k = rk(src, m[1], +m[2]);
        delete inflight[k]; delete batch[k];
        if (S().src === src) apply(m[1], +m[2], m[3]);
      });
    });
    p.stderr.on("data", function () {});                        // drain (a full pipe would block the engine)
    var done = false;
    var end = function () {
      if (done) return; done = true;
      if (buf) { var m = buf.match(/^THUMB (.+)\|([\d.]+)\|(.+)$/); if (m) { var k = rk(src, m[1], +m[2]); delete inflight[k]; delete batch[k]; if (S().src === src) apply(m[1], +m[2], m[3]); } buf = ""; }
      settle(); running = false;
      if (Object.keys(queue).length) timer = setTimeout(flush, 50);
    };
    p.on("error", end);
    p.on("close", end);
    p.stdin.on("error", function () {});
    p.stdin.end(JSON.stringify(reqs));
  }

  window.JESThumbs = { img: img, fill: fill, pathFor: pathFor, key: key };
})();
