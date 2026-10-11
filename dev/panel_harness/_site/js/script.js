/* Justin EDIT Suite — tab Kịch bản.
   Ba cách có kịch bản, chọn bằng nút gạt (♪ đổi luôn preset của folder):
   · ✍️ Tự viết       — ô soạn: gõ / dán / mở .docx .md .txt → Kiểm tra (nxcore.script_parser check) → Lưu & Align.
   · 📝 AI viết       — quy trình skill nx-script-from-probe: brief → digest → dàn ý + shot list (AI) → engine
                         nxcore.specscript dựng file NX → (kiểm tra hình) → KichBan_<tên>_vN.md ở gốc source → Align/Build.
                         Kết quả hiện luôn trong ô soạn của ✍️ để sửa tay.
   · ♪ Cắt theo nhạc  — preset mode "beat" (nxcore.montage): không kịch bản, 0 token, cắt đúng beat.
   Công cụ khác: plan mẫu, label màu clip source theo nhóm, marker mô tả lên sequence. */
(function () {
  "use strict";
  var fs = require("fs"), path = require("path");
  var NX = window.NX, $ = function (id) { return document.getElementById(id); };
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); };

  var LABELS = [["Violet", "#a990dd"], ["Iris", "#5f7fe0"], ["Caribbean", "#1dbd9b"], ["Lavender", "#e384e4"], ["Cerulean", "#2e9de0"],
    ["Forest", "#4f9a3a"], ["Rose", "#f35f7e"], ["Mango", "#f79a2f"], ["Purple", "#9348c9"], ["Blue", "#2f5fe0"], ["Teal", "#16a0a5"],
    ["Magenta", "#d53fa2"], ["Tan", "#c9a87c"], ["Green", "#6bc23d"], ["Brown", "#8c5c3b"], ["Yellow", "#e8d43a"]];
  var CYCLE = [4, 7, 13, 11, 2, 15, 8, 6, 10, 1, 12, 5, 3, 9, 0, 14];
  var FIXED = { role: { a_roll: 7, b_roll: 4, junk: 14, audio_master: 15, screen: 3 },
    quality: { "ĐẠT": 13, "ĐẠT(slow-mo)": 10, "YẾU": 7, "LOẠI": 6 }, daypart: { "Sáng": 15, "Trưa": 7, "Chiều": 11, "Tối": 9 } };
  var ROLE_VI = { a_roll: "A-roll (có thoại)", b_roll: "B-roll", junk: "Rác", audio_master: "Audio master", screen: "Quay màn hình" };
  var GROUPS = { folder: "Thư mục", role: "Vai trò (A/B-roll)", quality: "Chất lượng", daypart: "Buổi quay", cam: "Camera" };
  var MK = { chapter: 1, broll: 0, dlg: 6, issue: 3 }, MK_PREFIX = "NX ";
  var AUDIO_EXT = ["mp3", "wav", "m4a", "aif", "aiff", "flac", "aac", "ogg"];

  function S() { return NX.S; }
  function T() { var t = S().scriptTab; if (!t) { t = S().scriptTab = {}; } return t; }
  function work(p) { return path.join(S().src || "", "_nexys_work", p || ""); }
  function readJSON(p, d) { try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) { return d; } }
  function exists(p) { try { return !!p && fs.existsSync(p); } catch (e) { return false; } }
  function say(id, t, cls) { var el = $(id); if (el) { el.textContent = t || ""; el.className = "hint " + (cls || ""); } }
  function tc(x) { x = Math.max(0, +x || 0); var m = Math.floor(x / 60), s = x - m * 60; return m + ":" + (s < 10 ? "0" : "") + (Math.abs(s - Math.round(s)) > 0.05 ? s.toFixed(1) : Math.round(s)); }
  function g(v) { return String(Math.round(v * 100) / 100); }
  function norm(p) { return String(p || "").replace(/\\/g, "/").toLowerCase().normalize("NFC"); }
  function full(f) { return (S().src + "/" + f).replace(/\\/g, "/"); }
  function pdata(id) { try { return NX.presetData(id) || {}; } catch (e) { return {}; } }
  function isBeat(id) { return pdata(id).mode === "beat"; }
  function enable(id, on) { var b = $(id); if (b) b.disabled = !on; }
  function host(fn, args) {
    var expr = fn + "(" + (args || []).map(function (a) { return JSON.stringify(a); }).join(",") + ")";
    return NX.evalHost(expr).then(function (r) {
      var j; try { j = JSON.parse(r); } catch (e) { throw new Error(fn + ": " + String(r).slice(0, 200)); }
      if (j && j.ok === false) throw new Error(j.error);
      return j;
    });
  }
  function openFile(p) { if (exists(p)) { try { require("child_process").exec('start "" "' + p + '"'); } catch (e) {} } }

  // ============================================================ source
  function scan() {
    var src = S().src;
    if (!src) return null;
    var D = readJSON(work("digest.json"), null), media = readJSON(work("media.json"), {}) || {}, roles = readJSON(work("roles.json"), {}) || {};
    var files = Object.keys(media);
    var clips = D && D.clips ? Object.keys(D.clips).map(function (k) { return D.clips[k]; }) :
      files.map(function (f) { var m = media[f] || {}; return { file: f, created_local: m.created_local, cam: m.cam, status: "" }; });
    var vert = 0, folders = {}, t0 = null, t1 = null, days = {};
    files.forEach(function (f) {
      var m = media[f] || {};
      if ((+m.h || 0) > (+m.w || 0)) vert++;
      var fo = f.indexOf("/") > 0 ? f.split("/")[0] : "(gốc)";
      folders[fo] = (folders[fo] || 0) + 1;
      var cl = String(m.created_local || "");
      if (cl) { days[cl.slice(0, 10)] = 1; if (!t0 || cl < t0) t0 = cl; if (!t1 || cl > t1) t1 = cl; }
    });
    var scripts = [];
    try { scripts = fs.readdirSync(src).filter(function (f) { return /^KichBan_.*\.md$/i.test(f) && !/_BANG\.md$/i.test(f); }); } catch (e) {}
    return { analyzed: files.length > 0, n: files.length, vert: vert, folders: folders, t0: t0, t1: t1, days: Object.keys(days).sort(),
             clips: clips, roles: roles, scripts: scripts };
  }
  function renderSource() {
    var sc = scan(), el = $("scSource");
    if (!el) return sc;
    if (!S().src) { el.innerHTML = '<span class="muted">Chưa chọn folder source — chọn ở tab Chạy.</span>'; return sc; }
    if (!sc.analyzed) { el.innerHTML = '<b>' + esc(path.basename(S().src)) + '</b> <span class="muted">· chưa Phân tích — chạy bước 1 ở tab Chạy</span>'; return sc; }
    var fo = Object.keys(sc.folders), hz = sc.n - sc.vert;
    el.innerHTML = '<b>' + esc(path.basename(S().src)) + '</b> <span class="muted">· ' + sc.n + ' clip · ' + (sc.vert > hz ? "dọc 9:16" : "ngang 16:9") +
      (fo.length > 1 ? ' · ' + fo.map(function (f) { return esc(f) + " " + sc.folders[f]; }).join(" · ") : "") +
      (sc.t0 ? ' · ' + esc(sc.days.join(", ")) : "") + '</span>';
    return sc;
  }

  // ============================================================ chế độ: kịch bản AI ↔ cắt theo nhạc
  function setPreset(id) {
    var ps = $("presetSel");
    if (!ps || !id || ps.value === id) return;
    ps.value = id; ps.dispatchEvent(new Event("change"));
  }
  // three ways to get a script: ✍️ write / paste it (manual) · 📝 AI writes it (script) · ♪ cut on the music (beat preset)
  function curMode() { return isBeat() ? "beat" : (T().scMode === "script" ? "script" : "manual"); }
  function setMode(m) {
    var beatNow = isBeat();
    if (m === "beat" && !beatNow) { T().lastScriptPreset = S().preset; NX.save(); setPreset(T().lastBeatPreset || "beat_cut"); }
    if (m !== "beat") {
      T().scMode = m; NX.save();
      if (beatNow) { T().lastBeatPreset = S().preset; NX.save(); setPreset(T().lastScriptPreset || genreKey() || "vlog_travel"); }
    }
    setTimeout(syncMode, 0);
  }
  function syncMode() {
    var mode = curMode(), beat = mode === "beat";
    Array.prototype.forEach.call(document.querySelectorAll("#scMode button"), function (b) { b.classList.toggle("on", b.dataset.m === mode); });
    [["scBeatCard", beat], ["scManualCard", mode === "manual"], ["scBriefCard", mode === "script"], ["scRunCard", mode === "script"],
     ["scResultCard", mode === "script" && !!T().work]].forEach(function (x) {
      var el = $(x[0]); if (el) el.style.display = x[1] ? "" : "none";
    });
    if (mode === "manual") showCurrent();
    var pre = $("presetSel"), name = pre && pre.selectedOptions && pre.selectedOptions[0] ? pre.selectedOptions[0].textContent.trim() : S().preset;
    $("scModeNote").textContent = beat ? "Preset " + name + " — dựng theo nhạc, không cần kịch bản." :
      mode === "manual" ? "Preset " + name + " — gõ / dán / mở kịch bản; khi Align tool tìm từng câu thoại trong lời nói của source." :
      "Preset " + name + " — AI chọn shot ghim nguồn, ra file kịch bản cho Align (sửa tiếp ở ✍️ Tự viết).";
    var ps = $("pst_sc"); if (ps) ps.innerHTML = "<b>●</b>" + (beat ? "Nhạc" : "Kịch bản");
    var ref = $("scriptRef");
    if (ref) {
      var mu = exists(S().montageMusic) ? path.basename(S().montageMusic) : "";
      ref.textContent = beat ? "♪ Cắt theo nhạc — " + (mu || "chưa chọn bài") + " · chỉnh ở tab Kịch bản" :
        "Kịch bản: " + (S().script ? path.basename(S().script) : "chưa có") + " · viết / chọn ở tab Kịch bản";
    }
    if (beat) fillBeat();
  }

  // ============================================================ ✍️ tự viết / sửa kịch bản
  // One editor for "the current script": typed, pasted, opened from .docx/.md/.txt, or written by the AI (it lands here
  // too, ready to edit). Saving writes the file Align uses: the open .md/.txt itself (previous version kept as .bak),
  // or <source>/KichBan_TuViet.md for a new script / a Word file (Word is converted, the .docx is never touched).
  var man = { file: null, dirty: false, loadedFor: null };
  function manMeta(t) { var el = $("scManMeta"); if (el) el.textContent = t || ""; }
  function manTarget() {
    var f = man.file;
    if (f && /\.(md|txt)$/i.test(f)) return f;
    return S().src ? path.join(S().src, "KichBan_TuViet.md").replace(/\\/g, "/") : null;
  }
  function manSetText(txt, file, note) {
    $("scManText").value = txt || "";
    man.file = file || null; man.dirty = false; man.loadedFor = file || "";
    manMeta(file ? path.basename(file) + (note || "") : "kịch bản mới — Lưu thành KichBan_TuViet.md");
    say("scManCheck", "");
  }
  function loadFile(f) {
    if (!exists(f)) return Promise.reject(new Error("không thấy file " + f));
    if (/\.docx$/i.test(f)) {
      return NX.pyJSON(["-m", "nxcore.script_parser", "totext", f], "SCRIPT").then(function (r) {
        manSetText(r.text, f, " · Word (bấm Lưu = tạo bản .md để sửa tiếp)");
      });
    }
    manSetText(fs.readFileSync(f, "utf8"), f);
    return Promise.resolve();
  }
  function showCurrent() {                      // the editor follows S.script unless the editor has unsaved typing
    var f = S().script;
    if (man.dirty || (man.loadedFor !== null && man.loadedFor === (f || ""))) return;
    if (f && exists(f)) loadFile(f).catch(function (e) { manMeta("✖ " + e.message); });
    else if (man.loadedFor === null) manSetText("", null);
  }
  function manOpen() {
    var r = window.cep.fs.showOpenDialogEx(false, false, "Mở kịch bản", S().src || "", ["docx", "md", "txt"]);
    if (!r || !r.data || !r.data.length) return;
    var f = r.data[0].replace(/\\/g, "/");
    loadFile(f).then(function () {
      NX.setField("script", f); S().scriptMode = "file"; NX.save();
      say("scManCheck", "Đã mở " + path.basename(f) + " — Align sẽ dùng file này" + (/\.docx$/i.test(f) ? " (bản Word gốc)" : ""), "ok");
    }, function (e) { say("scManCheck", "✖ " + e.message, "bad"); });
  }
  function manNew() {
    if (man.dirty && $("scManText").value.trim() && !window.confirm("Bỏ phần đang gõ chưa lưu?")) return;
    manSetText("", null);
    $("scManText").focus();
  }
  function manSave() {
    var txt = $("scManText").value;
    if (!txt.trim()) { say("scManCheck", "Ô kịch bản đang trống — chưa lưu", "bad"); return null; }
    var f = manTarget();
    if (!f) { say("scManCheck", "Chọn folder source trước (tab Chạy)", "bad"); return null; }
    try { if (exists(f)) fs.copyFileSync(f, f + ".bak"); } catch (e) {}
    fs.writeFileSync(f, txt.replace(/\r\n/g, "\n"), "utf8");
    man.file = f; man.dirty = false; man.loadedFor = f;
    NX.setField("script", f); S().scriptMode = "file"; NX.save();
    manMeta(path.basename(f) + " · đã lưu " + new Date().toLocaleTimeString().slice(0, 5));
    return f;
  }
  function manCheck() {
    var txt = $("scManText").value;
    if (!txt.trim()) { say("scManCheck", "Ô kịch bản đang trống", "bad"); return Promise.resolve(); }
    var tmp = path.join(require("os").tmpdir(), "jes_script_check.md");
    fs.writeFileSync(tmp, txt, "utf8");
    say("scManCheck", "Đang đọc kịch bản như lúc Align…");
    return NX.pyJSON(["-m", "nxcore.script_parser", "check", tmp], "SCRIPT").then(function (r) {
      var parts = [r.scenes + " cảnh", (r.dialogue + r.question) + " câu thoại", r.broll + " B-roll", r.vo + " VO"];
      if (r.hooks) parts.push(r.hooks + " hook");
      if ((r.keywords || []).length) parts.push("từ khóa: " + r.keywords.slice(0, 6).join(", "));
      say("scManCheck", "✔ " + parts.join(" · ") + ((r.warn || []).length ? "  ⚠ " + r.warn.join(" · ") : ""), (r.warn || []).length ? "bad" : "ok");
    }, function (e) { say("scManCheck", "✖ " + e.message, "bad"); });
  }
  function manUse() {
    var f = manSave();
    if (!f) return Promise.resolve();
    NX.log("• Align với kịch bản " + path.basename(f));
    var b = document.querySelector('nav button[data-tab="run"]'); if (b) b.click();
    return NX.align();
  }
  // the AI's script (📝 AI viết) lands in the editor too, so it can be corrected by hand before Align
  NX.showScript = function (txt, file) {
    if (man.dirty && $("scManText").value.trim()) return;      // never overwrite unsaved typing
    manSetText(txt, file, " · AI viết");
  };

  // ============================================================ ♪ cắt theo nhạc
  function fillBeat() {
    var sel = $("scBeatPreset"), src = $("presetSel");
    if (sel && src) {
      sel.innerHTML = "";
      Array.prototype.forEach.call(src.options, function (o) {
        if (!isBeat(o.value)) return;
        var n = document.createElement("option"); n.value = o.value; n.textContent = o.textContent.trim(); sel.appendChild(n);
      });
      sel.value = S().preset;
    }
    var mu = exists(S().montageMusic) ? S().montageMusic : "";
    $("scBeatMusic").value = mu ? path.basename(mu) : ""; $("scBeatMusic").title = mu;
    var bt = T().beat || {};
    $("scBeatLen").value = String(S().montageTarget || 0); if (!$("scBeatLen").value) $("scBeatLen").value = "0";
    $("scBeatPace").value = String(bt.pace || 1);
    $("scBeatSpeech").value = String(bt.speech != null ? bt.speech : 0);
    $("scBeatEnergy").checked = bt.energy !== false;
    musicInfo(); renderBeat();
  }
  function saveBeat() {
    var bt = T().beat = T().beat || {}, pd = pdata();
    bt.pace = +$("scBeatPace").value || 1; bt.speech = +$("scBeatSpeech").value || 0; bt.energy = $("scBeatEnergy").checked;
    S().montageTarget = +$("scBeatLen").value || 0;
    var cfg = { speech_moments: bt.speech, energy_pace: bt.energy };
    if (bt.pace !== 1) cfg.pace = Math.round((+pd.pace || 1) * bt.pace * 100) / 100;
    S().montageCfg = cfg; NX.save();
  }
  function musicInfo() {
    var el = $("scBeatInfo"), mu = S().montageMusic, sm = readJSON(work("music/_last_summary.json"), null);
    if (!el) return;
    if (!exists(mu)) { el.textContent = "Chưa chọn nhạc."; return; }
    if (!sm || sm.file !== mu) { el.textContent = "Chưa phân tích bài — tự phân tích khi bấm Cắt theo nhạc (có cache)."; return; }
    el.textContent = sm.bpm + " BPM · " + sm.mood + " · " + tc(sm.duration) + " · " + sm.sections.map(function (x) { return x[0] + " " + tc(x[1]); }).join(" · ");
  }
  function analyzeMusic(mu) {
    var code = "import sys,json,os;from nxcore.montage import load_music;A=load_music(sys.argv[1],sys.argv[2]);" +
      "json.dump({'file':sys.argv[1],'bpm':A.get('bpm'),'mood':A.get('mood'),'duration':A.get('duration')," +
      "'sections':[[s['label'],round(s['start'],1),round(s['end'],1)] for s in A.get('sections',[])]}," +
      "open(os.path.join(sys.argv[2],'music','_last_summary.json'),'w',encoding='utf-8'),ensure_ascii=False)";
    return NX.run(["-c", code, mu, work("")], "Phân tích nhạc (BPM, đoạn)").then(musicInfo);
  }
  function pickMusic() {
    var r = window.cep && window.cep.fs ? window.cep.fs.showOpenDialogEx(false, false, "Chọn nhạc cho montage", S().src || "", AUDIO_EXT) : null;
    var f = r && r.data && r.data.length ? r.data[0].replace(/\\/g, "/") : "";
    if (!f) return;
    S().montageMusic = f; NX.save();
    try { document.dispatchEvent(new CustomEvent("jes:preset", { detail: S().preset })); } catch (e) {}
    $("scBeatMusic").value = path.basename(f);
    NX.exclusive("pipeline", "Phân tích nhạc", function () { return analyzeMusic(f); }).catch(function (e) { say("scBeatInfo", "✖ " + e.message, "bad"); });
  }
  function runBeat() {
    if (!isBeat()) return Promise.reject(new Error("preset hiện tại không phải kiểu cắt theo nhạc"));
    saveBeat();
    say("scBeatResult", "Đang cắt theo nhạc…");
    return NX.align().then(function () {
      keepVer(0, "");
      if (exists(S().montageMusic)) return analyzeMusic(S().montageMusic).catch(function () {});
    }).then(renderBeat, function (e) { say("scBeatResult", "✖ " + e.message, "bad"); throw e; });
  }
  /* ── 🎲 Shuffle: xáo lại shot của bản cắt theo nhạc bằng seed ngẫu nhiên. Mỗi bản lưu thành plan_X.v<seed>.json
     (bản gốc = v0) để quay lại; bản mới tránh đặt đúng clip cũ ở đúng chỗ. */
  function vers() { var pp = NX.getPlanPath && NX.getPlanPath(), all = T().beatVers = T().beatVers || {}; return pp ? (all[pp] = all[pp] || []) : []; }
  function verFile(seed) { var pp = NX.getPlanPath(); return pp.replace(/\.json$/i, ".v" + seed + ".json"); }
  function keepVer(seed, note) {
    var pp = NX.getPlanPath && NX.getPlanPath(); if (!pp || !exists(pp)) return;
    var f = verFile(seed), list = vers();
    try { fs.copyFileSync(pp, f); var md = pp.replace(/\.json$/i, "_align.md"); if (exists(md)) fs.copyFileSync(md, f.replace(/\.json$/i, "_align.md")); } catch (e) { return; }
    list = list.filter(function (v) { return v.seed !== seed; });
    list.push({ seed: seed, file: f, at: Date.now(), note: note || "" });
    while (list.length > 12) { var old = list.shift(); if (old.seed) { try { fs.unlinkSync(old.file); } catch (e) {} } }
    T().beatVers[pp] = list; T().beatCur = seed; NX.save();
  }
  function fillVers() {
    var sel = $("scBeatVer"); if (!sel) return;
    var list = vers().filter(function (v) { return exists(v.file); }), cur = T().beatCur;
    sel.innerHTML = list.length ? list.map(function (v) {
      var d = new Date(v.at);
      return '<option value="' + v.seed + '">' + (v.seed ? "🎲 #" + v.seed : "♪ bản gốc") + " · " + ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2) + (v.note ? " · " + esc(v.note) : "") + "</option>";
    }).join("") : '<option value="">— chưa có bản —</option>';
    if (list.some(function (v) { return v.seed === cur; })) sel.value = String(cur);
    sel.style.display = list.length > 1 ? "" : "none";
  }
  function useVer(seed) {
    var v = vers().filter(function (x) { return x.seed === +seed; })[0], pp = NX.getPlanPath && NX.getPlanPath();
    if (!v || !pp || !exists(v.file)) return;
    try { fs.copyFileSync(v.file, pp); var md = v.file.replace(/\.json$/i, "_align.md"); if (exists(md)) fs.copyFileSync(md, pp.replace(/\.json$/i, "_align.md")); } catch (e) { return say("scBeatResult", "✖ " + e.message, "bad"); }
    T().beatCur = v.seed; NX.save();
    if (NX.loadPlan) NX.loadPlan();
    renderBeat();
    NX.log("• Quay lại " + (v.seed ? "bản shuffle #" + v.seed : "bản gốc") + " — bấm Build để dựng bản này");
  }
  function shuffleBeat() {
    if (!isBeat()) return Promise.reject(new Error("preset hiện tại không phải kiểu cắt theo nhạc"));
    var pp = NX.getPlanPath && NX.getPlanPath();
    if (pp && exists(pp) && !vers().some(function (v) { return v.seed === (T().beatCur || 0); })) keepVer(T().beatCur || 0, "");
    var seed = 1 + Math.floor(Math.random() * 99998), rhythm = $("scBeatShufMode").value === "rhythm", amt = +$("scBeatShufAmt").value || 0.65;
    T().shufMode = $("scBeatShufMode").value; T().shufAmt = String(amt);
    saveBeat();
    var cfg = S().montageCfg = Object.assign({}, S().montageCfg || {}, { shuffle_seed: seed, shuffle_amount: amt, shuffle_rhythm: rhythm });
    if (pp && exists(pp)) { var prev = pp.replace(/\.json$/i, ".prev.json"); try { fs.copyFileSync(pp, prev); cfg.shuffle_avoid_plan = prev.replace(/\\/g, "/"); } catch (e) {} }
    NX.save();
    say("scBeatResult", "🎲 Đang shuffle #" + seed + "…");
    var clean = function () { var c = S().montageCfg || {}; ["shuffle_seed", "shuffle_amount", "shuffle_rhythm", "shuffle_avoid_plan"].forEach(function (k) { delete c[k]; }); NX.save(); };
    return NX.align().then(function () {
      clean(); keepVer(seed, (rhythm ? "hình+nhịp" : "hình") + " · " + ({ "0.35": "nhẹ", "0.65": "vừa", "1": "mạnh" }[String(amt)] || amt));
      renderBeat();
    }, function (e) { clean(); say("scBeatResult", "✖ " + e.message, "bad"); throw e; });
  }
  function renderBeat() {
    fillVers();
    var el = $("scBeatResult"), tb = $("scBeatTable"), P = plan();
    var ok = !!(P && P.segments && P.segments.some(function (s) { return s.tl; }));
    enable("bScBeatBuild", ok); enable("bScBeatReport", ok); enable("bScBeatShuffle", ok);
    if (!el) return;
    if (!ok) { if (!/Đang|✖/.test(el.textContent)) el.textContent = ""; tb.innerHTML = ""; return; }
    var segs = P.segments.filter(function (s) { return s.tl; }), end = segs[segs.length - 1].tl[1];
    var lens = segs.map(function (s) { return s.tl[1] - s.tl[0]; }), by = {};
    segs.forEach(function (s) { var k = s.beat || s.scene || "?"; (by[k] = by[k] || []).push(s.tl[1] - s.tl[0]); });
    var ins = P.audio_inserts || [], avg = function (v) { return v.reduce(function (a, b) { return a + b; }, 0) / v.length; };
    el.className = "hint ok";
    el.innerHTML = '✔ <b>' + tc(end) + '</b> · ' + segs.length + ' shot · TB ' + avg(lens).toFixed(2) + 's · ' +
      Object.keys(by).map(function (k) { return esc(k) + " " + avg(by[k]).toFixed(1) + "s×" + by[k].length; }).join(" · ") +
      ' · ' + (ins.length ? ins.length + ' câu nói chèn' : 'chỉ nhạc') + ' → bấm Build';
    tb.innerHTML = '<table class="sc-tbl"><thead><tr><th>TL</th><th>Đoạn</th><th>Shot</th></tr></thead><tbody>' + segs.map(function (s) {
      return '<tr data-f="' + esc(s.file || "") + '" data-a="' + (+s.in || 0) + '" data-b="' + (+s.out || 0) + '"><td>' + tc(s.tl[0]) + '</td><td>' + esc(s.beat || s.scene || "") +
        '</td><td><div>' + esc(String(s.label || "").replace(/^♪\s*\S+\s*·\s*/, "")) + '</div><div class="muted">' + esc(s.file || "—") + ' @ ' + g(+s.in || 0) + '–' + g(+s.out || 0) +
        (s.speed && s.speed !== 100 ? ' · ' + s.speed + '%' : '') + '</div></td></tr>';
    }).join("") + '</tbody></table>';
    bindRows(tb);
  }

  // ============================================================ 📝 kịch bản AI
  function genres() {
    var G = (window.NXAI && window.NXAI.specGenres) || {}, out = {};
    Object.keys(G).forEach(function (k) { if (!isBeat(k)) out[k] = G[k]; });     // thể loại theo nhạc → dùng chế độ ♪
    return out;
  }
  function genreKey() { var sel = $("scType"); return sel && sel.value ? sel.value : ""; }
  function fillBrief() {
    var sc = scan() || {}, b = S().brief || {}, G = genres(), sel = $("scType");
    if (sel && sel.options.length !== Object.keys(G).length) {
      sel.innerHTML = "";
      Object.keys(G).forEach(function (k) { var o = document.createElement("option"); o.value = k; o.textContent = G[k]; sel.appendChild(o); });
    }
    var mine = b.specFor === S().src;
    $("scDuration").value = mine && b.duration ? b.duration : (sc.vert > (sc.n || 0) / 2 ? "Dọc 9:16 · 60–90 giây" : "Ngang 16:9 · 3–4 phút");
    var key = Object.keys(G).filter(function (k) { return G[k] === b.type; })[0] || (G[S().preset] ? S().preset : Object.keys(G)[0]);
    if (sel) sel.value = key || "";
    $("scVo").value = mine && b.vo ? b.vo : "Không VO";
    $("scCta").value = mine && b.cta ? b.cta : "Không CTA";
    $("scNotes").value = mine && b.notes && !/^không có$/i.test(b.notes) ? b.notes : "";
    $("scVision").checked = T().vision !== false;
  }
  function saveBrief() {
    var G = genres(), b = Object.assign({}, S().brief || {});
    b.duration = $("scDuration").value.trim() || "Ngang 16:9 · 3–4 phút";
    b.type = G[genreKey()] || b.type;
    b.vo = $("scVo").value;
    b.cta = $("scCta").value.trim() || "Không CTA";
    b.notes = $("scNotes").value.trim() || "Không có";
    b.specFor = S().src;
    S().brief = b; T().vision = $("scVision").checked; NX.save();
    return b;
  }

  // ---- tiến trình: đọc log của luồng ghim nguồn → các bước có trạng thái
  var STEPS = [["digest", "Đọc source (digest)"], ["outline", "AI lập dàn ý"], ["chapters", "AI chọn shot từng chương"],
               ["engine", "Engine dựng file NX + check parser"], ["vision", "Kiểm tra hình (AI vision)"], ["publish", "Xuất KichBan_*.md ra gốc source"]];
  var run = null;
  function stepsInit(withVision) {
    run = { st: {}, note: {}, t0: Date.now(), cur: null };
    STEPS.forEach(function (s) { run.st[s[0]] = s[0] === "vision" && !withVision ? "skip" : ""; });
    $("scErr").style.display = "none";
    drawSteps();
    run.timer = setInterval(function () { $("scRunMeta").textContent = "đang chạy " + tc((Date.now() - run.t0) / 1000); }, 1000);
  }
  function setStep(k, state, note) {
    if (!run) return;
    if (state === "run") STEPS.forEach(function (s) { if (s[0] === k) return; if (run.st[s[0]] === "run") run.st[s[0]] = "ok"; });
    var idx = STEPS.map(function (s) { return s[0]; }).indexOf(k);
    if (state === "run" || state === "ok") STEPS.slice(0, idx).forEach(function (s) { if (!run.st[s[0]]) run.st[s[0]] = "ok"; });
    run.st[k] = state; if (note != null) run.note[k] = note;
    if (state === "run") run.cur = k;
    drawSteps();
  }
  function drawSteps() {
    var ol = $("scSteps"); if (!ol || !run) return;
    ol.innerHTML = STEPS.filter(function (s) { return run.st[s[0]] !== "skip"; }).map(function (s) {
      return '<li class="' + (run.st[s[0]] || "") + '">' + esc(s[1]) + (run.note[s[0]] ? ' <span class="muted">' + esc(run.note[s[0]]) + '</span>' : "") + '</li>';
    }).join("");
  }
  function stepsEnd(err) {
    if (!run) return;
    clearInterval(run.timer);
    if (err) { if (run.cur) run.st[run.cur] = "bad"; }
    else STEPS.forEach(function (s) { if (run.st[s[0]] === "run" || run.st[s[0]] === "") run.st[s[0]] = "ok"; });
    drawSteps();
    $("scRunMeta").textContent = (err ? "dừng sau " : "xong trong ") + tc((Date.now() - run.t0) / 1000);
  }
  function track(m) {
    m = String(m || "");
    var x;
    if (/Digest source/.test(m)) setStep("digest", /xong/.test(m) ? "ok" : "run");
    else if (/Dùng lại dàn ý/.test(m)) setStep("outline", "ok", "dùng lại, 0 token");
    else if ((x = m.match(/AI lập dàn ý \(([^,]+),\s*~?([\d.,]+k?) token/))) setStep("outline", "run", x[1] + " · ~" + x[2] + " token");
    else if (/✔ Dàn ý:/.test(m)) setStep("outline", "ok", (m.match(/\|/g) || []).length + 1 + " chương");
    else if ((x = m.match(/▶ Chương (\d+)\/(\d+)/))) setStep("chapters", "run", x[1] + "/" + x[2]);
    else if (/Dùng lại shot list|shot list.*0 token/.test(m)) setStep("chapters", "run", "dùng lại");
    else if (/Engine dựng kịch bản/.test(m)) setStep("engine", /xong/.test(m) ? "ok" : "run");
    else if (/Kịch bản ghim nguồn:/.test(m)) setStep("engine", "ok", (m.split("·")[1] || "").trim());
    else if (/Contact sheet từng shot|AI kiểm tra hình/.test(m)) setStep("vision", "run");
    else if ((x = m.match(/^• (\S+) (.+?) → (?:tự chuyển sang|tạm dùng) (\S+)/))) {
      if (run && run.cur) { run.note[run.cur] = (run.note[run.cur] ? run.note[run.cur] + " · " : "") + x[1] + " " + x[2] + " → " + x[3]; drawSteps(); }
    }
  }
  function showErr(e) {
    var msg = e && e.message ? e.message : String(e), box = $("scErr");
    var hint = /quota|quá tải|high demand|503|429|timeout|Gemini/i.test(msg) ?
      'Model AI đang hết quota / quá tải. Bấm <b>Viết kịch bản</b> lại sau ít phút — dàn ý và chương đã xong được lưu, không tốn token lại. Hoặc đổi model "Viết kịch bản" sang OpenRouter / Ollama: <a href="#" id="scGoSet">mở Cài đặt</a>.' :
      /phân tích|Phân tích/.test(msg) ? 'Chạy bước 1 · Phân tích ở tab Chạy trước.' : "";
    box.innerHTML = "✖ " + esc(msg) + (hint ? '<div class="hint" style="margin-top:4px">' + hint + '</div>' : "");
    box.style.display = "";
    var a = $("scGoSet"); if (a) a.onclick = function (ev) { ev.preventDefault(); var b = document.querySelector('nav button[data-tab="set"]'); if (b) b.click(); };
  }

  function latestRoot() {
    try {
      var L = fs.readdirSync(S().src).filter(function (f) { return /^KichBan_.*_v\d+\.md$/i.test(f) && !/_BANG\.md$/i.test(f); });
      L.sort(function (a, b) { return fs.statSync(path.join(S().src, a)).mtimeMs - fs.statSync(path.join(S().src, b)).mtimeMs; });
      return L.length ? path.join(S().src, L[L.length - 1]) : null;
    } catch (e) { return null; }
  }
  function publish(md) {
    var title = (String(fs.readFileSync(md, "utf8")).split(/\r?\n/)[0] || "").split("—")[0].trim();
    return NX.run(["-m", "nxcore.specscript", "publish", md, "--src", S().src, "--title", title], "Xuất KichBan ra gốc source").then(function () {
      T().root = latestRoot(); T().work = md; T().at = Date.now();
      if (T().root) { S().scriptMode = "file"; NX.setField("script", T().root.replace(/\\/g, "/")); } else NX.save();
    });
  }
  function write(fresh) {
    var sc = scan();
    if (!sc || !sc.analyzed) return Promise.reject(new Error("source chưa Phân tích — chạy bước 1 ở tab Chạy"));
    if (isBeat()) return Promise.reject(new Error("preset đang ở kiểu cắt theo nhạc — gạt sang 📝 Kịch bản AI"));
    if (!window.NXAI || !window.NXAI.specScript) return Promise.reject(new Error("module AI chưa tải"));
    var b = saveBrief(), vis = $("scVision").checked, md, orig = NX.log;
    stepsInit(vis);
    NX.log = function (m) { try { track(m); } catch (e) {} return orig.apply(NX, arguments); };
    var restore = function () { NX.log = orig; };
    orig.call(NX, "▶ Tab Kịch bản · " + b.duration + " · " + b.type + " · " + b.vo + " · " + b.cta, "#CCDC42");
    setStep("digest", "run");
    return window.NXAI.specScript({ noConfirm: true, fresh: !!fresh }).then(function () {
      md = S().script;
      if (!exists(md)) throw new Error("engine không tạo được file kịch bản");
      setStep("engine", "ok");
      if (!vis) return null;
      setStep("vision", "run");
      return window.NXAI.specVision(md).then(function (r) {
        setStep("vision", "ok", r && r.issues && r.issues.length ? r.issues.length + " shot cần xem" : "không thấy lỗi");
      }, function (e) { setStep("vision", "bad", e.message); });
    }).then(function () {
      setStep("publish", "run");
      return publish(md);
    }).then(function () {
      setStep("publish", "ok", T().root ? path.basename(T().root) : "");
      restore(); stepsEnd(null);
      renderResult(); syncMode();
      orig.call(NX, "✔ Kịch bản: " + (T().root || md).replace(/\\/g, "/") + " — sẵn sàng Align", "#5bbf6a");
    }, function (e) { restore(); stepsEnd(e); showErr(e); throw e; });
  }
  function vision() {
    var md = T().work;
    if (!exists(md)) return Promise.reject(new Error("chưa có kịch bản"));
    enable("bScVision", false);
    return window.NXAI.specVision(md).then(function () { return publish(md); }).then(renderResult, function (e) { renderResult(); throw e; });
  }

  // ---- kết quả
  function report() { var md = T().work; return md ? readJSON(md.replace(/\.md$/i, ".report.json"), null) : null; }
  function bindRows(box) {
    Array.prototype.forEach.call(box.querySelectorAll("tr[data-f]"), function (tr) {
      if (!tr.dataset.f) return;
      tr.title = "Bấm để mở clip đúng đoạn trong Source monitor";
      tr.onclick = function () { host("nxOpenInSource", [full(tr.dataset.f), +tr.dataset.a, +tr.dataset.b]).catch(function (e) { NX.log("✖ " + e.message, "#e5534b"); }); };
    });
  }
  function renderResult() {
    var box = $("scResult"), rep = report(), has = !!(rep && rep.shot_list);
    ["bScAlign", "bScOpen", "bScVision"].forEach(function (id) { enable(id, has); });
    enable("bScTable", has && exists(T().root && T().root.replace(/\.md$/i, "_BANG.md")));
    enable("bScSheets", has && exists((rep && rep.vision && rep.vision.sheets_dir) || (T().work || "").replace(/\.md$/i, "_sheets")));
    var card = $("scResultCard"); if (card) card.style.display = has && !isBeat() ? "" : "none";
    if (!box) return;
    if (!has) { box.innerHTML = ""; $("scTable").innerHTML = ""; return; }
    var issues = {}; ((rep.vision || {}).issues || []).forEach(function (x) { (issues[x.shot] = issues[x.shot] || []).push(x); });
    var nIss = Object.keys(issues).length, p = rep.parser || {};
    box.innerHTML = '<div><b>' + tc(rep.total) + '</b>' + (rep.target ? ' / đích ' + tc(rep.target) : "") + ' · ' + rep.shots + ' shot (' + rep.dialogue + ' thoại, ' + rep.broll + ' b-roll) · ' +
      rep.vo + ' VO · ' + rep.text + ' text · thiếu nguồn ' + ((p.missing || []).length) + '</div>' +
      '<div class="hint">' + esc(path.basename(T().root || T().work)) + '</div>' +
      ((rep.warnings || []).length ? '<div class="hint warn">⚠ ' + rep.warnings.slice(0, 4).map(esc).join(" · ") + '</div>' : "") +
      (rep.vision ? '<div class="hint ' + (nIss ? "warn" : "ok") + '">Kiểm tra hình: ' + (nIss ? nIss + " shot cần xem (tô cam)" : "không thấy lỗi") + '</div>' : "");
    var rows = [], cur = null;
    rep.shot_list.forEach(function (s) {
      if (s.ch !== cur) {
        cur = s.ch; var c = (rep.chapters || [])[cur - 1] || {};
        rows.push('<tr class="sc-ch"><td colspan="4">' + cur + ' · ' + esc(s.chapter) + ' <span class="muted">' + tc(c.tin) + '–' + tc(c.tout) + '</span></td></tr>');
      }
      var iss = issues[s.id] || [];
      var what = s.kind === "dlg" ? '<span class="sc-q">“' + esc(s.q) + '”</span>' : esc(s.d);
      var extra = [s.vo ? "VO: " + s.vo : "", s.title ? "TITLE: " + s.title : "", s.text ? "Text: " + s.text : ""].filter(Boolean).map(esc).join(" · ");
      rows.push('<tr class="' + (iss.length ? "sc-iss" : "") + '" data-f="' + esc(s.file) + '" data-a="' + s.a + '" data-b="' + s.b + '">' +
        '<td>' + tc(s.t0) + '</td><td>' + esc(s.id) + '</td>' +
        '<td><div>' + what + '</div><div class="muted">' + esc(s.file) + ' @ ' + g(s.a) + '–' + g(s.b) + ' · ' + esc(s.speed) + '</div>' +
        (iss.length ? '<div class="sc-issue">⚠ ' + iss.map(function (x) { return esc(x.problem); }).join(" · ") + '</div>' : "") + '</td>' +
        '<td>' + (extra || '<span class="muted">—</span>') + '</td></tr>');
    });
    $("scTable").innerHTML = '<table class="sc-tbl"><thead><tr><th>TC</th><th>Shot</th><th>Hình / thoại</th><th>VO · text</th></tr></thead><tbody>' + rows.join("") + '</tbody></table>';
    bindRows($("scTable"));
  }

  // ============================================================ label màu
  function daypart(cl) { var h = +String(cl || "").slice(11, 13); if (isNaN(h) || !cl) return "Không rõ giờ"; return h < 11 ? "Sáng" : h < 14 ? "Trưa" : h < 18 ? "Chiều" : "Tối"; }
  function groupOf(mode, c, sc) {
    if (mode === "folder") return String(c.file).indexOf("/") > 0 ? c.file.split("/")[0] : "(gốc)";
    if (mode === "role") return ((sc.roles[c.file] || {}).role) || "b_roll";
    if (mode === "quality") return c.status || "Chưa chấm";
    if (mode === "daypart") return daypart(c.created_local);
    return c.cam || "Không rõ cam";
  }
  function labelPlan(mode) {
    var sc = scan();
    if (!sc || !sc.analyzed) throw new Error("source chưa phân tích");
    var groups = {}, order = [];
    sc.clips.forEach(function (c) { var k = groupOf(mode, c, sc); if (!groups[k]) { groups[k] = []; order.push(k); } groups[k].push(c.file); });
    var used = {}, colors = {}, ci = 0;
    order.forEach(function (k) { var fx = (FIXED[mode] || {})[k]; if (fx !== undefined && !used[fx]) { colors[k] = fx; used[fx] = 1; } });
    order.forEach(function (k) {
      if (colors[k] !== undefined) return;
      while (used[CYCLE[ci % CYCLE.length]] && ci < 64) ci++;
      colors[k] = CYCLE[ci % CYCLE.length]; used[colors[k]] = 1; ci++;
    });
    var bin = path.basename(S().src), items = [];
    order.forEach(function (k) {
      groups[k].forEach(function (f) {
        var sub = String(f).indexOf("/") > 0 ? f.split("/").slice(0, -1).join("/") : "";
        items.push({ path: full(f), color: colors[k], bin: bin + (sub ? "/" + sub : "") });
      });
    });
    return { groups: groups, order: order, colors: colors, items: items, mode: mode };
  }
  function renderLegend() {
    var el = $("scLegend"), mode = $("scGroup").value;
    if (!el) return;
    try {
      var pl = labelPlan(mode);
      el.innerHTML = pl.order.map(function (k) {
        var L = LABELS[pl.colors[k]];
        return '<span class="sc-sw"><i style="background:' + L[1] + '"></i>' + esc(mode === "role" ? (ROLE_VI[k] || k) : k) + ' <span class="muted">' + pl.groups[k].length + ' · ' + L[0] + '</span></span>';
      }).join("");
    } catch (e) { el.innerHTML = '<span class="muted">' + esc(e.message) + '</span>'; }
  }
  function applyLabels(silent) {
    var mode = $("scGroup").value, pl;
    try { pl = labelPlan(mode); } catch (e) { say("scLabelStatus", "✖ " + e.message, "bad"); return Promise.resolve(); }
    say("scLabelStatus", "Đang gán màu " + pl.items.length + " clip…");
    return host("jesLabelItems", [JSON.stringify(pl.items), $("scImport").checked]).then(function (r) {
      var msg = "✔ Label " + r.labeled + "/" + r.total + " clip theo " + GROUPS[mode].toLowerCase() + " · " + pl.order.length + " nhóm" +
        (r.missing ? " · " + r.missing + " clip chưa có trong project (bật Import)" : "") + ((r.errors || []).length ? " · lỗi: " + r.errors[0] : "");
      say("scLabelStatus", msg, r.missing || (r.errors || []).length ? "warn" : "ok");
      NX.log(msg, r.missing ? "#e0a82e" : "#5bbf6a");
    }).catch(function (e) { say("scLabelStatus", "✖ " + e.message, "bad"); if (!silent) throw e; });
  }

  // ============================================================ marker
  function plan() {
    var P = NX.getPlan && NX.getPlan();
    if (P && P.segments) return P;
    var pp = NX.getPlanPath && NX.getPlanPath();
    return pp ? readJSON(pp, null) : null;
  }
  function markerItems(mode) {
    var rep = isBeat() ? null : report(), P = plan();
    return host("jesRvShots", []).then(function (r) {
      var shots = (r.shots || []).filter(function (s) { return s.enabled !== false; }).sort(function (a, b) { return a.start - b.start || a.track - b.track; });
      if (!shots.length) throw new Error("sequence đang mở chưa có clip");
      var list = rep && rep.shot_list ? rep.shot_list : [], segs = P && P.segments ? P.segments : [];
      function near(arr, media, inS, key) {
        var best = null, bd = 0.6;
        arr.forEach(function (s) { if (s.file && norm(full(s.file)) === media) { var d = Math.abs((+s[key] || 0) - inS); if (d < bd) { bd = d; best = s; } } });
        return best;
      }
      var iss = {}; (((rep || {}).vision || {}).issues || []).forEach(function (x) { (iss[x.shot] = iss[x.shot] || []).push(x.problem); });
      var items = [], lastCh = null, lastBeat = null, matched = 0;
      shots.forEach(function (sh) {
        var media = norm(sh.media), s = near(list, media, sh.in, "a"), seg = s ? null : near(segs, media, sh.in, "in");
        if (!s && !seg) return;
        matched++;
        var dur = Math.max(0.1, sh.end - sh.start);
        if (s && s.ch !== lastCh) {
          lastCh = s.ch; var c = (rep.chapters || [])[s.ch - 1] || {};
          items.push({ t: sh.start, name: MK_PREFIX + "▌" + s.ch + " · " + s.chapter, comment: "Phân cảnh " + s.ch + " · " + tc(c.tin) + "–" + tc(c.tout), color: MK.chapter,
                       dur: mode === "chapter" ? Math.max(dur, (c.tout || 0) - (c.tin || 0)) : 0 });
        }
        if (seg && seg.beat && seg.beat !== lastBeat) {
          lastBeat = seg.beat; items.push({ t: sh.start, name: MK_PREFIX + "▌" + seg.beat, comment: "Đoạn nhạc " + seg.beat, color: MK.chapter, dur: 0 });
        }
        if (mode === "chapter") return;
        var lines = [];
        if (s) {
          lines.push(s.kind === "dlg" ? "THOẠI: “" + s.q + "”" : "HÌNH: " + s.d);
          if (s.vo) lines.push("VO: " + s.vo);
          if (s.title) lines.push("TITLE: " + s.title);
          if (s.text) lines.push("TEXT: " + s.text);
          lines.push("Nguồn: " + s.file + " @ " + g(s.a) + "–" + g(s.b) + " · " + s.speed);
          if (iss[s.id]) lines.push("⚠ " + iss[s.id].join(" · "));
        } else {
          lines.push(String(seg.label || seg.file));
          lines.push("Nguồn: " + seg.file + " @ " + g(+seg.in) + "–" + g(+seg.out) + (seg.speed && seg.speed !== 100 ? " · " + seg.speed + "%" : ""));
        }
        items.push({ t: sh.start, name: MK_PREFIX + (s ? s.id + " · " + (s.kind === "dlg" ? "“" + s.q + "”" : s.d) : String(seg.label || "")).slice(0, 64),
                     comment: lines.join("\n"), color: s && iss[s.id] ? MK.issue : s && s.kind === "dlg" ? MK.dlg : MK.broll, dur: dur });
      });
      if (!matched) throw new Error("không khớp clip nào của sequence với kịch bản/plan hiện tại — mở đúng sequence đã Build");
      return { items: items, matched: matched, total: shots.length, seq: r.name };
    });
  }
  function addMarkers() {
    say("scMkStatus", "Đọc sequence…");
    return markerItems($("scMkMode").value).then(function (m) {
      return host("jesAddMarkers", [JSON.stringify(m.items), MK_PREFIX]).then(function (r) {
        say("scMkStatus", "✔ " + r.added + " marker trên '" + m.seq + "' (" + m.matched + "/" + m.total + " clip khớp)" + (r.removed ? " · thay " + r.removed + " marker NX cũ" : ""), "ok");
      });
    }).catch(function (e) { say("scMkStatus", "✖ " + e.message, "bad"); });
  }
  function clearMarkers() {
    return host("jesAddMarkers", ["[]", MK_PREFIX]).then(function (r) { say("scMkStatus", "✔ Xóa " + r.removed + " marker NX", "ok"); })
      .catch(function (e) { say("scMkStatus", "✖ " + e.message, "bad"); });
  }

  // ============================================================ init
  function refresh() {
    if (T().work && !exists(T().work)) { T().work = null; T().root = null; }
    renderSource(); fillBrief(); renderLegend(); renderResult(); syncMode();
  }
  function init() {
    if (!$("scriptTab")) return;
    Array.prototype.forEach.call(document.querySelectorAll("#scMode button"), function (b) { b.onclick = function () { setMode(b.dataset.m); }; });
    var gs = $("scGroup");
    Object.keys(GROUPS).forEach(function (k) { var o = document.createElement("option"); o.value = k; o.textContent = GROUPS[k]; gs.appendChild(o); });
    var sc = scan();
    gs.value = T().group || (sc && Object.keys(sc.folders || {}).length > 1 ? "folder" : "role");
    gs.onchange = function () { T().group = gs.value; NX.save(); renderLegend(); };
    $("scAutoLabel").checked = !!T().autoLabel;
    $("scAutoLabel").onchange = function () { T().autoLabel = $("scAutoLabel").checked; NX.save(); };
    $("scImport").checked = T().import !== false;
    $("scImport").onchange = function () { T().import = $("scImport").checked; NX.save(); };
    ["scDuration", "scType", "scVo", "scCta", "scNotes", "scVision"].forEach(function (id) { $(id).onchange = saveBrief; });
    $("bScWrite").onclick = NX.guard(function () { return write(false); }, "Viết kịch bản");
    $("bScRewrite").onclick = NX.guard(function () { return write(true); }, "Viết lại kịch bản");
    $("bScVision").onclick = NX.guard(vision, "Kiểm tra hình");
    $("bScOpen").onclick = function () { openFile(T().root || T().work); };
    $("bScTable").onclick = function () { openFile(T().root ? T().root.replace(/\.md$/i, "_BANG.md") : null); };
    $("bScSheets").onclick = function () { var r = report(); openFile(r && r.vision ? r.vision.sheets_dir : (T().work || "").replace(/\.md$/i, "_sheets")); };
    $("bScManOpen").onclick = manOpen;
    $("bScManNew").onclick = manNew;
    $("bScManCheck").onclick = function () { manCheck(); };
    $("bScManSave").onclick = function () { manSave(); };
    $("bScManUse").onclick = NX.guard(manUse, "Align với kịch bản");
    $("scManText").addEventListener("input", function () {
      if (!man.dirty) { man.dirty = true; manMeta(($("scManMeta").textContent || "").replace(/ · đã lưu.*$/, "") + " · chưa lưu"); }
    });
    $("bScAlign").onclick = function () {
      var p = T().root || T().work; if (!p) return;
      S().scriptMode = "file";
      NX.setField("script", p.replace(/\\/g, "/"));
      var b = document.querySelector('nav button[data-tab="run"]'); if (b) b.click();
      NX.log("• Kịch bản đã gắn vào bước Align: " + path.basename(p));
    };
    $("bScLabel").onclick = function () { applyLabels(false).catch(function () {}); };
    $("bScMarkers").onclick = addMarkers;
    $("bScMkClear").onclick = clearMarkers;
    $("scBeatPreset").onchange = function () { setPreset($("scBeatPreset").value); setTimeout(syncMode, 0); };
    ["scBeatLen", "scBeatPace", "scBeatSpeech", "scBeatEnergy"].forEach(function (id) { $(id).onchange = saveBeat; });
    $("bScBeatPick").onclick = pickMusic;
    $("bScBeatClear").onclick = function () {
      S().montageMusic = ""; NX.save(); $("scBeatMusic").value = "";
      try { document.dispatchEvent(new CustomEvent("jes:preset", { detail: S().preset })); } catch (e) {}
      musicInfo();
    };
    $("bScBeatRun").onclick = NX.guard(runBeat, "Cắt theo nhạc");
    $("bScBeatShuffle").onclick = NX.guard(shuffleBeat, "Shuffle cắt theo nhạc");
    $("scBeatShufMode").value = T().shufMode || "pics"; $("scBeatShufAmt").value = T().shufAmt || "0.65";
    $("scBeatShufMode").onchange = function () { T().shufMode = $("scBeatShufMode").value; NX.save(); };
    $("scBeatShufAmt").onchange = function () { T().shufAmt = $("scBeatShufAmt").value; NX.save(); };
    $("scBeatVer").onchange = function () { if ($("scBeatVer").value !== "") useVer($("scBeatVer").value); };
    $("bScBeatBuild").onclick = NX.guard(function () { return NX.build(); }, "Build sequence");
    $("bScBeatReport").onclick = function () { var pp = NX.getPlanPath && NX.getPlanPath(); openFile(pp ? pp.replace(/\.json$/i, "_align.md") : null); };
    document.addEventListener("nxtab", function (e) { if (e.detail === "scriptTab") refresh(); });
    document.addEventListener("jes:preset", function () { setTimeout(syncMode, 0); });
    if ($("src")) $("src").addEventListener("jes:set", function () {
      T().work = null; T().root = null; NX.save();
      if (!man.dirty) man.loadedFor = null;         // another folder: the editor reloads that folder's script (or starts empty)
      refresh();
    });
    document.addEventListener("jes:analyzed", function () {
      refresh();
      if (T().autoLabel) { NX.log("• Tự gán màu clip theo " + GROUPS[$("scGroup").value].toLowerCase()); applyLabels(true); }
    });
    refresh();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
  window.JESScript = { refresh: refresh, write: write, vision: vision, applyLabels: applyLabels, addMarkers: addMarkers, setMode: setMode,
                       showCurrent: showCurrent };
})();
