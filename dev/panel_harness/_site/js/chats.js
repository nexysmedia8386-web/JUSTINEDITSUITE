/* Justin EDIT Suite — persistent AI Assist conversations.
   One JSON file per conversation in %APPDATA%/JustinEditSuite/chats/<id>.json:
   { id, title, created, updated, provider, model, src, messages: [{ role: user|ai|ask|sys|err, text, t }] } */
(function () {
  "use strict";
  var fs = require("fs"), path = require("path");

  function Store(dir) { this.dir = dir; try { fs.mkdirSync(dir, { recursive: true }); } catch (e) {} this.cur = null; this._t = null; this._idx = null; this._dirM = -1; }
  function meta(j) { return { id: j.id, title: j.title, updated: j.updated, count: (j.messages || []).length }; }
  /* list of conversations: every chat file is parsed ONCE (index kept in memory); afterwards only when the folder
     changes from outside (other panel instance) — the open conversation comes from memory, so a message never
     re-reads every chat on disk */
  Store.prototype.list = function () {
    var self = this, dm = -1;
    try { dm = fs.statSync(this.dir).mtimeMs; } catch (e) {}
    if (!this._idx || dm !== this._dirM) {
      var idx = {};
      try {
        fs.readdirSync(this.dir).filter(function (f) { return /\.json$/.test(f); }).forEach(function (f) {
          var id = f.replace(/\.json$/, ""), old = self._idx && self._idx[id], st = null;
          try { st = fs.statSync(path.join(self.dir, f)); } catch (e) { return; }
          if (old && old._m === st.mtimeMs) { idx[id] = old; return; }
          try { var j = JSON.parse(fs.readFileSync(path.join(self.dir, f), "utf8")); var m = meta(j); m._m = st.mtimeMs; idx[m.id || id] = m; } catch (e) {}
        });
      } catch (e) {}
      this._idx = idx; this._dirM = dm;
    }
    var all = Object.assign({}, this._idx);
    if (this.cur && this.cur.messages && this.cur.messages.length) all[this.cur.id] = meta(this.cur);
    return Object.keys(all).map(function (k) { return all[k]; }).sort(function (a, b) { return (b.updated || 0) - (a.updated || 0); });
  };
  Store.prototype.create = function (meta) {
    var now = Date.now();
    this.cur = { id: "c" + now.toString(36), title: "Hội thoại mới", created: now, updated: now, messages: [] };
    Object.keys(meta || {}).forEach(function (k) { this.cur[k] = meta[k]; }, this);
    return this.cur;
  };
  Store.prototype.open = function (id) {
    try { this.cur = JSON.parse(fs.readFileSync(path.join(this.dir, id + ".json"), "utf8")); } catch (e) { this.cur = null; }
    return this.cur;
  };
  Store.prototype.add = function (role, text) {
    if (!this.cur) this.create();
    this.cur.messages.push({ role: role, text: String(text), t: Date.now() });
    if (role === "user" && this.cur.title === "Hội thoại mới") this.cur.title = String(text).replace(/\s+/g, " ").slice(0, 48);
    this.cur.updated = Date.now();
    this.saveSoon();
  };
  Store.prototype.saveSoon = function () {
    var self = this; clearTimeout(this._t);
    this._t = setTimeout(function () { self.save(); }, 250);
  };
  Store.prototype.save = function () {
    if (!this.cur || !this.cur.messages.length) return;
    var f = path.join(this.dir, this.cur.id + ".json");
    try {
      fs.writeFileSync(f, JSON.stringify(this.cur, null, 1), "utf8");
      if (this._idx) {                                   // keep the index in step with our own write (no re-parse)
        var m = meta(this.cur); try { m._m = fs.statSync(f).mtimeMs; } catch (e) {}
        this._idx[this.cur.id] = m;
        try { this._dirM = fs.statSync(this.dir).mtimeMs; } catch (e) {}
      }
    } catch (e) {}
  };
  Store.prototype.remove = function (id) {
    try { fs.unlinkSync(path.join(this.dir, id + ".json")); } catch (e) {}
    if (this._idx) { delete this._idx[id]; try { this._dirM = fs.statSync(this.dir).mtimeMs; } catch (e) {} }
    if (this.cur && this.cur.id === id) this.cur = null;
  };
  Store.prototype.rename = function (title) { if (this.cur) { this.cur.title = title; this.save(); } };
  /* messages to replay into a fresh LLM session (text only; tool internals are not replayed) */
  Store.prototype.replay = function (maxMsgs) {
    if (!this.cur) return [];
    var ms = this.cur.messages.filter(function (m) { return m.role === "user" || m.role === "ai" || m.role === "ask"; })
      .map(function (m) { return { role: m.role === "user" ? "user" : "ai", text: m.role === "ask" ? "(tôi đã hỏi) " + m.text : m.text }; });
    return ms.slice(-(maxMsgs || 40));
  };

  window.JESChats = { Store: Store };
})();
