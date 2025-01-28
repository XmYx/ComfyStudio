#!/usr/bin/env python
import sys

from qtpy.QtWidgets import (
    QApplication
)

from comfystudio.sdmodules.mainwindow import MainWindow

def main():
    from comfystudio.sdmodules.qss import qss
    app = QApplication(sys.argv)
    app.setStyleSheet(qss)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
