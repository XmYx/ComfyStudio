# CLAUDE.md — ComfyStudio developer notes

ComfyStudio (a.k.a. "Cinema Shot Designer") is a PyQt6/qtpy desktop app for designing
cinematic shots, rendering them through ComfyUI workflows, arranging the results on a
multitrack timeline, and exporting a final video.

## Running

- Entry point: `src/comfystudio/main.py` → `comfystudio.main:main` (console script `comfystudio`).
- Install: `pip install -e .` (see `setup.py`), or use `start.sh` / `start.bat` which create
  a venv with the **newest** Python found on the machine and launch the app.
- User settings live at `<AppDataLocation>/CinemaShotDesigner/user_settings.json`
  (managed by `sdmodules/settings.py::SettingsManager`).

## Architecture

`ComfyStudioWindow` (`sdmodules/core/mainwindow.py`) is a cooperative multiple-inheritance
composition — **MRO matters**:

```
ComfyStudioWindow(ComfyStudioUI, ComfyStudioShotManager, ComfyStudioComfyHandler, ShotManager)
    ComfyStudioUI(ComfyStudioBase, QMainWindow)      # sdmodules/core/ui.py
    ComfyStudioBase                                  # sdmodules/core/base.py
    ComfyStudioShotManager, ComfyStudioComfyHandler  # sdmodules/core/comfyhandler.py
    ShotManager                                      # sdmodules/shot_manager.py (legacy; mostly shadowed)
```

Methods defined in `ui.py`/`base.py` shadow same-named legacy ones in `shot_manager.py`
(e.g. `fillDock`, `clearDock`, `openProject`). `_saveProjectToPath` lives only in
`shot_manager.py`. All mixins call `super().__init__()` cooperatively.

### Key modules

| Module | Responsibility |
|---|---|
| `sdmodules/cs_datastruts.py` | `Shot` and `WorkflowAssignment` dataclasses (serialization) |
| `sdmodules/core/base.py` | Workflow import/validation, param discovery & sync, shot ops |
| `sdmodules/core/ui.py` | UI construction, docks, project load, browser bridge wiring, themes |
| `sdmodules/core/comfyhandler.py` | Render queue, ComfyUI HTTP submission, result download, versions |
| `sdmodules/new_widget.py` | Shot Library + multitrack Timeline + timeline export (`ShotManagerWidget`) |
| `sdmodules/preview_dock.py` | Per-shot render preview (QMediaPlayer) |
| `sdmodules/shot_node_editor.py` | Shot workflow graph dock (node editor for chaining workflows) |
| `sdmodules/workflow_edit_server.py` | Localhost bridge for save-back from embedded ComfyUI |
| `sdmodules/workflow_browser.py` | ComfyUI-examples scraper/import dialog (File → Import Example Workflows) |
| `sdmodules/comfy_locator.py` | Detects ComfyUI installs (`find_comfy_root`/`find_comfy_python`: repo, `ComfyUI/` subdir, portable bundle, venv/.venv). `startComfy` resolves/repairs settings paths through it, preflights core deps with `_missingComfyDeps` (find_spec subprocess, fails open) and offers to pip-install requirements.txt into that env; `ComfyWorker` runs with cwd = ComfyUI dir |
| `sdmodules/comfy_installer.py` | Install/Update ComfyUI wizard (destination selector page prefills from `comfy_main_path`; installs into the wizard-selected env, never `sys.executable`). `RequirementsInstallWorker` + `split_requirements`: pip self-upgrade, essentials batch with per-package salvage, the `#non essential dependencies` section (comfy-angle & co. — no wheels on some platforms) installed per package with failures downgraded to warnings. Env pages gate on `MIN_COMFY_PYTHON` (3.10 — comfy-kitchen/av≥16/torch resolve on nothing older; on older Pythons a plain `pip install -r` aborts entirely) |
| `sdmodules/qss.py` | `dark_qss` / `light_qss`, `get_stylesheet(theme)` |
| `sdmodules/pyenvutils.py` | `find_latest_python()` interpreter discovery |
| `sdmodules/worker.py` | `RenderWorker` (Comfy polling), `ComfyWorker` (Comfy process) |
| `plugins/` | Auto-loaded modules exposing `register(app)` (exporter, wizard, API handler…) |

## Data model invariants

- `Shot.to_dict()` / `from_dict()` must stay symmetric — every persisted field
  (`inPoint`, `outPoint`, `linkedAudio`, `thumbnail_path`, `fps`, …) is read back on load.
- `Shot.raw_duration` = full source duration (cv2 probe, cached in `_cached_duration`,
  invalidated by `__setattr__` on `videoPath`/`inPoint`/`outPoint`); `Shot.duration` =
  `raw_duration * (outPoint - inPoint)`. Timeline math must divide by **raw_duration**,
  never by the trimmed `duration`.
- `to_dict()` saves **raw** duration; `from_dict()` maps it to `default_duration`
  (fallback when the video file is unreadable).
- `WorkflowAssignment.selected_version_index` persists which version dropdown entry
  is active; version snapshots live in `versions` (`{params, output, is_video,
  output_type, timestamp}`). Restoring a version deep-copies `params` — never
  assign the snapshot by reference.
- Workflows are **type-agnostic**: `WorkflowAssignment.outputType` is a manual
  override of the Result port's media type (`""` = auto). `lastOutputType` is the
  type auto-detected from the last render (`IMAGE`/`VIDEO`/`AUDIO`/`STRING`).
  `result_port_type()` prefers override → `lastOutputType` → the legacy `isVideo`
  hint. `onComfyResult` detects the type per output node from ComfyUI's output
  bucket (`images`/`gifs`/`audio`) refined by file extension
  (`comfy_datatypes.detect_media_type`); when an override is set it also selects
  which of several outputs becomes the Result. `isVideo`/`lastOutputIsVideo` are
  kept for back-compat and derived from the type.
- `WorkflowAssignment.uid` is a stable identity **within one shot** (generated in
  `__post_init__`, repaired by `ensure_unique_workflow_uids(shot)` after deepcopies —
  called from `Shot.from_dict`, `addWorkflowToShot`, `extendClip`). Deepcopied shots
  (duplicates, timeline clips) legitimately share uids across shots — never use a uid
  as a global key. Match assignments by `uid`, not `path` (a shot can hold the same
  file twice).
- `WorkflowAssignment.lastOutput` / `lastOutputIsVideo` = most recent rendered output
  (set in `onComfyResult`, backfilled from `versions` by `_latestWorkflowOutput`).

## Shot workflow graph (node editor)

- `Shot.graph = {"connections": [...], "positions": {uid: [x, y]}}`. A connection is
  `{"from": {"wf": uid, "port": id}, "to": {"wf": uid, "port": id}}` where a port id is
  `workflow_param_port_id(param)` (= `name@nodeIDs`, the same identity
  `syncWorkflowFromFile` merges by), the reserved `RESULT_PORT` (`"__result__"`,
  output-only: the workflow's rendered file), or `node_output_port_id(node_id, slot)`
  (`"nodeout:<node_id>:<slot>"`, output-only: a ComfyUI node's output). Connections
  are **within one shot only**. The whole graph persists via `Shot.to_dict()` in the
  project JSON.
- Params opt into ports via `graphInput` / `graphOutput` flags (a param may be both);
  these flags are in `syncWorkflowFromFile`'s preserve list and the defaults merge —
  keep them there when adding new port-related keys.
- ComfyUI node outputs: the API JSON never declares them, so
  `extractWorkflowNodeOutputs` infers slots from `[node_id, slot]` links (slot 0
  always assumed — terminal nodes like SaveImage produce files without being
  referenced). Exposure choices live in
  `workflow.parameters["exposedNodeOutputs"]` (`[{"port", "label", "type"}]`, pruned by
  `syncWorkflowFromFile` when the node disappears).
- Port datatypes (`sdmodules/comfy_datatypes.py`): ports carry ComfyUI-convention
  types and colours (`COMFY_TYPE_COLORS`; edges take the source type's colour).
  Param ports map widget types (INT/FLOAT/BOOLEAN/STRING); Result type comes from
  `result_port_type` (override → detected `lastOutputType` → isVideo hint), one of
  IMAGE/VIDEO/AUDIO/STRING; node-output types are inferred **at exposure
  time** by `infer_node_output_type` (consumer input name → type, else class-name
  heuristic, else `"*"` wildcard) and stored in the exposure entry.
  `types_compatible` gates `tryConnect`: equal types or `"*"` match; STRING inputs
  additionally accept IMAGE/VIDEO/AUDIO (chaining passes file paths); FLOAT accepts INT;
  everything else is refused with a status message. Existing connections are never
  retro-validated. At render time `onComfyResult`
  records one file per output node in `workflow.lastNodeOutputs` (`{node_id: path}`);
  `resolveGraphInputs` feeds `nodeout:` connections from it, falling back to
  `_latestWorkflowOutput`. Non-file node outputs (MODEL/CLIP/LATENT…) can be exposed
  but only resolve when that node produced a history output file.
- Render order = `topoSortedWorkflowIndices` (Kahn, list-index tie-break; cycle →
  warning + list-order fallback). `resolveGraphInputs` fills connected params before
  the signature is computed; legacy `usePrevResultImage/Video` flags remain honored as
  a fallback for ports the graph didn't satisfy. `computeWorkflowSignature` includes
  the connections so re-wiring invalidates cached outputs.
- Old projects: `migrateShotGraph(shot)` (called per shot in `openProjectFromPath`,
  idempotent — no-op when connections exist) converts prev-result flags into Result
  connections from the previous **enabled** workflow; the flags are kept so older
  builds still chain.
- After removing workflows or params, call `pruneInvalidConnections(shot)`; the dock
  (`shot_node_editor.py`, objectName `shot_node_editor_dock`) rebuilds via
  `refreshNodeEditor()` and skips rebuilds while hidden (`_dirty` + `showEvent`).

## Workflows (ComfyUI)

- Workflows render in **API/prompt format** (node-id-keyed dict). UI-format graphs
  (`{"nodes": [...], "links": [...]}`, what ComfyUI's own browser saves) are
  **converted on the fly** by `base.py::loadWorkflowJson` via
  `comfy_workflow_convert.ui_workflow_to_api` — but only the **modern**
  widget-annotated format (each node's `inputs` entry carries a `widget` name, so
  widget→value mapping is embedded; no `/object_info` needed). Older UI exports
  (bare `widgets_values`, no widget names — the `control_after_generate` slot can't
  be placed reliably offline) fail `can_convert_ui_workflow` and are rejected with a
  "re-save in a recent ComfyUI" message and skipped from the listing.
  `extractWorkflowParams` exposes only literal int/float/bool/string inputs;
  list/tuple/dict values are node links and are skipped. `nodeIDs` are stored as strings.
- Param dict shape: `{name, type, value, nodeIDs, displayName, visible, nodeMetaTitle}`
  plus optional `usePrevResultImage/Video`, `useApiImage`, `dynamicOverrides`,
  `expression`, `global_var`.
- Chains are type-agnostic: a shot's workflow list may start with an image or a video
  workflow, and `executeWorkflow`'s prev-result overrides feed `shot.stillPath` /
  `shot.videoPath` based on what the param asks for (`usePrevResultImage/Video`), not on
  the previous workflow's declared `isVideo`.
- The UI lists workflows in **one** combo (`workflowCombo`, filled by `loadWorkflows`),
  with a manual refresh button (`refreshWorkflows`) beside it. Each assigned-workflow
  row also has an **Output** type selector (Auto/Image/Video/Audio/Text →
  `WorkflowAssignment.outputType`; `onWorkflowOutputTypeChanged` rebuilds the node
  editor since the Result port type changes).
  The **shared library is ComfyUI's own `user/default/workflows`**
  (`base.py::comfyUserWorkflowsDir`, derived from `comfy_main_path`): `loadWorkflows`
  scans it recursively (API-format files plus convertible modern UI-format graphs;
  older UI exports are skipped — see `_isApiWorkflowFile`)
  and, when it exists, it is the **authoritative and only** source — the bundled/
  fallback library is NOT mixed in, so the studio's list matches what the running
  ComfyUI instance sees. Fallback dirs apply only when no install is configured.
  The example importer saves API workflows into it (the single configured
  `comfy_workflows` folder, else the bundled dirs, are the fallback when no install is
  configured), and a `QFileSystemWatcher` `directoryChanged` hook
  (`_onWorkflowDirChanged`, debounced) refreshes the combo when files appear —
  `_watchWorkflowDir(recursive=True)` watches subfolders too, so Ctrl+S saves from
  the embedded editor and manual copies into subdirs are picked up. The bundled
  image/video default workflows were removed (outdated); `workflows/llm` stays
  (wizard plugin). Settings expose a **single** "Comfy Workflows Folder"
  (`comfy_workflows`; old `comfy_image_workflows`/`comfy_video_workflows` are migrated
  and kept in sync). `workflowLibraryDirs()` returns a **list** of fallback dirs.
  `isVideo` is only a legacy hint; the real result type is detected in
  `onComfyResult`. Keep the `image_workflows` (now the full unified list) /
  `video_workflows` (now empty) attributes and the `addWorkflowToShot(path,
  isVideo=)` signature — `plugins/wizard.py` depends on them.
- Example import (`workflow_browser.py`): parses the examples site with stdlib
  `HTMLParser`; extracts embedded workflows from PNG tEXt/iTXt/zTXt chunks and, as a
  fallback (webp/EXIF), by scanning for `prompt:{`/`workflow:{` markers with
  `json.JSONDecoder.raw_decode`. API format → library dirs
  (`workflowLibraryDirs()`), UI format → `<comfy>/user/default/workflows/`.
- `syncWorkflowFromFile(path)` re-reads a workflow file and merges into every shot using
  it: file values win, but visibility/displayName/dynamic-override config (and the
  placeholder value of dynamic params) are preserved; removed inputs are dropped.
  It sets `self._syncingWorkflow` while refreshing so `onWorkflowItemClicked` doesn't
  push the graph back into the embedded editor mid-sync.

## Embedded ComfyUI editor (browser dock)

- The Web Browser dock is **optional**: `PyQt6-WebEngine` import is wrapped in
  try/except in `ui.py::createWindowsMenu`; guard all access with `hasattr`.
- All ComfyUI server URLs go through `base.py::getComfyApiUrl()` — it strips the
  trailing slash and maps `localhost` → `127.0.0.1` (macOS resolves localhost to
  ::1 first while ComfyUI binds IPv4-only). Never read the `comfy_ip` setting raw.
- The browser view is loaded **lazily**: never at construction (ComfyUI isn't up
  yet — Chromium would cache an error page). `_webBrowserLoadOk` (set on
  `loadFinished`) marks failed loads; toggling the dock, `loadWorkflowInBrowser`
  and `onComfyReady` all reload when the view is blank or the last load failed.
  `startComfy` spawns a `ComfyReadyPoller` (worker.py, polls `/system_stats`)
  that fires `onComfyReady` → status message + browser (re)load; stopped in
  `stopComfy`.
- Selecting a workflow while the dock is visible loads it into the ComfyUI canvas via
  injected JS (`app.loadApiJson`, retry loop, `loadGraphData` fallback). Re-selecting
  the file already open is a no-op to preserve unsaved canvas edits.
- Save-back: `WorkflowEditBridge` runs a `ThreadingHTTPServer` on `127.0.0.1:<ephemeral>`;
  a Ctrl/Cmd+S hook injected on `loadFinished` posts `app.graphToPrompt().output` to
  `/save_workflow`. The write target is set by the app (`set_target`), never by the
  request. `workflowSaved(path)` → `onWorkflowFileSaved` → `syncWorkflowFromFile`.
- A debounced `QFileSystemWatcher` (`_watchWorkflowFile`) also resyncs on external edits.
- Shut the bridge down in `cleanUp()`.

## Timeline (`new_widget.py`)

- All time↔pixel mapping goes through `timeToX`/`xToTime` (respects `scale` px/sec and
  `time_offset` pan). Ctrl+wheel zooms around the cursor; plain wheel pans.
- The navigator handle is **derived state**: `scale`/`time_offset` are the single
  source of truth and `TimelineNavigator.syncFromTimeline()` recomputes the rect
  (call it after anything that changes zoom/pan/`timeline_end`/widget size — see
  `_syncNavigator()` call sites). Navigator → timeline goes through
  `onNavigatorRangeChanged` (guarded by `_syncingNavigator`, zoom clamped to the same
  [2, 2000] as Ctrl+wheel). Drag = pan, edge-drag = zoom (both edges), empty-area
  click = center-pan; a plain click must never change the zoom.
- Drag & drop payload (`application/x-shot`) round-trips through `Shot.to_dict`-style
  dicts and `Shot.from_dict` — **never** `Shot(**dict)` (the `duration` key is not a
  constructor arg).
- Timeline clips hold their own `Shot` copies (decoupled from the library). `splitClip`
  gives right-hand halves a fresh deepcopy so the two halves trim independently; linked
  audio/video clips are identified by **sharing the same Shot object** (`is`).
- Resizing clamps to available source media using `raw_duration`.
- `serializeTimeline()`/`restoreTimeline()` persist the timeline inside the project JSON
  under the `"timeline"` key (saved by `_saveProjectToPath`, restored by
  `openProjectFromPath`).
- `TimelineExportWorker` (QThread) renders the timeline with ffmpeg: per-clip `-ss/-t`
  trims, black+silence gap fillers, letterboxing to the first clip's (even) resolution,
  silent-audio injection for audio-less sources so concat streams always match, then a
  concat-demuxer `-c copy` join. Requires `ffmpeg` (and uses `ffprobe` when available).
- Dock objectNames must be unique for `saveState()`: the timeline preview dock is
  `timeline_preview_dock` (the main preview dock is `preview_dock`).

## Project files

`*.json`: `{"shots": [Shot.to_dict()...], "timeline": serializeTimeline()}`.
Old projects without `timeline` (or the new Shot fields) load fine via defaults.

## Themes

`qss.get_stylesheet(settingsManager.get("theme", "dark"))` applied to the QApplication at
startup (`main.py`) and on settings change (`ui.py::applyTheme`). Themes: `dark`, `light`.

## Testing recipes (headless)

- `QT_QPA_PLATFORM=offscreen` works for constructing the full window and calling methods
  directly. **`app.exec()` never returns under offscreen** (pre-existing; likely the
  QMediaPlayer in the preview dock) — don't rely on the event loop returning; use
  `app.processEvents()` in polling loops and `os._exit(0)` to end test scripts.
- This dev machine: Python 3.9 (miniconda) as `python3` (own PyQt6, no WebEngine),
  newest is `/usr/local/bin/python3.12`; the app venv is `./venv` (Python 3.11,
  PyQt6 ≥ 6.8 **with** WebEngine) — run GUI/WebEngine tests with `venv/bin/python`.
  `ffmpeg`/`ffprobe` exist (pyenv shims) — generate test media with
  `-f lavfi -i testsrc=...` / `sine=...`.
- **Never pin/downgrade Qt to 6.6**: QtWebEngine 6.6's Chromium segfaults on
  macOS 26 the moment a dock hosting a loaded QWebEngineView is hidden/resized
  (crash: `QWidget::hide` in QMainWindowLayout, thread `CrBrowserMain`).
  setup.py requires PyQt6 ≥ 6.8 for this reason. PyQt6 also has no
  `QFileDialog.Options()` — use no options arg / `QFileDialog.Option.*`.
- No test suite exists; verify with `python -m py_compile` plus small inline scripts.
