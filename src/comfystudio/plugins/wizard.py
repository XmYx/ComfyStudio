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
    # Step 1: Choose an LLM workflow
    llm_dir = os.path.join(os.path.dirname(__file__), "..", "workflows", "llm")
    if not os.path.isdir(llm_dir):
        QMessageBox.warning(app, "Error", f"No LLM workflow folder found at: {llm_dir}")
        return

    llm_workflows = [f for f in os.listdir(llm_dir) if f.lower().endswith(".json")]
    if not llm_workflows:
        QMessageBox.warning(app, "Error", "No LLM workflows found in 'workflows/llm'.")
        return

    workflow_file, ok = QInputDialog.getItem(app, "Select LLM Workflow",
                                             "Choose a workflow to use:",
                                             llm_workflows, 0, False)
    if not ok or not workflow_file:
        return

    llm_workflow_path = os.path.join(llm_dir, workflow_file)

    # Step 2: Load the LLM workflow JSON
    try:
        with open(llm_workflow_path, "r") as wf:
            workflow_json = json.load(wf)
    except Exception as e:
        QMessageBox.critical(app, "Error", f"Failed to load workflow: {e}")
        return

    # Step 3: Let user edit 'text' inputs in the LLM workflow (prompts, etc.)
    editable_nodes = []
    input_prompt = ""
    for node_id, node_data in workflow_json.items():
        if "inputs" in node_data:
            if "text" in node_data["inputs"]:
                title = node_data.get("_meta", {}).get("title", f"Node {node_id}")
                if "prompt" in title.lower():
                    editable_nodes.append((node_id, title, node_data["inputs"]["text"]))

    if editable_nodes:
        results = showNodeEditorDialog(app, editable_nodes)
        if results is None:
            return
        for node_id, new_text in results.items():
            workflow_json[node_id]["inputs"]["text"] = new_text
            if workflow_json[node_id]["_meta"].get("title") == "input prompt":
                input_prompt = new_text

    # Step 4: Send the LLM workflow to Comfy to get initial text lines
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

    # Poll for the result
    lines = pollTextResult(app, comfy_ip, prompt_id)
    if not lines:
        return

    # Step 5: Ask if we want multiple iterations
    iter_count, ok = QInputDialog.getInt(app, "Iterations", "Number of iterations:", 1, 1)
    if not ok:
        iter_count = 1

    all_lines = lines[:]
    prev_text = "\n".join(lines)

    # Step 6: For additional iterations, load and apply the iteration workflow (e.g. ollama_iter.json)
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

        # Overwrite text fields
        for node_id, node_data in iter_json.items():
            if "inputs" in node_data:
                if node_data.get("_meta", {}).get("title", "") == "prompt history":
                    node_data["inputs"]["text"] = prev_text
                elif node_data.get("_meta", {}).get("title", "") == "input prompt":
                    node_data["inputs"]["text"] = input_prompt
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

    # Step 7: Let user pick one or more workflows to add to each new shot
    available_workflows = []
    for wfile in app.image_workflows:
        available_workflows.append(("Image", wfile))
    for wfile in app.video_workflows:
        available_workflows.append(("Video", wfile))

    wdlg = WorkflowSelectionDialog(app, available_workflows)
    selected_workflows = wdlg.getSelectedWorkflows()
    if not selected_workflows:
        return

    # Step 8: Let user pick which workflow param to set with these generated lines
    # We'll gather all param names from the selected workflows (unique).
    all_param_names = set()
    for wtype, wpath in selected_workflows:
        try:
            with open(wpath, "r") as f:
                wf_json = json.load(f)
            for node_id, node_data in wf_json.items():
                inputs = node_data.get("inputs", {})
                for key in inputs.keys():
                    all_param_names.add(key)
        except:
            pass

    if not all_param_names:
        QMessageBox.warning(app, "Error", "No parameters found in the selected workflows.")
        return

    param_list = sorted(list(all_param_names))
    param_name, ok = QInputDialog.getItem(app, "Select Workflow Param",
                                          "Which parameter should we set with each generated line?",
                                          param_list, 0, False)
    if not ok or not param_name:
        return

    # Step 9: Create new shots, add chosen workflows, set the chosen param to each line
    for line_num, text_line in enumerate(all_lines, 1):
        new_shot = Shot(name=f"Shot {len(app.shots) + 1}")
        for wtype, wpath in selected_workflows:
            try:
                with open(wpath, "r") as wf_file:
                    wf_js = json.load(wf_file)
                params_to_expose = []
                for nid, ndata in wf_js.items():
                    inputs = ndata.get("inputs", {})
                    for k, val in inputs.items():
                        ptype = type(val).__name__
                        if ptype not in ["int", "float"]:
                            ptype = "string"
                        params_to_expose.append({
                            "name": k,
                            "type": ptype,
                            "value": val,
                            "nodeIDs": [nid],
                            "displayName": k,
                            "visible": True
                        })
                # Now override the param_name with text_line
                for p in params_to_expose:
                    if p["name"] == param_name:
                        # This is the param to set
                        p["value"] = text_line
                # new_wf = app.createWorkflowAssignment(
                #     path=wpath,
                #     enabled=True,
                #     parameters={"params": params_to_expose},
                #     isVideo=(wtype == "Video")
                # )


                new_wf = WorkflowAssignment(
                    path=wpath,
                    enabled=True,
                    parameters={"params": params_to_expose}
                )

                new_shot.workflows.append(new_wf)
            except Exception as e:
                QMessageBox.warning(app, "Error", f"Failed to load workflow '{wpath}': {e}")

        app.shots.append(new_shot)

    app.updateList()
    QMessageBox.information(app, "Shot Wizard",
                            f"Imported {len(all_lines)} lines into new shots using '{workflow_file}'.")


def pollTextResult(app, comfy_ip, prompt_id):
    """
    Helper to poll for a ComfyUI text result from a given prompt_id.
    Returns a list of lines if successful, or None if not.
    """
    history_url = f"{comfy_ip}/history/{prompt_id}"
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
                        for t in texts:
                            for line in t.splitlines():
                                s = line.strip()
                                if s:
                                    final_lines.append(s)
                    if final_lines:
                        return final_lines
        except:
            pass
    QMessageBox.warning(app, "Warning", f"No text result received for prompt_id {prompt_id}.")
    return None


def showNodeEditorDialog(parent, node_list):
    """
    A small dialog that allows editing 'text' fields for LLM prompt nodes.
    node_list: list of (node_id, node_title, current_text)
    Returns dict of node_id -> new_text or None on cancel.
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
    Dialog that shows available workflows in a list with checkboxes
    so the user can select multiple. Returns a list of (type, path).
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
