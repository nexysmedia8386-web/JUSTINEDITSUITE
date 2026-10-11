/* Justin EDIT Suite — work sessions (auto-saved, restorable per source folder) + cache manager. */
(function () {
  "use strict";
  var fs = require("fs"), path = require("path"), cp = require("child_process");
  var $ = function (id) { return document.getElementById(id); };
  var NX = window.NX;
  if (!NX) return;
  var FILE = path.join(NX.cfgDir, "sessions.json"), MAX = 20, LOG_TAIL = 60;
  var norm = function (p) { return String(p || "").replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase(); };
  var base = function (p) { return String(p || "").replace(/[\\\/]+$/, "").split(/[\\\/]/).pop(); };

  function read() {
    try { var j = JSON.parse(fs.readFileSync(FILE, "utf8")); if (j && j.list) return j; } catch (e) {}
    return { last: "", list: [] };
  }
  function write(db) {
    try {
      fs.mkdirSync(NX.cfgDir, { recursive: true });
      fs.writeFileSync(FILE + ".tmp", JSON.stringify(db, null, 1), "utf8");
      fs.renameSync(FILE + ".tmp", FILE);
    } catch (e) {}
  }
  function activeTab() { var b = document.querySelector("nav button.on"); return b ? b.dataset.tab : "run"; }
  function stageInfo() {
    var o = {};
    ["an", "rc", "sc", "al", "bu"].forEach(function (k) { var el = $("pst_" + k); if (el) o[k] = el.className.replace(/\bpst\b/, "").trim(); });
    return o;
  }
  function logTail() {
    var el = $("log"), out = [];
    if (!el) return out;
    var ch = el.childNodes, from = Math.max(0, ch.length - LOG_TAIL);
    for (var i = from; i < ch.length; i++) {
      if (ch[i].className === "sessOld") continue;
      out.push({ t: ch[i].textContent, c: ch[i].style ? ch[i].style.color : "" });
    }
    return out;
  }

  // a plan belongs to the folder it was aligned from (plan.src) — never carry another folder's plan into this session
  function planOf(p, src) {
    if (!p || !fs.existsSync(p)) return "";
    try { var j = JSON.parse(fs.readFileSync(p, "utf8")); if (j && j.src && norm(j.src) !== norm(src)) return ""; } catch (e) {}
    return p;
  }
  var restoring = false, lastSrc = "";
  function snap() {
    if (restoring) return;
    var S = NX.S;
    if (!S.src) return;
    var db = read(), id = norm(S.src);
    var e = db.list.filter(function (x) { return x.id === id; })[0];
    if (!e) { e = { id: id, created: new Date().toISOString() }; db.list.unshift(e); }
    var tail = logTail();
    Object.assign(e, {
      src: S.src, name: S.name || "", script: S.script || "", keywords: S.keywords || "", base: S.base || "",
      scriptMode: S.scriptMode || "file", brief: Object.assign({}, S.brief || {}),
      plan: planFor(S.src), tab: activeTab(), stages: stageInfo(),
      updated: new Date().toISOString(), log: tail.length ? tail : (e.log || [])
    });
    var plan = NX.getPlan();
    if (plan && e.plan && (!plan.src || norm(plan.src) === id)) e.planInfo = plan.segments.length + " đoạn"; else if (!e.plan) delete e.planInfo;
    db.list.sort(function (a, b) { return String(b.updated).localeCompare(String(a.updated)); });
    db.list = db.list.slice(0, MAX);
    db.last = id;
    write(db);
    lastSrc = id;
    fillSelect(db);
  }

  function planFor(src) {                     // the current plan path, only when it is this folder's plan
    var p = NX.getPlanPath() || "", P = NX.getPlan();
    if (!p) return "";
    if (P && P.src && norm(P.src) !== norm(src)) return "";
    return p;
  }
  function when(iso) {
    if (!iso) return "";
    var d = new Date(iso), now = new Date(), dd = Math.round((now - d) / 864e5);
    var hm = ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
    return dd <= 0 && d.getDate() === now.getDate() ? "hôm nay " + hm : dd <= 1 ? "hôm qua " + hm : d.getDate() + "/" + (d.getMonth() + 1) + " " + hm;
  }
  function fillSelect(db) {
    var sel = $("sessSel");
    if (!sel) return;
    db = db || read();
    var cur = norm(NX.S.src), keep = sel.value;
    sel.innerHTML = '<option value="">↺ Phiên gần đây (' + db.list.length + ')…</option>' + db.list.map(function (e) {
      var ok = fs.existsSync(e.src);
      return '<option value="' + NX.esc(e.id) + '"' + (ok ? "" : " disabled") + ">" + (e.id === cur ? "● " : "") +
        NX.esc((e.name || base(e.src)) + " · " + base(e.src) + " · " + when(e.updated) + (e.planInfo ? " · " + e.planInfo : "") + (ok ? "" : " (mất folder)")) + "</option>";
    }).join("");
    if (keep) sel.value = keep;
  }

  function restore(e, opt) {
    opt = opt || {};
    if (!e) return;
    if (NX.isBusy()) return NX.log("• Đang chạy tác vụ — mở lại phiên sau khi xong", "#e0a82e");
    restoring = true;
    try {
      var S = NX.S;
      if (!opt.keepFields) {
        S.src = e.src; S.name = e.name || ""; S.script = e.script || ""; S.keywords = e.keywords || "";
        S.base = e.base || S.base; S.scriptMode = e.scriptMode || S.scriptMode;
        S.brief = Object.assign({ type: "", duration: "", platform: "", notes: "" }, e.brief || {});
        if (NX.srcChanged) NX.srcChanged({ keepName: true, noPlan: true });   // main.js tracks the folder too (plan set just below)
        NX.save(); NX.bindInputs();
        try { NX.syncMode(); } catch (x) {}
      }
      var pp = planOf(e.plan, e.src);
      NX.setPlanPath(pp);
      if (!pp) try { NX.loadPlan(); } catch (x) {}
      var logEl = $("log");
      if (logEl && e.log && e.log.length) {
        var frag = document.createDocumentFragment(), hd = document.createElement("div");
        hd.className = "sessOld"; hd.textContent = "── phiên trước · " + when(e.updated) + " ──"; hd.style.color = "#8a8f98"; frag.appendChild(hd);
        e.log.forEach(function (l) { var d = document.createElement("div"); d.className = "sessOld"; d.textContent = l.t; d.style.color = l.c || "#8a8f98"; d.style.opacity = ".7"; frag.appendChild(d); });
        var hd2 = document.createElement("div"); hd2.className = "sessOld"; hd2.textContent = "── tiếp tục ──"; hd2.style.color = "#8a8f98"; frag.appendChild(hd2);
        logEl.insertBefore(frag, logEl.firstChild);
        logEl.scrollTop = logEl.scrollHeight;
      }
      if (e.tab && e.tab !== "run") { var b = document.querySelector('nav button[data-tab="' + e.tab + '"]'); if (b) b.click(); }
      try { if (window.JESDash) window.JESDash.render(); } catch (x) {}
      try { NX.refreshHints(); } catch (x) {}
      var bn = $("sessBanner");
      if (bn) {
        bn.style.display = "block";
        bn.textContent = "↺ Đã mở lại phiên " + (e.name || base(e.src)) + " · lưu " + when(e.updated) +
          (e.planInfo ? " · plan " + e.planInfo : "") + (S.script ? " · kịch bản " + base(S.script) : "");
        setTimeout(function () { bn.style.display = "none"; }, 12000);
      }
      lastSrc = norm(e.src);
    } finally { restoring = false; }
    snap();
  }

  // ---------------------------------------------------------------- cache
  function fmt(b) { b = +b || 0; var u = ["B", "KB", "MB", "GB"], i = 0; while (b >= 1024 && i < 3) { b /= 1024; i++; } return (i < 2 ? b.toFixed(0) : b.toFixed(1)) + " " + u[i]; }
  /* python -m nxcore.cache <src> → Promise<{cats, total}> (async: spawnSync froze the panel while python walked the cache) */
  function cacheList() {
    return new Promise(function (resolve, reject) {
      var out = "", err = "", p;
      try {
        p = cp.spawn(NX.S.python || "python", ["-m", "nxcore.cache", NX.S.src], { cwd: NX.S.tools, windowsHide: true,
          env: Object.assign({}, process.env, { PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" }) });
      } catch (e) { return reject(e); }
      if (NX.trackChild) NX.trackChild(p);
      try { p.stdout.setEncoding("utf8"); p.stderr.setEncoding("utf8"); } catch (e) {}
      p.stdout.on("data", function (d) { out += d; });
      p.stderr.on("data", function (d) { err += d; });
      p.on("error", function (e) { reject(new Error("không đọc được cache: " + e.message)); });
      p.on("close", function () {
        var m = out.match(/^CACHE (.*)$/m);
        if (!m) return reject(new Error("không đọc được cache: " + err.slice(0, 200)));
        try { resolve(JSON.parse(m[1])); } catch (e) { reject(new Error("không đọc được cache: " + e.message)); }
      });
    });
  }
  var cacheBusy = false;
  function cacheUI() {
    if (!NX.S.src) return NX.log("• Chọn folder source trước", "#e0a82e");
    if (NX.isBusy()) return NX.log("• Đang chạy tác vụ — xóa cache sau khi xong", "#e0a82e");
    if (cacheBusy) return;
    cacheBusy = true; if ($("bCache")) $("bCache").disabled = true;
    cacheList().then(cacheAsk).catch(function (e) { NX.log("✖ " + (e && e.message || e), "#e5534b"); })
      .then(function () { cacheBusy = false; if ($("bCache")) $("bCache").disabled = false; });
  }
  function cacheAsk(L) {
    var c = L.cats || {}, sz = function (ks) { return ks.reduce(function (a, k) { return a + ((c[k] || {}).bytes || 0); }, 0); };
    if (!L.total) return NX.log("• Source này chưa có cache (_nexys_work trống)");
    var html = '<table class="cacheTbl">' + Object.keys(c).filter(function (k) { return c[k].items; }).map(function (k) {
      return "<tr><td>" + NX.esc(c[k].label) + (c[k].keep ? " <span class='muted'>(giữ)</span>" : "") + '</td><td class="r">' + fmt(c[k].bytes) + "</td></tr>";
    }).join("") + '<tr><td><b>Tổng</b></td><td class="r"><b>' + fmt(L.total) + "</b></td></tr></table>" +
      "<div class='muted' style='font-size:11px'>Plan/XML trong thư mục output không bị ảnh hưởng.</div>";
    var o1 = "Nhẹ — proxy, thumbnail, WAV (" + fmt(sz(["proxy", "thumbs", "audio"])) + ")",
        o2 = "Quét lại từ đầu — thêm kết quả quét + AI (" + fmt(sz(["proxy", "thumbs", "audio", "analysis", "ai"])) + ")",
        o3 = "Reset hết — kể cả tag B-roll, triage, kịch bản AI";
    NX.ask("Xóa cache của " + base(NX.S.src) + "?", [o1, o2, o3], { html: html }).then(function (a) {
      if (!a) return;
      var args = ["-m", "nxcore.cache", NX.S.src, "--clear", a === o1 ? "heavy" : "all"];
      var chain = Promise.resolve(true);
      if (a === o3) {
        args.push("--decisions");
        chain = NX.ask("Chắc chắn xóa cả quyết định đã chỉnh tay (tag B-roll, loại nhóm clip, kịch bản AI)? Không hoàn tác được.", ["Xóa hết", "Thôi"])
          .then(function (x) { return x === "Xóa hết"; });
      }
      chain.then(function (go) {
        if (!go) return;
        NX.guard(function () {
          return NX.run(args, "Xóa cache").then(function () {
            if (a !== o1) { NX.log("• Đã xóa kết quả quét — bấm Quét / Phân tích để quét lại", "#e0a82e"); if (window.JESDash) window.JESDash.render(); }
            NX.refreshHints();
          });
        })();
      });
    });
  }

  // ---------------------------------------------------------------- wiring
  if ($("bCache")) $("bCache").onclick = cacheUI;
  if ($("bSessOpen")) $("bSessOpen").onclick = function () {
    var id = $("sessSel").value; if (!id) return NX.log("• Chọn một phiên trong danh sách");
    snap();                                   // keep the current one first
    restore(read().list.filter(function (x) { return x.id === id; })[0]);
  };
  if ($("sessSel")) $("sessSel").ondblclick = function () { $("bSessOpen").click(); };
  // switching source folder → offer that folder's previous session
  var asking = false;
  function onSrc() {
    var id = norm(NX.S.src);
    if (!id || id === lastSrc || asking) return;
    var e = read().list.filter(function (x) { return x.id === id; })[0];
    if (!e) { lastSrc = id; snap(); return; }
    asking = true;
    NX.ask("Folder này có phiên làm việc trước (" + when(e.updated) + (e.planInfo ? ", plan " + e.planInfo : "") + (e.script ? ", kịch bản " + base(e.script) : "") + "). Mở lại?",
      ["Mở lại phiên", "Bắt đầu mới (giữ kết quả quét)"]).then(function (a) {
      asking = false;
      if (a === "Mở lại phiên") restore(e); else { lastSrc = id; snap(); }
    });
  }
  if ($("src")) { $("src").addEventListener("change", onSrc); $("src").addEventListener("jes:set", onSrc); }
  window.addEventListener("beforeunload", snap);
  setInterval(function () { if (!NX.isBusy()) snap(); }, 30000);

  // startup: settings.json already restored the fields → bring back plan, tab, log tail of the last session
  var db = read();
  fillSelect(db);
  var cur = norm(NX.S.src);
  var last = db.list.filter(function (x) { return x.id === cur; })[0];
  if (last) restore(last, { keepFields: true });
  else lastSrc = cur;

  window.JESSession = { snap: snap, restore: restore, list: function () { return read().list; }, cache: cacheUI, cacheList: cacheList };
})();
