#!/usr/bin/env python
import copy
import json
import logging
import os
import random
import subprocess
import tempfile
import time
import urllib
from typing import List

import requests
from qtpy.QtCore import QThreadPool
from qtpy.QtCore import (
    Qt,
    QThread,
    QUrl,
    Signal
)
from qtpy.QtWidgets import (
    QFileDialog,
    QDialog,
    QMessageBox,
    QInputDialog
)

from comfystudio.sdmodules.comfy_installer import ComfyInstallerWizard
from comfystudio.sdmodules.comfy_datatypes import detect_media_type
from comfystudio.sdmodules.cs_datastruts import (
    RESULT_PORT,
    Shot,
    node_output_port_node_id,
    workflow_param_port_id,
)
from comfystudio.sdmodules.worker import (
    ComfyReadyPoller,
    ComfyWorker,
    CustomNodesSetupWorker,
    RenderWorker,
)


class ComfyStudioShotManager:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.shots: List[Shot] = []
        self.lastSelectedWorkflowIndex = {}
        self.currentShotIndex: int = -1

class ComfyStudioComfyHandler:

    renderSelectedSignal = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.renderSelectedSignal.connect(self.onRenderSelected)
        self.renderQueue = []  # We'll store shotIndices to render
        self.activeWorker = None  # The QThread worker checking results
        self.comfy_thread = None
        self.comfy_worker = None
        self.comfy_running = False
        self.render_mode = "per_workflow"
        # For progressive workflow rendering
        self.workflowQueue = {}   # Maps shotIndex -> list of (workflowIndex) to process
        self.shotInProgress = -1  # The shot we are currently processing
        self.workflowIndexInProgress = -1  # Current workflow index in that shot
    def _resolveComfyPaths(self, py_path, main_path):
        """
        Normalizes/repairs the configured ComfyUI paths so the launch always
        uses the install's own environment: directories are resolved to the
        real main.py / venv python (via comfy_locator), and a missing python
        is auto-detected next to the install. Returns (py_path, main_path) or
        (None, None) after showing a warning. Repaired values are saved back.
        """
        from comfystudio.sdmodules.comfy_locator import find_comfy_python, find_comfy_root

        orig_py, orig_main = py_path, main_path

        # main_path may point at the repo (or its parent) instead of main.py.
        if main_path and not os.path.isfile(main_path):
            root = find_comfy_root(main_path)
            if root:
                main_path = os.path.join(root, "main.py")
        if not main_path or not os.path.isfile(main_path):
            QMessageBox.warning(
                self, "Error",
                f"ComfyUI main.py not found:\n{orig_main}\n\n"
                "Use Settings → Select ComfyUI Installation to fix the path.")
            return None, None

        # py_path may point at a venv directory, or be stale/empty — resolve
        # to the interpreter of the environment installed with ComfyUI.
        if py_path and not os.path.isfile(py_path):
            for env_dir in (py_path, os.path.join(py_path, "venv"), os.path.join(py_path, ".venv")):
                candidate = os.path.join(
                    env_dir, "Scripts", "python.exe") if os.name == "nt" else os.path.join(env_dir, "bin", "python")
                if os.path.isfile(candidate):
                    py_path = candidate
                    break
        if not py_path or not os.path.isfile(py_path):
            py_path = find_comfy_python(os.path.dirname(main_path))
        if not py_path:
            QMessageBox.warning(
                self, "Error",
                "No Python environment found for ComfyUI.\n\n"
                "Use Settings → Select ComfyUI Installation (or the installer "
                "wizard) to configure the environment ComfyUI runs with.")
            return None, None

        if (py_path, main_path) != (orig_py, orig_main):
            self.settingsManager.set("comfy_py_path", py_path)
            self.settingsManager.set("comfy_main_path", main_path)
            self.settingsManager.save()
            self.appendLog(f"Resolved ComfyUI paths: {py_path} {main_path}")
        return py_path, main_path

    def _missingComfyDeps(self, py_path):
        """
        Fast preflight: which core ComfyUI packages are absent from the
        environment at py_path? Uses find_spec (no imports, so checking torch
        is cheap). Returns [] when everything is present or the check fails.
        """
        code = ("import importlib.util\n"
                "mods = ['torch', 'yaml', 'aiohttp', 'PIL', 'safetensors']\n"
                "print(','.join(m for m in mods if importlib.util.find_spec(m) is None))")
        try:
            result = subprocess.run([py_path, "-c", code],
                                    capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                logging.warning(f"Dependency preflight failed: {result.stderr.strip()}")
                return []
            return [m for m in result.stdout.strip().split(",") if m]
        except Exception as e:
            logging.warning(f"Dependency preflight failed: {e}")
            return []

    def _installComfyDeps(self, py_path, main_path):
        """
        Installs ComfyUI's requirements.txt into the environment at py_path
        (background thread, resilient two-phase install: optional packages
        like comfy-angle that have no wheels for a platform are skipped with a
        warning instead of aborting everything), then relaunches startComfy on
        success.
        """
        requirements = os.path.join(os.path.dirname(main_path), "requirements.txt")
        if not os.path.isfile(requirements):
            QMessageBox.warning(self, "Error",
                                f"requirements.txt not found next to main.py:\n{requirements}")
            return

        from comfystudio.sdmodules.comfy_locator import MIN_COMFY_PYTHON, get_python_version
        version = get_python_version(py_path)
        if version and version < MIN_COMFY_PYTHON:
            need = ".".join(str(v) for v in MIN_COMFY_PYTHON)
            QMessageBox.warning(
                self, "Python Too Old",
                f"The ComfyUI environment uses Python {version[0]}.{version[1]}, but "
                f"ComfyUI's requirements need Python {need}+ — the install would "
                "fail.\n\nUse Settings → Install/Update ComfyUI to set up a newer "
                "environment.")
            return

        from comfystudio.sdmodules.comfy_installer import RequirementsInstallWorker

        self.status_widgets["statusMessage"].setText("Installing ComfyUI dependencies...")
        self.appendLog(f"Installing ComfyUI dependencies into {py_path} ...")
        self._deps_thread = QThread()
        self._deps_worker = RequirementsInstallWorker(py_path, requirements)
        self._deps_worker.moveToThread(self._deps_thread)
        self._deps_thread.started.connect(self._deps_worker.run)
        self._deps_worker.log.connect(self.appendLog)

        def on_done(success, failed_essential, failed_optional):
            self._deps_thread.quit()
            if failed_optional:
                self.appendLog("Skipped optional ComfyUI packages (unavailable on this "
                               "platform): " + ", ".join(failed_optional))
            still_missing = self._missingComfyDeps(py_path)
            if not success or still_missing:
                self.status_widgets["statusMessage"].setText("Dependency install incomplete.")
                problems = failed_essential + [m for m in still_missing
                                               if m not in failed_essential]
                QMessageBox.warning(
                    self, "Dependencies Missing",
                    "These packages could not be installed:\n"
                    f"{', '.join(problems)}\n\nCheck the Terminal Output log.")
            else:
                self.status_widgets["statusMessage"].setText(
                    "Dependencies installed. Starting ComfyUI...")
                self.startComfy()

        self._deps_worker.finished.connect(on_done)
        self._deps_thread.start()

    def startComfy(self):
        """
        Launches the ComfyUI process in a separate thread using ComfyWorker.
        Always runs ComfyUI with the environment installed for it (paths are
        resolved/repaired first, and missing dependencies can be installed
        into that environment before launch).
        """
        if self.comfy_running:
            QMessageBox.information(self, "Info", "ComfyUI is already running.")
            return

        py_path = self.settingsManager.get("comfy_py_path")
        main_path = self.settingsManager.get("comfy_main_path")
        if py_path or main_path:
            py_path, main_path = self._resolveComfyPaths(py_path, main_path)
            if not py_path or not main_path:
                return

            missing = self._missingComfyDeps(py_path)
            if missing:
                box = QMessageBox(self)
                box.setWindowTitle("ComfyUI Dependencies Missing")
                box.setText(
                    "The ComfyUI environment is missing these packages:\n"
                    f"{', '.join(missing)}\n\n{py_path}")
                install_btn = box.addButton("Install Now", QMessageBox.ButtonRole.AcceptRole)
                box.addButton("Start Anyway", QMessageBox.ButtonRole.DestructiveRole)
                cancel_btn = box.addButton(QMessageBox.StandardButton.Cancel)
                box.exec()
                if box.clickedButton() is install_btn:
                    self._installComfyDeps(py_path, main_path)
                    return
                if box.clickedButton() is cancel_btn:
                    return

            # Create the worker and thread
            self.comfy_thread = QThread()
            self.comfy_worker = ComfyWorker(py_path=py_path, main_path=main_path)
            self.comfy_worker.moveToThread(self.comfy_thread)

            # Connect signals and slots
            self.comfy_thread.started.connect(self.comfy_worker.run)
            self.comfy_worker.log_message.connect(self.appendLog)
            self.comfy_worker.error.connect(self.appendLog)
            self.comfy_worker.finished.connect(self.comfy_thread.quit)
            self.comfy_worker.finished.connect(self.comfy_worker.deleteLater)
            self.comfy_thread.finished.connect(self.comfy_thread.deleteLater)
            self.comfy_worker.finished.connect(lambda: self.onComfyFinishedRunning())

            # Start the thread
            self.comfy_thread.start()
            self.comfy_running = True
            self.status_widgets['statusMessage'].setText("ComfyUI starting...")
            self.appendLog("ComfyUI process started.")
            # Announce the server (and load the embedded browser) once it
            # actually answers HTTP.
            self._startComfyReadyPoll()
        else:
            QMessageBox.warning(self, "Error", "Comfy paths not set in settings.")

    def _startComfyReadyPoll(self):
        self._stopComfyReadyPoll()
        url = self.getComfyApiUrl()
        self._comfy_ready_thread = QThread()
        self._comfy_ready_poller = ComfyReadyPoller(url)
        self._comfy_ready_poller.moveToThread(self._comfy_ready_thread)
        self._comfy_ready_thread.started.connect(self._comfy_ready_poller.run)
        self._comfy_ready_poller.ready.connect(self.onComfyReady)
        self._comfy_ready_poller.timed_out.connect(
            lambda u: self.appendLog(f"ComfyUI has not responded at {u} yet — "
                                     "check the log above for startup errors."))
        self._comfy_ready_poller.finished.connect(self._comfy_ready_thread.quit)
        self._comfy_ready_thread.start()

    def _stopComfyReadyPoll(self):
        poller = getattr(self, "_comfy_ready_poller", None)
        if poller is not None:
            poller.stop()

    def onComfyReady(self, url):
        """The ComfyUI server answered: announce it and show it in the app."""
        self.status_widgets["statusMessage"].setText(f"ComfyUI is running at {url}")
        self.appendLog(f"ComfyUI is reachable at {url}")
        view = getattr(self, "webBrowserView", None)
        if view is not None:
            current = view.url().toString()
            if (not current or current == "about:blank"
                    or not getattr(self, "_webBrowserLoadOk", False)):
                view.setUrl(QUrl(url))
    def stopComfy(self):
        """
        Signals the ComfyWorker to terminate the ComfyUI process.
        Cleans up the thread and worker.
        """
        if self.comfy_running and self.comfy_worker:
            try:
                self._stopComfyReadyPoll()
                self.comfy_worker.stop()
                self.comfy_running = False
                self.status_widgets["statusMessage"].setText("Stopping ComfyUI...")
                self.appendLog("Stopping ComfyUI process...")
                # The worker's 'finished' signal will handle further cleanup
            except Exception as e:
                self.status_widgets["statusMessage"].setText(str(e))
                self.appendLog(repr(e))
        else:
            QMessageBox.information(self, "Info", "ComfyUI is not running.")

    def onComfyFinishedRunning(self):
        """
        Handles the completion of the ComfyUI process.
        """
        self.comfy_running = False
        self.status_widgets["statusMessage"].setText("ComfyUI stopped.")
        self.appendLog("ComfyUI process has stopped.")
    def startNextRender(self):
        """
        Starts the next render task based on the current render mode.
        """
        if not self.renderQueue:
            self.shotInProgress = -1
            self.workflowIndexInProgress = -1
            self.status_widgets["statusMessage"].setText("Render queue is empty.")
            return

        if isinstance(self.renderQueue[0], int):
            # 'Per Shot' mode
            self.render_mode = 'per_shot'
            self.shotInProgress = self.renderQueue.pop(0)
            self.initWorkflowQueueForShot(self.shotInProgress)
            self.workflowIndexInProgress = 0
            self.processNextWorkflow()
        elif isinstance(self.renderQueue[0], tuple) and len(self.renderQueue[0]) == 2:
            # 'Per Workflow' mode
            self.render_mode = 'per_workflow'
            shot_idx, wf_idx = self.renderQueue.pop(0)
            self.executeWorkflow(shot_idx, wf_idx)
        else:
            logging.error(f"Invalid renderQueue item: {self.renderQueue[0]}")
            self.renderQueue.pop(0)
            self.startNextRender()


    def onRenderSelected(self):
        """
        Render only the currently selected shots based on the user's choice of render mode.
        If multiple shots are selected, prompt the user to choose between 'Per Shot' or 'Per Workflow' rendering.
        """
        selected_items = self.listWidget.selectedItems()
        if not selected_items:
            self.listWidget.setCurrentRow(0)
            selected_items = self.listWidget.selectedItems()

        if len(selected_items) > 1:
            # Prompt the user to choose render mode
            render_mode, ok = QInputDialog.getItem(
                self,
                "Select Render Mode",
                "Choose how to queue the render tasks:",
                ["Per Shot", "Per Workflow"],
                0,
                False
            )
            if not ok:
                return
            chosen_mode = 'per_shot' if render_mode == "Per Shot" else 'per_workflow'
        else:
            # Default to 'Per Shot' if only one shot is selected
            chosen_mode = 'per_shot'

        # First stop any current rendering processes
        self.stopRendering()

        if chosen_mode == 'per_shot':
            # Enqueue each selected shot to render all its enabled workflows
            for it in selected_items:
                idx = it.data(Qt.ItemDataRole.UserRole)
                if idx is not None and isinstance(idx, int) and 0 <= idx < len(self.shots):
                    self.renderQueue.append(idx)
        elif chosen_mode == 'per_workflow':
            # Enqueue workflows interleaved across selected shots, each shot's
            # workflows in graph (topological) order.
            selected_indices = [
                it.data(Qt.ItemDataRole.UserRole) for it in selected_items
                if it.data(Qt.ItemDataRole.UserRole) is not None and isinstance(it.data(Qt.ItemDataRole.UserRole), int)
            ]
            orders = {idx: self.topoSortedWorkflowIndices(self.shots[idx]) for idx in selected_indices}
            max_workflows = max((len(o) for o in orders.values()), default=0)
            for k in range(max_workflows):
                for shot_idx in selected_indices:
                    order = orders[shot_idx]
                    if k < len(order):
                        self.renderQueue.append((shot_idx, order[k]))
        else:
            QMessageBox.warning(self, "Warning", f"Unknown render mode: {chosen_mode}")
            return

        # Start rendering the new queue
        self.startNextRender()

    def onRenderAll(self):
        """
        Render all shots based on the user's choice of render mode.
        If multiple shots are present, prompt the user to choose between 'Per Shot' or 'Per Workflow' rendering.
        """
        if not self.shots:
            # QMessageBox.warning(self, "Warning", "No shots available to render.")
            return

        # if len(self.shots) > 1:
        #     # Prompt the user to choose render mode
        #     render_mode, ok = QInputDialog.getItem(
        #         self,
        #         "Select Render Mode",
        #         "Choose how to queue the render tasks:",
        #         ["Per Shot", "Per Workflow"],
        #         0,
        #         False
        #     )
        #     if not ok:
        #         return
        #     chosen_mode = 'per_shot' if render_mode == "Per Shot" else 'per_workflow'
        # else:
        #     # Default to 'Per Shot' if only one shot exists
        #     chosen_mode = 'per_shot'
        chosen_mode = 'per_workflow'
        # First stop any current rendering processes
        self.stopRendering()

        if chosen_mode == 'per_shot':
            # Enqueue all shots to render all their enabled workflows
            for idx in range(len(self.shots)):
                self.renderQueue.append(idx)
        elif chosen_mode == 'per_workflow':
            # Enqueue workflows interleaved across all shots, each shot's
            # workflows in graph (topological) order.
            orders = [self.topoSortedWorkflowIndices(shot) for shot in self.shots]
            max_workflows = max((len(o) for o in orders), default=0)
            for k in range(max_workflows):
                for shot_idx, order in enumerate(orders):
                    if k < len(order):
                        self.renderQueue.append((shot_idx, order[k]))
        else:
            # QMessageBox.warning(self, "Warning", f"Unknown render mode: {chosen_mode}")
            return

        # Start rendering if not already in progress
        if self.shotInProgress == -1 and self.renderQueue:
            self.startNextRender()
    def processNextWorkflow(self):
        if self.shotInProgress not in self.workflowQueue:
            self.shotInProgress = -1
            self.workflowIndexInProgress = -1
            if self.renderQueue:
                self.startNextRender()
            return

        wIndices = self.workflowQueue[self.shotInProgress]
        if self.workflowIndexInProgress >= len(wIndices):
            # Done with all workflows for this shot
            del self.workflowQueue[self.shotInProgress]
            self.shotInProgress = -1
            self.workflowIndexInProgress = -1
            if self.renderQueue:
                self.startNextRender()
            return

        currentWorkflowIndex = wIndices[self.workflowIndexInProgress]
        self.executeWorkflow(self.shotInProgress, currentWorkflowIndex)
    def initWorkflowQueueForShot(self, shotIndex):
        shot = self.shots[shotIndex]
        self.workflowQueue[shotIndex] = self.topoSortedWorkflowIndices(shot)

    def topoSortedWorkflowIndices(self, shot: Shot) -> List[int]:
        """
        Render order for a shot's enabled workflows: topological order of the
        shot graph, tie-broken by list index (no connections -> plain list
        order). On a cycle, warns and falls back to list order for the
        remaining workflows instead of aborting.
        """
        enabled = [i for i, wf in enumerate(shot.workflows) if wf.enabled]
        uid_to_idx = {shot.workflows[i].uid: i for i in enabled}
        deps = {i: set() for i in enabled}
        for conn in shot.graph.get("connections", []):
            src = uid_to_idx.get(conn.get("from", {}).get("wf"))
            dst = uid_to_idx.get(conn.get("to", {}).get("wf"))
            if src is None or dst is None or src == dst:
                continue
            deps[dst].add(src)

        order = []
        remaining = set(enabled)
        while remaining:
            ready = [i for i in sorted(remaining) if not (deps[i] & remaining)]
            if not ready:
                logging.warning(f"Workflow graph of shot '{shot.name}' contains a cycle; "
                                f"falling back to list order.")
                if hasattr(self, "status_widgets"):
                    self.status_widgets["statusMessage"].setText(
                        "Workflow graph contains a cycle — using list order for remaining workflows.")
                order.extend(sorted(remaining))
                break
            order.append(ready[0])
            remaining.discard(ready[0])
        return order

    def resolveGraphInputs(self, shot: Shot, workflow) -> set:
        """
        Fills this workflow's params from the shot graph connections targeting
        it. Returns the set of satisfied port ids so the legacy prev-result
        fallback can skip them. Mutates param values in place (same semantics
        as the legacy substitution, so signatures/version reuse keep working).
        """
        resolved = set()
        connections = shot.graph.get("connections", [])
        if not connections:
            return resolved
        by_uid = {wf.uid: wf for wf in shot.workflows}
        by_port = {workflow_param_port_id(p): p
                   for p in workflow.parameters.get("params", [])}
        for conn in connections:
            dst = conn.get("to", {})
            if dst.get("wf") != workflow.uid:
                continue
            param = by_port.get(dst.get("port"))
            src = conn.get("from", {})
            src_wf = by_uid.get(src.get("wf"))
            if param is None or src_wf is None:
                continue
            src_node_id = node_output_port_node_id(src.get("port"))
            if src.get("port") == RESULT_PORT or src_node_id is not None:
                value = None
                if src_node_id is not None:
                    # Exposed ComfyUI node output: the file that node produced
                    # on the last render (recorded in onComfyResult).
                    node_file = src_wf.lastNodeOutputs.get(src_node_id, "")
                    if node_file and os.path.exists(node_file):
                        value = node_file
                if value is None:
                    value = self._latestWorkflowOutput(shot, src_wf, param)
                if value is None:
                    logging.info(f"Graph input '{param['name']}' has no rendered source output "
                                 f"yet (from '{os.path.basename(src_wf.path)}').")
                    continue
            else:
                src_param = next(
                    (p for p in src_wf.parameters.get("params", [])
                     if workflow_param_port_id(p) == src.get("port")),
                    None
                )
                if src_param is None:
                    continue
                value = self._coerceParamValue(src_param.get("value"), param.get("type", "string"))
            print(f"[DEBUG] Graph override: '{param['name']}' <- "
                  f"'{src.get('port')}' of '{os.path.basename(src_wf.path)}' = {value}")
            param["value"] = value
            resolved.add(dst.get("port"))
        return resolved

    @staticmethod
    def _latestWorkflowOutput(shot: Shot, src_wf, target_param):
        """Best existing file for a workflow's Result port, or None."""
        if src_wf.lastOutput and os.path.exists(src_wf.lastOutput):
            return src_wf.lastOutput
        for version in reversed(src_wf.versions):
            out = version.get("output")
            if out and os.path.exists(out):
                # Backfill for projects saved before lastOutput existed.
                src_wf.lastOutput = out
                src_wf.lastOutputIsVideo = bool(version.get("is_video"))
                src_wf.lastOutputType = (version.get("output_type")
                                         or detect_media_type(out))
                return out
        # Last resort: the shot-level outputs (pre-graph projects).
        want_video = src_wf.lastOutputIsVideo or bool(target_param.get("usePrevResultVideo"))
        candidates = [shot.videoPath, shot.stillPath] if want_video else [shot.stillPath, shot.videoPath]
        for path in candidates:
            if path and os.path.exists(path):
                return path
        return None

    @staticmethod
    def _coerceParamValue(value, ptype):
        try:
            if ptype == "int":
                return int(value)
            if ptype == "float":
                return float(value)
            if ptype == "bool":
                return bool(value) and str(value).lower() not in ("false", "0", "")
            return "" if value is None else str(value)
        except (TypeError, ValueError):
            return value
    def computeWorkflowSignature(self, shot: Shot, workflowIndex: int) -> str:

        import hashlib, json
        workflow = shot.workflows[workflowIndex]

        data_struct = {
            "shotParams": shot.params,  # all shot params
            "workflowParams": workflow.parameters,
            "workflowPath": workflow.path,
            "isVideo": workflow.isVideo,
            # Re-wiring the shot graph must invalidate cached outputs.
            "graphConnections": shot.graph.get("connections", [])
        }
        signature_str = json.dumps(data_struct, sort_keys=True)
        return hashlib.md5(signature_str.encode("utf-8")).hexdigest()
    def computeRenderSignature(self, shot: Shot, isVideo=False):
        import hashlib
        relevantShotParams = []
        for workflow in shot.workflows:
            if workflow.isVideo == isVideo:
                relevantShotParams.append({
                    "workflow_path": workflow.path,
                    "enabled": workflow.enabled,
                    "parameters": workflow.parameters
                })
        for p in shot.params:
            relevantShotParams.append({
                "name": p["name"],
                "type": p.get("type", "string"),
                "value": p.get("value", "")
            })
        data_struct = {
            "shotParams": sorted(relevantShotParams, key=lambda x: x.get("name", x.get("workflow_path", "")))
        }
        signature_str = json.dumps(data_struct, sort_keys=True)
        signature = hashlib.md5(signature_str.encode("utf-8")).hexdigest()

        # Debugging: Log the signature generation
        logging.debug(f"Computed {'Video' if isVideo else 'Still'} Signature: {signature} for shot '{shot.name}'")

        return signature

    def executeWorkflow(self, shotIndex, workflowIndex):
        """
        Executes a workflow for a given shot, sending its JSON to ComfyUI via a RenderWorker.
        Only updates the relevant inputs in the existing JSON keys (no renumbering).
        Adds debug prints to show exactly what parameters get overridden.
        Overrides a node's input ONLY if node_id is listed in that param's "nodeIDs".
        """
        shot = self.shots[shotIndex]
        workflow = shot.workflows[workflowIndex]
        isVideo = workflow.isVideo
        wf_params = workflow.parameters.get("params", [])

        # Resolve dynamic inputs BEFORE computing the signature so the reuse
        # checks below compare the values that will actually be rendered.
        # Graph connections win; legacy prev-result flags fill whatever the
        # graph didn't satisfy.
        graph_resolved = self.resolveGraphInputs(shot, workflow)
        if self.render_mode == 'per_shot':
            if self.workflowIndexInProgress > 0:
                prevWorkflowIndex = self.workflowQueue.get(shotIndex, [])[self.workflowIndexInProgress - 1]
            else:
                prevWorkflowIndex = None
        else:
            prevWorkflowIndex = workflowIndex - 1 if workflowIndex > 0 else None
        if prevWorkflowIndex is not None:
            # The chain may mix image and video workflows in any order: a param
            # asking for the previous image gets the shot's latest still and one
            # asking for the previous video gets the latest video, regardless of
            # what type the immediately preceding workflow was declared as.
            prevVideo = shot.videoPath if (shot.videoPath and os.path.exists(shot.videoPath)) else None
            prevImage = shot.stillPath if (shot.stillPath and os.path.exists(shot.stillPath)) else None
            for param in wf_params:
                if workflow_param_port_id(param) in graph_resolved:
                    continue
                if param.get("usePrevResultImage") and prevImage:
                    print(f"[DEBUG] Setting param '{param['name']}' to prevImage: {prevImage}")
                    param["value"] = prevImage
                if param.get("usePrevResultVideo") and prevVideo:
                    print(f"[DEBUG] Setting param '{param['name']}' to prevVideo: {prevVideo}")
                    param["value"] = prevVideo

        currentSignature = self.computeWorkflowSignature(shot, workflowIndex)

        # First, check if an identical version already exists.
        existing_output = None
        # Compare the current workflow parameters with each saved version.
        for version in workflow.versions:
            # Here we assume that if the saved parameters (snapshot) are equal to the current
            # workflow.parameters then nothing has changed. You may need to adjust this
            # comparison if your workflow parameters include extra keys.
            if version["params"] == workflow.parameters and (
                    (version["is_video"] and isVideo) or ((not version["is_video"]) and not isVideo)):
                if os.path.exists(version["output"]):
                    existing_output = version["output"]
                    break

        if existing_output:
            print(f"[DEBUG] Reusing existing rendered output for shot '{shot.name}' in workflow {workflowIndex}.")
            # Update the shot with the output from the saved version.
            if isVideo:
                shot.videoPath = existing_output
                shot.videoVersions.append(existing_output)
                shot.currentVideoVersion = len(shot.videoVersions) - 1
                shot.lastVideoSignature = currentSignature
            else:
                shot.stillPath = existing_output
                shot.imageVersions.append(existing_output)
                shot.currentImageVersion = len(shot.imageVersions) - 1
                shot.lastStillSignature = currentSignature

            # self.updateList()
            # self.shotRenderComplete.emit(shotIndex, workflowIndex, existing_output, isVideo)
            if self.render_mode == 'per_shot':
                self.workflowIndexInProgress += 1
                self.processNextWorkflow()
            elif self.render_mode == 'per_workflow':
                self.startNextRender()
            return
        alreadyRendered = (shot.videoPath if isVideo else shot.stillPath)
        if not alreadyRendered:
            for other_shot_index, other_shot in enumerate(self.shots):
                if other_shot_index == shotIndex:
                    continue  # Skip current shot
                for other_wf_index, other_workflow in enumerate(other_shot.workflows):
                    if other_workflow.path != workflow.path:
                        continue  # Different workflow path
                    other_signature = self.computeWorkflowSignature(other_shot, other_wf_index)
                    if other_signature == currentSignature:
                        # Check if the other shot has a valid output
                        if isVideo and other_shot.videoPath and os.path.exists(other_shot.videoPath):
                            print(f"[DEBUG] Reusing video from shot '{other_shot.name}' for current shot '{shot.name}'.")
                            shot.videoPath = other_shot.videoPath
                            shot.videoVersions.append(other_shot.videoPath)
                            shot.currentVideoVersion = len(shot.videoVersions) - 1
                            shot.lastVideoSignature = other_shot.lastVideoSignature
                            workflow.lastSignature = currentSignature
                            # self.updateList()
                            # self.shotRenderComplete.emit(shotIndex, workflowIndex, other_shot.videoPath, True)
                        elif not isVideo and other_shot.stillPath and os.path.exists(other_shot.stillPath):
                            print(f"[DEBUG] Reusing image from shot '{other_shot.name}' for current shot '{shot.name}'.")
                            shot.stillPath = other_shot.stillPath
                            shot.imageVersions.append(other_shot.stillPath)
                            shot.currentImageVersion = len(shot.imageVersions) - 1
                            shot.lastStillSignature = other_shot.lastStillSignature
                            workflow.lastSignature = currentSignature
                            # self.updateList()
                            # self.shotRenderComplete.emit(shotIndex, workflowIndex, other_shot.stillPath, False)

        alreadyRendered = (shot.videoPath if isVideo else shot.stillPath)
        if workflow.lastSignature == currentSignature and alreadyRendered and os.path.exists(alreadyRendered):
            print(f"[DEBUG] Skipping workflow {workflowIndex} for shot '{shot.name}' because "
                  f"params haven't changed and a valid file exists.")
            if self.render_mode == 'per_shot':
                self.workflowIndexInProgress += 1
                self.processNextWorkflow()
            elif self.render_mode == 'per_workflow':
                self.startNextRender()
            return

        try:
            # Validated load (handles missing files, bad JSON and UI-format exports).
            workflow_json = self.loadWorkflowJson(workflow.path)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load workflow: {e}")
            if self.render_mode == 'per_shot':
                self.workflowIndexInProgress += 1
                self.processNextWorkflow()
            elif self.render_mode == 'per_workflow':
                self.startNextRender()
            return

        # Prepare any param overrides for workflow_json if needed
        local_params = copy.deepcopy(shot.params)

        print("[DEBUG] Original workflow JSON keys:")
        for k in workflow_json.keys():
            print("       ", k)

        # Override workflow_json with local_params + wf_params
        # (dynamic values were already resolved above, before the signature)
        for node_id, node_data in workflow_json.items():
            inputs_dict = node_data.get("inputs", {})
            meta_title = node_data.get("_meta", {}).get("title", "").lower()

            # 1) Shot-level param overrides (with nodeIDs check)
            for input_key in list(inputs_dict.keys()):
                ikey_lower = str(input_key).lower()
                for param in local_params:
                    # If param is for this node_id
                    node_ids = param.get("nodeIDs", [])
                    if str(node_id) not in node_ids:
                        continue  # skip if this param is not meant for this node

                    # If the param name matches this input key
                    if param["name"].lower() == ikey_lower:
                        old_val = inputs_dict[input_key]
                        new_val = param["value"]
                        print(f"[DEBUG] Overriding node '{node_id}' input '{input_key}' "
                              f"from '{old_val}' to '{new_val}' (SHOT-level param)")
                        inputs_dict[input_key] = new_val

            # 2) Workflow-level param overrides (with nodeIDs check)
            for input_key in list(inputs_dict.keys()):
                ikey_lower = str(input_key).lower()
                for param in wf_params:
                    node_ids = param.get("nodeIDs", [])
                    if str(node_id) not in node_ids:
                        continue
                    if param["name"].lower() == ikey_lower:
                        old_val = inputs_dict[input_key]
                        new_val = param["value"]
                        print(f"[DEBUG] Overriding node '{node_id}' input '{input_key}' "
                              f"from '{old_val}' to '{new_val}' (WF-level param)")
                        inputs_dict[input_key] = new_val

            # 3) Special override for "positive prompt" if found in shot params
            if "positive prompt" in [p["name"].lower() for p in local_params] and "positive prompt" in meta_title:
                for param in local_params:
                    if param["name"].lower() == "positive prompt":
                        node_ids = param.get("nodeIDs", [])
                        # If no nodeIDs on the param, or the node_id is listed, we override 'text'
                        if not node_ids or str(node_id) in node_ids:
                            old_val = inputs_dict.get("text", "")
                            new_val = param["value"]
                            print(f"[DEBUG] Overriding node '{node_id}' 'text' from '{old_val}' to '{new_val}' "
                                  f"(POSITIVE PROMPT param)")
                            inputs_dict["text"] = new_val

        # Create and start the RenderWorker to handle submission + result polling
        comfy_ip = self.getComfyApiUrl()
        worker = RenderWorker(
            workflow_json=workflow_json,
            shotIndex=shotIndex,
            isVideo=isVideo,
            comfy_ip=comfy_ip,
            parent=self
        )
        # Connect signals
        worker.signals.result.connect(lambda data, si, iv: self.onComfyResult(data, si, workflowIndex))
        worker.signals.error.connect(self.onComfyError)
        worker.signals.finished.connect(self.onComfyFinished)

        # Show final structure in debug before sending
        # print("[DEBUG] Final workflow JSON structure before sending:")
        # for k, v in workflow_json.items():
        #     print("       Node ID:", k)
        #     print("               ", v)

        # Start
        self.status_widgets["statusMessage"].setText(f"Rendering {shot.name} - Workflow {workflowIndex + 1}/{len(shot.workflows)} ...")
        self.activeWorker = worker  # Keep a reference to prevent garbage collection
        QThreadPool.globalInstance().start(worker)

    def onComfyResult(self, result_data, shotIndex, workflowIndex):
        """
        Handle the result data returned by a RenderWorker for the given shot/workflow.
        Ensures the shot's stillPath or videoPath is set before the next workflow runs.
        """
        shot = self.shots[shotIndex]
        workflow = shot.workflows[workflowIndex]

        # We'll brute force the single key from the result_data
        main_key = list(result_data.keys())[0]
        outputs = result_data[main_key].get("outputs", {})
        if not outputs:
            self.workflowIndexInProgress += 1
            self.processNextWorkflow()
            return

        # One produced file per output node (first image, else gif/video, else
        # audio); the first node's file stays the workflow's main result, the
        # rest feed exposed node-output ports on the shot graph. The media type
        # is detected from ComfyUI's output bucket, refined by file extension.
        node_files = {}  # node_id -> (comfy_path, media_type)
        for node_id, output_data in outputs.items():
            entry = None
            for image_info in output_data.get("images", []):
                filename = image_info.get("filename")
                if filename:
                    subfolder = image_info.get("subfolder", "")
                    rel = os.path.join(subfolder, filename) if subfolder else filename
                    # A SaveImage node emits .png; but some nodes stuff videos in
                    # the "images" bucket — trust the extension over the bucket.
                    entry = (rel, detect_media_type(filename, default="IMAGE"))
                    break
            if entry is None:
                for gif_info in output_data.get("gifs", []):
                    filename = gif_info.get("filename")
                    if filename:
                        subfolder = gif_info.get("subfolder", "")
                        rel = os.path.join(subfolder, filename) if subfolder else filename
                        # The "gifs" bucket is what VHS/video nodes use — animated
                        # gifs and real videos alike; default such files to VIDEO.
                        entry = (rel, detect_media_type(filename, default="VIDEO"))
                        break
            if entry is None:
                for audio_info in output_data.get("audio", []):
                    filename = audio_info.get("filename")
                    if filename:
                        subfolder = audio_info.get("subfolder", "")
                        rel = os.path.join(subfolder, filename) if subfolder else filename
                        entry = (rel, detect_media_type(filename, default="AUDIO"))
                        break
            if entry is not None:
                node_files[str(node_id)] = entry

        # Pick the "final" output that becomes this workflow's Result. Normally
        # the first produced file; but when the user pinned an output type
        # override, prefer the first file matching it (a workflow may emit e.g.
        # both an image and audio — the override says which one matters).
        final_path = None
        final_type = "IMAGE"
        ordered = list(node_files.values())
        override = (getattr(workflow, "outputType", "") or "").upper()
        if override:
            match = next((e for e in ordered if e[1] == override), None)
            if match is not None:
                final_path, final_type = match
        if final_path is None and ordered:
            final_path, final_type = ordered[0]

        if final_path:
            # Download from Comfy's output folder to our project or temp folder
            project_folder = None
            if hasattr(self, 'currentFilePath') and self.currentFilePath:
                project_folder = os.path.dirname(self.currentFilePath)
            else:
                dlg = QFileDialog(self, "Select a folder to store shot versions")
                dlg.setFileMode(QFileDialog.FileMode.Directory)
                if dlg.exec() == QDialog.DialogCode.Accepted:
                    project_folder = dlg.selectedFiles()[0]
                    self.currentFilePath = os.path.join(project_folder, "untitled.json")
                else:
                    project_folder = tempfile.gettempdir()

            shot_name = shot.name.replace(" ", "_")
            version_number = len(workflow.versions) + 1  # 1-indexed version
            timestamp = int(time.time())
            new_full = None
            workflow.lastNodeOutputs = {}
            # Subfolder per media type: stills / videos / audio / text.
            _type_subfolder = {"IMAGE": "stills", "VIDEO": "videos",
                               "AUDIO": "audio", "STRING": "text"}
            for out_node_id, (comfy_path, media_type) in node_files.items():
                local_path = self.downloadComfyFile(comfy_path)
                if not local_path:
                    continue
                ext = os.path.splitext(local_path)[1]
                # Choose subfolder based on the detected media type.
                subfolder = os.path.join(
                    project_folder, _type_subfolder.get(media_type, "stills"))
                if not os.path.exists(subfolder):
                    os.makedirs(subfolder, exist_ok=True)
                is_final = (comfy_path == final_path)
                # shot_workflow_version_timestamp.ext; secondary output nodes
                # get a node-id marker so their files don't collide.
                if is_final:
                    new_name = f"{shot_name}_{workflowIndex}_{version_number}_{timestamp}{ext}"
                else:
                    new_name = f"{shot_name}_{workflowIndex}_n{out_node_id}_{version_number}_{timestamp}{ext}"
                dest = os.path.join(subfolder, new_name)
                try:
                    with open(local_path, "rb") as src, open(dest, "wb") as dst:
                        dst.write(src.read())
                except Exception:
                    dest = local_path
                # Feeds exposed node-output ports on the shot graph.
                workflow.lastNodeOutputs[out_node_id] = dest
                if is_final:
                    new_full = dest

            if new_full:
                final_is_video = (final_type == "VIDEO")
                # --- IMPORTANT: Update the Shot with the new file path *now*, so the next workflow can see it ---
                # Only image/video results have a dedicated shot slot; audio and
                # text results still chain through workflow.lastOutput below.
                if final_is_video:
                    shot.videoPath = new_full
                    shot.videoVersions.append(new_full)
                    shot.currentVideoVersion = len(shot.videoVersions) - 1
                    shot.lastVideoSignature = self.computeRenderSignature(shot, isVideo=True)
                elif final_type == "IMAGE":
                    shot.stillPath = new_full
                    shot.imageVersions.append(new_full)
                    shot.currentImageVersion = len(shot.imageVersions) - 1
                    shot.lastStillSignature = self.computeRenderSignature(shot, isVideo=False)

                new_version = {
                    "params": copy.deepcopy(workflow.parameters),  # snapshot of current workflow params
                    "output": new_full,  # path to the rendered result
                    "is_video": final_is_video,
                    "output_type": final_type,
                    "timestamp": time.time()  # optionally, store when this version was created
                }

                workflow.versions.append(new_version)
                # This workflow's Result port (feeds graph connections).
                workflow.lastOutput = new_full
                workflow.lastOutputType = final_type
                workflow.lastOutputIsVideo = final_is_video

                # Mark this workflow's own signature, so we don't re-render if nothing changed
                workflow.lastSignature = self.computeRenderSignature(shot, isVideo=workflow.isVideo)

                # Update the UI / shot listing
                self.updateList()

                # Notify other parts (e.g. preview dock)
                self.shotRenderComplete.emit(shotIndex, workflowIndex, new_full, final_is_video)

        # Move on regardless of success/failure to next workflow in queue
        # self.workflowIndexInProgress += 1
        # self.processNextWorkflow()
        if self.render_mode == 'per_shot':
            # Move to the next workflow in the current shot
            self.workflowIndexInProgress += 1
            self.processNextWorkflow()
        elif self.render_mode == 'per_workflow':
            # Immediately start the next workflow across shots
            self.startNextRender()
        else:
            logging.error(f"Unknown render mode: {self.render_mode}")
            self.startNextRender()


    def onComfyError(self, error_msg):
        QMessageBox.warning(self, "Comfy Error", f"Error polling ComfyUI: {error_msg}")
        if self.render_mode == 'per_workflow':
            self.startNextRender()
        elif self.render_mode == 'per_shot':
            self.workflowIndexInProgress += 1
            self.processNextWorkflow()

    def onComfyFinished(self):
        """
        Worker is done, proceed with the next workflow or shot.
        """
        # self.activeWorker = None
        self.status_widgets["statusMessage"].setText("Ready")
        # self.workflowIndexInProgress += 1
        # self.processNextWorkflow()

    def downloadComfyFile(self, comfy_filename):
        comfy_ip = self.getComfyApiUrl()
        sub_parts = comfy_filename.replace("\\", "/").split("/")
        params = {}
        if len(sub_parts) > 1:
            sub = "/".join(sub_parts[:-1])
            fil = sub_parts[-1]
            params["subfolder"] = sub
            params["filename"] = fil
        else:
            params["filename"] = comfy_filename
        params["type"] = "output"
        query = urllib.parse.urlencode(params)
        url = f"{comfy_ip}/view?{query}"
        try:
            r = requests.get(url)
            r.raise_for_status()
            file_data = r.content
            suffix = os.path.splitext(comfy_filename)[-1]
            temp_path = os.path.join(tempfile.gettempdir(), f"comfy_result_{random.randint(0,999999)}{suffix}")
            with open(temp_path, "wb") as f:
                f.write(file_data)
            return temp_path
        except:
            return None
    def stopRendering(self):
        """
        Stop any current rendering processes by clearing the queue
        and stopping the active worker if it exists.
        """
        self.renderQueue.clear()
        self.shotInProgress = -1
        self.workflowIndexInProgress = -1
        if self.activeWorker:
            self.activeWorker.stop()
            self.activeWorker = None
        self.status_widgets["statusMessage"].setText("Render queue cleared.")

    def setupCustomNodes(self):
        """
        Initiates the setup of custom nodes in a separate thread to keep the UI responsive.
        """
        # Define the configuration file path
        config_file = os.path.join(os.path.dirname(__file__), "..", "defaults", "custom_nodes.json")

        # Retrieve paths from settingsManager
        comfy_exec_path = self.settingsManager.get("comfy_main_path")
        venv_python_path = self.settingsManager.get("comfy_py_path")

        if not comfy_exec_path:
            QMessageBox.warning(self, "Error", "ComfyUI main.py path not set in settings.")
            return

        if not venv_python_path:
            QMessageBox.warning(self, "Error", "ComfyUI virtual environment path not set in settings.")
            return

        # Determine the virtual environment directory
        if os.path.isfile(venv_python_path):
            venv_dir = os.path.dirname(os.path.dirname(venv_python_path))
        else:
            venv_dir = os.path.dirname(os.path.dirname(venv_python_path))  # Fallback

        # Create the worker and thread
        self.custom_nodes_thread = QThread()
        self.custom_nodes_worker = CustomNodesSetupWorker(
            config_file=config_file,
            venv_path=venv_dir,
            comfy_exec_path=comfy_exec_path
        )
        self.custom_nodes_worker.moveToThread(self.custom_nodes_thread)

        # Connect signals and slots
        self.custom_nodes_thread.started.connect(self.custom_nodes_worker.run)
        self.custom_nodes_worker.log_message.connect(self.appendLog)
        self.custom_nodes_worker.finished.connect(self.custom_nodes_thread.quit)
        # self.custom_nodes_worker.finished.connect(self.custom_nodes_worker.deleteLater)
        # self.custom_nodes_thread.finished.connect(self.custom_nodes_thread.deleteLater)
        self.custom_nodes_worker.finished.connect(lambda: QMessageBox.information(self, "Info", "Custom nodes setup completed."))

        # Start the thread
        self.custom_nodes_thread.start()

        # Log the initiation
        self.appendLog("Starting custom nodes setup...")
    def startComfyInstallerWizard(self):
        """
        Launches the Comfy Installer Wizard to install/update ComfyUI and its dependencies.
        """
        wizard = ComfyInstallerWizard(parent=self, settings_manager=self.settingsManager, log_callback=self.appendLog)
        wizard.exec()
        # A (new) install brings its own user/default/workflows library.
        if hasattr(self, "loadWorkflows"):
            self.loadWorkflows()

    def selectComfyInstallation(self):
        """
        Points ComfyStudio at an existing ComfyUI installation: the user picks
        the directory, main.py and the environment's Python are auto-detected
        (venv/.venv beside or inside the repo, or a portable bundle) and stored
        in settings.
        """
        from comfystudio.sdmodules.comfy_locator import detect_comfy_installation

        start_dir = ""
        current_main = self.settingsManager.get("comfy_main_path", "")
        if current_main:
            start_dir = os.path.dirname(os.path.dirname(current_main))
        directory = QFileDialog.getExistingDirectory(
            self, "Select ComfyUI Installation Directory", start_dir)
        if not directory:
            return
        info = detect_comfy_installation(directory)
        if info is None:
            QMessageBox.warning(
                self, "Not a ComfyUI Installation",
                f"No ComfyUI installation found in:\n{directory}\n\n"
                "Expected a folder containing main.py and the 'comfy' package "
                "(or a 'ComfyUI' subfolder with them). Use 'Install/Update "
                "ComfyUI' to install a fresh copy."
            )
            return

        python = info["python"]
        if not python:
            reply = QMessageBox.question(
                self, "Python Environment Not Found",
                "ComfyUI was found, but no Python environment could be detected "
                "next to it.\n\nLocate the Python executable ComfyUI should run "
                "with now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                python, _ = QFileDialog.getOpenFileName(
                    self, "Select Python Executable", "",
                    "Python Executable (python python3 python.exe);;All Files (*)")

        self.settingsManager.set("comfy_main_path", info["main_py"])
        if python:
            self.settingsManager.set("comfy_py_path", python)
        self.settingsManager.save()

        summary = f"ComfyUI: {info['main_py']}"
        if python:
            summary += f"\nPython: {python}"
            from comfystudio.sdmodules.comfy_locator import MIN_COMFY_PYTHON, get_python_version
            version = get_python_version(python)
            if version and version < MIN_COMFY_PYTHON:
                need = ".".join(str(v) for v in MIN_COMFY_PYTHON)
                summary += (f"\n\nWarning: this environment is Python "
                            f"{version[0]}.{version[1]} — ComfyUI's requirements need "
                            f"{need}+, dependency installs will fail.")
        else:
            summary += ("\nPython: not set — configure the ComfyUI Python path "
                        "in Settings before starting ComfyUI.")
        QMessageBox.information(self, "ComfyUI Installation Selected", summary)
        self.status_widgets["statusMessage"].setText("ComfyUI installation configured.")
        # The install's user/default/workflows folder is the shared library.
        if hasattr(self, "loadWorkflows"):
            self.loadWorkflows()