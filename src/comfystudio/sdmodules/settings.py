#!/usr/bin/env python
import json
import os
import sys

from PyQt6.QtWidgets import QComboBox
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
            if "language" not in self.data:
                self.data["language"] = "en"
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
    def __init__(self, settingsManager, localization_manager, parent=None):
        super().__init__(parent)
        self.settingsManager = settingsManager
        self.localization = localization_manager
        self.setWindowTitle("Settings")

        layout = QFormLayout(self)

        # Language Selection
        lang_layout = QHBoxLayout()
        lang_label = QLabel(self.localization.translate("label_language", default="Language:"))
        self.lang_combo = QComboBox()

        # self.languages = [
        #     ("Afrikaans", "af"),  # Afrikaans
        #     ("العربية", "ar"),  # Arabic
        #     ("বাংলা", "bn"),  # Bengali
        #     ("简体中文", "cn"),  # Chinese Simplified
        #     ("繁體中文", "ct"),  # Chinese Traditional
        #     ("Deutsch", "de"),
        #     ("English", "en"),
        #     ("Español", "es"),
        #     ("Français", "fr"),
        #     ("Frysk", "fy"),  # Frieslandic (West Frisian)
        #     ("עברית", "he"),
        #     ("हिन्दी", "hi"),  # Hindi
        #     ("Italiano", "it"),  # Italian
        #     ("Íslenska", "is"),  # Icelandic
        #     ("Japanese", "jp"),
        #     ("한국어", "ko"), # Korean
        #     ("Magyar", "hu"),
        #     ("Nederlands", "nl"),  # Dutch
        #     ("Português", "pt"),  # Portuguese
        #     ("Svenska", "sv"),
        #     ("Українська", "ur"),  # Ukrainian
        #     ("Русский", "ru"),  # Russian
        #     ("தமிழ்", "ta"),  # Tamil
        # ]

        self.languages = [
            ("Afrikaans", "af"),  # Afrikaans
            ("العربية", "ar"),  # Arabic
            ("বাংলা", "bn"),  # Bengali
            ("简体中文", "cn"),  # Chinese Simplified (Mandarin)
            ("繁體中文", "ct"),  # Chinese Traditional (Mandarin)
            # ("Cantonese", "yue"),  # Cantonese (New)
            ("Deutsch", "de"),  # German
            ("English", "en"),  # English
            ("Español", "es"),  # Spanish
            ("Français", "fr"),  # French
            ("Frysk", "fy"),  # Frieslandic (West Frisian)
            ("עברית", "he"),  # Hebrew
            ("हिन्दी", "hi"),  # Hindi
            # ("मराठी", "mr"),  # Marathi (New)
            # ("ગુજરાતી", "gu"),  # Gujarati (New)
            # ("ਪੰਜਾਬੀ", "pa"),  # Punjabi (New)
            # ("Bhojpuri", "bho"),  # Bhojpuri (New)
            ("Italiano", "it"),  # Italian
            ("Íslenska", "is"),  # Icelandic
            ("日本語", "jp"),  # Japanese
            ("한국어", "ko"),  # Korean
            ("Magyar", "hu"),  # Hungarian
            ("Nederlands", "nl"),  # Dutch
            ("Português", "pt"),  # Portuguese
            ("Svenska", "sv"),  # Swedish
            ("Українська", "ur"),  # Ukrainian
            ("Русский", "ru"),  # Russian
            ("தமிழ்", "ta"),  # Tamil
            ("తెలుగు", "te"),  # Telugu
            # ("മലയാളം", "ml"),  # Malayalam (New)
            # ("සිංහල", "si"),  # Sinhala (New)
            ("فارسی", "fa"),  # Persian (Farsi)
            ("Türkçe", "tr"),  # Turkish
            ("Polski", "pl"),  # Polish
            ("Čeština", "cs"),  # Czech
            ("Ελληνικά", "el"),  # Greek
            ("Suomi", "fi"),  # Finnish
            ("Dansk", "da"),  # Danish
            ("Norsk", "no"),  # Norwegian
            ("Kiswahili", "sw"),  # Swahili
            ("ไทย", "th"),  # Thai
            ("Tiếng Việt", "vi"),  # Vietnamese
            ("Bahasa Melayu", "ms"),  # Malay
            ("Tagalog", "tl"),  # Tagalog (Filipino)
            ("Euskara", "eu"),  # Basque
            ("Cymraeg", "cy"),  # Welsh
            ("Gaeilge", "ga"),  # Irish Gaelic
            ("Gàidhlig", "gd"),  # Scottish Gaelic
            ("Hausa", "ha"),  # Hausa
            ("Yorùbá", "yo"),  # Yoruba
            ("IsiZulu", "zu"),  # Zulu
            # ("Burmese", "my"),  # Burmese (New)
            # ("Javanese", "jv"),  # Javanese (New)
            # ("Sundanese", "su"),  # Sundanese (New)
            # ("Amharic", "am"),  # Amharic (New)
            # ("Igbo", "ig"),  # Igbo (New)
            # ("Twi", "tw"),  # Twi (New)
            # ("Pashto", "ps"),  # Pashto (New)
            # ("Uzbek", "uz"),  # Uzbek (New)
            # ("Mongolian", "mn"),  # Mongolian (New)
            # ("Serbian", "sr"),  # Serbian (New)
            # ("Croatian", "hr"),  # Croatian (New)
            # ("Slovak", "sk"),  # Slovak (New)
            # ("Slovenian", "sl"),  # Slovenian (New)
            # ("Māori", "mi"),  # Māori (New)
            # ("Sami", "se"),  # Sami (New)
            # ("Tatar", "tt")  # Tatar (New)
        ]

        for name, code in self.languages:
            self.lang_combo.addItem(name, code)
        current_lang = self.settingsManager.get("language", "en")
        index = self.lang_combo.findData(current_lang)
        if index != -1:
            self.lang_combo.setCurrentIndex(index)
        lang_layout.addWidget(self.lang_combo)
        layout.addRow(lang_label, lang_layout)

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
        selected_lang_code = self.lang_combo.currentData()
        self.settingsManager.set("comfy_ip", self.comfyIpEdit.text().strip())
        self.settingsManager.set("comfy_py_path", self.comfyPyPathEdit.text().strip())
        self.settingsManager.set("comfy_main_path", self.comfyMainPathEdit.text().strip())
        self.settingsManager.set("comfy_image_workflows", self.comfyImageWorkflowEdit.text().strip())
        self.settingsManager.set("comfy_video_workflows", self.comfyVideoWorkflowEdit.text().strip())
        self.settingsManager.set("language", selected_lang_code)
        self.settingsManager.save()
        super().accept()
