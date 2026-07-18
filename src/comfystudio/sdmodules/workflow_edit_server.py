#!/usr/bin/env python
"""Bridge between the embedded ComfyUI web view and ComfyStudio.

The ComfyUI page runs in a QWebEngineView and cannot write files itself, so
ComfyStudio runs a tiny localhost-only HTTP endpoint. A Ctrl+S hook injected
into the page posts the current graph (API format, from app.graphToPrompt())
here; the bridge writes it to the workflow file currently being edited and
emits workflowSaved so the UI can re-sync its parameters.
"""

import json
import logging
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from qtpy.QtCore import QObject, Signal


class _SaveRequestHandler(BaseHTTPRequestHandler):
    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_POST(self):
        bridge = self.server.bridge
        if self.path.rstrip("/") != "/save_workflow":
            self.send_response(404)
            self._cors_headers()
            self.end_headers()
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            result = bridge.handle_save(body)
            payload = json.dumps(result).encode("utf-8")
            self.send_response(200 if result.get("ok") else 400)
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as e:
            logging.warning(f"Workflow save endpoint error: {e}")
            try:
                self.send_response(500)
                self._cors_headers()
                self.end_headers()
            except Exception:
                pass

    def log_message(self, fmt, *args):
        # Keep the terminal clean; failures are logged via logging above.
        pass


class WorkflowEditBridge(QObject):
    """
    Localhost save endpoint plus the "which file is being edited" state.
    Emits workflowSaved(path) after a graph from the embedded ComfyUI has been
    written to disk (the signal crosses into the main thread automatically).
    """
    workflowSaved = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._target_path = None
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _SaveRequestHandler)
        self._server.bridge = self
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="WorkflowEditServer", daemon=True
        )
        self._thread.start()
        logging.info(f"Workflow edit bridge listening on 127.0.0.1:{self.port}")

    def set_target(self, path):
        """Sets the workflow file the embedded editor is currently editing."""
        with self._lock:
            self._target_path = path

    def target(self):
        with self._lock:
            return self._target_path

    def handle_save(self, body: bytes) -> dict:
        """Called from the HTTP thread. Validates and writes the posted graph."""
        target = self.target()
        if not target:
            return {"ok": False, "error": "No workflow is currently open for editing in ComfyStudio."}
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as e:
            return {"ok": False, "error": f"Invalid JSON: {e}"}
        if not isinstance(data, dict) or not data:
            return {"ok": False, "error": "Expected a non-empty API-format workflow object."}
        has_node = any(isinstance(v, dict) and "inputs" in v for v in data.values())
        if not has_node:
            return {"ok": False, "error": "Posted data does not look like an API-format workflow."}
        try:
            # Atomic-ish write: temp file in the same directory, then replace.
            dir_name = os.path.dirname(target) or "."
            fd, tmp_path = tempfile.mkstemp(suffix=".json", dir=dir_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp_path, target)
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
        except Exception as e:
            return {"ok": False, "error": f"Failed to write workflow file: {e}"}
        logging.info(f"Workflow saved from embedded ComfyUI: {target}")
        self.workflowSaved.emit(target)
        return {"ok": True, "path": target}

    def shutdown(self):
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:
            pass
