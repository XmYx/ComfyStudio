#!/usr/bin/env python
import json
import os

from qtpy.QtCore import (
    QStandardPaths
)
from qtpy.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QFormLayout,
    QPushButton,
    QDialog
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
        self.comfyIpEdit = QLineEdit(self.settingsManager.get("comfy_ip", "http://localhost:8188"))
        layout.addRow("ComfyUI IP/Port:", self.comfyIpEdit)
        self.comfyPyPathEdit = QLineEdit(self.settingsManager.get("comfy_py_path", ""))
        layout.addRow("Comfy Python Path:", self.comfyPyPathEdit)
        self.comfyMainPathEdit = QLineEdit(self.settingsManager.get("comfy_main_path", ""))
        layout.addRow("Comfy Main Path:", self.comfyMainPathEdit)

        btnLayout = QHBoxLayout()
        okBtn = QPushButton("OK")
        cancelBtn = QPushButton("Cancel")
        btnLayout.addWidget(okBtn)
        btnLayout.addWidget(cancelBtn)
        layout.addRow(btnLayout)

        okBtn.clicked.connect(self.accept)
        cancelBtn.clicked.connect(self.reject)

    def accept(self):
        self.settingsManager.set("comfy_ip", self.comfyIpEdit.text().strip())
        self.settingsManager.set("comfy_py_path", self.comfyPyPathEdit.text().strip())
        self.settingsManager.set("comfy_main_path", self.comfyMainPathEdit.text().strip())
        self.settingsManager.save()
        super().accept()