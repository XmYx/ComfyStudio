#!/usr/bin/env python
import sys

from qtpy.QtCore import QCoreApplication, Qt
from qtpy.QtWidgets import (
    QApplication,
    QStyleFactory
)

# from comfystudio.sdmodules.mainwindow import MainWindow
from comfystudio.sdmodules.core.mainwindow import ComfyStudioWindow


def main():
    from comfystudio.sdmodules.qss import get_stylesheet
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    # app.setStyle(QStyleFactory.create("Fusion"))
    window = ComfyStudioWindow()
    # Apply the theme chosen in settings (defaults to dark).
    app.setStyleSheet(get_stylesheet(window.settingsManager.get("theme", "dark")))

    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
