#!/usr/bin/env python
"""
This module defines an enhanced shot manager with a Shot Library, a multitrack Timeline,
and a Preview dock. In addition to the basic functionality, the timeline now enforces
that clips cannot be resized longer than the source clip, supports a toolbar with a
Select and Blade tool (with multi‐selection and splitting), implements audio/video
link/unlink so linked clips move/resize together, adjusts overlapping clips by trimming,
allows adding extra video/audio tracks with autosnapping, and enables ripple delete on
empty areas.

Additional features:
  - Selected clips are highlighted.
  - Clips can be deleted via the Delete key.
  - Selected clips can be copied (Ctrl+C) and pasted (Ctrl+V) at the playhead position.
  - Resizing clamps to at least one frame (1/fps) and prevents inversion.
  - New video tracks are inserted above previous ones while audio tracks are appended below,
    similar to DaVinci Resolve.
  - Right–clicking in the track label area opens a context menu:
      • For video tracks: hide/show track.
      • For audio tracks: mute, solo, and set level.
  - The timeline now supports actual playback:
      • A QTimer drives playback at a global default FPS of 25.
      • A timeline range is computed (from 0 to the last clip’s end).
      • A work–area subrange is auto–set (if not modified) to the timeline end,
        and playback loops within that range.

Usage in your MainWindow:
    from comfystudio.sdmodules.new_widget import ShotManagerWidget

    class MainWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.resize(1400, 900)
            # ... your initialization code ...
            self.shots = []  # Must be maintained if using ShotListView dragging etc.
            self.shotManager = ShotManagerWidget(self)
            # Optionally, add sample shots:
            sample_shot = Shot(
                name="AnimateDiff Preview",
                duration=5.0,  # seconds (full source duration)
                videoPath="/home/mix/Downloads/AnimateDiff_00007.mp4",
                stillPath="/home/mix/Downloads/AnimateDiff_00007.mp4",
                thumbnail_path="/home/mix/Downloads/AnimateDiff_00007.mp4",
                inPoint=0.0,
                outPoint=1.0,
                linkedAudio=True,
            )
            self.shots.append(sample_shot)
            self.shotManager.shotListView.addShot(sample_shot)
            # ... further initialization ...
"""

import sys
import json
import cv2
import copy
from dataclasses import dataclass
from typing import List

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt, QSize, QRect
from PyQt6.QtGui import QPixmap, QPainter, QPen, QColor
from PyQt6.QtWidgets import (
    QWidget,
    QListWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSlider,
    QListWidgetItem,
    QLabel,
    QDockWidget,
    QAbstractItemView,
    QMenu,
)
from qtpy.QtCore import Signal

# Minimal Shot class
@dataclass
class Shot:
    name: str
    duration: float  # full source duration in seconds
    videoPath: str
    stillPath: str
    thumbnail_path: str
    inPoint: float  # fraction (0.0-1.0) for trimmed start
    outPoint: float  # fraction (0.0-1.0) for trimmed end
    linkedAudio: bool  # whether audio is linked to video

########################################################################
# Helper: extract a video frame using OpenCV
########################################################################
def getVideoFrame(videoPath: str, fraction: float, size: QSize) -> QPixmap:
    cap = cv2.VideoCapture(videoPath)
    if not cap.isOpened():
        print(f"[DEBUG] Failed to open video: {videoPath}")
        placeholder = QPixmap(size)
        placeholder.fill(QColor("black"))
        return placeholder
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    if frame_count <= 0:
        print(f"[DEBUG] Invalid frame count in video: {videoPath}")
        cap.release()
        placeholder = QPixmap(size)
        placeholder.fill(QColor("black"))
        return placeholder
    target_frame = int(max(0, min(frame_count - 1, fraction * frame_count)))
    cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
    ret, frame = cap.read()
    cap.release()
    if not ret or frame is None:
        print(f"[DEBUG] Failed to read frame at index {target_frame} from video: {videoPath}")
        placeholder = QPixmap(size)
        placeholder.fill(QColor("black"))
        return placeholder
    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, ch = frame.shape
    bytesPerLine = ch * w
    qImg = QtGui.QImage(frame.data, w, h, bytesPerLine, QtGui.QImage.Format.Format_RGB888)
    pixmap = QPixmap.fromImage(qImg)
    pixmap = pixmap.scaled(size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return pixmap

########################################################################
# Data Classes
########################################################################
@dataclass
class TimelineClip:
    shot: Shot
    track: str  # e.g. "Video", "Audio", or custom track names like "Video 2"
    start_time: float  # in seconds (timeline start time)
    length: float      # in seconds; normally (shot.outPoint - shot.inPoint)*shot.duration

########################################################################
# FrameReadoutLabel: a transparent label covering the ruler area that always
# updates the playhead (timeline handle) when clicked and dragged.
########################################################################
class FrameReadoutLabel(QtWidgets.QLabel):
    def __init__(self, timeline_widget, parent=None):
        super().__init__(parent)
        self.timeline_widget = timeline_widget
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setStyleSheet("background: transparent; color: white;")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.updateText()

    def updateText(self):
        self.setText(f"Time: {self.timeline_widget.playhead:.2f}s")

    def mousePressEvent(self, event):
        new_playhead = event.x() / self.timeline_widget.scale
        self.timeline_widget.playhead = max(0.0, new_playhead)
        self.timeline_widget.playheadChanged.emit(self.timeline_widget.playhead)
        self.timeline_widget.update()
        self.updateText()
        event.accept()

    def mouseMoveEvent(self, event):
        new_playhead = event.x() / self.timeline_widget.scale
        self.timeline_widget.playhead = max(0.0, new_playhead)
        self.timeline_widget.playheadChanged.emit(self.timeline_widget.playhead)
        self.timeline_widget.update()
        self.updateText()
        event.accept()

########################################################################
# ShotListView: The Shot Library (grid view)
########################################################################
class ShotListView(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setIconSize(QSize(160, 120))
        self.setSpacing(10)
        self.setDragEnabled(True)
        self.setAcceptDrops(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setMouseTracking(True)
        self.hoverFraction = {}  # {id(item): fraction}
        self.currentHoverItem = None
        self.inMarkers = {}
        self.outMarkers = {}
        print("[DEBUG] ShotListView initialized")

    def mimeData(self, items):
        mimeData = QtCore.QMimeData()
        shots = []
        for item in items:
            shot_idx = item.data(Qt.ItemDataRole.UserRole)
            shot = QtWidgets.QApplication.activeWindow().shots[shot_idx]
            shots.append({
                "name": shot.name,
                "duration": shot.duration,
                "videoPath": shot.videoPath,
                "stillPath": shot.stillPath,
                "thumbnail_path": shot.thumbnail_path,
                "inPoint": shot.inPoint,
                "outPoint": shot.outPoint,
                "linkedAudio": shot.linkedAudio,
            })
        mimeData.setData("application/x-shot", json.dumps(shots).encode("utf-8"))
        return mimeData

    def mouseMoveEvent(self, event):
        pos = event.pos()
        item = self.itemAt(pos)
        if item:
            rect = self.visualItemRect(item)
            if rect.width() > 0:
                frac = (pos.x() - rect.x()) / rect.width()
                frac = max(0.0, min(1.0, frac))
                self.hoverFraction[id(item)] = frac
                self.currentHoverItem = item
        else:
            self.currentHoverItem = None
        super().mouseMoveEvent(event)
        self.viewport().update()

    def leaveEvent(self, event):
        self.currentHoverItem = None
        super().leaveEvent(event)
        self.viewport().update()

    def keyPressEvent(self, event):
        if self.currentHoverItem:
            item_id = id(self.currentHoverItem)
            if event.key() == Qt.Key.Key_I:
                self.inMarkers[item_id] = self.hoverFraction.get(item_id, 0.0)
                print(f"[DEBUG] Set In marker for item {item_id} at {self.inMarkers[item_id]}")
            elif event.key() == Qt.Key.Key_O:
                self.outMarkers[item_id] = self.hoverFraction.get(item_id, 1.0)
                print(f"[DEBUG] Set Out marker for item {item_id} at {self.outMarkers[item_id]}")
        super().keyPressEvent(event)
        self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        for i in range(self.count()):
            item = self.item(i)
            rect = self.visualItemRect(item)
            item_id = id(item)
            if item == self.currentHoverItem and item_id in self.hoverFraction:
                frac = self.hoverFraction[item_id]
                shot_idx = item.data(Qt.ItemDataRole.UserRole)
                shot = QtWidgets.QApplication.activeWindow().shots[shot_idx]
                frame_pix = getVideoFrame(shot.videoPath, frac, self.iconSize())
                iconSize = self.iconSize()
                icon_x = rect.x() + (rect.width() - iconSize.width()) // 2
                icon_y = rect.y() + (rect.height() - iconSize.height()) // 2
                icon_rect = QRect(icon_x, icon_y, iconSize.width(), iconSize.height())
                painter.drawPixmap(icon_rect, frame_pix)
            if item == self.currentHoverItem and item_id in self.hoverFraction:
                frac = self.hoverFraction[item_id]
                x = rect.x() + int(frac * rect.width())
                painter.setPen(QPen(QColor(255, 0, 0), 2))
                painter.drawLine(x, rect.y(), x, rect.bottom())
            if item_id in self.inMarkers:
                frac = self.inMarkers[item_id]
                x = rect.x() + int(frac * rect.width())
                painter.setBrush(QColor(0, 255, 0))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(x - 3, rect.y() + 5, 6, 6)
            if item_id in self.outMarkers:
                frac = self.outMarkers[item_id]
                x = rect.x() + int(frac * rect.width())
                painter.setBrush(QColor(255, 165, 0))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(x - 3, rect.y() + 5, 6, 6)
        painter.end()

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos())
        if item:
            menu = QMenu(self)
            trimAction = menu.addAction("Trim Clip")
            deleteAction = menu.addAction("Delete Clip")
            propertiesAction = menu.addAction("Properties")
            action = menu.exec(event.globalPos())
            if action == trimAction:
                print(f"[DEBUG] Trim clip: {item.text()}")
            elif action == deleteAction:
                row = self.row(item)
                self.takeItem(row)
                print(f"[DEBUG] Deleted clip: {item.text()}")
            elif action == propertiesAction:
                print(f"[DEBUG] Properties for: {item.text()}")
        else:
            super().contextMenuEvent(event)

    def addShot(self, shot: Shot):
        item = QListWidgetItem(shot.name)
        thumb = getVideoFrame(shot.videoPath, 0.0, self.iconSize())
        icon = QtGui.QIcon(thumb)
        item.setIcon(icon)
        item.setData(Qt.ItemDataRole.UserRole, QtCore.QVariant(0))
        self.addItem(item)
        print(f"[DEBUG] Added shot to ShotListView: {shot.name}")

    def clearShots(self):
        self.clear()
        self.hoverFraction.clear()
        self.inMarkers.clear()
        self.outMarkers.clear()
        print("[DEBUG] Cleared ShotListView")


########################################################################
# MultiTrackTimelineWidget: The multitrack timeline dock with enhanced tools
########################################################################
class MultiTrackTimelineWidget(QWidget):
    playheadChanged = Signal(float)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.scale = 100.0  # pixels per second
        self.playhead = 0.0  # in seconds
        self.timeline_clips: List[TimelineClip] = []
        # Maintain separate video and audio tracks (for ordering)
        self.video_tracks = ["Video 1"]
        self.audio_tracks = ["Audio 1"]
        self.tracks = self.video_tracks + self.audio_tracks
        self.left_panel_width = 80
        self.ruler_height = 20
        self.track_height = 60
        self.total_tracks = len(self.tracks)
        self.setMinimumHeight(self.ruler_height + self.track_height * self.total_tracks)
        self.setAcceptDrops(True)
        self.dragging_playhead = False
        # For clip manipulation:
        self.activeClip = None
        self.activeClipAction = None  # "move", "resize_left", "resize_right"
        self.activeClipOffset = 0.0
        self.activeClipOriginalStart = 0.0
        self.activeClipOriginalEnd = 0.0
        # For multi-selection:
        self.toolMode = "select"  # "select" or "blade"
        self.selectedClips: List[TimelineClip] = []
        self.activeClipOriginalPositions = {}  # keyed by id(clip)
        self.activeClipOffsets = {}  # keyed by id(clip)
        self.rubberBand = None
        self.selectionOrigin = None
        # For visual hover on handles:
        self.hoveredClip = None
        self.hoveredHandle = None  # "resize_left" or "resize_right"
        self.fps = 24
        self.previewResolution = QSize(640, 480)
        self.handle_width = 6
        # For drop preview:
        self.dropPreviewClip = None
        # Clipboard for copy/paste:
        self.clipboard = []
        # Track settings: video tracks have "visible", audio tracks have "mute", "solo", "level"
        self.track_settings = {}
        for t in self.video_tracks:
            self.track_settings[t] = {"visible": True}
        for t in self.audio_tracks:
            self.track_settings[t] = {"mute": False, "solo": False, "level": 1.0}
        # Playback functionality
        self.playback_fps = 25.0  # global playback fps
        self.playback_timer = QtCore.QTimer(self)
        self.playback_timer.timeout.connect(self.advancePlayhead)
        self.playing = False
        # Timeline/work area range (in seconds)
        self.timeline_start = 0.0
        self.timeline_end = 0.0
        self.work_area_start = 0.0
        self.work_area_end = 0.0  # if 0.0, will auto-set to timeline_end
        # Create the frame readout label and place it over the ruler area.
        self.frameReadoutLabel = FrameReadoutLabel(self, self)
        self.frameReadoutLabel.setGeometry(self.left_panel_width, 0, self.width() - self.left_panel_width, self.ruler_height)
        self.frameReadoutLabel.show()
        print("[DEBUG] MultiTrackTimelineWidget initialized")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.frameReadoutLabel.setGeometry(self.left_panel_width, 0, self.width() - self.left_panel_width, self.ruler_height)

    def clipRect(self, clip: TimelineClip) -> QRect:
        try:
            track_index = self.tracks.index(clip.track)
        except ValueError:
            track_index = 0
        # effective duration in seconds (trimmed duration)
        effective_duration = (clip.shot.outPoint - clip.shot.inPoint) * clip.shot.duration
        x = self.left_panel_width + int(clip.start_time * self.scale)
        y = self.ruler_height + track_index * self.track_height + 5
        width = int(effective_duration * self.scale)
        height = self.track_height - 10
        return QRect(x, y, width, height)

    def snapValue(self, value, snap_list, threshold=10):
        for snap_val in snap_list:
            if abs(value - snap_val) < threshold:
                return snap_val
        return value

    def updateTracks(self):
        self.tracks = self.video_tracks + self.audio_tracks
        self.total_tracks = len(self.tracks)
        self.setMinimumHeight(self.ruler_height + self.track_height * self.total_tracks)

    def addVideoTrack(self):
        new_track_name = f"Video {len(self.video_tracks) + 1}"
        self.video_tracks.insert(0, new_track_name)
        self.track_settings[new_track_name] = {"visible": True}
        self.updateTracks()
        self.update()
        print(f"[DEBUG] Added video track: {new_track_name}")

    def addAudioTrack(self):
        new_track_name = f"Audio {len(self.audio_tracks) + 1}"
        self.audio_tracks.append(new_track_name)
        self.track_settings[new_track_name] = {"mute": False, "solo": False, "level": 1.0}
        self.updateTracks()
        self.update()
        print(f"[DEBUG] Added audio track: {new_track_name}")

    def splitClip(self, clip: TimelineClip, split_time: float):
        original_start = clip.start_time
        original_length = clip.length
        if split_time <= original_start or split_time >= original_start + original_length:
            return
        left_length = split_time - original_start
        right_length = (original_start + original_length) - split_time
        left_inPoint = clip.shot.inPoint
        left_outPoint = left_inPoint + (left_length / clip.shot.duration)
        right_inPoint = left_outPoint
        right_outPoint = clip.shot.outPoint
        left_clip = TimelineClip(shot=clip.shot, track=clip.track, start_time=original_start, length=left_length)
        left_clip.shot.inPoint = left_inPoint
        left_clip.shot.outPoint = left_outPoint
        right_clip = TimelineClip(shot=clip.shot, track=clip.track, start_time=split_time, length=right_length)
        right_clip.shot.inPoint = right_inPoint
        right_clip.shot.outPoint = right_outPoint
        if clip in self.timeline_clips:
            self.timeline_clips.remove(clip)
        self.timeline_clips.append(left_clip)
        self.timeline_clips.append(right_clip)
        print(f"[DEBUG] Split clip '{clip.shot.name}' at {split_time}s into two clips")
        if clip.shot.linkedAudio:
            for other in self.timeline_clips.copy():
                if other.shot == clip.shot and other.track.lower() != clip.track.lower():
                    self.splitClip(other, split_time)
                    break

    def handleOverlap(self, new_clip: TimelineClip):
        for clip in self.timeline_clips.copy():
            if clip == new_clip:
                continue
            if clip.track.lower() == new_clip.track.lower():
                existing_start = clip.start_time
                existing_end = clip.start_time + clip.length
                new_start = new_clip.start_time
                new_end = new_clip.start_time + new_clip.length
                if existing_start < new_end and existing_end > new_start:
                    if existing_start < new_start < existing_end:
                        new_length = new_start - existing_start
                        clip.length = new_length
                        clip.shot.outPoint = clip.shot.inPoint + (new_length / clip.shot.duration)
                        print(f"[DEBUG] Overlap: Trimmed clip '{clip.shot.name}' to new length {new_length}s")
                        if new_length < 0.1:
                            self.timeline_clips.remove(clip)
                    elif new_start <= existing_start < new_end:
                        self.timeline_clips.remove(clip)
                        print(f"[DEBUG] Overlap: Removed clip '{clip.shot.name}' due to overlap with new clip")

    def updateTimelineRange(self):
        if self.timeline_clips:
            self.timeline_end = max(clip.start_time + clip.length for clip in self.timeline_clips)
        else:
            self.timeline_end = 0.0
        # Auto-set work area end if not modified.
        if self.work_area_end == 0.0:
            self.work_area_end = self.timeline_end

    # Playback functionality methods
    def startPlayback(self):
        if not self.playback_timer.isActive():
            self.playback_timer.start(int(1000.0 / self.playback_fps))
            self.playing = True

    def stopPlayback(self):
        if self.playback_timer.isActive():
            self.playback_timer.stop()
            self.playing = False

    def togglePlayback(self):
        if self.playback_timer.isActive():
            self.stopPlayback()
        else:
            self.startPlayback()

    def advancePlayhead(self):
        increment = 1.0 / self.playback_fps
        self.playhead += increment
        # Loop within the work area.
        if self.work_area_end > self.work_area_start and self.playhead >= self.work_area_end:
            self.playhead = self.work_area_start
        self.playheadChanged.emit(self.playhead)
        self.update()

    # Key events – note that this widget has StrongFocus.
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Left:
            step = 1.0 / self.fps
            self.playhead = max(0.0, self.playhead - step)
            print(f"[DEBUG] Frame step left: playhead = {self.playhead}s")
            self.playheadChanged.emit(self.playhead)
            self.update()
        elif event.key() == Qt.Key.Key_Right:
            step = 1.0 / self.fps
            self.playhead += step
            print(f"[DEBUG] Frame step right: playhead = {self.playhead}s")
            self.playheadChanged.emit(self.playhead)
            self.update()
        elif event.key() == Qt.Key.Key_Space:
            self.togglePlayback()
            print(f"[DEBUG] {'Play' if self.playing else 'Pause'}")
        elif event.key() == Qt.Key.Key_Delete:
            if self.selectedClips:
                for clip in self.selectedClips:
                    if clip in self.timeline_clips:
                        self.timeline_clips.remove(clip)
                print("[DEBUG] Deleted selected clips")
                self.selectedClips = []
                self.update()
        elif event.modifiers() == Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_C:
            self.clipboard = [copy.deepcopy(clip) for clip in self.selectedClips]
            print("[DEBUG] Copied selected clips")
        elif event.modifiers() == Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_V:
            if self.clipboard:
                min_start = min(clip.start_time for clip in self.clipboard)
                offset = self.playhead - min_start
                new_clips = []
                for clip in self.clipboard:
                    new_clip = copy.deepcopy(clip)
                    new_clip.start_time += offset
                    new_clips.append(new_clip)
                self.timeline_clips.extend(new_clips)
                print("[DEBUG] Pasted clips at playhead")
                self.update()
        elif event.key() == Qt.Key.Key_P:
            main_win = QtWidgets.QApplication.activeWindow()
            if main_win.isFullScreen():
                main_win.showNormal()
                print("[DEBUG] Exited presentation mode")
            else:
                main_win.showFullScreen()
                print("[DEBUG] Entered presentation mode")
        else:
            super().keyPressEvent(event)

    # (The remainder of mouse event handlers remains unchanged.)
    def mousePressEvent(self, event):
        pos = event.pos()
        if self.toolMode == "select":
            clicked_clip = None
            for clip in reversed(self.timeline_clips):
                if self.clipRect(clip).contains(pos):
                    clicked_clip = clip
                    break
            if clicked_clip:
                if clicked_clip:
                    self.selectedClips = [clicked_clip]
                    if clicked_clip.shot.linkedAudio:
                        for clip in self.timeline_clips:
                            if clip.shot == clicked_clip.shot and clip not in self.selectedClips:
                                self.selectedClips.append(clip)
                    self.activeClip = clicked_clip
                    self.activeClipOriginalPositions = {}
                    for clip in self.selectedClips:
                        self.activeClipOriginalPositions[id(clip)] = (clip.start_time, clip.length)
                    threshold = 10
                    rect = self.clipRect(clicked_clip)
                    if abs(pos.x() - rect.left()) <= threshold:
                        self.activeClipAction = "resize_left"
                    elif abs(pos.x() - rect.right()) <= threshold:
                        self.activeClipAction = "resize_right"
                    else:
                        self.activeClipAction = "move"
                        self.activeClipOffset = pos.x() - rect.left()
                    # Set the effective clip end time (in timeline seconds)
                    self.activeClipOriginalStart = clicked_clip.start_time
                    self.activeClipOriginalEnd = clicked_clip.start_time + (
                                (clicked_clip.shot.outPoint - clicked_clip.shot.inPoint) * clicked_clip.shot.duration)

                else:
                    threshold = 10
                    rect = self.clipRect(clicked_clip)
                    if abs(pos.x() - rect.left()) <= threshold:
                        self.activeClipAction = "resize_left"
                    elif abs(pos.x() - rect.right()) <= threshold:
                        self.activeClipAction = "resize_right"
                    else:
                        self.activeClipAction = "move"
                        self.activeClipOffset = pos.x() - rect.left()
                    self.activeClipOriginalStart = clicked_clip.start_time
                    self.activeClipOriginalEnd = clicked_clip.start_time + clicked_clip.length
            else:
                self.selectedClips = []
                self.rubberBand = QtWidgets.QRubberBand(QtWidgets.QRubberBand.Shape.Rectangle, self)
                self.selectionOrigin = pos
                self.rubberBand.setGeometry(QRect(pos, QSize()))
                self.rubberBand.show()
                self.activeClip = None
        elif self.toolMode == "blade":
            clicked_clip = None
            for clip in reversed(self.timeline_clips):
                if self.clipRect(clip).contains(pos):
                    clicked_clip = clip
                    break
            if clicked_clip:
                clip_start = clicked_clip.start_time
                clip_end = clicked_clip.start_time + clicked_clip.length
                if clip_start < self.playhead < clip_end:
                    self.splitClip(clicked_clip, self.playhead)
                    self.activeClip = None
                    self.update()
                    return
            else:
                super().mousePressEvent(event)
                return
        else:
            super().mousePressEvent(event)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.pos()
        if self.rubberBand:
            rect = QRect(self.selectionOrigin, event.pos()).normalized()
            self.rubberBand.setGeometry(rect)
            return
        if not self.activeClip and not self.dragging_playhead:
            self.hoveredClip = None
            self.hoveredHandle = None
            for clip in self.timeline_clips:
                rect = self.clipRect(clip)
                left_handle = QRect(rect.left(), rect.top(), self.handle_width, rect.height())
                right_handle = QRect(rect.right() - self.handle_width, rect.top(), self.handle_width, rect.height())
                if left_handle.contains(event.pos()):
                    self.hoveredClip = clip
                    self.hoveredHandle = "resize_left"
                    break
                elif right_handle.contains(event.pos()):
                    self.hoveredClip = clip
                    self.hoveredHandle = "resize_right"
                    break

        if self.activeClip:
            if self.activeClipAction == "move":
                if len(self.selectedClips) > 1:
                    for clip in self.selectedClips:
                        orig_start, _ = self.activeClipOriginalPositions[id(clip)]
                        new_left = event.pos().x() - self.activeClipOffsets[id(clip)]
                        new_start_time = (new_left - self.left_panel_width) / self.scale
                        clip.start_time = max(0.0, new_start_time)
                else:
                    new_left = event.pos().x() - self.activeClipOffset
                    new_start_time = (new_left - self.left_panel_width) / self.scale
                    other_edges = []
                    for c in self.timeline_clips:
                        if c != self.activeClip and c.track.lower() == self.activeClip.track.lower():
                            other_edges.append(self.left_panel_width + int(c.start_time * self.scale))
                            other_edges.append(self.left_panel_width + int((c.start_time + c.length) * self.scale))
                    new_left_pix = self.snapValue(new_left, other_edges)
                    new_start_time = (new_left_pix - self.left_panel_width) / self.scale
                    self.activeClip.start_time = max(0.0, new_start_time)
                print(f"[DEBUG] Moving clip(s) to start_time: {new_start_time}s")
            elif self.activeClipAction == "resize_left":
                new_left = event.pos().x()
                new_start_time = (new_left - self.left_panel_width) / self.scale
                min_effective = 1.0 / self.fps
                if self.activeClipOriginalEnd - new_start_time < min_effective:
                    new_start_time = self.activeClipOriginalEnd - min_effective
                self.activeClip.start_time = new_start_time
                self.activeClip.length = self.activeClipOriginalEnd - new_start_time
                new_inPoint = self.activeClip.shot.outPoint - (self.activeClip.length / self.activeClip.shot.duration)
                self.activeClip.shot.inPoint = max(0.0, new_inPoint)

                print(f"[DEBUG] Resizing left clip '{self.activeClip.shot.name}': new start_time {new_start_time}s, new inPoint {self.activeClip.shot.inPoint}")
            elif self.activeClipAction == "resize_right":
                new_right = event.pos().x()
                new_end_time = (new_right - self.left_panel_width) / self.scale
                min_effective = 1.0 / self.fps
                if new_end_time - self.activeClipOriginalStart < min_effective:
                    new_end_time = self.activeClipOriginalStart + min_effective
                self.activeClip.length = new_end_time - self.activeClipOriginalStart
                new_outPoint = self.activeClip.shot.inPoint + (self.activeClip.length / self.activeClip.shot.duration)
                self.activeClip.shot.outPoint = min(1.0, max(new_outPoint, self.activeClip.shot.inPoint + 0.01))

                print(f"[DEBUG] Resizing right clip '{self.activeClip.shot.name}': new end_time {new_end_time}s, new outPoint {self.activeClip.shot.outPoint}")
            self.update()
        elif self.dragging_playhead:
            new_playhead = (pos.x() - self.left_panel_width) / self.scale
            self.playhead = max(0.0, new_playhead)
            print(f"[DEBUG] Playhead moved to: {self.playhead}s")
            self.playheadChanged.emit(self.playhead)
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.rubberBand:
            selectionRect = self.rubberBand.geometry()
            self.rubberBand.hide()
            self.rubberBand = None
            for clip in self.timeline_clips:
                if self.clipRect(clip).intersects(selectionRect):
                    if clip not in self.selectedClips:
                        self.selectedClips.append(clip)
            print(f"[DEBUG] Selected clips: {[c.shot.name for c in self.selectedClips]}")
        if self.dragging_playhead:
            self.dragging_playhead = False
            print("[DEBUG] Stopped dragging playhead")
        if self.activeClip:
            print(f"[DEBUG] Finished manipulation on clip '{self.activeClip.shot.name}'")
            self.activeClip = None
            self.activeClipAction = None
        super().mouseReleaseEvent(event)

    def paintClips(self, painter):
        for clip in self.timeline_clips:
            ts = self.track_settings.get(clip.track, {})
            if clip.track.lower().startswith("video"):
                if not ts.get("visible", True):
                    continue
                color = QColor(100, 100, 250)
            else:
                if ts.get("mute", False):
                    color = QColor(128, 128, 128)
                else:
                    color = QColor(250, 150, 50)
            clip_rect = self.clipRect(clip)

            #Draw in and out points
            painter.setBrush(QColor(255, 0, 0))
            marker_y = clip_rect.bottom() - 10  # 10 pixels from bottom
            painter.drawEllipse(QRect(clip_rect.left() - 3, marker_y, 6, 6))
            painter.drawEllipse(QRect(clip_rect.right() - 3, marker_y, 6, 6))

            painter.fillRect(clip_rect, color)
            if clip in self.selectedClips:
                painter.setPen(QPen(QColor(0, 255, 0), 3))
            else:
                painter.setPen(QColor(255, 255, 255))
            painter.drawRect(clip_rect)
            painter.drawText(clip_rect.adjusted(2, 2, -2, -2),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             clip.shot.name)
            left_handle = QRect(clip_rect.left(), clip_rect.top(), self.handle_width, clip_rect.height())
            right_handle = QRect(clip_rect.right() - self.handle_width, clip_rect.top(), self.handle_width, clip_rect.height())
            if self.activeClip == clip and self.activeClipAction == "resize_left":
                painter.fillRect(left_handle, QColor(255, 255, 0))
            elif self.hoveredClip == clip and self.hoveredHandle == "resize_left":
                painter.fillRect(left_handle, QColor(255, 200, 0))
            else:
                painter.fillRect(left_handle, QColor(200, 200, 200))
            if self.activeClip == clip and self.activeClipAction == "resize_right":
                painter.fillRect(right_handle, QColor(255, 255, 0))
            elif self.hoveredClip == clip and self.hoveredHandle == "resize_right":
                painter.fillRect(right_handle, QColor(255, 200, 0))
            else:
                painter.fillRect(right_handle, QColor(200, 200, 200))

    def paintEvent(self, event):
        # Update timeline range before painting.
        self.updateTimelineRange()
        painter = QPainter(self)
        # Draw ruler.
        ruler_rect = QRect(self.left_panel_width, 0, self.width() - self.left_panel_width, self.ruler_height)
        painter.fillRect(ruler_rect, QColor(50, 50, 50))
        seconds = int((self.width() - self.left_panel_width) / self.scale) + 1
        for s in range(seconds):
            x = self.left_panel_width + int(s * self.scale)
            painter.setPen(QPen(QColor(200, 200, 200), 1))
            painter.drawLine(x, 0, x, self.ruler_height)
            painter.drawText(x + 2, self.ruler_height - 2, f"{s}s")
        # Draw left panel (track labels).
        left_rect = QRect(0, self.ruler_height, self.left_panel_width, self.height() - self.ruler_height)
        painter.fillRect(left_rect, QColor(80, 80, 80))
        for i, track in enumerate(self.tracks):
            y = self.ruler_height + i * self.track_height
            painter.setPen(QColor(255, 255, 255))
            ts = self.track_settings.get(track, {})
            extra = ""
            if track.lower().startswith("audio"):
                if ts.get("mute", False):
                    extra += " [Muted]"
                if ts.get("solo", False):
                    extra += " [Solo]"
                extra += f" (Lv: {ts.get('level', 1.0)})"
            painter.drawText(5, int(y + self.track_height / 2), track + extra)
        # Draw drop preview rectangle along with its target track name.
        if self.dropPreviewClip:
            preview_rect = QRect(
                self.left_panel_width + int(self.dropPreviewClip.start_time * self.scale),
                self.ruler_height + 5,
                int(self.dropPreviewClip.length * self.scale),
                self.track_height - 10
            )
            preview_pen = QPen(QColor(0, 255, 255), 2, Qt.PenStyle.DashLine)
            painter.setPen(preview_pen)
            painter.drawRect(preview_rect)
            painter.setBrush(QColor(0, 255, 255, 50))
            painter.fillRect(preview_rect, QColor(0, 255, 255, 50))
            painter.setPen(QColor(0, 0, 0))
            painter.drawText(preview_rect, Qt.AlignmentFlag.AlignCenter, self.dropPreviewClip.track)
        # Draw clips.
        self.paintClips(painter)
        # Draw playhead.
        playhead_x = self.left_panel_width + int(self.playhead * self.scale)
        painter.setPen(QPen(QColor(255, 0, 0), 2))
        painter.drawLine(playhead_x, self.ruler_height, playhead_x, self.height())
        painter.end()

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-shot"):
            event.acceptProposedAction()
            data = event.mimeData().data("application/x-shot")
            try:
                shots_data = json.loads(bytes(data).decode("utf-8"))
                if shots_data:
                    shot = Shot(**shots_data[0])
                    clip_length = (shot.outPoint - shot.inPoint) * shot.duration
                    drop_time = max(0.0, (event.pos().x() - self.left_panel_width) / self.scale)
                    drop_y = event.pos().y()
                    track_index = (drop_y - self.ruler_height) // self.track_height
                    if track_index < 0:
                        track_index = 0
                    if track_index >= len(self.tracks):
                        track_index = len(self.tracks) - 1
                    selected_track = self.tracks[track_index]
                    self.dropPreviewClip = TimelineClip(shot=shot, track=selected_track, start_time=drop_time,
                                                        length=clip_length)
            except Exception as e:
                print("[DEBUG] DragEnter preview error:", e)
            self.update()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat("application/x-shot"):
            event.acceptProposedAction()
            data = event.mimeData().data("application/x-shot")
            try:
                shots_data = json.loads(bytes(data).decode("utf-8"))
                if shots_data:
                    shot = Shot(**shots_data[0])
                    clip_length = (shot.outPoint - shot.inPoint) * shot.duration
                    drop_time = max(0.0, (event.pos().x() - self.left_panel_width) / self.scale)
                    drop_y = event.pos().y()
                    track_index = (drop_y - self.ruler_height) // self.track_height
                    if track_index < 0:
                        track_index = 0
                    if track_index >= len(self.tracks):
                        track_index = len(self.tracks) - 1
                    selected_track = self.tracks[track_index]
                    self.dropPreviewClip = TimelineClip(shot=shot, track=selected_track, start_time=drop_time,
                                                        length=clip_length)
            except Exception as e:
                print("[DEBUG] DragMove preview error:", e)
            self.update()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.dropPreviewClip = None
        self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasFormat("application/x-shot"):
            data = event.mimeData().data("application/x-shot")
            try:
                shots_data = json.loads(bytes(data).decode("utf-8"))
                pos = event.pos()
                drop_time = max(0.0, (pos.x() - self.left_panel_width) / self.scale)
                drop_y = pos.y()
                track_index = (drop_y - self.ruler_height) // self.track_height
                if track_index < 0:
                    track_index = 0
                if track_index >= len(self.tracks):
                    track_index = len(self.tracks) - 1
                selected_track = self.tracks[track_index]
                print(f"[DEBUG] Drop event at pos {pos}, calculated drop_time: {drop_time}s on track '{selected_track}'")
                for shot_dict in shots_data:
                    shot = Shot(**shot_dict)
                    clip_length = (shot.outPoint - shot.inPoint) * shot.duration
                    new_clip = TimelineClip(shot=shot, track=selected_track, start_time=drop_time, length=clip_length)
                    clips_on_track = [c for c in self.timeline_clips if c.track == selected_track]
                    if clips_on_track:
                        clips_on_track.sort(key=lambda c: c.start_time)
                        last_clip = clips_on_track[-1]
                        gap = new_clip.start_time - (last_clip.start_time + last_clip.length)
                        if abs(gap) < 0.2:
                            new_clip.start_time = last_clip.start_time + last_clip.length
                            print(f"[DEBUG] Autosnapped new clip to {new_clip.start_time}s on track '{selected_track}'")
                    self.handleOverlap(new_clip)
                    self.timeline_clips.append(new_clip)
                    print(f"[DEBUG] Added clip: {new_clip}")
                    if shot.linkedAudio:
                        audio_track = None
                        for t in self.tracks:
                            if t.lower().startswith("audio"):
                                audio_track = t
                                break
                        if not audio_track:
                            audio_track = "Audio"
                        audio_clip = TimelineClip(shot=shot, track=audio_track, start_time=new_clip.start_time, length=clip_length)
                        self.handleOverlap(audio_clip)
                        self.timeline_clips.append(audio_clip)
                        print(f"[DEBUG] Added linked audio clip: {audio_clip}")
                self.dropPreviewClip = None
                self.update()
                event.acceptProposedAction()
            except Exception as e:
                print("[DEBUG] Drop error:", e)
                event.ignore()
        else:
            event.ignore()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Left:
            step = 1.0 / self.fps
            self.playhead = max(0.0, self.playhead - step)
            print(f"[DEBUG] Frame step left: playhead = {self.playhead}s")
            self.playheadChanged.emit(self.playhead)
            self.update()
        elif event.key() == Qt.Key.Key_Right:
            step = 1.0 / self.fps
            self.playhead += step
            print(f"[DEBUG] Frame step right: playhead = {self.playhead}s")
            self.playheadChanged.emit(self.playhead)
            self.update()
        elif event.key() == Qt.Key.Key_Space:
            self.togglePlayback()
            print(f"[DEBUG] {'Play' if self.playing else 'Pause'}")
        elif event.key() == Qt.Key.Key_Delete:
            if self.selectedClips:
                for clip in self.selectedClips:
                    if clip in self.timeline_clips:
                        self.timeline_clips.remove(clip)
                print("[DEBUG] Deleted selected clips")
                self.selectedClips = []
                self.update()
        elif event.modifiers() == Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_C:
            self.clipboard = [copy.deepcopy(clip) for clip in self.selectedClips]
            print("[DEBUG] Copied selected clips")
        elif event.modifiers() == Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_V:
            if self.clipboard:
                min_start = min(clip.start_time for clip in self.clipboard)
                offset = self.playhead - min_start
                new_clips = []
                for clip in self.clipboard:
                    new_clip = copy.deepcopy(clip)
                    new_clip.start_time += offset
                    new_clips.append(new_clip)
                self.timeline_clips.extend(new_clips)
                print("[DEBUG] Pasted clips at playhead")
                self.update()
        elif event.key() == Qt.Key.Key_P:
            main_win = QtWidgets.QApplication.activeWindow()
            if main_win.isFullScreen():
                main_win.showNormal()
                print("[DEBUG] Exited presentation mode")
            else:
                main_win.showFullScreen()
                print("[DEBUG] Entered presentation mode")
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        pos = event.pos()
        if self.toolMode == "select":
            clicked_clip = None
            for clip in reversed(self.timeline_clips):
                if self.clipRect(clip).contains(pos):
                    clicked_clip = clip
                    break
            if clicked_clip:
                self.selectedClips = [clicked_clip]
                # Automatically add linked clips.
                if clicked_clip.shot.linkedAudio:
                    for clip in self.timeline_clips:
                        if clip.shot == clicked_clip.shot and clip not in self.selectedClips:
                            self.selectedClips.append(clip)
                self.activeClip = clicked_clip
                self.activeClipOriginalPositions = {}
                for clip in self.selectedClips:
                    self.activeClipOriginalPositions[id(clip)] = (clip.start_time, clip.length)
                if len(self.selectedClips) > 1:
                    self.activeClipAction = "move"
                    self.activeClipOffsets = {}
                    for clip in self.selectedClips:
                        clip_rect = self.clipRect(clip)
                        self.activeClipOffsets[id(clip)] = pos.x() - clip_rect.left()
                else:
                    threshold = 10
                    rect = self.clipRect(clicked_clip)
                    if abs(pos.x() - rect.left()) <= threshold:
                        self.activeClipAction = "resize_left"
                    elif abs(pos.x() - rect.right()) <= threshold:
                        self.activeClipAction = "resize_right"
                    else:
                        self.activeClipAction = "move"
                        self.activeClipOffset = pos.x() - rect.left()
                    self.activeClipOriginalStart = clicked_clip.start_time
                    self.activeClipOriginalEnd = clicked_clip.start_time + clicked_clip.length
            else:
                self.selectedClips = []
                self.rubberBand = QtWidgets.QRubberBand(QtWidgets.QRubberBand.Shape.Rectangle, self)
                self.selectionOrigin = pos
                self.rubberBand.setGeometry(QRect(pos, QSize()))
                self.rubberBand.show()
                self.activeClip = None
        elif self.toolMode == "blade":
            clicked_clip = None
            for clip in reversed(self.timeline_clips):
                if self.clipRect(clip).contains(pos):
                    clicked_clip = clip
                    break
            if clicked_clip:
                clip_start = clicked_clip.start_time
                clip_end = clicked_clip.start_time + clicked_clip.length
                if clip_start < self.playhead < clip_end:
                    self.splitClip(clicked_clip, self.playhead)
                    self.activeClip = None
                    self.update()
                    return
            else:
                super().mousePressEvent(event)
                return
        else:
            super().mousePressEvent(event)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.pos()
        if self.rubberBand:
            rect = QRect(self.selectionOrigin, event.pos()).normalized()
            self.rubberBand.setGeometry(rect)
            return
        if self.activeClip:
            if self.activeClipAction == "move":
                if len(self.selectedClips) > 1:
                    for clip in self.selectedClips:
                        orig_start, _ = self.activeClipOriginalPositions[id(clip)]
                        new_left = event.pos().x() - self.activeClipOffsets[id(clip)]
                        new_start_time = (new_left - self.left_panel_width) / self.scale
                        clip.start_time = max(0.0, new_start_time)
                else:
                    new_left = event.pos().x() - self.activeClipOffset
                    new_start_time = (new_left - self.left_panel_width) / self.scale
                    other_edges = []
                    for c in self.timeline_clips:
                        if c != self.activeClip and c.track.lower() == self.activeClip.track.lower():
                            other_edges.append(self.left_panel_width + int(c.start_time * self.scale))
                            other_edges.append(self.left_panel_width + int((c.start_time + c.length) * self.scale))
                    new_left_pix = self.snapValue(new_left, other_edges)
                    new_start_time = (new_left_pix - self.left_panel_width) / self.scale
                    self.activeClip.start_time = max(0.0, new_start_time)
                print(f"[DEBUG] Moving clip(s) to start_time: {new_start_time}s")
            elif self.activeClipAction == "resize_left":
                new_left = event.pos().x()
                new_start_time = (new_left - self.left_panel_width) / self.scale
                min_length = 1.0 / self.fps
                if self.activeClipOriginalEnd - new_start_time < min_length:
                    new_start_time = self.activeClipOriginalEnd - min_length
                self.activeClip.start_time = new_start_time
                self.activeClip.length = self.activeClipOriginalEnd - new_start_time
                new_inPoint = self.activeClip.shot.outPoint - (self.activeClip.length / self.activeClip.shot.duration)
                self.activeClip.shot.inPoint = max(0.0, new_inPoint)
                print(f"[DEBUG] Resizing left clip '{self.activeClip.shot.name}': new start_time {new_start_time}s, new inPoint {self.activeClip.shot.inPoint}")
            elif self.activeClipAction == "resize_right":
                new_right = event.pos().x()
                new_end_time = (new_right - self.left_panel_width) / self.scale
                min_length = 1.0 / self.fps
                if new_end_time - self.activeClipOriginalStart < min_length:
                    new_end_time = self.activeClipOriginalStart + min_length
                self.activeClip.length = new_end_time - self.activeClipOriginalStart
                new_outPoint = self.activeClip.shot.inPoint + (self.activeClip.length / self.activeClip.shot.duration)
                self.activeClip.shot.outPoint = min(1.0, max(new_outPoint, self.activeClip.shot.inPoint + 0.01))
                print(f"[DEBUG] Resizing right clip '{self.activeClip.shot.name}': new end_time {new_end_time}s, new outPoint {self.activeClip.shot.outPoint}")
            self.update()
        elif self.dragging_playhead:
            new_playhead = (pos.x() - self.left_panel_width) / self.scale
            self.playhead = max(0.0, new_playhead)
            print(f"[DEBUG] Playhead moved to: {self.playhead}s")
            self.playheadChanged.emit(self.playhead)
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.rubberBand:
            selectionRect = self.rubberBand.geometry()
            self.rubberBand.hide()
            self.rubberBand = None
            for clip in self.timeline_clips:
                if self.clipRect(clip).intersects(selectionRect):
                    if clip not in self.selectedClips:
                        self.selectedClips.append(clip)
            print(f"[DEBUG] Selected clips: {[c.shot.name for c in self.selectedClips]}")
        if self.dragging_playhead:
            self.dragging_playhead = False
            print("[DEBUG] Stopped dragging playhead")
        if self.activeClip:
            print(f"[DEBUG] Finished manipulation on clip '{self.activeClip.shot.name}'")
            self.activeClip = None
            self.activeClipAction = None
        super().mouseReleaseEvent(event)

    def paintClips(self, painter):
        for clip in self.timeline_clips:
            ts = self.track_settings.get(clip.track, {})
            if clip.track.lower().startswith("video"):
                if not ts.get("visible", True):
                    continue
                color = QColor(100, 100, 250)
            else:
                if ts.get("mute", False):
                    color = QColor(128, 128, 128)
                else:
                    color = QColor(250, 150, 50)
            clip_rect = self.clipRect(clip)
            painter.fillRect(clip_rect, color)
            if clip in self.selectedClips:
                painter.setPen(QPen(QColor(0, 255, 0), 3))
            else:
                painter.setPen(QColor(255, 255, 255))
            painter.drawRect(clip_rect)
            painter.drawText(clip_rect.adjusted(2, 2, -2, -2),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             clip.shot.name)
            left_handle = QRect(clip_rect.left(), clip_rect.top(), self.handle_width, clip_rect.height())
            right_handle = QRect(clip_rect.right() - self.handle_width, clip_rect.top(), self.handle_width, clip_rect.height())
            if self.activeClip == clip and self.activeClipAction == "resize_left":
                painter.fillRect(left_handle, QColor(255, 255, 0))
            elif self.hoveredClip == clip and self.hoveredHandle == "resize_left":
                painter.fillRect(left_handle, QColor(255, 200, 0))
            else:
                painter.fillRect(left_handle, QColor(200, 200, 200))
            if self.activeClip == clip and self.activeClipAction == "resize_right":
                painter.fillRect(right_handle, QColor(255, 255, 0))
            elif self.hoveredClip == clip and self.hoveredHandle == "resize_right":
                painter.fillRect(right_handle, QColor(255, 200, 0))
            else:
                painter.fillRect(right_handle, QColor(200, 200, 200))

    def paintEvent(self, event):
        self.updateTimelineRange()
        painter = QPainter(self)
        # Draw ruler.
        ruler_rect = QRect(self.left_panel_width, 0, self.width() - self.left_panel_width, self.ruler_height)
        painter.fillRect(ruler_rect, QColor(50, 50, 50))
        seconds = int((self.width() - self.left_panel_width) / self.scale) + 1
        for s in range(seconds):
            x = self.left_panel_width + int(s * self.scale)
            painter.setPen(QPen(QColor(200, 200, 200), 1))
            painter.drawLine(x, 0, x, self.ruler_height)
            painter.drawText(x + 2, self.ruler_height - 2, f"{s}s")
        # Draw left panel (track labels).
        left_rect = QRect(0, self.ruler_height, self.left_panel_width, self.height() - self.ruler_height)
        painter.fillRect(left_rect, QColor(80, 80, 80))
        for i, track in enumerate(self.tracks):
            y = self.ruler_height + i * self.track_height
            painter.setPen(QColor(255, 255, 255))
            ts = self.track_settings.get(track, {})
            extra = ""
            if track.lower().startswith("audio"):
                if ts.get("mute", False):
                    extra += " [Muted]"
                if ts.get("solo", False):
                    extra += " [Solo]"
                extra += f" (Lv: {ts.get('level', 1.0)})"
            painter.drawText(5, int(y + self.track_height / 2), track + extra)
        # Draw drop preview rectangle along with its target track name.
        if self.dropPreviewClip:
            preview_rect = QRect(
                self.left_panel_width + int(self.dropPreviewClip.start_time * self.scale),
                self.ruler_height + 5,
                int(self.dropPreviewClip.length * self.scale),
                self.track_height - 10
            )
            preview_pen = QPen(QColor(0, 255, 255), 2, Qt.PenStyle.DashLine)
            painter.setPen(preview_pen)
            painter.drawRect(preview_rect)
            painter.setBrush(QColor(0, 255, 255, 50))
            painter.fillRect(preview_rect, QColor(0, 255, 255, 50))
            painter.setPen(QColor(0, 0, 0))
            painter.drawText(preview_rect, Qt.AlignmentFlag.AlignCenter, self.dropPreviewClip.track)
        # Draw clips.
        self.paintClips(painter)
        # Draw playhead.
        playhead_x = self.left_panel_width + int(self.playhead * self.scale)
        painter.setPen(QPen(QColor(255, 0, 0), 2))
        painter.drawLine(playhead_x, self.ruler_height, playhead_x, self.height())
        painter.end()

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-shot"):
            event.acceptProposedAction()
            data = event.mimeData().data("application/x-shot")
            try:
                shots_data = json.loads(bytes(data).decode("utf-8"))
                if shots_data:
                    shot = Shot(**shots_data[0])
                    clip_length = (shot.outPoint - shot.inPoint) * shot.duration
                    drop_time = max(0.0, (event.pos().x() - self.left_panel_width) / self.scale)
                    drop_y = event.pos().y()
                    track_index = (drop_y - self.ruler_height) // self.track_height
                    if track_index < 0:
                        track_index = 0
                    if track_index >= len(self.tracks):
                        track_index = len(self.tracks) - 1
                    selected_track = self.tracks[track_index]
                    self.dropPreviewClip = TimelineClip(shot=shot, track=selected_track, start_time=drop_time,
                                                        length=clip_length)
            except Exception as e:
                print("[DEBUG] DragEnter preview error:", e)
            self.update()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat("application/x-shot"):
            event.acceptProposedAction()
            data = event.mimeData().data("application/x-shot")
            try:
                shots_data = json.loads(bytes(data).decode("utf-8"))
                if shots_data:
                    shot = Shot(**shots_data[0])
                    clip_length = (shot.outPoint - shot.inPoint) * shot.duration
                    drop_time = max(0.0, (event.pos().x() - self.left_panel_width) / self.scale)
                    drop_y = event.pos().y()
                    track_index = (drop_y - self.ruler_height) // self.track_height
                    if track_index < 0:
                        track_index = 0
                    if track_index >= len(self.tracks):
                        track_index = len(self.tracks) - 1
                    selected_track = self.tracks[track_index]
                    self.dropPreviewClip = TimelineClip(shot=shot, track=selected_track, start_time=drop_time,
                                                        length=clip_length)
            except Exception as e:
                print("[DEBUG] DragMove preview error:", e)
            self.update()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.dropPreviewClip = None
        self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasFormat("application/x-shot"):
            data = event.mimeData().data("application/x-shot")
            try:
                shots_data = json.loads(bytes(data).decode("utf-8"))
                pos = event.pos()
                drop_time = max(0.0, (pos.x() - self.left_panel_width) / self.scale)
                drop_y = pos.y()
                track_index = (drop_y - self.ruler_height) // self.track_height
                if track_index < 0:
                    track_index = 0
                if track_index >= len(self.tracks):
                    track_index = len(self.tracks) - 1
                selected_track = self.tracks[track_index]
                print(f"[DEBUG] Drop event at pos {pos}, calculated drop_time: {drop_time}s on track '{selected_track}'")
                for shot_dict in shots_data:
                    shot = Shot(**shot_dict)
                    clip_length = (shot.outPoint - shot.inPoint) * shot.duration
                    new_clip = TimelineClip(shot=shot, track=selected_track, start_time=drop_time, length=clip_length)
                    clips_on_track = [c for c in self.timeline_clips if c.track == selected_track]
                    if clips_on_track:
                        clips_on_track.sort(key=lambda c: c.start_time)
                        last_clip = clips_on_track[-1]
                        gap = new_clip.start_time - (last_clip.start_time + last_clip.length)
                        if abs(gap) < 0.2:
                            new_clip.start_time = last_clip.start_time + last_clip.length
                            print(f"[DEBUG] Autosnapped new clip to {new_clip.start_time}s on track '{selected_track}'")
                    self.handleOverlap(new_clip)
                    self.timeline_clips.append(new_clip)
                    print(f"[DEBUG] Added clip: {new_clip}")
                    if shot.linkedAudio:
                        audio_track = None
                        for t in self.tracks:
                            if t.lower().startswith("audio"):
                                audio_track = t
                                break
                        if not audio_track:
                            audio_track = "Audio"
                        audio_clip = TimelineClip(shot=shot, track=audio_track, start_time=new_clip.start_time, length=clip_length)
                        self.handleOverlap(audio_clip)
                        self.timeline_clips.append(audio_clip)
                        print(f"[DEBUG] Added linked audio clip: {audio_clip}")
                self.dropPreviewClip = None
                self.update()
                event.acceptProposedAction()
            except Exception as e:
                print("[DEBUG] Drop error:", e)
                event.ignore()
        else:
            event.ignore()

    # Playback functionality methods
    def startPlayback(self):
        if not self.playback_timer.isActive():
            self.playback_timer.start(int(1000.0 / self.playback_fps))
            self.playing = True

    def stopPlayback(self):
        if self.playback_timer.isActive():
            self.playback_timer.stop()
            self.playing = False

    def togglePlayback(self):
        if self.playback_timer.isActive():
            self.stopPlayback()
        else:
            self.startPlayback()

    def advancePlayhead(self):
        increment = 1.0 / self.playback_fps
        self.playhead += increment
        # Loop within the work area.
        if self.work_area_end > self.work_area_start and self.playhead >= self.work_area_end:
            self.playhead = self.work_area_start
        self.playheadChanged.emit(self.playhead)
        self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Left:
            step = 1.0 / self.fps
            self.playhead = max(0.0, self.playhead - step)
            print(f"[DEBUG] Frame step left: playhead = {self.playhead}s")
            self.playheadChanged.emit(self.playhead)
            self.update()
        elif event.key() == Qt.Key.Key_Right:
            step = 1.0 / self.fps
            self.playhead += step
            print(f"[DEBUG] Frame step right: playhead = {self.playhead}s")
            self.playheadChanged.emit(self.playhead)
            self.update()
        elif event.key() == Qt.Key.Key_Space:
            self.togglePlayback()
            print(f"[DEBUG] {'Play' if self.playing else 'Pause'}")
        elif event.key() == Qt.Key.Key_Delete:
            if self.selectedClips:
                for clip in self.selectedClips:
                    if clip in self.timeline_clips:
                        self.timeline_clips.remove(clip)
                print("[DEBUG] Deleted selected clips")
                self.selectedClips = []
                self.update()
        elif event.modifiers() == Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_C:
            self.clipboard = [copy.deepcopy(clip) for clip in self.selectedClips]
            print("[DEBUG] Copied selected clips")
        elif event.modifiers() == Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_V:
            if self.clipboard:
                min_start = min(clip.start_time for clip in self.clipboard)
                offset = self.playhead - min_start
                new_clips = []
                for clip in self.clipboard:
                    new_clip = copy.deepcopy(clip)
                    new_clip.start_time += offset
                    new_clips.append(new_clip)
                self.timeline_clips.extend(new_clips)
                print("[DEBUG] Pasted clips at playhead")
                self.update()
        elif event.key() == Qt.Key.Key_P:
            main_win = QtWidgets.QApplication.activeWindow()
            if main_win.isFullScreen():
                main_win.showNormal()
                print("[DEBUG] Exited presentation mode")
            else:
                main_win.showFullScreen()
                print("[DEBUG] Entered presentation mode")
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        pos = event.pos()
        if self.toolMode == "select":
            clicked_clip = None
            for clip in reversed(self.timeline_clips):
                if self.clipRect(clip).contains(pos):
                    clicked_clip = clip
                    break
            if clicked_clip:
                self.selectedClips = [clicked_clip]
                # Automatically add linked clips.
                if clicked_clip.shot.linkedAudio:
                    for clip in self.timeline_clips:
                        if clip.shot == clicked_clip.shot and clip not in self.selectedClips:
                            self.selectedClips.append(clip)
                self.activeClip = clicked_clip
                self.activeClipOriginalPositions = {}
                for clip in self.selectedClips:
                    self.activeClipOriginalPositions[id(clip)] = (clip.start_time, clip.length)
                if len(self.selectedClips) > 1:
                    self.activeClipAction = "move"
                    self.activeClipOffsets = {}
                    for clip in self.selectedClips:
                        clip_rect = self.clipRect(clip)
                        self.activeClipOffsets[id(clip)] = pos.x() - clip_rect.left()
                else:
                    threshold = 10
                    rect = self.clipRect(clicked_clip)
                    if abs(pos.x() - rect.left()) <= threshold:
                        self.activeClipAction = "resize_left"
                    elif abs(pos.x() - rect.right()) <= threshold:
                        self.activeClipAction = "resize_right"
                    else:
                        self.activeClipAction = "move"
                        self.activeClipOffset = pos.x() - rect.left()
                    self.activeClipOriginalStart = clicked_clip.start_time
                    self.activeClipOriginalEnd = clicked_clip.start_time + clicked_clip.length
            else:
                self.selectedClips = []
                self.rubberBand = QtWidgets.QRubberBand(QtWidgets.QRubberBand.Shape.Rectangle, self)
                self.selectionOrigin = pos
                self.rubberBand.setGeometry(QRect(pos, QSize()))
                self.rubberBand.show()
                self.activeClip = None
        elif self.toolMode == "blade":
            clicked_clip = None
            for clip in reversed(self.timeline_clips):
                if self.clipRect(clip).contains(pos):
                    clicked_clip = clip
                    break
            if clicked_clip:
                clip_start = clicked_clip.start_time
                clip_end = clicked_clip.start_time + clicked_clip.length
                if clip_start < self.playhead < clip_end:
                    self.splitClip(clicked_clip, self.playhead)
                    self.activeClip = None
                    self.update()
                    return
            else:
                super().mousePressEvent(event)
                return
        else:
            super().mousePressEvent(event)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.pos()
        if self.rubberBand:
            rect = QRect(self.selectionOrigin, event.pos()).normalized()
            self.rubberBand.setGeometry(rect)
            return
        if self.activeClip:
            if self.activeClipAction == "move":
                if len(self.selectedClips) > 1:
                    for clip in self.selectedClips:
                        orig_start, _ = self.activeClipOriginalPositions[id(clip)]
                        new_left = event.pos().x() - self.activeClipOffsets[id(clip)]
                        new_start_time = (new_left - self.left_panel_width) / self.scale
                        clip.start_time = max(0.0, new_start_time)
                else:
                    new_left = event.pos().x() - self.activeClipOffset
                    new_start_time = (new_left - self.left_panel_width) / self.scale
                    other_edges = []
                    for c in self.timeline_clips:
                        if c != self.activeClip and c.track.lower() == self.activeClip.track.lower():
                            other_edges.append(self.left_panel_width + int(c.start_time * self.scale))
                            other_edges.append(self.left_panel_width + int((c.start_time + c.length) * self.scale))
                    new_left_pix = self.snapValue(new_left, other_edges)
                    new_start_time = (new_left_pix - self.left_panel_width) / self.scale
                    self.activeClip.start_time = max(0.0, new_start_time)
                print(f"[DEBUG] Moving clip(s) to start_time: {new_start_time}s")
            elif self.activeClipAction == "resize_left":
                new_left = event.pos().x()
                new_start_time = (new_left - self.left_panel_width) / self.scale
                min_length = 1.0 / self.fps
                if self.activeClipOriginalEnd - new_start_time < min_length:
                    new_start_time = self.activeClipOriginalEnd - min_length
                self.activeClip.start_time = new_start_time
                self.activeClip.length = self.activeClipOriginalEnd - new_start_time
                new_inPoint = self.activeClip.shot.outPoint - (self.activeClip.length / self.activeClip.shot.duration)
                self.activeClip.shot.inPoint = max(0.0, new_inPoint)
                print(f"[DEBUG] Resizing left clip '{self.activeClip.shot.name}': new start_time {new_start_time}s, new inPoint {self.activeClip.shot.inPoint}")
            elif self.activeClipAction == "resize_right":
                new_right = event.pos().x()
                new_end_time = (new_right - self.left_panel_width) / self.scale
                min_length = 1.0 / self.fps
                if new_end_time - self.activeClipOriginalStart < min_length:
                    new_end_time = self.activeClipOriginalStart + min_length
                self.activeClip.length = new_end_time - self.activeClipOriginalStart
                new_outPoint = self.activeClip.shot.inPoint + (self.activeClip.length / self.activeClip.shot.duration)
                self.activeClip.shot.outPoint = min(1.0, max(new_outPoint, self.activeClip.shot.inPoint + 0.01))
                print(f"[DEBUG] Resizing right clip '{self.activeClip.shot.name}': new end_time {new_end_time}s, new outPoint {self.activeClip.shot.outPoint}")
            self.update()
        elif self.dragging_playhead:
            new_playhead = (pos.x() - self.left_panel_width) / self.scale
            self.playhead = max(0.0, new_playhead)
            print(f"[DEBUG] Playhead moved to: {self.playhead}s")
            self.playheadChanged.emit(self.playhead)
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.rubberBand:
            selectionRect = self.rubberBand.geometry()
            self.rubberBand.hide()
            self.rubberBand = None
            for clip in self.timeline_clips:
                if self.clipRect(clip).intersects(selectionRect):
                    if clip not in self.selectedClips:
                        self.selectedClips.append(clip)
            print(f"[DEBUG] Selected clips: {[c.shot.name for c in self.selectedClips]}")
        if self.dragging_playhead:
            self.dragging_playhead = False
            print("[DEBUG] Stopped dragging playhead")
        if self.activeClip:
            print(f"[DEBUG] Finished manipulation on clip '{self.activeClip.shot.name}'")
            self.activeClip = None
            self.activeClipAction = None
        super().mouseReleaseEvent(event)

    def paintClips(self, painter):
        for clip in self.timeline_clips:
            ts = self.track_settings.get(clip.track, {})
            if clip.track.lower().startswith("video"):
                if not ts.get("visible", True):
                    continue
                color = QColor(100, 100, 250)
            else:
                if ts.get("mute", False):
                    color = QColor(128, 128, 128)
                else:
                    color = QColor(250, 150, 50)
            clip_rect = self.clipRect(clip)
            painter.fillRect(clip_rect, color)
            if clip in self.selectedClips:
                painter.setPen(QPen(QColor(0, 255, 0), 3))
            else:
                painter.setPen(QColor(255, 255, 255))
            painter.drawRect(clip_rect)
            painter.drawText(clip_rect.adjusted(2, 2, -2, -2),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             clip.shot.name)
            left_handle = QRect(clip_rect.left(), clip_rect.top(), self.handle_width, clip_rect.height())
            right_handle = QRect(clip_rect.right() - self.handle_width, clip_rect.top(), self.handle_width, clip_rect.height())
            if self.activeClip == clip and self.activeClipAction == "resize_left":
                painter.fillRect(left_handle, QColor(255, 255, 0))
            elif self.hoveredClip == clip and self.hoveredHandle == "resize_left":
                painter.fillRect(left_handle, QColor(255, 200, 0))
            else:
                painter.fillRect(left_handle, QColor(200, 200, 200))
            if self.activeClip == clip and self.activeClipAction == "resize_right":
                painter.fillRect(right_handle, QColor(255, 255, 0))
            elif self.hoveredClip == clip and self.hoveredHandle == "resize_right":
                painter.fillRect(right_handle, QColor(255, 200, 0))
            else:
                painter.fillRect(right_handle, QColor(200, 200, 200))

    def paintEvent(self, event):
        self.updateTimelineRange()
        painter = QPainter(self)
        # Draw ruler.
        ruler_rect = QRect(self.left_panel_width, 0, self.width() - self.left_panel_width, self.ruler_height)
        painter.fillRect(ruler_rect, QColor(50, 50, 50))
        seconds = int((self.width() - self.left_panel_width) / self.scale) + 1
        for s in range(seconds):
            x = self.left_panel_width + int(s * self.scale)
            painter.setPen(QPen(QColor(200, 200, 200), 1))
            painter.drawLine(x, 0, x, self.ruler_height)
            painter.drawText(x + 2, self.ruler_height - 2, f"{s}s")
        # Draw left panel (track labels).
        left_rect = QRect(0, self.ruler_height, self.left_panel_width, self.height() - self.ruler_height)
        painter.fillRect(left_rect, QColor(80, 80, 80))
        for i, track in enumerate(self.tracks):
            y = self.ruler_height + i * self.track_height
            painter.setPen(QColor(255, 255, 255))
            ts = self.track_settings.get(track, {})
            extra = ""
            if track.lower().startswith("audio"):
                if ts.get("mute", False):
                    extra += " [Muted]"
                if ts.get("solo", False):
                    extra += " [Solo]"
                extra += f" (Lv: {ts.get('level', 1.0)})"
            painter.drawText(5, int(y + self.track_height / 2), track + extra)
        # Draw drop preview rectangle with target track name.
        if self.dropPreviewClip:
            preview_rect = QRect(
                self.left_panel_width + int(self.dropPreviewClip.start_time * self.scale),
                self.ruler_height + 5,
                int(self.dropPreviewClip.length * self.scale),
                self.track_height - 10
            )
            preview_pen = QPen(QColor(0, 255, 255), 2, Qt.PenStyle.DashLine)
            painter.setPen(preview_pen)
            painter.drawRect(preview_rect)
            painter.setBrush(QColor(0, 255, 255, 50))
            painter.fillRect(preview_rect, QColor(0, 255, 255, 50))
            painter.setPen(QColor(0, 0, 0))
            painter.drawText(preview_rect, Qt.AlignmentFlag.AlignCenter, self.dropPreviewClip.track)
        # Draw clips.
        self.paintClips(painter)
        # Draw playhead.
        playhead_x = self.left_panel_width + int(self.playhead * self.scale)
        painter.setPen(QPen(QColor(255, 0, 0), 2))
        painter.drawLine(playhead_x, self.ruler_height, playhead_x, self.height())
        painter.end()

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-shot"):
            event.acceptProposedAction()
            data = event.mimeData().data("application/x-shot")
            try:
                shots_data = json.loads(bytes(data).decode("utf-8"))
                if shots_data:
                    shot = Shot(**shots_data[0])
                    clip_length = (shot.outPoint - shot.inPoint) * shot.duration
                    drop_time = max(0.0, (event.pos().x() - self.left_panel_width) / self.scale)
                    drop_y = event.pos().y()
                    track_index = (drop_y - self.ruler_height) // self.track_height
                    if track_index < 0:
                        track_index = 0
                    if track_index >= len(self.tracks):
                        track_index = len(self.tracks) - 1
                    selected_track = self.tracks[track_index]
                    self.dropPreviewClip = TimelineClip(shot=shot, track=selected_track, start_time=drop_time,
                                                        length=clip_length)
            except Exception as e:
                print("[DEBUG] DragEnter preview error:", e)
            self.update()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat("application/x-shot"):
            event.acceptProposedAction()
            data = event.mimeData().data("application/x-shot")
            try:
                shots_data = json.loads(bytes(data).decode("utf-8"))
                if shots_data:
                    shot = Shot(**shots_data[0])
                    clip_length = (shot.outPoint - shot.inPoint) * shot.duration
                    drop_time = max(0.0, (event.pos().x() - self.left_panel_width) / self.scale)
                    drop_y = event.pos().y()
                    track_index = (drop_y - self.ruler_height) // self.track_height
                    if track_index < 0:
                        track_index = 0
                    if track_index >= len(self.tracks):
                        track_index = len(self.tracks) - 1
                    selected_track = self.tracks[track_index]
                    self.dropPreviewClip = TimelineClip(shot=shot, track=selected_track, start_time=drop_time,
                                                        length=clip_length)
            except Exception as e:
                print("[DEBUG] DragMove preview error:", e)
            self.update()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.dropPreviewClip = None
        self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasFormat("application/x-shot"):
            data = event.mimeData().data("application/x-shot")
            try:
                shots_data = json.loads(bytes(data).decode("utf-8"))
                pos = event.pos()
                drop_time = max(0.0, (pos.x() - self.left_panel_width) / self.scale)
                drop_y = pos.y()
                track_index = (drop_y - self.ruler_height) // self.track_height
                if track_index < 0:
                    track_index = 0
                if track_index >= len(self.tracks):
                    track_index = len(self.tracks) - 1
                selected_track = self.tracks[track_index]
                print(f"[DEBUG] Drop event at pos {pos}, calculated drop_time: {drop_time}s on track '{selected_track}'")
                for shot_dict in shots_data:
                    shot = Shot(**shot_dict)
                    clip_length = (shot.outPoint - shot.inPoint) * shot.duration
                    new_clip = TimelineClip(shot=shot, track=selected_track, start_time=drop_time, length=clip_length)
                    clips_on_track = [c for c in self.timeline_clips if c.track == selected_track]
                    if clips_on_track:
                        clips_on_track.sort(key=lambda c: c.start_time)
                        last_clip = clips_on_track[-1]
                        gap = new_clip.start_time - (last_clip.start_time + last_clip.length)
                        if abs(gap) < 0.2:
                            new_clip.start_time = last_clip.start_time + last_clip.length
                            print(f"[DEBUG] Autosnapped new clip to {new_clip.start_time}s on track '{selected_track}'")
                    self.handleOverlap(new_clip)
                    self.timeline_clips.append(new_clip)
                    print(f"[DEBUG] Added clip: {new_clip}")
                    if shot.linkedAudio:
                        audio_track = None
                        for t in self.tracks:
                            if t.lower().startswith("audio"):
                                audio_track = t
                                break
                        if not audio_track:
                            audio_track = "Audio"
                        audio_clip = TimelineClip(shot=shot, track=audio_track, start_time=new_clip.start_time, length=clip_length)
                        self.handleOverlap(audio_clip)
                        self.timeline_clips.append(audio_clip)
                        print(f"[DEBUG] Added linked audio clip: {audio_clip}")
                self.dropPreviewClip = None
                self.update()
                event.acceptProposedAction()
            except Exception as e:
                print("[DEBUG] Drop error:", e)
                event.ignore()
        else:
            event.ignore()

########################################################################
# PreviewWidget: The main preview showing the frame at the playhead position
########################################################################
class PreviewWidget(QWidget):
    def __init__(self, timeline_widget: MultiTrackTimelineWidget, parent=None):
        super().__init__(parent)
        self.timeline_widget = timeline_widget
        self.setMinimumHeight(200)
        self.timeline_widget.playheadChanged.connect(self.update)
        print("[DEBUG] PreviewWidget initialized")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(20, 20, 20))
        current_clip = None
        for clip in self.timeline_widget.timeline_clips:
            if clip.track.lower().startswith("video"):
                clip_start = clip.start_time
                clip_end = clip.start_time + clip.length
                if clip_start <= self.timeline_widget.playhead <= clip_end:
                    current_clip = clip
                    break
        if current_clip:
            fraction_in_clip = (self.timeline_widget.playhead - current_clip.start_time) / current_clip.length
            effective_fraction = current_clip.shot.inPoint + fraction_in_clip * (current_clip.shot.outPoint - current_clip.shot.inPoint)
            print(f"[DEBUG] Preview: showing frame for clip '{current_clip.shot.name}', effective fraction: {effective_fraction}")
            frame_pix = getVideoFrame(current_clip.shot.videoPath, effective_fraction, self.size())
            painter.drawPixmap(self.rect(), frame_pix)
        else:
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No clip under playhead")
        painter.end()

########################################################################
# ShotManagerWidget: Registers three dock widgets and a toolbar in the provided MainWindow.
########################################################################
class ShotManagerWidget:
    def __init__(self, main_window: QtWidgets.QMainWindow):
        self.main_window = main_window

        # Create Shot Library dock.
        self.shotListView = ShotListView()
        self.shotListDock = QDockWidget("Shot Library", main_window)
        self.shotListDock.setWidget(self.shotListView)
        self.shotListDock.setObjectName("shot_library_dock")
        main_window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.shotListDock)
        print("[DEBUG] Registered Shot Library dock.")

        # Create Timeline dock.
        self.timelineWidget = MultiTrackTimelineWidget()
        self.timelineDock = QDockWidget("Timeline", main_window)
        self.timelineDock.setWidget(self.timelineWidget)
        self.timelineDock.setObjectName("timeline_dock")
        main_window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.timelineDock)
        print("[DEBUG] Registered Timeline dock.")

        # Create Preview dock.
        self.previewWidget = PreviewWidget(self.timelineWidget)
        self.previewDock = QDockWidget("Preview", main_window)
        self.previewDock.setWidget(self.previewWidget)
        self.previewDock.setObjectName("preview_dock")
        main_window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.previewDock)
        print("[DEBUG] Registered Preview dock.")

        # Create Toolbar with Select, Blade, and Add Track tools.
        self.toolbar = QtWidgets.QToolBar("Timeline Tools", main_window)
        main_window.addToolBar(self.toolbar)
        self.selectAction = QtGui.QAction("Select", self.toolbar)
        self.bladeAction = QtGui.QAction("Blade", self.toolbar)
        self.addVideoTrackAction = QtGui.QAction("Add Video Track", self.toolbar)
        self.addAudioTrackAction = QtGui.QAction("Add Audio Track", self.toolbar)
        self.toolbar.addAction(self.selectAction)
        self.toolbar.addAction(self.bladeAction)
        self.toolbar.addSeparator()
        self.toolbar.addAction(self.addVideoTrackAction)
        self.toolbar.addAction(self.addAudioTrackAction)
        self.selectAction.triggered.connect(lambda: self.setToolMode("select"))
        self.bladeAction.triggered.connect(lambda: self.setToolMode("blade"))
        self.addVideoTrackAction.triggered.connect(self.timelineWidget.addVideoTrack)
        self.addAudioTrackAction.triggered.connect(self.timelineWidget.addAudioTrack)
        print("[DEBUG] Registered Timeline toolbar.")

    def setToolMode(self, mode: str):
        self.timelineWidget.toolMode = mode
        print(f"[DEBUG] Tool mode set to '{mode}'")
