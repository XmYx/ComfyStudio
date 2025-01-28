#!/usr/bin/env python
import os

from qtpy.QtGui import QPixmap
from qtpy.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QDoubleSpinBox,
    QDockWidget,
    QPushButton,
    QLabel,
    QFrame,
    QSlider, QSizePolicy
)
from qtpy.QtCore import (
    Qt,
    QUrl
)
from qtpy.QtMultimedia import QMediaPlayer, QAudioOutput
from qtpy.QtMultimediaWidgets import QVideoWidget


class ShotPreviewDock(QDockWidget):
    """
    A dock widget that shows the currently selected shot and workflow's output
    (image or video) in a scaled manner, along with essential transport controls,
    a timeline with frame notches, volume control, and metadata lines.
    """
    def __init__(self, parent=None):
        super().__init__("Shot Preview", parent)
        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)

        # Main widget inside the dock
        self.previewContainer = QWidget()
        self.setWidget(self.previewContainer)
        self.layout = QVBoxLayout(self.previewContainer)
        self.layout.setContentsMargins(2, 2, 2, 2)

        # Info line at top
        self.infoLabel = QLabel("(No shot selected)")
        self.infoLabel.setStyleSheet("QLabel { color: #CCC; background-color: #222; padding: 4px; }")
        self.layout.addWidget(self.infoLabel)

        # Preview area with a fixed border
        self.previewArea = QFrame()
        self.previewArea.setStyleSheet("QFrame { background-color: black; border: 2px solid white; }")
        # Let the preview area expand only as much as needed, not unbounded
        self.previewArea.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding
        )
        self.previewLayout = QVBoxLayout(self.previewArea)
        self.previewLayout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.previewArea, 1)

        # Image label
        self.imageLabel = QLabel()
        self.imageLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.imageLabel.setScaledContents(False)  # we'll manually scale
        # Ignore label size to prevent forcing layout to grow too large
        self.imageLabel.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Ignored
        )
        self.imageLabel.hide()
        self.previewLayout.addWidget(self.imageLabel)

        self.fullPixmap = None  # store original full-resolution pixmap

        # Video widget
        self.videoWidget = QVideoWidget()
        # Also let the video widget ignore strict size, so it fits area
        self.videoWidget.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Ignored
        )
        self.videoWidget.hide()
        self.previewLayout.addWidget(self.videoWidget)

        # Transport/controls row
        self.controlsRow = QWidget()
        self.controlsLayout = QHBoxLayout(self.controlsRow)
        self.controlsLayout.setContentsMargins(0, 0, 0, 0)

        self.playBtn = QPushButton("Play")
        self.pauseBtn = QPushButton("Pause")
        self.stopBtn = QPushButton("Stop")
        self.controlsLayout.addWidget(self.playBtn)
        self.controlsLayout.addWidget(self.pauseBtn)
        self.controlsLayout.addWidget(self.stopBtn)

        # Timeline slider with frame notches
        self.timelineSlider = QSlider(Qt.Orientation.Horizontal)
        self.timelineSlider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.timelineSlider.setTickInterval(5)  # Notch every 5 frames
        self.timelineSlider.setRange(0, 0)
        self.controlsLayout.addWidget(self.timelineSlider)

        # Volume slider
        self.volumeSlider = QSlider(Qt.Orientation.Horizontal)
        self.volumeSlider.setRange(0, 100)
        self.volumeSlider.setValue(80)
        self.volumeSlider.setFixedWidth(80)
        self.controlsLayout.addWidget(QLabel("Vol"))
        self.controlsLayout.addWidget(self.volumeSlider)

        # Playback rate
        self.rateLabel = QLabel("Rate:")
        self.rateLabel.setStyleSheet("QLabel { color: #CCC; }")
        self.rateSpin = QDoubleSpinBox()
        self.rateSpin.setRange(0.25, 3.0)
        self.rateSpin.setSingleStep(0.25)
        self.rateSpin.setValue(1.0)
        self.rateSpin.setFixedWidth(60)
        self.controlsLayout.addWidget(self.rateLabel)
        self.controlsLayout.addWidget(self.rateSpin)

        self.layout.addWidget(self.controlsRow)

        # Media player
        self.player = QMediaPlayer()
        self.audioOutput = QAudioOutput()
        self.player.setAudioOutput(self.audioOutput)
        self.player.setVideoOutput(self.videoWidget)

        # Connect controls
        self.playBtn.clicked.connect(self.player.play)
        self.pauseBtn.clicked.connect(self.player.pause)
        self.stopBtn.clicked.connect(self.player.stop)
        self.timelineSlider.sliderMoved.connect(self.onSliderMoved)
        self.player.positionChanged.connect(self.onPositionChanged)
        self.player.durationChanged.connect(self.onDurationChanged)
        self.rateSpin.valueChanged.connect(self.onRateChanged)
        self.volumeSlider.valueChanged.connect(self.onVolumeChanged)

        self.currentShot = None
        self.currentWorkflowIndex = -1
        self.player.setPlaybackRate(1.0)

    def onShotSelected(self, shotIndex):
        mainWin = self.parent()
        if not mainWin or shotIndex < 0 or shotIndex >= len(mainWin.shots):
            self.showNoMedia("(Invalid shot selection.)")
            self.currentShot = None
            self.currentWorkflowIndex = -1
            return
        shot = mainWin.shots[shotIndex]
        self.currentShot = shot
        if len(shot.workflows) > 0:
            last_wf_index = len(shot.workflows) - 1
            self.currentWorkflowIndex = last_wf_index
            self.showMediaForShotWorkflow(shot, last_wf_index)
        else:
            if shot.videoPath and os.path.exists(shot.videoPath):
                self.showVideo(shot.videoPath, f"{shot.name} (no workflows)")
            elif shot.stillPath and os.path.exists(shot.stillPath):
                self.showImage(shot.stillPath, f"{shot.name} (no workflows)")
            else:
                self.showNoMedia(f"{shot.name}: no workflows or media")

    def onWorkflowSelected(self, shotIndex, workflowIndex):
        mainWin = self.parent()
        if not mainWin:
            return
        if shotIndex < 0 or shotIndex >= len(mainWin.shots):
            self.showNoMedia("(Invalid shot index)")
            return
        shot = mainWin.shots[shotIndex]
        self.currentShot = shot
        if workflowIndex < 0 or workflowIndex >= len(shot.workflows):
            self.showNoMedia(f"{shot.name} (Invalid workflow selection)")
            return
        self.currentWorkflowIndex = workflowIndex
        self.showMediaForShotWorkflow(shot, workflowIndex)

    def onShotRenderComplete(self, shotIndex, workflowIndex, filePath, isVideo):
        if not self.currentShot:
            return
        mainWin = self.parent()
        if not mainWin:
            return
        if shotIndex == mainWin.shots.index(self.currentShot) and workflowIndex == self.currentWorkflowIndex:
            if isVideo and os.path.exists(filePath):
                self.showVideo(filePath, f"{self.currentShot.name} (WF {workflowIndex+1})")
            elif not isVideo and os.path.exists(filePath):
                self.showImage(filePath, f"{self.currentShot.name} (WF {workflowIndex+1})")
            else:
                self.showNoMedia(f"{self.currentShot.name} (Render done, file missing)")

    def showMediaForShotWorkflow(self, shot, wfIndex):
        if wfIndex < 0 or wfIndex >= len(shot.workflows):
            self.showNoMedia(f"{shot.name} (Invalid wf index)")
            return
        wfa = shot.workflows[wfIndex]
        if wfa.isVideo:
            if shot.videoPath and os.path.exists(shot.videoPath):
                self.showVideo(shot.videoPath, f"{shot.name} (WF {wfIndex+1})")
            else:
                self.showNoMedia(f"{shot.name} (WF {wfIndex+1}, no video path)")
        else:
            if shot.stillPath and os.path.exists(shot.stillPath):
                self.showImage(shot.stillPath, f"{shot.name} (WF {wfIndex+1})")
            else:
                self.showNoMedia(f"{shot.name} (WF {wfIndex+1}, no image)")

    def showImage(self, path, infoText="(Image)"):
        self.player.stop()
        self.videoWidget.hide()
        self.imageLabel.show()
        self.infoLabel.setText(infoText)
        self.timelineSlider.setValue(0)
        self.timelineSlider.setRange(0, 0)
        self.fullPixmap = QPixmap(path)
        if self.fullPixmap.isNull():
            self.imageLabel.setText("Failed to load image.")
            return
        self.updateInfoLabelExtras(
            fps="N/A",
            frames="1",
            timecode="00:00:00:00",
            resolution=f"{self.fullPixmap.width()}x{self.fullPixmap.height()}",
            hasAudio=False,
            encoding="image"
        )
        self.updateScaledImage()

    def showVideo(self, path, infoText="(Video)"):
        self.player.stop()
        self.imageLabel.hide()
        self.videoWidget.show()
        self.infoLabel.setText(infoText)
        self.timelineSlider.setValue(0)
        self.player.setSource(QUrl.fromLocalFile(path))

    def showNoMedia(self, infoText="(No media)"):
        self.player.stop()
        self.imageLabel.hide()
        self.videoWidget.hide()
        self.infoLabel.setText(infoText)
        self.timelineSlider.setValue(0)
        self.timelineSlider.setRange(0, 0)
        self.fullPixmap = None

    def onDurationChanged(self, duration):
        self.timelineSlider.setRange(0, duration if duration > 0 else 0)
        if duration > 0:
            # Approximate values for demonstration:
            fps_approx = 25.0
            frames = int(duration / 1000.0 * fps_approx)
            self.updateInfoLabelExtras(
                fps=str(round(fps_approx, 2)),
                frames=str(frames),
                timecode="00:00:00",  # simplistic, can convert from ms
                resolution="unknown",
                hasAudio=(self.audioOutput is not None),
                encoding="video"
            )

    def onPositionChanged(self, position):
        self.timelineSlider.setValue(position)

    def onSliderMoved(self, newPos):
        self.player.setPosition(newPos)

    def onRateChanged(self, val):
        self.player.setPlaybackRate(val)

    def onVolumeChanged(self, val):
        self.audioOutput.setVolume(val / 100.0)

    def updateScaledImage(self):
        if not self.fullPixmap or self.fullPixmap.isNull():
            return
        area_size = self.imageLabel.size()
        scaled = self.fullPixmap.scaled(
            area_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self.imageLabel.setPixmap(scaled)

    def updateInfoLabelExtras(self, fps, frames, timecode, resolution, hasAudio, encoding):
        audio_str = "yes" if hasAudio else "no"
        text = (
            f"FPS: {fps}   "
            f"Frames: {frames}   "
            f"Timecode: {timecode}   "
            f"Res: <span style='font-family: monospace;'>{resolution}</span>   "
            f"Audio: {audio_str}   "
            f"Enc: {encoding}"
        )
        self.infoLabel.setText(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.imageLabel.isVisible():
            self.updateScaledImage()

