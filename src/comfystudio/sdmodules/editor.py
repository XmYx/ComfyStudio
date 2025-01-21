import os
import json

from qtpy.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QPushButton, QLineEdit, QFileDialog, QMessageBox, QScrollArea,
    QWidget, QTabWidget, QListWidget, QListWidgetItem
)
from qtpy.QtCore import Qt

class WorkflowEditor(QDialog):
    """
    A Workflow Editor that can load and edit two workflow JSONs:
     - Image workflows from ./workflows/image
     - Video workflows from ./workflows/video

    We display them in two separate tabs, each listing available workflow files on the left,
    and their node input details on the right. Each input line has two buttons for exposure:
        - "Expose Global": calls mainAppRef.addGlobalParam(...)
        - "Expose Shot": calls mainAppRef.addShotParam(...)
    """

    def __init__(self, settingsManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Workflow Editor")
        self.settingsManager = settingsManager
        self.mainAppRef = parent  # We'll use this to call back to MainWindow

        # We'll store the currently loaded workflows:
        self.workflowDataImage = {}
        self.workflowDataVideo = {}

        # Last loaded workflow file paths
        self.currentImageWorkflowFile = None
        self.currentVideoWorkflowFile = None

        # Load from user settings if available
        saved_image_data = self.settingsManager.get("workflow_settings_image", {})
        if saved_image_data:
            self.workflowDataImage = saved_image_data

        saved_video_data = self.settingsManager.get("workflow_settings_video", {})
        if saved_video_data:
            self.workflowDataVideo = saved_video_data

        # Build the UI
        self.layout = QVBoxLayout(self)

        self.tabWidget = QTabWidget()

        # Image tab
        self.tabImage = QWidget()
        self.imageLayout = QHBoxLayout(self.tabImage)
        self.tabWidget.addTab(self.tabImage, "Image Workflows")

        # Video tab
        self.tabVideo = QWidget()
        self.videoLayout = QHBoxLayout(self.tabVideo)
        self.tabWidget.addTab(self.tabVideo, "Video Workflows")

        self.initImageTab()
        self.initVideoTab()

        btnLayout = QHBoxLayout()
        self.saveButton = QPushButton("Save Current Workflows to Settings")
        self.closeButton = QPushButton("Close")
        btnLayout.addWidget(self.saveButton)
        btnLayout.addWidget(self.closeButton)

        self.layout.addWidget(self.tabWidget)
        self.layout.addLayout(btnLayout)
        self.setLayout(self.layout)

        # Connect signals
        self.saveButton.clicked.connect(self.onSaveToSettings)
        self.closeButton.clicked.connect(self.close)

        # Build forms from loaded data
        self.rebuildFormImage()
        self.rebuildFormVideo()

    # ---------------------
    # Image Tab
    # ---------------------
    def initImageTab(self):
        self.imageFileList = QListWidget()
        self.imageFileList.itemClicked.connect(self.onImageFileSelected)

        self.scrollImage = QScrollArea()
        self.scrollImage.setWidgetResizable(True)
        self.formContainerImage = QWidget()
        self.formLayoutImage = QFormLayout(self.formContainerImage)
        self.scrollImage.setWidget(self.formContainerImage)

        self.imageLayout.addWidget(self.imageFileList, 1)
        self.imageLayout.addWidget(self.scrollImage, 3)

        # Populate the imageFileList from the folder
        image_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "workflows", "image")
        if os.path.isdir(image_dir):
            for fname in sorted(os.listdir(image_dir)):
                if fname.lower().endswith(".json"):
                    item = QListWidgetItem(fname)
                    self.imageFileList.addItem(item)

    def onImageFileSelected(self, item):
        fname = item.text()
        image_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "workflows", "image")
        path = os.path.join(image_dir, fname)
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    self.workflowDataImage = json.load(f)
                self.currentImageWorkflowFile = path
                self.rebuildFormImage()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Could not load workflow: {e}")

    def rebuildFormImage(self):
        while self.formLayoutImage.rowCount() > 0:
            self.formLayoutImage.removeRow(0)

        for node_id, node_data in self.workflowDataImage.items():
            inputs = node_data.get("inputs", {})
            title = node_data.get("_meta", {}).get("title", f"Node {node_id}")
            self.formLayoutImage.addRow(QLabel(f"<b>{node_id} - {title}</b>"))

            for inp_name, inp_value in inputs.items():
                rowLabel = QLabel(inp_name)
                rowEdit = QLineEdit(str(inp_value))

                def mkCallback(rowEditRef, nodeId=node_id, inName=inp_name):
                    def _callback(txt):
                        self.workflowDataImage[nodeId]["inputs"][inName] = txt
                    return _callback

                rowEdit.textChanged.connect(mkCallback(rowEdit))

                # Two buttons: Global, Shot
                exposeGlobalBtn = QPushButton("Expose Global")
                exposeGlobalBtn.clicked.connect(
                    lambda _=False,
                    nID=node_id,
                    iName=inp_name,
                    iVal=rowEdit.text():
                    self.exposeParamGlobal(nID, iName, iVal, isVideo=False)
                )

                exposeShotBtn = QPushButton("Expose Shot")
                exposeShotBtn.clicked.connect(
                    lambda _=False,
                    nID=node_id,
                    iName=inp_name,
                    iVal=rowEdit.text():
                    self.exposeParamShot(nID, iName, iVal, isVideo=False)
                )

                rowWidget = QWidget()
                rowHLayout = QHBoxLayout(rowWidget)
                rowHLayout.addWidget(rowEdit)
                rowHLayout.addWidget(exposeGlobalBtn)
                rowHLayout.addWidget(exposeShotBtn)
                rowWidget.setLayout(rowHLayout)

                self.formLayoutImage.addRow(rowLabel, rowWidget)

    # ---------------------
    # Video Tab
    # ---------------------
    def initVideoTab(self):
        self.videoFileList = QListWidget()
        self.videoFileList.itemClicked.connect(self.onVideoFileSelected)

        self.scrollVideo = QScrollArea()
        self.scrollVideo.setWidgetResizable(True)
        self.formContainerVideo = QWidget()
        self.formLayoutVideo = QFormLayout(self.formContainerVideo)
        self.scrollVideo.setWidget(self.formContainerVideo)

        self.videoLayout.addWidget(self.videoFileList, 1)
        self.videoLayout.addWidget(self.scrollVideo, 3)

        video_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "workflows", "video")
        if os.path.isdir(video_dir):
            for fname in sorted(os.listdir(video_dir)):
                if fname.lower().endswith(".json"):
                    item = QListWidgetItem(fname)
                    self.videoFileList.addItem(item)

    def onVideoFileSelected(self, item):
        fname = item.text()
        video_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "workflows", "video")
        path = os.path.join(video_dir, fname)
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    self.workflowDataVideo = json.load(f)
                self.currentVideoWorkflowFile = path
                self.rebuildFormVideo()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Could not load workflow: {e}")

    def rebuildFormVideo(self):
        while self.formLayoutVideo.rowCount() > 0:
            self.formLayoutVideo.removeRow(0)

        for node_id, node_data in self.workflowDataVideo.items():
            inputs = node_data.get("inputs", {})
            title = node_data.get("_meta", {}).get("title", f"Node {node_id}")
            self.formLayoutVideo.addRow(QLabel(f"<b>{node_id} - {title}</b>"))

            for inp_name, inp_value in inputs.items():
                rowLabel = QLabel(inp_name)
                rowEdit = QLineEdit(str(inp_value))

                def mkCallback(rowEditRef, nodeId=node_id, inName=inp_name):
                    def _callback(txt):
                        self.workflowDataVideo[nodeId]["inputs"][inName] = txt
                    return _callback

                rowEdit.textChanged.connect(mkCallback(rowEdit))

                exposeGlobalBtn = QPushButton("Expose Global")
                exposeGlobalBtn.clicked.connect(
                    lambda _=False,
                    nID=node_id,
                    iName=inp_name,
                    iVal=rowEdit.text():
                    self.exposeParamGlobal(nID, iName, iVal, isVideo=True)
                )

                exposeShotBtn = QPushButton("Expose Shot")
                exposeShotBtn.clicked.connect(
                    lambda _=False,
                    nID=node_id,
                    iName=inp_name,
                    iVal=rowEdit.text():
                    self.exposeParamShot(nID, iName, iVal, isVideo=True)
                )

                rowWidget = QWidget()
                rowHLayout = QHBoxLayout(rowWidget)
                rowHLayout.addWidget(rowEdit)
                rowHLayout.addWidget(exposeGlobalBtn)
                rowHLayout.addWidget(exposeShotBtn)
                rowWidget.setLayout(rowHLayout)

                self.formLayoutVideo.addRow(rowLabel, rowWidget)

    # ---------------------
    # Expose Param
    # ---------------------
    def exposeParamGlobal(self, nodeID, inputName, inputVal, isVideo):
        """
        Expose a param as a global image/video (or generic) parameter in the main app.
        We never force 'image' or 'video' here; we simply guess type from inputVal.
        """
        if not self.mainAppRef:
            QMessageBox.warning(self, "Error", "No reference to main app!")
            return

        paramType, val = self.guessParamType(inputVal)
        self.mainAppRef.addGlobalParam(
            nodeID=nodeID,
            paramName=inputName,
            paramType=paramType,
            paramValue=val,
            isVideo=isVideo
        )

    def exposeParamShot(self, nodeID, inputName, inputVal, isVideo):
        """
        Expose a param directly to the currently selected shot (image or video param) or a generic param.
        We do *not* override the paramType to 'image'/'video'; we rely solely on guessParamType().
        The user can handle actual image/video inputs manually in their defaults.
        """
        if not self.mainAppRef:
            QMessageBox.warning(self, "Error", "No reference to main app!")
            return

        paramType, val = self.guessParamType(inputVal)
        self.mainAppRef.addShotParam(
            nodeID=nodeID,
            paramName=inputName,
            paramType=paramType,
            paramValue=val,
            isVideo=isVideo
        )

    def guessParamType(self, inputVal):
        """
        Attempts to guess the best param type from the string inputVal.
        This will return 'int', 'float', or 'string'.
        We never forcibly return 'image' or 'video' here.
        """
        paramType = "string"
        val = inputVal
        try:
            ival = int(inputVal)
            paramType = "int"
            val = ival
        except:
            try:
                fval = float(inputVal)
                paramType = "float"
                val = fval
            except:
                pass
        return paramType, val

    # ---------------------
    # Save to Settings
    # ---------------------
    def onSaveToSettings(self):
        self.settingsManager.set("workflow_settings_image", self.workflowDataImage)
        self.settingsManager.set("workflow_settings_video", self.workflowDataVideo)
        self.settingsManager.save()
        QMessageBox.information(self, "Saved", "Workflow data (image & video) saved to settings.")
