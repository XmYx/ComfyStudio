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
from PyQt6.QtCore import QThreadPool, QThread, QEventLoop
from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import QFileDialog, QDialog, QMessageBox, QInputDialog

from comfystudio.sdmodules.comfy_installer import ComfyInstallerWizard
from comfystudio.sdmodules.cs_datastruts import Shot, ensure_parameters_dict
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
        self.renderQueue = []  # Stores shot indices or (shotIndex, workflowIndex) tuples
        self.activeWorker = None  # The active RenderWorker
        self.comfy_thread = None
        self.comfy_worker = None
        self.comfy_running = False
        self.render_mode = "per_workflow"
        # For progressive workflow rendering:
        self.workflowQueue = {}  # Maps shotIndex -> list of workflowIndices to process
        self.shotInProgress = -1  # Current shot being processed
        self.workflowIndexInProgress = -1  # Current workflow index in that shot

    def startComfy(self):
        if self.comfy_running:
            QMessageBox.information(self, "Info", "ComfyUI is already running.")
            return

        py_path = self.settingsManager.get("comfy_py_path")
        main_path = self.settingsManager.get("comfy_main_path")
        if py_path and main_path:
            self.comfy_thread = QThread()
            self.comfy_worker = ComfyWorker(py_path=py_path, main_path=main_path)
            self.comfy_worker.moveToThread(self.comfy_thread)
            self.comfy_thread.started.connect(self.comfy_worker.run)
            self.comfy_worker.log_message.connect(self.appendLog)
            self.comfy_worker.error.connect(self.appendLog)
            self.comfy_worker.finished.connect(self.comfy_thread.quit)
            self.comfy_worker.finished.connect(self.comfy_worker.deleteLater)
            self.comfy_thread.finished.connect(self.comfy_thread.deleteLater)
            self.comfy_worker.finished.connect(lambda: self.onComfyFinishedRunning())
            self.comfy_thread.start()
            self.comfy_running = True
            self.status_widgets['statusMessage'].setText("ComfyUI started.")
            self.appendLog("ComfyUI process started.")
        else:
            QMessageBox.warning(self, "Error", "Comfy paths not set in settings.")

    def stopComfy(self):
        if self.comfy_running and self.comfy_worker:
            try:
                self.comfy_worker.stop()
                self.comfy_running = False
                self.status_widgets["statusMessage"].setText("Stopping ComfyUI...")
                self.appendLog("Stopping ComfyUI process...")
            except Exception as e:
                self.status_widgets["statusMessage"].setText(str(e))
                self.appendLog(repr(e))
        else:
            QMessageBox.information(self, "Info", "ComfyUI is not running.")

    def onComfyFinishedRunning(self):
        self.comfy_running = False
        self.status_widgets["statusMessage"].setText("ComfyUI stopped.")
        self.appendLog("ComfyUI process has stopped.")

    def startNextRender(self):
        if not self.renderQueue:
            self.shotInProgress = -1
            self.workflowIndexInProgress = -1
            self.status_widgets["statusMessage"].setText("Render queue is empty.")
            return

        if isinstance(self.renderQueue[0], int):
            self.render_mode = 'per_shot'
            self.shotInProgress = self.renderQueue.pop(0)
            self.initWorkflowQueueForShot(self.shotInProgress)
            self.workflowIndexInProgress = 0
            self.processNextWorkflow()
        elif isinstance(self.renderQueue[0], tuple) and len(self.renderQueue[0]) == 2:
            self.render_mode = 'per_workflow'
            shot_idx, wf_idx = self.renderQueue.pop(0)
            self.executeWorkflow(shot_idx, wf_idx)
        else:
            logging.error(f"Invalid renderQueue item: {self.renderQueue[0]}")
            self.renderQueue.pop(0)
            self.startNextRender()

    def onRenderSelected(self):
        selected_items = self.listWidget.selectedItems()
        if not selected_items:
            self.listWidget.setCurrentRow(0)
            selected_items = self.listWidget.selectedItems()

        if len(selected_items) > 1:
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
            chosen_mode = 'per_shot'

        self.stopRendering()

        if chosen_mode == 'per_shot':
            for it in selected_items:
                idx = it.data(Qt.ItemDataRole.UserRole)
                if idx is not None and isinstance(idx, int) and 0 <= idx < len(self.shots):
                    self.renderQueue.append(idx)
        elif chosen_mode == 'per_workflow':
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

        self.startNextRender()

    def onRenderAll(self):
        if not self.shots:
            return

        chosen_mode = 'per_workflow'
        self.stopRendering()

        if chosen_mode == 'per_shot':
            for idx in range(len(self.shots)):
                self.renderQueue.append(idx)
        elif chosen_mode == 'per_workflow':
            max_workflows = max(len(shot.workflows) for shot in self.shots)
            for wf_idx in range(max_workflows):
                for shot_idx, shot in enumerate(self.shots):
                    if wf_idx < len(shot.workflows) and shot.workflows[wf_idx].enabled:
                        self.renderQueue.append((shot_idx, wf_idx))
        else:
            return

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
            "shotParams": shot.params,
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
        logging.debug(f"Computed {'Video' if isVideo else 'Still'} Signature: {signature} for shot '{shot.name}'")
        return signature

    def setCurrentFrameForWorkflow(self, workflow, frame_index: int):
        """
        Sets the current frame for the given workflow and (if available) updates a UI control.
        """
        workflow.current_frame = frame_index
        # if hasattr(self, "currentFrameSpin"):
        #     self.currentFrameSpin.setValue(frame_index)


    def processWorkflowForFrame(self, shot, workflow, frame_index: int):
        """
        Processes the given workflow for a single frame using RenderWorker.
        Loads the workflow JSON, applies parameter overrides, creates a RenderWorker,
        and waits synchronously for its result.
        Returns (True, output_path) on success or (False, error_message) on failure.
        """
        try:
            workflow_json = self.loadWorkflowJson(workflow)
        except Exception as e:
            return False, f"Failed to load workflow: {e}"

        local_params = copy.deepcopy(shot.params)
        wf_params = workflow.parameters.get("params", [])
        # (Apply shot-level and workflow-level overrides as in your original code)
        for node_id, node_data in workflow_json.items():
            inputs_dict = node_data.get("inputs", {})
            for input_key in list(inputs_dict.keys()):
                ikey_lower = str(input_key).lower()
                for param in local_params:
                    node_ids = param.get("nodeIDs", [])
                    if str(node_id) not in node_ids:
                        continue
                    if param["name"].lower() == ikey_lower:
                        inputs_dict[input_key] = param["value"]
            for input_key in list(inputs_dict.keys()):
                ikey_lower = str(input_key).lower()
                for param in wf_params:
                    node_ids = param.get("nodeIDs", [])
                    if str(node_id) not in node_ids:
                        continue
                    if param["name"].lower() == ikey_lower:
                        inputs_dict[input_key] = param["value"]
        comfy_ip = self.settingsManager.get("comfy_ip", "http://localhost:8188")
        loop = QEventLoop()
        result_container = []

        def handle_result(data, si, iv):
            result_container.append(data)
            loop.quit()

        def handle_error(err):
            result_container.append({"error": err})
            loop.quit()

        worker = RenderWorker(
            workflow_json=workflow_json,
            shotIndex=-1,  # Not used at frame level.
            isVideo=workflow.isVideo,
            comfy_ip=comfy_ip,
            parent=self
        )
        worker.signals.result.connect(lambda data, si, iv: handle_result(data, si, iv))
        worker.signals.error.connect(handle_error)
        QThreadPool.globalInstance().start(worker)
        loop.exec()
        if result_container:
            if "error" in result_container[0]:
                return False, result_container[0]["error"]
            output = self.extractOutputFromResult(result_container[0])
            return True, output
        return False, "No result returned."

    def assembleVideoFromFrames(self, frames: list):
        """
        Assembles a list of frame image paths into a video using ffmpeg.
        Returns the generated video file path, or None on failure.
        """
        try:
            temp_file_list = tempfile.mktemp(suffix=".txt")
            with open(temp_file_list, "w") as f:
                for frame in frames:
                    f.write(f"file '{frame}'\n")
            project_folder = tempfile.gettempdir()
            output_video = os.path.join(project_folder, f"assembled_{random.randint(1000,9999)}.mp4")
            command = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", temp_file_list,
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                output_video
            ]
            subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            os.remove(temp_file_list)
            return output_video
        except Exception as e:
            logging.error(f"Error assembling video: {e}")
            return None

    def extractOutputFromResult(self, result_data):
        main_key = list(result_data.keys())[0]
        outputs = result_data[main_key].get("outputs", {})
        final_path = None
        final_is_video = False
        for node_id, output_data in outputs.items():
            images = output_data.get("images", [])
            for image_info in images:
                filename = image_info.get("filename")
                subfolder = image_info.get("subfolder", "")
                if filename:
                    final_path = os.path.join(subfolder, filename) if subfolder else filename
                    break
            if final_path:
                break
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
            return self.downloadComfyFile(final_path)
        return None

    def runWorkflowFrames(self, shot, workflow):
        """
        Processes the workflow repeatedly for a number of runs (frames) specified by workflow.parameters["run_count"].
        For each frame:
          - If this is not the first frame and a parameter is flagged with usePrevResultImage/Video,
            update its value to the previous frame's output.
          - Set the current frame.
          - Process that frame.
          - Save a snapshot of the parameters.
        If run_count > 1, assemble the frames into a video and return its file path;
        otherwise, return the single frame's output.
        """
        run_count = int(workflow.parameters.get("run_count", 1))
        frame_outputs = []
        workflow.frame_params = {}
        for frame in range(run_count):
            # For subsequent frames, update parameters flagged to use previous results.
            if frame > 0:
                for param in workflow.parameters.get("params", []):
                    if param.get("usePrevResultImage", False) or param.get("usePrevResultVideo", False):
                        # Use the output of the previous frame run.
                        param["value"] = frame_outputs[-1]
            self.setCurrentFrameForWorkflow(workflow, frame)
            success, frame_output = self.processWorkflowForFrame(shot, workflow, frame)
            if not success:
                logging.warning(f"Failed to process frame {frame} for workflow {workflow.path}")
                continue
            frame_outputs.append(frame_output)
            workflow.frame_params[frame] = copy.deepcopy(workflow.parameters)
        if run_count > 1 and frame_outputs:
            video_path = self.assembleVideoFromFrames(frame_outputs)
            return video_path
        elif frame_outputs:
            return frame_outputs[0]
        return None

    def executeWorkflow(self, shotIndex, workflowIndex):
        shot = self.shots[shotIndex]
        workflow = shot.workflows[workflowIndex]
        isVideo = workflow.isVideo
        currentSignature = self.computeWorkflowSignature(shot, workflowIndex)

        # Check if an identical version already exists.
        existing_output = None
        for version in workflow.versions:
            if version["params"] == workflow.parameters and (
                (version["is_video"] and isVideo) or ((not version["is_video"]) and not isVideo)
            ):
                if os.path.exists(version["output"]):
                    existing_output = version["output"]
                    break
        if existing_output:
            print(f"[DEBUG] Reusing existing rendered output for shot '{shot.name}' in workflow {workflowIndex}.")
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
            if self.render_mode == 'per_shot':
                self.workflowIndexInProgress += 1
                self.processNextWorkflow()
            elif self.render_mode == 'per_workflow':
                self.startNextRender()
            return

        # Multi-run (frame-by-frame) branch
        run_count = int(workflow.parameters.get("run_count", 1))
        if run_count > 1:
            print(f"[DEBUG] Running workflow '{workflow.path}' for {run_count} frames.")
            video_output = self.runWorkflowFrames(shot, workflow)
            if video_output:
                # Prompt for a save folder if not already set
                if not hasattr(self, 'currentFilePath') or not self.currentFilePath:
                    dlg = QFileDialog(self, "Select a folder to store shot versions")
                    dlg.setFileMode(QFileDialog.FileMode.Directory)
                    if dlg.exec() == QDialog.DialogCode.Accepted:
                        project_folder = dlg.selectedFiles()[0]
                        self.currentFilePath = os.path.join(project_folder, "untitled.json")
                    else:
                        project_folder = tempfile.gettempdir()
                else:
                    project_folder = os.path.dirname(self.currentFilePath)
                ext = os.path.splitext(video_output)[1]
                subfolder = os.path.join(project_folder, "videos")
                if not os.path.exists(subfolder):
                    os.makedirs(subfolder, exist_ok=True)
                shot_name = shot.name.replace(" ", "_")
                version_number = len(workflow.versions) + 1
                timestamp = int(time.time())
                new_name = f"{shot_name}_{workflowIndex}_{version_number}_{timestamp}{ext}"
                new_full = os.path.join(subfolder, new_name)
                try:
                    with open(video_output, "rb") as src, open(new_full, "wb") as dst:
                        dst.write(src.read())
                except Exception:
                    new_full = video_output
                shot.videoPath = new_full
                shot.videoVersions.append(new_full)
                shot.currentVideoVersion = len(shot.videoVersions) - 1
                shot.lastVideoSignature = currentSignature
                workflow.lastSignature = currentSignature

                workflow.parameters = ensure_parameters_dict(workflow.parameters)

                new_version = {
                    "params": copy.deepcopy(workflow.parameters),
                    "output": new_full,
                    "is_video": True,
                    "timestamp": time.time()
                }
                workflow.versions.append(new_version)

                current_item = self.workflowListWidget.currentItem()
                if current_item:
                    self.onWorkflowItemClicked(current_item)
                    # Assume the version dropdown is the first widget in the workflowParamsLayout
                    # if self.workflowParamsLayout.rowCount() > 0:
                    #     item = self.workflowParamsLayout.itemAt(0, self.workflowParamsLayout.FieldRole)
                    #     if item and item.widget():
                    #         version_combo = item.widget()
                    #         version_combo.setCurrentIndex(len(workflow.versions))

            if self.render_mode == 'per_shot':
                self.workflowIndexInProgress += 1
                self.processNextWorkflow()
            elif self.render_mode == 'per_workflow':
                self.startNextRender()
            return

        # Single-run processing:
        try:
            with open(workflow.path, "r") as f:
                workflow_json = self.loadWorkflowJson(workflow)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load workflow: {e}")
            if self.render_mode == 'per_shot':
                self.workflowIndexInProgress += 1
                self.processNextWorkflow()
            elif self.render_mode == 'per_workflow':
                self.startNextRender()
            return

        local_params = copy.deepcopy(shot.params)
        wf_params = workflow.parameters.get("params", [])
        print("[DEBUG] Original workflow JSON keys:")
        for k in workflow_json.keys():
            print("       ", k)
        for node_id, node_data in workflow_json.items():
            inputs_dict = node_data.get("inputs", {})
            meta_title = node_data.get("_meta", {}).get("title", "").lower()
            for input_key in list(inputs_dict.keys()):
                ikey_lower = str(input_key).lower()
                for param in local_params:
                    node_ids = param.get("nodeIDs", [])
                    if str(node_id) not in node_ids:
                        continue
                    if param["name"].lower() == ikey_lower:
                        inputs_dict[input_key] = param["value"]
            for input_key in list(inputs_dict.keys()):
                ikey_lower = str(input_key).lower()
                for param in wf_params:
                    node_ids = param.get("nodeIDs", [])
                    if str(node_id) not in node_ids:
                        continue
                    if param["name"].lower() == ikey_lower:
                        inputs_dict[input_key] = param["value"]
            if "positive prompt" in [p["name"].lower() for p in local_params] and "positive prompt" in meta_title:
                for param in local_params:
                    if param["name"].lower() == "positive prompt":
                        node_ids = param.get("nodeIDs", [])
                        if not node_ids or str(node_id) in node_ids:
                            inputs_dict["text"] = param["value"]
        comfy_ip = self.settingsManager.get("comfy_ip", "http://localhost:8188")
        worker = RenderWorker(
            workflow_json=workflow_json,
            shotIndex=shotIndex,
            isVideo=isVideo,
            comfy_ip=comfy_ip,
            parent=self
        )
        worker.signals.result.connect(lambda data, si, iv: self.onComfyResult(data, si, workflowIndex))
        worker.signals.error.connect(self.onComfyError)
        worker.signals.finished.connect(self.onComfyFinished)
        self.status_widgets["statusMessage"].setText(
            f"Rendering {shot.name} - Workflow {workflowIndex + 1}/{len(shot.workflows)} ..."
        )
        self.activeWorker = worker
        QThreadPool.globalInstance().start(worker)

    def onComfyResult(self, result_data, shotIndex, workflowIndex):
        shot = self.shots[shotIndex]
        workflow = shot.workflows[workflowIndex]
        main_key = list(result_data.keys())[0]
        outputs = result_data[main_key].get("outputs", {})
        if not outputs:
            self.workflowIndexInProgress += 1
            self.processNextWorkflow()
            return

        final_path = None
        final_is_video = False
        for node_id, output_data in outputs.items():
            images = output_data.get("images", [])
            for image_info in images:
                filename = image_info.get("filename")
                subfolder = image_info.get("subfolder", "")
                if filename:
                    final_path = os.path.join(subfolder, filename) if subfolder else filename
                    break
            if final_path:
                break
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
                if final_is_video or workflow.isVideo:
                    subfolder = os.path.join(project_folder, "videos")
                else:
                    subfolder = os.path.join(project_folder, "stills")
                if not os.path.exists(subfolder):
                    os.makedirs(subfolder, exist_ok=True)
                shot_name = shot.name.replace(" ", "_")
                version_number = len(workflow.versions) + 1
                timestamp = int(time.time())
                new_name = f"{shot_name}_{workflowIndex}_{version_number}_{timestamp}{ext}"
                new_full = os.path.join(subfolder, new_name)
                try:
                    with open(local_path, "rb") as src, open(new_full, "wb") as dst:
                        dst.write(src.read())
                except Exception:
                    new_full = local_path
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

                workflow.parameters = ensure_parameters_dict(workflow.parameters)

                new_version = {
                    "params": copy.deepcopy(workflow.parameters),
                    "output": new_full,
                    "is_video": (final_is_video or workflow.isVideo),
                    "timestamp": time.time()
                }
                workflow.versions.append(new_version)

                current_item = self.workflowListWidget.currentItem()
                if current_item:
                    self.onWorkflowItemClicked(current_item)
                    # Assume the version dropdown is the first widget in the workflowParamsLayout
                    # if self.workflowParamsLayout.rowCount() > 0:
                    #     item = self.workflowParamsLayout.itemAt(0, self.workflowParamsLayout.FieldRole)
                    #     if item and item.widget():
                    #         version_combo = item.widget()
                    #         version_combo.setCurrentIndex(len(workflow.versions))

                workflow.lastSignature = self.computeRenderSignature(shot, isVideo=workflow.isVideo)
                self.updateList()
                self.shotRenderComplete.emit(shotIndex, workflowIndex, new_full, (final_is_video or workflow.isVideo))
        if self.render_mode == 'per_shot':
            self.workflowIndexInProgress += 1
            self.processNextWorkflow()
        elif self.render_mode == 'per_workflow':
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
        self.status_widgets["statusMessage"].setText("Ready")

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
        self.renderQueue.clear()
        self.shotInProgress = -1
        self.workflowIndexInProgress = -1
        if self.activeWorker:
            self.activeWorker.stop()
            self.activeWorker = None
        self.status_widgets["statusMessage"].setText("Render queue cleared.")

    def setupCustomNodes(self):
        config_file = os.path.join(os.path.dirname(__file__), "..", "defaults", "custom_nodes.json")
        comfy_exec_path = self.settingsManager.get("comfy_main_path")
        venv_python_path = self.settingsManager.get("comfy_py_path")
        if not comfy_exec_path:
            QMessageBox.warning(self, "Error", "ComfyUI main.py path not set in settings.")
            return
        if not venv_python_path:
            QMessageBox.warning(self, "Error", "ComfyUI virtual environment path not set in settings.")
            return
        if os.path.isfile(venv_python_path):
            venv_dir = os.path.dirname(os.path.dirname(venv_python_path))
        else:
            venv_dir = os.path.dirname(os.path.dirname(venv_python_path))
        self.custom_nodes_thread = QThread()
        self.custom_nodes_worker = CustomNodesSetupWorker(
            config_file=config_file,
            venv_path=venv_dir,
            comfy_exec_path=comfy_exec_path
        )
        self.custom_nodes_worker.moveToThread(self.custom_nodes_thread)
        self.custom_nodes_thread.started.connect(self.custom_nodes_worker.run)
        self.custom_nodes_worker.log_message.connect(self.appendLog)
        self.custom_nodes_worker.finished.connect(self.custom_nodes_thread.quit)
        self.custom_nodes_worker.finished.connect(lambda: QMessageBox.information(self, "Info", "Custom nodes setup completed."))
        self.custom_nodes_thread.start()
        self.appendLog("Starting custom nodes setup...")

    def startComfyInstallerWizard(self):
        wizard = ComfyInstallerWizard(parent=self, settings_manager=self.settingsManager, log_callback=self.appendLog)
        wizard.exec()