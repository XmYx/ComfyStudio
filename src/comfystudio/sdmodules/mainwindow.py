#!/usr/bin/env python
import copy
import json
import logging
import os
import random
import subprocess
import sys
import tempfile

import urllib
from typing import List, Dict, Any

import requests
from PyQt6.QtCore import QThreadPool, QUrl, QMetaObject
from PyQt6.QtMultimedia import QMediaPlayer

from qtpy import QtCore

from qtpy.QtGui import QCursor

from qtpy.QtWidgets import (
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
    QMenu,
    QFrame,
    QApplication,
    QSplitter
)

from qtpy.QtCore import (
    Qt,
    QPoint,
    QObject,
    Signal,
    Slot,
    QThread
)
from qtpy.QtGui import (
    QAction
)

from comfystudio.sdmodules.aboutdialog import AboutDialog
from comfystudio.sdmodules.comfy_installer import ComfyInstallerWizard
from comfystudio.sdmodules.cs_datastruts import Shot, WorkflowAssignment
from comfystudio.sdmodules.help import HelpWindow
from comfystudio.sdmodules.localization import LocalizationManager
from comfystudio.sdmodules.model_manager import ModelManagerWindow
from comfystudio.sdmodules.node_visualizer import WorkflowVisualizer
from comfystudio.sdmodules.preview_dock import ShotPreviewDock
from comfystudio.sdmodules.settings import SettingsManager, SettingsDialog
from comfystudio.sdmodules.shot_manager import ShotManager
from comfystudio.sdmodules.vareditor import GlobalVariablesEditor, DynamicParam, DynamicParamEditor
from comfystudio.sdmodules.videotools import extract_frame
from comfystudio.sdmodules.widgets import ReorderableListWidget
from comfystudio.sdmodules.new_widget import ShotManagerWidget as ReorderableListWidget
from comfystudio.sdmodules.worker import RenderWorker, CustomNodesSetupWorker, ComfyWorker


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
        self.lastSelectedWorkflowIndex = {}
        self.currentShotIndex: int = -1

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

        self.logStream = EmittingStream()
        self.logStream.text_written.connect(self.appendLog)

        self.showHiddenParams = False  # Toggles display of hidden parameters

        self.initUI()
        # self.setupLogging()
        self.loadWorkflows()
        self.updateList()

        self.loadPlugins()
        self.restoreWindowState()

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
        # self.listWidgetBase.updateTexts()
        self.listWidget = self.listWidgetBase.shotListView
        self.listWidget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.listWidget.itemClicked.connect(self.onItemClicked)
        self.listWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.listWidget.customContextMenuRequested.connect(self.onListWidgetContextMenu)
        self.listWidget.itemSelectionChanged.connect(self.onSelectionChanged)
        # self.mainLayout.addWidget(self.listWidgetBase)

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
                    {"name": "setupComfyNodesAct", "text": "Setup Comfy Nodes", "trigger": self.setup_custom_nodes},
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
        self.create_dynamic_menu_bar(menu_config)
        self.updateRecentsMenu()
        # self.createMenuBar()
        self.createToolBar()
        self.createStatusBar()

        self.shotSelected.connect(self.previewDock.onShotSelected)
        self.workflowSelected.connect(self.previewDock.onWorkflowSelected)
        self.shotRenderComplete.connect(self.previewDock.onShotRenderComplete)

        self.createWindowsMenu()
        # NEW: Global variables dictionary (shared among DynamicParam editors)
        self.global_vars: Dict[str, Any] = {}
        # Instantiate the GlobalVariablesEditor widget (it self-registers)
        self.globVarEditor = GlobalVariablesEditor(self)
        self.globVarEditor.variablesChanged.connect(self.updateGlobalVariables)
        # Add an action to open the Global Variables Editor from the menu bar
        self.globalVarsAct = QAction("Edit Global Variables", self)
        self.globalVarsAct.triggered.connect(self.openGlobalVariablesEditor)
        self.menuBar().addAction(self.globalVarsAct)
    def updateGlobalVariables(self, new_globals: Dict[str, Any]):
        self.global_vars = new_globals
        # Optionally refresh any UI elements that depend on globals

    def openGlobalVariablesEditor(self):
        self.globVarEditor.exec()
    def initWorkflowsTab(self):
        layout = self.workflowsLayout

        self.workflowGroupBox = QGroupBox(
            self.localization.translate("workflow_selection", default="Workflow Selection")
        )
        self.workflowGroupBox.setCheckable(True)
        self.workflowGroupBox.setChecked(True)
        groupLayout = QVBoxLayout(self.workflowGroupBox)
        self.workflowGroupBox.setLayout(groupLayout)

        def onWorkflowsToggled(checked):
            for w in self.workflowGroupBox.children():
                if w is not groupLayout:
                    if hasattr(w, "setVisible"):
                        w.setVisible(not w.isVisible())

        self.workflowGroupBox.toggled.connect(onWorkflowsToggled)

        comboLayout_1 = QHBoxLayout()
        comboLayout_2 = QHBoxLayout()

        self.imageWorkflowLabel = QLabel(
            self.localization.translate("label_image_workflow", default="Image Workflow:")
        )
        self.imageWorkflowCombo = QComboBox()
        self.imageWorkflowCombo.setToolTip(
            self.localization.translate("tooltip_select_image_workflow", default="Select an Image Workflow to add")
        )
        self.addImageWorkflowBtn = QPushButton(
            self.localization.translate("button_add_image_workflow", default="Add")
        )
        self.addImageWorkflowBtn.setToolTip(
            self.localization.translate("tooltip_add_image_workflow",
                                        default="Add the selected Image Workflow to the shot")
        )
        self.addImageWorkflowBtn.clicked.connect(self.addImageWorkflow)
        comboLayout_1.addWidget(self.imageWorkflowLabel)
        comboLayout_1.addWidget(self.imageWorkflowCombo)
        comboLayout_1.addWidget(self.addImageWorkflowBtn)
        # comboLayout.addSpacing(20)
        self.videoWorkflowLabel = QLabel(
            self.localization.translate("label_video_workflow", default="Video:")
        )
        self.videoWorkflowCombo = QComboBox()
        self.videoWorkflowCombo.setToolTip(
            self.localization.translate("tooltip_select_video_workflow", default="Select a Video Workflow to add")
        )
        self.addVideoWorkflowBtn = QPushButton(
            self.localization.translate("button_add_video_workflow", default="Add")
        )
        self.addVideoWorkflowBtn.setToolTip(
            self.localization.translate("tooltip_add_video_workflow",
                                        default="Add the selected Video Workflow to the shot")
        )
        self.addVideoWorkflowBtn.clicked.connect(self.addVideoWorkflow)
        comboLayout_2.addWidget(self.videoWorkflowLabel)
        comboLayout_2.addWidget(self.videoWorkflowCombo)
        comboLayout_2.addWidget(self.addVideoWorkflowBtn)
        groupLayout.addLayout(comboLayout_1)
        groupLayout.addLayout(comboLayout_2)

        self.workflowListLabel = QLabel(
            self.localization.translate("label_workflow_list", default="Assigned Workflows:")
        )
        groupLayout.addWidget(self.workflowListLabel)
        self.workflowListWidget = QListWidget()
        self.workflowListWidget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.workflowListWidget.itemClicked.connect(self.onWorkflowItemClicked)
        self.workflowListWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.workflowListWidget.customContextMenuRequested.connect(self.onWorkflowListContextMenu)
        groupLayout.addWidget(self.workflowListWidget)

        buttonsLayout = QHBoxLayout()
        self.removeWorkflowBtn = QPushButton(
            self.localization.translate("button_remove_workflow", default="Remove Workflow")
        )
        self.removeWorkflowBtn.setToolTip(
            self.localization.translate("tooltip_remove_workflow",
                                        default="Remove the selected Workflow from the shot")
        )
        self.removeWorkflowBtn.clicked.connect(self.removeWorkflowFromShot)
        buttonsLayout.addWidget(self.removeWorkflowBtn)
        groupLayout.addLayout(buttonsLayout)

        self.toggleHiddenParamsBtn = QPushButton(
            self.localization.translate("button_toggle_hidden_params", default="Show/Hide All Params")
        )
        self.toggleHiddenParamsBtn.setToolTip(
            self.localization.translate("tooltip_toggle_hidden_params",
                                        default="Toggle the visibility of hidden parameters")
        )
        self.toggleHiddenParamsBtn.clicked.connect(self.toggleHiddenParams)
        groupLayout.addWidget(self.toggleHiddenParamsBtn)

        self.workflowParamsGroup = QGroupBox(
            self.localization.translate("group_workflow_parameters", default="Workflow Parameters")
        )
        self.workflowParamsLayout = QFormLayout(self.workflowParamsGroup)
        self.workflowParamsGroup.setLayout(self.workflowParamsLayout)
        self.workflowParamsGroup.setEnabled(False)
        self.workflowParamsScroll = QScrollArea()
        self.workflowParamsScroll.setWidgetResizable(True)
        self.workflowParamsScroll.setWidget(self.workflowParamsGroup)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(20)
        splitter.addWidget(self.workflowGroupBox)
        splitter.addWidget(self.workflowParamsScroll)

        # Set minimum widths to prevent widgets from being hidden
        self.workflowGroupBox.setMinimumWidth(200)  # Adjust this value as needed
        self.workflowParamsScroll.setMinimumWidth(300)  # Adjust this value as needed

        # Optionally, set initial sizes to distribute space appropriately
        splitter.setSizes([200, 800])  # Adjust initial sizes based on your preference

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        layout.addWidget(splitter)

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

    def create_dynamic_menu_bar(self, menu_config):
        """
        Dynamically creates the menu bar from a configuration dict.

        menu_config: dict in the following format:
          {
              "MenuName": {
                  "title": "Menu Title",       # optional, defaults to key name
                  "actions": [
                      {
                          "name": "actionName",      # unique key to reference the action
                          "text": "Action Text",
                          "trigger": self.someFunction  # function to call on trigger
                      },
                      {
                          "separator": True          # to add a separator
                      },
                      {
                          "submenu": "SubmenuKey",   # indicates a submenu is desired
                          "title": "Submenu Title",    # optional, defaults to submenu key
                          "actions": [ ... ]           # actions for the submenu (same structure)
                      }
                  ]
              },
              ...
          }
        """
        # Clear the existing menu bar to avoid duplication
        self.menuBar().clear()

        # Dictionaries to optionally store menus and actions for later reference
        self.menus = {}
        self.actions = {}

        for menu_key, menu_data in menu_config.items():
            menu_title = menu_data.get("title", menu_key)
            menu = QMenu(menu_title, self)

            for item in menu_data.get("actions", []):
                # Add a separator if specified
                if item.get("separator"):
                    menu.addSeparator()
                # If item specifies a submenu, create it recursively
                elif "submenu" in item:
                    sub_title = item.get("title", item["submenu"])
                    submenu = QMenu(sub_title, self)
                    for subitem in item.get("actions", []):
                        if subitem.get("separator"):
                            submenu.addSeparator()
                        else:
                            action = QAction(self)
                            action.setText(subitem.get("text", ""))
                            if "trigger" in subitem and callable(subitem["trigger"]):
                                action.triggered.connect(subitem["trigger"])
                            submenu.addAction(action)
                            # Save the action reference if a name is provided
                            if "name" in subitem:
                                self.actions[subitem["name"]] = action
                    menu.addMenu(submenu)
                    # Optionally store the submenu reference
                    self.menus[item["submenu"]] = submenu
                # Otherwise, create a normal action
                else:
                    action = QAction(self)
                    action.setText(item.get("text", ""))
                    if "trigger" in item and callable(item["trigger"]):
                        action.triggered.connect(item["trigger"])
                    menu.addAction(action)
                    if "name" in item:
                        self.actions[item["name"]] = action

            self.menuBar().addMenu(menu)
            self.menus[menu_key] = menu


    def createMenuBar(self):
        # Clear existing menu bar to prevent duplication
        self.menuBar().clear()

        # Initialize Menus
        self.fileMenu = QMenu(self)
        self.settingsMenu = QMenu(self)
        self.helpMenu = QMenu(self)  # New Help Menu

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
        self.openModelManagerAct = QAction(self)
        self.setupComfyNodesAct = QAction(self)
        self.setupComfyAct = QAction(self)


        # Help Menu Actions
        self.userGuideAct = QAction(self)
        self.aboutAct = QAction(self)

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
        self.openModelManagerAct.triggered.connect(self.openModelManager)
        self.setupComfyNodesAct.triggered.connect(self.setup_custom_nodes)
        self.setupComfyAct.triggered.connect(self.startComfyInstallerWizard)

        # Help Menu Actions Connections
        self.userGuideAct.triggered.connect(self.openUserGuide)
        self.aboutAct.triggered.connect(self.openAboutDialog)

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

        # Add Recents Submenu
        self.recentsMenu = QMenu(self.localization.translate("menu_recents", default="Recents"), self)
        self.fileMenu.addMenu(self.recentsMenu)
        self.updateRecentsMenu()

        # Add actions to Settings Menu
        self.settingsMenu.addAction(self.openSettingsAct)
        self.settingsMenu.addAction(self.openModelManagerAct)
        self.settingsMenu.addAction(self.setupComfyNodesAct)
        self.settingsMenu.addAction(self.setupComfyAct)

        # Add actions to Help Menu
        self.helpMenu.addAction(self.userGuideAct)
        self.helpMenu.addSeparator()
        self.helpMenu.addAction(self.aboutAct)

        # Add Menus to Menu Bar
        self.menuBar().addMenu(self.fileMenu)
        self.menuBar().addMenu(self.settingsMenu)
        self.menuBar().addMenu(self.helpMenu)  # Add Help Menu

    def updateRecentsMenu(self):

        recentsMenu = self.menus['Recents']
        recentsMenu.clear()
        recents = self.settingsManager.get("recent_files", [])
        if not recents:
            emptyItem = QAction(self.localization.translate("menu_recents_empty", default="No recent projects"), self)
            emptyItem.setEnabled(False)
            recentsMenu.addAction(emptyItem)
        else:
            for filePath in recents:
                action = QAction(os.path.basename(filePath), self)
                action.setToolTip(filePath)
                action.triggered.connect(lambda checked, path=filePath: self.openProjectFromPath(path))
                recentsMenu.addAction(action)
            # Add separator and 'Clear Recents' option
            recentsMenu.addSeparator()
            clearAction = QAction(self.localization.translate("menu_recents_clear", default="Clear Recents"), self)
            clearAction.triggered.connect(self.clearRecents)
            recentsMenu.addAction(clearAction)

    def addToRecents(self, filePath):
        recents = self.settingsManager.get("recent_files", [])
        if filePath in recents:
            recents.remove(filePath)
        recents.insert(0, filePath)
        recents = recents[:10]  # Keep only the latest 10
        self.settingsManager.set("recent_files", recents)
        self.settingsManager.save()
        self.updateRecentsMenu()

    def clearRecents(self):
        self.settingsManager.set("recent_files", [])
        self.settingsManager.save()
        self.updateRecentsMenu()

    def openProjectFromPath(self, filePath):
        if os.path.exists(filePath):
            try:
                with open(filePath, "r") as f:
                    project_data = json.load(f)
                shots_data = project_data.get("shots", [])
                self.shots = [Shot.from_dict(shot_dict) for shot_dict in shots_data]
                self.updateList()
                self.currentFilePath = filePath
                self.statusMessage.setText(
                    f"{self.localization.translate('status_loaded_from', default='Project loaded from')} {filePath}")
                self.fillDock()
                self.addToRecents(filePath)
                self.setProjectModified(False)
            except Exception as e:
                QMessageBox.warning(self, self.localization.translate("dialog_error_title", default="Error"),
                                    self.localization.translate("error_failed_to_load_project",
                                                                default=f"Failed to load project: {e}"))
        else:
            QMessageBox.warning(self, self.localization.translate("dialog_error_title", default="Error"),
                                self.localization.translate("error_project_not_found",
                                                            default=f"Project file not found: {filePath}"))
            self.clearRecents()

    def createWindowsMenu(self):
        """
        Creates the 'Windows' menu in the menu bar with actions to toggle the visibility
        of various dock widgets, including the Web Browser. Also initializes the WebBrowser
        dock widget and tabs it with the 'Shot Details' dock widget.
        """
        # Create the Windows menu
        self.windowsMenu = QMenu(self.localization.translate("menu_windows", default="Windows"), self)

        # Action to toggle Shot Details dock
        self.toggleShotDetailsAct = QAction(
            self.localization.translate("menu_toggle_shot_details", default="Toggle Shot Details"),
            self,
            checkable=True
        )
        self.toggleShotDetailsAct.setChecked(True)
        self.toggleShotDetailsAct.triggered.connect(self.dock.setVisible)
        self.windowsMenu.addAction(self.toggleShotDetailsAct)

        # Action to toggle Terminal Output dock
        self.toggleTerminalAct = QAction(
            self.localization.translate("menu_toggle_terminal", default="Toggle Terminal Output"),
            self,
            checkable=True
        )
        self.toggleTerminalAct.setChecked(True)
        self.toggleTerminalAct.triggered.connect(self.terminalDock.setVisible)
        self.windowsMenu.addAction(self.toggleTerminalAct)

        # Action to toggle Preview Dock
        self.togglePreviewDockAct = QAction(
            self.localization.translate("menu_toggle_preview_dock", default="Toggle Preview Dock"),
            self,
            checkable=True
        )
        self.togglePreviewDockAct.setChecked(True)
        self.togglePreviewDockAct.triggered.connect(self.previewDock.setVisible)
        self.windowsMenu.addAction(self.togglePreviewDockAct)

        # Action to toggle Web Browser dock
        self.toggleWebBrowserAct = QAction(
            self.localization.translate("menu_toggle_webbrowser", default="Toggle Web Browser"),
            self,
            checkable=True
        )
        self.toggleWebBrowserAct.setChecked(False)
        self.toggleWebBrowserAct.triggered.connect(self.toggleWebBrowser)
        self.windowsMenu.addAction(self.toggleWebBrowserAct)

        # Add the Windows menu to the menu bar
        self.menuBar().addMenu(self.windowsMenu)

        # Create the WebBrowser dock widget if it doesn't exist
        if not hasattr(self, 'webBrowserDock'):
            # Initialize the WebBrowser dock
            self.webBrowserDock = QDockWidget(
                self.localization.translate("dock_web_browser", default="Web Browser"),
                self
            )
            self.webBrowserDock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)

            # Initialize the WebBrowser view
            from qtpy.QtWebEngineWidgets import QWebEngineView

            self.webBrowserView = QWebEngineView()
            self.webBrowserDock.setWidget(self.webBrowserView)

            # Add the WebBrowser dock to the same area as Shot Details and tabify
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.webBrowserDock)
            self.tabifyDockWidget(self.dock, self.webBrowserDock)

            # Initially hide the WebBrowser dock
            self.webBrowserDock.hide()
        self.updateWindowsMenuTexts()

    def updateWindowsMenuTexts(self):
        """
        Updates the texts of the 'Windows' menu and its actions based on the current localization.
        This should be called within the retranslateUi method to refresh UI elements when the language changes.
        """
        # Update Windows menu title
        self.windowsMenu.setTitle(self.localization.translate("menu_windows", default="Windows"))

        # Update actions' texts and tooltips
        self.toggleShotDetailsAct.setText(
            self.localization.translate("menu_toggle_shot_details", default="Toggle Shot Details")
        )
        self.toggleShotDetailsAct.setToolTip(
            self.localization.translate("tooltip_toggle_shot_details", default="Show or hide the Shot Details dock")
        )

        self.toggleTerminalAct.setText(
            self.localization.translate("menu_toggle_terminal", default="Toggle Terminal Output")
        )
        self.toggleTerminalAct.setToolTip(
            self.localization.translate("tooltip_toggle_terminal", default="Show or hide the Terminal Output dock")
        )

        self.togglePreviewDockAct.setText(
            self.localization.translate("menu_toggle_preview_dock", default="Toggle Preview Dock")
        )
        self.togglePreviewDockAct.setToolTip(
            self.localization.translate("tooltip_toggle_preview_dock", default="Show or hide the Preview Dock")
        )

        self.toggleWebBrowserAct.setText(
            self.localization.translate("menu_toggle_webbrowser", default="Toggle Web Browser")
        )
        self.toggleWebBrowserAct.setToolTip(
            self.localization.translate("tooltip_toggle_webbrowser", default="Show or hide the Web Browser dock")
        )

        # Update WebBrowser dock title
        self.webBrowserDock.setWindowTitle(
            self.localization.translate("dock_web_browser", default="Web Browser")
        )

    def toggleWebBrowser(self, checked):
        """
        Slot to handle the toggling of the WebBrowser dock widget. When shown,
        it loads the configured 'comfy_ip' URL. When hidden, it simply hides the dock.

        Args:
            checked (bool): The checked state of the toggle action.
        """
        if checked:
            # Show the WebBrowser dock
            self.webBrowserDock.show()

            # Retrieve the 'comfy_ip' URL from settings
            comfy_ip = self.settingsManager.get("comfy_ip", "http://127.0.0.1:8188")

            # Load the URL in the WebBrowser view
            self.webBrowserView.setUrl(QUrl(comfy_ip))
        else:
            # Hide the WebBrowser dock
            self.webBrowserDock.hide()


    def createToolBar(self):
        # Clear existing toolbar to prevent duplication
        # self.toolBar().clear()

        # Initialize Toolbar
        self.toolbar = self.addToolBar(self.localization.translate("toolbar_label", default="Main Toolbar"))
        self.toolbar.setObjectName("main_toolbar")

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
        self.terminalDock.setObjectName("terminal_dock")
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
        self.terminalDock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.terminalTextEdit.setReadOnly(True)
        self.terminalDock.setWidget(self.terminalTextEdit)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.terminalDock)
        self.terminalDock.hide()

    def updateMenuBarTexts(self):

        print("Updating menubar titles")
        print("File Menu will be", self.localization.translate("menu_file", default="File"))
        # Update File Menu Title
        self.fileMenu.setTitle(self.localization.translate("menu_file", default="File"))

        # Update Settings Menu Title
        self.settingsMenu.setTitle(self.localization.translate("menu_settings", default="Settings"))
        self.helpMenu.setTitle(self.localization.translate("menu_help", default="Help"))

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
        self.openModelManagerAct.setText(self.localization.translate("menu_open_model_manager", default="Open Model Manager"))
        self.setupComfyAct.setText(self.localization.translate("menu_setup_comfy_base", default="Install/Update ComfyUI"))
        self.setupComfyNodesAct.setText(self.localization.translate("menu_setup_comfy", default="Install/Update Custom Nodes"))
        # Update Help Menu Actions Texts
        self.userGuideAct.setText(self.localization.translate("menu_user_guide", default="User Guide"))
        self.aboutAct.setText(self.localization.translate("menu_about", default="About"))
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
    def openUserGuide(self):
        help_window = HelpWindow(self)
        help_window.exec()

    def openAboutDialog(self):
        about_dialog = AboutDialog(self)
        about_dialog.exec()
    def startComfyInstallerWizard(self):
        """
        Launches the Comfy Installer Wizard to install/update ComfyUI and its dependencies.
        """
        wizard = ComfyInstallerWizard(parent=self, settings_manager=self.settingsManager, log_callback=self.appendLog)
        wizard.exec()

    def openModelManager(self):
        """
        Opens the Model Manager Window.
        """
        self.model_manager_window = ModelManagerWindow(parent=self, settings_manager=self.settingsManager)
        self.model_manager_window.exec()
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
        self.setProjectModified(True)

    def updateList(self):
        previous_selection = self.listWidget.currentRow()

        self.listWidget.clear()

        for i, shot in enumerate(self.shots):
            icon = self.getShotIcon(shot)
            label_text = f"{shot.name}"
            item = QListWidgetItem(icon, label_text)
            item.setData(Qt.ItemDataRole.UserRole, i)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            self.listWidget.addItem(item)

        if previous_selection is not None and 0 <= previous_selection < self.listWidget.count():
            self.listWidget.setCurrentRow(previous_selection)
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
        deleteAction = menu.addAction(
            self.localization.translate("context_delete_shots", default="Delete Shot(s)")
        )
        duplicateAction = menu.addAction(
            self.localization.translate("context_duplicate_shots", default="Duplicate Shot(s)")
        )
        extendAction = menu.addAction(
            self.localization.translate("context_extend_clips", default="Extend Clip(s)")
        )

        mergeAction = QAction(self.localization.translate("context_merge_clips", default="Merge Clips"))

        if len(selected_items) > 1:
            menu.addAction(
                mergeAction
            )

        action = menu.exec(self.listWidget.mapToGlobal(pos))

        if action == deleteAction:
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
        elif action  == mergeAction:
            self.mergeClips(valid_indices)

    def mergeClips(self, selected_indices):
        if len(selected_indices) < 2:
            QMessageBox.warning(
                self,
                self.localization.translate("dialog_warning_title", default="Warning"),
                self.localization.translate("warning_merge_minimum", default="Select at least two clips to merge.")
            )
            return

        video_paths = []
        for idx in selected_indices:
            shot = self.shots[idx]
            video_path = shot.videoPath
            if not video_path or not os.path.exists(video_path):
                QMessageBox.warning(
                    self,
                    self.localization.translate("dialog_warning_title", default="Warning"),
                    self.localization.translate("warning_no_valid_video_path",
                                                default="Shot '{shot_name}' has no valid video path.").format(
                        shot_name=shot.name)
                )
                return
            video_paths.append(video_path)

        temp_file_list = tempfile.mktemp(suffix='.txt')
        with open(temp_file_list, 'w') as f:
            for path in video_paths:
                f.write(f"file '{path}'\n")

        if hasattr(self, 'currentFilePath') and self.currentFilePath:
            project_folder = os.path.dirname(self.currentFilePath)
        else:
            QMessageBox.warning(self,
                                self.localization.translate("dialog_warning_title", default="Warning"),
                                self.localization.translate("warning_no_project", default="No project file is currently open. Merged video will be saved to the temporary directory.")
                                )
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
            QMessageBox.warning(
                self,
                self.localization.translate("dialog_error_title", default="Error"),
                self.localization.translate("error_failed_to_merge", default="Failed to merge videos: {error}")
                .format(error=e.stderr.decode())
            )
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

    def releaseMedia(self):
        """
        Slot to handle stopping and releasing the media player safely.
        """
        if hasattr(self, 'player') and isinstance(self.player, QMediaPlayer):
            try:
                self.player.stop()
                self.player.setSource(None)  # Release the current media
            except Exception as e:
                # Log the exception if necessary
                print(f"Error stopping/releasing media player: {e}")

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
    def onRenderSelected(self):
        """
        Render only the currently selected shots based on the user's choice of render mode.
        If multiple shots are selected, prompt the user to choose between 'Per Shot' or 'Per Workflow' rendering.
        """
        selected_items = self.listWidget.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Warning", "No shot selected to render.")
            return

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

    def openWorkflowEditor(self):
        from comfystudio.sdmodules.editor import WorkflowEditor
        editor = WorkflowEditor(self.settingsManager, parent=self)
        editor.exec()

    def createBasicParamWidget(self, param):
        ptype = param["type"]
        pval = param.get("value", None)
        if pval is None and param.get("expression"):
            try:
                pval = eval(param["expression"], self.global_vars)
            except Exception as e:
                logging.error(f"Error evaluating expression '{param['expression']}' for param '{param['name']}': {e}")
                pval = 0  # fallback
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
        current_wf_selection = self.workflowListWidget.currentRow()
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
            if current_wf_selection is not None and 0 <= current_wf_selection < self.workflowListWidget.count():
                self.workflowListWidget.setCurrentRow(current_wf_selection)
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
                self.lastSelectedWorkflowIndex[self.currentShotIndex] = wfIndex
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
        currentItem = self.workflowListWidget.currentItem()
        paramType = param.get("type", "string")
        if paramType == "string":
            setPrevImage = menu.addAction("Set Param to Previous Workflow's Image")
            setPrevVideo = menu.addAction("Set Param to Previous Workflow's Video")
            clearDynOverride = menu.addAction("Clear Dynamic Override")
            setAllSelectedShotsAction = menu.addAction("Set All SELECTED Shots (this param)")
            setAllShotsAction = menu.addAction("Set ALL Shots (this param)")
            editDynamicParam = menu.addAction("Edit as Dynamic Parameter")
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
            elif chosen == setAllSelectedShotsAction:
                self.setParamValueInShots(param, onlySelected=True, item=currentItem)
            elif chosen == setAllShotsAction:
                self.setParamValueInShots(param, onlySelected=False, item=currentItem)
            elif chosen == editDynamicParam:
                # Create a DynamicParam from the existing param dictionary.
                dyn_param = DynamicParam(
                    name=param.get("name", ""),
                    param_type=param.get("type", "string"),
                    value=param.get("value", ""),
                    expression=param.get("expression", ""),
                    global_var=param.get("global_var", "")
                )
                editor = DynamicParamEditor(dyn_param, self.global_vars, self)
                if editor.exec() == QDialog.DialogCode.Accepted:
                    # Save the dynamic settings back into the parameter dictionary.
                    param["value"] = dyn_param.value
                    param["expression"] = dyn_param.expression
                    param["global_var"] = dyn_param.global_var
                    QMessageBox.information(self, "Info", "Dynamic parameter updated.")
        else:
            setAllSelectedShotsAction = menu.addAction("Set All SELECTED Shots (this param)")
            setAllShotsAction = menu.addAction("Set ALL Shots (this param)")
            editDynamicParam = menu.addAction("Edit as Dynamic Parameter")
            chosen = menu.exec(QCursor.pos())  # or mapToGlobal(pos) if needed
            if chosen == setAllSelectedShotsAction:
                self.setParamValueInShots(param, onlySelected=True, item=currentItem)
            elif chosen == setAllShotsAction:
                self.setParamValueInShots(param, onlySelected=False, item=currentItem)
            elif chosen == editDynamicParam:
                dyn_param = DynamicParam(
                    name=param.get("name", ""),
                    param_type=param.get("type", "string"),
                    value=param.get("value", ""),
                    expression=param.get("expression", ""),
                    global_var=param.get("global_var", "")
                )
                editor = DynamicParamEditor(dyn_param, self.global_vars, self)
                if editor.exec() == QDialog.DialogCode.Accepted:
                    param["value"] = dyn_param.value
                    param["expression"] = dyn_param.expression
                    param["global_var"] = dyn_param.global_var
                    QMessageBox.information(self, "Info", "Dynamic parameter updated.")

        # After making changes, re-fill the workflow item to show updated text
        # if the user re-opens the workflow item
        # For immediate refresh, you can re-call onWorkflowItemClicked on the current item:

        if currentItem:
            self.onWorkflowItemClicked(currentItem)

    def setParamValueInShots(self, param: dict, onlySelected: bool, item):
        """
        Copies this param's current value to the same-named parameter in either:
          - all selected shots, or
          - all shots

        If 'param' is a shot param, it matches shot params.
        If 'param' is a workflow param, it matches workflow params with the same name and workflow path.

        Args:
            param (dict): The parameter dictionary containing at least 'name', 'value', and optionally 'workflow_path'.
            onlySelected (bool): If True, apply changes only to selected shots; otherwise, apply to all shots.
        """
        # 1) Determine if it's a shot-level or workflow-level param
        is_shot_param = True
        if "nodeIDs" in param:
            # Workflow params typically have 'nodeIDs'
            is_shot_param = False

        # 2) The value to replicate
        new_value = param.get("value", "")

        # 3) Determine which shots to update
        shot_indices_to_update = []
        if onlySelected:
            selected_items = self.listWidget.selectedItems()
            for it in selected_items:
                idx = it.data(Qt.ItemDataRole.UserRole)
                if isinstance(idx, int) and 0 <= idx < len(self.shots):
                    shot_indices_to_update.append(idx)
        else:
            # All shots in the project
            shot_indices_to_update = list(range(len(self.shots)))

        # 4) Get the parameter name to match
        param_name = param.get("name", "")

        # 4b) Get the workflow path if it's a workflow param
        workflow: WorkflowAssignment = item.data(Qt.ItemDataRole.UserRole)
        if not workflow:
            return
        workflow_path = workflow.path

        # 5) Iterate through each shot and apply the parameter changes
        for sidx in shot_indices_to_update:
            shot = self.shots[sidx]

            if is_shot_param:
                # For shot-level param, update matching shot params
                for sp in shot.params:
                    if sp["name"] == param_name:
                        sp["value"] = new_value
                # Refresh the shot's parameter list in the UI
                self.refreshParamsList(shot)

            else:
                # For workflow-level param, update only in the specified workflow
                if not workflow_path:
                    logging.warning(
                        f"Workflow path not provided for parameter '{param_name}'. Skipping shot index {sidx}.")
                    continue  # Cannot determine which workflow to update without the path

                # Find the workflow with the matching path
                matching_workflows = [wf for wf in shot.workflows if wf.path == workflow_path]
                if not matching_workflows:
                    logging.warning(f"No matching workflow found for path '{workflow_path}' in shot index {sidx}.")
                    continue  # No matching workflow found

                for wf in matching_workflows:
                    if "params" not in wf.parameters:
                        continue
                    for p in wf.parameters["params"]:
                        if p["name"] == param_name:
                            p["value"] = new_value
                    # Save changes and refresh the workflow's parameter list in the UI
                    self.saveCurrentWorkflowParamsForShot(wf)

        # 6) Inform the user of the changes
        target_shots = len(shot_indices_to_update)
        scope = "selected" if onlySelected else "all"
        QMessageBox.information(
            self,
            "Info",
            f"Set parameter '{param_name}' to '{new_value}' in {target_shots} {scope} shot(s)."
        )

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
        """
        Starts the next render task based on the current render mode.
        """
        if not self.renderQueue:
            self.shotInProgress = -1
            self.workflowIndexInProgress = -1
            self.statusMessage.setText("Render queue is empty.")
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
        print("[DEBUG] Final workflow JSON structure before sending:")
        for k, v in workflow_json.items():
            print("       Node ID:", k)
            print("               ", v)

        # Start
        self.statusMessage.setText(f"Rendering {shot.name} - Workflow {workflowIndex + 1}/{len(shot.workflows)} ...")
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

        self.updateWindowsMenuTexts()
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
        signature = hashlib.md5(signature_str.encode("utf-8")).hexdigest()

        # Debugging: Log the signature generation
        logging.debug(f"Computed {'Video' if isVideo else 'Still'} Signature: {signature} for shot '{shot.name}'")

        return signature

    def newProject(self):
        self.shots.clear()
        self.updateList()
        self.currentShotIndex = -1
        self.clearDock()
        self.statusMessage.setText("New project created.")

    def openProject(self):
        filePath, _ = QFileDialog.getOpenFileName(
            self,
            self.localization.translate("dialog_open_project_title", default="Open Project"),
            "",
            "JSON Files (*.json);;All Files (*)"
        )
        if filePath:
            try:
                with open(filePath, "r") as f:
                    project_data = json.load(f)
                shots_data = project_data.get("shots", [])
                self.shots = [Shot.from_dict(shot_dict) for shot_dict in shots_data]
                self.updateList()
                self.currentFilePath = filePath
                self.statusMessage.setText(
                    f"{self.localization.translate('status_loaded_from', default='Project loaded from')} {filePath}")
                self.fillDock()
                self.addToRecents(filePath)
                self.setProjectModified(False)
            except Exception as e:
                QMessageBox.warning(self, self.localization.translate("dialog_error_title", default="Error"),
                                    self.localization.translate("error_failed_to_load_project",
                                                                default=f"Failed to load project: {e}"))


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
            self.statusMessage.setText("ComfyUI started.")
            self.appendLog("ComfyUI process started.")
        else:
            QMessageBox.warning(self, "Error", "Comfy paths not set in settings.")
    def onComfyFinishedRunning(self):
        """
        Handles the completion of the ComfyUI process.
        """
        self.comfy_running = False
        self.statusMessage.setText("ComfyUI stopped.")
        self.appendLog("ComfyUI process has stopped.")


    def setup_custom_nodes(self):
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
        self.custom_nodes_worker.finished.connect(self.custom_nodes_worker.deleteLater)
        self.custom_nodes_thread.finished.connect(self.custom_nodes_thread.deleteLater)
        self.custom_nodes_worker.finished.connect(lambda: QMessageBox.information(self, "Info", "Custom nodes setup completed."))

        # Start the thread
        self.custom_nodes_thread.start()

        # Log the initiation
        self.appendLog("Starting custom nodes setup...")

    def read_stream(self, stream):
        try:
            for line in iter(stream.readline, ''):
                if line:
                    self.logStream.write(line)
            stream.close()
        except Exception as e:
            self.logStream.write(f"Error reading stream: {e}")

    def stopComfy(self):
        """
        Signals the ComfyWorker to terminate the ComfyUI process.
        Cleans up the thread and worker.
        """
        if self.comfy_running and self.comfy_worker:
            try:
                self.comfy_worker.stop()
                self.comfy_running = False
                self.statusMessage.setText("Stopping ComfyUI...")
                self.appendLog("Stopping ComfyUI process...")
                # The worker's 'finished' signal will handle further cleanup
            except Exception as e:
                self.statusMessage.setText(str(e))
                self.appendLog(repr(e))
        else:
            QMessageBox.information(self, "Info", "ComfyUI is not running.")

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

    def restoreWindowState(self):
        geometry_str = self.settingsManager.get("mainwindow_geometry", "")
        if geometry_str:
            self.restoreGeometry(QtCore.QByteArray.fromBase64(geometry_str.encode("utf-8")))
        state_str = self.settingsManager.get("mainwindow_state", "")
        if state_str:
            self.restoreState(QtCore.QByteArray.fromBase64(state_str.encode("utf-8")))

    def saveWindowState(self):
        geometry_b64 = self.saveGeometry().toBase64().data().decode("utf-8")
        self.settingsManager.set("mainwindow_geometry", geometry_b64)

        state_b64 = self.saveState().toBase64().data().decode("utf-8")
        self.settingsManager.set("mainwindow_state", state_b64)

        self.settingsManager.save()

    def cleanUp(self):
        self.settingsManager.save()
        self.stopComfy()

    def isProjectModified(self):
        # Implement logic to check if the project has been modified.
        # This could involve setting a flag whenever shots or workflows are changed.
        return getattr(self, '_project_modified', False)

    def setProjectModified(self, modified=True):
        self._project_modified = modified

    def isProjectSaved(self):
        # Check if currentFilePath is set and the project is not modified
        return hasattr(self, 'currentFilePath') and self.currentFilePath and not self.isProjectModified()

    def keyPressEvent(self, event):
        print("I Work INstead")


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