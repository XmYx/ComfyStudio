#!/usr/bin/env python
import copy
import json
import logging
import os
import random
import tempfile
import time
import urllib
from typing import List

import requests
from PyQt6.QtCore import QThreadPool
from qtpy.QtCore import (
    Qt,
    QThread,
    Signal
)
from qtpy.QtWidgets import (
    QFileDialog,
    QDialog,
    QMessageBox,
    QInputDialog
)

from comfystudio.sdmodules.comfy_installer import ComfyInstallerWizard
from comfystudio.sdmodules.cs_datastruts import Shot
from comfystudio.sdmodules.worker import RenderWorker, CustomNodesSetupWorker, ComfyWorker


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
    def startComfy(self):
        """
        Launches the ComfyUI process in a separate thread using ComfyWorker.
        Ensures that the UI remains responsive and logs are captured.
        """
        if self.comfy_running:
            QMessageBox.information(self, "Info", "ComfyUI is already running.")
            return

        py_path = self.settingsManager.get("comfy_py_path")
        main_path = self.settingsManager.get("comfy_main_path")
        if py_path and main_path:
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
            self.status_widgets['statusMessage'].setText("ComfyUI started.")
            self.appendLog("ComfyUI process started.")
        else:
            QMessageBox.warning(self, "Error", "Comfy paths not set in settings.")
    def stopComfy(self):
        """
        Signals the ComfyWorker to terminate the ComfyUI process.
        Cleans up the thread and worker.
        """
        if self.comfy_running and self.comfy_worker:
            try:
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
            # Enqueue workflows in an interleaved manner across selected shots
            selected_indices = [
                it.data(Qt.ItemDataRole.UserRole) for it in selected_items
                if it.data(Qt.ItemDataRole.UserRole) is not None and isinstance(it.data(Qt.ItemDataRole.UserRole), int)
            ]
            max_workflows = max(len(self.shots[idx].workflows) for idx in selected_indices)
            for wf_idx in range(max_workflows):
                for shot_idx in selected_indices:
                    shot = self.shots[shot_idx]
                    if wf_idx < len(shot.workflows) and shot.workflows[wf_idx].enabled:
                        self.renderQueue.append((shot_idx, wf_idx))
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
            QMessageBox.warning(self, "Warning", "No shots available to render.")
            return

        if len(self.shots) > 1:
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
            # Default to 'Per Shot' if only one shot exists
            chosen_mode = 'per_shot'

        # First stop any current rendering processes
        self.stopRendering()

        if chosen_mode == 'per_shot':
            # Enqueue all shots to render all their enabled workflows
            for idx in range(len(self.shots)):
                self.renderQueue.append(idx)
        elif chosen_mode == 'per_workflow':
            # Enqueue workflows in an interleaved manner across all shots
            max_workflows = max(len(shot.workflows) for shot in self.shots)
            for wf_idx in range(max_workflows):
                for shot_idx, shot in enumerate(self.shots):
                    if wf_idx < len(shot.workflows) and shot.workflows[wf_idx].enabled:
                        self.renderQueue.append((shot_idx, wf_idx))
        else:
            QMessageBox.warning(self, "Warning", f"Unknown render mode: {chosen_mode}")
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
        wIndices = []
        for i, wf in enumerate(shot.workflows):
            if wf.enabled:
                wIndices.append(i)
        self.workflowQueue[shotIndex] = wIndices
    def computeWorkflowSignature(self, shot: Shot, workflowIndex: int) -> str:

        import hashlib, json
        workflow = shot.workflows[workflowIndex]

        data_struct = {
            "shotParams": shot.params,  # all shot params
            "workflowParams": workflow.parameters,
            "workflowPath": workflow.path,
            "isVideo": workflow.isVideo
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
        currentSignature = self.computeWorkflowSignature(shot, workflowIndex)
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
                            self.updateList()
                            self.shotRenderComplete.emit(shotIndex, workflowIndex, other_shot.videoPath, True)
                        elif not isVideo and other_shot.stillPath and os.path.exists(other_shot.stillPath):
                            print(f"[DEBUG] Reusing image from shot '{other_shot.name}' for current shot '{shot.name}'.")
                            shot.stillPath = other_shot.stillPath
                            shot.imageVersions.append(other_shot.stillPath)
                            shot.currentImageVersion = len(shot.imageVersions) - 1
                            shot.lastStillSignature = other_shot.lastStillSignature
                            workflow.lastSignature = currentSignature
                            self.updateList()
                            self.shotRenderComplete.emit(shotIndex, workflowIndex, other_shot.stillPath, False)

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
            with open(workflow.path, "r") as f:
                workflow_json = json.load(f)
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
        wf_params = workflow.parameters.get("params", [])

        print("[DEBUG] Original workflow JSON keys:")
        for k in workflow_json.keys():
            print("       ", k)

        # Apply dynamic overrides based on render mode
        if self.render_mode in ['per_shot', 'per_workflow']:
            if self.render_mode == 'per_shot':
                if self.workflowIndexInProgress > 0:
                    prevWorkflowIndex = self.workflowQueue.get(shotIndex, [])[self.workflowIndexInProgress - 1]
                else:
                    prevWorkflowIndex = None
            elif self.render_mode == 'per_workflow':
                if workflowIndex > 0:
                    prevWorkflowIndex = workflowIndex - 1
                else:
                    prevWorkflowIndex = None
            if prevWorkflowIndex is not None:
                prevWf = shot.workflows[prevWorkflowIndex]
                # Determine the previous output based on the workflow type
                prevVideo = shot.videoPath if prevWf.isVideo and shot.videoPath else None
                prevImage = shot.stillPath if (not prevWf.isVideo) and shot.stillPath else None
                for param in wf_params:
                    if param.get("usePrevResultImage") and prevImage:
                        print(f"[DEBUG] Setting param '{param['name']}' to prevImage: {prevImage}")
                        param["value"] = prevImage
                    if param.get("usePrevResultVideo") and prevVideo:
                        print(f"[DEBUG] Setting param '{param['name']}' to prevVideo: {prevVideo}")
                        param["value"] = prevVideo

        # Override workflow_json with local_params + wf_params
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
        comfy_ip = self.settingsManager.get("comfy_ip", "http://localhost:8188")
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

        final_path = None
        final_is_video = False
        for node_id, output_data in outputs.items():
            # Check images
            images = output_data.get("images", [])
            for image_info in images:
                filename = image_info.get("filename")
                subfolder = image_info.get("subfolder", "")
                if filename:
                    final_path = os.path.join(subfolder, filename) if subfolder else filename
                    break
            if final_path:
                break
            # Check gifs (or any other video-like outputs)
            gifs = output_data.get("gifs", [])
            for gif_info in gifs:
                filename = gif_info.get("filename")
                subfolder = gif_info.get("subfolder", "")
                if filename:
                    final_path = os.path.join(subfolder, filename) if subfolder else filename
                    final_is_video = True
                    break
            if final_path:
                break

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

            local_path = self.downloadComfyFile(final_path)
            if local_path:
                ext = os.path.splitext(local_path)[1]
                new_name = f"{'video' if final_is_video or workflow.isVideo else 'image'}_{random.randint(0, 999999)}{ext}"
                new_full = os.path.join(project_folder, new_name)
                try:
                    with open(local_path, "rb") as src, open(new_full, "wb") as dst:
                        dst.write(src.read())
                except Exception:
                    # Fallback if copy failed
                    new_full = local_path

                # --- IMPORTANT: Update the Shot with the new file path *now*, so the next workflow can see it ---
                if final_is_video or workflow.isVideo:
                    shot.videoPath = new_full
                    shot.videoVersions.append(new_full)
                    shot.currentVideoVersion = len(shot.videoVersions) - 1
                    shot.lastVideoSignature = self.computeRenderSignature(shot, isVideo=True)
                else:
                    shot.stillPath = new_full
                    shot.imageVersions.append(new_full)
                    shot.currentImageVersion = len(shot.imageVersions) - 1
                    shot.lastStillSignature = self.computeRenderSignature(shot, isVideo=False)

                new_version = {
                    "params": copy.deepcopy(workflow.parameters),  # snapshot of current workflow params
                    "output": new_full,  # path to the rendered still or video
                    "is_video": (final_is_video or workflow.isVideo),
                    "timestamp": time.time()  # optionally, store when this version was created
                }

                workflow.versions.append(new_version)

                # Mark this workflow's own signature, so we don't re-render if nothing changed
                workflow.lastSignature = self.computeRenderSignature(shot, isVideo=workflow.isVideo)

                # Update the UI / shot listing
                self.updateList()

                # Notify other parts (e.g. preview dock)
                self.shotRenderComplete.emit(shotIndex, workflowIndex, new_full, (final_is_video or workflow.isVideo))

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
        comfy_ip = self.settingsManager.get("comfy_ip", "http://localhost:8188").rstrip("/")
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