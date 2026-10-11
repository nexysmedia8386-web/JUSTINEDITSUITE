/* Justin EDIT Suite — "Tool đã học": editor profile (nxcore.prefs) view + learning from timelines / XML. */
(function () {
  "use strict";
  var fs = require("fs"), path = require("path");
  var $ = function (id) { return document.getElementById(id); };
  var NX = window.NX;
  if (!NX) return;
  var PF = path.join(NX.cfgDir, "profile.json");
  function rj(p, d) { try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) { return d; } }

  function bump(key, field) {
    var P = rj(PF, null) || { version: 1, stats: {}, history: [] };
    P.stats = P.stats || {}; var st = P.stats[key] = P.stats[key] || {};
    st[field] = (st[field] || 0) + 1;
    try { fs.mkdirSync(NX.cfgDir, { recursive: true }); fs.writeFileSync(PF, JSON.stringify(P, null, 1), "utf8"); } catch (e) {}
  }
  /* python -m nxcore.learn … → Promise<stdout + stderr>. Async: the old spawnSync froze the whole panel (first open
     of Cài đặt, learning from an XML) for as long as python took to start + run. */
  function py(args) { return NX.py(args).then(function (r) { return r.out; }); }   // shared helper (main.js)
  var LBL = {
    broll_dur: function (v) { return "Shot B-roll dài ~" + v + "s"; },
    broll_dur_static: function (v) { return "Shot tĩnh ~" + v + "s"; },
    auto_slowmo_60p: function (v) { return v ? "Hay slow-mo clip 50/60p" : "Ít khi slow-mo clip 50/60p (chỉ khi rung nhẹ)"; },
    slowmo_rate: function (v) { return "Tỉ lệ slow-mo " + Math.round(v * 100) + "%"; },
    slowmo_speed: function (v) { return "Tốc độ slow-mo " + v + "%"; },
    auto_rotate: function (v) { return v ? "Giữ tự xoay bù nghiêng" : "Không tự xoay (anh hay bỏ)"; },
    quality_weight: function (v) { return "Ưu tiên take ổn định: " + (v >= 0.16 ? "cao" : v >= 0.1 ? "vừa" : "thấp") + " (" + v + ")"; },
    min_quality: function (v) { return "Bỏ shot dưới ~" + v + "/100"; },
    triage_auto_exclude_upscale: function (v) { return v ? "Tự loại nhóm phải phóng to" : "Giữ nhóm phải phóng to"; },
    sfx_density: function (v) { return "SFX mức " + ({ light: "nhẹ", medium: "vừa", heavy: "dày" }[v] || v); },
    dissolve_s: function (v) { return "Dissolve ~" + v + "s"; },
    dissolve_rate: function (v) { return Math.round(v * 100) + "% chuyển cảnh là dissolve"; },
    shot_taste: function (v) { return "Gu chọn shot: " + v; }
  };
  function render(rules) {
    var P = rj(PF, null);
    if (!rules) {
      paint(P, (P && P.rules) || {});                       // what the profile already says — at once
      if (!P) return Promise.resolve();
      return py(["-m", "nxcore.learn", "show"]).then(function (out) {   // the engine's current rules — when python answers
        var m = out.match(/^RULES (.*)$/m), r = null;
        try { r = m ? JSON.parse(m[1]) : null; } catch (e) {}
        if (r) paint(rj(PF, null), r);
      });
    }
    paint(P, rules);
    return Promise.resolve();
  }
  function paint(P, rules) {
    var ks = Object.keys(rules || {});
    $("learnRules").innerHTML = ks.length ? '<div class="chips" style="display:flex;flex-wrap:wrap;gap:4px">' + ks.map(function (k) {
      return '<span class="bdg g" title="' + NX.esc(k) + '">' + NX.esc(LBL[k] ? LBL[k](rules[k]) : k + " = " + rules[k]) + "</span>";
    }).join("") + '</div><div class="muted" style="font-size:10.5px;margin-top:4px">Áp làm mặc định khi Align/Build (cần ≥3 lần quan sát mỗi thói quen).</div>'
      : "Chưa đủ dữ liệu — tool học dần từ các bản dựng anh chỉnh và từ XML dự án cũ.";
    var h = (P && P.history) || [];
    $("learnHist").innerHTML = h.slice(-15).reverse().map(function (x) { return "<div>" + NX.esc(x.t + " · " + x.text) + "</div>"; }).join("") || "<div class='muted'>—</div>";
    $("learnMeta").textContent = P && P.updated ? "cập nhật " + P.updated : "";
    book();
  }
  // 📒 Sổ luật (js/stylebook.js): the rules the editor approved explicitly — stronger than these learned habits
  function book() {
    var el = $("learnBook"); if (!el || !window.JESStyle) return;
    var c = window.JESStyle.count();
    el.textContent = "📒 Sổ luật phong cách: " + (c.all ? c.on + "/" + c.all + " luật đang bật (mạnh hơn thói quen đã học ở trên)" :
      "chưa có luật — ghi chú bản dựng ở AI → 📍 Ghi chú để AI đề xuất") + " ›";
    el.onclick = function () {
      var b = document.querySelector('nav button[data-tab="ai"]'); if (b) b.click();
      if (window.JESAIEdit) window.JESAIEdit.setView("notes");
      setTimeout(function () { var d = document.getElementById("aeBook"); if (d && !d.open) { d.open = true; } }, 0);
    };
  }
  document.addEventListener("jes:stylebook", book);
  function done(out) {
    var m = out.match(/^RULES (.*)$/m), rules = null;
    try { rules = m ? JSON.parse(m[1]) : null; } catch (e) {}
    out.split(/\r?\n/).filter(function (l) { return /^học từ/.test(l); }).forEach(function (l) { NX.log("🧠 " + l); });
    if (!m) NX.log("✖ Học thất bại: " + out.slice(-300), "#e5534b");
    render(rules);
  }
  function learnXml() {
    var r = window.cep.fs.showOpenDialogEx(false, false, "Chọn XML dự án đã hoàn thiện", NX.S.outRoot || "", ["xml"]);
    if (!r || !r.data || !r.data.length) return;
    var args = ["-m", "nxcore.learn", "xml", r.data[0]];
    if (NX.S.src) args.push("--src", NX.S.src);
    NX.log("🧠 Đang học từ " + r.data[0].replace(/\\/g, "/").split("/").pop() + "…", "#8b9099");
    py(args).then(done);
  }
  function learnSeq() {
    var plan = NX.getPlanPath();
    if (!plan || !fs.existsSync(plan)) return NX.log("• Cần plan của bản dựng này (Align → Build) để so với timeline anh đã chỉnh", "#e0a82e");
    NX.evalHost("jesSequence(400, true)").then(function (r) {
      var j = typeof r === "string" ? JSON.parse(r) : r;
      if (!j || j.ok === false) throw new Error((j && j.error) || "không đọc được sequence");
      var seq = j.data || j, tmp = path.join(NX.cfgDir, "_seq_learn.json");
      fs.writeFileSync(tmp, JSON.stringify(seq), "utf8");
      return py(["-m", "nxcore.learn", "sequence", "--plan", plan, "--seq", tmp]).then(done);
    }).catch(function (e) { NX.log("✖ " + e.message, "#e5534b"); });
  }
  function reset() {
    NX.ask("Xóa toàn bộ thói quen tool đã học?", ["Xóa", "Thôi"]).then(function (a) {
      if (a !== "Xóa") return;
      try { if (fs.existsSync(PF)) fs.copyFileSync(PF, PF + ".bak"); fs.writeFileSync(PF, JSON.stringify({ version: 1, stats: {}, history: [] }, null, 1), "utf8"); } catch (e) {}
      NX.log("✔ Đã xóa bộ nhớ học (bản cũ: profile.json.bak)");
      render({});
    });
  }
  if ($("bLearnXml")) $("bLearnXml").onclick = learnXml;
  if ($("bLearnSeq")) $("bLearnSeq").onclick = learnSeq;
  if ($("bLearnReset")) $("bLearnReset").onclick = reset;
  var shown = false;
  var tabBtn = document.querySelector('nav button[data-tab="set"]');
  if (tabBtn) tabBtn.addEventListener("click", function () { if (!shown) { shown = true; render(); } });
  window.JESLearn = { bump: bump, render: render };
})();
