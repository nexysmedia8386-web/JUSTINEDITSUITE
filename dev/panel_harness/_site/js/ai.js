/* Justin EDIT Suite — AI: per-task model routing, 2-stage AI script (vision analysis → writer),
   agentic AI Assist (plan tools + Premiere tools) with persistent chat history.
   Depends on window.NX (main.js), NXLLM (llm.js), JESPR (premiere.js), JESChats (chats.js). */
(function () {
  "use strict";
  var fs = require("fs"), path = require("path");
  var $ = function (id) { return document.getElementById(id); };
  var NX = window.NX, LLM = window.NXLLM, PR = window.JESPR;

  // ======================================================= shared helpers
  function work(p) { return path.join(NX.S.src, "_nexys_work", p || ""); }
  function readJSON(p, d) { try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) { return d; } }

  // ======================================================= model routing (per task)
  var TASKS = { chat: "AI Assist (agent)", script: "Viết kịch bản", vision: "Phân tích source (hình + transcript)" };
  function routeOf(task) {
    var S = NX.S, r = (S.route || {})[task] || {};
    var prov = r.provider || S.provider || "gemini";
    var model = r.model || (r.provider && r.provider !== S.provider ? "" : (prov === S.provider ? S.model : "")) || "";
    var key = prov === S.provider ? (S.apiKey || (S.keys || {})[prov]) : (S.keys || {})[prov];
    return { provider: prov, model: model, key: key || "" };
  }
  function ensureKeyFor(task) {
    var r = routeOf(task), P = LLM.PROVIDERS[r.provider] || LLM.PROVIDERS.gemini;
    if (!P.needsKey || r.key) return Promise.resolve();
    return NX.ask("Việc '" + TASKS[task] + "' đang dùng " + P.label + " nhưng chưa có API key. Dán key:", [], { text: true, placeholder: "API key" })
      .then(function (k) {
        if (!k) throw new Error("chưa có API key " + P.label);
        NX.S.keys = NX.S.keys || {}; NX.S.keys[r.provider] = k;
        if (r.provider === NX.S.provider) NX.setField("apiKey", k); else NX.save();
      });
  }
  function sessionFor(task) {
    var r = routeOf(task);
    return LLM.create(r.provider, r.key, r.model || undefined, function (m) {
      var own = ((NX.S.route || {})[task] || {}).provider;
      if (own) { NX.S.route[task] = { provider: r.provider, model: m }; NX.save(); syncRouteUI(); }
      else { NX.setField("model", m); NX.S.models = NX.S.models || {}; NX.S.models[r.provider] = m; NX.save(); }   // main provider -> fix for every task
      NX.log("• Model cũ không còn dùng được → '" + TASKS[task] + "' chuyển sang " + m);
    }, { workspaceId: (NX.S.anthropicWs || NX.S.anthropicWorkspaceId || "").trim(),
         numCtx: +NX.S.numCtx || 16384,
         fallbackKey: r.provider === "openrouter" ? ((NX.S.keys || {}).gemini || "") : "",
         onSwitch: function (from, to, why) { NX.log("• " + String(from).split("/").pop() + " " + why + " → tự chuyển sang " + to + " cho '" + TASKS[task] + "'", "#e0a82e"); },
         onQuota: function (from, to, why) { NX.log("• " + from + " " + (why || "hết quota free hôm nay") + " → tạm dùng " + to, "#e0a82e"); },
         onCtx: function (from, to, need) {
           NX.log("• Dữ liệu " + need + " token > context " + from + " → tự nâng lên " + to + " và chạy lại (đã lưu)", "#e0a82e");
           if (to > (+NX.S.numCtx || 16384)) { NX.S.numCtx = to; NX.save(); if ($("numCtx")) $("numCtx").value = to; }
         }
       }, function (from, to, why) {
      NX.log("• " + from + " " + (why || "hết quota free hôm nay") + " → tạm dùng " + to + " cho '" + TASKS[task] + "'", "#e0a82e");
    });
  }
  function routeLabel(task) { var r = routeOf(task); return r.provider + (r.model ? " · " + r.model : ""); }

  // ======================================================= AI SETTINGS (provider / key / model / Ollama)
  var prevProv = NX.S.provider || "gemini";
  (function migrate() {
    var S = NX.S; S.keys = S.keys || {}; S.models = S.models || {};
    if (S.apiKey && !S.keys[prevProv]) S.keys[prevProv] = S.apiKey;
    if (S.model && !S.models[prevProv]) S.models[prevProv] = S.model;
    NX.save();
  })();
  function syncProviderUI() {
    var p = NX.S.provider, P = LLM.PROVIDERS[p] || LLM.PROVIDERS.gemini;
    $("keyRow").style.display = P.needsKey ? "block" : "none";
    $("keyFor").textContent = "(" + P.label + ")";
    var hasClaude = p === "claude" || (NX.S.route && Object.keys(NX.S.route).some(function (k) { return (NX.S.route[k] || {}).provider === "claude"; }));
    if ($("anthropicWsRow")) {
      $("anthropicWsRow").style.display = hasClaude ? "block" : "none";
      if ($("anthropicWs")) $("anthropicWs").value = NX.S.anthropicWs || "";
    }
    $("ollamaBox").style.display = p === "ollama" ? "block" : "none";
    $("model").placeholder = P.def ? "mặc định: " + P.def : "bỏ trống = tự chọn model free tốt nhất";
    $("numCtx").value = NX.S.numCtx || 16384;
    if (p === "ollama") ollamaStatus();
  }
  function refreshModels() {
    var sel = $("modelSel");
    sel.innerHTML = '<option value="">(đang tải…)</option>';
    return LLM.listModels(NX.S.provider, NX.S.apiKey).then(function (ms) {
      sel.innerHTML = '<option value="">(mặc định)</option>' + ms.map(function (m) {
        return '<option value="' + NX.esc(m.id) + '"' + (m.id === NX.S.model ? " selected" : "") + ">" + NX.esc(m.id) + (m.note ? " · " + NX.esc(m.note) : "") + "</option>"; }).join("");
      if (NX.S.model && !ms.some(function (m) { return m.id === NX.S.model; }))
        sel.insertAdjacentHTML("beforeend", '<option value="' + NX.esc(NX.S.model) + '" selected>' + NX.esc(NX.S.model) + "</option>");
      return ms;
    }).catch(function (e) { sel.innerHTML = '<option value="">(không tải được: ' + NX.esc(e.message) + ")</option>"; return []; });
  }
  function ollamaStatus() {
    return LLM.ollamaModels().then(function (ms) {
      $("ollamaStatus").textContent = "Ollama: đang chạy · " + (ms.length ? ms.length + " model: " + ms.map(function (m) { return m.id; }).join(", ") : "chưa có model nào");
      return ms;
    }).catch(function () { $("ollamaStatus").textContent = "Ollama: chưa chạy — mở ứng dụng Ollama rồi bấm ↻"; return null; });
  }
  $("provider").addEventListener("change", function () {
    var S = NX.S, np = $("provider").value;
    S.keys[prevProv] = S.apiKey; S.models[prevProv] = S.model;
    S.provider = np; S.apiKey = S.keys[np] || ""; S.model = S.models[np] || "";
    $("apiKey").value = S.apiKey; $("model").value = S.model; prevProv = np; NX.save();
    chat = null; syncProviderUI(); refreshModels();
  });
  $("apiKey").addEventListener("change", function () { NX.S.keys[NX.S.provider] = NX.S.apiKey; NX.save(); refreshModels(); });
  if ($("anthropicWs")) $("anthropicWs").addEventListener("change", function () { NX.S.anthropicWs = $("anthropicWs").value.trim(); NX.save(); });
  $("model").addEventListener("change", function () { NX.S.models[NX.S.provider] = NX.S.model; chat = null; NX.save(); });
  $("modelSel").onchange = function () { NX.setField("model", $("modelSel").value); NX.S.models[NX.S.provider] = NX.S.model; chat = null; NX.save(); };
  $("bModels").onclick = function () { syncProviderUI(); refreshModels(); };
  $("numCtx").onchange = function () { NX.S.numCtx = Math.max(2048, +$("numCtx").value || 16384); NX.save(); chat = null; };
  $("bPull").onclick = function () {
    var name = $("pullName").value.trim(); if (!name) return;
    $("bPull").disabled = true; NX.log("▶ Ollama tải " + name + "…", "#CCDC42");
    var lastLog = 0;
    LLM.ollamaPull(name, function (st, pct) {
      $("ollamaStatus").textContent = "Đang tải " + name + ": " + st + (pct != null ? " " + pct + "%" : "");
      if (pct == null || pct - lastLog >= 10 || pct === 100) { lastLog = pct || 0; NX.log("  " + st + (pct != null ? " " + pct + "%" : "")); }
    }).then(function () {
      NX.log("✔ Đã tải " + name); NX.setField("model", name); NX.S.models.ollama = name; NX.save();
      return ollamaStatus().then(refreshModels);
    }).catch(function (e) { NX.log("✖ " + e.message, "#e5534b"); ollamaStatus(); })
      .then(function () { $("bPull").disabled = false; });
  };
  syncProviderUI(); refreshModels();

  // ---- routing + autonomy UI
  function syncRouteUI() {
    Object.keys(TASKS).forEach(function (t) {
      var r = (NX.S.route || {})[t] || {};
      if ($("route_" + t + "_p")) { $("route_" + t + "_p").value = r.provider || ""; $("route_" + t + "_m").value = r.model || "";
        $("route_" + t + "_m").placeholder = r.provider ? (LLM.PROVIDERS[r.provider] || {}).def || "tự chọn" : "theo provider chính"; }
    });
    if ($("autonomy")) $("autonomy").value = NX.S.autonomy || "backup";
  }
  Object.keys(TASKS).forEach(function (t) {
    ["p", "m"].forEach(function (k) {
      var el = $("route_" + t + "_" + k); if (!el) return;
      el.addEventListener("change", function () {
        NX.S.route = NX.S.route || {}; var r = NX.S.route[t] = NX.S.route[t] || {};
        if (k === "p") { r.provider = el.value; r.model = ""; } else r.model = el.value.trim();
        NX.save(); syncRouteUI(); if (t === "chat") chat = null;
      });
    });
  });
  if ($("autonomy")) $("autonomy").onchange = function () { NX.S.autonomy = $("autonomy").value; NX.save(); };
  if ($("bRouteRec")) $("bRouteRec").onclick = function () {
    var k = NX.S.keys || {}, hasClaude = !!k.claude, hasGem = !!(k.gemini || (NX.S.provider === "gemini" && NX.S.apiKey));
    NX.S.route = {
      vision: { provider: "ollama", model: "qwen3-vl:8b-instruct" },            // editor's default: Qwen local (free, no quota)
      script: hasClaude ? { provider: "claude", model: "claude-opus-5-5" } : hasGem ? { provider: "gemini", model: "gemini-3.1-pro-preview" } : { provider: "ollama", model: "gemma4:12b-it-qat" },
      chat: hasClaude ? { provider: "claude", model: "claude-sonnet-5" } : hasGem ? { provider: "gemini", model: "gemini-3.8-flash" } : { provider: "ollama", model: "gemma4:12b-it-qat" }
    };
    NX.save(); syncRouteUI(); chat = null;
    NX.log("✔ Áp dụng cấu hình model khuyến nghị: phân tích=" + routeLabel("vision") + " · kịch bản=" + routeLabel("script") + " · agent=" + routeLabel("chat"));
  };
  syncRouteUI();
  (function migrateModels() {          // editor asked: Pro writes, Flash analyses (quota fallback stays automatic)
    var S = NX.S; if (S.migr_models_v2) return;
    var k = S.keys || {}, hasGem = !!(k.gemini || (S.provider === "gemini" && S.apiKey));
    S.route = S.route || {};
    if (hasGem) {
      var sc = S.route.script || {}, vi = S.route.vision || {};
      if (!sc.provider || (sc.provider === "gemini" && /lite|flash/.test(sc.model || ""))) S.route.script = { provider: "gemini", model: "gemini-3.1-pro-preview" };
      if (!vi.provider || (vi.provider === "gemini" && /lite/.test(vi.model || ""))) S.route.vision = { provider: "gemini", model: "gemini-3.8-flash" };
      if (S.provider === "gemini" && /lite/.test(S.model || "")) { S.model = ""; if (S.models) S.models.gemini = ""; }
    }
    S.migr_models_v2 = true; NX.save(); syncRouteUI();
  })();
  (function migrateVisionQwen() {      // editor asked (2026-10): Qwen is the default analysis model
    var S = NX.S; if (S.migr_vision_qwen) return;
    S.route = S.route || {}; S.route.vision = { provider: "ollama", model: "qwen3-vl:8b-instruct" };
    if (!S.describeModel || S.describeModel === "auto") S.describeModel = "auto";   // nxcore.describe auto → Qwen first
    S.migr_vision_qwen = true; NX.save(); syncRouteUI();
    NX.log("• Model phân tích mặc định: Ollama · qwen3-vl:8b-instruct (đổi lại ở Cài đặt → Model theo việc)", "#8b9099");
  })();

  // nxcore.speech labels: junk (hallucination / loops / counting) never reaches the AI; fragments are marked
  var _spCache = { key: "", val: {} };
  function speechLabels() {
    var p = work("speech.json"), k = "";
    try { var st = fs.statSync(p); k = p + ":" + st.size + ":" + (st.mtimeMs || 0); } catch (e) { return {}; }
    if (k === _spCache.key) return _spCache.val;
    var sp = readJSON(p, {}), o = {};
    _spCache = { key: k, val: o };
    Object.keys(sp).forEach(function (f) { o[f] = (sp[f].seg || []).map(function (r) { return r[0]; }); });
    return o;
  }
  function segsFor(j, labels) {
    var lab = labels[j.file] || [];
    return (j.segments || []).filter(function (s, i) { return lab[i] !== "junk" && lab[i] !== "frag"; });
  }
  function transcripts(maxChars) {
    var d = work("transcripts"), out = [], labels = speechLabels();
    var allSegs = [];
    try {
      fs.readdirSync(d).filter(function (f) { return /\.json$/.test(f) && !/\.v2\.json$/.test(f); }).sort().forEach(function (f) {
        var j = readJSON(path.join(d, f), { segments: [] }), segs = segsFor(j, labels);
        if (!segs.length) return;
        segs.forEach(function (s) { allSegs.push({ file: j.file || f, seg: s }); });
      });
    } catch (e) {}

    // Deduplicate takes: word overlap ratio >= 0.65 within window of 8 segments
    var takeTags = {};
    for (var i = 0; i < allSegs.length; i++) {
      var w1 = (allSegs[i].seg.text || "").toLowerCase().trim().split(/\s+/).filter(Boolean);
      if (w1.length < 3) continue;
      for (var j = i + 1; j < Math.min(allSegs.length, i + 8); j++) {
        var w2 = (allSegs[j].seg.text || "").toLowerCase().trim().split(/\s+/).filter(Boolean);
        if (w2.length < 3) continue;
        var common = 0;
        var s2 = {}; w2.forEach(function (w) { s2[w] = (s2[w] || 0) + 1; });
        w1.forEach(function (w) { if (s2[w]) { common++; s2[w]--; } });
        var sim = (2.0 * common) / (w1.length + w2.length);
        if (sim >= 0.65) {
          takeTags[i] = "[Take cũ - bỏ qua]";
          takeTags[j] = "[Take CUỐI - ĐẠT]";
        }
      }
    }

    var byFile = {};
    allSegs.forEach(function (item, idx) {
      if (!byFile[item.file]) byFile[item.file] = [];
      var tag = takeTags[idx] ? " " + takeTags[idx] : "";
      byFile[item.file].push("  " + item.seg.start.toFixed(2) + "-" + item.seg.end.toFixed(2) + " " + item.seg.text + tag);
    });

    Object.keys(byFile).forEach(function (f) {
      out.push("## " + f + "\n" + byFile[f].join("\n"));
    });
    var t = out.join("\n");
    return t.length > (maxChars || 60000) ? t.slice(0, maxChars || 60000) + "\n…(cắt bớt)" : t;
  }
  function sheets() {
    var d = work("thumbs"), ims = [];
    try {
      fs.readdirSync(d).filter(function (f) { return /^sheet\d+\.jpg$/.test(f); }).sort().slice(0, 6).forEach(function (f) {
        ims.push({ mime: "image/jpeg", b64: fs.readFileSync(path.join(d, f)).toString("base64"), name: f });
      });
    } catch (e) {}
    return ims;
  }
  function stamp() {
    var d = new Date(), p = function (n) { return (n < 10 ? "0" : "") + n; };
    return d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + "-" + p(d.getHours()) + p(d.getMinutes());
  }


  // ======================================================= STAGE 1 — AI SOURCE ANALYSIS (vision model)
  /* model output names -> media keys (keys may include sub-folders: "Day1/CamA/C0001.MP4") */
  function stemOf(f) { return String(f).replace(/\\/g, "/").replace(/\.\w+$/, "").replace(/\//g, "__").replace(/[<>:"|?*]/g, "_"); }
  // QC: a long unchanged stretch of a continuous take (locked-off camera) is NOT a freeze (mirrors nxcore.qc.classify_freeze)
  var qcFix = function (q, dur) {
    q = Object.assign({}, q || {});
    if ((q.qc_v || 1) >= 2 || !(q.freeze || []).length) return q;
    var rs = q.freeze.map(function (r) { return [r[0], r[1]]; }).sort(function (a, b) { return a[0] - b[0]; }), runs = [];
    rs.forEach(function (r) { var L = runs[runs.length - 1]; if (L && r[0] - L[1] <= 1.5) { L[1] = Math.max(L[1], r[1]); L[2]++; } else runs.push([r[0], r[1], 1]); });
    var cover = runs.reduce(function (a, r) { return a + r[1] - r[0]; }, 0), fr = [], st = (q.static || []).slice();
    runs.forEach(function (r) { if ((dur && cover >= 0.4 * dur) || r[1] - r[0] >= Math.max(8, 0.5 * (dur || 0)) || r[2] >= 3) st.push([r[0], r[1]]); else fr.push([r[0], r[1]]); });
    q.freeze = fr; q.static = st;
    return q;
  };
  function staticTxt(q) {
    return (q.static || []).length ? " | cảnh tĩnh/quay liên tục máy cố định (KHÔNG phải lỗi) " + JSON.stringify(q.static.map(function (r) { return [r[0], r[1]]; })) : "";
  }
  function resolveKey(name, keys) {
    if (!name) return null;
    var n = String(name).replace(/\\/g, "/").trim(), low = n.toLowerCase();
    if (keys.indexOf(n) >= 0) return n;
    var ci = keys.filter(function (k) { return k.toLowerCase() === low; }); if (ci.length === 1) return ci[0];
    var base = low.split("/").pop(), stem = base.replace(/\.\w+$/, "");
    var bm = keys.filter(function (k) { return k.toLowerCase().split("/").pop() === base; }); if (bm.length === 1) return bm[0];
    var sm = keys.filter(function (k) { return k.toLowerCase().split("/").pop().replace(/\.\w+$/, "") === stem; }); if (sm.length === 1) return sm[0];
    return null;
  }
  function remapKeys(obj, keys) {
    var out = {};
    Object.keys(obj || {}).forEach(function (k) { if (k.charAt(0) === "_") { out[k] = obj[k]; return; } var r = resolveKey(k, keys); if (r) out[r] = obj[k]; });
    return out;
  }

  var VISION_SYSTEM = [
    "Bạn là trợ lý phân tích footage cho editor Nexys Media. Đầu vào: bảng media, transcript có timestamp, contact sheet",
    "(mỗi hàng = 1 clip, tên file in trên hàng, 4 khung ở 10/35/60/85% thời lượng).",
    "Nhiệm vụ: mô tả từng clip thật chính xác để người viết kịch bản (không xem được hình) dựng được video.",
    "Với mỗi clip: summary (1 câu: ai, làm gì, ở đâu), people (mô tả nhận diện: tóc, trang phục — để phân biệt người/buổi quay),",
    "shot (cỡ cảnh/góc máy), quality (lỗi: rung, tay che, mất nét, tối…), speech (có thoại gì chính, tóm tắt), use_as (hook|dialogue|broll|live_screen|room_angle|skip).",
    "Gắn broll_tags cho clip KHÔNG thoại: tags = 2–4 cụm tiếng Việt mô tả hình; role 'master' cho bản ghi màn hình live, 'angle' cho góc quay thực tế cùng màn biểu diễn;",
    "use = [giây_bắt_đầu, giây_kết_thúc] nếu đầu/cuối clip hỏng.",
    "Nhận xét toàn bộ source: số người xuất hiện, có nhiều buổi quay/nhiều ứng viên khác nhau không, cảnh nào thiếu.",
    "TRẢ VỀ DUY NHẤT JSON: {\"clips\":{\"FILE\":{\"summary\":\"…\",\"people\":\"…\",\"shot\":\"…\",\"quality\":\"…\",\"speech\":\"…\",\"use_as\":\"…\"}},",
    "\"broll_tags\":{\"FILE\":{\"tags\":[\"…\"],\"role\":\"angle\",\"use\":[2,10]}},\"overview\":\"…\",\"warnings\":[\"…\"]}"
  ].join("\n");

  function analyzeSource(force) {
    if (!NX.S.src) return Promise.reject(new Error("chưa có folder source"));
    if (!fs.existsSync(work("media.json"))) return Promise.reject(new Error("source chưa phân tích — chạy bước 1"));
    var outp = work("source_ai.json");
    if (!force && fs.existsSync(outp)) return Promise.resolve(readJSON(outp, null));
    if (routeOf("vision").provider === "gemini" && NX.S.videoAnalysis !== false) return analyzeSourceVideo();
    // contact-sheet mode: ALL sheets, 3 sheets (≈30 clips) per request, each request only carries its own clips' rows;
    // results are MERGED into the previous analysis (a partial run never wipes descriptions of other clips)
    var prev = readJSON(outp, {}) || {}, med0 = readJSON(work("media.json"), {}), qc = readJSON(work("qc.json"), {});
    var order = [];
    try { order = fs.readFileSync(work("thumbs/_sig.txt"), "utf8").split("|").map(function (x) { return x.slice(0, x.lastIndexOf(":")); }); } catch (e) {}
    if (!order.length) order = Object.keys(med0);
    var all = [], d = work("thumbs");
    try { all = fs.readdirSync(d).filter(function (f) { return /^sheet\d+\.jpg$/.test(f); }).sort(function (x, y) { return +x.match(/\d+/)[0] - +y.match(/\d+/)[0]; }); } catch (e) {}
    var batches = [];
    for (var bi = 0; bi < all.length; bi += 3) batches.push(all.slice(bi, bi + 3));
    var merged = Object.assign({}, prev.clips || {}), tagsNew = {}, warns = [], overviews = [], hooks = [];
    return ensureKeyFor("vision").then(function () {
      NX.log("▶ AI phân tích source (" + routeLabel("vision") + ", " + all.length + " contact sheet · " + batches.length + " lượt)…", "#CCDC42");
      return batches.reduce(function (pr, bt, k) {
        return pr.then(function () {
          var clips = []; bt.forEach(function (sh) { var n = +sh.match(/\d+/)[0]; clips = clips.concat(order.slice(n * 10, n * 10 + 10)); });
          clips = clips.filter(function (f) { return med0[f]; });
          if (!clips.length) return;
          var ims = bt.map(function (sh) { return { mime: "image/jpeg", b64: fs.readFileSync(path.join(d, sh)).toString("base64"), name: sh }; });
          var rows = clips.map(function (f) {
            var v = med0[f], q = qcFix(qc[f], v.dur), bad = ["blur", "black", "freeze", "dark", "bright"].filter(function (x) { return (q[x] || []).length; });
            return f + " | " + v.w + "x" + v.h + " | " + (+v.dur || 0).toFixed(1) + "s | " + (v.created || "?") +
              (bad.length ? " | lỗi hình: " + bad.map(function (x) { return x + JSON.stringify(q[x].map(function (r) { return [r[0], r[1]]; })); }).join(" ") : "") + staticTxt(q) +
              "\n   thoại: " + (clipTranscript(f) || "(không thoại)");
          }).join("\n");
          var llm = sessionFor("vision");
          llm.userTurn("CÁC CLIP TRONG " + bt.length + " CONTACT SHEET NÀY (đã đo + transcript đã lọc câu rác):\n" + rows + "\n\nMô tả đúng các clip trên, trả JSON.", ims);
          NX.log("  • lượt " + (k + 1) + "/" + batches.length + ": " + clips.length + " clip");
          return llm.send(VISION_SYSTEM, null, { json: true, temperature: 0.2, maxTokens: 12000 }).then(function (r) {
            var j = LLM.parseJSON(r.text);
            var cm = remapKeys(j.clips || {}, clips.concat(Object.keys(med0))), tm = remapKeys(j.broll_tags || {}, Object.keys(med0));
            Object.keys(cm).forEach(function (f) { merged[f] = cm[f]; });
            Object.assign(tagsNew, tm);
            if (j.overview) overviews.push(j.overview);
            warns = warns.concat(j.warnings || []); hooks = hooks.concat(j.hook_candidates || []);
          });
        });
      }, Promise.resolve());
    }).then(function () {
      var j = { mode: "sheets", clips: merged, broll_tags: tagsNew, overview: overviews.join(" ") || prev.overview || "",
                warnings: warns, hook_candidates: hooks.length ? hooks : prev.hook_candidates, people_groups: prev.people_groups,
                _model: routeLabel("vision"), _at: stamp() };
      if (fs.existsSync(outp)) try { fs.copyFileSync(outp, outp + ".bak"); } catch (e) {}
      fs.writeFileSync(outp, JSON.stringify(j, null, 1), "utf8");
      var tp = work("broll_tags.json"), cur = readJSON(tp, {}), added = 0;
      Object.keys(j.broll_tags || {}).forEach(function (f) { if (!cur[f]) { cur[f] = j.broll_tags[f]; added++; } });
      if (added) { cur._source = (cur._source ? cur._source + " + " : "") + "AI " + j._model + " " + stamp(); fs.writeFileSync(tp, JSON.stringify(cur, null, 1), "utf8"); }
      NX.log("✔ Phân tích source xong: " + Object.keys(j.clips || {}).length + " clip" + (added ? " · +" + added + " tag B-roll" : "") +
             ((j.warnings || []).length ? "\n  ⚠ " + j.warnings.join("\n  ⚠ ") : ""));
      NX.refreshHints();
      return j;
    });
  }


  // ======================================================= STAGE 1b — VIDEO + AUDIO ANALYSIS (Gemini Files API)
  var VIDEO_SYSTEM = [
    "Bạn là trợ lý phân tích footage cho editor Nexys Media. Bạn XEM video (chuyển động) và NGHE âm thanh của từng clip đính kèm (proxy 360p, có tiếng).",
    "Mỗi video được đặt sau nhãn 'CLIP <tên file>'. Transcript Whisper kèm theo để bạn đối chiếu lời nói — KHÔNG bịa nội dung thoại khác transcript.",
    "Với MỖI clip trả về:",
    "- summary: 1 câu (ai, làm gì, ở đâu) · people: mô tả nhận diện người (tóc, trang phục) để phân biệt người/buổi quay",
    "- shot: cỡ cảnh/góc máy · camera: chuyển động máy (tĩnh, pan, handheld rung, zoom…)",
    "- audio: {speech: tóm tắt lời, music: có nhạc gì, noise: tiếng ồn/tạp âm, issues: vỡ tiếng/nhỏ/gió…}",
    "- speakers: [{t0,t1,who}] ai đang nói trong khoảng nào",
    "- moments: [{t0,t1,type,desc,score}] khoảnh khắc đáng dùng; type = hook|dialogue|reaction|action|broll|transition; score 1–5 độ đắt",
    "- best_broll: [{t0,t1,desc}] đoạn hình đẹp, ổn định để làm B-roll (ưu tiên clip không thoại)",
    "- quality_issues: [{t0,t1,issue}] rung mạnh, tay che, mất nét, lộ ekip, đen…",
    "- use_as: hook|dialogue|broll|live_screen|room_angle|skip · tags: 2–4 cụm tiếng Việt mô tả hình",
    "Quay dài liên tục bằng máy cố định / cảnh ít chuyển động (livestream, phỏng vấn tripod, màn hình) là footage BÌNH THƯỜNG, KHÔNG phải lỗi freeze;",
    "chỉ báo 'freeze' khi QC ghi freeze (khung hình lặp y hệt) hoặc bạn thấy hình đứng hẳn trong khi âm thanh/người vẫn tiếp diễn.",
    "Thời gian là SỐ GIÂY tính từ đầu clip (số thập phân), không dùng MM:SS.",
    "TRẢ VỀ DUY NHẤT JSON: {\"clips\":{\"<tên file gốc>\":{...}}}"
  ].join("\n");
  var OVERVIEW_SYSTEM = [
    "Từ mô tả từng clip (đã xem video), tổng hợp cho người viết kịch bản.",
    "Clip quay dài liên tục / máy cố định KHÔNG phải lỗi freeze — không cảnh báo chuyện đó. TRẢ VỀ DUY NHẤT JSON:",
    "{\"overview\":\"toàn cảnh source: nội dung, số người, các buổi quay\",\"people_groups\":[{\"desc\":\"…\",\"clips\":[\"…\"]}],",
    "\"warnings\":[\"cảnh báo: nhiều ứng viên khác nhau, thiếu cảnh, âm thanh lỗi…\"],",
    "\"hook_candidates\":[{\"file\":\"…\",\"t0\":0,\"t1\":0,\"why\":\"…\"}]}"
  ].join("\n");

  function clipTranscript(f) {
    var j = readJSON(work("transcripts/" + stemOf(f) + ".json"), { segments: [] });
    j.file = j.file || f;
    return segsFor(j, speechLabels()).map(function (s) { return s.start.toFixed(1) + "-" + s.end.toFixed(1) + " " + s.text; }).join(" | ");
  }
  function uploadProxies(key, clips) {
    var man = readJSON(work("proxy/manifest.json"), {}), cp = work("gemini_files.json"), cache = readJSON(cp, {}), out = {}, i = 0;
    var todo = clips.filter(function (f) { return man[f]; });
    return todo.reduce(function (pr, f) {
      return pr.then(function () {
        i++;
        var m = man[f], c = cache[f], pth = work("proxy/" + m.proxy);
        var fresh = c && c.key === m.key && c.expires && Date.parse(c.expires) - Date.now() > 2 * 3600 * 1000;
        var check = fresh ? LLM.geminiFile(key, c.name).then(function (st) { return st && st.state === "ACTIVE" ? st : null; }).catch(function () { return null; }) : Promise.resolve(null);
        return check.then(function (st) {
          if (st) { out[f] = { uri: c.uri, reused: true }; NX.log("  [" + i + "/" + todo.length + "] " + f + " — dùng lại file đã upload"); return; }
          NX.log("  [" + i + "/" + todo.length + "] upload " + f + " (" + (m.bytes / 1e6).toFixed(1) + " MB)…");
          var up0 = null;
          return LLM.geminiUpload(key, pth, "video/mp4", f).then(function (file) { up0 = file; return LLM.geminiWaitActive(key, file); }).then(function (file) {
            var exp = file.expirationTime || (up0 && up0.expirationTime) || new Date(Date.now() + 47 * 3600e3).toISOString();
            cache[f] = { key: m.key, name: up0.name || file.name, uri: up0.uri || file.uri, expires: exp };
            fs.writeFileSync(cp, JSON.stringify(cache, null, 1), "utf8");
            out[f] = { uri: cache[f].uri };
          });
        });
      });
    }, Promise.resolve()).then(function () { return out; });
  }
  function analyzeSourceVideo() {
    var media = readJSON(work("media.json"), {}), qc = readJSON(work("qc.json"), {}), outp = work("source_ai.json");
    var clips = Object.keys(media).sort(function (a, b) { return String(media[a].created || "").localeCompare(String(media[b].created || "")) || a.localeCompare(b); });
    var stabInfo = readJSON(work("shake.json"), {});
    var key, files, merged = {}, carry = { model: null, tried: null };
    return ensureKeyFor("vision").then(function () {
      key = routeOf("vision").key;
      NX.log("▶ Phân tích source bằng VIDEO + ÂM THANH (" + routeLabel("vision") + ")", "#CCDC42");
      return NX.run(["-m", "nxcore.proxy", NX.S.src], "Tạo proxy 360p cho AI");
    }).then(function () {
      NX.log("▶ Upload proxy lên Gemini Files API (lưu 48 giờ, dùng lại được)…", "#CCDC42");
      return uploadProxies(key, clips);
    }).then(function (up) {
      files = up;
      var have = clips.filter(function (f) { return files[f]; }), batches = [];
      for (var k = 0; k < have.length; k += 8) batches.push(have.slice(k, k + 8));   // API: ≤10 videos / request
      var llm = null;   // carry (outer) keeps the working model across batches (skip exhausted ones)
      return batches.reduce(function (pr, batch, bi) {
        return pr.then(function () {
          NX.log("▶ AI xem video nhóm " + (bi + 1) + "/" + batches.length + " (" + batch.length + " clip)…");
          llm = sessionFor("vision");
          if (carry.model) { llm.model = carry.model; llm._tried = carry.tried; }
          var rows = batch.map(function (f) {
            var m = media[f], q = qcFix(qc[f], (media[f] || {}).dur), bad = ["blur", "black", "freeze", "dark", "bright"].filter(function (x) { return (q[x] || []).length; });
            var sb = stabInfo[f] || {}, cp = sb.composition || {};
            return "CLIP " + f + " | " + (m.dur || 0).toFixed(1) + "s | tạo " + (m.created || "?") + " | loudness " + q.lufs + " LUFS, peak " + q.true_peak +
              (sb.stability !== undefined ? " | ổn định " + sb.stability + "/100, bố cục " + (cp.score || "?") + "/100" + (sb.usable ? ", đoạn dùng được " + JSON.stringify(sb.usable.slice(0, 4)) : "") : "") +
              (bad.length ? " | QC lỗi: " + bad.map(function (x) { return x + JSON.stringify(q[x].map(function (r) { return [r[0], r[1]]; })); }).join(" ") : "") +
              staticTxt(q) +
              "\n   transcript: " + (clipTranscript(f) || "(không thoại)");
          }).join("\n");
          var vids = batch.map(function (f) { return { uri: files[f].uri, mime: "video/mp4", label: "CLIP " + f + ":", fps: (media[f].dur || 0) <= 40 ? 2 : 1 }; });
          llm.userTurn("THÔNG TIN CÁC CLIP (đã đo + transcript):\n" + rows + "\n\nPhân tích từng clip theo yêu cầu, trả JSON.", null, vids);
          return llm.send(VIDEO_SYSTEM, null, { json: true, temperature: 0.2, maxTokens: 16000 }).then(function (r) {
            var j = LLM.parseJSON(r.text);
            Object.keys(j.clips || {}).forEach(function (name) {
              var f = resolveKey(name, batch) || resolveKey(name, clips);
              if (f) merged[f] = j.clips[name]; else NX.log("  ⚠ AI trả tên clip lạ: " + name, "#e0a82e");
            });
            carry.model = llm.model; carry.tried = llm._tried;
            NX.log("  ✔ nhóm " + (bi + 1) + ": " + Object.keys(j.clips || {}).length + " clip · model " + llm.model);
          });
        });
      }, Promise.resolve());
    }).then(function () {
      NX.log("▶ Tổng hợp toàn bộ source…");
      var llm = sessionFor("vision");
      if (carry.model) { llm.model = carry.model; llm._tried = carry.tried; }
      llm.userTurn(JSON.stringify(Object.keys(merged).map(function (f) {
        var c = merged[f]; return { file: f, created: (media[f] || {}).created, summary: c.summary, people: c.people, use_as: c.use_as, moments: (c.moments || []).slice(0, 5), audio: c.audio };
      })));
      return llm.send(OVERVIEW_SYSTEM, null, { json: true, temperature: 0.2, maxTokens: 6000 }).then(function (r) { return LLM.parseJSON(r.text); })
        .catch(function (e) { NX.log("⚠ Bỏ qua bước tổng hợp: " + e.message, "#e0a82e"); return { overview: "", warnings: [] }; });
    }).then(function (ov) {
      var prevClips = (readJSON(outp, {}) || {}).clips || {};           // keep clips a previous run described (never lose data)
      if (fs.existsSync(outp)) try { fs.copyFileSync(outp, outp + ".bak"); } catch (e) {}
      var j = { mode: "video", clips: Object.assign({}, prevClips, merged), overview: ov.overview, people_groups: ov.people_groups, warnings: ov.warnings || [], hook_candidates: ov.hook_candidates,
                _model: routeLabel("vision"), _at: stamp() };
      // b-roll tags from what the model actually saw (never overwrite editor's tags)
      var tp = work("broll_tags.json"), cur = readJSON(tp, {}), added = 0;
      Object.keys(merged).forEach(function (f) {
        var c = merged[f]; if (cur[f] || !c || c.use_as === "skip" || c.use_as === "dialogue" || c.use_as === "hook") return;
        var bb = (c.best_broll || [])[0], t = { tags: (c.tags || []).slice(0, 4) };
        if (c.use_as === "live_screen") t.role = "master"; else if (c.use_as === "room_angle") t.role = "angle";
        if (bb && bb.t1 > bb.t0) t.use = [Math.max(0, bb.t0 - 0.5), bb.t1 + 0.5];
        if (t.tags.length) { cur[f] = t; added++; }
      });
      if (added) { cur._source = (cur._source ? cur._source + " + " : "") + "AI video " + j._model + " " + stamp(); fs.writeFileSync(tp, JSON.stringify(cur, null, 1), "utf8"); }
      j.broll_tags_added = added;
      fs.writeFileSync(outp, JSON.stringify(j, null, 1), "utf8");
      NX.log("✔ Phân tích video xong: " + Object.keys(merged).length + "/" + clips.length + " clip" + (added ? " · +" + added + " tag B-roll (có đoạn dùng tốt nhất)" : "") +
             (j.warnings.length ? "\n  ⚠ " + j.warnings.join("\n  ⚠ ") : ""));
      NX.refreshHints();
      return j;
    });
  }

  // ---- source quality table (what the script may pin) + post-check of the "Nguồn:" lines
  function minQuality() {
    try { var pr = JSON.parse(fs.readFileSync(path.join(NX.cfgDir, "profile.json"), "utf8")); if (pr.rules && pr.rules.min_quality) return pr.rules.min_quality; } catch (e) {}
    return 45;
  }
  function sourceTable() {
    var media = readJSON(work("media.json"), {}), stab = readJSON(work("shake.json"), {}), qc = readJSON(work("qc.json"), {}), minQ = minQuality();
    var rows = {}, lines = [];
    Object.keys(media).sort().forEach(function (f) {
      var m = media[f], sb = stab[f] || {}, cp = sb.composition || {}, q = qc[f] || {};
      var us = sb.usable || [], sl = sb.usable_slowmo || [];
      var longest = us.reduce(function (a, r) { return Math.max(a, r[1] - r[0]); }, 0), longSl = sl.reduce(function (a, r) { return Math.max(a, r[1] - r[0]); }, 0);
      var qual = sb.quality, why = [];
      if (m.status && m.status !== "ok") why.push("lỗi file");
      if (qual !== undefined && qual < minQ) why.push("chất lượng " + qual + " < " + minQ);
      if (sb.usable && longest < 1.0 && longSl < 1.0) why.push("không có đoạn ổn định ≥1s");
      if ((q.black || []).length && (q.black || []).reduce(function (a, r) { return a + r[1] - r[0]; }, 0) > 0.8 * (m.dur || 1)) why.push("gần như đen");
      var st = why.length ? "LOẠI" : (qual !== undefined && qual < minQ + 10 ? "YẾU" : "ĐẠT");
      if (!why.length && sb.usable && longest < 1.0 && longSl >= 1.0) st = "ĐẠT(slow-mo)";
      rows[f] = { status: st, quality: qual, why: why.join(", ") };
      lines.push(f + " | " + (m.dur || 0).toFixed(1) + "s | " + (m.ratio || "") + " " + (m.orient || "") + " " + (m.h || "") + "p" + (m.fps ? " " + Math.round(m.fps) + "fps" : "") +
        " | chất lượng " + (qual === undefined ? "?" : qual) + " (ổn định " + (sb.stability === undefined ? "?" : sb.stability) + ", bố cục " + (cp.score === undefined ? "?" : cp.score) + ")" +
        " | dùng được " + JSON.stringify(us.slice(0, 4)) + (sl.length ? " | slowmo " + JSON.stringify(sl.slice(0, 3)) : "") +
        " | " + st + (why.length ? " (" + why.join(", ") + ")" : ""));
    });
    return { rows: rows, text: lines.join("\n"), minQ: minQ };
  }
  // ======================================================= LONG-FORM (10–30 min) — digest + outline + per-chapter, minimal tokens
  function briefSecs() {
    var t = String((NX.S.brief || {}).duration || "").toLowerCase().replace(",", "."), m, v = 0;
    if ((m = t.match(/(\d+(?:\.\d+)?)\s*(?:[-–~]\s*(\d+(?:\.\d+)?))?\s*(phút|ph|p\b|min|m\b|')/))) v = (m[2] ? (+m[1] + +m[2]) / 2 : +m[1]) * 60;
    else if ((m = t.match(/(\d+(?:\.\d+)?)\s*(giây|s\b|sec|")/))) v = +m[1];
    else if ((m = t.match(/(\d+):(\d{2})/))) v = +m[1] * 60 + +m[2];
    return v;
  }
  // shooting-time order (folder choice / vlog preset) → a rule line for the writer, + the clip order when asked
  function chronoBrief(withList) {
    var on = NX.chronoOn ? NX.chronoOn() : false;
    if (!on) return "MẠCH THỜI GIAN QUAY: TẮT — sắp xếp theo ý / chủ đề, không cần theo giờ quay.";
    var txt = "MẠCH THỜI GIAN QUAY: BẬT — câu chuyện đi theo trình tự giờ quay (sáng → tối, ngày trước → ngày sau); chỉ Hook được lấy khoảnh khắc từ phía sau; " +
      "không chèn cảnh của buổi/ngày khác vào giữa một chặng trừ khi có chủ đích (ghi rõ trong 'Lưu ý edit').";
    if (!withList) return txt;
    var media = readJSON(work("media.json"), {}), rows = [];
    Object.keys(media).forEach(function (f) {
      var m = media[f] || {}, t = +m.created_ts || 0, mm = /DJI_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/.exec(f);
      if (mm) {                                              // DJI file name = true start (metadata may drift)
        var d = new Date(+mm[1], +mm[2] - 1, +mm[3], +mm[4], +mm[5], +mm[6]).getTime() / 1000;
        if (!t || Math.abs(t - d) > 120) t = d;
      }
      if (t) rows.push([t, f]);
    });
    rows.sort(function (a, b) { return a[0] - b[0]; });
    var p2 = function (x) { return ("0" + x).slice(-2); }, day = "", out = [];
    rows.forEach(function (r) {
      var d = new Date(r[0] * 1000), dd = p2(d.getDate()) + "/" + p2(d.getMonth() + 1);
      if (dd !== day) { day = dd; out.push("## NGÀY " + dd); }
      out.push(p2(d.getHours()) + ":" + p2(d.getMinutes()) + " " + r[1]);
    });
    return txt + "\nTHỨ TỰ QUAY (giờ bắt đầu · file):\n" + out.join("\n");
  }
  function tok(s) { return Math.round(String(s || "").length / 2.6); }
  function hashStr(s) { var h = 0; for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return (h >>> 0).toString(36); }
  function chapterDigest(txt, ids) {
    var want = {}, lines = txt.split("\n"), out = [], keep = false;
    ids.forEach(function (id) { want[String(id).split(".")[0]] = 1; });
    lines.forEach(function (l) {
      var m = l.match(/^([KC]\d+)\s/);
      if (m) keep = !!want[m[1]];
      else if (!/^\s+[KC]\d+\.\d+/.test(l)) keep = false;
      if (keep) out.push(l);
    });
    return out.join("\n");
  }
  function localModel() {
    if (NX.S.localDigest === false || !LLM.ollamaModels) return Promise.resolve(null);
    return LLM.ollamaModels().then(function (ms) {
      var ids = ms.map(function (m) { return m.id; });
      var pref = ["qwen3-vl:8b-instruct", "qwen2.5vl:7b", "gemma4:12b-it-qat", "gemma4:12b", "gemma3:12b"];   // Qwen first; plain qwen3-vl:8b thinks → empty answers
      return pref.filter(function (p) { return ids.indexOf(p) >= 0; })[0] ||
        ids.filter(function (i) { return /vl|vision|gemma[34]|llava|minicpm/i.test(i) && i !== "qwen3-vl:8b"; })[0] || ids[0] || null;
    }).catch(function () { return null; });
  }
  // ======================================================= SPEC SCRIPT — quy trình skill nx-script-from-probe (ghim nguồn)
  // AI chỉ chọn shot (clip + in/out + tốc độ, câu thoại theo ID + trích dẫn, VO/text); engine nxcore.specscript
  // dựng file NX: cắt thoại theo timestamp từng từ, kẹp b-roll vào đoạn dùng được, khớp thời lượng, check parser.
  var SPEC_GENRES = { vlog_travel: "Vlog du lịch / gia đình — theo giờ quay", event_recap: "Recap sự kiện — theo diễn biến",
    talking_head: "Talking-head / chia sẻ", interview_casting: "Phỏng vấn / casting", shortform_9x16: "Short-form 9:16 — theo cảm xúc", youtube_long: "YouTube dài — chương + giữ chân (talking head + demo)" };
  function specBrief() {
    var b = NX.S.brief || {}, fresh = b.specFor !== NX.S.src, chain = Promise.resolve();
    function q(key, question, opts, o) {
      chain = chain.then(function () {
        if (!fresh && b[key]) return;
        var list = opts.slice();
        if (b[key] && list.indexOf(b[key]) < 0) list.unshift(b[key]);
        return NX.ask(question, list, o || { text: true }).then(function (a) { b[key] = a == null ? "" : String(a).trim(); });
      });
    }
    var media = readJSON(work("media.json"), {}), vert = 0, n = 0;
    Object.keys(media).forEach(function (f) { n++; if (/portrait|vertical|dọc|9:16/i.test((media[f] || {}).orient || "")) vert++; });
    var fmt = vert > n / 2 ? ["Dọc 9:16 · 60–90s", "Ngang 16:9 · 5–8 phút", "Ngang 16:9 · 10 phút"] : ["Ngang 16:9 · 5–8 phút", "Ngang 16:9 · 10 phút", "Ngang 16:9 · 2–3 phút", "Dọc 9:16 · 60–90s"];
    var gk = Object.keys(SPEC_GENRES), cur = NX.S.preset;
    if (cur && gk.indexOf(cur) > 0) { gk.splice(gk.indexOf(cur), 1); gk.unshift(cur); }
    q("duration", "Format & thời lượng?", fmt, { text: true, placeholder: "vd: 16:9 · 10 phút" });
    q("type", "Thể loại & mạch kể?", gk.map(function (k) { return SPEC_GENRES[k]; }));
    q("vo", "Voice off?", ["Không VO", "Có VO — ngôi 'mình / nhà mình', thưa (~40%)", "Có VO — người dẫn trung tính"]);
    q("cta", "CTA cuối video?", ["Không CTA", "Có CTA — mời đăng ký / theo dõi kênh"], { text: true, placeholder: "hoặc gõ nội dung CTA" });
    q("notes", "Tên nhân vật / tên kênh / điều cần tránh? (để trống nếu không có)", ["Không có"], { text: true, placeholder: "vd: Bé Đậu, kênh Nhà Mình…" });
    return chain.then(function () {
      var key = Object.keys(SPEC_GENRES).filter(function (k) { return SPEC_GENRES[k] === b.type; })[0];
      if (key && key !== NX.S.preset) {        // preset.json của folder có thể sai thể loại → theo brief
        var ps = $("presetSel");
        if (ps) { ps.value = key; ps.dispatchEvent(new Event("change")); } else { NX.S.preset = key; NX.save(); }
        NX.log("• Preset → " + key + " (theo thể loại đã chọn)");
      }
      if (/^không có$/i.test(b.notes || "")) b.notes = "";
      b.platform = /9:16|dọc/i.test(b.duration || "") ? "Dọc 9:16" : "Ngang 16:9";
      b.specFor = NX.S.src; NX.S.brief = b; NX.save();
      ["type", "duration", "platform", "notes"].forEach(function (k) { if ($("brief_" + k)) $("brief_" + k).value = b[k] || ""; });
    });
  }
  function specHasVO() { return !/^không/i.test(String((NX.S.brief || {}).vo || "không").trim()); }
  function specCTA() { var c = String((NX.S.brief || {}).cta || "").trim(); return /^không/i.test(c) ? "" : c; }
  function genreGuide(type) {        // style_guide + script_structure của preset ứng với thể loại → AI viết đúng khuôn thể loại
    try {
      var key = Object.keys(SPEC_GENRES).filter(function (g) { return SPEC_GENRES[g] === type; })[0];
      var pd = key && NX.presetData ? NX.presetData(key) : null;
      if (!pd || (!pd.style_guide && !pd.script_structure)) return "";
      var out = ["HƯỚNG DẪN THỂ LOẠI (" + (pd.name || key) + "):"];
      Object.keys(pd.script_structure || {}).forEach(function (k2) { out.push("- cấu trúc " + k2 + ": " + pd.script_structure[k2]); });
      Object.keys(pd.style_guide || {}).forEach(function (k2) { out.push("- " + k2 + ": " + pd.style_guide[k2]); });
      return "\n" + out.join("\n");
    } catch (e) { return ""; }
  }
  function specBriefText(secs) {
    var b = NX.S.brief || {};
    return "BRIEF: format/thời lượng = " + b.duration + " (ĐÍCH " + Math.round(secs) + "s); thể loại = " + b.type +
      "; voice off = " + (b.vo || "Không VO") + "; CTA = " + (specCTA() || "KHÔNG") + "; ghi chú = " + (b.notes || "—") + "\n" + chronoBrief(false) + genreGuide(b.type);
  }
  var SPEC_RULES = [
    "QUY TẮC (skill nx-script-from-probe — engine dựng Y NGUYÊN theo lựa chọn của bạn, không tự chọn lại clip):",
    "- Không bịa: tên người, con số, quan hệ nhân vật, địa danh chỉ dùng khi có trong digest/brief. Không chắc → không ghi.",
    "- Chỉ dùng clip ĐẠT / ĐẠT(slow-mo); khoảng in–out nằm trong 'dùng' của clip. Không dùng lại cùng đoạn hình.",
    "- Thoại rác ASR (\"cảm ơn các bạn đã theo dõi\", lặp \"đúng rồi đúng rồi\", đếm số, câu cụt) → KHÔNG dùng làm thoại.",
    "- Tốc độ: clip 50/60p → \"50%\" cho chuyển động đẹp; 100p+ → \"25%\" chỉ cho khoảnh khắc đắt; 24–30p → \"100%\"; clip rất dài làm nền → \"timelapse ×N\"; thoại luôn 100%.",
    "- Cấu trúc: hook 0–8s (khoảnh khắc đắt nhất, được flash-forward) → định vị → thân theo thời gian → cao trào 65–85% → outro hạ nhịp."
  ].join("\n");
  var SPEC_OUTLINE_SYSTEM = [
    "Bạn là đạo diễn dựng phim của Nexys Media. Đầu vào: BRIEF + DIGEST SOURCE (clip ID Kn xếp theo giờ quay, câu thoại ID Kn.m).",
    "Lập DÀN Ý: video < 2 phút → 3–5 chương; dài hơn → 6–12 chương. TỔNG dur_s PHẢI = thời lượng ĐÍCH.",
    "Mỗi chương: title (VIẾT HOA, ngắn, theo nội dung thật), goal (cảm xúc/câu chuyện), dur_s, ids = đủ clip để lấp dur_s (≈ dur_s/4 clip b-roll + các câu thoại Kn.m muốn dùng).",
    "Mạch thời gian BẬT → chương đi đúng trình tự quay; chỉ chương 1 (hook) được lấy khoảnh khắc từ phía sau.",
    SPEC_RULES,
    "TRẢ VỀ DUY NHẤT JSON: {\"title\":\"TIÊU ĐỀ VIDEO\",\"chapters\":[{\"title\":\"…\",\"goal\":\"…\",\"dur_s\":40,\"ids\":[\"K3\",\"K3.2\",\"K7\"]}],\"gaps\":[\"…\"],\"notes\":\"…\"}"
  ].join("\n");
  var SPEC_CHAPTER_SYSTEM = [
    "Bạn viết SHOT LIST cho MỘT chương. Engine Justin EDIT Suite dựng đúng thứ tự, đúng file, đúng khoảng, đúng tốc độ bạn ghi.",
    "Mỗi shot là MỘT trong hai loại:",
    " • B-roll: {\"id\":\"Kn\",\"a\":giây_vào,\"b\":giây_ra,\"sp\":\"50%\",\"d\":\"mô tả cái thấy trong khung\"} — độ dài trên timeline = (b−a) ÷ tốc độ; 2–6s/shot (shot đẹp tĩnh tới 8s).",
    " • Thoại: {\"utt\":\"Kn.m\",\"quote\":\"trích NGUYÊN VĂN đúng phần cần dùng\",\"d\":\"ai nói / bối cảnh\"} — chỉ trích phần có nghĩa (1–2 câu liền nhau); engine tự cắt theo timestamp từng từ.",
    "Tùy chọn trên shot bất kỳ: \"vo\" (chỉ khi brief có VO: ≤ 25 âm tiết, ~4 âm tiết/giây, dựa trên điều thật trong footage, không đè lên thoại/cao trào),",
    "  \"text\" (chữ nhỏ dưới khung, ≤ 6 từ), \"title\" (chữ lớn giữa khung). Không VO → dùng \"text\" thưa (≤ 1 text / 20s) cho thông tin mạch truyện: giờ, địa điểm, bước ngoặt.",
    "TỔNG thời lượng các shot ≈ dur_s của chương (engine co giãn b-roll ±, nhưng hãy chọn đủ hình). B-roll và thoại cùng clip không chồng khoảng.",
    SPEC_RULES,
    "TRẢ VỀ DUY NHẤT JSON: {\"shots\":[…],\"gaps\":[\"ý thiếu hình + lý do\"]}"
  ].join("\n");
  // ---- model local: digest nén cho bước DÀN Ý (digest đủ của 500 clip ≈ 37K token → 12B model viết tràn / hỏng JSON).
  // Gom clip liền giờ quay thành cụm "K1–K7 05:41–05:42 (7 clip): mô tả…"; bỏ đường dẫn, điểm, clip LOẠI.
  // Bước chọn shot từng chương vẫn nhận dữ liệu đầy đủ của các clip trong chương.
  var COMPACT_RULE = "DIGEST ĐANG Ở DẠNG NÉN theo CỤM clip liền giờ quay. Trong \"ids\" được ghi KHOẢNG cụm, ví dụ \"K12-K30\" (lấy đúng mốc đầu–cuối của cụm), " +
    "và câu thoại Kn.m muốn dùng. Không liệt kê từng id. Giữ JSON NGẮN.";
  function compactDigest(dtxt) {
    var out = [], grp = null, n = 0;
    function mins(t) { var m = /^(\d+):(\d+)/.exec(t || ""); return m ? +m[1] * 60 + +m[2] : null; }
    function flush() {
      if (!grp) return;
      var d = grp.desc, pick = d.length <= 3 ? d : [d[0], d[Math.floor(d.length / 2)], d[d.length - 1]];
      out.push(grp.a + (grp.b !== grp.a ? "–" + grp.b : "") + " " + grp.t0 + (grp.t1 !== grp.t0 ? "–" + grp.t1 : "") + " (" + grp.n + " clip): " +
        pick.map(function (x) { return x.length > 70 ? x.slice(0, 68) + "…" : x; }).join(" / ") +
        (grp.u.length ? " [thoại: " + grp.u.slice(0, 2).join("; ") + (grp.u.length > 2 ? "; +" + (grp.u.length - 2) : "") + "]" : ""));
      grp = null;
    }
    String(dtxt || "").split(/\r?\n/).forEach(function (l) {
      if (/^##\s/.test(l)) { flush(); out.push(l); return; }
      if (/^#\s/.test(l)) return;
      var u = /^\s+(K\d+\.\d+)\s+[\d.]+–[\d.]+\s+(.*)$/.exec(l);
      if (u) { if (grp) grp.u.push(u[1] + " " + u[2].replace(/\s*\(\d+ từ\)\s*$/, "").slice(0, 60)); return; }
      var m = /^(K\d+)\s+(\d+:\d+)\s+[^|]*\|\s*([\d.]+)s\s*\|\s*([^|]*)\|[^|]*\|\s*(.*)$/.exec(l);
      if (!m) return;
      if (/LOẠI/.test(m[4])) return;
      var t = mins(m[2]);
      if (grp && (t == null || grp.tm == null || t - grp.tm > 3 || grp.n >= 14)) flush();
      if (!grp) grp = { a: m[1], b: m[1], t0: m[2], t1: m[2], tm: t, n: 0, desc: [], u: [] };
      grp.b = m[1]; grp.t1 = m[2]; grp.tm = t; grp.n++; n++;
      grp.desc.push(m[5].replace(/\s*·\s*có tiếng nói nền\s*$/, ""));
    });
    flush();
    return "# DIGEST NÉN — " + n + " clip đạt, gom theo cụm giờ quay: KHOẢNG id · giờ · số clip: mô tả đầu / giữa / cuối cụm [thoại]\n" + out.join("\n");
  }
  function expandIds(D, ids, cap) {
    var all = Object.keys(D.clips || {}), out = [], seen = {};
    function add(x) { if (!seen[x]) { seen[x] = 1; out.push(x); } }
    (ids || []).forEach(function (id) {
      var r = /^K(\d+)\s*[-–—]\s*K?(\d+)$/.exec(String(id).trim());
      if (!r) { add(String(id).trim()); return; }
      for (var i = +r[1]; i <= +r[2]; i++) if (D.clips["K" + i]) add("K" + i);
    });
    var clips = out.filter(function (x) { return x.indexOf(".") < 0; }), utts = out.filter(function (x) { return x.indexOf(".") >= 0; });
    if (clips.length > cap) {                      // chương quá rộng → lấy mẫu đều theo giờ quay
      var step = clips.length / cap; clips = Array.apply(null, Array(cap)).map(function (_, i) { return clips[Math.floor(i * step)]; });
    }
    return clips.concat(utts);
  }
  function specChapterContext(D, dtxt, ids) {
    var files = {}, desc = readJSON(work("auto_desc.json"), {}) || {}, out = [chapterDigest(dtxt, ids) || "(không có clip)"];
    ids.forEach(function (id) { var c = D.clips[String(id).split(".")[0]]; if (c && c.file) files[c.file] = String(id).split(".")[0]; });
    var segs = [];
    Object.keys(files).forEach(function (f) {
      var c = ((desc.clips || {})[f]) || {}, s = c.segs || {};
      Object.keys(s).forEach(function (k) { if (s[k] && s[k].desc) segs.push(files[f] + " [" + k + "s] " + s[k].desc); });
    });
    if (segs.length) out.push("\nMÔ TẢ THEO ĐOẠN (AI xem hình):\n" + segs.join("\n").slice(0, 6000));
    var utts = [];
    Object.keys(D.utts || {}).forEach(function (uid) {
      var u = D.utts[uid];
      if (files[u.file]) utts.push(uid + " " + (+u.t0).toFixed(1) + "–" + (+u.t1).toFixed(1) + "s" + (u.take_tag ? " " + u.take_tag : "") + ": " + String(u.text || "").trim());
    });
    if (utts.length) out.push("\nTHOẠI NGUYÊN VĂN (để trích quote):\n" + utts.join("\n").slice(0, 9000));
    return out.join("\n");
  }
  /* the editor's approved style rules for these scopes (js/stylebook.js), "" when none */
  function styleRules(scopes) { return window.JESStyle ? window.JESStyle.promptText(scopes) : ""; }
  function specScript(opts) {
    opts = opts || {};
    var D, dtxt, outline, cache = readJSON(work("spec_cache.json"), {}), used = 0, secs, qtab, vo, cta;
    var scriptRules = styleRules(["kich_ban"]);       // part of the cache keys: a new rule writes a new outline
    function saveCache() { try { fs.writeFileSync(work("spec_cache.json"), JSON.stringify(cache), "utf8"); } catch (e) {} }
    return specBrief().then(function () {
      secs = briefSecs() || 90; qtab = sourceTable(); vo = specHasVO(); cta = specCTA();
      NX.log("▶ Kịch bản GHIM NGUỒN (~" + Math.round(secs) + "s · " + (vo ? "có VO" : "không VO") + " · " + (cta ? "có CTA" : "không CTA") +
        "): digest → dàn ý → shot list từng chương → engine dựng + kiểm tra", "#CCDC42");
      return localModel();
    }).then(function (lm) {
      var args = ["-m", "nxcore.digest", NX.S.src, "--min-quality", String(qtab.minQ)];
      if (lm) args.push("--ollama", lm, "--describe");
      return NX.run(args, "Digest source" + (lm ? " + AI local" : ""));
    }).then(function () {
      D = readJSON(work("digest.json"), null); dtxt = fs.readFileSync(work("digest.txt"), "utf8");
      if (!D) throw new Error("không tạo được digest");
      return ensureKeyFor("script");
    }).then(function () {
      var local = routeOf("script").provider === "ollama";
      var input = specBriefText(secs) + "\n\n" + (local ? compactDigest(dtxt) : dtxt), key = "o:" + hashStr(input + scriptRules);
      if (cache[key] && !opts.fresh) { NX.log("  • Dùng lại dàn ý đã có (0 token)"); return cache[key]; }
      used += tok(input) + tok(SPEC_OUTLINE_SYSTEM);
      NX.log("▶ AI lập dàn ý (" + routeLabel("script") + ", ~" + tok(input) + " token vào)…", "#CCDC42");
      var llm = sessionFor("script"); llm.userTurn(input);
      return llm.send(SPEC_OUTLINE_SYSTEM + (local ? "\n" + COMPACT_RULE : "") + scriptRules, null, { json: true, temperature: 0.3, maxTokens: 4000 }).then(function (r) {
        var j = LLM.parseJSON(r.text); used += tok(r.text);
        if (!j.chapters || !j.chapters.length) throw new Error("AI không lập được dàn ý");
        cache[key] = j; saveCache(); return j;
      });
    }).then(function (o) {
      o.chapters.forEach(function (c) { c.ids = expandIds(D, c.ids, 60); });
      outline = o;
      NX.log("  ✔ Dàn ý: " + o.chapters.map(function (c, i) { return (i + 1) + ". " + c.title + " (" + (c.dur_s || "?") + "s)"; }).join(" | "));
      var titles = o.chapters.map(function (c, i) { return (i + 1) + ". " + c.title + " — " + (c.goal || "") + " (" + (c.dur_s || "?") + "s)"; }).join("\n");
      // Chương độc lập → gọi song song (3 luồng) với model cloud; model local (1 GPU) chạy tuần tự.
      // Nối chương: song song dùng clip cuối của chương trước theo dàn ý; tuần tự dùng shot cuối thật.
      var last = o.chapters.length - 1, parts = new Array(o.chapters.length), local = routeOf("script").provider === "ollama";
      function writeChapter(c, i, prevTail) {
        var role = i === 0 ? "Chương 1 = HOOK: mở bằng khoảnh khắc đắt nhất (được lấy từ bất kỳ đâu)." :
          i === last ? (cta ? "Chương cuối: hạ nhịp, kết gọn — engine tự thêm end card CTA: " + cta :
            "Chương cuối: KHÔNG CTA — đặt 1 \"title\" kết ngắn (≤ 6 từ, gợi lại hook) trên shot cuối.") : "";
        var input = specBriefText(secs) + "\nDÀN Ý TOÀN VIDEO:\n" + titles + "\n\nVIẾT CHƯƠNG " + (i + 1) + ": " + c.title +
          " · mục tiêu: " + (c.goal || "") + " · THỜI LƯỢNG ĐÍCH " + (c.dur_s || 30) + "s" + (role ? "\n" + role : "") +
          (prevTail ? "\nShot cuối chương trước: " + prevTail : "") + "\n\nDỮ LIỆU CỦA CHƯƠNG:\n" + specChapterContext(D, dtxt, c.ids || []);
        var key = "c:" + hashStr(input + scriptRules);
        var done = cache[key] && !opts.fresh ? Promise.resolve(cache[key]) : (function () {
          used += tok(input) + tok(SPEC_CHAPTER_SYSTEM);
          NX.log("▶ Chương " + (i + 1) + "/" + o.chapters.length + ": " + c.title + " (~" + tok(input) + " token vào)…");
          var llm = sessionFor("script"); llm.userTurn(input);
          return llm.send(SPEC_CHAPTER_SYSTEM + scriptRules, null, { json: true, temperature: 0.4, maxTokens: 6000 }).then(function (r) {
            var j = LLM.parseJSON(r.text); used += tok(r.text);
            if (!j.shots || !j.shots.length) throw new Error("AI không viết được shot list chương " + (i + 1));
            cache[key] = j; saveCache(); return j;
          });
        })();
        return done.then(function (j) {
          var shots = j.shots.map(function (s) { s = Object.assign({}, s); if (!vo) delete s.vo; return s; });
          parts[i] = { name: c.title, dur_s: c.dur_s || 30, shots: shots, gaps: j.gaps || [] };
          var t = shots[shots.length - 1] || {};
          return (t.d || "") + (t.id ? " (" + t.id + ")" : t.utt ? " (" + t.utt + ")" : "");
        });
      }
      if (local) {
        return o.chapters.reduce(function (p, c, i) {
          return p.then(function (tail) { return writeChapter(c, i, tail); });
        }, Promise.resolve("")).then(function () { return parts; });
      }
      var next = 0;
      function worker() {
        if (next >= o.chapters.length) return Promise.resolve();
        var i = next++, c = o.chapters[i], pc = o.chapters[i - 1];
        var tail = pc ? "cuối chương \"" + pc.title + "\" (cụm " + ((pc.ids || []).filter(function (x) { return x.indexOf(".") < 0; }).slice(-1)[0] || "?") + ")" : "";
        return writeChapter(c, i, tail).then(worker);
      }
      return Promise.all([worker(), worker(), worker()]).then(function () { return parts; });
    }).then(function (parts) {
      var b = NX.S.brief, st = stamp();
      var spec = { title: outline.title || "VIDEO", target: secs, cta: cta,
        notes: ["Thời lượng: " + b.duration + " · preset " + (NX.S.preset || "?"), "Thể loại: " + b.type + " · VO: " + (b.vo || "không") + " · CTA: " + (cta || "không")]
          .concat(b.notes ? ["Nhân vật / kênh / tránh: " + b.notes] : []),
        chapters: parts };
      var specFile = work("spec_" + st + ".json"), md = work("auto_script_spec_" + st + ".md");
      fs.writeFileSync(specFile, JSON.stringify(spec, null, 1), "utf8");
      return NX.run(["-m", "nxcore.specscript", "build", specFile, "--src", NX.S.src, "--out", md, "--target", String(Math.round(secs))],
        "Engine dựng kịch bản ghim nguồn").then(function () { return { md: md, parts: parts }; });
    }).then(function (r) {
      var rep = readJSON(r.md.replace(/\.md$/, ".report.json"), {}) || {};
      var gaps = [];
      r.parts.forEach(function (p, i) { (p.gaps || []).forEach(function (g) { gaps.push("Chương " + (i + 1) + ": " + g); }); });
      var txt = fs.readFileSync(r.md, "utf8");
      if (gaps.length) { txt += "\nGhi chú: ⚠ AI báo thiếu hình —\n" + gaps.map(function (g) { return "Ghi chú: · " + g; }).join("\n") + "\n"; fs.writeFileSync(r.md, txt, "utf8"); }
      NX.setField("script", r.md.replace(/\\/g, "/"));
      if (NX.showScript) NX.showScript(txt, r.md);      // tab Kịch bản: the script editor shows it
      var warn = (rep.warnings || []).length + gaps.length;
      NX.log("✔ Kịch bản ghim nguồn: " + path.basename(r.md) + " · " + (rep.total ? Math.floor(rep.total / 60) + ":" + ("0" + Math.round(rep.total % 60)).slice(-2) : "?") +
        " / đích " + Math.floor(secs / 60) + ":" + ("0" + Math.round(secs % 60)).slice(-2) + " · " + (rep.shots || 0) + " shot · " + (rep.vo || 0) + " VO · " + (rep.text || 0) + " text · ~" + used + " token" +
        ((rep.fixes || []).length ? "\n  Engine tự sửa " + rep.fixes.length + " chỗ (cắt thoại theo từ, kẹp đoạn dùng được, tốc độ theo fps)" : "") +
        (warn ? "\n  ⚠ " + warn + " điểm cần xem: " + (rep.warnings || []).concat(gaps).slice(0, 4).join(" · ") : ""), warn ? "#e0a82e" : null);
      var L = rep.leftovers;
      if (L) NX.log("✔ Kho source chưa dùng: " + path.basename(L.md) + " · " + L.left_dialogue + " thoại + " + L.left_broll + " đoạn hình (" +
        Math.floor(L.left_s / 60) + ":" + ("0" + Math.round(L.left_s % 60)).slice(-2) + ") · kịch bản chính dùng " + L.coverage_pct + "% hình đạt chuẩn · " +
        L.untouched_clips + " clip ĐẠT chưa đụng tới\n  → Align file kho để có sequence riêng dựng tay: " + L.md.replace(/\\/g, "/") +
        ((L.weak_clips || []).length ? "\n  ⓘ " + L.weak_clips.length + " clip YẾU không đưa vào kho" : ""));
      if (opts.noConfirm) return;
      return NX.ask("Kịch bản ghim nguồn đã sẵn sàng (" + path.basename(r.md) + "). Dùng luôn để Align?", ["Dùng luôn", "Để tôi sửa trước"]).then(function (a) {
        if (a !== "Dùng luôn") throw new Error("đã dừng để sửa kịch bản — sửa trong ô preview, bấm 'Lưu kịch bản' rồi Align");
      });
    });
  }

  // ======================================================= KIỂM TRA HÌNH (tab Kịch bản): contact sheet 1 khung/shot → model vision
  var SPEC_VISION_SYSTEM = [
    "Bạn là phụ dựng kiểm tra kịch bản. Mỗi ô trong ảnh là 1 shot, nhãn vàng ở góc trên = mã shot (C<chương>.<shot>) + file @ giây.",
    "Danh sách kèm theo ghi mô tả mà kịch bản giả định cho từng shot. So từng ô với mô tả của nó.",
    "Chỉ báo shot có vấn đề THẬT nhìn thấy được: hình không khớp mô tả (sai hành động, sai người, sai bối cảnh), khung đen/nhòe/rung,",
    "vật cản lọt khung (đầu người, máy quay, tay), mắt nhắm/biểu cảm hỏng ở shot mở hoặc kết, chữ burn-in/logo lạ.",
    "Không báo shot ổn. Không đoán điều không thấy. Viết tiếng Việt, ngắn.",
    "TRẢ VỀ DUY NHẤT JSON: {\"issues\":[{\"shot\":\"C1.2\",\"problem\":\"…\",\"suggest_d\":\"mô tả đúng của khung (nếu mô tả sai)\",\"severity\":\"high|low\"}]}"
  ].join("\n");
  function specVision(md) {
    var dir = md.replace(/\.md$/i, "_sheets"), rp = md.replace(/\.md$/i, ".report.json"), issues = [];
    return NX.run(["-m", "nxcore.specscript", "sheets", md, "--src", NX.S.src, "--out", dir], "Contact sheet từng shot").then(function () {
      return ensureKeyFor("vision");
    }).then(function () {
      var sj = readJSON(path.join(dir, "sheets.json"), {}) || {}, sheets = sj.sheets || [], legend = sj.legend || [];
      if (!sheets.length) throw new Error("không tạo được contact sheet");
      NX.log("▶ AI kiểm tra hình (" + routeLabel("vision") + ") · " + sheets.length + " sheet · " + legend.length + " shot…", "#CCDC42");
      return sheets.reduce(function (pr, f, i) {
        return pr.then(function () {
          var b64 = fs.readFileSync(f).toString("base64"), lines = legend.slice(i * 12, i * 12 + 12);
          var llm = sessionFor("vision");
          llm.userTurn("Sheet " + (i + 1) + "/" + sheets.length + ". Mô tả theo kịch bản:\n" + lines.join("\n"), [{ mime: "image/jpeg", b64: b64 }]);
          return llm.send(SPEC_VISION_SYSTEM, null, { json: true, temperature: 0.1, maxTokens: 2500 }).then(function (r) {
            var j = {}; try { j = LLM.parseJSON(r.text) || {}; } catch (e) {}
            (j.issues || []).forEach(function (x) { if (x && x.shot) issues.push(x); });
            NX.log("  • sheet " + (i + 1) + ": " + ((j.issues || []).length ? (j.issues || []).length + " shot cần xem" : "ổn"));
          }).catch(function (e) { NX.log("  ⚠ sheet " + (i + 1) + ": " + e.message, "#e0a82e"); });
        });
      }, Promise.resolve());
    }).then(function () {
      var rep = readJSON(rp, {}) || {};
      rep.vision = { at: new Date().toISOString(), model: routeLabel("vision"), issues: issues, sheets_dir: dir };
      try { fs.writeFileSync(rp, JSON.stringify(rep, null, 1), "utf8"); } catch (e) {}
      return { issues: issues, dir: dir };
    });
  }

  function autoScript(opts) {
    opts = opts || {};
    if (!NX.S.src) return Promise.reject(new Error("chưa có folder source"));
    if (!fs.existsSync(work("media.json"))) return Promise.reject(new Error("source chưa phân tích — chạy bước 1"));
    return specScript(opts);   // quy trình ghim nguồn (kiểu viết cũ đã bỏ)
  }
  // ======================================================= AI ASSIST — agent (plan tools + Premiere tools) + history
  var chat = null, store = new window.JESChats.Store(path.join(NX.cfgDir, "chats"));
  var EXT_DIR = (function () { try { return new CSInterface().getSystemPath("extension"); } catch (e) { return ""; } })();
  var kb = new window.JESKB.KB(path.join(EXT_DIR, "knowledge"), path.join(NX.cfgDir, "knowledge"));
  var ASSIST_SYSTEM = [
    "Bạn là AI Assist của Justin EDIT Suite trong Adobe Premiere Pro (Nexys Media) — agent dựng phim cho editor chuyên nghiệp.",
    "Trả lời tiếng Việt, ngắn, thẳng, giữ thuật ngữ kỹ thuật tiếng Anh. Không khen xã giao.",
    "Hai nhóm công cụ:",
    "1) PLAN (rough cut theo kịch bản): get_state, get_transcript, read_script, swap_take, trim/remove/move_segment, highlight, set_field, set_brief,",
    "   run_step (analyze|align|build), generate_script, analyze_source. Thời gian trong plan = giây trong clip gốc; build tạo sequence mới.",
    "2) PREMIERE (pr_*): thao tác trực tiếp trên project/timeline đang mở.",
    "",
    "QUY TẮC HỎI LẠI: thiếu thông tin cần thiết để làm đúng → gọi ask_user với câu hỏi ngắn + 2–4 lựa chọn cụ thể, KHÔNG đoán.",
    "Không hỏi điều đã rõ hoặc có mặc định hợp lý. Việc tốn thời gian/credit (build, tạo kịch bản, export) mà editor chưa yêu cầu rõ → xác nhận trước.",
    "Làm xong: tóm tắt ngắn những gì đã thay đổi (và bản backup nếu có).",
    "",
    "KIẾN THỨC DỰNG (kb_search): câu hỏi kỹ thuật edit/Premiere, hoặc trước thao tác chưa chắc cách làm → kb_search trước, trả lời theo đó:",
    "cách làm trong Premiere (menu + phím tắt Windows) và nếu làm được bằng tool thì đề xuất/làm luôn. Không bịa phím tắt/menu ngoài kb.",
    "ÂM THANH: sound_info xem thư viện; sound_design xem trước (place=false) rồi đặt (place=true). Mức SFX tùy nội dung: editor chưa nói thì để trống density (tool tự hỏi).",
    "Nhạc AI (Higgsfield) panel không tạo trực tiếp — gợi ý editor nhờ Claude Cowork tạo và lưu vào thư mục nhạc, kèm prompt mô tả (mood, BPM, độ dài, cấu trúc).",
    "Editor dặn quy tắc riêng (house style, yêu cầu khách, lỗi hay gặp) → kb_note để nhớ cho lần sau. Mục trong kb:",
    "__KB__",
    "",
    "__PR__"
  ].join("\n").replace("__PR__", PR.SYSTEM).replace("__KB__", kb.index());

  var PLAN_TOOLS = [
    { name: "ask_user", description: "Hỏi editor khi thiếu thông tin. Hiện nút lựa chọn trong panel, trả về câu trả lời.",
      parameters: { type: "object", properties: { question: { type: "string" }, options: { type: "array", items: { type: "string" } } }, required: ["question"] } },
    { name: "get_state", description: "Trạng thái hiện tại: cài đặt, brief, danh sách đoạn của plan (index, cảnh, clip, in/out, nội dung, điểm, take thay thế), highlight, câu thiếu, B-roll cần tìm.",
      parameters: { type: "object", properties: {} } },
    { name: "get_transcript", description: "Transcript có timestamp của 1 clip (hoặc tất cả nếu bỏ trống).",
      parameters: { type: "object", properties: { file: { type: "string" } } } },
    { name: "read_script", description: "Đọc nội dung kịch bản đang dùng.", parameters: { type: "object", properties: {} } },
    { name: "swap_take", description: "Đổi take cho đoạn bằng take thay thế thứ candidate (0 = take thay thế đầu tiên trong get_state).",
      parameters: { type: "object", properties: { segment: { type: "integer" }, candidate: { type: "integer" } }, required: ["segment", "candidate"] } },
    { name: "trim_segment", description: "Đặt lại in/out (giây trong clip gốc) cho một đoạn.",
      parameters: { type: "object", properties: { segment: { type: "integer" }, in: { type: "number" }, out: { type: "number" } }, required: ["segment", "in", "out"] } },
    { name: "remove_segment", description: "Xóa một đoạn khỏi plan.", parameters: { type: "object", properties: { segment: { type: "integer" } }, required: ["segment"] } },
    { name: "move_segment", description: "Di chuyển đoạn tới vị trí mới (index).",
      parameters: { type: "object", properties: { segment: { type: "integer" }, to: { type: "integer" } }, required: ["segment", "to"] } },
    { name: "set_highlight", description: "Sửa highlight thứ index (text và/hoặc style: pink|yellow|black|red).",
      parameters: { type: "object", properties: { index: { type: "integer" }, text: { type: "string" }, style: { type: "string" } }, required: ["index"] } },
    { name: "add_highlight", description: "Thêm highlight vào đoạn: match = cụm từ đúng như transcript để canh thời điểm.",
      parameters: { type: "object", properties: { segment: { type: "integer" }, match: { type: "string" }, text: { type: "string" }, style: { type: "string" } }, required: ["segment", "match", "text"] } },
    { name: "remove_highlight", description: "Xóa highlight thứ index.", parameters: { type: "object", properties: { index: { type: "integer" } }, required: ["index"] } },
    { name: "set_field", description: "Đổi cài đặt: src | script | name | keywords.",
      parameters: { type: "object", properties: { field: { type: "string" }, value: { type: "string" } }, required: ["field", "value"] } },
    { name: "set_brief", description: "Cập nhật brief cho AI tạo kịch bản.",
      parameters: { type: "object", properties: { type: { type: "string" }, duration: { type: "string" }, platform: { type: "string" }, notes: { type: "string" } } } },
    { name: "run_step", description: "Chạy bước pipeline: analyze | align | build (build tạo sequence mới trong Premiere).",
      parameters: { type: "object", properties: { step: { type: "string", enum: ["analyze", "align", "build"] } }, required: ["step"] } },
    { name: "generate_script", description: "AI tự tạo kịch bản từ source (dùng brief; tự hỏi thêm nếu thiếu).", parameters: { type: "object", properties: {} } }
  ];

  PLAN_TOOLS.push({ name: "kb_search", description: "Tra kiến thức dựng Premiere (kỹ thuật cơ bản/nâng cao, phím tắt, màu, âm thanh, multicam, export, QC, cách map sang tool, ghi chú của editor). query = từ khóa hoặc id mục.",
    parameters: { type: "object", properties: { query: { type: "string" }, limit: { type: "integer" } }, required: ["query"] } });
  PLAN_TOOLS.push({ name: "kb_note", description: "Lưu ghi chú/quy tắc riêng của editor vào kb (house style, yêu cầu khách hàng, cách sửa lỗi đã dùng). Trùng title thì ghi đè.",
    parameters: { type: "object", properties: { title: { type: "string" }, body: { type: "string" }, tags: { type: "array", items: { type: "string" } } }, required: ["title", "body"] } });
  PLAN_TOOLS.push({ name: "sound_info", description: "Thư viện âm thanh: số SFX theo loại (whoosh/riser/hit/pop/ambience…) và danh sách nhạc đã quét (BPM, mood, độ dài, đoạn).",
    parameters: { type: "object", properties: {} } });
  PLAN_TOOLS.push({ name: "sound_design", description: "Nhạc + SFX cho sequence đang mở: phân tích đoạn nhạc (intro/build/drop/outro), marker đoạn, whoosh ở điểm cắt, riser+hit vào drop, pop khi hiện chữ, ambience, ducking nhạc dưới thoại. place=false chỉ xem trước; place=true backup rồi đặt vào timeline. density light|medium|heavy (trống = hỏi editor), style clean|cinematic|tech|fun|hard, music = đường dẫn file | 'timeline' (nhạc đã có trên timeline) | '' (không thêm nhạc).",
    parameters: { type: "object", properties: { density: { type: "string", enum: ["light", "medium", "heavy"] }, style: { type: "string" }, music: { type: "string" },
      layers: { type: "array", items: { type: "string", enum: ["whoosh", "riser", "hit", "pop", "ambience"] } }, place: { type: "boolean" } } } });
  PLAN_TOOLS.push({ name: "motion_graphic", description: "Đồ họa động nền trong suốt (ProRes 4444) đặt lên sequence đang mở tại playhead hoặc giây 'at'. " +
      "template + fields: lower_third {name, role, pos bl|bc|tl} · logo_pop {logo = đường dẫn PNG, caption, pos c|tr|br} · counter {value, prefix, suffix, label, pos c|bl} · " +
      "callout {text, x %, y % của điểm cần chỉ} · title_card {kicker, title, dim on|off} · subscribe {text, done, sub, pos bc|c|br}. dur = giây; track = 2..5 (V3..V6, mặc định V4).",
    parameters: { type: "object", properties: { template: { type: "string", enum: ["lower_third", "logo_pop", "counter", "callout", "title_card", "subscribe"] },
      fields: { type: "object" }, at: { type: "number" }, dur: { type: "number" }, track: { type: "integer" } }, required: ["template"] } });
  PLAN_TOOLS.push({ name: "analyze_source", description: "AI xem contact sheet + transcript để mô tả từng clip, gắn tag B-roll, phát hiện nhiều người/buổi quay. force=true để làm lại.",
    parameters: { type: "object", properties: { force: { type: "boolean" } } } });
  var TOOLS = PLAN_TOOLS.concat(PR.TOOLS);

  function planOrErr() { var P = NX.getPlan(); if (!P) throw new Error("chưa có plan — chạy Align trước"); return P; }
  function reindex(P, fn) { // fn(oldIndex) -> newIndex | -1 (removed)
    P.highlights = (P.highlights || []).filter(function (h) { var n = fn(h.seg); if (n < 0) return false; h.seg = n; return true; });
    (P.overlays || []).forEach(function (o) { var n = fn(o.anchor); o.anchor = n < 0 ? o.anchor : n; });
  }
  function stateJSON() {
    var P = NX.getPlan(), S = NX.S;
    var st = { settings: { src: S.src, script: S.script, name: NX.currentName(), keywords: S.keywords, mode: S.scriptMode },
               brief: S.brief, plan: null };
    if (P) st.plan = {
      name: P.name, duration_s: +P.segments.reduce(function (a, s) { return a + s.out - s.in; }, 0).toFixed(1),
      segments: P.segments.map(function (s, i) {
        return { i: i, scene: s.scene, file: s.file, in: s.in, out: s.out, label: s.label, score: s.score,
                 flags: (s.flags || []).slice(0, 3), alternatives: (s.candidates || []).filter(function (c) {
                   return !(c.file === s.file && Math.abs(c.in - s.in) < 0.3); }).map(function (c) { return c.file + "@" + c.in + "-" + c.out + " s=" + c.score; }) };
      }),
      highlights: (P.highlights || []).map(function (h, i) { return { i: i, seg: h.seg, text: h.text, style: h.style, match: h.match }; }),
      missing: P.missing, broll_requests: P.broll_requests, notes: P.report_notes
    };
    return st;
  }


  var turnState = null;
  function execTool(c) {
    var a = c.args || {}, P;
    if (PR.isPremiere(c.name)) {
      return PR.exec(NX, c.name, a, { ask: function (q, o) { return askInline(q, o); }, note: function (t) { addMsg("sys", t); }, state: turnState });
    }
    // async failures become the tool result (the agent can explain / retry) instead of aborting the whole turn
    var wrap = function (p) { return Promise.resolve(p).then(function (r) { return { result: r }; }, function (e) { return { result: "lỗi: " + (e && e.message || e) }; }); };
    // long jobs (python / GPU / timeline) hold the panel-wide lock like the buttons do: clean cancel state,
    // progress hidden at the end, refused while a Build / Review edit / sound / voice job runs
    var job = function (label, fn) { return NX.exclusive ? NX.exclusive("agent", "AI agent: " + label, fn) : Promise.resolve().then(fn); };
    try {
      switch (c.name) {
        case "ask_user": return wrap(askInline(a.question, a.options || []));
        case "sound_info": {
          var si = {}; try { si.sfx = JSON.parse(fs.readFileSync(path.join(NX.cfgDir, "sound", "sfx_index.json"), "utf8")); si.sfx = { count: si.sfx.count, roles: si.sfx.roles, roots: si.sfx.roots }; } catch (e) { si.sfx = "chưa quét thư viện SFX"; }
          try { var mi = JSON.parse(fs.readFileSync(path.join(NX.cfgDir, "sound", "music_index.json"), "utf8"));
                si.music = Object.keys(mi).map(function (f) { var m = mi[f]; return { file: f, bpm: m.bpm, mood: m.mood, dur: Math.round(m.duration), sections: m.sections.map(function (x) { return x.label; }).join(">") }; }); } catch (e) { si.music = []; }
          si.current = { music: NX.S.sndMusic || "", density: NX.S.sndDensity || "ask", style: NX.S.sndStyle || "auto" };
          return wrap(si); }
        case "sound_design": {
          var mopt = a.music === "timeline" ? "__timeline__" : a.music;
          var ask0 = a.density ? Promise.resolve(a.density) : askInline("Mức SFX cho video này?", ["Nhẹ — chỉ đổi đoạn nhạc + drop", "Vừa — whoosh một phần điểm cắt + pop chữ", "Dày — mọi điểm cắt, kiểu TikTok"])
            .then(function (x) { return /^Nhẹ/.test(x) ? "light" : /^Dày/.test(x) ? "heavy" : "medium"; });
          return wrap(ask0.then(function (dens) {
            return job("sound design", function () {
              return window.JESSound.design({ density: dens, style: a.style, music: mopt, layers: a.layers }).then(function (R) {
                var brief = { density: R.density, style: R.style, cuts: R.cuts, stats: R.stats, markers: R.markers.map(function (m) { return m.t + "s " + m.name; }),
                              music: R.music, first_events: R.sfx.slice(0, 25).map(function (e) { return e.peak + "s " + e.role + " " + e.name + " (" + e.reason + ")"; }) };
                if (!a.place) return brief;
                return window.JESSound.place(R).then(function (sum) { brief.placed = sum; return brief; });
              });
            });
          })); }
        case "motion_graphic": {
          if (!window.JESMotion) return wrap("lỗi: module đồ họa chưa tải");
          return wrap(job("đồ họa " + a.template, function () {
            return window.JESMotion.place({ template: a.template, fields: a.fields || {}, at: a.at, dur: a.dur, track: a.track, noLock: true });
          })); }
        case "kb_search": { var hits = kb.search(a.query, Math.min(a.limit || 3, 6));
          return wrap(hits.length ? hits : { none: true, hint: "Không có mục phù hợp — thử từ khóa khác. Mục có sẵn:\n" + kb.index() }); }
        case "kb_note": { var nid = kb.note(a.title, a.body, a.tags || []); addMsg("sys", "📝 Đã lưu vào kiến thức: " + a.title);
          return wrap({ saved: nid }); }
        case "analyze_source":
          return wrap(job("phân tích source", function () { return analyzeSource(!!a.force); })
            .then(function (j) { return { overview: j.overview, warnings: j.warnings, clips: j.clips }; }));
        case "generate_script":
          return wrap(job("tạo kịch bản", function () {
            NX.S.scriptMode = "auto"; NX.save(); NX.syncMode();
            return autoScript({ noConfirm: true });
          }).then(function () { return "ok: kịch bản " + NX.S.script; }));
          case "get_state": return wrap(stateJSON());
          case "get_transcript":
            if (!a.file) return wrap(transcripts(40000));
            var j = readJSON(work("transcripts/" + stemOf(resolveKey(a.file, Object.keys(readJSON(work("media.json"), {}))) || a.file) + ".json"), null);
            return wrap(j ? j.segments.map(function (s) { return s.start + "-" + s.end + " " + s.text; }).join("\n") : "không có transcript cho " + a.file);
          case "read_script":
            return wrap(NX.S.script && fs.existsSync(NX.S.script) ? fs.readFileSync(NX.S.script, "utf8").slice(0, 30000) : "chưa có kịch bản");
          case "swap_take":
            P = planOrErr(); NX.swapTake(a.segment, a.candidate); return wrap("ok: đoạn " + a.segment + " → " + P.segments[a.segment].file + " " + P.segments[a.segment].in + "s");
          case "trim_segment":
            P = planOrErr(); var s = P.segments[a.segment]; if (!s) throw new Error("không có đoạn " + a.segment);
            s.in = +a.in; s.out = +a.out; s.flags = (s.flags || []).concat(["AI trim"]); NX.savePlan();
            return wrap("ok: đoạn " + a.segment + " = " + s.in + "–" + s.out);
          case "remove_segment":
            P = planOrErr(); if (!P.segments[a.segment]) throw new Error("không có đoạn " + a.segment);
            P.segments.splice(a.segment, 1); reindex(P, function (k) { return k === a.segment ? -1 : (k > a.segment ? k - 1 : k); }); NX.savePlan();
            return wrap("ok: đã xóa đoạn " + a.segment);
          case "move_segment":
            P = planOrErr(); var from = a.segment, to = Math.max(0, Math.min(P.segments.length - 1, a.to));
            var seg = P.segments.splice(from, 1)[0]; P.segments.splice(to, 0, seg);
            reindex(P, function (k) { if (k === from) return to; if (from < to && k > from && k <= to) return k - 1; if (to < from && k >= to && k < from) return k + 1; return k; });
            NX.savePlan(); return wrap("ok: đoạn " + from + " → vị trí " + to);
          case "set_highlight":
            P = planOrErr(); var h = (P.highlights || [])[a.index]; if (!h) throw new Error("không có highlight " + a.index);
            if (a.text) h.text = a.text; if (a.style) h.style = a.style; NX.savePlan(); return wrap("ok");
          case "add_highlight":
            P = planOrErr(); P.highlights = P.highlights || [];
            P.highlights.push({ seg: a.segment, match: a.match, text: a.text, style: a.style || "pink", dur: 1.6 }); NX.savePlan();
            return wrap("ok: highlight #" + (P.highlights.length - 1));
          case "remove_highlight":
            P = planOrErr(); P.highlights.splice(a.index, 1); NX.savePlan(); return wrap("ok");
          case "set_field":
            if (["src", "script", "name", "keywords"].indexOf(a.field) < 0) throw new Error("field không hợp lệ");
            NX.setField(a.field, a.value); return wrap("ok");
          case "set_brief":
            NX.S.brief = Object.assign({}, NX.S.brief || {}, a); NX.save();
            ["type", "duration", "platform", "notes"].forEach(function (k) { if ($("brief_" + k)) $("brief_" + k).value = NX.S.brief[k] || ""; });
            return wrap("ok");
          case "run_step":
            if (["analyze", "align", "build"].indexOf(a.step) < 0) throw new Error("step phải là analyze | align | build");
            addMsg("sys", "▶ chạy " + a.step + "… (xem log ở tab Chạy)");
            return wrap(job(a.step, function () { return NX[a.step](); }).then(function () { return "ok: " + a.step + " xong"; }));
      }
      return wrap("tool không tồn tại: " + c.name);
    } catch (e) { return wrap("lỗi: " + e.message); }
  }

  // ---- chat UI + history
  function renderMsg(role, text) {
    var el = document.createElement("div"); el.className = "msg " + role; el.textContent = text;
    $("chatLog").appendChild(el); $("chatLog").scrollTop = $("chatLog").scrollHeight; return el;
  }
  function addMsg(role, text, transient) {
    var el = renderMsg(role, text);
    if (!transient) store.add(role, text);
    return el;
  }
  function askInline(question, options) {
    return new Promise(function (resolve) {
      var box = addMsg("ask", question);
      var wrap = document.createElement("div"); wrap.className = "opts";
      (options || []).forEach(function (o) {
        var b = document.createElement("button"); b.className = "btn opt"; b.textContent = o;
        b.onclick = function () { finish(o); }; wrap.appendChild(b);
      });
      box.appendChild(wrap);
      pendingAnswer = finish;
      $("chatInput").placeholder = "Trả lời câu hỏi hoặc bấm lựa chọn…";
      function finish(v) {
        pendingAnswer = null; $("chatInput").placeholder = "Hỏi hoặc giao việc cho AI…";
        wrap.querySelectorAll("button").forEach(function (b) { b.disabled = true; if (b.textContent === v) b.classList.add("chosen"); });
        addMsg("user", v); resolve(v);
      }
    });
  }
  var pendingAnswer = null, thinking = false, stopRequested = false;

  function refreshChatList() {
    var sel = $("chatList"), items = store.list(), cur = store.cur && store.cur.id;
    sel.innerHTML = '<option value="">＋ Hội thoại mới</option>' + items.map(function (c) {
      var d = new Date(c.updated || 0), when = ("0" + d.getDate()).slice(-2) + "/" + ("0" + (d.getMonth() + 1)).slice(-2) + " " + ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
      return '<option value="' + c.id + '"' + (c.id === cur ? " selected" : "") + ">" + NX.esc(c.title) + " · " + when + "</option>";
    }).join("");
    if (!cur) sel.value = "";
  }
  function openChat(id) {
    chat = null; $("chatLog").innerHTML = "";
    if (!id) { store.cur = null; renderMsg("sys", "Hội thoại mới · agent: " + routeLabel("chat") + " · tự chủ: " + ({ ask: "hỏi trước mỗi thay đổi", backup: "tự làm + backup sequence", auto: "tự làm" }[NX.S.autonomy || "backup"])); }
    else {
      var c = store.open(id);
      (c && c.messages || []).forEach(function (m) { renderMsg(m.role, m.text); });
      renderMsg("sys", "↺ Tiếp tục hội thoại (" + (c ? c.messages.length : 0) + " tin) · agent: " + routeLabel("chat"));
    }
    NX.S.lastChat = id || ""; NX.save(); refreshChatList();
  }

  function send(text) {
    text = (text || "").trim(); if (!text) return;
    if (pendingAnswer) { $("chatInput").value = ""; return pendingAnswer(text); }
    if (thinking) return;
    $("chatInput").value = "";
    if (!store.cur) store.create({ provider: routeOf("chat").provider, model: routeOf("chat").model, src: NX.S.src });
    var isNew = !chat, replay = isNew ? store.replay(40) : [];
    addMsg("user", text); refreshChatList();
    thinking = true; stopRequested = false; $("chatSend").disabled = true; $("chatStop").style.display = "inline-block";
    turnState = { approvedAll: false, backedUp: false };
    var dots = renderMsg("sys", "AI đang xử lý…");
    ensureKeyFor("chat").then(function () {
      if (!chat) {
        chat = sessionFor("chat");
        replay.forEach(function (m) { if (chat.addText) chat.addText(m.role, m.text); });   // resume from history
      }
      var st = stateJSON();
      chat.userTurn(text + "\n\n[trạng thái nhanh: plan=" + (st.plan ? st.plan.name + ", " + st.plan.segments.length + " đoạn, " + st.plan.duration_s + "s" : "chưa có") +
        "; source=" + (st.settings.src || "—") + "; kịch bản=" + (st.settings.script ? path.basename(st.settings.script) : "—") + "]");
      var n = 0;
      function loop() {
        if (stopRequested) { addMsg("sys", "■ Đã dừng theo yêu cầu"); return; }
        dots.textContent = "AI đang xử lý… (bước " + (n + 1) + ")";
        return chat.send(ASSIST_SYSTEM + styleRules(["dung", "am_thanh", "chu", "kich_ban"]), TOOLS, { temperature: 0.3 }).then(function (r) {
          if (r.text) addMsg("ai", r.text);
          if (!r.calls.length) return;
          if (n++ >= 30) { addMsg("sys", "⚠ Dừng sau 30 bước — gõ 'tiếp tục' nếu cần làm tiếp"); return; }
          var results = [];
          return r.calls.reduce(function (p, c) {
            return p.then(function () {
              if (stopRequested) { results.push({ id: c.id, name: c.name, result: "người dùng đã dừng" }); return; }
              if (!/^(ask_user|get_state|get_transcript|read_script|pr_get_)/.test(c.name))
                addMsg("sys", "⚙ " + c.name + " " + JSON.stringify(c.args).slice(0, 160));
              return execTool(c).then(function (res) { results.push({ id: c.id, name: c.name, result: res.result, images: res.images }); });
            });
          }, Promise.resolve()).then(function () { chat.toolResults(results); return loop(); });
        });
      }
      return loop();
    }).catch(function (e) { addMsg("err", "✖ " + e.message); })
      .then(function () { dots.remove(); thinking = false; $("chatSend").disabled = false; $("chatStop").style.display = "none"; store.save(); refreshChatList(); });
  }

  // ---- wire UI
  $("chatSend").onclick = function () { send($("chatInput").value); };
  $("chatInput").onkeydown = function (e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send($("chatInput").value); } };
  $("chatStop").onclick = function () { stopRequested = true; $("chatStop").textContent = "Đang dừng…"; setTimeout(function () { $("chatStop").textContent = "Dừng"; }, 1500); };
  $("chatList").onchange = function () { if (!thinking) openChat($("chatList").value); };
  $("chatNew").onclick = function () { if (!thinking) openChat(""); };
  $("chatDel").onclick = function () {
    if (thinking || !store.cur) return;
    NX.ask("Xóa hội thoại '" + store.cur.title + "'?", ["Xóa", "Hủy"]).then(function (a) { if (a === "Xóa") { store.remove(store.cur.id); openChat(""); } });
  };
  // quick chips: a chip with data-tab opens that tab instead of spending an LLM turn
  Array.prototype.forEach.call(document.querySelectorAll("#ai .chips .chip"), function (b) {
    b.onclick = function () {
      if (b.dataset.tab) { var nb = document.querySelector('nav button[data-tab="' + b.dataset.tab + '"]'); if (nb) { nb.click(); return; } }
      send(b.dataset.q);
    };
  });
  if ($("localDigest")) { $("localDigest").checked = NX.S.localDigest !== false;
    $("localDigest").onchange = function () { NX.S.localDigest = $("localDigest").checked; NX.save(); }; }
  if ($("videoAnalysis")) { $("videoAnalysis").checked = NX.S.videoAnalysis !== false;
    $("videoAnalysis").onchange = function () { NX.S.videoAnalysis = $("videoAnalysis").checked; NX.save(); }; }
  openChat(NX.S.lastChat && fs.existsSync(path.join(NX.cfgDir, "chats", NX.S.lastChat + ".json")) ? NX.S.lastChat : "");

  window.NXAI = { autoScript: autoScript, analyzeSource: analyzeSource, sourceTable: sourceTable, routeOf: routeOf,
    specScript: specScript, specVision: specVision, _compactDigest: compactDigest, specGenres: SPEC_GENRES, routeLabel: routeLabel };
})();
