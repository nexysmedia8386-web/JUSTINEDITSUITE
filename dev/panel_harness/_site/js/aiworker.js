/* Justin EDIT Suite v2.2 — client of the shared AI worker (python -m nxcore.ai.worker).
   One long-lived Python process serves footage search, look-alike takes and every "Sửa AI" command: the model and the
   footage data stay in memory between requests. JSON lines over stdin/stdout, one request at a time, matched by id.

   JESWorker.ask(req, {onRow, onLog, replace}) → Promise<{data, rows, err}>
     req      {op: "search"|"dups"|"rank"|"embed"|"aiedit"|"ping", …}
     onRow    streamed HIT / DUP / RANK rows   onLog   progress lines of this request
     replace  tag: a queued request with the same tag is dropped (typing a new search replaces the waiting one)
   JESWorker.cancel(tag) — also stops the RUNNING request with that tag (a long vision re-rank).
   JESWorker.usable() → false after repeated start failures (callers then use their one-shot fallback). */
(function () {
  var NX = window.NX;
  if (!NX) return;
  var cp = require("child_process");
  var W = { p: null, ready: false, buf: "", queue: [], cur: null, starting: null, seq: 0, fails: 0 };

  function env() { return Object.assign({}, process.env, { PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" }); }

  function onLine(l) {
    if (l === "READY") { W.ready = true; W.fails = 0; if (W.starting) W.starting.resolve(); return; }
    var sp = l.indexOf(" "), kind = sp < 0 ? l : l.slice(0, sp), body = sp < 0 ? null : l.slice(sp + 1), o = null;
    if (body) { try { o = JSON.parse(body); } catch (e) { return; } }
    var c = W.cur;
    if (!c) return;
    if (kind === "HIT" || kind === "DUP" || kind === "RANK") { c.rows.push(o); if (c.onRow) c.onRow(o, kind); return; }
    if (!o || o.id !== c.req.id) return;                       // a stray line of an older request
    if (kind === "LOG") { if (c.onLog) c.onLog(o.msg); }
    else if (kind === "RES") c.data = o.data;
    else if (kind === "ERR") c.err = o.msg || "lỗi";
    else if (kind === "END") { W.cur = null; c.done(); pump(); }
  }

  function start() {
    if (W.p && W.ready) return Promise.resolve();
    if (W.starting) return W.starting.promise;
    var d = {};
    d.promise = new Promise(function (res, rej) { d.resolve = res; d.reject = rej; });
    W.starting = d;
    var p;
    try { p = cp.spawn(NX.S.python || "python", ["-m", "nxcore.ai.worker"], { cwd: NX.S.tools, env: env(), windowsHide: true }); }
    catch (e) { W.starting = null; W.fails++; return Promise.reject(e); }
    W.p = p; W.ready = false; W.buf = "";
    if (NX.trackChild) NX.trackChild(p);
    p.stdout.setEncoding("utf8");
    p.stdout.on("data", function (s) {
      W.buf += s;
      var ls = W.buf.split(/\r?\n/);
      W.buf = ls.pop();
      ls.forEach(onLine);
    });
    p.stderr.on("data", function () {});
    p.on("error", function () {});
    p.on("close", function () {
      if (NX.untrackChild) NX.untrackChild(p);
      var wasReady = W.ready;
      W.p = null; W.ready = false;
      if (!wasReady) W.fails++;
      if (W.starting) { var s = W.starting; W.starting = null; if (!wasReady) s.reject(new Error("worker AI không khởi động được (Python / engine?)")); }
      if (W.cur) { var c = W.cur; W.cur = null; c.err = c.err || "worker AI đã dừng giữa chừng"; c.done(); }
      if (W.queue.length) setTimeout(pump, 300);                // restart for the waiting requests
    });
    return d.promise.then(function () { W.starting = null; });
  }

  function pump() {
    if (W.cur || !W.queue.length) return;
    start().then(function () {
      if (W.cur || !W.queue.length || !W.p) return;
      W.cur = W.queue.shift();
      try { W.p.stdin.write(JSON.stringify(W.cur.req) + "\n"); }
      catch (e) { var c = W.cur; W.cur = null; c.err = String(e.message || e); c.done(); }
    }, function (e) {
      var q = W.queue.splice(0, W.queue.length);
      q.forEach(function (c) { c.err = e.message || String(e); c.done(); });
    });
  }

  function ask(req, opt) {
    opt = opt || {};
    return new Promise(function (resolve) {
      req = Object.assign({}, req, { id: "r" + (++W.seq) });
      var job = { req: req, rows: [], data: null, err: "", onRow: opt.onRow, onLog: opt.onLog, tag: opt.replace || "" };
      job.done = function () { resolve({ data: job.data, rows: job.rows, err: job.err }); };
      if (job.tag) W.queue = W.queue.filter(function (c) {
        if (c.tag !== job.tag) return true;
        c.err = "đã thay bằng yêu cầu mới"; c.done(); return false;
      });
      W.queue.push(job);
      pump();
    });
  }

  /* cancel(tag): drop queued requests with this tag and tell the worker to stop the running one (its reader thread
     takes the cancel at once; a vision re-rank stops between two shots). */
  function cancel(tag) {
    W.queue = W.queue.filter(function (c) {
      if (c.tag !== tag) return true;
      c.err = "đã hủy"; c.done(); return false;
    });
    if (W.cur && W.cur.tag === tag && W.p) {
      try { W.p.stdin.write(JSON.stringify({ op: "cancel", target: W.cur.req.id }) + "\n"); } catch (e) {}
    }
  }

  function stop() { try { if (W.p) W.p.kill(); } catch (e) {} }
  window.addEventListener("beforeunload", stop);
  window.JESWorker = {
    ask: ask, stop: stop, cancel: cancel, warm: function () { return start().then(null, function () {}); },
    usable: function () { return W.fails < 3; }, alive: function () { return !!(W.p && W.ready); }
  };
})();
