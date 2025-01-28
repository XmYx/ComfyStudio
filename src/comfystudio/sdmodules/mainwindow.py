#!/usr/bin/env python
import copy
import json
import logging
import os
import random
import subprocess
import sys
import tempfile
import threading
import urllib
from typing import List, Dict

import requests
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QTextEdit,
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
    QPushButton,
    QLabel,
    QDialog,
    QComboBox,
    QMessageBox,
    QCheckBox,
    QTabWidget,
    QAbstractItemView,
    QListWidget,
    QGroupBox,
    QScrollArea,
    QInputDialog,
    QMenu, QFrame, QApplication
)
from qtpy.QtCore import (
    Qt,
    QPoint,
    QObject,
    Signal,
    Slot
)
from qtpy.QtGui import (
    QAction
)
from qtpy.QtMultimedia import QMediaPlayer, QAudioOutput
from qtpy.QtMultimediaWidgets import QVideoWidget

from comfystudio.sdmodules.dataclasses import Shot, WorkflowAssignment
from comfystudio.sdmodules.localization import LocalizationManager
from comfystudio.sdmodules.node_visualizer import WorkflowVisualizer
from comfystudio.sdmodules.preview_dock import ShotPreviewDock
from comfystudio.sdmodules.settings import SettingsManager, SettingsDialog
from comfystudio.sdmodules.shot_manager import ShotManager
from comfystudio.sdmodules.videotools import extract_frame
from comfystudio.sdmodules.widgets import ReorderableListWidget
from comfystudio.sdmodules.worker import RenderWorker


class EmittingStream(QObject):
    text_written = Signal(str)

    def write(self, text):
        self.text_written.emit(str(text))

    def flush(self):
        pass


class QtLogHandler(logging.Handler):
    def __init__(self, emit_stream):
        super().__init__()
        self.emit_stream = emit_stream

    def emit(self, record):
        log_entry = self.format(record)
        self.emit_stream.write(log_entry + '\n')


class MainWindow(QMainWindow, ShotManager):

    shotSelected = Signal(int)
    workflowSelected = Signal(int, int)
    shotRenderComplete = Signal(int, int, str, bool)

    def __init__(self):
        QMainWindow.__init__(self)
        ShotManager.__init__(self)
        self.resize(1400, 900)

        self.settingsManager = SettingsManager()
        self.localization = LocalizationManager(self.settingsManager)

        self.setWindowTitle(self.localization.translate("app_title", default="Cinema Shot Designer"))

        self.shots: List[Shot] = []
        self.currentShotIndex: int = -1

        self.renderQueue = []  # We'll store shotIndices to render
        self.activeWorker = None  # The QThread worker checking results

        # For progressive workflow rendering
        self.workflowQueue = {}   # Maps shotIndex -> list of (workflowIndex) to process
        self.shotInProgress = -1  # The shot we are currently processing
        self.workflowIndexInProgress = -1  # Current workflow index in that shot

        self.logStream = EmittingStream()
        self.logStream.text_written.connect(self.appendLog)

        self.showHiddenParams = False  # Toggles display of hidden parameters

        self.initUI()
        self.setupLogging()
        self.loadWorkflows()
        self.updateList()

        self.loadPlugins()


    def setupLogging(self):
        log_handler = QtLogHandler(self.logStream)
        log_handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        log_handler.setFormatter(formatter)
        logging.getLogger().addHandler(log_handler)
        logging.getLogger().setLevel(logging.DEBUG)

    def initUI(self):
        central = QWidget()
        self.setCentralWidget(central)
        self.mainLayout = QVBoxLayout(central)

        # Shots list
        self.listWidgetBase = ReorderableListWidget(self)
        self.listWidgetBase.updateTexts()
        self.listWidget = self.listWidgetBase.listWidget
        self.listWidget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.listWidget.itemClicked.connect(self.onItemClicked)
        self.listWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.listWidget.customContextMenuRequested.connect(self.onListWidgetContextMenu)
        self.listWidget.itemSelectionChanged.connect(self.onSelectionChanged)
        self.mainLayout.addWidget(self.listWidgetBase)

        # Dock for shot parameters
        self.dock = QDockWidget(self.localization.translate("shot_details"), self)
        self.dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
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

        # Video preview
        self.videoWidget = QVideoWidget()
        self.dockLayout.addWidget(self.videoWidget)

        self.player = QMediaPlayer()
        self.audioOutput = QAudioOutput()
        self.player.setAudioOutput(self.audioOutput)
        self.player.setVideoOutput(self.videoWidget)


        self.dock.setWidget(self.dockContents)

        self.previewDock = ShotPreviewDock(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.previewDock)

        self.createMenuBar()
        self.createToolBar()
        self.createStatusBar()

        self.shotSelected.connect(self.previewDock.onShotSelected)
        self.workflowSelected.connect(self.previewDock.onWorkflowSelected)
        self.shotRenderComplete.connect(self.previewDock.onShotRenderComplete)

    def initWorkflowsTab(self):
        layout = self.workflowsLayout

        # Comboboxes for adding image/video workflows
        comboLayout = QHBoxLayout()

        # Image Workflow Section
        self.imageWorkflowLabel = QLabel(self.localization.translate("label_image_workflow", default="Image Workflow:"))
        self.imageWorkflowCombo = QComboBox()
        self.imageWorkflowCombo.setToolTip(
            self.localization.translate("tooltip_select_image_workflow", default="Select an Image Workflow to add"))
        self.addImageWorkflowBtn = QPushButton(
            self.localization.translate("button_add_image_workflow", default="Add Image Workflow"))
        self.addImageWorkflowBtn.setToolTip(self.localization.translate("tooltip_add_image_workflow",
                                                                        default="Add the selected Image Workflow to the shot"))

        # Connect Image Workflow Button
        self.addImageWorkflowBtn.clicked.connect(self.addImageWorkflow)

        # Video Workflow Section
        self.videoWorkflowLabel = QLabel(self.localization.translate("label_video_workflow", default="Video Workflow:"))
        self.videoWorkflowCombo = QComboBox()
        self.videoWorkflowCombo.setToolTip(
            self.localization.translate("tooltip_select_video_workflow", default="Select a Video Workflow to add"))
        self.addVideoWorkflowBtn = QPushButton(
            self.localization.translate("button_add_video_workflow", default="Add Video Workflow"))
        self.addVideoWorkflowBtn.setToolTip(self.localization.translate("tooltip_add_video_workflow",
                                                                        default="Add the selected Video Workflow to the shot"))

        # Connect Video Workflow Button
        self.addVideoWorkflowBtn.clicked.connect(self.addVideoWorkflow)

        # Assemble Combo Layout
        comboLayout.addWidget(self.imageWorkflowLabel)
        comboLayout.addWidget(self.imageWorkflowCombo)
        comboLayout.addWidget(self.addImageWorkflowBtn)
        comboLayout.addSpacing(20)  # Optional: Add spacing between sections
        comboLayout.addWidget(self.videoWorkflowLabel)
        comboLayout.addWidget(self.videoWorkflowCombo)
        comboLayout.addWidget(self.addVideoWorkflowBtn)

        layout.addLayout(comboLayout)

        # Workflow List
        self.workflowListLabel = QLabel(
            self.localization.translate("label_workflow_list", default="Available Workflows:"))
        layout.addWidget(self.workflowListLabel)

        self.workflowListWidget = QListWidget()
        self.workflowListWidget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.workflowListWidget.itemClicked.connect(self.onWorkflowItemClicked)
        self.workflowListWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.workflowListWidget.customContextMenuRequested.connect(self.onWorkflowListContextMenu)
        layout.addWidget(self.workflowListWidget)

        # Buttons to Remove Workflows
        buttonsLayout = QHBoxLayout()
        self.removeWorkflowBtn = QPushButton(
            self.localization.translate("button_remove_workflow", default="Remove Workflow"))
        self.removeWorkflowBtn.setToolTip(self.localization.translate("tooltip_remove_workflow",
                                                                      default="Remove the selected Workflow from the shot"))
        buttonsLayout.addWidget(self.removeWorkflowBtn)
        layout.addLayout(buttonsLayout)

        # Connect Remove Workflow Button
        self.removeWorkflowBtn.clicked.connect(self.removeWorkflowFromShot)

        # Toggle Hidden Parameters
        self.toggleHiddenParamsBtn = QPushButton(
            self.localization.translate("button_toggle_hidden_params", default="Show/Hide Hidden Params"))
        self.toggleHiddenParamsBtn.setToolTip(self.localization.translate("tooltip_toggle_hidden_params",
                                                                          default="Toggle the visibility of hidden parameters"))
        self.toggleHiddenParamsBtn.clicked.connect(self.toggleHiddenParams)
        layout.addWidget(self.toggleHiddenParamsBtn)

        # Parameters Area in a Scroll
        self.workflowParamsGroup = QGroupBox(
            self.localization.translate("group_workflow_parameters", default="Workflow Parameters"))
        self.workflowParamsLayout = QFormLayout(self.workflowParamsGroup)
        self.workflowParamsGroup.setLayout(self.workflowParamsLayout)
        self.workflowParamsGroup.setEnabled(False)

        self.workflowParamsScroll = QScrollArea()
        self.workflowParamsScroll.setWidgetResizable(True)
        self.workflowParamsScroll.setWidget(self.workflowParamsGroup)

        layout.addWidget(self.workflowParamsScroll)

    def onWorkflowListContextMenu(self, pos):
        item = self.workflowListWidget.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        enableAction = menu.addAction("Toggle Enabled")
        action = menu.exec(self.workflowListWidget.mapToGlobal(pos))
        if action == enableAction:
            workflow: WorkflowAssignment = item.data(Qt.ItemDataRole.UserRole)
            workflow.enabled = not workflow.enabled
            self.refreshWorkflowsList(self.shots[self.currentShotIndex])

    def initParamsTab(self):
        self.paramsScroll = QScrollArea()
        self.paramsScroll.setWidgetResizable(True)

        self.paramsContainer = QWidget()
        self.paramsContainerLayout = QFormLayout(self.paramsContainer)
        self.paramsScroll.setWidget(self.paramsContainer)

        self.paramsListWidget = QListWidget()
        self.paramsListWidget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.paramsListWidget.itemClicked.connect(self.onParamItemClicked)
        self.paramsListWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.paramsListWidget.customContextMenuRequested.connect(self.onParamContextMenu)

        self.paramsContainerLayout.addRow("Parameters:", self.paramsListWidget)

        paramsButtonsLayout = QHBoxLayout()
        self.addParamBtn = QPushButton("Add Param")
        self.removeParamBtn = QPushButton("Remove Param")
        paramsButtonsLayout.addWidget(self.addParamBtn)
        paramsButtonsLayout.addWidget(self.removeParamBtn)
        self.paramsContainerLayout.addRow(paramsButtonsLayout)
        self.addParamBtn.clicked.connect(self.addParamToShot)
        self.removeParamBtn.clicked.connect(self.removeParamFromShot)

        self.paramsLayout.addWidget(self.paramsScroll)

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

        menu = QMenu(self)
        if isinstance(data, tuple):
            paramItemType = data[0]
            # 'data' can be ("shot", param) or ("workflow", wf, param)
            if paramItemType in ["workflow", "shot"]:
                # We only show these menu items if the param is a string or something we can override
                param = data[-1] if paramItemType == "workflow" else data[1]
                paramType = param.get("type", "string")

                # Only do this for string-type or generally overrideable params
                if paramType == "string":
                    setPrevImage = menu.addAction("Set Param to Previous Workflow's Image")
                    setPrevVideo = menu.addAction("Set Param to Previous Workflow's Video")
                    clearDynOverride = menu.addAction("Clear Dynamic Override")

                    chosen = menu.exec(self.paramsListWidget.mapToGlobal(pos))
                    if chosen == setPrevImage:
                        # Flag it for dynamic override from the previous workflow's image
                        param["usePrevResultImage"] = True
                        param["usePrevResultVideo"] = False
                        param["value"] = "(Awaiting previous workflow image)"
                        QMessageBox.information(self, "Info",
                                                "This parameter is now flagged to use the previous workflow's image result."
                                                )
                    elif chosen == setPrevVideo:
                        # Flag it for dynamic override from the previous workflow's video
                        param["usePrevResultVideo"] = True
                        param["usePrevResultImage"] = False
                        param["value"] = "(Awaiting previous workflow video)"
                        QMessageBox.information(self, "Info",
                                                "This parameter is now flagged to use the previous workflow's video result."
                                                )
                    elif chosen == clearDynOverride:
                        # Clear any dynamic override flags
                        param.pop("usePrevResultImage", None)
                        param.pop("usePrevResultVideo", None)
                        QMessageBox.information(self, "Info", "Dynamic override cleared.")
                else:
                    # For non-string params, just show a no-op menu or skip
                    pass

    def createMenuBar(self):
        # Clear existing menu bar to prevent duplication
        self.menuBar().clear()

        # Initialize Menus
        self.fileMenu = QMenu(self)
        self.settingsMenu = QMenu(self)

        # Initialize Actions
        self.newAct = QAction(self)
        self.openAct = QAction(self)
        self.saveAct = QAction(self)
        self.saveAsAct = QAction(self)
        self.importAction = QAction(self)
        self.renderSelectedAct = QAction(self)
        self.renderAllAct = QAction(self)
        self.saveDefaultsAct = QAction(self)
        self.openSettingsAct = QAction(self)

        # Set initial texts
        self.updateMenuBarTexts()

        # Connect actions
        self.newAct.triggered.connect(self.newProject)
        self.openAct.triggered.connect(self.openProject)
        self.saveAct.triggered.connect(self.saveProject)
        self.saveAsAct.triggered.connect(self.saveProjectAs)
        self.importAction.triggered.connect(self.importShotsFromTxt)
        self.renderSelectedAct.triggered.connect(self.onRenderSelected)
        self.renderAllAct.triggered.connect(self.onRenderAll)
        self.saveDefaultsAct.triggered.connect(self.onSaveWorkflowDefaults)
        self.openSettingsAct.triggered.connect(self.showSettingsDialog)

        # Add actions to File Menu
        self.fileMenu.addAction(self.newAct)
        self.fileMenu.addAction(self.openAct)
        self.fileMenu.addAction(self.saveAct)
        self.fileMenu.addAction(self.saveAsAct)
        self.fileMenu.addAction(self.importAction)
        self.fileMenu.addSeparator()
        self.fileMenu.addAction(self.renderSelectedAct)
        self.fileMenu.addAction(self.renderAllAct)
        self.fileMenu.addAction(self.saveDefaultsAct)

        # Add actions to Settings Menu
        self.settingsMenu.addAction(self.openSettingsAct)

        # Add Menus to Menu Bar
        self.menuBar().addMenu(self.fileMenu)
        self.menuBar().addMenu(self.settingsMenu)

    def createToolBar(self):
        # Clear existing toolbar to prevent duplication
        # self.toolBar().clear()

        # Initialize Toolbar
        self.toolbar = self.addToolBar("Main Toolbar")

        # Initialize Actions
        self.addShotBtn = QAction(self)
        self.renderSelectedBtn = QAction(self)
        self.renderAllBtn = QAction(self)
        self.stopRenderingBtn = QAction(self)
        self.startComfyBtn = QAction(self)
        self.stopComfyBtn = QAction(self)

        # Set initial texts
        self.updateToolBarTexts()

        # Connect actions
        self.addShotBtn.triggered.connect(self.addShot)
        self.renderSelectedBtn.triggered.connect(self.onRenderSelected)
        self.renderAllBtn.triggered.connect(self.onRenderAll)
        self.stopRenderingBtn.triggered.connect(self.stopRendering)
        self.startComfyBtn.triggered.connect(self.startComfy)
        self.stopComfyBtn.triggered.connect(self.stopComfy)

        # Add actions to toolbar
        self.toolbar.addAction(self.addShotBtn)
        self.toolbar.addAction(self.renderSelectedBtn)
        self.toolbar.addAction(self.renderAllBtn)
        self.toolbar.addAction(self.stopRenderingBtn)
        self.toolbar.addAction(self.startComfyBtn)
        self.toolbar.addAction(self.stopComfyBtn)

    def createStatusBar(self):
        # Clear existing status bar to prevent duplication
        self.status = self.statusBar()
        self.status.clearMessage()

        # Initialize Widgets
        self.statusMessage = QLabel()
        self.logLabel = QLabel()
        self.terminalButton = QPushButton()
        self.terminalDock = QDockWidget()
        self.terminalTextEdit = QTextEdit()

        # Set initial texts
        self.updateStatusBarTexts()

        # Configure Widgets
        self.statusMessage.setText(self.localization.translate("status_ready", default="Ready"))
        self.logLabel.setText("")
        self.terminalButton.setText(self.localization.translate("button_terminal", default="Terminal"))

        # Add Widgets to Status Bar
        self.status.addPermanentWidget(self.statusMessage, 1)
        self.status.addPermanentWidget(self.logLabel)
        self.status.addPermanentWidget(self.terminalButton)

        # Connect Signals
        self.terminalButton.clicked.connect(self.toggleTerminalDock)

        # Configure Terminal Dock
        self.terminalDock.setWindowTitle(self.localization.translate("terminal_output", default="Terminal Output"))
        self.terminalDock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self.terminalTextEdit.setReadOnly(True)
        self.terminalDock.setWidget(self.terminalTextEdit)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.terminalDock)
        self.terminalDock.hide()

    def updateMenuBarTexts(self):
        # Update File Menu Title
        self.fileMenu.setTitle(self.localization.translate("menu_file", default="File"))

        # Update Settings Menu Title
        self.settingsMenu.setTitle(self.localization.translate("menu_settings", default="Settings"))

        # Update Actions Texts
        self.newAct.setText(self.localization.translate("menu_new_project", default="New Project"))
        self.openAct.setText(self.localization.translate("menu_open", default="Open"))
        self.saveAct.setText(self.localization.translate("menu_save", default="Save"))
        self.saveAsAct.setText(self.localization.translate("menu_save_as", default="Save As"))
        self.importAction.setText(self.localization.translate("menu_import_shots", default="Import Shots from TXT"))
        self.renderSelectedAct.setText(self.localization.translate("menu_render_selected", default="Render Selected"))
        self.renderAllAct.setText(self.localization.translate("menu_render_all", default="Render All"))
        self.saveDefaultsAct.setText(
            self.localization.translate("menu_save_defaults", default="Save Workflow Defaults"))
        self.openSettingsAct.setText(self.localization.translate("menu_open_settings", default="Open Settings"))

    def updateToolBarTexts(self):
        # Update Toolbar Name if needed (optional)
        # self.toolbar.setWindowTitle(self.localization.translate("toolbar_name", default="Main Toolbar"))

        # Update Actions Texts
        self.addShotBtn.setText(self.localization.translate("toolbar_add_shot", default="Add New Shot"))
        self.renderSelectedBtn.setText(self.localization.translate("menu_render_selected", default="Render Selected"))
        self.renderAllBtn.setText(self.localization.translate("menu_render_all", default="Render All"))
        self.stopRenderingBtn.setText(self.localization.translate("menu_render_stop", default="Stop Rendering"))
        self.startComfyBtn.setText(self.localization.translate("toolbar_start_comfy", default="Start Comfy"))
        self.stopComfyBtn.setText(self.localization.translate("toolbar_stop_comfy", default="Stop Comfy"))

    def updateStatusBarTexts(self):
        self.statusMessage.setText(self.localization.translate("status_ready", default="Ready"))
        self.terminalButton.setText(self.localization.translate("button_terminal", default="Terminal"))
        self.terminalButton.setToolTip(self.localization.translate("tooltip_terminal", default="Show/Hide Terminal"))
        self.terminalDock.setWindowTitle(self.localization.translate("terminal_output", default="Terminal Output"))

    def updateWorkflowsTabTexts(self):
        """
        Update all translatable texts in the Workflows Tab.
        Call this method within retranslateUi to refresh UI elements with the new language.
        """
        # Update Labels
        self.imageWorkflowLabel.setText(self.localization.translate("label_image_workflow", default="Image Workflow:"))
        self.videoWorkflowLabel.setText(self.localization.translate("label_video_workflow", default="Video Workflow:"))
        self.workflowListLabel.setText(
            self.localization.translate("label_workflow_list", default="Available Workflows:"))
        self.workflowParamsGroup.setTitle(
            self.localization.translate("group_workflow_parameters", default="Workflow Parameters"))

        # Update Buttons
        self.addImageWorkflowBtn.setText(
            self.localization.translate("button_add_image_workflow", default="Add Image Workflow"))
        self.addImageWorkflowBtn.setToolTip(self.localization.translate("tooltip_add_image_workflow",
                                                                        default="Add the selected Image Workflow to the shot"))

        self.addVideoWorkflowBtn.setText(
            self.localization.translate("button_add_video_workflow", default="Add Video Workflow"))
        self.addVideoWorkflowBtn.setToolTip(self.localization.translate("tooltip_add_video_workflow",
                                                                        default="Add the selected Video Workflow to the shot"))

        self.removeWorkflowBtn.setText(self.localization.translate("button_remove_workflow", default="Remove Workflow"))
        self.removeWorkflowBtn.setToolTip(self.localization.translate("tooltip_remove_workflow",
                                                                      default="Remove the selected Workflow from the shot"))

        self.toggleHiddenParamsBtn.setText(
            self.localization.translate("button_toggle_hidden_params", default="Show/Hide Hidden Params"))
        self.toggleHiddenParamsBtn.setToolTip(self.localization.translate("tooltip_toggle_hidden_params",
                                                                          default="Toggle the visibility of hidden parameters"))

        # Update Combobox Tooltips
        self.imageWorkflowCombo.setToolTip(
            self.localization.translate("tooltip_select_image_workflow", default="Select an Image Workflow to add"))
        self.videoWorkflowCombo.setToolTip(
            self.localization.translate("tooltip_select_video_workflow", default="Select a Video Workflow to add"))

    def appendLog(self, text):
        self.terminalTextEdit.append(text)
        self.logLabel.setText(text)

    def toggleTerminalDock(self):
        if self.terminalDock.isVisible():
            self.terminalDock.hide()
        else:
            self.terminalDock.show()

    def addShot(self):
        new_shot = Shot(name=f"Shot {len(self.shots) + 1}")
        self.shots.append(new_shot)
        self.updateList()
        self.currentShotIndex = len(self.shots) - 1
        self.listWidget.setCurrentRow(self.listWidget.count() - 1)
        self.fillDock()

    def updateList(self):
        self.listWidget.clear()
        for i, shot in enumerate(self.shots):
            icon = self.getShotIcon(shot)
            label_text = f"{shot.name}"
            item = QListWidgetItem(icon, label_text)
            item.setData(Qt.ItemDataRole.UserRole, i)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            self.listWidget.addItem(item)

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

        valid_indices = []
        for item in selected_items:
            index = item.data(Qt.ItemDataRole.UserRole)
            if index is None or not isinstance(index, int) or index < 0 or index >= len(self.shots):
                continue
            valid_indices.append(index)

        if not valid_indices:
            return

        menu = QMenu(self)
        deleteAction = menu.addAction("Delete Shot(s)")
        duplicateAction = menu.addAction("Duplicate Shot(s)")
        extendAction = menu.addAction("Extend Clip(s)")
        if len(selected_items) > 1:
            mergeAction = menu.addAction("Merge Clips")

        action = menu.exec(self.listWidget.mapToGlobal(pos))

        if action == deleteAction:
            reply = QMessageBox.question(
                self,
                "Delete Shot(s)",
                "Are you sure you want to delete the selected shots?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                for idx in sorted(valid_indices, reverse=True):
                    del self.shots[idx]
                self.currentShotIndex = -1
                self.updateList()
                self.clearDock()
        elif action == duplicateAction:
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
        elif action == extendAction:
            for idx in sorted(valid_indices):
                self.extendClip(idx)
        elif action and action.text() == "Merge Clips":
            self.mergeClips(valid_indices)

    def mergeClips(self, selected_indices):
        if len(selected_indices) < 2:
            QMessageBox.warning(self, "Warning", "Select at least two clips to merge.")
            return

        video_paths = []
        for idx in selected_indices:
            shot = self.shots[idx]
            video_path = shot.videoPath
            if not video_path or not os.path.exists(video_path):
                QMessageBox.warning(self, "Warning", f"Shot '{shot.name}' has no valid video path.")
                return
            video_paths.append(video_path)

        temp_file_list = tempfile.mktemp(suffix='.txt')
        with open(temp_file_list, 'w') as f:
            for path in video_paths:
                f.write(f"file '{path}'\n")

        if hasattr(self, 'currentFilePath') and self.currentFilePath:
            project_folder = os.path.dirname(self.currentFilePath)
        else:
            QMessageBox.warning(self, "Warning", "No project file is currently open. Merged video will be saved to the temporary directory.")
            project_folder = tempfile.gettempdir()

        merged_filename = f"merged_video_{random.randint(100000, 999999)}.mp4"
        output_path = os.path.join(project_folder, merged_filename)

        command = [
            'ffmpeg', '-f', 'concat', '-safe', '0',
            '-i', temp_file_list,
            '-c', 'copy', output_path
        ]

        try:
            subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as e:
            QMessageBox.warning(self, "Error", f"Failed to merge videos: {e.stderr.decode()}")
            os.remove(temp_file_list)
            return

        new_shot = copy.deepcopy(self.shots[selected_indices[-1]])
        new_shot.name = f"{new_shot.name} Merged"
        new_shot.videoPath = output_path
        new_shot.videoVersions = [output_path]
        new_shot.currentVideoVersion = 0
        new_shot.lastVideoSignature = self.computeRenderSignature(new_shot, isVideo=True)
        new_shot.workflows = []

        insert_idx = selected_indices[-1] + 1
        self.shots.insert(insert_idx, new_shot)

        # for idx in sorted(selected_indices, reverse=True):
        #     del self.shots[idx]

        self.updateList()
        self.currentShotIndex = insert_idx
        self.listWidget.setCurrentRow(insert_idx)
        self.fillDock()
        os.remove(temp_file_list)

    def onImageVersionChanged(self, shot: Shot, combo, idx):
        shot.currentImageVersion = idx
        new_path = combo.itemData(idx)
        shot.stillPath = new_path
        self.updateList()

    def onVideoVersionChanged(self, shot: Shot, combo, idx):
        try:
            if idx < 0 or idx >= combo.count():
                raise ValueError("Invalid video version index.")
            new_path = combo.itemData(idx)
            if not new_path or not os.path.exists(new_path):
                QMessageBox.warning(self, "Error", "Selected video file does not exist.")
                return
            # self.player.stop()
            # shot.currentVideoVersion = idx
            # shot.videoPath = new_path
            # self.player.setSource(QUrl.fromLocalFile(new_path))
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to change video version: {e}")

    def onSelectionChanged(self):
        try:
            self.player.stop()
        except:
            pass
        selected_items = self.listWidget.selectedItems()
        if len(selected_items) == 1:
            idx = selected_items[0].data(Qt.ItemDataRole.UserRole)
            if idx != -1:
                self.currentShotIndex = idx
                self.fillDock()
                self.shotSelected.emit(idx)
            else:
                self.currentShotIndex = -1
                self.clearDock()
        else:
            self.currentShotIndex = -1
            self.clearDock()

    def onRenderSelected(self):
        """
        Render only the currently selected shots.
        """
        selected_items = self.listWidget.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Warning", "No shot selected to render.")
            return

        # First stop any current rendering processes
        # but do NOT clear the queue again after we add new items
        self.stopRendering()

        # Now set up a new queue from selected shots
        for it in selected_items:
            idx = it.data(Qt.ItemDataRole.UserRole)
            if idx is not None and isinstance(idx, int) and 0 <= idx < len(self.shots):
                self.renderQueue.append(idx)

        # Start rendering the new queue
        self.startNextRender()

    def onRenderAll(self):
        """
        Render all shots by adding any shots not currently in the queue.
        If not already rendering, start the rendering process.
        """
        # Add all shots not already in the queue and not currently being rendered
        for i in range(len(self.shots)):
            if i not in self.renderQueue and i != self.shotInProgress:
                self.renderQueue.append(i)

        # Start rendering if not already in progress
        if self.shotInProgress == -1 and self.renderQueue:
            self.startNextRender()

    def openWorkflowEditor(self):
        from comfystudio.sdmodules.editor import WorkflowEditor
        editor = WorkflowEditor(self.settingsManager, parent=self)
        editor.exec()

    def createBasicParamWidget(self, param):
        ptype = param["type"]
        pval = param["value"]
        if ptype == "int":
            w = QSpinBox()
            w.setRange(-2147483648, 2147483647)
            w.setValue(min(int(pval), 2147483647))
            w.valueChanged.connect(lambda v, p=param: self.onWorkflowParamChanged(None, p, v))
            return w
        elif ptype == "float":
            w = QDoubleSpinBox()
            w.setRange(-1e12, 1e12)
            w.setDecimals(6)
            w.setValue(pval)
            w.valueChanged.connect(lambda v, p=param: self.onWorkflowParamChanged(None, p, v))
            return w
        else:
            w = QLineEdit()
            w.setText(str(pval))
            w.textChanged.connect(lambda v, p=param: self.onWorkflowParamChanged(None, p, v))
            return w

    @Slot(int)
    def onWorkflowEnabledChanged(self, state):
        checkbox = self.sender()
        if isinstance(checkbox, QCheckBox):
            workflow = checkbox.property("workflow")
            if isinstance(workflow, WorkflowAssignment):
                workflow.enabled = checkbox.isChecked()
                logging.debug(f"Workflow '{workflow.path}' enabled set to {workflow.enabled}")

    @Slot()
    def onVisualizeWorkflow(self):
        button = self.sender()
        if isinstance(button, QPushButton):
            workflow = button.property("workflow")
            if isinstance(workflow, WorkflowAssignment):
                self.showWorkflowVisualizer(workflow)

    def refreshWorkflowsList(self, shot):
        self.workflowListWidget.clear()
        if shot:
            for workflow in shot.workflows:
                rowWidget = QWidget()
                rowLayout = QHBoxLayout(rowWidget)
                rowLayout.setContentsMargins(0, 0, 0, 0)

                enableCheck = QCheckBox("Enabled")
                enableCheck.setChecked(workflow.enabled)
                enableCheck.setProperty("workflow", workflow)
                enableCheck.stateChanged.connect(self.onWorkflowEnabledChanged)
                rowLayout.addWidget(enableCheck)

                label = QLabel(os.path.basename(workflow.path))
                rowLayout.addWidget(label)

                visualizeBtn = QPushButton("Visualize")
                visualizeBtn.setProperty("workflow", workflow)
                visualizeBtn.clicked.connect(self.onVisualizeWorkflow)
                rowLayout.addWidget(visualizeBtn)

                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, workflow)
                item.setSizeHint(rowWidget.sizeHint())
                self.workflowListWidget.addItem(item)
                self.workflowListWidget.setItemWidget(item, rowWidget)

    def toggleWorkflowEnabled(self, workflow, state):
        workflow.enabled = (state == Qt.Checked)

    def showWorkflowVisualizer(self, workflow):
        try:
            with open(workflow.path, "r") as f:
                wf_json = json.load(f)
            dlg = WorkflowVisualizer(wf_json, self)
            dlg.exec()
        except:
            pass

    def onWorkflowItemClicked(self, item):
        workflow: WorkflowAssignment = item.data(Qt.ItemDataRole.UserRole)
        if not workflow:
            return

        self.workflowParamsGroup.setEnabled(True)

        # If you need to send signals:
        if self.currentShotIndex >= 0 and self.currentShotIndex < len(self.shots):
            shot = self.shots[self.currentShotIndex]
            wfIndex = shot.workflows.index(workflow) if workflow in shot.workflows else -1
            if wfIndex != -1:
                self.workflowSelected.emit(self.currentShotIndex, wfIndex)

        # Clear existing rows in the layout
        while self.workflowParamsLayout.rowCount() > 0:
            self.workflowParamsLayout.removeRow(0)

        params_list = workflow.parameters.get("params", [])

        # 1) Group by node_id (or use nodeMetaTitle as key if you prefer).
        #    We'll store data in a dict: node_id -> { "title": ..., "params": [] }
        node_map = {}
        for param in params_list:
            # Skip if invisible and user isn't showing hidden
            if not param.get("visible", True) and not self.showHiddenParams:
                continue

            # For each node in param["nodeIDs"], group them
            # Usually there's just one node_id in that list
            for node_id in param.get("nodeIDs", []):
                # Use nodeMetaTitle for display, fallback to node_id if empty
                meta_title = param.get("nodeMetaTitle", "") or node_id
                if node_id not in node_map:
                    node_map[node_id] = {
                        "title": meta_title,
                        "params": []
                    }
                node_map[node_id]["params"].append(param)

        # 2) Now display each node group if it has any visible params
        for node_id, node_info in node_map.items():
            # If "params" is empty, skip
            if not node_info["params"]:
                continue

            # a) Insert a label with the node’s title
            title_label = QLabel(f"Node: {node_info['title']}")
            title_font = title_label.font()
            title_font.setBold(True)
            title_label.setFont(title_font)
            self.workflowParamsLayout.addRow(title_label)

            # b) For each param in this node, add the param row
            for param in node_info["params"]:
                rowWidget = QWidget()
                rowLayout = QHBoxLayout(rowWidget)
                rowLayout.setContentsMargins(0, 0, 0, 0)

                paramLabel = QLabel(f"{param.get('displayName', param['name'])}")
                # If you still want to show node_id next to each param:
                # paramLabel.setText(paramLabel.text() + f" [{node_id}]")

                # Show suffix if using dynamic overrides
                suffix = ""
                if param.get("usePrevResultImage"):
                    suffix = " (Using prev image)"
                elif param.get("usePrevResultVideo"):
                    suffix = " (Using prev video)"
                if suffix:
                    paramLabel.setText(paramLabel.text() + suffix)

                paramWidget = self.createBasicParamWidget(param)

                visibilityCheckbox = QCheckBox("Visible?")
                visibilityCheckbox.setChecked(param.get("visible", False))
                visibilityCheckbox.setProperty("node_id", node_id)
                visibilityCheckbox.setProperty("param", param)
                visibilityCheckbox.stateChanged.connect(
                    lambda state, cb=visibilityCheckbox, wf=workflow, nid=node_id, p=param:
                    self.onParamVisibilityChanged(wf, nid, p, cb.isChecked())
                )

                rowLayout.addWidget(paramLabel)
                rowLayout.addWidget(paramWidget)
                rowLayout.addWidget(visibilityCheckbox)
                rowWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                rowWidget.customContextMenuRequested.connect(
                    lambda pos, p=param: self.onWorkflowParamContextMenu(pos, p)
                )

                self.workflowParamsLayout.addRow(rowWidget)

            # c) Insert a horizontal divider after each node group
            divider = QFrame()
            divider.setFrameShape(QFrame.Shape.HLine)
            divider.setFrameShadow(QFrame.Shadow.Sunken)
            self.workflowParamsLayout.addRow(divider)

    def onWorkflowParamContextMenu(self, pos, param):
        """
        Right-click context menu for a single workflow param row in the Workflow tab.
        Allows setting this param to use the previous workflow's image or video result.
        """
        menu = QMenu(self)

        paramType = param.get("type", "string")
        if paramType == "string":
            setPrevImage = menu.addAction("Set Param to Previous Workflow's Image")
            setPrevVideo = menu.addAction("Set Param to Previous Workflow's Video")
            clearDynOverride = menu.addAction("Clear Dynamic Override")

            chosen = menu.exec(QCursor.pos())  # or mapToGlobal(pos) if needed
            if chosen == setPrevImage:
                param["usePrevResultImage"] = True
                param["usePrevResultVideo"] = False
                param["value"] = "(Awaiting previous workflow image)"
                param["dynamicOverrides"] = {
                    "type": "shot",
                    "shotIndex": self.currentShotIndex,
                    "assetType": "image"
                }
                QMessageBox.information(self, "Info",
                                        "This parameter is now flagged to use the previous workflow's image result."
                                        )
            elif chosen == setPrevVideo:
                param["usePrevResultVideo"] = True
                param["usePrevResultImage"] = False
                param["value"] = "(Awaiting previous workflow video)"
                param["dynamicOverrides"] = {
                    "type": "shot",
                    "shotIndex": self.currentShotIndex,
                    "assetType": "video"
                }
                QMessageBox.information(self, "Info",
                                        "This parameter is now flagged to use the previous workflow's video result."
                                        )
            elif chosen == clearDynOverride:
                param.pop("usePrevResultImage", None)
                param.pop("usePrevResultVideo", None)
                param.pop("dynamicOverrides", None)
                QMessageBox.information(self, "Info", "Dynamic override cleared.")
        else:
            # Non-string param, do nothing or show minimal menu
            pass

        # After making changes, re-fill the workflow item to show updated text
        # if the user re-opens the workflow item
        # For immediate refresh, you can re-call onWorkflowItemClicked on the current item:
        currentItem = self.workflowListWidget.currentItem()
        if currentItem:
            self.onWorkflowItemClicked(currentItem)

    def onWorkflowParamChanged(self, workflow: WorkflowAssignment, param: Dict, newVal):
        param["value"] = newVal
        self.saveCurrentWorkflowParams()

    def onParamVisibilityChanged(self, workflow: WorkflowAssignment, node_id: str, param: Dict, visible: bool):
        param["visible"] = visible
        self.setParamVisibility(workflow.path, node_id, param["name"], visible)
        self.onWorkflowItemClicked(self.workflowListWidget.currentItem())
        self.refreshParamsList(self.shots[self.currentShotIndex])

    def saveCurrentWorkflowParamsForShot(self, workflow: WorkflowAssignment):
        if self.currentShotIndex is None or self.currentShotIndex < 0 or self.currentShotIndex >= len(self.shots):
            return
        shot = self.shots[self.currentShotIndex]
        for wf in shot.workflows:
            if wf.path == workflow.path:
                wf.parameters = workflow.parameters
                break
        self.saveCurrentWorkflowParams()

    def addImageWorkflow(self):
        if self.currentShotIndex < 0 or self.currentShotIndex >= len(self.shots):
            QMessageBox.warning(self, "Warning", "No shot selected.")
            return
        path = self.imageWorkflowCombo.currentData()
        if not path:
            return
        self.addWorkflowToShot(path, isVideo=False)

    def addVideoWorkflow(self):
        if self.currentShotIndex < 0 or self.currentShotIndex >= len(self.shots):
            QMessageBox.warning(self, "Warning", "No shot selected.")
            return
        path = self.videoWorkflowCombo.currentData()
        if not path:
            return
        self.addWorkflowToShot(path, isVideo=True)

    def saveWorkflowDefaults(self, workflow_path, parameters):
        defaults = self.settingsManager.get("workflow_defaults", {})
        defaults[workflow_path] = parameters
        self.settingsManager.set("workflow_defaults", defaults)
        self.settingsManager.save()

    def loadWorkflowDefaults(self, workflow_path):
        defaults = self.settingsManager.get("workflow_defaults", {})
        return defaults.get(workflow_path, None)

    def addWorkflowToShot(self, workflow_path, isVideo=False):
        """
        Adds a new workflow to the currently selected shot, loading any default
        parameters (including dynamic overrides) if they exist.
        """
        if self.currentShotIndex < 0 or self.currentShotIndex >= len(self.shots):
            QMessageBox.warning(self, "Warning", "No shot selected.")
            return

        shot = self.shots[self.currentShotIndex]

        # Prevent adding the same workflow twice
        # for wf in shot.workflows:
        #     if wf.path == workflow_path and wf.isVideo == isVideo:
        #         QMessageBox.information(self, "Info", "Workflow already added to this shot.")
        #         return

        try:
            # Load the workflow JSON
            with open(workflow_path, "r") as f:
                workflow_json = json.load(f)

            # Create a list of params to expose
            params_to_expose = []
            for node_id, node_data in workflow_json.items():
                inputs = node_data.get("inputs", {})
                node_meta_title = node_data.get("_meta", {}).get("title", "")  # <--- get the node's _meta.title
                for key, value in inputs.items():
                    ptype = type(value).__name__
                    if ptype not in ["int", "float"]:
                        ptype = "string"

                    # Load visibility state from settings, keyed by node_id + param name
                    param_visibility = self.getParamVisibility(workflow_path, node_id, key)

                    params_to_expose.append({
                        "name": key,
                        "type": ptype,
                        "value": value,
                        "nodeIDs": [node_id],
                        "displayName": key,
                        "visible": param_visibility,
                        "nodeMetaTitle": node_meta_title,
                    })

            new_workflow = WorkflowAssignment(
                path=workflow_path,
                enabled=True,
                parameters={"params": params_to_expose},
                isVideo=isVideo
            )

            # Attempt to load defaults from settings
            defaults = self.loadWorkflowDefaults(workflow_path)
            if defaults and "params" in defaults:
                # Merge default values (including dynamic overrides) into our new_workflow
                for param in new_workflow.parameters.get("params", []):
                    default_param = next(
                        (d for d in defaults["params"]
                         if d["name"] == param["name"] and
                         d.get("nodeIDs", []) == param.get("nodeIDs", [])),  # matching nodeIDs
                        None
                    )
                    if default_param:
                        # Copy basic value
                        param["value"] = default_param.get("value", param["value"])

                        # If the default has dynamicOverrides, merge them too
                        if "dynamicOverrides" in default_param:
                            param["dynamicOverrides"] = copy.deepcopy(default_param["dynamicOverrides"])
                            # If you also use flags like usePrevResultImage, restore them
                            asset_type = default_param["dynamicOverrides"].get("assetType", "")
                            if asset_type == "image":
                                param["usePrevResultImage"] = True
                                param["usePrevResultVideo"] = False
                                param["value"] = "(Awaiting previous workflow image)"
                            elif asset_type == "video":
                                param["usePrevResultVideo"] = True
                                param["usePrevResultImage"] = False
                                param["value"] = "(Awaiting previous workflow video)"

            # Attach the new workflow and refresh
            shot.workflows.append(new_workflow)
            self.refreshWorkflowsList(shot)
            # QMessageBox.information(self, "Info", "Workflow added to the shot.")

        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load workflow: {e}")

    def removeWorkflowFromShot(self):
        if self.currentShotIndex < 0 or self.currentShotIndex >= len(self.shots):
            QMessageBox.warning(self, "Warning", "No shot selected.")
            return
        shot = self.shots[self.currentShotIndex]
        selected_items = self.workflowListWidget.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Warning", "No workflow selected to remove.")
            return
        item = selected_items[0]
        workflow: WorkflowAssignment = item.data(Qt.ItemDataRole.UserRole)
        if workflow:
            reply = QMessageBox.question(
                self,
                "Remove Workflow",
                f"Are you sure you want to remove workflow '{os.path.basename(workflow.path)}' from this shot?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                shot.workflows.remove(workflow)
                self.refreshWorkflowsList(shot)
                while self.workflowParamsLayout.rowCount() > 0:
                    self.workflowParamsLayout.removeRow(0)
                self.workflowParamsGroup.setEnabled(False)
                # QMessageBox.information(self, "Info", "Workflow removed from the shot.")
                self.refreshParamsList(shot)

    def saveCurrentWorkflowParamsAsDefault(self, workflow: WorkflowAssignment):
        self.saveWorkflowDefaults(workflow.path, workflow.parameters)
        QMessageBox.information(self, "Info", f"Defaults saved for workflow '{os.path.basename(workflow.path)}'.")

    # Add slot method to handle saving defaults via UI
    @Slot()
    def onSaveWorkflowDefaults(self):
        selected_item = self.workflowListWidget.currentItem()
        if not selected_item:
            QMessageBox.warning(self, "Warning", "No workflow selected to save defaults.")
            return
        workflow: WorkflowAssignment = selected_item.data(Qt.ItemDataRole.UserRole)
        if not workflow:
            QMessageBox.warning(self, "Warning", "Selected item is not a workflow.")
            return
        self.saveCurrentWorkflowParamsAsDefault(workflow)

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

    def refreshParamsList(self, shot: Shot):
        self.paramsListWidget.clear()
        if shot:
            for param in shot.params:
                item = QListWidgetItem(f"{param['name']} ({param['type']}) : {param['value']}")
                item.setData(Qt.ItemDataRole.UserRole, ("shot", param))
                self.paramsListWidget.addItem(item)

            for wf in shot.workflows:
                if "params" in wf.parameters:
                    for param in wf.parameters["params"]:
                        node_ids = param.get("nodeIDs", [])
                        for node_id in node_ids:
                            if param.get("visible", True):
                                label = f"[{os.path.basename(wf.path)}] [{node_id}] {param['name']} ({param['type']}) : {param['value']}"
                                item = QListWidgetItem(label)
                                item.setData(Qt.ItemDataRole.UserRole, ("workflow", wf, node_id, param))
                                self.paramsListWidget.addItem(item)

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
        self.statusMessage.setText("Render queue cleared.")

    def toggleHiddenParams(self):
        self.showHiddenParams = not self.showHiddenParams
        item = self.workflowListWidget.currentItem()
        if item:
            self.onWorkflowItemClicked(item)
        if self.currentShotIndex != -1:
            self.refreshParamsList(self.shots[self.currentShotIndex])

    def startNextRender(self):

        print("Start Next Render")
        if not self.renderQueue:
            print("no item in queue, returning")

            return
        self.shotInProgress = self.renderQueue.pop(0)
        self.workflowIndexInProgress = 0
        self.initWorkflowQueueForShot(self.shotInProgress)
        self.processNextWorkflow()

    def initWorkflowQueueForShot(self, shotIndex):
        shot = self.shots[shotIndex]
        wIndices = []
        for i, wf in enumerate(shot.workflows):
            if wf.enabled:
                wIndices.append(i)
        self.workflowQueue[shotIndex] = wIndices

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
        if workflow.lastSignature == currentSignature and alreadyRendered and os.path.exists(alreadyRendered):
            print(f"[DEBUG] Skipping workflow {workflowIndex} for shot '{shot.name}' because "
                  f"params haven't changed and a valid file exists.")
            self.workflowIndexInProgress += 1
            self.processNextWorkflow()
            return

        try:
            with open(workflow.path, "r") as f:
                workflow_json = json.load(f)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load workflow: {e}")
            self.workflowIndexInProgress += 1
            self.processNextWorkflow()
            return

        # Prepare any param overrides for workflow_json if needed
        local_params = copy.deepcopy(shot.params)
        wf_params = workflow.parameters.get("params", [])

        print("[DEBUG] Original workflow JSON keys:")
        for k in workflow_json.keys():
            print("       ", k)

        # If there's a previous workflow result, apply it to params
        if self.workflowIndexInProgress > 0:
            prevWorkflowIndex = self.workflowQueue[shotIndex][self.workflowIndexInProgress - 1]
            prevWf = shot.workflows[prevWorkflowIndex]
            # If we have new still or video from that workflow
            prevVideo = shot.videoPath if prevWf.isVideo and shot.videoPath else None
            prevImage = shot.stillPath if (not prevWf.isVideo) and shot.stillPath else None
            for param in wf_params:
                if param.get("usePrevResultImage") and prevImage:
                    print(f"[DEBUG] Setting param '{param['name']}' to prevImage: {prevImage}")
                    param["value"] = prevImage
                if param.get("usePrevResultVideo") and prevVideo:
                    print(f"[DEBUG] Setting param '{param['name']}' to prevVideo: {prevVideo}")
                    param["value"] = prevVideo

        # Now override nodes in workflow_json with local_params + wf_params
        # BUT only if node_id is found in param["nodeIDs"] for that param.
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
                    # if not param.get("visible", True):
                    #     continue
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
            #    but also only if node_id is in the param's nodeIDs (if that param uses them).
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
        from qtpy.QtCore import QThreadPool
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
        print("[DEBUG] Final workflow JSON structure before sending:")
        for k, v in workflow_json.items():
            print("       Node ID:", k)
            print("               ", v)

        # Start
        self.statusMessage.setText(f"Rendering {shot.name} - Workflow {workflowIndex + 1}/{len(shot.workflows)} ...")
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

                # Mark this workflow's own signature, so we don't re-render if nothing changed
                workflow.lastSignature = self.computeWorkflowSignature(shot, workflowIndex)

                # Update the UI / shot listing
                self.updateList()

                # Notify other parts (e.g. preview dock)
                self.shotRenderComplete.emit(shotIndex, workflowIndex, new_full, (final_is_video or workflow.isVideo))

        # Move on regardless of success/failure to next workflow in queue
        self.workflowIndexInProgress += 1
        self.processNextWorkflow()

    def onComfyError(self, error_msg):
        QMessageBox.warning(self, "Comfy Error", f"Error polling ComfyUI: {error_msg}")

    def onComfyFinished(self):
        """
        Worker is done, proceed with the next workflow or shot.
        """
        self.activeWorker = None
        self.statusMessage.setText("Ready")
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

    def showSettingsDialog(self):
        dialog = SettingsDialog(self.settingsManager, self.localization, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.loadWorkflows()
            if self.currentShotIndex != -1:
                self.fillDock()
            # selected_language = dialog.get_selected_language()
            self.localization.set_language(self.settingsManager.get("language"))
            self.retranslateUi()

    def retranslateUi(self):
        """
        Update all UI elements with the new language.
        Call this method after changing the language.
        """
        # Update Menu Bar
        self.updateMenuBarTexts()

        # Update Tool Bar
        self.updateToolBarTexts()

        # Update Status Bar
        self.updateStatusBarTexts()

        # Update Workflow Tabs
        self.updateWorkflowsTabTexts()

        self.listWidgetBase.updateTexts()

        # Update Other UI Components
        self.updateList()
        self.refreshWorkflowsList(self.shots[self.currentShotIndex] if self.currentShotIndex != -1 else None)
        self.refreshParamsList(self.shots[self.currentShotIndex] if self.currentShotIndex != -1 else None)

        # Update Dock Titles if needed
        self.dock.setWindowTitle(self.localization.translate("shot_details", default="Shot Details"))
        self.dockTabWidget.setTabText(0, self.localization.translate("workflows", default="Workflows"))
        self.dockTabWidget.setTabText(1, self.localization.translate("params", default="Parameters"))

        # Update any dynamically created widgets or labels within dialogs
        # For example, if you have any currently open dialogs, you may need to update their texts as well
        # Update Terminal Dock
        self.terminalDock.setWindowTitle(self.localization.translate("terminal_output", default="Terminal Output"))

        rtl_languages = ['he', 'ar', 'fa', 'ur']  # Add other RTL language codes as needed
        current_language = self.localization.get_language()
        is_rtl = current_language in rtl_languages
        if is_rtl:
            QApplication.instance().setLayoutDirection(Qt.RightToLeft)
        else:
            QApplication.instance().setLayoutDirection(Qt.LeftToRight)

    def onParamChanged(self, paramDict, newVal):
        paramDict["value"] = newVal
        self.saveCurrentWorkflowParams()

    def getParamVisibility(self, workflow_path, node_id, param_name):
        data = self.settingsManager.get("workflow_param_visibility", {})
        if workflow_path in data and node_id in data[workflow_path] and param_name in data[workflow_path][node_id]:
            return data[workflow_path][node_id][param_name]
        return False

    def setParamVisibility(self, workflow_path, node_id, param_name, visible):
        data = self.settingsManager.get("workflow_param_visibility", {})
        if workflow_path not in data:
            data[workflow_path] = {}
        if node_id not in data[workflow_path]:
            data[workflow_path][node_id] = {}
        data[workflow_path][node_id][param_name] = visible
        self.settingsManager.set("workflow_param_visibility", data)
        self.settingsManager.save()

    def fillDock(self):
        if self.currentShotIndex is None or self.currentShotIndex < 0 or self.currentShotIndex >= len(self.shots):
            self.clearDock()
            return
        shot = self.shots[self.currentShotIndex]
        self.refreshWorkflowsList(shot)
        self.refreshParamsList(shot)

    def clearDock(self):
        self.workflowListWidget.clear()
        while self.workflowParamsLayout.rowCount() > 0:
            self.workflowParamsLayout.removeRow(0)
        self.workflowParamsGroup.setEnabled(False)
        self.paramsListWidget.clear()

    def saveCurrentWorkflowParams(self, isVideo=False):
        self.settingsManager.save()

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
        return hashlib.md5(signature_str.encode("utf-8")).hexdigest()

    def newProject(self):
        self.shots.clear()
        self.updateList()
        self.currentShotIndex = -1
        self.clearDock()
        self.statusMessage.setText("New project created.")

    def openProject(self):
        filePath, _ = QFileDialog.getOpenFileName(self, "Open Project", "", "JSON Files (*.json);;All Files (*)")
        if filePath:
            try:
                with open(filePath, "r") as f:
                    project_data = json.load(f)
                shots_data = project_data.get("shots", [])
                self.shots = [Shot.from_dict(shot_dict) for shot_dict in shots_data]
                self.updateList()
                self.currentFilePath = filePath
                self.statusMessage.setText(f"Project loaded from {filePath}")
                self.fillDock()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to load project: {e}")

    def saveProject(self):
        if not hasattr(self, 'currentFilePath') or not self.currentFilePath:
            self.saveProjectAs()
            return
        project_data = {
            "shots": [shot.to_dict() for shot in self.shots],
        }
        try:
            with open(self.currentFilePath, 'w') as f:
                json.dump(project_data, f, indent=4)
            self.statusMessage.setText(f"Project saved to {self.currentFilePath}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save project: {e}")

    def saveProjectAs(self):
        filePath, _ = QFileDialog.getSaveFileName(self, "Save Project As", "", "JSON Files (*.json);;All Files (*)")
        if filePath:
            self.currentFilePath = filePath
            self.saveProject()

    def importShotsFromTxt(self):
        filePath, _ = QFileDialog.getOpenFileName(self, "Import Shots from TXT", "", "Text Files (*.txt);;All Files (*)")
        if filePath:
            try:
                with open(filePath, "r") as f:
                    lines = f.readlines()
                for line in lines:
                    shot_name = line.strip()
                    if shot_name:
                        new_shot = Shot(name=shot_name)
                        self.shots.append(new_shot)
                self.updateList()
                self.statusMessage.setText(f"Imported {len(lines)} shots from {filePath}")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to import shots: {e}")

    def startComfy(self):
        py_path = self.settingsManager.get("comfy_py_path")
        main_path = self.settingsManager.get("comfy_main_path")
        if py_path and main_path:
            try:
                self.comfy_process = subprocess.Popen(
                    [py_path, main_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    universal_newlines=True
                )
                self.statusMessage.setText("Comfy started.")
                self.appendLog("Comfy process started.")
                self.comfy_stdout_thread = threading.Thread(target=self.read_stream, args=(self.comfy_process.stdout,))
                self.comfy_stderr_thread = threading.Thread(target=self.read_stream, args=(self.comfy_process.stderr,))
                self.comfy_stdout_thread.daemon = True
                self.comfy_stderr_thread.daemon = True
                self.comfy_stdout_thread.start()
                self.comfy_stderr_thread.start()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to start Comfy: {e}")
        else:
            QMessageBox.warning(self, "Error", "Comfy paths not set in settings.")

    def read_stream(self, stream):
        try:
            for line in iter(stream.readline, ''):
                if line:
                    self.logStream.write(line)
            stream.close()
        except Exception as e:
            self.logStream.write(f"Error reading stream: {e}")

    def stopComfy(self):
        if hasattr(self, 'comfy_process'):
            self.comfy_process.terminate()
            self.statusMessage.setText("Comfy stopped.")
    def loadWorkflows(self):
        base_dir = os.path.join(os.path.dirname(__file__), "workflows")
        image_dir = self.settingsManager.get("comfy_image_workflows", os.path.join(base_dir, "image"))
        video_dir = self.settingsManager.get("comfy_video_workflows", os.path.join(base_dir, "video"))
        self.image_workflows = []
        self.video_workflows = []
        if os.path.isdir(image_dir):
            for fname in os.listdir(image_dir):
                if fname.lower().endswith(".json"):
                    self.image_workflows.append(os.path.join(image_dir, fname))
        if os.path.isdir(video_dir):
            for fname in os.listdir(video_dir):
                if fname.lower().endswith(".json"):
                    self.video_workflows.append(os.path.join(video_dir, fname))

        # Fill combos
        self.imageWorkflowCombo.clear()
        for wf in self.image_workflows:
            base = os.path.basename(wf)
            idx = self.imageWorkflowCombo.count()
            self.imageWorkflowCombo.addItem(base, userData=wf)
            self.imageWorkflowCombo.setCurrentIndex(idx)

        self.videoWorkflowCombo.clear()
        for wf in self.video_workflows:
            base = os.path.basename(wf)
            idx = self.videoWorkflowCombo.count()
            self.videoWorkflowCombo.addItem(base, userData=wf)
            self.videoWorkflowCombo.setCurrentIndex(idx)

    def loadPlugins(self):
        plugins_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugins")
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

    def extendClip(self, idx):
        """
        Extends the current project by creating a new shot based on the last shot's
        last workflow's last video output or image. It adds the currently selected
        video workflow to the new shot and prompts the user to select which input
        parameter should be set to the last output. It also updates the UI to reflect
        the new shot and ensures proper result tracking and preview.
        """
        if not self.shots:
            QMessageBox.warning(self, "Warning", "No shots available to extend from.")
            return

        last_shot = self.shots[idx]
        if not last_shot.workflows:
            QMessageBox.warning(self, "Warning", self.localization.translate("warning_no_workflows_to_extend"))
            return

        last_workflow = last_shot.workflows[-1]
        if last_workflow.isVideo:
            if not last_shot.videoVersions:
                QMessageBox.warning(self, "Warning", self.localization.translate("warning_no_outputs_to_extend"))
                return
            last_output = last_shot.videoVersions[-1]
        else:
            if not last_shot.imageVersions:
                QMessageBox.warning(self, "Warning", self.localization.translate("warning_no_image_to_extend"))
                return
            last_output = last_shot.imageVersions[-1]

        success, last_frame = extract_frame(last_output)
        if success:

            # Create a new shot by deep copying the last shot
            new_shot = copy.deepcopy(last_shot)
            new_shot.name = f"{last_shot.name} - Extended"
            # Reset paths and versions for the new shot
            new_shot.stillPath = ""
            new_shot.videoPath = ""
            new_shot.imageVersions = []
            new_shot.videoVersions = []
            new_shot.currentImageVersion = -1
            new_shot.currentVideoVersion = -1
            new_shot.lastStillSignature = ""
            new_shot.lastVideoSignature = ""

            # Add the currently selected video workflow to the new shot
            # Assuming the last workflow is the currently selected one
            selected_workflow = last_workflow
            new_workflow = copy.deepcopy(selected_workflow)
            new_workflow.enabled = True  # Ensure the workflow is enabled
            new_shot.workflows.append(new_workflow)

            # Append the new shot to the shots list
            self.shots.append(new_shot)
            self.updateList()

            # Select the new shot in the list widget
            new_shot_idx = len(self.shots) - 1
            self.currentShotIndex = new_shot_idx
            self.listWidget.setCurrentRow(new_shot_idx)
            self.fillDock()

            # Prompt the user to select which input parameter to set to the last output
            params = new_workflow.parameters.get("params", [])
            # visible_params = [param for param in params if param.get("visible", True)]
            # if not visible_params:
            #     QMessageBox.information(self, "Info", "The workflow has no visible parameters to set.")
            #     return

            param_names = [param["name"] for param in params]
            param, ok = QInputDialog.getItem(
                self,
                "Select Input Parameter",
                "Which input parameter should be set to the last output?",
                param_names,
                0,
                False
            )

            if ok and param:
                # Find the selected parameter and set its value to the last output
                for p in params:
                    if p["name"] == param:
                        p["value"] = last_frame
                        break
                QMessageBox.information(
                    self,
                    "Info",
                    f"Parameter '{param}' has been set to '{last_output}'."
                )
                self.saveCurrentWorkflowParamsForShot(new_workflow)
                self.refreshParamsList(new_shot)

            # Emit signals to update the preview dock
            self.shotSelected.emit(new_shot_idx)
            self.workflowSelected.emit(new_shot_idx, len(new_shot.workflows) - 1)

            # # Update the preview dock to show the new workflow's output
            # self.previewDock.updatePreview(new_shot_idx, len(new_shot.workflows) - 1)

            # Optionally, automatically start rendering the new shot
            # Uncomment the following lines if desired
            # self.renderQueue.append(new_shot_idx)
            # self.startNextRender()
        else:
            QMessageBox.warning(self, "Error", last_frame)

    def cleanUp(self):
        self.settingsManager.save()
        self.stopComfy()

    def closeEvent(self, event):
        if len(self.shots) > 0:
            reply = QMessageBox.question(
                self,
                "Save Project?",
                "Do you want to save the project before exiting?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel
            )
            if reply in [QMessageBox.StandardButton.Yes]:
                self.saveProject()
                self.cleanUp()
                event.accept()
            elif reply == QMessageBox.StandardButton.No:
                self.cleanUp()
                event.accept()
            else:
                event.ignore()