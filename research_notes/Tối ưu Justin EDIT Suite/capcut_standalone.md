# CapCut desktop draft generation & shipping Justin EDIT Suite as a standalone Windows app

Research date: 2026-10-11. Scope: CapCut/JianYing draft format and open-source writers, encryption/version/ToS risk, desktop shell choice for reusing the CEP panel, and other NLE interchange formats. Most draft-format knowledge comes from community tools rather than official documentation (ByteDance publishes no draft spec). Treat it as reverse-engineered and version-fragile.

## 1. Open-source CapCut/JianYing draft writers: versions, licenses, keyframes, text, transitions, fades, templates, breaking changes

### Takeaway
pyJianYingDraft (Apache-2.0) is the reference implementation for draft JSON semantics: keyframe property names, fades, transitions, text and templates. Its CapCut-international sibling pyCapCut is still mid-migration. capcut-cli (MIT, Node/TS) has the best dated record of 2026 CapCut format changes, including the CapCut 8.7 Windows `Timelines/` behavior, so it is the most useful cross-check for Justin's writer. No project documents whether keyframe `time_offset` is in source time or segment time once speed ≠ 1. That needs an empirical probe on the user's CapCut 8.7.0.

### Cited Findings

**pyJianYingDraft (GuanYixuan), the JianYing/剪映 reference library**
- License: Apache-2.0. Current version 0.3.0 (described as a "大规模更新", a large update). A fix for masks on 10.8 is planned for 0.3.1 — [GitHub README](https://github.com/GuanYixuan/pyJianYingDraft)
- The feature tables cover JianYing 5.9 and 10.8. Auto-export (UI automation) works only on 剪映 6 and earlier (6.8) and not on 7+ (10.8). Template mode on 10.8 needs a custom `fallback_loader` because "draft_content.json 往往不是可直接读取的明文 JSON" (is often not directly readable plaintext JSON) — [README](https://github.com/GuanYixuan/pyJianYingDraft)
- Features: video/image segments with keyframes, masks, chroma key, background fill and 10 blend modes; stickers with keyframes; audio with fade in/out (`add_fade`), volume and volume keyframes; clip animations, effects, filters, standalone effect/filter tracks, transitions; text styling, fonts, text keyframes and animations, auto line-wrap, and `.srt` import only. Effect and filter parameters cannot be keyframed — [README](https://github.com/GuanYixuan/pyJianYingDraft)
- Warning: JianYing may fail to load effects, animations or transitions that are not cached locally, more often on 5.9. Uncached fonts may require reopening the draft — [README](https://github.com/GuanYixuan/pyJianYingDraft)
- Keyframe property enum (exact strings written to `property_type`): `KFTypePositionX` (unit = half canvas width, +right), `KFTypePositionY` (half canvas height, +up), `KFTypeRotation` (degrees clockwise), `KFTypeScaleX`/`KFTypeScaleY`, `UNIFORM_SCALE` (mutually exclusive with X/Y), `KFTypeAlpha` (1.0 = opaque, video only), `KFTypeSaturation`/`KFTypeContrast`/`KFTypeBrightness` (−1..1, video only), `KFTypeVolume` (1.0 = original, audio and video) — [keyframe.py](https://raw.githubusercontent.com/GuanYixuan/pyJianYingDraft/main/pyJianYingDraft/keyframe.py)
- Keyframe JSON shape: list = `{id, keyframe_list, material_id:"", property_type}`; each keyframe = `{curveType:"Line", graphID:"", left_control:{x:0,y:0}, right_control:{x:0,y:0}, id, time_offset, values:[v]}`. Only linear interpolation is implemented — [keyframe.py](https://raw.githubusercontent.com/GuanYixuan/pyJianYingDraft/main/pyJianYingDraft/keyframe.py)
- `time_offset` semantics: the class docstring says it is relative to the "素材起始点" (the material/segment start point), not the timeline. The README says keyframes are placed relative to the segment start. `add_keyframe` takes microseconds and does not apply `speed` or `source_timerange` — [keyframe.py](https://raw.githubusercontent.com/GuanYixuan/pyJianYingDraft/main/pyJianYingDraft/keyframe.py); [segment.py](https://raw.githubusercontent.com/GuanYixuan/pyJianYingDraft/main/pyJianYingDraft/segment.py); [README](https://github.com/GuanYixuan/pyJianYingDraft)
- Audio fades are exported as a separate `audio_fade` material `{in_duration, out_duration, fade_type:0}` in microseconds — [segment.py](https://raw.githubusercontent.com/GuanYixuan/pyJianYingDraft/main/pyJianYingDraft/segment.py)
- Transitions are attached to the earlier of the two clips: "注意转场添加在'前一个'视频片段上" (a transition is added on the *preceding* video segment). Text segments go on their own track, here named "caption" — [demo.py](https://raw.githubusercontent.com/GuanYixuan/pyJianYingDraft/main/demo.py)
- Track types: video, audio, effect, filter, sticker, text — [track.py](https://raw.githubusercontent.com/GuanYixuan/pyJianYingDraft/main/pyJianYingDraft/track.py)
- Template mode can replace materials by name or segment, replace text while keeping its formatting, and import tracks from a template. Imported tracks cannot take new segments, transitions, fades or effects — [README](https://github.com/GuanYixuan/pyJianYingDraft)
- Open PR #198 (2026-09-10, JianYing Pro 11.4.0 macOS): since 6.0 the main file is `draft_info.json`. A tool-written `draft_content.json` triggers "草稿内容已损坏" (draft content corrupted) because `root_meta_info.json` registers `draft_info.json`. 11.4.0 also silently drops `materials.masks` in favour of `common_mask`. No maintainer review shown — [PR #198](https://github.com/GuanYixuan/pyJianYingDraft/issues/198)

**pyCapCut (GuanYixuan), the CapCut-international port**
- Status: "正在从PyJianYingDraft进行迁移" (being migrated from pyJianYingDraft). The template and video/image modules are done; batch export is still being migrated. The README states no license — [GitHub](https://github.com/GuanYixuan/pyCapCut)
- Keyframes: alpha, position, rotation, scale, volume, saturation. Text and sticker keyframes cover only position and size. Transitions have adjustable duration. Audio fades exist for audio and video clips. Volume keyframes are supported. Text and sticker features include fonts, outline, background, shadow, bubble/flower effects and auto-wrap. SRT is the only subtitle import. Blend-mode enum is for "CapCut 9.5.0.4050". Warning: "并非所有在枚举类中的特效均可用" (not every effect in the enums is actually available). Curve speed is not supported — [GitHub](https://github.com/GuanYixuan/pyCapCut)

**VectCutAPI (sun-guannan; formerly CapCutAPI)**
- Apache-2.0, about 2.3k stars. Exposes an HTTP API and MCP server for JianYing or CapCut International. Python 3.10+ and FFmpeg required — [GitHub](https://github.com/sun-guannan/VectCutAPI)
- Placement: `save_draft` writes a `dfd_*` folder next to `capcut_server.py`, and the user copies it into the drafts directory by hand. The README does not mention `root_meta_info`. `draft_profile` options: `capcut_legacy`, `jianying_legacy`, `jianying_pro_10` (Windows JianYing Pro 10.x layout). The README does not discuss encryption — [GitHub](https://github.com/sun-guannan/VectCutAPI)
- Features: transitions, effects, filters, masks, multi-style text with shadows and backgrounds, SRT subtitles, stickers, and keyframes with easing. The MCP agent, web client and cloud rendering are not open source — [GitHub](https://github.com/sun-guannan/VectCutAPI)
- Its keyframe API takes a time point in seconds and passes it unchanged to `track.add_pending_keyframe`. The conversion to `time_offset` happens inside its vendored pyJianYingDraft code, which I could not inspect — [add_video_keyframe_impl.py](https://raw.githubusercontent.com/sun-guannan/VectCutAPI/main/add_video_keyframe_impl.py)
- Separate fork: tginart/CapCutAPI — [GitHub](https://github.com/tginart/CapCutAPI)

**capcut-mate (Hommy-master; "开源剪映小助手", open-source JianYing assistant)**
- Apache-2.0. A FastAPI service (Python 3.11+, uv) with `create_draft`, `save_draft`, `get_draft`, `gen_video` (cloud render), `add_keyframes` (position, scale, rotation), `add_mask_keyframes`, `add_captions` with keyword highlighting, `add_audios` (volume plus fade in/out, no volume keyframes), `add_effects`, and `add_images` with transitions. Supported app versions are not stated — [GitHub](https://github.com/Hommy-master/capcut-mate)
- One Chinese doc variant converts a 0–1 ratio into `time_offset = offset × segment_duration`. That puts time_offset in segment (target) time, at least at speed 1 — [CSDN article](https://adg.csdn.net/696f5614437a6b40336a0d3b.html)

**JianyingDraft.PY / JianYingProDraft lineage**
- notinmood/JianyingDraft.PY is a Python rework of xiaoyiv/JianYingProDraft, which had gone unmaintained. Its principle: write only `draft_content.json` and `draft_meta_info.json`, and JianYing fills in the remaining files when it opens the draft. This applies to old, pre-6.0 JianYing — [GitHub](https://github.com/notinmood/JianyingDraft.PY); [PyPI](https://pypi.org/project/JianYingDraft.PY/1.0.2)

**capcut-cli (renezander030, MIT, Node ≥ 18 + TS, Python wrapper `pip install capcut`). Best dated record of 2026 changes**
- Design is "JSON in, JSON out", local only. Edits are schema-validated, written atomically, and backed up to `.bak`. To compile into an existing project you create an empty project in CapCut and quit CapCut first. Versions ≤ 0.17.x had security bugs (command injection, unsafe temp files) that were fixed in 0.17.1 and 0.18.0 — [GitHub](https://github.com/renezander030/capcut-cli)
- Version matrix (`docs/version-support.md`). CapCut 6.2.8 is fixture-tested. 8.4.0 (macOS, 2026-08-31): the app built `Timelines/project.json` and `Timelines/<id>/` itself from root files. **8.7.0 Windows (build 8.7.0.3685, fixture 2026-10-02): nested `Timelines/` content wins on open/close.** 8.7 Windows (2026-09-10): drafts from a bundled 6.5.0 template were refused as "from an unusual path", while a template captured from the installed app round-tripped. 9.2.8-beta4 (macOS, 2026-08-23): the app reads `Timelines/<main_timeline_id>/draft_info.json` and root-only writes are a "silent no-op". 10.x has reports of "内容已损坏" rejections and no fixture. The write guard refuses CapCut > 9.x and `version` > 360000 — [version-support.md](https://raw.githubusercontent.com/renezander030/capcut-cli/master/docs/version-support.md)
- Changelog facts relevant to Justin's writer, dated 2026 — [CHANGELOG](https://raw.githubusercontent.com/renezander030/capcut-cli/master/CHANGELOG.md):
  - 0.18.0 (08-09): the "Current project is from an unusual path and cannot be used currently" error came from a template declaring `app_version 6.5.0` with none of the modern schema markers. CapCut snaps every duration to the project frame grid the first time it opens a draft ("866ms and 867ms both land on 26 frames"). On 8.5.0 the nested and root documents were byte-identical.
  - 0.19.0 (08-14): `UNIFORM_SCALE` is the one keyframe property that breaks the `KFType…` naming pattern. A keyframe built with the wrong name was "silently ignored by the app".
  - 0.19.1 (08-16): text `content.styles[].range` uses **UTF-16 code units**, not bytes. Measured on 211 text materials from CapCut International 7.9.0 and 8.9.1.
  - 0.21.0 (08-28): on macOS 9.2.8 the nested `Timelines/<main_timeline_id>/draft_info.json` is authoritative and root-file writes are "silently discarded". `Timelines/project.json` is the pointer that names the active timeline.
  - 0.22.0 (09-04): newer builds decide which media is imported from `draft_meta_info.json`'s `draft_materials`. Schema docs list a `"Hold"` curveType, but no app-authored draft carries it.
  - 0.23.0 (09-11): `draft_content.json` is the registered identity file on CapCut. JianYing 5.9+ and CapCut 9.3 resolve a local clip through the material's `local_material_id`, and a blank `local_material_id` is unresolvable ([JmsLdrn/capcut-mcp#1](https://github.com/JmsLdrn/capcut-mcp/issues/1)). In one case CapCut clamped the in-point to zero.
  - 0.26.1 / 0.28.0 (10-03): on the CapCut 8.7.0 Windows layout, reads follow the validated `Timelines/project.json` active pointer. A patched-app round trip is still pending.
- Fork capcut-cli-david (fixtures dated 2026-05-11): Windows CapCut 8.5.0 → `new_version 167.0.0`; 8.6.0 → `169.0.0`; integer `version: 360000` for all 8.x — [COMPATIBILITY.md](https://cdn.jsdelivr.net/npm/capcut-cli-david@2.5.0/COMPATIBILITY.md). Emitter differences: the CapCut UI routes filters through `materials.effects[]` on a `"filter"` track, uses `extra_material_refs` for transitions and masks, uses `materials.common_mask[]`, and omits `loudnesses` refs on unmodified clips. `common_keyframes` and `keyframe_graph_list[]` (FreeCurveInOut) are used. The top-level `keyframes` 8-bucket holder is always empty — [05-version-differences.md](https://cdn.jsdelivr.net/npm/capcut-cli-david@2.5.0/docs/draft-schema/05-version-differences.md)
- Text materials: `font_path` is an absolute path, and CapCut tolerates a missing font by falling back to its default. Resource catalogues are regional, so a JianYing draft references effect IDs CapCut can't resolve and vice versa — [COMPATIBILITY.md](https://cdn.jsdelivr.net/npm/capcut-cli-david@2.5.0/COMPATIBILITY.md)
- Root/nested sync (fork 1.14.1): CapCut writes `Timelines/<guid>/draft_content.json` only after it has opened the draft. If the guid's `draft_info.json` is stale (video-only), CapCut shows that state and on close "rewrites the root `draft_content.json` back to video-only". The fix is to reconcile both guid files to the root before opening — [release notes 1.14.1](https://cdn.jsdelivr.net/npm/capcut-cli-david@2.5.0/release-notes/1.14.1.md)

**Other**
- ChatCut documents a desktop-only handoff that writes an editable CapCut/Jianying Pro draft, which confirms that drafts are being used commercially as an interchange target — [ChatCut docs](https://chatcut.io/docs/export-capcut-jianying)

### Inferences
- Justin's own template approach (cloned from the user's own CapCut 8.7.0 drafts, `new_version 171.0.0`) avoids the main "unusual path" failure mode, which came from stale template markers. Re-capture the templates whenever CapCut updates, and do not hard-code `app_version`/`new_version`.
- Write identical content to the root `draft_content.json` **and** to every file under `Timelines/<id>/` (`draft_content.json`, plus `draft_info.json` or `template-2.tmp` if the template has them), and keep `Timelines/project.json` pointing at that id. On 8.7.x the nested copy wins and a stale nested copy overwrites the root on close.
- Emit times already snapped to the frame grid, e.g. `round(frames × 1e6 / fps)` with the same rounding CapCut uses, so CapCut's first-open snap does not shift cut points by ±1 frame relative to the FCP7 XML.
- Text styles: compute `styles[].range` in UTF-16 code units. This matters for Vietnamese text with combining marks and for emoji.
- Use exactly `UNIFORM_SCALE`, not `KFTypeUniformScale`. CapCut ignores keyframes with unknown property names without any error.
- Transitions and effects: reference only resource IDs that already exist in the user's CapCut 8.7 cache, i.e. copy the exact `materials.transitions[]` entries from the user's own drafts. Uncached or regional IDs may fail to load (pyJianYingDraft warning).
- `time_offset` hypothesis: offset from the segment start, in microseconds. Every library writes it without speed scaling, and the ratio-based variant multiplies by the segment (target) duration. At speed 1 source-relative and target-relative coincide, so that case is safe; with speed ≠ 1 or a non-zero source in-point, the answer is untested.

### Gaps
- No source states whether `time_offset` is source time or target time when `speed ≠ 1`, or whether it is measured from `source_timerange.start` or from 0. Suggested probe in CapCut 8.7.0: one clip with source in-point 5 s and speed 2×, alpha keyframes added in the UI at playhead positions +1 s and +3 s into the segment. Inspect `common_keyframes[].keyframe_list[].time_offset`: values of 1e6/3e6 mean target time, 2e6/6e6 mean source-relative.
- I found no source for the exact schema of subtitle text materials beyond styles/ranges, font_path and SRT import. The best reference is the user's own 8.7 drafts.
- pyCapCut license is unstated. I could not inspect VectCutAPI's internal `add_pending_keyframe` conversion.

## 2. Draft encryption: JianYing 6.x vs CapCut desktop, status as of 2026

### Takeaway
Encryption affects **JianYing/剪映 6.0+** (AES-encrypted `draft_info.json`/`draft_content.json`), not CapCut International, as of September 2026. Even on JianYing it blocks reading app-authored drafts, not writing new ones: JianYing 11.4 accepts a plaintext `draft_info.json` and encrypts it in place. CapCut international could adopt the same scheme at any time, so Justin should detect encryption and fail gracefully.

### Cited Findings
- A developer reported in June 2024 that after upgrading to 剪映 6.0 the formerly plaintext draft JSON was "編碼/加密" (encoded/encrypted) — [iT邦幫忙](https://ithelp.ithome.com.tw/questions/10215955)
- capcut-cli: JianYing 6.0+ "stores `draft_content.json` as an AES-encrypted binary payload". 5.9.x is the "last widely-used plaintext line". Keys and algorithm "have shifted across JianYing point releases". CapCut International is "**not** encrypted — only JianYing". Policy is "detect, do not decrypt". Detection heuristic: a file that does not start with `{` and does not parse is treated as encrypted. Evidence is as of 2026-09 — [jianying-encryption.md](https://raw.githubusercontent.com/renezander030/capcut-cli/master/docs/jianying-encryption.md)
- Workarounds in order of preference: (1) pin JianYing to 5.9.x and block auto-update; (2) use CapCut International; (3) community decryption references (pyJianYingDraft #142, duoec/duo-video), which are out of scope for that tool — [jianying-encryption.md](https://raw.githubusercontent.com/renezander030/capcut-cli/master/docs/jianying-encryption.md)
- JianYing Pro 11.4.0 (macOS, 2026-09-10): "plaintext is enough". JianYing detects plaintext vs ciphertext, accepts plaintext, converts it to the encrypted format, and adds `Timelines/` and a cover. The encryption note in pyJianYingDraft's README affects reading templates, not generating drafts — [pyJianYingDraft PR #198](https://github.com/GuanYixuan/pyJianYingDraft/issues/198)
- pyJianYingDraft keeps template mode on new JianYing only via a user-supplied `fallback_loader` decryptor — [README](https://github.com/GuanYixuan/pyJianYingDraft)
- capcut-cli-david: "We will **not** ship a decryption layer". Its 2026-05 fixtures put CapCut 9.x as "hypothetical, not yet released" on Windows — [COMPATIBILITY.md](https://cdn.jsdelivr.net/npm/capcut-cli-david@2.5.0/COMPATIBILITY.md)
- A Windows tool "jy-draftc" claims to decrypt and re-encrypt JianYing `draft_content.json`/`draft_meta_info.json`. It is unverified and its trustworthiness is unknown — [wingetly listing](https://wingetly.io/apps/wenshui330/jy-draftc)
- CapCut 10.x has unverified reports of tool-written drafts rejected as "内容已损坏". capcut-cli guards writes for CapCut > 9.x — [version-support.md](https://raw.githubusercontent.com/renezander030/capcut-cli/master/docs/version-support.md)

### Inferences
- For CapCut International 8.7.0 on Windows, plain JSON writing is currently safe. Risk grows with major versions, since 9.x/10.x on macOS already show layout changes.
- If CapCut International adopts JianYing-style encryption, the JianYing 11.4 behavior (plaintext accepted, then encrypted in place) suggests writing would still work, but reading the user's own drafts as templates would break. Mitigation: ship versioned template snapshots inside Justin, captured as plaintext, rather than reading templates live from the CapCut folder each time.
- Add a startup check: read the CapCut version (exe/product version or `draft_meta_info`), warn when it is outside a tested range, and refuse or offer to "write anyway" for untested majors.

### Gaps
- No source mentions `crypto_key_store.dat`. Searches in English and Chinese returned nothing relevant, so its role is unconfirmed.
- I found no official ByteDance statement about draft encryption, and no source that dates when CapCut International might adopt it.
- I found no source on whether CapCut International blocks or allows downgrades (pinning a version) on Windows.

## 3. How CapCut discovers drafts; safe registration practices

### Takeaway
CapCut lists drafts from `com.lveditor.draft\root_meta_info.json` (the root index) and relies on each draft's `draft_meta_info.json` sidecar. The registered file name must match a file that exists on disk. Safe practice: CapCut fully closed, atomic writes with backups, consistent root and `Timelines/` copies, and a full CapCut restart afterwards.

### Cited Findings
- Windows path: `%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft\<project>\`. macOS path: `~/Movies/CapCut/User Data/Projects/com.lveditor.draft` — [capcut-mcp (glama)](https://glama.ai/mcp/servers/d4tuwki262); confirmed on Windows by [capcut-cli-david COMPATIBILITY.md](https://cdn.jsdelivr.net/npm/capcut-cli-david@2.5.0/COMPATIBILITY.md)
- A draft needs `draft_meta_info.json` next to its content file to be listed and indexed. Without it the draft is hidden from the UI and `register` fails. The canonical sidecar has about 40 fields (`draft_id`, `draft_name` = folder name, `draft_root_path` = parent, `tm_*` in µs). capcut-cli touches `root_meta_info.json` only on an opt-in `--register` — [release notes 1.11.0](https://cdn.jsdelivr.net/npm/capcut-cli-david@2.5.0/release-notes/1.11.0.md)
- After generating, fully quit and reopen CapCut so the home page reloads `root_meta_info.json`. A draft missing from the home page usually lacks a root-index entry — [skill doc via search summary](https://skillselion.com/skills/binggandata/bggg-skills/bggg-tiktok-capcut)
- JianYing: `root_meta_info.json` registers `draft_info.json`, so a mismatched file name gives "草稿内容已损坏". A later fork commit (aoguai, 2026-09-21) makes the registered `draft_json_file` follow the actual save path — [pyJianYingDraft PR #198](https://github.com/GuanYixuan/pyJianYingDraft/issues/198)
- On CapCut, `draft_content.json` is the registered identity file that `register` reads first. Newer builds decide imported media from `draft_meta_info.json` → `draft_materials` — [capcut-cli CHANGELOG 0.22.0/0.23.0](https://raw.githubusercontent.com/renezander030/capcut-cli/master/CHANGELOG.md)
- Don't edit while CapCut has the draft open: the app holds the open draft in memory and writes it back on exit, discarding external changes. capcut-cli refuses to save while the app runs (`refused [editor-open]`) — [capcut-cli CHANGELOG 0.21.0](https://raw.githubusercontent.com/renezander030/capcut-cli/master/CHANGELOG.md); [search summary of capcut-cli README](https://cdn.jsdelivr.net/npm/capcut-cli@0.9.0/README.md)
- capcut-cli commits registration and timeline backups together. It recommends reopening, saving and reopening the project in CapCut to confirm edits persist — [capcut-cli README](https://github.com/renezander030/capcut-cli)
- VectCutAPI does not register drafts. It writes a `dfd_*` folder and leaves copying into the drafts directory to the user — [VectCutAPI README](https://github.com/sun-guannan/VectCutAPI)
- The old JianYing approach was to write only `draft_content.json` + `draft_meta_info.json` and let the app fill the rest. CapCut 8.4 likewise builds `Timelines/` itself from root files — [JianyingDraft.PY](https://github.com/notinmood/JianyingDraft.PY); [version-support.md](https://raw.githubusercontent.com/renezander030/capcut-cli/master/docs/version-support.md)

### Inferences
Recommended registration protocol for Justin:
1. Check that `CapCut.exe` is not running (process check) and refuse or prompt otherwise.
2. Back up `root_meta_info.json` to a timestamped `.bak`.
3. Write the draft folder to a temp folder in the same drafts root, then rename it into place.
4. Append one entry to `root_meta_info.json`'s draft list by cloning a real 8.7 entry, setting a fresh `draft_id`, path fields (Windows backslashes) and `draft_json_file` pointing at a file that exists. Write it atomically (temp file + rename) and never rewrite other entries.
5. Tell the user to start CapCut fresh.

A no-registration fallback ("open folder / import") is worth keeping in case root-index handling changes.

### Gaps
- No public source documents the full `root_meta_info.json` schema (the `all_draft_store` field names etc.). The user's own file is the only reliable reference.
- I found no source confirming whether CapCut 8.7 also scans folders without index entries.

## 4. CapCut ToS / legal considerations; official APIs or plugins

### Takeaway
CapCut's ToS (last updated 2026-04-15) prohibits reverse engineering, "decipher[ing]", automated scripts, and incorporating the Services into other products. Writing draft files that a user opens in their own CapCut is a grey zone rather than clearly sanctioned, and decrypting JianYing drafts would be riskier. There is no official public CapCut/JianYing editing API or plugin SDK.

### Cited Findings
- CapCut ToS, "Last updated: April 15, 2026". Section 6 prohibits users from: "copy, decipher, modify, adapt, translate, reverse engineer"; "reverse engineer, disassemble, decompile, or create any derivative works based on, the Services"; "use automated scripts or other technologies to collect information from or otherwise interact with the Services"; "interfere with or attempt to interfere with the proper working of the Services"; "incorporate the Services or any portion thereof into any other program or product, except as expressly permitted" — [CapCut Terms of Service](https://www.capcut.com/clause/terms-of-service)
- Same ToS, section 10: users grant CapCut "a non-exclusive, royalty-free, transferable, sub-licensable, perpetual and worldwide license to use your User Content". Commercial use of Company Content "may vary" — [CapCut Terms of Service](https://www.capcut.com/clause/terms-of-service)
- The mid-2025 ToS update (effective June 12, per CapCut's help page) drew creator backlash over the perpetual content license. CapCut called it minor updates to third-party integrations, Company Content and regional terms — [CapCut resource page](https://www.capcut.com/resource/about-capcut-terms-of-service); [Hipertextual](https://hipertextual.com/2025/06/capcut-polemica-nuevos-terminos-condiciones); [TechNews](https://technews.tw/?p=1411489)
- capcut-cli won't decrypt JianYing drafts until "a legal posture clear enough to state without hedging" exists, among other conditions — [jianying-encryption.md](https://raw.githubusercontent.com/renezander030/capcut-cli/master/docs/jianying-encryption.md)
- No official SDK: a third-party (and interested) vendor states CapCut has no native Zapier/Make integration and no official Python SDK for editing automation — [json2video](https://json2video.com/how-to/capcut-api/). Searches for an official CapCut or 剪映 open platform or editing SDK found only community draft-file projects — [search: VectCutAPI/capcut-mate articles on Aliyun](https://developer.aliyun.com/article/1731528)

### Inferences
- Lowest-risk posture for Justin:
  - Write plaintext drafts only to the user's own drafts folder, on the user's own action. This is local file interop: no automation of the CapCut UI and no network interaction with CapCut services.
  - Never ship decryption code.
  - Do not bundle CapCut-owned assets (effect packages, fonts); reference only resources already in the user's cache.
  - Name CapCut only descriptively ("export to CapCut draft") without its logos.
  - Keep FCPXML/OTIO exports as a ToS-neutral fallback.
- pyJianYingDraft's UI-automation auto-export (it takes over the mouse) is closer to the "automated scripts … interact with the Services" clause and should be avoided.
- This is not legal advice. If Justin is distributed commercially, a lawyer should review the "decipher/reverse engineer/derivative works" wording against the draft writer, especially in the user's jurisdiction.

### Gaps
- I found no court case, ByteDance statement or takedown aimed at any draft-generator repo. The main repos (pyJianYingDraft, VectCutAPI, capcut-mate, capcut-cli) remained public as of the sources read.
- I could not verify whether any enterprise or partner CapCut API exists (e.g. under Pippit/CapCut for Business). No public developer docs were found.

## 5. Desktop shell for reusing the CEP-style HTML/Node panel as a standalone Windows app

### Takeaway
**NW.js is the closest drop-in** for a CEP panel. CEP's Node integration *is* Node-Webkit: CEP 12 is listed as "Node-Webkit 0.62.1", and both use the same `--mixed-context` model where `require()` works in page scripts. NW.js 0.118.0 shipped 2026-10-10 (Node 26.7.0, Chromium 155, MIT). Electron is the more mainstream choice: better auto-update and signing tooling and a bigger ecosystem. It can emulate CEP's mixed context only by turning off its security defaults, or with a preload refactor. Tauri and Neutralino have no Node runtime, so they would need a Node sidecar and a rewrite of every in-page `require`.

### Cited Findings
**CEP baseline**
- CEP 12: CEF/Chromium 99, Node.js 17.7.1, "CEF/Node integration: Node-Webkit 0.62.1". Node is enabled via manifest `<Parameter>--enable-nodejs</Parameter>` and `<Parameter>--mixed-context</Parameter>`. In mixed context Node globals exist in every frame. `cep_node` holds Buffer, global, process and require. Integrated Node adds `module`/`exports` globals that can break jQuery-style libraries. `require` needs absolute paths — [CEP 12 Cookbook](https://github.com/Adobe-CEP/CEP-Resources/blob/master/CEP_12.x/Documentation/CEP%2012%20HTML%20Extension%20Cookbook.md)

**NW.js**
- Mixed context (since NW.js 0.13): enabled by `"chromium-args": "--mixed-context"` in package.json. "you can use all browser and NW.js API in Node modules, and vice versa". `"main": "index.html"` makes the HTML the entry point. Browser-context `require()` resolves relative to the main HTML file — [NW.js docs: JavaScript Contexts](https://docs.nwjs.io/For%20Users/Advanced/JavaScript%20Contexts%20in%20NW.js/)
- Latest NW.js v0.118.0 (2026-10-10): Node.js v26.7.0, Chromium 155, MIT license. A packaging guide is linked. An "SDK build" flavor exists (with DevTools). No auto-update is mentioned — [nwjs/nw.js GitHub](https://github.com/nwjs/nw.js)
- v0.117.0 (late Sept 2026): Chromium 154.0.8037.58 — [nwjs-general announcement](https://groups.google.com/g/nwjs-general/c/MHNQvSIbxpc)
- Build tooling: `nw-builder` (`nwbuild --mode=build`) — [SheetJS desktop demo](https://git.sheetjs.com/sheetjs/docs.sheetjs.com/blame/commit/9cf036b3f822c53ae3bde9df32add95b74751de3/docz/docs/03-demos/03-desktop.md). `electron-to-nwjs` can run Electron apps as NW.js — [nwutils/electron-to-nwjs](https://github.com/nwutils/electron-to-nwjs)

**Electron**
- Security defaults: context isolation since 12.0.0 and sandbox since 20.0.0. Node integration for content has been off by default since 5.0.0. "Under no circumstances should you load and execute remote code with Node.js integration enabled". Run Node code only from local packaged files — [Electron security docs](https://www.electronjs.org/docs/latest/tutorial/security)
- Release cadence: 8-week majors, latest 3 supported. Electron 42 shipped 2026-05-05 with Chromium M148 and Node 24. Electron 45 is scheduled stable around 2026-10-20, when 42 reaches EOL (third-party report) — [Electron timelines](https://Electronjs.org/docs/latest/tutorial/electron-timelines); [endoflife.ai](https://endoflife.ai/article-electron-42-eol)
- Mature auto-update tooling (electron-updater, electron-builder, Forge) — [PkgPulse/dev.to 2026 guide](https://dev.to/royce_fabbd83cb268312e928/tauri-vs-electron-vs-neutralino-2026-javascript-desktop-app-decision-guide-2kaf)

**Tauri / Neutralino / WebView2**
- Tauri 2.x (`@tauri-apps/api@2.11.0`) uses the OS WebView (WebView2 on Windows) with a Rust backend and no Node at runtime. It has an official signed-update plugin. Neutralino (`neu@11.7.2`) uses the OS WebView with a built-in native API server, no Node and no comparable built-in updater — [PkgPulse/dev.to 2026 guide](https://dev.to/royce_fabbd83cb268312e928/tauri-vs-electron-vs-neutralino-2026-javascript-desktop-app-decision-guide-2kaf)
- Size and memory are directional. Typical Tauri apps are 2–10 MB vs Electron's 80–200 MB. One unverified July 2026 measurement: hello-world 3.2 MB (Tauri) vs 85 MB (Electron), idle RAM 42 MB vs 168 MB — [Tech Insider](https://tech-insider.org/tauri-vs-electron-2026/); [Better Stack](https://betterstack.com/community/guides/scaling-nodejs/tauri-vs-electron-vs-deno-vs-electrobun/)
- Tauri's official Node.js sidecar guide compiles Node with `@yao-pkg/pkg` into `src-tauri/binaries/` (`externalBin`) and calls it through the shell plugin (`Command.sidecar(...)`), passing data via argv and stdout. For long-lived work it suggests a localhost server or sockets. Embedding the Node runtime is "usually larger than a `pkg` packaged application" — [Tauri v2: Node.js as a sidecar](https://v2.tauri.app/learn/sidecar-nodejs/)

**Porting pattern**
- A CEP panel is a Chromium (CEF) browser instance. The UI ports directly, and the work is replacing `CSInterface.evalScript` calls and CEP-only APIs. Adobe's samples show standalone Node server ↔ panel communication over WebSockets — [Adobe developer blog](https://blog.developer.adobe.com/scripting-for-cc-apps-via-cep-panels-579b2934b9c6); [Adobe CEP samples](https://docsearch.algolia.com/mcp/docs/repo/adobe-cep/samples)

**Code signing / SmartScreen (applies to all shells)**
- EV certificates no longer give instant SmartScreen reputation (2024 change). Azure Artifact Signing (renamed Trusted Signing) costs about $10/month and does not give instant trust either: reputation is per file and builds through downloads. Eligibility is organizations in the USA, Canada, EU and UK, and individuals in the USA and Canada only. Others need an OV certificate ($150–300/yr). Microsoft Store installs never show SmartScreen. Some users reported fresh warnings after Microsoft's intermediate-CA migration around 2026-03-26 — [codenote.net 2026](https://codenote.net/en/posts/windows-desktop-app-code-signing-distribution-japan-2026/); [ToDesktop PSA](https://www.todesktop.com/blog/posts/windows-apps-psa-ev-certs-do-not-grant-immediate-reputation-anymore); [Microsoft code signing options](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options)
- Electron's docs still claim Artifact Signing removes SmartScreen warnings, which conflicts with Microsoft's guidance. Electron Forge and electron-builder document Artifact Signing setup — [Electron code signing](https://electronjs.org/docs/latest/tutorial/code-signing); [Electron Forge](https://www.electronforge.io/guides/code-signing/code-signing-windows)

### Inferences
- **Recommended path: NW.js now, Electron as plan B.** Justin's panel already assumes CEP mixed context (`require` in page scripts, Node `fs`/`child_process` for the Python engine). Under NW.js with `--mixed-context` that code runs nearly unchanged. Only the host shim is new: replace `CSInterface`/`evalScript` with direct calls to the Python engine instead of Premiere. NW.js is also a much newer Node (26.7) than CEP 12's 17.7.1, so check deprecated Node APIs and the `module`/`exports` global quirk.
- With Electron, the near-zero-change port means `nodeIntegration:true, contextIsolation:false, sandbox:false`. That is acceptable only because the UI is 100% local files, but it goes against Electron's guidance. The cleaner refactor is a preload script that exposes a narrow `window.justin` API (fs, spawn Python, paths). That is more work, but it also makes a later Tauri move possible.
- Tauri or Neutralino would force rewriting every in-page Node call into IPC with a sidecar. The size win mostly disappears, because the app already ships a Python engine plus models (likely hundreds of MB) and would add a pkg'd Node of about 40–80 MB (my estimate, not sourced). Not worth it for this app.
- WebView2 directly (C#/C++ host) gives the same no-Node problem plus a custom native host to write and maintain. It makes sense only if a native .NET host is wanted for other reasons.
- Auto-update for NW.js: there is no official updater, so plan a simple custom updater (download a signed zip or installer, verify SHA-256, swap the app folder on restart) or use an installer-based update (Inno Setup/NSIS). Electron has electron-updater out of the box.
- Signing: the user is likely outside the Artifact Signing eligibility regions as an individual (Vietnam is my inference from the project language). Budget for an OV certificate or Microsoft Store distribution, and expect SmartScreen warnings for early releases regardless.

### Gaps
- I found no public write-up of a commercial CEP panel ported specifically to NW.js. Evidence for the port is architectural (shared Node-Webkit lineage), not a case study.
- No authoritative cold-start time comparison NW.js vs Electron for 2026 was found.
- NW.js auto-update tooling status in 2026 was not verified.

## 6. Premiere-free interchange alternatives worth supporting alongside CapCut

### Takeaway
OTIO is now the strongest neutral target. Premiere 26.0 (Jan 2026) made OTIO import/export GA, Resolve exports OTIO since 18.5, and OTIO's Python adapters convert to and from FCP7 XML and FCPXML. FCPXML 1.10 imports into Resolve 18/19+ and Final Cut. Since Justin already generates FCP7 XML, adding OTIO via `otio-fcp-adapter` and FCPXML via `otio-fcpx-xml-adapter` is cheap.

### Cited Findings
- Premiere Pro 26.0 (January 2026) release notes: Premiere "now supports importing and exporting OpenTimelineIO (OTIO) projects". The beta (Oct 2024) carried names, cut list, source in/out, durations, start timecode, frame rate, multiple tracks, track names, linear speed and sequence markers. On import, frame size and sample rate may need manual fixing. UXP scripting does not expose OTIO/EDL/XML export — [Adobe Premiere release notes](https://helpx.adobe.com/premiere-pro/kb/fixed-issues.html); [Adobe beta thread](https://community.adobe.com/t5/premiere-pro-beta-discussions/new-in-beta-otio-import-and-export/td-p/14937493); [CC developer forum](https://forums.creativeclouddeveloper.com/t/export-apis-for-edl-omf-aaf-xml-opentimelineio/11616)
- Since after v0.16, the `OpenTimelineIO` PyPI package ships only core libraries. Adapters come from `OpenTimelineIO-Plugins`. `otio-fcp-adapter` (FCP7 XML, core plugin) and `otio-fcpx-xml-adapter` (FCPX XML, contrib; markers, transitions, linear and "fancy" speed) are both at 1.0.0. Distros package OTIO 0.18.1 (built 2025-11-10) — [otio-fcp-adapter](https://pypi.org/project/otio-fcp-adapter); [otio-fcpx-xml-adapter](https://pypi.org/project/otio-fcpx-xml-adapter); [OTIO GitHub](https://github.com/AcademySoftwareFoundation/OpenTimelineIO); [openmamba](https://openmamba.org/en/rpms/base/opentimelineio)
- DaVinci Resolve: OTIO export (File > Export > Timeline, `.otio`/`.otioz`) needs 18.5+. FCPXML import via File > Import > Timeline needs v18/19+ for FCPXML 1.10. Effects and transitions don't survive cross-NLE interchange — [OTIO wiki: AWS Picchu notes](https://github.com/AcademySoftwareFoundation/OpenTimelineIO/wiki/AWS-Picchu-Edit-OTIO-Conversion-Notes); [ToolsOnAir guide](https://toolsonair.atlassian.net/wiki/x/WIDnEQE); [KDE forum](https://forum.kde.org/viewtopic.php%3Ff=265&t=165060&start=15.html)
- Resolve scripting: an OTIO export from 18.5 Beta 3 keeps clip speed and keyframes. `timeline.Export(path, resolve.EXPORT_FCPXML)` and `mediaPool.ImportTimelineFromFile(...)` exist. A community OTIO plugin (tested on 17.4.3) noted that the API couldn't lay out clips, so only export was possible. The Python scripting API is a Studio feature for external scripting — [Blackmagic forum](https://forum.blackmagicdesign.com/viewtopic.php?p=441035); [resolve-otio](https://github.com/eric-with-a-c/resolve-otio); [Resolve 20.2 New Features Guide](https://documents.blackmagicdesign.com/SupportNotes/DaVinci_Resolve_20.2_New_Features_Guide.pdf); [davinci-resolve-mcp-free](https://github.com/emircbngl/davinci-resolve-mcp-free)
- Remotion documents exporting OTIO and states that Premiere 25.6+ imports OTIO. This conflicts with Adobe's beta-to-GA timeline (GA in 26.0) — [Remotion docs](https://www.remotion.dev/docs/export-opentimeline)

### Inferences
- Suggested output matrix for the standalone app:
  - CapCut draft: the primary target.
  - FCP7 XML: already exists; serves Premiere and older Resolve.
  - OTIO: Premiere 26+, Resolve, Kdenlive, Avid via adapters.
  - FCPXML 1.10: Final Cut Pro, Resolve 18/19+.
- Generating OTIO first, then FCP7 XML and FCPXML through the adapters, could replace hand-written XML. The FCP7 XML writer is already proven, though, so keep it as-is and add OTIO from the same internal timeline model.
- Keyframed opacity/volume, transitions and title styling will not round-trip reliably through OTIO/FCPXML. CapCut drafts stay the richest Premiere-free target for Justin's text/graphics "studio" output.

### Gaps
- I could not confirm from an official Blackmagic source that Resolve 20.x imports `.otio` natively (vs export only), or whether the free Resolve edition allows OTIO import.
- The exact first Premiere build with stable OTIO import (25.6 per Remotion vs 26.0 per Adobe) is unresolved.
