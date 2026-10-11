# Premiere Pro integration for Justin EDIT Suite: extensibility roadmap (CEP/ExtendScript vs UXP), migration, and faster, more reliable timeline building (as of Oct 2026)

Research date: 2026-10-11. Windows focus. Version and date facts come from Adobe developer docs, Adobe blogs and Adobe-hosted forums unless marked otherwise. Items older than 2025 are labelled **[OLDER]**.

Version map used below. Adobe renamed "Premiere Pro" to "Premiere" ([Hyper Brew, 31 Mar 2026](https://hyperbrew.co/blog/uxp-plugins-in-premiere-2026/)).
- 25.2 beta: UXP public beta, Dec 2024.
- 25.6: UXP GA, Nov/Dec 2025. A 25.6.6 LTS patch shipped in June 2026.
- 26.0: Jan 2026.
- 26.2: Apr 2026.
- 26.3: Jun/Jul 2026.
- 26.5: Sep 2026. 26.5.1 was out by 25 Sep 2026.

---

## Q1. Status of UXP for Premiere as of Oct 2026: releases, API coverage, performance, Node/FS, hybrid C++, distribution, CEP/ExtendScript deprecation

### Takeaway
UXP has been GA in Premiere since 25.6 (late 2025), and hybrid C++ plugins have been supported since 26.2 (Apr 2026). The Premiere UXP DOM covers project items, sequences, tracks, insert and overwrite, keyframes, markers, transcripts, AAF export and encoding. Real gaps remain: razor/split, caption-track creation, drag-and-drop, sequence in/out, and AME-free export. Bulk timeline edits of 500 or more clips still freeze or crash Premiere as of 26.5.1.

Adobe's official CEP timeline for Premiere (Sep 2026 blog):
- December 2027: no new CEP submissions to Marketplace.
- December 2028: CEP disabled by default.
- From December 2029: CEP removed.

ExtendScript is explicitly not affected by the CEP retirement, and Adobe has no removal schedule for it.

### Cited Findings
**Release history (official changelog)**
- **25.2.0 (Dec 4, 2024):** "initial public beta of UXP in Premiere Pro". Known issues at the time included Spectrum Web Components not being fully supported and command plugins not working standalone. — [Premiere UXP changelog](https://developer.adobe.com/premiere-pro/uxp/changelog/)
- **25.6.0:** "official UXP extensibility release". It came with a documentation overhaul, a new distribution section, and the API reference updated to v25.6. — [Premiere UXP changelog](https://developer.adobe.com/premiere-pro/uxp/changelog/)
- Adobe blog (URL dated Dec 2025): "UXP has officially graduated from beta and is now available in Adobe Premiere 25.6!" It requires UXP Developer Tools (UDT) v2.2.1 to load, debug and package plugins, and plugins "can now [be submitted] to the Adobe Marketplace for approval". — [Adobe Developer Blog, "UXP Arrives in Premiere"](https://blog.developer.adobe.com/en/publish/2025/12/uxp-arrives-in-premiere-a-new-era-for-plugin-development)
- The community-maintained scripting guide says: "As of November 2025, Premiere Pro has moved to extensibility based on UXP". — [Premiere Pro Scripting Guide (docsforadobe)](https://ppro-scripting.docsforadobe.dev/)
- **26.2.0:** adds UXP Hybrid Plugin support (native C++ via `.uxpaddon`), a Hybrid Plugin SDK and documentation. No new Premiere DOM APIs. — [changelog](https://developer.adobe.com/premiere-pro/uxp/changelog/); see also [Adobe blog, Apr 2026, "UXP Hybrid Plugins now available for Premiere"](https://blog.developer.adobe.com/en/publish/2026/04/uxp-hybrid-plugins-now-available-for-premiere)
- **26.3.0** — [changelog](https://developer.adobe.com/premiere-pro/uxp/changelog/)
  - New APIs:
    - `Project.createSequenceWithPresetPath`
    - `ClipProjectItem.createSubClipAction`
    - `Marker.guid`
    - `Transcript.querySupportedLanguages` and `Transcript.hasTranscript`
    - `EncoderManager.launchEncoder`, `setEmbeddedXMPEnabled`, `setSidecarXMPEnabled` and `startBatchEncode`
    - `ProjectConverter.exportAAF` with `AAFExportOptions`
    - `createSetNameAction` for audio, video and caption tracks
    - `ObjectMaskUtils`
    - `SourceMonitor.setPosition`
  - **Breaking:** "`Action` creation must now occur inside `project.lockedAccess(() => {...})`".
  - **Breaking:** `Sequence.setSelection` is now synchronous.
- **26.5.0** — [changelog](https://developer.adobe.com/premiere-pro/uxp/changelog/)
  - New APIs:
    - `Transcript.isLanguagePackAvailable` and `Transcript.transcribeClipProjectItem`
    - `C2PAService`
    - `MediaManager`, which can purge the media cache
    - `WorkAreaUtils`
    - Synchronous `Media.getStart()` and `Media.getDuration()`
    - `host.applicationPath`
  - Fixes to `importAEComps`, `Markers.getMarkers` filters and `ComponentParam.getValueAtTime`.
  - **Conflict:** the Transcript class reference lists `transcribeClipProjectItem` and `isLanguagePackAvailable` as "since 25.6". — [Transcript reference](https://developer.adobe.com/premiere-pro/uxp/ppro_reference/classes/transcript/)

**API coverage, by area**
- **Overall DOM scope:** "sequences, tracks, clips, markers, project items, and application settings". Plugins can be extended with "high-performance C++ native libraries". — [Premiere UXP overview](https://developer.adobe.com/premiere-pro/uxp/)
- **Timeline building: `SequenceEditor` (since 25.6)** — [SequenceEditor reference](https://developer.adobe.com/premiere-pro/uxp/ppro_reference/classes/sequenceeditor/)
  - Methods:
    - `createInsertProjectItemAction(projectItem, time, videoTrackIndex, audioTrackIndex, limitShift)`
    - `createOverwriteItemAction(projectItem, time, vIdx, aIdx)`
    - `createCloneTrackItemAction(...)`
    - `createRemoveItemsAction(selection, ripple, mediaType, shiftOverlapping)`
    - `insertMogrtFromPath` and `insertMogrtFromLibrary`
  - "If you pass a track index greater than the number of existing tracks, a new track will be created."
  - No speed or time-remap method is documented.
- **Keyframes: `ComponentParam` (since 25.6)** — [ComponentParam reference](https://developer.adobe.com/premiere-pro/uxp/ppro_reference/classes/componentparam/)
  - Methods:
    - `areKeyframesSupported`, `createKeyframe(value)` and `createAddKeyframeAction`
    - `createRemoveKeyframeAction` and `createRemoveKeyframeRangeAction`
    - `createSetInterpolationAtKeyframeAction`
    - `createSetTimeVaryingAction` and `createSetValueAction`
    - `getKeyframeListAsTickTimes` and `getValueAtTime`
  - Interpolation modes are untyped numbers in the docs.
- **Transcripts (Text-Based Editing data)** — [Transcript reference](https://developer.adobe.com/premiere-pro/uxp/ppro_reference/classes/transcript/)
  - `exportToJSON`, `importFromJSON` and `createImportTextSegmentsAction`, so external transcripts can be imported into a clip.
  - `transcribeClipProjectItem(clip, {language})`, plus language-pack queries.
- **Gaps raised by developers migrating from CEP** — [CEP→UXP API Gap & Issue Summary thread](https://forums.creativeclouddeveloper.com/t/premierepro-migrating-from-cep-to-uxp-community-api-gap-issue-summary/11373)
  - Aug 2025: sequence in/out actions "have no effect".
  - Aug 2025: `EncoderManager.exportSequence` fails without Adobe Media Encoder installed and exports only the full sequence.
  - Sep 15, 2025, Adobe staff: "there is no createCaptionTrack() equivalent, yet; working on it".
  - On `exportAsFinalCutProXML` and `exportAsProject`, staff said: "The plan = make sure all previously available functionality, remains available", while calling conventions "may need to be changed".
  - Jan 2026: no `app.projects` equivalent (workaround: `ProjectUtils.getProjectViewIds()`).
  - Apr 24–29, 2026: "no drag-n-drop in UXP yet". Adobe "has announced no release date".
- **Razor/split:** an open-source Premiere auto-cut panel says the cut operations it needs "are only reachable from the QE DOM". A UXP port will be needed "when Premiere's UXP API gains split and move". — [rafcopy/auto-cut-agent (finaltake-agent) README](https://github.com/rafcopy/finaltake-agent)
  - Conflict: the repo's tagline says "UXP panel + local Node server", while the README describes a CEP panel.
  - Related open forum thread: [ETA for programmatic split/razor](https://forums.creativeclouddeveloper.com/t/premiere-pro-uxp-dom-eta-for-programmatic-split-razor-on-timeline/11375) (title only seen).

**Performance and stability**
- Bulk UXP timeline edits freeze and crash. In a thread started Apr 29, 2026, a plugin places hundreds to thousands of clips, using one `lockedAccess` plus `executeTransaction` per step. — [forum thread](https://forums.creativeclouddeveloper.com/t/how-to-prevent-ui-freezes-and-crashes-during-bulk-timeline-edits-via-uxp-api/11882)
  - Symptoms: Premiere's UI freezes, the timeline only updates at the end, and with 500+ segments Premiere sometimes crashes.
  - Adobe staff (bbb_999):
    - May 26: "partially fixed" in beta.
    - Jul 14: "not completely fixed in 26.3".
    - Sep 25, 2026: the 26.5.1 memory-spike fix is unrelated.
  - The issue persisted in 26.3.2 and 26.5.1.
  - Workarounds suggested in the thread:
    - Group independent actions into one `lockedAccess` / `executeTransaction` (also gives a single undo step).
    - Serialize all API calls.
    - Minimize the number of locks.
- Batching all actions into one transaction "is faster than CEP", according to a developer in Feb 2026. Actions still execute sequentially inside Premiere, and some `create*Action` calls only work inside the lock. No timings were given. — [Improve Performance on executeTransaction](https://forums.creativeclouddeveloper.com/t/improve-performance-on-executetransaction-monitoring-progress/11695)
- A 26.3.2 data-loss bug: `createCloneTrackItemAction` with `isInsert=true` "collapses the following audio clip to zero duration" (title-level evidence). — [forum](https://forums.creativeclouddeveloper.com/t/premiere-pro-uxp-createclonetrackitemaction-with-isinsert-true-collapses-the-following-audio-clip-to-zero-duration/12077)
- Several users report `createInsertProjectItemAction` failing with "Script Action failed to execute". — [Adobe community](https://community.adobe.com/t5/premiere-pro-discussions/help-with-the-new-uxp-createinsertprojectitemaction-method-quot-script-action-failed-to-execute-quot/m-p/15242114)
- Architecture (vendor opinion, not a benchmark):
  - CEP runs Node.js plus Chromium and is "heavy"; UXP runs on Adobe's own runtime and is "lighter and faster".
  - UXP has one JavaScript context for UI and host calls.
  - — [Hyper Brew, 31 Mar 2026](https://hyperbrew.co/blog/uxp-plugins-in-premiere-2026/)

**Hybrid C++ plugins (26.2+)** — [Adobe hybrid build guide](https://developer.adobe.com/premiere-pro/uxp/plugins/hybrid-plugins/build)
- Build a `.dll` on Windows and rename it to `.uxpaddon`, placed under `win/x64`.
- The manifest needs `manifestVersion` ≥ 6, an `addon` field, the `enableAddon` permission and host `minVersion` "26.2.0".
- Load the addon in JS with `require("x.uxpaddon")`. Init and terminate run on the host main thread.
- The SDK is downloaded from an Adobe Developer Console card.

**Distribution and install** — [Install a UXP plugin](https://developer.adobe.com/premiere-pro/uxp/plugins/distribution/install)
- Plugins install from Marketplace through Creative Cloud Desktop ("Manage Plugins"), or as an independent `.ccx` (double-click).
- Enterprise deployments can install from the command line through the Unified Plugin Installer Agent (UPIA).
- Installed plugins appear under **Window > UXP Plugins** in Premiere.
- Install problems are reported. On 26.2.2/Windows, error -411 appeared, or the plugin reported success but didn't show in the menu. A reply suggested enabling Developer Mode. — [Adobe community, May 2026](https://community.adobe.com/questions-729/plugin-install-fails-with-error-code-411-1561751)

**CEP / ExtendScript deprecation (current official position)** — all from the [Adobe Developer Blog, Sep 2026](https://blog.developer.adobe.com/en/publish/2026/09/investing-in-the-future-of-creative-cloud-extensibility-uxp-comes-to-our-flagship-applications) unless noted
- Premiere status: "GA UXP plugins + Hybrid plugins available".
- "Premiere stops accepting new CEP submissions in December 2027".
- "Both [Premiere and InDesign] move to CEP-disabled-by-default in December 2028".
- "Starting December 2029, CEP will no longer be included in these apps".
- "ExtendScripts are not affected by this transition".
- Existing Marketplace CEP plugins keep working and can still be updated until removal.
- Some migrations need "UXP-supported patterns rather than a direct port"; consider Webview or Hybrid plugins.
- The scripting guide agrees: "ExtendScript-based integrations are still supported, and Adobe currently has no plan or schedule for removing ExtendScript … which will not be removed as part of any CEP removal." — [Premiere Pro Scripting Guide](https://ppro-scripting.docsforadobe.dev/)

**Superseded statements**
- **[OLDER, superseded]** Adobe's PProPanel sample ReadMe said CEP was superseded by UXP as of 25.6, and that the plan was "to support both CEP and UXP for a calendar year, after which we will remove support for CEP". — [Adobe-CEP/Samples PProPanel ReadMe](https://github.com/Adobe-CEP/Samples/blob/master/PProPanel/ReadMe.md)
- **[OLDER, superseded]** An earlier version of the scripting guide and a Mar 2026 forum post said ExtendScript support would end in September 2026. — reported via [Adobe community](https://community.adobe.com/questions-729/extendscript-to-uxp-for-premiere-pro-1553924). This contradicts the current guide and the Sep 2026 blog.
- **[OLDER]** Hyper Brew (Mar 2026) said Adobe gave only "several years" with no date. That predates the Sep 2026 blog.

**CEP in practice**
- CEP still loads in 2026 builds. Motion Bro 7.3.1 (Jul 30, 2026) and FireCut 1.2.36 still ship JSX/ZXP builds (see Q5).
- One user reported AutoSubs' CEP panel not appearing in Premiere 2026 on Windows (Jun 2026). The maintainer closed it with a commit titled "…enable CEP loa[ding]…", which suggests a loader fix rather than a UXP port. — [tmoroney/auto-subs #571](https://github.com/tmoroney/auto-subs/issues/571)

### Inferences
- For Justin EDIT Suite, CEP plus ExtendScript remains viable for roughly two more years: fully supported until Dec 2028, then needs a user opt-in until Dec 2029. ExtendScript itself has no end date.
- The binding constraint is the CEP *panel shell*, not ExtendScript. A UXP panel cannot call `evalScript` (see Q2), so a "UXP shell plus existing host.jsx" design is not a supported path.
- UXP's per-action model (create Action → `lockedAccess` → `executeTransaction`) is the closest thing to "disable UI updates and batch". However, Premiere's crash and freeze bug at 500+ clips is still open in 26.5.1. For 100–1000-clip sequences, a UXP-only build path is riskier than XML import today.
- UXP gives typed access to things ExtendScript makes awkward: keyframe interpolation, transcripts import/export, AAF export, media-cache purge, work area and sub-clip actions. These could become reasons to migrate specific features first.

### Gaps
- There is no official benchmark comparing UXP against ExtendScript for clip insertion, keyframing or XML import.
- I could not confirm from Adobe text two claims seen only in a search summary: "CEP no longer works reliably", and "at least two years from UXP public beta before CEP removal".
- No dedicated UXP speed or time-remap API was found, and no confirmed public status for a UXP razor/split API.
- Exact dates of the 25.6 GA build: the scripting guide says Nov 2025, and the Adobe blog URL is dated Dec 2025.

---

## Q2. Migrating a CEP panel to UXP: what breaks, guides, and how to call a local Python engine

### Takeaway
Migration is a rewrite of the host layer, not a port.
- `CSInterface` and `evalScript` disappear, and the UXP plugin calls the Premiere DOM directly.
- Node built-ins such as `child_process` and `http` are not available.
- File access becomes permissioned through the `storage`/`fs` modules.
- The UI runs in UXP's limited DOM, which supports Spectrum widgets and is not Chromium.

For a Python engine, the practical pattern is that Python runs as a local server and the UXP panel connects as a fetch/WebSocket client. The engine has to be started some other way, because UXP cannot spawn processes. Options:
- a hybrid C++ addon (26.2+),
- `shell.openPath` with a user consent dialog,
- a launcher or tray app started outside Premiere.

### Cited Findings
**What changes, per Adobe's CEP→UXP technical migration guide** — [Adobe CEP→UXP Technical Migration Guide](https://developer.adobe.com/uxp/migration-center/uxp-for-cep-devs/technical-migration-guide/)
- **Host scripting:** "UXP plugins call host APIs directly from the same JavaScript context … no need to use `evalScript` anymore!"
- **Manifest:** `manifest.xml` becomes `manifest.json` plus `entrypoints.setup()`.
- **File I/O:** `window.cep.fs` becomes the `storage` module (file picker, tokens) and an `fs` module ("utf-8"/"utf-16" encodings). "UXP does not allow arbitrary access to any file on the host system."
- **Process launch:** `window.cep.process` has no equivalent. "UXP does not yet have its own comprehensive set of process APIs". Only `shell.openPath` and `shell.openExternal` exist, and they need the `launchProcess` permission.
- **Network:** "UXP supports XMLHttpRequest, WebSockets, and Fetch APIs", and domains must be declared in the manifest.
- **UI:** "UXP … is not a browser, and therefore complete feature parity is unlikely". It has a DOM with flexbox, plus Spectrum UXP widgets.
- **Panel sizing:** limited to the sizes in the manifest.
- **Vulcan:** replaced by `invokeCommand`/`showPanel`, within the same host only.

**Manifest permissions for Premiere** — [Premiere UXP manifest docs](https://developer.adobe.com/premiere-pro/uxp/plugins/concepts/manifest/)
- `manifestVersion` 5. Hybrid plugins need ≥ 6, per the build guide.
- `host.app` is `"premierepro"`.
- `network.domains`: a list or `"all"`, with wildcards. The WebSocket example is `"wss://*.myplugin.com"`. Localhost is not mentioned.
- `localFileSystem`: `"plugin"`, `"request"` or `"fullAccess"`.
- `launchProcess`: `schemes` and `extensions`.
- `enableAddon` is needed for hybrid plugins.
- `webview`: `allow`, `domains`, and `enableMessageBridge: "localAndRemote"`.
- Also `clipboard` and `ipc`.
- Users are asked to consent when the plugin uses `openExternal()` or `openPath()`. Other permissions are granted at install.

**Launching external processes** — [Premiere UXP recipe: Launch External Processes](https://developer.adobe.com/premiere-pro/uxp/resources/recipes/external-process)
- By default, UXP plugins can't launch external processes.
- With `launchProcess`, `openPath()` opens a file in its default application and `openExternal()` launches an application via a URL scheme.
- Minimum: Premiere 25.6, UDT 2.2, manifest v5.
- "Whenever your plugin attempts to launch an external process, the user must provide explicit consent through a system dialog."

**Community experience calling Python from UXP**
- A Dec 16, 2025 question confirms UXP lacks `child_process` and shell access. The only reply, from a community expert rather than Adobe staff, suggested ExtendScript `File.execute()` on a batch file, or `openPath` on a .bat that reads a parameter file. — [Adobe community](https://community.adobe.com/questions-729/is-there-any-supported-way-to-execute-a-python-script-from-a-premiere-pro-uxp-panel-1621734)
- Recommended pattern: "Python would be your web socket server, and then the UXP plugin would establish a connection as a client". The hardest part is "establishing the server and communicating the port". UXP can only be a WebSocket *client*. — [forum: UXP ↔ Python](https://forums.creativeclouddeveloper.com/t/how-to-communicate-between-a-uxp-plug-in-and-a-python-module/4370)
- **[OLDER, 2025]** Hybrid plugins were then unavailable in Premiere. Once available, a C++ hybrid function could "execute CLI commands or communicate with other processes". Bolt UXP has a hybrid `execSync()` example (Photoshop/InDesign at that time). — [forum: Running .exe in UXP](https://forums.creativeclouddeveloper.com/t/running-exe-unix-executable-in-uxp/10893)
  - Now superseded: Premiere has supported hybrid plugins since 26.2. — [changelog](https://developer.adobe.com/premiere-pro/uxp/changelog/)
- Open-source Premiere MCP bridges use the same architectures:
  - LLM → Python (fastmcp) server → UXP plugin, over WebSocket. — [morim3/mcp_adobe_premiere](https://github.com/morim3/mcp_adobe_premiere)
  - Shared temp-folder plus polling, with a folder-access token. — [hetpatel-11 Adobe_Premiere_Pro_MCP bridge.js](https://glama.ai/mcp/servers/@hetpatel-11/Adobe_Premiere_Pro_MCP/blob/ab3b527f37cef2c2ae274260f727c10795e4fe83/uxp-plugin/bridge.js)
- **No CEP+UXP bridge:** a forum answer, surfaced via search and not fetched in full, states "there is no way to have a CEP/UXP hybrid". Adobe's migration guide maps `evalScript` → the UXP DOM. — [forum: UXP vs CEP](https://forums.creativeclouddeveloper.com/t/uxp-vs-cep/9643); [Adobe community question on evalScript-like API](https://community.adobe.com/questions-712/why-doesn-t-uxp-expose-a-direct-evalscript-like-api-to-run-existing-extendscript-jsx-files-1183642)
- **Adobe's guidance (Sep 2026):** where architectures differ, use Webview or Hybrid plugins. — [Adobe Developer Blog Sep 2026](https://blog.developer.adobe.com/en/publish/2026/09/investing-in-the-future-of-creative-cloud-extensibility-uxp-comes-to-our-flagship-applications)
- **Hyper Brew's migration advice:**
  - Test every API your tool needs now.
  - Report gaps on the Adobe forums.
  - Keep CEP builds for users on Premiere versions older than the UXP GA.
  - Start early.
  - Tools: Bolt UXP (Premiere target), UDT, and the [AdobeDocs/uxp-premiere-pro-samples](https://github.com/AdobeDocs/uxp-premiere-pro-samples) repo.
  - — [Hyper Brew blog](https://hyperbrew.co/blog/uxp-plugins-in-premiere-2026/)

### Inferences
**Recommended target architecture for Justin EDIT Suite (UXP era)**
1. Run the Python engine as a long-lived local service bound to 127.0.0.1, exposing HTTP for jobs and WebSocket for progress. Declare it in `network.domains`, for example `"http://127.0.0.1:PORT"` and `"ws://127.0.0.1:PORT"`. Whether the manifest accepts loopback with an explicit port is undocumented and needs testing.
2. Start the service in one of these ways:
   - (a) a small hybrid `.uxpaddon` (26.2+) calling `CreateProcessW`. This is the only in-plugin way with no per-launch consent dialog, though that is an inference from the docs.
   - (b) a Windows tray/launcher app, or a scheduled task at login.
   - (c) `shell.openPath` on a `.bat`/`.exe` file, which shows a consent dialog each time.
3. Port the panel's file I/O to `fs` with `localFileSystem: "fullAccess"`, or let Python do all file work and return paths.
4. Move rendering of the ProRes 4444 text/graphics clips from the panel's Node code into the Python engine. UXP has no Node and cannot spawn ffmpeg.

**Migration effort**
- Every function in host.jsx (1,500+ lines, many using QE DOM: `addTracks`, `getVideoEffectByName`, `exportFramePNG`) must be re-implemented against the UXP DOM.
- The QE DOM-only features are the highest risk: track adding, effect lookup by name, and razor. Track creation has a documented UXP path: pass a track index beyond the current count to `createInsertProjectItemAction`.
- A pragmatic plan is a dual build:
  - Keep the CEP panel as primary through 2027.
  - Prototype a UXP panel for Premiere 26.3+ that drives the same Python service. Only the host adapter layer differs, so this justifies factoring host calls behind one adapter interface now.

### Gaps
- There is no official Premiere-specific CEP→UXP migration guide page. The Premiere `fundamentals/cep-to-uxp` URL returned 404, so the generic UXP migration center was used.
- Not confirmed:
  - whether `network.domains` accepts `http://localhost:port`/`ws://127.0.0.1:port` without `"all"`;
  - whether a hybrid addon may spawn child processes under Adobe's Marketplace review rules;
  - whether `localFileSystem: "fullAccess"` passes Marketplace review.
- No public UXP-vs-CEP memory or startup-time numbers for Premiere.

---

## Q3. Fastest, most robust ways to build long sequences (100–1000 clips): XML vs FCPXML vs OTIO vs AAF vs ExtendScript vs UXP; speed, keyframes, audio levels; escaping

### Takeaway
One native interchange import, the FCP7 XML (xmeml) Justin EDIT Suite already generates, remains the fastest and most stable way to materialise hundreds of clips in 2026.
- Per-clip ExtendScript insertion costs roughly 0.3 s per clip and slows as the project grows.
- UXP bulk insertion still freezes or crashes at 500+ clips.

OTIO is officially supported since 26.0 but carries less: cuts, tracks, linear speed and markers. AAF is only exportable from UXP.

The robust path is to encode everything the XML importer supports in the XML, and to keep scripting only for post-processing. That means cuts, constant speed, Motion scale keyframes, audio level keyframes, markers, and the alpha-graphics clips as ordinary clipitems on upper tracks. File paths must be strictly percent-encoded `file://localhost/` URLs, with XML escaping on top.

### Cited Findings
**ExtendScript insertion cost**
- A user scripted 200 clip insertions into an empty sequence at about 60 s. A second batch of 200 took another 60 s, and insertion "gets slower" as items accumulate. Another user observed that the total number of inserted clips across the *project* matters, so creating a new sequence does not help. These are user observations, not Adobe's. — [Adobe community](https://community.adobe.com/questions-729/i-wish-ppro-had-even-better-perfs-optimization-it-is-slow-when-inserting-clips-on-filled-sequences-1419440)
- **[OLDER, 2024]** Regression in 24.2/24.2.1: `overwriteClip` behaves like `insertClip` and ripples clips. — [Adobe community, 24.2 critical bug](https://community.adobe.com/questions-729/premiere-24-2-extendscript-critical-bug-1414490)
- **[OLDER]** Adobe staff say the QE DOM is officially unsupported, because its project-model refresh is unreliable. Adobe has been moving safe QE functions to the regular DOM since CC 2013. — [Adobe community, "QE – what is this rabbithole"](https://community.adobe.com/t5/premiere-pro-discussions/qe-what-is-this-rabbithole/m-p/7645306)
- Bulk-friendly ExtendScript calls documented in the scripting guide — [Project object](https://ppro-scripting.docsforadobe.dev/general/project/); [Sequence object](https://ppro-scripting.docsforadobe.dev/sequence/sequence/)
  - `app.project.importFiles(filePaths[], suppressUI, targetBin, importAsNumberedStills)` takes an array, so one call can import many files.
  - `app.project.createNewSequenceFromClips(name, projectItems[], bin)` builds a sequence from many items in one call.
  - `app.project.importSequences(prprojPath, sequenceIDs[])`
  - `app.project.newSequence(name, sqpresetPath)`
  - `sequence.importMGT(path, ticks, vOff, aOff)`
  - `sequence.createCaptionTrack(projectItem, startSec, format)`
  - `sequence.exportAsFinalCutProXML(path)`
  - Units are inconsistent: ticks vs seconds, and the docs disagree on `overwriteClip`'s time type.

**UXP insertion** (see Q1)
- One `lockedAccess` plus one `executeTransaction` with many actions is the recommended batching. The 500+ clip freeze/crash is still unresolved in 26.5.1. — [forum](https://forums.creativeclouddeveloper.com/t/how-to-prevent-ui-freezes-and-crashes-during-bulk-timeline-edits-via-uxp-api/11882)

**OTIO in Premiere**
- The beta (Oct 2024) carried:
  - names
  - cut lists with clips in place
  - source in/out
  - durations
  - timecode
  - frame rate
  - multiple tracks and track names
  - linear speed
  - sequence markers
  
  Clip markers round-trip only Premiere→Premiere. For OTIO created outside Premiere, "some sequence settings may not transfer automatically". — [Adobe beta forum: OTIO import and export](https://community.adobe.com/t5/premiere-pro-beta-discussions/new-in-beta-otio-import-and-export/td-p/14937493)
- Official in **26.0 (Jan 2026)**: "Premiere now supports importing and exporting OpenTimelineIO (OTIO) projects". — [Adobe Premiere release notes](https://helpx.adobe.com/sea/premiere-pro/kb/fixed-issues.html)
  - Remotion's docs claim 25.6+. — [Remotion](https://www.remotion.dev/docs/export-opentimeline) (unverified; conflicts with Adobe staff saying OTIO was still beta-only in mid-2025)
- A user reported in Mar 2026 that clip names are lost on a Premiere→OTIO→Premiere round trip. — [Adobe beta forum thread](https://community.adobe.com/t5/premiere-pro-beta-discussions/now-in-beta-otio-import-and-export/m-p/15486920/highlight/true) (surfaced via search)

**What FCP XML carries into Premiere**
- Adobe's FCP7 migration page lists "motion effects, titles, some keyframes, dissolves, wipes, composite blend modes, speed variations, audio keyframes, and levels". The FCPX page makes a similar claim. — [Adobe: migrate from Final Cut Pro](https://helpx.adobe.com/si/premiere-pro/how-to/migrate-from-final-cut-pro.html); [Adobe: migrate from FCP X](https://helpx.adobe.com/il_en/premiere/desktop/organize-media/import-files/migrate-from-final-cut-pro-x.html)
- **[OLDER, 2013, reverse direction Premiere→FCP]** "time remapping will not get passed in the final cut xml". Frame blending is not in the DTD. — [Creative COW](https://creativecow.net/?p=351851)

**Paths and special characters in xmeml**
- `pathurl` values are `file://localhost/` URLs with percent-encoded segments, e.g. `file://localhost/Volumes/Macintosh%20HD/…`. Adobe staff say media must be on local, mapped-drive or UNC paths, and HTTPS URLs don't work. — [Adobe community: generate path url](https://community.adobe.com/t5/premiere-pro-discussions/generate-path-url-for-asset-in-xml-file/m-p/13301401); [Adobe community: https links in XML](https://community.adobe.com/questions-729/can-i-use-https-video-links-in-xml-to-create-a-sequence-in-premiere-pro-1416088)
- Special characters break imports:
  - `<` `>` in paths broke XML import, and renaming fixed it. — [Adobe community](https://community.adobe.com/t5/premiere-pro/import-xml-not-working/m-p/9665460)
  - "Generic error" on FCP XML import traced to filenames with special characters and punctuation. — [Adobe community](https://community.adobe.com/t5/premiere-pro-discussions/quot-generic-error-quot-when-importing-fcp-xml-files/m-p/5261915/highlight/true)
  - Non-ASCII (Norwegian Æ/Ø/Å) characters in names were mangled on XML import. Fixed in "Premiere Pro beta version 25.6.0 build 78 and later". — [Adobe bug report](https://community.adobe.com/bug-reports-728/xml-import-issue-norwegian-characters-replaced-with-symbols-and-numbers-1332004)
- General standards:
  - In XML text and attribute values, `&` must be written `&amp;` and `<` `&lt;`. — [W3C XML 1.0 §2.4](https://www.w3.org/TR/xml/#syntax)
  - In URLs, `%` must be encoded as `%25`. `#` starts a fragment, so a literal one must be `%23`. `;`, `&` and other sub-delims are reserved, so encoding them as `%3B`/`%26` is safe. — [RFC 3986 §2](https://www.rfc-editor.org/rfc/rfc3986#section-2)
- **[OLDER, 2016]** Premiere duplicates media already in the project when importing an XML that lost Premiere's metadata. The workaround is to import into a temporary project and then bring the sequence across. — [Digital Rebellion](https://www.digitalrebellion.com/blog/posts/how_to_import_sequences_into_premiere_pro_from_xml_without_duplicating_files)

**AAF**
- UXP 26.3 adds `ProjectConverter.exportAAF` (export only). — [changelog](https://developer.adobe.com/premiere-pro/uxp/changelog/)

### Inferences
**Ranking for 100–1000 clips in 2026**
1. FCP7 XML import, as now. It is a single native operation and all clips appear at once.
2. OTIO, only for plain cut lists with linear speed and markers.
3. ExtendScript `createNewSequenceFromClips` with pre-made subclips (in/out baked in), for straight cuts without XML.
4. UXP batched transaction, which is promising but still crash-prone at scale.
5. Per-clip ExtendScript `overwriteClip`, the slowest at about 0.3 s per clip and growing.

**Concrete XML hardening for Justin EDIT Suite**
- **(a) Use Premiere's exporter as ground truth.** Build a tiny sequence from files whose names contain `& # % ; ' , é Ư 中` and a space. Run `sequence.exportAsFinalCutProXML()`, then mirror exactly how Premiere encodes `pathurl` (drive letter, e.g. `C%3a`, case of hex digits, UTF-8 bytes) and `<name>`.
- **(b) Encode `pathurl` with a strict allow-list.** Percent-encode everything except unreserved characters (`A–Z a–z 0–9 - . _ ~`) and `/`, with non-ASCII as UTF-8 bytes. Then XML-escape the result, which only matters for `&` if left raw. Plain `<name>` text needs only XML escaping, not percent-encoding.
- **(c) Pre-flight and fallback.** Check `os.path.exists` on every decoded path before writing the XML. If a filename still fails, fall back to an NTFS hard link with a sanitised name in the work folder (same volume, no copy) rather than renaming user files.
- **(d) Put effects in the XML instead of the post-pass**, then verify on import:
  - constant speed;
  - scale keyframes (Basic Motion);
  - audio level keyframes (Audio Levels);
  - sequence and clip markers;
  - the ProRes 4444 graphics clips as normal clipitems with alpha on upper tracks.
  
  Keep ExtendScript only for what the importer drops, likely variable time-remap curves and Lumetri.
- **(e) Avoid duplicate master clips** by importing the XML into a dedicated bin. Optionally dedupe the project items afterwards.
- **(f) Collapse ExtendScript round-trips.** Send one JSON payload per `evalScript` and do all post-processing in a single host-side loop. ExtendScript blocks Premiere either way, but the CEP↔ExtendScript round-trip overhead multiplies with clip count.

### Gaps
- No published benchmarks of FCP7 XML vs OTIO vs AAF import time in Premiere, and no Adobe numbers for ExtendScript or UXP insertion.
- No current Adobe documentation specifying which xmeml effects survive import:
  - variable time-remap (`timeremap` graph keyframes),
  - Basic Motion scale keyframe interpolation,
  - audio-level keyframes.
  
  Needs an in-house round-trip test matrix.
- Not confirmed: Premiere's exact Windows `pathurl` form (`file://localhost/C%3a/...`), the handling of `#`/`%`/`;` in current builds, and whether OTIO import carries keyframes or audio levels. The beta notes list only linear speed and markers.
- AAF *import* behaviour and speed in 26.x were not researched.

---

## Q4. Premiere 2025–2026 built-in features that overlap or can be leveraged, and whether third-party code can call them

### Takeaway
Premiere 26.x added substantial AI tooling, including Object Mask, Generative Extend, Media Intelligence search, caption translation and an in-timeline Generative Media Tool. The only scriptable entry points found are:
- UXP: Transcript (transcribe, export/import JSON segments), ObjectMaskUtils, C2PA and encoder.
- ExtendScript: Auto Reframe, caption tracks from SRT, and scene-edit detection.

No public API was found for Generative Extend, Enhance Speech, Remix, Morph Cut or Media Intelligence search.

### Cited Findings
**Features by release**
- **26.0 (Jan 2026):** Object Mask (AI selection and tracking of objects and people), redesigned shape masks, Generative Extend (AI frames added to clip head or tail), Media Intelligence (AI search across visuals, transcripts and metadata), Translate Captions, NVIDIA Blackwell acceleration. — [Adobe: What's new in Premiere](https://helpx.adobe.com/premiere-pro/using/whats-new.html); [NewsShooter, 20 Jan 2026](https://www.newsshooter.com/2026/01/20/whats-new-in-adobe-premiere-pro-26-0/)
  - Larry Jordan's list places Media Intelligence under Media Encoder 26.0, and adds Firefly Boards import, transition handles, GPU thumbnails and native Windows ARM64. — [Larry Jordan](https://larryjordan.com/articles/latest-features-in-adobe-premiere-26-0/)
- **26.2 (Apr 2026):** Object Mask Sharp/Smooth modes and edge controls, Global Mute, Content Credentials, Sequence Index panel. — [Adobe release notes](https://helpx.adobe.com/sea/premiere-pro/kb/fixed-issues.html)
- **26.3 (Jun 2026):** Firefly Boards import, one-word captions, Shape Dissolve. — [Adobe release notes](https://helpx.adobe.com/sea/premiere-pro/kb/fixed-issues.html)
- **26.5 (Sep 2026):** a Generative Media Tool that creates clips inside the timeline; Generative Extend expanded to more source formats. — [Adobe release notes](https://helpx.adobe.com/sea/premiere-pro/kb/fixed-issues.html)

**Programmatic access**
- **UXP Transcript API** — [Transcript reference](https://developer.adobe.com/premiere-pro/uxp/ppro_reference/classes/transcript/); [changelog](https://developer.adobe.com/premiere-pro/uxp/changelog/)
  - `transcribeClipProjectItem` runs Premiere's own speech-to-text.
  - `exportToJSON` reads the Text-Based Editing transcript.
  - `importFromJSON` plus `createImportTextSegmentsAction` push an *external* transcript into the clip, so Text-Based Editing works on it.
  - `querySupportedLanguages`, `hasTranscript` (26.3) and `isLanguagePackAvailable`.
- **Other UXP 26.3–26.5 APIs:** `ObjectMaskUtils` (26.3), `C2PAService` (26.5), `MediaManager` media-cache purge (26.5), and `EncoderManager` batch encoding. — [changelog](https://developer.adobe.com/premiere-pro/uxp/changelog/)
- **ExtendScript** — [Sequence object](https://ppro-scripting.docsforadobe.dev/sequence/sequence/)
  - `sequence.autoReframeSequence(numerator, denominator, motionPreset, newName, useNestedSequences)` creates an auto-reframed copy.
  - `createCaptionTrack(projectItem, startAtTime, captionFormat)` builds captions from an SRT project item.
  - `performSceneEditDetectionOnSelection('CreateMarkers'|'ApplyCuts', …)`
- **UXP gaps:** there was no `createCaptionTrack` equivalent as of Sep 2025 (Adobe said "working on it"). — [forum gap thread](https://forums.creativeclouddeveloper.com/t/premierepro-migrating-from-cep-to-uxp-community-api-gap-issue-summary/11373)

### Inferences
- **Transcripts:** the engine already transcribes. Pushing its word-timed transcript into Premiere through UXP `createImportTextSegmentsAction` would light up Text-Based Editing and Premiere's caption tools on the cut, without Premiere re-transcribing. This is a UXP-only capability, and a concrete early reason to ship a small UXP companion panel.
- **Auto Reframe:** `autoReframeSequence` in ExtendScript could produce 9:16 versions of the built sequence natively.
- **AI tools without APIs:** Generative Extend, Enhance Speech, Remix, Morph Cut and Media Intelligence remain manual, user-invoked features. The panel can at best select clips or place markers that guide the user to them.

### Gaps
- No documentation found of any API for Generative Extend, Enhance Speech, Remix, Morph Cut, Media Intelligence search, or the Generative Media Tool.
- Whether `autoReframeSequence` still works in 26.x was not verified.
- The JSON schema for `Transcript.importFromJSON` was not retrieved.
- Release-note pages returned 403 to direct fetch. The 26.x feature lists above come from search snippets of Adobe pages plus NewsShooter and Larry Jordan.

---

## Q5. How leading third-party Premiere extensions are architected (public info only)

### Takeaway
As of Aug–Sep 2026, the visible commercial tools were still shipping CEP/ExtendScript builds. Evidence:
- AutoPod: described as CEP plus ExtendScript.
- Motion Bro 7.3.1: JSX files.
- FireCut 1.2.36: `.zxp` packages.
- AutoCut: supports Premiere 13.1+ and installs into Window > Extensions.

No evidence of their UXP versions was found. Cutback splits its workflow between a standalone "Selects" app that hands a project off to NLEs and an in-Premiere plugin. Open-source AI editors use either CEP (for QE DOM razor) or UXP plus a local Python/Node server over WebSocket.

### Cited Findings
- **AutoPod:** "built as an Adobe Premiere Pro extension using CEP panels and the ExtendScript API", for Premiere 2023+. This is a competitor's blog, so treat with caution. — [NemoVideo](https://www.nemovideo.com/alternative/autopod)
- **Motion Bro 7.3.1 (Jul 30, 2026):** added a "Fix Transition Timing" panel for Premiere. The changelog mentions automatic recovery for missing or damaged **JSX** files. The Exchange listing shows compatibility "Premiere 15.0 or higher". — [Motion Bro updates](https://motionbro.com/category/blog/updates/); [Adobe Exchange: Motion Bro](https://exchange.adobe.com/apps/cc/105006/motion-bro)
- **FireCut v1.2.36:** latest feature "Add Graphics brings AI motion graphics to Premiere Pro (experimental)". Each version is offered as a `.zxp` (CEP package). — [FireCut downloads](https://firecut.ai/downloads/premiere-pro/old/)
- **Premiere Composer 2.1.12 (12 Aug 2026):** "Requires Premiere Pro 2025 or newer". The changelog refers to MOGRT files and team projects. Platform not stated. — [Mister Horse changelog](https://misterhorse.com/changelog/premiere-composer/3)
- **AutoCut:** Mac/Windows, Premiere 13.1+. Install with a downloaded installer, then open from Window > Extensions. Platform not named. — [AutoCut download](https://www.autocut.com/en/download/)
- **Cutback:**
  - "Selects" is a standalone desktop app that does sync, transcription and rough cut, then hands "a structured, edit-ready project" to Premiere, FCP or Resolve.
  - "Premiere Assistant" (previously "Cutback") is the in-Premiere AI plugin.
  - Handoff format not disclosed.
  - — [Cutback blog](https://cutback.video/blog/cutback-50-editing); [cutback llms.txt](https://cutback.video/llms.txt)
- **Standalone AI editors handing off via Premiere XML** (pattern evidence): TimeBolt "Export XML for Premiere Pro"; Vidio "Export to Premiere Pro (XML)". — [TimeBolt docs](https://timebolt.notion.site/Export-XML-for-Premiere-Pro-TimeBolt-78142477a2cc41b78bf3cda3eea8e781); [Vidio support](https://www.vidio.ai/support/export-to-premiere-pro-xml)
- **Open-source references:**
  - auto-cut-agent: a CEP panel because razor/move are QE-DOM-only, with a UXP port pending. — [GitHub](https://github.com/rafcopy/finaltake-agent)
  - Premiere MCP bridges: UXP plugin plus a Python server over WebSocket, or temp-folder polling. — [morim3/mcp_adobe_premiere](https://github.com/morim3/mcp_adobe_premiere); [hetpatel-11 bridge](https://glama.ai/mcp/servers/@hetpatel-11/Adobe_Premiere_Pro_MCP/blob/ab3b527f37cef2c2ae274260f727c10795e4fe83/uxp-plugin/bridge.js)
- **Excalibur:** no reliable public source found on its architecture or UXP status.

### Inferences
- Justin EDIT Suite's current architecture is mainstream for the category in 2026: a CEP panel, a local engine, XML or ExtendScript timeline building, and generated media. Market leaders do not appear to have shipped UXP-only builds yet.
- Vendors without a UXP build by Dec 2028 face the CEP-disabled-by-default switch. The likely industry pattern is dual CEP and UXP builds through 2027–2028.
- A standalone app plus XML handoff (Cutback Selects, TimeBolt, Vidio) avoids extension-API risk entirely. Justin EDIT Suite's XML path already gives it this fallback, if the panel is ever blocked.

### Gaps
- No vendor documentation found confirming CEP vs UXP for AutoCut, FireCut or Premiere Composer, or any of their UXP roadmaps.
- No info on Excalibur.
- No public info on how these vendors build timelines internally (XML import vs scripted insertion) or on their performance.
- AutoPod's architecture comes from a competitor's secondary source.
