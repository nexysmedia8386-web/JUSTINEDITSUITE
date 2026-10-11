/* Justin EDIT Suite — tab Giọng (v2, gọn): đọc các câu [VO] của bản Build gần nhất, tạo giọng bằng VoiceStudio
   (github.com/debpalash/VoiceStudio, AGPL-3.0 — chương trình riêng chạy trên máy, panel chỉ gọi HTTP :3900),
   lưu thành <out>/vo/VO_01.wav … đúng tên bước Build tự đặt lên A4, hoặc đặt ngay lên timeline đúng TC.
   Giao diện web đầy đủ của VoiceStudio mở trong trình duyệt (không nhúng iframe vào khung hẹp nữa). */
(function () {
  "use strict";
  var fs = require("fs"), path = require("path"), cp = require("child_process"), http = require("http"), toUrl = require("url").pathToFileURL;
  var NX = window.NX, $ = function (id) { return document.getElementById(id); };
  var PORT = 3900, URL0 = "http://127.0.0.1:" + PORT;
  function S() { var s = NX.S; s.voice = s.voice || {}; return s.voice; }
  function esc(x) { return NX.esc(String(x == null ? "" : x)); }
  function pad2(n) { return (n < 10 ? "0" : "") + n; }
  function tc(s) { s = Math.max(0, +s || 0); var m = Math.floor(s / 60), r = s - m * 60; return m + ":" + (r < 10 ? "0" : "") + r.toFixed(1); }

  function firstDir(list) { for (var i = 0; i < list.length; i++) { try { if (list[i] && fs.existsSync(list[i])) return list[i]; } catch (e) {} } return list[0]; }
  var HOME = process.env.USERPROFILE || process.env.HOME || ".";
  function repo() { return S().repo || firstDir(["E:/tmp/premiere-mcp-bridge/VoiceStudio", path.join(HOME, "VoiceStudio"), path.join(HOME, "Documents", "VoiceStudio")]); }
  function dataDir() { return S().dataDir || firstDir([path.join(path.dirname(repo()), "VoiceStudio-data"), path.join(process.env.APPDATA || HOME, "OmniVoice")]); }
  function env() {
    var e = Object.assign({}, process.env, { OMNIVOICE_DATA_DIR: dataDir(), PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" });
    if (fs.existsSync("D:/")) { e.HF_HOME = e.HF_HOME || "D:/cache/huggingface"; e.UV_CACHE_DIR = e.UV_CACHE_DIR || "D:/cache/uv"; }
    return e;
  }

  /* ── backend (giữ nguyên cách bật/tắt cũ) ── */
  function health() {
    return new Promise(function (res) {
      var rq = http.get(URL0 + "/health", { timeout: 1500 }, function (r) { r.resume(); res(r.statusCode === 200); });
      rq.on("error", function () { res(false); }); rq.on("timeout", function () { rq.destroy(); res(false); });
    });
  }
  var starting = false, up = false;
  function start() {
    if (starting) return;
    var r = repo();
    if (!fs.existsSync(path.join(r, "backend", "main.py"))) { NX.log("✖ Không thấy VoiceStudio ở " + r + " — sửa đường dẫn ở cuối tab Giọng", "#e5534b"); return; }
    if (!fs.existsSync(path.join(r, ".venv"))) { NX.log("✖ VoiceStudio chưa cài Python (thiếu .venv) — chạy: node scripts/setup-api.mjs trong " + r, "#e5534b"); return; }
    starting = true; state("đang khởi động… (lần đầu ~30–60s)");
    var log = path.join(dataDir(), "jes_backend.log");
    try { fs.mkdirSync(dataDir(), { recursive: true }); } catch (e) {}
    var out, p;
    try {
      out = fs.openSync(log, "a");
      p = cp.spawn("uv", ["run", "--no-sync", "uvicorn", "main:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", String(PORT)],
                   { cwd: r, env: env(), detached: true, windowsHide: true, stdio: ["ignore", out, out] });
    } catch (e) { NX.log("✖ VoiceStudio: " + e.message, "#e5534b"); starting = false; state(); return; }
    finally { try { if (out != null) fs.closeSync(out); } catch (e) {} }     // the backend has its own copy of the log handle
    p.on("error", function (e) { NX.log("✖ VoiceStudio: " + e.message + " (cần uv trong PATH)", "#e5534b"); starting = false; state(); });
    p.unref();
    NX.log("▶ VoiceStudio đang khởi động · log: " + log, "#CCDC42");
    var t0 = Date.now();
    (function wait() {
      health().then(function (ok) {
        if (ok) { starting = false; up = true; state(); loadProfiles(); NX.log("✔ VoiceStudio sẵn sàng (" + Math.round((Date.now() - t0) / 1000) + "s)", "#6fd08c"); return; }
        if (Date.now() - t0 > 240000) { starting = false; state(); NX.log("✖ VoiceStudio không lên sau 4 phút — xem " + log, "#e5534b"); return; }
        setTimeout(wait, 1500);
      });
    })();
  }
  function stop() {
    cp.exec("netstat -ano -p tcp | findstr LISTENING | findstr :" + PORT, { windowsHide: true }, function (err, so) {
      var pids = {}; String(so || "").split(/\r?\n/).forEach(function (l) { var m = /\s(\d+)\s*$/.exec(l); if (m && /:3900\s/.test(l)) pids[m[1]] = 1; });
      var ids = Object.keys(pids);
      if (!ids.length) { up = false; state(); return; }
      ids.forEach(function (id) { cp.exec("taskkill /T /F /PID " + id, { windowsHide: true }, function () {}); });
      setTimeout(function () { up = false; state(); NX.log("■ Đã tắt VoiceStudio (giải phóng VRAM)", "#8b9099"); }, 800);
    });
  }
  function state(msg) {
    var el = $("vsState"); if (!el) return;
    el.className = "vs-pill " + (up ? "on" : starting ? "wait" : "off");
    el.textContent = msg || (up ? "● VoiceStudio đang chạy" : "○ VoiceStudio đang tắt");
    var locked = !running && NX.isBusy && NX.isBusy();          // another job holds the panel lock
    $("vsStart").disabled = up || starting; $("vsStop").disabled = !up;
    $("vsGenAll").disabled = !up || running || locked; $("vsGenMissing").disabled = !up || running || locked; $("vsOpenFull").disabled = !up;
    if ($("vsPlaceAll")) $("vsPlaceAll").disabled = running || locked;
    $("vsSetup").style.display = up ? "none" : "block";
  }

  /* ── HTTP ── */
  function getJSON(p) {
    return new Promise(function (res, rej) {
      var rq = http.get(URL0 + p, { timeout: 8000 }, function (r) {
        var b = []; r.on("data", function (c) { b.push(c); });
        r.on("end", function () { try { res(JSON.parse(Buffer.concat(b).toString("utf8"))); } catch (e) { rej(e); } });
      });
      rq.on("error", rej);
      rq.on("timeout", function () { rq.destroy(new Error("hết thời gian chờ")); });    // the option alone never aborts
    });
  }
  // POST /generate (multipart) → WAV bytes + headers (x-audio-duration)
  function generate(fields) {
    return new Promise(function (res, rej) {
      var bd = "----jesvo" + Date.now().toString(16), parts = [];
      Object.keys(fields).forEach(function (k) {
        if (fields[k] == null || fields[k] === "") return;
        parts.push(Buffer.from("--" + bd + "\r\nContent-Disposition: form-data; name=\"" + k + "\"\r\n\r\n" + fields[k] + "\r\n", "utf8"));
      });
      parts.push(Buffer.from("--" + bd + "--\r\n", "utf8"));
      var body = Buffer.concat(parts);
      var rq = http.request(URL0 + "/generate", { method: "POST", timeout: 600000,
        headers: { "Content-Type": "multipart/form-data; boundary=" + bd, "Content-Length": body.length } }, function (r) {
        var b = []; r.on("data", function (c) { b.push(c); });
        r.on("end", function () {
          var buf = Buffer.concat(b);
          if (r.statusCode !== 200) return rej(new Error("HTTP " + r.statusCode + " " + buf.toString("utf8").slice(0, 160)));
          res({ wav: buf, dur: parseFloat(r.headers["x-audio-duration"] || "0") });
        });
      });
      rq.on("error", rej); rq.on("timeout", function () { rq.destroy(new Error("hết thời gian chờ")); });
      rq.end(body);
    });
  }

  /* ── giọng đọc ── */
  var profiles = [];
  function loadProfiles() {
    if (!up) return;
    getJSON("/profiles").then(function (ps) {
      profiles = ps || [];
      var sel = $("vsProfile"), cur = S().profile || "";
      sel.innerHTML = '<option value="">Giọng mặc định của VoiceStudio</option>' + profiles.map(function (p) {
        return '<option value="' + esc(p.id) + '">' + esc(p.name) + (p.language ? " · " + esc(p.language) : "") + "</option>";
      }).join("");
      sel.value = profiles.some(function (p) { return p.id === cur; }) ? cur : "";
    }).catch(function () {});
  }

  /* ── các câu VO: <out>/<tên>.vo.json do bước Build ghi (TC thật trên timeline) ── */
  var lines = [], srcName = "";
  function voDir() { return path.join(NX.outDir(), "vo"); }
  function wavOf(n) { return path.join(voDir(), "VO_" + pad2(n) + ".wav"); }
  function edits() { var k = NX.currentName(); S().edits = S().edits || {}; S().edits[k] = S().edits[k] || {}; return S().edits[k]; }
  function loadLines() {
    lines = []; srcName = NX.currentName();
    try { lines = JSON.parse(fs.readFileSync(path.join(NX.outDir(), srcName + ".vo.json"), "utf8")); } catch (e) {}
    var ed = edits();
    lines.forEach(function (l, i) {
      l.n = l.n || i + 1;
      if (ed[l.line]) l.text = ed[l.line];
      l.slot = i + 1 < lines.length ? lines[i + 1].s - l.s : null;
      try { var st = fs.statSync(wavOf(l.n)); l.file = wavOf(l.n); l.mtime = st.mtimeMs; } catch (e) { l.file = ""; }
      if (l.file && !l.dur) l.dur = S().durs && S().durs[l.file] || 0;
    });
    render();
  }
  function metaHtml(l) {
    var st = l.busy ? '<span class="vs-st wait">đang tạo…</span>'
           : l.err ? '<span class="vs-st bad" title="' + esc(l.err) + '">lỗi</span>'
           : l.file ? '<span class="vs-st ok">✓ ' + (l.dur ? l.dur.toFixed(1) + "s" : "có file") + "</span>"
           : '<span class="vs-st">chưa tạo</span>';
    return "<b>VO " + pad2(l.n) + "</b><span class=\"muted\">" + tc(l.s) + (l.slot ? " · khoảng " + l.slot.toFixed(1) + "s" : "") + "</span>" +
      st + '<span class="sp"></span>' +
      '<button class="btn vs-play" title="Nghe"' + (l.file ? "" : " disabled") + ">▶</button>" +
      '<button class="btn vs-gen" title="Tạo lại câu này"' + (up && !l.busy && !(running && !l.busy) ? "" : " disabled") + ">↻ Tạo</button>";
  }
  function warnHtml(l) {
    return l.file && l.dur && l.slot && l.dur > l.slot + 0.05
      ? '<div class="vs-warn">Dài hơn khoảng trống tới câu sau (' + l.slot.toFixed(1) + "s) — rút câu hoặc tăng tốc độ</div>" : "";
  }
  var shownLines = null;
  function render() {
    var box = $("vsLines"); if (!box) return;
    $("vsSrc").textContent = lines.length ? srcName + " · " + lines.length + " câu" : "";
    if (!lines.length) {
      shownLines = lines;
      box.innerHTML = '<div class="empty">Chưa có câu VO. Viết <b>[VO] …</b> trong kịch bản → <b>Align</b> → <b>Build</b>, rồi bấm ↻.</div>';
      return;
    }
    // same list on screen → refresh status / buttons / warning per row; the textareas are left alone
    // (generate-all repaints after every line — rebuilding them wiped what the editor was typing)
    var rows = box.querySelectorAll(".vs-line");
    if (shownLines === lines && rows.length === lines.length) {
      lines.forEach(function (l, i) {
        var row = rows[i], meta = row.querySelector(".vs-meta"), w = row.querySelector(".vs-warn"), wh = warnHtml(l);
        if (meta) meta.innerHTML = metaHtml(l);
        if (w && !wh) w.remove();
        else if (wh) { if (w) w.outerHTML = wh; else row.insertAdjacentHTML("beforeend", wh); }
      });
      return;
    }
    shownLines = lines;
    box.innerHTML = lines.map(function (l, i) {
      return '<div class="vs-line" data-i="' + i + '"><div class="vs-meta">' + metaHtml(l) + "</div>" +
        '<textarea class="vs-text" rows="2" spellcheck="false">' + esc(l.text) + "</textarea>" + warnHtml(l) + "</div>";
    }).join("");
  }

  /* ── tạo giọng ── */
  function genOne(l) {
    l.busy = true; l.err = "";
    var row = $("vsLines") && $("vsLines").querySelector('.vs-line[data-i="' + lines.indexOf(l) + '"] .vs-text');
    if (row && row.value.trim() && row.value.trim() !== l.text) { l.text = row.value.trim(); edits()[l.line] = l.text; NX.save(); }   // typed, not yet blurred
    render();
    var spd = +(S().speed || 1);
    return generate({ text: l.text, language: S().lang || "vi", profile_id: S().profile || "", speed: spd.toFixed(2) })
      .then(function (r) {
        fs.mkdirSync(voDir(), { recursive: true });
        fs.writeFileSync(wavOf(l.n), r.wav);
        l.file = wavOf(l.n); l.dur = r.dur; l.busy = false;
        S().durs = S().durs || {}; S().durs[l.file] = r.dur; NX.save();
        render();
      })
      .catch(function (e) { l.busy = false; l.err = e.message; render(); NX.log("✖ VO " + pad2(l.n) + ": " + e.message, "#e5534b"); });
  }
  var running = false;
  // one line from its ↻ button: same panel-wide lock as generate-all (GPU / VoiceStudio is single-job)
  function genSingle(l) {
    if (running || l.busy) return;
    if (NX.tryLock && !NX.tryLock("voice", "Tạo giọng VO")) return NX.log("• Đang chạy: " + NX.busyLabel() + " — tạo giọng sau khi xong", "#e0a82e");
    running = true; state();
    genOne(l).then(function () { running = false; if (NX.unlock) NX.unlock("voice"); state(); render(); });
  }
  function genAll(onlyMissing) {
    if (running || !lines.length) return;
    var todo = lines.filter(function (l) { return !onlyMissing || !l.file; }), k = 0;
    if (!todo.length) return NX.log("• Mọi câu VO đã có file", "#8b9099");
    if (NX.tryLock && !NX.tryLock("voice", "Tạo giọng VO")) return NX.log("• Đang chạy: " + NX.busyLabel() + " — tạo giọng sau khi xong", "#e0a82e");
    running = true; state();
    (function next() {
      if (k >= todo.length) {
        running = false; if (NX.unlock) NX.unlock("voice"); state(); render(); $("vsProg").textContent = "";
        var long = lines.filter(function (l) { return l.dur && l.slot && l.dur > l.slot + 0.05; }).length;
        NX.log("✔ Đã tạo " + todo.length + " câu VO → " + voDir() + (long ? " · ⚠ " + long + " câu dài hơn khoảng trống" : ""), long ? "#e0a82e" : "#6fd08c");
        return;
      }
      $("vsProg").textContent = "Đang tạo " + (k + 1) + "/" + todo.length + "…";
      genOne(todo[k++]).then(next);
    })();
  }
  function placeAll() {
    var items = lines.filter(function (l) { return l.file; }).map(function (l) { return { path: l.file.replace(/\\/g, "/"), t: l.s, name: "VO_" + pad2(l.n) }; });
    if (!items.length) return NX.log("• Chưa có file VO — bấm Tạo giọng trước", "#e0a82e");
    if (running) return NX.log("• Đang tạo giọng — đặt lên timeline sau khi xong", "#e0a82e");
    if (NX.tryLock && !NX.tryLock("voice", "Đặt VO lên timeline")) return NX.log("• Đang chạy: " + NX.busyLabel() + " — đặt VO sau khi xong", "#e0a82e");
    var tr = +(S().track != null ? S().track : 3);
    NX.evalHost("jesPlaceAudio(" + JSON.stringify(JSON.stringify({ track: tr, bin: "JES_VO", items: items })) + ")").then(function (r) {
      var j = JSON.parse(r); if (!j.ok) throw new Error(j.error);
      NX.log("✔ Đặt " + j.placed + "/" + j.total + " câu VO lên " + j.track + " đúng TC" + (j.errors && j.errors.length ? " · lỗi: " + j.errors.join("; ") : ""), "#6fd08c");
    }).catch(function (e) { NX.log("✖ " + e.message, "#e5534b"); })
      .then(function () { if (NX.unlock) NX.unlock("voice"); });
  }
  var player = null;
  function fileUrl(p) { try { return toUrl(p).href; } catch (e) { return "file:///" + String(p).replace(/\\/g, "/"); } }
  function play(p) { if (player) player.pause(); player = new Audio(fileUrl(p) + "?t=" + Date.now()); player.play().catch(function () {}); }

  /* ── audio khác trong VoiceStudio (tạo tay trong giao diện đầy đủ) ── */
  var AUD = /\.(wav|mp3|flac|m4a|ogg|aac)$/i;
  function recent(n) {
    var out = [], roots = [path.join(dataDir(), "outputs"), path.join(dataDir(), "dub_jobs"), path.join(dataDir(), "exports")];
    function walk(d, depth) {
      var ls; try { ls = fs.readdirSync(d); } catch (e) { return; }
      ls.forEach(function (f) {
        var p = path.join(d, f), st; try { st = fs.statSync(p); } catch (e) { return; }
        if (st.isDirectory()) { if (depth < 3) walk(p, depth + 1); }
        else if (AUD.test(f) && st.size > 2000) out.push({ p: p, n: f, t: st.mtimeMs });
      });
    }
    roots.forEach(function (r) { walk(r, 0); });
    return out.sort(function (a, b) { return b.t - a.t; }).slice(0, n || 10);
  }
  var listAt = 0;
  function list(force) {
    var box = $("vsList"); if (!box) return;
    var det = box.closest ? box.closest("details") : null;
    if (!force && det && !det.open) return;                         // folded: walked when opened
    if (!force && Date.now() - listAt < 10000) return;               // the walk is a few hundred stat calls
    listAt = Date.now();
    var items = recent(10); box._items = items;
    box.innerHTML = items.length ? items.map(function (x, i) {
      var age = Math.round((Date.now() - x.t) / 60000);
      return '<div class="vs-row" data-i="' + i + '"><span class="vs-n" title="' + esc(x.p) + '">' + esc(x.n) + '</span><span class="muted">' +
        (age < 60 ? age + " phút" : Math.round(age / 60) + " giờ") + ' trước</span><button class="btn vs-p2" title="Nghe">▶</button>' +
        '<button class="btn vs-put" title="Đặt tại playhead">⤓ Playhead</button></div>';
    }).join("") : '<div class="muted" style="padding:4px 2px">Chưa có audio.</div>';
  }

  /* ── init ── */
  var inited = false;
  function refresh() { health().then(function (ok) { up = ok; state(); if (ok) loadProfiles(); loadLines(); list(false); }); }
  function init() {
    if (inited) return refresh();
    inited = true;
    $("vsStart").onclick = start; $("vsStop").onclick = stop;
    $("vsOpenFull").onclick = function () { cp.exec('start "" "' + URL0 + '/"', { windowsHide: true }); };
    $("vsReload").onclick = function () { loadLines(); list(true); };
    var det = $("vsList").closest ? $("vsList").closest("details") : null;
    if (det) det.addEventListener("toggle", function () { if (det.open) list(false); });
    document.addEventListener("jes:busy", function () { state(); });
    $("vsGenAll").onclick = function () { genAll(false); };
    $("vsGenMissing").onclick = function () { genAll(true); };
    $("vsPlaceAll").onclick = placeAll;
    $("vsProfile").onchange = function () { S().profile = $("vsProfile").value; NX.save(); };
    $("vsLang").value = S().lang || "vi"; $("vsLang").onchange = function () { S().lang = $("vsLang").value; NX.save(); };
    var sp = $("vsSpeed"); sp.value = S().speed || 1; $("vsSpeedV").textContent = (+sp.value).toFixed(2) + "×";
    sp.oninput = function () { S().speed = +sp.value; $("vsSpeedV").textContent = (+sp.value).toFixed(2) + "×"; NX.save(); };
    $("vsTrack").value = String(S().track != null ? S().track : 3);
    $("vsTrack").onchange = function () { S().track = +$("vsTrack").value; NX.save(); };
    $("vsRepo").value = repo(); $("vsRepo").onchange = function () { S().repo = $("vsRepo").value.trim(); NX.save(); };
    $("vsLines").addEventListener("click", function (ev) {
      var row = ev.target.closest(".vs-line"); if (!row) return; var l = lines[+row.getAttribute("data-i")]; if (!l) return;
      if (ev.target.classList.contains("vs-play") && l.file) play(l.file);
      else if (ev.target.classList.contains("vs-gen")) genSingle(l);
    });
    $("vsLines").addEventListener("change", function (ev) {
      if (!ev.target.classList.contains("vs-text")) return;
      var l = lines[+ev.target.closest(".vs-line").getAttribute("data-i")]; if (!l) return;
      l.text = ev.target.value.trim(); edits()[l.line] = l.text; NX.save();
    });
    $("vsList").addEventListener("click", function (ev) {
      var row = ev.target.closest(".vs-row"); if (!row) return; var x = $("vsList")._items[+row.getAttribute("data-i")]; if (!x) return;
      if (ev.target.classList.contains("vs-p2")) play(x.p);
      else if (ev.target.classList.contains("vs-put")) {
        var a = +(S().track != null ? S().track : 3);
        NX.evalHost("jesSequence(1)").then(function (r) {
          var j = JSON.parse(r); if (!j.ok) throw new Error(j.error);
          return NX.evalHost("jesInsertClip(" + JSON.stringify(x.p.replace(/\\/g, "/")) + "," + j.playhead + ",null," + a + ",true)").then(function (r2) {
            var k = JSON.parse(r2); if (!k.ok) throw new Error(k.error); NX.log("✔ Đặt " + x.n + " lên " + k.track, "#6fd08c");
          });
        }).catch(function (e) { NX.log("✖ " + e.message, "#e5534b"); });
      }
    });
    refresh();
    if (S().autoStart) health().then(function (ok) { if (!ok) start(); });
  }
  document.addEventListener("nxtab", function (e) { if (e.detail === "voice") init(); });
  if ($("voice") && $("voice").classList.contains("on")) init();
  window.JESVoice = { start: start, stop: stop, health: health, recent: recent, reload: loadLines };
})();
