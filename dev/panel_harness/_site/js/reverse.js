/* Justin EDIT Suite — tab "Phân tích video" (reverse prompt).
   1. Engine nxcore.reverse đo video tham khảo (0 token): cắt shot, chuyển động máy, màu, nhịp cắt, BPM/beat, thoại, proxy, contact sheet.
   2. AI xem video: Gemini (cả video + tiếng qua Files API) → hết quota / lỗi thì tự chuyển Ollama local (contact sheet + số đo).
   3. AI viết ngôn ngữ dựng + shot list (cỡ cảnh, máy, chữ trên màn hình, SFX, hiệu ứng từng shot).
   Prompt Seedance 2.5 là bước RIÊNG (card Prompt): format khối cố định của editor, mỗi prompt một đoạn ≤ N giây.
   Model: chọn riêng cho 'phân tích' và 'viết prompt' — Tự động (Gemini → local) / một model Gemini / một model Ollama.
   4. Xuất <outRoot>/REVERSE/<tên>/ReversePrompt_<tên>.md + ref/ref_segNN.jpg (khung đầu từng đoạn làm ảnh tham chiếu). */
(function () {
  "use strict";
  var fs = require("fs"), path = require("path"), cp = require("child_process"), toUrl = require("url").pathToFileURL;
  var NX = window.NX, LLM = window.NXLLM, $ = function (id) { return document.getElementById(id); };
  if (!$("revTab") || !NX) return;

  function S() { var s = NX.S; s.rev = s.rev || {}; return s.rev; }
  function esc(x) { return NX.esc(String(x == null ? "" : x)); }
  function readJSON(p, d) { try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) { return d; } }
  function tc(s) { s = Math.max(0, +s || 0); var m = Math.floor(s / 60), r = s - m * 60; return m + ":" + (r < 10 ? "0" : "") + r.toFixed(1); }
  function tcs(s) { s = Math.round(Math.max(0, +s || 0)); return Math.floor(s / 60) + ":" + (s % 60 < 10 ? "0" : "") + (s % 60); }
  function slug(s) {
    return String(s).normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/đ/g, "d").replace(/Đ/g, "D")
      .replace(/[^\w\-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 60) || "video";
  }
  function stemOf(v) { return path.basename(v, path.extname(v)); }
  function outFor(v) { return ((NX.S.outRoot || path.dirname(v)) + "/REVERSE/" + slug(stemOf(v))).replace(/\\/g, "/"); }
  function fileUrl(p) { try { var st = fs.statSync(p); return toUrl(p).href + "?v=" + Math.round(st.mtimeMs); } catch (e) { return ""; } }

  /* Format prompt Seedance 2.5 của editor — thứ tự + tiêu đề khối cố định. Sửa ở đây nếu format đổi. */
  var SEEDANCE_BLOCKS = [
    ["Reference Lock:", "khoá TỪNG ảnh tham chiếu với TỪNG nhân vật / bối cảnh bằng đặc điểm nhận diện cụ thể (@ref1 = khung đầu đoạn đính kèm; nhân vật lặp lại giữ đúng mô tả ở mọi đoạn)"],
    ["Technical Spec:", "duration, aspect ratio, resolution, fps, style, negative style — nêu ngay đầu"],
    ["[Tiền đề]", "safety frame / ý đồ cảnh"],
    ["[Tương tác cốt lõi]", "quy tắc quan hệ giữa nhân vật / vật thể + số lần lặp beat"],
    ["[Nguyên tắc Hành động]", "whitelist / blacklist hành vi từng nhân vật"],
    ["[Quy tắc Quay phim]", "cỡ cảnh, chuyển động máy, quy tắc không cắt ở beat chính, thời lượng từng beat (giây)"],
    ["[Biểu cảm & Tính cách]", "thang leo thang biểu cảm qua từng lần lặp"],
    ["[Quy tắc thoại]", "whitelist thoại chính xác (chỉ từ transcript của đoạn), khoá ngôn ngữ gốc, cấm thoại tự phát; không có thoại → ghi rõ 'không thoại, không lip-sync'"],
    ["[Dòng thời gian]", "khối theo giây (0–2s, 2–5s…) phủ kín thời lượng, mỗi beat chính một khối; mỗi điểm cắt trong đoạn là một khối mới ghi 'CẮT →'"],
    ["[Ghi chú diễn xuất]", ""],
    ["[Bối cảnh & Vật lý]", "vật liệu, ánh sáng, vật lý (nước, vải, tóc, khói, hạt…)"],
    ["[Âm thanh]", "SFX diegetic ưu tiên trước nhạc nền; nhạc ghi BPM / mood nếu có"],
    ["[Nghiêm cấm]", "danh sách âm chi tiết: mesh error, face distortion, identity swap, extra fingers/limbs, morphing, flicker, text/watermark/logo, phụ đề tự sinh + các lỗi tông"]
  ];

  // ───────────────────────────────────────────── UI state
  var R = null, A = null, busy = false;          // R = reverse.json (số đo) · A = reverse_ai.json (AI)
  var STEPS = [["measure", "Đo video — cắt shot, chuyển động máy, màu, nhịp, beat, thoại, SFX"], ["see", "AI xem video"],
               ["lang", "Ngôn ngữ dựng + shot list (chữ · SFX · hiệu ứng)"], ["md", "Xuất ReversePrompt_*.md"]];
  var PSTEPS = [["prompt", "Viết prompt Seedance 2.5"], ["pmd", "Ảnh ref + cập nhật .md"]];
  var where = "rv";                              // "rv" = card Phân tích · "pr" = card Prompt (bước nào đang chạy, lỗi hiện ở đâu)
  function steps(reset, w) {
    where = w || "rv";
    var ol = $(where === "pr" ? "rvPrSteps" : "rvSteps"); if (!ol) return;
    if (reset) ol.innerHTML = (where === "pr" ? PSTEPS : STEPS).map(function (s) { return '<li data-k="' + s[0] + '">' + esc(s[1]) + ' <span class="muted"></span></li>'; }).join("");
  }
  function step(k, st, note) {
    var li = document.querySelector('#rvSteps li[data-k="' + k + '"], #rvPrSteps li[data-k="' + k + '"]'); if (!li) return;
    li.className = st || ""; if (note != null) li.querySelector("span").textContent = note;
  }
  function err(msg, w) {
    ["rvErr", "rvPrErr"].forEach(function (id) { var e = $(id); if (e) { e.style.display = "none"; e.innerHTML = ""; } });
    var e = $((w || where) === "pr" ? "rvPrErr" : "rvErr"); if (!e || !msg) return; e.style.display = ""; e.innerHTML = msg;
  }

  function bindOpts() {
    var o = S();
    $("rvFile").value = o.file || "";
    [["rvThresh", "thresh", "0.32"], ["rvSeg", "seg", "10"], ["rvMode", "mode", "recreate"]].forEach(function (x) {
      var el = $(x[0]); el.value = o[x[1]] || x[2];
      el.onchange = function () { S()[x[1]] = el.value; NX.save(); };
    });
    $("rvTx").checked = o.tx !== false; $("rvTx").onchange = function () { S().tx = $("rvTx").checked; NX.save(); };
    $("rvNote").value = o.note || ""; $("rvNote").onchange = function () { S().note = $("rvNote").value; NX.save(); };
    $("rvFile").onchange = function () { S().file = $("rvFile").value.trim().replace(/\\/g, "/"); NX.save(); load(); };
  }
  function pickFile() {
    var r = window.cep.fs.showOpenDialogEx(false, false, "Chọn video tham khảo", S().file ? path.dirname(S().file) : "", ["mp4", "mov", "m4v", "mkv", "webm", "avi", "mxf"]);
    if (r && r.data && r.data.length) { S().file = r.data[0].replace(/\\/g, "/"); $("rvFile").value = S().file; NX.save(); load(); }
  }
  function cur() { var f = S().file; return f && fs.existsSync(f) ? f : ""; }

  function load() {
    var v = cur(); R = A = null;
    if (v) {
      var od = outFor(v); R = readJSON(od + "/reverse.json", null); A = readJSON(od + "/reverse_ai.json", null);
      if (R && R.video && path.resolve(R.video) !== path.resolve(v)) { R = A = null; }
      if (A && R && !(A.shots || []).length) A = null;
    }
    render();
  }
  function info() {
    var el = $("rvInfo"), v = cur();
    if (!S().file) { el.innerHTML = "Chọn hoặc kéo-thả một video tham khảo (TVC, MV, clip AI, trailer…) — tool cắt shot, đo nhịp + màu + chuyển động máy, AI viết lại ngôn ngữ dựng và prompt Seedance."; return; }
    if (!v) { el.innerHTML = '<span class="bad">Không thấy file: ' + esc(S().file) + "</span>"; return; }
    if (!R) { el.textContent = "Chưa phân tích — bấm ▶ Phân tích video."; return; }
    var i = R.info, e = R.edit, au = R.audio || {};
    el.innerHTML = chips([[i.w + "×" + i.h, i.ratio], [i.fps + " fps", ""], [tcs(i.dur), "thời lượng"], [e.shots + " shot", ""], ["ASL " + e.asl + "s", e.pacing],
      [e.cuts_per_min + " cắt/phút", ""], au.bpm ? [au.bpm + " BPM", (au.cut_on_beat != null ? Math.round(au.cut_on_beat * 100) + "% cắt trúng beat" : "")] : null,
      [(au.speech && au.speech.length ? au.speech.length + " câu thoại" : i.has_audio ? "không thoại" : "không tiếng"), ""]]);
  }
  function chips(list) {
    return '<div class="rv-chips">' + list.filter(Boolean).map(function (c) { return '<span class="rv-chip" title="' + esc(c[1]) + '"><b>' + esc(c[0]) + "</b>" + (c[1] ? " " + esc(c[1]) : "") + "</span>"; }).join("") + "</div>";
  }

  // ───────────────────────────────────────────── 1. engine
  function measure(force) {
    var v = cur(); if (!v) return Promise.reject(new Error("chưa chọn video tham khảo"));
    var od = outFor(v), args = ["-m", "nxcore.reverse", v, "--out", od, "--thresh", S().thresh || "0.32"];
    if (S().tx === false) args.push("--no-tx");
    if (NX.S.cuda) args.push("--cuda");
    if (force) args.push("--force");
    step("measure", "run");
    return NX.run(args, "Đo video tham khảo").then(function () {
      R = readJSON(od + "/reverse.json", null);
      if (!R || !R.shots) throw new Error("engine không tạo được reverse.json — xem log");
      step("measure", "ok", R.shots.length + " shot · ASL " + R.edit.asl + "s"); info();
      return R;
    });
  }

  // ───────────────────────────────────────────── số đo → văn bản gọn cho AI
  function speechIn(t0, t1) {
    return ((R.audio || {}).speech || []).filter(function (s) { return s[1] > t0 + 0.05 && s[0] < t1 - 0.05; });
  }
  function metricsText(shotsOnly) {
    var i = R.info, e = R.edit, au = R.audio || {}, L = R.look || {}, out = [];
    out.push("VIDEO: " + R.name + " · " + i.w + "x" + i.h + " (" + i.ratio + ") · " + i.fps + " fps · " + i.dur + "s · " + (i.has_audio ? "có tiếng" : "không tiếng"));
    out.push("NHỊP: " + e.shots + " shot · ASL " + e.asl + "s (median " + e.median + ", " + e.min + "–" + e.max + "s) · " + e.cuts_per_min + " cắt/phút · " + e.pacing +
      " · mật độ cắt theo 10 đoạn (cắt/phút): " + e.cpm_curve_10.join(" "));
    if (au.bpm) out.push("NHẠC: ~" + au.bpm + " BPM · " + (au.mood || "") + (au.cut_on_beat != null ? " · " + Math.round(au.cut_on_beat * 100) + "% điểm cắt trúng beat (±90ms)" : "") +
      ((au.sections || []).length ? " · đoạn nhạc: " + au.sections.map(function (s) { return (s.label || "?") + " " + s.t0.toFixed(1) + "–" + s.t1.toFixed(1); }).join(", ") : ""));
    if (L.L != null) out.push("LOOK TRUNG BÌNH: sáng L" + L.L + "/100 · tương phản " + L.contrast + " · bão hoà " + L.sat + " · ấm/lạnh b*=" + L.warm + " · tint a*=" + L.tint);
    var sp = au.speech || [];
    out.push(sp.length ? "THOẠI (Whisper, nguyên văn):\n" + sp.map(function (s) { return "  [" + s[0].toFixed(1) + "–" + s[1].toFixed(1) + "] " + s[2]; }).join("\n") : "THOẠI: không có");
    var sx = (au.sfx_events || []).filter(function (e) { return !shotsOnly || shotsOnly.some(function (s) { return e.t >= s.t0 - 0.3 && e.t < s.t1; }); });
    if (au.sfx_events) out.push(sx.length ? "SFX NGHE ĐƯỢC (engine đo năng lượng ngoài thoại — ứng viên, AI đặt tên + bỏ cái là nhạc/tiếng cảnh): " +
      sx.slice(0, 120).map(function (e) { return e.t.toFixed(2) + "s " + e.kind + " " + e.dur.toFixed(2) + "s"; }).join(" · ") : "SFX NGHE ĐƯỢC: không thấy");
    out.push("SHOTS — số đo engine; 'máy' là GỢI Ý từ optical flow (nước, khói, đám đông, CGI có thể làm sai — tin video/hình hơn):");
    (shotsOnly || R.shots).forEach(function (s) {
      var c = s.color || {};
      out.push("#" + s.i + " " + s.t0.toFixed(2) + "–" + s.t1.toFixed(2) + " (" + s.dur.toFixed(2) + "s) | máy: " + (s.cam || {}).label + " | " + (s.cam || {}).subject_motion +
        " | màu: L" + c.L + " C" + c.contrast + " sat " + c.sat + " " + c.key + " " + c.tone + " | palette " + (c.palette || []).slice(0, 3).map(function (p) { return p[0]; }).join(" "));
    });
    return out.join("\n");
  }

  // ───────────────────────────────────────────── 2. AI xem video
  var ANALYZE_SCHEMA = [
    '{"overview":{"genre":"thể loại","subject":"nội dung chính","story":"mạch nội dung 2–3 câu","mood":"…","visual_style":"live-action / 3D CGI / anime / stop-motion… + look tổng thể","ai_generated":"có dấu hiệu video AI không, dấu hiệu nào"},',
    '"characters":[{"id":"C1","name":"tên gọi ngắn","look":"nhận diện chi tiết đủ để khoá reference: tuổi, giới, dáng, tóc, mặt, trang phục + màu + chất liệu, phụ kiện","shots":[1,2]}],',
    '"settings":[{"id":"L1","desc":"bối cảnh, thời điểm, thời tiết, vật liệu, đạo cụ chính","shots":[1]}],',
    '"editing":{"structure":[{"t0":0,"t1":0,"part":"hook|setup|build|climax|payoff|outro","desc":"…"}],"rhythm":"nhịp cắt, cách giãn/dồn","camera":"ngôn ngữ máy chủ đạo","lens":"tiêu cự, DOF, bokeh","lighting":"sơ đồ sáng, hướng, chất lượng, practical","color":"grade/LUT, tương phản, bảng màu, skin tone","transitions":"kiểu chuyển cảnh, match cut, J/L-cut","sound":"nhạc/SFX/thoại, mix, cách cắt theo nhạc","text_graphics":"chữ, title, overlay (không có thì \'\')","techniques":["kỹ thuật đáng học — cụ thể, áp dụng được khi quay/dựng"]},',
    '"shots":[{"i":1,"size":"ECU|CU|MCU|MS|MLS|LS|ELS","angle":"eye-level|high|low|top-down|dutch|POV|OTS","lens":"wide|normal|tele|macro + mm ước lượng","camera":"chuyển động máy thực tế (sửa số đo engine nếu sai)","speed":"real-time|slow-mo|speed ramp|timelapse|reverse","subject":"ai/cái gì (C1, L1…)","action":"hành động quan sát được theo thứ tự","light":"hướng + chất lượng sáng","transition_in":"cut|match cut|J-cut|L-cut|dissolve|whip|flash|fade","sound":"âm thanh trong shot","talking":"true nếu người trong khung đang nói trước máy (thấy khẩu hình) — false nếu là hình minh hoạ / màn hình / đồ hoạ dù có giọng đọc","text":[{"content":"chữ ĐỒ HOẠ thêm khi dựng, nguyên văn","kind":"title|caption|label|lower-third|callout|number","pos":"top|center|lower|left|right","t0":0,"t1":0}],"sfx":[{"t":0,"kind":"whoosh|riser|hit|pop|click|ding|typing|glitch|meme","desc":"mô tả ngắn"}],"fx":["hiệu ứng dựng trên shot: punch-in|zoom in|zoom out|speed ramp|shake|flash|blur|glitch|freeze frame|split screen|mask reveal|picture-in-picture|color pop…"]}]}'
  ].join("\n");
  var ANALYZE_RULES = [
    "Bạn là đạo diễn hình ảnh + editor phân tích một VIDEO THAM KHẢO để reverse thành ngôn ngữ dựng và prompt tạo video AI (Seedance 2.5).",
    "Mô tả HÀNH VI QUAN SÁT ĐƯỢC, không dùng cảm xúc trừu tượng. Tiếng Việt, giữ nguyên thuật ngữ kỹ thuật tiếng Anh (shot size, dolly, rim light, LUT…).",
    "Không bịa thoại: chỉ dùng thoại trong transcript hoặc nghe rõ. Thời gian là SỐ GIÂY tính từ đầu video.",
    "Danh sách shot đã cắt sẵn bằng engine (#i + giờ) — giữ đúng số i, mỗi shot đúng 1 phần tử trong 'shots'.",
    "Mỗi shot: 'text' chỉ ghi chữ đồ hoạ editor thêm vào (title, caption, số liệu, callout) — KHÔNG ghi phụ đề thoại, KHÔNG ghi chữ có sẵn trong cảnh (biển hiệu, bao bì); không có thì [].",
    "'sfx' = hiệu ứng âm thanh nghe được trong shot (không tính nhạc nền, thoại); t = SỐ GIÂY tính từ đầu video, ưu tiên khớp thời điểm trong 'SFX NGHE ĐƯỢC' của engine. 'fx' = hiệu ứng dựng thấy được; không có thì []."
  ].join("\n");

  function geminiKey() { var k = NX.S.keys || {}; return k.gemini || (NX.S.provider === "gemini" ? NX.S.apiKey : "") || ""; }
  function geminiModel() { var r = (NX.S.route || {}).vision || {}; return r.provider === "gemini" && r.model ? r.model : ((NX.S.models || {}).gemini || undefined); }
  function ollamaVision() { var r = (NX.S.route || {}).vision || {}; return r.provider === "ollama" && r.model ? r.model : "qwen3-vl:8b-instruct"; }
  function ollamaText() { var r = (NX.S.route || {}).script || {}; return r.provider === "ollama" && r.model ? r.model : "gemma4:12b-it-qat"; }
  /* ── chọn model: "auto" | "gemini:<model>" | "ollama:<model>" — riêng cho phân tích (mSee) và viết prompt (mWrite) */
  var OLL = null;                                // model Ollama đã cài: null = chưa hỏi · [] = Ollama chưa chạy / chưa có model
  function isVision(id) { return /vl\b|vl:|-vl|vision|llava|minicpm-v|moondream|gemma3|gemma4|llama4|qwen2\.5vl|bakllava/i.test(id); }
  function migrateModels() {
    var o = S();
    if (o.mSee == null) o.mSee = o.engine === "gemini" ? "gemini:" + (geminiModel() || LLM.GEMINI_CHAIN[0]) : o.engine === "local" ? "ollama:" + ollamaVision() : "auto";
    if (o.mWrite == null) o.mWrite = o.engine === "gemini" ? "gemini:" + (geminiModel() || LLM.GEMINI_CHAIN[0]) : o.engine === "local" ? "ollama:" + ollamaText() : "auto";
  }
  function choice(k) { var m = /^(gemini|ollama):(.+)$/.exec(S()[k] || "auto"); return m ? { prov: m[1], model: m[2] } : { prov: "auto" }; }
  function choiceLabel(k) {
    var c = choice(k);
    return c.prov === "auto" ? "Tự động: Gemini " + (geminiModel() || LLM.GEMINI_CHAIN[0]) + " → local " + (k === "mSee" ? ollamaVision() : ollamaText()) : (c.prov === "gemini" ? "Gemini " : "Ollama ") + c.model;
  }
  function fillModels() {
    var hasKey = !!geminiKey(), gm = (LLM.GEMINI_CHAIN || []).slice(), def = geminiModel();
    if (def && gm.indexOf(def) < 0) gm.unshift(def);
    [["rvMSee", "mSee", true], ["rvMWrite", "mWrite", false]].forEach(function (x) {
      var sel = $(x[0]); if (!sel) return;
      var vis = x[2], cur_ = S()[x[1]] || "auto", h = [];
      h.push('<option value="auto">Tự động — Gemini' + (hasKey ? "" : " (chưa có key → bỏ qua)") + " → local " + esc(vis ? ollamaVision() : ollamaText()) + "</option>");
      h.push('<optgroup label="Gemini ' + (hasKey ? "· " + (vis ? "xem + nghe cả video" : "cloud") : "· chưa có API key") + '">' + gm.map(function (m) {
        return '<option value="gemini:' + esc(m) + '"' + (hasKey ? "" : " disabled") + ">" + esc(m) + (m === def ? " · mặc định" : "") + "</option>";
      }).join("") + "</optgroup>");
      var ol = (OLL || []).slice().sort(function (a, b) { return vis ? (isVision(b.id) - isVision(a.id)) : 0; });
      var known = ol.map(function (m) { return m.id; });
      var curO = /^ollama:(.+)$/.exec(cur_);
      if (curO && known.indexOf(curO[1]) < 0) ol.push({ id: curO[1], note: OLL ? "không thấy trong Ollama" : "" });
      h.push('<optgroup label="Ollama local ' + (OLL === null ? "· đang kiểm tra…" : !OLL.length ? "· chưa chạy / chưa có model" : vis ? "· xem contact sheet + transcript (không nghe)" : "") + '">' + ol.map(function (m) {
        var nv = vis && !isVision(m.id);
        return '<option value="ollama:' + esc(m.id) + '">' + esc(m.id) + (m.note ? " · " + esc(m.note) : "") + (nv ? " · ⚠ không xem được hình" : "") + "</option>";
      }).join("") + "</optgroup>");
      sel.innerHTML = h.join("");
      sel.value = cur_; if (sel.value !== cur_) { sel.value = "auto"; }
    });
    modelStat();
  }
  function modelStat() {
    var el = $("rvMStat"); if (!el) return;
    var nv = (OLL || []).filter(function (m) { return isVision(m.id); }).length, w = [];
    var c = choice("mSee");
    if (c.prov === "ollama" && !isVision(c.model)) w.push('<span class="bad">model phân tích không xem được hình — chọn model VL</span>');
    if ((c.prov === "gemini" || choice("mWrite").prov === "gemini") && !geminiKey()) w.push('<span class="bad">chưa có API key Gemini (tab Cài đặt)</span>');
    el.innerHTML = "Gemini: " + (geminiKey() ? "key ✓" : '<span class="bad">chưa có key</span>') + " · Ollama: " +
      (OLL === null ? "đang kiểm tra…" : OLL.length ? OLL.length + " model (" + nv + " xem hình)" : '<span class="bad">chưa chạy / chưa có model</span>') + (w.length ? "<br>" + w.join("<br>") : "");
  }
  function refreshModels() {
    OLL = null; fillModels();
    return LLM.listModels("ollama").then(function (ms) {
      var seen = {}; OLL = (ms || []).filter(function (m) { if (!m || !m.id || seen[m.id] || /^llamacpp:[0-9a-f]{20,}/.test(m.id)) return false; seen[m.id] = 1; return true; });
    }).catch(function () { OLL = []; }).then(fillModels);
  }
  function mk(provider, model) {
    return LLM.create(provider, provider === "gemini" ? geminiKey() : "", model, null,
      { numCtx: Math.max(+NX.S.numCtx || 16384, 16384), onCtx: function (f, t) { NX.log("• context AI local " + f + " → " + t, "#8b9099"); } },
      function (from, to, why) { NX.log("• " + from + " " + (why || "hết quota") + " → " + to, "#e0a82e"); });
  }
  function b64(p) { return { mime: "image/jpeg", b64: fs.readFileSync(p).toString("base64") }; }

  function seeGemini(model) {
    var key = geminiKey(), od = outFor(cur()), cache = readJSON(od + "/gemini_file.json", {}), up;
    if (!R.proxy || !fs.existsSync(R.proxy)) return Promise.reject(new Error("thiếu proxy.mp4 — bấm 'Đo lại'"));
    var fresh = cache.key === R.key && cache.expires && Date.parse(cache.expires) - Date.now() > 2 * 3600e3;
    var chk = fresh ? LLM.geminiFile(key, cache.name).then(function (f) { return f && f.state === "ACTIVE" ? f : null; }).catch(function () { return null; }) : Promise.resolve(null);
    step("see", "run", "Gemini · upload proxy…");
    return chk.then(function (f) {
      if (f) return { uri: cache.uri };
      return LLM.geminiUpload(key, R.proxy, "video/mp4", R.name).then(function (file) { up = file; return LLM.geminiWaitActive(key, file); }).then(function (file) {
        var c = { key: R.key, name: up.name || file.name, uri: up.uri || file.uri, expires: file.expirationTime || up.expirationTime || new Date(Date.now() + 47 * 3600e3).toISOString() };
        fs.writeFileSync(od + "/gemini_file.json", JSON.stringify(c, null, 1), "utf8");
        return { uri: c.uri };
      });
    }).then(function (f) {
      var fpsV = R.info.dur <= 40 ? 4 : R.info.dur <= 120 ? 2 : 1;
      var vid = [{ uri: f.uri, mime: "video/mp4", label: "VIDEO THAM KHẢO:", fps: fpsV }];
      // video dài nhiều shot: lượt 1 = tổng quan + ngôn ngữ dựng + shot đầu, các lượt sau chỉ trả 'shots' của từng khúc → không bị cụt JSON
      var PER = 50, groups = [];
      for (var k = 0; k < R.shots.length; k += PER) groups.push(R.shots.slice(k, k + PER));
      var shots = [], j0 = null;
      return groups.reduce(function (pr, grp, g) {
        return pr.then(function () {
          var llm = mk("gemini", model || geminiModel());
          var rng = "#" + grp[0].i + "–#" + grp[grp.length - 1].i;
          step("see", "run", "Gemini " + llm.model + " đang xem video" + (groups.length > 1 ? " · shot " + rng + " (" + (g + 1) + "/" + groups.length + ")" : "") + "…");
          var ask = g === 0
            ? "SỐ ĐO ENGINE:\n" + metricsText() + "\n\nXem video đính kèm (có tiếng) và phân tích theo schema." +
              (groups.length > 1 ? " Lượt này 'shots' CHỈ gồm shot " + rng + " (các shot sau sẽ hỏi ở lượt khác)." : "") + " TRẢ VỀ DUY NHẤT JSON:\n" + ANALYZE_SCHEMA
            : "SỐ ĐO ENGINE CỦA CÁC SHOT " + rng + ":\n" + metricsText(grp) + "\n\nNhân vật / bối cảnh đã xác định: " + JSON.stringify({ characters: j0.characters || [], settings: j0.settings || [] }) +
              "\n\nXem lại video đính kèm, CHỈ phân tích các shot " + rng + ". TRẢ VỀ DUY NHẤT JSON {\"shots\":[…]} theo đúng schema phần tử shot:\n" + ANALYZE_SCHEMA;
          llm.userTurn(ask, null, vid);
          if (g > 0 && llm.history && llm.history.length) {        // lượt sau: chỉ gửi khúc video của các shot đó (ít token, xem kỹ hơn 2 fps)
            llm.history[llm.history.length - 1].parts.forEach(function (p) {
              if (p.video_metadata) p.video_metadata = { start_offset: Math.max(0, grp[0].t0 - 0.5).toFixed(1) + "s", end_offset: (grp[grp.length - 1].t1 + 0.5).toFixed(1) + "s", fps: 2 };
            });
          }
          return llm.send(ANALYZE_RULES, null, { json: true, temperature: 0.2, maxTokens: 30000 }).then(function (r) {
            var j = LLM.parseJSON(r.text);
            if (g === 0) { j0 = j; j0._model = "gemini · " + llm.model; }
            shots = shots.concat((j.shots || []).filter(function (s) { return s && +s.i >= grp[0].i && +s.i <= grp[grp.length - 1].i; }));
          });
        });
      }, Promise.resolve()).then(function () {
        j0.shots = shots; j0._via = "video + tiếng" + (groups.length > 1 ? " · " + groups.length + " lượt" : ""); return j0;
      });
    });
  }
  function seeLocal(model) {
    model = model || ollamaVision();
    var sheets = (R.sheets || []).filter(function (p) { return fs.existsSync(p); }), shots = [], chars = [], sets = [];
    if (!sheets.length) return Promise.reject(new Error("thiếu contact sheet — bấm 'Đo lại'"));
    var per = 16;
    return sheets.reduce(function (pr, sh, k) {
      return pr.then(function () {
        step("see", "run", "Ollama " + model + " · contact sheet " + (k + 1) + "/" + sheets.length);
        var grp = R.shots.slice(k * per, (k + 1) * per), llm = mk("ollama", model);
        llm.userTurn("Ảnh là CONTACT SHEET: mỗi ô là khung giữa của một shot, góc trên ghi '#số  giờ'. Không có âm thanh — dùng transcript.\n" +
          "SỐ ĐO ENGINE CỦA CÁC SHOT NÀY:\n" + metricsText(grp) + "\n\nTRẢ VỀ DUY NHẤT JSON {\"shots\":[…],\"characters\":[…],\"settings\":[…]} theo schema:\n" + ANALYZE_SCHEMA, [b64(sh)]);
        return llm.send(ANALYZE_RULES, null, { json: true, temperature: 0.2, maxTokens: 6000 }).then(function (r) {
          var j = LLM.parseJSON(r.text);
          shots = shots.concat(j.shots || []); chars = chars.concat(j.characters || []); sets = sets.concat(j.settings || []);
        });
      });
    }, Promise.resolve()).then(function () {
      step("see", "run", "Ollama " + ollamaText() + " · tổng hợp ngôn ngữ dựng");
      var llm = mk("ollama", ollamaText());
      var rows = shots.map(function (s) { return "#" + s.i + " " + [s.size, s.angle, s.lens, s.camera, s.speed, s.subject, s.action, s.light, s.transition_in].filter(Boolean).join(" | "); }).join("\n");
      llm.userTurn("SỐ ĐO ENGINE:\n" + metricsText() + "\n\nMÔ TẢ TỪNG SHOT (AI đã xem hình):\n" + rows + "\n\nNHÂN VẬT ĐÃ THẤY: " + JSON.stringify(chars) + "\nBỐI CẢNH ĐÃ THẤY: " + JSON.stringify(sets) +
        "\n\nTổng hợp, gộp nhân vật/bối cảnh trùng (đánh lại id C1, C2… / L1…). TRẢ VỀ DUY NHẤT JSON {\"overview\":{…},\"characters\":[…],\"settings\":[…],\"editing\":{…}} theo schema:\n" + ANALYZE_SCHEMA);
      return llm.send(ANALYZE_RULES, null, { json: true, temperature: 0.2, maxTokens: 6000 }).then(function (r) {
        var j = LLM.parseJSON(r.text); j.shots = shots;
        if (!(j.characters || []).length) j.characters = chars;
        if (!(j.settings || []).length) j.settings = sets;
        j._model = "ollama · " + model + " + " + ollamaText(); j._via = "contact sheet + transcript";
        return j;
      });
    });
  }
  function see() {
    var c = choice("mSee"), hasKey = !!geminiKey();
    if (c.prov === "ollama") return seeLocal(c.model);
    if (c.prov === "gemini") {
      if (!hasKey) return Promise.reject(new Error("chưa có API key Gemini — nhập ở tab Cài đặt, hoặc chọn model local"));
      return seeGemini(c.model);
    }
    if (!hasKey) {
      NX.log("• Chưa có API key Gemini → phân tích bằng model local (không nghe được tiếng, dùng transcript)", "#e0a82e");
      return seeLocal(ollamaVision());
    }
    return seeGemini(geminiModel()).catch(function (e) {
      NX.log("• Gemini lỗi: " + String(e.message || e).slice(0, 160) + " → chuyển sang Ollama local", "#e0a82e");
      step("see", "run", "Gemini lỗi → chuyển local");
      return seeLocal(ollamaVision());
    });
  }
  function normalize(j) {
    j.overview = j.overview || {}; j.editing = j.editing || {}; j.characters = j.characters || []; j.settings = j.settings || [];
    var by = {}; (j.shots || []).forEach(function (s) { if (s && s.i != null) by[+s.i] = s; });
    j.shots = R.shots.map(function (s) { return Object.assign({}, by[s.i] || {}, { i: s.i }); });
    j.missing = R.shots.filter(function (s) { return !by[s.i]; }).map(function (s) { return s.i; });
    return j;
  }

  // ───────────────────────────────────────────── 3. prompt Seedance
  function segments(maxLen) {
    var segs = [], c = null;
    R.shots.forEach(function (s) {
      if (s.dur > maxLen + 0.5) {
        if (c) { segs.push(c); c = null; }
        var n = Math.ceil(s.dur / maxLen), L = s.dur / n;
        for (var k = 0; k < n; k++) segs.push({ t0: s.t0 + k * L, t1: s.t0 + (k + 1) * L, shots: [s.i], part: n > 1 ? (k + 1) + "/" + n : "" });
        return;
      }
      if (c && s.t1 - c.t0 > maxLen + 0.5) { segs.push(c); c = null; }
      if (!c) c = { t0: s.t0, t1: s.t1, shots: [] };
      c.t1 = s.t1; c.shots.push(s.i);
    });
    if (c) segs.push(c);
    for (var i = segs.length - 1; i > 0; i--) {                 // đoạn cuối < 3 s → gộp vào đoạn trước nếu còn chỗ
      var a = segs[i - 1], b = segs[i];
      if (b.t1 - b.t0 < 3 && !b.part && !a.part && b.t1 - a.t0 <= maxLen + 3) { a.t1 = b.t1; a.shots = a.shots.concat(b.shots); segs.splice(i, 1); }
    }
    segs.forEach(function (g, i) { g.i = i + 1; g.dur = +(g.t1 - g.t0).toFixed(2); });
    return segs;
  }
  function blocksText() {
    return SEEDANCE_BLOCKS.map(function (b) { return "  " + b[0] + (b[1] ? "   ← nội dung: " + b[1] : ""); }).join("\n");
  }
  // AI hay chép cả số thứ tự / dấu — của hướng dẫn vào prompt → gỡ, giữ đúng tiêu đề khối
  function cleanPrompt(t) {
    var heads = SEEDANCE_BLOCKS.map(function (b) { return b[0].replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }).join("|");
    var reNum = new RegExp("^\\s*(?:\\d{1,2}[.)]\\s*)?(" + heads + ")[ \\t]*(?:[—–-][ \\t]*|←[^\\n]*?:[ \\t]*)?", "gm");
    return String(t || "").replace(/\r/g, "").replace(reNum, "$1 ").replace(/ +$/gm, "").replace(/\n{3,}/g, "\n\n").trim();
  }
  function promptSystem() {
    return [
      "Bạn viết prompt Seedance 2.5 theo FORMAT KHỐI CỐ ĐỊNH của editor Nexys Media. Văn xuôi tiếng Việt liền mạch trong từng khối, thuật ngữ kỹ thuật giữ tiếng Anh.",
      "Mỗi prompt = MỘT đoạn video đã chia sẵn; Technical Spec dùng đúng 'spec' của đoạn (duration, aspect ratio, resolution, fps). Thứ tự khối cố định, mỗi khối bắt đầu một dòng mới bằng ĐÚNG tiêu đề (KHÔNG đánh số, KHÔNG thêm dấu gạch, KHÔNG chép phần '← nội dung'):",
      blocksText(),
      "NGUYÊN TẮC: mô tả hành vi quan sát được thay vì cảm xúc trừu tượng; KHÔNG slow-motion trừ khi shot gốc là slow-mo; giữ nhất quán nhân vật, trang phục, ánh sáng, màu giữa các đoạn liên tiếp;",
      "[Dòng thời gian] phủ kín duration trong 'spec', khớp điểm cắt thật của bản gốc ('giờ_trong_đoạn', tính lại từ 0); [Quy tắc thoại] chỉ dùng câu trong 'thoại' của đoạn, giữ nguyên văn và ngôn ngữ gốc.",
      "TRẢ VỀ DUY NHẤT JSON: {\"prompts\":[{\"seg\":1,\"title\":\"tên ngắn của đoạn\",\"prompt\":\"toàn bộ các khối, xuống dòng giữa các khối\"}]}"
    ].join("\n");
  }
  function segBrief(g, an) {
    var shots = g.shots.map(function (i) {
      var m = R.shots[i - 1], a = an.shots[i - 1] || {}, c = m.color || {};
      var a0 = Math.max(m.t0, g.t0) - g.t0, a1 = Math.min(m.t1, g.t1) - g.t0;
      return { shot: i, "giờ_trong_đoạn": a0.toFixed(1) + "–" + a1.toFixed(1) + "s", size: a.size, angle: a.angle, lens: a.lens, camera: a.camera || (m.cam || {}).label,
        speed: a.speed, subject: a.subject, action: a.action, light: a.light, transition_in: a.transition_in, sound: a.sound,
        color: c.key + ", " + c.tone + ", palette " + (c.palette || []).slice(0, 3).map(function (p) { return p[0]; }).join(" ") };
    });
    var ids = {}; g.shots.forEach(function (i) { ids[i] = 1; });
    var who = an.characters.filter(function (c) { return (c.shots || []).some(function (i) { return ids[i]; }); });
    var where = an.settings.filter(function (c) { return (c.shots || []).some(function (i) { return ids[i]; }); });
    var au = R.audio || {};
    var sec = (au.sections || []).filter(function (s) { return s.t1 > g.t0 && s.t0 < g.t1; }).map(function (s) { return s.label; });
    return { seg: g.i, "gốc": tc(g.t0) + "–" + tc(g.t1) + (g.part ? " (phần " + g.part + " của 1 shot dài)" : ""), spec: "duration " + Math.min(15, Math.max(4, Math.round(g.dur))) + "s · aspect ratio " + R.info.ratio + " · 1080p · " + specFps() + " fps",
      "ảnh_ref": "@ref1 = khung đầu đoạn (ref_seg" + ("0" + g.i).slice(-2) + ".jpg)",
      "nhân_vật": who.length ? who : an.characters.slice(0, 2), "bối_cảnh": where.length ? where : an.settings.slice(0, 1), shots: shots,
      "thoại": speechIn(g.t0, g.t1).map(function (s) { return "[" + (Math.max(s[0], g.t0) - g.t0).toFixed(1) + "s] " + s[2]; }),
      "nhạc": au.bpm ? au.bpm + " BPM" + (sec.length ? " · " + sec.join(" → ") : "") : "" };
  }
  function specFps() { var f = +R.info.fps || 24; return f >= 47 ? 24 : f >= 29 ? 30 : f >= 24.5 ? 25 : 24; }
  function writePrompts(an, provider, model) {
    var segs = segments(+S().seg || 10), mode = S().mode || "recreate", note = (S().note || "").trim(), out = [];
    var per = provider === "gemini" ? 4 : 1, batches = [];
    for (var k = 0; k < segs.length; k += per) batches.push(segs.slice(k, k + per));
    var head = "TỔNG QUAN: " + JSON.stringify(an.overview) + "\nNGÔN NGỮ DỰNG: " + JSON.stringify(an.editing) +
      "\nĐỊNH DẠNG GỐC: " + R.info.ratio + " · " + R.info.w + "x" + R.info.h + " · " + R.info.fps + " fps" +
      "\nCHẾ ĐỘ: " + (mode === "style" ? "GIỮ PHONG CÁCH (máy, ánh sáng, màu, nhịp, cấu trúc) nhưng ĐỔI NỘI DUNG theo yêu cầu editor" : "TÁI TẠO SÁT BẢN GỐC") +
      (note ? "\nYÊU CẦU EDITOR: " + note : "");
    return batches.reduce(function (pr, batch) {
      return pr.then(function () {
        step("prompt", "run", "đoạn " + batch[0].i + (batch.length > 1 ? "–" + batch[batch.length - 1].i : "") + "/" + segs.length);
        var llm = provider === "gemini" ? mk("gemini", model || geminiModel()) : mk("ollama", model || ollamaText());
        usedModel = (provider === "gemini" ? "gemini · " : "ollama · ") + llm.model;
        llm.userTurn(head + "\n\nCÁC ĐOẠN CẦN VIẾT PROMPT:\n" + JSON.stringify(batch.map(function (g) { return segBrief(g, an); }), null, 1) +
          "\n\nViết " + batch.length + " prompt (mỗi đoạn một). TRẢ VỀ DUY NHẤT JSON {\"prompts\":[…]}.");
        return llm.send(promptSystem(), null, { json: true, temperature: 0.5, maxTokens: 9000 }).then(function (r) {
          var j = LLM.parseJSON(r.text);
          (j.prompts || []).forEach(function (p, k) {
            var g = segs.filter(function (x) { return x.i === +p.seg; })[0] || batch[k]; if (!g) return;
            if (out.some(function (o) { return o.seg === g.i; })) return;
            out.push({ seg: g.i, t0: g.t0, t1: g.t1, dur: g.dur, shots: g.shots, part: g.part || "", title: p.title || "", prompt: cleanPrompt(p.prompt) });
          });
        });
      });
    }, Promise.resolve()).then(function () {
      out.sort(function (a, b) { return a.seg - b.seg; });
      var miss = segs.filter(function (g) { return !out.some(function (p) { return p.seg === g.i; }); }).map(function (g) { return g.i; });
      if (miss.length) NX.log("⚠ AI bỏ sót prompt đoạn " + miss.join(", ") + " — bấm 'Viết lại prompt'", "#e0a82e");
      if (!out.length) throw new Error("AI không trả prompt nào");
      return out;
    });
  }

  var usedModel = "";
  function writeRun(an) {
    var c = choice("mWrite"), hasKey = !!geminiKey();
    if (c.prov === "ollama") return writePrompts(an, "ollama", c.model);
    if (c.prov === "gemini") {
      if (!hasKey) return Promise.reject(new Error("chưa có API key Gemini — nhập ở tab Cài đặt, hoặc chọn model local"));
      return writePrompts(an, "gemini", c.model);
    }
    if (!hasKey) return writePrompts(an, "ollama", ollamaText());
    return writePrompts(an, "gemini", geminiModel()).catch(function (e) {
      NX.log("• Gemini lỗi khi viết prompt: " + String(e.message || e).slice(0, 120) + " → viết bằng local " + ollamaText(), "#e0a82e");
      step("prompt", "run", "Gemini lỗi → chuyển local");
      return writePrompts(an, "ollama", ollamaText());
    });
  }

  // ───────────────────────────────────────────── 4. ảnh ref + .md
  function ffFrame(t, dst) {
    return new Promise(function (res) {
      cp.execFile("ffmpeg", ["-v", "error", "-y", "-ss", Math.max(0, t).toFixed(3), "-i", R.video, "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2", "-q:v", "2", dst],
        { windowsHide: true }, function () { res(fs.existsSync(dst)); });
    });
  }
  function refs(prompts) {
    var dir = outFor(cur()) + "/ref"; try { fs.mkdirSync(dir, { recursive: true }); } catch (e) {}
    return prompts.reduce(function (pr, p) {
      return pr.then(function () { var d = dir + "/ref_seg" + ("0" + p.seg).slice(-2) + ".jpg"; p.ref = d; return ffFrame(p.t0 + 0.05, d); });
    }, Promise.resolve());
  }
  function md() {
    var i = R.info, e = R.edit, au = R.audio || {}, o = A.overview || {}, ed = A.editing || {}, L = [];
    var cl = function (x) { return String(x == null ? "" : x).replace(/\|/g, "/").replace(/\r?\n/g, " "); };
    L.push("# Reverse prompt — " + R.name, "");
    L.push("Nguồn: `" + R.video + "`  ", i.w + "×" + i.h + " (" + i.ratio + ") · " + i.fps + " fps · " + tcs(i.dur) + " · " + e.shots + " shot · ASL " + e.asl + "s · " +
      e.cuts_per_min + " cắt/phút" + (au.bpm ? " · " + au.bpm + " BPM · " + Math.round((au.cut_on_beat || 0) * 100) + "% cắt trúng beat" : "") + "  ",
      "AI: " + (A._model || "") + " (" + (A._via || "") + ") · " + new Date().toLocaleString("vi-VN"), "");
    L.push("## Tổng quan", "", "| | |", "|---|---|");
    [["Thể loại", o.genre], ["Nội dung", o.subject], ["Mạch", o.story], ["Mood", o.mood], ["Style", o.visual_style], ["Video AI?", o.ai_generated]].forEach(function (r) { if (r[1]) L.push("| " + r[0] + " | " + cl(r[1]) + " |"); });
    L.push("", "## Ngôn ngữ dựng", "");
    if ((ed.structure || []).length) { L.push("| Giờ | Phần | Nội dung |", "|---|---|---|"); ed.structure.forEach(function (s) { L.push("| " + tc(s.t0) + "–" + tc(s.t1) + " | " + cl(s.part) + " | " + cl(s.desc) + " |"); }); L.push(""); }
    [["Nhịp", "rhythm"], ["Máy quay", "camera"], ["Ống kính", "lens"], ["Ánh sáng", "lighting"], ["Màu", "color"], ["Chuyển cảnh", "transitions"], ["Âm thanh", "sound"], ["Chữ / đồ hoạ", "text_graphics"]]
      .forEach(function (r) { if (ed[r[1]]) L.push("- **" + r[0] + ":** " + ed[r[1]]); });
    if ((ed.techniques || []).length) { L.push("", "### Kỹ thuật đáng học", ""); ed.techniques.forEach(function (t) { L.push("- " + t); }); }
    L.push("", "## Nhân vật & bối cảnh", "");
    A.characters.forEach(function (c) { L.push("- **" + c.id + " · " + (c.name || "") + "** — " + (c.look || "") + (c.shots ? " _(shot " + c.shots.join(", ") + ")_" : "")); });
    A.settings.forEach(function (c) { L.push("- **" + c.id + "** — " + (c.desc || "") + (c.shots ? " _(shot " + c.shots.join(", ") + ")_" : "")); });
    L.push("", "## Shot list", "", "| # | Giờ | Cỡ / góc / lens | Máy | Hành động | Sáng / màu | Vào bằng | Chữ · SFX · FX |", "|---|---|---|---|---|---|---|---|");
    A.shots.forEach(function (a) {
      var m = R.shots[a.i - 1], c = m.color || {};
      L.push("| " + a.i + " | " + tc(m.t0) + "–" + tc(m.t1) + " | " + cl([a.size, a.angle, a.lens].filter(Boolean).join(" · ")) + " | " + cl(a.camera || (m.cam || {}).label) + (a.speed && a.speed !== "real-time" ? " · " + cl(a.speed) : "") +
        " | " + cl([a.subject, a.action].filter(Boolean).join(": ")) + " | " + cl([a.light, c.key + " " + c.tone].filter(Boolean).join(" · ")) + " | " + cl(a.transition_in) + " | " + cl(extras(a)) + " |");
    });
    if ((A.prompts || []).length) L.push("", "## Prompt Seedance 2.5", "", (A.prompts_model ? "AI: " + A.prompts_model + " · " : "") + "Mỗi đoạn ≤ " + (S().seg || 10) + "s · chế độ " + ((S().mode || "recreate") === "style" ? "giữ phong cách, đổi nội dung" : "tái tạo sát bản gốc") +
      ((S().note || "").trim() ? " · yêu cầu: " + S().note.trim() : "") + " · ảnh ref trong `ref/`.", "");
    (A.prompts || []).forEach(function (p) {
      L.push("### Đoạn " + p.seg + " · " + tc(p.t0) + "–" + tc(p.t1) + " · shot " + p.shots.join(", ") + (p.part ? " (phần " + p.part + ")" : "") + (p.title ? " — " + p.title : ""), "");
      if (p.ref) L.push("Ảnh ref: `ref/" + path.basename(p.ref) + "`", "");
      L.push("```text", p.prompt, "```", "");
    });
    if ((au.speech || []).length) { L.push("## Transcript", ""); au.speech.forEach(function (s) { L.push("- `" + tc(s[0]) + "` " + s[2]); }); }
    var f = outFor(cur()) + "/ReversePrompt_" + slug(stemOf(R.video)) + ".md";
    fs.writeFileSync(f, L.join("\n") + "\n", "utf8");
    A._md = f; return f;
  }
  function extras(a) {
    var t = (a.text || []).map(function (x) { return "“" + (typeof x === "string" ? x : x.content || "") + "”"; }).filter(function (x) { return x.length > 2; });
    var f = (a.sfx || []).map(function (x) { return "♪" + (typeof x === "string" ? x : x.kind || ""); });
    return t.concat(f).concat((a.fx || []).map(function (x) { return "✦" + x; })).join(" ");
  }
  function saveA() { fs.writeFileSync(outFor(cur()) + "/reverse_ai.json", JSON.stringify(A, null, 1), "utf8"); }

  // ───────────────────────────────────────────── chạy
  function lock(label, fn, w) {
    if (busy) return;
    if (NX.isBusy && NX.isBusy()) { NX.log("• Đang chạy: " + NX.busyLabel() + " — chờ xong rồi bấm lại", "#e0a82e"); return; }
    busy = true; where = w || "rv"; err(""); setBtns();
    var t0 = Date.now();
    (NX.exclusive ? NX.exclusive("pipeline", label, fn) : fn()).then(function () {
      NX.log("✔ " + label + " xong (" + Math.round((Date.now() - t0) / 1000) + "s)" + (A && A._md ? " → " + A._md : ""));
    }).catch(function (e) {
      var m = String(e && e.message || e);
      var ol = where === "pr" ? "#rvPrSteps" : "#rvSteps", run = document.querySelectorAll(ol + " li.run");
      if (!run.length) run = Array.prototype.filter.call(document.querySelectorAll(ol + " li"), function (li) { return li.className !== "ok"; }).slice(0, 1);
      Array.prototype.forEach.call(run, function (li) { li.className = "bad"; });
      err("✖ " + esc(m) + (/quota|quá tải|429|503/i.test(m) ? "<br>→ Đổi model sang một model <b>Ollama</b> (local), hoặc thử lại sau ít phút." :
        /Ollama chưa chạy|ECONNREFUSED/.test(m) ? "<br>→ Mở ứng dụng Ollama rồi bấm lại." : /context|token/i.test(m) ? "<br>→ Video quá nhiều shot cho model local: dùng Gemini, hoặc giảm độ nhạy cắt (ít shot hơn)." : ""));
      NX.log("✖ " + label + ": " + m, "#e5534b");
    }).then(function () { busy = false; render(); });
  }
  function runAll(force) {
    lock("Phân tích video", function () {
      steps(true);
      return measure(force).then(function () { return see(); }).then(function (j) {
        step("see", "ok", j._model);
        step("lang", "run");
        A = normalize(j); saveA(); render();
        var nt = 0, ns = 0, nf = 0;
        A.shots.forEach(function (a) { nt += (a.text || []).length; ns += (a.sfx || []).length; nf += (a.fx || []).length; });
        step("lang", "ok", A.shots.length + " shot · " + nt + " chữ · " + ns + " SFX · " + nf + " hiệu ứng" + (A.missing.length ? " · thiếu mô tả " + A.missing.length : ""));
        step("md", "run");
        var f = md(); saveA(); step("md", "ok", path.basename(f));
        NX.log("• Prompt Seedance là bước riêng — card 'Prompt Seedance' bên dưới", "#8b9099");
      });
    }, "rv");
  }
  function rePrompt() {
    if (!R || !A) { err("Phân tích video trước (card 2), rồi mới viết prompt.", "pr"); return; }
    lock("Viết prompt Seedance", function () {
      steps(true, "pr"); step("prompt", "run", choiceLabel("mWrite"));
      return writeRun(A).then(finish);
    }, "pr");
  }
  function finish(prompts) {
    A.prompts = prompts; A.prompts_model = usedModel; A.opts = { seg: S().seg, mode: S().mode, note: S().note, at: new Date().toISOString() };
    step("prompt", "ok", prompts.length + " prompt · " + usedModel);
    step("pmd", "run");
    return refs(prompts).then(function () { var f = md(); saveA(); step("pmd", "ok", path.basename(f)); render(); });
  }

  // ───────────────────────────────────────────── hiển thị
  function setBtns() {
    var has = !!(R && A);
    $("bRvRun").disabled = busy; if ($("bRvTpl")) $("bRvTpl").disabled = busy;
    $("bRvPrompt").disabled = busy || !has; $("bRvRedo").disabled = busy || !R;
    ["bRvMd", "bRvFolder"].forEach(function (id) { $(id).disabled = !(A && A._md && fs.existsSync(A._md)); });
    $("bRvCopyAll").disabled = !(A && (A.prompts || []).length);
    if ($("rvMSee")) $("rvMSee").disabled = busy; if ($("rvMWrite")) $("rvMWrite").disabled = busy;
  }
  function render() {
    setBtns(); info();
    try { fillTplPick(); } catch (e) {}
    var box = $("rvResult");
    if (!R || !A) { box.innerHTML = ""; $("rvResultCard").style.display = "none"; if ($("rvPrCard")) $("rvPrCard").style.display = "none"; return; }
    $("rvResultCard").style.display = ""; if ($("rvPrCard")) $("rvPrCard").style.display = "";
    var o = A.overview || {}, ed = A.editing || {}, h = [];
    h.push('<div class="rv-meta muted">AI: ' + esc(A._model || "") + " · " + esc(A._via || "") + "</div>");
    h.push('<div class="rv-sec"><h4>Tổng quan</h4><dl class="rv-dl">' + [["Thể loại", o.genre], ["Nội dung", o.subject], ["Mạch", o.story], ["Mood", o.mood], ["Style", o.visual_style], ["Video AI?", o.ai_generated]]
      .filter(function (r) { return r[1]; }).map(function (r) { return "<dt>" + r[0] + "</dt><dd>" + esc(r[1]) + "</dd>"; }).join("") + "</dl></div>");
    h.push('<div class="rv-sec"><h4>Ngôn ngữ dựng</h4>');
    if ((ed.structure || []).length) {
      var dur = R.info.dur || 1;
      h.push('<div class="rv-struct">' + ed.structure.map(function (s, k) {
        return '<span style="flex:' + Math.max(0.5, (s.t1 - s.t0) / dur * 100).toFixed(1) + '" class="p' + (k % 5) + '" title="' + esc(tc(s.t0) + "–" + tc(s.t1) + " · " + (s.desc || "")) + '">' + esc(s.part || "") + "</span>";
      }).join("") + "</div>");
    }
    var cv = R.edit.cpm_curve_10 || [], mx = Math.max.apply(null, cv.concat([1]));
    h.push('<div class="rv-cpm" title="Mật độ cắt theo 10 đoạn thời lượng (cắt/phút)">' + cv.map(function (v) {
      return '<i style="height:' + Math.max(2, Math.round(v / mx * 26)) + 'px" title="' + v + ' cắt/phút"></i>';
    }).join("") + '<span class="muted">mật độ cắt</span></div>');
    h.push('<dl class="rv-dl">' + [["Nhịp", ed.rhythm], ["Máy quay", ed.camera], ["Ống kính", ed.lens], ["Ánh sáng", ed.lighting], ["Màu", ed.color], ["Chuyển cảnh", ed.transitions], ["Âm thanh", ed.sound], ["Chữ / đồ hoạ", ed.text_graphics]]
      .filter(function (r) { return r[1]; }).map(function (r) { return "<dt>" + r[0] + "</dt><dd>" + esc(r[1]) + "</dd>"; }).join("") + "</dl>");
    if ((ed.techniques || []).length) h.push('<div class="rv-tech"><b>Kỹ thuật đáng học</b><ul>' + ed.techniques.map(function (t) { return "<li>" + esc(t) + "</li>"; }).join("") + "</ul></div>");
    h.push("</div>");
    if (A.characters.length || A.settings.length) {
      h.push('<div class="rv-sec"><h4>Nhân vật & bối cảnh <span class="muted">— dùng cho Reference Lock</span></h4><ul class="rv-ul">' +
        A.characters.map(function (c) { return "<li><b>" + esc(c.id + " · " + (c.name || "")) + "</b> " + esc(c.look || "") + "</li>"; }).join("") +
        A.settings.map(function (c) { return "<li><b>" + esc(c.id) + "</b> " + esc(c.desc || "") + "</li>"; }).join("") + "</ul></div>");
    }
    h.push('<div class="rv-sec"><h4>Shot list <span class="muted">— bấm để mở trong Source monitor</span></h4><div class="rv-shots">');
    A.shots.forEach(function (a) {
      var m = R.shots[a.i - 1], c = m.color || {};
      h.push('<div class="rv-shot" data-i="' + a.i + '"><img loading="lazy" src="' + fileUrl(m.mid || m.first) + '"><div class="rv-sx"><b>#' + a.i + "</b> <span class=\"muted\">" + tc(m.t0) + "–" + tc(m.t1) + " · " + m.dur.toFixed(1) + "s</span>" +
        (a.transition_in && !/^cut$/i.test(a.transition_in) ? ' <span class="rv-tag">' + esc(a.transition_in) + "</span>" : "") +
        '<div class="rv-l1">' + esc([a.size, a.angle, a.lens].filter(Boolean).join(" · ")) + "</div>" +
        '<div class="rv-l2">🎥 ' + esc(a.camera || (m.cam || {}).label || "") + (a.speed && a.speed !== "real-time" ? " · " + esc(a.speed) : "") + "</div>" +
        '<div class="rv-l2">' + esc([a.subject, a.action].filter(Boolean).join(": ")) + "</div>" +
        (extras(a) ? '<div class="rv-l2 rv-x">' + esc(extras(a)) + "</div>" : "") +
        '<div class="rv-pal"><span class="rv-sw">' + (c.palette || []).map(function (p) { return '<i style="background:' + p[0] + ";flex:" + p[1] + '" title="' + p[0] + " · " + Math.round(p[1] * 100) + '%"></i>'; }).join("") + '</span><span class="muted">' + esc((c.key || "") + " · " + (c.tone || "")) + "</span></div>" +
        "</div></div>");
    });
    h.push("</div></div>");
    box.innerHTML = h.join("");
    var pbox = $("rvPrompts"); h = [];
    if ((A.prompts || []).length) {
      h.push('<div class="rv-sec"><h4>' + A.prompts.length + ' đoạn <span class="muted">— ' + esc(A.prompts_model || "") + (A.opts && A.opts.at ? " · " + esc(new Date(A.opts.at).toLocaleString("vi-VN")) : "") +
        (A.opts ? " · ≤ " + esc(A.opts.seg || 10) + "s · " + (A.opts.mode === "style" ? "đổi nội dung" : "tái tạo") : "") + "</span></h4>");
      A.prompts.forEach(function (p, k) {
        h.push('<div class="rv-pr"><div class="rv-prh">' + (p.ref && fs.existsSync(p.ref) ? '<img src="' + fileUrl(p.ref) + '" title="ảnh ref: ' + esc(path.basename(p.ref)) + '">' : "") +
          '<div class="rv-prt"><b>Đoạn ' + p.seg + "</b> " + esc(p.title || "") + '<div class="muted">' + tc(p.t0) + "–" + tc(p.t1) + " · " + p.dur + "s · shot " + p.shots.join(", ") + (p.part ? " · phần " + p.part : "") + "</div></div>" +
          '<button class="btn rv-copy" data-k="' + k + '">Copy</button></div><pre class="rv-pre">' + esc(p.prompt) + "</pre></div>");
      });
      h.push("</div>");
    } else h.push('<div class="hint">Chưa có prompt — chọn độ dài đoạn, mục tiêu, model rồi bấm ▶ Viết prompt Seedance.</div>');
    pbox.innerHTML = h.join("");
    Array.prototype.forEach.call(pbox.querySelectorAll(".rv-copy"), function (b) {
      b.onclick = function () { copy(A.prompts[+b.dataset.k].prompt); b.textContent = "✓ Đã copy"; setTimeout(function () { b.textContent = "Copy"; }, 1500); };
    });
    Array.prototype.forEach.call(box.querySelectorAll(".rv-shot"), function (d) {
      d.onclick = function () { var m = R.shots[+d.dataset.i - 1]; NX.evalHost("nxOpenInSource(" + JSON.stringify(R.video) + "," + m.t0 + "," + m.t1 + ")"); };
    });
  }
  function copy(text) {
    var ta = document.createElement("textarea"); ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select(); try { document.execCommand("copy"); } catch (e) {} document.body.removeChild(ta);
  }
  function openPath(p) { try { cp.exec('start "" "' + p.replace(/\//g, "\\") + '"'); } catch (e) {} }

  // ───────────────────────────────────────────── 5. template sequence (đổi source, giữ toàn bộ kỹ thuật dựng)
  function revRoot() { return ((NX.S.outRoot || "") + "/REVERSE").replace(/\\/g, "/"); }
  function tplList() {
    var out = [];
    try {
      fs.readdirSync(revRoot()).forEach(function (d) {
        var dir = revRoot() + "/" + d, T = readJSON(dir + "/template.json", null);
        if (T && T.slots) out.push({ dir: dir, name: T.name, slots: T.slots.length, dur: T.dur, ratio: T.ratio, t: fs.statSync(dir + "/template.json").mtimeMs });
      });
    } catch (e) {}
    return out.sort(function (a, b) { return b.t - a.t; });
  }
  function fillTplPick() {
    var sel = $("rvTplPick"); if (!sel) return;
    var cv = cur() && R && A ? outFor(cur()) : "", list = tplList(), h = [];
    if (cv) h.push('<option value="' + esc(cv) + '">▶ ' + esc(stemOf(cur())) + " — video đang phân tích</option>");
    list.forEach(function (t) {
      if (t.dir === cv) return;
      h.push('<option value="' + esc(t.dir) + '">' + esc(t.name) + " · " + t.slots + " shot · " + tcs(t.dur) + " · " + esc(t.ratio || "") + "</option>");
    });
    sel.innerHTML = h.join("") || '<option value="">— chưa có template: phân tích 1 video tham khảo trước —</option>';
    if (S().tpl && Array.prototype.some.call(sel.options, function (o) { return o.value === S().tpl; }) && !cv) sel.value = S().tpl;
    $("rvTplCard").style.display = h.length ? "" : "none";
    tplInfo();
  }
  function sfxIndex() { return path.join(NX.cfgDir || "", "sound", "sfx_index.json").replace(/\\/g, "/"); }
  var tplDone = null;            // kết quả lần tạo gần nhất (giữ lại khi panel vẽ lại)
  function srcReady() { var s = NX.S.src; return !!(s && fs.existsSync(path.join(s, "_nexys_work", "media.json"))); }
  function tplInfo() {
    var el = $("rvTplInfo"); if (!el || busy) return;
    var dir = $("rvTplPick").value, T = dir ? readJSON(dir + "/template.json", null) : null, R2 = dir ? readJSON(dir + "/reverse.json", null) : null;
    var parts = [];
    if (T) parts.push("Template <b>" + esc(T.name) + "</b>: " + T.slots.length + " shot · " + tcs(T.dur) + " · " + esc(T.ratio || "") +
      (T.bpm ? " · " + T.bpm + " BPM" : "") + " · " + T.slots.filter(function (x) { return x.role === "dialogue"; }).length + " slot thoại");
    else if (R2) parts.push("Template sẽ được tạo từ phân tích của " + esc(R2.name) + " (" + R2.shots.length + " shot)");
    if ($("rvTplSrc").value === "cur") parts.push(srcReady() ? "Source: <b>" + esc(path.basename(NX.S.src)) + "</b> · đã phân tích ✓"
      : '<span class="bad">Source tab Chạy ' + (NX.S.src ? "(" + esc(path.basename(NX.S.src)) + ") chưa phân tích" : "chưa chọn") + " — chạy 1 · Phân tích, hoặc chọn 'chỉ khung slot'</span>");
    else parts.push("Không lấp source: mỗi shot là thẻ chữ đúng thời lượng + mô tả, thay bằng hình của anh (Replace with clip)");
    if (T) {
      var ntx = 0, nsx = 0, nfx = 0;
      T.slots.forEach(function (x) { ntx += (x.texts || []).length; nsx += (x.sfx || []).length; if (x.zoom || x.punch || (x.fx || []).length) nfx++; });
      var ix = readJSON(sfxIndex(), null);
      parts.push("Áp lên timeline: " + (S().tplFx !== false ? nfx + " shot có hiệu ứng (zoom keyframe / punch scale)" : "hiệu ứng tắt") + " · " +
        (S().tplText !== false ? ntx + " chữ → V4" : "chữ tắt") + " · " + (S().tplSfx !== false ? nsx + " SFX → " + (ix && ix.count ? "A3 từ thư viện " + ix.count + " file" : '<span class="bad">marker (chưa quét thư viện SFX ở tab Âm thanh)</span>') : "SFX tắt") +
        ((T.v || 1) < 2 ? ' · <span class="bad">template cũ — bấm Phân tích lại để có chữ/SFX/hiệu ứng</span>' : ""));
    }
    if ($("rvTplMusic").value === "file") parts.push("Nhạc: " + (S().tplMusic ? esc(path.basename(S().tplMusic)) : '<span class="bad">chưa chọn</span>'));
    el.innerHTML = (tplDone && tplDone.dir === dir ? tplDone.html + '<div class="muted" style="margin-top:6px">' : "<div>") + parts.join("<br>") + "</div>";
  }
  function pickTplMusic() {
    var r = window.cep.fs.showOpenDialogEx(false, false, "Chọn nhạc cho template", S().tplMusic ? path.dirname(S().tplMusic) : "", ["mp3", "wav", "m4a", "aac", "flac", "aif", "aiff", "ogg"]);
    if (r && r.data && r.data.length) { S().tplMusic = r.data[0].replace(/\\/g, "/"); NX.save(); }
    tplInfo();
  }
  function makeTemplate(dir) {
    var tp = dir + "/template.json", ra = dir + "/reverse_ai.json";
    var stale = !fs.existsSync(tp) || (fs.existsSync(ra) && fs.statSync(ra).mtimeMs > fs.statSync(tp).mtimeMs);
    return stale ? NX.run(["-m", "nxcore.template", "make", dir], "Tạo template từ phân tích") : Promise.resolve();
  }
  function runTemplate() {
    lock("Template sequence", function () {
      var dir = $("rvTplPick").value, mode = $("rvTplSrc").value, src = "";
      if (!dir) throw new Error("chưa có template — phân tích 1 video tham khảo trước");
      if (mode === "cur") {
        if (!srcReady()) throw new Error("folder source ở tab Chạy chưa phân tích — chạy 1 · Phân tích trước, hoặc chọn 'chỉ khung slot'");
        src = NX.S.src;
      }
      var mu = $("rvTplMusic").value;
      if (mu === "file" && !(S().tplMusic && fs.existsSync(S().tplMusic))) throw new Error("chưa chọn bài nhạc");
      S().tpl = dir; NX.save();
      return makeTemplate(dir).then(function () {
        var T = readJSON(dir + "/template.json", null);
        if (!T || !T.slots) throw new Error("không tạo được template.json");
        var name = ("TPL_" + slug(T.name).slice(0, 24) + (src ? "_" + slug(path.basename(src)).slice(0, 20) : "_SLOT")).replace(/_+/g, "_");
        var od = src ? NX.outDir() : dir + "/sequence", plan = od + "/plan_" + name + ".json";
        try { fs.mkdirSync(od, { recursive: true }); } catch (e) {}
        var args = ["-m", "nxcore.template", "fill", dir + "/template.json", "--plan", plan, "--name", name, "--out", od,
                    "--music", mu === "file" ? S().tplMusic : mu];
        if (src) args.push("--src", src);
        if ($("rvTplSeq").value === "panel" && NX.seqSpec) args.push("--seq", NX.seqSpec().spec);
        if (S().tplFx === false) args.push("--no-fx");
        if (S().tplText === false) args.push("--no-text");
        if (S().tplSfx === false) args.push("--no-sfx"); else args.push("--sfx-index", sfxIndex());
        $("rvTplInfo").textContent = "Đang lấp " + T.slots.length + " slot…";
        return NX.run(args, "Lấp template bằng source").then(function () {
          if (!NX.buildPlan) throw new Error("panel chưa có buildPlan — cập nhật main.js");
          return NX.buildPlan(plan);
        }).then(function () {
          var rep = readJSON(plan.replace(/\.json$/, ".template.json"), {});
          tplDone = { dir: dir, html: "" };
          tplDone.html = "✔ <b>" + esc(name) + "</b>: " + rep.slots + " shot · lấp " + rep.filled + " từ source · " + rep.gaps + " thẻ chữ (slot trống)" +
            " · " + (rep.fx || 0) + " hiệu ứng trên thẻ clip · " + (rep.texts || 0) + " chữ (V4) · " + (rep.sfx || 0) + " SFX (A3)" +
            (rep.music ? " · nhạc " + esc(path.basename(rep.music)) : "") +
            ((rep.picks || []).length ? '<div class="rv-tplp">' + rep.picks.slice(0, 40).map(function (p) {
              return "<div>#" + p.slot + " · " + esc(p.file) + " @ " + tc(p.in) + "–" + tc(p.out) + ' <span class="muted">' + esc(p.desc || "") + "</span></div>";
            }).join("") + "</div>" : "");
          $("rvTplInfo").innerHTML = tplDone.html;
        });
      });
    });
  }

  // ───────────────────────────────────────────── wiring
  bindOpts();
  migrateModels();
  [["rvMSee", "mSee"], ["rvMWrite", "mWrite"]].forEach(function (x) {
    var el = $(x[0]); if (!el) return;
    el.onchange = function () { S()[x[1]] = el.value; NX.save(); modelStat(); };
  });
  if ($("bRvMRef")) $("bRvMRef").onclick = refreshModels;
  [["rvTplFx", "tplFx"], ["rvTplText", "tplText"], ["rvTplSfx", "tplSfx"]].forEach(function (x) {
    var el = $(x[0]); if (!el) return; el.checked = S()[x[1]] !== false;
    el.onchange = function () { S()[x[1]] = el.checked; NX.save(); tplDone = null; tplInfo(); };
  });
  $("bRvPick").onclick = pickFile;
  [["rvTplSrc", "tplSrc", "cur"], ["rvTplMusic", "tplMu", "guide"], ["rvTplSeq", "tplSeq", "ref"]].forEach(function (x) {
    var el = $(x[0]); if (!el) return; el.value = S()[x[1]] || x[2];
    el.onchange = function () { S()[x[1]] = el.value; NX.save(); if (x[0] === "rvTplMusic" && el.value === "file") pickTplMusic(); else tplInfo(); };
  });
  if ($("rvTplPick")) $("rvTplPick").onchange = function () { S().tpl = $("rvTplPick").value; NX.save(); tplDone = null; tplInfo(); };
  if ($("bRvTpl")) $("bRvTpl").onclick = runTemplate;
  if ($("bRvTplDir")) $("bRvTplDir").onclick = function () { var d = $("rvTplPick").value; if (d) openPath(d); };
  $("bRvRun").onclick = function () { runAll(false); };
  $("bRvRedo").onclick = function () { runAll(true); };
  $("bRvPrompt").onclick = rePrompt;
  $("bRvMd").onclick = function () { if (A && A._md) openPath(A._md); };
  $("bRvFolder").onclick = function () { if (cur()) openPath(outFor(cur())); };
  $("bRvCopyAll").onclick = function () {
    if (!A || !A.prompts) return;
    copy(A.prompts.map(function (p) { return "=== ĐOẠN " + p.seg + " · " + tc(p.t0) + "–" + tc(p.t1) + " ===\n" + p.prompt; }).join("\n\n"));
    NX.log("✔ Đã copy " + A.prompts.length + " prompt");
  };
  var dropZone = $("revTab");
  dropZone.addEventListener("dragover", function (e) { e.preventDefault(); });
  dropZone.addEventListener("drop", function (e) {
    e.preventDefault(); var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f && f.path) { S().file = f.path.replace(/\\/g, "/"); $("rvFile").value = S().file; NX.save(); load(); }
  });
  document.addEventListener("nxtab", function (e) { if (e.detail === "revTab" && !busy) { load(); if (OLL === null || !OLL.length) refreshModels(); } });
  load(); fillModels(); refreshModels();
  window.JESReverse = { load: load, segments: function (n) { return R ? segments(n || 10) : []; }, blocks: SEEDANCE_BLOCKS,
    _test: { choice: choice, extras: extras, writeRun: function (a) { return writeRun(a || A); }, metricsText: function () { return R ? metricsText() : ""; }, segBrief: function (g) { return segBrief(g, A); }, md: function () { return md(); }, setR: function (r, a) { R = r; A = a; } } };
})();
