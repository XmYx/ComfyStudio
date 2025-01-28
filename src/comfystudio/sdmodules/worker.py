#!/usr/bin/env python
import logging
import os
import time

import requests

from qtpy.QtCore import QRunnable

from qtpy.QtCore import (
    QObject,
    Signal,
    Slot
)


class RenderWorkerSignals(QObject):
    """Signals for the RenderWorker."""
    finished = Signal()            # Emits when the worker finishes
    error = Signal(str)            # Emits on error
    result = Signal(dict, int, bool)  # Emits the result data, shotIndex, isVideo

class RenderWorker(QRunnable):
    """
    A worker that sends a workflow JSON to Comfy, obtains prompt_id,
    and polls for results. Once the result is obtained or an error
    occurs, it emits signals.
    """
    def __init__(self, workflow_json, shotIndex, isVideo, comfy_ip, parent=None):
        super().__init__()
        self.signals = RenderWorkerSignals()
        self.workflow_json = workflow_json
        self.shotIndex = shotIndex
        self.isVideo = isVideo
        self.comfy_ip = comfy_ip.rstrip("/")
        self._stop = False
        self.parent = parent

    @Slot()
    def run(self):
        """Run the worker: send prompt, wait for necessary files, poll for results, emit signals."""
        try:
            # Wait for any parameters that depend on previous results to have valid file paths
            params = self.workflow_json.get("parameters", {}).get("params", [])
            for param in params:
                if param.get("usePrevResultImage") or param.get("usePrevResultVideo"):
                    file_path = param.get("value", "")
                    if not os.path.exists(file_path):
                        logging.debug(f"Waiting for file {file_path} to exist for parameter '{param['name']}'")
                        timeout = 300  # seconds
                        interval = 1   # seconds
                        elapsed = 0
                        while not os.path.exists(file_path):
                            if self._stop:
                                logging.debug("RenderWorker stopped while waiting for file.")
                                self.signals.finished.emit()
                                return
                            time.sleep(interval)
                            elapsed += interval
                            if elapsed >= timeout:
                                self.signals.error.emit(f"Timeout waiting for file: {file_path}")
                                self.signals.finished.emit()
                                return
                        logging.debug(f"File {file_path} exists. Proceeding with rendering.")

            prompt_id = self.sendPrompt()
            if not prompt_id:
                self.signals.error.emit("No prompt_id returned from ComfyUI.")
                self.signals.finished.emit()
                return

            # Poll for results
            while not self._stop:
                url = f"{self.comfy_ip}/history/{prompt_id}"
                try:
                    resp = requests.get(url)
                    if resp.status_code == 200:
                        rd = resp.json()
                        if rd:
                            self.signals.result.emit(rd, self.shotIndex, self.isVideo)
                            break
                    elif resp.status_code == 404:
                        pass
                except Exception as e:
                    self.signals.error.emit(str(e))
                    break
                time.sleep(2)  # Poll every 2 seconds
        finally:
            self.signals.finished.emit()

    def stop(self):
        """Stop polling."""
        self._stop = True

    def sendPrompt(self):
        """Send the prompt to ComfyUI, return prompt_id or None."""
        url = f"{self.comfy_ip}/prompt"
        headers = {"Content-Type": "application/json"}
        data = {"prompt": self.workflow_json}
        try:
            r = requests.post(url, headers=headers, json=data)
            r.raise_for_status()
            js = r.json()
            pid = js.get("prompt_id", None)
            return pid
        except Exception as e:
            logging.error(f"Failed to send prompt to ComfyUI: {e}")
            return None