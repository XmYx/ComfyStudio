#!/usr/bin/env python
import copy
import json
import logging
import os
import sys

from PyQt6.QtCore import QUrl, QTimer
from PyQt6.QtGui import QPixmap, QIcon
from PyQt6.QtWidgets import QSpinBox
from qtpy import QtCore
from qtpy.QtCore import (
    Qt,
    QObject,
    Signal,
    Slot
)
from qtpy.QtGui import (
    QAction
)
from qtpy.QtWidgets import (
    QTextEdit,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QListWidgetItem,
    QFormLayout,
    QDockWidget,
    QPushButton,
    QLabel,
    QDialog,
    QComboBox,
    QMessageBox,
    QCheckBox,
    QAbstractItemView,
    QListWidget,
    QGroupBox,
    QScrollArea,
    QMenu,
    QApplication,
    QSplitter
)

from comfystudio.sdmodules.aboutdialog import AboutDialog
from comfystudio.sdmodules.core.base import ComfyStudioBase
from comfystudio.sdmodules.cs_datastruts import Shot, WorkflowAssignment, ensure_parameters_dict
from comfystudio.sdmodules.help import HelpWindow
from comfystudio.sdmodules.model_manager import ModelManagerWindow
from comfystudio.sdmodules.node_visualizer import WorkflowVisualizer
from comfystudio.sdmodules.settings import SettingsDialog


class EmittingStream(QObject):
    text_written = Signal(str)

    def write(self, text):
        self.text_written.emit(str(text))

    def flush(self):
        pass

class ComfyStudioUI(ComfyStudioBase, QMainWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.logStream = EmittingStream()
        self.logStream.text_written.connect(self.appendLog)

    def toggleHiddenParams(self):
        self.showHiddenParams = not self.showHiddenParams
        item = self.workflowListWidget.currentItem()
        if item:
            self.onWorkflowItemClicked(item)
        if self.currentShotIndex != -1:
            self.refreshParamsList(self.shots[self.currentShotIndex])

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
        self.toggleTerminalAct.triggered.connect(self.status_docks["terminalDock"].setVisible)
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
            self.setCentralWidget(self.webBrowserDock)
            # Retrieve the 'comfy_ip' URL from settings
            comfy_ip = self.settingsManager.get("comfy_ip", "http://127.0.0.1:8188")

            # Load the URL in the WebBrowser view
            self.webBrowserView.setUrl(QUrl(comfy_ip))
            # Add the WebBrowser dock to the same area as Shot Details and tabify
            # self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.webBrowserDock)
            # self.tabifyDockWidget(self.dock, self.webBrowserDock)

            # Initially hide the WebBrowser dock
            # self.webBrowserDock.hide()
        self.updateWindowsMenuTexts()

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

    def create_dynamic_toolbar(self, toolbar_config):
        """
        Dynamically creates a toolbar from a configuration dict.

        toolbar_config: dict in the following format:
          {
              "Toolbar Title": {
                  "objectName": "main_toolbar",  # optional object name for the toolbar
                  "actions": [
                      {
                          "name": "addShotBtn",       # unique key to reference the action
                          "text": "Add Shot",
                          "trigger": self.addShot      # function to call on trigger
                      },
                      {
                          "name": "renderSelectedBtn",
                          "text": "Render Selected",
                          "trigger": self.onRenderSelected
                      },
                      {
                          "name": "renderAllBtn",
                          "text": "Render All",
                          "trigger": self.onRenderAll
                      },
                      {
                          "name": "stopRenderingBtn",
                          "text": "Stop Rendering",
                          "trigger": self.stopRendering
                      },
                      {
                          "name": "startComfyBtn",
                          "text": "Start Comfy",
                          "trigger": self.startComfy
                      },
                      {
                          "name": "stopComfyBtn",
                          "text": "Stop Comfy",
                          "trigger": self.stopComfy
                      }
                  ]
              }
          }
        """
        # Get the toolbar title and config; assume only one toolbar is configured
        toolbar_title, config = list(toolbar_config.items())[0]

        # Create and configure the toolbar
        translated_title = self.localization.translate("toolbar_label", default=toolbar_title)
        self.toolbar = self.addToolBar(translated_title)
        self.toolbar.setObjectName(config.get("objectName", "toolbar"))

        # Optionally, store the actions in a dictionary for later reference.
        if not hasattr(self, "toolbar_actions"):
            self.toolbar_actions = {}

        # Add actions from configuration
        for action_conf in config.get("actions", []):
            action = QAction(self)
            action.setText(action_conf.get("text", ""))
            if "trigger" in action_conf and callable(action_conf["trigger"]):
                action.triggered.connect(action_conf["trigger"])
            self.toolbar.addAction(action)
            if "name" in action_conf:
                self.toolbar_actions[action_conf["name"]] = action

    def create_dynamic_status_bar(self, status_config):
        """
        Dynamically creates the status bar (including permanent widgets and dock widgets)
        from a configuration dict.

        The configuration dictionary format is as follows:

        {
            "widgets": [
                {
                    "type": "label" | "button",
                    "name": "widgetKey",          # key to reference the widget later
                    "text": "Display Text",         # initial text
                    "stretch": int,                 # optional stretch factor for addPermanentWidget
                    "trigger": callable             # for buttons only: function to call when clicked
                },
                ...
            ],
            "dockWidgets": [
                {
                    "name": "dockKey",            # key to reference the dock later
                    "objectName": "dock_object",    # optional object name
                    "title": "Dock Title",          # title for the dock window
                    "allowedAreas": Qt.DockWidgetArea.AllDockWidgetAreas,  # allowed docking areas
                    "defaultArea": Qt.DockWidgetArea.BottomDockWidgetArea,   # default docking area
                    "hidden": bool,                 # if True, the dock is hidden initially
                    "widget": {
                        "type": "textEdit",         # type of widget inside the dock
                        "name": "innerWidgetKey",   # key for reference (if needed)
                        "readOnly": bool            # True if the text edit should be read-only
                    }
                },
                ...
            ]
        }
        """
        # Get the status bar and clear any message.
        self.status = self.statusBar()
        self.status.clearMessage()

        # Optionally, store references to created widgets and docks
        self.status_widgets = {}
        self.status_docks = {}

        # Process permanent widgets for the status bar.
        for widget_conf in status_config.get("widgets", []):
            widget_type = widget_conf.get("type")
            widget_name = widget_conf.get("name")
            widget_text = widget_conf.get("text", "")
            widget = None

            if widget_type == "label":
                widget = QLabel(self)
                widget.setText(widget_text)
            elif widget_type == "button":
                widget = QPushButton(self)
                widget.setText(widget_text)
                if "trigger" in widget_conf and callable(widget_conf["trigger"]):
                    widget.clicked.connect(widget_conf["trigger"])
            else:
                continue  # Unsupported widget type; skip it

            # Save the widget reference for later use.
            if widget_name:
                self.status_widgets[widget_name] = widget

            # Add the widget to the status bar. Use stretch if specified.
            stretch = widget_conf.get("stretch")
            if stretch is not None:
                self.status.addPermanentWidget(widget, stretch)
            else:
                self.status.addPermanentWidget(widget)

        # Process dock widgets (such as the terminal dock).
        for dock_conf in status_config.get("dockWidgets", []):
            dock = QDockWidget(self)
            dock_object_name = dock_conf.get("objectName", "")
            if dock_object_name:
                dock.setObjectName(dock_object_name)
            dock_title = dock_conf.get("title", "")
            dock.setWindowTitle(dock_title)

            # Set allowed docking areas if provided.
            if "allowedAreas" in dock_conf:
                dock.setAllowedAreas(dock_conf["allowedAreas"])

            # Create the inner widget for the dock.
            inner_conf = dock_conf.get("widget", {})
            inner_widget = None
            if inner_conf.get("type") == "textEdit":
                inner_widget = QTextEdit(self)
                if inner_conf.get("readOnly", False):
                    inner_widget.setReadOnly(True)
            # (Add support for additional inner widget types as needed.)

            if inner_widget:
                dock.setWidget(inner_widget)
                # Optionally, store the inner widget reference.
                inner_name = inner_conf.get("name")
                if inner_name:
                    self.status_widgets[inner_name] = inner_widget

            # Add the dock widget to the main window.
            default_area = dock_conf.get("defaultArea", Qt.DockWidgetArea.BottomDockWidgetArea)
            self.addDockWidget(default_area, dock)
            if dock_conf.get("hidden", False):
                dock.hide()

            # Save the dock reference using the provided key.
            dock_key = dock_conf.get("name", dock_object_name)
            if dock_key:
                self.status_docks[dock_key] = dock

    def appendLog(self, text):
        self.status_widgets["terminalTextEdit"].append(text)
        self.status_widgets["logLabel"].setText(text)
        # self.terminalTextEdit.append(text)
        # self.logLabel.setText(text)

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

        # Add "Current Frame" spin box at the top.
        self.currentFrameSpin = QSpinBox()
        self.currentFrameSpin.setMinimum(0)
        self.currentFrameSpin.setValue(0)
        self.currentFrameSpin.setToolTip("Select the current frame to view/edit its parameters")
        self.currentFrameSpin.valueChanged.connect(self.onCurrentFrameChanged)
        self.workflowParamsLayout.addRow("Current Frame:", self.currentFrameSpin)

        # Add "Run Count" spin box
        self.runCountSpin = QSpinBox()
        self.runCountSpin.setMinimum(1)
        self.runCountSpin.setValue(1)
        self.runCountSpin.setToolTip("Set the number of runs (frames) for this workflow")
        self.runCountSpin.valueChanged.connect(self.onRunCountChanged)
        self.workflowParamsLayout.addRow("Run Count:", self.runCountSpin)

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

    def onCurrentFrameChanged(self, frame_index):
        """
        When the user changes the current frame using the spin box,
        load the parameters for that frame (if available) into the parameters dock.
        """
        current_item = self.workflowListWidget.currentItem()
        if not current_item:
            return
        workflow = current_item.data(Qt.ItemDataRole.UserRole)
        if hasattr(workflow, "frame_params") and frame_index in workflow.frame_params:
            frame_params = workflow.frame_params[frame_index]
            self.refreshWorkflowParamsForFrame(workflow, frame_params)
        else:
            # Optionally clear or leave parameters unchanged.
            pass

    def refreshWorkflowParamsForFrame(self, workflow, frame_params):
        """
        Re-populate the workflow parameters dock widget with the parameters for the given frame.
        This function clears all parameter rows (except the fixed "Current Frame" and "Run Count" controls)
        and then re-adds rows for each parameter defined in frame_params["params"].

        Args:
            workflow: The WorkflowAssignment object.
            frame_params: A dictionary containing the parameter snapshot for the current frame,
                          expected to have a key "params" with a list of parameter dicts.
        """
        # Preserve the fixed controls at the top (assumed to be present)
        fixed_widgets = []
        # Assuming the first two rows are "Current Frame" and "Run Count"
        row_count = self.workflowParamsLayout.rowCount()
        # Save the first two rows so we can re-add them after clearing.
        if row_count >= 2:
            # Retrieve the labels and widgets from the first two rows
            fixed_widgets.append(self.workflowParamsLayout.itemAt(0, self.workflowParamsLayout.LabelRole).widget())
            fixed_widgets.append(self.workflowParamsLayout.itemAt(0, self.workflowParamsLayout.FieldRole).widget())
            fixed_widgets.append(self.workflowParamsLayout.itemAt(1, self.workflowParamsLayout.LabelRole).widget())
            fixed_widgets.append(self.workflowParamsLayout.itemAt(1, self.workflowParamsLayout.FieldRole).widget())

        # Clear the layout entirely.
        while self.workflowParamsLayout.rowCount() > 0:
            self.workflowParamsLayout.removeRow(0)

        # Re-add the fixed controls.
        if fixed_widgets:
            self.workflowParamsLayout.addRow("Current Frame:", fixed_widgets[1])
            self.workflowParamsLayout.addRow("Run Count:", fixed_widgets[3])

        # Now add each parameter for the current frame.
        params_list = frame_params.get("params", [])
        for param in params_list:
            display_name = param.get("displayName", param.get("name", ""))
            # createBasicParamWidget is assumed to be a helper that returns a QWidget for the parameter.
            param_widget = self.createBasicParamWidget(param)
            self.workflowParamsLayout.addRow(display_name, param_widget)
    def onRunCountChanged(self, value):
        """
        Update the workflow's 'run_count' parameter when the user changes the run count.
        """
        current_item = self.workflowListWidget.currentItem()
        if not current_item:
            return
        workflow = current_item.data(Qt.ItemDataRole.UserRole)
        workflow.parameters["run_count"] = value
    def initParamsTab(self):
        self.paramsScroll = QScrollArea()
        self.paramsScroll.setWidgetResizable(True)

        self.paramsContainer = QWidget()
        self.paramsContainerLayout = QFormLayout(self.paramsContainer)
        self.paramsScroll.setWidget(self.paramsContainer)

        self.paramsListWidget = QListWidget()
        self.paramsListWidget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.paramsListWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)



        self.paramsContainerLayout.addRow("Parameters:", self.paramsListWidget)

        paramsButtonsLayout = QHBoxLayout()
        self.addParamBtn = QPushButton("Add Param")
        self.removeParamBtn = QPushButton("Remove Param")
        paramsButtonsLayout.addWidget(self.addParamBtn)
        paramsButtonsLayout.addWidget(self.removeParamBtn)
        self.paramsContainerLayout.addRow(paramsButtonsLayout)
        self.paramsLayout.addWidget(self.paramsScroll)

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
        self.status_docks["terminalDock"].setWindowTitle(self.localization.translate("terminal_output", default="Terminal Output"))

        rtl_languages = ['he', 'ar', 'fa', 'ur']  # Add other RTL language codes as needed
        current_language = self.localization.get_language()
        is_rtl = current_language in rtl_languages
        if is_rtl:
            QApplication.instance().setLayoutDirection(Qt.RightToLeft)
        else:
            QApplication.instance().setLayoutDirection(Qt.LeftToRight)
    def updateMenuBarTexts(self):

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

    def openProjectFromPath(self, filePath):
        if os.path.exists(filePath):
            try:
                with open(filePath, "r") as f:
                    project_data = json.load(f)
                shots_data = project_data.get("shots", [])
                self.shots = [Shot.from_dict(shot_dict) for shot_dict in shots_data]
                self.updateList()
                self.currentFilePath = filePath
                self.status_widgets["statusMessage"].setText(
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
    def fillDock(self):
        print("fillDock was called")
        # if self.currentShotIndex is None or self.currentShotIndex >= len(self.shots):
        #     self.clearDock()
        #     return


        shot = self.shots[self.currentShotIndex]
        self.refreshWorkflowsList(shot)
        self.refreshParamsList(shot)

    def clearDock(self):
        print("clearDock was called")

        self.workflowListWidget.clear()
        while self.workflowParamsLayout.rowCount() > 0:
            self.workflowParamsLayout.removeRow(0)
        self.workflowParamsGroup.setEnabled(False)
        self.paramsListWidget.clear()

    def createWorkflowVersionDropdown(self, workflow):
        combo = QComboBox()
        combo.addItem("Select version")  # Placeholder item
        # Populate with versions if any exist.
        for idx, version in enumerate(workflow.get("versions", [])):
            label = f"Version {idx + 1}"
            combo.addItem(label, version)
        # If the workflow has a stored selection, restore it.
        if hasattr(workflow, "selected_version_index"):
            combo.setCurrentIndex(workflow.selected_version_index)
        else:
            combo.setCurrentIndex(0)
        combo.currentIndexChanged.connect(
            lambda idx, wf=workflow, cb=combo: self.onWorkflowVersionChanged(wf, cb)
        )
        return combo

    def onWorkflowVersionChanged(self, workflow, combo):
        idx = combo.currentIndex()
        # Store the current selection
        workflow.selected_version_index = idx
        if idx <= 0:
            return

        version = combo.itemData(idx)
        if not version:
            return

        workflow.parameters = copy.deepcopy(ensure_parameters_dict(version.get("params", {})))

        shot = self.getShotForWorkflow(workflow)
        if shot:
            if version.get("is_video"):
                shot.videoPath = version.get("output", "")
            else:
                shot.stillPath = version.get("output", "")
            self.refreshWorkflowsList(shot)
            self.refreshParamsList(shot)
            try:
                wf_index = shot.workflows.index(workflow)
            except ValueError:
                wf_index = 0
            self.fillDock()
            self.previewDock.showMediaForShotWorkflow(shot, wf_index)

        # Schedule asynchronous update of the UI without rebuilding the dropdown unnecessarily.
        current_item = self.workflowListWidget.currentItem()
        if current_item:
            QTimer.singleShot(0, lambda: self.onWorkflowItemClicked(current_item))

        workflow.version_dropdown = combo

    # def onWorkflowVersionChanged(self, workflow, combo):
    #     idx = combo.currentIndex()
    #     if idx <= 0:
    #         # First item is a placeholder.
    #         return
    #
    #     # Retrieve the selected version snapshot (stored in userData)
    #     version = combo.itemData(idx)
    #     if not version:
    #         return
    #
    #     # Update workflow parameters from the version snapshot.
    #     workflow.parameters = copy.deepcopy(version["params"])
    #
    #     # Also update the shot’s output (e.g., stillPath or videoPath) based on the version.
    #     shot = self.getShotForWorkflow(workflow)  # implement this helper to return the shot containing 'workflow'
    #     if shot:
    #         if version.get("is_video"):
    #             shot.videoPath = version["output"]
    #         else:
    #             shot.stillPath = version["output"]
    #
    #         # Optionally refresh other parts of your UI (parameters, etc.)
    #         self.refreshWorkflowsList(shot)
    #         self.refreshParamsList(shot)
    #
    #         # Now determine the workflow’s index and refresh the preview.
    #         try:
    #             wf_index = shot.workflows.index(workflow)
    #         except ValueError:
    #             wf_index = 0  # Fallback if not found
    #
    #         # Call the preview dock refresh function to display the new version.
    #         self.fillDock()
    #         self.previewDock.showMediaForShotWorkflow(shot, wf_index)

    def getShotForWorkflow(self, workflow: WorkflowAssignment):
        for shot in self.shots:
            if workflow in shot.workflows:
                return shot
        return None

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

    def refreshWorkflowsList(self, shot):
        current_wf_selection = self.workflowListWidget.currentRow()
        self.workflowListWidget.clear()
        if shot:
            for workflow in shot.workflows:
                rowWidget = QWidget()
                rowLayout = QHBoxLayout(rowWidget)
                rowLayout.setContentsMargins(0, 0, 0, 0)

                # 1. Enabled checkbox
                enableCheck = QCheckBox("Enabled")
                enableCheck.setChecked(workflow.enabled)
                enableCheck.setProperty("workflow", workflow)
                enableCheck.stateChanged.connect(self.onWorkflowEnabledChanged)
                rowLayout.addWidget(enableCheck)

                # 2. Workflow label (basename of the workflow file)
                label = QLabel(os.path.basename(workflow.path))
                rowLayout.addWidget(label)

                # 3. Run Count spin box
                runCountSpin = QSpinBox()
                runCountSpin.setMinimum(1)
                # Use the existing run count if present; default to 1 otherwise.
                run_count = int(workflow.parameters.get("run_count", 1))
                runCountSpin.setValue(run_count)
                runCountSpin.setToolTip("Set the number of runs (frames) for this workflow")
                # Update the workflow's parameters when changed.
                runCountSpin.valueChanged.connect(
                    lambda value, wf=workflow: wf.parameters.__setitem__("run_count", value)
                )
                rowLayout.addWidget(QLabel("Runs:"))
                rowLayout.addWidget(runCountSpin)

                # 4. Current Frame spin box
                currentFrameSpin = QSpinBox()
                currentFrameSpin.setMinimum(0)
                # Use an attribute 'current_frame' if available; default to 0.
                current_frame = getattr(workflow, "current_frame", 0)
                currentFrameSpin.setValue(current_frame)
                currentFrameSpin.setToolTip("Select the current frame for this workflow")
                currentFrameSpin.valueChanged.connect(
                    lambda value, wf=workflow: setattr(wf, "current_frame", value)
                )
                rowLayout.addWidget(QLabel("Frame:"))
                rowLayout.addWidget(currentFrameSpin)

                # 5. Visualize button
                visualizeBtn = QPushButton("Visualize")
                visualizeBtn.setProperty("workflow", workflow)
                visualizeBtn.clicked.connect(self.onVisualizeWorkflow)
                rowLayout.addWidget(visualizeBtn)

                # Create a list widget item and attach the row widget
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, workflow)
                item.setSizeHint(rowWidget.sizeHint())
                self.workflowListWidget.addItem(item)
                self.workflowListWidget.setItemWidget(item, rowWidget)
            if current_wf_selection is not None and 0 <= current_wf_selection < self.workflowListWidget.count():
                self.workflowListWidget.setCurrentRow(current_wf_selection)
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
    def showWorkflowVisualizer(self, workflow):
        try:
            with open(workflow.path, "r") as f:
                wf_json = json.load(f)
            dlg = WorkflowVisualizer(wf_json, self)
            dlg.exec()
        except:
            pass
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
        plugins_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "plugins")
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
                        print("Registered plugin: ", modulename)
                except Exception as e:
                    print(f"Error loading plugin {modulename}: {e}")
        sys.path.pop(0)

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

        if previous_selection is not None:
            self.listWidget.setCurrentRow(previous_selection)

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

    def getShotIcon(self, shot):
        if shot.get("stillPath") and os.path.exists(shot.get("stillPath")):
            base_pix = QPixmap(shot.get("stillPath"))
            if not base_pix.isNull():
                base_pix = base_pix.scaled(120, 90, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
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

    def toggleTerminalDock(self):
        if self.status_docks["terminalDock"].isVisible():
            self.status_docks["terminalDock"].hide()
        else:
            self.status_docks["terminalDock"].show()


    def showSettingsDialog(self):
        dialog = SettingsDialog(self.settingsManager, self.localization, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.loadWorkflows()
            if self.currentShotIndex != -1:
                self.fillDock()
            # selected_language = dialog.get_selected_language()
            self.localization.set_language(self.settingsManager.get("language"))
            # self.retranslateUi()

    def openModelManager(self):
        """
        Opens the Model Manager Window.
        """
        self.model_manager_window = ModelManagerWindow(parent=self, settings_manager=self.settingsManager)
        self.model_manager_window.exec()
    def openUserGuide(self):
        help_window = HelpWindow(self)
        help_window.exec()
    def openAboutDialog(self):
        about_dialog = AboutDialog(self)
        about_dialog.exec()
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
