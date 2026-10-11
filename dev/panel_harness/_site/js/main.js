/* Justin EDIT Suite — drives nxcut (analysis/build) + nxcore (script alignment) and imports into Premiere. */
(function () {
  "use strict";
  var cs = new CSInterface();
  var fs = require("fs"), path = require("path"), cp = require("child_process");
  var $ = function (id) { return document.getElementById(id); };
  var S = {};                // settings + last inputs
  var P = null, planPath = ""; // current plan

  // ---------- settings ----------
  // portable defaults: installer puts the engine in %LOCALAPPDATA%/JustinEditSuite/engine; legacy dev path kept as fallback
  function firstDir(list) { for (var i = 0; i < list.length; i++) { try { if (list[i] && fs.existsSync(list[i])) return list[i].replace(/\\/g, "/"); } catch (e) {} } return (list[0] || "").replace(/\\/g, "/"); }
  var HOME = process.env.USERPROFILE || process.env.HOME || ".";
  var DEF = { tools: firstDir([
                path.join(process.env.LOCALAPPDATA || HOME, "JustinEditSuite", "engine"),
                path.join(HOME, "Library", "Application Support", "JustinEditSuite", "engine"),
                path.resolve(__dirname, "..", "engine"),
                path.resolve(__dirname, "engine"),
                path.resolve(__dirname, "..", "..", ".."),
                "E:/tmp/premiere-mcp-bridge/JustinEditSuite"
              ]),
              python: process.platform === "darwin" ? "python3" : "python",
              outRoot: fs.existsSync("E:/tmp/premiere-mcp-bridge") ? "E:/tmp/premiere-mcp-bridge" : path.join(HOME, "Documents", "JustinEditSuite").replace(/\\/g, "/"),
              cuda: process.platform === "win32" || process.platform === "darwin", whisperModel: "large-v3-turbo",
              src: "", script: "", name: "", keywords: "", base: "",
              provider: "gemini", apiKey: "", model: "", keys: {}, models: {}, numCtx: 16384, scriptMode: "file", autonomy: "backup", route: {}, lastChat: "",
              preset: "talking_head",
              brief: { type: "", duration: "", platform: "", notes: "" } };
  var AUTO = "__auto__";
  var CFG_DIR = path.join(process.env.APPDATA || process.env.HOME || ".", "JustinEditSuite"), CFG = path.join(CFG_DIR, "settings.json");
  function load() {
    var disk = null, ls = null;
    try { disk = JSON.parse(fs.readFileSync(CFG, "utf8")); } catch (e) {}
    try { ls = JSON.parse(localStorage.getItem("jes") || "null"); } catch (e) {}
    // the newer copy wins (each save stamps _savedAt): a panel closed inside the 300 ms disk debounce left
    // localStorage ahead of settings.json; a settings.json edited / restored by hand is ahead of localStorage
    var dT = (disk && +disk._savedAt) || 0, lT = (ls && +ls._savedAt) || 0;
    S = lT > dT ? Object.assign({}, DEF, disk || {}, ls) : Object.assign({}, DEF, ls || {}, disk || {});
    S.brief = Object.assign({}, DEF.brief, S.brief || {});
    if (!S.migr_preset_ui_v1) {           // v1.9: Chuyển cảnh / Tiếng B-roll / Ducking follow the preset unless picked explicitly
      S.seq = Object.assign({}, S.seq || {}, { trans: "preset", brollDb: "preset" });
      S.sndDuckDb = "preset"; S.migr_preset_ui_v1 = true;
    }
  }
  var saveT = null;
  function flushSave() {
    clearTimeout(saveT); saveT = null;
    try { fs.mkdirSync(CFG_DIR, { recursive: true }); fs.writeFileSync(CFG, JSON.stringify(S, null, 1), "utf8"); } catch (e) {}
  }
  function save() {
    S._savedAt = Date.now();
    try { localStorage.setItem("jes", JSON.stringify(S)); } catch (e) {}
    clearTimeout(saveT);
    saveT = setTimeout(flushSave, 300);    // debounce disk writes (flushed synchronously on unload)
  }
  function bindInputs() {
    ["src", "script", "name", "keywords", "tools", "python", "outRoot", "apiKey", "model", "anthropicWs"].forEach(function (k) {
      if ($(k)) {
        $(k).value = S[k] || "";
        $(k).addEventListener("change", function () {
          S[k] = $(k).value.trim();
          if (k === "name") S.nameAuto = false;            // typed by the editor → never auto-reset
          if (k === "src") srcChanged();
          save(); refreshHints();
        });
      }
    });
    $("provider").value = S.provider || "gemini";
    $("provider").addEventListener("change", function () { S.provider = $("provider").value; save(); });
    ["type", "duration", "platform", "notes"].forEach(function (k) {   // the old brief box left tab Kịch bản in 2.4.3 (S.brief stays: ai.js / queue use it)
      var el = $("brief_" + k); if (!el) return;
      el.value = (S.brief || {})[k] || "";
      el.addEventListener("change", function () { S.brief = S.brief || {}; S.brief[k] = el.value.trim(); save(); });
    });
    $("cuda").checked = !!S.cuda;
    $("cuda").addEventListener("change", function () { S.cuda = $("cuda").checked; save(); });
    if ($("whisperModel")) {
      $("whisperModel").value = S.whisperModel || "large-v3-turbo";
      $("whisperModel").addEventListener("change", function () { S.whisperModel = $("whisperModel").value; save(); });
    }
  }

  var PRESET_DESCS = {
    talking_head: "⚡ Tối ưu: Bỏ qua quét rung trên A-Roll tripod, Whisper chỉ chạy trên clip có giọng nói (VAD), cắt im lặng gap 0.25s, punch-in 112%, ducking -22dB.",
    vlog_travel: "⚡ Tối ưu: Pacing B-roll 3.5s, tự slow-mo 50% cho clip 60p, giữ nat sound -9dB, quét rung đầy đủ cho B-roll.",
    interview_casting: "⚡ Tối ưu: Phỏng vấn 2 máy & casting. Bỏ qua quét rung A-Roll tripod, nhận diện Q&A, chuyển góc multicam 6s.",
    shortform_9x16: "⚡ Tối ưu: Khung dọc 9:16, nhịp siêu nhanh, cắt im lặng cực gắt 0.18s, punch-in 115%, SFX dồn dập, kinetic text.",
    event_recap: "⚡ Tối ưu: Tắt Whisper (tiết kiệm GPU), montage cắt đúng beat nhạc theo trình tự giờ quay, chỉ lấy đoạn ổn định, giữ tiếng không khí -14dB. Chọn nhạc ngay bên dưới."
  };
  // preset JSON (<engine>/presets/<id>.json) — the single source of the preset's numbers (re-read only when the file changes)
  var presetCache = {};
  function presetData(id) {
    id = id || S.preset || "";
    if (!id || /[\\\/]/.test(id)) return {};
    var p = path.join(S.tools || "", "presets", id + ".json"), st;
    try { st = fs.statSync(p); } catch (e) { return {}; }
    var c = presetCache[p];
    if (c && c.m === st.mtimeMs && c.z === st.size) return c.v;
    var v = {}; try { v = JSON.parse(fs.readFileSync(p, "utf8")) || {}; } catch (e) { return c ? c.v : {}; }
    presetCache[p] = { m: st.mtimeMs, z: st.size, v: v };
    return v;
  }
  function presetIds() {
    try { return fs.readdirSync(path.join(S.tools || "", "presets")).filter(function (f) { return /^[\w.-]+\.json$/i.test(f); }).map(function (f) { return f.replace(/\.json$/i, ""); }); }
    catch (e) { return []; }
  }
  function presetDesc(id) {
    if (PRESET_DESCS[id]) return PRESET_DESCS[id];
    var d = presetData(id).description;
    return d ? "⚡ " + d : "";
  }
  function ratioOfSeq(q) {          // preset "seq": {w, h} → nearest ratio of the Khung hình list
    if (!q || !(+q.w > 0) || !(+q.h > 0)) return "";
    var a = +q.w / +q.h, best = "", bd = 1e9;
    Object.keys(RATIOS).forEach(function (k) { var d = Math.abs(Math.log(a / (RATIOS[k][0] / RATIOS[k][1]))); if (d < bd) { bd = d; best = k; } });
    return bd < 0.05 ? best : "";
  }
  function bindPresets() {
    var el = $("presetSel"), desc = $("presetDesc");
    if (!el) return;
    presetIds().forEach(function (id) {            // presets added to the engine later show up without touching the panel
      if (el.querySelector('option[value="' + id + '"]')) return;
      var o = document.createElement("option"); o.value = id; o.textContent = presetData(id).name || id; el.appendChild(o);
    });
    el.value = S.preset || "talking_head";
    if (el.value !== (S.preset || "talking_head")) el.value = "talking_head";
    if (desc) desc.textContent = presetDesc(el.value);
    el.addEventListener("change", function () {
      S.preset = el.value;
      save();
      try { syncChronoUi(); syncAudioUi(); } catch (e) {}
      if (desc) desc.textContent = presetDesc(el.value);
      // Chuyển cảnh / Tiếng B-roll / Ducking left on "Theo preset" follow the new preset by themselves;
      // only the frame is set here, and only when the preset defines one (e.g. short-form → 9:16)
      var r = ratioOfSeq(presetData(el.value).seq);
      if (r && $("seqRatio")) { $("seqRatio").value = r; S.seq = seqCfg(); S.seq.ratio = r; save(); }
      seqInfo();
      try { document.dispatchEvent(new CustomEvent("jes:preset", { detail: el.value })); } catch (e) {}
    });
  }

  // ---------- helpers ----------
  var logFile = null;
  // v1.8: log lines are batched — 1 DOM append + 1 scroll per animation frame, file written async every 0.8 s
  // (was: appendFileSync + forced layout on EVERY line → the whole panel stuttered during Phân tích)
  var LOG_MAX = 800, logQ = [], logRaf = 0, fileQ = [], fileT = 0;
  var LOG_COL = { "#CCDC42": "var(--acc-h)", "#4fd1c5": "var(--acc-h)", "#6fd08c": "var(--ok)", "#8fe0a8": "var(--ok)", "#e0a82e": "var(--warn)",
                  "#f7cf73": "var(--warn)", "#e5534b": "var(--bad)", "#ff6b6b": "var(--bad)", "#8b9099": "var(--dim)", "#8a8f98": "var(--dim)" };
  function flushLog() {
    logRaf = 0;
    var el = $("log"); if (!el || !logQ.length) return;
    var stick = el.scrollHeight - el.scrollTop - el.clientHeight < 40, frag = document.createDocumentFragment();
    logQ.splice(0, logQ.length).forEach(function (x) {
      var line = document.createElement("div"); line.textContent = x[0];
      if (x[1]) line.style.color = LOG_COL[x[1]] || LOG_COL[String(x[1]).toUpperCase()] || x[1];
      frag.appendChild(line);
    });
    el.appendChild(frag);
    var extra = el.childNodes.length - LOG_MAX;
    while (extra-- > 0) el.removeChild(el.firstChild);
    if (stick) el.scrollTop = el.scrollHeight;                           // don't yank the view while the editor reads older lines
  }
  function flushFile() {
    fileT = 0; if (!fileQ.length) return;
    var txt = fileQ.splice(0, fileQ.length).join("");
    try {
      if (!logFile) { var d = path.join(CFG_DIR, "logs"); fs.mkdirSync(d, { recursive: true });
        logFile = path.join(d, new Date().toISOString().slice(0, 10) + ".log"); }
      fs.appendFile(logFile, txt, "utf8", function () {});
    } catch (e) {}
  }
  function log(msg, cls) {
    logQ.push([msg, cls]);
    if (logQ.length > LOG_MAX) logQ.splice(0, logQ.length - LOG_MAX);
    if (!logRaf) logRaf = requestAnimationFrame(flushLog);
    fileQ.push(new Date().toTimeString().slice(0, 8) + "  " + msg + "\n");
    if (!fileT) fileT = setTimeout(flushFile, 800);
  }
  // ---------- child processes (every python / ffmpeg the panel starts is registered → killed with the panel) ----------
  var kids = [];
  function untrackChild(p) { var i = kids.indexOf(p); if (i >= 0) kids.splice(i, 1); }
  function trackChild(p) {
    if (!p || kids.indexOf(p) >= 0) return p;
    kids.push(p);
    try { p.once("exit", function () { untrackChild(p); }); p.once("error", function () { untrackChild(p); }); } catch (e) {}
    return p;
  }
  // kill the whole tree: on Windows "python" is often a launcher shim → kill() alone leaves the real worker running
  function killTree(p, sync) {
    if (!p || p.exitCode != null || p.signalCode) return;
    if (process.platform === "win32" && p.pid) {
      try {
        if (sync) cp.spawnSync("taskkill", ["/PID", String(p.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore", timeout: 4000 });
        else cp.spawn("taskkill", ["/PID", String(p.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" }).on("error", function () { try { p.kill(); } catch (e) {} });
        return;
      } catch (e) {}
    }
    try { p.kill(); } catch (e) {}
  }
  window.addEventListener("beforeunload", function () {
    kids.slice().forEach(function (p) { killTree(p, true); }); kids = [];
    if (saveT) flushSave();                                   // settings typed < 300 ms before closing
    try {
      if (fileQ.length) {
        if (!logFile) { var d = path.join(CFG_DIR, "logs"); fs.mkdirSync(d, { recursive: true }); logFile = path.join(d, new Date().toISOString().slice(0, 10) + ".log"); }
        fs.appendFileSync(logFile, fileQ.join(""), "utf8"); fileQ = [];
      }
    } catch (e) {}
  });
  /* stdout/stderr → whole lines: utf8 decoding on the stream (a Vietnamese char split across two chunks stays intact)
     + a carry-over buffer per stream (a line split across chunks, e.g. "@stage 3/10", is delivered once, whole) */
  function lineReader(stream, onLine) {
    var buf = "";
    try { stream.setEncoding("utf8"); } catch (e) {}
    stream.on("data", function (d) {
      buf += d;
      var ls = buf.split(/\r\n|\n|\r/); buf = ls.pop();
      ls.forEach(onLine);
    });
    return function flush() { if (buf) { var l = buf; buf = ""; onLine(l); } };
  }

  // ---------- busy lock (pipeline · review · sound · text → timeline · voice · agent share ONE lock) ----------
  var locks = {};
  function isBusy() { for (var k in locks) if (locks.hasOwnProperty(k)) return true; return false; }
  function busyLabel() { return Object.keys(locks).map(function (k) { return locks[k]; }).join(", "); }
  function busyChanged() {
    var b = isBusy();
    ["bAnalyze", "bAudio", "bRough", "bAlign", "bBuild", "bAll", "bRebuild"].forEach(function (id) { if ($(id)) $(id).disabled = b; });
    try { document.dispatchEvent(new CustomEvent("jes:busy", { detail: { busy: b, label: busyLabel() } })); } catch (e) {}
  }
  function tryLock(key, label) {
    if (isBusy()) return false;
    cancelled = false;                      // a new job starts with a clean cancel state
    locks[key] = label || key; busyChanged();
    return true;
  }
  function unlock(key) {
    if (!locks.hasOwnProperty(key)) return;
    delete locks[key]; busyChanged();
    if (!isBusy()) progressDone();
  }
  function busyError() { var e = new Error("đang chạy: " + busyLabel() + " — chờ xong rồi thử lại"); e.busy = true; return e; }
  /* run fn() holding the shared lock; rejects at once (e.busy) when something else is running */
  function exclusive(key, label, fn) {
    if (!tryLock(key, label)) return Promise.reject(busyError());
    return Promise.resolve().then(fn).then(function (r) { unlock(key); return r; }, function (e) { unlock(key); throw e; });
  }

  // ---------- progress / cancel ----------
  var runs = [], cancelled = false, prog = { steps: 1, idx: 0, frac: 0 };
  function progress(label, stepsTotal, stepIdx) {
    if (stepsTotal != null) { prog.steps = stepsTotal; prog.idx = stepIdx; prog.frac = 0; }
    $("progress").style.display = "flex";
    if (label) $("pLabel").textContent = label;
    $("pbar").firstChild.style.width = Math.min(100, (prog.idx + prog.frac) / prog.steps * 100) + "%";
  }
  function progressDone() { $("progress").style.display = "none"; $("pbar").firstChild.style.width = "0"; }
  function cancel() {
    cancelled = true;
    if (!runs.length) return;
    runs.slice().forEach(function (h) { h.cancelled = true; killTree(h.child, false); });
    log("■ Đã hủy", "#e0a82e");
  }
  function rawEval(expr) {
    return new Promise(function (res) { cs.evalScript(expr, function (r) { res(r); }); });
  }
  /* host.jsx is loaded by us (not manifest ScriptPath: a failed ScriptPath load breaks the whole engine
     and every evalScript returns "EvalScript error." with no detail). Errors come back with file line. */
  var hostP = null, hostErr = "";
  function loadHost(force) {
    if (hostP && !force) return hostP;
    var hp = (cs.getSystemPath("extension") + "/jsx/host.jsx").replace(/\\/g, "/");
    // must run at GLOBAL scope: $.evalFile inside a function keeps host functions local to that function
    hostP = rawEval("var __jes='';try{var __f=new File(" + JSON.stringify(hp) + ");if(!__f.exists){__jes='ERR file missing: '+__f.fsName;}else{$.evalFile(__f);__jes=(typeof nxPing=='function')?'LOADED':'ERR nxPing undefined after load';}}catch(e){__jes='ERR line '+e.line+': '+e;}__jes")
      .then(function (r) {
        hostErr = r === "LOADED" ? "" : String(r || "no response");
        if (hostErr) log("✖ ExtendScript host: " + hostErr, "#ff6b6b");
        return !hostErr;
      });
    return hostP;
  }
  function evalHost(expr) {
    // uncaught ExtendScript exceptions otherwise surface only as "EvalScript error." -> return them as JSON
    var wrapped = "var __r;try{__r=" + expr + ";}catch(e){__r='{\"ok\":false,\"error\":\"ExtendScript: '+String(e).replace(/[\"\\\\\\r\\n]/g,' ')+(e.line?' (line '+e.line+')':'')+'\"}';}__r";
    return loadHost().then(function () { return rawEval(wrapped); });
  }
  /* one python job of the engine. Each call keeps its own child / cancel handle (several modules may call it),
     the panel-wide Hủy button cancels every running one. */
  function run(args, label) {
    return new Promise(function (resolve, reject) {
      log("▶ " + label, "#CCDC42");
      var env = Object.assign({}, process.env, { PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" });
      if (cancelled) return reject(new Error("đã hủy"));
      var h = { label: label, child: null, cancelled: false }, p, settled = false;
      try { p = h.child = cp.spawn(S.python || "python", args, { cwd: S.tools, env: env, windowsHide: true }); }
      catch (e) { log("✖ " + e.message, "#e5534b"); return reject(e); }
      runs.push(h); trackChild(p);
      var tail = "";
      progress(label);
      var onLine = function (l) {
        if (!l.trim()) return;
        if (l.charAt(0) === "@") { if (window.JESDash) window.JESDash.onStage(l); return; }   // per-stage lanes (analyze)
        var m = l.match(/^\[(\d+)\/(\d+)\]\s*(.*)$/);            // "[i/n] ..." lines -> fine-grained progress
        if (m) {
          prog.frac = +m[1] / +m[2]; progress(label + " · " + m[1] + "/" + m[2] + (m[3] ? " · " + m[3].slice(0, 60) : ""));
          if (+m[1] % 10 && +m[1] !== +m[2] && !/lỗi|error/i.test(l)) { tail = l; return; }   // keep the log readable
        }
        log(l); tail = l;
      };
      var flushOut = lineReader(p.stdout, onLine), flushErr = lineReader(p.stderr, onLine);
      var finish = function () { settled = true; var i = runs.indexOf(h); if (i >= 0) runs.splice(i, 1); untrackChild(p); };
      p.on("error", function (e) { if (settled) return; finish(); log("✖ " + e.message, "#e5534b"); reject(e); });
      p.on("close", function (code) {
        if (settled) return;
        flushOut(); flushErr(); finish();
        if (h.cancelled || cancelled) return reject(new Error("đã hủy"));
        if (code === 0) { log("✔ " + label + " xong"); resolve(tail); }
        else { log("✖ " + label + " lỗi (code " + code + ")", "#e5534b"); reject(new Error(label)); }
      });
    });
  }
  /* quiet python call → Promise<{code, out}> (stdout + stderr lines, in order); onLine sees each line as it comes.
     For short engine calls that answer with a "TAG {json}" line (doctor, learn, ytanalytics…): nothing goes to the
     console log, but the child is tracked so closing the panel still kills it. One helper instead of three copies. */
  function py(args, onLine) {
    return new Promise(function (resolve) {
      var p, out = "", done = false;
      var fin = function (code, extra) { if (done) return; done = true; resolve({ code: code, out: out + (extra || "") }); };
      try {
        p = cp.spawn(S.python || "python", args, { cwd: S.tools, windowsHide: true,
          env: Object.assign({}, process.env, { PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" }) });
      } catch (e) { return fin(-1, String(e && e.message || e)); }
      trackChild(p);
      var each = function (l) { out += l + "\n"; if (onLine) onLine(l); };
      var fo = lineReader(p.stdout, each), fe = lineReader(p.stderr, each);
      p.on("error", function (e) { fin(-1, "\n" + e.message); });
      p.on("close", function (c) { fo(); fe(); untrackChild(p); fin(c); });
    });
  }
  /* py() → the object on its last "TAG {json}" line; rejects when it is missing or carries {error} */
  function pyJSON(args, tag, onLine) {
    return py(args, onLine).then(function (r) {
      var ls = r.out.split(/\r?\n/).filter(function (l) { return l.indexOf(tag + " ") === 0; }), j = null;
      try { j = ls.length ? JSON.parse(ls[ls.length - 1].slice(tag.length + 1)) : null; } catch (e) {}
      if (!j) throw new Error(tag + ": không đọc được kết quả" + (r.out ? " — " + r.out.trim().split(/\r?\n/).pop().slice(0, 160) : ""));
      if (j.error) throw new Error(j.error);
      return j;
    });
  }
  function slug(s) { return String(s).replace(/[\\\/]+$/, "").split(/[\\\/]/).pop().replace(/\s+/g, "_"); }
  function outDir() { return (S.outRoot + "/NX_" + slug(S.src)).replace(/\\/g, "/"); }
  function defaultName() { return slug(S.src).toUpperCase() + "_SCRIPT_v1"; }
  function currentName() { return (S.name || defaultName()).trim(); }
  function planFor(name) { return outDir() + "/plan_" + name + ".json"; }
  function normP(p) { return String(p || "").replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase(); }
  /* the source folder changed (typed, picked, set by the agent): plan / name of the previous folder must not follow */
  var curSrcN = null, curSrcRaw = "";
  function srcChanged(opt) {
    opt = opt || {};
    var n = normP(S.src);
    if (curSrcN === n) return;
    var prev = curSrcRaw; curSrcN = n; curSrcRaw = S.src || "";
    if (!opt.keepName && S.name) {
      // a name the build bumped (X_v1 → X_v2) or the previous folder's default name belongs to that folder
      var oldDef = prev ? slug(prev).toUpperCase() + "_SCRIPT_v" : "";
      if (S.nameAuto || (oldDef && S.name.indexOf(oldDef) === 0 && /^\d+$/.test(S.name.slice(oldDef.length)))) {
        S.name = ""; if ($("name")) { $("name").value = ""; $("name").placeholder = S.src ? defaultName() : ""; }
      }
    }
    S.nameAuto = false;
    if (!opt.noPlan) setPlanPath(S.src ? planFor(currentName()) : "");
    save();
  }
  function setPlanPath(p) {
    planPath = p || ""; P = null;
    try { if (window.JESReview && window.JESReview.reset) window.JESReview.reset(); } catch (e) {}   // old timeline match is void
    if (planPath && fs.existsSync(planPath)) loadPlan();
    else {
      if ($("planInfo")) $("planInfo").textContent = "chưa có plan";
      if ($("segTable")) $("segTable").innerHTML = "";
      if ($("reportPre")) $("reportPre").textContent = "Chưa có report.";
      [["kpiDur", "0.0s"], ["kpiTarget", ""], ["kpiSegs", "0"], ["kpiRatio", ""], ["kpiIssues", "0"], ["kpiIssuesDesc", ""]].forEach(function (k) { if ($(k[0])) $(k[0]).textContent = k[1]; });
      if ($("bFillGaps")) $("bFillGaps").style.display = "none";
    }
  }
  function bump(name, existing) {
    var m = name.match(/^(.*_v)(\d+)$/), base = m ? m[1] : name + "_v", n = m ? +m[2] : 1;
    while (existing.indexOf(base + n) >= 0) n++;
    return base + n;
  }
  function pick(id, folder) {
    var r = folder ? window.cep.fs.showOpenDialogEx(false, true, "Chọn folder source", S.src || "")
                   : window.cep.fs.showOpenDialogEx(false, false, "Chọn kịch bản", S.src || "", ["docx", "md", "txt"]);
    if (r && r.data && r.data.length) { $(id).value = r.data[0].replace(/\\/g, "/"); S[id] = $(id).value;
      if (id === "src") srcChanged();
      save(); refreshHints();
      try { $(id).dispatchEvent(new CustomEvent("jes:set")); } catch (e) {} }
  }

  // ---------- ask the user (modal) ----------
  /* one modal at a time: a second ask() (or the clip picker) waits in line instead of overwriting the open one.
     showModal(fn): fn(box, close) paints the box and calls close() when done. */
  var modalQ = [], modalOn = false;
  function showModal(fn) { modalQ.push(fn); if (!modalOn) nextModal(); }
  function nextModal() {
    var fn = modalQ.shift(), m = $("modal"), box = $("modalBox");
    if (!fn) { modalOn = false; m.style.display = "none"; return; }
    modalOn = true; box.style.maxWidth = ""; box.innerHTML = "";
    var closed = false;
    var close = function () {
      if (closed) return; closed = true;
      box.style.maxWidth = ""; box.innerHTML = ""; m.style.display = "none";
      setTimeout(nextModal, 0);
    };
    try { fn(box, close); m.style.display = "flex"; } catch (e) { log("✖ " + e.message, "#e5534b"); close(); }
  }
  /* ask("Câu hỏi", ["A","B"], {text:true, placeholder:"…"}) -> Promise<string>  ("" if cancelled)
     While the overnight queue runs (js/queue.js) nobody is at the screen: a policy answers instead of a modal, and
     every automatic answer is logged so the morning report can be trusted. */
  var autoAnswer = null;
  function ask(question, options, opt) {
    opt = opt || {};
    if (autoAnswer) {
      var a = "";
      try { a = autoAnswer(String(question || ""), options || [], opt) || ""; } catch (e) { a = ""; }
      log("🤖 Tự trả lời: " + String(question || "").split("\n")[0].slice(0, 90) + " → " + (a || "(bỏ qua)"), "#8b9099");
      return Promise.resolve(a);
    }
    return new Promise(function (resolve) {
      showModal(function (box, close) {
        box.innerHTML = '<div class="q"></div><div class="opts"></div>' +
          (opt.text ? '<div class="row" style="margin-top:8px"><input id="modalText" placeholder="' + esc(opt.placeholder || "Trả lời khác…") +
          '"><button class="btn" id="modalOk">OK</button></div>' : "") +
          '<div style="text-align:right;margin-top:8px"><button class="btn" id="modalCancel">Bỏ qua</button></div>';
        box.querySelector(".q").textContent = question;
        if (opt.html) { var hd = document.createElement("div"); hd.innerHTML = opt.html; box.insertBefore(hd, box.querySelector(".opts")); }
        var o = box.querySelector(".opts");
        (options || []).forEach(function (op) {
          var b = document.createElement("button"); b.className = "btn opt"; b.textContent = op;
          b.onclick = function () { done(op); }; o.appendChild(b);
        });
        function done(v) { close(); resolve(v || ""); }
        if (opt.text) {
          $("modalOk").onclick = function () { done($("modalText").value.trim()); };
          $("modalText").onkeydown = function (e) { if (e.key === "Enter") done($("modalText").value.trim()); };
        }
        $("modalCancel").onclick = function () { done(""); };
      });
    });
  }

  // ---------- hints / status ----------
  function refreshHints() {
    var h = $("tagHint");
    try { syncChronoUi(); syncAudioUi(); } catch (e) {}          // 🕒 checkbox follows the folder + preset
    try { roughHint(); } catch (e) {}
    if (!S.src) { h.textContent = ""; return; }
    var tp = path.join(S.src, "_nexys_work", "broll_tags.json");
    var mp = path.join(S.src, "_nexys_work", "media.json");
    var parts = [];
    parts.push(fs.existsSync(mp) ? "✔ đã phân tích source" : "• chưa phân tích source (bước 1)");
    if (fs.existsSync(tp)) {
      try { var n = Object.keys(JSON.parse(fs.readFileSync(tp, "utf8"))).filter(function (k) { return k[0] !== "_"; }).length;
            parts.push("✔ " + n + " clip đã gắn tag B-roll"); } catch (e) { parts.push("⚠ broll_tags.json lỗi JSON"); }
    } else parts.push("⚠ chưa có broll_tags.json → B-roll sẽ không tự chèn");
    h.textContent = parts.join("  ·  ");
    if (!$("name").value) $("name").placeholder = defaultName();
  }
  function listBases() {
    var sel = $("base"), opts = [["", "(mặc định)"]];
    try {
      fs.readdirSync(S.tools).filter(function (f) { return /^plan.*\.json$/i.test(f); })
        .forEach(function (f) { opts.push([S.tools + "/" + f, f]); });
      fs.readdirSync(S.outRoot).forEach(function (d) {
        var dp = S.outRoot + "/" + d;
        try { if (fs.statSync(dp).isDirectory()) fs.readdirSync(dp).filter(function (f) { return /^plan.*\.json$/i.test(f); })
          .forEach(function (f) { opts.push([dp + "/" + f, d + "/" + f]); }); } catch (e) {}
      });
    } catch (e) {}
    sel.innerHTML = opts.map(function (o) { return '<option value="' + o[0] + '">' + o[1] + "</option>"; }).join("");
    sel.value = S.base || "";
    sel.onchange = function () { S.base = sel.value; save(); };
  }
  function syncMode() {            // the script itself is edited / chosen in tab Kịch bản (js/script.js)
    try { if (window.JESScript && window.JESScript.showCurrent) window.JESScript.showCurrent(); } catch (e) {}
  }
  function baseForRun() {
    // auto mode: no user template chosen -> reuse the casting example for subs/SFX defaults if present
    if (S.scriptMode !== "auto") return S.base;
    var ex = S.tools + "/plan_example_casting.json";
    return fs.existsSync(ex) ? ex : "";
  }
  /* Host check: Premiere may load jsx/host.jsx after the panel -> retry; if nxPing is still missing,
     force-load host.jsx with $.evalFile and surface the real ExtendScript error. Click the status to retry. */
  function ping(n) {
    n = n || 0;
    loadHost(n > 0).then(function (ok) {
      if (!ok) {
        if (n < 3) return setTimeout(function () { ping(n + 1); }, 1500);
        $("status").textContent = "host lỗi — bấm để thử lại"; $("status").className = "err"; $("status").title = hostErr;
        return;
      }
      return rawEval("var __jp;try{__jp=nxPing();}catch(e){__jp='ERR '+e+(e.line?' (line '+e.line+')':'');}__jp").then(function (r) {
        var j = null; try { j = JSON.parse(r); } catch (e) {}
        if (j && j.v) { $("status").textContent = (window.JES_HOST_NAME || "Premiere") + " " + j.v + " · " + (j.project || "(chưa lưu)"); $("status").className = "ok"; $("status").title = j.path || ""; return; }
        if (n < 3) return setTimeout(function () { ping(n + 1); }, 1500);
        $("status").textContent = "host lỗi — bấm để thử lại"; $("status").className = "err"; $("status").title = String(r);
        log("✖ nxPing(): " + String(r), "#ff6b6b");
      });
    });
  }

  // ---------- pipeline ----------
  function needSrc() {
    if (S.src && fs.existsSync(S.src)) return Promise.resolve();
    return ask(S.src ? "Không thấy folder source: " + S.src : "Chưa có folder source để làm việc.", ["Chọn folder…"]).then(function (a) {
      if (a) pick("src", true);
      if (!S.src || !fs.existsSync(S.src)) throw new Error("chưa có folder source");
    });
  }
  function needAnalysis() {
    if (fs.existsSync(path.join(S.src, "_nexys_work", "media.json"))) return Promise.resolve();
    return ask("Source này chưa được phân tích (transcribe, quét rung). Chạy bước 1 trước?", ["Chạy Phân tích (khuyến nghị)", "Hủy"]).then(function (a) {
      if (a.indexOf("Chạy") === 0) return analyze();
      throw new Error("cần phân tích source trước");
    });
  }
  function needScript() {
    if (S.scriptMode === "auto") {
      if (S.script && fs.existsSync(S.script) && /auto_script/.test(S.script)) return Promise.resolve();
      if (!window.NXAI) throw new Error("module AI chưa tải");
      return window.NXAI.autoScript();
    }
    if (S.script && fs.existsSync(S.script)) return Promise.resolve();
    return ask("Chưa có kịch bản. Làm thế nào?", ["✍️ Tự viết / dán kịch bản (tab Kịch bản)", "Chọn file kịch bản…", "✨ AI viết kịch bản từ source", "Hủy"]).then(function (a) {
      if (a.indexOf("Tự viết") >= 0) {
        if (window.JESScript && window.JESScript.setMode) window.JESScript.setMode("manual");
        var tb = document.querySelector('nav button[data-tab="scriptTab"]'); if (tb) tb.click();
        throw new Error("viết kịch bản ở tab Kịch bản rồi bấm Lưu & Align");
      }
      if (a.indexOf("Chọn") === 0) { pick("script", false); if (!S.script) throw new Error("chưa chọn kịch bản"); S.scriptMode = "file"; save(); return; }
      if (a.indexOf("AI") >= 0) { S.scriptMode = "auto"; save(); return window.NXAI.autoScript(); }
      throw new Error("chưa có kịch bản");
    });
  }
  // ---------- shooting-time order (nxcore.chrono) ----------
  // vlog / event presets: on, no question. Other presets: ask ONCE per folder + preset before scanning; the answer lives
  // in <src>/_nexys_work/chrono.json (the engine reads it) and the 🕒 checkbox in the preset card can change it any time.
  var CHRONO_PRESETS = ["vlog_travel", "event_recap", "wedding_highlight", "wedding_full"];
  function chronoPath() { return path.join(S.src || "", "_nexys_work", "chrono.json"); }
  function chronoState() { try { return JSON.parse(fs.readFileSync(chronoPath(), "utf8")); } catch (e) { return null; } }
  function chronoOn() {          // the folder's choice counts for the preset it was made with; otherwise the preset default
    var c = chronoState();
    if (c && typeof c.on === "boolean" && (c.preset || "") === (S.preset || "")) return c.on;
    return CHRONO_PRESETS.indexOf(S.preset) >= 0;
  }
  function setChrono(on, by) {
    if (!S.src) return;
    try {
      fs.mkdirSync(path.dirname(chronoPath()), { recursive: true });
      fs.writeFileSync(chronoPath(), JSON.stringify({ on: !!on, preset: S.preset || "", by: by || "editor", at: new Date().toISOString() }, null, 1), "utf8");
    } catch (e) { log("✖ Không lưu được lựa chọn mạch thời gian: " + e.message, "#e5534b"); }
    syncChronoUi();
  }
  function presetLabel() { var el = $("presetSel"); return el && el.selectedOptions && el.selectedOptions[0] ? el.selectedOptions[0].textContent.trim() : (S.preset || ""); }
  function askChrono() {
    if (!S.src || CHRONO_PRESETS.indexOf(S.preset) >= 0) return Promise.resolve();
    var c = chronoState();
    if (c && typeof c.on === "boolean" && c.preset === (S.preset || "")) return Promise.resolve();     // decided for this folder + preset
    return ask("Preset " + presetLabel() + " — dùng MẠCH THỜI GIAN QUAY cho folder này?\n" +
      "B-roll và kịch bản AI sẽ đi theo trình tự giờ quay (sáng → tối, ngày trước → ngày sau). " +
      "Hợp khi câu chuyện diễn ra theo thời gian (hành trình, một ngày làm việc, sự kiện); phỏng vấn / chia sẻ theo chủ đề thường không cần.",
      ["Có — theo trình tự quay", "Không"]).then(function (a) {
      if (!a) { log("• Chưa chọn mạch thời gian — tạm TẮT, sẽ hỏi lại lần sau", "#e0a82e"); return; }
      var on = /^Có/.test(a);
      setChrono(on, "editor");
      log(on ? "🕒 Mạch thời gian quay: BẬT cho folder này" : "🕒 Mạch thời gian quay: TẮT cho folder này (đổi lại ở thẻ Thể loại video)", "#8b9099");
    });
  }
  function syncChronoUi() {
    var el = $("chronoChk"), note = $("chronoNote"); if (!el) return;
    var c = S.src ? chronoState() : null, on = chronoOn();
    el.checked = on; el.disabled = !S.src;
    if (note) note.textContent = !S.src ? "(chọn folder source)" :
      c && typeof c.on === "boolean" ? "· bạn đã chọn cho folder này" + (c.preset && c.preset !== S.preset ? " (với preset khác — sẽ hỏi lại khi quét)" : "") :
      CHRONO_PRESETS.indexOf(S.preset) >= 0 ? "· mặc định của preset" : "· sẽ hỏi trước khi quét";
  }
  // ---------- audio analysis on / off (per folder) ----------
  // Speech recognition (Whisper), search by sound and who-speaks-when cost GPU time that a music montage of b-roll never
  // uses. Asked ONCE per folder before the first analysis (suggestion from the genre), kept in <src>/_nexys_work/audio.json
  // (the engine reads it too) and runnable on its own later with the 🎙 Âm thanh button.
  var AUDIO_OFF_PRESETS = ["beat_cut"];                      // music only — nobody's words end up in the edit
  function audioPath() { return path.join(S.src || "", "_nexys_work", "audio.json"); }
  function audioState() { try { return JSON.parse(fs.readFileSync(audioPath(), "utf8")); } catch (e) { return null; } }
  function audioOn() { var a = audioState(); return a && typeof a.on === "boolean" ? a.on : AUDIO_OFF_PRESETS.indexOf(S.preset) < 0; }
  function setAudio(on, by) {
    if (!S.src) return;
    try {
      fs.mkdirSync(path.dirname(audioPath()), { recursive: true });
      fs.writeFileSync(audioPath(), JSON.stringify({ on: !!on, by: by || "editor", at: new Date().toISOString() }, null, 1), "utf8");
    } catch (e) { log("✖ Không lưu được lựa chọn âm thanh: " + e.message, "#e5534b"); }
    syncAudioUi();
  }
  function askAudio() {
    var a = audioState();
    if (!S.src || (a && typeof a.on === "boolean")) return Promise.resolve();
    var off = AUDIO_OFF_PRESETS.indexOf(S.preset) >= 0;
    var yes = "Có — cả âm thanh" + (off ? "" : " (gợi ý)"), no = "Không — chỉ hình" + (off ? " (gợi ý)" : "");
    return ask("Phân tích ÂM THANH cho folder này?\n" +
      "Có: nhận dạng lời nói (Whisper), tìm footage theo tiếng, nhận diện người nói — cần khi video có thoại / phỏng vấn / kịch bản.\n" +
      "Không: chỉ hình (ổn định, QC, bố cục) — nhanh hơn, hợp montage nhạc / B-roll. Cần lời nói sau này thì bấm 🎙 Âm thanh.",
      off ? [no, yes] : [yes, no]).then(function (r) {
      if (!r) { log("• Chưa chọn — lần này phân tích cả âm thanh, sẽ hỏi lại lần sau", "#e0a82e"); return; }
      var on = /^Có/.test(r);
      setAudio(on, "editor");
      log(on ? "🎙 Âm thanh: phân tích cho folder này" : "🎙 Âm thanh: BỎ QUA cho folder này (bấm 🎙 Âm thanh khi cần lời nói)", "#8b9099");
    });
  }
  function syncAudioUi() {
    var b = $("bAudio"); if (!b) return;
    var a = S.src ? audioState() : null, note = $("audioNote");
    b.disabled = !S.src;
    b.classList.toggle("on", !!(a && a.on === false));
    if (note) note.textContent = !S.src ? "" : !a || typeof a.on !== "boolean" ? "Âm thanh: sẽ hỏi trước khi phân tích" :
      a.on ? "Âm thanh: có phân tích (lời nói · tìm theo tiếng · người nói)" : "Âm thanh: đang bỏ qua — chỉ hình";
    b.title = !S.src ? "Chọn folder source trước" : a && a.on === false
      ? "Folder này đang BỎ QUA âm thanh — bấm để phân tích riêng phần âm thanh (lời nói, tìm theo tiếng, người nói)"
      : "Phân tích riêng phần âm thanh (lời nói, tìm theo tiếng, người nói) — chỉ clip mới / đổi";
  }
  function audioRun() {
    return needSrc().then(function () {
      setAudio(true, "editor");
      var args = ["-m", "nxcore.analyze", S.src, "--audio-only", "--model", S.whisperModel || "large-v3-turbo"];
      if (S.preset) args.push("--preset", S.preset);
      if (S.cuda) args.push("--cuda");
      return run(args, "Phân tích âm thanh").then(function () { refreshHints(); });
    });
  }
  function analyze() {
    return needSrc().then(askChrono).then(askAudio).then(analyzeRun);
  }
  function visionProvider() {           // same routing rule as ai.js routeOf("vision")
    try { if (window.NXAI && window.NXAI.routeOf) return window.NXAI.routeOf("vision").provider; } catch (e) {}
    var r = (S.route || {}).vision || {};
    return r.provider || S.provider || "gemini";
  }
  function analyzeRun() {
    var src = S.src;
    var args = ["-m", "nxcore.analyze", src, "--model", S.whisperModel || "large-v3-turbo"];
    if (S.preset) args.push("--preset", S.preset);
    if (S.cuda) args.push("--cuda");
    if (!audioOn()) args.push("--no-audio");                   // folder choice: pictures only (🎙 Âm thanh runs it later)
    // 360p proxies only feed the Gemini Files API (video analysis); Ollama / Qwen vision works from frames → no proxies
    if (S.videoAnalysis !== false && visionProvider() === "gemini") args.push("--proxy");
    if (window.JESDash) window.JESDash.resetLanes();
    progress("Phân tích source", 1, 0);
    var done = function () { if (window.JESDash) { window.JESDash.endLanes(); } refreshHints(); };
    return run(args, "Phân tích source (song song, cả thư mục con)").then(function () {
      done();
      try { document.dispatchEvent(new CustomEvent("jes:analyzed", { detail: { src: src } })); } catch (e) {}
    }, function (e) { done(); throw e; });
  }
  function align() {
    var mode = presetMode();
    if (mode !== "script") {
      // montage presets need no script: music (beat) / voice-over (vo) / shooting time (story). An explainer with a
      // script instead of a VO file goes through the script pipeline ([VO] lines + VoiceStudio).
      return needSrc().then(needAnalysis).then(askChrono).then(function () { return needMontageInput(mode); }).then(function (r) {
        return r === "script" ? needScript().then(alignRun) : montageRun(mode);
      });
    }
    return needSrc().then(needAnalysis).then(askChrono).then(needSpeech).then(needScript).then(alignRun);
  }
  // a script edit is cut on the words: a folder analysed "pictures only" must get its speech analysed first
  function needSpeech() {
    if (audioOn()) return Promise.resolve();
    return ask("Kịch bản dựng theo LỜI NÓI, nhưng folder này đã chọn bỏ qua âm thanh khi phân tích. Phân tích âm thanh bây giờ?",
               ["Phân tích âm thanh (khuyến nghị)", "Bỏ qua — dựng không có thoại"]).then(function (a) {
      if (a && a.indexOf("Phân tích") === 0) return audioRun();
    });
  }
  // ---------- montage presets (nxcore.montage): beat_cut / wedding_* / event_recap / explainer_vo ----------
  var AUDIO_EXT = ["mp3", "wav", "m4a", "aif", "aiff", "flac", "aac", "ogg"];
  function presetMode(id) { var m = presetData(id).mode; return m === "beat" || m === "vo" || m === "story" ? m : "script"; }
  function existing(list) { return (list || []).filter(function (f) { try { return f && fs.existsSync(f); } catch (e) { return false; } }); }
  function voDirFiles() {           // VoiceStudio / recorded takes dropped in <output>/vo
    var d = path.join(outDir(), "vo"); try {
      return fs.readdirSync(d).filter(function (f) { return new RegExp("\\.(" + AUDIO_EXT.join("|") + ")$", "i").test(f); })
        .sort().map(function (f) { return path.join(d, f).replace(/\\/g, "/"); });
    } catch (e) { return []; }
  }
  function pickAudio(title, multi) {
    var r = window.cep.fs.showOpenDialogEx(!!multi, false, title, S.src || "", AUDIO_EXT);
    return r && r.data && r.data.length ? r.data.map(function (f) { return f.replace(/\\/g, "/"); }) : [];
  }
  function montageUi() {
    var mode = presetMode(), row = $("montageRow"); if (!row) return;
    row.style.display = mode === "vo" ? "" : "none";             // ♪ music: tab Kịch bản → Cắt theo nhạc
    var lbl = $("montageLbl"), f = $("montageFile"), info = $("montageInfo");
    if (mode === "beat") {
      var m = existing([S.montageMusic])[0];
      lbl.textContent = "♪ Nhạc"; f.value = m ? path.basename(m) : ""; f.title = m || "";
      f.placeholder = "chưa chọn — Align sẽ hỏi (không nhạc = lưới 120 BPM)";
    } else if (mode === "vo") {
      var v = existing(S.montageVo);
      lbl.textContent = "🎙 VO"; f.value = v.length ? v.map(function (x) { return path.basename(x); }).join(", ") : ""; f.title = v.join("\n");
      f.placeholder = "file voice-over (hoặc để trống + kịch bản [VO] → VoiceStudio)";
    }
    if (info) {
      var pd = presetData(), t = briefSeconds(S.brief && S.brief.duration) || pd.target_dur || 0;
      var mu = existing([S.montageMusic])[0];
      info.textContent = mode === "beat" ? "♪ " + (mu ? path.basename(mu) : "chưa chọn nhạc") + " — chọn nhạc, nhịp, shuffle ở tab Kịch bản → Cắt theo nhạc · " +
          (t ? "dài ~" + t + "s" : "hết bài") +
          (pd.speech_moments ? " · " + pd.speech_moments + " câu nói chèn, nhạc tự duck" : " · không thoại") + (pd.nat_sound ? "" : " · tắt tiếng gốc") :
        mode === "vo" ? "VO → câu → B-roll khớp nội dung · phụ đề từ VO" + (existing([S.montageMusic])[0] ? " · nhạc nền duck" : "") :
        mode === "story" ? "Theo giờ quay · giữ thoại hay nhất · ~" + Math.round((t || 1500) / 60) + " phút" : "";
      info.style.display = mode === "script" ? "none" : "";
    }
  }
  function bindMontage() {
    if (!$("montagePick")) return;
    $("montagePick").addEventListener("click", function () {
      var mode = presetMode();
      if (mode === "beat") { var m = pickAudio("Chọn nhạc cho montage", false); if (m.length) { S.montageMusic = m[0]; save(); } }
      else if (mode === "vo") { var v = pickAudio("Chọn file voice-over (chọn nhiều file = nối theo tên)", true); if (v.length) { S.montageVo = v.sort(); save(); } }
      montageUi();
    });
    $("montageClear").addEventListener("click", function () {
      if (presetMode() === "vo") S.montageVo = []; else S.montageMusic = "";
      save(); montageUi();
    });
    document.addEventListener("jes:preset", montageUi);
    montageUi();
  }
  function needMontageInput(mode) {
    if (mode === "story") return Promise.resolve("");
    if (mode === "beat") {
      if (existing([S.montageMusic]).length) return Promise.resolve("");
      var snd = S.sndMusic && S.sndMusic !== "__timeline__" && existing([S.sndMusic]).length ? S.sndMusic : "";
      var opts = ["Chọn file nhạc…"].concat(snd ? ["Dùng nhạc tab Âm thanh: " + path.basename(snd)] : []).concat(["Không nhạc (lưới 120 BPM)", "Hủy"]);
      return ask("Preset " + presetLabel() + " dựng theo nhạc — cắt đúng beat, nhịp theo intro/build/drop. Chọn nhạc:", opts).then(function (a) {
        if (a.indexOf("Chọn") === 0) { var m = pickAudio("Chọn nhạc cho montage", false); if (!m.length) throw new Error("chưa chọn nhạc"); S.montageMusic = m[0]; }
        else if (a.indexOf("Dùng nhạc") === 0) S.montageMusic = snd;
        else if (a.indexOf("Không nhạc") === 0) S.montageMusic = "";
        else throw new Error("đã hủy");
        save(); montageUi(); return "";
      });
    }
    // vo
    if (existing(S.montageVo).length) return Promise.resolve("");
    var inDir = voDirFiles();
    var hasScript = S.script && fs.existsSync(S.script);
    var o2 = ["Chọn file VO…"].concat(inDir.length ? ["Dùng " + inDir.length + " file trong output/vo (VoiceStudio)"] : [])
      .concat(hasScript ? ["Dùng kịch bản " + path.basename(S.script) + " ([VO] → VoiceStudio)"] : ["Viết / chọn kịch bản có dòng [VO]"]).concat(["Hủy"]);
    return ask("Explainer cần voice-over: file thu sẵn / VoiceStudio, hoặc kịch bản có dòng [VO].", o2).then(function (a) {
      if (a.indexOf("Chọn file") === 0) { var v = pickAudio("Chọn file voice-over", true); if (!v.length) throw new Error("chưa chọn VO"); S.montageVo = v.sort(); save(); montageUi(); return ""; }
      if (a.indexOf("Dùng " + inDir.length + " file") === 0) { S.montageVo = inDir; save(); montageUi(); return ""; }
      if (/kịch bản/.test(a)) return "script";
      throw new Error("đã hủy");
    });
  }
  function montageRun(mode) {
    var name = currentName(); planPath = planFor(name);
    try { fs.mkdirSync(outDir(), { recursive: true }); } catch (e) {}
    var sq = seqSpec();
    var args = ["-m", "nxcore.montage", "--src", S.src, "--preset", S.preset, "--plan", planPath, "--name", name, "--out", outDir(), "--seq", sq.spec];
    var mu = existing([S.montageMusic])[0];
    if (mu && (mode === "beat" || mode === "vo")) args.push("--music", mu);
    if (mode === "vo") existing(S.montageVo).forEach(function (v) { args.push("--vo", v); });
    // tab Kịch bản › Cắt theo nhạc: độ dài riêng (0 = theo preset / hết bài) + override montage (pace, câu nói chèn…)
    var tgt = S.montageTarget != null && S.montageTarget !== "" ? +S.montageTarget || 0 : briefSeconds(S.brief && S.brief.duration);
    if (tgt) args.push("--target", String(tgt));
    if (mode === "beat" && S.montageCfg && Object.keys(S.montageCfg).length) {
      var cf = path.join(S.src, "_nexys_work", "montage_cfg.json");
      try { fs.writeFileSync(cf, JSON.stringify(S.montageCfg), "utf8"); args.push("--cfg", cf); } catch (e) {}
    }
    if (S.cuda) args.push("--cuda");
    args.push("--model", S.whisperModel || "large-v3-turbo");
    var label = { beat: "Montage theo nhạc", vo: "Explainer: VO → B-roll", story: "Phim theo giờ quay" }[mode];
    progress(label, 1, 0);
    return run(args, label + " → plan").then(loadPlan).then(function (r) { if (window.JESDash) window.JESDash.render(); return r; });
  }
  /* "~60s", "90 giây", "2–3 phút", "1:30" -> seconds (range -> midpoint) */
  function briefSeconds(d) {
    if (!d) return 0;
    var t = String(d).toLowerCase().replace(/,/g, "."), m;
    if ((m = t.match(/(\d+):(\d{2})/))) return +m[1] * 60 + +m[2];
    var nums = (t.match(/\d+(\.\d+)?/g) || []).map(Number);
    if (!nums.length) return 0;
    var v = nums.length > 1 ? (nums[0] + nums[1]) / 2 : nums[0];
    return /ph[uú]t|min|'/.test(t) ? Math.round(v * 60) : Math.round(v);
  }
  // ---------- sequence frame (ratio / resolution / fps / fit) + b-roll sound ----------
  var RATIOS = { "9:16": [9, 16], "16:9": [16, 9], "1:1": [1, 1], "4:5": [4, 5], "4:3": [4, 3], "21:9": [21, 9] };
  // brollDb / trans = "preset" (default): the preset's broll_nat_gain_db / transitions are used; any other value is the editor's override
  function seqCfg() { return Object.assign({ ratio: "auto", res: "1080", fps: "auto", fit: "fill", brollDb: "preset", trans: "preset" }, S.seq || {}); }
  var mediaCache = { p: "", m: 0, z: 0, v: {} };
  function activeMedia() {            // parsed once per media.json change (seqInfo runs after every dashboard repaint)
    var p = path.join(S.src || "", "_nexys_work", "media.json"), st;
    try { st = fs.statSync(p); } catch (e) { mediaCache = { p: "", m: 0, z: 0, v: {} }; return {}; }
    if (mediaCache.p === p && mediaCache.m === st.mtimeMs && mediaCache.z === st.size) return mediaCache.v;
    var v; try { v = JSON.parse(fs.readFileSync(p, "utf8")) || {}; } catch (e) { return mediaCache.p === p ? mediaCache.v : {}; }
    mediaCache = { p: p, m: st.mtimeMs, z: st.size, v: v };
    return v;
  }
  function presetBrollDb() { var v = presetData().broll_nat_gain_db; return typeof v === "number" ? v : null; }
  function presetTransitions() { var t = presetData().transitions; return t && typeof t === "object" ? t : null; }
  function sourceRatio() {          // orientation of most footage (by duration) among the clips in use
    var med = activeMedia(), w = { "9:16": 0, "16:9": 0, "1:1": 0 };
    Object.keys(med).forEach(function (f) {
      var m = med[f], a = (+m.w || 1) / (+m.h || 1), d = +m.dur || 1;
      w[a > 1.15 ? "16:9" : a < 0.87 ? "9:16" : "1:1"] += d;
    });
    var best = Object.keys(w).sort(function (a, b) { return w[b] - w[a]; })[0];
    return w[best] ? best : "9:16";
  }
  function sourceFps() {
    var med = activeMedia(), w = {};
    Object.keys(med).forEach(function (f) {
      var r = +med[f].fps || 0, d = +med[f].dur || 1, k = r >= 47 && r < 55 ? "25" : r >= 55 ? "29.97" : r >= 24.5 && r < 26 ? "25" : r > 23 && r < 24.5 ? "23.976" : "29.97";
      w[k] = (w[k] || 0) + d;
    });
    return Object.keys(w).sort(function (a, b) { return w[b] - w[a]; })[0] || "29.97";
  }
  function seqSpec() {
    var c = seqCfg(), ratio = c.ratio === "auto" ? sourceRatio() : c.ratio, r = RATIOS[ratio] || RATIOS["9:16"], short = +c.res || 1080;
    var W = r[0] >= r[1] ? Math.round(short * r[0] / r[1] / 2) * 2 : short, H = r[0] >= r[1] ? short : Math.round(short * r[1] / r[0] / 2) * 2;
    var fps = c.fps === "auto" ? sourceFps() : c.fps;
    var byPreset = c.brollDb === "preset" || c.brollDb === "" || c.brollDb == null;
    var bdb = byPreset ? presetBrollDb() : +c.brollDb;
    return { w: W, h: H, fps: +fps, fit: c.fit, spec: W + "x" + H + "@" + fps + ":" + c.fit, brollDb: bdb, brollPreset: byPreset, ratio: ratio, auto: c.ratio === "auto" };
  }
  function seqInfo() {
    var q = seqSpec(), el = $("seqInfo"); if (!el) return;
    var c = seqCfg(), tm = c.trans || "preset", db = q.brollDb;
    var dbTxt = db == null ? "theo preset" : (db <= -90 ? "tắt tiếng" : db + " dB") + (q.brollPreset ? " · theo preset" : "");
    var trTxt;
    if (tm === "preset") {
      var pt = presetTransitions();
      trTxt = !pt ? " · chuyển cảnh theo plan" : pt.on === false ? " · cắt thẳng (theo preset)" :
        " · dissolve " + (pt.dur ? pt.dur + "s " : "") + "khi đổi cảnh" + (pt.broll_rate ? " + " + Math.round(pt.broll_rate * 100) + "% montage" : "") + " (theo preset)";
    } else trTxt = { auto: " · dissolve khi đổi cảnh + một phần montage, fade đầu/cuối", scene: " · dissolve khi đổi cảnh, fade đầu/cuối", off: " · cắt thẳng" }[tm] || "";
    el.textContent = "Sequence " + q.w + "×" + q.h + (q.auto ? " (" + q.ratio + " theo source)" : "") + " · " + q.fps + " fps · " + (q.fit === "fit" ? "vừa khung" : "lấp đầy") +
      " · mọi clip kèm âm thanh gốc (thoại A1, tiếng B-roll A2 " + dbTxt + ")" + trTxt;
    var pdb = presetBrollDb(), o1 = $("seqBrollDb") && $("seqBrollDb").querySelector('option[value="preset"]');
    if (o1) o1.textContent = "Theo preset" + (pdb != null ? " (" + pdb + " dB)" : "");
  }
  function selectValue(el, v) {      // a saved value missing from the list (older version / custom) is added, not silently dropped
    if (v == null || v === "") return;
    if (el.tagName === "SELECT" && !Array.prototype.some.call(el.options, function (o) { return o.value === String(v); })) {
      var o = document.createElement("option"); o.value = String(v); o.textContent = String(v) + (/Db$/.test(el.id) && /^-?\d/.test(String(v)) ? " dB" : ""); el.appendChild(o);
    }
    el.value = String(v);
  }
  function bindSeq() {
    var c = seqCfg(), map = { seqRatio: "ratio", seqRes: "res", seqFps: "fps", seqFit: "fit", seqBrollDb: "brollDb", seqTrans: "trans" };
    Object.keys(map).forEach(function (id) {
      var el = $(id); if (!el) return;
      selectValue(el, c[map[id]]);
      el.addEventListener("change", function () { S.seq = seqCfg(); S.seq[map[id]] = el.value; save(); seqInfo(); });
    });
    seqInfo();
  }
  function alignRun() {
    var name = currentName(); planPath = planFor(name);
    try { fs.mkdirSync(outDir(), { recursive: true }); } catch (e) {}
    var args = ["-m", "nxcore.run", "--src", S.src, "--script", S.script, "--plan", planPath, "--name", name, "--out", outDir()];
    if (S.preset) args.push("--preset", S.preset);
    var b = baseForRun(); if (b) args.push("--base", b);
    if (S.keywords) args.push("--keywords", S.keywords);
    var sq = seqSpec(); args.push("--seq", sq.spec);
    if (!sq.brollPreset && sq.brollDb != null && !isNaN(sq.brollDb)) args.push("--broll-audio-db", String(sq.brollDb));   // explicit override only
    var tgt = briefSeconds(S.brief && S.brief.duration);
    if (tgt) {
      var cfgp = outDir() + "/_align_cfg.json";
      try { fs.writeFileSync(cfgp, JSON.stringify({ assemble: { target_dur: tgt } }), "utf8"); args.push("--cfg", cfgp); } catch (e) {}
    }
    progress("Align kịch bản → plan", 1, 0);
    return run(args, "Align kịch bản → plan").then(loadPlan).then(function (r) { if (window.JESDash) window.JESDash.render(); return r; });
  }
  var forcePlan = null;          // NX.buildPlan(): build exactly this plan file (template / rough), wherever it lives
  function build() {
    var forced = forcePlan; forcePlan = null;
    if (forced) planPath = forced;
    // a plan of another source folder (folder switched after Align) is never built for this one
    if (!forced && (!planPath || !fs.existsSync(planPath) || normP(path.dirname(planPath)) !== normP(outDir()))) planPath = planFor(currentName());
    if (!fs.existsSync(planPath)) {
      return ask("Chưa có plan cho " + currentName() + ". Chạy Align trước rồi Build?", ["Chạy Align rồi Build", "Hủy"]).then(function (a) {
        if (a.indexOf("Chạy") === 0) return align().then(build);
        throw new Error("chưa có plan");
      });
    }
    return evalHost("nxSeqNames()").then(function (r) {
      var names = []; try { names = JSON.parse(r); } catch (e) {}
      P = JSON.parse(fs.readFileSync(planPath, "utf8"));
      if (!forced && P.src && S.src && normP(P.src) !== normP(S.src)) throw new Error("plan " + path.basename(planPath) + " thuộc folder khác (" + P.src + ") — chạy Align cho folder này");
      if (names.indexOf(P.name) >= 0) {           // never overwrite an edited sequence
        var nn = bump(P.name, names);
        log("• " + P.name + " đã có trong project → build thành " + nn);
        P.name = nn; var np = forced ? (path.dirname(planPath) + "/plan_" + nn + ".json").replace(/\\/g, "/") : planFor(nn);
        fs.writeFileSync(np, planJSON(P), "utf8");
        var rp = planPath.replace(/\.json$/, "_align.md");
        if (fs.existsSync(rp)) fs.copyFileSync(rp, np.replace(/\.json$/, "_align.md"));
        planPath = np; S.name = nn; S.nameAuto = true; $("name").value = nn; save();
      }
      var sq = seqSpec();                        // frame / fps chosen now win over what the plan was aligned with
      var tpl = P.mode === "template";            // template từ video tham khảo: giữ khung / chuyển cảnh / tiếng b-roll của chính plan
      if (!tpl) P.seq = Object.assign({}, P.seq || {}, { w: sq.w, h: sq.h, fps: sq.fps, fit: sq.fit });
      // b-roll sound / transitions: an explicit pick in the panel wins; "Theo preset" = the preset's numbers (plan's own when the preset has none)
      var rough = P.mode === "rough";              // rough cut: straight cuts, every clip with its own sound, whatever the preset says
      var noNat = !rough && P.mode && P.mode !== "script" && presetData().nat_sound === false;   // beat_cut: music only, never nat sound
      if (tpl) { /* plan đã đặt tiếng b-roll dưới nhạc dẫn */ }
      else if (sq.brollDb != null && !isNaN(sq.brollDb) && !noNat) P.broll_audio = { on: true, gain_db: sq.brollDb };
      else if (noNat && !sq.brollPreset) log("• Preset " + presetLabel() + " chỉ dùng nhạc → bỏ qua mức tiếng B-roll " + sq.brollDb + " dB đã chọn", "#8b9099");
      var tm = seqCfg().trans || "preset", tr = Object.assign({ on: true, dur: 0.8, scene: true, broll_rate: 0.3, fade_in: 0.8, fade_out: 1.0, audio: true }, P.transitions || {});
      if (tm === "preset") { var pt = presetTransitions(); if (pt) tr = Object.assign(tr, pt); }
      else { tr.on = tm !== "off"; if (tm === "scene") tr.broll_rate = 0; else if (!tr.broll_rate) tr.broll_rate = 0.3; }
      var ds = window.JESStyle && window.JESStyle.param("dissolve_s");   // 📒 Sổ luật beats the preset's dissolve length
      if (ds) tr.dur = ds;
      P.transitions = rough ? Object.assign({}, P.transitions || {}, { on: false }) : tpl ? (P.transitions || tr) : tr;
      fs.writeFileSync(planPath, planJSON(P), "utf8");
      log("• Sequence " + sq.w + "×" + sq.h + " @" + sq.fps + " · " + (sq.fit === "fit" ? "vừa khung" : "lấp đầy") + " · mọi clip kèm âm thanh");
      var jsx = path.dirname(planPath).replace(/\\/g, "/") + "/" + P.name + "_import.jsx";
      progress("Build XML + SRT + highlight", 2, 0);
      return run(["nxcut.py", "build", planPath, "--jsx-out", jsx], "Build XML + SRT + highlight").then(function () {
        var HN = window.JES_HOST_NAME || "Premiere";
        progress("Import vào " + HN, 2, 1);
        log("▶ Import vào " + HN + "…", "#CCDC42");
        // DaVinci Resolve: import FCP XML qua API (ImportTimelineFromFile) + tô màu clip theo plan — không có ExtendScript
        if (window.JES_HOST === "resolve") return evalHost("nxImportXml(" + JSON.stringify(path.dirname(planPath).replace(/\\/g, "/") + "/" + P.name + ".xml") + "," + JSON.stringify(planPath) + ")");
        var code = fs.readFileSync(jsx, "utf8");
        return evalHost("nxRunScript(" + JSON.stringify(code) + ")");
      }).then(function (r) {
        try {
          var j = JSON.parse(r);
          if (j.error) { log("✖ " + j.error, "#e5534b"); return; }
          log("✔ Sequence " + j.seq.name + " · " + j.seq.end.toFixed(1) + "s · V " + j.seq.v.join("/") + " · A " + j.seq.a.join("/") +
              (j.colored != null ? " · tô màu " + j.colored + " clip" : "") + (j.markers ? " · " + j.markers + " marker" : ""));
        } catch (e) { log("✔ import: " + r); }
        loadPlan();
      });
    });
  }
  /* ---------- rough cut: every usable stretch of the whole source, in shooting order, clip colour by role (no script) ---------- */
  function hms(x) { x = Math.round(+x || 0); var h = Math.floor(x / 3600), m = Math.floor(x % 3600 / 60), s = x % 60; return (h ? h + ":" + (m < 10 ? "0" : "") : "") + m + ":" + (s < 10 ? "0" : "") + s; }
  function roughBase() { return currentName().replace(/_ROUGH(_v?\d+)?$/i, ""); }
  function roughPlan() { return S.src ? planFor(roughBase() + "_ROUGH") : ""; }
  function roughHint() {
    var el = $("roughHint"); if (!el) return;
    var rp = roughPlan(), rep = null;
    try { rep = rp && JSON.parse(fs.readFileSync(rp.replace(/\.json$/, ".rough.json"), "utf8")); } catch (e) {}
    if (!rep || !rep.legend) { el.style.display = "none"; el.innerHTML = ""; return; }
    var HEX = { Violet: "#a990dd", Iris: "#6f7ee0", Caribbean: "#29d698", Lavender: "#e384e6", Cerulean: "#1ba4e6", Forest: "#519e2c",
      Rose: "#f4639e", Mango: "#f39a2d", Purple: "#9b59d0", Blue: "#3a6fe0", Teal: "#29b6b6", Magenta: "#e0359b", Tan: "#c9a77c",
      Green: "#5fbf3f", Brown: "#9b6b3f", Yellow: "#e4d84c" };
    var pct = rep.source ? Math.round(100 * rep.total / rep.source) : 0;
    var items = Object.keys(rep.legend).map(function (k) {
      var lg = rep.legend[k], n = (rep.roles || {})[k] || 0;
      return '<span title="label ' + esc(lg[0]) + '"' + (n ? "" : ' class="muted"') + '><i style="background:' + (HEX[lg[0]] || "#888") + '"></i>' + esc(lg[1]) + " " + n + "</span>";
    }).join("");
    var sk = rep.skipped ? Object.keys(rep.skipped).map(function (k) { return k + " " + rep.skipped[k]; }).join(" · ") : "";
    el.innerHTML = "Rough cut: <b>" + rep.segments + "</b> đoạn · " + hms(rep.total) + " / " + hms(rep.source) + " source (" + pct + "%)" +
      '<div class="rleg">' + items + "</div>" + (sk ? '<div class="muted">Bỏ qua: ' + esc(sk) + "</div>" : "");
    el.style.display = "";
  }
  function roughCut() {
    return needSrc().then(needAnalysis).then(function () {
      var base = roughBase(), name = base + "_ROUGH", rp = planFor(name);
      try { fs.mkdirSync(outDir(), { recursive: true }); } catch (e) {}
      var sq = seqSpec(), keep = { name: S.name, auto: S.nameAuto };
      progress("Rough cut theo giờ quay", 2, 0);
      return run(["-m", "nxcore.rough", S.src, "--plan", rp, "--name", name, "--out", outDir(), "--seq", sq.spec], "Rough cut theo giờ quay").then(function () {
        if (!fs.existsSync(rp)) throw new Error("rough cut không tạo được plan");
        var R = JSON.parse(fs.readFileSync(rp, "utf8"));
        if (!R.segments || !R.segments.length) throw new Error("không có đoạn nào đạt chuẩn — kiểm tra bước Phân tích (QC/ổn định) đã xong chưa");
        planPath = rp; roughHint();
        return build();
      }).then(function (r) {
        // the editor's sequence name stays theirs: build() may rename to X_ROUGH(_v2) — that belongs to the rough sequence only
        S.name = keep.name; S.nameAuto = keep.auto; if ($("name")) $("name").value = keep.name || ""; save();
        if (window.JESDash) window.JESDash.render();
        return r;
      });
    });
  }
  /* build một plan có sẵn (template / plan ngoài folder source); tên sequence chính của editor giữ nguyên */
  function buildPlan(p) {
    var keep = { name: S.name, auto: S.nameAuto };
    forcePlan = p;
    return build().then(function (r) {
      S.name = keep.name; S.nameAuto = keep.auto; if ($("name")) $("name").value = keep.name || ""; save();
      if (window.JESDash) window.JESDash.render();
      return r;
    });
  }
  /* button handler around a pipeline job: shared lock, clean cancel state, errors (even synchronous throws) logged,
     progress hidden + session saved at the end */
  function guard(fn, label) {
    return function () {
      if (isBusy()) { log("• Đang chạy: " + busyLabel() + " — chờ xong rồi bấm lại", "#e0a82e"); return Promise.resolve(); }
      return exclusive("pipeline", label || "tác vụ pipeline", fn)
        .catch(function (e) { log("✖ " + (e && e.message || e), "#e5534b"); })
        .then(function () {                       // (unlock already hid the progress bar)
          try { if (window.JESSession) window.JESSession.snap(); } catch (e) {}
        });
    };
  }

  // ---------- review ----------
  // runtime-only fields (timeline sync, timeline positions, order check) never go into plan_*.json
  var RUNTIME_KEYS = { _shot: 1, _shots: 1, _gone: 1, _tl_start: 1, _tl_end: 1, _back: 1 };
  function planJSON(plan) { return JSON.stringify(plan, function (k, v) { return RUNTIME_KEYS[k] ? undefined : v; }, 1); }
  function loadPlan() {
    if (!planPath) planPath = planFor(currentName());
    if (!fs.existsSync(planPath)) { $("planInfo").textContent = "chưa có plan"; return; }
    var np = JSON.parse(fs.readFileSync(planPath, "utf8"));
    if (np && np.src && S.src && normP(np.src) !== normP(S.src)) {        // a plan of another source folder never becomes current
      log("• " + path.basename(planPath) + " thuộc folder khác (" + np.src + ") — bỏ qua", "#e0a82e");
      var own = planFor(currentName()), was = planPath;
      planPath = own; P = null; $("planInfo").textContent = "chưa có plan";
      if (normP(own) !== normP(was) && fs.existsSync(own)) loadPlan();     // (two folders with the same name share an output dir)
      return;
    }
    P = np;
    loadPlanInfo();
    renderReview();
    var rp = planPath.replace(/\.json$/, "_align.md");
    $("reportPre").textContent = fs.existsSync(rp) ? fs.readFileSync(rp, "utf8") : "Chưa có report.";
  }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function formatTime(s) {
    if (s == null || isNaN(s)) return "00:00";
    s = Math.max(0, Number(s));
    var m = Math.floor(s / 60);
    var rem = s - m * 60;
    var secInt = Math.floor(rem);
    var tenths = Math.floor((rem - secInt) * 10);
    var mm = (m < 10 ? "0" : "") + m;
    var ss = (secInt < 10 ? "0" : "") + secInt;
    return mm + ":" + ss + "." + tenths;
  }
  function round2(v) { return Math.round(Number(v) * 100) / 100; }
  function getScoreBadge(score) {
    if (score == null || isNaN(score)) return "";
    var val = Math.round(score * 100);
    var cls = score >= 0.75 ? "score-high" : (score >= 0.60 ? "score-mid" : "score-low");
    return '<span class="score-pill ' + cls + '" title="Độ khớp kịch bản: ' + val + '%">' + val + '%</span>';
  }

  function nudgeSeg(i, side, delta) {
    if (!P || !P.segments || !P.segments[i]) return;
    var s = P.segments[i];
    if (s.gap) return;
    if (side === "in") {
      var newIn = Math.max(0, round2(s["in"] + delta));
      if (newIn >= s.out - 0.2) return;
      s["in"] = newIn;
    } else if (side === "out") {
      var newOut = Math.max(round2(s["in"] + 0.2), round2(s.out + delta));
      s.out = newOut;
    }
    delete s.tl;                 // montage: a trimmed shot keeps its new length (cuts after it move with it)
    savePlan();
    log("• Đoạn #" + i + " " + side.toUpperCase() + (delta > 0 ? " +" : " ") + delta + "s → " + s["in"].toFixed(2) + "–" + s.out.toFixed(2) + "s (bấm Rebuild để cập nhật timeline)");
  }

  var legacyReview = false;          // set on window load only if js/review.js is missing
  function renderReview() {
    if (window.JESReview) { window.JESReview.render(); return; }      // v1.7 Review (js/review.js); below = legacy table
    if (!legacyReview) return;       // startup: review.js (loaded after main.js) paints the table itself
    if (!P || !P.segments) return;
    var wrap = $("reviewWrap");
    var savedScroll = wrap ? wrap.scrollTop : 0;

    var query = ($("reviewSearch") ? $("reviewSearch").value.trim().toLowerCase() : "");
    var sceneVal = ($("reviewSceneFilter") ? $("reviewSceneFilter").value : "");
    var typeVal = ($("reviewTypeFilter") ? $("reviewTypeFilter").value : "all");
    var onlyIssues = $("onlyIssues") && $("onlyIssues").checked;

    var h = ['<thead><tr><th style="width:42px;text-align:center">#</th><th style="width:85px">Cảnh</th><th style="width:175px">Clip & In–Out</th><th>Nội dung kịch bản & Gợi ý</th><th style="width:125px;text-align:center">Điểm & Lệnh</th></tr></thead><tbody>'];
    var matchedCount = 0;

    P.segments.forEach(function (s, i) {
      var isGap = !!s.gap;
      var isIssue = isGap || (s.score && s.score < 0.65) || (s.flags || []).some(function (f) {
        return /lỗi|rung|STITCH|soft|thủ công|THIẾU|chừa/i.test(f);
      });
      var isSpeech = !isGap && (s.audio !== false && !/b-roll|montage/i.test(s.label || ""));

      if (onlyIssues && !isIssue) return;
      if (sceneVal && (s.scene || "Chung") !== sceneVal) return;
      if (typeVal === "speech" && !isSpeech) return;
      if (typeVal === "broll" && (isGap || isSpeech)) return;
      if (typeVal === "gaps" && !isGap) return;
      if (typeVal === "issues" && !isIssue) return;

      if (query) {
        var str = (String(s.label || "") + " " + String(s.file || "") + " " + String(s.scene || "") + " " + (s.flags || []).join(" ")).toLowerCase();
        if (str.indexOf(query) < 0) return;
      }

      matchedCount++;

      if (isGap) {
        var sg = (s.suggest || []).map(function (c, k) {
          var why = c.why ? " · " + esc(c.why) : "";
          var scorePct = c.score != null ? Math.round(c.score * 100) + "%" : "";
          return '<button class="btn sug" data-g="' + i + '" data-k="' + k + '" title="Thay thẻ bằng source khuyến nghị (' + scorePct +
            (c.q != null ? " · ổn định " + Math.round(c.q) + "%" : "") + why + ')">⇄ ' + esc(String(c.file).split("/").pop().replace(/\.\w+$/, "")) +
            " " + c["in"].toFixed(1) + "–" + c.out.toFixed(1) + "s" + (scorePct ? " (" + scorePct + ")" : "") + "</button>";
        }).join("");
        var pickBtn = '<button class="btn btn-pick-other" data-i="' + i + '" title="Chọn clip khác từ thư mục source để thay vào thẻ này" style="font-size:10px; padding:2px 7px; color:var(--acc2);">📂 Chọn clip khác…</button>';

        h.push('<tr class="seg gap" data-i="' + i + '">' +
          '<td style="text-align:center"><div style="font-weight:700">#' + i + '</div><span class="bdg tl" style="font-size:9px" title="Thời điểm bắt đầu">⏱ ' + formatTime(s._tl_start) + '</span></td>' +
          '<td class="scene"><b style="color:var(--warn)">' + esc(s.scene || "CHUNG") + '</b></td>' +
          '<td><b style="color:var(--warn)">▲ THẺ THIẾU</b><br><span class="muted">chừa ' + (s.out - s.in).toFixed(1) + 's</span></td>' +
          '<td><span class="seg-chip chip-gap">THẺ THIẾU</span> ' + esc(s.label) +
          '<div class="flag">' + (sg ? "Source khuyến nghị (đã lọc nội dung & khoảnh khắc):" : '<span style="color:var(--warn)">Chưa có source đạt chuẩn nội dung — cần quay bổ sung / chọn tay:</span>') + '</div>' +
          '<div class="cands" style="margin-top:4px;">' + sg + pickBtn + '</div></td>' +
          '<td style="text-align:center"><button class="btn-nav tl" data-nav="timeline" data-t="' + (s._tl_start || 0) + '" title="Nhảy playhead Sequence đến thẻ thiếu này">📍 Timeline</button></td></tr>');
        return;
      }

      var cands = (s.candidates || []).filter(function (c) { return !(c.file === s.file && Math.abs(c["in"] - s["in"]) < 0.3); });
      var pickBtn = '<button class="btn btn-pick-other" data-i="' + i + '" title="Chọn clip khác từ thư mục source để thay thế" style="font-size:10px; padding:2px 7px; color:var(--acc2);">📂 Đổi clip…</button>';
      var cb = '<div class="cands" style="margin-top:4px;">' + cands.map(function (c, k) {
          var why = c.why ? " · " + esc(c.why) : "";
          var scorePct = c.score != null ? Math.round((c.score || 0) * 100) + "%" : "";
          return '<button class="btn" data-i="' + i + '" data-k="' + k + '" title="Đổi sang take này' + why + '">⇄ ' + esc(c.file.replace(/\.\w+$/, "")) + " " + c["in"].toFixed(1) + "s" + (scorePct ? " (" + scorePct + ")" : "") + "</button>";
        }).join("") + pickBtn + "</div>";

      var typeChip = isSpeech
        ? '<span class="seg-chip chip-speech">THOẠI</span>'
        : '<span class="seg-chip chip-broll">B-ROLL</span>';
      if (s.punch_in) typeChip += '<span class="seg-chip" style="background:#3d2752;color:#d8b4fe">📐 PUNCH-IN</span>';
      if (s.speed && s.speed < 1) typeChip += '<span class="seg-chip" style="background:#1e3a5f;color:#93c5fd">⚡ SLOW-MO ' + Math.round(s.speed * 100) + '%</span>';

      var flagsHtml = (s.flags && s.flags.length)
        ? '<div class="flag">' + esc(s.flags.join(" · ")) + '</div>'
        : '';

      var scoreBadge = getScoreBadge(s.score);

      h.push('<tr class="seg' + (isIssue ? ' has-issue' : '') + '" data-i="' + i + '">' +
        '<td style="text-align:center"><div style="font-weight:700">#' + i + '</div><span class="bdg tl" style="font-size:9px" title="Thời điểm bắt đầu trên Sequence">⏱ ' + formatTime(s._tl_start) + '</span></td>' +
        '<td class="scene"><div style="font-weight:700; color:var(--acc2);">' + esc(s.scene || "CHUNG") + '</div></td>' +
        '<td>' +
          '<div style="font-weight:600; font-size:11px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="' + esc(s.file) + '">' + esc(s.file) + '</div>' +
          '<div style="font-size:10px; color:var(--dim); margin-bottom:2px;">Cắt: <b>' + (s.out - s.in).toFixed(2) + 's</b>' + (s.speed && s.speed !== 1 ? ' · ' + s.speed + 'x' : '') + '</div>' +
          '<div class="nudge-ctrl"><span>In: <b>' + s["in"].toFixed(2) + 's</b></span> <button class="nudge-btn" data-i="' + i + '" data-side="in" data-delta="-0.5" title="Mở rộng đầu clip 0.5s">◀ -0.5</button><button class="nudge-btn" data-i="' + i + '" data-side="in" data-delta="0.5" title="Cắt bớt đầu clip 0.5s">+0.5 ▶</button></div>' +
          '<div class="nudge-ctrl"><span>Out: <b>' + s.out.toFixed(2) + 's</b></span> <button class="nudge-btn" data-i="' + i + '" data-side="out" data-delta="-0.5" title="Cắt bớt đuôi clip 0.5s">◀ -0.5</button><button class="nudge-btn" data-i="' + i + '" data-side="out" data-delta="0.5" title="Mở rộng đuôi clip 0.5s">+0.5 ▶</button></div>' +
        '</td>' +
        '<td>' + typeChip + ' ' + esc(s.label) + flagsHtml + cb + '</td>' +
        '<td style="text-align:center">' +
          scoreBadge +
          '<div style="display:flex; gap:3px; justify-content:center; margin-top:5px;">' +
            '<button class="btn-nav" data-nav="source" data-i="' + i + '" title="Mở clip gốc tại In/Out trong Source Monitor">📺 Source</button>' +
            '<button class="btn-nav tl" data-nav="timeline" data-t="' + (s._tl_start || 0) + '" title="Nhảy đầu đọc Sequence đến đoạn này">📍 Timeline</button>' +
          '</div>' +
        '</td>' +
      '</tr>');
    });

    if (matchedCount === 0) {
      h.push('<tr><td colspan="5" style="text-align:center; padding:18px; color:var(--dim);">Không tìm thấy phân đoạn nào khớp bộ lọc.</td></tr>');
    }
    h.push('</tbody>');
    $("segTable").innerHTML = h.join("");

    if (wrap && savedScroll > 0) wrap.scrollTop = savedScroll;

    // Bind row clicks
    Array.prototype.forEach.call(document.querySelectorAll("tr.seg"), function (tr) {
      tr.addEventListener("click", function (ev) {
        if (ev.target.tagName === "BUTTON" || ev.target.tagName === "INPUT" || ev.target.tagName === "SELECT") return;
        document.querySelectorAll("tr.sel").forEach(function (x) { x.classList.remove("sel"); });
        tr.classList.add("sel");
        var s = P.segments[+tr.dataset.i];
        if (!s) return;
        if (!s.gap) {
          evalHost("nxOpenInSource(" + JSON.stringify(P.src + "/" + s.file) + "," + s["in"] + "," + s.out + ")").then(function (r) {
            try { var j = JSON.parse(r); if (j.error) log("✖ " + j.error, "#e5534b"); } catch (e) {}
          });
        }
        if (s._tl_start != null) {
          evalHost("jesSetPlayhead(" + s._tl_start + ")");
        }
      });
    });

    // Bind Dual Nav buttons
    Array.prototype.forEach.call(document.querySelectorAll(".btn-nav"), function (b) {
      b.addEventListener("click", function (ev) {
        ev.stopPropagation();
        var nav = b.dataset.nav;
        if (nav === "source") {
          var s = P.segments[+b.dataset.i];
          if (s && !s.gap) {
            evalHost("nxOpenInSource(" + JSON.stringify(P.src + "/" + s.file) + "," + s["in"] + "," + s.out + ")").then(function (r) {
              try { var j = JSON.parse(r); if (j.error) log("✖ " + j.error, "#e5534b"); } catch (e) {}
            });
          }
        } else if (nav === "timeline") {
          var t = parseFloat(b.dataset.t);
          evalHost("jesSetPlayhead(" + t + ")").then(function (r) {
            try { var j = JSON.parse(r); if (j.error) log("✖ " + j.error, "#e5534b"); } catch (e) {}
          });
        }
      });
    });

    // Bind Nudge buttons
    Array.prototype.forEach.call(document.querySelectorAll(".nudge-btn"), function (b) {
      b.addEventListener("click", function (ev) {
        ev.stopPropagation();
        nudgeSeg(+b.dataset.i, b.dataset.side, +b.dataset.delta);
      });
    });

    // Bind Candidate & Gap buttons
    Array.prototype.forEach.call(document.querySelectorAll(".cands button.sug"), function (b) {
      b.addEventListener("click", function (ev) { ev.stopPropagation(); fillGap(+b.dataset.g, +b.dataset.k); });
    });
    Array.prototype.forEach.call(document.querySelectorAll(".cands button:not(.sug)"), function (b) {
      b.addEventListener("click", function (ev) { ev.stopPropagation(); swapTake(+b.dataset.i, +b.dataset.k); });
    });

    // Bind Manual Clip Picker buttons
    Array.prototype.forEach.call(document.querySelectorAll(".btn-pick-other"), function (b) {
      b.addEventListener("click", function (ev) {
        ev.stopPropagation();
        openClipPicker(+b.dataset.i);
      });
    });
  }

  function openClipPicker(i) {
    if (!P || !P.segments || !P.segments[i]) return;
    var s = P.segments[i];
    var workDir = path.join(S.src || "", "_nexys_work");
    var mediaPath = path.join(workDir, "media.json");
    var aiPath = path.join(workDir, "source_ai.json");
    var stabPath = path.join(workDir, "shake.json");

    var media = {}, ai = {}, stab = {};
    try { if (fs.existsSync(mediaPath)) media = JSON.parse(fs.readFileSync(mediaPath, "utf8")); } catch (e) {}
    try { if (fs.existsSync(aiPath)) ai = (JSON.parse(fs.readFileSync(aiPath, "utf8")) || {}).clips || {}; } catch (e) {}
    try { if (fs.existsSync(stabPath)) stab = JSON.parse(fs.readFileSync(stabPath, "utf8")); } catch (e) {}

    var allFiles = Object.keys(media);
    if (!allFiles.length && S.src && fs.existsSync(S.src)) {
      try {
        allFiles = fs.readdirSync(S.src).filter(function (f) { return /\.(mp4|mov|mkv|m4v|avi)$/i.test(f); });
      } catch (e) {}
    }

    if (!allFiles.length) {
      log("✖ Không tìm thấy danh sách video trong thư mục source", "#e5534b");
      return;
    }

    showModal(function (box, close) {
    box.style.maxWidth = "500px";                 // reset by close() for the next modal

    function renderList(query) {
      var q = (query || "").trim().toLowerCase();
      var filtered = allFiles.filter(function (f) {
        if (!q) return true;
        var info = media[f] || {};
        var ainfo = ai[f] || {};
        var str = (f + " " + (ainfo.summary || "") + " " + (ainfo.tags || []).join(" ")).toLowerCase();
        return str.indexOf(q) >= 0;
      });

      var listHtml = filtered.map(function (f) {
        var mf = media[f] || {};
        var af = ai[f] || {};
        var sf = stab[f] || {};
        var durStr = mf.dur ? mf.dur.toFixed(1) + "s" : "";
        var qStr = sf.quality ? " · Q: " + Math.round(sf.quality) + "%" : "";
        var sumStr = af.summary ? '<div style="font-size:10px; color:var(--dim); margin-top:2px;">' + esc(af.summary.slice(0, 80)) + '</div>' : '';
        return '<div class="clip-pick-item" style="display:flex; justify-content:space-between; align-items:center; padding:6px 8px; border-bottom:1px solid var(--line); font-size:11px;">' +
          '<div style="flex:1; min-width:0; padding-right:8px;">' +
            '<div style="font-weight:700; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">' + esc(f) + '</div>' +
            '<div style="font-size:10px; color:var(--acc2);">' + durStr + qStr + (mf.raw_w ? " · " + mf.raw_w + "p" : "") + '</div>' +
            sumStr +
          '</div>' +
          '<div style="display:flex; gap:4px;">' +
            '<button class="btn btn-preview" data-f="' + esc(f) + '" style="font-size:9.5px; padding:2px 6px;">📺 Xem</button>' +
            '<button class="btn main btn-choose" data-f="' + esc(f) + '" style="font-size:9.5px; padding:2px 8px;">✔ Chọn</button>' +
          '</div>' +
        '</div>';
      }).join("");

      $("clipPickItems").innerHTML = listHtml || '<div style="padding:16px; text-align:center; color:var(--dim);">Không tìm thấy clip nào khớp từ khóa.</div>';

      Array.prototype.forEach.call(document.querySelectorAll("#clipPickItems .btn-preview"), function (b) {
        b.onclick = function () {
          var targetFile = b.dataset.f;
          evalHost("nxOpenInSource(" + JSON.stringify(P.src + "/" + targetFile) + ", 0, 5)");
        };
      });

      Array.prototype.forEach.call(document.querySelectorAll("#clipPickItems .btn-choose"), function (b) {
        b.onclick = function () {
          var chosenFile = b.dataset.f;
          var mf = media[chosenFile] || {};
          var dur = s.gap ? Math.max(1.5, Math.min(mf.dur || 3.0, s.out - s.in || 3.0)) : Math.min(mf.dur || 3.0, s.out - s.in);
          s.file = chosenFile;
          s["in"] = 0.0;
          s.out = Math.round(dur * 100) / 100;
          s.audio = (s.gap && !mf.audio) ? false : (s.audio !== false);
          if (s.gap) {
            delete s.gap; delete s.text; delete s.suggest;
            s.label = "B-roll: " + String(s.label || "").replace(/^⚠ THIẾU (hình|thoại):\s*/, "");
          }
          s.flags = (s.flags || []).concat(["chọn clip thủ công: " + chosenFile]);
          close();
          savePlan();
          log("✔ Đoạn #" + i + ": đã đổi sang clip " + chosenFile + " — bấm Rebuild để cập nhật timeline");
        };
      });
    }

    box.innerHTML = '<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">' +
      '<b>📂 Chọn clip từ thư mục source</b>' +
      '<button class="btn" id="bCloseClipPick" style="padding:1px 6px;">✕</button>' +
      '</div>' +
      '<div style="font-size:10.5px; color:var(--dim); margin-bottom:6px;">Áp dụng cho đoạn #' + i + ': ' + esc((s.label || "").slice(0, 50)) + '</div>' +
      '<input id="clipPickSearch" placeholder="🔍 Tìm theo tên file, tag..." style="width:100%; font-size:11px; padding:5px 8px; margin-bottom:8px;">' +
      '<div id="clipPickItems" style="max-height:300px; overflow-y:auto; border:1px solid var(--line); border-radius:6px; background:var(--panel2);"></div>';

    renderList("");

    $("clipPickSearch").oninput = function () { renderList($("clipPickSearch").value); };
    $("bCloseClipPick").onclick = function () { close(); };
    });
  }
  function swapTake(i, k) {
    var s = P && P.segments[i]; if (!s) return;
    var cands = (s.candidates || []).filter(function (c) { return !(c.file === s.file && Math.abs(c["in"] - s["in"]) < 0.3); });
    var c = cands[k]; if (!c) return;
    // drop the "↳ cut" pieces of the old take (they belong to the old clip)
    var removed = [];
    for (var j = i + 1; j < P.segments.length && P.segments[j].script_line === s.script_line && String(P.segments[j].label || "").indexOf("↳") === 0; j++) removed.push(j);
    P.segments.splice(i + 1, removed.length);
    P.highlights = (P.highlights || []).filter(function (h) { return removed.indexOf(h.seg) < 0 && h.seg !== i; })
      .map(function (h) { if (h.seg > i) h.seg -= removed.length; return h; });
    (P.overlays || []).forEach(function (o) { if (o.anchor > i) o.anchor -= removed.length; });
    var old = { file: s.file, "in": s["in"], out: s.out, score: s.score };
    s.file = c.file; s["in"] = c["in"]; s.out = c.out; s.score = c.score;
    s.candidates = [old].concat(cands.filter(function (_, x) { return x !== k; }));
    s.flags = (s.flags || []).concat(["đổi take thủ công"]);
    try {   // learning: does the editor prefer the steadier / better-framed take?
      var sk = JSON.parse(fs.readFileSync(path.join(S.src, "_nexys_work", "shake.json"), "utf8"));
      var qo = (sk[old.file] || {}).quality, qn = (sk[c.file] || {}).quality;
      if (window.JESLearn && qo != null && qn != null && Math.abs(qn - qo) >= 5) window.JESLearn.bump("take", qn > qo ? "stab_win" : "stab_loss");
    } catch (e) {}
    fs.writeFileSync(planPath, planJSON(P), "utf8");
    log("• Đoạn " + i + ": đổi sang " + c.file + " " + c["in"] + "s (bấm Rebuild để dựng bản mới)");
    renderReview();
  }

  // ---------- recommended source for a placeholder card ----------
  function onTimeline(file, a, b, skip) {
    return P.segments.some(function (x, j) { return j !== skip && !x.gap && x.file === file && Math.min(b, x.out) - Math.max(a, x["in"]) > 0.3; });
  }
  function fillGap(i, k, quiet) {
    var s = P.segments[i], c = s && s.gap && (s.suggest || [])[k];
    if (!c) return false;
    if (onTimeline(c.file, c["in"], c.out, i)) { if (!quiet) log("• " + c.file + " " + c["in"] + "–" + c.out + "s đã có trên timeline — chọn gợi ý khác", "#e0a82e"); return false; }
    var txt = String(s.label || "").replace(/^⚠ THIẾU (hình|thoại):\s*/, "");
    s.file = c.file; s["in"] = c["in"]; s.out = c.out; s.score = c.score;
    s.audio = !!c.audio; if (!c.audio) { s.silence = false; s.subs = false; }
    s.label = (c.audio ? "" : "B-roll: ") + txt;
    s.candidates = (s.suggest || []).filter(function (_, x) { return x !== k; });
    s.flags = ["thay thẻ thiếu bằng source khuyến nghị" + (c.score != null && !isNaN(c.score) ? " (khớp " + (+c.score).toFixed(2) + ")" : "")];
    delete s.gap; delete s.text; delete s.suggest;
    if (!quiet) { savePlan(); log("✔ Đoạn " + i + ": thay thẻ thiếu bằng " + c.file + " " + c["in"] + "–" + c.out + "s — bấm Rebuild để dựng bản mới"); }
    return true;
  }
  function fillAllGaps() {
    if (!P) return log("• Chưa có plan");
    var n = 0, left = 0;
    P.segments.forEach(function (s, i) {
      if (!s.gap) return;
      for (var k = 0; k < (s.suggest || []).length; k++) if (fillGap(i, k, true)) { n++; return; }
      left++;
    });
    if (n) savePlan();
    log(n ? "✔ Đã thay " + n + " thẻ thiếu bằng source khuyến nghị" + (left ? " · còn " + left + " thẻ không có gợi ý" : "") + " — bấm Rebuild" : "• Không có thẻ nào có source khuyến nghị", n ? null : "#e0a82e");
    loadPlanInfo();
  }
  function loadPlanInfo() {
    if (!P || !P.segments) {
      if ($("planInfo")) $("planInfo").textContent = "chưa có plan";
      return;
    }
    var totalDur = 0, speechCount = 0, brollCount = 0, gapCount = 0, issueCount = 0;
    var scenesMap = {};
    var curTl = 0;

    P.segments.forEach(function (s, i) {
      var spd = +s.speed || 100; if (spd <= 4) spd *= 100;          // percent (old plans: 1.0 = 100 %)
      if (s.tl && s.tl.length === 2) curTl = +s.tl[0];
      s._tl_start = curTl;
      var segDur = s.tl && s.tl.length === 2 ? Math.max(0, s.tl[1] - s.tl[0]) : Math.max(0, (s.out - s.in) * 100 / spd);
      curTl += segDur;
      s._tl_end = curTl;
      totalDur += segDur;

      var isGap = !!s.gap;
      var isSpeech = !isGap && (s.audio !== false && !/b-roll|montage/i.test(s.label || ""));
      if (isGap) gapCount++;
      else if (isSpeech) speechCount++;
      else brollCount++;

      var isIssue = isGap || (s.score && s.score < 0.65) || (s.flags || []).some(function (f) {
        return /lỗi|rung|STITCH|soft|thủ công|THIẾU|chừa/i.test(f);
      });
      if (isIssue) issueCount++;

      var sc = s.scene || "Chung";
      scenesMap[sc] = (scenesMap[sc] || 0) + 1;
    });

    if ($("kpiDur")) $("kpiDur").textContent = formatTime(totalDur) + " (" + totalDur.toFixed(1) + "s)";
    var tgt = (S.brief && S.brief.duration) || (P.brief && P.brief.duration) || "";
    if ($("kpiTarget")) $("kpiTarget").textContent = tgt ? "Mục tiêu: " + tgt : "Dự kiến";

    if ($("kpiSegs")) $("kpiSegs").textContent = P.segments.length + " đoạn";
    if ($("kpiRatio")) $("kpiRatio").textContent = speechCount + " thoại · " + brollCount + " B-roll";

    if ($("kpiIssues")) {
      $("kpiIssues").textContent = issueCount;
      $("kpiIssues").style.color = issueCount > 0 ? "var(--warn)" : "var(--ok)";
    }
    if ($("kpiIssuesDesc")) {
      $("kpiIssuesDesc").textContent = issueCount > 0 
        ? (gapCount ? gapCount + " thẻ thiếu · " : "") + (issueCount - gapCount) + " cần xem lại"
        : "Tất cả đoạn đều đạt chuẩn";
    }

    if ($("planInfo")) {
      $("planInfo").textContent = P.name + " · " + P.segments.length + " đoạn · ~" + totalDur.toFixed(0) + "s" +
        (gapCount ? " · ▲ " + gapCount + " thẻ thiếu" : "");
    }
    if ($("bFillGaps")) {
      $("bFillGaps").style.display = P.segments.some(function (s) { return s.gap && (s.suggest || []).length; }) ? "" : "none";
    }

    var scSelect = $("reviewSceneFilter");
    if (scSelect) {
      var curVal = scSelect.value;
      var scOptions = ['<option value="">Tất cả cảnh (' + P.segments.length + ')</option>'];
      Object.keys(scenesMap).forEach(function (sc) {
        scOptions.push('<option value="' + esc(sc) + '"' + (curVal === sc ? " selected" : "") + '>' + esc(sc) + ' (' + scenesMap[sc] + ')</option>');
      });
      scSelect.innerHTML = scOptions.join("");
    }
  }

  // ---------- system check ----------
  function sysCheck() {
    var box = $("sysList"); box.innerHTML = "";
    function row(ok, name, detail, fix) {
      var d = document.createElement("div");
      d.innerHTML = '<span class="' + (ok ? "ok" : "bad") + '">' + (ok ? "✔" : "✖") + "</span> <b>" + esc(name) + "</b> " + esc(detail || "") +
        (!ok && fix ? '<div class="hint">→ ' + esc(fix) + "</div>" : "");
      box.appendChild(d);
    }
    function sh(cmd, args) {
      return new Promise(function (res) {
        cp.execFile(cmd, args, { cwd: S.tools, windowsHide: true, timeout: 20000 }, function (err, out, errOut) {
          res({ ok: !err, out: String(out || "") + String(errOut || "") });
        });
      });
    }
    var toolsOk = fs.existsSync(path.join(S.tools, "nxcut.py")) && fs.existsSync(path.join(S.tools, "nxcore", "run.py"));
    row(toolsOk, "Thư mục engine", S.tools, "Sửa đường dẫn 'Thư mục tools' — phải chứa nxcut.py và nxcore/");
    return sh(S.python || "python", ["--version"]).then(function (r) {
      row(r.ok, "Python", r.out.trim(), "Cài Python 3.10+ hoặc sửa ô Python");
      return sh(S.python || "python", ["-c", "import numpy,cv2,PIL,faster_whisper,docx;print('ok')"]);
    }).then(function (r) {
      row(r.ok, "Thư viện Python", r.ok ? "numpy · opencv · pillow · faster-whisper · python-docx" : r.out.split("\n").filter(Boolean).pop(),
          "pip install numpy opencv-python pillow faster-whisper python-docx");
      return sh("ffmpeg", ["-hide_banner", "-version"]);
    }).then(function (r) {
      row(r.ok, "FFmpeg", r.ok ? r.out.split("\n")[0].replace("ffmpeg version ", "") : "", "Cài FFmpeg ≥ 6 (winget install Gyan.FFmpeg)");
      return sh("nvidia-smi", ["--query-gpu=name,memory.total,memory.used", "--format=csv,noheader"]);
    }).then(function (r) {
      row(r.ok, "GPU (CUDA)", r.out.trim(), "Không có GPU NVIDIA → transcribe/QC chạy CPU (chậm hơn)");
      return new Promise(function (res) {
        var rq = require("http").get({ host: "127.0.0.1", port: 11434, path: "/api/version", timeout: 3000 }, function (rs) {
          var b = ""; rs.setEncoding("utf8"); rs.on("data", function (c) { b += c; }); rs.on("end", function () { res(b); });
        });
        rq.on("error", function () { res(null); });
        rq.on("timeout", function () { rq.destroy(); res(null); });     // the timeout option alone never aborts the request
      });
    }).then(function (v) {
      var ver = null; try { ver = v ? JSON.parse(v).version : null; } catch (e) {}
      row(!!ver, "Ollama (AI local)", ver ? "v" + ver : "không chạy", "Mở ứng dụng Ollama (chỉ cần khi dùng provider Ollama)");
      return evalHost("nxPing()");
    }).then(function (r) {
      var j = null; try { j = JSON.parse(r); } catch (e) {}
      S._hostOk = !!j;
      row(!!j, (window.JES_HOST_NAME || "Premiere") + " host", j ? j.v + " · " + (j.project || "(chưa mở project)") : "không phản hồi",
        window.JES_HOST === "resolve" ? "Cần Resolve Studio; mở plugin từ Workspace → Workflow Integrations, mở 1 project" : "Đóng/mở lại panel; kiểm tra PlayerDebugMode");
      if (window.JES_HOST === "resolve") return;              // agent DOM/QE rows = Premiere only
      return evalHost("jesSelfTest()").then(function (t) {
        var x = null; try { x = JSON.parse(t); } catch (e) {}
        if (!x) { row(false, "Công cụ AI agent", "host.jsx chưa nạp bản mới", "Đóng/mở lại panel"); return; }
        row(x.dom, "Agent · DOM (clip, marker, trim, effect params)", x.seq ? "sequence: " + x.seq.name + " · " + x.seq.v + "V/" + x.seq.a + "A · V1 " + x.seq.clipsV1 + " clip" : "chưa mở sequence (vẫn OK)", x.domErr);
        row(x.qe, "Agent · QE (razor, effect, transition, speed)", x.qe ? "effect " + (x.fx ? "✓" : "✗") + " · transition " + (x.tr ? "✓" : "✗") : x.qeErr, "QE DOM không khả dụng trên bản Premiere này");
        row(x.frameExport || !x.seq, "Agent · xuất frame cho AI xem", x.seq ? (x.frameExport ? "OK" : "không có") : "mở sequence để kiểm tra", "Premiere không hỗ trợ exportFramePNG");
        row(x.encoder, "Agent · export qua Media Encoder", x.encoder ? "OK" : "không có", "Cài Adobe Media Encoder");
      });
    }).then(function () {
      var key = S.apiKey || (S.keys && S.keys[S.provider]);
      row(S.provider === "ollama" || !!key, "AI provider", S.provider + (S.model ? " · " + S.model : ""), "Dán API key ở trên hoặc chọn Ollama");
    });
  }

  /* opt.noRender: the caller repaints the Review table itself (Review sync / direct edits) */
  function savePlan(opt) {
    if (P && planPath) fs.writeFileSync(planPath, planJSON(P), "utf8");
    loadPlanInfo();                                  // timeline positions first — the Review table shows them
    if (!(opt && opt.noRender)) renderReview();
  }

  window.NX = {
    get S() { return S; }, save: save, cfgDir: CFG_DIR, log: log, ask: ask, showModal: showModal, run: run, py: py, pyJSON: pyJSON,
    evalHost: evalHost, esc: esc,
    analyze: analyze, align: align, build: build, cancel: cancel, loadPlan: loadPlan, savePlan: savePlan, planJSON: planJSON, swapTake: swapTake, fillGap: fillGap, fillAllGaps: fillAllGaps,
    nudgeSeg: nudgeSeg, formatTime: formatTime,
    getPlan: function () { return P; }, getPlanPath: function () { return planPath; }, outDir: outDir, roughPlan: roughPlan, roughCut: roughCut, buildPlan: buildPlan,
    setPlanPath: setPlanPath, srcChanged: srcChanged,
    // one busy lock shared by every module that runs jobs / edits the timeline
    isBusy: isBusy, busyLabel: busyLabel, tryLock: tryLock, unlock: unlock, exclusive: exclusive,
    trackChild: trackChild, untrackChild: untrackChild, killTree: killTree, presetData: presetData,
    bindInputs: function () { ["src", "script", "name", "keywords"].forEach(function (k) { $(k).value = S[k] || ""; });
      ["type", "duration", "platform", "notes"].forEach(function (k) { if ($("brief_" + k)) $("brief_" + k).value = (S.brief || {})[k] || ""; }); },
    askChrono: askChrono, chronoOn: chronoOn, askAudio: askAudio, audioOn: audioOn,
    setAutoAnswer: function (fn) { autoAnswer = typeof fn === "function" ? fn : null; },
    currentName: currentName, refreshHints: refreshHints, seqSpec: seqSpec, seqInfo: seqInfo, syncMode: syncMode, guard: guard, pick: pick,
    setField: function (k, v) {
      S[k] = v; if ($(k)) $(k).value = v;
      if (k === "name") S.nameAuto = false;
      if (k === "src") srcChanged();
      save(); refreshHints();
      if (k === "src" && $("src")) { try { $("src").dispatchEvent(new CustomEvent("jes:set")); } catch (e) {} }
    }
  };

  // ---------- init ----------
  function tabs() {
    Array.prototype.forEach.call(document.querySelectorAll("nav button"), function (b) {
      b.onclick = function () {
        var prev = document.querySelector("nav button.on");
        if (prev === b) return;
        if (prev) { prev.classList.remove("on"); var ps = $(prev.dataset.tab); if (ps) ps.classList.remove("on"); }
        b.classList.add("on"); $(b.dataset.tab).classList.add("on");
        // v1.8: tabs only paint what changed (each module listens to "nxtab" and skips when its data is unchanged)
        if (b.dataset.tab === "review" && P) {
          loadPlanInfo();
          if (window.JESReview && window.JESReview.show) window.JESReview.show(); else renderReview();
        }
        try { document.dispatchEvent(new CustomEvent("nxtab", { detail: b.dataset.tab })); } catch (e) {}
        clearTimeout(tabs._snap);                                       // session file write: once, after the user settles
        tabs._snap = setTimeout(function () { try { if (window.JESSession) window.JESSession.snap(); } catch (e) {} }, 1500);
      };
    });
  }
  load(); bindInputs(); bindPresets(); bindSeq(); bindMontage(); tabs(); listBases(); syncMode(); refreshHints(); ping();
  // Cài đặt → "Đường dẫn" is collapsed (the installer fills it) — open it when the engine path is still missing
  if (!S.tools && $("tools")) { var pathBox = $("tools").closest("details"); if (pathBox) pathBox.open = true; }
  if ($("chronoChk")) $("chronoChk").onchange = function () {
    var on = $("chronoChk").checked; setChrono(on, "editor");
    log("🕒 Mạch thời gian quay: " + (on ? "BẬT" : "TẮT") + " cho folder này — áp dụng từ lần Align tiếp theo", "#8b9099");
  };
  if ($("src")) $("src").addEventListener("change", function () { try { syncChronoUi(); syncAudioUi(); } catch (e) {} });
  $("status").style.cursor = "pointer"; $("status").onclick = function () { $("status").textContent = "đang kiểm tra host…"; ping(0); };
  $("pickSrc").onclick = function () { pick("src", true); };
  $("bAnalyze").onclick = guard(analyze, "Phân tích source");
  if ($("bAudio")) $("bAudio").onclick = guard(audioRun, "Phân tích âm thanh");
  $("bRough").onclick = guard(roughCut, "Rough cut");
  $("bAlign").onclick = guard(align, "Align");
  $("bBuild").onclick = guard(build, "Build");
  $("bAll").onclick = guard(function () { return analyze().then(align).then(build); }, "Phân tích → Align → Build");
  $("bRebuild").onclick = guard(build, "Rebuild");
  $("bReload").onclick = function () { loadPlan(); };
  if ($("bFillGaps")) $("bFillGaps").onclick = fillAllGaps;
  $("bSaveSet").onclick = function () { save(); listBases(); log("✔ Đã lưu cài đặt"); };
  $("bSysCheck").onclick = function () { sysCheck(); };
  $("bCancel").onclick = cancel;
  if ($("onlyIssues")) $("onlyIssues").onchange = function () { if (P) renderReview(); };
  if ($("reviewSearch")) $("reviewSearch").oninput = function () { if (P) renderReview(); };
  if ($("reviewSceneFilter")) $("reviewSceneFilter").onchange = function () { if (P) renderReview(); };
  if ($("reviewTypeFilter")) $("reviewTypeFilter").onchange = function () { if (P) renderReview(); };
  curSrcN = normP(S.src); curSrcRaw = S.src || "";
  // plan data only: the Review table is painted by js/review.js once it loads (no throw-away legacy render at startup)
  if (S.src) { planPath = planFor(currentName()); if (fs.existsSync(planPath)) loadPlan(); }
  window.addEventListener("load", function () { if (!window.JESReview) { legacyReview = true; if (P) renderReview(); } });
})();
