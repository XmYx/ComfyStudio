#!/usr/bin/env python
import json
import os
import sys

from qtpy.QtCore import (
    QStandardPaths
)
from qtpy.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QFormLayout,
    QPushButton,
    QDialog,
    QFileDialog,
    QLabel
)


class SettingsManager:
    def __init__(self):
        self.settings_file = os.path.join(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation),
            "CinemaShotDesigner",
            "user_settings.json"
        )

        self.data = {
            "comfy_py_path": "",
            "comfy_main_path": "",
            "comfy_ip": "http://localhost:8188",
            "default_shot_params": [],
            "default_image_params": [],
            "default_video_params": [
                {
                    "type": "image",
                    "name": "Image",
                    "value": "",
                    "useShotImage": True,
                    "nodeIDs": ["1"]
                },
                {
                    "type": "video",
                    "name": "Video",
                    "value": "",
                    "useShotVideo": True,
                    "nodeIDs": ["2"]
                },
            ],
            "workflow_params": {}
        }
        self.load()

    def load(self):
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, "r") as f:
                    self.data.update(json.load(f))
            else:
                # Load defaults from defaults/config.json if user_settings.json doesn't exist
                default_config = os.path.join(os.path.dirname(os.path.dirname(__file__)), "defaults", "config.json")
                if os.path.exists(default_config):
                    with open(default_config, "r") as df:
                        self.data.update(json.load(df))
            if "workflow_params" not in self.data:
                self.data["workflow_params"] = {}
        except Exception as e:
            print(f"Error loading configuration: {e}")

    def save(self):
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
        with open(self.settings_file, "w") as f:
            json.dump(self.data, f, indent=4)

    def set(self, key, value):
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)


class SettingsDialog(QDialog):
    def __init__(self, settingsManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.settingsManager = settingsManager
        layout = QFormLayout(self)

        # Comfy IP
        self.comfyIpEdit = QLineEdit(self.settingsManager.get("comfy_ip", "http://localhost:8188"))
        layout.addRow("ComfyUI IP/Port:", self.comfyIpEdit)

        # Comfy Python Path with Browse Button
        self.comfyPyPathEdit = QLineEdit(self.settingsManager.get("comfy_py_path", ""))
        self.comfyPyBrowseBtn = QPushButton("Browse")
        self.comfyPyBrowseBtn.clicked.connect(self.browse_comfy_py_path)
        comfyPyLayout = QHBoxLayout()
        comfyPyLayout.addWidget(self.comfyPyPathEdit)
        comfyPyLayout.addWidget(self.comfyPyBrowseBtn)
        layout.addRow("Comfy Python Path:", comfyPyLayout)

        # Comfy Main Path with Browse Button
        self.comfyMainPathEdit = QLineEdit(self.settingsManager.get("comfy_main_path", ""))
        self.comfyMainBrowseBtn = QPushButton("Browse")
        self.comfyMainBrowseBtn.clicked.connect(self.browse_comfy_main_path)
        comfyMainLayout = QHBoxLayout()
        comfyMainLayout.addWidget(self.comfyMainPathEdit)
        comfyMainLayout.addWidget(self.comfyMainBrowseBtn)
        layout.addRow("Comfy Main Path:", comfyMainLayout)

        # Comfy Image Workflows with Browse Button
        self.comfyImageWorkflowEdit = QLineEdit(self.settingsManager.get("comfy_image_workflows", ""))
        self.comfyImageWorkflowBrowseBtn = QPushButton("Browse")
        self.comfyImageWorkflowBrowseBtn.clicked.connect(self.browse_comfy_image_workflows)
        comfyImageWorkflowLayout = QHBoxLayout()
        comfyImageWorkflowLayout.addWidget(self.comfyImageWorkflowEdit)
        comfyImageWorkflowLayout.addWidget(self.comfyImageWorkflowBrowseBtn)
        layout.addRow("Comfy Image Workflows:", comfyImageWorkflowLayout)

        # Comfy Video Workflows with Browse Button
        self.comfyVideoWorkflowEdit = QLineEdit(self.settingsManager.get("comfy_video_workflows", ""))
        self.comfyVideoWorkflowBrowseBtn = QPushButton("Browse")
        self.comfyVideoWorkflowBrowseBtn.clicked.connect(self.browse_comfy_video_workflows)
        comfyVideoWorkflowLayout = QHBoxLayout()
        comfyVideoWorkflowLayout.addWidget(self.comfyVideoWorkflowEdit)
        comfyVideoWorkflowLayout.addWidget(self.comfyVideoWorkflowBrowseBtn)
        layout.addRow("Comfy Video Workflows:", comfyVideoWorkflowLayout)

        # Buttons
        btnLayout = QHBoxLayout()
        okBtn = QPushButton("OK")
        cancelBtn = QPushButton("Cancel")
        btnLayout.addWidget(okBtn)
        btnLayout.addWidget(cancelBtn)
        layout.addRow(btnLayout)

        okBtn.clicked.connect(self.accept)
        cancelBtn.clicked.connect(self.reject)

    def browse_comfy_py_path(self):
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        if sys.platform.startswith('win'):
            filter_str = "Python Executable (*.exe);;All Files (*)"
        else:
            filter_str = "Python Executable (*)"
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Comfy Python Executable",
            self.comfyPyPathEdit.text(),
            filter_str,
            options=options
        )
        if file_path:
            self.comfyPyPathEdit.setText(file_path)

    def browse_comfy_main_path(self):
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        if sys.platform.startswith('win'):
            filter_str = "Executable Files (*.exe);;All Files (*)"
        else:
            filter_str = "Executable Files (*)"
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Comfy Main Executable",
            self.comfyMainPathEdit.text(),
            filter_str,
            options=options
        )
        if file_path:
            self.comfyMainPathEdit.setText(file_path)

    def browse_comfy_image_workflows(self):
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Comfy Image Workflows Folder",
            self.comfyImageWorkflowEdit.text(),
            QFileDialog.ShowDirsOnly | QFileDialog.DontUseNativeDialog
        )
        if directory:
            self.comfyImageWorkflowEdit.setText(directory)

    def browse_comfy_video_workflows(self):
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Comfy Video Workflows Folder",
            self.comfyVideoWorkflowEdit.text(),
            QFileDialog.ShowDirsOnly | QFileDialog.DontUseNativeDialog
        )
        if directory:
            self.comfyVideoWorkflowEdit.setText(directory)

    def accept(self):
        self.settingsManager.set("comfy_ip", self.comfyIpEdit.text().strip())
        self.settingsManager.set("comfy_py_path", self.comfyPyPathEdit.text().strip())
        self.settingsManager.set("comfy_main_path", self.comfyMainPathEdit.text().strip())
        self.settingsManager.set("comfy_image_workflows", self.comfyImageWorkflowEdit.text().strip())
        self.settingsManager.set("comfy_video_workflows", self.comfyVideoWorkflowEdit.text().strip())
        self.settingsManager.save()
        super().accept()
