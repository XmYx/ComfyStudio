"""
plugins/media_linker.py

This plugin adds a “Media Linker” tool to the application. It provides a dialog
that lists for each shot:
  • Shot-level media (Still and Video) and their version lists (Image Versions and Video Versions)
  • For each workflow, its versions (stored in the workflow’s “versions” list)

Each media item displays the file path and its availability (Available/Missing).
The user may manually relink a missing item or search a selected folder for matching files.
The plugin uses the internal data structure defined in cs_datastruts (Shot and WorkflowAssignment)
to locate and update media file paths.
"""

import os
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QAction,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QPushButton,
    QFileDialog,
    QMessageBox,
    QInputDialog
)


class MediaLinkerDialog(QDialog):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("Media Linker Tool")
        self.resize(800, 600)
        self.initUI()

    def initUI(self):
        layout = QVBoxLayout(self)

        instructions = QLabel(
            "This tool lists all shots, their workflows and media versions. "
            "Items that are missing are marked as 'Missing'. You can manually relink "
            "a media item or search a folder for potential matches."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)

        # Tree widget to display media items.
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Shot", "Item Type", "Version", "Media Type", "File", "Status"])
        layout.addWidget(self.tree)

        # Buttons: Refresh, Relink Selected, Search Folder, Close.
        btnLayout = QHBoxLayout()
        self.refreshBtn = QPushButton("Refresh")
        self.relinkBtn = QPushButton("Relink Selected")
        self.searchBtn = QPushButton("Search Folder for Matches")
        self.closeBtn = QPushButton("Close")
        btnLayout.addWidget(self.refreshBtn)
        btnLayout.addWidget(self.relinkBtn)
        btnLayout.addWidget(self.searchBtn)
        btnLayout.addStretch()
        btnLayout.addWidget(self.closeBtn)
        layout.addLayout(btnLayout)

        # Connect signals.
        self.refreshBtn.clicked.connect(self.populateTree)
        self.relinkBtn.clicked.connect(self.relinkSelected)
        self.searchBtn.clicked.connect(self.searchFolder)
        self.closeBtn.clicked.connect(self.accept)

        self.populateTree()

    def populateTree(self):
        self.tree.clear()
        # Iterate over all shots in the main application.
        for shot_index, shot in enumerate(self.app.shots):
            # Top-level shot item.
            shot_item = QTreeWidgetItem([shot.name, "Shot", "N/A", "", "", ""])
            shot_item.setData(0, Qt.ItemDataRole.UserRole, ("shot", shot_index))
            self.tree.addTopLevelItem(shot_item)

            # Shot-level Still.
            still_path = shot.stillPath or ""
            still_status = "Available" if still_path and os.path.exists(still_path) else "Missing"
            still_item = QTreeWidgetItem(["", "Shot Still", "N/A", "Image", still_path, still_status])
            still_item.setData(0, Qt.ItemDataRole.UserRole, ("shot_still", shot_index))
            shot_item.addChild(still_item)

            # Shot-level Video.
            video_path = shot.videoPath or ""
            video_status = "Available" if video_path and os.path.exists(video_path) else "Missing"
            video_item = QTreeWidgetItem(["", "Shot Video", "N/A", "Video", video_path, video_status])
            video_item.setData(0, Qt.ItemDataRole.UserRole, ("shot_video", shot_index))
            shot_item.addChild(video_item)

            # Image Versions.
            for i, img in enumerate(shot.imageVersions):
                status = "Available" if img and os.path.exists(img) else "Missing"
                img_item = QTreeWidgetItem(["", "Image Version", str(i), "Image", img or "", status])
                img_item.setData(0, Qt.ItemDataRole.UserRole, ("image_version", shot_index, i))
                shot_item.addChild(img_item)

            # Video Versions.
            for i, vid in enumerate(shot.videoVersions):
                status = "Available" if vid and os.path.exists(vid) else "Missing"
                vid_item = QTreeWidgetItem(["", "Video Version", str(i), "Video", vid or "", status])
                vid_item.setData(0, Qt.ItemDataRole.UserRole, ("video_version", shot_index, i))
                shot_item.addChild(vid_item)

            # Workflows.
            for wf_index, wf in enumerate(shot.workflows):
                wf_name = os.path.basename(wf.path) if wf.path else "Workflow"
                wf_item = QTreeWidgetItem(["", wf_name, "N/A", "Workflow", "", ""])
                wf_item.setData(0, Qt.ItemDataRole.UserRole, ("workflow", shot_index, wf_index))
                shot_item.addChild(wf_item)
                # Workflow Versions.
                for v_index, version in enumerate(wf.versions):
                    output = version.get("output", "") if isinstance(version, dict) else ""
                    v_status = "Available" if output and os.path.exists(output) else "Missing"
                    media_type = "Video" if version.get("is_video", False) else "Image"
                    wf_ver_item = QTreeWidgetItem(["", "Workflow Version", str(v_index), media_type, output, v_status])
                    wf_ver_item.setData(0, Qt.ItemDataRole.UserRole, ("workflow_version", shot_index, wf_index, v_index))
                    wf_item.addChild(wf_ver_item)

        self.tree.expandAll()

    def relinkSelected(self):
        item = self.tree.currentItem()
        if not item:
            QMessageBox.warning(self, "No Selection", "Please select a media item to relink.")
            return

        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            QMessageBox.warning(self, "Error", "Selected item has no associated data.")
            return

        new_file, _ = QFileDialog.getOpenFileName(self, "Select New Media File", "", "All Files (*)")
        if not new_file:
            return

        # Update the corresponding media reference.
        kind = data[0]
        if kind == "shot_still":
            shot_index = data[1]
            self.app.shots[shot_index].stillPath = new_file
        elif kind == "shot_video":
            shot_index = data[1]
            self.app.shots[shot_index].videoPath = new_file
        elif kind == "image_version":
            shot_index, version_index = data[1], data[2]
            if version_index < len(self.app.shots[shot_index].imageVersions):
                self.app.shots[shot_index].imageVersions[version_index] = new_file
        elif kind == "video_version":
            shot_index, version_index = data[1], data[2]
            if version_index < len(self.app.shots[shot_index].videoVersions):
                self.app.shots[shot_index].videoVersions[version_index] = new_file
        elif kind == "workflow_version":
            shot_index, wf_index, ver_index = data[1], data[2], data[3]
            if ver_index < len(self.app.shots[shot_index].workflows[wf_index].versions):
                self.app.shots[shot_index].workflows[wf_index].versions[ver_index]["output"] = new_file
        else:
            QMessageBox.warning(self, "Error", "Selected item is not a relinkable media item.")
            return

        # Update the tree item.
        item.setText(4, new_file)
        item.setText(5, "Available" if os.path.exists(new_file) else "Missing")
        QMessageBox.information(self, "Media Relinked", "Media file updated successfully.")

    def searchFolder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder to Search")
        if not folder:
            return

        item = self.tree.currentItem()
        if not item:
            QMessageBox.warning(self, "No Selection", "Please select a media item to search for.")
            return

        current_file = item.text(4)
        base_name = os.path.basename(current_file) if current_file else ""
        if not base_name:
            QMessageBox.warning(self, "Error", "The selected item has no file name to search for.")
            return

        matches = []
        for root, _, files in os.walk(folder):
            for file in files:
                if base_name.lower() in file.lower():
                    matches.append(os.path.join(root, file))
        if matches:
            chosen, ok = QInputDialog.getItem(
                self, "Select Replacement", "Found matching files:", matches, 0, False
            )
            if ok and chosen:
                data = item.data(0, Qt.ItemDataRole.UserRole)
                kind = data[0] if data else None
                if kind == "shot_still":
                    shot_index = data[1]
                    self.app.shots[shot_index].stillPath = chosen
                elif kind == "shot_video":
                    shot_index = data[1]
                    self.app.shots[shot_index].videoPath = chosen
                elif kind == "image_version":
                    shot_index, version_index = data[1], data[2]
                    if version_index < len(self.app.shots[shot_index].imageVersions):
                        self.app.shots[shot_index].imageVersions[version_index] = chosen
                elif kind == "video_version":
                    shot_index, version_index = data[1], data[2]
                    if version_index < len(self.app.shots[shot_index].videoVersions):
                        self.app.shots[shot_index].videoVersions[version_index] = chosen
                elif kind == "workflow_version":
                    shot_index, wf_index, ver_index = data[1], data[2], data[3]
                    if ver_index < len(self.app.shots[shot_index].workflows[wf_index].versions):
                        self.app.shots[shot_index].workflows[wf_index].versions[ver_index]["output"] = chosen
                else:
                    QMessageBox.warning(self, "Error", "Selected item is not searchable.")
                    return

                item.setText(4, chosen)
                item.setText(5, "Available" if os.path.exists(chosen) else "Missing")
                QMessageBox.information(self, "Media Updated", "Media file updated from search results.")
        else:
            QMessageBox.information(self, "No Matches", "No matching media files found in the selected folder.")


def openMediaLinkerDialog(app):
    dialog = MediaLinkerDialog(app)
    dialog.exec()


def register(app):
    # Add a "Media Linker" menu item to the Settings menu.
    mediaLinkerAction = QAction("Media Linker", app)
    settings_menu = None
    for action in app.menuBar().actions():
        if action.text() == "Settings":
            settings_menu = action.menu()
            break
    if settings_menu:
        settings_menu.addAction(mediaLinkerAction)
    mediaLinkerAction.triggered.connect(lambda: openMediaLinkerDialog(app))
