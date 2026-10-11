/* Browser stand-in for CEP + Node so the panel's modules initialise for a UI check (no Premiere, no disk).
   An in-memory file system holds settings.json + a sample stylebook; evalScript answers jesSequence with a fake sequence. */
(function () {
  window.__errs = []; window.addEventListener("error", function (e) { window.__errs.push(String(e.filename).split("/").pop() + ":" + e.lineno + " " + e.message); });
  var mem = {};
  var SETTINGS = "C:/Users/X/AppData/Roaming/JustinEditSuite/settings.json";
  mem[SETTINGS] = JSON.stringify({ tools: "C:/Engine", python: "python", ffmpeg: "ffmpeg", src: "D:/Footage/HomeFlow", preset: "talking_head", outRoot: "D:/Out",
    queue: { awake: true, items: [
      { src: "D:/Footage/HomeFlow", preset: "talking_head", steps: "all", script: "D:/Footage/HomeFlow/KichBan_HomeFlow_v2.md", music: "", state: "ok",
        msg: "Phân tích → Dựng → Build · 18 phút 4s · HOMEFLOW_SCRIPT_v1 · 42 shot · 3:12" },
      { src: "D:/Footage/DaNang_Trip", preset: "beat_cut", steps: "all", script: "", music: "", state: "wait" },
      { src: "D:/Footage/Phong_van_KTS", preset: "talking_head", steps: "plan", script: "", music: "", state: "fail", msg: "Dựng: Align kịch bản → plan" }] },
    edNotes: { items: [{ t: 12.4, text: "zoom to quá, nhìn bị vỡ", seq: "HOMEFLOW_SCRIPT_v1" }, { t: 47.0, text: "shot này rung, đổi cảnh khác", seq: "HOMEFLOW_SCRIPT_v1", mk: 1 },
                       { t: 95.6, text: "SFX whoosh dày quá, nghe ồn", seq: "HOMEFLOW_SCRIPT_v1" }] },
    motion: { view: "text" }, _savedAt: 9e12 });
  mem["C:/Engine/nxcore/stylebook_params.json"] = null;           // fetched below from the real engine copy
  mem["C:/Users/X/AppData/Roaming/JustinEditSuite/stylebook.json"] = JSON.stringify({ version: 1, rules: [
    { id: "r1", text: "Zoom punch-in nhẹ thôi, không quá 108%", scope: "dung", param: { key: "punch_in_scale", value: 108 }, on: true, src: "note", from: ["0:12 zoom to quá"] },
    { id: "r2", text: "Hook luôn dưới 5 giây, vào thẳng vấn đề", scope: "kich_ban", param: null, on: true, src: "manual" },
    { id: "r3", text: "SFX nhẹ cho video phỏng vấn", scope: "am_thanh", param: { key: "sfx_density", value: "light" }, preset: "talking_head", on: false, src: "note" }] });
  try { var x = new XMLHttpRequest(); x.open("GET", "stylebook_params.json", false); x.send(); mem["C:/Engine/nxcore/stylebook_params.json"] = x.responseText; } catch (e) {}
  mem["C:/Engine/presets/beat_cut.json"] = JSON.stringify({ id: "beat_cut", name: "Cut on beat", mode: "beat" });
  mem["C:/Engine/presets/talking_head.json"] = JSON.stringify({ id: "talking_head", name: "Talking Head" });
  function norm(p) { return String(p).replace(/\\/g, "/"); }
  var fsShim = {
    existsSync: function (p) { p = norm(p); return mem[p] != null || (/^(C:\/Engine|D:\/)/.test(p) && !/\.\w+$/.test(p)); },
    readFileSync: function (p) { p = norm(p); if (mem[p] == null) throw new Error("ENOENT " + p); return mem[p]; },
    writeFileSync: function (p, d) { mem[norm(p)] = String(d); }, appendFileSync: function () {}, appendFile: function () {},
    mkdirSync: function () {}, readdirSync: function () { return []; }, renameSync: function (a, b) { mem[norm(b)] = mem[norm(a)]; delete mem[norm(a)]; },
    statSync: function (p) { p = norm(p); if (mem[p] == null && !/^(C:\/Engine|D:\/)/.test(p)) throw new Error("ENOENT"); return { mtimeMs: 1, size: (mem[p] || "").length, isDirectory: function () { return !/\.\w+$/.test(p); } }; },
    copyFileSync: function () {}, unlinkSync: function () {}, watch: function () { return { close: function () {} }; }
  };
  var pathShim = {
    sep: "/", join: function () { return Array.prototype.join.call(arguments, "/").replace(/\\/g, "/").replace(/\/+/g, "/"); },
    basename: function (p, e) { var b = norm(p).replace(/\/+$/, "").split("/").pop(); return e && b.slice(-e.length) === e ? b.slice(0, -e.length) : b; },
    dirname: function (p) { var a = norm(p).split("/"); a.pop(); return a.join("/") || "."; },
    extname: function (p) { var m = /\.[^./]+$/.exec(norm(p)); return m ? m[0] : ""; },
    resolve: function () { return pathShim.join.apply(null, arguments); }, normalize: norm, isAbsolute: function (p) { return /^[A-Z]:|^\//.test(p); }
  };
  // a child that "succeeds" when its stdin is closed (ffmpeg fed frames) — frames written are counted
  window.__spawned = [];
  function fakeProc(cmd, args) {
    var h = {}, rec = { cmd: cmd, args: args || [], frames: 0 };
    window.__spawned.push(rec);
    var ev = function () { return { on: function () {}, setEncoding: function () {}, write: function () { rec.frames++; return true; }, once: function () {},
      end: function () { setTimeout(function () { (h.close || []).forEach(function (f) { f(0); }); }, 5); } }; };
    return { on: function (e, f) { (h[e] = h[e] || []).push(f); }, stdout: ev(), stderr: ev(), stdin: ev(), kill: function () {}, pid: 1, exitCode: null };
  }
  window.require = function (m) {
    if (m === "fs") return fsShim;
    if (m === "path") return pathShim;
    if (m === "os") return { tmpdir: function () { return "C:/Temp"; }, homedir: function () { return "C:/Users/X"; }, platform: function () { return "win32"; }, cpus: function () { return [1, 2, 3, 4]; } };
    if (m === "child_process") return { spawn: fakeProc, exec: function () { return fakeProc(); }, execSync: function () { return ""; }, spawnSync: function () { return { status: 0, stdout: "" }; }, execFile: function () { return fakeProc(); } };
    if (m === "buffer") return { Buffer: { from: function () { return {}; } } };
    if (m === "http" || m === "https" || m === "net") return { request: function () { return fakeProc(); }, get: function () { return fakeProc(); } };
    return {};
  };
  window.process = { env: { APPDATA: "C:/Users/X/AppData/Roaming", USERPROFILE: "C:/Users/X" }, platform: "win32", pid: 1, versions: { node: "16" }, on: function () {}, cwd: function () { return "C:/"; } };
  var host = { appName: "PPRO", appVersion: "25.0", appLocale: "vi_VN", isAppOnline: true,
    appSkinInfo: { baseFontSize: 10, baseFontFamily: "Segoe UI", panelBackgroundColor: { color: { red: 35, green: 35, blue: 35, alpha: 255 } }, appBarBackgroundColor: { color: { red: 35, green: 35, blue: 35 } } } };
  function answer(script) {
    if (/jesSequence/.test(script)) return JSON.stringify({ ok: true, name: "HOMEFLOW_SCRIPT_v1", w: 1920, h: 1080, fps: 25, dur: 192, playhead: 47.2, markers: [], video: [], audio: [] });
    if (/nxPing/.test(script)) return JSON.stringify({ v: "25.0", project: "HomeFlow.prproj", path: "D:/HomeFlow.prproj" });
    if (/jesTextFxPlace/.test(script)) return JSON.stringify({ ok: true, placed: 1, track: "V4", errors: [] });
    return JSON.stringify({ ok: true });
  }
  window.__adobe_cep__ = {
    getHostEnvironment: function () { return JSON.stringify(host); },
    evalScript: function (s, cb) { (window.__evals = window.__evals || []).push(s); if (cb) setTimeout(function () { cb(answer(s)); }, 5); },
    getSystemPath: function (k) { return k === "extension" ? "C:/Ext" : "C:/Users/X/AppData/Roaming"; },
    addEventListener: function () {}, removeEventListener: function () {}, dispatchEvent: function () {},
    getExtensionId: function () { return "com.justin.editsuite.panel"; }, getCurrentApiVersion: function () { return JSON.stringify({ major: 11, minor: 0, micro: 0 }); },
    requestOpenExtension: function () {}, closeExtension: function () {}, invokeSync: function () { return ""; }, invokeAsync: function () {},
    getNetworkPreferences: function () { return "{}"; }, initResourceBundle: function () { return "{}"; }, dumpInstallationInfo: function () { return ""; },
    getMonitorScaleFactor: function () { return 1; }, getScaleFactor: function () { return 1; }, setScaleFactorChangedHandler: function () {}
  };
  window.cep = { fs: { showOpenDialogEx: function () { return { data: [] }; }, showSaveDialogEx: function () { return { data: "" }; } }, process: {}, util: { openURLInDefaultBrowser: function () {} } };
  window.__dirname = "C:/Ext/js"; window.__filename = "C:/Ext/js/main.js"; window.module = undefined;
  window.__mem = mem;
})();
