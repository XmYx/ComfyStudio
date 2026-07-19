#!/usr/bin/env python
import copy
import json
import logging
import os
import sys

from qtpy.QtCore import QUrl, QTimer
from qtpy.QtGui import QPixmap, QIcon
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
from comfystudio.sdmodules.cs_datastruts import Shot, WorkflowAssignment
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

        # Action to toggle the Shot Workflow Graph dock (node editor)
        if hasattr(self, "shotNodeEditorDock"):
            self.toggleShotNodeEditorAct = QAction(
                self.localization.translate("menu_toggle_shot_node_editor",
                                            default="Toggle Workflow Graph"),
                self,
                checkable=True
            )
            self.toggleShotNodeEditorAct.setChecked(self.shotNodeEditorDock.isVisible())
            self.toggleShotNodeEditorAct.triggered.connect(self.shotNodeEditorDock.setVisible)
            # Keep the check state honest when the dock is closed directly.
            self.shotNodeEditorDock.visibilityChanged.connect(self.toggleShotNodeEditorAct.setChecked)
            self.windowsMenu.addAction(self.toggleShotNodeEditorAct)

        # Action to toggle Web Browser dock (only when QtWebEngine is available)
        try:
            from qtpy.QtWebEngineWidgets import QWebEngineView
            web_engine_available = True
        except Exception:
            web_engine_available = False
            logging.info("QtWebEngine not available; Web Browser dock disabled.")

        if web_engine_available:
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
        if web_engine_available and not hasattr(self, 'webBrowserDock'):
            # Initialize the WebBrowser dock
            self.webBrowserDock = QDockWidget(
                self.localization.translate("dock_web_browser", default="Web Browser"),
                self
            )
            self.webBrowserDock.setObjectName("web_browser_dock")
            self.webBrowserDock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)

            self.webBrowserView = QWebEngineView()
            self.webBrowserDock.setWidget(self.webBrowserView)
            # The ComfyUI page is loaded lazily (when the dock is shown or the
            # server becomes reachable) — loading it here, before ComfyUI is
            # running, would leave a stale connection-error page in the view.
            self._webBrowserLoadOk = False
            # Add the WebBrowser dock to the same area as Shot Details and tabify
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.webBrowserDock)
            self.webBrowserDock.hide()

            # Bridge so the embedded ComfyUI can save workflows back into ComfyStudio.
            from comfystudio.sdmodules.workflow_edit_server import WorkflowEditBridge
            self.workflowEditBridge = WorkflowEditBridge(self)
            self.workflowEditBridge.workflowSaved.connect(self.onWorkflowFileSaved)
            self.webBrowserView.loadFinished.connect(self._injectComfySaveHook)
        self.updateWindowsMenuTexts()

    def _injectComfySaveHook(self, ok=True):
        """
        Installs a Ctrl+S / Cmd+S hook in the embedded ComfyUI page that posts the
        current graph (API format) to ComfyStudio's local save endpoint instead of
        triggering the browser/ComfyUI download.
        """
        # Track whether the last page load succeeded so toggling the dock (or
        # ComfyUI becoming reachable) knows to retry a failed load.
        self._webBrowserLoadOk = bool(ok)
        if not ok or not hasattr(self, "workflowEditBridge"):
            return
        js = """
(function() {
    if (window.__comfyStudioSaveHook) return;
    window.__comfyStudioSaveHook = true;
    const PORT = %d;
    async function comfyStudioSave() {
        try {
            const app = window.app;
            if (!app || typeof app.graphToPrompt !== 'function') {
                console.warn('[ComfyStudio] ComfyUI app not ready, cannot save');
                return;
            }
            const p = await app.graphToPrompt();
            const resp = await fetch('http://127.0.0.1:' + PORT + '/save_workflow', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(p.output)
            });
            const result = await resp.json().catch(() => ({}));
            if (resp.ok) {
                console.log('[ComfyStudio] Workflow saved to', result.path);
            } else {
                console.warn('[ComfyStudio] Save failed:', result.error || resp.status);
            }
        } catch (e) {
            console.warn('[ComfyStudio] Save error:', e);
        }
    }
    window.comfyStudioSave = comfyStudioSave;
    window.addEventListener('keydown', function(ev) {
        if ((ev.ctrlKey || ev.metaKey) && !ev.shiftKey && !ev.altKey &&
                (ev.key === 's' || ev.key === 'S')) {
            ev.preventDefault();
            ev.stopPropagation();
            comfyStudioSave();
        }
    }, true);
    console.log('[ComfyStudio] Save hook installed (Ctrl/Cmd+S -> ComfyStudio, port ' + PORT + ')');
})();
""" % self.workflowEditBridge.port
        self.webBrowserView.page().runJavaScript(js)

    def loadWorkflowInBrowser(self, workflow):
        """
        Loads the given workflow assignment's JSON into the embedded ComfyUI
        graph editor so it can be edited (and saved back with Ctrl+S).
        """
        if not hasattr(self, "webBrowserView"):
            QMessageBox.information(
                self, "Web Browser Unavailable",
                "The embedded browser requires PyQt6-WebEngine, which is not installed."
            )
            return False
        try:
            wf_json = self.loadWorkflowJson(workflow.path)
        except ValueError as e:
            QMessageBox.warning(self, "Error", str(e))
            return False

        if hasattr(self, "workflowEditBridge"):
            self.workflowEditBridge.set_target(workflow.path)
        self._watchWorkflowFile(workflow.path)

        # Make sure the ComfyUI page itself is loaded (retry failed loads too).
        comfy_ip = self.getComfyApiUrl()
        current_url = self.webBrowserView.url().toString()
        if (not current_url or current_url == "about:blank"
                or not getattr(self, "_webBrowserLoadOk", False)):
            self.webBrowserView.setUrl(QUrl(comfy_ip))

        name = os.path.basename(workflow.path)
        js = """
(function() {
    const data = %s;
    const name = %s;
    function tryLoad() {
        try {
            const app = window.app;
            if (app && typeof app.loadApiJson === 'function') {
                app.loadApiJson(data, name);
                console.log('[ComfyStudio] Loaded workflow', name);
                return true;
            }
            if (app && typeof app.loadGraphData === 'function' && data.nodes) {
                app.loadGraphData(data);
                return true;
            }
        } catch (e) {
            console.warn('[ComfyStudio] Failed to load workflow:', e);
            return true;  // stop retrying on a hard error
        }
        return false;
    }
    if (!tryLoad()) {
        let attempts = 0;
        const timer = setInterval(function() {
            if (tryLoad() || ++attempts > 50) clearInterval(timer);
        }, 200);
    }
})();
""" % (json.dumps(wf_json), json.dumps(name))
        self.webBrowserView.page().runJavaScript(js)
        self.status_widgets["statusMessage"].setText(
            f"Opened '{name}' in ComfyUI editor (Ctrl+S there saves back to ComfyStudio)."
        )
        return True

    def onWorkflowFileSaved(self, path):
        """A workflow file was rewritten by the embedded ComfyUI editor."""
        self.status_widgets["statusMessage"].setText(
            f"Workflow saved from ComfyUI: {os.path.basename(path)}"
        )
        self.syncWorkflowFromFile(path)

    # --- Workflow file watching (external edits & embedded-editor saves) ---
    def _workflowFileWatcher(self):
        if not hasattr(self, "_workflowWatcherObj"):
            from qtpy.QtCore import QFileSystemWatcher
            self._workflowWatcherObj = QFileSystemWatcher(self)
            self._workflowWatcherObj.fileChanged.connect(self._onWorkflowFileChanged)
            # Library directories: new/removed workflow files (e.g. saved from
            # the embedded ComfyUI editor) refresh the workflow listing.
            self._workflowWatcherObj.directoryChanged.connect(self._onWorkflowDirChanged)
        return self._workflowWatcherObj

    def _watchWorkflowDir(self, directory):
        if directory and os.path.isdir(directory):
            watcher = self._workflowFileWatcher()
            if directory not in watcher.directories():
                watcher.addPath(directory)

    def _onWorkflowDirChanged(self, _path):
        # Debounce: a save often produces several change events.
        if getattr(self, "_libraryReloadPending", False):
            return
        self._libraryReloadPending = True

        def reload():
            self._libraryReloadPending = False
            self.loadWorkflows()

        QTimer.singleShot(400, reload)

    def _watchWorkflowFile(self, path):
        if path and os.path.isfile(path):
            watcher = self._workflowFileWatcher()
            if path not in watcher.files():
                watcher.addPath(path)

    def _onWorkflowFileChanged(self, path):
        # Debounce: editors often emit several change events per save, and some
        # replace the file (which drops it from the watcher, so re-add it).
        def resync():
            if os.path.isfile(path):
                self._watchWorkflowFile(path)
                self.syncWorkflowFromFile(path)
        QTimer.singleShot(250, resync)

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

        # Workflows are type-agnostic: one unified list (image + video library
        # directories combined); the rendered result type is detected at runtime.
        comboLayout = QHBoxLayout()
        self.workflowLabel = QLabel(
            self.localization.translate("label_workflow", default="Workflow:")
        )
        self.workflowCombo = QComboBox()
        self.workflowCombo.setToolTip(
            self.localization.translate("tooltip_select_workflow", default="Select a workflow to add")
        )
        self.addWorkflowBtn = QPushButton(
            self.localization.translate("button_add_workflow", default="Add")
        )
        self.addWorkflowBtn.setToolTip(
            self.localization.translate("tooltip_add_workflow",
                                        default="Add the selected workflow to the shot")
        )
        self.addWorkflowBtn.clicked.connect(self.addWorkflow)
        comboLayout.addWidget(self.workflowLabel)
        comboLayout.addWidget(self.workflowCombo)
        comboLayout.addWidget(self.addWorkflowBtn)
        groupLayout.addLayout(comboLayout)

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
    def updateWorkflowsTabTexts(self):
        """Retranslates the Workflows tab labels (unified workflow combo)."""
        self.workflowLabel.setText(
            self.localization.translate("label_workflow", default="Workflow:"))
        self.addWorkflowBtn.setText(
            self.localization.translate("button_add_workflow", default="Add"))
        self.workflowListLabel.setText(
            self.localization.translate("label_workflow_list", default="Assigned Workflows:"))
        self.removeWorkflowBtn.setText(
            self.localization.translate("button_remove_workflow", default="Remove Workflow"))
        self.toggleHiddenParamsBtn.setText(
            self.localization.translate("button_toggle_hidden_params", default="Show/Hide All Params"))

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

        if hasattr(self, "toggleShotNodeEditorAct"):
            self.toggleShotNodeEditorAct.setText(
                self.localization.translate("menu_toggle_shot_node_editor", default="Toggle Workflow Graph")
            )
            self.toggleShotNodeEditorAct.setToolTip(
                self.localization.translate("tooltip_toggle_shot_node_editor",
                                            default="Show or hide the shot workflow graph editor")
            )

        if hasattr(self, "toggleWebBrowserAct"):
            self.toggleWebBrowserAct.setText(
                self.localization.translate("menu_toggle_webbrowser", default="Toggle Web Browser")
            )
            self.toggleWebBrowserAct.setToolTip(
                self.localization.translate("tooltip_toggle_webbrowser", default="Show or hide the Web Browser dock")
            )

        # Update WebBrowser dock title
        if hasattr(self, "webBrowserDock"):
            self.webBrowserDock.setWindowTitle(
                self.localization.translate("dock_web_browser", default="Web Browser")
            )

    def openProjectFromPath(self, filePath):
        if os.path.exists(filePath):
            try:
                with open(filePath, "r") as f:
                    project_data = json.load(f)
                if not isinstance(project_data, dict):
                    raise ValueError("Project file is not a valid ComfyStudio project (expected a JSON object).")
                shots_data = project_data.get("shots", [])
                self.shots = [Shot.from_dict(shot_dict) for shot_dict in shots_data]
                # Convert legacy prev-result flags into shot graph connections.
                migrated_any = False
                for shot in self.shots:
                    migrated_any = self.migrateShotGraph(shot) or migrated_any
                self.currentShotIndex = 0 if self.shots else -1
                self.lastSelectedWorkflowIndex = {}
                # Watch every referenced workflow file for external edits.
                for shot in self.shots:
                    for wf in shot.workflows:
                        self._watchWorkflowFile(wf.path)
                self.updateList()
                if self.shots:
                    self.listWidget.setCurrentRow(0)
                self.currentFilePath = filePath
                # Restore the timeline layout if it was saved with the project.
                timeline_data = project_data.get("timeline")
                if hasattr(self, "listWidgetBase"):
                    try:
                        if timeline_data:
                            self.listWidgetBase.restoreTimeline(timeline_data)
                        else:
                            self.listWidgetBase.clearTimeline()
                    except Exception as e:
                        logging.warning(f"Failed to restore timeline: {e}")
                self.status_widgets["statusMessage"].setText(
                    f"{self.localization.translate('status_loaded_from', default='Project loaded from')} {filePath}")
                self.fillDock()
                self.addToRecents(filePath)
                # Migration created new connections worth saving; otherwise
                # a freshly opened project is unmodified.
                self.setProjectModified(migrated_any)
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
        if self.currentShotIndex is None or not (0 <= self.currentShotIndex < len(self.shots)):
            self.clearDock()
            return
        shot = self.shots[self.currentShotIndex]
        self.refreshWorkflowsList(shot)
        self.refreshParamsList(shot)
        self.refreshNodeEditor()

    def clearDock(self):
        print("clearDock was called")

        self.workflowListWidget.clear()
        while self.workflowParamsLayout.rowCount() > 0:
            self.workflowParamsLayout.removeRow(0)
        self.workflowParamsGroup.setEnabled(False)
        self.paramsListWidget.clear()
        self.refreshNodeEditor()

    def createWorkflowVersionDropdown(self, workflow):
        """
        Creates a QComboBox populated with version snapshots for a given workflow.
        The first entry is a placeholder, and then one entry per version.
        The previously selected version is restored (without re-triggering the
        version-changed handler while the dropdown is being rebuilt).
        """
        import datetime
        combo = QComboBox()
        combo.blockSignals(True)
        combo.addItem("Current (unsaved) parameters")  # Placeholder item

        versions = workflow.get("versions", []) or []
        for idx, version in enumerate(versions):
            label = f"Version {idx + 1}"
            ts = version.get("timestamp") if isinstance(version, dict) else None
            if ts:
                try:
                    label += datetime.datetime.fromtimestamp(ts).strftime(" (%Y-%m-%d %H:%M)")
                except Exception:
                    pass
            if isinstance(version, dict) and version.get("is_video"):
                label += " [video]"
            combo.addItem(label, version)  # store the version data in userData

        # Restore the previously selected version for this workflow.
        saved_idx = workflow.get("selected_version_index", 0) or 0
        if 0 <= saved_idx < combo.count():
            combo.setCurrentIndex(saved_idx)
        combo.blockSignals(False)

        # Connect the signal to restore the version when selected.
        combo.currentIndexChanged.connect(
            lambda idx, wf=workflow, cb=combo: self.onWorkflowVersionChanged(wf, cb)
        )
        return combo

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
    def onWorkflowVersionChanged(self, workflow, combo):
        idx = combo.currentIndex()
        # Store the current selection so it can be restored (and persisted with the project).
        workflow.selected_version_index = idx
        if idx <= 0:
            return

        version = combo.itemData(idx)
        if not version:
            return

        # Deep-copy the snapshot so subsequent edits don't mutate the stored version.
        workflow.parameters = copy.deepcopy(version.get("params", {}))

        shot = self.getShotForWorkflow(workflow)
        if shot:
            output = version.get("output", "")
            if output and os.path.exists(output):
                if version.get("is_video"):
                    shot.videoPath = output
                else:
                    shot.stillPath = output
            self.refreshWorkflowsList(shot)
            self.refreshParamsList(shot)
            try:
                wf_index = shot.workflows.index(workflow)
            except ValueError:
                wf_index = 0
            self.previewDock.showMediaForShotWorkflow(shot, wf_index)
        self.setProjectModified(True)

        # Schedule asynchronous update of the UI without rebuilding the dropdown unnecessarily.
        current_item = self.workflowListWidget.currentItem()
        if current_item:
            QTimer.singleShot(0, lambda: self.onWorkflowItemClicked(current_item))
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
    def workflowLibraryDirs(self):
        """Returns (image_dir, video_dir) of the workflow library (settings or bundled)."""
        # <pkg>/workflows (this file lives in <pkg>/sdmodules/core/)
        base_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "workflows")
        image_dir = self.settingsManager.get("comfy_image_workflows") or os.path.join(base_dir, "image")
        video_dir = self.settingsManager.get("comfy_video_workflows") or os.path.join(base_dir, "video")
        return image_dir, video_dir

    def _isApiWorkflowFile(self, path):
        """True when the file parses as an API-format (renderable) workflow."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return False
        if not isinstance(data, dict) or not data:
            return False
        if isinstance(data.get("nodes"), list):  # UI-format export
            return False
        return any(isinstance(nd, dict) and isinstance(nd.get("inputs", {}), dict)
                   for nd in data.values())

    def loadWorkflows(self):
        image_dir, video_dir = self.workflowLibraryDirs()
        comfy_dir = self.comfyUserWorkflowsDir()

        def collect_workflows(directory, recursive=False):
            found = []
            if not directory or not os.path.isdir(directory):
                return found
            try:
                if recursive:
                    for root, _dirs, files in os.walk(directory):
                        for fname in sorted(files, key=str.lower):
                            if fname.lower().endswith(".json"):
                                found.append(os.path.join(root, fname))
                else:
                    for fname in sorted(os.listdir(directory), key=str.lower):
                        if fname.lower().endswith(".json"):
                            full = os.path.join(directory, fname)
                            if os.path.isfile(full):
                                found.append(full)
            except OSError as e:
                logging.warning(f"Cannot list workflow directory '{directory}': {e}")
            return found

        # ComfyUI's own workflow folder is the shared library (kept in sync
        # with the embedded ComfyUI). Only API-format files are renderable;
        # UI-format saves living there are skipped from the listing.
        comfy_workflows = [p for p in collect_workflows(comfy_dir, recursive=True)
                           if self._isApiWorkflowFile(p)]

        # Kept as separate attributes for plugin compatibility (wizard plugin
        # reads image_workflows/video_workflows).
        self.image_workflows = collect_workflows(image_dir) + comfy_workflows
        self.video_workflows = collect_workflows(video_dir)

        # Refresh the listing when files appear/disappear in any library dir
        # (e.g. Ctrl+S saves from the embedded ComfyUI editor).
        for directory in (comfy_dir, image_dir, video_dir):
            self._watchWorkflowDir(directory)

        # One type-agnostic list, deduped by absolute path.
        combined = []
        seen = set()
        for wf in self.image_workflows + self.video_workflows:
            key = os.path.abspath(wf)
            if key not in seen:
                seen.add(key)
                combined.append(wf)
        combined.sort(key=lambda p: os.path.basename(p).lower())

        previous = self.workflowCombo.currentData()
        self.workflowCombo.blockSignals(True)
        self.workflowCombo.clear()
        for wf in combined:
            self.workflowCombo.addItem(os.path.basename(wf), userData=wf)
            # Full path as tooltip disambiguates same-named files.
            self.workflowCombo.setItemData(self.workflowCombo.count() - 1, wf,
                                           Qt.ItemDataRole.ToolTipRole)
        if previous:
            idx = self.workflowCombo.findData(previous)
            if idx != -1:
                self.workflowCombo.setCurrentIndex(idx)
        self.workflowCombo.blockSignals(False)

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


    def openWorkflowBrowser(self):
        """Opens the ComfyUI examples browser to import workflows into the library."""
        from comfystudio.sdmodules.workflow_browser import WorkflowBrowserDialog
        dialog = WorkflowBrowserDialog(self)
        dialog.exec()
        if dialog.imported_any:
            self.loadWorkflows()

    def applyTheme(self):
        from comfystudio.sdmodules.qss import get_stylesheet
        app = QApplication.instance()
        if app:
            app.setStyleSheet(get_stylesheet(self.settingsManager.get("theme", "dark")))

    def showSettingsDialog(self):
        dialog = SettingsDialog(self.settingsManager, self.localization, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.loadWorkflows()
            if self.currentShotIndex != -1:
                self.fillDock()
            # selected_language = dialog.get_selected_language()
            self.localization.set_language(self.settingsManager.get("language"))
            self.applyTheme()
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
