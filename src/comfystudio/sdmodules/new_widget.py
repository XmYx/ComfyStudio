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

import os
import sys
import json
import cv2
import copy
from dataclasses import dataclass
from typing import List

from qtpy import QtCore, QtGui, QtWidgets
from qtpy.QtCore import Qt, QSize, QRect
from qtpy.QtGui import QPixmap, QPainter, QPen, QColor
from qtpy.QtWidgets import (
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

from comfystudio.sdmodules.cs_datastruts import Shot


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

    def _setPlayheadFromEvent(self, event):
        # The label starts at left_panel_width, so translate into widget coords first.
        tw = self.timeline_widget
        new_playhead = tw.xToTime(event.pos().x() + tw.left_panel_width)
        tw.playhead = max(0.0, new_playhead)
        tw.playheadChanged.emit(tw.playhead)
        tw.update()
        self.updateText()
        event.accept()

    def mousePressEvent(self, event):
        self._setPlayheadFromEvent(event)

    def mouseMoveEvent(self, event):
        self._setPlayheadFromEvent(event)

########################################################################
# ShotListView: The Shot Library (grid view)
########################################################################
class ShotListView(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.zoomFactor = 1.0
        self.baseIconSize = QSize(160, 120)

        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setIconSize(QSize(160, 120))
        self.setSpacing(10)
        self.setDragEnabled(True)
        self.setAcceptDrops(False)
        self.setSortingEnabled(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setMouseTracking(True)
        self.hoverFraction = {}  # {id(item): fraction}
        self.currentHoverItem = None
        self.inMarkers = {}
        self.outMarkers = {}
        self.mainWin = parent
        print("[DEBUG] ShotListView initialized")

    def mimeData(self, items):
        mimeData = QtCore.QMimeData()
        main_win = self.mainWin or QtWidgets.QApplication.activeWindow()
        shots = []
        for item in items:
            shot_idx = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(shot_idx, int) or not (0 <= shot_idx < len(getattr(main_win, "shots", []))):
                continue
            shot = main_win.shots[shot_idx]
            shots.append({
                "name": shot.name,
                "duration": shot.raw_duration,
                "videoPath": shot.videoPath,
                "stillPath": shot.stillPath,
                "thumbnail_path": shot.thumbnail_path,
                "inPoint": shot.inPoint,
                "outPoint": shot.outPoint,
                "linkedAudio": shot.linkedAudio,
                "fps": shot.fps,
            })
        mimeData.setData("application/x-shot", json.dumps(shots).encode("utf-8"))
        return mimeData

    def resizeEvent(self, event):
        # Call the parent resizeEvent and force a redraw.
        self.update()
        self.doItemsLayout()
        super().resizeEvent(event)


    def wheelEvent(self, event):
        # Use Ctrl+wheel to zoom (adjust the icon size and refresh the view)
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y() / 120  # one notch = 1.0 increment (or decrement)
            self.zoomFactor *= (1 + 0.1 * delta)
            # Clamp the zoom factor between 0.5 and 3.0 (for example)
            self.zoomFactor = max(0.25, min(self.zoomFactor, 10.0))
            new_size = QSize(int(self.baseIconSize.width() * self.zoomFactor),
                             int(self.baseIconSize.height() * self.zoomFactor))
            self.setIconSize(new_size)
            self.update()
        else:
            super().wheelEvent(event)
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
                shot_idx = self.currentHoverItem.data(Qt.ItemDataRole.UserRole)
                shot = self.mainWin.shots[shot_idx]
                shot.inPoint = self.inMarkers[item_id]
                print(f"[DEBUG] Set In marker for item {item_id} at {self.inMarkers[item_id]}")
            elif event.key() == Qt.Key.Key_O:
                self.outMarkers[item_id] = self.hoverFraction.get(item_id, 1.0)
                shot_idx = self.currentHoverItem.data(Qt.ItemDataRole.UserRole)
                shot = self.mainWin.shots[shot_idx]
                shot.outPoint = self.outMarkers[item_id]
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
                if isinstance(shot_idx, int) and 0 <= shot_idx < len(self.mainWin.shots):
                    shot = self.mainWin.shots[shot_idx]
                    if shot.videoPath:
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


class TimelineNavigator(QWidget):
    """
    Overview strip showing which part of the timeline is visible.

    The handle rect is DERIVED state: the timeline's scale/time_offset are the
    single source of truth and syncFromTimeline() recomputes the rect from
    them. User interaction (drag = pan, edge-drag = zoom, empty-area click =
    center-pan) emits rangeChanged, which the timeline applies; a plain click
    never changes the zoom.
    """

    rangeChanged = Signal(float, float)  # Emits new visible start and end times

    EDGE = 5        # px hit zone for the resize handles
    MIN_WIDTH = 20  # px minimum handle width while resizing

    def __init__(self, timeline_widget, parent=None):
        super().__init__(parent)
        self.timeline_widget = timeline_widget
        self.setMinimumHeight(40)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)  # hover cursor on the resize edges
        self.visible_range = QRect(5, 5, 100, self.height() - 10)
        self.dragging = False
        self.resizing_left = False
        self.resizing_right = False
        self.drag_offset = 0

    def _trackRect(self) -> QRect:
        return QRect(5, 5, max(1, self.width() - 10), max(1, self.height() - 10))

    def _fullLength(self) -> float:
        tw = self.timeline_widget
        return tw.timeline_end if tw.timeline_end > 0 else 100.0

    def _interacting(self) -> bool:
        return self.dragging or self.resizing_left or self.resizing_right

    def syncFromTimeline(self):
        """Recomputes the handle rect from the timeline's scale/time_offset."""
        if self._interacting():
            return
        tw = self.timeline_widget
        full = self._fullLength()
        track = self._trackRect()
        visible_secs = max(0.0, tw.width() - tw.left_panel_width) / max(tw.scale, 1e-6)
        w = track.width() * min(1.0, visible_secs / full)
        w = max(8.0, w)
        x = track.x() + track.width() * min(1.0, tw.time_offset / full)
        x = max(float(track.x()), min(x, track.right() - w))
        self.visible_range = QRect(int(round(x)), track.y(), int(round(w)), track.height())
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(220, 220, 220))
        painter.fillRect(self._trackRect(), QColor(200, 200, 200))
        painter.setBrush(QColor(100, 150, 250, 150))
        painter.drawRect(self.visible_range)
        painter.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Derived state: recomputing preserves the represented time window,
        # so resizing the navigator keeps the timeline zoom consistent.
        self.syncFromTimeline()

    def mousePressEvent(self, event):
        x = event.pos().x()
        vr = self.visible_range
        if abs(x - vr.left()) <= self.EDGE:
            self.resizing_left = True
        elif abs(x - vr.right()) <= self.EDGE:
            self.resizing_right = True
        elif vr.contains(event.pos()):
            self.dragging = True
            self.drag_offset = x - vr.x()
        else:
            # Empty-area click: center-pan the handle there (zoom unchanged)
            # and allow continued dragging.
            track = self._trackRect()
            new_x = max(track.x(), min(int(x - vr.width() / 2), track.right() - vr.width()))
            self.visible_range.moveLeft(new_x)
            self.dragging = True
            self.drag_offset = vr.width() // 2
            self.update()
            self.emitRangeChanged()
        event.accept()

    def mouseMoveEvent(self, event):
        track = self._trackRect()
        vr = self.visible_range
        x = event.pos().x()
        if self.dragging:
            new_x = max(track.x(), min(x - self.drag_offset, track.right() - vr.width()))
            vr.moveLeft(int(round(new_x)))
        elif self.resizing_left:
            new_left = max(track.x(), min(x, vr.right() - self.MIN_WIDTH))
            vr.setLeft(int(round(new_left)))
        elif self.resizing_right:
            new_right = max(vr.left() + self.MIN_WIDTH, min(x, track.right()))
            vr.setRight(int(round(new_right)))
        else:
            on_edge = abs(x - vr.left()) <= self.EDGE or abs(x - vr.right()) <= self.EDGE
            self.setCursor(Qt.CursorShape.SizeHorCursor if on_edge else Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        self.update()
        self.emitRangeChanged()
        event.accept()

    def mouseReleaseEvent(self, event):
        was_interacting = self._interacting()
        self.dragging = False
        self.resizing_left = False
        self.resizing_right = False
        if was_interacting:
            # Snap the rect to the canonical mapping of the applied range.
            self.syncFromTimeline()
        event.accept()

    def emitRangeChanged(self):
        full = self._fullLength()
        track = self._trackRect()
        start_ratio = (self.visible_range.x() - track.x()) / track.width()
        end_ratio = (self.visible_range.right() - track.x()) / track.width()
        start_ratio = max(0.0, min(1.0, start_ratio))
        end_ratio = max(0.0, min(1.0, end_ratio))
        if end_ratio > start_ratio:
            self.rangeChanged.emit(full * start_ratio, full * end_ratio)

class RangeSelectorWidget(QWidget):
    rangeChanged = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(30)
        # Default in/out times in seconds (adjust as needed)
        self.in_handle = 0.0
        self.out_handle = 5.0
        self.handle_width = 10
        self.dragging_in = False
        self.dragging_out = False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(180, 180, 180))
        scale = self.parent().scale if self.parent() else 100.0
        in_x = int(self.in_handle * scale)
        out_x = int(self.out_handle * scale)
        # Draw the selected range
        painter.setBrush(QColor(100, 100, 250, 100))
        painter.drawRect(in_x, 0, out_x - in_x, self.height())
        # Draw the in-handle
        painter.setBrush(QColor(100, 200, 100))
        painter.drawRect(in_x - self.handle_width // 2, 0, self.handle_width, self.height())
        # Draw the out-handle
        painter.setBrush(QColor(200, 100, 100))
        painter.drawRect(out_x - self.handle_width // 2, 0, self.handle_width, self.height())
        painter.end()

    def mousePressEvent(self, event):
        x = event.x()
        scale = self.parent().scale if self.parent() else 100.0
        in_x = int(self.in_handle * scale)
        out_x = int(self.out_handle * scale)
        if abs(x - in_x) < self.handle_width:
            self.dragging_in = True
        elif abs(x - out_x) < self.handle_width:
            self.dragging_out = True
        event.accept()

    def mouseMoveEvent(self, event):
        scale = self.parent().scale if self.parent() else 100.0
        x = event.x()
        time = x / scale
        if self.dragging_in:
            self.in_handle = min(time, self.out_handle - 0.1)
        elif self.dragging_out:
            self.out_handle = max(time, self.in_handle + 0.1)
        self.rangeChanged.emit(self.in_handle, self.out_handle)
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event):
        self.dragging_in = False
        self.dragging_out = False
        event.accept()

    def setRange(self, in_time: float, out_time: float):
        self.in_handle = in_time
        self.out_handle = out_time
        self.update()

    def getRange(self):
        return self.in_handle, self.out_handle



########################################################################
# MultiTrackTimelineWidget: The multitrack timeline dock with enhanced tools
########################################################################
class MultiTrackTimelineWidget(QWidget):
    playheadChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.scale = 100.0  # pixels per second
        self.time_offset = 0.0  # leftmost visible time (seconds) for panning/zooming
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
        self.frameReadoutLabel.setGeometry(self.left_panel_width, 0, self.width() - self.left_panel_width,
                                           self.ruler_height)
        self.frameReadoutLabel.show()
        # Create the timeline navigator (for zoom and range selection)
        self._syncingNavigator = False
        self.navigator = TimelineNavigator(self, self)
        self.navigator.setGeometry(0, self.height() - 40, self.width(), 40)
        self.navigator.rangeChanged.connect(self.onNavigatorRangeChanged)
        self.navigator.syncFromTimeline()

        # Keep the readout label in sync with playback/dragging.
        self.playheadChanged.connect(lambda _t: self.frameReadoutLabel.updateText())

        print("[DEBUG] MultiTrackTimelineWidget initialized")

    # --- Time <-> pixel mapping (accounts for zoom scale and pan offset) ---
    def timeToX(self, t: float) -> int:
        return self.left_panel_width + int(round((t - self.time_offset) * self.scale))

    def xToTime(self, x: float) -> float:
        return self.time_offset + (x - self.left_panel_width) / self.scale

    def setWorkArea(self, start: float, end: float):
        self.work_area_start = start
        self.work_area_end = end
        self._syncNavigator()
        self.update()

    def _syncNavigator(self):
        navigator = getattr(self, "navigator", None)
        if navigator is not None:
            navigator.syncFromTimeline()

    def onNavigatorRangeChanged(self, start, end):
        # The navigator selects the visible time range: pan + zoom.
        if self._syncingNavigator:
            return
        if (end - start) > 0:
            self._syncingNavigator = True
            try:
                viewport = max(1, self.width() - self.left_panel_width)
                # Same zoom clamp as Ctrl+wheel so both paths agree.
                self.scale = max(2.0, min(viewport / (end - start), 2000.0))
                self.time_offset = max(0.0, start)
            finally:
                self._syncingNavigator = False
        self.update()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Zoom around the cursor position so the time under the mouse stays put.
            pos_x = event.position().x() if hasattr(event, "position") else event.pos().x()
            anchor_time = self.xToTime(pos_x)
            factor = 1.15 if delta > 0 else (1 / 1.15)
            self.scale = max(2.0, min(self.scale * factor, 2000.0))
            self.time_offset = max(0.0, anchor_time - (pos_x - self.left_panel_width) / self.scale)
            self._syncNavigator()
            self.update()
            event.accept()
        else:
            # Plain wheel pans the timeline horizontally.
            pan_seconds = (-delta / 120.0) * (100.0 / self.scale)
            self.time_offset = max(0.0, self.time_offset + pan_seconds)
            self._syncNavigator()
            self.update()
            event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.frameReadoutLabel.setGeometry(self.left_panel_width, 0, self.width() - self.left_panel_width,
                                           self.ruler_height)
        self.navigator.setGeometry(0, self.height() - 40, self.width(), 40)
        # The visible-seconds window depends on the widget width.
        self._syncNavigator()

    def clipRect(self, clip: TimelineClip) -> QRect:
        try:
            track_index = self.tracks.index(clip.track)
        except ValueError:
            track_index = 0
        # Use clip.length directly for width
        x = self.timeToX(clip.start_time)
        y = self.ruler_height + track_index * self.track_height + 5
        width = max(1, int(clip.length * self.scale))
        height = self.track_height - 10
        return QRect(x, y, width, height)

    def snapValue(self, value, snap_list, threshold=10):
        best = value
        best_dist = threshold
        for snap_val in snap_list:
            dist = abs(value - snap_val)
            if dist < best_dist:
                best = snap_val
                best_dist = dist
        return best

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
        # Linked clips (e.g. the audio counterpart) share the same Shot object;
        # collect them by identity BEFORE mutating anything.
        group = [clip] + [c for c in self.timeline_clips
                          if c is not clip and c.shot is clip.shot and clip.shot.linkedAudio]

        shot = clip.shot
        src_span = shot.outPoint - shot.inPoint
        split_frac = shot.inPoint + src_span * ((split_time - original_start) / original_length)

        # The right-hand halves get their own Shot copy so the two halves can be
        # trimmed independently; linked halves share the same copy to stay linked.
        right_shot = copy.deepcopy(shot)
        right_shot.inPoint = split_frac
        right_shot.outPoint = shot.outPoint
        shot.outPoint = split_frac  # left halves keep the original shot, trimmed

        for member in group:
            if member not in self.timeline_clips:
                continue
            m_start = member.start_time
            m_len = member.length
            m_split = split_time
            if not (m_start < m_split < m_start + m_len):
                continue
            left_length = m_split - m_start
            right_length = (m_start + m_len) - m_split
            left_clip = TimelineClip(shot=shot, track=member.track, start_time=m_start, length=left_length)
            right_clip = TimelineClip(shot=right_shot, track=member.track, start_time=m_split, length=right_length)
            self.timeline_clips.remove(member)
            if member in self.selectedClips:
                self.selectedClips.remove(member)
            self.timeline_clips.append(left_clip)
            self.timeline_clips.append(right_clip)
        print(f"[DEBUG] Split clip '{shot.name}' at {split_time}s")

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
                        if clip.shot.raw_duration > 0:
                            clip.shot.outPoint = min(1.0, clip.shot.inPoint + (new_length / clip.shot.raw_duration))
                        print(f"[DEBUG] Overlap: Trimmed clip '{clip.shot.name}' to new length {new_length}s")
                        if new_length < 0.1:
                            self.timeline_clips.remove(clip)
                    elif new_start <= existing_start < new_end:
                        self.timeline_clips.remove(clip)
                        print(f"[DEBUG] Overlap: Removed clip '{clip.shot.name}' due to overlap with new clip")

    def updateTimelineRange(self):
        old_end = self.timeline_end
        if self.timeline_clips:
            self.timeline_end = max(clip.start_time + clip.length for clip in self.timeline_clips)
        else:
            self.timeline_end = 0.0
        # Keep the auto work area tracking the timeline end unless the user changed it.
        if self.work_area_end == 0.0 or abs(self.work_area_end - old_end) < 1e-6:
            self.work_area_end = self.timeline_end
        # timeline_end rescales the navigator's whole mapping.
        self._syncNavigator()

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
                self.updateTimelineRange()
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
                self.updateTimelineRange()
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

    def contextMenuEvent(self, event):
        """Right-click on a track label: hide/show video tracks, mute/solo/level audio."""
        pos = event.pos()
        if pos.x() >= self.left_panel_width or pos.y() < self.ruler_height:
            super().contextMenuEvent(event)
            return
        track_index = int((pos.y() - self.ruler_height) // self.track_height)
        if not (0 <= track_index < len(self.tracks)):
            return
        track = self.tracks[track_index]
        ts = self.track_settings.setdefault(track, {})
        menu = QMenu(self)
        if track.lower().startswith("video"):
            visible = ts.get("visible", True)
            toggleAct = menu.addAction("Hide Track" if visible else "Show Track")
            chosen = menu.exec(event.globalPos())
            if chosen == toggleAct:
                ts["visible"] = not visible
        else:
            mute = ts.get("mute", False)
            solo = ts.get("solo", False)
            muteAct = menu.addAction("Unmute" if mute else "Mute")
            soloAct = menu.addAction("Unsolo" if solo else "Solo")
            levelAct = menu.addAction("Set Level...")
            chosen = menu.exec(event.globalPos())
            if chosen == muteAct:
                ts["mute"] = not mute
            elif chosen == soloAct:
                ts["solo"] = not solo
            elif chosen == levelAct:
                val, ok = QtWidgets.QInputDialog.getDouble(
                    self, "Track Level", f"Level for {track}:",
                    ts.get("level", 1.0), 0.0, 2.0, 2
                )
                if ok:
                    ts["level"] = val
        self.update()

    def mousePressEvent(self, event):
        pos = event.pos()
        # Clicking in the ruler area starts dragging the playhead.
        if pos.y() < self.ruler_height and pos.x() >= self.left_panel_width:
            self.dragging_playhead = True
            self.playhead = max(0.0, self.xToTime(pos.x()))
            self.playheadChanged.emit(self.playhead)
            self.update()
            return
        if self.toolMode == "blade":
            # Blade splits at the click position.
            for clip in reversed(self.timeline_clips):
                if self.clipRect(clip).contains(pos):
                    self.splitClip(clip, self.xToTime(pos.x()))
                    self.activeClip = None
                    self.updateTimelineRange()
                    self.update()
                    return
            super().mousePressEvent(event)
            return
        # Check if click is on a resize handle first
        handle_zone = 10
        for clip in reversed(self.timeline_clips):
            rect = self.clipRect(clip)
            left_zone = QRect(rect.left(), rect.top(), handle_zone, rect.height())
            right_zone = QRect(rect.right() - handle_zone, rect.top(), handle_zone, rect.height())
            if left_zone.contains(pos):
                self.activeClip = clip
                self.activeClipAction = "resize_left"
                self.activeClipOriginalStart = clip.start_time
                self.activeClipOriginalEnd = clip.start_time + clip.length
                self.update()
                return
            elif right_zone.contains(pos):
                self.activeClip = clip
                self.activeClipAction = "resize_right"
                self.activeClipOriginalStart = clip.start_time
                self.activeClipOriginalEnd = clip.start_time + clip.length
                self.update()
                return
        if self.toolMode == "select":
            clicked_clip = None
            for clip in reversed(self.timeline_clips):
                if self.clipRect(clip).contains(pos):
                    clicked_clip = clip
                    break
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
        else:
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
            handle_zone = 10
            for clip in self.timeline_clips:
                rect = self.clipRect(clip)
                left_zone = QRect(rect.left(), rect.top(), handle_zone, rect.height())
                right_zone = QRect(rect.right() - handle_zone, rect.top(), handle_zone, rect.height())
                if left_zone.contains(pos):
                    self.hoveredClip = clip
                    self.hoveredHandle = "resize_left"
                    break
                elif right_zone.contains(pos):
                    self.hoveredClip = clip
                    self.hoveredHandle = "resize_right"
                    break
        if self.activeClip:
            if self.activeClipAction == "move":
                if len(self.selectedClips) > 1:
                    # Move the whole selection, preserving relative offsets, without
                    # letting any member go below t=0.
                    deltas = []
                    for clip in self.selectedClips:
                        orig_start, _ = self.activeClipOriginalPositions[id(clip)]
                        new_left = event.pos().x() - self.activeClipOffsets[id(clip)]
                        deltas.append(self.xToTime(new_left) - orig_start)
                    delta = deltas[0] if deltas else 0.0
                    min_start = min(self.activeClipOriginalPositions[id(c)][0] for c in self.selectedClips)
                    delta = max(delta, -min_start)
                    for clip in self.selectedClips:
                        orig_start, _ = self.activeClipOriginalPositions[id(clip)]
                        clip.start_time = orig_start + delta
                else:
                    new_left = event.pos().x() - self.activeClipOffset
                    # Snap to other clip edges on the same track and to the playhead.
                    snap_targets = [self.timeToX(self.playhead)]
                    for c in self.timeline_clips:
                        if c is not self.activeClip and c.track.lower() == self.activeClip.track.lower():
                            snap_targets.append(self.timeToX(c.start_time))
                            snap_targets.append(self.timeToX(c.start_time + c.length))
                    new_left_pix = self.snapValue(new_left, snap_targets)
                    self.activeClip.start_time = max(0.0, self.xToTime(new_left_pix))
            elif self.activeClipAction == "resize_left":
                new_start_time = self.xToTime(event.pos().x())
                min_effective = 1.0 / self.fps
                shot = self.activeClip.shot
                # Can't reveal media before the source start: available head = inPoint * raw.
                max_head = shot.inPoint * shot.raw_duration
                new_start_time = max(new_start_time, self.activeClipOriginalStart - max_head, 0.0)
                if self.activeClipOriginalEnd - new_start_time < min_effective:
                    new_start_time = self.activeClipOriginalEnd - min_effective
                self.activeClip.start_time = new_start_time
                self.activeClip.length = self.activeClipOriginalEnd - new_start_time
                if shot.raw_duration > 0:
                    shot.inPoint = min(max(0.0, shot.outPoint - (self.activeClip.length / shot.raw_duration)),
                                       shot.outPoint - (min_effective / shot.raw_duration))
            elif self.activeClipAction == "resize_right":
                new_end_time = self.xToTime(event.pos().x())
                min_effective = 1.0 / self.fps
                shot = self.activeClip.shot
                # Can't extend past the source end: available tail = (1 - inPoint) * raw.
                if shot.raw_duration > 0:
                    max_len = (1.0 - shot.inPoint) * shot.raw_duration
                    new_end_time = min(new_end_time, self.activeClipOriginalStart + max_len)
                if new_end_time - self.activeClipOriginalStart < min_effective:
                    new_end_time = self.activeClipOriginalStart + min_effective
                self.activeClip.length = new_end_time - self.activeClipOriginalStart
                if shot.raw_duration > 0:
                    new_outPoint = shot.inPoint + (self.activeClip.length / shot.raw_duration)
                    shot.outPoint = min(1.0, max(new_outPoint, shot.inPoint + (min_effective / shot.raw_duration)))
            self.update()
        elif self.dragging_playhead:
            self.playhead = max(0.0, self.xToTime(pos.x()))
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
            self.updateTimelineRange()
            self.update()
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
            right_handle = QRect(clip_rect.right() - self.handle_width, clip_rect.top(), self.handle_width,
                                 clip_rect.height())
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

    def _rulerStep(self) -> float:
        """Pick a tick step so labels stay at least ~60px apart at any zoom."""
        candidates = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600]
        for step in candidates:
            if step * self.scale >= 60:
                return step
        return candidates[-1]

    def paintEvent(self, event):
        painter = QPainter(self)
        # Draw ruler.
        ruler_rect = QRect(self.left_panel_width, 0, self.width() - self.left_panel_width, self.ruler_height)
        painter.fillRect(ruler_rect, QColor(50, 50, 50))
        step = self._rulerStep()
        t = max(0.0, (self.time_offset // step) * step)
        painter.setPen(QPen(QColor(200, 200, 200), 1))
        while True:
            x = self.timeToX(t)
            if x > self.width():
                break
            if x >= self.left_panel_width:
                painter.drawLine(x, 0, x, self.ruler_height)
                label = f"{t:g}s"
                painter.drawText(x + 2, self.ruler_height - 2, label)
            t += step
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
            preview_rect = self.clipRect(self.dropPreviewClip)
            preview_pen = QPen(QColor(0, 255, 255), 2, Qt.PenStyle.DashLine)
            painter.setPen(preview_pen)
            painter.drawRect(preview_rect)
            painter.setBrush(QColor(0, 255, 255, 50))
            painter.fillRect(preview_rect, QColor(0, 255, 255, 50))
            painter.setPen(QColor(0, 0, 0))
            painter.drawText(preview_rect, Qt.AlignmentFlag.AlignCenter, self.dropPreviewClip.track)
        # Draw clips.
        self.paintClips(painter)
        # Draw playhead (only when inside the visible range).
        playhead_x = self.timeToX(self.playhead)
        if playhead_x >= self.left_panel_width:
            painter.setPen(QPen(QColor(255, 0, 0), 2))
            painter.drawLine(playhead_x, self.ruler_height, playhead_x, self.height())
        painter.end()

    def _dropTargetForEvent(self, event):
        """Returns (drop_time, track_name) for a drag/drop event position."""
        pos = event.pos() if hasattr(event, "pos") else event.position().toPoint()
        drop_time = max(0.0, self.xToTime(pos.x()))
        track_index = int((pos.y() - self.ruler_height) // self.track_height)
        track_index = max(0, min(track_index, len(self.tracks) - 1))
        return drop_time, self.tracks[track_index]

    def _updateDropPreview(self, event):
        if not event.mimeData().hasFormat("application/x-shot"):
            event.ignore()
            return
        event.acceptProposedAction()
        try:
            shots_data = json.loads(bytes(event.mimeData().data("application/x-shot")).decode("utf-8"))
            if shots_data:
                shot = Shot.from_dict(shots_data[0])
                clip_length = max(shot.duration, 1.0 / max(self.fps, 1))
                drop_time, selected_track = self._dropTargetForEvent(event)
                self.dropPreviewClip = TimelineClip(shot=shot, track=selected_track, start_time=drop_time,
                                                    length=clip_length)
        except Exception as e:
            print("[DEBUG] Drag preview error:", e)
        self.update()

    def dragEnterEvent(self, event):
        self._updateDropPreview(event)

    def dragMoveEvent(self, event):
        self._updateDropPreview(event)

    def dragLeaveEvent(self, event):
        self.dropPreviewClip = None
        self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasFormat("application/x-shot"):
            data = event.mimeData().data("application/x-shot")
            try:
                shots_data = json.loads(bytes(data).decode("utf-8"))
                drop_time, selected_track = self._dropTargetForEvent(event)
                print(f"[DEBUG] Drop event, drop_time: {drop_time}s on track '{selected_track}'")
                for shot_dict in shots_data:
                    shot = Shot.from_dict(shot_dict)
                    clip_length = max(shot.duration, 1.0 / max(self.fps, 1))
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
                        audio_clip = TimelineClip(shot=shot, track=audio_track, start_time=new_clip.start_time,
                                                  length=clip_length)
                        self.handleOverlap(audio_clip)
                        self.timeline_clips.append(audio_clip)
                        print(f"[DEBUG] Added linked audio clip: {audio_clip}")
                    # When several shots are dropped together, lay them out back-to-back.
                    drop_time = new_clip.start_time + new_clip.length
                self.dropPreviewClip = None
                self.updateTimelineRange()
                self.update()
                event.acceptProposedAction()
            except Exception as e:
                print("[DEBUG] Drop error:", e)
                event.ignore()
        else:
            event.ignore()

    # --- Project persistence -------------------------------------------------
    def serializeTimeline(self) -> dict:
        """Serializes tracks, settings and clips so the timeline can be saved with a project."""
        return {
            "video_tracks": list(self.video_tracks),
            "audio_tracks": list(self.audio_tracks),
            "track_settings": copy.deepcopy(self.track_settings),
            "playhead": self.playhead,
            "scale": self.scale,
            "time_offset": self.time_offset,
            "work_area_start": self.work_area_start,
            "work_area_end": self.work_area_end,
            "clips": [
                {
                    "shot": clip.shot.to_dict(),
                    "track": clip.track,
                    "start_time": clip.start_time,
                    "length": clip.length,
                }
                for clip in self.timeline_clips
            ],
        }

    def restoreTimeline(self, data: dict):
        """Restores a timeline previously produced by serializeTimeline()."""
        if not isinstance(data, dict):
            return
        self.video_tracks = list(data.get("video_tracks") or ["Video 1"])
        self.audio_tracks = list(data.get("audio_tracks") or ["Audio 1"])
        self.track_settings = data.get("track_settings") or {}
        for t in self.video_tracks:
            self.track_settings.setdefault(t, {"visible": True})
        for t in self.audio_tracks:
            self.track_settings.setdefault(t, {"mute": False, "solo": False, "level": 1.0})
        self.updateTracks()

        self.timeline_clips = []
        for clip_data in data.get("clips", []):
            try:
                shot = Shot.from_dict(clip_data.get("shot", {}))
                track = clip_data.get("track", self.tracks[0])
                if track not in self.tracks:
                    track = self.tracks[0]
                self.timeline_clips.append(TimelineClip(
                    shot=shot,
                    track=track,
                    start_time=float(clip_data.get("start_time", 0.0)),
                    length=max(0.01, float(clip_data.get("length", shot.duration or 1.0))),
                ))
            except Exception as e:
                print(f"[DEBUG] Skipping unrestorable timeline clip: {e}")
        self.playhead = float(data.get("playhead", 0.0))
        self.scale = float(data.get("scale", 100.0)) or 100.0
        self.time_offset = max(0.0, float(data.get("time_offset", 0.0)))
        self.work_area_start = float(data.get("work_area_start", 0.0))
        self.work_area_end = float(data.get("work_area_end", 0.0))
        self.selectedClips = []
        self.activeClip = None
        self.updateTimelineRange()
        self.frameReadoutLabel.updateText()
        self.update()

    def clearTimeline(self):
        self.timeline_clips = []
        self.selectedClips = []
        self.activeClip = None
        self.playhead = 0.0
        self.time_offset = 0.0
        self.timeline_start = 0.0
        self.timeline_end = 0.0
        self.work_area_start = 0.0
        self.work_area_end = 0.0
        self.frameReadoutLabel.updateText()
        self._syncNavigator()
        self.update()


########################################################################
# TimelineExportWorker: renders the arranged timeline to a video via ffmpeg
########################################################################
class TimelineExportWorker(QtCore.QThread):
    progress = Signal(int, str)   # percent, message
    finished = Signal(bool, str)  # success, message/output path

    def __init__(self, clips, output_path, fps=25.0, parent=None):
        super().__init__(parent)
        # clips: list of dicts {videoPath, src_start, duration, gap_before}
        self.clips = clips
        self.output_path = output_path
        self.fps = fps

    @staticmethod
    def _hasAudio(path) -> bool:
        import subprocess
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a",
                 "-show_entries", "stream=index", "-of", "csv=p=0", path],
                capture_output=True, text=True, timeout=15
            )
            return bool(result.stdout.strip())
        except Exception:
            return False

    @staticmethod
    def _videoSize(path):
        cap = cv2.VideoCapture(path)
        try:
            if cap.isOpened():
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                if w > 0 and h > 0:
                    # Even dimensions are required by libx264/yuv420p.
                    return w - (w % 2), h - (h % 2)
        finally:
            cap.release()
        return 1280, 720

    def run(self):
        import subprocess, tempfile, shutil
        if shutil.which("ffmpeg") is None:
            self.finished.emit(False, "ffmpeg was not found on PATH. Please install ffmpeg to export video.")
            return
        if not self.clips:
            self.finished.emit(False, "The timeline has no video clips to export.")
            return

        tmp_dir = tempfile.mkdtemp(prefix="comfystudio_export_")
        segments = []
        width, height = self._videoSize(self.clips[0]["videoPath"])
        vf = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
              f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1")
        common_video = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                        "-pix_fmt", "yuv420p", "-r", f"{self.fps}"]
        common_audio = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]

        try:
            total_steps = len(self.clips) + 1
            step = 0
            for i, clip in enumerate(self.clips):
                # Insert black filler for gaps between clips.
                gap = clip.get("gap_before", 0.0)
                if gap > 0.05:
                    seg = os.path.join(tmp_dir, f"gap_{i:03d}.mp4")
                    cmd = ["ffmpeg", "-y",
                           "-f", "lavfi", "-i", f"color=black:s={width}x{height}:r={self.fps}",
                           "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                           "-t", f"{gap:.3f}"] + common_video + common_audio + ["-shortest", seg]
                    result = subprocess.run(cmd, capture_output=True, text=True)
                    if result.returncode != 0:
                        raise RuntimeError(f"ffmpeg failed on gap segment:\n{result.stderr[-800:]}")
                    segments.append(seg)

                seg = os.path.join(tmp_dir, f"seg_{i:03d}.mp4")
                src = clip["videoPath"]
                src_start = max(0.0, clip["src_start"])
                duration = max(0.04, clip["duration"])
                if self._hasAudio(src):
                    cmd = (["ffmpeg", "-y", "-ss", f"{src_start:.3f}", "-i", src,
                            "-t", f"{duration:.3f}", "-vf", vf]
                           + common_video + common_audio + [seg])
                else:
                    # Add silent audio so every segment has matching streams for concat.
                    cmd = (["ffmpeg", "-y", "-ss", f"{src_start:.3f}", "-i", src,
                            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                            "-t", f"{duration:.3f}", "-vf", vf,
                            "-map", "0:v:0", "-map", "1:a:0"]
                           + common_video + common_audio + ["-shortest", seg])
                self.progress.emit(int(100 * step / total_steps),
                                   f"Encoding clip {i + 1}/{len(self.clips)}...")
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    raise RuntimeError(f"ffmpeg failed on clip {i + 1}:\n{result.stderr[-800:]}")
                segments.append(seg)
                step += 1

            self.progress.emit(int(100 * step / total_steps), "Joining segments...")
            list_path = os.path.join(tmp_dir, "concat.txt")
            with open(list_path, "w", encoding="utf-8") as f:
                for seg in segments:
                    f.write(f"file '{seg}'\n")
            cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
                   "-c", "copy", self.output_path]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg failed while joining segments:\n{result.stderr[-800:]}")
            self.progress.emit(100, "Done")
            self.finished.emit(True, self.output_path)
        except Exception as e:
            self.finished.emit(False, str(e))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


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
        self.shotListView = ShotListView(self.main_window)
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
        self.previewDock = QDockWidget("Timeline Preview", main_window)
        self.previewDock.setWidget(self.previewWidget)
        # NOTE: must differ from the main window's "preview_dock" objectName,
        # otherwise saveState/restoreState gets confused by the duplicate.
        self.previewDock.setObjectName("timeline_preview_dock")
        main_window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.previewDock)
        print("[DEBUG] Registered Preview dock.")

        # Create Toolbar with Select, Blade, and Add Track tools.
        self.toolbar = QtWidgets.QToolBar("Timeline Tools", main_window)
        self.toolbar.setObjectName('timeline_tools')
        main_window.addToolBar(self.toolbar)
        self.selectAction = QtGui.QAction("Select", self.toolbar)
        self.bladeAction = QtGui.QAction("Blade", self.toolbar)
        self.addVideoTrackAction = QtGui.QAction("Add Video Track", self.toolbar)
        self.addAudioTrackAction = QtGui.QAction("Add Audio Track", self.toolbar)
        self.exportAction = QtGui.QAction("Export Video", self.toolbar)
        self.toolbar.addAction(self.selectAction)
        self.toolbar.addAction(self.bladeAction)
        self.toolbar.addSeparator()
        self.toolbar.addAction(self.addVideoTrackAction)
        self.toolbar.addAction(self.addAudioTrackAction)
        self.toolbar.addSeparator()
        self.toolbar.addAction(self.exportAction)
        self.selectAction.triggered.connect(lambda: self.setToolMode("select"))
        self.bladeAction.triggered.connect(lambda: self.setToolMode("blade"))
        self.addVideoTrackAction.triggered.connect(self.timelineWidget.addVideoTrack)
        self.addAudioTrackAction.triggered.connect(self.timelineWidget.addAudioTrack)
        self.exportAction.triggered.connect(self.exportTimelineVideo)
        self._export_worker = None
        self._export_progress = None
        print("[DEBUG] Registered Timeline toolbar.")

    def setToolMode(self, mode: str):
        self.timelineWidget.toolMode = mode
        print(f"[DEBUG] Tool mode set to '{mode}'")

    # --- Project persistence (delegated from the main window) ---
    def serializeTimeline(self) -> dict:
        return self.timelineWidget.serializeTimeline()

    def restoreTimeline(self, data: dict):
        self.timelineWidget.restoreTimeline(data)

    def clearTimeline(self):
        self.timelineWidget.clearTimeline()

    def updateTexts(self):
        """Hook for retranslateUi; timeline labels are currently not localized."""
        pass

    # --- Final video export ---
    def exportTimelineVideo(self):
        tw = self.timelineWidget
        # Collect visible video-track clips in chronological order.
        clips = sorted(
            (c for c in tw.timeline_clips
             if c.track.lower().startswith("video")
             and tw.track_settings.get(c.track, {}).get("visible", True)),
            key=lambda c: c.start_time
        )
        export_clips = []
        prev_end = 0.0
        for clip in clips:
            src = clip.shot.videoPath
            if not src or not os.path.exists(src):
                QtWidgets.QMessageBox.warning(
                    self.main_window, "Export",
                    f"Clip '{clip.shot.name}' has no valid video file and will be skipped."
                )
                continue
            raw = clip.shot.raw_duration
            export_clips.append({
                "videoPath": src,
                "src_start": clip.shot.inPoint * raw,
                "duration": clip.length,
                "gap_before": max(0.0, clip.start_time - prev_end),
            })
            prev_end = max(prev_end, clip.start_time + clip.length)
        if not export_clips:
            QtWidgets.QMessageBox.information(
                self.main_window, "Export", "The timeline has no exportable video clips."
            )
            return

        output_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self.main_window, "Export Timeline Video", "",
            "Video Files (*.mp4);;All Files (*)"
        )
        if not output_path:
            return
        if not os.path.splitext(output_path)[1]:
            output_path += ".mp4"

        progress = QtWidgets.QProgressDialog("Exporting timeline...", "Cancel", 0, 100, self.main_window)
        progress.setWindowTitle("Export Timeline")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        worker = TimelineExportWorker(export_clips, output_path, fps=tw.playback_fps)
        self._export_worker = worker
        self._export_progress = progress

        def on_progress(pct, msg):
            progress.setValue(pct)
            progress.setLabelText(msg)

        def on_finished(success, message):
            progress.reset()
            progress.close()
            self._export_worker = None
            self._export_progress = None
            if success:
                QtWidgets.QMessageBox.information(
                    self.main_window, "Export", f"Timeline exported to:\n{message}"
                )
            else:
                QtWidgets.QMessageBox.critical(
                    self.main_window, "Export Failed", message
                )

        worker.progress.connect(on_progress)
        worker.finished.connect(on_finished)
        progress.canceled.connect(worker.terminate)
        worker.start()