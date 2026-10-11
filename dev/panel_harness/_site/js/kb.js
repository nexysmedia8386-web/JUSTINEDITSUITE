/* Justin EDIT Suite — Premiere editing knowledge base for AI Assist.
   Built-in: <extension>/knowledge/*.json  ({sections:[{id,title,level,tags,body,tools,sources}]})
   Editor's own notes: %APPDATA%/JustinEditSuite/knowledge/notes.json (kb_note writes here; survives panel updates). */
(function () {
  "use strict";
  var fs = require("fs"), path = require("path");

  function fold(s) {  // lowercase, strip Vietnamese diacritics -> accent-insensitive matching
    return String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/đ/g, "d");
  }
  function words(s) { return fold(s).split(/[^a-z0-9:+.\-]+/).filter(function (w) { return w.length > 1; }); }

  function KB(builtinDir, userDir) {
    this.builtinDir = builtinDir; this.userDir = userDir; this.sections = []; this.load();
  }
  KB.prototype.userFile = function () { return path.join(this.userDir, "notes.json"); };
  KB.prototype.load = function () {
    var out = [], self = this;
    function readDir(dir, origin) {
      try {
        fs.readdirSync(dir).filter(function (f) { return /\.json$/i.test(f); }).sort().forEach(function (f) {
          try {
            var j = JSON.parse(fs.readFileSync(path.join(dir, f), "utf8"));
            (j.sections || []).forEach(function (s) { s.origin = origin; out.push(s); });
          } catch (e) {}
        });
      } catch (e) {}
    }
    readDir(this.builtinDir, "builtin");
    readDir(this.userDir, "user");
    out.forEach(function (s) {
      s._t = fold(s.title); s._g = fold((s.tags || []).join(" ")); s._b = fold(s.body + " " + (s.tools || ""));
    });
    this.sections = out;
    return out.length;
  };
  /* one line per section for the system prompt */
  KB.prototype.index = function () {
    return this.sections.map(function (s) { return "- " + s.id + ": " + s.title; }).join("\n");
  };
  KB.prototype.search = function (query, limit) {
    var q = words(query), ql = fold(query);
    if (!q.length) return [];
    var scored = this.sections.map(function (s) {
      var sc = 0;
      if (s.id === query) sc += 100;
      q.forEach(function (w) {
        if (s._t.indexOf(w) >= 0) sc += 4;
        if (s._g.indexOf(w) >= 0) sc += 3;
        var m = s._b.split(w).length - 1; sc += Math.min(m, 4);
      });
      (s.tags || []).forEach(function (t) { var ft = fold(t); if (ft.length > 2 && ql.indexOf(ft) >= 0) sc += 5; });  // multi-word tag phrase
      return { s: s, sc: sc };
    }).filter(function (x) { return x.sc > 0; }).sort(function (a, b) { return b.sc - a.sc; });
    var top = scored.length ? scored[0].sc : 0;
    return scored.filter(function (x) { return x.sc >= top * 0.35; }).slice(0, limit || 3).map(function (x) {
      return { id: x.s.id, title: x.s.title, level: x.s.level, body: x.s.body, tools: x.s.tools || "", sources: x.s.sources || [], origin: x.s.origin };
    });
  };
  KB.prototype.get = function (id) {
    for (var i = 0; i < this.sections.length; i++) if (this.sections[i].id === id) return this.search(id, 1)[0];
    return null;
  };
  /* append/replace an editor note (house style, client rules, learned fixes) */
  KB.prototype.note = function (title, body, tags) {
    var f = this.userFile(), j = { sections: [] };
    try { fs.mkdirSync(this.userDir, { recursive: true }); } catch (e) {}
    try { j = JSON.parse(fs.readFileSync(f, "utf8")); } catch (e) {}
    var id = "note-" + fold(title).replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);
    j.sections = (j.sections || []).filter(function (s) { return s.id !== id; });
    j.sections.push({ id: id, title: title, level: "house", tags: tags || [], body: body, tools: "", sources: [], updated: new Date().toISOString() });
    fs.writeFileSync(f, JSON.stringify(j, null, 1), "utf8");
    this.load();
    return id;
  };

  window.JESKB = { KB: KB, fold: fold };
})();
