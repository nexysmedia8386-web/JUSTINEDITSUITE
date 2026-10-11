/* Justin EDIT Suite — 🌙 Hàng đợi chạy đêm (tab Chạy).
   Several source folders run one after another: Phân tích → Dựng (Align kịch bản / Cắt theo nhạc / theo giờ quay) → Build,
   in the Premiere project that is open, while nobody watches. Learned from "edit while I sleep" workflows: the editor
   shoots, queues the folders, and in the morning only watches + leaves notes (AI → 📍 Ghi chú).
   · questions mid-way are answered by a policy (NX.setAutoAnswer): the suggested answer, or skip when a person is
     needed (no script / no music) — every automatic answer is logged
   · a folder that fails does not stop the queue; Hủy (or "Dừng") does
   · the panel's own folder / preset / script / music settings are put back when the queue ends
   · optional: keep Windows / macOS awake while it runs (a power request held by a child process, released at the end)
   Report: <outRoot>/HangDoi_<date>.md + the table in the card. */
(function () {
  "use strict";
  var NX = window.NX;
  if (!NX) return;
  var fs = require("fs"), path = require("path"), cp = require("child_process");
  function $(id) { return document.getElementById(id); }
  function esc(s) { return NX.esc ? NX.esc(s) : String(s == null ? "" : s); }
  function Q() { var q = NX.S.queue; if (!q || !Array.isArray(q.items)) q = NX.S.queue = { items: [], awake: true }; return q; }
  var running = false, stopAfter = false, awakeProc = null;

  function norm(p) { return String(p || "").replace(/\\/g, "/").replace(/\/+$/, ""); }
  function exists(p) { try { return !!p && fs.existsSync(p); } catch (e) { return false; } }
  function readJSON(p) { try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) { return null; } }
  function modeOf(preset) { var m = (NX.presetData(preset) || {}).mode; return m === "beat" || m === "vo" || m === "story" ? m : "script"; }
  function presetName(id) {
    var o = $("presetSel") && $("presetSel").querySelector('option[value="' + id + '"]');
    return o ? o.textContent.trim() : id;
  }
  function hms(s) { s = Math.round(+s || 0); var h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60); return (h ? h + " giờ " : "") + m + " phút" + (h ? "" : " " + (s % 60) + "s"); }
  function mmss(s) { s = Math.round(+s || 0); return Math.floor(s / 60) + ":" + ("0" + s % 60).slice(-2); }

  // ------------------------------------------------------------------ a folder → queue item with good guesses
  function findScript(src) {
    if (norm(NX.S.src) === norm(src) && exists(NX.S.script)) return norm(NX.S.script);
    var best = null, bm = 0;
    try {
      fs.readdirSync(src).forEach(function (f) {
        var md = /^KichBan_.*\.md$/i.test(f) && !/_BANG\.md$/i.test(f), doc = /\.(docx|md|txt)$/i.test(f) && /kich.?ban|script/i.test(f);
        if (!md && !doc) return;
        var m = fs.statSync(path.join(src, f)).mtimeMs + (md ? 1e12 : 0);      // the tool's own KichBan_*.md first, newest
        if (m > bm) { bm = m; best = path.join(src, f); }
      });
    } catch (e) {}
    return best ? norm(best) : "";
  }
  function guessPreset(src) {
    var pj = readJSON(path.join(src, "_nexys_work", "preset.json"));
    if (pj && pj.id && NX.presetData(pj.id) && Object.keys(NX.presetData(pj.id)).length) return pj.id;
    return norm(NX.S.src) === norm(src) ? NX.S.preset : (NX.S.preset || "talking_head");
  }
  function addFolder(src) {
    src = norm(src);
    if (!src || !exists(src)) return;
    if (Q().items.some(function (x) { return norm(x.src) === src; })) { NX.log("• " + path.basename(src) + " đã có trong hàng đợi", "#e0a82e"); return; }
    var preset = guessPreset(src);
    Q().items.push({ src: src, preset: preset, steps: "all", script: findScript(src),
                     music: norm(NX.S.src) === src && exists(NX.S.montageMusic) ? norm(NX.S.montageMusic) : "", state: "wait" });
    NX.save(); paint();
  }
  function addPick() {
    var r = window.cep.fs.showOpenDialogEx(true, true, "Chọn folder source cho hàng đợi", NX.S.src || "");
    ((r && r.data) || []).forEach(addFolder);
  }
  /* what this item can do, before it runs → {plan: bool, why: "" | reason} */
  function plan(it) {
    var m = modeOf(it.preset);
    if (it.steps === "an") return { plan: false, why: "" };
    if (m === "script" && !exists(it.script)) return { plan: false, why: "thiếu kịch bản → chỉ Phân tích" };
    if (m === "beat" && !exists(it.music)) return { plan: false, why: "thiếu nhạc → chỉ Phân tích" };
    if (m === "vo") {
      var vo = path.join((NX.S.outRoot || "") + "/NX_" + path.basename(it.src).replace(/\s+/g, "_"), "vo");
      var hasVo = false; try { hasVo = fs.readdirSync(vo).some(function (f) { return /\.(mp3|wav|m4a|aif|aiff|flac|aac|ogg)$/i.test(f); }); } catch (e) {}
      if (!hasVo) return { plan: false, why: "explainer chưa có file VO (output/vo) → chỉ Phân tích" };
    }
    return { plan: true, why: "" };
  }

  // ------------------------------------------------------------------ UI
  var STEPS = [["all", "Phân tích → Dựng → Build"], ["plan", "Phân tích → Dựng"], ["an", "Chỉ Phân tích"]];
  var STATE = { wait: ["chờ", ""], run: ["đang chạy…", "run"], ok: ["✔ xong", "ok"], part: ["✔ một phần", "warn"], fail: ["✖ lỗi", "bad"], skip: ["bỏ qua", ""] };
  function presetOptions(cur) {
    var ps = $("presetSel"); if (!ps) return "";
    return Array.prototype.map.call(ps.options, function (o) {
      return '<option value="' + esc(o.value) + '"' + (o.value === cur ? " selected" : "") + ">" + esc(o.textContent.trim()) + "</option>";
    }).join("");
  }
  function paint() {
    var box = $("qList"); if (!box) return;
    var it = Q().items;
    $("qMeta").textContent = it.length ? it.length + " folder" + (running ? " · đang chạy" : "") : "";
    box.innerHTML = it.length ? it.map(function (x, i) {
      var m = modeOf(x.preset), pl = plan(x), st = STATE[x.state] || STATE.wait;
      var need = m === "script" ? '<button class="btn chip q-pick" data-i="' + i + '" data-k="script" title="' + esc(x.script || "chọn kịch bản") + '">📝 ' +
          esc(x.script ? path.basename(x.script) : "chọn kịch bản") + "</button>" :
        m === "beat" ? '<button class="btn chip q-pick" data-i="' + i + '" data-k="music" title="' + esc(x.music || "chọn nhạc") + '">♪ ' +
          esc(x.music ? path.basename(x.music) : "chọn nhạc") + "</button>" : "";
      return '<div class="q-item' + (x.state === "run" ? " run" : "") + '">' +
        '<div class="q-head"><b title="' + esc(x.src) + '">' + (i + 1) + ". " + esc(path.basename(x.src)) + '</b><span class="q-st ' + st[1] + '">' + st[0] + "</span>" +
        (running ? "" : '<button class="btn chip q-up" data-i="' + i + '" title="Lên trên">↑</button><button class="btn chip q-del" data-i="' + i + '" title="Bỏ khỏi hàng đợi">✕</button>') + "</div>" +
        '<div class="row q-opts"><select class="q-pre" data-i="' + i + '"' + (running ? " disabled" : "") + ">" + presetOptions(x.preset) + "</select>" +
        '<select class="q-steps" data-i="' + i + '"' + (running ? " disabled" : "") + ">" + STEPS.map(function (s) {
          return '<option value="' + s[0] + '"' + (s[0] === x.steps ? " selected" : "") + ">" + s[1] + "</option>"; }).join("") + "</select>" + need + "</div>" +
        (x.msg ? '<div class="q-msg ' + st[1] + '">' + esc(x.msg) + "</div>" : pl.why ? '<div class="q-msg warn">' + esc(pl.why) + "</div>" : "") + "</div>";
    }).join("") : '<div class="muted" style="padding:4px 0">Chưa có folder nào.</div>';
    if (running) return;
    Array.prototype.forEach.call(box.querySelectorAll(".q-pre"), function (s) { s.onchange = function () { it[+s.dataset.i].preset = s.value; done(); }; });
    Array.prototype.forEach.call(box.querySelectorAll(".q-steps"), function (s) { s.onchange = function () { it[+s.dataset.i].steps = s.value; done(); }; });
    Array.prototype.forEach.call(box.querySelectorAll(".q-del"), function (b) { b.onclick = function () { it.splice(+b.dataset.i, 1); done(); }; });
    Array.prototype.forEach.call(box.querySelectorAll(".q-up"), function (b) {
      b.onclick = function () { var i = +b.dataset.i; if (i > 0) { var t = it[i]; it[i] = it[i - 1]; it[i - 1] = t; done(); } };
    });
    Array.prototype.forEach.call(box.querySelectorAll(".q-pick"), function (b) {
      b.onclick = function () {
        var x = it[+b.dataset.i], music = b.dataset.k === "music";
        var r = window.cep.fs.showOpenDialogEx(false, false, music ? "Chọn nhạc cho " + path.basename(x.src) : "Chọn kịch bản cho " + path.basename(x.src),
          x.src, music ? ["mp3", "wav", "m4a", "aif", "aiff", "flac", "aac", "ogg"] : ["docx", "md", "txt"]);
        if (r && r.data && r.data.length) { x[music ? "music" : "script"] = norm(r.data[0]); done(); }
      };
    });
    function done() { NX.save(); paint(); }
    $("qStart").disabled = !it.length;
  }
  function buttons() {
    $("qStart").style.display = running ? "none" : "";
    $("qStop").style.display = running ? "" : "none";
    $("qKill").style.display = running ? "" : "none";
    $("qAdd").disabled = $("qAddCur").disabled = running;
  }

  // ------------------------------------------------------------------ keep the machine awake while it runs
  function awake(on) {
    if (!on) { if (awakeProc) { try { NX.killTree ? NX.killTree(awakeProc, false) : awakeProc.kill(); } catch (e) {} awakeProc = null; } return; }
    if (awakeProc) return;
    try {
      if (process.platform === "win32") {
        // ES_CONTINUOUS | ES_SYSTEM_REQUIRED — a power request of this child only, gone the moment it exits (no setting changes)
        var ps = "$t=Add-Type -MemberDefinition '[DllImport(\"kernel32.dll\")] public static extern uint SetThreadExecutionState(uint f);' " +
                 "-Name P -Namespace JES -PassThru; [void]$t::SetThreadExecutionState([uint32]2147483649); while ($true) { Start-Sleep -Seconds 60 }";
        awakeProc = cp.spawn("powershell.exe", ["-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps], { windowsHide: true });
      } else if (process.platform === "darwin") {
        awakeProc = cp.spawn("caffeinate", ["-i"], {});
      }
      if (awakeProc) {
        if (NX.trackChild) NX.trackChild(awakeProc);                 // dies with the panel
        awakeProc.on("error", function () { awakeProc = null; });
        awakeProc.on("exit", function () { awakeProc = null; });
      }
    } catch (e) { awakeProc = null; }
  }

  // ------------------------------------------------------------------ run
  /* nobody at the screen: the suggested answer, or "" (skip that step) when a person is needed */
  function policy(q, opts) {
    function find(re) { for (var i = 0; i < opts.length; i++) if (re.test(opts[i])) return opts[i]; return ""; }
    if (/MẠCH THỜI GIAN/i.test(q)) return find(/^Không/);                          // decide later with the 🕒 checkbox
    if (/ÂM THANH cho folder/i.test(q)) return find(/gợi ý/) || opts[0] || "";
    if (/theo LỜI NÓI/i.test(q)) return find(/^Phân tích/);
    if (/chưa được phân tích/i.test(q)) return find(/^Chạy/);
    if (/Chưa có plan/i.test(q)) return find(/^Chạy Align/);
    if (/Không thấy folder|Chưa có kịch bản|Chọn nhạc|voice-over|Mức SFX/i.test(q)) return "";
    return find(/gợi ý|khuyến nghị/);
  }
  function setPreset(id) {
    var ps = $("presetSel");
    if (!ps || !id) return;
    if (ps.value !== id) { ps.value = id; ps.dispatchEvent(new Event("change")); }
  }
  var KEEP = ["src", "preset", "script", "scriptMode", "montageMusic", "montageCfg", "montageTarget", "brief", "name", "nameAuto"];
  function snapshot() {
    var o = { _plan: NX.getPlanPath ? NX.getPlanPath() : "" };
    KEEP.forEach(function (k) { o[k] = NX.S[k] === undefined ? undefined : JSON.parse(JSON.stringify(NX.S[k])); });
    return o;
  }
  function restore(o) {
    if (norm(NX.S.src) !== norm(o.src)) NX.setField("src", o.src || "");
    setPreset(o.preset);
    KEEP.forEach(function (k) { if (k !== "src" && k !== "preset") NX.S[k] = o[k]; });
    NX.save();
    try { NX.bindInputs(); } catch (e) {}
    try { NX.setPlanPath(o._plan && exists(o._plan) ? o._plan : ""); } catch (e) {}     // the plan the editor had open (maybe an AI-edited _vN)
    try { if (NX.refreshHints) NX.refreshHints(); } catch (e) {}
  }
  function planStats() {
    var pp = NX.getPlanPath && NX.getPlanPath(), P = null;
    try { P = JSON.parse(fs.readFileSync(pp, "utf8")); } catch (e) {}
    if (!P || !P.segments) return null;
    var dur = 0, gaps = 0;
    P.segments.forEach(function (s) {
      if (s.gap) gaps++;
      dur += s.tl ? (+s.tl[1] - +s.tl[0]) : (s.gap ? (+s.out || 1) - (+s.in || 0) : ((+s.out - +s.in) * 100 / (+s.speed || 100)));
    });
    return { name: P.name, segs: P.segments.length, dur: dur, gaps: gaps, notes: (P.report_notes || []).slice(0, 3) };
  }
  function runItem(it) {
    var t0 = Date.now(), pl = plan(it), mode = modeOf(it.preset);
    it.state = "run"; it.msg = ""; it.res = null; paint();
    NX.log("🌙 Hàng đợi: " + path.basename(it.src) + " · " + presetName(it.preset) + " · " + (STEPS.filter(function (s) { return s[0] === it.steps; })[0] || STEPS[0])[1], "#CCDC42");
    if (!exists(it.src)) return Promise.reject(new Error("không thấy folder"));
    NX.setField("src", it.src);                     // the previous folder's plan / name do not follow
    setPreset(it.preset);
    NX.S.name = ""; NX.S.nameAuto = false; if ($("name")) $("name").value = "";
    NX.S.brief = { type: "", duration: "", platform: "", notes: "" }; NX.S.montageCfg = {}; NX.S.montageTarget = 0;
    // every folder gets ITS script / music only — nothing carries over from the previous folder (a VO preset would pick up music)
    NX.S.script = mode === "script" ? it.script || "" : ""; NX.S.scriptMode = "file"; if ($("script")) $("script").value = NX.S.script;
    NX.S.montageMusic = mode === "beat" ? it.music || "" : "";
    NX.save();
    var did = ["Phân tích"];
    return NX.analyze().then(function () {
      if (!pl.plan) return;
      did.push("Dựng");
      return NX.align().then(function () {
        if (it.steps !== "all") return;
        did.push("Build");
        return NX.build();
      });
    }).then(function () {
      var st = pl.plan ? planStats() : null;
      it.res = st; it.secs = (Date.now() - t0) / 1000;
      it.state = pl.plan || it.steps === "an" ? "ok" : "part";
      it.msg = did.join(" → ") + " · " + hms(it.secs) + (st ? " · " + st.name + " · " + st.segs + " shot · " + mmss(st.dur) + (st.gaps ? " · ⚠ " + st.gaps + " ô thiếu hình" : "") : "") +
               (pl.why ? " · " + pl.why : "");
    }, function (e) {
      it.secs = (Date.now() - t0) / 1000; it.state = "fail"; it.msg = (did[did.length - 1] || "") + ": " + (e && e.message || e);
      if (/đã hủy/.test(it.msg)) throw e;           // Hủy = stop the whole queue
    });
  }
  function start() {
    var items = Q().items.filter(function (x) { return x.state !== "ok"; });
    if (!items.length) { if (Q().items.length) { Q().items.forEach(function (x) { x.state = "wait"; x.msg = ""; }); items = Q().items; } else return; }
    if (NX.isBusy()) { NX.log("• Đang chạy: " + NX.busyLabel() + " — chờ xong rồi chạy hàng đợi", "#e0a82e"); return; }
    var keep = snapshot(), t0 = new Date(), stopped = false;
    running = true; stopAfter = false; buttons(); paint();
    Q().awake = $("qAwake").checked; NX.save();
    if (Q().awake) awake(true);
    NX.setAutoAnswer(policy);
    NX.log("🌙 Bắt đầu hàng đợi: " + items.length + " folder" + (awakeProc ? " · giữ máy không ngủ" : ""), "#CCDC42");
    return NX.exclusive("pipeline", "Hàng đợi chạy đêm", function () {
      return items.reduce(function (p, it) {
        return p.then(function () {
          if (stopAfter || stopped) { if (it.state === "wait") it.state = "skip"; return; }
          return runItem(it).catch(function () { stopped = true; }).then(function () { NX.save(); paint(); });
        });
      }, Promise.resolve());
    }).catch(function (e) { NX.log("✖ Hàng đợi: " + (e && e.message || e), "#e5534b"); }).then(function () {
      NX.setAutoAnswer(null); awake(false);
      running = false;
      restore(keep);
      var rep = report(items, t0, new Date(), stopped || stopAfter);
      buttons(); paint();
      NX.log("🌙 Hàng đợi xong: " + items.filter(function (x) { return x.state === "ok"; }).length + "/" + items.length + " folder" + (rep ? " · báo cáo " + rep : ""), "#6fd08c");
      try { if (window.JESSession) window.JESSession.snap(); } catch (e) {}
    });
  }
  function report(items, t0, t1, stopped) {
    function d(t) { return t.toLocaleDateString("vi-VN") + " " + t.toTimeString().slice(0, 5); }
    var rows = items.map(function (x, i) {
      var r = x.res || {};
      return "| " + (i + 1) + " | " + path.basename(x.src) + " | " + presetName(x.preset) + " | " + (STATE[x.state] || STATE.wait)[0] + " | " +
        (r.name || "—") + " | " + (r.dur ? mmss(r.dur) + " · " + r.segs + " shot" : "—") + " | " + (r.gaps || 0) + " | " + (x.secs ? hms(x.secs) : "—") + " |";
    });
    var notes = items.filter(function (x) { return x.msg && x.state !== "ok"; }).map(function (x) { return "- **" + path.basename(x.src) + "**: " + x.msg; });
    var md = "# Hàng đợi chạy đêm — " + d(t0) + " → " + d(t1) + " (" + hms((t1 - t0) / 1000) + ")" + (stopped ? " · DỪNG GIỮA CHỪNG" : "") + "\n\n" +
      "| # | Folder | Thể loại | Kết quả | Sequence | Thời lượng | Thiếu hình | Thời gian chạy |\n|---|---|---|---|---|---|---|---|\n" + rows.join("\n") + "\n" +
      (notes.length ? "\n## Cần xem\n" + notes.join("\n") + "\n" : "") +
      "\nSáng ra: mở từng sequence trong Premiere, xem một lượt, ghi chú chỗ chưa ưng ở **AI → 📍 Ghi chú** rồi bấm *AI sửa theo ghi chú*. " +
      "Luật rút ra được lưu vào Sổ luật và áp cho lần chạy sau.\n";
    var dir = NX.S.outRoot && exists(NX.S.outRoot) ? NX.S.outRoot : NX.cfgDir, f = path.join(dir, "HangDoi_" + t0.getFullYear() + ("0" + (t0.getMonth() + 1)).slice(-2) +
      ("0" + t0.getDate()).slice(-2) + "_" + ("0" + t0.getHours()).slice(-2) + ("0" + t0.getMinutes()).slice(-2) + ".md");
    try { fs.writeFileSync(f, md, "utf8"); Q().last = norm(f); NX.save(); } catch (e) { f = ""; }
    lastReport();
    return f ? path.basename(f) : "";
  }
  function lastReport() {
    var el = $("qReport"), f = Q().last; if (!el) return;
    el.innerHTML = f && exists(f) ? '<button class="btn chip" id="qOpenRep" title="' + esc(f) + '">📄 Báo cáo lần chạy gần nhất: ' + esc(path.basename(f)) + "</button>" : "";
    if ($("qOpenRep")) $("qOpenRep").onclick = function () { try { cp.exec('start "" "' + f.replace(/\//g, "\\") + '"'); } catch (e) {} };
  }

  function init() {
    if (!$("qCard")) return;
    $("qAwake").checked = Q().awake !== false;
    $("qAwake").onchange = function () { Q().awake = $("qAwake").checked; NX.save(); };
    $("qAdd").onclick = addPick;
    $("qAddCur").onclick = function () { if (NX.S.src) addFolder(NX.S.src); };
    $("qStart").onclick = start;
    $("qStop").onclick = function () { stopAfter = true; NX.log("• Hàng đợi sẽ dừng sau folder đang chạy", "#e0a82e"); $("qStop").disabled = true; };
    $("qKill").onclick = function () { stopAfter = true; NX.cancel(); };
    $("qCard").addEventListener("toggle", function () { if ($("qCard").open) paint(); });
    document.addEventListener("jes:preset", function () { if (!running) paint(); });
    Q().items.forEach(function (x) { if (x.state === "run") { x.state = "fail"; x.msg = "panel đóng giữa chừng"; } });
    buttons(); paint(); lastReport();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
  window.JESQueue = { add: addFolder, start: start, items: function () { return Q().items; }, _test: { policy: policy, plan: plan, findScript: findScript } };
})();
