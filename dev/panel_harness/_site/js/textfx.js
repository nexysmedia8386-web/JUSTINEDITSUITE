/* Justin EDIT Suite — tab Chữ = the full JIZURA page (js/jizura/app.html, MIT © hakoniwa) embedded in an iframe,
   plus a thin bridge bar:
     • Thoại / Highlight → JIZURA : timeline-timed cues of the build become LRC lyrics in the JIZURA editor
     • ▶ Đưa vào timeline        : the editor's current plan → transparent ProRes 4444 clips (one per group of cuts)
                                   placed on the chosen video track (time 0 = sequence start, or the playhead)
     • ✨ AI Style                 : đọc ý nghĩa từng câu → vai trò (hook / số liệu / bước / so sánh / cảnh báo / CTA…) → layout, hiệu ứng vào-ra, decor + từ khoá nhấn
     • JIZURA's own Save / Export buttons write into <out>/textfx/ (CEP has no browser downloads)
   JIZURA ships hooks for embedding hosts (J.ui = editor state, J.uiApi = replan / syncUI / seek / pause …). */
(function () {
  "use strict";
  var fs = require("fs"), path = require("path"), cp = require("child_process"), Buf = require("buffer").Buffer;
  var NX = window.NX, $ = function (id) { return document.getElementById(id); };
  function S() { var s = NX.S; s.textfx = s.textfx || {}; return s.textfx; }
  var W = null, J = null;                                   // iframe window + its JIZURA namespace

  /* ── Vietnamese: own language entry so every font key draws in a face with the stacked diacritics ── */
  var VI_CHARS = /[ăâđêôơưĂÂĐÊÔƠƯàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ]/i;
  function patchVietnamese(J) {
    if (J.LANG_FACES.vi) return;
    var F = function (family, weight, gf) { return { family: family, weight: weight, gf: gf }; };
    var BVP = "Be+Vietnam+Pro:wght@300;500;700;800;900", NSF = "Noto+Serif:wght@300;500;700;900";
    J.LANG_FACES.vi = {
      sans: F("Be Vietnam Pro", 500, BVP), serif: F("Noto Serif", 500, NSF),
      fbSans: '"Be Vietnam Pro","Segoe UI",Arial', fbSerif: '"Noto Serif","Times New Roman"',
      map: {
        gothic_black: F("Be Vietnam Pro", 900, BVP), gothic_bold: F("Be Vietnam Pro", 700, BVP), gothic_med: F("Be Vietnam Pro", 500, BVP),
        gothic_light: F("Be Vietnam Pro", 300, BVP), zenkaku: F("Be Vietnam Pro", 800, BVP), sansui: F("Be Vietnam Pro", 500, BVP),
        mincho_black: F("Noto Serif", 900, NSF), mincho_bold: F("Noto Serif", 700, NSF), mincho: F("Noto Serif", 500, NSF),
        mincho_light: F("Noto Serif", 300, NSF), tokumin: F("Playfair Display", 800, "Playfair+Display:wght@800"), shippori: F("Fraunces", 800, "Fraunces:wght@800"),
        dela: F("Anton", 400, "Anton"), round: F("Nunito", 800, "Nunito:wght@800"), pop: F("Baloo 2", 800, "Baloo+2:wght@800"),
        kiwi: F("Mali", 600, "Mali:wght@600"), klee: F("Patrick Hand", 400, "Patrick+Hand"), brush: F("Dancing Script", 700, "Dancing+Script:wght@700"),
        reggae: F("Bangers", 400, "Bangers"), rampart: F("Bungee", 400, "Bungee"), dot: F("VT323", 400, "VT323"), mono: F("IBM Plex Mono", 500, "IBM+Plex+Mono:wght@500;600"),
      },
    };
    J.LANG_LABEL.vi = "Tiếng Việt"; if (J.LANGS.indexOf("vi") < 0) J.LANGS.push("vi");
    var detect = J.detectLang;
    J.detectLang = function (text) { return VI_CHARS.test(String(text || "")) ? "vi" : detect(text); };
    // tách cụm tiếng Việt theo khoảng trắng (mỗi âm tiết cách nhau sẵn) — bộ tách gốc dính số vào chữ trước ("phí3 tiếng")
    var chunk = J.chunkText;
    J.chunkText = function (text) {
      var t = String(text || "");
      if (!VI_CHARS.test(t) && J.lang !== "vi") return chunk(text);
      var out = [];
      t.trim().split(/\s+/).forEach(function (w) {
        if (!w) return;
        if (out.length && /^[^0-9A-Za-zÀ-ỹ]+$/.test(w)) out[out.length - 1] += " " + w;   // dấu câu đứng riêng → dính vào chữ trước
        else out.push(w);
      });
      return out.length ? out : chunk(text);
    };
    var seg = J.segLocale;
    J.segLocale = function () { return J.lang === "vi" ? "vi" : seg(); };
    try {                                                       // offer it in the editor's language menu too
      var sel = W.document.getElementById("lyricLang");
      if (sel && !sel.querySelector('option[value="vi"]')) { var o = W.document.createElement("option"); o.value = "vi"; o.textContent = "Tiếng Việt"; sel.appendChild(o); }
    } catch (e) {}
  }

  /* ── where files go ── */
  function outDir() { return NX.outDir ? NX.outDir() : ""; }
  function seqName() { return NX.currentName ? NX.currentName() : ""; }
  function fxDir() {
    var d = path.join(outDir() || path.join(NX.cfgDir, "out"), "textfx");
    try { fs.mkdirSync(d, { recursive: true }); } catch (e) {}
    return d;
  }
  // JIZURA's Save / Export → files on disk (CEP panels cannot download)
  function hookSave() {
    J.saveFile = function (filename, data) {
      var blob = data instanceof W.Blob || data instanceof Blob ? data : new W.Blob([data]);
      return blob.arrayBuffer().then(function (ab) {
        var p = path.join(fxDir(), String(filename).replace(/[\\/:*?"<>|]/g, "_"));
        fs.writeFileSync(p, Buf.from(ab));
        NX.log("✔ JIZURA đã lưu: " + p, "#6fd08c");
        try { J.uiApi.toast("Đã lưu: " + p); } catch (e) {}
        return "saved";
      });
    };
  }

  /* ── sequence + timed text from the build ── */
  var seq = { w: 1920, h: 1080, fps: 25, playhead: 0, name: "" };
  function readSeq() {
    return NX.evalHost("jesSequence(1)").then(function (r) {
      var j = JSON.parse(r); if (!j.ok) throw new Error(j.error || "không đọc được sequence");
      seq = { w: j.w, h: j.h, fps: j.fps, playhead: j.playhead, name: j.name };
      return seq;
    });
  }
  function readSide() { try { return JSON.parse(fs.readFileSync(path.join(outDir(), seqName() + ".text.json"), "utf8")); } catch (e) { return null; } }
  function srtCues() {
    var txt; try { txt = fs.readFileSync(path.join(outDir(), seqName() + ".srt"), "utf8"); } catch (e) { return null; }
    var ts = function (x) { var m = /(\d+):(\d+):(\d+)[,.](\d+)/.exec(x); return m ? +m[1] * 3600 + +m[2] * 60 + +m[3] + +m[4] / 1000 : 0; };
    return txt.replace(/\r/g, "").split(/\n\n+/).map(function (b) {
      var ls = b.trim().split("\n"); if (ls.length < 3) return null;
      var m = ls[1].split("-->"); return m.length === 2 ? { s: ts(m[0]), e: ts(m[1]), text: ls.slice(2).join(" ") } : null;
    }).filter(Boolean);
  }
  var ASPECTS = [["16:9", 16 / 9], ["9:16", 9 / 16], ["1:1", 1], ["4:5", 0.8], ["4:3", 4 / 3], ["3:4", 0.75], ["21:9", 21 / 9]];
  function aspectOf(w, h) {
    var r = w / h, best = ASPECTS[0], bd = 1e9;
    ASPECTS.forEach(function (a) { var d = Math.abs(Math.log(r / a[1])); if (d < bd) { bd = d; best = a; } });
    return best[0];
  }
  function clean(t) { return String(t || "").replace(/[\r\n]+/g, " ").replace(/[#|\[\]*\/]/g, " ").replace(/\s+/g, " ").trim().normalize("NFC"); }
  function lrc(t) { var m = Math.floor(t / 60), s = t - m * 60; return "[" + (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s.toFixed(2) + "]"; }

  // timeline-timed lines → JIZURA lyrics in LRC form; the plan's t = sequence time, so it is placed from 0
  function feed(kind) {
    if (!J) return;
    var side = readSide(), list;
    if (kind === "sub") {
      list = side && side.cues ? side.cues : srtCues();
      if (!list || !list.length) return NX.log("• Chưa có phụ đề — bấm Build (bước 3) trước", "#e0a82e");
    } else {
      list = side && side.highlights;
      if (!list || !list.length) return NX.log(side ? "• Plan không có highlight" : "• Chưa có timing highlight — bấm Build lại (bản build mới ghi file .text.json)", "#e0a82e");
    }
    return readSeq().catch(function () { return seq; }).then(function () {
      var U = J.ui, p = U.project;
      p.lyrics = list.map(function (c) { return lrc(c.s) + clean(c.text) + (kind === "hl" ? "!" : ""); }).join("\n");
      p.title = ""; p.artist = "";
      p.aspect = aspectOf(seq.w, seq.h); p.fps = Math.max(1, Math.round(seq.fps || 25));
      if (p.timing) { p.timing.useAudioLength = false; p.timing.tail = 0.6; }
      S().anchor = "zero"; NX.save(); bar();
      J.uiApi.syncUI(); J.uiApi.replan(); J.uiApi.flushSave();
      try { J.uiApi.seek(Math.max(0, list[0].s)); } catch (e) {}
      NX.log("✔ Đã nạp " + list.length + (kind === "sub" ? " câu thoại" : " highlight") + " vào JIZURA (mốc = đầu sequence " + (seq.name || "") + ")", "#6fd08c");
    });
  }

  /* ── render the editor's current plan → ProRes 4444 with alpha, one clip per group of cuts ── */
  function ranges(plan) {
    if (plan.hud) return [[0, plan.duration]];                // a HUD runs the whole time → one clip
    var rs = plan.cuts.map(function (c) { return [Math.max(0, c.start - 0.05), Math.min(plan.duration, c.end + 0.3)]; })
      .sort(function (a, b) { return a[0] - b[0]; }), out = [];
    rs.forEach(function (r) { var l = out[out.length - 1]; if (l && r[0] - l[1] < 1.0) l[1] = Math.max(l[1], r[1]); else out.push(r.slice()); });
    return out;
  }
  function renderRange(R, plan, t0, t1, file, fast, onFrame, cancelled) {
    var fps = seq.fps || plan.fps, f0 = Math.round(t0 * fps), f1 = Math.max(f0 + 1, Math.round(t1 * fps)), total = f1 - f0;
    var cap = fast ? 1080 : 2160;
    var fit = Math.min(seq.w / plan.W, seq.h / plan.H), sc = Math.min(fit, cap / Math.min(plan.W, plan.H));
    var rw = Math.round(plan.W * sc / 2) * 2, rh = Math.round(plan.H * sc / 2) * 2;
    var fw = Math.round(plan.W * fit / 2) * 2, fh = Math.round(plan.H * fit / 2) * 2;
    var vf = (rw !== fw || rh !== fh ? "scale=" + fw + ":" + fh + ":flags=lanczos," : "") +
             (fw !== seq.w || fh !== seq.h ? "pad=" + seq.w + ":" + seq.h + ":(ow-iw)/2:(oh-ih)/2:color=black@0," : "") + "format=yuva444p10le";
    var rate = Math.abs(fps - 29.97) < 0.02 ? "30000/1001" : Math.abs(fps - 23.976) < 0.02 ? "24000/1001" : Math.abs(fps - 59.94) < 0.02 ? "60000/1001" : String(fps);
    var args = ["-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", rw + "x" + rh, "-r", rate, "-i", "-",
                "-vf", vf, "-c:v", "prores_ks", "-profile:v", "4", "-vendor", "apl0", "-an", file];
    var c = document.createElement("canvas"); c.width = rw; c.height = rh;
    var ctx = c.getContext("2d");
    return new Promise(function (res, rej) {
      var p = cp.spawn(NX.S.ffmpeg || "ffmpeg", args, { windowsHide: true }), err = "", i = 0, done = false;
      if (NX.trackChild) NX.trackChild(p);                   // killed with the panel
      try { p.stderr.setEncoding("utf8"); } catch (e) {}
      p.stderr.on("data", function (d) { err += d; if (err.length > 8000) err = err.slice(-4000); });
      p.on("error", function (e) { if (!done) { done = true; rej(new Error("ffmpeg: " + e.message)); } });
      p.on("close", function (code) { if (done) return; done = true; code === 0 ? res() : rej(new Error("ffmpeg " + code + ": " + err.slice(-200))); });
      p.stdin.on("error", function () {});
      var step = function () {
        if (done) return;
        if (cancelled()) { done = true; try { p.kill(); } catch (e) {} return rej(new Error("Đã huỷ")); }
        var t = performance.now();
        while (i < total && performance.now() - t < 30) {          // ~30 ms of work per slice → panel stays responsive
          R.frame(ctx, plan, (f0 + i) / fps, { scale: sc, transparent: true });
          i++; onFrame(i, total);
          if (!p.stdin.write(Buf.from(ctx.getImageData(0, 0, rw, rh).data.buffer))) { p.stdin.once("drain", function () { setTimeout(step, 0); }); return; }
        }
        if (i >= total) { p.stdin.end(); return; }
        setTimeout(step, 0);
      };
      step();
    });
  }
  var job = null;
  function place() {
    if (job || !J || !J.ui || !J.ui.plan) return;
    try { J.uiApi.pause(); } catch (e) {}
    var plan = Object.assign({}, J.ui.plan, { keyBg: null }), rs = ranges(plan), cancelled = false, s = S();
    if (!rs.length) return NX.log("• JIZURA chưa có chữ nào để đặt", "#e0a82e");
    // panel-wide lock: no Build / Review edit / sound / voice job touches the timeline while the text clips render + land
    if (NX.tryLock && !NX.tryLock("textfx", "Chữ → timeline")) return NX.log("• Đang chạy: " + NX.busyLabel() + " — đặt chữ sau khi xong", "#e0a82e");
    var track = s.track != null ? +s.track : 3, fast = s.res === "fast";
    job = { cancel: function () { cancelled = true; } }; bar();
    var R, dir, t0 = Date.now(), placed = [];
    try { R = new J.Renderer(); dir = path.join(fxDir(), (seq.name || "seq").replace(/[\\/:*?"<>|]/g, "_")); }
    catch (e0) { job = null; if (NX.unlock) NX.unlock("textfx"); bar(); return NX.log("✖ JIZURA → timeline: " + e0.message, "#e5534b"); }
    try { fs.mkdirSync(dir, { recursive: true }); } catch (e) {}
    var stamp = new Date().toTimeString().slice(0, 8).replace(/:/g, ""), tot = rs.reduce(function (a, r) { return a + r[1] - r[0]; }, 0), doneDur = 0;
    readSeq().then(function () {
      var anchor = s.anchor === "playhead" ? seq.playhead : 0;
      NX.log("▶ JIZURA → timeline: " + rs.length + " clip · " + tot.toFixed(1) + "s chữ · V" + (track + 1) + " · mốc " + (anchor ? "playhead " + anchor.toFixed(2) + "s" : "đầu sequence"), "#CCDC42");
      return rs.reduce(function (pr, r, k) {
        return pr.then(function () {
          var file = path.join(dir, "jizura_" + stamp + "_" + ("00" + k).slice(-3) + ".mov");
          return renderRange(R, plan, r[0], r[1], file, fast, function (i, n) {
            prog((doneDur + (r[1] - r[0]) * i / n) / tot, "Clip " + (k + 1) + "/" + rs.length + " · frame " + i + "/" + n);
          }, function () { return cancelled; }).then(function () {
            doneDur += r[1] - r[0]; placed.push({ path: file.replace(/\\/g, "/"), at: anchor + r[0] });
          });
        });
      }, Promise.resolve());
    }).then(function () {
      prog(1, "Đặt lên timeline…");
      return NX.evalHost("jesTextFxPlace(" + JSON.stringify(JSON.stringify(placed)) + "," + track + ")");
    }).then(function (r) {
      var j = JSON.parse(r); if (!j.ok) throw new Error(j.error);
      NX.log("✔ JIZURA: " + j.placed + " clip lên V" + (track + 1) + " · " + Math.round((Date.now() - t0) / 1000) + "s · " + dir, "#6fd08c");
    }).catch(function (e) { NX.log("✖ JIZURA → timeline: " + e.message, "#e5534b"); })
      .then(function () { job = null; if (NX.unlock) NX.unlock("textfx"); prog(-1); bar(); });
  }
  function prog(f, label) {
    var b = $("tfxProg"); if (!b) return;
    if (f < 0) { b.style.display = "none"; return; }
    b.style.display = "flex"; b.querySelector("i").style.width = Math.round(f * 100) + "%"; b.querySelector("span").textContent = label || "";
  }

  /* ── AI Style: tự chọn style chữ theo Ý NGHĨA từng câu ──
     LLM (hoặc luật, 0 token) chỉ làm phần ngữ nghĩa: vai trò câu (hook, số liệu, bước, so sánh, cảnh báo…) + từ khoá + impact.
     Bảng ROLES cố định map vai trò → key JIZURA (layout / enter / exit / hold / decor) nên key luôn hợp lệ và nhất quán giữa các video.
     Look tổng (mood + style + fx) theo nhóm preset; câu đã gán được khoá (lock) → Shuffle / Randomize vẫn giữ đúng nghĩa. */
  var ROLES = {
    hook:       { label: "Hook",        layout: ["huge", "condensed", "center"], enter: ["zoom", "slice", "stretch"], exit: ["explode", "slice"], hold: "still", decor: ["brackets"], impact: true },
    question:   { label: "Câu hỏi",     layout: ["huge", "center", "stack"], enter: ["type", "zoom"], exit: ["blur", "shrink"], hold: "breathe", decor: [] },
    number:     { label: "Số liệu",     layout: ["labels", "condensed", "huge"], enter: ["type", "wipe", "scramble"], exit: ["wipe", "slice"], hold: "still", decor: ["brackets"], nums: true },
    definition: { label: "Định nghĩa",  layout: ["gloss", "stack", "type"], enter: ["type", "blur"], exit: ["blur", "wipe"], hold: "still", decor: ["brackets"] },
    step:       { label: "Bước / list", layout: ["pill", "labels", "stack"], enter: ["wipe", "stretch"], exit: ["wipe", "stretch"], hold: "still", decor: ["arrows"] },
    compare:    { label: "So sánh",     layout: ["diag", "tile", "condensed"], enter: ["slice", "wipe"], exit: ["slice", "wipe"], hold: "still", decor: ["slash"] },
    warning:    { label: "Cảnh báo",    layout: ["huge", "condensed"], enter: ["flicker", "scramble"], exit: ["glitch", "slice"], hold: "jitter", decor: ["slash"], impact: true },
    quote:      { label: "Trích dẫn",   layout: ["center", "gloss", "stack"], enter: ["blur", "assemble"], exit: ["drift", "blur"], hold: "breathe", decor: [] },
    cta:        { label: "CTA",         layout: ["pill", "huge", "center"], enter: ["pop", "drop"], exit: ["shrink", "scatter"], hold: "still", decor: ["dots"], impact: true },
    normal:     { label: "Thường" }                       // không khoá: JIZURA tự chọn theo mood
  };
  var LOOKS = {
    explainer: { label: "Explainer", moods: ["graphic", "editorial"], styles: ["blueprint", "specimen", "transit", "paper", "caution"] },
    youtube:   { label: "YouTube",   moods: ["pop", "graphic"], styles: ["magenta", "caution", "blueprint", "transit", "rouge"] },
    cinematic: { label: "Cinematic", moods: ["emotional", "calm", "editorial"], styles: ["noir", "paper", "specimen", "mono", "hud"] },
    hype:      { label: "Hype",      moods: ["glitch", "pop"], styles: ["noir", "crimson", "mint", "mono", "magenta"] }
  };
  function lookAuto() {
    var p = String(NX.S.preset || "");
    if (/explainer|documentary|talking|interview/.test(p)) return "explainer";
    if (/youtube|short|vlog/.test(p)) return "youtube";
    if (/wedding|cinematic/.test(p)) return "cinematic";
    if (/beat|event|recap|hype/.test(p)) return "hype";
    return "explainer";
  }
  var RULES = [
    ["cta", /(đăng ký|subscribe|follow|bình luận|comment|nhấn like|bấm like|link (ở|dưới)|tải ngay|thử ngay|inbox|liên hệ|đặt hàng)/i],
    ["warning", /(đừng|không bao giờ|sai lầm|cẩn thận|lỗi|tránh|nguy hiểm|cảnh báo|rủi ro|never|don'?t|mistake|warning)/i],
    ["step", /^(bước|thứ nhất|thứ hai|thứ ba|đầu tiên|tiếp theo|cuối cùng|số \d|\d+[.)]\s|step|first|second|next|finally)/i],
    ["compare", /(so với|thay vì|ngược lại|trong khi đó|khác với|\bvs\.?\b|hơn hẳn|thay cho|instead of|compared)/i],
    ["question", /[?？]\s*$/],
    ["definition", /(là gì|nghĩa là|được gọi là|tức là|hiểu đơn giản|định nghĩa|means|is called)/i],
    ["number", /(\d[\d.,]*\s?(%|phần trăm|triệu|tỷ|nghìn|ngàn|k\b|x\b|lần|giờ|phút|giây|năm|tháng|ngày|người|đồng|usd|\$))|(\$\s?\d)/i],
    ["quote", /^["“”'‘].+["“”'’]$/]
  ];
  function ruleRole(text, i) {
    for (var k = 0; k < RULES.length; k++) if (RULES[k][1].test(text)) return RULES[k][0];
    return i === 0 ? "hook" : "normal";
  }
  function numsIn(t) { return (String(t).match(/\$?\d[\d.,]*\s?(%|triệu|tỷ|nghìn|ngàn|k\b|x\b|lần)?/gi) || []).map(function (x) { return x.trim(); }).filter(function (x) { return x.length > 0; }); }

  // dòng lyrics (LRC) ↔ dòng JIZURA: bỏ dòng trống / # / [ti:…], gỡ markup cũ (*từ*, ! cuối câu) để AI chạy lại được
  function splitLyrics(raw) {
    var rows = [];
    String(raw || "").replace(/\r/g, "").split("\n").forEach(function (src) {
      var s0 = src.trim(), m, pre = "";
      if (!s0 || s0.charAt(0) === "#" || /^\[(ti|ar|al|by|offset):/i.test(s0)) { rows.push({ raw: src }); return; }
      var s = s0;
      while ((m = s.match(/^\[(\d+):(\d+(?:[.:]\d+)?)\]/))) { pre += m[0]; s = s.slice(m[0].length); }
      var note = "", bar = s.indexOf("|");
      if (bar >= 0) { note = s.slice(bar); s = s.slice(0, bar); }
      s = s.trim().replace(/\*([^*]+)\*/g, "$1").replace(/\s*!$/, "");
      if (!s) { rows.push({ raw: src }); return; }
      rows.push({ pre: pre, text: s, note: note });
    });
    return rows;
  }
  function aiKey() { var k = NX.S.keys || {}; return k.gemini || (NX.S.provider === "gemini" ? NX.S.apiKey : "") || ""; }
  function aiSession(prov) {
    var LLM = window.NXLLM, r = (NX.S.route || {}).script || {};
    if (prov === "gemini") return LLM.create("gemini", aiKey(), (NX.S.models || {}).gemini || undefined, null, {});
    return LLM.create("ollama", "", r.provider === "ollama" && r.model ? r.model : "gemma4:12b-it-qat", null, { numCtx: Math.max(+NX.S.numCtx || 16384, 16384) });
  }
  var AI_SYS = [
    "Bạn là motion designer chữ (kinetic typography) cho video. Nhiệm vụ: đọc các câu chữ sẽ hiện trên màn hình và gán VAI TRÒ cho từng câu để chọn kiểu chữ động.",
    "Vai trò (chỉ dùng đúng các key này): hook (câu mở / gây tò mò), question (câu hỏi), number (câu có số liệu là trọng tâm), definition (giải thích khái niệm), step (bước / ý trong danh sách),",
    "compare (so sánh, đối lập, trước–sau), warning (cảnh báo, sai lầm, điều cấm), quote (trích dẫn, câu cảm xúc / chốt ý), cta (kêu gọi hành động), normal (câu dẫn bình thường — đa số câu nên là normal).",
    "key = 0–2 từ khoá cần nhấn, chép NGUYÊN VĂN, phải nằm trong câu. impact = true chỉ cho câu đắt nhất (≤ 15% số câu).",
    "look = explainer | youtube | cinematic | hype theo tone tổng; mood = graphic | editorial | pop | calm | emotional | glitch.",
    "TRẢ VỀ DUY NHẤT JSON: {\"tone\":\"1 câu mô tả tone\",\"look\":\"…\",\"mood\":\"…\",\"lines\":[{\"i\":0,\"role\":\"…\",\"key\":[\"…\"],\"impact\":false}]}"
  ].join("\n");
  function askAI(texts, prov) {
    var per = prov === "gemini" ? 400 : 60, out = { lines: [] }, chunks = [];
    for (var k = 0; k < texts.length; k += per) chunks.push(k);
    return chunks.reduce(function (pr, k0) {
      return pr.then(function () {
        var llm = aiSession(prov), part = texts.slice(k0, k0 + per);
        llm.userTurn("CÁC CÂU (i = số thứ tự):\n" + part.map(function (t, j) { return (k0 + j) + ". " + t; }).join("\n") + "\n\nGán vai trò cho từng câu. TRẢ VỀ DUY NHẤT JSON.");
        return llm.send(AI_SYS + (window.JESStyle ? window.JESStyle.promptText(["chu"]) : ""), null, { json: true, temperature: 0.2 }).then(function (r) {
          var j = window.NXLLM.parseJSON(r.text);
          if (k0 === 0) { out.tone = j.tone; out.look = j.look; out.mood = j.mood; out.model = (prov === "gemini" ? "Gemini " : "Ollama ") + llm.model; }
          out.lines = out.lines.concat(j.lines || []);
        });
      });
    }, Promise.resolve()).then(function () { return out; });
  }
  function pickKey(group, cands, used) {
    var reg = (J.registry && J.registry(group)) || {}, en = ((J.ui.project.enabled || {})[group]) || {};
    var ok = (cands || []).filter(function (k) { return reg[k] && en[k] !== false; });
    if (!ok.length) return undefined;
    var n = used[group + ":" + cands.join(",")] = (used[group + ":" + cands.join(",")] || 0) + 1;
    return ok[(n - 1) % ok.length];                          // câu cùng vai trò liên tiếp → xoay vòng, không lặp y hệt
  }
  function aiStyle() {
    if (!J || !J.ui) return;
    if (NX.isBusy && NX.isBusy()) return NX.log("• Đang chạy: " + NX.busyLabel() + " — thử lại sau", "#e0a82e");
    var p = J.ui.project, rows = splitLyrics(p.lyrics), items = rows.filter(function (r) { return r.text; });
    if (!items.length) return NX.log("• JIZURA chưa có chữ — bấm Thoại → JIZURA hoặc Highlight → JIZURA trước", "#e0a82e");
    var mode = S().ai || "auto", lookSel = S().look || "auto", t0 = Date.now();
    var useAI = mode !== "rule", prov = mode === "local" ? "ollama" : (aiKey() ? "gemini" : "ollama");
    $("tfxAiStyle").disabled = true; $("tfxState").textContent = useAI ? "AI đang đọc " + items.length + " câu…" : "Đang gán vai trò theo luật…";
    var texts = items.map(function (r) { return r.text; });
    var run = !useAI ? Promise.resolve({ lines: [] }) : askAI(texts, prov).catch(function (e) {
      if (prov === "gemini" && mode === "auto") {
        NX.log("• Gemini lỗi: " + String(e.message || e).slice(0, 120) + " → chạy bằng local", "#e0a82e");
        return askAI(texts, "ollama");
      }
      throw e;
    }).catch(function (e) { NX.log("• AI Style: " + String(e.message || e).slice(0, 160) + " → dùng luật (0 token)", "#e0a82e"); return { lines: [], failed: true }; });
    run.then(function (res) {
      var by = {}; (res.lines || []).forEach(function (x) { if (x && x.i != null) by[+x.i] = x; });
      var lk = lookSel !== "auto" ? lookSel : lookAuto(), L = LOOKS[lk];            // look theo preset; AI chỉ chọn mood trong look đó
      var mood = L.moods.indexOf(res.mood) >= 0 ? res.mood : L.moods[0];
      var styles = L.styles.filter(function (k) { return J.STYLES[k] && (J.STYLES[k].moods || [mood]).indexOf(mood) >= 0; });
      var style = (styles.indexOf(p.style) >= 0 ? p.style : null) || styles[0] || L.styles.filter(function (k) { return J.STYLES[k]; })[0] || p.style;
      var M = J.MOODS[mood] || {}, fx = Object.assign({}, p.fx);
      Object.keys(M.fx || {}).forEach(function (k) { var r = M.fx[k]; fx[k] = +((r[0] + r[1]) / 2).toFixed(2); });
      var ov = {}, used = {}, count = {};
      Object.keys(p.overrides || {}).forEach(function (k) { var o = p.overrides[k]; if (o && o.lock && !o.ai) ov[k] = o; });   // khoá tay của anh giữ nguyên
      var li = 0;
      rows.forEach(function (r) {
        if (!r.text) return;
        var a = by[li] || {}, role = ROLES[a.role] ? a.role : ruleRole(r.text, li), R = ROLES[role], txt = r.text;
        count[role] = (count[role] || 0) + 1;
        var keys = (Array.isArray(a.key) ? a.key : []).filter(function (w) { return w && txt.indexOf(w) >= 0 && w.length < txt.length; }).slice(0, 2);
        if (!keys.length && R.nums) keys = numsIn(txt).slice(0, 1);
        keys.forEach(function (w) { txt = txt.replace(w, "*" + w + "*"); });
        var impact = a.impact != null ? !!a.impact : !!R.impact;
        r.out = r.pre + txt + (impact ? "!" : "") + (r.note ? " " + r.note : "");
        if (!ov[li] && R.layout) {
          ov[li] = { ai: role, lock: true, lockedSeed: J.h(p.seed, li + 1, 0), layout: pickKey("layout", R.layout, used), enter: pickKey("enter", R.enter, used),
                     exit: pickKey("exit", R.exit, used), hold: pickKey("hold", [R.hold], used), decor: (R.decor || []).filter(function (d) { return J.DECOR && J.DECOR[d]; }) };
          Object.keys(ov[li]).forEach(function (k) { if (ov[li][k] === undefined) delete ov[li][k]; });
        }
        li++;
      });
      p.lyrics = rows.map(function (r) { return r.text ? r.out : r.raw; }).join("\n");
      p.overrides = ov; p.mood = mood; p.style = style; p.fx = fx;
      J.uiApi.syncUI(); J.uiApi.replan(); J.uiApi.flushSave();
      var sum = Object.keys(count).sort(function (a, b) { return count[b] - count[a]; }).map(function (k) { return ROLES[k].label + " " + count[k]; }).join(" · ");
      S().last = { at: new Date().toISOString(), look: lk, mood: mood, style: style, roles: count, model: useAI && !res.failed ? res.model : "luật" }; NX.save();
      $("tfxState").textContent = "AI Style: " + L.label + " · " + mood + " · " + style + " — " + sum;
      NX.log("✔ AI Style (" + (useAI && !res.failed ? res.model : "luật, 0 token") + ", " + Math.round((Date.now() - t0) / 1000) + "s): " + items.length + " câu · look " + L.label +
        " · mood " + mood + " · style " + style + (res.tone ? " · tone: " + String(res.tone).slice(0, 80) : "") + "\n  " + sum + " — câu có vai trò đã khoá (Shuffle / Randomize vẫn giữ), sửa tay từng dòng được", "#6fd08c");
    }).catch(function (e) { NX.log("✖ AI Style: " + (e.message || e), "#e5534b"); $("tfxState").textContent = "AI Style lỗi"; })
      .then(function () { $("tfxAiStyle").disabled = false; });
  }

  /* ── bar + iframe sizing ── */
  function bar() {
    var s = S();
    if ($("tfxAnchor")) $("tfxAnchor").value = s.anchor || "zero";
    if ($("tfxTrack")) $("tfxTrack").value = String(s.track != null ? s.track : 3);
    if ($("tfxRes")) $("tfxRes").value = s.res || "seq";
    if ($("tfxPlace")) $("tfxPlace").disabled = !!job || !J || (!job && NX.isBusy && NX.isBusy());
    if ($("tfxCancel")) $("tfxCancel").style.display = job ? "" : "none";
  }
  function fit() {
    var f = $("tfxFrame"); if (!f || !$("hl").classList.contains("on")) return;
    var top = f.getBoundingClientRect().top;
    f.style.height = Math.max(360, window.innerHeight - top - 8) + "px";
  }
  var started = false;
  function start() {
    if (started) { fit(); return; } started = true;
    var f = $("tfxFrame"), s = S();
    bar();
    ["tfxAnchor", "tfxTrack", "tfxRes"].forEach(function (id) {
      $(id).onchange = function () { var v = $(id).value; S()[{ tfxAnchor: "anchor", tfxTrack: "track", tfxRes: "res" }[id]] = id === "tfxTrack" ? +v : v; NX.save(); };
    });
    $("tfxSub").onclick = function () { feed("sub"); };
    $("tfxHl").onclick = function () { feed("hl"); };
    if ($("tfxAiStyle")) {
      $("tfxLook").value = S().look || "auto"; $("tfxAi").value = S().ai || "auto";
      $("tfxLook").onchange = function () { S().look = $("tfxLook").value; NX.save(); };
      $("tfxAi").onchange = function () { S().ai = $("tfxAi").value; NX.save(); };
      $("tfxAiStyle").onclick = aiStyle;
    }
    $("tfxPlace").onclick = place;
    $("tfxCancel").onclick = function () { if (job) job.cancel(); };
    f.onload = function () {
      W = f.contentWindow; J = W && W.J;
      if (!J || !J.uiApi) { NX.log("✖ Không nạp được JIZURA trong tab Chữ", "#e5534b"); return; }
      patchVietnamese(J); hookSave();
      try { J.uiApi.replan(); } catch (e) {}
      $("tfxState").textContent = "JIZURA sẵn sàng"; bar();
      try { document.dispatchEvent(new CustomEvent("jes:jizura")); } catch (e) {}     // the Studio renders with this engine
    };
    f.src = "js/jizura/app.html";
    window.addEventListener("resize", fit);
    setTimeout(fit, 0);
  }
  document.addEventListener("jes:busy", function () { if (started) bar(); });
  document.addEventListener("nxtab", function (e) {
    if (e.detail === "hl") start();
    else if (J) { try { J.uiApi.pause(); } catch (x) {} }        // stop the preview loop when the tab is hidden
  });
  /* timed lines of the current build for the Studio: "sub" = dialogue cues, "hl" = highlights → [{s, e, text}] or null */
  function cues(kind) {
    var side = readSide();
    var list = kind === "sub" ? (side && side.cues ? side.cues : srtCues()) : side && side.highlights;
    return (list || []).map(function (c) { return { s: +c.s, e: +c.e, text: clean(c.text) }; }).filter(function (c) { return c.text && c.e > c.s; });
  }
  window.JESTextFx = { start: start, feed: feed, place: place, aiStyle: aiStyle, J: function () { return J; }, W: function () { return W; }, cues: cues,
    roles: function () { return ROLES; }, ruleRole: ruleRole,
    _test: { splitLyrics: splitLyrics, ruleRole: ruleRole, ROLES: ROLES, LOOKS: LOOKS, setJ: function (w, j) { W = w; J = j; } } };
})();
