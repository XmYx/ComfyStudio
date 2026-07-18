# ComfyStudio

**Cinema Shot Designer for ComfyUI** — design shots, render them through your ComfyUI
workflows, arrange the results on a multitrack timeline, and export a finished video.
All from one desktop app.

ComfyStudio is built with Python and Qt (PyQt6). It talks to a running ComfyUI instance
over its HTTP API, keeps track of every render as a version you can go back to, and even
embeds the ComfyUI node editor so you can tweak workflows without leaving the app.

---

## Highlights

- 🎬 **Shot management** — create, duplicate, extend, and merge shots; import a batch of
  shots from a text file (one prompt per line).
- 🔗 **Workflow integration** — assign any number of ComfyUI image/video workflows to a
  shot, chain them (feed the previous workflow's image/video into the next), and render
  per shot or per workflow across shots.
- 🎛️ **Parameter control** — every literal input of a workflow (seeds, prompts, steps,
  toggles…) is discovered automatically and editable in the UI; mark the ones you care
  about as visible, apply a value across selected or all shots, or make it dynamic.
- 🕘 **Version history** — each render is snapshotted with its parameters and output;
  switch between versions from a dropdown and the parameters/preview follow.
- 🖥️ **Embedded ComfyUI editor** — open the shot's workflow in the built-in ComfyUI
  browser, edit the graph, hit **Ctrl+S**, and the workflow file *and* the shot's
  parameters in ComfyStudio update automatically.
- 🎞️ **Multitrack timeline** — drag shots from the library onto video/audio tracks,
  move/trim/split clips (Select and Blade tools), zoom with Ctrl+wheel, snap to edges
  and the playhead, and play back with the spacebar.
- 📤 **Final video export** — one click renders the arranged timeline to a single MP4
  with ffmpeg (trims respected, gaps filled with black, mixed resolutions letterboxed).
  A separate **File → Export Project** dialog exports per-shot clips or a merged video
  with full codec/bitrate/CRF control.
- 🎨 **Dark & light themes** — pick in Settings; dark is the default.
- 🧩 **Plugins** — drop a Python file with a `register(app)` function into `plugins/`
  to add menus, exporters, API endpoints, or anything else.
- 🌍 **Localization** — the UI ships with a large set of language options.

---

## Requirements

- **Python 3.9+** (the launch scripts automatically pick the *newest* Python installed
  on your machine when creating the environment)
- **A ComfyUI installation** — either one you already have, or let ComfyStudio install
  one for you (see [First-run setup](#first-run-setup))
- **ffmpeg** on your PATH — needed for timeline/video export and clip merging
- Linux, macOS, or Windows

## Installation

### Quick start (recommended)

```bash
git clone https://github.com/XmYx/ComfyStudio.git
cd ComfyStudio
```

Then launch:

- **Windows:** double-click `start.bat`
- **Linux/macOS:** `./start.sh`

The script creates a virtual environment using the newest Python 3 it can find,
installs ComfyStudio into it, and starts the app. Subsequent launches reuse the
same environment and start much faster.

### Manual install

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -e .
comfystudio
```

## First-run setup

1. **Point ComfyStudio at ComfyUI.** Open **Settings → Settings**:
   - *ComfyUI IP/Port* — where your ComfyUI listens (default `http://localhost:8188`).
   - *Comfy Python Path* + *Comfy Main Path* — only needed if you want ComfyStudio to
     start/stop ComfyUI for you with the **Start Comfy** / **Stop Comfy** toolbar buttons.
   - *Comfy Image/Video Workflows* — folders that hold your workflow JSON files.
     If unset, the bundled `workflows/image` and `workflows/video` folders are used.
   - *Theme* — Dark or Light.

2. **No ComfyUI yet?** Use **Settings → Setup Comfy** — a wizard that installs ComfyUI
   into a folder of your choice, creating a fresh environment (conda or venv; the venv
   page auto-detects the latest Python on your machine). **Settings → Setup Comfy Nodes**
   installs the custom nodes ComfyStudio's bundled workflows rely on.

3. **Export your workflows in API format.** ComfyStudio consumes ComfyUI's **API
   format** JSON. In ComfyUI, enable *Dev mode options* in the settings, then use
   **Save (API Format)** and place the file in your image/video workflow folder.
   (If you load a regular UI-format export, ComfyStudio will tell you exactly that.)

## Using ComfyStudio

### 1. Create shots and assign workflows

- Click **Add Shot** in the toolbar (or **File → Import** to create one shot per line of
  a text file, mapped onto a parameter of your choice).
- Select the shot, and in the **Shot Details** dock pick an image and/or video workflow
  from the dropdowns and click **Add**. A shot can chain several workflows in **any
  order** — start with an image workflow, a video workflow, whatever fits: a
  "use previous image/video" parameter always receives the shot's latest still or
  video output, regardless of which stage produced it.

#### Importing workflows from the ComfyUI examples library

Don't have workflows yet? **File → Import Example Workflows** opens a browser for the
official [ComfyUI examples](https://comfyanonymous.github.io/ComfyUI_examples/) site:

- Categories (Flux, SDXL, SD3, Wan, LTX-Video, …) load as a tree; expand one to see
  its examples with **preview images and descriptions**.
- Every example image on that site carries its full workflow embedded in the file's
  metadata. Clicking **Import Workflow** extracts it and saves:
  - the **API-format** workflow into your ComfyStudio library (image or video folder —
    the type is pre-guessed from the category and adjustable), ready to assign to shots;
  - the **UI-format** graph into ComfyUI's own `user/default/workflows/` folder (when
    your ComfyUI path is set in Settings), so the same workflow also appears inside
    ComfyUI's workflow browser.
- The workflow dropdowns refresh automatically after importing.

### 2. Edit parameters

- Every literal input of the workflow appears under **Workflow Parameters**, grouped by
  node. Tick **Visible?** on the ones you want on the shot's quick param list.
- Right-click a parameter for power features:
  - *Set Param to Previous Workflow's Image/Video* — chains workflow outputs.
  - *Set All SELECTED / ALL Shots* — copy this value across shots.
  - *Edit as Dynamic Parameter* — drive the value with an expression or global variable.
- String parameters that contain an image path show a live thumbnail preview.

### 3. Render

- **Render Selected** renders the selected shot(s); **Render All** renders everything.
  With multiple shots you can choose *Per Shot* (finish one shot's whole chain first) or
  *Per Workflow* (run stage 1 for every shot, then stage 2, …).
- ComfyStudio skips renders whose parameters haven't changed, reuses identical results
  from other shots, and stores every new result as a **version**. Shot thumbnails show
  green/orange/red status dots for up-to-date / stale / missing outputs.
- Use the **version dropdown** at the top of the workflow parameters to jump back to any
  earlier render — the parameters and preview follow, and your choice is remembered.

### 4. Edit workflows in the embedded ComfyUI

- Toggle **Windows → Toggle Web Browser**, or right-click an assigned workflow and pick
  **Edit in ComfyUI**. The shot's selected workflow opens in the ComfyUI graph editor.
- Edit the graph, then press **Ctrl+S** (Cmd+S on macOS) *inside the browser view*:
  the workflow JSON file is saved and every shot using it picks up the changes —
  new inputs appear, removed ones vanish, and changed defaults update, while your
  visibility choices and dynamic overrides are preserved.
- Editing the JSON file externally works too: ComfyStudio watches workflow files and
  resyncs parameters automatically.

### 5. Arrange the timeline

- Drag shots from the **Shot Library** dock onto the **Timeline** dock. Hover a library
  thumbnail and scrub; press **I** / **O** to set in/out trim points before dropping.
- Timeline controls:

  | Action | How |
  |---|---|
  | Zoom | **Ctrl + mouse wheel** (zooms around the cursor) |
  | Pan | mouse wheel, or the navigator strip at the bottom |
  | Move clip | drag (snaps to clip edges and the playhead) |
  | Trim clip | drag its left/right edge (clamped to the source media) |
  | Split clip | **Blade** tool, click where you want the cut |
  | Select multiple | drag a rubber-band or use the **Select** tool |
  | Copy / paste clips | **Ctrl+C** / **Ctrl+V** (pastes at the playhead) |
  | Delete clips | **Delete** |
  | Play / pause | **Space**; step frames with **←** / **→** |
  | Scrub | click/drag in the ruler |
  | More tracks | **Add Video Track** / **Add Audio Track** |

- Right-click a track label to hide video tracks or mute/solo/level audio tracks.
- The timeline is saved inside your project file and restored on load.

### 6. Export

- **Timeline toolbar → Export Video** renders the arranged timeline (visible video
  tracks, in order) to a single MP4: trims are respected, gaps become black, mixed
  resolutions are letterboxed, and clips without audio get silent audio so the result
  always plays cleanly.
- **File → Export Project** exports each shot's final video individually or merges them,
  with codec, bitrate, CRF, fps, preset, and custom ffmpeg arguments.

### Projects

**File → Save / Save As / Open / New**, plus a **Recents** menu. A project is a single
JSON file containing your shots (with all parameters and version history) and the
timeline. Rendered stills/videos are collected into `stills/` and `videos/` folders next
to the project file.

## Extending ComfyStudio

Drop a Python file into `src/comfystudio/plugins/` that exposes:

```python
def register(app):
    # `app` is the main window: add menu actions, docks, endpoints, ...
    ...
```

Bundled plugins include the project exporter, an LLM-driven **Shot Wizard**
(generate shots from prompts), a media linker, and an HTTP API handler that lets
external apps submit an image and receive the rendered result.

## Troubleshooting

- **"…appears to be saved in ComfyUI's UI format"** — re-export the workflow with
  **Save (API Format)** (enable Dev mode options in ComfyUI's settings).
- **Export fails / "ffmpeg was not found"** — install ffmpeg and make sure it's on PATH.
- **No Web Browser dock** — install the optional dependency: `pip install PyQt6-WebEngine`.
  The rest of the app works without it.
- **Renders never finish** — check that ComfyUI is actually running at the IP/port set in
  Settings (the **Terminal** button in the status bar shows logs).

## Repository structure

```
src/comfystudio/
├── main.py                  # entry point
├── plugins/                 # auto-loaded plugins (exporter, wizard, API, …)
├── workflows/               # bundled image/video/LLM workflow JSON (API format)
├── defaults/                # default configuration
└── sdmodules/
    ├── core/                # main window, UI, render queue, workflow handling
    ├── new_widget.py        # shot library + multitrack timeline + export
    ├── cs_datastruts.py     # Shot / WorkflowAssignment data model
    ├── workflow_edit_server.py  # save-back bridge for the embedded ComfyUI
    ├── qss.py               # dark & light themes
    └── ...                  # settings, preview dock, installer wizard, helpers
start.sh / start.bat         # one-click launchers (create venv with newest Python)
```

## Contributing

Contributions are welcome! Open an issue or pull request — new plugins, workflows,
translations, and bug fixes are all appreciated. For larger changes, please open an
issue first to discuss the direction.

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

Thanks to the ComfyUI project and the open source community for the tools and feedback
that make ComfyStudio possible.
