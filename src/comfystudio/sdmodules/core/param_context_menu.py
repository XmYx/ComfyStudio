import copy

from PyQt6.QtWidgets import QApplication, QMessageBox, QFileDialog


# Default actions.
def set_prev_image(window, param):
    param["usePrevResultImage"] = True
    param["usePrevResultVideo"] = False
    param["value"] = "(Awaiting previous workflow image)"
    QMessageBox.information(
        window,
        "Info",
        "This parameter is now flagged to use the previous workflow's image result."
    )

def set_prev_video(window, param):
    param["usePrevResultVideo"] = True
    param["usePrevResultImage"] = False
    param["value"] = "(Awaiting previous workflow video)"
    QMessageBox.information(
        window,
        "Info",
        "This parameter is now flagged to use the previous workflow's video result."
    )

def clear_dyn_override(window, param):
    param.pop("usePrevResultImage", None)
    param.pop("usePrevResultVideo", None)
    QMessageBox.information(window, "Info", "Dynamic override cleared.")

def import_files_for_param(window, param):
    """
    Opens a file dialog to import files. If multiple files (or multiple lines in a text file)
    are imported, the callback ensures that there are enough shots (cloning the last shot if needed)
    and then updates each shot’s workflow-level parameter (identified by the same name) with the
    imported value. This uses the currently selected workflow item from the workflow list.
    """
    file_filter = "Images (*.png *.jpg *.bmp);;Videos (*.mp4 *.avi);;Text Files (*.txt)"
    files, _ = QFileDialog.getOpenFileNames(window, "Import Files", "", file_filter)
    if not files:
        return

    # Determine if we're importing text (multiple lines) or media files.
    ext = files[0].split('.')[-1].lower()
    if ext == "txt":
        try:
            with open(files[0], "r", encoding="utf-8") as f:
                imported_values = [line.strip() for line in f if line.strip()]
        except Exception as e:
            QMessageBox.critical(window, "Error", f"Failed to read text file:\n{e}")
            return
    else:
        imported_values = files

    # Determine if this parameter is a workflow-level parameter.
    # (Workflow params typically include "nodeIDs".)
    is_workflow_param = "nodeIDs" in param

    # For workflow-level parameters, we need a workflow list item.
    workflow_item = None
    if is_workflow_param:
        workflow_item = window.workflowListWidget.currentItem()
        if not workflow_item:
            QMessageBox.warning(window, "Error", "No workflow selected.")
            return

    # If there are more imported values than shots, clone the last shot enough times.
    num_imported = len(imported_values)
    current_num_shots = len(window.shots)
    if num_imported > current_num_shots:
        num_to_create = num_imported - current_num_shots
        last_shot = window.shots[-1]
        for i in range(num_to_create):
            # Clone the last shot.
            new_shot = copy.deepcopy(last_shot)
            new_shot.name = f"{last_shot.name} - Extra {i+1}"
            # Optionally, reset output paths and versions.
            new_shot.stillPath = ""
            new_shot.videoPath = ""
            new_shot.imageVersions = []
            new_shot.videoVersions = []
            new_shot.currentImageVersion = -1
            new_shot.currentVideoVersion = -1
            window.shots.append(new_shot)
        # Update the shots list UI.
        window.updateList()

    # Now iterate through the imported values and update each corresponding shot.
    # (We assume that each shot has the same workflow that should be updated.)
    for idx, imported_value in enumerate(imported_values):
        new_param = param.copy()
        new_param["value"] = imported_value
        # Here, we use the workflow_item (from the workflow list widget) so that
        # setParamValueInShots can retrieve the workflow assignment via data().
        window.setParamValueInShots(new_param, onlySelected=False, item=workflow_item)

    QMessageBox.information(window, "Info", "Imported values have been assigned to shots.")


from PyQt6.QtWidgets import QApplication, QMessageBox, QMenu, QDialog
from PyQt6.QtGui import QCursor

def _get_registry():
    """
    Returns (and initializes if needed) the appwide registry of workflow parameter
    context menu action specifications.
    """
    app = QApplication.instance()
    if app is None:
        raise RuntimeError("QApplication instance not found!")
    if not hasattr(app, "_workflow_param_context_action_specs"):
        # Define the default callbacks.
        def set_prev_image(window, param):
            param["usePrevResultImage"] = True
            param["usePrevResultVideo"] = False
            param["value"] = "(Awaiting previous workflow image)"
            param["dynamicOverrides"] = {
                "type": "shot",
                "shotIndex": window.currentShotIndex,
                "assetType": "image"
            }
            QMessageBox.information(window, "Info",
                                    "This parameter is now flagged to use the previous workflow's image result.")

        def set_prev_video(window, param):
            param["usePrevResultVideo"] = True
            param["usePrevResultImage"] = False
            param["value"] = "(Awaiting previous workflow video)"
            param["dynamicOverrides"] = {
                "type": "shot",
                "shotIndex": window.currentShotIndex,
                "assetType": "video"
            }
            QMessageBox.information(window, "Info",
                                    "This parameter is now flagged to use the previous workflow's video result.")

        def clear_dyn_override(window, param):
            param.pop("usePrevResultImage", None)
            param.pop("usePrevResultVideo", None)
            param.pop("dynamicOverrides", None)
            QMessageBox.information(window, "Info", "Dynamic override cleared.")

        def set_all_selected_shots(window, param):
            currentItem = window.workflowListWidget.currentItem()
            window.setParamValueInShots(param, onlySelected=True, item=currentItem)

        def set_all_shots(window, param):
            currentItem = window.workflowListWidget.currentItem()
            window.setParamValueInShots(param, onlySelected=False, item=currentItem)

        def edit_dynamic_param(window, param):
            # Assumes DynamicParam and DynamicParamEditor are imported appropriately.
            from comfystudio.sdmodules.vareditor import DynamicParam, DynamicParamEditor
            dyn_param = DynamicParam(
                name=param.get("name", ""),
                param_type=param.get("type", "string"),
                value=param.get("value", ""),
                expression=param.get("expression", ""),
                global_var=param.get("global_var", "")
            )
            editor = DynamicParamEditor(dyn_param, window.global_vars, window)
            if editor.exec() == QDialog.DialogCode.Accepted:
                param["value"] = dyn_param.value
                param["expression"] = dyn_param.expression
                param["global_var"] = dyn_param.global_var
                QMessageBox.information(window, "Info", "Dynamic parameter updated.")

        # Initialize the registry with default action specs.
        # For "string" parameters we include the first three actions,
        # while the remaining three are common (available for both string and non-string).
        app._workflow_param_context_action_specs = [
            {
                "text": "Set Param to Previous Workflow's Image",
                "callback": set_prev_image,
                "param_types": ["string"]
            },
            {
                "text": "Set Param to Previous Workflow's Video",
                "callback": set_prev_video,
                "param_types": ["string"]
            },
            {
                "text": "Clear Dynamic Override",
                "callback": clear_dyn_override,
                "param_types": ["string"]
            },
            {
                "text": "Set All SELECTED Shots (this param)",
                "callback": set_all_selected_shots,
                "param_types": ["string", "other"]
            },
            {
                "text": "Set ALL Shots (this param)",
                "callback": set_all_shots,
                "param_types": ["string", "other"]
            },
            {
                "text": "Edit as Dynamic Parameter",
                "callback": edit_dynamic_param,
                "param_types": ["string", "other"]
            },
            {
                "text": "Import Files for Parameter",
                "callback": import_files_for_param,
                "param_types": ["string", "other"]
            }
        ]
    return app._workflow_param_context_action_specs


def register_param_context_action_spec(action_spec):
    """
    Register an additional action spec in the appwide registry.
    """
    registry = _get_registry()
    registry.append(action_spec)

def unregister_param_context_action_spec(action_text):
    """
    Optionally, remove an action by its display text.
    """
    registry = _get_registry()
    filtered = [spec for spec in registry if spec.get("text") != action_text]
    # Replace the registry list in the app instance.
    QApplication.instance()._param_context_action_specs = filtered

def bind_actions(window, param, action_specs):
    """
    Wrap each callback so it receives the current window and parameter object.
    """
    bound_actions = []
    for action in action_specs:
        new_action = action.copy()
        if "callback" in new_action and callable(new_action["callback"]):
            original_callback = new_action["callback"]
            # Using a default argument in the lambda avoids late binding issues.
            new_action["callback"] = lambda oc=original_callback: oc(window, param)
        bound_actions.append(new_action)
    return bound_actions

def get_param_context_action_specs(window, param, extra_specs=None):
    """
    Returns an extendable list of action specifications (with callbacks bound to
    the current window and parameter). extra_specs, if provided, are appended.
    Always reads from the appwide registry.
    """
    # Always fetch a fresh copy of the registry.
    action_specs = list(_get_registry())
    if extra_specs:
        action_specs.extend(extra_specs)
    return bind_actions(window, param, action_specs)
