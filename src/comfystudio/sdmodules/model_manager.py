import json
import os
from qtpy.QtCore import Qt, QThread
from qtpy.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QProgressBar, QMessageBox,
    QFileDialog, QHeaderView
)

from comfystudio.sdmodules.worker import DownloadWorker

from qtpy.QtGui import QIcon

class ModelManagerWindow(QDialog):
    """
    Window to manage and download models from Hugging Face Hub.
    """
    def __init__(self, parent=None, settings_manager=None):
        super().__init__(parent)
        self.setWindowTitle("Model Manager")
        self.resize(800, 500)
        self.settings_manager = settings_manager
        self.initUI()
        self.loadModels()

    def initUI(self):
        layout = QVBoxLayout(self)

        # Table to display models
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Name", "Type", "Status", "Progress", "Download"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)

        # Download All Button
        btn_layout = QHBoxLayout()
        self.download_all_btn = QPushButton("Download All")
        self.download_all_btn.setIcon(QIcon.fromTheme("download"))
        self.download_all_btn.clicked.connect(self.downloadAll)
        btn_layout.addStretch()
        btn_layout.addWidget(self.download_all_btn)
        layout.addLayout(btn_layout)

        # Dictionary to keep track of threads to prevent garbage collection
        self.threads = {}

    def loadModels(self):
        """
        Loads models from the JSON configuration and populates the table.
        """
        # Define the path to models.json
        config_path = os.path.join(os.path.dirname(__file__), "..", "defaults",  "models.json")
        if not os.path.exists(config_path):
            QMessageBox.critical(self, "Error", f"Configuration file not found: {config_path}")
            self.close()
            return

        with open(config_path, "r") as f:
            data = json.load(f)

        self.models = data.get("models", [])
        self.table.setRowCount(0)

        for idx, model in enumerate(self.models):
            self.table.insertRow(idx)

            # Name
            name_item = QTableWidgetItem(model.get("name", "Unknown"))
            self.table.setItem(idx, 0, name_item)

            # Type
            type_item = QTableWidgetItem(model.get("type", "Unknown"))
            self.table.setItem(idx, 1, type_item)

            # Status
            dest_path = self.getModelDestinationPath(model)
            if os.path.exists(dest_path):
                status_text = "Downloaded"
                download_btn = QPushButton("Downloaded")
                download_btn.setEnabled(False)
            else:
                status_text = "Not Downloaded"
                download_btn = QPushButton("Download")
                download_btn.setEnabled(True)
                download_btn.clicked.connect(self.createDownloadHandler(model, idx))

            status_item = QTableWidgetItem(status_text)
            self.table.setItem(idx, 2, status_item)

            # Progress Bar
            progress_bar = QProgressBar()
            if status_text == "Downloaded":
                progress_bar.setValue(100)
                progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #4caf50; }")
            else:
                progress_bar.setRange(0, 0)  # Indeterminate
            self.table.setCellWidget(idx, 3, progress_bar)

            # Download Button
            self.table.setCellWidget(idx, 4, download_btn)

    def getComfyUIModelsPath(self):
        """
        Retrieves the ComfyUI models directory from settings.
        """
        comfyui_path = self.settings_manager.get("comfy_main_path")
        if not comfyui_path:
            QMessageBox.warning(self, "Warning", "ComfyUI installation directory not set in settings.")
            return None
        models_path = os.path.join(os.path.dirname(comfyui_path), "models")
        os.makedirs(models_path, exist_ok=True)
        return models_path

    def getModelDestinationPath(self, model):
        """
        Determines the destination path for a model based on its type.
        """
        models_dir = self.getComfyUIModelsPath()
        if not models_dir:
            return ""
        target_subdir = model.get("type", "models")
        target_dir = os.path.join(models_dir, target_subdir)
        os.makedirs(target_dir, exist_ok=True)
        filename = model.get("filename")
        if not filename:
            filename = os.path.basename(model.get("download_url", ""))
        return os.path.join(target_dir, filename)

    def createDownloadHandler(self, model, row):
        """
        Creates and connects a handler for downloading a specific model.
        """
        def handler():
            # Disable the download button to prevent multiple clicks
            download_btn = self.table.cellWidget(row, 4)
            download_btn.setEnabled(False)

            # Update status
            self.table.item(row, 2).setText("Downloading...")

            # Set progress bar to indeterminate
            progress_bar = self.table.cellWidget(row, 3)
            progress_bar.setRange(0, 0)  # Indeterminate
            progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #2196f3; }")

            # Retrieve necessary parameters
            repo_id = model.get("repo_id")
            if not repo_id:
                # Extract repo_id from download_url if not specified
                download_url = model.get("download_url")
                if not download_url:
                    QMessageBox.warning(self, "Warning", f"No download URL for model '{model.get('name')}'.")
                    self.table.item(row, 2).setText("Error")
                    download_btn.setEnabled(True)
                    return
                # Example download_url: https://huggingface.co/{repo_id}/resolve/main/{filename}
                parts = download_url.split('/')
                try:
                    repo_id = '/'.join(parts[3:5])  # e.g., "Lightricks/LTX-Video"
                except IndexError:
                    QMessageBox.warning(self, "Warning", f"Invalid download URL format for model '{model.get('name')}'.")
                    self.table.item(row, 2).setText("Error")
                    download_btn.setEnabled(True)
                    return

            filename = model.get("filename")
            repo_type = model.get("repo_type", "model")
            revision = model.get("revision", None)
            target_subdir = model.get("type", "models")
            local_dir = os.path.join(self.getComfyUIModelsPath(), target_subdir)
            cache_dir = self.settings_manager.get("huggingface_cache_dir", None)  # Optional: allow custom cache
            force_download = False  # Set to True if you want to force re-download

            # Initialize the DownloadWorker
            worker = DownloadWorker(
                repo_id=repo_id,
                filename=filename,
                repo_type=repo_type,
                revision=revision,
                local_dir=local_dir,
                cache_dir=cache_dir,
                force_download=force_download
            )

            # Create a new thread
            thread = QThread()
            worker.moveToThread(thread)

            # Connect signals
            worker.started.connect(lambda: self.onDownloadStarted(row))
            worker.finished.connect(lambda path, success: self.onDownloadFinished(path, success, row))
            worker.error.connect(lambda e: self.onDownloadError(e, row))
            thread.started.connect(worker.run)
            # worker.finished.connect(thread.quit)
            # worker.finished.connect(worker.deleteLater)
            # thread.finished.connect(thread.deleteLater)

            # Start the thread
            thread.start()

            # Keep a reference to prevent garbage collection
            self.threads[thread] = worker

        return handler

    def onDownloadStarted(self, row):
        """
        Slot called when a download starts.
        """
        pass  # Currently handled in the handler; can be used for additional actions if needed

    def onDownloadFinished(self, path, success, row):
        """
        Slot called when a download finishes.
        """
        if success:
            self.table.item(row, 2).setText("Downloaded")
            progress_bar = self.table.cellWidget(row, 3)
            progress_bar.setRange(0, 100)
            progress_bar.setValue(100)
            progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #4caf50; }")
            download_btn = self.table.cellWidget(row, 4)
            download_btn.setText("Downloaded")
            download_btn.setEnabled(False)
            QMessageBox.information(self, "Success", f"Model '{self.table.item(row, 0).text()}' downloaded successfully.")
        else:
            self.table.item(row, 2).setText("Failed")
            progress_bar = self.table.cellWidget(row, 3)
            progress_bar.setRange(0, 100)
            progress_bar.setValue(0)
            progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #f44336; }")
            download_btn = self.table.cellWidget(row, 4)
            download_btn.setText("Download")
            download_btn.setEnabled(True)
            QMessageBox.warning(self, "Download Failed", f"Failed to download model '{self.table.item(row, 0).text()}'.")

        # # Remove the thread from the dictionary
        # sender_thread = self.sender().thread()
        # if sender_thread in self.threads:
        #     del self.threads[sender_thread]

    def onDownloadError(self, error_msg, row):
        """
        Slot called when a download encounters an error.
        """
        self.table.item(row, 2).setText("Error")
        progress_bar = self.table.cellWidget(row, 3)
        progress_bar.setRange(0, 100)
        progress_bar.setValue(0)
        progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #f44336; }")
        download_btn = self.table.cellWidget(row, 4)
        download_btn.setText("Download")
        download_btn.setEnabled(True)
        QMessageBox.warning(self, "Download Error", f"An error occurred while downloading model '{self.table.item(row, 0).text()}':\n{error_msg}")

    def downloadAll(self):
        """
        Initiates downloading of all models that are not yet downloaded.
        """
        for row in range(self.table.rowCount()):
            status = self.table.item(row, 2).text()
            if status in ["Not Downloaded", "Failed", "Error"]:
                download_btn = self.table.cellWidget(row, 4)
                if download_btn.isEnabled():
                    download_btn.click()
