import copy
import os
import json
import random
import time

import requests

from qtpy.QtWidgets import (
    QAction,
    QMessageBox,
    QInputDialog,
    QDialog,
    QVBoxLayout,
    QFormLayout,
    QDialogButtonBox,
    QPlainTextEdit,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QCheckBox,
    QComboBox,
    QPushButton,
    QHBoxLayout,
    QScrollArea,
    QWidget
)
from qtpy.QtCore import Qt

from comfystudio.sdmodules.dataclasses import Shot, WorkflowAssignment


def register(app):
    """
    Registers the "Shot Wizard" menu item inside the File menu of the main application.
    """
    shotWizardAction = QAction("Shot Wizard", app)
    file_menu = None
    for action in app.menuBar().actions():
        if action.text() == "File":
            file_menu = action.menu()
            break
    if file_menu:
        file_menu.addAction(shotWizardAction)
    shotWizardAction.triggered.connect(lambda: run_shot_wizard(app))


def run_shot_wizard(app):
    """
    Main function that:
      1) Lets you pick an LLM workflow (from workflows/llm) and edit prompt texts.
      2) Sends the prompt to ComfyUI for multiple iterations if desired.
      3) Collects each iteration's output lines.
      4) Lets you pick normal workflows (image/video) to apply to each line.
      5) Lets you pick which single parameter in those workflows to set to each line.
      6) Creates brand-new Shots, each containing copies of the selected workflows
         (inheriting workflow defaults), and sets that parameter to the line's text.
    """
    # Step 1: Choose an LLM workflow
    llm_dir = os.path.join(os.path.dirname(__file__), "..", "workflows", "llm")
    if not os.path.isdir(llm_dir):
        QMessageBox.warning(app, "Error", f"No LLM workflow folder found at: {llm_dir}")
        return

    llm_workflows = [f for f in os.listdir(llm_dir) if f.lower().endswith(".json")]
    if not llm_workflows:
        QMessageBox.warning(app, "Error", "No LLM workflows found in 'workflows/llm'.")
        return

    workflow_file, ok = QInputDialog.getItem(
        app,
        "Select LLM Workflow",
        "Choose a workflow to use:",
        llm_workflows,
        0,
        False
    )
    if not ok or not workflow_file:
        return

    llm_workflow_path = os.path.join(llm_dir, workflow_file)

    # Step 2: Load the chosen LLM workflow JSON
    try:
        with open(llm_workflow_path, "r") as wf:
            workflow_json = json.load(wf)
    except Exception as e:
        QMessageBox.critical(app, "Error", f"Failed to load workflow: {e}")
        return

    # Step 3: Let user edit 'text' inputs (prompts) in the LLM workflow
    editable_nodes = []
    input_prompt = ""
    for node_id, node_data in workflow_json.items():
        if "inputs" in node_data and "text" in node_data["inputs"]:
            title = node_data.get("_meta", {}).get("title", f"Node {node_id}")
            # We'll consider any node that has "prompt" in its title as something we can edit
            if "prompt" in title.lower():
                editable_nodes.append((node_id, title, node_data["inputs"]["text"]))

    if editable_nodes:
        results = showNodeEditorDialog(app, editable_nodes)
        if results is None:
            return
        for node_id, new_text in results.items():
            workflow_json[node_id]["inputs"]["text"] = new_text
            # If it's the "input prompt" node, keep a copy for iteration steps
            if workflow_json[node_id]["_meta"].get("title", "").lower() == "input prompt":
                input_prompt = new_text

    # Step 4: Send the initial LLM workflow to ComfyUI to get text lines
    comfy_ip = app.settingsManager.get("comfy_ip", "http://localhost:8188").rstrip("/")
    prompt_url = f"{comfy_ip}/prompt"
    headers = {"Content-Type": "application/json"}
    data = {"prompt": workflow_json}

    try:
        resp = requests.post(prompt_url, headers=headers, json=data)
        resp.raise_for_status()
        result_json = resp.json()
        prompt_id = result_json.get("prompt_id")
    except Exception as e:
        QMessageBox.critical(app, "Error", f"Failed to send workflow: {e}")
        return

    if not prompt_id:
        QMessageBox.critical(app, "Error", "No prompt_id returned from ComfyUI.")
        return

    lines = pollTextResult(app, comfy_ip, prompt_id)
    if not lines:
        return

    # Step 5: Ask how many LLM iteration steps to do
    iter_count, ok = QInputDialog.getInt(app, "Iterations", "Number of iterations:", 1, 1)
    if not ok:
        iter_count = 1

    all_lines = lines[:]
    prev_text = "\n".join(lines)

    # Step 6: For each extra iteration, load a separate iteration workflow (ollama_iter.json) if it exists
    for iteration in range(2, iter_count + 1):
        iter_workflow_path = os.path.join(llm_dir, "ollama_iter.json")
        if not os.path.isfile(iter_workflow_path):
            QMessageBox.warning(app, "Error", f"No iteration workflow found at: {iter_workflow_path}")
            break

        try:
            with open(iter_workflow_path, "r") as f:
                iter_json = json.load(f)
        except Exception as e:
            QMessageBox.critical(app, "Error", f"Failed to load iteration workflow: {e}")
            break

        # Update iteration workflow text fields
        for node_id, node_data in iter_json.items():
            if "inputs" in node_data:
                title = node_data.get("_meta", {}).get("title", "").lower()
                if "prompt history" in title:
                    node_data["inputs"]["text"] = prev_text
                elif "input prompt" in title:
                    node_data["inputs"]["text"] = input_prompt
                # Reset seed for each iteration
                if "seed" in node_data["inputs"]:
                    node_data["inputs"]["seed"] = random.randint(0, 2**31 - 1)

        iter_data = {"prompt": iter_json}
        try:
            ir = requests.post(prompt_url, headers=headers, json=iter_data)
            ir.raise_for_status()
            iter_resp = ir.json()
            iter_pid = iter_resp.get("prompt_id")
        except Exception as e:
            QMessageBox.critical(app, "Error", f"Failed to send iteration {iteration}: {e}")
            break

        if not iter_pid:
            QMessageBox.critical(app, "Error", f"Iteration {iteration} - no prompt_id returned.")
            break

        iter_lines = pollTextResult(app, comfy_ip, iter_pid)
        if not iter_lines:
            break

        all_lines.extend(iter_lines)
        prev_text = "\n".join(iter_lines)

    if not all_lines:
        QMessageBox.information(app, "Shot Wizard", "No lines were generated.")
        return

    # Step 7: Let user pick one or more normal workflows (image/video) to apply to each line
    available_workflows = []
    # The main app provides these lists from self.image_workflows and self.video_workflows
    for wfile in app.image_workflows:
        available_workflows.append(("Image", wfile))
    for wfile in app.video_workflows:
        available_workflows.append(("Video", wfile))

    wdlg = WorkflowSelectionDialog(app, available_workflows)
    selected_workflows = wdlg.getSelectedWorkflows()
    if not selected_workflows:
        return

    # Step 8: Let the user pick which single param (including node [_meta][title]) to set
    # Step 8: Let the user pick which parameter to set for EACH selected workflow
    param_selection = {}  # Maps wpath -> the chosen parameter name for that workflow

    for wtype, wpath in selected_workflows:
        try:
            with open(wpath, "r") as f:
                wf_js = json.load(f)
        except:
            # If a particular workflow fails to load, skip it
            continue

        # Gather all input parameters for this workflow
        param_map = {}  # e.g. "text [Prompt Node]" -> "text"
        for node_id, node_data in wf_js.items():
            node_title = node_data.get("_meta", {}).get("title", "") or "Untitled"
            inputs = node_data.get("inputs", {})
            for param_name in inputs.keys():
                label = f"{param_name} [{node_title}]"
                param_map[label] = param_name

        if not param_map:
            # No parameters in this workflow
            QMessageBox.warning(app, "Error", f"No parameters found in '{os.path.basename(wpath)}'.")
            continue

        # Prompt user to pick which param in this *workflow* to set
        param_list = sorted(list(param_map.keys()))
        param_label, ok = QInputDialog.getItem(
            app,
            f"Select Parameter for {os.path.basename(wpath)}",
            "Which parameter should be set with each generated line?",
            param_list,
            0,
            False
        )
        if not ok or not param_label:
            # User canceled or closed; skip
            continue

        # Remember the raw param name (like "text") for use later
        selected_param_name = param_map[param_label]
        param_selection[wpath] = selected_param_name

    # If user canceled everything or no valid picks, bail
    if not param_selection:
        return

    # Step 9: Create new shots from each line, add chosen workflows, apply defaults, set chosen param
    for line_num, text_line in enumerate(all_lines, 1):
        new_shot = Shot(name=f"Shot {len(app.shots) + 1}")
        for wtype, wpath in selected_workflows:
            try:
                # Load the base workflow JSON to create param stubs
                with open(wpath, "r") as wf_file:
                    base_json = json.load(wf_file)

                params_to_expose = []
                for nid, ndata in base_json.items():
                    inputs = ndata.get("inputs", {})
                    node_title = ndata.get("_meta", {}).get("title", "")
                    for key, val in inputs.items():
                        ptype = type(val).__name__
                        if ptype not in ["int", "float"]:
                            ptype = "string"  # strings are default
                        param_visibility = app.getParamVisibility(wpath, nid, key)
                        params_to_expose.append({
                            "name": key,
                            "type": ptype,
                            "value": val,
                            "nodeIDs": [nid],
                            "displayName": key,
                            "visible": param_visibility,  # by default, mark param invisible or not
                            "nodeMetaTitle": node_title,
                        })

                # Build the new workflow assignment
                new_wf = WorkflowAssignment(
                    path=wpath,
                    enabled=True,
                    parameters={"params": params_to_expose},
                    isVideo=(wtype == "Video")
                )

                # 1) Merge in stored workflow defaults (the same way the main app does)
                defaults = app.loadWorkflowDefaults(wpath)
                if defaults and "params" in defaults:
                    for param in new_wf.parameters.get("params", []):
                        default_param = next(
                            (
                                d for d in defaults["params"]
                                if d["name"] == param["name"]
                                and d.get("nodeIDs", []) == param.get("nodeIDs", [])
                            ),
                            None
                        )
                        if default_param:
                            # Copy value
                            param["value"] = default_param.get("value", param["value"])
                            # If there's dynamic overrides in defaults
                            if "dynamicOverrides" in default_param:
                                param["dynamicOverrides"] = copy.deepcopy(default_param["dynamicOverrides"])
                                # If the default says use an image or video from previous workflow
                                asset_type = default_param["dynamicOverrides"].get("assetType", "")
                                if asset_type == "image":
                                    param["usePrevResultImage"] = True
                                    param["usePrevResultVideo"] = False
                                    param["value"] = "(Awaiting previous workflow image)"
                                elif asset_type == "video":
                                    param["usePrevResultVideo"] = True
                                    param["usePrevResultImage"] = False
                                    param["value"] = "(Awaiting previous workflow video)"

                # 2) Finally, set the single user-chosen parameter to the generated line
                for p in new_wf.parameters["params"]:
                    if p["name"] == selected_param_name:
                        p["value"] = text_line

                # Add new workflow to the shot
                new_shot.workflows.append(new_wf)

            except Exception as e:
                QMessageBox.warning(app, "Error", f"Failed to load workflow '{wpath}': {e}")

        # Add the newly created shot to the main app
        app.shots.append(new_shot)

    # Update UI to reflect new shots
    app.updateList()

    QMessageBox.information(
        app,
        "Shot Wizard",
        f"Imported {len(all_lines)} text lines into {len(all_lines)} new shots.\n"
        f"All assigned workflows are normal shots with inherited defaults."
    )


def pollTextResult(app, comfy_ip, prompt_id):
    """
    Helper to poll for a ComfyUI text result from a given prompt_id.
    Returns a list of lines if successful, or None if the result never arrives.
    """
    history_url = f"{comfy_ip}/history/{prompt_id}"
    # We'll try up to ~400 seconds (200 * 2s).
    for _ in range(200):
        time.sleep(2)
        try:
            hr = requests.get(history_url)
            if hr.status_code == 200:
                j = hr.json()
                if j:
                    outputs = j.get(prompt_id, {}).get("outputs", {})
                    final_lines = []
                    for node_id, out_data in outputs.items():
                        texts = out_data.get("text", [])
                        # Each text chunk could have multiple lines
                        for t in texts:
                            for line in t.splitlines():
                                s = line.strip()
                                if s:
                                    final_lines.append(s)
                    if final_lines:
                        return final_lines
        except Exception:
            pass

    QMessageBox.warning(app, "Warning", f"No text result received for prompt_id {prompt_id}.")
    return None


def showNodeEditorDialog(parent, node_list):
    """
    A small dialog that allows editing 'text' fields for LLM prompt nodes.
    node_list: list of (node_id, node_title, current_text)
    Returns dict of node_id -> new_text or None if canceled.
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle("Edit Node Text Inputs")
    layout = QVBoxLayout(dlg)

    form = QFormLayout()
    editors = {}
    for nid, title, txtval in node_list:
        lab = QLabel(title)
        ed = QPlainTextEdit()
        ed.setPlainText(str(txtval))
        form.addRow(lab, ed)
        editors[nid] = ed
    layout.addLayout(form)

    btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    layout.addWidget(btns)
    btns.accepted.connect(dlg.accept)
    btns.rejected.connect(dlg.reject)

    if dlg.exec() == QDialog.Accepted:
        results = {}
        for nid, ed in editors.items():
            results[nid] = ed.toPlainText()
        return results
    return None


class WorkflowSelectionDialog(QDialog):
    """
    Dialog that shows a list of workflows (image/video) with checkboxes so the user can select multiple.
    Returns a list of (type, path) if accepted.
    """
    def __init__(self, parent, workflows):
        super().__init__(parent)
        self.setWindowTitle("Select Workflows to Assign")
        self._workflows = workflows
        self._selected = []

        layout = QVBoxLayout(self)
        self.listWidget = QListWidget()
        layout.addWidget(self.listWidget)

        for wtype, wpath in self._workflows:
            item = QListWidgetItem(f"{wtype} Workflow: {os.path.basename(wpath)}")
            item.setData(Qt.UserRole, (wtype, wpath))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.listWidget.addItem(item)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def getSelectedWorkflows(self):
        if self.exec() != QDialog.Accepted:
            return []
        selected = []
        for i in range(self.listWidget.count()):
            it = self.listWidget.item(i)
            if it.checkState() == Qt.Checked:
                wtype, wpath = it.data(Qt.UserRole)
                selected.append((wtype, wpath))
        return selected
