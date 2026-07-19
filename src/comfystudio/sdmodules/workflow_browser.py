#!/usr/bin/env python
"""Workflow import browser for the ComfyUI examples library.

Scrapes https://comfyanonymous.github.io/ComfyUI_examples/ into a tree of
categories and examples. Every example image on that site carries the workflow
that produced it embedded in its metadata (PNG tEXt/iTXt chunks, or EXIF for
webp); selecting an example shows its preview and description, and importing it
extracts the workflow and saves it:

- API format  -> the ComfyStudio workflow library (image or video folder), and
- UI format   -> ComfyUI's own user workflow folder (user/default/workflows),
                 so the same workflow shows up inside ComfyUI too.
"""

import json
import logging
import os
import re
import zlib
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests
from qtpy.QtCore import Qt, QThread, Signal
from qtpy.QtGui import QPixmap
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

EXAMPLES_ROOT = "https://comfyanonymous.github.io/ComfyUI_examples/"
_REQUEST_HEADERS = {"User-Agent": "ComfyStudio/1.0 (workflow browser)"}

# Categories whose workflows most likely produce video output.
_VIDEO_HINTS = ("video", "mochi", "ltxv", "cosmos", "wan", "hunyuan", "svd", "animate")


# ---------------------------------------------------------------------------
# Embedded workflow extraction
# ---------------------------------------------------------------------------
def _classify(obj):
    """'ui' for UI-format graphs, 'api' for API-format prompts, None otherwise."""
    if not isinstance(obj, dict) or not obj:
        return None
    if isinstance(obj.get("nodes"), list):
        return "ui"
    if any(isinstance(v, dict) and "inputs" in v for v in obj.values()):
        return "api"
    return None


def _png_text_chunks(data: bytes) -> dict:
    """Returns {keyword: raw_bytes} for all PNG text chunks."""
    out = {}
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return out
    pos = 8
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        try:
            if ctype == b"tEXt" and b"\x00" in chunk:
                key, _, val = chunk.partition(b"\x00")
                out[key.decode("latin-1")] = val
            elif ctype == b"iTXt":
                key, rest = chunk.split(b"\x00", 1)
                comp_flag = rest[0]
                _lang, rest2 = rest[2:].split(b"\x00", 1)
                _translated, text = rest2.split(b"\x00", 1)
                if comp_flag == 1:
                    text = zlib.decompress(text)
                out[key.decode("latin-1")] = text
            elif ctype == b"zTXt":
                key, rest = chunk.split(b"\x00", 1)
                out[key.decode("latin-1")] = zlib.decompress(rest[1:])
        except Exception:
            pass
        if ctype == b"IEND":
            break
    return out


def _scan_for_json(data: bytes, markers) -> dict:
    """Fallback for webp/EXIF: locate '<marker>{...json...}' blobs by parsing."""
    text = data.decode("latin-1", errors="ignore")
    decoder = json.JSONDecoder()
    for marker in markers:
        idx = 0
        while True:
            i = text.find(marker, idx)
            if i < 0:
                break
            j = text.find("{", i)
            if 0 <= j < i + 64:
                try:
                    obj, _end = decoder.raw_decode(text[j:])
                    if isinstance(obj, dict):
                        return obj
                except Exception:
                    pass
            idx = i + len(marker)
    return None


def extract_workflows_from_bytes(data: bytes) -> dict:
    """
    Extracts embedded ComfyUI workflows from an image file's bytes.
    Returns {"api": dict|None, "ui": dict|None}.
    """
    result = {"api": None, "ui": None}
    chunks = _png_text_chunks(data)
    for key, raw in chunks.items():
        try:
            obj = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            continue
        kind = _classify(obj)
        if kind and result[kind] is None:
            result[kind] = obj
    if result["api"] is None:
        obj = _scan_for_json(data, ("prompt:", "Prompt:"))
        if _classify(obj) == "api":
            result["api"] = obj
    if result["ui"] is None:
        obj = _scan_for_json(data, ("workflow:", "Workflow:"))
        if _classify(obj) == "ui":
            result["ui"] = obj
    return result


# ---------------------------------------------------------------------------
# HTML parsing (stdlib only)
# ---------------------------------------------------------------------------
class _IndexParser(HTMLParser):
    """Collects the category links (<p><a href="relative">Title</a></p>)."""

    def __init__(self):
        super().__init__()
        self.links = []  # list of (title, relative_href)
        self._in_p = False
        self._in_a = False
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag == "p":
            self._in_p = True
        elif tag == "a" and self._in_p:
            href = dict(attrs).get("href", "")
            if href and "://" not in href and not href.startswith(("#", "/")):
                self._in_a = True
                self._href = href
                self._text = []

    def handle_data(self, data):
        if self._in_a:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._in_a:
            title = "".join(self._text).strip()
            if title:
                self.links.append((title, self._href))
            self._in_a = False
        elif tag == "p":
            self._in_p = False


class _CategoryParser(HTMLParser):
    """
    Collects example entries from a category page: every <img> is an example
    carrying an embedded workflow; <a href="*.json"> links are direct workflow
    downloads. Each entry keeps the nearest heading and preceding paragraphs
    as its title/description.
    """

    def __init__(self):
        super().__init__()
        self.entries = []  # {kind, url, title, description}
        self._heading = ""
        self._capture_heading = False
        self._htext = []
        self._in_p = False
        self._para = []
        self._desc_parts = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("h1", "h2", "h3", "h4"):
            self._capture_heading = True
            self._htext = []
        elif tag == "p":
            self._in_p = True
            self._para = []
        elif tag == "img":
            src = a.get("src", "")
            if src:
                self._add("image", src)
        elif tag == "a":
            href = a.get("href", "")
            if href.lower().endswith(".json"):
                self._add("json", href)

    def _add(self, kind, url):
        self.entries.append({
            "kind": kind,
            "url": url,
            "title": self._heading,
            "description": "\n".join(self._desc_parts[-3:]).strip(),
        })

    def handle_data(self, data):
        if self._capture_heading:
            self._htext.append(data)
        elif self._in_p:
            self._para.append(data)

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "h4") and self._capture_heading:
            self._capture_heading = False
            heading = "".join(self._htext).strip()
            if heading:
                self._heading = heading
                self._desc_parts = []
        elif tag == "p" and self._in_p:
            self._in_p = False
            text = "".join(self._para).strip()
            if text:
                self._desc_parts.append(text)


def parse_index(html: str):
    parser = _IndexParser()
    parser.feed(html)
    return parser.links


def parse_category(html: str, page_url: str):
    parser = _CategoryParser()
    parser.feed(html)
    entries = []
    seen_titles = {}
    for entry in parser.entries:
        entry["url"] = urljoin(page_url, entry["url"])
        title = entry["title"] or os.path.basename(entry["url"])
        n = seen_titles.get(title, 0) + 1
        seen_titles[title] = n
        entry["label"] = title if n == 1 else f"{title} ({n})"
        entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------
def comfy_user_workflow_dir(settings_manager):
    """ComfyUI's own workflow folder (user/default/workflows), or None."""
    main_path = settings_manager.get("comfy_main_path", "") or ""
    if not main_path:
        return None
    base = os.path.dirname(main_path)
    if not os.path.isdir(base):
        return None
    return os.path.join(base, "user", "default", "workflows")


def slugify(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")
    return slug or "workflow"


def unique_path(directory: str, base_name: str) -> str:
    path = os.path.join(directory, f"{base_name}.json")
    counter = 2
    while os.path.exists(path):
        path = os.path.join(directory, f"{base_name}_{counter}.json")
        counter += 1
    return path


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------
class _Fetcher(QThread):
    ok = Signal(str, bytes)
    fail = Signal(str, str)

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        try:
            r = requests.get(self.url, timeout=30, headers=_REQUEST_HEADERS)
            r.raise_for_status()
            self.ok.emit(self.url, r.content)
        except Exception as e:
            self.fail.emit(self.url, str(e))


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------
class WorkflowBrowserDialog(QDialog):
    """Tree browser for the ComfyUI examples library with one-click import."""

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main = main_window
        self.setWindowTitle("Import Workflows — ComfyUI Examples")
        self.resize(1000, 640)

        self._fetchers = []
        self._media_cache = {}       # url -> bytes
        self._current_entry = None
        self.imported_any = False

        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        # Left: category/example tree
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("ComfyUI Examples")
        self.tree.itemExpanded.connect(self._onItemExpanded)
        self.tree.currentItemChanged.connect(self._onItemSelected)
        splitter.addWidget(self.tree)

        # Right: preview + description + import controls
        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.previewLabel = QLabel("Select an example to see its preview.")
        self.previewLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.previewLabel.setMinimumHeight(260)
        right_layout.addWidget(self.previewLabel, 1)

        self.descriptionEdit = QTextEdit()
        self.descriptionEdit.setReadOnly(True)
        self.descriptionEdit.setMaximumHeight(140)
        right_layout.addWidget(self.descriptionEdit)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self.nameEdit = QLineEdit()
        name_row.addWidget(self.nameEdit, 1)
        self.typeCombo = QComboBox()
        self.typeCombo.addItem("Image workflow", False)
        self.typeCombo.addItem("Video workflow", True)
        name_row.addWidget(self.typeCombo)
        right_layout.addLayout(name_row)

        self.saveToComfyCheck = QCheckBox("Also save to ComfyUI's workflow folder")
        comfy_dir = comfy_user_workflow_dir(self.main.settingsManager)
        if comfy_dir:
            self.saveToComfyCheck.setChecked(True)
            self.saveToComfyCheck.setToolTip(comfy_dir)
        else:
            self.saveToComfyCheck.setChecked(False)
            self.saveToComfyCheck.setEnabled(False)
            self.saveToComfyCheck.setToolTip(
                "Set the ComfyUI main path in Settings to enable this."
            )
        right_layout.addWidget(self.saveToComfyCheck)

        self.importBtn = QPushButton("Import Workflow")
        self.importBtn.setEnabled(False)
        self.importBtn.clicked.connect(self._onImportClicked)
        right_layout.addWidget(self.importBtn)

        splitter.addWidget(right)
        splitter.setSizes([380, 620])

        self.statusLabel = QLabel("Loading example index...")
        layout.addWidget(self.statusLabel)

        closeBtn = QPushButton("Close")
        closeBtn.clicked.connect(self.accept)
        layout.addWidget(closeBtn)

        self._fetch(EXAMPLES_ROOT, self._onIndexFetched)

    # --- networking plumbing ---
    def _fetch(self, url, on_ok):
        fetcher = _Fetcher(url, self)

        def ok(u, data):
            self._fetchers.remove(fetcher)
            on_ok(u, data)

        def fail(u, err):
            self._fetchers.remove(fetcher)
            self.statusLabel.setText(f"Failed to fetch {u}: {err}")

        fetcher.ok.connect(ok)
        fetcher.fail.connect(fail)
        self._fetchers.append(fetcher)
        fetcher.start()

    def closeEvent(self, event):
        for fetcher in list(self._fetchers):
            fetcher.wait(3000)
        super().closeEvent(event)

    # --- index / category loading ---
    def _onIndexFetched(self, url, data):
        links = parse_index(data.decode("utf-8", errors="replace"))
        self.tree.clear()
        for title, href in links:
            item = QTreeWidgetItem([title])
            item.setData(0, Qt.ItemDataRole.UserRole, {
                "type": "category",
                "url": urljoin(EXAMPLES_ROOT, href.rstrip("/") + "/"),
                "loaded": False,
            })
            item.addChild(QTreeWidgetItem(["Loading..."]))  # expander placeholder
            self.tree.addTopLevelItem(item)
        self.statusLabel.setText(f"{len(links)} example categories. Expand one to browse.")

    def _onItemExpanded(self, item):
        info = item.data(0, Qt.ItemDataRole.UserRole)
        if not info or info.get("type") != "category" or info.get("loaded"):
            return
        info["loaded"] = True
        item.setData(0, Qt.ItemDataRole.UserRole, info)
        url = info["url"]
        self.statusLabel.setText(f"Loading {item.text(0)}...")

        def on_ok(u, data, target=item, page_url=url):
            target.takeChildren()
            entries = parse_category(data.decode("utf-8", errors="replace"), page_url)
            examples = [e for e in entries if e["kind"] in ("image", "json")]
            for entry in examples:
                entry["category"] = target.text(0)
                child = QTreeWidgetItem([entry["label"]])
                child.setData(0, Qt.ItemDataRole.UserRole, {"type": "example", "entry": entry})
                child.setToolTip(0, entry["url"])
                target.addChild(child)
            if not examples:
                target.addChild(QTreeWidgetItem(["(no examples found)"]))
            self.statusLabel.setText(
                f"{target.text(0)}: {len(examples)} example(s)."
            )

        self._fetch(url, on_ok)

    # --- example selection ---
    def _onItemSelected(self, current, _previous):
        self._current_entry = None
        self.importBtn.setEnabled(False)
        if not current:
            return
        info = current.data(0, Qt.ItemDataRole.UserRole)
        if not info or info.get("type") != "example":
            return
        entry = info["entry"]
        self._current_entry = entry
        self.descriptionEdit.setPlainText(
            (entry["title"] + "\n\n" if entry["title"] else "") + (entry["description"] or "")
        )
        base = os.path.splitext(os.path.basename(entry["url"]))[0]
        self.nameEdit.setText(slugify(f"{entry['category']}_{base}"))
        hint_text = f"{entry.get('category','')} {entry['title']}".lower()
        self.typeCombo.setCurrentIndex(
            1 if any(h in hint_text for h in _VIDEO_HINTS) else 0
        )

        if entry["kind"] == "json":
            self.previewLabel.setText("Direct workflow JSON (no preview image).")
            self.importBtn.setEnabled(True)
            self._ensureMedia(entry["url"], preview=False)
            return

        self.previewLabel.setText("Loading preview...")
        self._ensureMedia(entry["url"], preview=True)

    def _ensureMedia(self, url, preview):
        if url in self._media_cache:
            if preview:
                self._showPreview(url, self._media_cache[url])
            self.importBtn.setEnabled(True)
            return

        def on_ok(u, data):
            self._media_cache[u] = data
            if self._current_entry and self._current_entry["url"] == u:
                if preview:
                    self._showPreview(u, data)
                self.importBtn.setEnabled(True)

        self._fetch(url, on_ok)

    def _showPreview(self, url, data):
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            scaled = pixmap.scaled(
                self.previewLabel.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.previewLabel.setPixmap(scaled)
        else:
            self.previewLabel.setText("(preview not supported for this file)")

    # --- import ---
    def _onImportClicked(self):
        entry = self._current_entry
        if not entry:
            return
        data = self._media_cache.get(entry["url"])
        if data is None:
            QMessageBox.information(self, "Import", "Still downloading — try again in a moment.")
            return

        if entry["kind"] == "json":
            try:
                obj = json.loads(data.decode("utf-8", errors="replace"))
            except Exception as e:
                QMessageBox.warning(self, "Import Failed", f"Invalid workflow JSON: {e}")
                return
            kind = _classify(obj)
            workflows = {"api": obj if kind == "api" else None,
                         "ui": obj if kind == "ui" else None}
        else:
            workflows = extract_workflows_from_bytes(data)

        if not workflows["api"] and not workflows["ui"]:
            QMessageBox.warning(
                self, "Import Failed",
                "No embedded ComfyUI workflow could be found in this example."
            )
            return

        name = slugify(self.nameEdit.text().strip() or "workflow")
        is_video = bool(self.typeCombo.currentData())
        saved = []

        # 1) API format (usable by shots directly) — into ComfyUI's own
        # workflow folder when an install is configured, so the embedded
        # ComfyUI sees the same library as ComfyStudio's listing. Falls back
        # to the legacy image/video library dirs otherwise.
        if workflows["api"]:
            lib_dir = None
            dest_label = "ComfyUI workflows"
            if hasattr(self.main, "comfyUserWorkflowsDir"):
                lib_dir = self.main.comfyUserWorkflowsDir(create=True)
            if not lib_dir:
                image_dir, video_dir = self.main.workflowLibraryDirs()
                lib_dir = video_dir if is_video else image_dir
                dest_label = f"Library ({'video' if is_video else 'image'})"
            try:
                os.makedirs(lib_dir, exist_ok=True)
                lib_path = unique_path(lib_dir, name)
                with open(lib_path, "w", encoding="utf-8") as f:
                    json.dump(workflows["api"], f, indent=2)
                saved.append(f"{dest_label}: {lib_path}")
            except Exception as e:
                QMessageBox.warning(self, "Import Failed", f"Could not save to library: {e}")
                return

        # 2) UI format into ComfyUI's own workflow folder (named *_ui when an
        # API copy already lives there under the plain name).
        if self.saveToComfyCheck.isChecked():
            comfy_dir = comfy_user_workflow_dir(self.main.settingsManager)
            if comfy_dir and workflows["ui"]:
                try:
                    os.makedirs(comfy_dir, exist_ok=True)
                    base_name = f"{name}_ui" if workflows["api"] else name
                    comfy_path = unique_path(comfy_dir, base_name)
                    with open(comfy_path, "w", encoding="utf-8") as f:
                        json.dump(workflows["ui"], f, indent=2)
                    saved.append(f"ComfyUI (UI format): {comfy_path}")
                except Exception as e:
                    logging.warning(f"Could not save workflow to ComfyUI folder: {e}")

        notes = []
        if not workflows["api"]:
            notes.append(
                "Note: only a UI-format graph was found, so nothing was added to the "
                "ComfyStudio library — shots need an API-format export. Open the "
                "workflow in ComfyUI and use 'Save (API Format)'."
            )

        if not saved:
            QMessageBox.warning(self, "Import", "\n\n".join(
                ["Nothing was saved."] + notes
            ))
            return

        self.imported_any = True
        self.statusLabel.setText(f"Imported '{name}'.")
        QMessageBox.information(self, "Import Complete", "\n\n".join(saved + notes))
