# plugins/exporter.py

import os
import subprocess
import copy

from qtpy.QtWidgets import (
    QAction, QDialog, QFormLayout, QLineEdit, QComboBox, QCheckBox, QHBoxLayout,
    QVBoxLayout, QDialogButtonBox, QLabel, QFileDialog, QMessageBox, QPushButton
)

def register(app):
    exporterAction = QAction("Export Project", app)
    file_menu = None
    for action in app.menuBar().actions():
        if action.text() == "File":
            file_menu = action.menu()
            break
    if file_menu:
        file_menu.addAction(exporterAction)
    exporterAction.triggered.connect(lambda: openExportDialog(app))

def openExportDialog(app):
    dialog = ExportShotsDialog(app)
    if dialog.exec() == QDialog.Accepted:
        config = dialog.getConfig()
        performExport(app, config, copy.deepcopy(app.shots))

def performExport(app, config, shots):
    # shots = copy.deepcopy(app.shots)
    # Gather list of final video files from each shot
    # according to user "single" or "individual" preference
    final_paths = []
    for shot in shots:
        # We always use the selected versions
        # which is the shot["videoPath"] for video, if it exists
        path = shot.get("videoPath", "")
        if path and os.path.exists(path):
            final_paths.append(path)

    if not final_paths:
        QMessageBox.information(app, "Export", "No valid videos found.")
        return

    dest = config["dest"]
    codec = config["codec"]
    do_merge = config["merge"]
    custom_args = config["custom_args"]

    if do_merge:
        # Merge into single clip
        if not dest:
            QMessageBox.warning(app, "Export", "No output file selected.")
            return
        # Create a file list for ffmpeg
        list_path = os.path.join(os.path.dirname(dest), "merge_list.txt")
        try:
            with open(list_path, "w") as f:
                for fp in final_paths:
                    f.write(f"file '{fp}'\n")
        except Exception as e:
            QMessageBox.critical(app, "Export Error", str(e))
            return

        # basic example ffmpeg command
        cmd = [
            "ffmpeg",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_path,
            "-c:v", codec,
            "-c:a", "aac",
        ]
        if custom_args.strip():
            cmd.extend(custom_args.split(" "))
        cmd.append(dest)
        runFFmpeg(cmd, app)
        try:
            os.remove(list_path)
        except:
            pass
    else:
        # Export as individual clips
        if not dest or not os.path.isdir(dest):
            QMessageBox.warning(app, "Export", "Please select a valid destination folder.")
            return
        for i, fp in enumerate(final_paths):
            out_name = f"shot_{i+1:03d}.mp4"
            out_full = os.path.join(dest, out_name)
            cmd = [
                "ffmpeg",
                "-y",
                "-i", fp,
                "-c:v", codec,
                "-c:a", "aac"
            ]
            if custom_args.strip():
                cmd.extend(custom_args.split(" "))
            cmd.append(out_full)
            runFFmpeg(cmd, app)

    QMessageBox.information(app, "Export", "Export completed.")

def runFFmpeg(cmd, app):
    try:
        subprocess.run(cmd, check=True)
    except Exception as e:
        QMessageBox.critical(app, "FFmpeg Error", str(e))

class ExportShotsDialog(QDialog):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export Shots")
        self.app = app

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.mergeCheck = QCheckBox("Export as a single merged clip")
        form.addRow(self.mergeCheck)

        self.codecEdit = QComboBox()
        self.codecEdit.addItems(["libx264", "libx265", "mpeg4", "vp9"])
        form.addRow("Video Codec:", self.codecEdit)

        self.customArgsEdit = QLineEdit()
        form.addRow("Custom FFmpeg Args:", self.customArgsEdit)

        self.destEdit = QLineEdit()
        destBtnLayout = QHBoxLayout()
        destBtn = QPushButton("Browse...")
        destBtnLayout.addWidget(self.destEdit)
        destBtnLayout.addWidget(destBtn)
        form.addRow("Destination:", destBtnLayout)

        layout.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        layout.addWidget(btns)

        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        destBtn.clicked.connect(self.onBrowse)

    def onBrowse(self):
        if self.mergeCheck.isChecked():
            # single file
            path, _ = QFileDialog.getSaveFileName(self, "Select Output File", "", "Video Files (*.mp4 *.mov *.avi);;All Files (*)")
            if path:
                self.destEdit.setText(path)
        else:
            # folder
            folder = QFileDialog.getExistingDirectory(self, "Select Output Folder", "")
            if folder:
                self.destEdit.setText(folder)

    def getConfig(self):
        return {
            "merge": self.mergeCheck.isChecked(),
            "codec": self.codecEdit.currentText().strip(),
            "dest": self.destEdit.text().strip(),
            "custom_args": self.customArgsEdit.text().strip()
        }
