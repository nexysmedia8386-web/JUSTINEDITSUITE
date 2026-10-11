/* Justin EDIT Suite — minimal LLM client (Gemini REST + Anthropic Messages) with images + function calling.
   Uses Node https (CEP mixed context) so there is no CORS issue for either provider. */
(function () {
  "use strict";
  var https = require("https"), http = require("http");
  var GEMINI_DEFAULT = "gemini-3.8-flash";
  var GEMINI_CHAIN = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"];

  function post(host, pathname, headers, body, timeoutMs, plain, port) {
    return new Promise(function (resolve, reject) {
      var data = Buffer.from(JSON.stringify(body), "utf8");
      var req = (plain ? http : https).request({ host: host, port: port, path: pathname, method: "POST",
        headers: Object.assign({ "content-type": "application/json", "content-length": data.length }, headers) }, function (res) {
        var chunks = [];
        res.on("data", function (c) { chunks.push(c); });
        res.on("end", function () {
          var txt = Buffer.concat(chunks).toString("utf8"), j = null;
          try { j = JSON.parse(txt); } catch (e) {}
          if (res.statusCode >= 400) {
            var msg = (j && (j.error && (j.error.message || j.error.type))) || txt.slice(0, 300);
            var err = new Error("HTTP " + res.statusCode + ": " + msg); err.status = res.statusCode; return reject(err);
          }
          resolve(j);
        });
      });
      req.on("error", reject);
      req.setTimeout(timeoutMs || 180000, function () { req.destroy(new Error("timeout")); });
      req.write(data); req.end();
    });
  }

  function get(host, pathname, headers, plain, port) {
    return new Promise(function (resolve, reject) {
      var req = (plain ? http : https).get({ host: host, port: port, path: pathname, headers: headers, timeout: 8000 }, function (res) {
        var chunks = []; res.on("data", function (c) { chunks.push(c); });
        res.on("end", function () { try { resolve(JSON.parse(Buffer.concat(chunks).toString("utf8"))); } catch (e) { reject(e); } });
      });
      req.on("error", reject);
      req.on("timeout", function () { req.destroy(new Error("timeout")); });   // the timeout option alone only emits this event
    });
  }

  /* Pick a replacement Gemini model: the one named in the 404 message, else the newest "flash" model the key can use. */
  function geminiFallback(key, current, errMsg) {
    var named = (String(errMsg).match(/models\/(gemini-[\w.\-]+)/g) || []).map(function (m) { return m.replace("models/", ""); })
      .filter(function (m) { return m !== current; });
    if (named.length) return Promise.resolve(named[named.length - 1]);
    return get("generativelanguage.googleapis.com", "/v1beta/models?pageSize=200", { "x-goog-api-key": key }).then(function (j) {
      var ok = (j.models || []).filter(function (m) {
        return (m.supportedGenerationMethods || []).indexOf("generateContent") >= 0 && /gemini-[\d.]+-flash$/.test(m.name.replace("models/", ""));
      }).map(function (m) { return m.name.replace("models/", ""); });
      ok.sort(function (a, b) { return parseFloat(b.split("-")[1]) - parseFloat(a.split("-")[1]); });
      return ok[0] || null;
    }).catch(function () { return null; });
  }

  /* ---------- Gemini Files API (resumable upload) ---------- */
  function rawRequest(opts, bodyBuf) {
    return new Promise(function (resolve, reject) {
      var req = https.request(opts, function (res) {
        var chunks = []; res.on("data", function (c) { chunks.push(c); });
        res.on("end", function () { resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks).toString("utf8") }); });
      });
      req.on("error", reject);
      req.setTimeout(600000, function () { req.destroy(new Error("timeout")); });
      if (bodyBuf) req.write(bodyBuf);
      req.end();
    });
  }
  function geminiUpload(key, filePath, mime, displayName) {
    var fs = require("fs"), data = fs.readFileSync(filePath);
    var meta = Buffer.from(JSON.stringify({ file: { display_name: displayName } }), "utf8");
    return rawRequest({ host: "generativelanguage.googleapis.com", path: "/upload/v1beta/files", method: "POST", headers: {
      "x-goog-api-key": key, "X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
      "X-Goog-Upload-Header-Content-Length": String(data.length), "X-Goog-Upload-Header-Content-Type": mime,
      "Content-Type": "application/json", "Content-Length": meta.length } }, meta).then(function (r) {
      var url = r.headers["x-goog-upload-url"];
      if (!url) throw new Error("Files API không trả upload URL (HTTP " + r.status + "): " + r.body.slice(0, 200));
      var u = new URL(url);
      return rawRequest({ host: u.host, path: u.pathname + u.search, method: "POST", headers: {
        "Content-Length": data.length, "X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize" } }, data);
    }).then(function (r) {
      var j = JSON.parse(r.body);
      if (!j.file) throw new Error("Upload lỗi (HTTP " + r.status + "): " + r.body.slice(0, 200));
      return j.file;
    });
  }
  function geminiFile(key, name) {
    return get("generativelanguage.googleapis.com", "/v1beta/" + name, { "x-goog-api-key": key });
  }
  function geminiWaitActive(key, file, timeoutMs) {
    var t0 = Date.now();
    return (function poll(f) {
      if (f.state === "ACTIVE") return Promise.resolve(f);
      if (f.state === "FAILED") return Promise.reject(new Error("Gemini xử lý file thất bại: " + (f.displayName || f.name)));
      if (Date.now() - t0 > (timeoutMs || 180000)) return Promise.reject(new Error("Quá thời gian chờ Gemini xử lý " + (f.displayName || f.name)));
      return new Promise(function (r) { setTimeout(r, 2000); }).then(function () { return geminiFile(key, f.name); }).then(poll);
    })(file);
  }

  function withRetry(fn, n) {
    return fn().catch(function (e) {
      var quotaGone = e.status === 429 && /free_tier|quota exceeded/i.test(e.message);   // daily quota: retrying the same model is pointless
      if (n > 0 && !quotaGone && (e.status === 429 || e.status >= 500 || e.message === "timeout")) {
        return new Promise(function (r) { setTimeout(r, 2500); }).then(function () { return withRetry(fn, n - 1); });
      }
      throw e;
    });
  }

  /* ---------- Gemini ---------- */
  function Gemini(key, model) { this.key = key; this.model = model || GEMINI_DEFAULT; this.history = []; this.onModelChange = null; }
  /* files: [{ uri, mime, label, fps }] = videos uploaded with the Files API */
  Gemini.prototype.userTurn = function (text, images, files) {
    var parts = [];
    (files || []).forEach(function (f) {
      if (f.label) parts.push({ text: f.label });
      var p = { file_data: { mime_type: f.mime || "video/mp4", file_uri: f.uri } };
      if (f.fps) p.video_metadata = { fps: f.fps };
      parts.push(p);
    });
    parts.push({ text: text });
    (images || []).forEach(function (im) { parts.push({ inline_data: { mime_type: im.mime, data: im.b64 } }); });
    this.history.push({ role: "user", parts: parts });
  };
  Gemini.prototype.send = function (system, tools, opts) {
    var self = this, body = { contents: this.history, systemInstruction: { parts: [{ text: system }] },
      generationConfig: { temperature: (opts && opts.temperature) != null ? opts.temperature : 0.4 } };
    if (opts && opts.json) body.generationConfig.responseMimeType = "application/json";
    if (tools && tools.length) body.tools = [{ functionDeclarations: tools.map(function (t) {
      return { name: t.name, description: t.description, parameters: t.parameters }; }) }];
    function call() {
      return withRetry(function () {
        return post("generativelanguage.googleapis.com", "/v1beta/models/" + self.model + ":generateContent",
          { "x-goog-api-key": self.key }, body);
      }, 2);
    }
    self._tried = self._tried || {};
    function attempt() {
      return call().catch(function (e) {
        self._tried[self.model] = true;
        if (e.status === 404 && !self._switched) {        // model retired / not available for this key -> switch once
          self._switched = true;
          return geminiFallback(self.key, self.model, e.message).then(function (m) {
            if (!m) throw e;
            self.model = m; if (self.onModelChange) self.onModelChange(m);
            return attempt();
          });
        }
        if (e.status === 429 && /free_tier|quota/i.test(e.message)) {   // this model's free quota is used up -> next model in chain
          var next = GEMINI_CHAIN.filter(function (m) { return !self._tried[m]; })[0];
          if (!next) throw new Error("Hết quota free tier của mọi model Gemini hôm nay — thêm billing, đổi sang Ollama/OpenRouter, hoặc thử lại sau. (" + e.message.slice(0, 160) + ")");
          if (self.onQuota) self.onQuota(self.model, next);
          self.model = next;
          return attempt();
        }
        // 503 "high demand" / 5xx / timeout còn lỗi sau khi đã retry → model kế tiếp trong chuỗi thay vì dừng cả việc
        if (e.status >= 500 || e.message === "timeout") {
          var nx = GEMINI_CHAIN.filter(function (m) { return !self._tried[m]; })[0];
          if (nx) {
            if (self.onQuota) self.onQuota(self.model, nx, e.status ? "quá tải (HTTP " + e.status + ")" : "không phản hồi (timeout)");
            self.model = nx;
            return attempt();
          }
          throw new Error("Mọi model Gemini đang quá tải — thử lại sau ít phút, hoặc đổi model 'Viết kịch bản' sang OpenRouter/Ollama trong Cài đặt. (" + e.message.slice(0, 120) + ")");
        }
        throw e;
      });
    }
    return attempt().then(function (j) {
      var c = j && j.candidates && j.candidates[0];
      if (!c || !c.content) throw new Error("Gemini không trả nội dung" + (c && c.finishReason ? " (" + c.finishReason + ")" : ""));
      self.history.push(c.content);
      var text = "", calls = [];
      (c.content.parts || []).forEach(function (p, i) {
        if (p.text) text += p.text;
        if (p.functionCall) calls.push({ id: "g" + i, name: p.functionCall.name, args: p.functionCall.args || {} });
      });
      return { text: text.trim(), calls: calls };
    });
  };
  Gemini.prototype.toolResults = function (results) {
    var parts = [];
    results.forEach(function (r) {
      parts.push({ functionResponse: { name: r.name, response: { result: r.result } } });
      (r.images || []).forEach(function (im) { parts.push({ inline_data: { mime_type: im.mime, data: im.b64 } }); });
    });
    this.history.push({ role: "user", parts: parts });
  };
  Gemini.prototype.addText = function (role, text) {
    var r = role === "ai" ? "model" : "user", last = this.history[this.history.length - 1];
    if (last && last.role === r) last.parts.push({ text: text }); else this.history.push({ role: r, parts: [{ text: text }] });
  };

  /* ---------- Claude ---------- */
  function Claude(key, model, extra) {
    this.key = key;
    this.model = model || "claude-sonnet-5";
    this.extra = extra || {};
    this.history = [];
  }
  Claude.prototype.userTurn = function (text, images) {
    var content = (images || []).map(function (im) {
      return { type: "image", source: { type: "base64", media_type: im.mime, data: im.b64 } }; });
    content.push({ type: "text", text: text });
    this.history.push({ role: "user", content: content });
  };
  Claude.prototype.send = function (system, tools, opts) {
    var self = this, body = { model: this.model, max_tokens: (opts && opts.maxTokens) || 8000, system: system,
      messages: this.history, temperature: (opts && opts.temperature) != null ? opts.temperature : 0.4 };
    if (tools && tools.length) body.tools = tools.map(function (t) {
      return { name: t.name, description: t.description, input_schema: t.parameters }; });

    // Parse API key & optional workspace ID (for Anthropic Org-level / Admin keys)
    var rawKey = (self.key || "").trim();
    var key = rawKey;
    var wsId = ((self.extra && self.extra.workspaceId) || "").trim();

    if (rawKey.indexOf("|") >= 0) {
      var p1 = rawKey.split("|"); key = p1[0].trim(); if (!wsId) wsId = p1[1].trim();
    } else if (rawKey.indexOf(";") >= 0) {
      var p2 = rawKey.split(";"); key = p2[0].trim(); if (!wsId) wsId = p2[1].trim();
    } else if (rawKey.indexOf("#") >= 0) {
      var p3 = rawKey.split("#"); key = p3[0].trim(); if (!wsId) wsId = p3[1].trim();
    }

    if (!wsId && typeof window !== "undefined" && window.NX && window.NX.S) {
      wsId = (window.NX.S.anthropicWs || window.NX.S.anthropicWorkspaceId || (window.NX.S.workspaces && window.NX.S.workspaces.claude) || "").trim();
    }

    var headers = { "x-api-key": key, "anthropic-version": "2023-06-01" };
    if (wsId) headers["anthropic-workspace-id"] = wsId;

    return withRetry(function () {
      return post("api.anthropic.com", "/v1/messages", headers, body).catch(function (e) {
        if (e && e.message && /anthropic-workspace-id|scoped to a workspace/i.test(e.message)) {
          var help = "\n\n💡 Hướng dẫn sửa nhanh:\n1. Vào console.anthropic.com > bấm vào Workspace của bạn (vd: 'Default') > tạo API Key mới từ bên trong Workspace đó (khuyến nghị);\nHOẶC\n2. Điền Workspace ID (wrk_...) vào ô 'Anthropic Workspace ID' ở tab Cài đặt (hoặc dán key dạng: sk-ant-...|wrk_...).";
          var newErr = new Error(e.message + help);
          newErr.status = e.status;
          throw newErr;
        }
        throw e;
      });
    }, 2).then(function (j) {
      self.history.push({ role: "assistant", content: j.content });
      var text = "", calls = [];
      (j.content || []).forEach(function (b) {
        if (b.type === "text") text += b.text;
        if (b.type === "tool_use") calls.push({ id: b.id, name: b.name, args: b.input || {} });
      });
      return { text: text.trim(), calls: calls };
    });
  };
  Claude.prototype.toolResults = function (results) {
    this.history.push({ role: "user", content: results.map(function (r) {
      var txt = typeof r.result === "string" ? r.result : JSON.stringify(r.result);
      if (!r.images || !r.images.length) return { type: "tool_result", tool_use_id: r.id, content: txt };
      return { type: "tool_result", tool_use_id: r.id, content: r.images.map(function (im) {
        return { type: "image", source: { type: "base64", media_type: im.mime, data: im.b64 } }; }).concat([{ type: "text", text: txt }]) };
    }) });
  };
  Claude.prototype.addText = function (role, text) {
    var r = role === "ai" ? "assistant" : "user", last = this.history[this.history.length - 1];
    if (!this.history.length && r === "assistant") this.history.push({ role: "user", content: [{ type: "text", text: "(tiếp tục hội thoại cũ)" }] });
    if (last && last.role === r) last.content.push({ type: "text", text: text }); else this.history.push({ role: r, content: [{ type: "text", text: text }] });
  };

  /* ---------- Groq (OpenAI-compatible, ultra-fast LPU inference: https://console.groq.com) ---------- */
  function Groq(key, model) {
    this.key = key;
    this.model = model || "llama-3.3-70b-versatile";
    this.history = [];
  }
  Groq.prototype.userTurn = function (text, images) {
    var content = [{ type: "text", text: text }];
    (images || []).forEach(function (im) {
      content.push({ type: "image_url", image_url: { url: "data:" + im.mime + ";base64," + im.b64 } });
    });
    this.history.push({ role: "user", content: images && images.length ? content : text });
  };
  Groq.prototype.send = function (system, tools, opts) {
    var self = this;
    var body = {
      model: this.model || "llama-3.3-70b-versatile",
      messages: [{ role: "system", content: system }].concat(this.history),
      temperature: (opts && opts.temperature) != null ? opts.temperature : 0.4
    };
    if (opts && opts.json) body.response_format = { type: "json_object" };
    if (tools && tools.length) {
      body.tools = tools.map(function (t) {
        return { type: "function", function: { name: t.name, description: t.description, parameters: t.parameters } };
      });
    }
    return withRetry(function () {
      return post("api.groq.com", "/openai/v1/chat/completions", {
        authorization: "Bearer " + self.key
      }, body, 120000);
    }, 2).then(function (j) {
      var m = j.choices && j.choices[0] && j.choices[0].message;
      if (!m) throw new Error("Groq không trả nội dung");
      self.history.push({ role: "assistant", content: m.content || "", tool_calls: m.tool_calls });
      var calls = (m.tool_calls || []).map(function (c) {
        var a = {};
        try { a = JSON.parse(c.function.arguments || "{}"); } catch (e) {}
        return { id: c.id, name: c.function.name, args: a };
      });
      return { text: String(m.content || "").replace(/<think>[\s\S]*?<\/think>/g, "").trim(), calls: calls };
    });
  };
  Groq.prototype.toolResults = function (results) {
    var self = this, imgs = [];
    results.forEach(function (r) {
      self.history.push({ role: "tool", tool_call_id: r.id, content: typeof r.result === "string" ? r.result : JSON.stringify(r.result) });
      imgs = imgs.concat(r.images || []);
    });
    if (imgs.length) self.userTurn("(ảnh do tool trả về)", imgs);
  };
  Groq.prototype.addText = function (role, text) {
    this.history.push({ role: role === "ai" ? "assistant" : "user", content: text });
  };
  function groqModels(key) {
    if (!key) return Promise.resolve(defaultGroqModels());
    return get("api.groq.com", "/openai/v1/models", { authorization: "Bearer " + key }).then(function (j) {
      var ok = (j.data || []).filter(function (m) {
        return m.active !== false && !/whisper/i.test(m.id);
      }).map(function (m) {
        var note = "";
        if (/70b/i.test(m.id)) note = "mạnh, hỗ trợ tool · 128K";
        else if (/8b/i.test(m.id)) note = "siêu nhanh · 128K";
        else if (/r1/i.test(m.id)) note = "suy luận sâu (reasoning)";
        else if (/vision|vl/i.test(m.id)) note = "nhận diện ảnh";
        return { id: m.id, note: note };
      });
      return ok.length ? ok : defaultGroqModels();
    }).catch(function () {
      return defaultGroqModels();
    });
  }
  function defaultGroqModels() {
    return [
      { id: "llama-3.3-70b-versatile", note: "khuyến nghị · 128K context · thông minh nhất" },
      { id: "llama-3.1-8b-instant", note: "siêu tốc · 128K context · nhẹ nhất" },
      { id: "deepseek-r1-distill-llama-70b", note: "suy luận sâu (reasoning)" },
      { id: "mixtral-8x7b-32768", note: "32K context · cân bằng" },
      { id: "gemma2-9b-it", note: "Google Gemma 2" }
    ];
  }

  /* ---------- OpenRouter (OpenAI-compatible; free models end with ":free") ----------
     Free models fail often: 403 (model restricted, e.g. "only available on agentic harnesses"), 404 (retired),
     429 (upstream provider rate-limited), 402/5xx. send() fails over automatically to the next suitable free model,
     remembers bad models (403/404: 3 days, 429: 15 min) and, when every free model fails and a Gemini key exists,
     hands the conversation to Gemini. */
  var OR_BAD_KEY = "jes_or_bad";
  function orBad() { try { var j = JSON.parse(localStorage.getItem(OR_BAD_KEY) || "{}"), now = Date.now(), o = {};
    Object.keys(j).forEach(function (k) { if (j[k].until > now) o[k] = j[k]; }); return o; } catch (e) { return {}; } }
  function orMark(model, status, msg) {
    try { var j = orBad(), long = status === 403 || status === 404 || status === 400;
      j[model] = { until: Date.now() + (long ? 3 * 864e5 : 15 * 60e3), status: status, why: String(msg || "").slice(0, 120) };
      localStorage.setItem(OR_BAD_KEY, JSON.stringify(j)); } catch (e) {}
  }
  function orReason(e) {
    var m = String(e.message || "");
    if (e.status === 403 && /agentic|harness/i.test(m)) return "chỉ mở cho app agent";
    if (e.status === 403) return "bị chặn (403)";
    if (e.status === 404) return "không còn/không có quyền";
    if (e.status === 429) return "đang quá tải (429)";
    if (e.status === 402) return "cần credit";
    if (e.status === 400) return "không hỗ trợ yêu cầu này";
    if (e.status >= 500) return "lỗi server (" + e.status + ")";
    return m.slice(0, 60);
  }
  function OpenRouter(key, model, extra) { this.key = key; this.model = model; this.pinned = model || ""; this.history = []; this.extra = extra || {}; this._tried = {}; }
  OpenRouter.prototype._sys = function (system) { return [{ role: "system", content: system }].concat(this.history); };
  OpenRouter.prototype.userTurn = function (text, images) {
    if (this._delegate) return this._delegate.userTurn(text, images);
    var content = [{ type: "text", text: text }];
    (images || []).forEach(function (im) { content.push({ type: "image_url", image_url: { url: "data:" + im.mime + ";base64," + im.b64 } }); });
    this.history.push({ role: "user", content: images && images.length ? content : text });
  };
  OpenRouter.prototype._hasImages = function () {
    return this.history.some(function (h) { return Array.isArray(h.content) && h.content.some(function (c) { return c.type === "image_url"; }); });
  };
  OpenRouter.prototype._candidates = function (needTools) {
    var self = this, bad = orBad(), vis = this._hasImages();
    return openRouterModels(this.key).then(function (ms) {
      var ok = ms.filter(function (m) { return !bad[m.id] && !self._tried[m.id] && (!vis || m.vision) && (!needTools || m.tools); });
      ok.sort(function (a, b) { return (b.ctx || 0) - (a.ctx || 0); });
      return ok.map(function (m) { return m.id; });
    }).catch(function () { return []; });
  };
  OpenRouter.prototype._toGemini = function (key) {
    var g = new Gemini(key, GEMINI_DEFAULT);
    this.history.forEach(function (h) {
      if (h.role === "user") {
        if (Array.isArray(h.content)) {
          var parts = [];
          h.content.forEach(function (c) {
            if (c.type === "text") parts.push({ text: c.text });
            else if (c.type === "image_url") { var m = /^data:([^;]+);base64,(.*)$/.exec(c.image_url.url); if (m) parts.push({ inline_data: { mime_type: m[1], data: m[2] } }); }
          });
          g.history.push({ role: "user", parts: parts });
        } else g.addText("user", String(h.content || ""));
      } else if (h.role === "assistant") {
        var t = String(h.content || "") + ((h.tool_calls || []).length ? "\n(đã gọi tool: " + h.tool_calls.map(function (c) { return c.function.name; }).join(", ") + ")" : "");
        g.addText("ai", t || "(…)");
      } else if (h.role === "tool") g.addText("user", "(kết quả tool) " + String(h.content || "").slice(0, 4000));
    });
    return g;
  };
  OpenRouter.prototype.send = function (system, tools, opts) {
    var self = this, needTools = !!(tools && tools.length), hops = 0, errs = [];
    if (this._delegate) return this._delegate.send(system, tools, opts);
    function one() {
      var body = { model: self.model, messages: self._sys(system), temperature: (opts && opts.temperature) != null ? opts.temperature : 0.4 };
      if (opts && opts.json) body.response_format = { type: "json_object" };
      if (needTools) body.tools = tools.map(function (t) { return { type: "function", function: { name: t.name, description: t.description, parameters: t.parameters } }; });
      return (function tryOnce(n) {
        return post("openrouter.ai", "/api/v1/chat/completions", { authorization: "Bearer " + self.key, "x-title": "Justin EDIT Suite" }, body).catch(function (e) {
          if (e.status === 400 && body.response_format && /response_format|json/i.test(e.message)) { delete body.response_format; return tryOnce(n); }
          if (n > 0 && (e.status === 429 || e.status >= 500 || e.message === "timeout"))
            return new Promise(function (r) { setTimeout(r, e.status === 429 ? 4000 : 2500); }).then(function () { return tryOnce(n - 1); });
          throw e;
        });
      })(1);
    }
    function next(e) {
      var failover = e && (e.status === 403 || e.status === 404 || e.status === 429 || e.status === 402 || e.status === 400 || e.status >= 500 || e.message === "timeout");
      if (!failover) throw e;
      self._tried[self.model] = true;
      orMark(self.model, e.status, e.message);
      errs.push(self.model.split("/").pop() + ": " + orReason(e));
      if (++hops > 6) return exhausted(e);
      return self._candidates(needTools).then(function (c) {
        if (!c.length) return exhausted(e);
        var from = self.model; self.model = c[0];
        if (self.extra.onSwitch) self.extra.onSwitch(from, self.model, orReason(e), e.status === 403 || e.status === 404);
        return one().catch(next);
      });
    }
    function exhausted(e) {
      var gk = self.extra.fallbackKey;
      if (gk) {
        if (self.extra.onSwitch) self.extra.onSwitch("OpenRouter", "Gemini " + GEMINI_DEFAULT, "hết model free dùng được", false);
        self._delegate = self._toGemini(gk);
        self._delegate.onQuota = self.extra.onQuota;
        return self._delegate.send(system, tools, opts);
      }
      var err = new Error("Mọi model free OpenRouter đều lỗi (" + errs.slice(-4).join(" · ") + "). Thử lại sau vài phút, dán Gemini API key ở Cài đặt để tool tự chuyển sang Gemini, hoặc dùng Ollama.");
      err.status = e && e.status; throw err;
    }
    var ready = this.model && !orBad()[this.model] ? Promise.resolve(this.model) : this._candidates(needTools).then(function (c) {
      if (self.model && orBad()[self.model] && self.extra.onSwitch && c[0]) self.extra.onSwitch(self.model, c[0], "đang bị tạm loại (" + orBad()[self.model].why.slice(0, 50) + ")", false);
      if (!c.length) { if (self.extra.fallbackKey) return null; throw new Error("Không tìm thấy model free phù hợp trên OpenRouter" + (self._hasImages() ? " (cần model đọc được ảnh)" : "")); }
      self.model = c[0]; return c[0];
    });
    return ready.then(function (m) {
      if (m === null) return exhausted(null);
      return one().catch(next);
    }).then(function (j) {
      if (self._delegate && j && j.text !== undefined && !j.choices) return j;      // already a Gemini result
      var m = j.choices && j.choices[0] && j.choices[0].message;
      if (!m) throw new Error("OpenRouter không trả nội dung");
      self.history.push({ role: "assistant", content: m.content || "", tool_calls: m.tool_calls });
      var calls = (m.tool_calls || []).map(function (c) {
        var a = {}; try { a = JSON.parse(c.function.arguments || "{}"); } catch (e) {}
        return { id: c.id, name: c.function.name, args: a };
      });
      return { text: String(m.content || "").trim(), calls: calls };
    });
  };
  OpenRouter.prototype.toolResults = function (results) {
    if (this._delegate) return this._delegate.toolResults(results);
    var self = this, imgs = [];
    results.forEach(function (r) {
      self.history.push({ role: "tool", tool_call_id: r.id, content: typeof r.result === "string" ? r.result : JSON.stringify(r.result) });
      imgs = imgs.concat(r.images || []);
    });
    if (imgs.length) self.userTurn("(ảnh do tool trả về)", imgs);
  };
  OpenRouter.prototype.addText = function (role, text) { if (this._delegate) return this._delegate.addText(role, text); this.history.push({ role: role === "ai" ? "assistant" : "user", content: text }); };
  function openRouterModels(key) {
    return get("openrouter.ai", "/api/v1/models", key ? { authorization: "Bearer " + key } : {}).then(function (j) {
      return (j.data || []).filter(function (m) { return /:free$/.test(m.id); }).map(function (m) {
        var mods = (m.architecture && m.architecture.input_modalities) || [], sp = m.supported_parameters || [];
        return { id: m.id, vision: mods.indexOf("image") >= 0, tools: sp.indexOf("tools") >= 0, ctx: m.context_length };
      });
    });
  }

  /* ---------- Ollama (local, native /api/chat so num_ctx can be raised) ---------- */
  var OLLAMA = { host: "127.0.0.1", port: 11434 };
  function Ollama(model, numCtx) { this.model = model || "gemma4:12b-it-qat"; this.numCtx = numCtx || 16384; this.history = []; }
  Ollama.prototype.userTurn = function (text, images) {
    var m = { role: "user", content: text };
    if (images && images.length) m.images = images.map(function (im) { return im.b64; });
    this.history.push(m);
  };
  Ollama.prototype.send = function (system, tools, opts) {
    var self = this, body = { model: this.model, stream: false, keep_alive: "5m",
      messages: [{ role: "system", content: system }].concat(this.history),
      options: { num_ctx: this.numCtx, temperature: (opts && opts.temperature) != null ? opts.temperature : 0.4 } };
    if (opts && opts.json) body.format = "json";
    if (tools && tools.length) body.tools = tools.map(function (t) { return { type: "function", function: { name: t.name, description: t.description, parameters: t.parameters } }; });
    var CTX_CAP = 49152;          // 12 GB VRAM: 8–12B model + KV cache stays on the GPU up to ~48K
    var outTok = (opts && opts.maxTokens) || 4096;
    // Ollama cắt im lặng phần prompt vượt num_ctx và dừng output khi chạm trần → JSON dở dang.
    // Ước lượng trước (đo tokenizer gemma4: tiếng Việt ~2.0 ký tự/token, ảnh ~600 token) và mở context đủ cho input + output.
    var estIn = 0;
    (function fit() {
      var chars = 0, imgs = 0;
      body.messages.forEach(function (m) { chars += String(m.content || "").length; imgs += (m.images || []).length; });
      estIn = Math.ceil(chars / 2.0) + imgs * 600;
      var need = estIn + outTok + 512;
      if (need > CTX_CAP) throw new Error("Dữ liệu gửi AI local ~" + estIn + " token, vượt context tối đa " + CTX_CAP +
        " của model local — dùng Gemini/OpenRouter cho bước 'Viết kịch bản', hoặc loại bớt nhóm clip.");
      var want = Math.min(CTX_CAP, Math.ceil(need / 4096) * 4096);
      if (want > body.options.num_ctx) {
        var from = body.options.num_ctx; body.options.num_ctx = self.numCtx = want;
        if (self.onCtx) self.onCtx(from, want, need - outTok - 512);
      }
    })();
    if (opts && opts.maxTokens) body.options.num_predict = outTok;
    function call() { return post(OLLAMA.host, "/api/chat", {}, body, 600000, true, OLLAMA.port); }
    var jsonTries = 0;
    // gemma4 / qwen3 là model "thinking": Ollama dồn output vào message.thinking → content rỗng → "JSON không hợp lệ".
    // Bước cần JSON không cần suy nghĩ dài: tắt thinking (model không hỗ trợ → Ollama báo 400, bỏ cờ rồi gọi lại).
    if (opts && opts.json && !opts.think) body.think = false;
    function stripFence(t) { return t.replace(/^```(?:json)?\s*/i, "").replace(/```\s*$/, "").trim(); }
    function checkJSON(j) {
      if (!(opts && opts.json) || (tools && tools.length)) return j;
      var m = j.message || {}, txt = String(m.content || "").replace(/<think>[\s\S]*?<\/think>/g, "").trim();
      var pe = j.prompt_eval_count || 0, ev = j.eval_count || 0, ctx = body.options.num_ctx;
      // input bị Ollama cắt (giữ ~nửa context cuối, mất cả system prompt): số token nhận ≈ trần context nhưng nhỏ hơn hẳn ước lượng.
      var inCut = pe >= 0.45 * ctx && estIn > pe * 1.15;
      var ctxFull = pe + ev >= ctx - 16, outCut = j.done_reason === "length";
      try { m.content = JSON.stringify(JSON.parse(stripFence(txt))); return j; } catch (e) {}
      var fixed = null; try { fixed = parseJSON(txt); } catch (e) {}
      if (fixed && !inCut && !outCut) { m.content = JSON.stringify(fixed); return j; }
      if (jsonTries++ >= 2) {
        if (fixed && !inCut) { m.content = JSON.stringify(fixed); return j; }      // hết lượt thử: nhận bản đã vá
        throw new Error("AI local (" + self.model + ") trả JSON không hợp lệ" +
          (!txt && m.thinking ? " — model dùng hết lượt output để 'suy nghĩ'" : inCut || ctxFull ? " — hết context" : outCut ? " — output quá dài" : "") +
          ". Thử model local khác, hoặc dùng Gemini/OpenRouter cho bước này.");
      }
      if ((inCut || (outCut && ctxFull)) && ctx < CTX_CAP) {                  // thiếu context → mở rộng, chạy lại
        body.options.num_ctx = self.numCtx = Math.min(CTX_CAP, Math.ceil(ctx * 1.5 / 4096) * 4096);
        if (self.onCtx) self.onCtx(ctx, body.options.num_ctx, pe);
        return call().then(checkJSON);
      }
      if (outCut && !ctxFull) {                                                // chạm num_predict → cho viết dài hơn
        body.options.num_predict = Math.min(16000, Math.round((body.options.num_predict || outTok) * 1.6));
        if (!txt && m.thinking) body.think = false;
        return call().then(checkJSON);
      }
      // trả lời lệch format → hỏi lại 1 lượt, đưa câu trả lời lỗi vào lịch sử
      var msgs = body.messages;
      body.messages = msgs.concat([{ role: "assistant", content: txt.slice(0, 6000) },
        { role: "user", content: "Câu trả lời trên KHÔNG phải JSON hợp lệ (thiếu/thừa dấu, bị cắt, hoặc có chữ ngoài JSON). " +
          "Trả lại DUY NHẤT một object JSON hợp lệ, đúng schema đã yêu cầu, đầy đủ, không giải thích, không markdown." }]);
      return call().then(function (j2) { body.messages = msgs; return checkJSON(j2); }, function (e2) { body.messages = msgs; throw e2; });
    }
    return call().then(checkJSON).catch(function grow(e) {
      // prompt longer than num_ctx → raise the context window and retry (remember it for the next calls)
      if (e.status === 400 && /exceed.*context|context size|n_ctx/i.test(e.message)) {
        var m = /n_prompt_tokens\\?"?\s*:\s*(\d+)/.exec(e.message) || /request \((\d+) tokens\)/.exec(e.message);
        var need = m ? +m[1] : Math.round(body.options.num_ctx * 1.5);
        var want = Math.min(CTX_CAP, Math.ceil((need + ((opts && opts.maxTokens) || 4096)) / 4096) * 4096);
        if (want > body.options.num_ctx) {
          var from = body.options.num_ctx;
          body.options.num_ctx = self.numCtx = want;
          if (self.onCtx) self.onCtx(from, want, need);
          return call().then(checkJSON).catch(grow);
        }
        throw new Error("Dữ liệu gửi AI (" + need + " token) vượt giới hạn " + CTX_CAP + " token của model local — chia nhỏ source (loại bớt nhóm clip) hoặc dùng Gemini cho bước này.");
      }
      if (e.status === 400 && body.think === false && /think/i.test(e.message)) { delete body.think; return call().then(checkJSON).catch(grow); }
      if (/ECONNREFUSED/.test(e.message)) throw new Error("Ollama chưa chạy — mở ứng dụng Ollama (hoặc chạy 'ollama serve')");
      if (e.status === 404) throw new Error("Chưa có model " + self.model + " trong Ollama — bấm 'Tải model' ở Cài đặt");
      if (e.status === 400 && tools && tools.length) throw new Error("Model " + self.model + " không hỗ trợ gọi tool — chọn gemma4 / qwen3-vl");
      throw e;
    }).then(function (j) {
      var m = j.message || {};
      self.history.push({ role: "assistant", content: m.content || "", tool_calls: m.tool_calls });
      var calls = (m.tool_calls || []).map(function (c, i) {
        var a = c.function.arguments; if (typeof a === "string") { try { a = JSON.parse(a); } catch (e) { a = {}; } }
        return { id: "o" + i, name: c.function.name, args: a || {} };
      });
      return { text: String(m.content || "").replace(/<think>[\s\S]*?<\/think>/g, "").trim(), calls: calls };
    });
  };
  Ollama.prototype.toolResults = function (results) {
    var self = this, imgs = [];
    results.forEach(function (r) {
      self.history.push({ role: "tool", tool_name: r.name, content: typeof r.result === "string" ? r.result : JSON.stringify(r.result) });
      imgs = imgs.concat(r.images || []);
    });
    if (imgs.length) self.userTurn("(ảnh do tool trả về)", imgs);
  };
  Ollama.prototype.addText = function (role, text) { this.history.push({ role: role === "ai" ? "assistant" : "user", content: text }); };
  function ollamaModels() {
    return get(OLLAMA.host, "/api/tags", {}, true, OLLAMA.port).then(function (j) {
      return (j.models || []).map(function (m) { return { id: m.name, size: m.size }; });
    });
  }
  /* pull with progress callback(status, pct) */
  function ollamaPull(name, onProgress) {
    return new Promise(function (resolve, reject) {
      var data = Buffer.from(JSON.stringify({ model: name, stream: true }), "utf8"), last = -1, buf = "";
      var req = http.request({ host: OLLAMA.host, port: OLLAMA.port, path: "/api/pull", method: "POST",
        headers: { "content-type": "application/json", "content-length": data.length } }, function (res) {
        res.setEncoding("utf8");                              // multi-byte chars never split between chunks
        res.on("data", function (c) {
          buf += c;
          var lines = buf.split("\n"); buf = lines.pop();
          lines.forEach(function (l) {
            if (!l.trim()) return; var j; try { j = JSON.parse(l); } catch (e) { return; }
            if (j.error) return reject(new Error(j.error));
            var pct = j.total ? Math.floor(j.completed / j.total * 100) : null;
            if (pct !== last || !j.total) { last = pct; onProgress && onProgress(j.status, pct); }
          });
        });
        res.on("end", resolve);
      });
      req.on("error", function (e) { reject(/ECONNREFUSED/.test(e.message) ? new Error("Ollama chưa chạy") : e); });
      req.write(data); req.end();
    });
  }

  var PROVIDERS = {
    gemini:     { label: "Gemini (Google) — free tier", needsKey: true,  def: GEMINI_DEFAULT },
    groq:       { label: "Groq (siêu tốc, LPU)",        needsKey: true,  def: "llama-3.3-70b-versatile" },
    claude:     { label: "Claude (Anthropic)",          needsKey: true,  def: "claude-sonnet-5" },
    openrouter: { label: "OpenRouter — model free",     needsKey: true,  def: "" },
    ollama:     { label: "Ollama — local, offline",     needsKey: false, def: "gemma4:12b-it-qat" }
  };
  function create(provider, key, model, onModelChange, extra, onQuota) {
    var P = PROVIDERS[provider] || PROVIDERS.gemini;
    if (P.needsKey && !key) throw new Error("Chưa có API key " + P.label + " — điền ở tab Cài đặt.");
    if (provider === "groq") return new Groq(key, model || P.def);
    if (provider === "claude") return new Claude(key, model || P.def, extra);
    if (provider === "openrouter") return new OpenRouter(key, model || "", extra);
    if (provider === "ollama") { var o = new Ollama(model || P.def, extra && extra.numCtx); o.onCtx = extra && extra.onCtx; return o; }
    var g = new Gemini(key, model || P.def); g.onModelChange = onModelChange; g.onQuota = onQuota; return g;
  }
  function listModels(provider, key) {
    if (provider === "groq") return groqModels(key);
    if (provider === "ollama") return ollamaModels().then(function (ms) { return ms.map(function (m) { return { id: m.id, note: (m.size / 1e9).toFixed(1) + "GB" }; }); });
    if (provider === "openrouter") return openRouterModels(key).then(function (ms) {
      return ms.sort(function (a, b) { return (b.vision + b.tools) - (a.vision + a.tools); })
        .map(function (m) { var bd = orBad()[m.id];
          return { id: m.id, note: (bd ? "⛔ tạm loại: " + (bd.status === 429 ? "quá tải" : bd.status === 403 ? "bị chặn" : "lỗi " + bd.status) + " · " : "") + (m.vision ? "ảnh " : "") + (m.tools ? "tool " : "") + Math.round((m.ctx || 0) / 1000) + "K" }; }); });
    if (provider === "gemini" && key) return get("generativelanguage.googleapis.com", "/v1beta/models?pageSize=200", { "x-goog-api-key": key }).then(function (j) {
      return (j.models || []).filter(function (m) { return (m.supportedGenerationMethods || []).indexOf("generateContent") >= 0 && /gemini/.test(m.name); })
        .map(function (m) { return { id: m.name.replace("models/", ""), note: "" }; }); });
    if (provider === "claude") return Promise.resolve([
      { id: "claude-opus-5-5", note: "mạnh nhất cân bằng · $4/$20" }, { id: "claude-sonnet-5", note: "nhanh · $2/$10" },
      { id: "claude-fable-5-1", note: "suy luận sâu · $10/$50" }, { id: "claude-haiku-4-5-20251001", note: "rẻ nhất · $1/$5" }]);
    return Promise.resolve([]);
  }

  /* parse JSON even when the model wraps it in ```json fences */
  function repairJSON(t) {
    var a = t.search(/[{\[]/);
    if (a < 0) return null;
    t = t.slice(a);
    var out = "", stack = [], inStr = false, escp = false, i, c;
    for (i = 0; i < t.length; i++) {
      c = t[i];
      if (inStr) {
        if (escp) { out += c; escp = false; continue; }
        if (c === "\\") { out += c; escp = true; continue; }
        if (c === '"') { inStr = false; out += c; continue; }
        if (c === "\n") { out += "\\n"; continue; }
        if (c === "\r") continue;
        if (c === "\t") { out += "\\t"; continue; }
        out += c; continue;
      }
      if (c === '"') { inStr = true; out += c; continue; }
      if (c === "/" && t[i + 1] === "/") { while (i < t.length && t[i] !== "\n") i++; continue; }
      if (c === "/" && t[i + 1] === "*") { i = t.indexOf("*/", i + 2); if (i < 0) break; i++; continue; }
      if (c === "{" || c === "[") stack.push(c === "{" ? "}" : "]");
      else if (c === "}" || c === "]") { if (stack.length && stack[stack.length - 1] === c) stack.pop(); else continue; if (!stack.length) { out += c; break; } }
      out += c;
    }
    if (inStr) out += '"';
    out = out.replace(/,\s*$/, "").replace(/,?\s*"[^"]*"\s*:\s*$/, "").replace(/:\s*$/, ": null");
    while (stack.length) out = out.replace(/,\s*$/, "") + stack.pop();
    return out.replace(/,\s*([}\]])/g, "$1");
  }
  function parseJSON(text) {
    var t = String(text || "").replace(/<think>[\s\S]*?<\/think>/g, "").trim().replace(/^```(?:json)?\s*/i, "").replace(/```\s*$/, "");
    try { return JSON.parse(t); } catch (e) {}
    var a = t.indexOf("{"), b = t.lastIndexOf("}");
    if (a >= 0 && b > a) { try { return JSON.parse(t.slice(a, b + 1)); } catch (e) {} }
    var r = repairJSON(t);
    if (r) { try { return JSON.parse(r); } catch (e) {} }
    throw new Error("AI trả JSON không hợp lệ");
  }

  window.NXLLM = { create: create, parseJSON: parseJSON, listModels: listModels, ollamaPull: ollamaPull, ollamaModels: ollamaModels, PROVIDERS: PROVIDERS,
    geminiUpload: geminiUpload, geminiFile: geminiFile, geminiWaitActive: geminiWaitActive, GEMINI_CHAIN: GEMINI_CHAIN };
})();
