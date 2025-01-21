#!/usr/bin/env python

import sys
import os
import json
import random
import tempfile
import requests
import urllib
import copy

from qtpy.QtCore import (
    Qt, QUrl, QSize, QTimer, QStandardPaths, QPoint
)
from qtpy.QtGui import (
    QAction, QPixmap, QIcon, QColor
)
from qtpy.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QListWidget,
    QListWidgetItem, QLineEdit, QSpinBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QDockWidget, QMenuBar, QMenu, QPushButton, QLabel, QDialog,
    QComboBox, QMessageBox, QCheckBox, QTabWidget, QInputDialog
)
from qtpy.QtMultimedia import QMediaPlayer, QAudioOutput
from qtpy.QtMultimediaWidgets import QVideoWidget

class SettingsManager:
    def __init__(self):
        self.settings_file = os.path.join(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation),
            "CinemaShotDesigner",
            "user_settings.json"
        )
        self.data = {
            "comfy_py_path": "",
            "comfy_main_path": "",
            "comfy_ip": "http://localhost:8188",
            "default_shot_params": [],
            "default_image_params": [],
            "default_video_params": [
                {
                    "type": "image",
                    "name": "Image",
                    "value": "",
                    "useShotImage": True,
                    "nodeIDs": ["1"]
                },
            ]
        }
        self.load()

    def load(self):
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, "r") as f:
                    self.data.update(json.load(f))
            else:
                # Load defaults from defaults/config.json if user_settings.json doesn't exist
                default_config = os.path.join(os.path.dirname(__file__), "defaults", "config.json")
                if os.path.exists(default_config):
                    with open(default_config, "r") as df:
                        self.data.update(json.load(df))
        except Exception as e:
            print(f"Error loading configuration: {e}")

    def save(self):
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
        with open(self.settings_file, "w") as f:
            json.dump(self.data, f, indent=4)

    def set(self, key, value):
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)

class SettingsDialog(QDialog):
    def __init__(self, settingsManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.settingsManager = settingsManager

        layout = QFormLayout(self)

        self.comfyIpEdit = QLineEdit(self.settingsManager.get("comfy_ip", "http://localhost:8188"))
        layout.addRow("ComfyUI IP/Port:", self.comfyIpEdit)

        self.comfyPyPathEdit = QLineEdit(self.settingsManager.get("comfy_py_path", ""))
        layout.addRow("Comfy Python Path:", self.comfyPyPathEdit)
        self.comfyMainPathEdit = QLineEdit(self.settingsManager.get("comfy_main_path", ""))
        layout.addRow("Comfy Main Path:", self.comfyMainPathEdit)
        btnLayout = QHBoxLayout()
        okBtn = QPushButton("OK")
        cancelBtn = QPushButton("Cancel")
        btnLayout.addWidget(okBtn)
        btnLayout.addWidget(cancelBtn)
        layout.addRow(btnLayout)

        okBtn.clicked.connect(self.accept)
        cancelBtn.clicked.connect(self.reject)

    def accept(self):
        self.settingsManager.set("comfy_ip", self.comfyIpEdit.text().strip())
        self.settingsManager.set("comfy_py_path", self.comfyPyPathEdit.text().strip())
        self.settingsManager.set("comfy_main_path", self.comfyMainPathEdit.text().strip())
        self.settingsManager.save()
        super().accept()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cinema Shot Designer")
        self.resize(1200, 800)

        self.settingsManager = SettingsManager()

        self.globalImageParams = self.settingsManager.get("global_image_params", [])
        self.globalVideoParams = self.settingsManager.get("global_video_params", [])
        self.defaultShotParams = self.settingsManager.get("default_shot_params", [])
        self.defaultImageParams = self.settingsManager.get("default_image_params", [])
        self.defaultVideoParams = self.settingsManager.get("default_video_params", [])

        self.currentFilePath = None
        self.shots = []
        self.currentShotIndex = None

        self.last_prompt_id = None
        self.renderQueue = []
        self.activePrompts = {}

        self.result_timer = QTimer()
        self.result_timer.setInterval(2000)
        self.result_timer.timeout.connect(self.checkComfyResult)

        self.image_workflows = []
        self.video_workflows = []

        self.initUI()
        self.loadPlugins()
        self.loadWorkflows()
        self.updateList()

    def initUI(self):
        central = QWidget()
        self.setCentralWidget(central)
        self.mainLayout = QVBoxLayout(central)

        class ReorderableListWidget(QListWidget):
            def dropEvent(self, event):
                super().dropEvent(event)  # Perform the default drop behavior
                # After the drop, update the parent’s shots order
                if hasattr(self.parent(), 'syncShotsFromList'):
                    self.parent().syncShotsFromList()
        # Shots list
        self.listWidget = ReorderableListWidget()
        self.listWidget.setViewMode(self.listWidget.ViewMode.IconMode)
        self.listWidget.setFlow(self.listWidget.Flow.LeftToRight)
        self.listWidget.setWrapping(True)
        self.listWidget.setResizeMode(self.listWidget.ResizeMode.Adjust)
        self.listWidget.setMovement(self.listWidget.Movement.Static)
        self.listWidget.setIconSize(QSize(120, 90))
        self.listWidget.setSpacing(10)
        self.listWidget.itemClicked.connect(self.onItemClicked)
        self.listWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.listWidget.customContextMenuRequested.connect(self.onListWidgetContextMenu)
        self.listWidget.setDragEnabled(True)
        self.listWidget.setAcceptDrops(True)
        self.listWidget.setDropIndicatorShown(True)
        self.listWidget.setDragDropMode(self.listWidget.DragDropMode.InternalMove)
        self.listWidget.setMovement(self.listWidget.Movement.Free)
        self.listWidget.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.listWidget.model().rowsMoved.connect(self.onShotsReordered)

        self.mainLayout.addWidget(self.listWidget)

        # Dock for shot parameters
        self.dock = QDockWidget("Shot Parameters", self)
        self.dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock)

        self.dockContents = QWidget()
        self.dockLayout = QVBoxLayout(self.dockContents)
        self.dockTabWidget = QTabWidget()
        self.dockLayout.addWidget(self.dockTabWidget)

        # We have 5 tabs: Global Image, Global Video, Shot Image, Shot Video, Shot Misc
        self.globalImageTab = QWidget()
        self.globalImageForm = QFormLayout(self.globalImageTab)

        self.globalVideoTab = QWidget()
        self.globalVideoForm = QFormLayout(self.globalVideoTab)

        self.imageTab = QWidget()
        self.imageForm = QFormLayout(self.imageTab)

        self.videoTab = QWidget()
        self.videoForm = QFormLayout(self.videoTab)

        self.currentShotTab = QWidget()
        self.currentShotForm = QFormLayout(self.currentShotTab)

        self.dockTabWidget.addTab(self.globalImageTab, "Global Image Params")
        self.dockTabWidget.addTab(self.globalVideoTab, "Global Video Params")
        self.dockTabWidget.addTab(self.imageTab, "Shot Image Params")
        self.dockTabWidget.addTab(self.videoTab, "Shot Video Params")
        self.dockTabWidget.addTab(self.currentShotTab, "Shot: Misc")

        # Render workflow selectors
        renderLayout = QHBoxLayout()
        self.stillWorkflowCombo = QComboBox()
        self.renderStillBtn = QPushButton("Render Still")
        renderLayout.addWidget(self.stillWorkflowCombo)
        renderLayout.addWidget(self.renderStillBtn)

        self.videoWorkflowCombo = QComboBox()
        self.renderVideoBtn = QPushButton("Render Video")
        renderLayout.addWidget(self.videoWorkflowCombo)
        renderLayout.addWidget(self.renderVideoBtn)
        self.dockLayout.addLayout(renderLayout)

        # Video preview
        self.videoWidget = QVideoWidget()
        self.dockLayout.addWidget(self.videoWidget)
        self.player = QMediaPlayer()
        self.audioOutput = QAudioOutput()
        self.player.setAudioOutput(self.audioOutput)
        self.player.setVideoOutput(self.videoWidget)

        # Media controls
        self.controlsLayout = QHBoxLayout()
        self.playBtn = QPushButton("Play")
        self.pauseBtn = QPushButton("Pause")
        self.stopBtn = QPushButton("Stop")
        self.controlsLayout.addWidget(self.playBtn)
        self.controlsLayout.addWidget(self.pauseBtn)
        self.controlsLayout.addWidget(self.stopBtn)
        self.dockLayout.addLayout(self.controlsLayout)

        self.playBtn.clicked.connect(self.player.play)
        self.pauseBtn.clicked.connect(self.player.pause)
        self.stopBtn.clicked.connect(self.player.stop)
        self.renderStillBtn.clicked.connect(self.onRenderStill)
        self.renderVideoBtn.clicked.connect(self.onRenderVideo)

        self.dock.setWidget(self.dockContents)
        self.createMenuBar()
        self.createToolBar()
        self.createStatusBar()

        # Populate global param forms
        self.refreshGlobalImageParams()
        self.refreshGlobalVideoParams()

    def onShotsReordered(self, parent, start, end, destination, row):
        print(start, end, destination, row)
        # Extract the block of shots being moved
        moved_block = self.shots[start:end + 1]

        # Remove the moved items from their original positions
        del self.shots[start:end + 1]

        # Adjust the target insertion index if necessary.
        # If the destination index is after the removed block, adjust for the removed items.
        if row > start:
            row -= (end - start + 1)

        # Insert the moved block at the new position
        for i, shot in enumerate(moved_block):
            self.shots.insert(row + i, shot)

        # Refresh the visual list to renumber items and update icons
        self.updateList()

    def syncShotsFromList(self):
        new_order = []
        for i in range(self.listWidget.count()):
            item = self.listWidget.item(i)
            idx = item.data(Qt.ItemDataRole.UserRole)
            # Skip the special "Add New Shot" item
            if idx is None or idx == -1:
                continue
            new_order.append(self.shots[idx])
        self.shots = new_order
        self.updateList()
    def loadPlugins(self):
        plugins_dir = os.path.join(os.path.dirname(__file__), "plugins")
        if not os.path.isdir(plugins_dir):
            return
        sys.path.insert(0, plugins_dir)
        for filename in os.listdir(plugins_dir):
            if filename.endswith(".py") and not filename.startswith("__"):
                modulename = filename[:-3]
                try:
                    module = __import__(modulename)
                    if hasattr(module, "register"):
                        module.register(self)
                except Exception as e:
                    print(f"Error loading plugin {modulename}: {e}")
        sys.path.pop(0)
    def createMenuBar(self):
        menuBar = QMenuBar(self)

        fileMenu = QMenu("File", self)
        newAct = QAction("New Project", self)
        openAct = QAction("Open", self)
        saveAct = QAction("Save", self)
        saveAsAct = QAction("Save As", self)
        newAct.triggered.connect(self.newProject)
        openAct.triggered.connect(self.openProject)
        saveAct.triggered.connect(self.saveProject)
        saveAsAct.triggered.connect(self.saveProjectAs)
        fileMenu.addAction(newAct)
        fileMenu.addAction(openAct)
        fileMenu.addAction(saveAct)
        fileMenu.addAction(saveAsAct)

        importAction = QAction("Import Shots from TXT", self)
        importAction.triggered.connect(self.importShotsFromTxt)
        fileMenu.addAction(importAction)

        genAllStillsAct = QAction("Generate All Stills", self)
        genAllVideosAct = QAction("Generate All Videos", self)
        genAllStillsAct.triggered.connect(self.onGenerateAllStills)
        genAllVideosAct.triggered.connect(self.onGenerateAllVideos)
        fileMenu.addAction(genAllStillsAct)
        fileMenu.addAction(genAllVideosAct)

        workflowEditorAct = QAction("Workflow Editor", self)
        workflowEditorAct.triggered.connect(self.openWorkflowEditor)
        fileMenu.addAction(workflowEditorAct)

        settingsMenu = QMenu("Settings", self)
        openSettingsAct = QAction("Open Settings", self)
        openSettingsAct.triggered.connect(self.showSettingsDialog)
        settingsMenu.addAction(openSettingsAct)

        menuBar.addMenu(fileMenu)
        menuBar.addMenu(settingsMenu)
        self.setMenuBar(menuBar)
    def createToolBar(self):
        # Add toolbar
        toolbar = self.addToolBar("Main Toolbar")
        renderAllStillsBtn = QAction("Render All Stills", self)
        renderAllVideosBtn = QAction("Render All Videos", self)
        stopRenderingBtn = QAction("Stop Rendering", self)
        toolbar.addAction(renderAllStillsBtn)
        toolbar.addAction(renderAllVideosBtn)
        toolbar.addAction(stopRenderingBtn)
        self.startComfyBtn = QAction("Start Comfy", self)
        self.stopComfyBtn = QAction("Stop Comfy", self)
        toolbar.addAction(self.startComfyBtn)
        toolbar.addAction(self.stopComfyBtn)
        renderAllStillsBtn.triggered.connect(self.onGenerateAllStills)
        renderAllVideosBtn.triggered.connect(self.onGenerateAllVideos)
        stopRenderingBtn.triggered.connect(self.stopRendering)

        self.startComfyBtn.triggered.connect(self.startComfy)
        self.stopComfyBtn.triggered.connect(self.stopComfy)
    def stopRendering(self):
        self.renderQueue.clear()
        self.statusMessage.setText("Render queue cleared.")

    def createStatusBar(self):
        self.status = self.statusBar()
        self.statusMessage = QLabel("Ready")
        self.status.addPermanentWidget(self.statusMessage, 1)
    def startComfy(self):
        import subprocess
        py_path = self.settingsManager.get("comfy_py_path")
        main_path = self.settingsManager.get("comfy_main_path")
        if py_path and main_path:
            self.comfy_process = subprocess.Popen([py_path, main_path])
            self.statusMessage.setText("Comfy started.")
        else:
            QMessageBox.warning(self, "Error", "Comfy paths not set in settings.")

    def stopComfy(self):
        if hasattr(self, 'comfy_process'):
            self.comfy_process.terminate()
            self.statusMessage.setText("Comfy stopped.")

    def loadWorkflows(self):
        base_dir = os.path.join(os.path.dirname(__file__), "workflows")
        image_dir = os.path.join(base_dir, "image")
        video_dir = os.path.join(base_dir, "video")

        self.image_workflows = []
        self.video_workflows = []

        if os.path.isdir(image_dir):
            for fname in os.listdir(image_dir):
                if fname.lower().endswith(".json"):
                    self.image_workflows.append(fname)

        if os.path.isdir(video_dir):
            for fname in os.listdir(video_dir):
                if fname.lower().endswith(".json"):
                    self.video_workflows.append(fname)

        self.stillWorkflowCombo.clear()
        self.videoWorkflowCombo.clear()

        self.stillWorkflowCombo.addItem("Select workflow...", "")
        for wf in self.image_workflows:
            self.stillWorkflowCombo.addItem(wf, os.path.join(image_dir, wf))

        self.videoWorkflowCombo.addItem("Select workflow...", "")
        for wf in self.video_workflows:
            self.videoWorkflowCombo.addItem(wf, os.path.join(video_dir, wf))

    def openWorkflowEditor(self):
        from comfystudio.sdmodules.editor import WorkflowEditor
        editor = WorkflowEditor(self.settingsManager, parent=self)
        editor.exec()

    def newProject(self):
        self.currentFilePath = None
        self.shots = []
        self.currentShotIndex = None
        self.updateList()
        self.clearDock()

    def addShot(self):
        """
        Create a new shot. If there is a current shot, inherit its parameter structure
        (but clear out the actual paths and versions). Otherwise, use the default params.
        """
        if self.shots and self.currentShotIndex is not None and self.currentShotIndex >= 0:
            reference_shot = self.shots[self.currentShotIndex]
            new_shot = copy.deepcopy(reference_shot)
            new_shot["name"] = f"Shot {len(self.shots) + 1}"
            new_shot["stillPath"] = ""
            new_shot["videoPath"] = ""
            new_shot["imageVersions"] = []
            new_shot["videoVersions"] = []
            new_shot["currentImageVersion"] = -1
            new_shot["currentVideoVersion"] = -1
        else:
            # fallback to default
            new_shot = {
                "name": f"Shot {len(self.shots) + 1}",
                "shotParams": copy.deepcopy(self.defaultShotParams),
                "imageParams": copy.deepcopy(self.defaultImageParams),
                "videoParams": copy.deepcopy(self.defaultVideoParams),
                "params": [],
                "stillPath": "",
                "videoPath": "",
                "imageVersions": [],
                "videoVersions": [],
                "currentImageVersion": -1,
                "currentVideoVersion": -1
            }
        self.shots.append(new_shot)
        self.updateList()

    def importShotsFromTxt(self):
        # Step 1: Open a file dialog to select a TXT file
        filename, _ = QFileDialog.getOpenFileName(self, "Select TXT File", "", "Text Files (*.txt)")
        if not filename:
            return

        # Step 2: Read lines from the file
        with open(filename, "r") as f:
            lines = [line.strip() for line in f if line.strip()]
        if not lines:
            QMessageBox.information(self, "Info", "No lines found in file.")
            return

        # Step 3: Gather available parameter fields from default parameter lists
        possible_fields = []
        param_mapping = {}
        for param in self.defaultShotParams:
            key = f"ShotParam: {param['name']}"
            possible_fields.append(key)
            param_mapping[key] = ('shotParams', param)
        for param in self.defaultImageParams:
            key = f"ImageParam: {param['name']}"
            possible_fields.append(key)
            param_mapping[key] = ('imageParams', param)
        for param in self.defaultVideoParams:
            key = f"VideoParam: {param['name']}"
            possible_fields.append(key)
            param_mapping[key] = ('videoParams', param)

        if not possible_fields:
            QMessageBox.information(self, "Info", "No parameter fields available for import.")
            return

        # Step 4: Ask the user to select a field into which to import lines
        field, ok = QInputDialog.getItem(self, "Select Field",
                                         "Select a field to import lines into:",
                                         possible_fields, 0, False)
        if not ok or not field:
            return

        array_name, default_param = param_mapping[field]

        # Step 5: Determine a reference shot for inheritance, if available
        reference_shot = None
        if self.shots and self.currentShotIndex is not None and self.currentShotIndex >= 0:
            reference_shot = self.shots[self.currentShotIndex]

        # For each line, create a new shot and override the chosen field
        for line in lines:
            # Create a new shot based on current reference or default
            if reference_shot:
                new_shot = copy.deepcopy(reference_shot)
                new_shot["name"] = f"Shot {len(self.shots) + 1}"
                new_shot["stillPath"] = ""
                new_shot["videoPath"] = ""
                new_shot["imageVersions"] = []
                new_shot["videoVersions"] = []
                new_shot["currentImageVersion"] = -1
                new_shot["currentVideoVersion"] = -1
            else:
                new_shot = {
                    "name": f"Shot {len(self.shots) + 1}",
                    "shotParams": copy.deepcopy(self.defaultShotParams),
                    "imageParams": copy.deepcopy(self.defaultImageParams),
                    "videoParams": copy.deepcopy(self.defaultVideoParams),
                    "params": [],
                    "stillPath": "",
                    "videoPath": "",
                    "imageVersions": [],
                    "videoVersions": [],
                    "currentImageVersion": -1,
                    "currentVideoVersion": -1
                }

            # Override the selected field with the line's content
            params_array = new_shot[array_name]
            for param in params_array:
                if param["name"] == default_param["name"]:
                    param["value"] = line
                    break  # Assume field names are unique within this array

            self.shots.append(new_shot)

        # Step 6: Update the UI list of shots
        self.updateList()
        QMessageBox.information(self, "Import Completed", f"Imported {len(lines)} shots.")
    def openProject(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Project", "", "JSON Files (*.json)")
        if path:
            with open(path, "r") as f:
                data = json.load(f)
            self.shots = data.get("shots", [])
            self.currentFilePath = path
            self.currentShotIndex = None
            self.updateList()
            self.clearDock()

    def saveProject(self):
        if not self.currentFilePath:
            self.saveProjectAs()
        else:
            self.writeProject(self.currentFilePath)

    def saveProjectAs(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Project As", "", "JSON Files (*.json)")
        if path:
            self.currentFilePath = path
            self.writeProject(path)

    def writeProject(self, path):
        data = {"shots": self.shots}
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            QMessageBox.warning(self, "Save Error", str(e))

    def updateList(self):
        self.listWidget.clear()
        for i, shot in enumerate(self.shots):
            icon = self.getShotIcon(shot)
            label_text = f"Shot {i + 1}"
            item = QListWidgetItem(icon, label_text)
            item.setData(Qt.ItemDataRole.UserRole, i)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            self.listWidget.addItem(item)

        # "Add New Shot"
        addIcon = QIcon()
        addItem = QListWidgetItem(addIcon, "Add New Shot")
        addItem.setData(Qt.ItemDataRole.UserRole, -1)
        addItem.setFlags(addItem.flags() | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
        self.listWidget.addItem(addItem)

    def onItemClicked(self, item):
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx == -1:
            self.addShot()
        else:
            self.currentShotIndex = idx
            self.fillDock()
    def onListWidgetContextMenu(self, pos: QPoint):
        """
        Show a right-click context menu on the shots list for deleting or duplicating a shot.
        """
        item = self.listWidget.itemAt(pos)
        if not item:
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx < 0 or idx >= len(self.shots):
            return  # Ignore the 'Add new shot' item or out-of-bounds

        menu = QMenu(self)
        deleteAction = menu.addAction("Delete Shot")
        duplicateAction = menu.addAction("Duplicate Shot")
        extendAction = menu.addAction("Extend Clip")


        action = menu.exec(self.listWidget.mapToGlobal(pos))
        if action == deleteAction:
            # Confirm
            reply = QMessageBox.question(
                self,
                "Delete Shot",
                f"Are you sure you want to delete '{self.shots[idx].get('name','Shot')}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                del self.shots[idx]
                self.currentShotIndex = None
                self.updateList()
                self.clearDock()
        elif action == duplicateAction:
            ref_shot = self.shots[idx]
            new_shot = copy.deepcopy(ref_shot)
            new_shot["name"] = f"{ref_shot['name']} (Copy)"
            # Clear out any final paths/versions if you want
            new_shot["stillPath"] = ""
            new_shot["videoPath"] = ""
            new_shot["imageVersions"] = []
            new_shot["videoVersions"] = []
            new_shot["currentImageVersion"] = -1
            new_shot["currentVideoVersion"] = -1

            self.shots.append(new_shot)
            self.updateList()
        elif action == extendAction:
            self.extendClip(idx)
    def clearDock(self):
        for frm in [self.imageForm, self.videoForm, self.currentShotForm]:
            while frm.rowCount() > 0:
                frm.removeRow(0)

    def fillDock(self):
        """
        Fill the three shot tabs:
        1) Shot Image Params (self.imageForm)
        2) Shot Video Params (self.videoForm)
        3) Shot Misc (self.currentShotForm) => includes only shotParams + extras in 'params'
        """
        self.clearDock()
        if self.currentShotIndex is None or self.currentShotIndex < 0 or self.currentShotIndex >= len(self.shots):
            return

        shot = self.shots[self.currentShotIndex]

        # --- 1) Shot Image Params ---
        for param in shot.get("imageParams", []):
            ptype = param.get("type", "string")
            if ptype == "image":
                rowWidget = self.createImageParamWidget(param)
            elif ptype == "video":
                rowWidget = self.createVideoParamWidget(param)
            else:
                rowWidget = self.createBasicParamWidget(param)
            self.imageForm.addRow(param["name"], rowWidget)

        # --- 2) Shot Video Params ---
        for param in shot.get("videoParams", []):
            ptype = param.get("type", "string")
            if ptype == "image":
                rowWidget = self.createImageParamWidget(param)
            elif ptype == "video":
                rowWidget = self.createVideoParamWidget(param)
            else:
                rowWidget = self.createBasicParamWidget(param)
            self.videoForm.addRow(param["name"], rowWidget)

        # --- 3) Shot Misc => shotParams + shot["params"] ---
        # Only fill non-'image' and non-'video' from shotParams, because 'image' / 'video'
        # are already handled above. The same logic for param in shot["params"].
        for param in shot.get("shotParams", []):
            ptype = param.get("type", "string")
            if ptype in ("image", "video"):
                # Skip here since we already place them in the relevant tab
                continue
            rowWidget = self.createBasicParamWidget(param)
            self.currentShotForm.addRow(param["name"], rowWidget)

        for param in shot["params"]:
            ptype = param.get("type", "string")
            pname = param.get("name", "Unknown")
            if ptype in ("image", "video"):
                # Skip here, or handle similarly if you want them in the Misc tab
                continue
            rowWidget = self.createBasicParamWidget(param)
            self.currentShotForm.addRow(pname, rowWidget)

        # Versions & video preview
        if shot["imageVersions"]:
            label = QLabel("Image Version:")
            combo = QComboBox()
            for i, path in enumerate(shot["imageVersions"]):
                combo.addItem(f"Version {i + 1}: {os.path.basename(path)}", path)
            combo.setCurrentIndex(shot["currentImageVersion"] if shot["currentImageVersion"] >= 0 else 0)
            combo.currentIndexChanged.connect(
                lambda idx, s=shot, c=combo: self.onImageVersionChanged(s, c, idx)
            )
            self.currentShotForm.addRow(label, combo)

        if shot["videoVersions"]:
            label = QLabel("Video Version:")
            combo = QComboBox()
            for i, path in enumerate(shot["videoVersions"]):
                combo.addItem(f"Version {i + 1}: {os.path.basename(path)}", path)
            combo.setCurrentIndex(shot["currentVideoVersion"] if shot["currentVideoVersion"] >= 0 else 0)
            combo.currentIndexChanged.connect(
                lambda idx, s=shot, c=combo: self.onVideoVersionChanged(s, c, idx)
            )
            self.currentShotForm.addRow(label, combo)

        videoPath = shot.get("videoPath", "")
        if videoPath and os.path.exists(videoPath):
            self.player.setSource(QUrl.fromLocalFile(videoPath))
            self.player.play()
            self.player.pause()
        else:
            self.player.setSource(QUrl())
        self.videoWidget.setMinimumSize(320, 240)
        self.statusMessage.setText("Ready")

    def getShotIcon(self, shot):
        if shot.get("stillPath") and os.path.exists(shot["stillPath"]):
            base_pix = QPixmap(shot["stillPath"])
            if not base_pix.isNull():
                base_pix = base_pix.scaled(120, 90, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation)
            else:
                base_pix = self.makeFallbackPixmap()
        else:
            base_pix = self.makeFallbackPixmap()

        final_pix = QPixmap(120, 90)
        final_pix.fill(Qt.GlobalColor.transparent)

        from qtpy.QtGui import QPainter, QBrush, QPen
        painter = QPainter(final_pix)
        painter.drawPixmap(0, 0, base_pix)

        img_status_color = self.getShotImageStatusColor(shot)
        vid_status_color = self.getShotVideoStatusColor(shot)

        circle_radius = 8

        painter.setBrush(QBrush(img_status_color))
        painter.setPen(QPen(Qt.GlobalColor.black, 1))
        painter.drawEllipse(2, 2, circle_radius, circle_radius)

        painter.setBrush(QBrush(vid_status_color))
        painter.setPen(QPen(Qt.GlobalColor.black, 1))
        painter.drawEllipse(final_pix.width() - circle_radius - 2, 2, circle_radius, circle_radius)
        painter.end()

        return QIcon(final_pix)

    def makeFallbackPixmap(self):
        pix = QPixmap(120, 90)
        pix.fill(Qt.GlobalColor.lightGray)
        return pix

    def getShotImageStatusColor(self, shot):
        if not shot.get("stillPath"):
            return QColor("red")
        if not os.path.exists(shot["stillPath"]):
            return QColor("red")
        current_sig = self.computeRenderSignature(shot, isVideo=False)
        last_sig = shot.get("lastStillSignature", "")
        return QColor("green") if (last_sig == current_sig) else QColor("orange")

    def getShotVideoStatusColor(self, shot):
        if not shot.get("videoPath"):
            return QColor("red")
        if not os.path.exists(shot["videoPath"]):
            return QColor("red")
        current_sig = self.computeRenderSignature(shot, isVideo=True)
        last_sig = shot.get("lastVideoSignature", "")
        return QColor("green") if (last_sig == current_sig) else QColor("orange")

    def createBasicParamWidget(self, param):
        ptype = param["type"]
        pval = param["value"]
        if ptype == "int":
            w = QSpinBox()
            w.setRange(0, 2147483647)
            w.setValue(min(pval, 2147483647))
            w.valueChanged.connect(lambda v, p=param: self.onParamChanged(p, v))
            return w
        elif ptype == "float":
            w = QDoubleSpinBox()
            w.setRange(0.0, 2147483647.0)
            w.setDecimals(3)
            w.setValue(pval)
            w.valueChanged.connect(lambda v, p=param: self.onParamChanged(p, v))
            return w
        else:
            w = QLineEdit()
            w.setText(str(pval))
            w.textChanged.connect(lambda v, p=param: self.onParamChanged(p, v))
            return w

    def createImageParamWidget(self, param):
        if param.get("type", "string") != "image":
            # Fallback to basic widget if it's not really an image param
            return self.createBasicParamWidget(param)
        container = QWidget()
        col = QVBoxLayout(container)

        row1 = QHBoxLayout()
        pathEdit = QLineEdit(param["value"])
        selectBtn = QPushButton("Select")
        preview = QLabel()
        preview.setFixedSize(60, 45)
        preview.setStyleSheet("border: 1px solid gray;")

        if param["value"] and os.path.exists(param["value"]):
            pix = QPixmap(param["value"])
            if not pix.isNull():
                scaled = pix.scaled(60, 45, Qt.AspectRatioMode.KeepAspectRatio)
                preview.setPixmap(scaled)

        def onSelect():
            filters = "Image Files (*.png *.jpg *.bmp);;All Files (*)"
            filePath, _ = QFileDialog.getOpenFileName(self, "Select Image", "", filters)
            if filePath:
                param["value"] = filePath
                pathEdit.setText(filePath)
                pix_ = QPixmap(filePath)
                if not pix_.isNull():
                    scaled_ = pix_.scaled(60, 45, Qt.AspectRatioMode.KeepAspectRatio)
                    preview.setPixmap(scaled_)
                else:
                    preview.clear()

        pathEdit.textChanged.connect(lambda val, p=param: self.onParamChanged(p, val))
        selectBtn.clicked.connect(onSelect)

        row1.addWidget(pathEdit)
        row1.addWidget(selectBtn)
        row1.addWidget(preview)

        row2 = QHBoxLayout()
        useShotCheck = QCheckBox("Use Rendered Shot")
        useShotCheck.setChecked(param.get("useShotImage", False))

        def onUseShotToggled(state):
            param["useShotImage"] = bool(state)

        useShotCheck.stateChanged.connect(onUseShotToggled)
        row2.addWidget(useShotCheck)

        col.addLayout(row1)
        col.addLayout(row2)
        return container

    def createVideoParamWidget(self, param):

        if param.get("type", "string") != "video":
            # Fallback to basic widget if it's not really an image param
            return self.createBasicParamWidget(param)
        container = QWidget()
        hbox = QHBoxLayout(container)
        pathEdit = QLineEdit(param["value"])
        selectBtn = QPushButton("Select")

        preview = QLabel("No Video")
        if param["value"] and os.path.exists(param["value"]):
            preview.setText("Video Loaded")

        def onSelect():
            filters = "Video Files (*.mp4 *.mov *.avi);;All Files (*)"
            filePath, _ = QFileDialog.getOpenFileName(self, "Select Video", "", filters)
            if filePath:
                param["value"] = filePath
                pathEdit.setText(filePath)
                preview.setText("Video Loaded")

        pathEdit.textChanged.connect(lambda val, p=param: self.onParamChanged(p, val))
        selectBtn.clicked.connect(onSelect)

        hbox.addWidget(pathEdit)
        hbox.addWidget(selectBtn)
        hbox.addWidget(preview)
        return container

    def onParamChanged(self, paramDict, newVal):
        paramDict["value"] = newVal

    def onImageVersionChanged(self, shot, combo, idx):
        shot["currentImageVersion"] = idx
        new_path = combo.itemData(idx)
        shot["stillPath"] = new_path
        self.updateList()

    def onVideoVersionChanged(self, shot, combo, idx):
        shot["currentVideoVersion"] = idx
        new_path = combo.itemData(idx)
        shot["videoPath"] = new_path
        self.player.setSource(QUrl.fromLocalFile(new_path))
        self.updateList()

    def refreshGlobalImageParams(self):
        while self.globalImageForm.rowCount() > 0:
            self.globalImageForm.removeRow(0)
        for idx, param in enumerate(self.globalImageParams):
            rowWidget = self.createGlobalParamWidget(param, isVideo=False)
            self.globalImageForm.addRow(param.get("name", f"Param {idx}"), rowWidget)

    def refreshGlobalVideoParams(self):
        while self.globalVideoForm.rowCount() > 0:
            self.globalVideoForm.removeRow(0)
        for idx, param in enumerate(self.globalVideoParams):
            rowWidget = self.createGlobalParamWidget(param, isVideo=True)
            self.globalVideoForm.addRow(param.get("name", f"Param {idx}"), rowWidget)

    def createGlobalParamWidget(self, param, isVideo=False):
        container = QWidget()
        layout = QHBoxLayout(container)

        ptype = param["type"]
        pval = param["value"]

        if ptype == "int":
            w = QSpinBox()
            w.setRange(0, 2147483647)
            w.setValue(min(pval, 2147483647))
            w.valueChanged.connect(lambda v, p=param: self.onGlobalParamChanged(p, v))
        elif ptype == "float":
            w = QDoubleSpinBox()
            w.setRange(0.0, 2147483647.0)
            w.setDecimals(3)
            w.setValue(pval)
            w.valueChanged.connect(lambda v, p=param: self.onGlobalParamChanged(p, v))
        else:
            w = QLineEdit(str(pval))
            w.textChanged.connect(lambda val, p=param: self.onGlobalParamChanged(p, val))

        removeBtn = QPushButton("Remove")
        removeBtn.clicked.connect(lambda _, p=param, vid=isVideo: self.removeGlobalParam(p, vid))

        layout.addWidget(w)
        layout.addWidget(removeBtn)
        return container

    def onGlobalParamChanged(self, param, newVal):
        param["value"] = newVal

    def addShotParam(self, nodeID, paramName, paramType, paramValue, isVideo=False):
        """
        When a new shot-level parameter is exposed, also store it in the default arrays
        so that newly created shots will inherit it in the future.
        """
        # Always add to default arrays, regardless of currentShotIndex:
        if isVideo:
            self.defaultVideoParams.append({
                "type": paramType,
                "name": paramName,
                "value": paramValue,
                "nodeIDs": [str(nodeID)]
            })
            self.settingsManager.set("default_video_params", self.defaultVideoParams)
        else:
            self.defaultImageParams.append({
                "type": paramType,
                "name": paramName,
                "value": paramValue,
                "nodeIDs": [str(nodeID)]
            })
            self.settingsManager.set("default_image_params", self.defaultImageParams)

        self.settingsManager.save()

        # If no active shot, we're done. The param is stored in defaults for future shots.
        if self.currentShotIndex is None or self.currentShotIndex < 0 or self.currentShotIndex >= len(self.shots):
            QMessageBox.information(
                self, "Info",
                f"No active shot. Param '{paramName}' saved to {'video' if isVideo else 'image'} defaults."
            )
            return

        # Otherwise, attach to the current shot
        shot = self.shots[self.currentShotIndex]
        new_param = {
            "type": paramType,
            "name": paramName,
            "value": paramValue,
            "nodeIDs": [str(nodeID)]
        }
        if isVideo:
            shot["videoParams"].append(new_param)
        else:
            shot["imageParams"].append(new_param)

        QMessageBox.information(
            self,
            "Param Exposed",
            f"Param '{paramName}' (type '{paramType}') added to {'video' if isVideo else 'image'} params "
            f"and stored in defaults."
        )
        self.fillDock()

    def addGlobalParam(self, nodeID, paramName, paramType, paramValue, isVideo=False):
        """
        Creates a new param dictionary and appends it to either globalVideoParams or
        globalImageParams, based solely on isVideo (not overriding the paramType).
        Then refresh the global forms.
        """
        new_param = {
            "type": paramType,
            "name": paramName,
            "value": paramValue,
            "nodeIDs": [str(nodeID)],
            "useShotImage": False
        }

        if isVideo:
            self.globalVideoParams.append(new_param)
            self.refreshGlobalVideoParams()
            QMessageBox.information(
                self,
                "Global Param Added",
                f"Param '{paramName}' (type '{paramType}') added to Global Video Params."
            )
        else:
            self.globalImageParams.append(new_param)
            self.refreshGlobalImageParams()
            QMessageBox.information(
                self,
                "Global Param Added",
                f"Param '{paramName}' (type '{paramType}') added to Global Image Params."
            )

    def removeGlobalParam(self, param, isVideo):
        if isVideo:
            self.globalVideoParams.remove(param)
            self.refreshGlobalVideoParams()
        else:
            self.globalImageParams.remove(param)
            self.refreshGlobalImageParams()

    def onRenderStill(self):
        if self.currentShotIndex is None or self.currentShotIndex < 0 or self.currentShotIndex >= len(self.shots):
            return
        shot = self.shots[self.currentShotIndex]
        workflow_path = self.stillWorkflowCombo.currentData()
        if not workflow_path:
            QMessageBox.information(self, "Info", "Please select a workflow for Still render.")
            return
        self.renderWithWorkflow(workflow_path, shot, isVideo=False)

    def onRenderVideo(self):
        if self.currentShotIndex is None or self.currentShotIndex < 0 or self.currentShotIndex >= len(self.shots):
            return
        shot = self.shots[self.currentShotIndex]
        workflow_path = self.videoWorkflowCombo.currentData()
        if not workflow_path:
            QMessageBox.information(self, "Info", "Please select a workflow for Video render.")
            return
        self.renderWithWorkflow(workflow_path, shot, isVideo=True)

    def onGenerateAllStills(self):
        """
        Clear the render queue, then enqueue any shots whose signature has changed
        or which lack a valid stillPath. Start the queue.
        """
        self.renderQueue.clear()
        for i, shot in enumerate(self.shots):
            new_signature = self.computeRenderSignature(shot, isVideo=False)
            last_sig = shot.get("lastStillSignature", "")
            still_path = shot.get("stillPath", "")
            # Only re-render if there's no existing still or signature changed
            if not still_path or (new_signature != last_sig):
                self.queueShotRender(i, isVideo=False)
        self.startNextRender()

    def onGenerateAllVideos(self):
        """
        Clear the render queue, then enqueue any shots whose signature has changed
        or which lack a valid videoPath. Start the queue.
        """
        self.renderQueue.clear()
        for i, shot in enumerate(self.shots):
            new_signature = self.computeRenderSignature(shot, isVideo=True)
            last_sig = shot.get("lastVideoSignature", "")
            video_path = shot.get("videoPath", "")
            if not video_path or (new_signature != last_sig):
                self.queueShotRender(i, isVideo=True)
        self.startNextRender()
    def extendClip(self, shotIndex):
        import cv2, copy
        shot = self.shots[shotIndex]
        video_path = shot.get("videoPath", "")
        if not video_path or not os.path.exists(video_path):
            QMessageBox.information(self, "Info", "No video found for this shot.")
            return

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            QMessageBox.warning(self, "Error", "Cannot open video file.")
            return

        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_count - 1)
        ret, frame = cap.read()
        if not ret:
            QMessageBox.warning(self, "Error", "Failed to read last frame.")
            cap.release()
            return

        temp_dir = tempfile.gettempdir()
        frame_filename = os.path.join(temp_dir, f"extracted_frame_{random.randint(0,999999)}.png")
        cv2.imwrite(frame_filename, frame)
        cap.release()

        new_shot = copy.deepcopy(shot)
        new_shot["name"] = f"{shot['name']} Extended"
        new_shot["videoPath"] = ""
        new_shot["videoVersions"] = []
        new_shot["currentVideoVersion"] = -1
        new_shot["stillPath"] = frame_filename
        new_shot.setdefault("imageVersions", []).append(frame_filename)
        new_shot["currentImageVersion"] = len(new_shot["imageVersions"]) - 1
        new_shot["lastStillSignature"] = self.computeRenderSignature(new_shot, isVideo=False)

        self.shots.insert(shotIndex + 1, new_shot)
        self.updateList()
    def computeRenderSignature(self, shot, isVideo=False):
        """
        Incorporates shotParams, plus imageParams or videoParams accordingly,
        plus shot['params'] that are relevant to image or video,
        plus the relevant global params. Also includes the actual shot’s
        rendered stillPath if 'useShotImage' is True on an image param,
        so that changing the shot's upstream image can invalidate the signature.
        """
        import hashlib

        relevantShotParams = []
        # shotParams => "misc"
        for p in shot.get("shotParams", []):
            relevantShotParams.append({
                "name": p["name"],
                "type": p.get("type","string"),
                "useShotImage": p.get("useShotImage", False),
                "nodeIDs": p.get("nodeIDs", []),
                "value": p["value"]
            })

        # include either imageParams or videoParams
        if not isVideo:
            for p in shot.get("imageParams", []):
                # If useShotImage == True, incorporate shot["stillPath"] in the signature
                param_value = (
                    shot.get("stillPath", "") if p.get("useShotImage") else p.get("value", "")
                )
                relevantShotParams.append({
                    "name": p["name"],
                    "type": p.get("type","string"),
                    "useShotImage": p.get("useShotImage", False),
                    "nodeIDs": p.get("nodeIDs", []),
                    "value": param_value
                })
        else:
            for p in shot.get("videoParams", []):
                # If useShotImage == True, incorporate shot["stillPath"] in the signature
                param_value = (
                    shot.get("stillPath", "") if p.get("useShotImage") else p.get("value", "")
                )
                relevantShotParams.append({
                    "name": p["name"],
                    "type": p.get("type","string"),
                    "useShotImage": p.get("useShotImage", False),
                    "nodeIDs": p.get("nodeIDs", []),
                    "value": param_value
                })

        # shot["params"], respecting usage
        for p in shot["params"]:
            usage = p.get("usage", "both").lower()
            ptype = p.get("type", "string")
            if usage not in ("both", "image", "video"):
                continue
            if (isVideo and usage in ("both", "video")) or ((not isVideo) and usage in ("both", "image")):
                relevantShotParams.append({
                    "name": p["name"],
                    "type": ptype,
                    "useShotImage": p.get("useShotImage", False),
                    "nodeIDs": p.get("nodeIDs", []),
                    "value": (
                        shot.get("stillPath", "") if p.get("useShotImage") else p.get("value", "")
                    )
                })

        # global
        relevantGlobalParams = []
        if isVideo:
            for gp in self.globalVideoParams:
                param_value = gp["value"]
                # If for some reason you want to also incorporate shot's stillPath if gp says useShotImage...
                if gp.get("useShotImage"):
                    param_value = shot.get("stillPath", "")
                relevantGlobalParams.append({
                    "name": gp["name"],
                    "type": gp.get("type","string"),
                    "useShotImage": gp.get("useShotImage", False),
                    "nodeIDs": gp.get("nodeIDs", []),
                    "value": param_value
                })
        else:
            for gp in self.globalImageParams:
                param_value = gp["value"]
                if gp.get("useShotImage"):
                    param_value = shot.get("stillPath", "")
                relevantGlobalParams.append({
                    "name": gp["name"],
                    "type": gp.get("type","string"),
                    "useShotImage": gp.get("useShotImage", False),
                    "nodeIDs": gp.get("nodeIDs", []),
                    "value": param_value
                })

        data_struct = {
            "shotParams": sorted(relevantShotParams, key=lambda x: x["name"]),
            "globalParams": sorted(relevantGlobalParams, key=lambda x: x["name"])
        }
        signature_str = json.dumps(data_struct, sort_keys=True)
        return hashlib.md5(signature_str.encode("utf-8")).hexdigest()

    def queueShotRender(self, shotIndex, isVideo=False):
        self.renderQueue.append((shotIndex, isVideo))

    def startNextRender(self):
        if self.last_prompt_id:
            return
        if not self.renderQueue:
            return

        shotIndex, isVideo = self.renderQueue.pop(0)
        if shotIndex < 0 or shotIndex >= len(self.shots):
            return
        shot = self.shots[shotIndex]

        if isVideo:
            wf_path = self.videoWorkflowCombo.currentData()
            if not wf_path:
                QMessageBox.information(self, "Info", "No workflow selected for Video. Aborting.")
                return
            self.renderWithWorkflow(wf_path, shot, isVideo=True, queueMode=True, shotIndex=shotIndex)
        else:
            wf_path = self.stillWorkflowCombo.currentData()
            if not wf_path:
                QMessageBox.information(self, "Info", "No workflow selected for Still. Aborting.")
                return
            self.renderWithWorkflow(wf_path, shot, isVideo=False, queueMode=True, shotIndex=shotIndex)

    def renderWithWorkflow(self, workflow_path, shot, isVideo=False, queueMode=False, shotIndex=None):
        try:
            with open(workflow_path, "r") as f:
                workflow_json = json.load(f)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load workflow: {e}")
            return

        local_params = []
        local_params.extend(shot["shotParams"])
        if not isVideo:
            local_params.extend(shot["imageParams"])
        else:
            local_params.extend(shot["videoParams"])
        local_params.extend(shot["params"])

        global_params = self.globalVideoParams if isVideo else self.globalImageParams
        debug_info = []

        targetShotIndex = shotIndex if queueMode and shotIndex is not None else self.currentShotIndex
        the_shot = self.shots[targetShotIndex]

        self.statusMessage.setText(f"Rendering {shot.get('name', 'Unnamed')} - {'Video' if isVideo else 'Image'} ...")

        for node_id, node_data in workflow_json.items():
            inputs_dict = node_data.get("inputs", {})
            meta_title = node_data.get("_meta", {}).get("title", "").lower()

            # Local shot-level
            for input_key in list(inputs_dict.keys()):
                ikey_lower = str(input_key).lower()
                for param in local_params:
                    pNameLower = param["name"].lower()
                    pType = param["type"]
                    pValue = param["value"]
                    nodeIDs = param.get("nodeIDs", [])

                    if nodeIDs and str(node_id) not in nodeIDs:
                        continue

                    if pNameLower == ikey_lower:
                        if pType == "image" and param.get("useShotImage"):
                            val_to_set = the_shot.get("stillPath") or pValue
                            inputs_dict[input_key] = val_to_set
                            debug_info.append(
                                f"[SHOT] Node {node_id} input '{input_key}' (image) -> '{val_to_set}'"
                            )
                        else:
                            inputs_dict[input_key] = pValue
                            debug_info.append(
                                f"[SHOT] Node {node_id} input '{input_key}' -> '{pValue}'"
                            )

                if "positive prompt" in meta_title and input_key == "text":
                    for param in local_params:
                        if param["name"].lower() == "positive prompt":
                            nodeIDs = param.get("nodeIDs", [])
                            if nodeIDs and str(node_id) not in nodeIDs:
                                continue
                            inputs_dict["text"] = param["value"]
                            debug_info.append(
                                f"[SHOT] Node {node_id} 'text' overridden by Positive Prompt = '{param['value']}'"
                            )

            # Global
            for input_key in list(inputs_dict.keys()):
                ikey_lower = str(input_key).lower()
                for param in global_params:
                    pNameLower = param["name"].lower()
                    pValue = param["value"]
                    nodeIDs = param.get("nodeIDs", [])
                    if nodeIDs and str(node_id) not in nodeIDs:
                        continue
                    if pNameLower == ikey_lower:
                        inputs_dict[input_key] = pValue
                        debug_info.append(
                            f"[GLOBAL] Node {node_id} input '{input_key}' -> '{pValue}'"
                        )

        print("=== Debug Param Setting ===")
        for line in debug_info:
            print(line)

        comfy_ip = self.settingsManager.get("comfy_ip", "http://localhost:8188").rstrip("/")
        url = f"{comfy_ip}/prompt"
        headers = {"Content-Type": "application/json"}
        data = {"prompt": workflow_json}

        try:
            resp = requests.post(url, headers=headers, json=data)
            resp.raise_for_status()
            result = resp.json()
            self.last_prompt_id = result.get("prompt_id")

            if self.last_prompt_id:
                self.activePrompts[self.last_prompt_id] = (targetShotIndex, isVideo)
                self.result_timer.start()
            else:
                QMessageBox.information(self, "Info", "No prompt_id returned from ComfyUI.")
                if queueMode:
                    self.startNextRender()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Request to ComfyUI failed: {e}")
            if queueMode:
                self.startNextRender()

    def checkComfyResult(self):
        if not self.last_prompt_id:
            return
        comfy_ip = self.settingsManager.get("comfy_ip", "http://localhost:8188").rstrip("/")
        url = f"{comfy_ip}/history/{self.last_prompt_id}"
        try:
            response = requests.get(url)
            if response.status_code == 200:
                result_data = response.json()
                if result_data:
                    self.result_timer.stop()
                    self.handleComfyResult(result_data)
            elif response.status_code == 404:
                pass
        except:
            pass

    def handleComfyResult(self, result_data):
        if self.last_prompt_id not in self.activePrompts:
            return
        shotIndex, isVideo = self.activePrompts[self.last_prompt_id]

        prompt_result = result_data.get(self.last_prompt_id, {})
        outputs = prompt_result.get("outputs", {})
        if not outputs:
            del self.activePrompts[self.last_prompt_id]
            self.last_prompt_id = None
            self.startNextRender()
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
            if self.currentFilePath:
                project_folder = os.path.dirname(self.currentFilePath)
            else:
                dlg = QFileDialog(self, "Select a folder to store shot versions")
                dlg.setFileMode(QFileDialog.FileMode.Directory)
                if dlg.exec() == QDialog.DialogCode.Accepted:
                    project_folder = dlg.selectedFiles()[0]
                    if not self.currentFilePath:
                        self.currentFilePath = os.path.join(project_folder, "untitled.json")
                else:
                    project_folder = tempfile.gettempdir()

            local_path = self.downloadComfyFile(final_path)
            if local_path:
                ext = os.path.splitext(local_path)[1]
                new_name = f"{'video' if final_is_video else 'image'}_{random.randint(0, 999999)}{ext}"
                new_full = os.path.join(project_folder, new_name)
                try:
                    with open(local_path, "rb") as src, open(new_full, "wb") as dst:
                        dst.write(src.read())
                except Exception as e:
                    new_full = local_path

                shot = self.shots[shotIndex]
                if final_is_video or isVideo:
                    shot["videoPath"] = new_full
                    shot["videoVersions"].append(new_full)
                    shot["currentVideoVersion"] = len(shot["videoVersions"]) - 1
                    shot["lastVideoSignature"] = self.computeRenderSignature(shot, isVideo=True)
                else:
                    shot["stillPath"] = new_full
                    shot["imageVersions"].append(new_full)
                    shot["currentImageVersion"] = len(shot["imageVersions"]) - 1
                    shot["lastStillSignature"] = self.computeRenderSignature(shot, isVideo=False)

                self.updateList()

        del self.activePrompts[self.last_prompt_id]
        self.last_prompt_id = None
        self.statusMessage.setText("Ready")
        self.startNextRender()

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

    def showSettingsDialog(self):
        dialog = SettingsDialog(self.settingsManager, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            pass

    def closeEvent(self, event):
        if len(self.shots) > 0:
            reply = QMessageBox.question(
                self,
                "Save Project?",
                "Do you want to save the project before exiting?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.saveProject()
                self.settingsManager.set("global_image_params", self.globalImageParams)
                self.settingsManager.set("global_video_params", self.globalVideoParams)
                self.settingsManager.set("default_shot_params", self.defaultShotParams)
                self.settingsManager.set("default_image_params", self.defaultImageParams)
                self.settingsManager.set("default_video_params", self.defaultVideoParams)
                self.settingsManager.save()
                event.accept()
            elif reply == QMessageBox.StandardButton.No:
                self.settingsManager.set("global_image_params", self.globalImageParams)
                self.settingsManager.set("global_video_params", self.globalVideoParams)
                self.settingsManager.set("default_shot_params", self.defaultShotParams)
                self.settingsManager.set("default_image_params", self.defaultImageParams)
                self.settingsManager.set("default_video_params", self.defaultVideoParams)
                self.settingsManager.save()
                event.accept()
            else:
                event.ignore()
        else:
            self.settingsManager.set("global_image_params", self.globalImageParams)
            self.settingsManager.set("global_video_params", self.globalVideoParams)
            self.settingsManager.set("default_shot_params", self.defaultShotParams)
            self.settingsManager.set("default_image_params", self.defaultImageParams)
            self.settingsManager.set("default_video_params", self.defaultVideoParams)
            self.settingsManager.save()
            event.accept()

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
