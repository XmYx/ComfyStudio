#!/usr/bin/env python
import copy
import json
import os
import random
import sys
import tempfile
import urllib

import requests
from qtpy.QtCore import (
    Qt,
    QUrl,
    QSize,
    QTimer,
    QPoint
)
from qtpy.QtGui import (
    QAction,
    QPixmap
)
from qtpy.QtMultimedia import QMediaPlayer, QAudioOutput
from qtpy.QtMultimediaWidgets import QVideoWidget
from qtpy.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QListWidgetItem,
    QLineEdit,
    QSpinBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QDockWidget,
    QMenuBar,
    QMenu,
    QPushButton,
    QLabel,
    QDialog,
    QComboBox,
    QMessageBox,
    QCheckBox,
    QTabWidget,
    QAbstractItemView
)

from comfystudio.sdmodules.settings import SettingsManager, SettingsDialog
from comfystudio.sdmodules.shot_manager import ShotManager
from comfystudio.sdmodules.widgets import ReorderableListWidget


class MainWindow(QMainWindow, ShotManager):
    def __init__(self):
        # super().__init__()
        QMainWindow.__init__(self)
        ShotManager.__init__(self)
        self.setWindowTitle("Cinema Shot Designer")
        self.resize(1200, 800)
        self.settingsManager = SettingsManager()
        self.globalImageParams = []
        self.globalVideoParams = []
        self.defaultShotParams = []
        self.defaultImageParams = []
        self.defaultVideoParams = self.settingsManager.get("default_video_params", [
            {
                "type": "image",
                "name": "Image",
                "value": "",
                "useShotImage": True,
                "nodeIDs": ["1"]
            },
        ])
        self.currentFilePath = None

        self.last_prompt_id = None


        # self.listWidget.model().rowsMoved.connect(self.onShotsReordered)
        self.renderQueue = []
        self.activePrompts = {}
        self.result_timer = QTimer()
        self.result_timer.setInterval(2000)
        self.result_timer.timeout.connect(self.checkComfyResult)
        self.image_workflows = []
        self.video_workflows = []
        self.current_image_workflow = None
        self.current_video_workflow = None
        self.initUI()
        self.loadPlugins()
        self.loadWorkflows()
        self.updateList()

    def initUI(self):
        central = QWidget()
        self.setCentralWidget(central)
        self.mainLayout = QVBoxLayout(central)

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
        self.listWidget.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.listWidget.itemSelectionChanged.connect(self.onSelectionChanged)
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

        # Connect workflow combo box changes
        self.stillWorkflowCombo.currentIndexChanged.connect(self.onStillWorkflowChanged)
        self.videoWorkflowCombo.currentIndexChanged.connect(self.onVideoWorkflowChanged)

    def onShotsReordered(self, parent, start, end, destination, row):
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
            shot = item.data(Qt.ItemDataRole.UserRole)
            # Skip the 'Add New Shot' item
            if shot is None:
                continue
            new_order.append(shot)
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
        addShotBtn = QAction("Add New Shot", self)  # New button
        toolbar.addAction(addShotBtn)  # Add to toolbar
        toolbar.addAction(renderAllStillsBtn)
        toolbar.addAction(renderAllVideosBtn)
        toolbar.addAction(stopRenderingBtn)
        self.startComfyBtn = QAction("Start Comfy", self)
        self.stopComfyBtn = QAction("Stop Comfy", self)
        toolbar.addAction(self.startComfyBtn)
        toolbar.addAction(self.stopComfyBtn)
        addShotBtn.triggered.connect(self.addShot)  # Connect to addShot method
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

    def updateList(self):
        self.listWidget.clear()
        for i, shot in enumerate(self.shots):
            icon = self.getShotIcon(shot)
            label_text = f"Shot {i + 1}"
            item = QListWidgetItem(icon, label_text)
            item.setData(Qt.ItemDataRole.UserRole, i)  # Store the index
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            self.listWidget.addItem(item)

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
            w.valueChanged.connect(lambda v, p=param: self.onGlobalParamChanged(p, v, isVideo))
        elif ptype == "float":
            w = QDoubleSpinBox()
            w.setRange(0.0, 2147483647.0)
            w.setDecimals(3)
            w.setValue(pval)
            w.valueChanged.connect(lambda v, p=param: self.onGlobalParamChanged(p, v, isVideo))
        else:
            w = QLineEdit(str(pval))
            w.textChanged.connect(lambda val, p=param: self.onGlobalParamChanged(p, val, isVideo))
        removeBtn = QPushButton("Remove")
        removeBtn.clicked.connect(lambda _, p=param, vid=isVideo: self.removeGlobalParam(p, vid))
        layout.addWidget(w)
        layout.addWidget(removeBtn)
        return container

    def createParamWidgetWithRemove(self, param, isVideo, isShotLevel, misc=False):
        container = QWidget()
        layout = QHBoxLayout(container)
        if param.get("type", "string") == "image":
            w = self.createImageParamWidget(param)
        elif param.get("type", "string") == "video":
            w = self.createVideoParamWidget(param)
        else:
            w = self.createBasicParamWidget(param)
        removeBtn = QPushButton("Remove")
        removeBtn.clicked.connect(lambda _, p=param, isShotLevel=isShotLevel, misc=misc, isVideo=isVideo: self.removeShotParam(p, isShotLevel, isVideo, misc))
        layout.addWidget(w)
        layout.addWidget(removeBtn)
        return container

    def removeGlobalParam(self, param, isVideo):
        if isVideo:
            self.globalVideoParams.remove(param)
            self.refreshGlobalVideoParams()
        else:
            self.globalImageParams.remove(param)
            self.refreshGlobalImageParams()
        self.saveCurrentWorkflowParams(isVideo)

    def removeShotParam(self, param, isShotLevel, isVideo, misc):
        if self.currentShotIndex is None or self.currentShotIndex < 0:
            return
        shot = self.shots[self.currentShotIndex]
        if isShotLevel:
            if misc:
                if param in shot["shotParams"]:
                    shot["shotParams"].remove(param)
                elif param in shot["params"]:
                    shot["params"].remove(param)
            else:
                if isVideo:
                    shot["videoParams"].remove(param)
                else:
                    shot["imageParams"].remove(param)
        self.fillDock()

    def addGlobalParam(self, nodeID, paramName, paramType, paramValue, isVideo=False):
        """Creates a new param dictionary and appends it to either globalVideoParams or globalImageParams."""
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
            self.saveCurrentWorkflowParams(isVideo=True)
        else:
            self.globalImageParams.append(new_param)
            self.refreshGlobalImageParams()
            QMessageBox.information(
                self,
                "Global Param Added",
                f"Param '{paramName}' (type '{paramType}') added to Global Image Params."
            )
            self.saveCurrentWorkflowParams(isVideo=False)

    def addShotParam(self, nodeID, paramName, paramType, paramValue, isVideo=False):
        """Add shot parameter and store it in defaults."""
        new_param = {
            "type": paramType,
            "name": paramName,
            "value": paramValue,
            "nodeIDs": [str(nodeID)]
        }
        if isVideo:
            self.defaultVideoParams.append(new_param)
            self.saveCurrentWorkflowParams(isVideo=True)
        else:
            self.defaultImageParams.append(new_param)
            self.saveCurrentWorkflowParams(isVideo=False)
        if self.currentShotIndex is None or self.currentShotIndex < 0 or self.currentShotIndex >= len(self.shots):
            QMessageBox.information(
                self,
                "Info",
                f"No active shot. Param '{paramName}' saved to {'video' if isVideo else 'image'} defaults."
            )
            return
        shot = self.shots[self.currentShotIndex]
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

    def loadWorkflowParams(self, workflow_path, isVideo):
        workflow_params = self.settingsManager.get("workflow_params", {})
        workflow_data = workflow_params.get(workflow_path, {})
        if isVideo:
            self.globalVideoParams = copy.deepcopy(workflow_data.get("global_video_params", []))
            self.defaultVideoParams = copy.deepcopy(workflow_data.get("default_video_params", []))
            if not self.defaultVideoParams:
                self.defaultVideoParams = [
                    {
                        "type": "image",
                        "name": "Image",
                        "value": "",
                        "useShotImage": True,
                        "nodeIDs": ["1"]
                    },
                ]
            self.refreshGlobalVideoParams()
            self.updateShotsParams(isVideo=True)
        else:
            self.globalImageParams = copy.deepcopy(workflow_data.get("global_image_params", []))
            self.defaultImageParams = copy.deepcopy(workflow_data.get("default_image_params", []))
            self.refreshGlobalImageParams()
            self.updateShotsParams(isVideo=False)

    def updateShotsParams(self, isVideo):
        for shot in self.shots:
            if isVideo:
                shot["videoParams"] = copy.deepcopy(self.defaultVideoParams)
            else:
                shot["imageParams"] = copy.deepcopy(self.defaultImageParams)
        self.updateList()
        self.fillDock()

    def saveCurrentWorkflowParams(self, isVideo):
        if isVideo:
            workflow_path = self.current_video_workflow
        else:
            workflow_path = self.current_image_workflow
        if not workflow_path:
            return
        workflow_params = self.settingsManager.get("workflow_params", {})
        workflow_data = workflow_params.get(workflow_path, {})
        if isVideo:
            workflow_data["global_video_params"] = self.globalVideoParams
            workflow_data["default_video_params"] = self.defaultVideoParams
        else:
            workflow_data["global_image_params"] = self.globalImageParams
            workflow_data["default_image_params"] = self.defaultImageParams
        workflow_params[workflow_path] = workflow_data
        self.settingsManager.set("workflow_params", workflow_params)
        self.settingsManager.save()

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
        """Compute signature for render parameters."""
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

    def onParamChanged(self, paramDict, newVal):
        paramDict["value"] = newVal
        self.saveCurrentWorkflowParams(isVideo=False)

    def onItemClicked(self, item):
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx == -1:
            self.addShot()
        else:
            self.currentShotIndex = idx
            self.fillDock()

    def onListWidgetContextMenu(self, pos: QPoint):
        selected_items = self.listWidget.selectedItems()
        if not selected_items:
            return

        # Collect valid shot indices (exclude 'Add New Shot' and invalid items)
        valid_indices = []
        for item in selected_items:
            index = item.data(Qt.ItemDataRole.UserRole)
            if index is None or not isinstance(index, int) or index < 0 or index >= len(self.shots):
                continue  # Skip invalid items
            valid_indices.append(index)

        if not valid_indices:
            return

        menu = QMenu(self)
        deleteAction = menu.addAction("Delete Shot(s)")
        duplicateAction = menu.addAction("Duplicate Shot(s)")
        extendAction = menu.addAction("Extend Clip(s)")
        action = menu.exec(self.listWidget.mapToGlobal(pos))

        if action == deleteAction:
            # Confirm deletion
            reply = QMessageBox.question(
                self,
                "Delete Shot(s)",
                "Are you sure you want to delete the selected shots?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                # Delete shots in reverse order to prevent index shifting
                for idx in sorted(valid_indices, reverse=True):
                    del self.shots[idx]
                self.currentShotIndex = None
                self.updateList()
                self.clearDock()
        elif action == duplicateAction:
            # Duplicate shots
            # To preserve order, sort indices
            for idx in sorted(valid_indices):
                shot = self.shots[idx]
                new_shot = copy.deepcopy(shot)
                new_shot["name"] = f"{shot['name']} (Copy)"
                # Clear out any final paths/versions if needed
                new_shot["stillPath"] = ""
                new_shot["videoPath"] = ""
                new_shot["imageVersions"] = []
                new_shot["videoVersions"] = []
                new_shot["currentImageVersion"] = -1
                new_shot["currentVideoVersion"] = -1
                self.shots.insert(idx + 1, new_shot)  # Insert after the original
            self.updateList()
        elif action == extendAction:
            # Extend clips
            for idx in sorted(valid_indices):
                self.extendClip(idx)
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

    def onSelectionChanged(self):
        selected_items = self.listWidget.selectedItems()
        if len(selected_items) == 1:
            idx = selected_items[0].data(Qt.ItemDataRole.UserRole)
            if idx != -1:
                self.currentShotIndex = idx
                self.fillDock()
            else:
                self.currentShotIndex = None
                self.clearDock()
        else:
            self.currentShotIndex = None
            self.clearDock()

    def onGlobalParamChanged(self, param, newVal, isVideo):
        param["value"] = newVal
        self.saveCurrentWorkflowParams(isVideo)

    def onStillWorkflowChanged(self, index):
        workflow_path = self.stillWorkflowCombo.currentData()
        if not workflow_path:
            return
        self.current_image_workflow = workflow_path
        self.loadWorkflowParams(workflow_path, isVideo=False)

    def onVideoWorkflowChanged(self, index):
        workflow_path = self.videoWorkflowCombo.currentData()
        if not workflow_path:
            return
        self.current_video_workflow = workflow_path
        self.loadWorkflowParams(workflow_path, isVideo=True)

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
        """Clear the render queue and enqueue shots for rendering."""
        self.renderQueue.clear()
        for i, shot in enumerate(self.shots):
            new_signature = self.computeRenderSignature(shot, isVideo=False)
            last_sig = shot.get("lastStillSignature", "")
            still_path = shot.get("stillPath", "")
            if not still_path or (new_signature != last_sig):
                self.queueShotRender(i, isVideo=False)
        self.startNextRender()

    def onGenerateAllVideos(self):
        """Clear the render queue and enqueue shots for rendering."""
        self.renderQueue.clear()
        for i, shot in enumerate(self.shots):
            new_signature = self.computeRenderSignature(shot, isVideo=True)
            last_sig = shot.get("lastVideoSignature", "")
            video_path = shot.get("videoPath", "")
            if not video_path or (new_signature != last_sig):
                self.queueShotRender(i, isVideo=True)
        self.startNextRender()

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
                self.settingsManager.save()
                event.accept()
            elif reply == QMessageBox.StandardButton.No:
                self.settingsManager.save()
                event.accept()
            else:
                event.ignore()
        else:
            self.settingsManager.save()
            event.accept()

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()