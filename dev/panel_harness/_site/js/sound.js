/* Justin EDIT Suite — Âm thanh: soundtrack (phân tích đoạn nhạc, fit độ dài, ducking) + SFX tự đặt + marker đoạn nhạc.
   Chạy trên sequence đang mở (dựng bằng tool hay dựng tay đều được). Engine: nxcore.sfxlib / nxcore.music / nxcore.sounddesign. */
(function () {
  "use strict";
  var fs = require("fs"), path = require("path");
  var NX = window.NX, $ = function (id) { return document.getElementById(id); };
  var DEF_SFX = "D:/Tai nguyen lam viec (2)/Tài Nguyên làm viêcc/20. Sound FX";
  var LABEL_VI = { intro: "Intro", verse: "Verse", build: "Build", drop: "Drop", "break": "Break", outro: "Outro" };
  var busy = false, last = null;   // last design result

  function S() { return NX.S; }
  function dir() { var d = path.join(NX.cfgDir, "sound"); try { fs.mkdirSync(d, { recursive: true }); } catch (e) {} return d; }
  function sfxIndexPath() { return path.join(dir(), "sfx_index.json"); }
  function musicIndexPath() { return path.join(dir(), "music_index.json"); }
  function readJSON(p) { try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) { return null; } }
  function say(t, cls) { var el = $("sndStatus"); if (el) { el.textContent = t; el.className = "hint " + (cls || ""); } NX.log(t); }
  function hash(s) { var h = 0; s = String(s); for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return (h >>> 0).toString(36); }
  function host(fn, args) {
    var expr = fn + "(" + (args || []).map(function (a) { return JSON.stringify(a); }).join(",") + ")";
    return NX.evalHost(expr).then(function (r) {
      var j; try { j = JSON.parse(r); } catch (e) { throw new Error(fn + ": " + r); }
      if (j && j.ok === false) throw new Error(j.error);
      return j;
    });
  }
  function tracksFrom(s) { return String(s || "").split(/[,; ]+/).filter(Boolean).map(function (x) { return Math.max(0, parseInt(x, 10) - 1); }); }
  function hhmm() { var d = new Date(); return ("0" + d.getHours()).slice(-2) + ("0" + d.getMinutes()).slice(-2); }

  // ------------------------------------------------------------ settings <-> UI
  // sndDuckDb "preset" (default) = the preset's bgm_ducking_db; a number = the editor's override
  var FIELDS = { sfxRoots: DEF_SFX, musicRoot: "", sndDensity: "ask", sndStyle: "auto", sndMusicTrack: "5", sndSfxTracks: "3,4",
                 sndAmbTrack: "7", sndMusicDb: "-4", sndDuckDb: "preset" };
  var LAYERS = ["whoosh", "riser", "hit", "pop", "ambience"];
  function setVal(el, v) {          // a <select> gets a saved value that is not in its list (custom dB) as an extra option
    v = String(v);
    if (el.tagName === "SELECT" && !Array.prototype.some.call(el.options, function (o) { return o.value === v; })) {
      var o = document.createElement("option"); o.value = v; o.textContent = v + (/^-?\d/.test(v) ? " dB" : ""); el.appendChild(o);
    }
    el.value = v;
  }
  function duckDb() {               // explicit pick > preset's bgm_ducking_db > plan's > −22 (only when nothing gives a value)
    var v = S().sndDuckDb;
    if (v !== undefined && v !== null && v !== "" && v !== "preset" && !isNaN(+v)) return +v;
    var pd = NX.presetData ? NX.presetData(S().preset).bgm_ducking_db : undefined;
    if (typeof pd === "number") return pd;
    var pl = NX.getPlan && NX.getPlan();
    if (pl && typeof pl.bgm_ducking_db === "number") return pl.bgm_ducking_db;
    return -22;
  }
  function duckHint() {
    var el = $("sndDuckDb"); if (!el) return;
    var o = el.querySelector('option[value="preset"]'), pd = NX.presetData ? NX.presetData(S().preset).bgm_ducking_db : undefined;
    if (o) o.textContent = "Theo preset" + (typeof pd === "number" ? " (" + pd + " dB)" : "");
  }
  function loadUI() {
    Object.keys(FIELDS).forEach(function (k) { if ($(k)) setVal($(k), S()[k] !== undefined && S()[k] !== "" ? S()[k] : FIELDS[k]); });
    duckHint();
    var L = S().sndLayers || ["whoosh", "riser", "hit", "pop"];
    LAYERS.forEach(function (l) { if ($("L_" + l)) $("L_" + l).checked = L.indexOf(l) >= 0; });
    $("sndDuck").checked = S().sndDuck !== false; $("sndFit").checked = S().sndFit !== false;
    refreshSfxInfo(); refreshMusicList();
  }
  function saveUI() {
    Object.keys(FIELDS).forEach(function (k) { if ($(k)) S()[k] = $(k).value; });
    S().sndLayers = LAYERS.filter(function (l) { return $("L_" + l) && $("L_" + l).checked; });
    S().sndDuck = $("sndDuck").checked; S().sndFit = $("sndFit").checked;
    NX.save();
  }

  // ------------------------------------------------------------ libraries
  function refreshSfxInfo() {
    var j = readJSON(sfxIndexPath());
    $("sfxInfo").textContent = j ? ("SFX: " + j.count + " file · " + Object.keys(j.roles).sort(function (a, b) { return j.roles[b] - j.roles[a]; })
      .map(function (k) { return k + " " + j.roles[k]; }).join(" · ")) : "Chưa quét thư viện SFX.";
  }
  function scanSfx() {
    saveUI();
    var roots = String(S().sfxRoots || "").split(";").map(function (x) { return x.trim(); }).filter(Boolean);
    if (!roots.length) return Promise.reject(new Error("chưa nhập thư mục SFX"));
    return NX.run(["-m", "nxcore.sfxlib"].concat(roots).concat(["--out", sfxIndexPath()]), "Quét thư viện SFX").then(refreshSfxInfo);
  }
  function refreshMusicList() {
    var idx = readJSON(musicIndexPath()) || {}, sel = $("musicSel"), cur = S().sndMusic || "";
    var opts = ['<option value="">(không thêm nhạc mới)</option>', '<option value="__timeline__">♪ Dùng nhạc đang có trên timeline</option>'];
    Object.keys(idx).sort().forEach(function (f) {
      var a = idx[f];
      opts.push('<option value="' + NX.esc(f) + '">' + NX.esc(a.name) + " · " + a.bpm + " BPM · " + a.mood + " · " + Math.round(a.duration) + "s</option>");
    });
    if (cur && cur !== "__timeline__" && !idx[cur]) opts.push('<option value="' + NX.esc(cur) + '">' + NX.esc(path.basename(cur)) + "</option>");
    sel.innerHTML = opts.join(""); sel.value = cur;
    showMusic();
  }
  function scanMusic() {
    saveUI();
    if (!S().musicRoot) return Promise.reject(new Error("chưa nhập thư mục nhạc"));
    return NX.run(["-m", "nxcore.music", "index", S().musicRoot, "--out", musicIndexPath()], "Quét thư viện nhạc").then(refreshMusicList);
  }
  function pickMusicFile() {
    var r = window.cep.fs.showOpenDialogEx(false, false, "Chọn file nhạc", S().musicRoot || "", ["mp3", "wav", "m4a", "aif", "aiff", "flac"]);
    if (r && r.data && r.data.length) { S().sndMusic = r.data[0].replace(/\\/g, "/"); NX.save(); refreshMusicList(); }
  }
  function analysisPath(file) { return path.join(dir(), "music", hash(file) + ".music.json"); }
  function analyzeMusic(file) {
    var out = analysisPath(file);
    try { fs.mkdirSync(path.dirname(out), { recursive: true }); } catch (e) {}
    var have = readJSON(out);
    if (have && have.beats) return Promise.resolve(have);
    return NX.run(["-m", "nxcore.music", "analyze", file, "--out", out], "Phân tích nhạc " + path.basename(file)).then(function () { return readJSON(out); });
  }
  function showMusic() {
    var f = S().sndMusic, box = $("musicInfo");
    if (!f || f === "__timeline__") { box.innerHTML = ""; return; }
    var a = readJSON(analysisPath(f)) || (readJSON(musicIndexPath()) || {})[f];
    if (!a) { box.innerHTML = '<span class="muted">Chưa phân tích — sẽ phân tích khi Xem trước.</span>'; return; }
    box.innerHTML = "<b>" + a.bpm + " BPM</b> · " + a.mood + " · " + Math.round(a.duration) + "s<br>" + a.sections.map(function (s) {
      return '<span class="chip ' + s.label + '">' + (LABEL_VI[s.label] || s.label) + " " + s.start.toFixed(1) + "–" + s.end.toFixed(1) + "s</span>";
    }).join(" ");
  }

  // ------------------------------------------------------------ timeline + design
  function timelineMusic(tl, minTrack) {
    var best = null;
    (tl.audio || []).forEach(function (tr) {
      if (tr.track < minTrack) return;
      tr.clips.forEach(function (c) {
        if (!/\.(mp3|wav|m4a|aif|aiff|flac|aac|ogg)$/i.test(c.media || "")) return;
        if (!best || (c.end - c.start) > (best.end - best.start)) best = c;
      });
    });
    return best;
  }
  function suggestDensity(tl, music) {
    var v = (tl.video || []).filter(function (t) { return t.track === 0; })[0], n = v ? v.clips.length : 0;
    var avg = n ? (tl.dur || 1) / n : 3, bpm = music ? music.bpm : 110;
    if (avg < 1.6 || bpm >= 128) return "heavy";
    if (avg > 3.2 && bpm < 100) return "light";
    return "medium";
  }
  function chooseDensity(tl, music) {
    var d = $("sndDensity").value;
    if (d !== "ask") return Promise.resolve(d);
    var rule = window.JESStyle && window.JESStyle.param("sfx_density");     // 📒 Sổ luật: the editor already decided
    if (rule) { NX.log("• Mức SFX theo Sổ luật: " + rule, "#8b9099"); return Promise.resolve(rule); }
    var sug = suggestDensity(tl, music), v = (tl.video || [])[0], n = v ? v.clips.length : 0;
    var label = { light: "Nhẹ — chỉ đổi đoạn nhạc + drop", medium: "Vừa — thêm whoosh ở một phần điểm cắt, pop khi hiện chữ", heavy: "Dày — SFX mọi điểm cắt + accent (kiểu TikTok)" };
    var opts = ["light", "medium", "heavy"].map(function (k) { return label[k] + (k === sug ? "  ← gợi ý" : ""); });
    return NX.ask("Mức SFX cho sequence này? (" + n + " shot, trung bình " + (n ? (tl.dur / n).toFixed(1) : "?") + "s/shot" +
                  (music ? ", nhạc " + music.bpm + " BPM " + music.mood : "") + ")", opts).then(function (a) {
      if (!a) throw new Error("đã hủy");
      return a.indexOf("Nhẹ") === 0 ? "light" : a.indexOf("Vừa") === 0 ? "medium" : "heavy";
    });
  }
  function chooseStyle(music) {
    var s = $("sndStyle").value;
    if (s !== "auto") return s;
    if (!music) return "clean";
    return /fast/.test(music.mood) ? "tech" : /slow/.test(music.mood) ? "cinematic" : "clean";
  }

  function design(opts) {
    opts = opts || {};
    saveUI();
    if (!fs.existsSync(sfxIndexPath())) return Promise.reject(new Error("chưa quét thư viện SFX (bấm 'Quét')"));
    var ctx = {};
    return host("jesSequence", [3000]).then(function (tl) {
      ctx.tl = tl; ctx.seq = seqKey(tl);
      fs.writeFileSync(path.join(dir(), "timeline.json"), JSON.stringify(tl), "utf8");
      var mTrack = Math.max(0, parseInt(S().sndMusicTrack, 10) - 1) || 0;
      var choice = opts.music !== undefined ? opts.music : S().sndMusic;
      if (choice === "__timeline__") {
        var c = timelineMusic(tl, Math.min(mTrack, 2));
        if (!c) throw new Error("không thấy file nhạc (mp3/wav) trên timeline");
        ctx.musicOnTimeline = true; ctx.musicStart = c.start; ctx.musicIn = c["in"];
        return analyzeMusic(c.media);
      }
      if (choice) { ctx.musicStart = 0; ctx.musicIn = 0; return analyzeMusic(choice); }
      return null;
    }).then(function (music) {
      ctx.music = music;
      return opts.density ? opts.density : chooseDensity(ctx.tl, music);
    }).then(function (density) {
      ctx.density = density; if (window.JESLearn) window.JESLearn.bump("sfx", density); ctx.style = opts.style || chooseStyle(ctx.music);
      var out = path.join(dir(), "sound_design.json");
      var args = ["-m", "nxcore.sounddesign", "--timeline", path.join(dir(), "timeline.json"), "--sfx", sfxIndexPath(),
                  "--density", density, "--style", ctx.style, "--layers", (opts.layers || S().sndLayers || []).join(","), "--out", out];
      if (ctx.music) args = args.concat(["--music", analysisPath(ctx.music.file), "--music-start", String(ctx.musicStart || 0), "--music-in", String(ctx.musicIn || 0)]);
      if (!S().sndDuck || ctx.musicOnTimeline) args.push("--no-duck");
      if (!S().sndFit) args.push("--no-fit");
      var gdb = window.JESStyle && window.JESStyle.param("sfx_gain_db");
      if (gdb) args.push("--gain-db", String(gdb));
      return NX.run(args, "Sound design").then(function () {
        last = readJSON(out); last._ctx = { musicOnTimeline: !!ctx.musicOnTimeline, seq: ctx.seq, seqName: ctx.tl && ctx.tl.name };
        renderPreview(last); showMusic();
        return last;
      });
    });
  }

  function renderPreview(R) {
    var st = Object.keys(R.stats || {}).map(function (k) { return k + " " + R.stats[k]; }).join(" · ");
    var rows = (R.sfx || []).concat(R.ambience || []).sort(function (a, b) { return a.t - b.t; }).map(function (e) {
      return "<tr><td>" + (e.peak !== undefined ? e.peak : e.t).toFixed(2) + "s</td><td>" + e.role + "</td><td>" + (e.gain_db > 0 ? "+" : "") + e.gain_db +
             " dB</td><td>" + NX.esc(e.name) + '</td><td class="flag">' + NX.esc(e.reason) + "</td></tr>";
    }).join("");
    $("sndPreview").innerHTML = '<div class="hint">Mức <b>' + R.density + "</b> · style <b>" + R.style + "</b> · " + R.cuts + " điểm cắt · " + st +
      (R.music ? " · nhạc " + R.music.bpm + " BPM" + (R.music.dur ? ", cắt còn " + R.music.dur.toFixed(1) + "s + fade " + R.music.fade_out + "s" : "") : "") +
      " · " + (R.markers || []).length + " marker</div>" +
      ((R.notes || []).length ? '<div class="hint" style="color:var(--warn)">• ' + R.notes.map(NX.esc).join("<br>• ") + "</div>" : "") +
      '<div style="max-height:34vh;overflow:auto"><table><tr><th>Peak</th><th>Loại</th><th>Gain</th><th>File</th><th>Lý do</th></tr>' + rows + "</table></div>";
  }

  function assignTracks(events, tracks) {
    var ends = tracks.map(function () { return -1; }), out = tracks.map(function () { return []; });
    events.forEach(function (e) {
      var k = 0, bestGap = -1e9;
      for (var i = 0; i < tracks.length; i++) { var gap = e.t - ends[i]; if (gap >= 0.02) { k = i; bestGap = gap; break; } if (gap > bestGap) { bestGap = gap; k = i; } }
      if (bestGap < 0.02) { var trimTo = ends[k] - e.t; e.t = +(e.t + trimTo + 0.02).toFixed(3); e["in"] = +(e["in"] + trimTo + 0.02).toFixed(3); e.dur = +(e.dur - trimTo - 0.02).toFixed(3); }
      if (e.dur > 0.1) { out[k].push(e); ends[k] = e.t + e.dur; }
    });
    return out;
  }

  function seqKey(tl) { return tl ? String(tl.id || "") + "|" + String(tl.name || "") : ""; }
  function place(R) {
    R = R || last;
    if (!R) return Promise.reject(new Error("chưa có bản xem trước"));
    var sfxTracks = tracksFrom(S().sndSfxTracks), ambTrack = tracksFrom(S().sndAmbTrack)[0], mTrack = tracksFrom(S().sndMusicTrack)[0];
    var summary = [];
    // a preview is timed on ONE sequence (its cuts / length): never lay it onto another one
    return host("jesSequence", [1]).then(function (cur) {
      if (R._ctx && R._ctx.seq && seqKey(cur) !== R._ctx.seq)
        throw new Error("bản xem trước làm cho sequence " + (R._ctx.seqName || "khác") + " — đang mở " + cur.name + ": bấm Xem trước lại");
      return host("jesCloneSequence", ["_bak_" + hhmm()]);
    }).then(function (b) {
      summary.push("backup " + (b.backup || b.name || "ok"));
      if (R.music && !R._ctx.musicOnTimeline) {
        var m = R.music;
        return host("jesPlaceAudio", [JSON.stringify({ track: mTrack, bin: "JES_MUSIC", items: [{
          path: m.path, t: m.start || 0, "in": m["in"] || 0, dur: m.dur || 0, gain_db: Number(S().sndMusicDb || -4),
          fade_in: 0, fade_out: m.fade_out || 0, duck: m.duck || [], duck_db: duckDb(), name: path.basename(m.path) }] })])
          .then(function (r) { summary.push("nhạc → " + r.track); });
      }
    }).then(function () {
      if (!(R.markers || []).length) return;
      return host("jesAddMarkers", [JSON.stringify(R.markers), "♪ "]).then(function (r) { summary.push(r.added + " marker đoạn nhạc"); });
    }).then(function () {
      var groups = assignTracks((R.sfx || []).slice().sort(function (a, b) { return a.t - b.t; }), sfxTracks.length ? sfxTracks : [2]);
      var chain = Promise.resolve();
      groups.forEach(function (items, i) {
        if (!items.length) return;
        var t = (sfxTracks.length ? sfxTracks : [2])[i];
        chain = chain.then(function () {
          return host("jesPlaceAudio", [JSON.stringify({ track: t, bin: "JES_SFX", items: items })]).then(function (r) {
            summary.push(r.placed + "/" + r.total + " SFX → " + r.track + (r.errors && r.errors.length ? " (lỗi: " + r.errors.join("; ") + ")" : ""));
          });
        });
      });
      return chain;
    }).then(function () {
      if (!(R.ambience || []).length) return;
      return host("jesPlaceAudio", [JSON.stringify({ track: ambTrack, bin: "JES_SFX", items: R.ambience.map(function (a) {
        return Object.assign({}, a, { fade_in: 0.8, fade_out: 0.8 }); }) })]).then(function (r) { summary.push(r.placed + " ambience → " + r.track); });
    }).then(function () {
      say("✔ Đã đặt: " + summary.join(" · "), "ok");
      return summary;
    });
  }

  var BTNS = ["bSndPreview", "bSndPlace", "bSfxScan", "bMusicScan"];
  function buttons() { var off = busy || (NX.isBusy && NX.isBusy()); BTNS.forEach(function (id) { if ($(id)) $(id).disabled = !!off; }); }
  // the panel-wide lock: no sound job while a pipeline step / timeline edit / text or voice job runs (and the other way round)
  function guard(fn) {
    return function () {
      if (busy) return;
      if (NX.tryLock && !NX.tryLock("sound", "Âm thanh")) { say("• Đang chạy: " + NX.busyLabel() + " — chờ xong", "warn"); return; }
      busy = true; buttons();
      Promise.resolve().then(fn).catch(function (e) { say("✖ " + (e && e.message || e), "bad"); })
        .then(function () { busy = false; if (NX.unlock) NX.unlock("sound"); buttons(); });
    };
  }

  function init() {
    if (!$("snd")) return;
    loadUI();
    ["sfxRoots", "musicRoot", "sndDensity", "sndStyle", "sndMusicTrack", "sndSfxTracks", "sndAmbTrack", "sndMusicDb", "sndDuckDb", "sndDuck", "sndFit"]
      .concat(LAYERS.map(function (l) { return "L_" + l; })).forEach(function (id) { if ($(id)) $(id).onchange = saveUI; });
    $("musicSel").onchange = function () { S().sndMusic = $("musicSel").value; NX.save(); showMusic(); };
    $("bSfxScan").onclick = guard(scanSfx);
    $("bMusicScan").onclick = guard(scanMusic);
    $("bMusicPick").onclick = pickMusicFile;
    $("bSndPreview").onclick = guard(function () { return design().then(function (R) { say("Xem trước: " + R.sfx.length + " SFX, " + R.ambience.length + " ambience, " + R.markers.length + " marker — bấm 'Đặt vào sequence' để áp."); }); });
    $("bSndPlace").onclick = guard(function () {
      // the last preview only if it was made for the sequence open now — otherwise a fresh preview first
      return host("jesSequence", [1]).then(function (cur) {
        return last && last._ctx && last._ctx.seq === seqKey(cur) ? last : design();
      }).then(place);
    });
    document.addEventListener("jes:busy", buttons);
    document.addEventListener("jes:preset", duckHint);
  }

  window.JESSound = { design: design, place: place, scanSfx: scanSfx, scanMusic: scanMusic, analyzeMusic: analyzeMusic,
                      last: function () { return last; }, suggestDensity: suggestDensity };
  init();
})();
