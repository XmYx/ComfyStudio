from qtpy.QtCore import Qt
from qtpy.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout

class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About ComfyStudio")
        self.resize(400, 200)
        self.initUI()

    def initUI(self):
        layout = QVBoxLayout(self)

        # Application Information
        app_name = QLabel("<h2>ComfyStudio</h2>")
        version = QLabel("Version: 0.3.1")
        author = QLabel("Author: Miklós Nagy")
        license_info = QLabel("License: MIT License")
        app_name.setAlignment(Qt.AlignCenter)
        version.setAlignment(Qt.AlignCenter)
        author.setAlignment(Qt.AlignCenter)
        license_info.setAlignment(Qt.AlignCenter)

        layout.addWidget(app_name)
        layout.addWidget(version)
        layout.addWidget(author)
        layout.addWidget(license_info)

        # OK Button
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self.accept)
        button_layout.addWidget(ok_button)
        layout.addLayout(button_layout)