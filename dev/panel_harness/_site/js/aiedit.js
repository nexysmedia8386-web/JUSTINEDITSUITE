/* Justin EDIT Suite v2.1 — tab "Sửa AI": AI editing of the current plan, always preview-then-apply.
   Modes: Rà lỗi (nxcore.aiedit review) · Ra lệnh (LLM → ops → resolve) · Transcript (cut words / fillers) ·
   Hậu kỳ (reframe / colour match / stabilise applied to the open sequence through host.jsx jesApplyPost).
   Applying plan changes writes plan_<name>_vN+1.json and builds a NEW sequence — the old version is never touched. */
(function () {
  var NX = window.NX;
  if (!NX) return;
  var cp = require("child_process"), fs = require("fs"), path = require("path"), os = require("os");
  function $(id) { return document.getElementById(id); }
  function esc(s) { return NX.esc ? NX.esc(s) : String(s == null ? "" : s); }
  function fileUrl(p) { return "file:///" + encodeURI(String(p).replace(/\\/g, "/").replace(/^\/+/, "")).replace(/#/g, "%23"); }
  function mmss(t) { t = Math.max(0, +t || 0); var m = Math.floor(t / 60), s = (t - m * 60).toFixed(1); return m + ":" + (s < 10 ? "0" : "") + s; }
  function base(f) { return String(f || "").split("/").pop(); }
  var KIND = { replace: "Thay hình", slide: "Đổi khoảnh khắc", trim: "Cắt gọn", remove: "Xóa shot", move: "Di chuyển",
    swap: "Đổi chỗ", speed: "Tốc độ", scale: "Zoom", cut_words: "Cắt chữ", replan: "Dựng lại" };
  var ST = { mode: "review", changes: [], busy: false, blocks: null, post: [] };
  var TMP = path.join(os.tmpdir(), "jes_aiedit_" + process.pid + ".json");

  function planPath() { return (NX.getPlanPath && NX.getPlanPath()) || ""; }
  function status(t, cls) { var e = $("aeStatus"); if (e) { e.textContent = t || ""; e.className = "ae-status " + (cls || ""); } }

  /* One AI-edit command → result object. opt: {ops, thumbs, want}.
     Normal path: the shared AI worker (js/aiworker.js) — footage data stays in memory, no process start per command.
     Fallback (worker failed to start 3×): python -m nxcore.aiedit <cmd> <plan> as a one-shot process. */
  function aiedit(cmd, opt) {
    opt = opt || {};
    var pp = planPath();
    if (!pp || !fs.existsSync(pp)) return Promise.reject(new Error("chưa có plan — chạy Align / dựng montage trước"));
    if (window.JESWorker && window.JESWorker.usable()) {
      return window.JESWorker.ask({ op: "aiedit", cmd: cmd, plan: pp, ops: opt.ops || null, thumbs: !!opt.thumbs, want: opt.want || "" },
        { onLog: function (m) { if (/^aiedit:|^\[\d+\/\d+\]/.test(m)) status(m.replace(/^aiedit:\s*/, ""), "run"); } })
        .then(function (r) {
          if (r.err) throw new Error(r.err);
          return r.data;
        });
    }
    var extra = [];
    if (opt.ops) { fs.writeFileSync(TMP, JSON.stringify(opt.ops), "utf8"); extra.push("--ops", TMP); }
    if (opt.thumbs) extra.push("--thumbs");
    if (opt.want) extra.push("--want", opt.want);
    return new Promise(function (resolve, reject) {
      var env = Object.assign({}, process.env, { PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" }), p, buf = "", out = "";
      try { p = cp.spawn(NX.S.python || "python", ["-m", "nxcore.aiedit", cmd, pp].concat(extra), { cwd: NX.S.tools, env: env, windowsHide: true }); }
      catch (e) { return reject(e); }
      if (NX.trackChild) NX.trackChild(p);
      function feed(d) {
        buf += d; var ls = buf.split(/\r?\n/); buf = ls.pop();
        ls.forEach(function (l) { out += l + "\n"; if (/^aiedit:|^\[\d+\/\d+\]/.test(l)) status(l.replace(/^aiedit:\s*/, "")); });
      }
      p.stdout.setEncoding("utf8"); p.stderr.setEncoding("utf8"); p.stdout.on("data", feed); p.stderr.on("data", feed);
      p.on("error", reject);
      p.on("close", function (code) {
        out += buf;
        var l = out.split(/\r?\n/).filter(function (x) { return x.indexOf("AIEDIT ") === 0; }).pop();
        if (!l) { var tail = out.split(/\r?\n/).filter(Boolean).slice(-2).join(" | "); return reject(new Error(tail || ("aiedit " + cmd + " lỗi " + code))); }
        try { resolve(JSON.parse(l.slice(7))); } catch (e) { reject(e); }
      });
    });
  }
  function busy(on, msg) {
    ST.busy = on;
    Array.prototype.forEach.call(document.querySelectorAll("#aiEdit button"), function (b) { b.disabled = on && !b.classList.contains("ae-keep"); });
    status(msg || (on ? "Đang xử lý…" : ""), on ? "run" : "");
  }
  function guard(fn, label) {
    if (ST.busy) return;
    busy(true, label);
    return Promise.resolve().then(fn).then(function () { busy(false); }, function (e) {
      busy(false); status("✖ " + (e && e.message || e), "bad"); NX.log("✖ Sửa AI: " + (e && e.message || e), "#e5534b");
    });
  }

  // ===================================================================== change set preview (shared by all modes)
  function thumbImg(v, label) {
    if (!v || !v.file) return '<div class="ae-th empty">' + esc(label || "—") + "</div>";
    return '<div class="ae-th" data-f="' + esc(v.file) + '" data-a="' + v["in"] + '" data-b="' + v.out + '" title="' + esc(v.file) + " · " + mmss(v["in"]) +
      '">' + (v.thumb ? '<img src="' + fileUrl(v.thumb) + '">' : "") + "<span>" + esc(base(v.file)) + " " + mmss(v["in"]) + "</span></div>";
  }
  function renderChanges(list, title) {
    ST.changes = list || [];
    ST.fromNotes = null;                              // runNotes sets it again right after its own change set
    var card = $("aeChCard"), box = $("aeList");
    card.style.display = ST.changes.length ? "" : "none";
    $("aeChMeta").textContent = title || "";
    box.innerHTML = ST.changes.map(function (c, i) {
      var ok = c.ok !== false, k = KIND[c.kind] || c.kind;
      var where = c.seg != null ? "#" + c.seg + (c.t != null ? " · " + mmss(c.t) : "") : "";
      var detail = "";
      if (c.kind === "cut_words") detail = "“" + esc(c.text || "") + "” " + mmss(c.t0) + "–" + mmss(c.t1);
      else if (c.kind === "speed") detail = (c.after && c.after.speed) + "%";
      else if (c.kind === "scale") detail = (c.after && c.after.scale) + "%";
      else if (c.kind === "swap" || c.kind === "move") detail = "↔ #" + c.to;
      else if (c.kind === "replan") detail = Object.keys(c.cfg || {}).map(function (x) { return x + "=" + c.cfg[x]; }).join(", ");
      if (c.vlm != null) detail += (detail ? " · " : "") + "AI xem kỹ: " + Math.round(c.vlm * 10) + "/10";
      var pics = (c.kind === "replace" || c.kind === "slide" || c.kind === "swap") ?
        '<div class="ae-pics">' + thumbImg(c.before, "trước") + '<i>→</i>' + thumbImg(c.after, "sau") + "</div>" :
        (c.before && c.kind !== "replan" ? '<div class="ae-pics">' + thumbImg(c.before) + "</div>" : "");
      return '<label class="ae-ch' + (ok ? "" : " bad") + '"><input type="checkbox" data-i="' + i + '"' + (ok ? " checked" : " disabled") + ">" +
        '<div class="ae-body"><div class="ae-head"><span class="ae-kind k-' + c.kind + '">' + esc(k) + "</span><b>" + esc(where) + "</b> <span class=\"muted\">" + detail + "</span></div>" +
        pics + (c.why ? '<div class="ae-why">' + esc(c.why) + "</div>" : "") + (ok ? "" : '<div class="ae-why bad">✖ ' + esc(c.err) + "</div>") +
        (c.desc ? '<div class="ae-why muted">' + esc(c.desc).slice(0, 120) + "</div>" : "") + "</div></label>";
    }).join("");
    Array.prototype.forEach.call(box.querySelectorAll(".ae-th[data-f]"), function (t) {
      t.onclick = function (e) {
        e.preventDefault();
        var full = (NX.S.src + "/" + t.dataset.f).replace(/\\/g, "/");
        NX.evalHost("nxOpenInSource(" + JSON.stringify(full) + "," + (+t.dataset.a) + "," + (+t.dataset.b) + ")");
      };
    });
    countSel();
    box.onchange = countSel;
  }
  function countSel() {
    var n = document.querySelectorAll("#aeList input[type=checkbox]:checked").length;
    var b = $("aeApply"); if (b) b.textContent = n ? "Áp dụng " + n + " thay đổi" : "Áp dụng";
  }
  function selected() {
    var on = {};
    Array.prototype.forEach.call(document.querySelectorAll("#aeList input[type=checkbox]"), function (x) { on[x.dataset.i] = x.checked; });
    return ST.changes.map(function (c, i) { return Object.assign({}, c, { skip: !on[i] }); });
  }
  function applyChanges() {
    var ch = selected(), n = ch.filter(function (c) { return !c.skip && c.ok !== false; }).length;
    if (!n) { status("Chưa chọn thay đổi nào"); return; }
    var notes = ST.fromNotes;
    return guard(function () {
      return aiedit("apply", { ops: ch }).then(function (r) {
        NX.log("✔ Sửa AI: " + n + " thay đổi → " + r.name + (r.note ? " (" + r.note + ")" : ""), "#4cc38a");
        NX.setPlanPath(r.plan);
        renderChanges([], "");
        if (notes) {                                  // the new sequence has other timings: these notes are done
          NX.S.edNotesLast = notes; NS().items = NS().items.filter(function (x) { return notes.indexOf(x) < 0; }); NX.save();
          renderNotes();
        }
        status("Đang build " + r.name + "…", "run");
        return NX.build();
      }).then(function () { status("✔ Đã tạo sequence mới — bản cũ giữ nguyên, so sánh hai bản trong Project", "ok"); refreshMeta(); });
    }, "Đang áp dụng…");
  }

  // ===================================================================== mode: review
  function runReview() {
    return guard(function () {
      return aiedit("review", { thumbs: true }).then(function (r) {
        var nI = (r.issues || []).length, nF = (r.changes || []).filter(function (c) { return c.ok; }).length;
        renderChanges(r.changes, nI + " vấn đề · " + nF + " cách sửa");
        var noFix = (r.issues || []).filter(function (x) { return !x.fix; });
        $("aeReviewNote").innerHTML = !nI ? '<span class="ok">✔ Không thấy lỗi nào trong bản dựng.</span>' :
          (noFix.length ? "Không tự sửa được " + noFix.length + " vấn đề: " + noFix.slice(0, 6).map(function (x) { return "#" + x.seg + " " + esc(x.msg); }).join(" · ") : "");
        status(nI ? "Bỏ tick những thay đổi không muốn rồi bấm Áp dụng" : "Bản dựng ổn", nI ? "" : "ok");
      });
    }, "AI đang rà bản dựng (lặp cảnh, cắt giữa hành động, rung, thoại trong montage…)");
  }

  // ===================================================================== mode: command (LLM)
  var CMD_SYS = [
    "Bạn là trợ lý dựng phim trong Premiere. Người dùng ra lệnh sửa một bản dựng bằng tiếng Việt.",
    "Bạn nhận danh sách shot: #chỉ_số thời_gian (độ dài) [đoạn nhạc hoặc loại] tên_file · nội dung (nhãn tự động).",
    "Trả về DUY NHẤT JSON: {\"reply\": \"câu trả lời ngắn tiếng Việt\", \"ops\": [ ... ]}. Các op hợp lệ:",
    "{\"kind\":\"replace\",\"seg\":i,\"query\":\"mô tả cảnh muốn thay, tiếng Việt hoặc Anh\",\"why\":\"...\"}  — seg có thể là danh sách [i,j,...]",
    "{\"kind\":\"slide\",\"seg\":i,\"by\":giây}  (cùng clip, lấy khoảnh khắc sớm/muộn hơn)",
    "{\"kind\":\"remove\",\"seg\":i}   {\"kind\":\"swap\",\"seg\":i,\"with\":j}   {\"kind\":\"move\",\"seg\":i,\"to\":j}",
    "{\"kind\":\"speed\",\"seg\":i,\"speed\":50}  (slow-mo 50 = chậm một nửa)   {\"kind\":\"scale\",\"seg\":i,\"scale\":112}",
    "{\"kind\":\"trim\",\"seg\":i,\"start\":+giây,\"end\":-giây}  (CHỈ cho bản dựng tự do, KHÔNG cho montage theo nhạc)",
    "{\"kind\":\"replan\",\"cfg\":{\"pace\":0.8}}  (montage theo nhạc: dựng lại toàn bộ; pace < 1 = cắt nhanh hơn, > 1 = chậm hơn; min_shot, max_shot giây)",
    "Quy tắc: chỉ dùng chỉ số shot có trong danh sách; montage theo nhạc giữ nguyên thời lượng từng ô nhịp (dùng replace/slide/swap, không trim);",
    "lệnh về nhịp chung (nhanh hơn, chậm hơn, dồn dập hơn) → replan; lệnh mơ hồ → chọn cách an toàn nhất và giải thích trong reply;",
    "không bịa nội dung shot ngoài nhãn đã cho; nếu không làm được thì ops rỗng và nói lý do."
  ].join("\n");

  function styleText(scopes) { return window.JESStyle ? window.JESStyle.promptText(scopes) : ""; }
  /* system + user message → the model's JSON answer (route "AI Assist (agent)") */
  function llmJSON(sys, msg, maxTokens) {
    var R = window.NXAI && window.NXAI.routeOf ? window.NXAI.routeOf("chat") : { provider: NX.S.provider, model: NX.S.model, key: NX.S.apiKey };
    status("AI (" + R.provider + ") đang đọc…", "run");
    // local models: a context window just big enough (Vietnamese with diacritics ≈ 2 chars/token; Ollama truncates silently) — 16K on an 8 GB card spills to CPU (10× slower)
    var need = Math.ceil((msg.length + sys.length) / 2) + 1500, ctxN = 4096;
    while (ctxN < need && ctxN < 32768) ctxN *= 2;
    var llm = window.NXLLM.create(R.provider, R.key, R.model || undefined, null,
                                  { numCtx: R.provider === "ollama" ? Math.min(ctxN, Math.max(4096, +NX.S.numCtx || 16384)) : NX.S.numCtx });
    llm.userTurn(msg);
    return llm.send(sys, null, { json: true, temperature: 0.2, maxTokens: maxTokens || 4000 }).then(function (r) { return window.NXLLM.parseJSON(r.text) || {}; });
  }
  /* the model's ops → concrete changes in the preview (same path for a typed command and for notes) */
  function handleOps(j, why, title) {
    var ops = (j && j.ops) || [];
    $("aeReply").innerHTML = '<div class="ae-reply"><b>AI:</b> ' + esc(j.reply || "(không có trả lời)") + "</div>";
    if (!ops.length) { renderChanges([], ""); status("AI không đề xuất thay đổi nào trên hình"); return Promise.resolve([]); }
    var deep = false;
    try { deep = localStorage.getItem("jesFindDeep") === "1"; } catch (e) {}
    ops.forEach(function (o) {
      if (!o.why) o.why = why;
      if (deep && o.query) o.verify = true;          // B2: the vision model checks the 8 best candidates first
    });
    status(deep ? "Đang tìm cảnh phù hợp (SigLIP2 + AI xem kỹ ~5 s/cảnh + luật dựng)…" : "Đang tìm cảnh phù hợp (SigLIP2 + luật dựng)…", "run");
    // the search worker (model already in memory) embeds the scene descriptions into the shared text cache, so the
    // resolve process does not load SigLIP2 again (~8 s); without the worker resolve simply loads it itself
    var qs = ops.map(function (o) { return o.query; }).filter(Boolean);
    var pre = (qs.length && window.JESAI && window.JESAI.srvAsk)
      ? window.JESAI.srvAsk({ embed: qs }).then(null, function () {}) : Promise.resolve();
    return pre.then(function () { return aiedit("resolve", { ops: ops, thumbs: true }); }).then(function (x) {
      renderChanges(x.changes, title || ops.length + " thao tác");
      status("Xem lại rồi bấm Áp dụng");
      return x.changes;
    });
  }
  function runCommand() {
    var cmd = ($("aeCmd").value || "").trim();
    if (!cmd) return;
    return guard(function () {
      return aiedit("context").then(function (c) {
        return llmJSON(CMD_SYS + styleText(["dung"]), "BẢN DỰNG: " + JSON.stringify(c.head) + "\n" + c.text + "\n\nLỆNH: " + cmd);
      }).then(function (j) { return handleOps(j, "lệnh: " + cmd, ((j && j.ops) || []).length + " thao tác từ lệnh"); });
    }, "Đang đọc bản dựng…");
  }

  // ===================================================================== mode: notes (📍 Ghi chú → AI sửa → Sổ luật)
  /* Watch the built sequence in Premiere, stop where something is off, type a note: it is pinned to the playhead second
     (or comes from a sequence marker with a comment, key M). "AI sửa" turns ALL notes into one previewable change set
     (same resolve / apply path as a typed command) and proposes lasting rules for the style book. Only the rules the
     editor ticks are saved; from then on they shape every build (param rules) and every AI prompt (text rules). */
  var NOTES_SYS = [
    "CHẾ ĐỘ GHI CHÚ: editor đã xem bản dựng và để lại ghi chú theo thời điểm trên timeline; mỗi ghi chú đã kèm shot tương ứng.",
    "1) Ghi chú nào LÀM ĐƯỢC bằng các op ở trên → op cho đúng shot đi kèm (why = nội dung ghi chú). Ghi chú về âm thanh, chữ, màu,",
    "   đồ họa… không có op → không bịa op, nói ngắn trong reply phần đó làm ở đâu (tab Âm thanh / Chữ / Hậu kỳ).",
    "2) \"rules\": luật LÂU DÀI cho những lần dựng sau, CHỈ khi ghi chú cho thấy GU CHUNG của editor (vd 'zoom to quá' → punch-in",
    "   nhỏ hơn; 'shot nào cũng dài' → B-roll ngắn hơn; 'SFX ồn' → SFX nhỏ hơn). KHÔNG tạo luật cho sửa nội dung một lần",
    "   (vd 'thay cảnh này bằng cảnh biển'). Tối đa 3 luật, không có thì [].",
    "   Mỗi luật: {\"text\": \"câu mệnh lệnh ngắn tiếng Việt\", \"scope\": \"dung|am_thanh|chu|kich_ban|chung\", \"param\": {\"key\": \"…\", \"value\": …} | null}.",
    "   param CHỈ khi luật đúng là một tham số dưới đây (giá trị trong khoảng cho phép); còn lại param = null:",
    "__PARAMS__",
    "TRẢ VỀ DUY NHẤT JSON: {\"reply\": \"…\", \"ops\": [ … ], \"rules\": [ … ]}"
  ].join("\n");
  function NS() { var n = NX.S.edNotes; if (!n || !Array.isArray(n.items)) n = NX.S.edNotes = { items: [] }; return n; }
  function seqNow() {
    return NX.evalHost("jesSequence(1)").then(function (r) {
      var j = JSON.parse(r); if (!j.ok) throw new Error(j.error || "chưa mở sequence trong Premiere"); return j;
    });
  }
  function byT(a, b) { return a.t - b.t; }
  function addNote() {
    var tx = ($("aeNoteText").value || "").trim();
    if (!tx) { $("aeNoteText").focus(); return; }
    seqNow().then(function (j) {
      NS().items.push({ t: Math.round((+j.playhead || 0) * 100) / 100, text: tx, seq: j.name });
      NS().items.sort(byT); NX.save();
      $("aeNoteText").value = ""; renderNotes(); $("aeNoteText").focus();
    }).catch(function (e) { status("✖ " + e.message, "bad"); });
  }
  function fromMarkers() {
    seqNow().then(function (j) {
      var have = {}, chap = {}, n = 0;
      NS().items.forEach(function (x) { have[x.t.toFixed(1) + "|" + x.text] = 1; });
      (ST.chapters || []).forEach(function (c) { chap[String(c.title || c.name || "").trim()] = 1; });
      (j.markers || []).forEach(function (m) {
        var name = String(m.name || "").trim(), tx = String(m.comment || "").trim() || name;
        if (!tx || /^NX /.test(name) || (m.type && m.type !== "Comment") || (!m.comment && chap[name])) return;   // tool markers / chapters are not notes
        var t = Math.round((+m.t || 0) * 100) / 100;
        if (have[t.toFixed(1) + "|" + tx]) return;
        NS().items.push({ t: t, text: tx, seq: j.name, mk: 1 }); n++;
      });
      NS().items.sort(byT); NX.save(); renderNotes();
      status(n ? "Đã lấy " + n + " ghi chú từ marker của " + j.name : "Không có marker mới có ghi chú (đặt marker bằng phím M, gõ vào Name / Comments)", n ? "ok" : "");
    }).catch(function (e) { status("✖ " + e.message, "bad"); });
  }
  function renderNotes() {
    var box = $("aeNotes"); if (!box) return;
    var it = NS().items, seqs = {};
    it.forEach(function (n) { seqs[n.seq || "?"] = 1; });
    $("aeNoteMeta").textContent = it.length ? it.length + " ghi chú · " + Object.keys(seqs).join(", ") : "";
    $("aeNoteRun").textContent = it.length ? "✨ AI sửa theo " + it.length + " ghi chú" : "✨ AI sửa theo ghi chú";
    box.innerHTML = it.length ? it.map(function (n, i) {
      return '<div class="ae-note"><button class="btn-nav tl" data-t="' + n.t + '" title="Nhảy playhead tới đây">' + mmss(n.t) + "</button>" +
        '<span class="ae-nt" contenteditable="true" spellcheck="false" data-i="' + i + '">' + esc(n.text) + "</span>" +
        (n.mk ? '<span class="muted" title="Lấy từ marker">M</span>' : "") + '<button class="btn chip ae-ndel ae-keep" data-i="' + i + '" title="Xóa ghi chú">✕</button></div>';
    }).join("") : '<div class="muted" style="padding:4px 0">Chưa có ghi chú.</div>';
    Array.prototype.forEach.call(box.querySelectorAll(".btn-nav[data-t]"), function (b) {
      b.onclick = function () { NX.evalHost("jesSetPlayhead(" + (+b.dataset.t) + ")"); };
    });
    Array.prototype.forEach.call(box.querySelectorAll(".ae-nt"), function (s) {
      s.onblur = function () { var n = it[+s.dataset.i], t = s.textContent.trim(); if (!n) return; if (t) { n.text = t; NX.save(); } else renderNotes(); };
      s.onkeydown = function (e) { if (e.key === "Enter") { e.preventDefault(); s.blur(); } };
    });
    Array.prototype.forEach.call(box.querySelectorAll(".ae-ndel"), function (b) {
      b.onclick = function () { it.splice(+b.dataset.i, 1); NX.save(); renderNotes(); };
    });
  }
  function clearNotes() {
    if (!NS().items.length) return;
    NX.ask("Xóa hết " + NS().items.length + " ghi chú?", ["Xóa hết", "Giữ lại"]).then(function (a) {
      if (a !== "Xóa hết") return;
      NX.S.edNotesLast = NS().items; NS().items = []; NX.save(); renderNotes();
    });
  }
  /* "#i m:ss.s d.ds …" / "#i a–b THIẾU HÌNH" lines of nxcore.aiedit context → [{i, a, b}] timeline spans */
  function ctxSegs(text) {
    var out = [];
    String(text || "").split("\n").forEach(function (l) {
      var m = /^#(\d+) (\d+):(\d+(?:\.\d+)?)(?:–(\d+):(\d+(?:\.\d+)?)| (\d+(?:\.\d+)?)s)/.exec(l);
      if (!m) return;
      var a = +m[2] * 60 + +m[3], b = m[4] != null ? +m[4] * 60 + +m[5] : a + +m[6];
      out.push({ i: +m[1], a: a, b: b });
    });
    return out;
  }
  function segAt(segs, t) {
    for (var k = 0; k < segs.length; k++) if (t >= segs[k].a - 0.05 && t < segs[k].b) return segs[k].i;
    return segs.length && t >= segs[segs.length - 1].a ? segs[segs.length - 1].i : null;
  }
  function runNotes() {
    var items = NS().items.slice();
    if (!items.length) { status("Chưa có ghi chú — dừng playhead ở chỗ cần sửa rồi gõ ghi chú"); $("aeNoteText").focus(); return; }
    return guard(function () {
      var ctx;
      return aiedit("context").then(function (c) {
        ctx = c;
        var other = items.filter(function (n) { return n.seq && c.head.name && n.seq.indexOf(c.head.name) !== 0; });
        if (!other.length) return "ok";
        return NX.ask("Ghi chú ghi trên sequence " + other[0].seq + " nhưng plan đang mở là " + c.head.name +
                      " — thời điểm có thể không khớp shot. Vẫn tiếp tục?", ["Tiếp tục", "Hủy"]);
      }).then(function (a) {
        if (a !== "ok" && a !== "Tiếp tục") throw new Error("đã hủy — mở đúng plan của sequence (tab Review → Tải plan)");
        var segs = ctxSegs(ctx.text);
        var lines = items.map(function (n) { var s = segAt(segs, n.t); return "- [" + mmss(n.t) + (s != null ? " → shot #" + s : "") + "] " + n.text; });
        var sys = CMD_SYS + "\n\n" + NOTES_SYS.replace("__PARAMS__", window.JESStyle ? window.JESStyle.paramGuide() : "") + styleText(["dung"]);
        return llmJSON(sys, "BẢN DỰNG: " + JSON.stringify(ctx.head) + "\n" + ctx.text + "\n\nGHI CHÚ CỦA EDITOR (thời điểm trên timeline → shot):\n" + lines.join("\n"), 5000);
      }).then(function (j) {
        var props = (window.JESStyle ? (j.rules || []).map(window.JESStyle.clean).filter(Boolean) : []).slice(0, 3);
        renderProposals(props, items);
        var segs = ctxSegs(ctx.text), bySeg = {};
        items.forEach(function (n) { var s = segAt(segs, n.t); if (s != null) bySeg[s] = (bySeg[s] ? bySeg[s] + " · " : "📍 " + mmss(n.t) + " ") + n.text; });
        (j.ops || []).forEach(function (o) { if (!o.why && typeof o.seg === "number" && bySeg[o.seg]) o.why = bySeg[o.seg]; });   // the preview shows WHICH note
        return handleOps(j, "ghi chú", ((j.ops || []).length) + " thao tác từ " + items.length + " ghi chú").then(function (ch) {
          ST.fromNotes = ch && ch.length ? items : null;
          if (!ch || !ch.length) status(props.length ? "Không có thay đổi trên hình — xem luật đề xuất bên dưới" : "AI không đề xuất thay đổi nào");
        });
      });
    }, "Đang đọc bản dựng + ghi chú…");
  }
  function renderProposals(list, items) {
    var el = $("aeRuleProp"); if (!el) return;
    if (!list.length) { el.innerHTML = ""; return; }
    var SC = window.JESStyle.SCOPES, from = items.map(function (n) { return mmss(n.t) + " " + n.text; });
    el.innerHTML = '<div class="ae-props"><div class="ae-props-h">📒 Luật đề xuất cho những lần dựng sau <span class="muted">— sửa chữ được, chỉ lưu luật được tick</span></div>' +
      list.map(function (r, i) {
        return '<div class="ae-prop"><input type="checkbox" data-i="' + i + '" checked>' +
          '<div class="sb-body"><div class="sb-text" contenteditable="true" spellcheck="false" data-i="' + i + '">' + esc(r.text) + "</div>" +
          '<div class="sb-meta"><span class="sb-scope">' + esc(SC[r.scope] || r.scope) + "</span>" +
          (r.param ? ' <span class="sb-param">⚙ ' + esc(window.JESStyle.paramText(r.param)) + " — tool tự áp khi dựng</span>" : ' <span class="muted">AI đọc khi làm việc</span>') +
          "</div></div></div>";
      }).join("") +
      '<div class="row" style="gap:6px;margin-top:6px"><label class="chk" style="font-size:11px" title="Chỉ áp khi dùng preset này"><input type="checkbox" id="aePropPre"> chỉ preset ' + esc(NX.S.preset || "") + "</label>" +
      '<button class="btn" id="aePropSkip" style="margin-left:auto">Bỏ qua</button><button class="btn main ae-keep" id="aePropSave">Lưu vào Sổ luật</button></div></div>';
    $("aePropSkip").onclick = function () { el.innerHTML = ""; };
    $("aePropSave").onclick = function () {
      var pre = $("aePropPre").checked ? NX.S.preset || "" : "", n = 0;
      Array.prototype.forEach.call(el.querySelectorAll(".ae-prop"), function (row) {
        var cb = row.querySelector("input"), r = list[+cb.dataset.i];
        if (!cb.checked || !r) return;
        var t = row.querySelector(".sb-text").textContent.trim() || r.text;
        if (window.JESStyle.add({ text: t, scope: r.scope, param: r.param, preset: pre, src: "note", from: from })) n++;
      });
      el.innerHTML = n ? '<div class="hint ok">✔ Đã lưu ' + n + " luật vào Sổ luật — áp từ lần dựng / lệnh AI tiếp theo.</div>" : "";
      bookMeta();
      if ($("aeBook") && $("aeBook").open) window.JESStyle.render($("aeBookList"));
    };
  }
  function bookMeta() {
    var el = $("aeBookMeta"); if (!el || !window.JESStyle) return;
    var c = window.JESStyle.count();
    el.textContent = c.all ? c.on + "/" + c.all + " luật đang bật" : "chưa có luật";
  }

  // ===================================================================== mode: transcript
  /* C3: a coloured chip where the speaker changes (words carry "S1"… once the folder was diarized) */
  function spkChip(s) {
    if (!s) return "";
    var n = (parseInt(String(s).slice(1), 10) || 1) - 1;
    return '<span class="ae-spk c' + (n % 6) + '" title="Người nói ' + esc(s) + '">' + esc(s) + "</span>";
  }
  function loadTranscript() {
    return guard(function () { return aiedit("transcript").then(renderTranscript); }, "Đang đọc transcript của bản dựng…");
  }
  function renderTranscript(r) {
        ST.blocks = r.blocks || [];
        var box = $("aeTx"), speech = ST.blocks.filter(function (b) { return b.kind === "speech"; });
        if (!speech.length) { box.innerHTML = '<div class="muted">Bản dựng này không có đoạn thoại (montage / chỉ B-roll) — dùng Rà lỗi hoặc Ra lệnh.</div>'; return; }
        var prev = null;
        box.innerHTML = ST.blocks.map(function (b) {
          if (b.kind !== "speech") return '<div class="ae-broll">🎞 #' + b.seg + " " + esc(b.label || base(b.file) || "") + "</div>";
          return '<p class="ae-par" data-seg="' + b.seg + '"><span class="ae-segno">#' + b.seg + "</span>" + b.words.map(function (w) {
            var chip = w[3] && w[3] !== prev ? spkChip(w[3]) + " " : "";
            if (w[3]) prev = w[3];
            return chip + '<span class="w" data-seg="' + b.seg + '" data-t0="' + w[0] + '" data-t1="' + w[1] + '">' + esc(w[2]) + "</span>";
          }).join(" ") + "</p>";
        }).join("");
        var last = null;
        Array.prototype.forEach.call(box.querySelectorAll(".w"), function (w) {
          w.onclick = function (e) {
            if (e.shiftKey && last && last.dataset.seg === w.dataset.seg) {      // shift-click: mark a run of words
              var all = Array.prototype.slice.call(box.querySelectorAll('.w[data-seg="' + w.dataset.seg + '"]'));
              var a = all.indexOf(last), b = all.indexOf(w), on = !w.classList.contains("cut");
              all.slice(Math.min(a, b), Math.max(a, b) + 1).forEach(function (x) { x.classList.toggle("cut", on); });
            } else w.classList.toggle("cut");
            last = w;
            var n = box.querySelectorAll(".w.cut").length;
            $("aeCutSel").textContent = n ? "Cắt " + n + " chữ đã đánh dấu" : "Cắt chữ đã đánh dấu";
          };
        });
        status("Bấm chữ để đánh dấu (Shift+bấm = cả đoạn) → Cắt chữ đã đánh dấu");
  }
  function cutMarked() {
    var box = $("aeTx"), ops = [], run = null;
    Array.prototype.forEach.call(box.querySelectorAll(".w"), function (w) {
      var on = w.classList.contains("cut"), seg = +w.dataset.seg;
      if (on && run && run.seg === seg) { run.t1 = +w.dataset.t1; run.text += " " + w.textContent; return; }
      if (run) { ops.push(run); run = null; }
      if (on) run = { kind: "cut_words", seg: seg, t0: +w.dataset.t0, t1: +w.dataset.t1, text: w.textContent, why: "cắt trong transcript" };
    });
    if (run) ops.push(run);
    if (!ops.length) { status("Chưa đánh dấu chữ nào"); return; }
    return guard(function () {
      return aiedit("resolve", { ops: ops }).then(function (r) { renderChanges(r.changes, ops.length + " đoạn chữ cần cắt"); });
    }, "Đang chuẩn bị…");
  }
  function findFillers() {
    return guard(function () {
      return aiedit("fillers").then(function (r) {
        renderChanges(r.changes, (r.changes || []).length + " từ đệm / lặp từ / khoảng lặng");
        if (ST.blocks) (r.changes || []).forEach(function (c) {          // show them in the transcript too
          Array.prototype.forEach.call(document.querySelectorAll('#aeTx .w[data-seg="' + c.seg + '"]'), function (w) {
            if (+w.dataset.t0 >= c.t0 - 0.01 && +w.dataset.t1 <= c.t1 + 0.01) w.classList.add("cut");
          });
        });
        status((r.changes || []).length ? "Xem lại các chỗ cắt rồi bấm Áp dụng" : "Không thấy từ đệm / khoảng lặng dài", "");
      });
    }, "Đang tìm từ đệm (ừ, ờ, ừm…), lặp từ, khoảng lặng…");
  }

  function findSpeakers() {
    return guard(function () {
      return aiedit("speakers").then(function (r) {
        var sp = r.speakers || {}, ks = Object.keys(sp);
        renderTranscript(r);
        status(ks.length ? "✔ " + ks.length + " người nói: " + ks.map(function (k) { return k + " " + Math.round(sp[k].secs) + "s"; }).join(" · ") +
               " — AI ra lệnh / chia chương cũng thấy ai đang nói" : "Không thấy đoạn thoại nào để nhận diện", ks.length ? "ok" : "");
      });
    }, "Đang nghe giọng từng người (cả thư mục source)…");
  }

  // ===================================================================== mode: post-production (on the open sequence)
  function analysePost() {
    var want = ["reframe", "color", "stabilize"].filter(function (k) { return $("aeP_" + k).checked; });
    if (!want.length) return;
    return guard(function () {
      return aiedit("post", { want: want.join(",") }).then(function (r) {
        ST.post = r.items || [];
        $("aePost").innerHTML = ST.post.length ? ST.post.map(function (it, i) {
          var d = [];
          if (it.reframe) d.push(it.reframe.length > 1 ? "reframe bám chủ thể (" + it.reframe.length + " keyframe)" : "reframe tĩnh");
          if (it.color) d.push("màu: " + Object.keys(it.color).map(function (k) { return k + " " + it.color[k]; }).join(", "));
          if (it.stabilize) d.push("chống rung (Warp Stabilizer)");
          return '<label class="ae-ch"><input type="checkbox" data-p="' + i + '" checked><div class="ae-body"><div class="ae-head"><b>' + mmss(it.tl) +
            "</b> " + esc(base(it.file)) + '</div><div class="ae-why">' + esc(d.join(" · ")) + "</div></div></label>";
        }).join("") : '<div class="muted">Không cần hậu kỳ gì thêm.</div>';
        $("aePostApply").style.display = ST.post.length ? "" : "none";
        status(ST.post.length ? "Mở sequence của bản dựng này trong Premiere rồi bấm Áp dụng lên sequence" : "");
      });
    }, "Đang phân tích hình (chủ thể, màu, rung)…");
  }
  function applyPost() {
    var pick = [];
    Array.prototype.forEach.call(document.querySelectorAll("#aePost input[data-p]"), function (x) { if (x.checked) pick.push(ST.post[+x.dataset.p]); });
    if (!pick.length) return;
    /* Batches of BATCH clips per ExtendScript call (one round trip + one effect lookup per batch instead of per clip).
       Small enough that Premiere's UI — blocked while ExtendScript runs — gets control back between batches. */
    var BATCH = 8;
    return guard(function () {
      var errs = [], okN = 0, chain = Promise.resolve(), legacy = false;
      if ($("aeP_backup").checked) chain = chain.then(function () { return NX.evalHost('jesCloneSequence("_truocAI")'); });
      function one(it) {                                   // host.jsx without the batch function (older panel build)
        return NX.evalHost("jesApplyPost(" + JSON.stringify(JSON.stringify(it)) + ")").then(function (r) {
          var j = null; try { j = JSON.parse(r); } catch (e) {}
          if (j && j.ok) okN++; else errs.push(mmss(it.tl) + " " + ((j && j.error) || r));
        });
      }
      for (var b0 = 0; b0 < pick.length; b0 += BATCH) (function (part, from) {
        chain = chain.then(function () {
          status("Áp dụng " + Math.min(from + part.length, pick.length) + "/" + pick.length + " clip…", "run");
          if (legacy) return part.reduce(function (p, it) { return p.then(function () { return one(it); }); }, Promise.resolve());
          return NX.evalHost("jesApplyPostBatch(" + JSON.stringify(JSON.stringify(part)) + ")").then(function (r) {
            var j = null; try { j = JSON.parse(r); } catch (e) {}
            if (!j || (!j.ok && /jesApplyPostBatch/.test(String(j.error || r)))) {   // function missing → per clip
              legacy = true;
              return part.reduce(function (p, it) { return p.then(function () { return one(it); }); }, Promise.resolve());
            }
            if (!j.ok) { part.forEach(function (it) { errs.push(mmss(it.tl) + " " + j.error); }); return; }
            (j.results || []).forEach(function (x, i) {
              if (x.ok) okN++; else errs.push(mmss(part[i].tl) + " " + x.error);
            });
          });
        });
      })(pick.slice(b0, b0 + BATCH), b0);
      return chain.then(function () {
        status("✔ Hậu kỳ " + okN + "/" + pick.length + " clip" + (errs.length ? " · lỗi: " + errs.slice(0, 3).join(" | ") : "") +
          ($("aeP_backup").checked ? " · bản sao lưu: <tên sequence>_truocAI" : ""), errs.length ? "bad" : "ok");
        if (errs.length) NX.log("✖ Hậu kỳ: " + errs.join(" | "), "#e5534b");
      });
    }, "Đang áp dụng hậu kỳ lên sequence…");
  }

  // ===================================================================== mode: YouTube chapters (B4)
  /* Chapter-Llama style: the LLM reads the dialogue of the EDITED timeline as "[m:ss] text" blocks and answers with
     chapter starts + titles; the engine snaps them to sentence starts and enforces YouTube's rules (first at 0:00,
     ≥ 3 chapters, each ≥ 10 s). Output: text for the video description + chapter markers on the open sequence. */
  var CH_SYS = [
    "Bạn là biên tập viên YouTube. Từ lời thoại có mốc thời gian của một video ĐÃ DỰNG, hãy chia chương cho người xem.",
    "Quy tắc:",
    "- Chỉ dùng mốc có sẵn trong ngoặc vuông; chương đầu luôn 0:00.",
    "- Mỗi chương là MỘT chủ đề / một phần nội dung rõ ràng; đặt mốc ở câu bắt đầu ý mới (chuyển chủ đề, sang thiết bị / địa điểm / câu hỏi mới).",
    "- Chương thường dài 30 giây đến vài phút; video 5 phút ~4–6 chương, 20 phút ~8–12; tối thiểu 3, tối đa 15.",
    "- Tên chương tiếng Việt, 2–7 từ, nói đúng nội dung phần đó (không đặt “Phần 1”, không emoji, không dấu ngoặc kép).",
    "- Lời thoại là nhận dạng giọng nói tự động, có thể sai chính tả — hiểu theo ngữ cảnh, đừng chép lỗi vào tên chương.",
    'Chỉ trả JSON: {"chapters":[{"t":"m:ss","title":"..."}]}'
  ].join("\n");

  function runChapters() {
    var R = window.NXAI && window.NXAI.routeOf ? window.NXAI.routeOf("chat") : { provider: NX.S.provider, model: NX.S.model, key: NX.S.apiKey };
    return guard(function () {
      return aiedit("chapters_ctx").then(function (c) {
        if (!c.n) throw new Error("bản dựng chưa có lời thoại (transcript) — chương YouTube dựa trên lời nói");
        status("AI (" + R.provider + ") đang chia chương " + c.dur_tc + "…", "run");
        var msg = "THỜI LƯỢNG VIDEO: " + c.dur_tc + "\nLỜI THOẠI:\n" + c.text;
        var need = Math.ceil((msg.length + CH_SYS.length) / 2) + 1200, ctxN = 4096;
        while (ctxN < need && ctxN < 32768) ctxN *= 2;
        var llm = window.NXLLM.create(R.provider, R.key, R.model || undefined, null,
                                      { numCtx: R.provider === "ollama" ? Math.min(ctxN, Math.max(4096, +NX.S.numCtx || 16384)) : NX.S.numCtx });
        llm.userTurn(msg);
        return llm.send(CH_SYS, null, { json: true, temperature: 0.2, maxTokens: 1500 });
      }).then(function (r) {
        var j = window.NXLLM.parseJSON(r.text), items = (j && j.chapters) || [];
        if (!items.length) throw new Error("AI không trả về chương nào");
        return aiedit("chapters", { ops: items });
      }).then(function (x) {
        ST.chapters = x.chapters || [];
        renderChapters();
        status(x.ok ? "✔ " + ST.chapters.length + " chương — sửa tên nếu cần rồi chép / gắn marker" : "⚠ " + x.warn, x.ok ? "ok" : "bad");
      });
    }, "Đang đọc lời thoại…");
  }

  function chText() {
    return (ST.chapters || []).map(function (c) { return c.tc + " " + c.title; }).join("\n");
  }
  function renderChapters() {
    var box = $("aeChList");
    if (!box) return;
    box.innerHTML = (ST.chapters || []).map(function (c, i) {
      return '<div class="row ae-chap" style="gap:6px;margin-top:4px"><b style="font-family:var(--mono);min-width:44px">' + esc(c.tc) + "</b>" +
        '<input data-c="' + i + '" value="' + esc(c.title) + '" style="flex:1">' +
        (i ? '<button class="btn chip" data-x="' + i + '" title="Bỏ chương này (gộp vào chương trước)">✕</button>' : "") + "</div>";
    }).join("");
    Array.prototype.forEach.call(box.querySelectorAll("input[data-c]"), function (x) {
      x.oninput = function () { ST.chapters[+x.dataset.c].title = x.value.trim(); $("aeChText").value = chText(); };
    });
    Array.prototype.forEach.call(box.querySelectorAll("button[data-x]"), function (b) {
      b.onclick = function () { ST.chapters.splice(+b.dataset.x, 1); renderChapters(); };
    });
    $("aeChText").value = chText();
    $("aeChOut").style.display = (ST.chapters || []).length ? "" : "none";
  }
  function copyChapters() {
    var t = $("aeChText");
    t.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) {}
    status(ok ? "✔ Đã chép — dán vào mô tả video YouTube" : "Chọn khung chữ rồi Ctrl+C", ok ? "ok" : "");
  }
  function markChapters() {
    var items = (ST.chapters || []).map(function (c) { return { t: c.t, name: c.title, chapter: true }; });
    if (!items.length) return;
    return guard(function () {
      return NX.evalHost("jesSetChapters(" + JSON.stringify(JSON.stringify(items)) + ")").then(function (r) {
        var j = null; try { j = JSON.parse(r); } catch (e) {}
        if (!j || !j.ok) throw new Error((j && j.error) || r);
        status("✔ Gắn " + j.added + " chapter marker" + (j.removed ? " (thay " + j.removed + " cái cũ)" : "") +
               " — xuất H.264 / MP4 là chương đi kèm file", "ok");
      });
    }, "Đang gắn marker chương…");
  }

  // B5: thumbnail candidates — 1 frame / s of the edit scored on sharpness, face, beauty, colour; 8 kept apart
  function runThumbs() {
    return guard(function () {
      return aiedit("thumbs").then(function (r) {
        ST.thumbs = r;
        var box = $("aeThumbs");
        box.innerHTML = (r.thumbs || []).map(function (t) {
          return '<div class="find-tile" title="' + esc(base(t.file) + " · " + mmss(t.src_t) + (t.why ? "\n" + t.why : "")) + '">' +
            '<img src="' + fileUrl(t.path) + "?" + Date.now() + '"><div class="cap"><b>' + mmss(t.t) + " · " + Math.round(t.score * 100) +
            "</b><span>" + esc(t.why || base(t.file)) + "</span></div></div>";
        }).join("") || '<div class="muted">Không có khung phù hợp.</div>';
        $("aeThDir").style.display = r.dir ? "" : "none";
        status("✔ " + (r.thumbs || []).length + " khung 1280×720 — chọn 1 làm nền thumbnail (thêm chữ / mặt cười ở Photoshop / Canva)", "ok");
      });
    }, "Đang chấm khung hình (1 khung / giây)…");
  }
  function openThumbDir() {
    var d = ST.thumbs && ST.thumbs.dir;
    if (!d) return;
    try {
      var cp = require("child_process");
      cp.spawn(process.platform === "win32" ? "explorer" : "open", [process.platform === "win32" ? d.replace(/\//g, "\\") : d],
               { detached: true, stdio: "ignore" }).unref();
    } catch (e) { status("Thư mục: " + d); }
  }

  // C1: real audience retention from the editor's channel (YouTube Analytics, read-only OAuth in their own browser)
  function yt(args) {
    return NX.pyJSON(["-m", "nxcore.ytanalytics"].concat(args), "YT", function (l) {
      if (/^youtube:/.test(l)) status(l.replace(/^youtube:\s*/, ""), "run");
    });
  }
  function ytStatus() {
    return yt(["status"]).then(function (s) {
      $("aeYtState").textContent = !s.client ? "chưa có OAuth client" : !s.token ? "chưa kết nối kênh" :
        "kênh: " + ((s.channel && s.channel.title) || "?");
      $("aeYtLogin").style.display = s.client ? "" : "none";
      $("aeYtRun").style.display = s.token ? "" : "none";
      $("aeYtLearn").style.display = s.token ? "" : "none";
    }, function (e) { $("aeYtState").textContent = e.message; });
  }
  function ytPickClient(ev) {
    var f = ev.target.files && ev.target.files[0];
    if (!f) return;
    try {
      var j = JSON.parse(fs.readFileSync(f.path, "utf8"));
      if (!(j.installed || j.web || j).client_id) throw new Error("không phải file OAuth client");
      fs.writeFileSync(path.join(NX.cfgDir, "google_client.json"), JSON.stringify(j), "utf8");
      status("✔ Đã lưu OAuth client — bấm Kết nối kênh", "ok");
      ytStatus();
    } catch (e) { status("✖ " + e.message, "bad"); }
  }
  function ytLogin() {
    return guard(function () {
      return yt(["login"]).then(function (ch) { status("✔ Đã kết nối kênh " + (ch.title || ""), "ok"); return ytStatus(); });
    }, "Đăng nhập Google trong trình duyệt vừa mở (chỉ quyền ĐỌC thống kê)…");
  }
  function sparkline(curve, events, dur) {
    var W = 560, H = 120, x = function (t) { return 4 + (W - 8) * t / Math.max(dur, 1); };
    var ymax = Math.max(1.05, Math.max.apply(null, curve.map(function (c) { return c[1]; })));
    var y = function (v) { return H - 6 - (H - 14) * v / ymax; };
    var pts = curve.map(function (c) { return x(c[0]).toFixed(1) + "," + y(c[1]).toFixed(1); }).join(" ");
    var marks = (events || []).map(function (e) {
      var col = e.type === "leave" ? "#e5534b" : "#4cc38a";
      return '<line x1="' + x(e.t) + '" x2="' + x(e.t) + '" y1="4" y2="' + (H - 4) + '" stroke="' + col + '" stroke-dasharray="3 3"/>';
    }).join("");
    return '<svg viewBox="0 0 ' + W + " " + H + '" style="width:100%;height:auto;background:var(--sunk);border-radius:8px">' + marks +
      '<polyline fill="none" stroke="var(--acc)" stroke-width="2" points="' + pts + '"/></svg>';
  }
  function ytShow(r) {
    var s = r.summary || {}, pct = function (v) { return v == null ? "–" : Math.round(v * 100) + "%"; };
    $("aeYtOut").innerHTML = sparkline(r.curve || [], r.events, s.dur) +
      '<div class="hint">Còn xem: giây 30 ' + pct(s.at30s) + " · giữa video " + pct(s.mid) + " · cuối " + pct(s.end) +
      " · so với video cùng độ dài trên YouTube: " + (s.rel_mean == null ? "–" : (s.rel_mean >= 0.55 ? "tốt hơn" : s.rel_mean <= 0.45 ? "kém hơn" : "ngang") +
      " (" + s.rel_mean + ")") + "</div>" +
      ((r.events || []).map(function (e) {
        var lab = e.type === "leave" ? "▼ rời đi " + (e.lost * 100).toFixed(1) + "%" : "▲ xem lại +" + (e.gain * 100).toFixed(1) + "%";
        return '<div class="ae-ch"><div class="ae-body"><div class="ae-head"><b>' + mmss(e.t) + "</b> <span class=\"ae-kind\">" + lab +
          '</span> <span class="muted">#' + e.seg + " " + esc(e.kind || "") + " · shot " + e.shot_len + "s</span></div>" +
          (e.text ? '<div class="ae-why">“' + esc(e.text) + "”</div>" : '<div class="ae-why muted">' + esc(e.label || "") + "</div>") + "</div></div>";
      }).join("") || '<div class="muted">Không có chỗ nào người xem bỏ đi bất thường (≥ 1,5%).</div>');
  }
  function ytAnalyze() {
    var pp = planPath();
    return guard(function () {
      var manual = ($("aeYtVid").value || "").trim(), id = (manual.match(/(?:v=|youtu\.be\/|shorts\/)([\w-]{11})/) || [])[1] || (/^[\w-]{11}$/.test(manual) ? manual : "");
      var pick = id ? Promise.resolve(id) : yt(["suggest", pp]).then(function (c) {
        if (!c.length) throw new Error("không có video nào trên kênh dài bằng bản dựng này — dán link video vào ô bên cạnh");
        if (c.length > 1) status("Có " + c.length + " video cùng độ dài — chọn: " + c.slice(0, 3).map(function (v) { return v.title; }).join(" | ") + " (dán link để chọn đúng)");
        return c[0].id;
      });
      return pick.then(function (vid) {
        status("Đang lấy retention của video " + vid + "…", "run");
        return yt(["analyze", pp, vid]);
      }).then(function (r) {
        ytShow(r);
        status("✔ " + (r.events || []).filter(function (e) { return e.type === "leave"; }).length + " chỗ người xem rời đi · " +
               (r.events || []).filter(function (e) { return e.type === "rewatch"; }).length + " chỗ được xem lại", "ok");
      });
    }, "Đang tìm video tương ứng trên kênh…");
  }
  function ytLessons() {
    return guard(function () {
      return yt(["lessons"]).then(function (r) {
        var L = (r.lessons || []).filter(function (x) { return x.leaves >= 3 && x.videos >= 2; });
        $("aeYtOut").innerHTML = "<div class=\"hint\">" + r.videos + " video đã phân tích.</div>" + (L.length ? L.map(function (x) {
          return '<div class="ae-ch"><div class="ae-body"><b>' + esc(x.text) + "</b>: " + (x.lift >= 1.2 ? "người xem rời đi nhiều gấp " + x.lift + "×" :
            x.lift <= 0.8 ? "ít bị bỏ hơn (×" + x.lift + ")" : "như mức chung (×" + x.lift + ")") +
            ' <span class="muted">— ' + x.leaves + " lần ở " + x.videos + " video, chiếm " + Math.round(x.share_time * 100) + "% thời lượng</span></div></div>";
        }).join("") : '<div class="muted">Chưa đủ dữ liệu: cần ≥ 2 video đã phân tích và ≥ 3 chỗ rời đi cho mỗi loại đoạn.</div>');
      });
    }, "Đang tổng hợp các video đã phân tích…");
  }

  // ===================================================================== layout
  var PANES = {
    notes: '<div class="hint" style="margin-top:0">Xem sequence trong Premiere, dừng ở chỗ chưa ưng → gõ ghi chú → Enter (gắn đúng giây của playhead). ' +
      'Hoặc đặt marker có chữ (phím M) rồi bấm <b>Lấy từ marker</b>. Xong bấm <b>AI sửa</b>: mọi ghi chú thành một bộ thay đổi để duyệt, ' +
      'kèm luật lâu dài đề xuất cho <b>Sổ luật</b> — luật đã lưu được áp cho mọi lần dựng sau.</div>' +
      '<div class="row" style="gap:6px"><input id="aeNoteText" placeholder="vd: zoom to quá · shot này rung · chỗ này nên là cảnh biển · nhạc lấn tiếng"><button class="btn main" id="aeNoteAdd" title="Ghi chú tại vị trí playhead hiện tại" style="white-space:nowrap;flex:none">📍 Ghi</button></div>' +
      '<div class="row" style="gap:6px;margin-top:4px"><button class="btn chip" id="aeNoteMk" title="Marker trên sequence đang mở có Name / Comments → ghi chú">Lấy từ marker</button>' +
      '<button class="btn chip" id="aeNoteClear">Xóa hết</button><span class="muted" id="aeNoteMeta" style="font-size:var(--fs-xs)"></span></div>' +
      '<div id="aeNotes" class="ae-notes"></div>' +
      '<button class="btn main cta" id="aeNoteRun">✨ AI sửa theo ghi chú</button><div id="aeReply"></div><div id="aeRuleProp"></div>' +
      '<details id="aeBook" class="sb-book"><summary>📒 Sổ luật phong cách <span class="muted" id="aeBookMeta"></span></summary>' +
      '<div class="hint">Luật có ⚙ là tham số tool tự áp mỗi lần dựng (mạnh hơn preset, thua chỉnh tay của lần chạy đó). Luật chữ được đưa vào mọi bước AI: ' +
      'sửa bằng lời, AI Assist, viết kịch bản, AI Style chữ. Bấm vào chữ để sửa, bỏ tick để tạm tắt.</div><div id="aeBookList"></div></details>',
    review: '<button class="btn main cta" id="aeRun">Rà bản dựng</button>' +
      '<div class="hint">AI xem toàn bộ bản dựng: cảnh lặp / take giống nhau đứng gần nhau, cắt khi hành động chưa xong, đoạn rung / mờ / đen, ' +
      'có người nói trong montage nhạc, jump cut, ô thiếu hình — mỗi lỗi kèm cách sửa (cảnh thay chọn theo đúng luật dựng).</div><div id="aeReviewNote" class="hint"></div>',
    command: '<textarea id="aeCmd" rows="3" placeholder="vd: thay shot 5 bằng cảnh hoàng hôn trên biển · drop nhanh hơn · bỏ hết cảnh có người nói · đổi chỗ shot 3 và 8 · làm slow-mo các cảnh flycam"></textarea>' +
      '<div class="row" style="margin-top:6px;gap:6px;flex-wrap:wrap">' +
      '<button class="btn chip ae-q">Thay các cảnh lặp bằng cảnh khác</button><button class="btn chip ae-q">Cắt nhanh hơn</button>' +
      '<button class="btn chip ae-q">Mở đầu bằng cảnh đẹp nhất</button><button class="btn chip ae-q">Slow-mo các cảnh flycam</button>' +
      '<button class="btn main" id="aeSend" style="margin-left:auto">Gửi</button></div><div id="aeReply"></div>' +
      '<div class="hint">Dùng model "AI Assist (agent)" trong Cài đặt. AI chỉ đề xuất — tìm cảnh thay bằng SigLIP2 trên máy bạn.</div>',
    transcript: '<div class="row" style="gap:6px;flex-wrap:wrap"><button class="btn" id="aeTxLoad">Tải transcript</button>' +
      '<button class="btn" id="aeFill">Tìm từ đệm &amp; khoảng lặng</button>' +
      '<button class="btn" id="aeSpk" title="Nghe giọng trong mọi clip có lời của thư mục source → gắn S1, S2… cho từng câu (cùng người = cùng nhãn ở mọi clip)">Nhận diện người nói</button>' +
      '<button class="btn main" id="aeCutSel">Cắt chữ đã đánh dấu</button></div>' +
      '<div id="aeTx" class="ae-tx"><div class="muted">Bấm “Tải transcript” — sửa video như sửa văn bản: bấm chữ để đánh dấu cắt.</div></div>',
    post: '<div class="row" style="gap:12px;flex-wrap:wrap">' +
      '<label class="chk"><input type="checkbox" id="aeP_reframe" checked> Reframe bám chủ thể</label>' +
      '<label class="chk"><input type="checkbox" id="aeP_color" checked> Cân màu đồng bộ</label>' +
      '<label class="chk"><input type="checkbox" id="aeP_stabilize" checked> Chống rung</label></div>' +
      '<div class="row" style="margin-top:6px;gap:6px"><button class="btn main" id="aePostRun">Phân tích</button></div>' +
      '<div id="aePost" style="margin-top:8px"></div>' +
      '<div id="aePostApply" style="display:none;margin-top:8px"><label class="chk"><input type="checkbox" id="aeP_backup" checked> Sao lưu sequence trước khi sửa</label>' +
      '<button class="btn main cta" id="aePostGo">Áp dụng lên sequence đang mở</button>' +
      '<div class="hint">Reframe: đặt keyframe Position (khung dọc 9:16 từ footage ngang). Màu: thêm Lumetri Color, chỉ sửa lệch vừa phải — cảnh hoàng hôn / đêm giữ nguyên chủ ý. ' +
      'Chống rung: thêm Warp Stabilizer (Premiere tự phân tích, mất vài giây mỗi clip).</div></div>',
    chapters: '<button class="btn main cta" id="aeChRun">Chia chương từ lời thoại</button>' +
      '<div class="hint">AI đọc lời thoại của bản dựng, đặt mốc ở chỗ chuyển chủ đề và đặt tên chương. Đúng luật YouTube: chương đầu 0:00, ≥ 3 chương, mỗi chương ≥ 10 giây.</div>' +
      '<div id="aeChList"></div>' +
      '<div id="aeChOut" style="display:none;margin-top:8px"><textarea id="aeChText" rows="6" style="width:100%;font-family:var(--mono)" readonly></textarea>' +
      '<div class="row" style="margin-top:6px;gap:6px;flex-wrap:wrap"><button class="btn" id="aeChCopy">Chép cho mô tả YouTube</button>' +
      '<button class="btn main" id="aeChMark" title="Mở sequence của bản dựng này trước — marker cũ loại Chapter được thay">Gắn chapter marker vào sequence</button></div></div>' +
      '<div style="margin-top:14px;border-top:1px solid var(--line2);padding-top:10px"><button class="btn main" id="aeThRun">Gợi ý khung thumbnail</button>' +
      '<button class="btn chip" id="aeThDir" style="display:none;margin-left:6px">Mở thư mục</button>' +
      '<div class="hint">Chấm 1 khung mỗi giây của bản dựng: nét (không nhòe), có mặt người rõ, đẹp (SigLIP2), màu rực — giữ 8 khung khác nhau, lưu 1280×720 cạnh file plan.</div>' +
      '<div id="aeThumbs" class="find-grid"></div></div>' +
      '<div style="margin-top:14px;border-top:1px solid var(--line2);padding-top:10px"><b>Retention thật từ kênh</b> <span class="muted" id="aeYtState">…</span>' +
      '<div class="row" style="margin-top:6px;gap:6px;flex-wrap:wrap">' +
      '<label class="btn chip" title="File OAuth client loại Desktop app tải từ Google Cloud Console">Chọn file OAuth client (.json)<input type="file" id="aeYtClient" accept=".json" style="display:none"></label>' +
      '<button class="btn" id="aeYtLogin" style="display:none">Kết nối kênh</button>' +
      '<button class="btn main" id="aeYtRun" style="display:none">Phân tích bản dựng này</button>' +
      '<input id="aeYtVid" placeholder="link video (để trống = tự tìm theo độ dài)" style="flex:1;min-width:160px">' +
      '<button class="btn chip" id="aeYtLearn" style="display:none">Bài học từ kênh</button></div>' +
      '<div id="aeYtOut" style="margin-top:8px"></div>' +
      '<div class="hint">Cài một lần: Google Cloud Console → tạo project → bật <b>YouTube Data API v3</b> và <b>YouTube Analytics API</b> → ' +
      'OAuth consent screen (External, thêm email của anh vào Test users) → Credentials → OAuth client ID loại <b>Desktop app</b> → tải JSON → chọn ở nút trên. ' +
      'Đăng nhập diễn ra trong trình duyệt của anh, tool chỉ xin quyền ĐỌC thống kê; số liệu có sau khi đăng ~2–3 ngày.</div></div>'
  };

  function setMode(m) {
    ST.mode = m;
    $("aePane").innerHTML = PANES[m];
    $("aeTitle").textContent = { notes: "Ghi chú theo thời điểm", review: "Rà lỗi bản dựng", command: "Ra lệnh sửa bằng lời", transcript: "Sửa bằng transcript",
                                 post: "Hậu kỳ tự động", chapters: "Chuẩn bị đăng YouTube" }[m];
    if (m === "notes") {
      $("aeNoteAdd").onclick = addNote; $("aeNoteMk").onclick = fromMarkers; $("aeNoteClear").onclick = clearNotes; $("aeNoteRun").onclick = runNotes;
      $("aeNoteText").onkeydown = function (e) { if (e.key === "Enter") { e.preventDefault(); addNote(); } };
      $("aeBook").ontoggle = function () { if ($("aeBook").open && window.JESStyle) window.JESStyle.render($("aeBookList")); };
      renderNotes(); bookMeta();
    }
    if (m === "chapters") {
      $("aeChRun").onclick = runChapters; $("aeChCopy").onclick = copyChapters; $("aeChMark").onclick = markChapters;
      $("aeThRun").onclick = runThumbs; $("aeThDir").onclick = openThumbDir;
      $("aeYtClient").onchange = ytPickClient; $("aeYtLogin").onclick = ytLogin; $("aeYtRun").onclick = ytAnalyze; $("aeYtLearn").onclick = ytLessons;
      ytStatus();
      if ((ST.chapters || []).length) renderChapters();
    }
    if (m === "review") $("aeRun").onclick = runReview;
    if (m === "command") {
      $("aeSend").onclick = runCommand;
      $("aeCmd").onkeydown = function (e) { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) runCommand(); };
      Array.prototype.forEach.call(document.querySelectorAll(".ae-q"), function (b) { b.onclick = function () { $("aeCmd").value = b.textContent; runCommand(); }; });
    }
    if (m === "transcript") {
      $("aeTxLoad").onclick = function () { loadTranscript(); }; $("aeFill").onclick = findFillers; $("aeCutSel").onclick = cutMarked;
      $("aeSpk").onclick = findSpeakers;
    }
    if (m === "post") { $("aePostRun").onclick = analysePost; $("aePostGo").onclick = applyPost; }
  }

  function refreshMeta() {
    var pp = planPath(), el = $("aeMeta");
    if (!el) return;
    if (!pp || !fs.existsSync(pp)) { el.textContent = "chưa có plan — chạy Align / dựng trước"; return; }
    try {
      var P = JSON.parse(fs.readFileSync(pp, "utf8")), grid = P.segments.some(function (s) { return s.tl; });
      el.textContent = P.name + " · " + P.segments.length + " shot · " + (grid ? "montage theo nhạc" : "dựng tự do") + ((P.ai_edits || []).length ? " · đã sửa AI " + P.ai_edits.length + " lần" : "");
    } catch (e) { el.textContent = path.basename(pp); }
  }

  /* One "AI" tab: the agent chat (ai.js, untouched) + the four editing views share a switch bar at the top. */
  var VIEWS = [["chat", "Trò chuyện"], ["notes", "📍 Ghi chú"], ["review", "Rà lỗi"], ["command", "Ra lệnh sửa"], ["transcript", "Transcript"],
               ["post", "Hậu kỳ"], ["chapters", "YouTube"]];
  function setView(v) {
    Array.prototype.forEach.call(document.querySelectorAll("#aiTop button"), function (b) { b.classList.toggle("on", b.dataset.v === v); });
    $("aiChatPane").style.display = v === "chat" ? "" : "none";
    $("aiEdit").style.display = v === "chat" ? "none" : "";
    if (v !== "chat" && ST.mode !== v) setMode(v);
    if (v !== "chat") refreshMeta();
    try { localStorage.setItem("jes_ai_view", v); } catch (e) {}
  }

  function mount() {
    var sec = $("ai");
    if (!sec || $("aiTop")) return;
    var chat = document.createElement("div");                 // the existing agent chat moves into its own pane
    chat.id = "aiChatPane";
    while (sec.firstChild) chat.appendChild(sec.firstChild);
    var top = document.createElement("div");
    top.id = "aiTop"; top.className = "sc-seg ai-top";
    top.innerHTML = VIEWS.map(function (v) { return '<button data-v="' + v[0] + '">' + v[1] + "</button>"; }).join("");
    var edit = document.createElement("div");
    edit.id = "aiEdit"; edit.style.display = "none";
    sec.appendChild(top); sec.appendChild(chat); sec.appendChild(edit);
    edit.innerHTML =
      '<div class="card ai-card"><h3><span class="ai-spark"></span><span id="aeTitle">Chỉnh sửa bằng AI</span> <span class="sp muted" id="aeMeta"></span></h3>' +
      '<div id="aePane"></div><div id="aeStatus" class="ae-status"></div></div>' +
      '<div class="card" id="aeChCard" style="display:none"><h3>Thay đổi đề xuất <span class="sp muted" id="aeChMeta"></span></h3>' +
      '<div id="aeList" class="ae-list"></div>' +
      '<div class="row" style="margin-top:8px;gap:6px"><button class="btn" id="aeAll">Chọn hết</button><button class="btn" id="aeNone">Bỏ hết</button>' +
      '<button class="btn main" id="aeApply" style="margin-left:auto">Áp dụng</button></div>' +
      '<div class="hint">Áp dụng = lưu plan bản mới (_vN+1) rồi build sequence mới. Bản cũ giữ nguyên — bấm ảnh để xem trong Source Monitor.</div></div>';
    Array.prototype.forEach.call(top.querySelectorAll("button"), function (b) {
      b.onclick = function () { if (!ST.busy) setView(b.dataset.v); };
    });
    $("aeAll").onclick = function () { Array.prototype.forEach.call(document.querySelectorAll("#aeList input:not(:disabled)"), function (x) { x.checked = true; }); countSel(); };
    $("aeNone").onclick = function () { Array.prototype.forEach.call(document.querySelectorAll("#aeList input"), function (x) { x.checked = false; }); countSel(); };
    $("aeApply").onclick = applyChanges;
    setMode("review");
    var last = "chat";
    try { last = localStorage.getItem("jes_ai_view") || "chat"; } catch (e) {}
    setView(VIEWS.some(function (v) { return v[0] === last; }) ? last : "chat");
    document.addEventListener("nxtab", function (e) { if (e.detail === "ai" && ST.mode && $("aiEdit").style.display !== "none") refreshMeta(); });
    document.addEventListener("jes:stylebook", bookMeta);
  }

  window.JESAIEdit = { review: runReview, command: runCommand, setMode: setMode, setView: setView, refresh: refreshMeta,
                      show: renderChanges };      // other modules (agent chat) can push a change set into the preview
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount); else mount();
})();
