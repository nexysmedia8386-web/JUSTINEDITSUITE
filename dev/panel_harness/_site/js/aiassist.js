/* Justin EDIT Suite v2.1 — AI Assist: footage search by meaning (SigLIP2), look-alike takes, AI pack status and the
   self-healing doctor (nxcore.doctor): on panel open the engine is checked and whatever REQUIRED piece is missing is
   installed automatically; the heavy AI pack stays one click away (Cài đặt → AI tăng tốc & tự sửa). */
(function () {
  var NX = window.NX;
  if (!NX) return;
  function $(id) { return document.getElementById(id); }
  function S() { return NX.S; }
  function esc(s) { return NX.esc ? NX.esc(s) : String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function fileUrl(p) { return "file:///" + encodeURI(String(p).replace(/\\/g, "/").replace(/^\/+/, "")).replace(/#/g, "%23"); }
  function mmss(t) { t = Math.max(0, +t || 0); var m = Math.floor(t / 60), s = (t - m * 60).toFixed(1); return m + ":" + (s < 10 ? "0" : "") + s; }

  /* one-shot python child: resolves {code, out}; onLine gets every output line (shared helper in main.js) */
  function py(args, onLine) { return NX.py(args, onLine); }
  function jsonAfter(out, tag) {
    var l = String(out).split(/\r?\n/).filter(function (x) { return x.indexOf(tag + " ") === 0; }).pop();
    try { return l ? JSON.parse(l.slice(tag.length + 1)) : null; } catch (e) { return null; }
  }

  // ===================================================================== doctor (self-healing)
  var DOC = { rep: null, running: false };
  var LABEL = { python: "Python", numpy: "numpy", cv2: "OpenCV", PIL: "Pillow", faster_whisper: "Whisper",
    docx: "Đọc .docx", mlx_whisper: "Whisper (Apple GPU)", ffmpeg: "FFmpeg", cuda_libs: "CUDA cho Whisper",
    torch: "PyTorch", transformers: "SigLIP2 / căn chỉnh từ", sentencepiece: "Tokenizer", protobuf: "Protobuf",
    beat_this: "Bắt beat AI", transnetv2_pytorch: "Tách cảnh AI", ollama: "Ollama (mô tả clip)",
    torchlibrosa: "Tìm theo âm thanh", scipy: "SciPy", sklearn: "Nhận diện người nói",
    face: "Nhận diện khuôn mặt", vram: "VRAM cho AI", ollama_gpu: "Ollama trên GPU" };

  function renderDoctor() {
    var box = $("aiList"), rep = DOC.rep;
    if (!box) return;
    if (!rep) { box.innerHTML = '<div class="muted">Chưa kiểm tra.</div>'; return; }
    var gpu = rep.gpu || {};
    $("aiMeta").textContent = (gpu.nvidia ? gpu.nvidia + " · " + gpu.vram_gb + " GB" : gpu.apple_silicon ? "Apple Silicon" : "không GPU") +
      " · Python " + rep.python;
    box.innerHTML = Object.keys(rep.items || {}).map(function (k) {
      var it = rep.items[k], cls = it.ok ? "ok" : (it.required ? "bad" : "off");
      return '<div class="ai-item ' + cls + '" title="' + esc(it.detail) + '"><i></i><b>' + esc(LABEL[k] || k) + '</b><span>' + esc(it.detail) + "</span></div>";
    }).join("");
  }

  function doctor(fix, extra, label) {
    if (DOC.running) return Promise.resolve(DOC.rep);
    DOC.running = true;
    var args = ["-m", "nxcore.doctor"].concat(fix ? ["--fix"] : []).concat(extra || []);
    var job = fix ? NX.run(args, label || "Tự cài thành phần còn thiếu").then(function () { return null; }, function () { return null; })
                  : Promise.resolve(null);
    return job.then(function () { return py(["-m", "nxcore.doctor", "--quiet"]); }).then(function (r) {
      DOC.running = false;
      if (r.code === -1) {
        NX.log("✖ Không chạy được Python (" + (S().python || "python") + "): " + r.out.trim() + " — chạy CAI_DAT.bat của bộ cài để tự cài Python.", "#e5534b");
        return null;
      }
      DOC.rep = jsonAfter(r.out, "DOCTOR");
      renderDoctor();
      return DOC.rep;
    });
  }

  /* panel open: check silently; a missing REQUIRED piece is installed right away (never the heavy AI pack) */
  function autoHeal() {
    doctor(false).then(function (rep) {
      if (!rep || rep.ok) return;
      NX.log("⚙ Thiếu " + (rep.failed || []).join(", ") + " → đang tự cài (không cần làm gì)…", "#e0b341");
      return doctor(true, ["--ai", "no"], "Tự cài: " + (rep.failed || []).join(", ")).then(function (r2) {
        if (r2 && r2.ok) NX.log("✔ Đã tự cài đủ thành phần bắt buộc.", "#4cc38a");
        else if (r2) NX.log("✖ Vẫn thiếu: " + (r2.failed || []).join(", ") + " — kiểm tra mạng rồi bấm Cài đặt → Tự sửa.", "#e5534b");
      });
    });
  }

  // ===================================================================== footage search (shared AI worker, js/aiworker.js)
  /* srvAsk(req, onRow) — kept as the public name (aiedit.js pre-embeds scene descriptions with it).
     req: {src, q, k} = search · {src, dups, thr, k} = look-alike takes · {embed: [texts]} = fill the text cache */
  function srvAsk(req, onRow) {
    if (!window.JESWorker) return Promise.reject(new Error("thiếu js/aiworker.js"));
    var r = req.embed ? { op: "embed", texts: req.embed }
          : req.dups ? { op: "dups", src: req.src, thr: req.thr, k: req.k }
          : { op: req.sound ? "sounds" : "search", src: req.src, q: req.q, k: req.k };
    return window.JESWorker.ask(r, { onRow: onRow, replace: r.op === "search" || r.op === "sounds" ? "search" : "" });
  }

  function tile(h, label) {
    var d = document.createElement("div");
    d.className = "find-tile";
    var pct = Math.max(4, Math.min(100, Math.round((+h.score || 0) * 600)));
    d.innerHTML = (h.thumb ? '<img loading="lazy" src="' + fileUrl(h.thumb) + '">' : '<div class="ph"></div>') +
      '<div class="cap"><b>' + esc(String(h.file).split("/").pop()) + "</b><span>" + mmss(h.a) + "–" + mmss(h.b) + "</span></div>" +
      (label ? '<div class="tag">' + esc(label) + "</div>" : '<div class="meter"><i style="width:' + pct + '%"></i></div>');
    d.title = h.file + "  ·  " + mmss(h.a) + "–" + mmss(h.b) + "\nBấm: mở trong Source Monitor (in/out đặt sẵn)";
    d.onclick = function () {
      var full = (S().src + "/" + h.file).replace(/\\/g, "/");
      NX.evalHost("nxOpenInSource(" + JSON.stringify(full) + "," + (+h.a) + "," + (+h.b) + ")").then(function (r) {
        var j = null; try { j = JSON.parse(r); } catch (e) {}
        if (j && j.error) NX.log("✖ Mở clip: " + j.error, "#e5534b");
      });
    };
    return d;
  }

  function hitKey(h) { return h.file + "@" + (+h.a).toFixed(2) + "-" + (+h.b).toFixed(2); }
  function deepOn() { try { return localStorage.getItem("jesFindDeep") === "1"; } catch (e) { return false; } }
  function setDeep(v) {
    try { localStorage.setItem("jesFindDeep", v ? "1" : "0"); } catch (e) {}
    if ($("bFindDeep")) $("bFindDeep").classList.toggle("on", !!v);
  }
  /* C2: search by SOUND (PANNs AudioSet tags) instead of picture */
  function soundOn() { try { return localStorage.getItem("jesFindSound") === "1"; } catch (e) { return false; } }
  function setSound(v) {
    try { localStorage.setItem("jesFindSound", v ? "1" : "0"); } catch (e) {}
    if ($("bFindSnd")) $("bFindSnd").classList.toggle("on", !!v);
    if ($("findQ")) $("findQ").placeholder = v ? "Tiếng cần tìm: tiếng cười · sóng biển · vỗ tay · xe máy · nhạc · gió"
                                               : "Mô tả cảnh: hoàng hôn trên biển · người cười · xe chạy ban đêm";
  }

  function find() {
    var q = ($("findQ").value || "").trim(), res = $("findRes");
    if (!q) return;
    if (!S().src) { NX.log("Chọn folder source trước.", "#e0b341"); return; }
    if (window.JESWorker && window.JESWorker.cancel) window.JESWorker.cancel("rank");   // the previous search's re-rank stops
    res.innerHTML = '<div class="find-wait">' + (window.JESWorker && window.JESWorker.alive() ? "Đang tìm…" : "Đang khởi động AI (lần đầu ~10 s)…") + "</div>";
    var t0 = Date.now(), first = true, tiles = {}, snd = soundOn();
    srvAsk({ src: S().src, q: q, k: 24, sound: snd }, function (h) {
      if (first) { res.innerHTML = ""; first = false; }
      var el = snd ? tile(h, "🔊 " + ((h.labels || []).slice(0, 2).join(" · ") || "âm thanh")) : tile(h);
      tiles[hitKey(h)] = el;
      res.appendChild(el);
    }).then(function (r) {
      if (r.err) res.innerHTML = '<div class="find-wait bad">' + esc(r.err) + "</div>";
      else if (!r.rows.length) res.innerHTML = '<div class="find-wait">Không có kết quả' + (snd ? " — thử từ khác (tiếng cười, sóng, gió, xe, nhạc, vỗ tay…)" : "") + ".</div>";
      $("findMeta").textContent = r.rows.length ? r.rows.length + " kết quả" + (snd ? " theo âm thanh" : "") + " · " + ((Date.now() - t0) / 1000).toFixed(1) + " s" : "";
      if (!r.err && r.rows.length && deepOn() && !snd) deepLook(q, r.rows, tiles, res);   // the VLM judges pictures, not sound
    }, function (e) {
      res.innerHTML = '<div class="find-wait bad">Chưa dùng được: ' + esc(e.message) + ' — Cài đặt → <b>Cài gói AI</b>.</div>';
    });
  }

  /* B2 — "Xem kỹ": the local vision model (Ollama) looks at the best 12 hits and re-orders them. Scores land on the
     tiles one by one; at the end the grid re-sorts and shots it judged unrelated fade out. A new search cancels it. */
  function deepLook(q, rows, tiles, res) {
    var top = rows.slice(0, 12), n = 0, ok = 0;
    top.forEach(function (h) { var el = tiles[hitKey(h)]; if (el) el.classList.add("vlm-wait"); });
    var meta = $("findMeta"), base = meta.textContent;
    meta.textContent = base + " · AI đang xem kỹ 0/" + top.length;
    function clear() { top.forEach(function (h) { var el = tiles[hitKey(h)]; if (el) el.classList.remove("vlm-wait"); }); }
    window.JESWorker.ask({ op: "rank", src: S().src, q: q, k: top.length,
                           hits: top.map(function (h) { return { file: h.file, a: h.a, b: h.b, score: h.score }; }) },
      { replace: "rank", onRow: function (h, kind) {
        if (kind !== "RANK") return;
        n++;
        var el = tiles[hitKey(h)];
        if (!el) return;
        el.classList.remove("vlm-wait");
        var b = el.querySelector(".vlm") || el.appendChild(document.createElement("div"));
        b.className = "vlm" + (h.vlm == null ? "" : h.vlm >= 0.6 ? " ok" : h.vlm < 0.35 ? " no" : "");
        b.textContent = h.vlm == null ? "AI ?" : "AI " + Math.round(h.vlm * 10) + "/10";
        if (h.vlm != null && h.vlm >= 0.6) ok++;
        if (h.why) el.title = el.title.split("\nAI: ")[0] + "\nAI: " + h.why;
        el.classList.toggle("find-dim", h.vlm != null && h.vlm < 0.35);
        meta.textContent = base + " · AI đang xem kỹ " + n + "/" + top.length;
      } }).then(function (r) {
        clear();
        if (r.err) { if (r.err !== "đã hủy") meta.textContent = base + " · Xem kỹ: " + r.err; return; }
        var d = r.data || {};
        if (d.cancelled) return;
        (d.order || []).forEach(function (o) {             // re-append in the new order (moves the existing tiles)
          var el = tiles[hitKey({ file: o[0], a: o[1], b: o[2] })];
          if (el) res.appendChild(el);
        });
        meta.textContent = base + " · AI xem kỹ " + n + " cảnh (" + d.secs + " s): " + ok + " khớp";
      });
  }

  function lookAlike() {
    var res = $("findRes");
    if (!S().src) return;
    res.innerHTML = '<div class="find-wait">Đang so các take…</div>';
    srvAsk({ src: S().src, dups: true, thr: 0.95, k: 30 }).then(function (r) {
      res.innerHTML = "";
      if (r.err) { res.innerHTML = '<div class="find-wait bad">' + esc(r.err) + "</div>"; return; }
      if (!r.rows.length) { res.innerHTML = '<div class="find-wait">Không có take nào gần như trùng nhau.</div>'; return; }
      r.rows.forEach(function (d) {
        var pair = document.createElement("div");
        pair.className = "find-pair";
        pair.appendChild(tile({ file: d.a[0], a: d.a[1], b: d.a[2], thumb: d.thumb_a }, "A"));
        pair.appendChild(tile({ file: d.b[0], a: d.b[1], b: d.b[2], thumb: d.thumb_b }, Math.round(d.sim * 100) + "% giống"));
        res.appendChild(pair);
      });
      $("findMeta").textContent = r.rows.length + " cặp take giống nhau — giữ take đẹp hơn, ẩn take còn lại khi dựng";
    }, function (e) { res.innerHTML = '<div class="find-wait bad">' + esc(e.message) + "</div>"; });
  }

  function buildIndex() {
    if (!S().src) { NX.log("Chọn folder source trước.", "#e0b341"); return; }
    NX.run(["-m", "nxcore.segments", S().src], "Tách đoạn (shot)")
      .then(function () { return NX.run(["-m", "nxcore.ai.embed", "index", S().src], "Lập chỉ mục SigLIP2"); })
      .then(function () { return NX.run(["-m", "nxcore.ai.sounds", "index", S().src], "Lập chỉ mục âm thanh"); })
      .then(function () { $("findMeta").textContent = "Chỉ mục sẵn sàng — gõ mô tả cảnh cần tìm"; },
            function (e) { NX.log("✖ " + e.message + " — cần gói AI (Cài đặt → Cài gói AI)", "#e5534b"); });
  }

  // ===================================================================== DOM
  function mount() {
    var run = $("run");
    if (run && !$("findCard")) {
      var c = document.createElement("div");
      c.className = "card ai-card"; c.id = "findCard";
      c.innerHTML = '<h3><span class="ai-spark"></span>Tìm footage bằng AI <span class="sp muted" id="findMeta"></span></h3>' +
        '<div class="row"><input id="findQ" placeholder="Mô tả cảnh: hoàng hôn trên biển · người cười · xe chạy ban đêm"><button class="btn main" id="bFind">Tìm</button></div>' +
        '<div class="row" style="margin-top:6px;gap:6px;flex-wrap:wrap"><button class="btn chip" id="bFindIdx" title="Tách shot + lập chỉ mục hình ảnh (SigLIP2) cho source này — chạy 1 lần, có cache">Lập chỉ mục</button>' +
        '<button class="btn chip" id="bFindDup" title="Tìm các take gần như giống hệt nhau (quay lại cùng cảnh)">Take giống nhau</button>' +
        '<button class="btn chip" id="bFindSnd" title="Bật: tìm theo TIẾNG trong clip (tiếng cười, vỗ tay, sóng biển, gió, xe, nhạc, chim…) thay vì theo hình. Cần Lập chỉ mục (có thêm bước âm thanh, ~1 phút cho 300 clip).">Theo âm thanh</button>' +
        '<button class="btn chip" id="bFindDeep" title="Bật: sau mỗi lần tìm, AI nhìn ảnh (Qwen3-VL qua Ollama, chạy trên máy) xem kỹ 12 kết quả đầu, chấm điểm và sắp xếp lại — đúng hơn với câu có hành động (“người chạy trên đồi cát”), chậm hơn vài giây mỗi cảnh. Gõ tìm câu mới là dừng ngay.">Xem kỹ bằng AI</button>' +
        '<span class="muted" style="font-size:10.5px;align-self:center">Gõ tiếng Việt hoặc tiếng Anh · bấm ảnh để mở trong Source Monitor</span></div>' +
        '<div id="findRes" class="find-grid"></div>';
      var first = run.querySelector(".card");
      run.insertBefore(c, first ? first.nextSibling : run.firstChild);
      $("bFind").onclick = find;
      $("findQ").onkeydown = function (e) { if (e.key === "Enter") find(); };
      $("bFindIdx").onclick = buildIndex;
      $("bFindDup").onclick = lookAlike;
      $("bFindDeep").onclick = function () { setDeep(!deepOn()); };
      setDeep(deepOn());
      $("bFindSnd").onclick = function () { setSound(!soundOn()); };
      setSound(soundOn());
    }
    var set = $("set");
    if (set && !$("aiCard")) {
      var a = document.createElement("div");
      a.className = "card ai-card"; a.id = "aiCard";
      a.innerHTML = '<h3><span class="ai-spark"></span>AI tăng tốc &amp; tự sửa <span class="sp muted" id="aiMeta"></span></h3>' +
        '<div id="aiList" class="ai-list"><div class="muted">Đang kiểm tra…</div></div>' +
        '<div class="row" style="margin-top:8px;gap:6px;flex-wrap:wrap">' +
        '<button class="btn" id="bDocCheck">Kiểm tra</button>' +
        '<button class="btn main" id="bDocFix" title="Cài mọi thành phần bắt buộc còn thiếu (thư viện Python, FFmpeg, CUDA cho Whisper)">Tự sửa</button>' +
        '<button class="btn" id="bAiPack" title="PyTorch + SigLIP2 + beat_this + TransNetV2 + căn chỉnh từ + âm thanh + người nói (~3 GB, máy có GPU chạy nhanh hơn nhiều)">Cài gói AI</button>' +
        '<button class="btn" id="bAiModels" title="Tải trước model Whisper / SigLIP2 / wav2vec2 / beat_this / PANNs / WeSpeaker để lần chạy đầu không phải chờ">Tải trước model</button></div>' +
        '<div class="hint">Mở panel là tự kiểm tra — thiếu thành phần bắt buộc thì tự cài. Gói AI: bắt beat bằng mạng nơ-ron, tìm footage bằng câu chữ, nhận take giống nhau, chấm điểm đẹp/nét, căn thời gian từng từ thoại, tách cảnh video đã dựng.</div>';
      var sysCard = $("bSysCheck") ? $("bSysCheck").closest(".card") : null;
      set.insertBefore(a, sysCard || null);
      $("bDocCheck").onclick = function () { doctor(false); };
      $("bDocFix").onclick = function () { doctor(true, ["--ai", "no"]); };
      $("bAiPack").onclick = function () {
        NX.ask("Cài gói AI (~3 GB: PyTorch, SigLIP2, beat_this, TransNetV2, wav2vec2)? Có thể mất 5–15 phút tùy mạng.",
               ["Cài ngay"]).then(function (v) {
          if (v === "Cài ngay") doctor(true, ["--ai", "yes", "--models"], "Cài gói AI");
        });
      };
      $("bAiModels").onclick = function () { doctor(true, ["--ai", "no", "--models"], "Tải trước model"); };
    }
    var wm = $("whisperModel");
    if (wm && !wm.querySelector('option[value="erax-turbo"]')) {
      var o = document.createElement("option");
      o.value = "erax-turbo"; o.textContent = "erax-turbo — large-v3-turbo tinh chỉnh tiếng Việt (EraX, MIT)";
      wm.appendChild(o);
      if (S().whisperModel === "erax-turbo") wm.value = "erax-turbo";
    }
  }

  function init() {
    mount();
    renderDoctor();
    setTimeout(autoHeal, 3500);                   // after the panel finished its own start-up work
    setTimeout(function () { if (window.JESWorker) window.JESWorker.warm(); }, 6000);   // AI worker + model ready before the first use
  }
  window.JESAI = { find: find, lookAlike: lookAlike, buildIndex: buildIndex, doctor: doctor, srvAsk: srvAsk };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
