#!/usr/bin/env python
import os
import subprocess
import copy
import json

from qtpy.QtWidgets import (
    QAction, QDialog, QFormLayout, QLineEdit, QComboBox, QCheckBox, QHBoxLayout,
    QVBoxLayout, QDialogButtonBox, QLabel, QFileDialog, QMessageBox, QPushButton, QSpinBox, QDoubleSpinBox
)
from qtpy.QtCore import Qt

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
        # Save advanced ffmpeg parameters into settings for future sessions.
        app.settingsManager.set("ffmpeg_export_params", config.get("advanced", {}))
        app.settingsManager.save()
        performExport(app, config, copy.deepcopy(app.shots))

def performExport(app, config, shots):
    # Gather list of final video files from each shot.
    final_paths = []
    for shot in shots:
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
    advanced = config.get("advanced", {})

    # Build a list of advanced options for ffmpeg.
    # We add options only if their values are not None (or are non-default)
    adv_options = []
    if advanced.get("video_bitrate"):
        adv_options.extend(["-b:v", f"{advanced['video_bitrate']}k"])
    if advanced.get("crf"):
        adv_options.extend(["-crf", str(advanced["crf"])])
    if advanced.get("frame_rate"):
        adv_options.extend(["-r", str(advanced["frame_rate"])])
    if advanced.get("preset"):
        adv_options.extend(["-preset", advanced["preset"]])
    # Audio options
    audio_codec = advanced.get("audio_codec", "aac")
    adv_options.extend(["-c:a", audio_codec])
    if advanced.get("audio_bitrate"):
        adv_options.extend(["-b:a", f"{advanced['audio_bitrate']}k"])

    if do_merge:
        # Merge into single clip.
        if not dest:
            QMessageBox.warning(app, "Export", "No output file selected.")
            return
        # Create a file list for ffmpeg.
        list_path = os.path.join(os.path.dirname(dest), "merge_list.txt")
        try:
            with open(list_path, "w") as f:
                for fp in final_paths:
                    f.write(f"file '{fp}'\n")
        except Exception as e:
            QMessageBox.critical(app, "Export Error", str(e))
            return

        # Basic ffmpeg command for merging.
        cmd = [
            "ffmpeg",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_path,
            "-c:v", codec,
        ]
        # Append advanced options.
        cmd.extend(adv_options)
        if custom_args.strip():
            cmd.extend(custom_args.split())
        cmd.append(dest)
        runFFmpeg(cmd, app)
        try:
            os.remove(list_path)
        except Exception:
            pass
    else:
        # Export as individual clips.
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
            ]
            # Append advanced options.
            cmd.extend(adv_options)
            if custom_args.strip():
                cmd.extend(custom_args.split())
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

        # Merge option.
        self.mergeCheck = QCheckBox("Export as a single merged clip")
        form.addRow(self.mergeCheck)

        # Video Codec dropdown.
        self.codecEdit = QComboBox()
        self.codecEdit.addItems(["libx264", "libx265", "mpeg4", "vp9"])
        form.addRow("Video Codec:", self.codecEdit)

        # Advanced FFmpeg options.
        adv_label = QLabel("<b>Advanced FFmpeg Options</b>")
        form.addRow(adv_label)

        # Video Bitrate.
        self.videoBitrateSpin = QSpinBox()
        self.videoBitrateSpin.setRange(100, 10000)
        self.videoBitrateSpin.setSuffix(" kbps")
        self.videoBitrateSpin.setValue(2500)
        form.addRow("Video Bitrate:", self.videoBitrateSpin)

        # CRF.
        self.crfSpin = QSpinBox()
        self.crfSpin.setRange(0, 51)
        self.crfSpin.setValue(23)
        form.addRow("CRF:", self.crfSpin)

        # Frame Rate.
        self.frameRateSpin = QDoubleSpinBox()
        self.frameRateSpin.setRange(1.0, 60.0)
        self.frameRateSpin.setDecimals(2)
        self.frameRateSpin.setValue(25.0)
        form.addRow("Frame Rate:", self.frameRateSpin)

        # Preset dropdown.
        self.presetEdit = QComboBox()
        self.presetEdit.addItems(["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"])
        self.presetEdit.setCurrentText("medium")
        form.addRow("Preset:", self.presetEdit)

        # Audio Codec.
        self.audioCodecEdit = QComboBox()
        self.audioCodecEdit.addItems(["aac", "mp3", "ac3"])
        form.addRow("Audio Codec:", self.audioCodecEdit)

        # Audio Bitrate.
        self.audioBitrateSpin = QSpinBox()
        self.audioBitrateSpin.setRange(64, 320)
        self.audioBitrateSpin.setSuffix(" kbps")
        self.audioBitrateSpin.setValue(128)
        form.addRow("Audio Bitrate:", self.audioBitrateSpin)

        # Custom FFmpeg arguments.
        self.customArgsEdit = QLineEdit()
        form.addRow("Custom FFmpeg Args:", self.customArgsEdit)

        # Destination.
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

        # Load any previously stored advanced settings from settingsManager.
        adv_defaults = self.app.settingsManager.get("ffmpeg_export_params", {})
        if adv_defaults:
            self.videoBitrateSpin.setValue(adv_defaults.get("video_bitrate", 2500))
            self.crfSpin.setValue(adv_defaults.get("crf", 23))
            self.frameRateSpin.setValue(adv_defaults.get("frame_rate", 25.0))
            self.presetEdit.setCurrentText(adv_defaults.get("preset", "medium"))
            self.audioCodecEdit.setCurrentText(adv_defaults.get("audio_codec", "aac"))
            self.audioBitrateSpin.setValue(adv_defaults.get("audio_bitrate", 128))

    def onBrowse(self):
        if self.mergeCheck.isChecked():
            # Single file output.
            path, _ = QFileDialog.getSaveFileName(self, "Select Output File", "", "Video Files (*.mp4 *.mov *.avi);;All Files (*)")
            if path:
                self.destEdit.setText(path)
        else:
            # Folder output.
            folder = QFileDialog.getExistingDirectory(self, "Select Output Folder", "")
            if folder:
                self.destEdit.setText(folder)

    def getConfig(self):
        config = {
            "merge": self.mergeCheck.isChecked(),
            "codec": self.codecEdit.currentText().strip(),
            "dest": self.destEdit.text().strip(),
            "custom_args": self.customArgsEdit.text().strip(),
            # Group advanced options under a separate key.
            "advanced": {
                "video_bitrate": self.videoBitrateSpin.value(),
                "crf": self.crfSpin.value(),
                "frame_rate": self.frameRateSpin.value(),
                "preset": self.presetEdit.currentText().strip(),
                "audio_codec": self.audioCodecEdit.currentText().strip(),
                "audio_bitrate": self.audioBitrateSpin.value()
            }
        }
        return config
