#!/usr/bin/env python
import copy
import json
import logging
import os
import random
import subprocess
import tempfile
from typing import List, Dict

from qtpy.QtGui import QPixmap
from qtpy.QtWidgets import QVBoxLayout
from qtpy.QtCore import (
    Qt,
    Slot,
    Signal
)
from qtpy.QtGui import QCursor
from qtpy.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QLineEdit,
    QSpinBox,
    QDoubleSpinBox,
    QFileDialog,
    QLabel,
    QDialog,
    QMessageBox,
    QCheckBox,
    QInputDialog,
    QMenu,
    QFrame
)

from comfystudio.sdmodules.cs_datastruts import (
    RESULT_PORT,
    Shot,
    WorkflowAssignment,
    ensure_unique_workflow_uids,
    workflow_param_port_id,
)
from comfystudio.sdmodules.localization import LocalizationManager
from comfystudio.sdmodules.settings import SettingsManager
from comfystudio.sdmodules.vareditor import DynamicParamEditor, DynamicParam
from comfystudio.sdmodules.videotools import extract_frame


class ImagePreviewLineEdit(QWidget):
    # Re-emit QLineEdit's textChanged signal so it behaves similarly.
    textChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.line_edit = QLineEdit(self)
        self.image_label = QLabel(self)
        # Disable automatic scaling to avoid unwanted stretching.
        self.image_label.setScaledContents(False)
        # Align the preview to the right and center vertically.
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # Optionally, set a default maximum height (this will be overridden dynamically).
        self.image_label.setMaximumHeight(200)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.line_edit)
        layout.addWidget(self.image_label)

        # Connect QLineEdit signal to our custom handler.
        self.line_edit.textChanged.connect(self._on_text_changed)

    def _on_text_changed(self, text):
        # Re-emit the textChanged signal.
        self.textChanged.emit(text)

        # Try to load an image using the text as a file path.
        pixmap = QPixmap(text)
        if not pixmap.isNull():
            # Calculate the maximum allowed height (4x the QLineEdit's height).
            max_height = 4 * self.line_edit.height()
            # Scale the pixmap if its height exceeds the maximum, preserving aspect ratio.
            if pixmap.height() > max_height:
                scaled_pixmap = pixmap.scaledToHeight(max_height, Qt.TransformationMode.SmoothTransformation)
            else:
                scaled_pixmap = pixmap

            self.image_label.setPixmap(scaled_pixmap)
            # Ensure the label's size fits the pixmap without stretching.
            self.image_label.setFixedSize(scaled_pixmap.size())
            self.image_label.show()
        else:
            self.image_label.clear()
            self.image_label.hide()

    # Expose common QLineEdit methods to remain compatible.
    def text(self):
        return self.line_edit.text()

    def setText(self, text):
        self.line_edit.setText(text)

    def setPlaceholderText(self, text):
        self.line_edit.setPlaceholderText(text)

    def placeholderText(self):
        return self.line_edit.placeholderText()

    def selectAll(self):
        self.line_edit.selectAll()


class ComfyStudioBase:
    def __init__(self, *args, **kwargs):
        # Always call super() to allow cooperative initialization.
        super().__init__(*args, **kwargs)
        self.settingsManager = SettingsManager()
        self.localization = LocalizationManager(self.settingsManager)
        self.shots: List[Shot] = []
        self.lastSelectedWorkflowIndex = {}
        self.currentShotIndex: int = -1

    def newProject(self):
        self.shots.clear()
        self.currentShotIndex = -1
        self.currentFilePath = None

        self.updateList()
        self.clearDock()
        if hasattr(self, "listWidgetBase") and hasattr(self.listWidgetBase, "clearTimeline"):
            self.listWidgetBase.clearTimeline()
        self.status_widgets["statusMessage"].setText("New project created.")
        self.setProjectModified(False)

    def openProject(self):
        filePath, _ = QFileDialog.getOpenFileName(
            self,
            self.localization.translate("dialog_open_project_title", default="Open Project"),
            "",
            "JSON Files (*.json);;All Files (*)"
        )
        if filePath:
            # Single code path for loading a project (handles timeline restore, selection, recents).
            self.openProjectFromPath(filePath)
    def isProjectModified(self):
        # Implement logic to check if the project has been modified.
        # This could involve setting a flag whenever shots or workflows are changed.
        return getattr(self, '_project_modified', False)

    def setProjectModified(self, modified=True):
        self._project_modified = modified

    def isProjectSaved(self):
        # Check if currentFilePath is set and the project is not modified
        return hasattr(self, 'currentFilePath') and self.currentFilePath and not self.isProjectModified()


    def getWidgetData(self, name):
        if hasattr(self, name):
            w = getattr(self, name)
            if hasattr(w, 'currentData'):
                return w.currentData()
        return None
    def getParamVisibility(self, workflow_path, node_id, param_name):
        data = self.settingsManager.get("workflow_param_visibility", {})
        if workflow_path in data and node_id in data[workflow_path] and param_name in data[workflow_path][node_id]:
            return data[workflow_path][node_id][param_name]
        return False

    def addImageWorkflow(self):
        if self.currentShotIndex < 0 or self.currentShotIndex >= len(self.shots):
            QMessageBox.warning(self, "Warning", "No shot selected.")
            return
        #path = self.imageWorkflowCombo.currentData()
        path = self.getWidgetData("imageWorkflowCombo")
        if not path:
            return
        self.addWorkflowToShot(path, isVideo=False)

    def addVideoWorkflow(self):
        if self.currentShotIndex < 0 or self.currentShotIndex >= len(self.shots):
            QMessageBox.warning(self, "Warning", "No shot selected.")
            return
        # path = self.videoWorkflowCombo.currentData()
        path = self.getWidgetData("videoWorkflowCombo")
        if not path:
            return
        self.addWorkflowToShot(path, isVideo=True)

    def addWorkflow(self):
        """
        Adds the workflow selected in the unified combo to the current shot.
        Workflows are type-agnostic; isVideo is only a hint (output subfolder
        naming) based on which library directory the file came from.
        """
        if self.currentShotIndex < 0 or self.currentShotIndex >= len(self.shots):
            QMessageBox.warning(self, "Warning", "No shot selected.")
            return
        path = self.getWidgetData("workflowCombo")
        if not path:
            return
        is_video_hint = path in getattr(self, "video_workflows", [])
        self.addWorkflowToShot(path, isVideo=is_video_hint)

    def loadWorkflowJson(self, workflow_path):
        """
        Loads and validates a ComfyUI workflow JSON file (API format).
        Raises ValueError with a user-friendly message when the file is missing,
        malformed, or saved in the UI (non-API) export format.
        """
        if not workflow_path or not os.path.isfile(workflow_path):
            raise ValueError(f"Workflow file not found:\n{workflow_path}")
        try:
            with open(workflow_path, "r", encoding="utf-8") as f:
                workflow_json = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Workflow file is not valid JSON:\n{workflow_path}\n\n{e}")
        if not isinstance(workflow_json, dict) or not workflow_json:
            raise ValueError(f"Workflow file has an unexpected structure:\n{workflow_path}")
        # UI-format exports have "nodes"/"links" lists instead of node-id keyed dicts.
        if isinstance(workflow_json.get("nodes"), list):
            raise ValueError(
                f"'{os.path.basename(workflow_path)}' appears to be saved in ComfyUI's UI format.\n\n"
                "Please re-export it from ComfyUI using 'Save (API Format)' "
                "(enable Dev mode options in ComfyUI settings) and try again."
            )
        # Sanity-check that at least one entry looks like an API-format node.
        has_valid_node = any(
            isinstance(nd, dict) and isinstance(nd.get("inputs", {}), dict)
            for nd in workflow_json.values()
        )
        if not has_valid_node:
            raise ValueError(
                f"No valid nodes with inputs found in workflow:\n{workflow_path}\n"
                "Expected a ComfyUI API-format workflow."
            )
        return workflow_json

    def extractWorkflowParams(self, workflow_json, workflow_path):
        """
        Discovers the editable local inputs ("locals") of an API-format workflow.
        Node-to-node connections (list/tuple values) are skipped — only literal
        int/float/bool/string values are exposed as parameters.
        """
        params_to_expose = []
        for node_id, node_data in workflow_json.items():
            if not isinstance(node_data, dict):
                continue
            inputs = node_data.get("inputs", {})
            if not isinstance(inputs, dict):
                continue
            meta = node_data.get("_meta", {})
            node_meta_title = ""
            if isinstance(meta, dict):
                node_meta_title = meta.get("title", "")
            if not node_meta_title:
                node_meta_title = node_data.get("class_type", "")
            for key, value in inputs.items():
                # Skip links to other nodes ([node_id, slot]) and nested structures:
                # they are connections, not user-settable values.
                if isinstance(value, (list, tuple, dict)):
                    continue
                if isinstance(value, bool):
                    ptype = "bool"
                elif isinstance(value, int):
                    ptype = "int"
                elif isinstance(value, float):
                    ptype = "float"
                else:
                    ptype = "string"
                    value = "" if value is None else str(value)

                # Load visibility state from settings, keyed by node_id + param name
                param_visibility = self.getParamVisibility(workflow_path, node_id, key)

                params_to_expose.append({
                    "name": key,
                    "type": ptype,
                    "value": value,
                    "nodeIDs": [str(node_id)],
                    "displayName": key,
                    "visible": param_visibility,
                    "nodeMetaTitle": node_meta_title,
                })
        return params_to_expose

    def syncWorkflowFromFile(self, workflow_path):
        """
        Re-reads a workflow file and updates the parameters of every shot
        workflow assignment that uses it. Values saved in the file win; UI state
        (visibility, display names) and dynamic overrides are preserved.
        Called after the embedded ComfyUI editor saves, or on external edits.
        """
        try:
            wf_json = self.loadWorkflowJson(workflow_path)
        except ValueError as e:
            logging.warning(f"Cannot sync workflow '{workflow_path}': {e}")
            return False
        fresh_params = self.extractWorkflowParams(wf_json, workflow_path)

        target = os.path.abspath(workflow_path)
        changed_any = False
        for shot in self.shots:
            for wf in shot.workflows:
                if os.path.abspath(wf.path) != target:
                    continue
                old_params = wf.parameters.get("params", [])
                old_by_key = {}
                for p in old_params:
                    old_by_key[(p.get("name"), tuple(p.get("nodeIDs", [])))] = p

                merged = []
                for new_param in copy.deepcopy(fresh_params):
                    key = (new_param["name"], tuple(new_param["nodeIDs"]))
                    old = old_by_key.get(key)
                    if old:
                        # Keep UI state and dynamic-override configuration.
                        new_param["visible"] = old.get("visible", new_param["visible"])
                        new_param["displayName"] = old.get("displayName", new_param["displayName"])
                        for flag in ("usePrevResultImage", "usePrevResultVideo", "useApiImage",
                                     "graphInput", "graphOutput"):
                            if old.get(flag):
                                new_param[flag] = old[flag]
                        if "dynamicOverrides" in old:
                            new_param["dynamicOverrides"] = copy.deepcopy(old["dynamicOverrides"])
                            # Dynamic params hold a placeholder/runtime value, not
                            # a literal from the file — keep the existing one.
                            new_param["value"] = old.get("value", new_param["value"])
                        if old.get("expression"):
                            new_param["expression"] = old["expression"]
                        if old.get("global_var"):
                            new_param["global_var"] = old["global_var"]
                    merged.append(new_param)
                wf.parameters["params"] = merged
                # Inputs removed from the file may have been graph ports.
                self.pruneInvalidConnections(shot)
                changed_any = True

        if changed_any:
            self.setProjectModified(True)
            # Refresh the dock without pushing the workflow back into the
            # embedded editor (that would clobber the canvas state there).
            self._syncingWorkflow = True
            try:
                if 0 <= self.currentShotIndex < len(self.shots):
                    shot = self.shots[self.currentShotIndex]
                    self.refreshWorkflowsList(shot)
                    self.refreshParamsList(shot)
                    current_item = self.workflowListWidget.currentItem()
                    if current_item:
                        self.onWorkflowItemClicked(current_item)
                self.refreshNodeEditor()
            finally:
                self._syncingWorkflow = False
            self.status_widgets["statusMessage"].setText(
                f"Parameters updated from '{os.path.basename(workflow_path)}'."
            )
        return changed_any

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
            workflow_json = self.loadWorkflowJson(workflow_path)
            params_to_expose = self.extractWorkflowParams(workflow_json, workflow_path)

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

                        # Preserve graph port exposure saved with the defaults.
                        for flag in ("graphInput", "graphOutput"):
                            if default_param.get(flag):
                                param[flag] = default_param[flag]

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
            ensure_unique_workflow_uids(shot)
            self.refreshWorkflowsList(shot)
            self.refreshParamsList(shot)
            self.refreshNodeEditor()
            self._watchWorkflowFile(workflow_path)
            self.setProjectModified(True)

        except ValueError as e:
            QMessageBox.warning(self, "Error", str(e))
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load workflow: {e}")
    def loadWorkflowDefaults(self, workflow_path):
        defaults = self.settingsManager.get("workflow_defaults", {})
        return defaults.get(workflow_path, None)

    def onWorkflowItemClicked(self, item):
        workflow: WorkflowAssignment = item.data(Qt.ItemDataRole.UserRole)
        if not workflow:
            return

        self.workflowParamsGroup.setEnabled(True)

        # Mirror the selection into the embedded ComfyUI editor when it's shown.
        # Skip when the same file is already open there (preserves unsaved edits)
        # or when this refresh was itself triggered by a save-back sync.
        if (getattr(self, "webBrowserDock", None) is not None
                and self.webBrowserDock.isVisible()
                and not getattr(self, "_syncingWorkflow", False)):
            bridge = getattr(self, "workflowEditBridge", None)
            if bridge is None or bridge.target() != workflow.path:
                self.loadWorkflowInBrowser(workflow)

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

        version_dropdown = self.createWorkflowVersionDropdown(workflow)
        self.workflowParamsLayout.addWidget(version_dropdown)

        params_list = workflow.parameters.get("params", [])

        # When no parameter is marked visible, show all of them instead of an
        # empty editor (freshly imported workflows start with everything hidden).
        show_all = self.showHiddenParams or not any(
            p.get("visible", True) for p in params_list
        )

        # 1) Group by node_id (or use nodeMetaTitle as key if you prefer).
        #    We'll store data in a dict: node_id -> { "title": ..., "params": [] }
        node_map = {}
        for param in params_list:
            # Skip if invisible and user isn't showing hidden
            if not param.get("visible", True) and not show_all:
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
                if param.get("graphInput"):
                    suffix += " (graph in)"
                if param.get("graphOutput"):
                    suffix += " (graph out)"
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
        Right-click context menu for a single workflow parameter row.
        This version uses the dynamic registry to build the menu.
        """
        menu = QMenu(self)
        from comfystudio.sdmodules.core.param_context_menu import _get_registry
        registry = _get_registry()


        param_type = param.get("type", "string")
        # For string-type parameters include all actions; for others, include only common ones.
        if param_type == "string":
            actions = [spec for spec in registry
                       if "string" in spec.get("param_types", []) or "other" in spec.get("param_types", [])]
        else:
            actions = [spec for spec in registry if "other" in spec.get("param_types", [])]

        # Create the menu and map each QAction to its callback.
        action_map = {}
        for spec in actions:
            act = menu.addAction(spec["text"])
            # Bind the callback with (self, param) using a default-argument lambda.
            action_map[act] = lambda cb=spec["callback"]: cb(self, param)

        chosen = menu.exec(QCursor.pos())
        if chosen in action_map:
            action_map[chosen]()

        # Refresh the workflow item display.
        currentItem = self.workflowListWidget.currentItem()
        if currentItem:
            self.onWorkflowItemClicked(currentItem)
    # def onWorkflowParamContextMenu(self, pos, param):
    #     """
    #     Right-click context menu for a single workflow param row in the Workflow tab.
    #     Allows setting this param to use the previous workflow's image or video result.
    #     """
    #     menu = QMenu(self)
    #     currentItem = self.workflowListWidget.currentItem()
    #     paramType = param.get("type", "string")
    #     if paramType == "string":
    #         setPrevImage = menu.addAction("Set Param to Previous Workflow's Image")
    #         setPrevVideo = menu.addAction("Set Param to Previous Workflow's Video")
    #         clearDynOverride = menu.addAction("Clear Dynamic Override")
    #         setAllSelectedShotsAction = menu.addAction("Set All SELECTED Shots (this param)")
    #         setAllShotsAction = menu.addAction("Set ALL Shots (this param)")
    #         editDynamicParam = menu.addAction("Edit as Dynamic Parameter")
    #         chosen = menu.exec(QCursor.pos())  # or mapToGlobal(pos) if needed
    #         if chosen == setPrevImage:
    #             param["usePrevResultImage"] = True
    #             param["usePrevResultVideo"] = False
    #             param["value"] = "(Awaiting previous workflow image)"
    #             param["dynamicOverrides"] = {
    #                 "type": "shot",
    #                 "shotIndex": self.currentShotIndex,
    #                 "assetType": "image"
    #             }
    #             QMessageBox.information(self, "Info",
    #                                     "This parameter is now flagged to use the previous workflow's image result."
    #                                     )
    #         elif chosen == setPrevVideo:
    #             param["usePrevResultVideo"] = True
    #             param["usePrevResultImage"] = False
    #             param["value"] = "(Awaiting previous workflow video)"
    #             param["dynamicOverrides"] = {
    #                 "type": "shot",
    #                 "shotIndex": self.currentShotIndex,
    #                 "assetType": "video"
    #             }
    #             QMessageBox.information(self, "Info",
    #                                     "This parameter is now flagged to use the previous workflow's video result."
    #                                     )
    #         elif chosen == clearDynOverride:
    #             param.pop("usePrevResultImage", None)
    #             param.pop("usePrevResultVideo", None)
    #             param.pop("dynamicOverrides", None)
    #             QMessageBox.information(self, "Info", "Dynamic override cleared.")
    #         elif chosen == setAllSelectedShotsAction:
    #             self.setParamValueInShots(param, onlySelected=True, item=currentItem)
    #         elif chosen == setAllShotsAction:
    #             self.setParamValueInShots(param, onlySelected=False, item=currentItem)
    #         elif chosen == editDynamicParam:
    #             # Create a DynamicParam from the existing param dictionary.
    #             dyn_param = DynamicParam(
    #                 name=param.get("name", ""),
    #                 param_type=param.get("type", "string"),
    #                 value=param.get("value", ""),
    #                 expression=param.get("expression", ""),
    #                 global_var=param.get("global_var", "")
    #             )
    #             editor = DynamicParamEditor(dyn_param, self.global_vars, self)
    #             if editor.exec() == QDialog.DialogCode.Accepted:
    #                 # Save the dynamic settings back into the parameter dictionary.
    #                 param["value"] = dyn_param.value
    #                 param["expression"] = dyn_param.expression
    #                 param["global_var"] = dyn_param.global_var
    #                 QMessageBox.information(self, "Info", "Dynamic parameter updated.")
    #     else:
    #         setAllSelectedShotsAction = menu.addAction("Set All SELECTED Shots (this param)")
    #         setAllShotsAction = menu.addAction("Set ALL Shots (this param)")
    #         editDynamicParam = menu.addAction("Edit as Dynamic Parameter")
    #         chosen = menu.exec(QCursor.pos())  # or mapToGlobal(pos) if needed
    #         if chosen == setAllSelectedShotsAction:
    #             self.setParamValueInShots(param, onlySelected=True, item=currentItem)
    #         elif chosen == setAllShotsAction:
    #             self.setParamValueInShots(param, onlySelected=False, item=currentItem)
    #         elif chosen == editDynamicParam:
    #             dyn_param = DynamicParam(
    #                 name=param.get("name", ""),
    #                 param_type=param.get("type", "string"),
    #                 value=param.get("value", ""),
    #                 expression=param.get("expression", ""),
    #                 global_var=param.get("global_var", "")
    #             )
    #             editor = DynamicParamEditor(dyn_param, self.global_vars, self)
    #             if editor.exec() == QDialog.DialogCode.Accepted:
    #                 param["value"] = dyn_param.value
    #                 param["expression"] = dyn_param.expression
    #                 param["global_var"] = dyn_param.global_var
    #                 QMessageBox.information(self, "Info", "Dynamic parameter updated.")
    #
    #     # After making changes, re-fill the workflow item to show updated text
    #     # if the user re-opens the workflow item
    #     # For immediate refresh, you can re-call onWorkflowItemClicked on the current item:
    #
    #     if currentItem:
    #         self.onWorkflowItemClicked(currentItem)


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

    def onParamVisibilityChanged(self, workflow: WorkflowAssignment, node_id: str, param: Dict, visible: bool):
        param["visible"] = visible
        self.setParamVisibility(workflow.path, node_id, param["name"], visible)
        self.onWorkflowItemClicked(self.workflowListWidget.currentItem())
        self.refreshParamsList(self.shots[self.currentShotIndex])

    def setParamVisibility(self, workflow_path, node_id, param_name, visible):
        data = self.settingsManager.get("workflow_param_visibility", {})
        if workflow_path not in data:
            data[workflow_path] = {}
        if node_id not in data[workflow_path]:
            data[workflow_path][node_id] = {}
        data[workflow_path][node_id][param_name] = visible
        self.settingsManager.set("workflow_param_visibility", data)
        self.settingsManager.save()

    # --- Shot workflow graph helpers ---------------------------------------

    def refreshNodeEditor(self):
        """Refreshes the shot node editor dock, when it exists."""
        editor = getattr(self, "shotNodeEditor", None)
        if editor is not None:
            editor.refresh()

    def pruneInvalidConnections(self, shot: Shot) -> bool:
        """
        Drops graph connections whose endpoints no longer resolve (workflow
        removed, param dropped by a resync) and positions of removed
        workflows. Returns True when anything was removed.
        """
        graph = shot.graph
        by_uid = {wf.uid: wf for wf in shot.workflows}

        def port_exists(wf, port, is_source):
            if port == RESULT_PORT:
                return is_source  # Result is an output-only port
            return any(
                workflow_param_port_id(p) == port
                for p in wf.parameters.get("params", [])
            )

        kept = []
        for conn in graph.get("connections", []):
            src = conn.get("from", {})
            dst = conn.get("to", {})
            src_wf = by_uid.get(src.get("wf"))
            dst_wf = by_uid.get(dst.get("wf"))
            if (src_wf and dst_wf
                    and port_exists(src_wf, src.get("port"), True)
                    and port_exists(dst_wf, dst.get("port"), False)):
                kept.append(conn)
        changed = len(kept) != len(graph.get("connections", []))
        graph["connections"] = kept

        positions = graph.get("positions", {})
        stale = [uid for uid in positions if uid not in by_uid]
        for uid in stale:
            del positions[uid]
        return changed or bool(stale)

    def removeParamPortConnections(self, param) -> bool:
        """
        Removes every connection of the current shot that touches the given
        param's port on its owning workflow (used when un-exposing a port).
        """
        if not (0 <= self.currentShotIndex < len(self.shots)):
            return False
        shot = self.shots[self.currentShotIndex]
        owner = None
        for wf in shot.workflows:
            if any(p is param for p in wf.parameters.get("params", [])):
                owner = wf
                break
        if owner is None:
            return False
        port = workflow_param_port_id(param)
        conns = shot.graph.get("connections", [])
        kept = [
            c for c in conns
            if not ((c.get("from", {}).get("wf") == owner.uid and c.get("from", {}).get("port") == port)
                    or (c.get("to", {}).get("wf") == owner.uid and c.get("to", {}).get("port") == port))
        ]
        if len(kept) != len(conns):
            shot.graph["connections"] = kept
            self.setProjectModified(True)
            return True
        return False

    def migrateShotGraph(self, shot: Shot) -> bool:
        """
        Converts legacy usePrevResultImage/Video flags into explicit graph
        connections to the previous enabled workflow's Result port. Idempotent:
        does nothing when the shot already has connections. The legacy flags
        are kept on the params — they stay honored at render time as a
        fallback and keep older builds working.
        """
        ensure_unique_workflow_uids(shot)
        if shot.graph.get("connections"):
            return False
        added = False
        prev = None
        for wf in shot.workflows:
            if not wf.enabled:
                continue
            if prev is not None:
                for param in wf.parameters.get("params", []):
                    if param.get("usePrevResultImage") or param.get("usePrevResultVideo"):
                        shot.graph["connections"].append({
                            "from": {"wf": prev.uid, "port": RESULT_PORT},
                            "to": {"wf": wf.uid, "port": workflow_param_port_id(param)},
                        })
                        param["graphInput"] = True
                        added = True
            prev = wf
        return added

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
            try:
                w.setValue(max(-2147483648, min(int(pval), 2147483647)))
            except (TypeError, ValueError):
                w.setValue(0)
            w.valueChanged.connect(lambda v, p=param: self.onWorkflowParamChanged(None, p, v))
            return w
        elif ptype == "float":
            w = QDoubleSpinBox()
            w.setRange(-1e12, 1e12)
            w.setDecimals(6)
            try:
                w.setValue(float(pval))
            except (TypeError, ValueError):
                w.setValue(0.0)
            w.valueChanged.connect(lambda v, p=param: self.onWorkflowParamChanged(None, p, v))
            return w
        elif ptype == "bool":
            w = QCheckBox()
            w.setChecked(bool(pval) and str(pval).lower() not in ("false", "0", ""))
            w.toggled.connect(lambda v, p=param: self.onWorkflowParamChanged(None, p, bool(v)))
            return w
        else:
            w = ImagePreviewLineEdit()
            w.setText(str(pval))
            w.textChanged.connect(lambda v, p=param: self.onWorkflowParamChanged(None, p, v))
            return w
    def onWorkflowParamChanged(self, workflow: WorkflowAssignment, param: Dict, newVal):
        param["value"] = newVal
        self.saveCurrentWorkflowParams()


    def onWorkflowListContextMenu(self, pos):
        item = self.workflowListWidget.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        enableAction = menu.addAction("Toggle Enabled")
        editInComfyAction = menu.addAction("Edit in ComfyUI")
        action = menu.exec(self.workflowListWidget.mapToGlobal(pos))
        workflow: WorkflowAssignment = item.data(Qt.ItemDataRole.UserRole)
        if action == enableAction:
            workflow.enabled = not workflow.enabled
            self.refreshWorkflowsList(self.shots[self.currentShotIndex])
        elif action == editInComfyAction:
            if getattr(self, "webBrowserDock", None) is not None:
                self.webBrowserDock.setVisible(True)
                if hasattr(self, "toggleWebBrowserAct"):
                    self.toggleWebBrowserAct.setChecked(True)
                self.loadWorkflowInBrowser(workflow)
            else:
                QMessageBox.information(
                    self, "Web Browser Unavailable",
                    "The embedded browser requires PyQt6-WebEngine, which is not installed."
                )
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
                self.pruneInvalidConnections(shot)
                self.refreshWorkflowsList(shot)
                while self.workflowParamsLayout.rowCount() > 0:
                    self.workflowParamsLayout.removeRow(0)
                self.workflowParamsGroup.setEnabled(False)
                # QMessageBox.information(self, "Info", "Workflow removed from the shot.")
                self.refreshParamsList(shot)
                self.refreshNodeEditor()
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
            # The deepcopy duplicated a uid already present in this shot.
            ensure_unique_workflow_uids(new_shot)

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
