#!/usr/bin/env python
import copy
import os
import time

from PyQt6.QtCore import QMetaObject, QEventLoop, QCoreApplication, QMutex, QWaitCondition, QSemaphore, QObject, QThread
from qtpy.QtCore import (
    Qt,
    QPoint,
    Signal
)
from qtpy.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QFormLayout,
    QDockWidget,
    QPushButton,
    QDialog,
    QComboBox,
    QMessageBox,
    QTabWidget,
    QAbstractItemView,
    QInputDialog
)

from comfystudio.sdmodules.contextmenuhelper import create_context_menu
from comfystudio.sdmodules.core.comfyhandler import ComfyStudioShotManager
from comfystudio.sdmodules.core.ui import ComfyStudioUI
from comfystudio.sdmodules.cs_datastruts import Shot, WorkflowAssignment
from comfystudio.sdmodules.mainwindow_ui import ComfyStudioComfyHandler
from comfystudio.sdmodules.new_widget import ShotManagerWidget as ReorderableListWidget
from comfystudio.sdmodules.preview_dock import ShotPreviewDock
from comfystudio.sdmodules.shot_manager import ShotManager
# from comfystudio.sdmodules.widgets import ReorderableListWidget



class ProcessApiRequestWorker(QObject):
    finished = Signal(object)  # Emits the output path (or None on error)
    error = Signal(object)     # Emits an exception or error message

    def __init__(self, window, endpoint_config, image_data, parent=None):
        super().__init__(parent)
        self.window = window
        self.endpoint_config = endpoint_config
        self.image_data = image_data

    def run(self):
        try:
            # Call your existing method (which uses a semaphore, etc.)
            result = self.window.process_api_request_async(self.endpoint_config, self.image_data)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(e)


class ComfyStudioWindow(ComfyStudioUI, ComfyStudioShotManager, ComfyStudioComfyHandler, ShotManager):

    shotSelected = Signal(int)
    workflowSelected = Signal(int, int)
    shotRenderComplete = Signal(int, int, str, bool)
    apiRenderFinished = Signal()
    apiSemaphoreRelease = Signal()
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.resize(1400, 900)
        self.setWindowTitle(self.localization.translate("app_title", default="Cinema Shot Designer"))
        # self._api_mutex = QMutex()
        # self._api_wait_condition = QWaitCondition()
        self.apiSemaphoreRelease.connect(self._release_api_semaphore)
        self._api_semaphore = QSemaphore(0)
        self.showHiddenParams = False  # Toggles display of hidden parameters
        self.global_vars = {}
        self.initUI()
        self.loadWorkflows()
        self.updateList()
        self.restoreWindowState()
        self.connectSignals()

        self.loadPlugins()

    def _release_api_semaphore(self):
        """Slot to safely release the API semaphore in the main thread."""
        self._api_semaphore.release()

    def initUI(self):
        central = QWidget()

        # self.logStream = EmittingStream()
        # self.logStream.text_written.connect(self.appendLog)

        self.setCentralWidget(central)
        self.mainLayout = QVBoxLayout(central)

        # Shots list
        self.listWidgetBase = ReorderableListWidget(self)
        self.listWidget = self.listWidgetBase.shotListView
        self.listWidget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.listWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        # Dock for shot parameters
        self.dock = QDockWidget(self.localization.translate("shot_details"), self)
        self.dock.setObjectName("shot_details_dock")
        self.dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)

        self.dockContents = QWidget()
        self.dockLayout = QVBoxLayout(self.dockContents)

        self.dockTabWidget = QTabWidget()
        self.dockLayout.addWidget(self.dockTabWidget)

        # Tabs: Workflows and Params
        self.workflowsTab = QWidget()
        self.workflowsLayout = QVBoxLayout(self.workflowsTab)

        self.paramsTab = QWidget()
        self.paramsLayout = QVBoxLayout(self.paramsTab)  # We'll place a scroll area inside

        self.dockTabWidget.addTab(self.workflowsTab, self.localization.translate("workflows"))
        self.dockTabWidget.addTab(self.paramsTab, self.localization.translate("params"))

        # Workflow management UI
        self.initWorkflowsTab()

        # Params management UI
        self.initParamsTab()

        self.dock.setWidget(self.dockContents)

        self.previewDock = ShotPreviewDock(self)
        self.previewDock.setObjectName("preview_dock")
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.previewDock)

        menu_config = {
            "File": {
                "title": "File",
                "actions": [
                    {"name": "newAct", "text": "New", "trigger": self.newProject},
                    {"name": "openAct", "text": "Open", "trigger": self.openProject},
                    {"name": "saveAct", "text": "Save", "trigger": self.saveProject},
                    {"name": "saveAsAct", "text": "Save As", "trigger": self.saveProjectAs},
                    {"name": "importAction", "text": "Import", "trigger": self.importShotsFromTxt},
                    {"separator": True},
                    {"name": "renderSelectedAct", "text": "Render Selected", "trigger": self.onRenderSelected},
                    {"name": "renderAllAct", "text": "Render All", "trigger": self.onRenderAll},
                    {"name": "saveDefaultsAct", "text": "Save Defaults", "trigger": self.onSaveWorkflowDefaults},
                    # Recents submenu: note that actions list is empty and can be updated dynamically
                    {"submenu": "Recents",
                     "title": self.localization.translate("menu_recents", default="Recents"),
                     "actions": []}
                ]
            },
            "Settings": {
                "title": "Settings",
                "actions": [
                    {"name": "openSettingsAct", "text": "Settings", "trigger": self.showSettingsDialog},
                    {"name": "openModelManagerAct", "text": "Model Manager", "trigger": self.openModelManager},
                    {"name": "setupComfyNodesAct", "text": "Setup Comfy Nodes", "trigger": self.setupCustomNodes},
                    {"name": "setupComfyAct", "text": "Setup Comfy", "trigger": self.startComfyInstallerWizard}
                ]
            },
            "Help": {
                "title": "Help",
                "actions": [
                    {"name": "userGuideAct", "text": "User Guide", "trigger": self.openUserGuide},
                    {"separator": True},
                    {"name": "aboutAct", "text": "About", "trigger": self.openAboutDialog}
                ]
            }
        }

        toolbar_config = {
            "Main Toolbar": {
                "objectName": "main_toolbar",
                "actions": [
                    {"name": "addShotBtn", "text": "Add Shot", "trigger": self.addShot},
                    {"name": "renderSelectedBtn", "text": "Render Selected", "trigger": self.onRenderSelected},
                    {"name": "renderAllBtn", "text": "Render All", "trigger": self.onRenderAll},
                    {"name": "stopRenderingBtn", "text": "Stop Rendering", "trigger": self.stopRendering},
                    {"name": "startComfyBtn", "text": "Start Comfy", "trigger": self.startComfy},
                    {"name": "stopComfyBtn", "text": "Stop Comfy", "trigger": self.stopComfy}
                ]
            }
        }

        status_config = {
            "widgets": [
                {
                    "type": "label",
                    "name": "statusMessage",
                    "text": self.localization.translate("status_ready", default="Ready"),
                    "stretch": 1  # This widget will take extra space.
                },
                {
                    "type": "label",
                    "name": "logLabel",
                    "text": ""
                },
                {
                    "type": "button",
                    "name": "terminalButton",
                    "text": self.localization.translate("button_terminal", default="Terminal"),
                    "trigger": self.toggleTerminalDock
                }
            ],
            "dockWidgets": [
                {
                    "name": "terminalDock",
                    "objectName": "terminal_dock",
                    "title": self.localization.translate("terminal_output", default="Terminal Output"),
                    "allowedAreas": Qt.DockWidgetArea.AllDockWidgetAreas,
                    "defaultArea": Qt.DockWidgetArea.BottomDockWidgetArea,
                    "hidden": True,  # Initially hide the terminal dock.
                    "widget": {
                        "type": "textEdit",
                        "name": "terminalTextEdit",
                        "readOnly": True
                    }
                }
            ]
        }

        self.create_dynamic_menu_bar(menu_config)
        self.create_dynamic_toolbar(toolbar_config)
        self.create_dynamic_status_bar(status_config)
        self.updateRecentsMenu()

        self.shotSelected.connect(self.previewDock.onShotSelected)
        self.workflowSelected.connect(self.previewDock.onWorkflowSelected)
        self.shotRenderComplete.connect(self.previewDock.onShotRenderComplete)
        self.createWindowsMenu()

    def process_api_request(self, endpoint_config, image_data):
        """
        Called by the API server when an external app sends an image.
        Saves the image, updates the API-flagged parameter, triggers rendering,
        and polls until the render is finished before returning the output file.
        """
        """
        Wraps the process_api_request call in its own QThread so that its semaphore wait
        does not block the main thread. This method waits (using a local QEventLoop)
        until the worker finishes and then returns the output file path.
        """
        # Create the worker and a new thread:
        worker = ProcessApiRequestWorker(self, endpoint_config, image_data)
        thread = QThread()
        worker.moveToThread(thread)

        # Use a local event loop to wait for the worker to finish.
        loop = QEventLoop()
        result_container = []

        def handle_finished(result):
            result_container.append(result)
            loop.quit()

        def handle_error(err):
            result_container.append(None)
            loop.quit()

        worker.finished.connect(handle_finished)
        worker.error.connect(handle_error)
        thread.started.connect(worker.run)
        thread.start()

        # Run the event loop until the worker emits a signal.
        loop.exec()

        # Clean up the thread.
        thread.quit()
        thread.wait()

        if result_container:
            return result_container[0]
        return None
    def process_api_request_async(self, endpoint_config, image_data):
        try:
            import tempfile, os
            # Save the received image.
            tmp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp_file.write(image_data)
            tmp_file.close()
            received_image_path = tmp_file.name
            print(f"[DEBUG] Received API image saved to: {received_image_path}")

            # Ensure a shot is selected. If not, default to the first shot.
            if self.currentShotIndex < 0 and self.shots:
                self.currentShotIndex = 0
            shot = self.shots[self.currentShotIndex]

            # Update any workflow parameter flagged for API input.
            api_param_found = False
            for wf in shot.workflows:
                for param in wf.parameters.get("params", []):
                    if param.get("useApiImage") and param.get("dynamicOverrides", {}).get("type") == "api":
                        param["value"] = received_image_path
                        api_param_found = True
                        break
                if api_param_found:
                    break

            if not api_param_found:
                print("[DEBUG] No workflow parameter found for API dynamic assignment.")
                return None

            # Reset our API render-done flag.
            self._api_render_done = False

            # Trigger rendering by calling your onRenderSelected function.
            self.onRenderSelected()

            # Poll until rendering is finished (or timeout after, say, 60 seconds).
            start_time = time.time()
            timeout = 60  # seconds
            while not self._api_render_done:
                # Allow the main event loop to process pending events/signals.
                QCoreApplication.processEvents()
                time.sleep(0.01)
                if time.time() - start_time > timeout:
                    print("[DEBUG] API render timeout.")
                    break

            #TODO THE CURRENT IMPLEMENTAION FEELS SLOW, TEST WITH LARGE FILES, QUICK WF'S
            # self._api_mutex.lock()
            # timeout_ms = 60000  # 60 seconds
            # self._api_wait_condition.wait(self._api_mutex, timeout_ms)
            # self._api_mutex.unlock()

            # TODO BLOCKING IMPLEMENTATION TYPE 2:
            # if not self._api_semaphore.tryAcquire(1, 120000):  # wait for 60,000 ms
            #     print("[DEBUG] API render timeout.")
            #     return None

            # Check whether a still or video output file was produced.
            output_path = ""
            if shot.stillPath and os.path.exists(shot.stillPath):
                output_path = shot.stillPath
            elif shot.videoPath and os.path.exists(shot.videoPath):
                output_path = shot.videoPath

            print(f"[DEBUG] Returning output file: {output_path}")
            return output_path

        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "API Request Error", f"Error processing API request: {e}")
            return None

    # def process_api_request(self, endpoint_config, image_data):
    #     """
    #     This is called by the API server when an external app sends an image.
    #     It saves the image, sets the dynamic image path parameter for the matching Shot's Workflow,
    #     triggers that workflow (via executeWorkflow), and returns the generated output file.
    #     """
    #     try:
    #         # Save the received image.
    #         import tempfile
    #         tmp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    #         tmp_file.write(image_data)
    #         tmp_file.close()
    #         received_image_path = tmp_file.name
    #         print(f"[DEBUG] Received API image saved to: {received_image_path}")
    #
    #         # Find the currently selected shot and update any workflow parameter flagged for API input.
    #         shot = self.shots[self.currentShotIndex]
    #         matched = False
    #         for wf in shot.workflows:
    #             for param in wf.parameters.get("params", []):
    #                 if param.get("useApiImage") and param.get("dynamicOverrides", {}).get("type") == "api":
    #                     param["value"] = received_image_path
    #                     matched = True
    #                     # Trigger the workflow (you may choose to run only this workflow or all matching ones)
    #                     self.executeWorkflow(self.currentShotIndex, shot.workflows.index(wf))
    #                     break
    #             if matched:
    #                 break
    #         if not matched:
    #             print("[DEBUG] No workflow parameter found for API dynamic assignment.")
    #             return None
    #
    #         # After execution, assume the shot's output (stillPath or videoPath) is updated.
    #         output_path = shot.stillPath if shot.stillPath and os.path.exists(shot.stillPath) else ""
    #         return output_path
    #     except Exception as e:
    #         from PyQt6.QtWidgets import QMessageBox
    #         QMessageBox.warning(self, "API Request Error", f"Error processing API request: {e}")
    #         return None

    def toggleWebBrowser(self, checked):
        """
        Slot to handle the toggling of the WebBrowser dock widget. When shown,
        it loads the configured 'comfy_ip' URL. When hidden, it simply hides the dock.

        Args:
            checked (bool): The checked state of the toggle action.
        """
        pass
        # if checked:
        #     # Show the WebBrowser dock
        #     self.webBrowserDock.setVisible(True)
        #
        #     # Retrieve the 'comfy_ip' URL from settings
        #     comfy_ip = self.settingsManager.get("comfy_ip", "http://127.0.0.1:8188")
        #     # Load the URL in the WebBrowser view
        #     self.webBrowserView.setUrl(QUrl(comfy_ip))
        # else:
        #     # Hide the WebBrowser dock
        #     self.webBrowserDock.hide()

    def connectSignals(self):

        def onWorkflowsToggled(checked):
            for w in self.workflowGroupBox.children():
                if w is not self.workflowGroupBoxLayout:
                    if hasattr(w, "setVisible"):
                        w.setVisible(not w.isVisible())

        self.listWidget.itemClicked.connect(self.onItemClicked)
        self.listWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.listWidget.customContextMenuRequested.connect(self.onListWidgetContextMenu)
        self.listWidget.itemSelectionChanged.connect(self.onSelectionChanged)
        self.workflowGroupBox.toggled.connect(onWorkflowsToggled)
        self.paramsListWidget.itemClicked.connect(self.onParamItemClicked)
        self.paramsListWidget.customContextMenuRequested.connect(self.onParamContextMenu)
        self.addParamBtn.clicked.connect(self.addParamToShot)
        self.removeParamBtn.clicked.connect(self.removeParamFromShot)

    def addShot(self):
        new_shot = Shot(name=f"Shot {len(self.shots) + 1}")
        self.shots.append(new_shot)
        self.updateList()
        self.currentShotIndex = len(self.shots) - 1
        self.listWidget.setCurrentRow(self.listWidget.count() - 1)
        self.fillDock()
        self.setProjectModified(True)

    def onItemClicked(self, item):
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx == -1:
            self.addShot()
        else:
            self.currentShotIndex = idx
            self.fillDock()

    def onSelectionChanged(self):
        # try:
        #     self.player.stop()
        # except Exception:
        #     pass
        QMetaObject.invokeMethod(self.previewDock, "release_media", Qt.ConnectionType.QueuedConnection)
        selected_items = self.listWidget.selectedItems()

        if len(selected_items) == 1:
            item = selected_items[0]
            idx = item.data(Qt.ItemDataRole.UserRole)
            if idx != -1:
                self.currentShotIndex = idx
                self.fillDock()
                shot = self.shots[idx]
                if idx in self.lastSelectedWorkflowIndex:
                    last_wf_idx = self.lastSelectedWorkflowIndex[idx]
                    if 0 <= last_wf_idx < len(shot.workflows):
                        self.workflowListWidget.setCurrentRow(last_wf_idx)
                        workflow_item = self.workflowListWidget.item(last_wf_idx)
                        if workflow_item:
                            self.onWorkflowItemClicked(workflow_item)
                        self.shotSelected.emit(idx)
                        self.workflowSelected.emit(idx, last_wf_idx)
                    else:
                        del self.lastSelectedWorkflowIndex[idx]
                else:
                    last_rendered_workflow_idx = None
                    if shot.lastStillSignature:
                        for i, wf in enumerate(shot.workflows):
                            if wf.lastSignature == shot.lastStillSignature:
                                last_rendered_workflow_idx = i
                                break
                    if last_rendered_workflow_idx is None and shot.lastVideoSignature:
                        for i, wf in enumerate(shot.workflows):
                            if wf.lastSignature == shot.lastVideoSignature:
                                last_rendered_workflow_idx = i
                                break
                    if last_rendered_workflow_idx is not None:
                        self.workflowListWidget.setCurrentRow(last_rendered_workflow_idx)
                        workflow_item = self.workflowListWidget.item(last_rendered_workflow_idx)
                        if workflow_item:
                            self.onWorkflowItemClicked(workflow_item)
                        self.shotSelected.emit(idx)
                        self.workflowSelected.emit(idx, last_rendered_workflow_idx)
                    else:
                        self.shotSelected.emit(idx)
            else:
                self.currentShotIndex = -1
                self.clearDock()
        else:
            self.currentShotIndex = -1
            self.clearDock()

    def onListWidgetContextMenu(self, pos: QPoint):
        selected_items = self.listWidget.selectedItems()
        if not selected_items:
            return

        # Gather valid indices for shots
        valid_indices = []
        for item in selected_items:
            index = item.data(Qt.ItemDataRole.UserRole)
            if index is None or not isinstance(index, int) or index < 0 or index >= len(self.shots):
                continue
            valid_indices.append(index)
        if not valid_indices:
            return

        # Define local callbacks for each menu action
        def delete_shots():
            reply = QMessageBox.question(
                self,
                self.localization.translate("dialog_delete_shots_title", default="Delete Shot(s)"),
                self.localization.translate("dialog_delete_shots_question",
                                            default="Are you sure you want to delete the selected shots?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                for idx in sorted(valid_indices, reverse=True):
                    del self.shots[idx]
                self.currentShotIndex = -1
                self.updateList()
                self.clearDock()

        def duplicate_shots():
            for idx in sorted(valid_indices):
                shot = self.shots[idx]
                new_shot = copy.deepcopy(shot)
                new_shot.name = f"{shot.name} (Copy)"
                new_shot.stillPath = ""
                new_shot.videoPath = ""
                new_shot.imageVersions = []
                new_shot.videoVersions = []
                new_shot.currentImageVersion = -1
                new_shot.currentVideoVersion = -1
                new_shot.lastStillSignature = ""
                new_shot.lastVideoSignature = ""
                self.shots.insert(idx + 1, new_shot)
            self.updateList()

        def extend_clips():
            for idx in sorted(valid_indices):
                self.extendClip(idx)

        def merge_clips():
            self.mergeClips(valid_indices)

        # Build the list of menu actions.
        action_specs = [
            {
                "text": self.localization.translate("context_delete_shots", default="Delete Shot(s)"),
                "callback": delete_shots
            },
            {
                "text": self.localization.translate("context_duplicate_shots", default="Duplicate Shot(s)"),
                "callback": duplicate_shots
            },
            {
                "text": self.localization.translate("context_extend_clips", default="Extend Clip(s)"),
                "callback": extend_clips
            }
        ]
        # Only add "Merge Clips" if more than one item is selected.
        if len(selected_items) > 1:
            action_specs.append(
                {
                    "text": self.localization.translate("context_merge_clips", default="Merge Clips"),
                    "callback": merge_clips
                }
            )

        # Show the context menu using the helper.
        create_context_menu(self, action_specs, pos)

    def onParamContextMenu(self, pos):
        """
        Context menu to allow flagging a parameter so that its value
        will be dynamically replaced by the previous workflow's image
        or video output when the previous workflow finishes.
        """
        item = self.paramsListWidget.itemAt(pos)
        if not item:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return

        # Only proceed if data is a tuple in the expected format.
        if isinstance(data, tuple):
            paramItemType = data[0]
            # 'data' can be ("shot", param) or ("workflow", wf, param)
            if paramItemType in ["workflow", "shot"]:
                # Extract the parameter dictionary.
                param = data[-1] if paramItemType == "workflow" else data[1]
                paramType = param.get("type", "string")

                # Only process string-type or overrideable parameters.
                if param.get("type", "string") == "string":
                    from comfystudio.sdmodules.core.param_context_menu import get_param_context_action_specs
                    # For debugging: print the current registry.
                    from comfystudio.sdmodules.core.param_context_menu import _get_registry
                    print("Current registry:", _get_registry())
                    action_specs = get_param_context_action_specs(self, param)
                    create_context_menu(self, action_specs, pos)
                else:
                    # For non-string parameters, no actions are provided.
                    return
    def onParamItemClicked(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        if isinstance(data, tuple):
            if data[0] == "shot":
                param = data[1]
                ptype = param["type"]
                old_val = param["value"]
                self.editParamValue(param, ptype, old_val)
                self.saveCurrentWorkflowParams()
                self.refreshParamsList(self.shots[self.currentShotIndex])
            elif data[0] == "workflow":
                wf = data[1]
                node_id = data[2]
                param = data[3]
                ptype = param["type"]
                old_val = param["value"]
                self.editParamValue(param, ptype, old_val)
                self.saveCurrentWorkflowParamsForShot(wf)
                self.refreshParamsList(self.shots[self.currentShotIndex])
    def saveCurrentWorkflowParams(self, isVideo=False):
        self.settingsManager.save()

    def saveCurrentWorkflowParamsForShot(self, workflow: WorkflowAssignment):
        if self.currentShotIndex is None or self.currentShotIndex < 0 or self.currentShotIndex >= len(self.shots):
            return
        shot = self.shots[self.currentShotIndex]
        for wf in shot.workflows:
            if wf.path == workflow.path:
                wf.parameters = workflow.parameters
                break
        self.saveCurrentWorkflowParams()

    def editParamValue(self, param, ptype, old_val):
        if ptype == "int":
            new_val, ok = QInputDialog.getInt(self, "Edit Parameter", f"Enter new value for '{param['name']}':", old_val)
            if ok:
                param["value"] = new_val
        elif ptype == "float":
            new_val, ok = QInputDialog.getDouble(self, "Edit Parameter", f"Enter new value for '{param['name']}':", old_val, decimals=6)
            if ok:
                param["value"] = new_val
        else:
            text, ok = QInputDialog.getText(self, "Edit Parameter", f"Enter new value for '{param['name']}':", QLineEdit.EchoMode.Normal, str(old_val))
            if ok:
                param["value"] = text
    def addParamToShot(self):
        if self.currentShotIndex < 0 or self.currentShotIndex >= len(self.shots):
            QMessageBox.warning(self, "Warning", "No shot selected.")
            return
        shot = self.shots[self.currentShotIndex]

        dialog = QDialog(self)
        dialog.setWindowTitle("Add Parameter")
        dialogLayout = QFormLayout(dialog)

        nameEdit = QLineEdit()
        typeCombo = QComboBox()
        typeCombo.addItems(["string", "int", "float"])
        valueEdit = QLineEdit()

        dialogLayout.addRow("Name:", nameEdit)
        dialogLayout.addRow("Type:", typeCombo)
        dialogLayout.addRow("Value:", valueEdit)

        buttonsLayout = QHBoxLayout()
        addBtn = QPushButton("Add")
        cancelBtn = QPushButton("Cancel")
        buttonsLayout.addWidget(addBtn)
        buttonsLayout.addWidget(cancelBtn)
        dialogLayout.addRow(buttonsLayout)

        addBtn.clicked.connect(dialog.accept)
        cancelBtn.clicked.connect(dialog.reject)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            param_name = nameEdit.text().strip()
            param_type = typeCombo.currentText()
            param_value_str = valueEdit.text().strip()
            if not param_name:
                QMessageBox.warning(self, "Warning", "Parameter name cannot be empty.")
                return
            try:
                if param_type == "int":
                    param_value = int(param_value_str)
                elif param_type == "float":
                    param_value = float(param_value_str)
                else:
                    param_value = param_value_str
            except ValueError:
                QMessageBox.warning(self, "Warning", f"Invalid value for type '{param_type}'.")
                return

            new_param = {
                "name": param_name,
                "type": param_type,
                "displayName": param_name,
                "value": param_value,
                "nodeIDs": []
            }
            shot.params.append(new_param)
            self.refreshParamsList(shot)
            QMessageBox.information(self, "Info", f"Parameter '{param_name}' added to the shot.")

    def removeParamFromShot(self):
        if self.currentShotIndex < 0 or self.currentShotIndex >= len(self.shots):
            QMessageBox.warning(self, "Warning", "No shot selected.")
            return
        shot = self.shots[self.currentShotIndex]
        selected_items = self.paramsListWidget.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Warning", "No parameter selected to remove.")
            return
        item = selected_items[0]
        data = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(data, tuple) and data[0] == "shot":
            param = data[1]
            reply = QMessageBox.question(
                self,
                "Remove Parameter",
                f"Are you sure you want to remove parameter '{param['name']}' from this shot?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                shot.params.remove(param)
                self.refreshParamsList(shot)
                QMessageBox.information(self, "Info", "Parameter removed from the shot.")
        elif isinstance(data, tuple) and data[0] == "workflow":
            wf = data[1]
            param = data[2]
            reply = QMessageBox.question(
                self,
                "Remove Parameter",
                f"Are you sure you want to remove parameter '{param['name']}' from workflow '{os.path.basename(wf.path)}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                if "params" in wf.parameters:
                    wf.parameters["params"].remove(param)
                self.saveCurrentWorkflowParamsForShot(wf)
                self.refreshParamsList(shot)
                QMessageBox.information(self, "Info", "Parameter removed from the workflow.")

    def cleanUp(self):
        self.saveWindowState()
        self.settingsManager.save()
        self.stopComfy()

    def closeEvent(self, event):
        if self.isProjectModified():
            reply = QMessageBox.question(
                self,
                self.localization.translate("dialog_save_project_title", default="Save Project?"),
                self.localization.translate("dialog_save_project_question",
                                            default="Do you want to save the project before exiting?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.saveProject()
                # if self.isProjectSaved():
                self.cleanUp()
                event.accept()
                # else:
                #     event.ignore()
            elif reply == QMessageBox.StandardButton.No:
                self.cleanUp()
                event.accept()
            else:
                event.ignore()
        else:
            self.cleanUp()
            event.accept()