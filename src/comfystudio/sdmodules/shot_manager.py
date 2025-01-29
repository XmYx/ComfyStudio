#!/usr/bin/env python
import copy
import json
import os

from qtpy.QtCore import (
    Qt,
    QUrl
)
from qtpy.QtGui import (
    QPixmap,
    QIcon,
    QColor
)
from qtpy.QtWidgets import (
    QFileDialog,
    QLabel,
    QComboBox,
    QMessageBox,
    QInputDialog
)


class ShotManager:

    def __init__(self):
        self.shots = []
        self.currentShotIndex = None
    def clearDock(self):
        try:
            for frm in [self.imageForm, self.videoForm, self.currentShotForm]:
                while frm.rowCount() > 0:
                    frm.removeRow(0)
        except:
            pass

    def fillDock(self):
        """Fill the three shot tabs."""
        self.clearDock()
        if self.currentShotIndex is None or self.currentShotIndex < 0 or self.currentShotIndex >= len(self.shots):
            return
        shot = self.shots[self.currentShotIndex]
        # --- 1) Shot Image Params ---
        for idx, param in enumerate(shot.get("imageParams", [])):
            ptype = param.get("type", "string")
            rowWidget = self.createParamWidgetWithRemove(param, isVideo=False, isShotLevel=True)
            self.imageForm.addRow(param.get("displayName", param["name"]), rowWidget)
        # --- 2) Shot Video Params ---
        for idx, param in enumerate(shot.get("videoParams", [])):
            ptype = param.get("type", "string")
            rowWidget = self.createParamWidgetWithRemove(param, isVideo=True, isShotLevel=True)
            self.videoForm.addRow(param.get("displayName", param["name"]), rowWidget)
        # --- 3) Shot Misc => shotParams + shot["params"] ---
        for idx, param in enumerate(shot["shotParams"]):
            ptype = param.get("type", "string")
            rowWidget = self.createParamWidgetWithRemove(param, isVideo=False, isShotLevel=True, misc=True)
            self.currentShotForm.addRow(param.get("displayName", param["name"]), rowWidget)
        for idx, param in enumerate(shot["params"]):
            ptype = param.get("type", "string")
            pname = param.get("displayName", "Unknown")
            rowWidget = self.createParamWidgetWithRemove(param, isVideo=False, isShotLevel=True, misc=True)
            self.currentShotForm.addRow(pname, rowWidget)
        # Versions & video preview
        if shot["imageVersions"]:
            label = QLabel("Image Version:")
            combo = QComboBox()
            for i, path in enumerate(shot["imageVersions"]):
                combo.addItem(f"Version {i + 1}: {os.path.basename(path)}", path)
            combo.setCurrentIndex(shot["currentImageVersion"] if shot["currentImageVersion"] >= 0 else 0)
            combo.currentIndexChanged.connect(
                lambda idx, s=shot, c=combo: self.onImageVersionChanged(s, c, idx)
            )
            self.currentShotForm.addRow(label, combo)

        if shot["videoVersions"]:
            label = QLabel("Video Version:")
            combo = QComboBox()
            for i, path in enumerate(shot["videoVersions"]):
                combo.addItem(f"Version {i + 1}: {os.path.basename(path)}", path)
            combo.setCurrentIndex(shot["currentVideoVersion"] if shot["currentVideoVersion"] >= 0 else 0)
            combo.currentIndexChanged.connect(
                lambda idx, s=shot, c=combo: self.onVideoVersionChanged(s, c, idx)
            )
            self.currentShotForm.addRow(label, combo)
        videoPath = shot.get("videoPath", "")
        if videoPath and os.path.exists(videoPath):
            self.player.setSource(QUrl.fromLocalFile(videoPath))
            self.player.play()
            self.player.pause()
        else:
            self.player.setSource(QUrl())
        self.videoWidget.setMinimumSize(320, 240)
        self.statusMessage.setText("Ready")

    def getShotIcon(self, shot):
        if shot.get("stillPath") and os.path.exists(shot.get("stillPath")):
            base_pix = QPixmap(shot.get("stillPath"))
            if not base_pix.isNull():
                base_pix = base_pix.scaled(120, 90, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            else:
                base_pix = self.makeFallbackPixmap()
        else:
            base_pix = self.makeFallbackPixmap()
        final_pix = QPixmap(120, 90)
        final_pix.fill(Qt.GlobalColor.transparent)
        from qtpy.QtGui import QPainter, QBrush, QPen
        painter = QPainter(final_pix)
        painter.drawPixmap(0, 0, base_pix)
        img_status_color = self.getShotImageStatusColor(shot)
        vid_status_color = self.getShotVideoStatusColor(shot)
        circle_radius = 8
        painter.setBrush(QBrush(img_status_color))
        painter.setPen(QPen(Qt.GlobalColor.black, 1))
        painter.drawEllipse(2, 2, circle_radius, circle_radius)
        painter.setBrush(QBrush(vid_status_color))
        painter.setPen(QPen(Qt.GlobalColor.black, 1))
        painter.drawEllipse(final_pix.width() - circle_radius - 2, 2, circle_radius, circle_radius)
        painter.end()
        return QIcon(final_pix)

    def makeFallbackPixmap(self):
        pix = QPixmap(120, 90)
        pix.fill(Qt.GlobalColor.lightGray)
        return pix

    def getShotImageStatusColor(self, shot):
        if not shot.get("stillPath"):
            return QColor("red")
        if not os.path.exists(shot.get("stillPath")):
            return QColor("red")
        current_sig = self.computeRenderSignature(shot, isVideo=False)
        last_sig = shot.get("lastStillSignature", "")
        return QColor("green") if (last_sig == current_sig) else QColor("orange")

    def getShotVideoStatusColor(self, shot):
        if not shot.get("videoPath"):
            return QColor("red")
        if not os.path.exists(shot.get("videoPath")):
            return QColor("red")
        current_sig = self.computeRenderSignature(shot, isVideo=True)
        last_sig = shot.get("lastVideoSignature", "")
        return QColor("green") if (last_sig == current_sig) else QColor("orange")

    def newProject(self):
        self.currentFilePath = None
        self.shots = []
        self.currentShotIndex = None
        self.updateList()
        self.clearDock()

    def addShot(self):
        """Create a new shot with default parameters from current workflows."""
        if self.shots and self.currentShotIndex is not None and self.currentShotIndex >= 0:
            reference_shot = self.shots[self.currentShotIndex]
            new_shot = copy.deepcopy(reference_shot)
            new_shot["name"] = f"Shot {len(self.shots) + 1}"
            new_shot["stillPath"] = ""
            new_shot["videoPath"] = ""
            new_shot["imageVersions"] = []
            new_shot["videoVersions"] = []
            new_shot["currentImageVersion"] = -1
            new_shot["currentVideoVersion"] = -1
        else:
            new_shot = {
                "name": f"Shot {len(self.shots) + 1}",
                "shotParams": copy.deepcopy(self.defaultShotParams),
                "imageParams": copy.deepcopy(self.defaultImageParams),
                "videoParams": copy.deepcopy(self.defaultVideoParams),
                "params": [],
                "stillPath": "",
                "videoPath": "",
                "imageVersions": [],
                "videoVersions": [],
                "currentImageVersion": -1,
                "currentVideoVersion": -1
            }
        self.shots.append(new_shot)
        self.updateList()

    def importShotsFromTxt(self):
        # Step 1: Open a file dialog to select a TXT file
        filename, _ = QFileDialog.getOpenFileName(self, "Select TXT File", "", "Text Files (*.txt)")
        if not filename:
            return
        # Step 2: Read lines from the file
        with open(filename, "r") as f:
            lines = [line.strip() for line in f if line.strip()]
        if not lines:
            QMessageBox.information(self, "Info", "No lines found in file.")
            return
        # Step 3: Gather available parameter fields from default parameter lists
        possible_fields = []
        param_mapping = {}
        for param in self.defaultShotParams:
            key = f"ShotParam: {param['name']}"
            possible_fields.append(key)
            param_mapping[key] = ('shotParams', param)
        for param in self.defaultImageParams:
            key = f"ImageParam: {param['name']}"
            possible_fields.append(key)
            param_mapping[key] = ('imageParams', param)
        for param in self.defaultVideoParams:
            key = f"VideoParam: {param['name']}"
            possible_fields.append(key)
            param_mapping[key] = ('videoParams', param)
        if not possible_fields:
            QMessageBox.information(self, "Info", "No parameter fields available for import.")
            return
        # Step 4: Ask the user to select a field into which to import lines
        field, ok = QInputDialog.getItem(self, "Select Field", "Select a field to import lines into:", possible_fields, 0, False)
        if not ok or not field:
            return
        array_name, default_param = param_mapping[field]
        # Step 5: Determine a reference shot for inheritance, if available
        reference_shot = None
        if self.shots and self.currentShotIndex is not None and self.currentShotIndex >= 0:
            reference_shot = self.shots[self.currentShotIndex]
        # For each line, create a new shot and override the chosen field
        for line in lines:
            # Create a new shot based on current reference or default
            if reference_shot:
                new_shot = copy.deepcopy(reference_shot)
                new_shot["name"] = f"Shot {len(self.shots) + 1}"
                new_shot["stillPath"] = ""
                new_shot["videoPath"] = ""
                new_shot["imageVersions"] = []
                new_shot["videoVersions"] = []
                new_shot["currentImageVersion"] = -1
                new_shot["currentVideoVersion"] = -1
            else:
                new_shot = {
                    "name": f"Shot {len(self.shots) + 1}",
                    "shotParams": copy.deepcopy(self.defaultShotParams),
                    "imageParams": copy.deepcopy(self.defaultImageParams),
                    "videoParams": copy.deepcopy(self.defaultVideoParams),
                    "params": [],
                    "stillPath": "",
                    "videoPath": "",
                    "imageVersions": [],
                    "videoVersions": [],
                    "currentImageVersion": -1,
                    "currentVideoVersion": -1
                }
            # Override the selected field with the line's content
            params_array = new_shot[array_name]
            for param in params_array:
                if param["name"] == default_param["name"]:
                    param["value"] = line
                    break  # Assume field names are unique within this array
            self.shots.append(new_shot)
        # Step 6: Update the UI list of shots
        self.updateList()
        QMessageBox.information(self, "Import Completed", f"Imported {len(lines)} shots.")

    def openProject(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Project", "", "JSON Files (*.json)")
        if path:
            with open(path, "r") as f:
                data = json.load(f)
            self.shots = data.get("shots", [])
            self.currentFilePath = path
            self.currentShotIndex = None
            self.updateList()
            self.clearDock()


    def saveProject(self):
        self.setProjectModified(False)
        if not hasattr(self, 'currentFilePath') or not self.currentFilePath:
            self.saveProjectAs()
            return
        self._saveProjectToPath(self.currentFilePath)

    def saveProjectAs(self):
        filePath, _ = QFileDialog.getSaveFileName(
            self,
            self.localization.translate("dialog_save_as_title", default="Save Project As"),
            "",
            "JSON Files (*.json);;All Files (*)"
        )
        if filePath:
            self.currentFilePath = filePath
            self._saveProjectToPath(filePath)
            self.addToRecents(filePath)

    def _saveProjectToPath(self, filePath):
        project_data = {
            "shots": [shot.to_dict() for shot in self.shots],
        }
        try:
            with open(filePath, 'w') as f:
                json.dump(project_data, f, indent=4)
            self.statusMessage.setText(
                f"{self.localization.translate('status_saved_to', default='Project saved to')} {filePath}")
            self.addToRecents(filePath)
        except Exception as e:
            QMessageBox.warning(self, self.localization.translate("dialog_error_title", default="Error"),
                                self.localization.translate("error_failed_to_save_project",
                                                            default=f"Failed to save project: {e}"))
