#!/usr/bin/env python
import sys

from PyQt6.QtCore import QCoreApplication, Qt
from qtpy.QtWidgets import (
    QApplication,
    QStyleFactory
)

# from comfystudio.sdmodules.mainwindow import MainWindow
from comfystudio.sdmodules.core.mainwindow import ComfyStudioWindow


def main():
    from comfystudio.sdmodules.qss import qss
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    # app.setStyle(QStyleFactory.create("Fusion"))
    app.setStyleSheet(qss)
    # window = MainWindow()
    window = ComfyStudioWindow()

    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
