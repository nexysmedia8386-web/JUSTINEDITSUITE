// Justin EDIT Suite — ExtendScript host (Premiere Pro). All functions take/return JSON strings.
// Agent toolkit: project/sequence introspection + editing via DOM, and QE DOM where the DOM has no API.

// ExtendScript (ES3) minimal polyfill (stringify + parse)
if (typeof JSON !== "object" || !JSON) { JSON = {}; }
(function () {
    function q(s) {
        return '"' + String(s).replace(/[\\"\u0000-\u001f\u2028\u2029]/g, function (c) {
            var m = { "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r", '"': '\\"', "\\": "\\\\" };
            return m[c] || "\\u" + ("0000" + c.charCodeAt(0).toString(16)).slice(-4);
        }) + '"';
    }
    function str(v) {
        if (v === null || v === undefined) return "null";
        var t = typeof v;
        if (t === "number") return isFinite(v) ? String(v) : "null";
        if (t === "boolean") return String(v);
        if (t === "string") return q(v);
        if (t === "function") return undefined;
        if (Object.prototype.toString.call(v) === "[object Array]") {
            var a = [];
            for (var i = 0; i < v.length; i++) {
                var x = str(v[i]);
                a.push(x === undefined ? "null" : x);
            }
            return "[" + a.join(",") + "]";
        }
        var o = [];
        for (var k in v) {
            if (Object.prototype.hasOwnProperty.call(v, k)) {
                var y = str(v[k]);
                if (y !== undefined) o.push(q(k) + ":" + y);
            }
        }
        return "{" + o.join(",") + "}";
    }
    if (typeof JSON.stringify !== "function") JSON.stringify = function (v) { return str(v); };
    if (typeof JSON.parse !== "function") JSON.parse = function (s) { return eval("(" + s + ")"); };
})();

var TICKS = 254016000000;

function _ok(o) { o = o || {}; o.ok = true; return JSON.stringify(o); }
function _err(e) { return JSON.stringify({ ok: false, error: String(e) }); }
function _seq() { var s = app.project.activeSequence; if (!s) throw "Chưa mở sequence nào"; return s; }
function _time(sec) { var t = new Time(); t.seconds = Number(sec); return t; }
function _r(x) { var n = Number(x); return isNaN(n) ? 0 : Math.round(n * 1000) / 1000; }
function _tracks(seq, type) { return type === "audio" ? seq.audioTracks : seq.videoTracks; }
function _clip(seq, type, track, index) {
    var tracks = _tracks(seq, type);
    var tIdx = Number(track) || 0;
    if (tIdx < 0 || tIdx >= tracks.numTracks) {
        throw "Không có track " + type + " " + tIdx + " (tổng: " + tracks.numTracks + ")";
    }
    var tr = tracks[tIdx];
    var cIdx = Number(index) || 0;
    if (cIdx < 0 || cIdx >= tr.clips.numItems) {
        throw "Không có clip " + cIdx + " trên " + type + " track " + tIdx + " (tổng clip: " + tr.clips.numItems + ")";
    }
    return tr.clips[cIdx];
}
function _tc(seq, sec) {
    var t = _time(sec);
    return t.getFormatted(seq.getSettings().videoFrameRate, seq.videoDisplayFormat);
}
function _qeSeq() { app.enableQE(); var q = qe.project.getActiveSequence(); if (!q) throw "QE: không có sequence"; return q; }
function _qeClip(type, track, domClip) {
    // QE item indexes include empty gaps -> match by start time with precision tolerance
    var qs = _qeSeq(), qt = type === "audio" ? qs.getAudioTrackAt(track) : qs.getVideoTrackAt(track);
    if (!qt) throw "QE: Không tìm thấy track " + type + " " + track;
    var want = domClip.start.seconds;
    for (var i = 0; i < qt.numItems; i++) {
        var it = qt.getItemAt(i);
        if (it && it.start) {
            var itSec = Number(it.start.secs !== undefined ? it.start.secs : it.start);
            if (!isNaN(itSec)) {
                if (it.type !== "Empty" && Math.abs(itSec - want) < 0.05) return it;
                if (itSec > want + 0.1) break; // early exit: QE track items are chronologically ordered
            }
        }
    }
    throw "QE: Không tìm thấy clip tại " + _r(want) + "s trên " + type + " track " + track;
}

// High-performance single-pass media path indexing
var _mediaCache = null;
function _normPath(p) { return p ? String(p).replace(/\\/g, "/").toLowerCase() : ""; }

function _buildMediaCache() {
    _mediaCache = {};
    if (!app.project || !app.project.rootItem) return _mediaCache;
    var stack = [app.project.rootItem];
    while (stack.length > 0) {
        var bin = stack.pop();
        if (!bin || !bin.children) continue;
        var num = bin.children.numItems;
        for (var i = 0; i < num; i++) {
            var it = bin.children[i];
            if (it.type === ProjectItemType.BIN) {
                stack.push(it);
            } else {
                try {
                    var mp = it.getMediaPath();
                    if (mp) _mediaCache[_normPath(mp)] = it;
                } catch (e) {}
            }
        }
    }
    return _mediaCache;
}

function _itemByPath(path) {
    if (!path) return null;
    var want = _normPath(path);
    if (_mediaCache && _mediaCache[want]) {
        try {
            if (_normPath(_mediaCache[want].getMediaPath()) === want) return _mediaCache[want];
        } catch (e) { delete _mediaCache[want]; }
    }
    if (!_mediaCache) _buildMediaCache();
    if (_mediaCache[want]) return _mediaCache[want];
    // Re-index once in case item was imported recently
    _buildMediaCache();
    return _mediaCache[want] || null;
}

function _resolveBinPath(binPath, createIfMissing) {
    if (!binPath) return app.project.rootItem;
    var parts = String(binPath).replace(/\\/g, "/").split("/");
    var curBin = app.project.rootItem;
    for (var p = 0; p < parts.length; p++) {
        var partName = parts[p].replace(/^\s+|\s+$/g, "");
        if (!partName) continue;
        var found = null;
        if (curBin.children) {
            for (var i = 0; i < curBin.children.numItems; i++) {
                var it = curBin.children[i];
                if (it.type === ProjectItemType.BIN && it.name === partName) {
                    found = it;
                    break;
                }
            }
        }
        if (!found) {
            if (createIfMissing === false) return null;
            found = curBin.createBin(partName);
        }
        curBin = found;
    }
    return curBin;
}

function _importOne(path, binName) {
    var it = _itemByPath(path);
    if (it) return it;
    var bin = _resolveBinPath(binName || "JES_AI", true);
    app.project.importFiles([path], true, bin, false);
    _mediaCache = null; // invalidate cache
    it = _itemByPath(path);
    if (!it) throw "Import thất bại: " + path;
    return it;
}

// ---------------------------------------------------------------- legacy helpers (panel pipeline)
function nxPing() { return JSON.stringify({ v: app.version, project: app.project.name, path: app.project.path }); }
function nxSeqNames() {
    var out = [];
    for (var i = 0; i < app.project.sequences.numSequences; i++) out.push(app.project.sequences[i].name);
    return JSON.stringify(out);
}
function nxRunScript(code) { try { return eval(code); } catch (e) { return JSON.stringify({ error: String(e) }); } }
function nxOpenInSource(path, tin, tout) {
    try {
        var it = _importOne(path);
        it.setInPoint(Number(tin), 4); it.setOutPoint(Number(tout), 4);
        app.sourceMonitor.openProjectItem(it);
        return JSON.stringify({ ok: true, name: it.name });
    } catch (e) { return JSON.stringify({ error: String(e) }); }
}

// ---------------------------------------------------------------- agent: read
function jesProject() {
    try {
        var seqs = [];
        for (var i = 0; i < app.project.sequences.numSequences; i++) {
            var s = app.project.sequences[i];
            seqs.push({ name: s.name, id: s.sequenceID, dur: _r(Number(s.end) / TICKS) });
        }
        var bins = [];
        for (var j = 0; j < app.project.rootItem.children.numItems; j++) {
            var c = app.project.rootItem.children[j];
            bins.push({ name: c.name, bin: c.type === ProjectItemType.BIN, items: c.children ? c.children.numItems : 0 });
        }
        var a = app.project.activeSequence;
        return _ok({ project: app.project.name, path: app.project.path, version: app.version,
                     active: a ? a.name : null, sequences: seqs.slice(-60), sequenceCount: seqs.length, root: bins.slice(0, 80) });
    } catch (e) { return _err(e); }
}

function _clipRich(cl, o) {
    try { if (cl.getSpeed) o.speed = _r(cl.getSpeed()); } catch (e) {}
    try {
        for (var i = 0; i < cl.components.numItems; i++) {
            var cp = cl.components[i];
            if (cp.matchName !== "AE.ADBE Motion") continue;
            var rp = null;
            for (var p = 0; p < cp.properties.numItems; p++) {
                if (/rotat/i.test(String(cp.properties[p].displayName))) { rp = cp.properties[p]; break; }
            }
            if (!rp && cp.properties.numItems > 4) rp = cp.properties[4];
            if (rp) { var v = rp.getValue(); if (typeof v === "number") o.rotation = _r(v); }
            break;
        }
    } catch (e2) {}
    return o;
}

function jesSequence(maxClips, rich) {
    try {
        var s = _seq(), st = s.getSettings(), lim = Number(maxClips) || 150, n = 0;
        var out = { name: s.name, id: String(s.sequenceID || ""), fps: _r(1 / st.videoFrameRate.seconds), w: st.videoFrameWidth, h: st.videoFrameHeight,
                    dur: _r(Number(s.end) / TICKS), playhead: _r(s.getPlayerPosition().seconds),
                    inPoint: _r(Number(s.getInPoint())), outPoint: _r(Number(s.getOutPoint())), video: [], audio: [], markers: [] };
        var kinds = [["video", s.videoTracks], ["audio", s.audioTracks]];
        for (var k = 0; k < 2; k++) {
            var tracks = kinds[k][1];
            for (var t = 0; t < tracks.numTracks; t++) {
                var tr = tracks[t], clips = [];
                for (var c = 0; c < tr.clips.numItems && n < lim; c++, n++) {
                    var cl = tr.clips[c], mp = "";
                    try { mp = cl.projectItem ? cl.projectItem.getMediaPath() : ""; } catch (e) {}
                    var isSel = false;
                    try { isSel = !!cl.isSelected(); } catch (eSel) {}
                    var co = { i: c, name: cl.name, start: _r(cl.start.seconds), end: _r(cl.end.seconds),
                               "in": _r(cl.inPoint.seconds), out: _r(cl.outPoint.seconds), media: mp,
                               enabled: cl.disabled !== true, selected: isSel };
                    if (rich && k === 0) _clipRich(cl, co);
                    clips.push(co);
                }
                var isMut = false;
                try { if (tr.isMuted) isMut = tr.isMuted(); } catch (eM) {}
                var isLok = false;
                try { if (tr.isLocked) isLok = tr.isLocked(); } catch (eL) {}
                var isTar = false;
                try { if (tr.isTargeted) isTar = tr.isTargeted(); } catch (eT) {}
                out[kinds[k][0]].push({
                    track: t,
                    name: tr.name || ((k === 0 ? "V" : "A") + (t + 1)),
                    muted: isMut,
                    locked: isLok,
                    targeted: isTar,
                    clips: clips,
                    count: tr.clips.numItems
                });
            }
        }
        var m = s.markers.getFirstMarker(), mi = 0;
        while (m && mi < 200) {
            out.markers.push({ t: _r(m.start.seconds), end: _r(m.end.seconds), name: m.name, comment: m.comments, type: String(m.type || "") });
            m = s.markers.getNextMarker(m); mi++;
        }
        out.truncated = n >= lim;
        return _ok(out);
    } catch (e) { return _err(e); }
}

function jesClipComponents(type, track, index) {
    try {
        var cl = _clip(_seq(), type, Number(track), Number(index)), comps = [];
        for (var i = 0; i < cl.components.numItems; i++) {
            var cp = cl.components[i], props = [];
            for (var p = 0; p < cp.properties.numItems; p++) {
                var pr = cp.properties[p], v = null;
                try { v = pr.getValue(); if (typeof v === "object" && v !== null && v.length === undefined) v = String(v); } catch (e) {}
                props.push({ name: pr.displayName, value: v });
            }
            comps.push({ name: cp.displayName, props: props });
        }
        return _ok({ clip: cl.name, components: comps });
    } catch (e) { return _err(e); }
}

// ---------------------------------------------------------------- agent: edit
function jesSetPlayhead(sec) {
    try { var s = _seq(); s.setPlayerPosition(String(Math.round(Number(sec) * TICKS))); return _ok({ playhead: Number(sec) }); } catch (e) { return _err(e); }
}
function jesSetInOut(inSec, outSec) {
    try { var s = _seq(); s.setInPoint(Number(inSec)); s.setOutPoint(Number(outSec)); return _ok(); } catch (e) { return _err(e); }
}
function jesAddMarker(sec, name, comment, color, dur) {
    try {
        var s = _seq(), m = s.markers.createMarker(Number(sec));
        m.name = name || ""; m.comments = comment || "";
        if (dur) m.end = Number(sec) + Number(dur);
        if (color !== undefined && color !== null && m.setColorByIndex) m.setColorByIndex(Number(color));
        return _ok({ t: Number(sec) });
    } catch (e) { return _err(e); }
}
function jesRazor(sec, type, tracksCsv) {
    try {
        var s = _seq(), qs = _qeSeq(), tc = _tc(s, sec), done = [];
        var kinds = type === "audio" ? ["audio"] : type === "video" ? ["video"] : ["video", "audio"];
        for (var k = 0; k < kinds.length; k++) {
            var n = _tracks(s, kinds[k]).numTracks;
            for (var t = 0; t < n; t++) {
                if (tracksCsv && ("," + tracksCsv + ",").indexOf("," + t + ",") < 0) continue;
                var qt = kinds[k] === "audio" ? qs.getAudioTrackAt(t) : qs.getVideoTrackAt(t);
                qt.razor(tc); done.push(kinds[k][0].toUpperCase() + (t + 1));
            }
        }
        return _ok({ at: Number(sec), timecode: tc, tracks: done });
    } catch (e) { return _err(e); }
}
function jesRemoveClips(listJson, ripple) {
    try {
        var s = _seq();
        var raw = typeof listJson === "string" ? JSON.parse(listJson) : (listJson || []);
        var seen = {}, list = [];
        for (var idx = 0; idx < raw.length; idx++) {
            var itm = raw[idx];
            var key = (itm.type || "video") + "_" + Number(itm.track) + "_" + Number(itm.index);
            if (!seen[key]) { seen[key] = true; list.push(itm); }
        }
        // Remove from the end so indexes remain valid
        list.sort(function (a, b) {
            if (a.type !== b.type) return a.type < b.type ? -1 : 1;
            if (a.track !== b.track) return Number(a.track) - Number(b.track);
            return Number(b.index) - Number(a.index);
        });
        var removed = 0, errors = [];
        for (var i = 0; i < list.length; i++) {
            try {
                var c = _clip(s, list[i].type, Number(list[i].track), Number(list[i].index));
                c.remove(ripple === true || ripple === "true", true);
                removed++;
            } catch (eClip) {
                errors.push("Track " + list[i].track + " clip " + list[i].index + ": " + String(eClip));
            }
        }
        var res = { removed: removed, total: list.length };
        if (errors.length) res.errors = errors;
        return _ok(res);
    } catch (e) { return _err(e); }
}
function jesMoveClip(type, track, index, deltaSec) {
    try { var c = _clip(_seq(), type, Number(track), Number(index)); c.move(_time(deltaSec)); return _ok({ start: _r(c.start.seconds) }); } catch (e) { return _err(e); }
}
function jesTrimClip(type, track, index, startSec, endSec) {
    try {
        var c = _clip(_seq(), type, Number(track), Number(index));
        if (startSec !== null && startSec !== undefined && startSec !== "") c.start = _time(startSec);
        if (endSec !== null && endSec !== undefined && endSec !== "") c.end = _time(endSec);
        return _ok({ start: _r(c.start.seconds), end: _r(c.end.seconds) });
    } catch (e) { return _err(e); }
}
function jesSetClipEnabled(type, track, index, enabled) {
    try { var c = _clip(_seq(), type, Number(track), Number(index)); c.disabled = !(enabled === true || enabled === "true"); return _ok(); } catch (e) { return _err(e); }
}
function jesSelectClip(type, track, index, on) {
    try { var c = _clip(_seq(), type, Number(track), Number(index)); c.setSelected(on === true || on === "true", true); return _ok(); } catch (e) { return _err(e); }
}
function jesInsertClip(path, sec, vTrack, aTrack, overwrite, inSec, outSec) {
    try {
        var s = _seq(), it = _importOne(path, "JES_AI");
        if (inSec !== undefined && inSec !== null && inSec !== "") it.setInPoint(Number(inSec), 4);
        if (outSec !== undefined && outSec !== null && outSec !== "") it.setOutPoint(Number(outSec), 4);
        var ext = String(path).slice(String(path).lastIndexOf(".")).toLowerCase();
        var isAudioOnly = /^\.(wav|mp3|aac|m4a|aiff|flac|ogg)$/i.test(ext);
        var placedTrack = "";
        var ow = (overwrite === true || overwrite === "true");
        var atSec = Number(sec) || 0;

        if (isAudioOnly || vTrack === -1 || (vTrack === null && aTrack !== null && aTrack !== undefined)) {
            var atIdx = Number(aTrack) || 0;
            if (atIdx >= s.audioTracks.numTracks) _ensureAudioTracks(s, atIdx + 1);
            var at = s.audioTracks[atIdx];
            if (!at) throw "Không tìm thấy audio track A" + (atIdx + 1);
            if (ow) at.overwriteClip(it, atSec); else at.insertClip(it, atSec);
            placedTrack = "A" + (atIdx + 1);
        } else {
            var vtIdx = Number(vTrack) || 0;
            var vt = s.videoTracks[vtIdx];
            if (!vt) throw "Không tìm thấy video track V" + (vtIdx + 1);
            if (ow) vt.overwriteClip(it, atSec); else vt.insertClip(it, atSec);
            placedTrack = "V" + (vtIdx + 1);
        }
        try { it.clearInPoint(4); it.clearOutPoint(4); } catch (e0) {}
        return _ok({ name: it.name, at: atSec, track: placedTrack });
    } catch (e) { return _err(e); }
}
function jesImport(pathsJson, binName) {
    try {
        var paths = JSON.parse(pathsJson), names = [];
        for (var i = 0; i < paths.length; i++) names.push(_importOne(paths[i], binName || "JES_AI").name);
        return _ok({ imported: names });
    } catch (e) { return _err(e); }
}
function jesSetParam(type, track, index, compName, paramName, value) {
    try {
        var c = _clip(_seq(), type, Number(track), Number(index));
        var wantComp = String(compName).toLowerCase();
        var wantParam = String(paramName).toLowerCase();
        for (var i = 0; i < c.components.numItems; i++) {
            var cp = c.components[i];
            var cName = cp.displayName.toLowerCase();
            var mName = cp.matchName ? cp.matchName.toLowerCase() : "";
            if (cName !== wantComp && mName !== wantComp) continue;
            for (var p = 0; p < cp.properties.numItems; p++) {
                var pr = cp.properties[p];
                if (pr.displayName.toLowerCase() !== wantParam) continue;
                var v = value;
                try { v = JSON.parse(value); } catch (e) {}
                if (pr.isTimeVarying && pr.isTimeVarying()) pr.setTimeVarying(false);
                pr.setValue(v, true);
                return _ok({ component: cp.displayName, param: pr.displayName, value: v });
            }
            throw "Không có thông số '" + paramName + "' trong " + cp.displayName;
        }
        throw "Clip chưa có effect '" + compName + "'";
    } catch (e) { return _err(e); }
}
function jesAddKeyframe(type, track, index, compName, paramName, sec, value) {
    try {
        var c = _clip(_seq(), type, Number(track), Number(index));
        var wantComp = String(compName).toLowerCase();
        var wantParam = String(paramName).toLowerCase();
        for (var i = 0; i < c.components.numItems; i++) {
            var cp = c.components[i];
            var cName = cp.displayName.toLowerCase();
            var mName = cp.matchName ? cp.matchName.toLowerCase() : "";
            if (cName !== wantComp && mName !== wantComp) continue;
            for (var p = 0; p < cp.properties.numItems; p++) {
                var pr = cp.properties[p];
                if (pr.displayName.toLowerCase() !== wantParam) continue;
                if (!pr.isTimeVarying()) pr.setTimeVarying(true);
                var t = _time(Number(sec) - c.start.seconds + c.inPoint.seconds);   // keyframe time is in clip media time
                pr.addKey(t);
                var v = value;
                try { v = JSON.parse(value); } catch (e) {}
                pr.setValueAtKey(t, v, true);
                return _ok();
            }
        }
        throw "Không tìm thấy " + compName + " / " + paramName;
    } catch (e) { return _err(e); }
}
function jesApplyEffect(type, track, index, effectName) {
    try {
        var s = _seq(), c = _clip(s, type, Number(track), Number(index)), qc = _qeClip(type, Number(track), c);
        var fx = type === "audio" ? qe.project.getAudioEffectByName(effectName) : qe.project.getVideoEffectByName(effectName);
        if (!fx) throw "Kh\u00f4ng c\u00f3 effect '" + effectName + "' (d\u00f9ng t\u00ean ti\u1ebfng Anh nh\u01b0 trong panel Effects)";
        if (type === "audio") qc.addAudioEffect(fx); else qc.addVideoEffect(fx);
        return _ok({ effect: effectName });
    } catch (e) { return _err(e); }
}
function jesAddTransition(type, track, index, name, atStart, durSec) {
    try {
        var s = _seq(), c = _clip(s, type, Number(track), Number(index)), qc = _qeClip(type, Number(track), c);
        var tr = type === "audio" ? qe.project.getAudioTransitionByName(name || "Constant Power") : qe.project.getVideoTransitionByName(name || "Cross Dissolve");
        if (!tr) throw "Kh\u00f4ng c\u00f3 transition '" + name + "'";
        var d = _tc(s, Number(durSec) || 0.5), start = atStart === true || atStart === "true";
        try { qc.addTransition(tr, start, d, "00:00:00:00", 0.5, false, true); } catch (e1) { qc.addTransition(tr, start, d); }
        return _ok({ transition: name, duration: d });
    } catch (e) { return _err(e); }
}
function jesSetSpeed(type, track, index, speedPct, ripple) {
    try {
        var s = _seq(), c = _clip(s, type, Number(track), Number(index)), qc = _qeClip(type, Number(track), c);
        qc.setSpeed(Number(speedPct) / 100, "", false, false, ripple === true || ripple === "true");
        return _ok({ speed: Number(speedPct) });
    } catch (e) { return _err(e); }
}
function jesExportFrame(sec, outPath) {
    try {
        var s = _seq(), qs = _qeSeq(), tc = _tc(s, sec);
        qs.exportFramePNG(tc, outPath);
        return _ok({ path: outPath, timecode: tc });
    } catch (e) { return _err(e); }
}
function jesCloneSequence(suffix) {
    try {
        var s = _seq(), before = app.project.sequences.numSequences, orig = s.name;
        s.clone();
        var copy = null;
        for (var i = app.project.sequences.numSequences - 1; i >= 0; i--) {
            var q = app.project.sequences[i];
            if (q.sequenceID !== s.sequenceID && q.name.indexOf(orig) === 0 && q.name !== orig) { copy = q; break; }
        }
        if (copy) copy.name = orig + (suffix || "_bak");
        app.project.openSequence(s.sequenceID);   // keep working on the original
        return _ok({ backup: copy ? copy.name : null, added: app.project.sequences.numSequences - before });
    } catch (e) { return _err(e); }
}
function jesOpenSequence(name) {
    try {
        for (var i = 0; i < app.project.sequences.numSequences; i++) {
            var q = app.project.sequences[i];
            if (q.name === name) { app.project.openSequence(q.sequenceID); return _ok({ opened: name }); }
        }
        throw "Kh\u00f4ng c\u00f3 sequence '" + name + "'";
    } catch (e) { return _err(e); }
}
function jesRenameSequence(newName) {
    try { var s = _seq(); s.name = newName; return _ok({ name: newName }); } catch (e) { return _err(e); }
}
function jesExport(outPath, presetPath, range) {
    try {
        var s = _seq();
        app.encoder.launchEncoder();
        var job = app.encoder.encodeSequence(s, outPath, presetPath, Number(range) || 0, 1);
        app.encoder.startBatch();
        return _ok({ job: job, out: outPath });
    } catch (e) { return _err(e); }
}
function jesSave() { try { app.project.save(); return _ok(); } catch (e) { return _err(e); } }
function jesEval(code) {
    try { var r = eval(code); return JSON.stringify({ ok: true, result: r === undefined ? null : String(r) }); } catch (e) { return _err(e); }
}

// self-test for "Ki\u1ec3m tra h\u1ec7 th\u1ed1ng": read-only checks of every agent capability
function jesSelfTest() {
    var r = { dom: false, seq: null, qe: false, fx: false, tr: false, frameExport: false, encoder: false };
    try { r.dom = !!app.project; var s = app.project.activeSequence;
          if (s) r.seq = { name: s.name, v: s.videoTracks.numTracks, a: s.audioTracks.numTracks, clipsV1: s.videoTracks.numTracks ? s.videoTracks[0].clips.numItems : 0 }; } catch (e) { r.domErr = String(e); }
    try { app.enableQE(); r.qe = !!qe.project; if (qe.project.getActiveSequence()) r.frameExport = typeof qe.project.getActiveSequence().exportFramePNG === "function" || !!qe.project.getActiveSequence().exportFramePNG;
          r.fx = !!qe.project.getVideoEffectByName("Gaussian Blur"); r.tr = !!qe.project.getVideoTransitionByName("Cross Dissolve"); } catch (e) { r.qeErr = String(e); }
    try { r.encoder = !!app.encoder; } catch (e) {}
    return JSON.stringify(r);
}

// ---------------------------------------------------------------- sound design (soundtrack + SFX + markers)
function _dbToLevel(dB) {
    var d = Number(dB);
    if (isNaN(d)) d = 0;
    return Math.pow(10, (d - 15) / 20); // Premiere Volume>Level: 0 dB = 0.1778
}
function _volLevel(clip) {
    if (!clip || !clip.components) return null;
    for (var i = 0; i < clip.components.numItems; i++) {
        var cp = clip.components[i];
        if (cp.displayName === "Volume" || cp.matchName === "audioVolume" || cp.matchName === "AE.ADBE Volume") {
            for (var p = 0; p < cp.properties.numItems; p++) {
                var pr = cp.properties[p];
                if (pr.displayName === "Level") return pr;
            }
        }
    }
    return null;
}
function _getBin(name) {
    return _resolveBinPath(name, true);
}
function _clipAt(track, sec) {
    if (!track || !track.clips) return null;
    var targetSec = Number(sec);
    var num = track.clips.numItems;
    if (num === 0) return null;
    var low = 0, high = num - 1;
    while (low <= high) {
        var mid = Math.floor((low + high) / 2);
        var cl = track.clips[mid];
        var s = cl.start.seconds;
        if (Math.abs(s - targetSec) < 0.08) return cl;
        if (s < targetSec) {
            low = mid + 1;
        } else {
            high = mid - 1;
        }
    }
    // Check neighbor boundary clips in case of sub-frame timing differences
    var checkIdx = [low - 1, low, low + 1];
    for (var k = 0; k < checkIdx.length; k++) {
        var idx = checkIdx[k];
        if (idx >= 0 && idx < num) {
            var ncl = track.clips[idx];
            if (Math.abs(ncl.start.seconds - targetSec) < 0.08) return ncl;
        }
    }
    return null;
}
function _ensureAudioTracks(seq, need) {
    var have = seq.audioTracks.numTracks;
    if (have >= need) return true;
    try { _qeSeq().addTracks(0, 0, need - have, 1, have, 0, 0, 0); } catch (e) {}
    return seq.audioTracks.numTracks >= need;
}
// spec: {track, bin, clear:false, items:[{path,t,in,dur,gain_db,fade_in,fade_out,duck:[[s,e]],duck_db}]}
function jesPlaceAudio(specJson) {
    try {
        var spec = JSON.parse(specJson), seq = _seq(), ti = Number(spec.track || 0), items = spec.items || [];
        if (!_ensureAudioTracks(seq, ti + 1)) throw "Sequence thi\u1ebfu audio track A" + (ti + 1) + " \u2014 th\u00eam track r\u1ed3i ch\u1ea1y l\u1ea1i";
        var track = seq.audioTracks[ti], bin = _getBin(spec.bin || "JES_SOUND");
        // batch import files not yet in the project
        var need = [], seen = {};
        for (var i = 0; i < items.length; i++) {
            var p = items[i].path;
            if (!seen[p] && !_itemByPath(p)) need.push(p);
            seen[p] = 1;
        }
        if (need.length) app.project.importFiles(need, true, bin, false);
        var placed = 0, errs = [];
        for (var k = 0; k < items.length; k++) {
            var it = items[k];
            try {
                var pi = _itemByPath(it.path);
                if (!pi) throw "import l\u1ed7i";
                var tin = Number(it["in"] || 0), dur = Number(it.dur || 0);
                if (dur > 0) { pi.setInPoint(tin, 4); pi.setOutPoint(tin + dur, 4); }
                track.overwriteClip(pi, Number(it.t));
                try { pi.clearInPoint(4); pi.clearOutPoint(4); } catch (e0) {}
                var cl = _clipAt(track, Number(it.t));
                if (cl) {
                    var lv = _volLevel(cl), base = _dbToLevel(it.gain_db || 0);
                    if (lv) {
                        var keys = [], cs = cl.start.seconds, ce = cl.end.seconds, off = cl.inPoint.seconds - cs;
                        var fi = Number(it.fade_in || 0), fo = Number(it.fade_out || 0), dk = it.duck || [], ddb = Number(it.duck_db || -10);
                        if (fi > 0) { keys.push([cs, 0]); keys.push([cs + fi, base]); }
                        for (var d = 0; d < dk.length; d++) {
                            var s = Number(dk[d][0]), e = Number(dk[d][1]);
                            if (e < cs || s > ce) continue;
                            var low = _dbToLevel(Number(it.gain_db || 0) + ddb);
                            keys.push([Math.max(cs, s - 0.3), base]); keys.push([Math.max(cs, s), low]);
                            keys.push([Math.min(ce, e), low]); keys.push([Math.min(ce, e + 0.4), base]);
                        }
                        if (fo > 0) { keys.push([Math.max(cs, ce - fo), base]); keys.push([ce - 0.02, 0]); }
                        if (keys.length) {
                            lv.setTimeVarying(true);
                            for (var q = 0; q < keys.length; q++) {
                                var tm = _time(keys[q][0] + off);
                                lv.addKey(tm); lv.setValueAtKey(tm, keys[q][1], true);
                            }
                        } else {
                            lv.setValue(base, true);
                        }
                    }
                }
                placed++;
            } catch (e1) { errs.push((it.name || it.path) + ": " + e1); }
        }
        return _ok({ placed: placed, total: items.length, track: "A" + (ti + 1), errors: errs.slice(0, 10) });
    } catch (e) { return _err(e); }
}
// items: [{t,name,comment,color,dur}] ; clearPrefix: delete existing markers whose name starts with it
function jesAddMarkers(itemsJson, clearPrefix) {
    try {
        var seq = _seq(), items = JSON.parse(itemsJson), removed = 0;
        if (clearPrefix) {
            var m = seq.markers.getFirstMarker(), dead = [];
            while (m) { if (String(m.name).indexOf(clearPrefix) === 0) dead.push(m); m = seq.markers.getNextMarker(m); }
            for (var i = 0; i < dead.length; i++) { seq.markers.deleteMarker(dead[i]); removed++; }
        }
        for (var k = 0; k < items.length; k++) {
            var it = items[k], mk = seq.markers.createMarker(Number(it.t));
            mk.name = it.name || ""; mk.comments = it.comment || "";
            if (it.dur) mk.end = Number(it.t) + Number(it.dur);
            if (it.color !== undefined && mk.setColorByIndex) mk.setColorByIndex(Number(it.color));
        }
        return _ok({ added: items.length, removed: removed });
    } catch (e) { return _err(e); }
}

// YouTube chapters (AI tab → Chương): replace the sequence's Chapter-type markers with items [{t, name}].
// Chapter markers travel with an H.264 / MP4 export; the panel also gives the same list as text for the description.
function jesSetChapters(itemsJson) {
    try {
        var seq = _seq(), items = JSON.parse(itemsJson), removed = 0, dead = [];
        if (!seq) return _err("Ch\u01b0a m\u1edf sequence");
        var m = seq.markers.getFirstMarker();
        while (m) { if (String(m.type) === "Chapter") dead.push(m); m = seq.markers.getNextMarker(m); }
        for (var i = 0; i < dead.length; i++) { seq.markers.deleteMarker(dead[i]); removed++; }
        for (var k = 0; k < items.length; k++) {
            var mk = seq.markers.createMarker(Number(items[k].t));
            mk.name = items[k].name || "";
            if (mk.setTypeAsChapter) mk.setTypeAsChapter();
        }
        return _ok({ added: items.length, removed: removed });
    } catch (e) { return _err(e); }
}

// ---------------------------------------------------------------- menu command runner & shortcuts
function jesFindCommand(cmdName) {
    try {
        if (!app.findMenuCommandId) return _err("Phi\u00ean b\u1ea3n Premiere n\u00e0y kh\u00f4ng h\u1ed7 tr\u1ee3 app.findMenuCommandId");
        var id = app.findMenuCommandId(String(cmdName));
        return _ok({ command: cmdName, commandId: id || 0, found: !!id });
    } catch (e) { return _err(e); }
}

function jesExecuteCommand(cmdNameOrId) {
    try {
        var id = Number(cmdNameOrId);
        if (isNaN(id) || id <= 0) {
            if (app.findMenuCommandId) {
                id = app.findMenuCommandId(String(cmdNameOrId));
            }
        }
        if (!id || id <= 0) {
            throw "Kh\u00f4ng t\u00ecm th\u1ea5y l\u1ec7nh Premiere: '" + cmdNameOrId + "'";
        }
        app.executeCommand(id);
        return _ok({ command: cmdNameOrId, commandId: id, executed: true });
    } catch (e) { return _err(e); }
}

function jesCloseGap() {
    try {
        if (app.findMenuCommandId) {
            var cid = app.findMenuCommandId("Close Gap");
            if (cid) { app.executeCommand(cid); return _ok({ method: "menu_command" }); }
        }
        var s = _seq(), count = 0;
        var allTracks = [];
        for (var v = 0; v < s.videoTracks.numTracks; v++) allTracks.push(s.videoTracks[v]);
        for (var a = 0; a < s.audioTracks.numTracks; a++) allTracks.push(s.audioTracks[a]);

        for (var t = 0; t < allTracks.length; t++) {
            var tr = allTracks[t];
            var num = tr.clips.numItems;
            if (num === 0) continue;

            var accShift = 0;
            // Close leading gap before the first clip if present
            var firstStart = tr.clips[0].start.seconds;
            if (firstStart > 0.04) {
                accShift += firstStart;
                tr.clips[0].move(_time(-accShift));
                count++;
            }

            for (var c = 0; c < num - 1; c++) {
                var c1 = tr.clips[c];
                var c2 = tr.clips[c + 1];
                var c1End = c1.end.seconds;
                var c2Start = c2.start.seconds - accShift;
                var gap = c2Start - c1End;
                if (gap > 0.04) {
                    accShift += gap;
                    count++;
                }
                if (accShift > 0.001) {
                    c2.move(_time(-accShift));
                }
            }
        }
        return _ok({ gapsClosed: count });
    } catch (e) { return _err(e); }
}

function jesMatchFrame(reverse) {
    try {
        var name = (reverse === true || reverse === "true") ? "Reverse Match Frame" : "Match Frame";
        if (app.findMenuCommandId) {
            var cid = app.findMenuCommandId(name);
            if (cid) { app.executeCommand(cid); return _ok({ matched: name }); }
        }
        return _err("Kh\u00f4ng t\u00ecm th\u1ea5y l\u1ec7nh Match Frame");
    } catch (e) { return _err(e); }
}

function jesFitToFrame(type, track, index, fill) {
    try {
        var c = _clip(_seq(), type || "video", Number(track) || 0, Number(index) || 0);
        c.setSelected(true, true);
        var cmdName = (fill === true || fill === "fill") ? "Fill frame" : "Scale to Frame Size";
        if (app.findMenuCommandId) {
            var cid = app.findMenuCommandId(cmdName) || app.findMenuCommandId("Fit to frame");
            if (cid) { app.executeCommand(cid); return _ok({ clip: c.name, fit: cmdName }); }
        }
        for (var i = 0; i < c.components.numItems; i++) {
            var cp = c.components[i];
            if (cp.matchName === "AE.ADBE Motion") {
                for (var p = 0; p < cp.properties.numItems; p++) {
                    var pr = cp.properties[p];
                    if (pr.displayName === "Scale") {
                        pr.setValue(100, true);
                        return _ok({ clip: c.name, scaled: 100 });
                    }
                }
            }
        }
        return _ok({ clip: c.name });
    } catch (e) { return _err(e); }
}

function jesApplyDefaultTransition(target) {
    try {
        var cmd = "Apply Default Transitions to Selection";
        if (target === "video") cmd = "Apply Video Transition";
        else if (target === "audio") cmd = "Apply Audio Transition";
        if (app.findMenuCommandId) {
            var cid = app.findMenuCommandId(cmd);
            if (cid) { app.executeCommand(cid); return _ok({ applied: cmd }); }
        }
        return _err("Kh\u00f4ng t\u00ecm th\u1ea5y l\u1ec7nh transition");
    } catch (e) { return _err(e); }
}

function jesZoomSequence() {
    try {
        if (app.findMenuCommandId) {
            var cid = app.findMenuCommandId("Zoom to Sequence");
            if (cid) { app.executeCommand(cid); return _ok({ zoom: "sequence" }); }
        }
        return _err("Kh\u00f4ng t\u00ecm th\u1ea5y l\u1ec7nh Zoom to Sequence");
    } catch (e) { return _err(e); }
}

function jesToggleTrack(type, trackIndex, mode) {
    try {
        var s = _seq(), tracks = _tracks(s, type || "audio");
        var tr = tracks[Number(trackIndex) || 0];
        if (!tr) throw "Kh\u00f4ng t\u00ecm th\u1ea5y track " + type + " " + trackIndex;
        var r = {};
        if (mode === "mute") {
            if (tr.setMute) { var m = !tr.isMuted(); tr.setMute(m); r.muted = m; }
        } else if (mode === "lock") {
            if (tr.setLocked) { var l = !tr.isLocked(); tr.setLocked(l); r.locked = l; }
        } else if (mode === "solo") {
            if (tr.setSolo) { var so = !tr.isSolo(); tr.setSolo(so); r.solo = so; }
        } else if (mode === "target") {
            if (tr.setTargeted) { var tg = !tr.isTargeted(); tr.setTargeted(tg, true); r.targeted = tg; }
        }
        return _ok(r);
    } catch (e) { return _err(e); }
}

function jesProjectTree(maxDepth) {
    try {
        var maxD = Number(maxDepth) || 8;
        function dumpBin(bin, depth) {
            var res = { name: bin.name, type: "bin", items: [] };
            if (depth > maxD || !bin.children) return res;
            for (var i = 0; i < bin.children.numItems; i++) {
                var it = bin.children[i];
                if (it.type === ProjectItemType.BIN) {
                    res.items.push(dumpBin(it, depth + 1));
                } else {
                    var mp = "";
                    try { mp = it.getMediaPath(); } catch (e) {}
                    res.items.push({ name: it.name, type: "clip", media: mp });
                }
            }
            return res;
        }
        var tree = dumpBin(app.project.rootItem, 0);
        return _ok({ tree: tree });
    } catch (e) { return _err(e); }
}

// ---------------------------------------------------------------- full Premiere MCP toolset
function jesCreateProject(projPath) {
    try {
        if (!projPath) throw "Cần truyền đường dẫn file dự án mới";
        var p = String(projPath).replace(/\\/g, "/");
        app.newProject(p);
        return _ok({ project: app.project.name, path: app.project.path });
    } catch (e) { return _err(e); }
}

function jesOpenProject(projPath) {
    try {
        if (!projPath) throw "Cần truyền đường dẫn file dự án cần mở";
        var p = String(projPath).replace(/\\/g, "/");
        var res = app.openDocument(p);
        return _ok({ project: app.project.name, path: app.project.path, opened: res });
    } catch (e) { return _err(e); }
}

function jesSaveProjectAs(newPath) {
    try {
        if (!newPath) throw "Cần truyền đường dẫn lưu mới";
        var p = String(newPath).replace(/\\/g, "/");
        var res = app.project.saveAs(p);
        return _ok({ project: app.project.name, path: app.project.path, saved: res });
    } catch (e) { return _err(e); }
}

function jesCreateBin(binName, parentBinPath) {
    try {
        if (!binName) throw "Cần truyền tên Bin";
        var parent = _resolveBinPath(parentBinPath || "", true);
        var b = parent.createBin(String(binName));
        return _ok({ bin: b.name, parent: parent.name });
    } catch (e) { return _err(e); }
}

function jesImportFolder(folderPath, binName) {
    try {
        if (!folderPath) throw "Cần truyền đường dẫn thư mục";
        var fld = new Folder(String(folderPath));
        if (!fld.exists) throw "Thư mục không tồn tại: " + folderPath;
        var bin = _resolveBinPath(binName || fld.name, true);
        var entries = fld.getFiles();
        var paths = [];
        for (var i = 0; i < entries.length; i++) {
            var item = entries[i];
            if (item instanceof File) {
                var ext = item.name.slice(item.name.lastIndexOf(".")).toLowerCase();
                if (/^\.(mp4|mov|mxf|mts|avi|mkv|wav|mp3|aac|m4a|png|jpg|jpeg|psd|tif|tiff)$/i.test(ext)) {
                    paths.push(item.fsName);
                }
            }
        }
        if (paths.length > 0) {
            app.project.importFiles(paths, true, bin, false);
        }
        return _ok({ folder: folderPath, bin: bin.name, importedCount: paths.length });
    } catch (e) { return _err(e); }
}

function jesCreateSequence(seqName, width, height, fps, presetPath) {
    try {
        var name = seqName || ("Seq_" + Date.now());
        var s = null;
        if (presetPath && (new File(presetPath)).exists) {
            s = app.project.createNewSequenceFromPreset(name, presetPath);
        } else {
            s = app.project.createNewSequence(name, "1");
        }
        if (!s) s = app.project.activeSequence;
        if (s && (width || height || fps)) {
            try {
                var st = s.getSettings();
                if (width) st.videoFrameWidth = Number(width);
                if (height) st.videoFrameHeight = Number(height);
                if (fps) {
                    var fr = new Time();
                    fr.seconds = 1 / Number(fps);
                    st.videoFrameRate = fr;
                }
                s.setSettings(st);
            } catch (eSet) {}
        }
        return _ok({ sequence: s ? s.name : name, id: s ? s.sequenceID : null,
                     w: width || (s ? s.getSettings().videoFrameWidth : 1920),
                     h: height || (s ? s.getSettings().videoFrameHeight : 1080) });
    } catch (e) { return _err(e); }
}

function jesDuplicateSequence(seqName, newName) {
    try {
        var target = null;
        if (seqName) {
            for (var i = 0; i < app.project.sequences.numSequences; i++) {
                if (app.project.sequences[i].name === seqName) {
                    target = app.project.sequences[i];
                    break;
                }
            }
        }
        if (!target) target = _seq();
        var orig = target.name;
        target.clone();
        var copy = null;
        for (var j = app.project.sequences.numSequences - 1; j >= 0; j--) {
            var q = app.project.sequences[j];
            if (q.sequenceID !== target.sequenceID && q.name.indexOf(orig) === 0 && q.name !== orig) {
                copy = q;
                break;
            }
        }
        if (copy) copy.name = newName || (orig + "_copy");
        return _ok({ original: orig, duplicated: copy ? copy.name : null });
    } catch (e) { return _err(e); }
}

function jesDeleteSequence(seqName) {
    try {
        if (!seqName) throw "Cần truyền tên sequence để xóa";
        var seqObj = null;
        for (var i = 0; i < app.project.sequences.numSequences; i++) {
            if (app.project.sequences[i].name === seqName) {
                seqObj = app.project.sequences[i];
                break;
            }
        }
        if (!seqObj) throw "Không tìm thấy sequence: " + seqName;
        var pItem = seqObj.projectItem;
        if (pItem) {
            pItem.deleteItem();
            return _ok({ deleted: seqName });
        }
        throw "Không xóa được sequence projectItem: " + seqName;
    } catch (e) { return _err(e); }
}

function jesRemoveEffect(type, track, index, effectName) {
    try {
        var c = _clip(_seq(), type, Number(track), Number(index));
        var targetLc = String(effectName).toLowerCase();
        for (var i = c.components.numItems - 1; i >= 0; i--) {
            var cp = c.components[i];
            if (cp.displayName.toLowerCase() === targetLc || cp.matchName.toLowerCase() === targetLc) {
                if (cp.remove) {
                    cp.remove();
                    return _ok({ removed: cp.displayName });
                }
            }
        }
        var qc = _qeClip(type, Number(track), c);
        var num = type === "audio" ? qc.numAudioEffects : qc.numVideoEffects;
        for (var k = num - 1; k >= 0; k--) {
            var f = type === "audio" ? qc.getAudioEffectAt(k) : qc.getVideoEffectAt(k);
            if (f && f.name && f.name.toLowerCase() === targetLc) {
                if (type === "audio") qc.removeAudioEffect(k);
                else qc.removeVideoEffect(k);
                return _ok({ removed: f.name });
            }
        }
        return _ok({ note: "Không tìm thấy effect '" + effectName + "'" });
    } catch (e) { return _err(e); }
}

function jesAdjustAudioLevels(track, index, gainDb) {
    try {
        var c = _clip(_seq(), "audio", Number(track), Number(index));
        var db = Number(gainDb) || 0;
        var lvl = _volLevel(c);
        if (lvl) {
            var raw = _dbToLevel(db);
            lvl.setValue(raw, true);
            return _ok({ track: track, index: index, gain_db: db });
        }
        if (c.projectItem && c.projectItem.setAudioGain) {
            c.projectItem.setAudioGain(db);
            return _ok({ track: track, index: index, gain_db: db });
        }
        throw "Không tìm thấy thông số Level trên clip";
    } catch (e) { return _err(e); }
}

function jesColorCorrect(track, index, adjJson) {
    try {
        var s = _seq(), c = _clip(s, "video", Number(track), Number(index));
        var adj = typeof adjJson === "string" ? JSON.parse(adjJson) : (adjJson || {});
        var lumetri = null;
        for (var i = 0; i < c.components.numItems; i++) {
            if (/lumetri/i.test(c.components[i].displayName)) {
                lumetri = c.components[i];
                break;
            }
        }
        if (!lumetri) {
            var qc = _qeClip("video", Number(track), c);
            var fx = qe.project.getVideoEffectByName("Lumetri Color");
            if (fx) qc.addVideoEffect(fx);
            for (var j = 0; j < c.components.numItems; j++) {
                if (/lumetri/i.test(c.components[j].displayName)) { lumetri = c.components[j]; break; }
            }
        }
        if (!lumetri) throw "Không tìm thấy Lumetri Color";
        var lowerAdj = {};
        for (var k in adj) {
            if (Object.prototype.hasOwnProperty.call(adj, k)) lowerAdj[String(k).toLowerCase()] = Number(adj[k]);
        }
        var applied = {};
        for (var p = 0; p < lumetri.properties.numItems; p++) {
            var prop = lumetri.properties[p];
            var pname = prop.displayName.toLowerCase();
            for (var lk in lowerAdj) {
                if (pname.indexOf(lk) >= 0) {
                    var val = lowerAdj[lk];
                    prop.setValue(val, true);
                    applied[prop.displayName] = val;
                    break;
                }
            }
        }
        return _ok({ clip: c.name, applied: applied });
    } catch (e) { return _err(e); }
}

function jesApplyLUT(track, index, lutPath) {
    try {
        var s = _seq(), c = _clip(s, "video", Number(track), Number(index));
        var p = String(lutPath).replace(/\\/g, "/");
        if (!(new File(p)).exists) throw "File LUT không tồn tại: " + p;
        var lumetri = null;
        for (var i = 0; i < c.components.numItems; i++) {
            if (/lumetri/i.test(c.components[i].displayName)) { lumetri = c.components[i]; break; }
        }
        if (!lumetri) {
            var qc = _qeClip("video", Number(track), c);
            var fx = qe.project.getVideoEffectByName("Lumetri Color");
            if (fx) qc.addVideoEffect(fx);
            for (var j = 0; j < c.components.numItems; j++) {
                if (/lumetri/i.test(c.components[j].displayName)) { lumetri = c.components[j]; break; }
            }
        }
        if (!lumetri) throw "Không tìm thấy Lumetri Color";
        for (var k = 0; k < lumetri.properties.numItems; k++) {
            var pr = lumetri.properties[k];
            if (/look|lut/i.test(pr.displayName)) {
                try { pr.setValue(p, true); return _ok({ clip: c.name, lut: p, prop: pr.displayName }); } catch (eLut) {}
            }
        }
        return _ok({ clip: c.name, lut: p, note: "Đã thêm Lumetri Color" });
    } catch (e) { return _err(e); }
}

function jesStabilizeClip(track, index) {
    try {
        var c = _clip(_seq(), "video", Number(track), Number(index));
        var qc = _qeClip("video", Number(track), c);
        var fx = qe.project.getVideoEffectByName("Warp Stabilizer") || qe.project.getVideoEffectByName("Warp Stabilizer VFX");
        if (!fx) throw "Không tìm thấy effect Warp Stabilizer";
        qc.addVideoEffect(fx);
        return _ok({ clip: c.name, stabilized: true });
    } catch (e) { return _err(e); }
}

function jesCreateProxyMedia(type, track, index) {
    try {
        var c = _clip(_seq(), type || "video", Number(track), Number(index));
        var pItem = c.projectItem;
        if (!pItem) throw "Clip không có projectItem";
        if (pItem.canCreateProxy && pItem.canCreateProxy()) {
            pItem.createProxy("", 0);
            return _ok({ clip: c.name, proxyCreated: true });
        }
        return _ok({ clip: c.name, proxySupported: false });
    } catch (e) { return _err(e); }
}

function jesAutoEditToMusic(musicTrack, videoBin, pace) {
    try {
        var s = _seq(), mt = Number(musicTrack) || 0;
        var aTrack = s.audioTracks[mt];
        if (!aTrack || aTrack.clips.numItems === 0) throw "Track audio A" + (mt + 1) + " chưa có nhạc";
        var beats = [];
        var m = s.markers.getFirstMarker();
        while (m) {
            var mName = String(m.name || "").toLowerCase();
            if (mName.indexOf("♪") >= 0 || mName.indexOf("beat") >= 0 || mName.indexOf("drop") >= 0) {
                beats.push(m.start.seconds);
            }
            m = s.markers.getNextMarker(m);
        }
        if (beats.length === 0) {
            m = s.markers.getFirstMarker();
            while (m) {
                beats.push(m.start.seconds);
                m = s.markers.getNextMarker(m);
            }
        }
        beats.sort(function (a, b) { return a - b; });
        if (beats.length < 2) {
            var step = (pace === "fast") ? 1.0 : (pace === "slow") ? 3.0 : 1.8;
            var musicDur = aTrack.clips[0].end.seconds;
            beats = [];
            for (var t = 0; t < musicDur; t += step) beats.push(_r(t));
        }
        var bin = _resolveBinPath(videoBin || "B-roll", false) || app.project.rootItem;
        var vItems = [];
        var binStack = [bin];
        while (binStack.length > 0) {
            var curB = binStack.pop();
            if (!curB || !curB.children) continue;
            for (var bi = 0; bi < curB.children.numItems; bi++) {
                var child = curB.children[bi];
                if (child.type === ProjectItemType.CLIP) vItems.push(child);
                else if (child.type === ProjectItemType.BIN) binStack.push(child);
            }
        }
        if (vItems.length === 0) throw "Không tìm thấy clip video trong bin: " + (videoBin || "root");
        var vt = s.videoTracks[0];
        var placed = 0;
        for (var b = 0; b < beats.length - 1; b++) {
            var clipIt = vItems[b % vItems.length];
            var bStart = beats[b], bEnd = beats[b + 1];
            var durNeeded = bEnd - bStart;
            if (durNeeded <= 0.05) continue;
            clipIt.setInPoint(0, 4);
            clipIt.setOutPoint(durNeeded, 4);
            vt.overwriteClip(clipIt, bStart);
            try { clipIt.clearInPoint(4); clipIt.clearOutPoint(4); } catch (e0) {}
            placed++;
        }
        return _ok({ placedClips: placed, beats: beats.length });
    } catch (e) { return _err(e); }
}

function jesCreateMulticamSequence(seqName, clipsJson) {
    try {
        var name = seqName || ("Multicam_" + Date.now());
        var s = app.project.createNewSequence(name, "1");
        if (!s) s = app.project.activeSequence;
        var list = clipsJson ? (typeof clipsJson === "string" ? JSON.parse(clipsJson) : clipsJson) : [];
        for (var i = 0; i < list.length; i++) {
            var it = _itemByPath(list[i]);
            if (it && s.videoTracks[i]) {
                s.videoTracks[i].overwriteClip(it, 0);
            }
        }
        return _ok({ multicam: name, angleCount: list.length });
    } catch (e) { return _err(e); }
}

function jesListSequences() {
    try {
        var p = app.project;
        var res = [];
        var actId = p.activeSequence ? p.activeSequence.sequenceID : null;
        for (var i = 0; i < p.sequences.numSequences; i++) {
            var s = p.sequences[i];
            var item = {
                name: s.name,
                sequenceID: s.sequenceID,
                active: (s.sequenceID === actId),
                videoTracks: s.videoTracks ? s.videoTracks.numTracks : 0,
                audioTracks: s.audioTracks ? s.audioTracks.numTracks : 0
            };
            try {
                var st = s.getSettings();
                if (st) {
                    item.width = st.videoFrameWidth;
                    item.height = st.videoFrameHeight;
                    if (st.videoFrameRate && st.videoFrameRate.seconds) {
                        item.fps = Math.round(1 / st.videoFrameRate.seconds * 100) / 100;
                    }
                }
            } catch (e1) {}
            try {
                if (s.end) {
                    var endVal = Number(s.end);
                    item.duration = endVal > 1000000 ? _r(endVal / TICKS) : _r(endVal);
                }
            } catch (e2) {}
            res.push(item);
        }
        return _ok({ sequences: res, count: res.length });
    } catch (e) { return _err(e); }
}

function jesListSequenceTracks(seqName) {
    try {
        var s = null;
        if (seqName) {
            for (var i = 0; i < app.project.sequences.numSequences; i++) {
                if (app.project.sequences[i].name === seqName) {
                    s = app.project.sequences[i];
                    break;
                }
            }
        }
        if (!s) s = _seq();
        var vTracks = [], aTracks = [];
        for (var v = 0; v < s.videoTracks.numTracks; v++) {
            var vt = s.videoTracks[v];
            var clips = [];
            for (var c = 0; c < vt.clips.numItems; c++) {
                var cl = vt.clips[c];
                clips.push({ index: c, name: cl.name, start: _r(cl.start.seconds), end: _r(cl.end.seconds), dur: _r(cl.duration.seconds) });
            }
            vTracks.push({
                index: v,
                name: vt.name || ("V" + (v + 1)),
                clipsCount: vt.clips.numItems,
                locked: vt.isLocked ? vt.isLocked() : false,
                targeted: vt.isTargeted ? vt.isTargeted() : false,
                clips: clips
            });
        }
        for (var a = 0; a < s.audioTracks.numTracks; a++) {
            var at = s.audioTracks[a];
            var aclips = [];
            for (var ac = 0; ac < at.clips.numItems; ac++) {
                var acl = at.clips[ac];
                aclips.push({ index: ac, name: acl.name, start: _r(acl.start.seconds), end: _r(acl.end.seconds), dur: _r(acl.duration.seconds) });
            }
            aTracks.push({
                index: a,
                name: at.name || ("A" + (a + 1)),
                clipsCount: at.clips.numItems,
                muted: at.isMuted ? at.isMuted() : false,
                solo: at.isSolo ? at.isSolo() : false,
                locked: at.isLocked ? at.isLocked() : false,
                targeted: at.isTargeted ? at.isTargeted() : false,
                clips: aclips
            });
        }
        return _ok({ sequence: s.name, videoTracks: vTracks, audioTracks: aTracks });
    } catch (e) { return _err(e); }
}

function jesAddAudioKeyframe(track, index, sec, gainDb) {
    try {
        var s = _seq(), c = _clip(s, "audio", Number(track), Number(index));
        var lvl = _volLevel(c);
        if (!lvl) throw "Không tìm thấy thông số Volume / Level trên clip";
        var raw = _dbToLevel(gainDb);
        var t = _time(sec);
        lvl.setTimeVarying(true);
        lvl.addKey(t);
        lvl.setValueAtKey(t, raw, true);
        return _ok({ track: track, index: index, seconds: Number(sec), gain_db: Number(gainDb) });
    } catch (e) { return _err(e); }
}



// ---------------------------------------------------------------- Review v1.7: direct edits on the open sequence
// A shot is located by (timeline start, media path) — track/clip indexes shift after every edit, these two don't.
// media "" = a text card / graphic (missing-footage placeholder).
function _rvMedia(cl) { var mp = ""; try { mp = cl.projectItem ? cl.projectItem.getMediaPath() : ""; } catch (e) {} return _normPath(mp); }
function _rvFindV(seq, start, media) {
    var want = _normPath(media), best = null, bd = 1e9;
    for (var t = 0; t < seq.videoTracks.numTracks; t++) {
        var tr = seq.videoTracks[t];
        for (var c = 0; c < tr.clips.numItems; c++) {
            var cl = tr.clips[c], d = Math.abs(cl.start.seconds - Number(start));
            if (d > 0.06) continue;
            var mp = _rvMedia(cl);
            if (want ? mp !== want : !!mp) continue;
            if (d < bd) { bd = d; best = { track: t, idx: c, clip: cl }; }
        }
    }
    if (!best) throw "Không tìm thấy shot trên timeline tại " + _r(start) + "s" + (media ? " (" + String(media).replace(/^.*[\\\/]/, "") + ")" : "") +
                     " — timeline đã đổi? bấm ↻ Đồng bộ";
    return best;
}
function _rvLinkedAudio(seq, v, media) {
    // audio of the same source laid with this shot (J/L cuts may start/end it up to ~1.5 s away)
    var want = _normPath(media), res = [];
    if (!want) return res;
    for (var t = 0; t < seq.audioTracks.numTracks; t++) {
        var tr = seq.audioTracks[t];
        for (var c = 0; c < tr.clips.numItems; c++) {
            var cl = tr.clips[c];
            if (_rvMedia(cl) !== want) continue;
            if (Math.abs(cl.start.seconds - v.start.seconds) < 1.5 && Math.min(cl.end.seconds, v.end.seconds) > Math.max(cl.start.seconds, v.start.seconds))
                res.push(cl);
        }
    }
    return res;
}
function _rvShift(seq, after, delta) {
    // ripple: every item (all tracks) starting at/after `after` moves by delta; order avoids collisions
    if (Math.abs(delta) < 0.001) return 0;
    var items = [], kinds = [seq.videoTracks, seq.audioTracks];
    for (var k = 0; k < 2; k++) {
        for (var t = 0; t < kinds[k].numTracks; t++) {
            var tr = kinds[k][t];
            for (var c = 0; c < tr.clips.numItems; c++) { var cl = tr.clips[c]; if (cl.start.seconds >= after - 0.01) items.push(cl); }
        }
    }
    items.sort(function (a, b) { return delta > 0 ? b.start.seconds - a.start.seconds : a.start.seconds - b.start.seconds; });
    for (var i = 0; i < items.length; i++) items[i].move(_time(delta));
    return items.length;
}
function jesRvReplace(start, media, newPath, inSec, outSec, keepAudio) {
    // Replace one shot in place: same slot, new source range; later shots ripple by the length change.
    try {
        var s = _seq(), v = _rvFindV(s, start, media), cl = v.clip;
        var t0 = cl.start.seconds, oldEnd = cl.end.seconds, oldDur = oldEnd - t0;
        var auds = _rvLinkedAudio(s, cl, media);
        var it = _importOne(newPath, "JES_AI");
        var a = Math.max(0, Number(inSec)), b = Number(outSec), want = b - a;
        if (!(want > 0.05)) throw "In/Out không hợp lệ";
        it.setInPoint(a, 4); it.setOutPoint(b, 4);
        cl.remove(false, false);
        for (var i = 0; i < auds.length; i++) { try { auds[i].remove(false, false); } catch (e1) {} }
        _rvShift(s, oldEnd, want - oldDur);
        s.videoTracks[v.track].overwriteClip(it, t0);
        try { it.clearInPoint(4); it.clearOutPoint(4); } catch (e2) {}
        var nv = _rvFindV(s, t0, newPath).clip, got = nv.end.seconds - nv.start.seconds;
        if (got < want - 0.04) _rvShift(s, t0 + want, got - want);       // source shorter than asked → close the hole
        var placed = _rvLinkedAudio(s, nv, newPath), mute = (keepAudio === false || keepAudio === "false");
        if (mute) for (var j = 0; j < placed.length; j++) { try { placed[j].remove(false, false); } catch (e3) {} }
        return _ok({ start: _r(t0), dur: _r(got), track: v.track, audio: mute ? 0 : placed.length, shift: _r(got - oldDur) });
    } catch (e) { return _err(e); }
}
function jesRvRemove(start, media) {
    // Remove one shot (+ its audio) and ripple the rest left.
    try {
        var s = _seq(), v = _rvFindV(s, start, media), cl = v.clip, t0 = cl.start.seconds, e0 = cl.end.seconds;
        var auds = _rvLinkedAudio(s, cl, media);
        cl.remove(false, false);
        for (var i = 0; i < auds.length; i++) { try { auds[i].remove(false, false); } catch (e1) {} }
        _rvShift(s, e0, -(e0 - t0));
        return _ok({ removed: 1, dur: _r(e0 - t0) });
    } catch (e) { return _err(e); }
}
function jesRvShots() {
    // Every video shot of the active sequence (for Review <-> timeline sync).
    try {
        var s = _seq(), out = [];
        for (var t = 0; t < s.videoTracks.numTracks; t++) {
            var tr = s.videoTracks[t];
            for (var c = 0; c < tr.clips.numItems; c++) {
                var cl = tr.clips[c], spd = 1;
                try { if (cl.getSpeed) spd = cl.getSpeed(); } catch (e0) {}
                out.push({ track: t, start: _r(cl.start.seconds), end: _r(cl.end.seconds), "in": _r(cl.inPoint.seconds), out: _r(cl.outPoint.seconds),
                           media: _rvMedia(cl), name: cl.name, enabled: cl.disabled !== true, speed: _r(spd) });
            }
        }
        return _ok({ name: s.name, id: s.sequenceID, shots: out });
    } catch (e) { return _err(e); }
}

// ---------------------------------------------------------------- tab Kịch bản: label màu clip source theo nhóm
// items = [{path, color (0–15, bảng label Premiere), bin?}] ; doImport: import clip chưa có trong project vào bin của nó
function jesLabelItems(itemsJson, doImport) {
    try {
        var items = JSON.parse(itemsJson), done = 0, missing = [], errs = [], byBin = {}, k, i;
        _mediaCache = null;
        if (doImport) {
            for (i = 0; i < items.length; i++) {
                if (!_itemByPath(items[i].path)) { k = items[i].bin || "JES_Source"; (byBin[k] = byBin[k] || []).push(items[i].path); }
            }
            for (k in byBin) {
                if (!byBin.hasOwnProperty(k)) continue;
                try { app.project.importFiles(byBin[k], true, _resolveBinPath(k, true), false); } catch (e0) { errs.push(k + ": " + e0); }
            }
            _mediaCache = null;
        }
        for (i = 0; i < items.length; i++) {
            var it = _itemByPath(items[i].path);
            if (!it) { missing.push(items[i].path); continue; }
            try { it.setColorLabel(Number(items[i].color)); done++; } catch (e1) { errs.push(items[i].path + ": " + e1); }
        }
        return _ok({ labeled: done, total: items.length, missing: missing.length, missingSample: missing.slice(0, 5), errors: errs.slice(0, 5) });
    } catch (e) { return _err(e); }
}

// ---------------------------------------------------------------- tab Chữ (JIZURA kinetic text, v1.8)
// items = [{path, at}] (seconds on the open sequence) → import into bin JES_TextFX and overwrite on video track vTrack (0-based);
// missing video tracks are added first (QE). Clips are ProRes 4444 with alpha, so they composite over V1/V2 as they are.
function jesTextFxPlace(itemsJson, vTrack, binName) {
    try {
        var s = _seq(), items = JSON.parse(itemsJson), vt = Number(vTrack) || 0, n = 0, errs = [];
        if (vt >= s.videoTracks.numTracks) {
            try { app.enableQE(); var q = qe.project.getActiveSequence(); q.addTracks(vt + 1 - s.videoTracks.numTracks, s.videoTracks.numTracks, 0); } catch (eq) {}
            s = _seq();
            if (vt >= s.videoTracks.numTracks) throw "Không thêm được track V" + (vt + 1) + " — thêm tay rồi thử lại";
        }
        var tr = s.videoTracks[vt];
        for (var i = 0; i < items.length; i++) {
            try {
                var it = _importOne(items[i].path, binName || "JES_TextFX");
                tr.overwriteClip(it, Number(items[i].at) || 0);
                n++;
            } catch (e1) { errs.push(String(e1)); }
        }
        return _ok({ placed: n, track: "V" + (vt + 1), errors: errs.slice(0, 5) });
    } catch (e) { return _err(e); }
}

// ---------------------------------------------------------------- AI edit: post-production on the built sequence
// item = {tl, file, reframe:[[srcSec, x, y]…], color:{Exposure, Temperature, Tint, Saturation}, stabilize:true}
// (from nxcore.aiedit post). The V1 clip is found by its timeline start; keyframe times are clip media seconds.
function _aiComp(clip, names) {
    for (var i = 0; i < clip.components.numItems; i++) {
        var cp = clip.components[i], dn = String(cp.displayName).toLowerCase(), mn = cp.matchName ? String(cp.matchName).toLowerCase() : "";
        for (var k = 0; k < names.length; k++) if (dn === names[k] || mn === names[k]) return cp;
    }
    return null;
}
function _aiProp(comp, name) {
    var want = String(name).toLowerCase();
    for (var p = 0; p < comp.properties.numItems; p++) if (String(comp.properties[p].displayName).toLowerCase() === want) return comp.properties[p];
    return null;
}
// One clip. fx = effects looked up once per batch ({lumetri, warp}, filled lazily).
function _aiPostItem(it, s, tr, fx) {
    var c = _clipAt(tr, Number(it.tl));
    if (!c) throw "Không thấy clip tại " + it.tl + "s trên V1";
    var done = [];
    if (it.reframe && it.reframe.length) {
        var mo = _aiComp(c, ["motion", "ae.adbe motion"]);
        var pos = mo ? _aiProp(mo, "Position") : null;
        if (!pos) throw "Clip không có Motion > Position";
        if (it.reframe.length === 1) {
            if (pos.isTimeVarying()) pos.setTimeVarying(false);
            pos.setValue([Number(it.reframe[0][1]), Number(it.reframe[0][2])], true);
        } else {
            if (!pos.isTimeVarying()) pos.setTimeVarying(true);
            for (var k = 0; k < it.reframe.length; k++) {
                var kf = it.reframe[k], t = _time(Number(kf[0]));
                pos.addKey(t);
                pos.setValueAtKey(t, [Number(kf[1]), Number(kf[2])], true);
            }
        }
        done.push("reframe");
    }
    if (it.color) {
        var lum = _aiComp(c, ["lumetri color", "ae.adbe lumetri"]);
        if (!lum) {
            if (fx.lumetri === undefined) { app.enableQE(); fx.lumetri = qe.project.getVideoEffectByName("Lumetri Color") || null; }
            if (!fx.lumetri) throw "Không có effect Lumetri Color";
            _qeClip("video", 0, c).addVideoEffect(fx.lumetri);
            c = _clipAt(tr, Number(it.tl));                       // re-read: components changed
            lum = _aiComp(c, ["lumetri color", "ae.adbe lumetri"]);
        }
        if (!lum) throw "Không thêm được Lumetri Color";
        var set = [];
        for (var name in it.color) {
            if (!it.color.hasOwnProperty(name)) continue;
            var pr = _aiProp(lum, name);
            if (pr) { pr.setValue(Number(it.color[name]), true); set.push(name); }
        }
        done.push("color:" + set.join("/"));
    }
    if (it.stabilize) {
        if (fx.warp === undefined) {
            app.enableQE();
            fx.warp = qe.project.getVideoEffectByName("Warp Stabilizer") || qe.project.getVideoEffectByName("Warp Stabilizer VFX") || null;
        }
        if (!fx.warp) throw "Không có effect Warp Stabilizer";
        _qeClip("video", 0, c).addVideoEffect(fx.warp);
        done.push("stabilize");
    }
    return { clip: c.name, done: done };
}
function jesApplyPost(itemJson) {
    try {
        var s = _seq(), r = _aiPostItem(JSON.parse(itemJson), s, s.videoTracks[0], {});
        return _ok(r);
    } catch (e) { return _err(e); }
}
// A batch of items in ONE ExtendScript call: one round trip, one effect lookup, one sequence lookup. Each item has its
// own try/catch so one bad clip never stops the batch. → {ok, results: [{ok, clip, done} | {ok:false, tl, error}]}
function jesApplyPostBatch(itemsJson) {
    try {
        var items = JSON.parse(itemsJson), s = _seq(), tr = s.videoTracks[0], fx = {}, out = [];
        for (var i = 0; i < items.length; i++) {
            try {
                var r = _aiPostItem(items[i], s, tr, fx);
                r.ok = true;
                out.push(r);
            } catch (e1) { out.push({ ok: false, tl: items[i].tl, error: String(e1) }); }
        }
        return _ok({ results: out });
    } catch (e) { return _err(e); }
}
