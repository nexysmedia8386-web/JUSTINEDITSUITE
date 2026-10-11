/* Justin EDIT Suite — Premiere agent toolkit: tool schemas for the LLM + executors over jsx/host.jsx.
   Autonomy (Cài đặt → Mức tự chủ của AI):
     ask    = hỏi trước mỗi thao tác sửa timeline
     backup = tự làm, nhưng clone sequence thành "<tên>_bak_HHMM" trước lần sửa đầu tiên mỗi lượt (mặc định)
     auto   = tự làm hoàn toàn
   pr_run_extendscript luôn hỏi xác nhận trừ khi ở chế độ auto. */
(function () {
  "use strict";
  var fs = require("fs"), path = require("path");

  var T = function (name, description, props, required) {
    return { name: name, description: description, parameters: { type: "object", properties: props || {}, required: required || [] } };
  };
  var CLIP = { type: { type: "string", enum: ["video", "audio"] }, track: { type: "integer", description: "0 = V1/A1" }, index: { type: "integer", description: "chỉ số clip trên track (từ pr_get_sequence)" } };
  var CLIP_REQ = ["type", "track", "index"];
  function ext(o, more) { var r = {}; Object.keys(o).forEach(function (k) { r[k] = o[k]; }); Object.keys(more).forEach(function (k) { r[k] = more[k]; }); return r; }

  var TOOLS = [
    T("pr_get_project", "Tổng quan project Premiere: tên, danh sách sequence, sequence đang mở, bin gốc."),
    T("pr_get_sequence", "Chi tiết sequence đang mở: fps, khung, thời lượng, playhead, in/out, từng track với clip (i, start/end trên timeline, in/out trong media, đường dẫn), marker. GỌI LẠI sau mỗi lần sửa vì chỉ số clip thay đổi.",
      { max_clips: { type: "integer" } }),
    T("pr_get_clip_components", "Liệt kê effect/thông số của một clip (Motion, Opacity, Volume, Lumetri…) với giá trị hiện tại.", CLIP, CLIP_REQ),
    T("pr_set_playhead", "Di chuyển playhead tới giây trên timeline.", { seconds: { type: "number" } }, ["seconds"]),
    T("pr_set_in_out", "Đặt In/Out của sequence (giây).", { in: { type: "number" }, out: { type: "number" } }, ["in", "out"]),
    T("pr_add_marker", "Thêm marker trên sequence.", { seconds: { type: "number" }, name: { type: "string" }, comment: { type: "string" },
      color: { type: "integer", description: "0 xanh lá,1 đỏ,2 tím,3 cam,4 vàng,5 trắng,6 xanh dương,7 cyan" }, duration: { type: "number" } }, ["seconds", "name"]),
    T("pr_razor", "Cắt (razor) tại một thời điểm trên timeline.", { seconds: { type: "number" }, type: { type: "string", enum: ["all", "video", "audio"] },
      tracks: { type: "array", items: { type: "integer" }, description: "bỏ trống = mọi track" } }, ["seconds"]),
    T("pr_remove_clips", "Xóa clip khỏi timeline (ripple = dồn khoảng trống).", { clips: { type: "array", items: { type: "object", properties: CLIP } },
      ripple: { type: "boolean" } }, ["clips"]),
    T("pr_move_clip", "Dịch clip trên timeline thêm delta_seconds (âm = sang trái).", ext(CLIP, { delta_seconds: { type: "number" } }), CLIP_REQ.concat(["delta_seconds"])),
    T("pr_trim_clip", "Đặt lại điểm bắt đầu/kết thúc của clip trên timeline (giây).", ext(CLIP, { start: { type: "number" }, end: { type: "number" } }), CLIP_REQ),
    T("pr_enable_clip", "Bật/tắt clip.", ext(CLIP, { enabled: { type: "boolean" } }), CLIP_REQ.concat(["enabled"])),
    T("pr_select_clip", "Chọn/bỏ chọn clip.", ext(CLIP, { selected: { type: "boolean" } }), CLIP_REQ.concat(["selected"])),
    T("pr_insert_clip", "Import (nếu cần) và chèn file vào timeline tại giây, track video chỉ định; overwrite=true để ghi đè. in/out = đoạn trong media.",
      { path: { type: "string" }, seconds: { type: "number" }, video_track: { type: "integer" }, overwrite: { type: "boolean" }, in: { type: "number" }, out: { type: "number" } }, ["path", "seconds"]),
    T("pr_import", "Import file vào project (bin JES_AI hoặc bin chỉ định).", { paths: { type: "array", items: { type: "string" } }, bin: { type: "string" } }, ["paths"]),
    T("pr_set_param", "Đặt giá trị thông số effect của clip. Ví dụ component 'Motion' param 'Scale' value 110; 'Opacity'/'Opacity' 80; 'Motion'/'Position' [540,960]. Tên đúng như pr_get_clip_components.",
      ext(CLIP, { component: { type: "string" }, param: { type: "string" }, value: { description: "số, chuỗi hoặc mảng" } }), CLIP_REQ.concat(["component", "param", "value"])),
    T("pr_add_keyframe", "Thêm keyframe cho thông số tại giây trên timeline.", ext(CLIP, { component: { type: "string" }, param: { type: "string" }, seconds: { type: "number" }, value: {} }),
      CLIP_REQ.concat(["component", "param", "seconds", "value"])),
    T("pr_apply_effect", "Gắn effect (tên tiếng Anh như panel Effects: 'Gaussian Blur', 'Lumetri Color', 'Warp Stabilizer', 'DeNoise'…).", ext(CLIP, { effect: { type: "string" } }), CLIP_REQ.concat(["effect"])),
    T("pr_add_transition", "Thêm transition vào đầu/cuối clip ('Cross Dissolve', 'Dip to Black', 'Constant Power' cho audio…).",
      ext(CLIP, { name: { type: "string" }, at_start: { type: "boolean" }, duration: { type: "number" } }), CLIP_REQ),
    T("pr_set_speed", "Đổi tốc độ clip (%), ripple tùy chọn.", ext(CLIP, { percent: { type: "number" }, ripple: { type: "boolean" } }), CLIP_REQ.concat(["percent"])),
    T("pr_look_at_frame", "Xuất frame tại giây trên timeline và XEM ảnh (để kiểm tra khung hình, chữ, bố cục, lỗi).", { seconds: { type: "number" } }, ["seconds"]),
    T("pr_open_sequence", "Mở sequence theo tên.", { name: { type: "string" } }, ["name"]),
    T("pr_rename_sequence", "Đổi tên sequence đang mở.", { name: { type: "string" } }, ["name"]),
    T("pr_backup_sequence", "Clone sequence đang mở thành bản backup."),
    T("pr_export", "Render/export sequence qua Adobe Media Encoder (H.264 Match Source - High bitrate mặc định).",
      { out_path: { type: "string" }, preset_path: { type: "string" }, range: { type: "string", enum: ["all", "inout", "workarea"] } }),
    T("pr_save_project", "Lưu project."),
    T("pr_menu_command", "Thực thi trực tiếp bất kỳ lệnh Premiere theo tên (menu / phím tắt), ví dụ: 'Close Gap', 'Nest...', 'Add Edit', 'Add Edit to All Tracks', 'Ripple Delete', 'Fit to frame', 'Scale to Frame Size', 'Apply Video Transition', 'Apply Audio Transition', 'Apply Default Transitions to Selection', 'Match Frame', 'Reverse Match Frame', 'Zoom to Sequence', 'Export Frame', 'Make Subclip...', 'Link', 'Group', 'Ungroup', 'Enable', 'Audio Gain...', 'Speed/Duration...', 'Scene Edit Detection...', 'Toggle Multi-Camera View'…",
      { command: { type: "string", description: "Tên chính xác của lệnh menu trong Premiere Pro" } }, ["command"]),
    T("pr_close_gap", "Đóng các khoảng trống (Close Gap) trên sequence / track đang mở."),
    T("pr_match_frame", "Match Frame (F) hoặc Reverse Match Frame (Shift+R) tại playhead.", { reverse: { type: "boolean" } }),
    T("pr_fit_to_frame", "Fit hoặc Fill clip vào khung hình sequence (Fit to frame: Shift+Z, hoặc Scale to Frame Size).",
      ext(CLIP, { fill: { type: "boolean", description: "true = fill frame (crop), false = fit to frame (letterbox/pillarbox)" } }), CLIP_REQ),
    T("pr_apply_default_transition", "Áp transition mặc định cho selection hoặc điểm cắt tại playhead (video: Ctrl+D, audio: Ctrl+Shift+D, cả hai: Shift+D).",
      { target: { type: "string", enum: ["all", "video", "audio"] } }),
    T("pr_zoom_to_sequence", "Zoom toàn bộ sequence vừa khít timeline (phím tắt \\)."),
    T("pr_toggle_track", "Bật/tắt trạng thái track: mute, solo, lock, hoặc target.",
      { type: { type: "string", enum: ["video", "audio"] }, track: { type: "integer" }, mode: { type: "string", enum: ["mute", "solo", "lock", "target"] } }, ["type", "track", "mode"]),
    T("pr_find_command", "Kiểm tra xem một lệnh Premiere có hỗ trợ trên phiên bản hiện tại không và lấy Command ID.",
      { command: { type: "string" } }, ["command"]),
    T("pr_get_project_tree", "Duyệt cây thư mục Bin và Project Item trong Premiere theo cấu trúc phân cấp đệ quy JSON.",
      { max_depth: { type: "integer", description: "Độ sâu tối đa duyệt bin (mặc định 8)" } }),

    // --- Bộ công cụ Premiere Pro MCP mở rộng ---
    // Project Management
    T("pr_create_project", "Tạo một project Premiere Pro mới tại đường dẫn file chỉ định.",
      { path: { type: "string", description: "Đường dẫn file .prproj cần tạo" } }, ["path"]),
    T("pr_open_project", "Mở một file project Premiere Pro đã có sẵn trên ổ đĩa.",
      { path: { type: "string", description: "Đường dẫn file .prproj cần mở" } }, ["path"]),
    T("pr_save_project_as", "Lưu project hiện tại sang tên hoặc vị trí mới.",
      { path: { type: "string", description: "Đường dẫn file .prproj mới" } }, ["path"]),

    // Media Management
    T("pr_import_folder", "Import toàn bộ file media (video, audio, ảnh) trong thư mục vào một bin trong project.",
      { folder_path: { type: "string", description: "Đường dẫn thư mục media" }, bin: { type: "string", description: "Tên bin trong Premiere (mặc định lấy tên thư mục)" } }, ["folder_path"]),
    T("pr_create_bin", "Tạo một bin (thư mục) mới trong project panel.",
      { name: { type: "string", description: "Tên bin cần tạo" }, parent_bin: { type: "string", description: "Đường dẫn bin cha nếu lồng nhau" } }, ["name"]),

    // Sequence Management
    T("pr_create_sequence", "Tạo sequence (timeline) mới với độ phân giải và fps tùy chọn (ví dụ: 1080x1920 30fps cho TikTok/Reels hoặc 1920x1080).",
      { name: { type: "string", description: "Tên sequence" }, width: { type: "integer", description: "Chiều rộng khung hình (1920, 1080, 3840...)" }, height: { type: "integer", description: "Chiều cao khung hình (1080, 1920, 2160...)" }, fps: { type: "number", description: "Tốc độ khung hình (24, 29.97, 30, 60...)" }, preset_path: { type: "string" } }, ["name"]),
    T("pr_duplicate_sequence", "Nhân bản (duplicate) một sequence.",
      { sequence_name: { type: "string", description: "Tên sequence cần nhân bản (để trống = sequence đang mở)" }, new_name: { type: "string", description: "Tên cho bản sao mới" } }),
    T("pr_delete_sequence", "Xóa một sequence khỏi project.",
      { sequence_name: { type: "string", description: "Tên sequence cần xóa" } }, ["sequence_name"]),

    // Effects & Transitions
    T("pr_remove_effect", "Gỡ bỏ một effect khỏi clip trên timeline.",
      ext(CLIP, { effect_name: { type: "string", description: "Tên effect cần xóa (tiếng Anh như panel Effects)" } }), CLIP_REQ.concat(["effect_name"])),

    // Audio Operations
    T("pr_adjust_audio_levels", "Điều chỉnh âm lượng/Gain của clip audio trên timeline tính bằng dB (ví dụ: -3, +6, 0).",
      { track: { type: "integer", description: "Chỉ số audio track (0 = A1)" }, index: { type: "integer", description: "Chỉ số clip trên track" }, gain_db: { type: "number", description: "Mức âm lượng tính bằng dB" } }, ["track", "index", "gain_db"]),

    // Color Correction & LUT
    T("pr_color_correct", "Chỉnh màu cơ bản Lumetri Color cho clip (exposure, contrast, highlights, shadows, saturation, temperature, tint).",
      { track: { type: "integer", description: "Chỉ số video track (0 = V1)" }, index: { type: "integer", description: "Chỉ số clip trên track" },
        adjustments: { type: "object", description: "Thông số chỉnh màu: exposure, contrast, highlights, shadows, whites, blacks, saturation, temperature, tint" } }, ["track", "index", "adjustments"]),
    T("pr_apply_lut", "Gán file Look-Up Table (.cube, .look) vào clip qua Lumetri Color.",
      { track: { type: "integer", description: "Chỉ số video track (0 = V1)" }, index: { type: "integer", description: "Chỉ số clip trên track" }, lut_path: { type: "string", description: "Đường dẫn file LUT (.cube/.look)" } }, ["track", "index", "lut_path"]),

    // Advanced Features
    T("pr_create_multicam_sequence", "Tạo multicam sequence từ danh sách clip các góc máy.",
      { name: { type: "string", description: "Tên sequence multicam" }, clip_paths: { type: "array", items: { type: "string" }, description: "Danh sách đường dẫn các góc máy" } }, ["name", "clip_paths"]),
    T("pr_create_proxy_media", "Tạo file proxy cho clip để dựng mượt hơn.",
      ext(CLIP, {}), CLIP_REQ),
    T("pr_auto_edit_to_music", "Tự động cắt ghép B-roll theo nhịp điệu (beat markers) của track nhạc.",
      { music_track: { type: "integer", description: "Track chứa nhạc (0 = A1)" }, video_bin: { type: "string", description: "Bin chứa clip B-roll cần chèn" }, pace: { type: "string", enum: ["slow", "medium", "fast"] } }),
    T("pr_stabilize_clip", "Áp dụng chống rung Warp Stabilizer cho clip bị rung lắc trên timeline.",
      { track: { type: "integer", description: "Chỉ số video track (0 = V1)" }, index: { type: "integer", description: "Chỉ số clip trên track" } }, ["track", "index"]),
    T("pr_add_audio_keyframes", "Thêm keyframe âm lượng cho clip audio tại giây chỉ định.",
      { track: { type: "integer", description: "Chỉ số audio track (0 = A1)" }, index: { type: "integer", description: "Chỉ số clip trên track" }, seconds: { type: "number", description: "Thời điểm trên timeline (giây)" }, gain_db: { type: "number", description: "Mức âm lượng (dB)" } }, ["track", "index", "seconds", "gain_db"]),
    T("pr_mute_track", "Bật hoặc tắt trạng thái Mute (tắt tiếng) của toàn bộ track audio.",
      { track: { type: "integer", description: "Chỉ số audio track (0 = A1)" } }, ["track"]),
    T("pr_add_transition_to_clip", "Thêm transition vào đầu hoặc cuối clip cụ thể trên timeline.",
      ext(CLIP, { name: { type: "string" }, at_start: { type: "boolean" }, duration: { type: "number" } }), CLIP_REQ),
    T("pr_list_sequences", "Liệt kê danh sách tất cả các sequence trong project với thông số chi tiết (tên, ID, kích thước WxH, fps, duration, số track)."),
    T("pr_list_sequence_tracks", "Liệt kê tất cả các track video và audio cùng danh sách clip trên từng track trong sequence.",
      { sequence_name: { type: "string", description: "Tên sequence (để trống = sequence hiện tại)" } }),

    T("pr_run_extendscript", "Chạy ExtendScript tùy ý trong Premiere (app, qe) cho tính năng chưa có tool riêng. Trả về giá trị biểu thức cuối. Cú pháp ExtendScript = ES3: không let/const/arrow/template string, không Array.forEach/map/filter, key trùng từ khóa (in, default, class...) phải để trong ngoặc kép. Luôn nêu reason.",
      { code: { type: "string" }, reason: { type: "string" } }, ["code", "reason"])
  ];

  var MCP_TOOLS = {
    create_project: 1, open_project: 1, save_project: 1, save_project_as: 1,
    import_media: 1, import_folder: 1, create_bin: 1,
    create_sequence: 1, duplicate_sequence: 1, delete_sequence: 1,
    add_to_timeline: 1, remove_from_timeline: 1, move_clip: 1, trim_clip: 1, split_clip: 1,
    apply_effect: 1, remove_effect: 1, add_transition: 1, add_transition_to_clip: 1,
    adjust_audio_levels: 1, add_audio_keyframes: 1, mute_track: 1,
    color_correct: 1, apply_lut: 1,
    export_sequence: 1, export_frame: 1,
    create_multicam_sequence: 1, create_proxy_media: 1, auto_edit_to_music: 1,
    stabilize_clip: 1, speed_change: 1,
    list_project_items: 1, list_sequences: 1, list_sequence_tracks: 1, get_project_info: 1
  };

  var READONLY = {
    pr_get_project: 1, pr_get_project_info: 1, get_project: 1, get_project_info: 1,
    pr_get_project_tree: 1, pr_list_project_items: 1, list_project_items: 1,
    pr_get_sequence: 1, get_sequence: 1,
    pr_list_sequences: 1, list_sequences: 1,
    pr_list_sequence_tracks: 1, list_sequence_tracks: 1,
    pr_get_clip_components: 1, get_clip_components: 1,
    pr_look_at_frame: 1, pr_export_frame: 1, export_frame: 1, look_at_frame: 1,
    pr_set_playhead: 1, set_playhead: 1,
    pr_backup_sequence: 1, backup_sequence: 1,
    pr_zoom_to_sequence: 1, zoom_to_sequence: 1,
    pr_find_command: 1, find_command: 1
  };

  function isPremiere(n) {
    if (!n) return false;
    return /^pr_/.test(n) || !!MCP_TOOLS[n] || !!MCP_TOOLS[n.replace(/^pr_/, "")];
  }

  var SYSTEM = [
    "ĐIỀU KHIỂN PREMIERE PRO: bạn là AI agent có đầy đủ quyền thao tác trực tiếp trên Premiere Pro qua bộ công cụ MCP:",
    "- Quản lý Project: pr_create_project, pr_open_project, pr_save_project, pr_save_project_as, pr_get_project_info",
    "- Quản lý Media & Bin: pr_import_media (pr_import), pr_import_folder, pr_create_bin, pr_list_project_items (pr_get_project_tree)",
    "- Quản lý Sequence: pr_create_sequence, pr_duplicate_sequence, pr_delete_sequence, pr_list_sequences, pr_list_sequence_tracks, pr_open_sequence, pr_rename_sequence",
    "- Thao tác Timeline: pr_add_to_timeline (pr_insert_clip), pr_remove_from_timeline (pr_remove_clips, ripple), pr_move_clip, pr_trim_clip, pr_split_clip (pr_razor), pr_set_playhead, pr_set_in_out, pr_add_marker",
    "- Hiệu ứng & Chuyển cảnh: pr_apply_effect, pr_remove_effect, pr_add_transition, pr_add_transition_to_clip, pr_apply_default_transition",
    "- Âm thanh: pr_adjust_audio_levels (dB), pr_add_audio_keyframes, pr_mute_track, pr_toggle_track (solo/mute/lock/target)",
    "- Chỉnh màu & LUT: pr_color_correct (Lumetri), pr_apply_lut (.cube/.look)",
    "- Tính năng nâng cao: pr_stabilize_clip (chống rung Warp Stabilizer), pr_create_proxy_media, pr_auto_edit_to_music (dựng theo nhịp beat), pr_create_multicam_sequence, pr_speed_change (pr_set_speed)",
    "- Xuất & Render: pr_export_sequence (pr_export qua AME), pr_export_frame / pr_look_at_frame (kiểm tra khung hình)",
    "- Lệnh menu Premiere: pr_menu_command, pr_close_gap, pr_match_frame, pr_fit_to_frame, pr_zoom_to_sequence",
    "- ExtendScript can thiệp sâu: pr_run_extendscript (cú pháp ES3)",
    "- Trước khi sửa timeline: gọi pr_get_sequence để lấy đúng track/index; sau mỗi thao tác làm đổi chỉ số (cắt, xóa, chèn) phải gọi lại.",
    "- Thời gian tính bằng GIÂY trên timeline sequence."
  ].join("\n");

  // ---------------------------------------------------------------- execution
  function call(NX, fn, args) {
    var expr = fn + "(" + (args || []).map(function (a) { return a === undefined || a === null ? "null" : JSON.stringify(a); }).join(",") + ")";
    return NX.evalHost(expr).then(function (r) {
      var j; try { j = JSON.parse(r); } catch (e) { throw new Error("Premiere trả về không hợp lệ: " + String(r).slice(0, 200)); }
      if (j && j.ok === false) throw new Error(j.error);
      return j;
    });
  }
  function hhmm() { var d = new Date(), p = function (n) { return (n < 10 ? "0" : "") + n; }; return p(d.getHours()) + p(d.getMinutes()); }
  function shrinkImage(file, maxSide) {
    return new Promise(function (resolve, reject) {
      var img = new Image();
      img.onload = function () {
        var k = Math.min(1, (maxSide || 768) / Math.max(img.width, img.height));
        var c = document.createElement("canvas"); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        var b64 = c.toDataURL("image/jpeg", 0.85).split(",")[1];
        c.width = 0; c.height = 0; // free buffer
        img.src = "";
        resolve(b64);
      };
      img.onerror = function () { reject(new Error("không đọc được ảnh frame")); };
      var u; try { u = require("url").pathToFileURL(file).href; } catch (e) { u = "file:///" + String(file).replace(/\\/g, "/"); }
      img.src = u + "?t=" + Date.now();
    });
  }
  function waitFile(p, ms) {
    return new Promise(function (resolve) {
      var t0 = Date.now();
      (function poll() { if (fs.existsSync(p) && fs.statSync(p).size > 0) return resolve(true); if (Date.now() - t0 > ms) return resolve(false); setTimeout(poll, 200); })();
    });
  }
  /* H.264 "Match Source - High bitrate" preset: remembered in settings; otherwise found by an ASYNC walk of the Adobe
     folder (the old synchronous walk froze the panel for seconds) — one walk per session, even when nothing is found */
  var presetP = null;
  function findPreset(NX) {
    if (NX.S.exportPreset && fs.existsSync(NX.S.exportPreset)) return Promise.resolve(NX.S.exportPreset);
    if (presetP) return presetP;
    var roots = process.platform === "darwin" ? ["/Applications"] : [(process.env.ProgramFiles || "C:/Program Files").replace(/\\/g, "/") + "/Adobe"];
    var hit = null;
    function list(d) { return new Promise(function (res) { fs.readdir(d, { withFileTypes: true }, function (e, items) { res(e ? [] : items); }); }); }
    function walk(d, depth) {
      if (hit || depth > 6) return Promise.resolve();
      return list(d).then(function (items) {
        var dirs = [];
        items.forEach(function (it) {
          if (hit) return;
          var n = it.name, p = d + "/" + n;
          if (/^Match Source - High bitrate\.epr$/i.test(n) && !it.isDirectory()) { hit = p; return; }
          if (depth === 0 && !/Premiere|Media Encoder/i.test(n)) return;
          if (it.isDirectory()) dirs.push(p);
        });
        return dirs.reduce(function (pr, p) { return pr.then(function () { return walk(p, depth + 1); }); }, Promise.resolve());
      });
    }
    presetP = roots.reduce(function (pr, r) { return pr.then(function () { return walk(r, 0); }); }, Promise.resolve()).then(function () {
      if (hit) { NX.S.exportPreset = hit; NX.save(); }
      return hit;
    });
    return presetP;
  }

  /* ctx: { ask(question, options) -> Promise<string>, note(text), state: { approvedAll, backedUp } } */
  function exec(NX, name, a, ctx) {
    a = a || {};
    var mode = NX.S.autonomy || "backup", st = ctx.state;
    var gate = Promise.resolve(true);
    if ((name === "pr_run_extendscript" || name === "run_extendscript") && mode !== "auto") {
      gate = ctx.ask("AI muốn chạy ExtendScript" + (a.reason ? " — " + a.reason : "") + ":\n" + String(a.code).slice(0, 600), ["Cho chạy", "Từ chối"])
        .then(function (x) { return x === "Cho chạy"; });
    } else if (!READONLY[name] && mode === "ask" && !st.approvedAll) {
      gate = ctx.ask("AI muốn thực hiện: " + name + " " + JSON.stringify(a).slice(0, 300), ["Cho phép", "Cho phép tất cả trong lượt này", "Từ chối"])
        .then(function (x) { if (x.indexOf("tất cả") >= 0) st.approvedAll = true; return x.indexOf("Cho phép") === 0; });
    }
    return gate.then(function (ok) {
      if (!ok) return { result: "Editor từ chối thao tác này — đề xuất cách khác hoặc hỏi lại." };
      var pre = Promise.resolve();
      if (!READONLY[name] && mode === "backup" && !st.backedUp &&
          name !== "pr_open_sequence" && name !== "open_sequence" &&
          name !== "pr_import" && name !== "import_media" && name !== "pr_import_media" &&
          name !== "pr_save_project" && name !== "save_project") {
        pre = call(NX, "jesCloneSequence", ["_bak_" + hhmm()]).then(function (r) {
          st.backedUp = true; if (r.backup) ctx.note("🛟 Đã backup sequence → " + r.backup);
        }).catch(function (e) { ctx.note("⚠ Không backup được sequence: " + e.message); st.backedUp = true; });
      }
      return pre.then(function () { return run(NX, name, a); });
    }).catch(function (e) { return { result: "lỗi: " + e.message }; });
  }

  function run(NX, name, a) {
    a = a || {};
    var C = [a.type || "video", a.track || 0, a.index || 0];
    var wrap = function (p) { return p.then(function (j) { return { result: j }; }); };
    var op = name.replace(/^pr_/, "");

    switch (op) {
      // 📁 Project Management
      case "create_project":
        return wrap(call(NX, "jesCreateProject", [a.path || a.project_path || a.projPath]));
      case "open_project":
        return wrap(call(NX, "jesOpenProject", [a.path || a.project_path]));
      case "save_project":
        return wrap(call(NX, "jesSave"));
      case "save_project_as":
        return wrap(call(NX, "jesSaveProjectAs", [a.path || a.new_path]));
      case "get_project":
      case "get_project_info":
        return wrap(call(NX, "jesProject"));

      // 📂 Media Management
      case "import":
      case "import_media": {
        var paths = a.paths || (a.path ? [a.path] : []);
        return wrap(call(NX, "jesImport", [JSON.stringify(paths), a.bin || ""]));
      }
      case "import_folder":
        return wrap(call(NX, "jesImportFolder", [a.folder_path || a.path, a.bin || ""]));
      case "create_bin":
        return wrap(call(NX, "jesCreateBin", [a.name || a.bin_name, a.parent_bin || ""]));
      case "get_project_tree":
      case "list_project_items":
        return wrap(call(NX, "jesProjectTree", [a.max_depth || 8]));

      // 🎬 Sequence Management
      case "create_sequence":
        return wrap(call(NX, "jesCreateSequence", [a.name || a.sequence_name, a.width, a.height, a.fps, a.preset_path || ""]));
      case "duplicate_sequence":
        return wrap(call(NX, "jesDuplicateSequence", [a.sequence_name || a.name || "", a.new_name || ""]));
      case "delete_sequence":
        return wrap(call(NX, "jesDeleteSequence", [a.sequence_name || a.name]));
      case "list_sequences":
        return wrap(call(NX, "jesListSequences"));
      case "list_sequence_tracks":
        return wrap(call(NX, "jesListSequenceTracks", [a.sequence_name || a.name || ""]));
      case "open_sequence":
        return wrap(call(NX, "jesOpenSequence", [a.name]));
      case "rename_sequence":
        return wrap(call(NX, "jesRenameSequence", [a.name]));
      case "backup_sequence":
        return wrap(call(NX, "jesCloneSequence", ["_bak_" + hhmm()]));
      case "get_sequence":
        return wrap(call(NX, "jesSequence", [a.max_clips || 150]));

      // ⏱️ Timeline Operations
      case "insert_clip":
      case "add_to_timeline":
        return wrap(call(NX, "jesInsertClip", [
          a.path,
          a.seconds != null ? a.seconds : (a.start_time != null ? a.start_time : (a.position || 0)),
          a.video_track != null ? a.video_track : (a.track || 0),
          a.audio_track || 0,
          !!a.overwrite,
          a.in,
          a.out
        ]));
      case "remove_clips":
      case "remove_from_timeline": {
        var list = a.clips || (a.track != null && a.index != null ? [{ type: a.type || "video", track: a.track, index: a.index }] : []);
        return wrap(call(NX, "jesRemoveClips", [JSON.stringify(list), !!a.ripple]));
      }
      case "move_clip":
        return wrap(call(NX, "jesMoveClip", C.concat([a.delta_seconds != null ? a.delta_seconds : 0])));
      case "trim_clip":
        return wrap(call(NX, "jesTrimClip", C.concat([a.start, a.end])));
      case "razor":
      case "split_clip":
        return wrap(call(NX, "jesRazor", [a.seconds != null ? a.seconds : (a.time || a.split_time || 0), a.type || "all", (a.tracks || []).join(",")]));
      case "enable_clip":
        return wrap(call(NX, "jesSetClipEnabled", C.concat([!!a.enabled])));
      case "select_clip":
        return wrap(call(NX, "jesSelectClip", C.concat([!!a.selected])));
      case "set_playhead":
        return wrap(call(NX, "jesSetPlayhead", [a.seconds != null ? a.seconds : (a.time || 0)]));
      case "set_in_out":
        return wrap(call(NX, "jesSetInOut", [a.in, a.out]));
      case "add_marker":
        return wrap(call(NX, "jesAddMarker", [a.seconds, a.name, a.comment || "", a.color, a.duration]));

      // 🎨 Effects & Transitions
      case "apply_effect":
        return wrap(call(NX, "jesApplyEffect", C.concat([a.effect || a.effect_name])));
      case "remove_effect":
        return wrap(call(NX, "jesRemoveEffect", C.concat([a.effect_name || a.effect])));
      case "add_transition":
      case "add_transition_to_clip":
        return wrap(call(NX, "jesAddTransition", C.concat([a.name || a.transition_name || "", a.at_start !== false, a.duration || 0.5])));
      case "apply_default_transition":
        return wrap(call(NX, "jesApplyDefaultTransition", [a.target || "all"]));
      case "get_clip_components":
        return wrap(call(NX, "jesClipComponents", C));
      case "set_param":
        return wrap(call(NX, "jesSetParam", C.concat([a.component, a.param, JSON.stringify(a.value)])));
      case "add_keyframe":
        return wrap(call(NX, "jesAddKeyframe", C.concat([a.component, a.param, a.seconds, JSON.stringify(a.value)])));

      // 🔊 Audio Operations
      case "adjust_audio_levels":
        return wrap(call(NX, "jesAdjustAudioLevels", [a.track || 0, a.index || 0, a.gain_db != null ? a.gain_db : (a.level != null ? a.level : 0)]));
      case "add_audio_keyframes":
        return wrap(call(NX, "jesAddAudioKeyframe", [a.track || 0, a.index || 0, a.seconds != null ? a.seconds : (a.time || 0), a.gain_db != null ? a.gain_db : (a.level != null ? a.level : 0)]));
      case "mute_track":
        return wrap(call(NX, "jesToggleTrack", ["audio", a.track != null ? a.track : (a.track_index || 0), "mute"]));
      case "toggle_track":
        return wrap(call(NX, "jesToggleTrack", [a.type || "audio", a.track || 0, a.mode || "mute"]));

      // 🎛️ Color Correction
      case "color_correct":
        return wrap(call(NX, "jesColorCorrect", [a.track || 0, a.index || 0, JSON.stringify(a.adjustments || a.settings || {})]));
      case "apply_lut":
        return wrap(call(NX, "jesApplyLUT", [a.track || 0, a.index || 0, a.lut_path || a.path]));

      // 📤 Export & Rendering
      case "look_at_frame":
      case "export_frame": {
        var dir = path.join(NX.cfgDir, "frames"); try { fs.mkdirSync(dir, { recursive: true }); } catch (e) {}
        var out = (a.out_path || path.join(dir, "frame_" + Date.now() + ".png")).replace(/\\/g, "/");
        var sec = a.seconds != null ? a.seconds : (a.time || 0);
        return call(NX, "jesExportFrame", [sec, out]).then(function (r) {
          return waitFile(out, 5000).then(function (ok) {
            if (!ok) throw new Error("Premiere chưa xuất được frame");
            return shrinkImage(out, 768).then(function (b64) {
              return { result: { ok: true, seconds: sec, path: out, timecode: r.timecode, note: "ảnh đính kèm" }, images: [{ mime: "image/jpeg", b64: b64 }] };
            });
          });
        });
      }
      case "export":
      case "export_sequence": {
        var preset;
        return (a.preset_path ? Promise.resolve(a.preset_path) : findPreset(NX)).then(function (pp) {
          preset = pp;
          if (!preset) throw new Error("không tìm thấy preset H.264 — truyền preset_path (.epr)");
          return call(NX, "jesProject");
        }).then(function (p) {
          var outp = a.out_path || (NX.S.outRoot + "/exports/" + String(p.active || "sequence").replace(/[\\\/:*?"<>|]/g, "_") + "_" + Date.now() + ".mp4");
          try { fs.mkdirSync(path.dirname(outp), { recursive: true }); } catch (e) {}
          var range = { all: 0, inout: 1, workarea: 2 }[a.range || "all"];
          return call(NX, "jesExport", [outp, preset, range]).then(function (j) { return { result: j }; });
        });
      }

      // 🎥 Advanced Features
      case "create_multicam_sequence":
        return wrap(call(NX, "jesCreateMulticamSequence", [a.name || a.sequence_name, JSON.stringify(a.clip_paths || a.clips || [])]));
      case "create_proxy_media":
        return wrap(call(NX, "jesCreateProxyMedia", [a.type || "video", a.track || 0, a.index || 0]));
      case "auto_edit_to_music":
        return wrap(call(NX, "jesAutoEditToMusic", [a.music_track || 0, a.video_bin || "", a.pace || "medium"]));
      case "stabilize_clip":
        return wrap(call(NX, "jesStabilizeClip", [a.track || 0, a.index || 0]));
      case "set_speed":
      case "speed_change":
        return wrap(call(NX, "jesSetSpeed", C.concat([a.percent != null ? a.percent : (a.speed != null ? a.speed : 100), !!a.ripple])));

      // ⚡ Premiere Menu & Shortcuts
      case "menu_command":
        return wrap(call(NX, "jesExecuteCommand", [a.command]));
      case "close_gap":
        return wrap(call(NX, "jesCloseGap"));
      case "match_frame":
        return wrap(call(NX, "jesMatchFrame", [!!a.reverse]));
      case "fit_to_frame":
        return wrap(call(NX, "jesFitToFrame", C.concat([!!a.fill])));
      case "zoom_to_sequence":
        return wrap(call(NX, "jesZoomSequence"));
      case "find_command":
        return wrap(call(NX, "jesFindCommand", [a.command]));

      // 🔧 ExtendScript
      case "run_extendscript":
        return wrap(call(NX, "jesEval", [a.code]));
    }
    return Promise.resolve({ result: "tool không tồn tại: " + name });
  }

  function isPremiere(n) {
    if (!n || typeof n !== "string") return false;
    if (/^pr_/.test(n)) return true;
    var base = n.replace(/^pr_/, "");
    return !!MCP_TOOLS[base] || !!READONLY[base] || [
      "close_gap", "match_frame", "fit_to_frame", "zoom_to_sequence", "find_command",
      "menu_command", "run_extendscript", "look_at_frame", "insert_clip", "remove_clips",
      "razor", "enable_clip", "select_clip", "set_playhead", "set_in_out", "add_marker",
      "set_param", "add_keyframe", "set_speed", "get_clip_components", "get_sequence",
      "get_project", "get_project_tree", "open_sequence", "rename_sequence", "backup_sequence",
      "toggle_track"
    ].indexOf(base) >= 0;
  }

  window.JESPR = {
    TOOLS: TOOLS,
    SYSTEM: SYSTEM,
    READONLY: READONLY,
    exec: exec,
    isPremiere: isPremiere
  };
})();

