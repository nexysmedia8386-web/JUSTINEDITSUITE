/* Justin EDIT Suite — 📒 Sổ luật phong cách (style book).
   The editor's explicit house rules, born from timestamped notes (AI → 📍 Ghi chú: the AI proposes, the editor approves)
   or typed by hand. File: <cfgDir>/stylebook.json — this module writes it, the engine (nxcore.stylebook) reads it.
     · text rules  → added to every AI prompt of their scope (AI edit, AI Assist, script writing, text style)
     · param rules → one engine setting from nxcore/stylebook_params.json (shared whitelist), applied on every build
   A rule may be limited to one preset; a preset-specific rule beats a general one, a newer rule beats an older one. */
(function () {
  "use strict";
  var NX = window.NX;
  if (!NX) return;
  var fs = require("fs"), path = require("path");
  var SCOPES = { chung: "Chung", dung: "Dựng / cắt", am_thanh: "Âm thanh", chu: "Chữ & đồ họa", kich_ban: "Kịch bản" };
  var SPEC = null;

  function file() { return path.join(NX.cfgDir, "stylebook.json"); }
  function spec() {
    if (SPEC) return SPEC;
    try { SPEC = JSON.parse(fs.readFileSync(path.join(NX.S.tools || "", "nxcore", "stylebook_params.json"), "utf8")) || {}; }
    catch (e) { SPEC = {}; }
    return SPEC;
  }
  var cache = { m: -1, v: null };
  function load() {
    var st = null;
    try { st = fs.statSync(file()); } catch (e) {}
    if (!st) return { version: 1, rules: [] };
    if (cache.v && cache.m === st.mtimeMs) return cache.v;
    var j = null;
    try { j = JSON.parse(fs.readFileSync(file(), "utf8")); } catch (e) {}
    if (!j || !Array.isArray(j.rules)) j = { version: 1, rules: [] };
    cache = { m: st.mtimeMs, v: j };
    return j;
  }
  function save(j) {
    try { fs.mkdirSync(path.dirname(file()), { recursive: true }); } catch (e) {}
    var tmp = file() + ".tmp";
    fs.writeFileSync(tmp, JSON.stringify(j, null, 1), "utf8");
    fs.renameSync(tmp, file());
    cache = { m: -1, v: null };
    try { document.dispatchEvent(new CustomEvent("jes:stylebook")); } catch (e) {}
  }

  /* a proposed / typed value → the typed, clamped value the engine will use, or null (same rules as nxcore.stylebook.coerce) */
  function coerce(key, value) {
    var sp = spec()[key];
    if (!sp) return null;
    if (sp.type === "bool") {
      if (typeof value === "boolean") return value;
      var s = String(value).trim().toLowerCase();
      return /^(1|true|on|yes|có|bật)$/.test(s) ? true : /^(0|false|off|no|không|tắt)$/.test(s) ? false : null;
    }
    if (sp.type === "enum") { var e = String(value).trim().toLowerCase(); return (sp.values || []).indexOf(e) >= 0 ? e : null; }
    var v = parseFloat(String(value).replace(",", "."));
    if (!isFinite(v)) return null;
    v = Math.min(+sp.hi, Math.max(+sp.lo, v));
    return Math.round(v * 1000) / 1000;
  }
  function valText(key, v) {
    var sp = spec()[key] || {};
    if (sp.type === "bool") return v ? "bật" : "tắt";
    if (sp.type === "enum") return (sp.names || {})[v] || String(v);
    return String(v) + (sp.unit || "");
  }
  function paramText(p) { var sp = spec()[p.key] || {}; return (sp.label || p.key) + ": " + valText(p.key, p.value); }

  function uid() { return "r" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }
  /* rule = {text, scope, param: {key, value} | null, preset: "" | id, src: "note" | "manual" | "ai", from: [note texts]} → id */
  function add(r) {
    var j = load(), p = null;
    if (r.param && r.param.key) {
      var v = coerce(r.param.key, r.param.value);
      if (v !== null) p = { key: r.param.key, value: v };
    }
    var text = String(r.text || "").trim() || (p ? paramText(p) : "");
    if (!text) return null;
    var rule = { id: uid(), text: text.slice(0, 300), scope: SCOPES[r.scope] ? r.scope : (p ? scopeOfParam(p.key) : "chung"),
                 param: p, preset: r.preset || "", on: true, src: r.src || "manual", at: new Date().toISOString(),
                 from: (r.from || []).slice(0, 8) };
    // one live value per setting: an older rule on the same parameter (same preset scope) is switched off, kept for history
    if (p) j.rules.forEach(function (o) {
      if (o.on !== false && o.param && o.param.key === p.key && (o.preset || "") === rule.preset) { o.on = false; o.replacedBy = rule.id; }
    });
    j.rules.push(rule); save(j);
    return rule.id;
  }
  function update(id, patch) {
    var j = load(), hit = false;
    j.rules = j.rules.map(function (r) { if (r.id !== id) return r; hit = true; return Object.assign({}, r, patch); });
    if (hit) save(j);
    return hit;
  }
  function remove(id) { var j = load(); j.rules = j.rules.filter(function (r) { return r.id !== id; }); save(j); }
  function scopeOfParam(key) {
    var m = (spec()[key] || {}).mod;
    return m === "sound" ? "am_thanh" : m === "motion" ? "chu" : "dung";
  }
  function applies(r, preset) { return r.on !== false && (!r.preset || r.preset === (preset || NX.S.preset || "")); }

  /* enabled param rules of one engine module → {key: value} (mirror of nxcore.stylebook.params) */
  function params(mod, preset) {
    var out = {}, rank = {};
    load().rules.forEach(function (r) {
      if (!r.param || !applies(r, preset)) return;
      var sp = spec()[r.param.key];
      if (!sp || sp.mod !== mod) return;
      var v = coerce(r.param.key, r.param.value);
      if (v === null) return;
      var k = r.preset ? 1 : 0;
      if (!(r.param.key in out) || k >= rank[r.param.key]) { out[r.param.key] = v; rank[r.param.key] = k; }
    });
    return out;
  }
  function param(key, preset) {
    var sp = spec()[key];
    if (!sp) return undefined;
    var p = params(sp.mod, preset);
    return key in p ? p[key] : undefined;
  }

  /* the rules an AI prompt of these scopes must follow ("" when none) — "chung" always included */
  function promptText(scopes) {
    var want = { chung: 1 };
    (scopes || []).forEach(function (s) { want[s] = 1; });
    var lines = load().rules.filter(function (r) { return applies(r) && want[r.scope || "chung"]; }).map(function (r) {
      return "- " + r.text + (r.param ? " [" + paramText(r.param) + " — tool đã tự áp khi dựng]" : "");
    });
    if (!lines.length) return "";
    return "\n\nLUẬT PHONG CÁCH CỦA EDITOR (đã duyệt, LUÔN làm theo trừ khi lệnh hiện tại nói khác):\n" + lines.slice(0, 40).join("\n");
  }
  /* parameter list for the AI that proposes rules: key, meaning, allowed values */
  function paramGuide() {
    var sp = spec();
    return Object.keys(sp).map(function (k) {
      var s = sp[k];
      return k + ": " + s.label + " (" + (s.type === "bool" ? "true|false" : s.type === "enum" ? s.values.join("|") : s.lo + "–" + s.hi + (s.unit || "")) + ")";
    }).join("\n");
  }
  /* an AI-proposed rule → a clean proposal, or null */
  function clean(r) {
    if (!r || typeof r !== "object") return null;
    var text = String(r.text || "").trim();
    var p = r.param && r.param.key && spec()[r.param.key] ? { key: r.param.key, value: coerce(r.param.key, r.param.value) } : null;
    if (p && p.value === null) p = null;
    if (!text && !p) return null;
    return { text: text || paramText(p), scope: SCOPES[r.scope] ? r.scope : (p ? scopeOfParam(p.key) : "chung"), param: p };
  }

  // ------------------------------------------------------------------ UI: the rule list (+ add form)
  function esc(s) { return NX.esc ? NX.esc(s) : String(s == null ? "" : s); }
  function scopeSel(cls, cur) {
    return '<select class="' + cls + '">' + Object.keys(SCOPES).map(function (k) {
      return '<option value="' + k + '"' + (k === cur ? " selected" : "") + ">" + SCOPES[k] + "</option>";
    }).join("") + "</select>";
  }
  function paramSel(cur) {
    var sp = spec();
    return '<select class="sb-pk"><option value="">(chỉ là chữ — AI đọc)</option>' + Object.keys(sp).map(function (k) {
      return '<option value="' + k + '"' + (k === cur ? " selected" : "") + ">" + esc(sp[k].label) + "</option>";
    }).join("") + "</select>";
  }
  function render(el) {
    if (!el) return;
    var j = load(), pre = NX.S.preset || "";
    var rows = j.rules.slice().reverse().map(function (r) {
      return '<div class="sb-rule' + (r.on === false ? " off" : "") + '" data-id="' + r.id + '">' +
        '<label class="chk" title="Bật / tắt luật"><input type="checkbox" class="sb-on"' + (r.on === false ? "" : " checked") + "></label>" +
        '<div class="sb-body"><div class="sb-text" contenteditable="true" spellcheck="false">' + esc(r.text) + "</div>" +
        '<div class="sb-meta"><span class="sb-scope">' + esc(SCOPES[r.scope] || r.scope || "Chung") + "</span>" +
        (r.param ? ' <span class="sb-param">⚙ ' + esc(paramText(r.param)) + "</span>" : "") +
        (r.preset ? ' <span class="sb-pre' + (r.preset === pre ? "" : " muted") + '">chỉ preset ' + esc(r.preset) + "</span>" : "") +
        (r.replacedBy && r.on === false ? ' <span class="muted">đã thay bằng luật mới hơn</span>' : "") +
        ((r.from || []).length ? ' <span class="muted" title="' + esc(r.from.join("\n")) + '">từ ' + r.from.length + " ghi chú</span>" : "") +
        '</div></div><button class="btn chip sb-del" title="Xóa luật">✕</button></div>';
    });
    el.innerHTML = (rows.length ? rows.join("") : '<div class="muted" style="padding:4px 0">Chưa có luật nào — ghi chú bản dựng rồi để AI đề xuất, hoặc thêm tay bên dưới.</div>') +
      '<div class="sb-add"><input class="sb-nt" placeholder="Luật mới, vd: hook luôn dưới 5 giây · không dùng whoosh ở video phỏng vấn">' +
      '<div class="row" style="gap:6px;margin-top:4px">' + scopeSel("sb-ns", "chung") + paramSel("") +
      '<input class="sb-pv" placeholder="giá trị" style="width:70px;display:none">' +
      '<label class="chk" style="font-size:11px" title="Chỉ áp khi dùng preset đang chọn"><input type="checkbox" class="sb-np"> chỉ preset ' + esc(pre) + "</label>" +
      '<button class="btn sb-addb" style="margin-left:auto">＋ Thêm luật</button></div></div>';
    Array.prototype.forEach.call(el.querySelectorAll(".sb-rule"), function (row) {
      var id = row.dataset.id;
      row.querySelector(".sb-on").onchange = function (e) { update(id, { on: e.target.checked }); row.classList.toggle("off", !e.target.checked); };
      row.querySelector(".sb-del").onclick = function () { remove(id); render(el); };
      var tx = row.querySelector(".sb-text");
      tx.onblur = function () { var t = tx.textContent.trim(); if (t) update(id, { text: t }); else render(el); };
      tx.onkeydown = function (e) { if (e.key === "Enter") { e.preventDefault(); tx.blur(); } };
    });
    var pk = el.querySelector(".sb-pk"), pv = el.querySelector(".sb-pv");
    pk.onchange = function () {
      var s = spec()[pk.value];
      pv.style.display = s ? "" : "none";
      pv.placeholder = s ? (s.type === "bool" ? "bật / tắt" : s.type === "enum" ? s.values.join(" / ") : s.lo + "–" + s.hi + (s.unit || "")) : "";
      if (s) el.querySelector(".sb-ns").value = scopeOfParam(pk.value);
    };
    el.querySelector(".sb-addb").onclick = function () {
      var t = el.querySelector(".sb-nt").value.trim(), key = pk.value, p = null;
      if (key) {
        var v = coerce(key, pv.value);
        if (v === null) { pv.focus(); NX.log("• Giá trị không hợp lệ cho " + spec()[key].label, "#e0a82e"); return; }
        p = { key: key, value: v };
      }
      if (!t && !p) { el.querySelector(".sb-nt").focus(); return; }
      add({ text: t, scope: el.querySelector(".sb-ns").value, param: p, preset: el.querySelector(".sb-np").checked ? pre : "", src: "manual" });
      render(el);
    };
  }
  function count() { var r = load().rules; return { all: r.length, on: r.filter(function (x) { return x.on !== false; }).length }; }

  window.JESStyle = { load: load, add: add, update: update, remove: remove, params: params, param: param, coerce: coerce,
                      promptText: promptText, paramGuide: paramGuide, clean: clean, paramText: paramText, render: render,
                      count: count, SCOPES: SCOPES, spec: spec };
})();
