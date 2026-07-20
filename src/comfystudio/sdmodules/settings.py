#!/usr/bin/env python
import json
import os
import sys

from qtpy.QtWidgets import QComboBox
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
            "comfy_ip": "http://127.0.0.1:8188",
            "workflow_params": {}
        }
        if "recent_files" not in self.data:
            self.data["recent_files"] = []
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
            ("L33T", "lt"),
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

        # Theme selection
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Dark", "dark")
        self.theme_combo.addItem("Light", "light")
        current_theme = self.settingsManager.get("theme", "dark")
        theme_idx = self.theme_combo.findData(current_theme)
        if theme_idx != -1:
            self.theme_combo.setCurrentIndex(theme_idx)
        layout.addRow(self.localization.translate("label_theme", default="Theme:"), self.theme_combo)

        # Comfy IP
        self.comfyIpEdit = QLineEdit(self.settingsManager.get("comfy_ip", "http://127.0.0.1:8188"))
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

        # Comfy Workflows folder with Browse Button. Workflows are type-agnostic
        # now (the output type is detected at render time), so this is a single
        # optional fallback library folder — the primary library is ComfyUI's own
        # user/default/workflows, derived from the Comfy Main Path above.
        # Migrate the old split image/video settings into the unified one.
        workflows_dir = (self.settingsManager.get("comfy_workflows", "")
                         or self.settingsManager.get("comfy_image_workflows", "")
                         or self.settingsManager.get("comfy_video_workflows", ""))
        self.comfyWorkflowEdit = QLineEdit(workflows_dir)
        self.comfyWorkflowBrowseBtn = QPushButton("Browse")
        self.comfyWorkflowBrowseBtn.clicked.connect(self.browse_comfy_workflows)
        comfyWorkflowLayout = QHBoxLayout()
        comfyWorkflowLayout.addWidget(self.comfyWorkflowEdit)
        comfyWorkflowLayout.addWidget(self.comfyWorkflowBrowseBtn)
        layout.addRow("Comfy Workflows Folder:", comfyWorkflowLayout)

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
        # PyQt6 has no QFileDialog.Options() factory — pass no options.
        if sys.platform.startswith('win'):
            filter_str = "Python Executable (*.exe);;All Files (*)"
        else:
            filter_str = "Python Executable (*)"
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Comfy Python Executable",
            self.comfyPyPathEdit.text(),
            filter_str
        )
        if file_path:
            self.comfyPyPathEdit.setText(file_path)

    def browse_comfy_main_path(self):
        if sys.platform.startswith('win'):
            filter_str = "Executable Files (*.exe);;All Files (*)"
        else:
            filter_str = "Executable Files (*)"
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Comfy Main Executable",
            self.comfyMainPathEdit.text(),
            filter_str
        )
        if file_path:
            self.comfyMainPathEdit.setText(file_path)

    def browse_comfy_workflows(self):
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Comfy Workflows Folder",
            self.comfyWorkflowEdit.text(),
            QFileDialog.Option.ShowDirsOnly
        )
        if directory:
            self.comfyWorkflowEdit.setText(directory)

    def accept(self):
        selected_lang_code = self.lang_combo.currentData()
        self.settingsManager.set("theme", self.theme_combo.currentData())
        self.settingsManager.set("comfy_ip", self.comfyIpEdit.text().strip())
        self.settingsManager.set("comfy_py_path", self.comfyPyPathEdit.text().strip())
        self.settingsManager.set("comfy_main_path", self.comfyMainPathEdit.text().strip())
        workflows_dir = self.comfyWorkflowEdit.text().strip()
        self.settingsManager.set("comfy_workflows", workflows_dir)
        # Keep the legacy keys in sync so older code paths and downgrades still
        # resolve the same library folder.
        self.settingsManager.set("comfy_image_workflows", workflows_dir)
        self.settingsManager.set("comfy_video_workflows", workflows_dir)
        self.settingsManager.set("language", selected_lang_code)
        self.settingsManager.save()
        super().accept()
