import os
import sys
import json
import random
import tempfile
import urllib
import requests

from PyQt6.QtCore import Qt, QTimer, QPointF, pyqtSlot, QThreadPool
from PyQt6.QtGui import QAction, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QListWidget, QTextEdit,
    QVBoxLayout, QHBoxLayout, QLabel, QDockWidget, QFormLayout,
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QFileDialog,
    QMessageBox, QInputDialog, QPushButton
)
import qtpynodeeditor as nodeeditor
from qtpynodeeditor import NodeData, NodeDataModel, NodeDataType, PortType, NodeValidationState
from qtpynodeeditor.port import Port

from comfystudio.sdmodules.worker import RenderWorker

# Do not monkey-patch NodeDataType: we will rely on creating NodeDataType instances with
# their id set to the normalized type (for equality checking) and their name used only for display.

SERVER_URL = "http://127.0.0.1:8188"

def fetch_node_info():
    try:
        response = requests.get(f"{SERVER_URL}/api/object_info")
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print("Error fetching node info:", e)
        return {}

class ParameterData(NodeData):
    data_type = NodeDataType("parameter", "Parameter")
    def __init__(self, value=""):
        self._value = value
    @property
    def value(self):
        return self._value
    def __str__(self):
        return str(self._value)

class ComfyNodeDataModel(NodeDataModel):
    # The data_type here is used only as a default identifier.
    data_type = NodeDataType("comfy", "Comfy Node")
    def __init__(self, node_name, node_info, parent=None):
        super().__init__(parent=parent)
        self.name = node_name
        self.node_info = node_info
        self.parameters = {}
        self.connection_params = {}
        self.init_ui()
    def init_ui(self):
        widget = QWidget()
        layout = QFormLayout(widget)
        self.parameter_edits = {}
        inputs = self.node_info.get("input", {})
        combined = {}
        if isinstance(inputs, dict):
            for key, value in inputs.items():
                if isinstance(value, dict):
                    combined.update(value)
                else:
                    combined[key] = value
        else:
            combined = inputs
        ordered_keys = []
        input_order = self.node_info.get("input_order", {})
        if isinstance(input_order, dict):
            for group in ["required", "optional"]:
                group_keys = input_order.get(group, [])
                ordered_keys.extend(group_keys)
        if not ordered_keys:
            ordered_keys = sorted(combined.keys())
        for param in ordered_keys:
            details = combined.get(param)
            if not details or not isinstance(details, list) or len(details) < 1:
                continue
            param_type = details[0]
            options = details[1] if len(details) > 1 and isinstance(details[1], dict) else {}
            default_val = options.get("default", "")
            if param_type == "INT":
                spin = QSpinBox()
                if "min" in options:
                    spin.setMinimum(int(max(options["min"], -2147483648)))
                if "max" in options:
                    spin.setMaximum(int(min(options["max"], 2147483647)))
                if "step" in options:
                    spin.setSingleStep(int(options["step"]))
                try:
                    spin.setValue(int(default_val))
                except Exception:
                    spin.setValue(0)
                layout.addRow(param, spin)
                spin.valueChanged.connect(lambda val, p=param: self.on_param_changed(p, val))
                self.parameter_edits[param] = spin
                self.parameters[param] = int(default_val) if default_val != "" else 0
            elif param_type == "FLOAT":
                dspin = QDoubleSpinBox()
                try:
                    dspin.setValue(float(default_val))
                except Exception:
                    dspin.setValue(0.0)
                if "min" in options:
                    dspin.setMinimum(float(options["min"]))
                if "max" in options:
                    dspin.setMaximum(float(options["max"]))
                if "step" in options:
                    dspin.setSingleStep(float(options["step"]))
                layout.addRow(param, dspin)
                dspin.valueChanged.connect(lambda val, p=param: self.on_param_changed(p, val))
                self.parameter_edits[param] = dspin
                self.parameters[param] = float(default_val) if default_val != "" else 0.0
            elif param_type == "STRING":
                line = QLineEdit(str(default_val))
                layout.addRow(param, line)
                line.textChanged.connect(lambda text, p=param: self.on_param_changed(p, text))
                self.parameter_edits[param] = line
                self.parameters[param] = default_val
            elif isinstance(param_type, list):
                combo = QComboBox()
                flat_options = []
                for opt in param_type:
                    if isinstance(opt, str):
                        flat_options.append(opt)
                    elif isinstance(opt, list) and opt and isinstance(opt[0], str):
                        flat_options.append(opt[0])
                combo.addItems(flat_options)
                default_item = str(default_val) if default_val else (flat_options[0] if flat_options else "")
                index = combo.findText(default_item)
                if index >= 0:
                    combo.setCurrentIndex(index)
                layout.addRow(param, combo)
                combo.currentTextChanged.connect(lambda text, p=param: self.on_param_changed(p, text))
                self.parameter_edits[param] = combo
                self.parameters[param] = default_item
            else:
                self.connection_params[param] = {
                    "type": param_type,
                    "options": options,
                    "default": default_val
                }
                self.parameters[param] = None
        self._preview_label = QLabel("No preview")
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addRow("Preview:", self._preview_label)
        self._embedded_widget = widget
    def update_preview(self, output_data):
        if self.name == "SaveImage":
            images = output_data.get("images", [])
            if images:
                image_info = images[0]
                comfy_filename = image_info.get("filename", "")
                local_path = self.download_comfy_file(comfy_filename)
                if local_path and os.path.exists(local_path):
                    pix = QPixmap(local_path)
                    if not pix.isNull():
                        scaled = pix.scaled(200, 200, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                        self._preview_label.setPixmap(scaled)
                        return
                    else:
                        self._preview_label.setText("Invalid image file")
                else:
                    self._preview_label.setText("Download failed or file not found")
        elif isinstance(output_data, dict) and "images" in output_data:
            images = output_data["images"]
            if images:
                image_info = images[0]
                filename = image_info.get("filename", "")
                if os.path.exists(filename):
                    pix = QPixmap(filename)
                    if not pix.isNull():
                        scaled = pix.scaled(200, 200, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                        self._preview_label.setPixmap(scaled)
                        return
                    else:
                        self._preview_label.setText("Invalid image file")
                else:
                    self._preview_label.setText("Image not found")
        elif isinstance(output_data, dict) and "text" in output_data:
            self._preview_label.setText(str(output_data["text"]))
            return
        self._preview_label.setText(json.dumps(output_data, indent=2))
    def download_comfy_file(self, comfy_filename):
        comfy_ip = "http://localhost:8188".rstrip("/")
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
        except Exception as e:
            print("Download failed:", e)
            return None
    @property
    def caption(self):
        return self.name
    def on_param_changed(self, param, value):
        self.parameters[param] = value
        self.data_updated.emit(0)
    def embedded_widget(self) -> QWidget:
        return self._embedded_widget
    @property
    def num_ports(self):
        num_input = len(self.connection_params)
        api_out = self.node_info.get("output", [])
        if isinstance(api_out, list) and len(api_out) > 0:
            num_output = len(api_out)
        else:
            num_output = 1
        return {PortType.input: num_input, PortType.output: num_output}
    @property
    def data_type(self):
        num_input = len(self.connection_params)
        input_types = {}
        for i, param in enumerate(self.connection_params.keys()):
            expected = self.connection_params[param].get("type", "STRING").lower()
            # Use the normalized expected type as the id and the original for display.
            input_types[i] = NodeDataType(expected, expected.capitalize())
        api_out = self.node_info.get("output", [])
        api_out_names = self.node_info.get("output_name", [])
        if isinstance(api_out, list) and len(api_out) > 0:
            output_types = {}
            for i in range(len(api_out)):
                name = (api_out_names[i] if i < len(api_out_names) else api_out[i]).lower()
                output_types[i] = NodeDataType(name, name.capitalize())
        else:
            output_types = {0: NodeDataType("result", "Result")}
        return {PortType.input: input_types, PortType.output: output_types}
    @property
    def port_caption(self):
        input_captions = {i: param for i, param in enumerate(self.connection_params.keys())}
        api_out_names = self.node_info.get("output_name", [])
        if isinstance(api_out_names, list) and len(api_out_names) > 0:
            output_captions = {i: name for i, name in enumerate(api_out_names)}
        else:
            output_captions = {0: "Result"}
        for i, cap in output_captions.items():
            if cap in input_captions.values():
                output_captions[i] = f"{cap} (out)"
        return {PortType.input: input_captions, PortType.output: output_captions}
    @property
    def port_caption_visible(self):
        input_visible = {i: True for i in range(len(self.connection_params))}
        api_out = self.node_info.get("output", [])
        if isinstance(api_out, list) and len(api_out) > 0:
            output_visible = {i: True for i in range(len(api_out))}
        else:
            output_visible = {0: True}
        return {PortType.input: input_visible, PortType.output: output_visible}
    def out_data(self, port: int):
        class SimpleData(NodeData):
            data_type = ComfyNodeDataModel.data_type
            def __init__(self, value):
                self.value = value
        result = {"immediate": self.parameters, "connection": self.connection_params}
        return SimpleData(json.dumps(result))
    def set_in_data(self, node_data, port):
        pass
    def compute(self):
        self.data_updated.emit(0)
    def validation_state(self) -> NodeValidationState:
        return NodeValidationState.valid
    def validation_message(self) -> str:
        return ""
    def load_state(self, state: dict):
        self.parameters = state.get("parameters", {})
        for param, widget in self.parameter_edits.items():
            value = self.parameters.get(param, "")
            if isinstance(widget, QLineEdit):
                widget.setText(str(value))
            elif isinstance(widget, QComboBox):
                index = widget.findText(str(value))
                if index >= 0:
                    widget.setCurrentIndex(index)

def make_creator(node_name, info):
    def creator(**kwargs):
        return ComfyNodeDataModel(node_name, info)
    creator.name = node_name
    return creator

class NodeEditorPlugin(QMainWindow):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("ComfyUI Node Editor")
        self.resize(1200, 800)
        self.node_info = fetch_node_info()  # {node_name: node_info, ...}
        self.registry = nodeeditor.DataModelRegistry()
        self.setup_registry()
        self.scene = nodeeditor.FlowScene(registry=self.registry)
        self.view = nodeeditor.FlowView(self.scene)
        self.setup_ui()
        self.create_menus()

    def setup_registry(self):
        for node_name, info in self.node_info.items():
            creator = make_creator(node_name, info)
            self.registry.register_model(creator, category="Comfy Nodes", style=None, name=node_name)

    def setup_ui(self):
        central_widget = QWidget()
        main_layout = QHBoxLayout(central_widget)
        self.palette_list = QListWidget()
        self.palette_list.setMinimumWidth(150)
        for node_name in self.node_info.keys():
            self.palette_list.addItem(node_name)
        self.palette_list.itemDoubleClicked.connect(self.on_palette_item_double_clicked)
        editor_container = self.view
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        button_layout = QHBoxLayout()
        self.preview_button = QPushButton("Preview JSON")
        self.preview_button.clicked.connect(self.preview_json)
        self.run_button = QPushButton("Trigger Prompt")
        self.run_button.clicked.connect(self.trigger_prompt)
        button_layout.addWidget(self.preview_button)
        button_layout.addWidget(self.run_button)
        self.result_viewer = QTextEdit()
        self.result_viewer.setReadOnly(True)
        right_layout.addLayout(button_layout)
        right_layout.addWidget(QLabel("Returned Results:"))
        right_layout.addWidget(self.result_viewer)
        main_layout.addWidget(self.palette_list, 1)
        main_layout.addWidget(editor_container, 4)
        main_layout.addWidget(right_panel, 2)
        self.setCentralWidget(central_widget)

    def create_menus(self):
        file_menu = self.menuBar().addMenu("File")
        import_action = QAction("Import Flow", self)
        import_action.triggered.connect(self.import_flow)
        save_action = QAction("Save Flow", self)
        save_action.triggered.connect(self.save_flow)
        export_action = QAction("Export JSON", self)
        export_action.triggered.connect(self.export_json)
        file_menu.addAction(import_action)
        file_menu.addAction(save_action)
        file_menu.addAction(export_action)

    def load_json(self, file_path: str):
        """
        Load a JSON file (in the ComfyUI nodegraph format) and reconstruct
        the node graph by creating nodes, setting their widget values, and
        connecting them based on the provided links.
        """
        try:
            with open(file_path, "r") as f:
                data = json.load(f)
        except Exception as e:
            self.result_viewer.setPlainText("Error loading JSON: " + str(e))
            return

        # Clear the current scene
        if hasattr(self.scene, "clear_scene"):
            self.scene.clear_scene()
        else:
            for connection in list(self.scene.connections):
                self.scene.remove_connection(connection)
            for node in list(self.scene.nodes.values()):
                self.scene.remove_node(node)

        node_mapping = {}
        # Process nodes
        for node_info in data.get("nodes", []):
            node_id = node_info.get("id")
            node_type = node_info.get("type")
            pos = node_info.get("pos", [0, 0])
            if isinstance(pos, dict):
                pos = [pos["0"], pos["1"]]
            widgets_values = node_info.get("widgets_values", [])
            try:
                data_model, _ = self.registry.get_model_by_name(node_type)
            except Exception as e:
                self.result_viewer.append(
                    f"Error: Node type '{node_type}' not found. Skipping node id {node_id}."
                )
                continue
            new_node = self.scene.create_node(data_model)
            if hasattr(new_node, "graphics_object"):
                new_node.graphics_object.setPos(QPointF(pos[0], pos[1]))
            if isinstance(widgets_values, list):
                for idx, (param, widget) in enumerate(new_node.model.parameter_edits.items()):
                    if idx < len(widgets_values):
                        value = widgets_values[idx]
                        if isinstance(widget, QLineEdit):
                            widget.setText(str(value))
                        elif isinstance(widget, QComboBox):
                            index = widget.findText(str(value))
                            if index >= 0:
                                widget.setCurrentIndex(index)
            elif isinstance(widgets_values, dict):
                for param, widget in new_node.model.parameter_edits.items():
                    if param in widgets_values:
                        value = widgets_values[param]
                        if isinstance(widget, QLineEdit):
                            widget.setText(str(value))
                        elif isinstance(widget, QComboBox):
                            index = widget.findText(str(value))
                            if index >= 0:
                                widget.setCurrentIndex(index)
            node_mapping[node_id] = new_node

        # Process connections
        for link in data.get("links", []):
            if isinstance(link, list) and len(link) >= 6:
                _, src_node_id, src_port_index, dest_node_id, dest_port_index, _ = link
                src_node = node_mapping.get(src_node_id)
                dest_node = node_mapping.get(dest_node_id)
                if src_node is None or dest_node is None:
                    continue
                src_port = src_node[PortType.output].get(src_port_index)
                dest_port = dest_node[PortType.input].get(dest_port_index)
                if src_port and dest_port:
                    try:
                        self.scene.create_connection(src_port, dest_port)
                    except Exception as e:
                        print(f"Skipping connection from node {src_node_id} to node {dest_node_id} due to error: {e}")

    def on_palette_item_double_clicked(self, item):
        node_type = item.text()
        data_model, _ = self.registry.get_model_by_name(node_type)
        self.scene.create_node(data_model)

    def export_workflow(self):
        new_ids = {}
        nodes_list = []
        for i, (old_id, node) in enumerate(self.scene.nodes.items()):
            new_ids[old_id] = i
            node_dict = {
                "id": i,
                "type": node.model.name,
                "pos": [],
                "size": [],
                "flags": getattr(node.model, "flags", {}),
                "order": getattr(node.model, "order", 0),
                "mode": getattr(node.model, "mode", 0),
                "inputs": getattr(node.model, "inputs", []),
                "outputs": getattr(node.model, "outputs", []),
                "properties": getattr(node.model, "properties", {}),
                "widgets_values": getattr(node.model, "widgets_values", [])
            }
            if hasattr(node, "graphics_object"):
                pos = node.graphics_object.pos()
                node_dict["pos"] = [pos.x(), pos.y()]
                if hasattr(node.graphics_object, "boundingRect"):
                    rect = node.graphics_object.boundingRect()
                    node_dict["size"] = [rect.width(), rect.height()]
            nodes_list.append(node_dict)
        last_node_id = max(new_ids.values()) if new_ids else 0
        links_list = []
        link_id = 1
        for connection in self.scene.connections:
            in_port, out_port = connection.ports
            src_old = out_port.node.id
            dest_old = in_port.node.id
            src_new = new_ids.get(src_old, 0)
            dest_new = new_ids.get(dest_old, 0)
            links_list.append([link_id, src_new, out_port.index, dest_new, in_port.index, ""])
            link_id += 1
        last_link_id = link_id - 1 if link_id > 1 else 0
        extra = {
            "ds": {
                "scale": 0.45,
                "offset": [2242.5291951497397, 778.5084771050349]
            },
            "ue_links": [],
            "VHS_latentpreview": False,
            "VHS_latentpreviewrate": 0
        }
        workflow = {
            "last_node_id": last_node_id,
            "last_link_id": last_link_id,
            "nodes": nodes_list,
            "links": links_list,
            "groups": [],
            "config": {},
            "extra": extra,
            "version": 0.4,
            "api_prompt": self.export_workflow_api()
        }
        return workflow

    def export_workflow_api(self):
        new_ids = {}
        self._last_export_mapping = {}
        new_id = 0
        for old_id, node in self.scene.nodes.items():
            new_ids[old_id] = str(new_id)
            self._last_export_mapping[str(new_id)] = node
            new_id += 1
        result = {}
        for old_id, node in self.scene.nodes.items():
            new_node_id = new_ids[old_id]
            inputs = dict(node.model.parameters)
            input_ports = node[PortType.input]
            for port_index, port in input_ports.items():
                key = node.model.port_caption[PortType.input].get(port_index)
                if not key:
                    continue
                if port.connections:
                    conn = port.connections[0]
                    in_port, out_port = conn.ports
                    connected_node_id = new_ids.get(out_port.node.id, str(out_port.node.id))
                    inputs[key] = [connected_node_id, int(out_port.index)]
            node_dict = {
                "inputs": inputs,
                "class_type": node.model.name,
                "_meta": {
                    "title": getattr(node.model, "display_name", node.model.name)
                }
            }
            result[new_node_id] = node_dict
        return result

    def preview_json(self):
        workflow_json = self.export_workflow_api()
        preview_text = json.dumps(workflow_json, indent=2)
        self.result_viewer.setPlainText("Preview Workflow JSON:\n" + preview_text)

    def trigger_prompt(self):
        api_json = self.export_workflow_api()
        worker = RenderWorker(
            workflow_json=api_json,
            shotIndex=0,
            isVideo=False,
            comfy_ip=SERVER_URL,
            parent=self
        )
        worker.signals.result.connect(self.onRenderResult)
        worker.signals.error.connect(lambda msg: self.result_viewer.setPlainText("Error: " + msg))
        worker.signals.finished.connect(lambda: self.result_viewer.append("\nRenderWorker finished."))
        QThreadPool.globalInstance().start(worker)

    @pyqtSlot(dict, int, bool)
    def onRenderResult(self, result_data, shotIndex, isVideo):
        if not result_data:
            self.result_viewer.setPlainText("Empty result")
            return
        prompt_key = next(iter(result_data))
        res = result_data[prompt_key]
        outputs = res.get("outputs", {})
        for new_id, output in outputs.items():
            node = self._last_export_mapping.get(new_id)
            if node is not None:
                node.model.update_preview(output)
        self.result_viewer.setPlainText("Prompt queued successfully:\n" + json.dumps(result_data, indent=2))

    def poll_results(self, prompt_id):
        def poll():
            try:
                r = requests.get(f"{SERVER_URL}/history/{prompt_id}")
                if r.status_code == 200:
                    history = r.json()
                    if history:
                        self.result_viewer.append("\nResults:\n" + json.dumps(history, indent=2))
                        self.poll_timer.stop()
                    else:
                        self.result_viewer.append("Polling... no results yet.")
                else:
                    self.result_viewer.append("Polling error: " + r.text)
            except Exception as ex:
                self.result_viewer.append("Polling exception: " + str(ex))
        self.poll_timer = QTimer()
        self.poll_timer.timeout.connect(poll)
        self.poll_timer.start(3000)

    def import_flow(self):
        file_name, _ = QFileDialog.getOpenFileName(self, "Import Flow", "", "JSON Files (*.json)")
        if not file_name:
            return
        try:
            self.load_json(file_name)
        except Exception as e:
            self.result_viewer.setPlainText("Error reading file: " + str(e))
            return

    def save_flow(self):
        file_name, _ = QFileDialog.getSaveFileName(self, "Save Flow", "", "JSON Files (*.json)")
        if not file_name:
            return
        data = self.export_workflow()
        try:
            with open(file_name, "w") as f:
                json.dump(data, f, indent=2)
            self.result_viewer.setPlainText(f"Flow saved to {file_name}")
        except Exception as e:
            self.result_viewer.setPlainText("Error saving flow: " + str(e))

    def export_json(self):
        file_name, _ = QFileDialog.getSaveFileName(self, "Export JSON", "", "JSON Files (*.json)")
        if not file_name:
            return
        data = self.export_workflow_api()
        try:
            with open(file_name, "w") as f:
                json.dump(data, f, indent=2)
            self.result_viewer.setPlainText(f"JSON exported to {file_name}")
        except Exception as e:
            self.result_viewer.setPlainText("Error exporting JSON: " + str(e))

def register(app):
    node_editor_plugin = NodeEditorPlugin(app)
    app.setCentralWidget(node_editor_plugin)
    dock_action = QAction("Node Editor Plugin", app)
    menu = None
    for action in app.menuBar().actions():
        if action.text() == "Tools":
            menu = action.menu()
            break
    if menu:
        menu.addAction(dock_action)
    dock_action.triggered.connect(lambda: app.setCentralWidget(node_editor_plugin))
