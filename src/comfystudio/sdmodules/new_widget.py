# #!/usr/bin/env python
# """
# This module defines an enhanced shot manager with a Shot Library, a multitrack Timeline,
# and a Preview dock. In addition to the basic functionality, the timeline now enforces
# that clips cannot be resized longer than the source clip, supports a toolbar with a
# Select and Blade tool (with multi‐selection and splitting), implements audio/video
# link/unlink so linked clips move/resize together, adjusts overlapping clips by trimming,
# allows adding extra video/audio tracks with autosnapping and toggleable snapping,
# and enables ripple delete on empty areas.
#
# Additional features:
#   - Selected clips are highlighted.
#   - Clips can be deleted via the Delete key.
#   - Selected clips can be copied (Ctrl+C) and pasted (Ctrl+V) at the playhead position.
#   - Resizing clamps to at least one frame (1/fps) and prevents inversion.
#   - New video tracks are inserted above previous ones while audio tracks are appended below,
#     similar to DaVinci Resolve.
#   - Right–clicking in the track label area opens a context menu:
#       • For video tracks: hide/show track.
#       • For audio tracks: mute, solo, and set level.
#   - The timeline now supports actual playback:
#       • A QTimer drives playback at a global default FPS of 25.
#       • A timeline range is computed (from 0 to the last clip’s end).
#       • A work–area subrange is auto–set (if not modified) to the timeline end,
#         and playback loops within that range.
#   - Linked video and audio clips resize and move simultaneously.
#   - Clicking on an empty area deselects all clips and starts rubberband selection.
#   - Overlapping clips on the same track cause the underlying clip to shorten.
#   - When dragging a clip to a video track, the drop preview for the video always appears on the topmost video track and the corresponding audio preview appears on the matching audio track.
#   - Toggleable clip snapping (toggled by pressing "S").
#   - The timeline navigator now has a wider zoom range and supports horizontal scrolling.
#   - In Blade tool mode the cursor changes (to CrossCursor) and a cut preview (a purple dashed line) is shown when hovering over a clip.
#
# Usage in your MainWindow:
#     from comfystudio.sdmodules.new_widget import ShotManagerWidget
#
#     class MainWindow(QMainWindow):
#         def __init__(self):
#             super().__init__()
#             self.resize(1400, 900)
#             # ... your initialization code ...
#             self.shots = []  # Must be maintained if using ShotListView dragging etc.
#             self.shotManager = ShotManagerWidget(self)
#             # Optionally, add sample shots:
#             sample_shot = Shot(
#                 name="AnimateDiff Preview",
#                 duration=5.0,  # seconds (full source duration)
#                 videoPath="/home/mix/Downloads/AnimateDiff_00007.mp4",
#                 stillPath="/home/mix/Downloads/AnimateDiff_00007.mp4",
#                 thumbnail_path="/home/mix/Downloads/AnimateDiff_00007.mp4",
#                 inPoint=0.0,
#                 outPoint=1.0,
#                 linkedAudio=True,
#             )
#             self.shots.append(sample_shot)
#             self.shotManager.shotListView.addShot(sample_shot)
#             # ... further initialization ...
# """
#
# import sys
# import json
# import cv2
# import copy
# from dataclasses import dataclass
# from typing import List
#
# from PyQt6 import QtCore, QtGui, QtWidgets
# from PyQt6.QtCore import Qt, QSize, QRect
# from PyQt6.QtGui import QPixmap, QPainter, QPen, QColor
# from PyQt6.QtWidgets import (
#     QWidget,
#     QListWidget,
#     QVBoxLayout,
#     QHBoxLayout,
#     QSlider,
#     QListWidgetItem,
#     QLabel,
#     QDockWidget,
#     QAbstractItemView,
#     QMenu,
# )
# from qtpy.QtCore import Signal
#
#
# ########################################################################
# # Minimal Shot class
# ########################################################################
# @dataclass
# class Shot:
#     name: str
#     duration: float  # full source duration in seconds
#     videoPath: str
#     stillPath: str
#     thumbnail_path: str
#     inPoint: float  # fraction (0.0-1.0) for trimmed start
#     outPoint: float  # fraction (0.0-1.0) for trimmed end
#     linkedAudio: bool  # whether audio is linked to video
#
#
# ########################################################################
# # Helper: extract a video frame using OpenCV
# ########################################################################
# def getVideoFrame(videoPath: str, fraction: float, size: QSize) -> QPixmap:
#     cap = cv2.VideoCapture(videoPath)
#     if not cap.isOpened():
#         print(f"[DEBUG] Failed to open video: {videoPath}")
#         placeholder = QPixmap(size)
#         placeholder.fill(QColor("black"))
#         return placeholder
#     frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
#     if frame_count <= 0:
#         print(f"[DEBUG] Invalid frame count in video: {videoPath}")
#         cap.release()
#         placeholder = QPixmap(size)
#         placeholder.fill(QColor("black"))
#         return placeholder
#     target_frame = int(max(0, min(frame_count - 1, fraction * frame_count)))
#     cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
#     ret, frame = cap.read()
#     cap.release()
#     if not ret or frame is None:
#         print(f"[DEBUG] Failed to read frame at index {target_frame} from video: {videoPath}")
#         placeholder = QPixmap(size)
#         placeholder.fill(QColor("black"))
#         return placeholder
#     frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
#     h, w, ch = frame.shape
#     bytesPerLine = ch * w
#     qImg = QtGui.QImage(frame.data, w, h, bytesPerLine, QtGui.QImage.Format.Format_RGB888)
#     pixmap = QPixmap.fromImage(qImg)
#     pixmap = pixmap.scaled(size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
#     return pixmap
#
#
# ########################################################################
# # Data Classes
# ########################################################################
# import itertools
#
# _uid_counter = itertools.count()
#
# @dataclass(eq=False)
# class TimelineClip:
#     shot: Shot  # light reference to the shot asset
#     track: str
#     start_time: float  # timeline start time (seconds)
#     length: float      # duration on the timeline (seconds)
#     inPoint: float     # independent in point (fraction)
#     outPoint: float    # independent out point (fraction)
#     uid: int = None    # unique identifier for each timeline clip
#
#     def __post_init__(self):
#         if self.uid is None:
#             self.uid = next(_uid_counter)
#
#     def __eq__(self, other):
#         if isinstance(other, TimelineClip):
#             return self.uid == other.uid
#         return False
#
# ########################################################################
# # FrameReadoutLabel
# ########################################################################
# class FrameReadoutLabel(QtWidgets.QLabel):
#     def __init__(self, timeline_widget, parent=None):
#         super().__init__(parent)
#         self.timeline_widget = timeline_widget
#         self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
#         self.setStyleSheet("background: transparent; color: white;")
#         self.setAlignment(Qt.AlignmentFlag.AlignCenter)
#         self.updateText()
#
#     def updateText(self):
#         self.setText(f"Time: {self.timeline_widget.playhead:.2f}s")
#
#     def mousePressEvent(self, event):
#         new_playhead = self.timeline_widget.view_offset + event.x() / self.timeline_widget.scale
#         self.timeline_widget.playhead = max(0.0, new_playhead)
#         self.timeline_widget.playheadChanged.emit(self.timeline_widget.playhead)
#         self.timeline_widget.update()
#         self.updateText()
#         event.accept()
#
#     def mouseMoveEvent(self, event):
#         new_playhead = self.timeline_widget.view_offset + event.x() / self.timeline_widget.scale
#         self.timeline_widget.playhead = max(0.0, new_playhead)
#         self.timeline_widget.playheadChanged.emit(self.timeline_widget.playhead)
#         self.timeline_widget.update()
#         self.updateText()
#         event.accept()
#
#
# ########################################################################
# # ShotListView
# ########################################################################
# class ShotListView(QListWidget):
#     def __init__(self, parent=None, main_window=None):
#         super().__init__(parent)
#         self.setViewMode(QListWidget.ViewMode.IconMode)
#         self.setIconSize(QSize(160, 120))
#         self.setSpacing(10)
#         self.setDragEnabled(True)
#         self.setAcceptDrops(False)
#         self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
#         self.setMouseTracking(True)
#         self.hoverFraction = {}
#         self.currentHoverItem = None
#         self.inMarkers = {}
#         self.outMarkers = {}
#         self.main_window = main_window
#         print("[DEBUG] ShotListView initialized")
#
#     def mimeData(self, items):
#         mimeData = QtCore.QMimeData()
#         shots = []
#         for item in items:
#             shot_idx = item.data(Qt.ItemDataRole.UserRole)
#             shot = QtWidgets.QApplication.activeWindow().shots[shot_idx]
#             shots.append({
#                 "name": shot.name,
#                 "duration": shot.duration,
#                 "videoPath": shot.videoPath,
#                 "stillPath": shot.stillPath,
#                 "thumbnail_path": shot.thumbnail_path,
#                 "inPoint": shot.inPoint,
#                 "outPoint": shot.outPoint,
#                 "linkedAudio": shot.linkedAudio,
#             })
#         mimeData.setData("application/x-shot", json.dumps(shots).encode("utf-8"))
#         return mimeData
#
#     def mouseMoveEvent(self, event):
#         pos = event.pos()
#         item = self.itemAt(pos)
#         if item:
#             rect = self.visualItemRect(item)
#             if rect.width() > 0:
#                 frac = (pos.x() - rect.x()) / rect.width()
#                 frac = max(0.0, min(1.0, frac))
#                 self.hoverFraction[id(item)] = frac
#                 self.currentHoverItem = item
#         else:
#             self.currentHoverItem = None
#         super().mouseMoveEvent(event)
#         self.viewport().update()
#
#     def leaveEvent(self, event):
#         self.currentHoverItem = None
#         super().leaveEvent(event)
#         self.viewport().update()
#
#     def keyPressEvent(self, event):
#         if self.currentHoverItem:
#             item_id = id(self.currentHoverItem)
#             if event.key() == Qt.Key.Key_I:
#                 self.inMarkers[item_id] = self.hoverFraction.get(item_id, 0.0)
#                 shot_idx = self.currentHoverItem.data(Qt.ItemDataRole.UserRole)
#                 shot = self.main_window.shots[shot_idx]
#                 shot.inPoint = self.inMarkers[item_id]
#                 print(f"[DEBUG] Set In marker for item {item_id} at {self.inMarkers[item_id]}")
#             elif event.key() == Qt.Key.Key_O:
#                 self.outMarkers[item_id] = self.hoverFraction.get(item_id, 1.0)
#                 shot_idx = self.currentHoverItem.data(Qt.ItemDataRole.UserRole)
#                 shot = self.main_window.shots[shot_idx]
#                 shot.outPoint = self.outMarkers[item_id]
#                 print(f"[DEBUG] Set Out marker for item {item_id} at {self.outMarkers[item_id]}")
#         super().keyPressEvent(event)
#         self.viewport().update()
#
#     def paintEvent(self, event):
#         super().paintEvent(event)
#         painter = QPainter(self.viewport())
#         for i in range(self.count()):
#             item = self.item(i)
#             rect = self.visualItemRect(item)
#             item_id = id(item)
#             if item == self.currentHoverItem and item_id in self.hoverFraction:
#                 frac = self.hoverFraction[item_id]
#                 shot_idx = item.data(Qt.ItemDataRole.UserRole)
#                 shot = self.main_window.shots[shot_idx]
#                 frame_pix = getVideoFrame(shot.videoPath, frac, self.iconSize())
#                 iconSize = self.iconSize()
#                 icon_x = rect.x() + (rect.width() - iconSize.width()) // 2
#                 icon_y = rect.y() + (rect.height() - iconSize.height()) // 2
#                 icon_rect = QRect(icon_x, icon_y, iconSize.width(), iconSize.height())
#                 painter.drawPixmap(icon_rect, frame_pix)
#             if item == self.currentHoverItem and item_id in self.hoverFraction:
#                 frac = self.hoverFraction[item_id]
#                 x = rect.x() + int(frac * rect.width())
#                 painter.setPen(QPen(QColor(255, 0, 0), 2))
#                 painter.drawLine(x, rect.y(), x, rect.bottom())
#             if item_id in self.inMarkers:
#                 frac = self.inMarkers[item_id]
#                 x = rect.x() + int(frac * rect.width())
#                 painter.setBrush(QColor(0, 255, 0))
#                 painter.setPen(Qt.PenStyle.NoPen)
#                 painter.drawEllipse(x - 3, rect.y() + 5, 6, 6)
#             if item_id in self.outMarkers:
#                 frac = self.outMarkers[item_id]
#                 x = rect.x() + int(frac * rect.width())
#                 painter.setBrush(QColor(255, 165, 0))
#                 painter.setPen(Qt.PenStyle.NoPen)
#                 painter.drawEllipse(x - 3, rect.y() + 5, 6, 6)
#         painter.end()
#
#
# ########################################################################
# # TimelineNavigator
# ########################################################################
# class TimelineNavigator(QWidget):
#     rangeChanged = Signal(float, float)  # Emits new work-area start and end times
#
#     def __init__(self, timeline_widget, parent=None):
#         super().__init__(parent)
#         self.timeline_widget = timeline_widget
#         self.setMinimumHeight(30)  # reduced height
#         self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
#         self.visible_range = QRect(5, 5, int(self.width() * 0.8), self.height() - 10)
#         self.dragging = False
#         self.resizing = False
#         self.drag_offset = 0
#
#     def paintEvent(self, event):
#         painter = QPainter(self)
#         painter.fillRect(self.rect(), QColor(220, 220, 220))
#         timeline_rect = QRect(5, 5, self.width() - 10, self.height() - 10)
#         painter.fillRect(timeline_rect, QColor(200, 200, 200))
#         painter.setBrush(QColor(100, 150, 250, 150))
#         painter.drawRect(self.visible_range)
#         painter.end()
#
#     def mousePressEvent(self, event):
#         if self.visible_range.contains(event.pos()):
#             if abs(event.pos().x() - self.visible_range.right()) < 5:
#                 self.resizing = True
#             else:
#                 self.dragging = True
#                 self.drag_offset = event.pos().x() - self.visible_range.x()
#         event.accept()
#
#     def mouseMoveEvent(self, event):
#         if self.dragging:
#             new_x = event.pos().x() - self.drag_offset
#             new_x = max(5, min(new_x, self.width() - 10 - self.visible_range.width()))
#             self.visible_range.moveLeft(new_x)
#             self.update()
#             self.emitRangeChanged()
#         elif self.resizing:
#             new_width = event.pos().x() - self.visible_range.x()
#             new_width = max(20, min(new_width, self.width() - 10 - self.visible_range.x()))
#             self.visible_range.setWidth(new_width)
#             self.update()
#             self.emitRangeChanged()
#         event.accept()
#
#     def mouseReleaseEvent(self, event):
#         self.dragging = False
#         self.resizing = False
#         event.accept()
#
#     def emitRangeChanged(self):
#         full_length = self.timeline_widget.timeline_end if self.timeline_widget.timeline_end > 0 else 100.0
#         timeline_rect = QRect(5, 5, self.width() - 10, self.height() - 10)
#         start_ratio = (self.visible_range.x() - timeline_rect.x()) / timeline_rect.width()
#         end_ratio = (self.visible_range.right() - timeline_rect.x()) / timeline_rect.width()
#         start_time = full_length * start_ratio
#         end_time = full_length * end_ratio
#         self.rangeChanged.emit(start_time, end_time)
#
#
# ########################################################################
# # MultiTrackTimelineWidget
# ########################################################################
# class MultiTrackTimelineWidget(QWidget):
#     playheadChanged = Signal(float)
#
#     def __init__(self, parent=None):
#         super().__init__(parent)
#         self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
#         self.scale = 100.0  # pixels per second
#         self.playhead = 0.0  # in seconds
#         self.timeline_clips: List[TimelineClip] = []
#         # Video and audio tracks
#         self.video_tracks = ["Video 1"]
#         self.audio_tracks = ["Audio 1"]
#         self.tracks = self.video_tracks + self.audio_tracks
#         self.left_panel_width = 80
#         self.ruler_height = 20
#         self.track_height = 60
#         self.total_tracks = len(self.tracks)
#         self.setMinimumHeight(self.ruler_height + self.track_height * self.total_tracks)
#         self.setAcceptDrops(True)
#         self.dragging_playhead = False
#         # Clip manipulation
#         self.activeClip = None
#         self.activeClipAction = None  # "move", "resize_left", "resize_right"
#         self.activeClipOffset = 0.0
#         self.activeClipOriginalStart = 0.0
#         self.activeClipOriginalEnd = 0.0
#         # Multi-selection
#         self.toolMode = "select"  # "select" or "blade"
#         self.selectedClips: List[TimelineClip] = []
#         self.activeClipOriginalPositions = {}
#         self.activeClipOffsets = {}
#         self.rubberBand = None
#         self.selectionOrigin = None
#         # Visual hover on handles
#         self.hoveredClip = None
#         self.hoveredHandle = None  # "resize_left" or "resize_right"
#         self.fps = 24
#         self.previewResolution = QSize(640, 480)
#         self.handle_width = 6
#         # Drop preview
#         self.dropPreviewClip = None
#         self.dropPreviewAudioClip = None
#         # Clipboard for copy/paste
#         self.clipboard = []
#         # Toggleable snapping
#         self.snapping = True
#         # Track settings
#         self.track_settings = {}
#         for t in self.video_tracks:
#             self.track_settings[t] = {"visible": True}
#         for t in self.audio_tracks:
#             self.track_settings[t] = {"mute": False, "solo": False, "level": 1.0}
#         # Playback functionality
#         self.playback_fps = 25.0
#         self.playback_timer = QtCore.QTimer(self)
#         self.playback_timer.timeout.connect(self.advancePlayhead)
#         self.playing = False
#         # Timeline/work area range
#         self.timeline_start = 0.0
#         self.timeline_end = 0.0
#         self.work_area_start = 0.0
#         self.work_area_end = 0.0
#         # Horizontal view offset
#         self.view_offset = 0.0
#         # Blade preview (for blade tool mode)
#         self.bladePreviewTime = None
#         # Frame readout label
#         self.frameReadoutLabel = FrameReadoutLabel(self, self)
#         self.frameReadoutLabel.setGeometry(self.left_panel_width, 0, self.width() - self.left_panel_width,
#                                            self.ruler_height)
#         self.frameReadoutLabel.show()
#         # Navigator (for zoom and horizontal navigation)
#         self.navigator = TimelineNavigator(self, self)
#         self.navigator.setGeometry(0, self.height() - 30, self.width(), 30)
#         self.navigator.rangeChanged.connect(self.onNavigatorRangeChanged)
#         print("[DEBUG] MultiTrackTimelineWidget initialized")
#
#     def onNavigatorRangeChanged(self, start, end):
#         self.work_area_start = start
#         self.work_area_end = end
#         full_length = self.timeline_end if self.timeline_end > 0 else 100.0
#         if (end - start) > 0:
#             self.scale = (self.width() - self.left_panel_width) / (end - start)
#         nav_margin = 5
#         nav_total_width = self.navigator.width() - 10
#         start_ratio = (self.navigator.visible_range.x() - nav_margin) / nav_total_width
#         self.view_offset = full_length * start_ratio
#         self.update()
#
#     def resizeEvent(self, event):
#         super().resizeEvent(event)
#         self.frameReadoutLabel.setGeometry(self.left_panel_width, 0, self.width() - self.left_panel_width,
#                                            self.ruler_height)
#         self.navigator.setGeometry(0, self.height() - 30, self.width(), 30)
#
#     def clipRect(self, clip: TimelineClip) -> QRect:
#         try:
#             track_index = self.tracks.index(clip.track)
#         except ValueError:
#             track_index = 0
#         x = self.left_panel_width + int((clip.start_time - self.view_offset) * self.scale)
#         y = self.ruler_height + track_index * self.track_height + 5
#         width = int(clip.length * self.scale)
#         height = self.track_height - 10
#         return QRect(x, y, width, height)
#
#     def snapValue(self, value, snap_list, threshold=10):
#         if not self.snapping:
#             return value
#         for snap_val in snap_list:
#             if abs(value - snap_val) < threshold:
#                 return snap_val
#         return value
#
#     def updateTracks(self):
#         self.tracks = self.video_tracks + self.audio_tracks
#         self.total_tracks = len(self.tracks)
#         self.setMinimumHeight(self.ruler_height + self.track_height * self.total_tracks)
#
#     def addVideoTrack(self):
#         new_track_name = f"Video {len(self.video_tracks) + 1}"
#         self.video_tracks.insert(0, new_track_name)
#         self.track_settings[new_track_name] = {"visible": True}
#         self.updateTracks()
#         self.update()
#         print(f"[DEBUG] Added video track: {new_track_name}")
#
#     def addAudioTrack(self):
#         new_track_name = f"Audio {len(self.audio_tracks) + 1}"
#         self.audio_tracks.append(new_track_name)
#         self.track_settings[new_track_name] = {"mute": False, "solo": False, "level": 1.0}
#         self.updateTracks()
#         self.update()
#         print(f"[DEBUG] Added audio track: {new_track_name}")
#
#     # def splitClip(self, clip: TimelineClip, split_time: float):
#     #     original_start = clip.start_time
#     #     original_length = clip.length
#     #     if split_time <= original_start or split_time >= original_start + original_length:
#     #         return
#     #     left_length = split_time - original_start
#     #     right_length = (original_start + original_length) - split_time
#     #     left_inPoint = clip.shot.inPoint
#     #     left_outPoint = left_inPoint + (left_length / clip.shot.duration)
#     #     right_inPoint = left_outPoint
#     #     right_outPoint = clip.shot.outPoint
#     #     left_clip = TimelineClip(shot=clip.shot, track=clip.track, start_time=original_start, length=left_length)
#     #     left_clip.shot.inPoint = left_inPoint
#     #     left_clip.shot.outPoint = left_outPoint
#     #     right_clip = TimelineClip(shot=clip.shot, track=clip.track, start_time=split_time, length=right_length)
#     #     right_clip.shot.inPoint = right_inPoint
#     #     right_clip.shot.outPoint = right_outPoint
#     #     if clip in self.timeline_clips:
#     #         self.timeline_clips.remove(clip)
#     #     self.timeline_clips.append(left_clip)
#     #     self.timeline_clips.append(right_clip)
#     #     print(f"[DEBUG] Split clip '{clip.shot.name}' at {split_time}s into two clips")
#     #     if clip.shot.linkedAudio:
#     #         for other in self.timeline_clips.copy():
#     #             if other.shot == clip.shot and other.track.lower() != clip.track.lower():
#     #                 self.splitClip(other, split_time)
#     #                 break
#
#     # def handleOverlap(self, new_clip: TimelineClip):
#     #     for clip in self.timeline_clips.copy():
#     #         if clip == new_clip:
#     #             continue
#     #         if clip.track.lower() == new_clip.track.lower():
#     #             existing_start = clip.start_time
#     #             existing_end = clip.start_time + clip.length
#     #             new_start = new_clip.start_time
#     #             new_end = new_clip.start_time + new_clip.length
#     #             if existing_start < new_end and existing_end > new_start:
#     #                 if existing_start < new_start < existing_end:
#     #                     new_length = new_start - existing_start
#     #                     clip.length = new_length
#     #                     clip.shot.outPoint = clip.shot.inPoint + (new_length / clip.shot.duration)
#     #                     print(f"[DEBUG] Overlap: Trimmed clip '{clip.shot.name}' to new length {new_length}s")
#     #                     if new_length < 0.1:
#     #                         self.timeline_clips.remove(clip)
#     #                 elif new_start <= existing_start < new_end:
#     #                     self.timeline_clips.remove(clip)
#     #                     print(f"[DEBUG] Overlap: Removed clip '{clip.shot.name}' due to overlap with new clip")
#     def splitClip(self, clip: TimelineClip, split_time: float):
#         original_start = clip.start_time
#         original_length = clip.length
#         if split_time <= original_start or split_time >= original_start + original_length:
#             return
#         left_length = split_time - original_start
#         right_length = (original_start + original_length) - split_time
#         left_inPoint = clip.inPoint  # use the clip’s own inPoint
#         left_outPoint = left_inPoint + (left_length / clip.shot.duration)
#         right_inPoint = left_outPoint
#         right_outPoint = clip.outPoint
#         left_clip = TimelineClip(
#             shot=clip.shot,
#             track=clip.track,
#             start_time=original_start,
#             length=left_length,
#             inPoint=left_inPoint,
#             outPoint=left_outPoint
#         )
#         right_clip = TimelineClip(
#             shot=clip.shot,
#             track=clip.track,
#             start_time=split_time,
#             length=right_length,
#             inPoint=right_inPoint,
#             outPoint=right_outPoint
#         )
#         if clip in self.timeline_clips:
#             self.timeline_clips.remove(clip)
#         self.timeline_clips.append(left_clip)
#         self.timeline_clips.append(right_clip)
#         print(f"[DEBUG] Split clip '{clip.shot.name}' at {split_time}s into two clips")
#         # No further splitting of linked clips.
#
#     def handleOverlap(self, new_clip: TimelineClip):
#         # Do not remove or trim any clips automatically.
#         # (Alternatively, you might want to notify the user or log an overlap.)
#         print(
#             f"[DEBUG] handleOverlap called for clip '{new_clip.shot.name}' on track '{new_clip.track}' – no auto‐trimming applied.")
#         # pass  # Simply do nothing.
#     def updateTimelineRange(self):
#         if self.timeline_clips:
#             self.timeline_end = max(clip.start_time + clip.length for clip in self.timeline_clips)
#         else:
#             self.timeline_end = 0.0
#         if self.work_area_end == 0.0:
#             self.work_area_end = self.timeline_end
#
#     def startPlayback(self):
#         if not self.playback_timer.isActive():
#             self.playback_timer.start(int(1000.0 / self.playback_fps))
#             self.playing = True
#
#     def stopPlayback(self):
#         if self.playback_timer.isActive():
#             self.playback_timer.stop()
#             self.playing = False
#
#     def togglePlayback(self):
#         if self.playback_timer.isActive():
#             self.stopPlayback()
#         else:
#             self.startPlayback()
#
#     def advancePlayhead(self):
#         increment = 1.0 / self.playback_fps
#         self.playhead += increment
#         if self.work_area_end > self.work_area_start and self.playhead >= self.work_area_end:
#             self.playhead = self.work_area_start
#         self.playheadChanged.emit(self.playhead)
#         self.update()
#
#     def keyPressEvent(self, event):
#         if event.key() == Qt.Key.Key_S:
#             self.snapping = not self.snapping
#             print(f"[DEBUG] Snapping {'enabled' if self.snapping else 'disabled'}")
#             return
#         if event.key() == Qt.Key.Key_Left:
#             step = 1.0 / self.fps
#             self.playhead = max(0.0, self.playhead - step)
#             print(f"[DEBUG] Frame step left: playhead = {self.playhead}s")
#             self.playheadChanged.emit(self.playhead)
#             self.update()
#         elif event.key() == Qt.Key.Key_Right:
#             step = 1.0 / self.fps
#             self.playhead += step
#             print(f"[DEBUG] Frame step right: playhead = {self.playhead}s")
#             self.playheadChanged.emit(self.playhead)
#             self.update()
#         elif event.key() == Qt.Key.Key_Space:
#             self.togglePlayback()
#             print(f"[DEBUG] {'Play' if self.playing else 'Pause'}")
#         elif event.key() == Qt.Key.Key_Delete:
#             if self.selectedClips:
#                 for clip in self.selectedClips:
#                     if clip in self.timeline_clips:
#                         self.timeline_clips.remove(clip)
#                 print("[DEBUG] Deleted selected clips")
#                 self.selectedClips = []
#                 self.update()
#         elif event.modifiers() == Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_C:
#             self.clipboard = [copy.deepcopy(clip) for clip in self.selectedClips]
#             print("[DEBUG] Copied selected clips")
#         elif event.modifiers() == Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_V:
#             if self.clipboard:
#                 min_start = min(clip.start_time for clip in self.clipboard)
#                 offset = self.playhead - min_start
#                 new_clips = []
#                 for clip in self.clipboard:
#                     new_clip = copy.deepcopy(clip)
#                     new_clip.start_time += offset
#                     new_clips.append(new_clip)
#                 self.timeline_clips.extend(new_clips)
#                 print("[DEBUG] Pasted clips at playhead")
#                 self.update()
#         elif event.key() == Qt.Key.Key_P:
#             main_win = QtWidgets.QApplication.activeWindow()
#             if main_win.isFullScreen():
#                 main_win.showNormal()
#                 print("[DEBUG] Exited presentation mode")
#             else:
#                 main_win.showFullScreen()
#                 print("[DEBUG] Entered presentation mode")
#         else:
#             super().keyPressEvent(event)
#
#     def mousePressEvent(self, event):
#         pos = event.pos()
#         # Check if click on a clip
#         clicked_clip = None
#         for clip in reversed(self.timeline_clips):
#             if self.clipRect(clip).contains(pos):
#                 clicked_clip = clip
#                 break
#         if not clicked_clip:
#             # Start rubberband selection on empty area
#             self.selectedClips = []
#             self.rubberBand = QtWidgets.QRubberBand(QtWidgets.QRubberBand.Shape.Rectangle, self)
#             self.selectionOrigin = pos
#             self.rubberBand.setGeometry(QRect(pos, QSize()))
#             self.rubberBand.show()
#             self.activeClip = None
#             return
#         # Blade tool: change cursor and preview cut line
#         if self.toolMode == "blade":
#             self.setCursor(QtGui.QCursor(Qt.CursorShape.CrossCursor))
#             # Blade preview will be set in mouseMoveEvent
#         # In select mode, do NOT clear previous selection if clicked clip is already selected.
#         if self.toolMode == "select":
#             if clicked_clip not in self.selectedClips:
#                 self.selectedClips.append(clicked_clip)
#             self.activeClip = clicked_clip
#             self.activeClipOriginalPositions = {id(c): (c.start_time, c.length) for c in self.selectedClips}
#             if len(self.selectedClips) > 1:
#                 self.activeClipAction = "move"
#                 self.activeClipOffsets = {id(c): pos.x() - self.clipRect(c).left() for c in self.selectedClips}
#             else:
#                 threshold = 10
#                 rect = self.clipRect(clicked_clip)
#                 if abs(pos.x() - rect.left()) <= threshold:
#                     self.activeClipAction = "resize_left"
#                 elif abs(pos.x() - rect.right()) <= threshold:
#                     self.activeClipAction = "resize_right"
#                 else:
#                     self.activeClipAction = "move"
#                     self.activeClipOffset = pos.x() - rect.left()
#                 self.activeClipOriginalStart = clicked_clip.start_time
#                 self.activeClipOriginalEnd = clicked_clip.start_time + clicked_clip.length
#         elif self.toolMode == "blade":
#             # Blade tool: don't change selection on click.
#             self.activeClip = clicked_clip
#         else:
#             super().mousePressEvent(event)
#         super().mousePressEvent(event)
#
#     def mouseMoveEvent(self, event):
#         pos = event.pos()
#         # Rubberband selection in progress
#         if self.rubberBand:
#             rect = QRect(self.selectionOrigin, event.pos()).normalized()
#             self.rubberBand.setGeometry(rect)
#             return
#
#         # Blade tool preview
#         if self.toolMode == "blade":
#             self.setCursor(QtGui.QCursor(Qt.CursorShape.CrossCursor))
#             for clip in self.timeline_clips:
#                 if self.clipRect(clip).contains(pos):
#                     self.bladePreviewTime = self.playhead
#                     break
#             else:
#                 self.bladePreviewTime = None
#
#         # Update hovered handles (for resize feedback)
#         if not self.activeClip and not self.dragging_playhead:
#             self.hoveredClip = None
#             self.hoveredHandle = None
#             handle_zone = 10
#             for clip in self.timeline_clips:
#                 rect = self.clipRect(clip)
#                 left_zone = QRect(rect.left(), rect.top(), handle_zone, rect.height())
#                 right_zone = QRect(rect.right() - handle_zone, rect.top(), handle_zone, rect.height())
#                 if left_zone.contains(pos):
#                     self.hoveredClip = clip
#                     self.hoveredHandle = "resize_left"
#                     break
#                 elif right_zone.contains(pos):
#                     self.hoveredClip = clip
#                     self.hoveredHandle = "resize_right"
#                     break
#
#         if self.activeClip:
#             if self.activeClipAction == "move":
#                 # Update only the active clip
#                 new_left = event.pos().x() - self.activeClipOffset
#                 new_left = self.snapValue(
#                     new_left,
#                     [self.left_panel_width + int(c.start_time * self.scale)
#                      for c in self.timeline_clips if c != self.activeClip]
#                 )
#                 new_start_time = self.view_offset + (new_left - self.left_panel_width) / self.scale
#                 self.activeClip.start_time = max(0.0, new_start_time)
#
#                 if self.activeClip.shot.linkedAudio:
#                     for other in self.timeline_clips:
#                         if other != self.activeClip and other.shot.name == self.activeClip.shot.name:
#                             other.start_time = self.activeClip.start_time
#                             other.length = self.activeClip.length
#
#                 print(f"[DEBUG] Moving clip to start_time: {new_start_time}s")
#             elif self.activeClipAction == "resize_left":
#                 new_left = event.pos().x()
#                 new_start_time = self.view_offset + (new_left - self.left_panel_width) / self.scale
#                 min_effective = 1.0 / self.fps
#                 if self.activeClipOriginalEnd - new_start_time < min_effective:
#                     new_start_time = self.activeClipOriginalEnd - min_effective
#                 self.activeClip.start_time = new_start_time
#                 self.activeClip.length = self.activeClipOriginalEnd - new_start_time
#                 new_inPoint = self.activeClip.shot.outPoint - (self.activeClip.length / self.activeClip.shot.duration)
#                 self.activeClip.shot.inPoint = max(0.0, new_inPoint)
#
#                 if self.activeClip.shot.linkedAudio:
#                     for other in self.timeline_clips:
#                         if other != self.activeClip and other.shot.name == self.activeClip.shot.name:
#                             other.start_time = self.activeClip.start_time
#                             other.length = self.activeClip.length
#                             other.shot.inPoint = self.activeClip.shot.inPoint
#                             other.shot.outPoint = self.activeClip.shot.outPoint
#                 print(
#                     f"[DEBUG] Resizing left clip '{self.activeClip.shot.name}': new start_time {new_start_time}s, new inPoint {self.activeClip.shot.inPoint}")
#             elif self.activeClipAction == "resize_right":
#                 new_right = event.pos().x()
#                 new_end_time = self.view_offset + (new_right - self.left_panel_width) / self.scale
#                 min_effective = 1.0 / self.fps
#                 if new_end_time - self.activeClipOriginalStart < min_effective:
#                     new_end_time = self.activeClipOriginalStart + min_effective
#                 self.activeClip.length = new_end_time - self.activeClipOriginalStart
#                 new_outPoint = self.activeClip.shot.inPoint + (self.activeClip.length / self.activeClip.shot.duration)
#                 self.activeClip.shot.outPoint = min(1.0, max(new_outPoint, self.activeClip.shot.inPoint + 0.01))
#                 if self.activeClip.shot.linkedAudio:
#                     for other in self.timeline_clips:
#                         if other != self.activeClip and other.shot.name == self.activeClip.shot.name:
#                             other.length = self.activeClip.length
#                             other.shot.outPoint = self.activeClip.shot.outPoint
#                 print(
#                     f"[DEBUG] Resizing right clip '{self.activeClip.shot.name}': new end_time {new_end_time}s, new outPoint {self.activeClip.shot.outPoint}")
#             self.update()
#         elif self.dragging_playhead:
#             new_playhead = self.view_offset + (event.pos().x() - self.left_panel_width) / self.scale
#             self.playhead = max(0.0, new_playhead)
#             print(f"[DEBUG] Playhead moved to: {self.playhead}s")
#             self.playheadChanged.emit(self.playhead)
#             self.update()
#
#         super().mouseMoveEvent(event)
#
#     def mouseReleaseEvent(self, event):
#         if self.rubberBand:
#             selectionRect = self.rubberBand.geometry()
#             self.rubberBand.hide()
#             self.rubberBand = None
#             for clip in self.timeline_clips:
#                 if self.clipRect(clip).intersects(selectionRect):
#                     if clip not in self.selectedClips:
#                         self.selectedClips.append(clip)
#             print(f"[DEBUG] Selected clips: {[c.shot.name for c in self.selectedClips]}")
#         if self.dragging_playhead:
#             self.dragging_playhead = False
#             print("[DEBUG] Stopped dragging playhead")
#         if self.activeClip:
#             print(f"[DEBUG] Finished manipulation on clip '{self.activeClip.shot.name}'")
#             self.activeClip = None
#             self.activeClipAction = None
#         # Reset blade cursor when leaving blade mode.
#         if self.toolMode == "blade":
#             self.unsetCursor()
#             self.bladePreviewTime = None
#         super().mouseReleaseEvent(event)
#
#     def paintClips(self, painter):
#         for clip in self.timeline_clips:
#             ts = self.track_settings.get(clip.track, {})
#             if clip.track.lower().startswith("video"):
#                 if not ts.get("visible", True):
#                     continue
#                 color = QColor(100, 100, 250)
#             else:
#                 if ts.get("mute", False):
#                     color = QColor(128, 128, 128)
#                 else:
#                     color = QColor(250, 150, 50)
#             clip_rect = self.clipRect(clip)
#             painter.fillRect(clip_rect, color)
#             if clip in self.selectedClips:
#                 painter.setPen(QPen(QColor(0, 255, 0), 3))
#             else:
#                 painter.setPen(QColor(255, 255, 255))
#             painter.drawRect(clip_rect)
#             painter.drawText(clip_rect.adjusted(2, 2, -2, -2),
#                              Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
#                              clip.shot.name)
#             left_handle = QRect(clip_rect.left(), clip_rect.top(), self.handle_width, clip_rect.height())
#             right_handle = QRect(clip_rect.right() - self.handle_width, clip_rect.top(), self.handle_width,
#                                  clip_rect.height())
#             if self.activeClip == clip and self.activeClipAction == "resize_left":
#                 painter.fillRect(left_handle, QColor(255, 255, 0))
#             elif self.hoveredClip == clip and self.hoveredHandle == "resize_left":
#                 painter.fillRect(left_handle, QColor(255, 200, 0))
#             else:
#                 painter.fillRect(left_handle, QColor(200, 200, 200))
#             if self.activeClip == clip and self.activeClipAction == "resize_right":
#                 painter.fillRect(right_handle, QColor(255, 255, 0))
#             elif self.hoveredClip == clip and self.hoveredHandle == "resize_right":
#                 painter.fillRect(right_handle, QColor(255, 200, 0))
#             else:
#                 painter.fillRect(right_handle, QColor(200, 200, 200))
#
#     def paintEvent(self, event):
#         self.updateTimelineRange()
#         painter = QPainter(self)
#         # Draw ruler (adjusted for view_offset)
#         ruler_rect = QRect(self.left_panel_width, 0, self.width() - self.left_panel_width, self.ruler_height)
#         painter.fillRect(ruler_rect, QColor(50, 50, 50))
#         seconds_visible = int((self.width() - self.left_panel_width) / self.scale) + 1
#         for s in range(seconds_visible):
#             time_sec = self.view_offset + s
#             x = self.left_panel_width + int(s * self.scale)
#             painter.setPen(QPen(QColor(200, 200, 200), 1))
#             painter.drawLine(x, 0, x, self.ruler_height)
#             painter.drawText(x + 2, self.ruler_height - 2, f"{time_sec:.0f}s")
#         # Draw left panel (track labels)
#         left_rect = QRect(0, self.ruler_height, self.left_panel_width, self.height() - self.ruler_height)
#         painter.fillRect(left_rect, QColor(80, 80, 80))
#         for i, track in enumerate(self.tracks):
#             y = self.ruler_height + i * self.track_height
#             painter.setPen(QColor(255, 255, 255))
#             ts = self.track_settings.get(track, {})
#             extra = ""
#             if track.lower().startswith("audio"):
#                 if ts.get("mute", False):
#                     extra += " [Muted]"
#                 if ts.get("solo", False):
#                     extra += " [Solo]"
#                 extra += f" (Lv: {ts.get('level', 1.0)})"
#             painter.drawText(5, int(y + self.track_height / 2), track + extra)
#         # Draw drop preview for video clip.
#         if self.dropPreviewClip:
#             target_track = self.dropPreviewClip.track
#
#             preview_rect = QRect(
#                 self.left_panel_width + int((self.dropPreviewClip.start_time - self.view_offset) * self.scale),
#                 self.ruler_height + self.tracks.index(target_track) * self.track_height + 5,
#                 int(self.dropPreviewClip.length * self.scale),
#                 self.track_height - 10
#             )
#             preview_pen = QPen(QColor(0, 255, 255), 2, Qt.PenStyle.DashLine)
#             painter.setPen(preview_pen)
#             painter.drawRect(preview_rect)
#             painter.setBrush(QColor(0, 255, 255, 50))
#             painter.fillRect(preview_rect, QColor(0, 255, 255, 50))
#             painter.setPen(QColor(0, 0, 0))
#             painter.drawText(preview_rect, Qt.AlignmentFlag.AlignCenter, target_track)
#         # Draw drop preview for audio clip.
#         if self.dropPreviewAudioClip:
#             preview_rect = QRect(
#                 self.left_panel_width + int((self.dropPreviewAudioClip.start_time - self.view_offset) * self.scale),
#                 self.ruler_height + self.tracks.index(self.dropPreviewAudioClip.track) * self.track_height + 5,
#                 int(self.dropPreviewAudioClip.length * self.scale),
#                 self.track_height - 10
#             )
#             preview_pen = QPen(QColor(0, 255, 255), 2, Qt.PenStyle.DashLine)
#             painter.setPen(preview_pen)
#             painter.drawRect(preview_rect)
#             painter.setBrush(QColor(0, 255, 255, 50))
#             painter.fillRect(preview_rect, QColor(0, 255, 255, 50))
#             painter.setPen(QColor(0, 0, 0))
#             painter.drawText(preview_rect, Qt.AlignmentFlag.AlignCenter, self.dropPreviewAudioClip.track)
#         # Draw blade preview if in blade mode.
#         if self.toolMode == "blade" and self.bladePreviewTime is not None:
#             blade_x = self.left_panel_width + int((self.bladePreviewTime - self.view_offset) * self.scale)
#             painter.setPen(QPen(QColor(128, 0, 128), 2, Qt.PenStyle.DashLine))
#             painter.drawLine(blade_x, self.ruler_height, blade_x, self.height())
#         # Draw clips.
#         self.paintClips(painter)
#         # Draw playhead.
#         playhead_x = self.left_panel_width + int((self.playhead - self.view_offset) * self.scale)
#         painter.setPen(QPen(QColor(255, 0, 0), 2))
#         painter.drawLine(playhead_x, self.ruler_height, playhead_x, self.height())
#         painter.end()
#
#     def dragEnterEvent(self, event):
#         if event.mimeData().hasFormat("application/x-shot"):
#             event.acceptProposedAction()
#             data = event.mimeData().data("application/x-shot")
#             try:
#                 shots_data = json.loads(bytes(data).decode("utf-8"))
#                 if shots_data:
#                     shot = Shot(**shots_data[0])
#                     clip_length = (shot.outPoint - shot.inPoint) * shot.duration
#                     drop_time = self.view_offset + max(0.0, (event.pos().x() - self.left_panel_width) / self.scale)
#                     drop_y = event.pos().y()
#                     track_index = (drop_y - self.ruler_height) // self.track_height
#                     if track_index < 0:
#                         track_index = 0
#                     if track_index >= len(self.tracks):
#                         track_index = len(self.tracks) - 1
#                     selected_track = self.tracks[track_index]
#                     self.dropPreviewClip = TimelineClip(shot=shot, track=selected_track, start_time=drop_time,
#                                                         length=clip_length)
#                     if shot.linkedAudio:
#                         audio_track = None
#                         if selected_track.lower().startswith("video"):
#                             parts = selected_track.split()
#                             if len(parts) == 2 and parts[1].isdigit():
#                                 audio_track_candidate = f"Audio {parts[1]}"
#                                 if audio_track_candidate in self.tracks:
#                                     audio_track = audio_track_candidate
#                         if not audio_track:
#                             for t in self.tracks:
#                                 if t.lower().startswith("audio"):
#                                     audio_track = t
#                                     break
#                             if not audio_track:
#                                 audio_track = "Audio"
#                         self.dropPreviewAudioClip = TimelineClip(shot=shot, track=audio_track, start_time=drop_time,
#                                                                  length=clip_length)
#             except Exception as e:
#                 print("[DEBUG] DragEnter preview error:", e)
#             self.update()
#         else:
#             event.ignore()
#
#     def dragMoveEvent(self, event):
#         if event.mimeData().hasFormat("application/x-shot"):
#             event.acceptProposedAction()
#             data = event.mimeData().data("application/x-shot")
#             try:
#                 shots_data = json.loads(bytes(data).decode("utf-8"))
#                 if shots_data:
#                     shot = Shot(**shots_data[0])
#                     clip_length = (shot.outPoint - shot.inPoint) * shot.duration
#                     drop_time = self.view_offset + max(0.0, (event.pos().x() - self.left_panel_width) / self.scale)
#                     drop_y = event.pos().y()
#                     track_index = (drop_y - self.ruler_height) // self.track_height
#                     if track_index < 0:
#                         track_index = 0
#                     if track_index >= len(self.tracks):
#                         track_index = len(self.tracks) - 1
#                     selected_track = self.tracks[track_index]
#                     self.dropPreviewClip = TimelineClip(shot=shot, track=selected_track, start_time=drop_time,
#                                                         length=clip_length)
#                     if shot.linkedAudio:
#                         audio_track = None
#                         if selected_track.lower().startswith("video"):
#                             parts = selected_track.split()
#                             if len(parts) == 2 and parts[1].isdigit():
#                                 audio_track_candidate = f"Audio {parts[1]}"
#                                 if audio_track_candidate in self.tracks:
#                                     audio_track = audio_track_candidate
#                         if not audio_track:
#                             for t in self.tracks:
#                                 if t.lower().startswith("audio"):
#                                     audio_track = t
#                                     break
#                             if not audio_track:
#                                 audio_track = "Audio"
#                         self.dropPreviewAudioClip = TimelineClip(shot=shot, track=audio_track, start_time=drop_time,
#                                                                  length=clip_length)
#             except Exception as e:
#                 print("[DEBUG] DragMove preview error:", e)
#             self.update()
#         else:
#             event.ignore()
#
#     def dragLeaveEvent(self, event):
#         self.dropPreviewClip = None
#         self.dropPreviewAudioClip = None
#         self.update()
#         super().dragLeaveEvent(event)
#
#     def dropEvent(self, event):
#         if event.mimeData().hasFormat("application/x-shot"):
#             data = event.mimeData().data("application/x-shot")
#             try:
#                 shots_data = json.loads(bytes(data).decode("utf-8"))
#                 pos = event.pos()
#                 drop_time = self.view_offset + max(0.0, (pos.x() - self.left_panel_width) / self.scale)
#                 drop_y = pos.y()
#                 track_index = (drop_y - self.ruler_height) // self.track_height
#                 if track_index < 0:
#                     track_index = 0
#                 if track_index >= len(self.tracks):
#                     track_index = len(self.tracks) - 1
#                 selected_track = self.tracks[track_index]
#                 print(
#                     f"[DEBUG] Drop event at pos {pos}, calculated drop_time: {drop_time}s on track '{selected_track}'")
#                 for shot_dict in shots_data:
#                     shot = Shot(**shot_dict)
#                     clip_length = (shot.outPoint - shot.inPoint) * shot.duration
#                     # Use deepcopy so that each TimelineClip gets its own distinct Shot instance.
#                     new_clip = TimelineClip(
#                         shot=copy.deepcopy(shot),
#                         track=selected_track,
#                         start_time=drop_time,
#                         length=clip_length,
#                         inPoint=shot.inPoint,
#                         outPoint=shot.outPoint
#                     )
#                     clips_on_track = [c for c in self.timeline_clips if c.track == selected_track]
#                     if clips_on_track:
#                         clips_on_track.sort(key=lambda c: c.start_time)
#                         last_clip = clips_on_track[-1]
#                         gap = new_clip.start_time - (last_clip.start_time + last_clip.length)
#                         if abs(gap) < 0.2:
#                             new_clip.start_time = last_clip.start_time + last_clip.length
#                             print(f"[DEBUG] Autosnapped new clip to {new_clip.start_time}s on track '{selected_track}'")
#                     self.handleOverlap(new_clip)
#                     self.timeline_clips.append(new_clip)
#                     print(f"[DEBUG] Added clip: {new_clip}")
#                     if shot.linkedAudio:
#                         audio_track = None
#                         if selected_track.lower().startswith("video"):
#                             parts = selected_track.split()
#                             if len(parts) == 2 and parts[1].isdigit():
#                                 audio_track_candidate = f"Audio {parts[1]}"
#                                 if audio_track_candidate in self.tracks:
#                                     audio_track = audio_track_candidate
#                         if not audio_track:
#                             for t in self.tracks:
#                                 if t.lower().startswith("audio"):
#                                     audio_track = t
#                                     break
#                             if not audio_track:
#                                 audio_track = "Audio"
#                         audio_clip = TimelineClip(
#                             shot=copy.deepcopy(shot),
#                             track=audio_track,
#                             start_time=new_clip.start_time,
#                             length=clip_length,
#                             inPoint=shot.inPoint,
#                             outPoint=shot.outPoint
#                         )
#                         self.handleOverlap(audio_clip)
#                         self.timeline_clips.append(audio_clip)
#                         print(f"[DEBUG] Added linked audio clip: {audio_clip}")
#                 self.dropPreviewClip = None
#                 self.dropPreviewAudioClip = None
#                 self.update()
#                 event.acceptProposedAction()
#             except Exception as e:
#                 print(f"[DEBUG] Drop error: {e}")
#                 event.ignore()
#         else:
#             event.ignore()
#
#
# ########################################################################
# # PreviewWidget
# ########################################################################
# class PreviewWidget(QWidget):
#     def __init__(self, timeline_widget: MultiTrackTimelineWidget, parent=None):
#         super().__init__(parent)
#         self.timeline_widget = timeline_widget
#         self.setMinimumHeight(200)
#         self.timeline_widget.playheadChanged.connect(self.update)
#         print("[DEBUG] PreviewWidget initialized")
#
#     def paintEvent(self, event):
#         painter = QPainter(self)
#         painter.fillRect(self.rect(), QColor(20, 20, 20))
#         current_clip = None
#         for clip in self.timeline_widget.timeline_clips:
#             if clip.track.lower().startswith("video"):
#                 clip_start = clip.start_time
#                 clip_end = clip.start_time + clip.length
#                 if clip_start <= self.timeline_widget.playhead <= clip_end:
#                     current_clip = clip
#                     break
#         if current_clip:
#             fraction_in_clip = (self.timeline_widget.playhead - current_clip.start_time) / current_clip.length
#             effective_fraction = current_clip.shot.inPoint + fraction_in_clip * (
#                         current_clip.shot.outPoint - current_clip.shot.inPoint)
#             print(
#                 f"[DEBUG] Preview: showing frame for clip '{current_clip.shot.name}', effective fraction: {effective_fraction}")
#             frame_pix = getVideoFrame(current_clip.shot.videoPath, effective_fraction, self.size())
#             painter.drawPixmap(self.rect(), frame_pix)
#         else:
#             painter.setPen(QColor(255, 255, 255))
#             painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No clip under playhead")
#         painter.end()
#
#
# ########################################################################
# # ShotManagerWidget
# ########################################################################
# class ShotManagerWidget:
#     def __init__(self, main_window: QtWidgets.QMainWindow):
#         self.main_window = main_window
#
#         # Create Shot Library dock.
#         self.shotListView = ShotListView(None, self.main_window)
#         self.shotListDock = QDockWidget("Shot Library", main_window)
#         self.shotListDock.setWidget(self.shotListView)
#         self.shotListDock.setObjectName("shot_library_dock")
#         main_window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.shotListDock)
#         print("[DEBUG] Registered Shot Library dock.")
#
#         # Create Timeline dock.
#         self.timelineWidget = MultiTrackTimelineWidget()
#         self.timelineDock = QDockWidget("Timeline", main_window)
#         self.timelineDock.setWidget(self.timelineWidget)
#         self.timelineDock.setObjectName("timeline_dock")
#         main_window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.timelineDock)
#         print("[DEBUG] Registered Timeline dock.")
#
#         # Create Preview dock.
#         self.previewWidget = PreviewWidget(self.timelineWidget)
#         self.previewDock = QDockWidget("Preview", main_window)
#         self.previewDock.setWidget(self.previewWidget)
#         self.previewDock.setObjectName("preview_dock")
#         main_window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.previewDock)
#         print("[DEBUG] Registered Preview dock.")
#
#         # Create Toolbar with Select, Blade, and Add Track tools.
#         self.toolbar = QtWidgets.QToolBar("Timeline Tools", main_window)
#         main_window.addToolBar(self.toolbar)
#         self.selectAction = QtGui.QAction("Select", self.toolbar)
#         self.bladeAction = QtGui.QAction("Blade", self.toolbar)
#         self.addVideoTrackAction = QtGui.QAction("Add Video Track", self.toolbar)
#         self.addAudioTrackAction = QtGui.QAction("Add Audio Track", self.toolbar)
#         self.toolbar.addAction(self.selectAction)
#         self.toolbar.addAction(self.bladeAction)
#         self.toolbar.addSeparator()
#         self.toolbar.addAction(self.addVideoTrackAction)
#         self.toolbar.addAction(self.addAudioTrackAction)
#         self.selectAction.triggered.connect(lambda: self.setToolMode("select"))
#         self.bladeAction.triggered.connect(lambda: self.setToolMode("blade"))
#         self.addVideoTrackAction.triggered.connect(self.timelineWidget.addVideoTrack)
#         self.addAudioTrackAction.triggered.connect(self.timelineWidget.addAudioTrack)
#         print("[DEBUG] Registered Timeline toolbar.")
#
#     def setToolMode(self, mode: str):
#         self.timelineWidget.toolMode = mode
#         print(f"[DEBUG] Tool mode set to '{mode}'")
# !/usr/bin/env python
"""
This module defines an enhanced shot manager with a Shot Library, a multitrack Timeline,
and a Preview dock. In addition to the basic functionality, the timeline now enforces
that clips cannot be resized longer than the source clip, supports a toolbar with a
Select and Blade tool (with multi‐selection and splitting), implements audio/video
link/unlink so linked clips move/resize together, adjusts overlapping clips by trimming,
allows adding extra video/audio tracks with autosnapping and toggleable snapping,
and enables ripple delete on empty areas.

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
  - Linked video and audio clips resize and move simultaneously.
  - Clicking on an empty area deselects all clips and starts rubberband selection.
  - Overlapping clips on the same track cause the underlying clip to shorten.
  - When dragging a clip to a video track, the drop preview for the video always appears on the topmost video track and the corresponding audio preview appears on the matching audio track.
  - Toggleable clip snapping (toggled by pressing "S").
  - The timeline navigator now has a wider zoom range and supports horizontal scrolling.
  - In Blade tool mode the cursor changes (to CrossCursor) and a cut preview (a purple dashed line) is shown when hovering over a clip.
  - Pressing the L key toggles linking for the active clip.

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

import sys, json, cv2, copy, itertools
from dataclasses import dataclass
from typing import List

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt, QSize, QRect
from PyQt6.QtGui import QPixmap, QPainter, QPen, QColor
from PyQt6.QtWidgets import QWidget, QListWidget, QVBoxLayout, QHBoxLayout, QSlider, QListWidgetItem, QLabel, \
    QDockWidget, QAbstractItemView, QMenu
from qtpy.QtCore import Signal


########################################################################
# Minimal Shot class
########################################################################
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
_uid_counter = itertools.count()


@dataclass(eq=False)
class TimelineClip:
    shot: Shot  # Light reference to the shot asset.
    track: str
    start_time: float  # Timeline start time (seconds)
    length: float  # Duration on the timeline (seconds)
    inPoint: float  # Independent in point (fraction)
    outPoint: float  # Independent out point (fraction)
    uid: int = None  # Unique identifier for each clip
    group_id: int = None  # Group id for linked clips (if any)

    def __post_init__(self):
        if self.uid is None:
            self.uid = next(_uid_counter)

    def __eq__(self, other):
        if isinstance(other, TimelineClip):
            return self.uid == other.uid
        return False


########################################################################
# FrameReadoutLabel
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
        new_playhead = self.timeline_widget.view_offset + event.x() / self.timeline_widget.scale
        self.timeline_widget.playhead = max(0.0, new_playhead)
        self.timeline_widget.playheadChanged.emit(self.timeline_widget.playhead)
        self.timeline_widget.update()
        self.updateText()
        event.accept()

    def mouseMoveEvent(self, event):
        new_playhead = self.timeline_widget.view_offset + event.x() / self.timeline_widget.scale
        self.timeline_widget.playhead = max(0.0, new_playhead)
        self.timeline_widget.playheadChanged.emit(self.timeline_widget.playhead)
        self.timeline_widget.update()
        self.updateText()
        event.accept()


########################################################################
# ShotListView
########################################################################
class ShotListView(QListWidget):
    def __init__(self, parent=None, main_window=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setIconSize(QSize(160, 120))
        self.setSpacing(10)
        self.setDragEnabled(True)
        self.setAcceptDrops(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setMouseTracking(True)
        self.hoverFraction = {}
        self.currentHoverItem = None
        self.inMarkers = {}
        self.outMarkers = {}
        self.main_window = main_window
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
                shot_idx = self.currentHoverItem.data(Qt.ItemDataRole.UserRole)
                shot = self.main_window.shots[shot_idx]
                shot.inPoint = self.inMarkers[item_id]
                print(f"[DEBUG] Set In marker for item {item_id} at {self.inMarkers[item_id]}")
            elif event.key() == Qt.Key.Key_O:
                self.outMarkers[item_id] = self.hoverFraction.get(item_id, 1.0)
                shot_idx = self.currentHoverItem.data(Qt.ItemDataRole.UserRole)
                shot = self.main_window.shots[shot_idx]
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
                shot = self.main_window.shots[shot_idx]
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


########################################################################
# TimelineNavigator
########################################################################
class TimelineNavigator(QWidget):
    rangeChanged = Signal(float, float)  # Emits new work-area start and end times

    def __init__(self, timeline_widget, parent=None):
        super().__init__(parent)
        self.timeline_widget = timeline_widget
        self.setMinimumHeight(30)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        self.visible_range = QRect(5, 5, int(self.width() * 0.8), self.height() - 10)
        self.dragging = False
        self.resizing = False
        self.drag_offset = 0

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(220, 220, 220))
        timeline_rect = QRect(5, 5, self.width() - 10, self.height() - 10)
        painter.fillRect(timeline_rect, QColor(200, 200, 200))
        painter.setBrush(QColor(100, 150, 250, 150))
        painter.drawRect(self.visible_range)
        painter.end()

    def mousePressEvent(self, event):
        if self.visible_range.contains(event.pos()):
            if abs(event.pos().x() - self.visible_range.right()) < 5:
                self.resizing = True
            else:
                self.dragging = True
                self.drag_offset = event.pos().x() - self.visible_range.x()
        event.accept()

    def mouseMoveEvent(self, event):
        if self.dragging:
            new_x = event.pos().x() - self.drag_offset
            new_x = max(5, min(new_x, self.width() - 10 - self.visible_range.width()))
            self.visible_range.moveLeft(new_x)
            self.update()
            self.emitRangeChanged()
        elif self.resizing:
            new_width = event.pos().x() - self.visible_range.x()
            new_width = max(20, min(new_width, self.width() - 10 - self.visible_range.x()))
            self.visible_range.setWidth(new_width)
            self.update()
            self.emitRangeChanged()
        event.accept()

    def mouseReleaseEvent(self, event):
        self.dragging = False
        self.resizing = False
        event.accept()

    def emitRangeChanged(self):
        full_length = self.timeline_widget.timeline_end if self.timeline_widget.timeline_end > 0 else 100.0
        timeline_rect = QRect(5, 5, self.width() - 10, self.height() - 10)
        start_ratio = (self.visible_range.x() - timeline_rect.x()) / timeline_rect.width()
        end_ratio = (self.visible_range.right() - timeline_rect.x()) / timeline_rect.width()
        start_time = full_length * start_ratio
        end_time = full_length * end_ratio
        self.rangeChanged.emit(start_time, end_time)


########################################################################
# MultiTrackTimelineWidget
########################################################################
class MultiTrackTimelineWidget(QWidget):
    playheadChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.scale = 100.0
        self.playhead = 0.0
        self.timeline_clips: List[TimelineClip] = []
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
        self.activeClip = None
        self.activeClipAction = None  # "move", "resize_left", "resize_right"
        self.activeClipOffset = 0.0
        self.activeClipOriginalStart = 0.0
        self.activeClipOriginalEnd = 0.0
        self.toolMode = "select"  # "select" or "blade"
        self.selectedClips: List[TimelineClip] = []
        self.activeClipOriginalPositions = {}
        self.activeClipOffsets = {}
        self.rubberBand = None
        self.selectionOrigin = None
        self.fps = 24
        self.previewResolution = QSize(640, 480)
        self.handle_width = 6
        self.hoveredClip = None
        self.dropPreviewClip = None
        self.dropPreviewAudioClip = None
        self.clipboard = []
        self.snapping = True
        self.track_settings = {}
        for t in self.video_tracks:
            self.track_settings[t] = {"visible": True}
        for t in self.audio_tracks:
            self.track_settings[t] = {"mute": False, "solo": False, "level": 1.0}
        self.playback_fps = 25.0
        self.playback_timer = QtCore.QTimer(self)
        self.playback_timer.timeout.connect(self.advancePlayhead)
        self.playing = False
        self.timeline_start = 0.0
        self.timeline_end = 0.0
        self.work_area_start = 0.0
        self.work_area_end = 0.0
        self.view_offset = 0.0
        self.bladePreviewTime = None
        self.frameReadoutLabel = FrameReadoutLabel(self, self)
        self.frameReadoutLabel.setGeometry(self.left_panel_width, 0, self.width() - self.left_panel_width,
                                           self.ruler_height)
        self.frameReadoutLabel.show()
        self.navigator = TimelineNavigator(self, self)
        self.navigator.setGeometry(0, self.height() - 30, self.width(), 30)
        self.navigator.rangeChanged.connect(self.onNavigatorRangeChanged)
        print("[DEBUG] MultiTrackTimelineWidget initialized")

    def onNavigatorRangeChanged(self, start, end):
        self.work_area_start = start
        self.work_area_end = end
        full_length = self.timeline_end if self.timeline_end > 0 else 100.0
        if (end - start) > 0:
            self.scale = (self.width() - self.left_panel_width) / (end - start)
        nav_margin = 5
        nav_total_width = self.navigator.width() - 10
        start_ratio = (self.navigator.visible_range.x() - nav_margin) / nav_total_width
        self.view_offset = full_length * start_ratio
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.frameReadoutLabel.setGeometry(self.left_panel_width, 0, self.width() - self.left_panel_width,
                                           self.ruler_height)
        self.navigator.setGeometry(0, self.height() - 30, self.width(), 30)

    def clipRect(self, clip: TimelineClip) -> QRect:
        try:
            track_index = self.tracks.index(clip.track)
        except ValueError:
            track_index = 0
        x = self.left_panel_width + int((clip.start_time - self.view_offset) * self.scale)
        y = self.ruler_height + track_index * self.track_height + 5
        width = int(clip.length * self.scale)
        height = self.track_height - 10
        return QRect(x, y, width, height)

    def snapValue(self, value, snap_list, threshold=15):
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
        left_inPoint = clip.inPoint
        left_outPoint = left_inPoint + (left_length / clip.shot.duration)
        right_inPoint = left_outPoint
        right_outPoint = clip.outPoint
        left_clip = TimelineClip(
            shot=clip.shot,
            track=clip.track,
            start_time=original_start,
            length=left_length,
            inPoint=left_inPoint,
            outPoint=left_outPoint,
            group_id=clip.group_id
        )
        right_clip = TimelineClip(
            shot=clip.shot,
            track=clip.track,
            start_time=split_time,
            length=right_length,
            inPoint=right_inPoint,
            outPoint=right_outPoint,
            group_id=clip.group_id
        )
        if clip in self.timeline_clips:
            self.timeline_clips.remove(clip)
        self.timeline_clips.append(left_clip)
        self.timeline_clips.append(right_clip)
        print(f"[DEBUG] Split clip '{clip.shot.name}' at {split_time}s into two clips")

    def handleOverlap(self, new_clip: TimelineClip):
        print(
            f"[DEBUG] handleOverlap called for clip '{new_clip.shot.name}' on track '{new_clip.track}' – no auto‐trimming applied.")

    def updateTimelineRange(self):
        if self.timeline_clips:
            self.timeline_end = max(clip.start_time + clip.length for clip in self.timeline_clips)
        else:
            self.timeline_end = 0.0
        if self.work_area_end == 0.0:
            self.work_area_end = self.timeline_end

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
        # Toggle linking with the L key.
        if event.key() == Qt.Key.Key_L:
            if self.activeClip is not None:
                if self.activeClip.group_id is None:
                    new_group_id = self.activeClip.uid
                    for clip in self.timeline_clips:
                        if clip.shot.name == self.activeClip.shot.name:
                            clip.group_id = new_group_id
                    print(f"[DEBUG] Linked all clips with shot '{self.activeClip.shot.name}'")
                else:
                    self.activeClip.group_id = None
                    print(f"[DEBUG] Unlinked clip '{self.activeClip.shot.name}'")
            self.update()
            return

        if event.key() == Qt.Key.Key_S:
            self.snapping = not self.snapping
            print(f"[DEBUG] Snapping {'enabled' if self.snapping else 'disabled'}")
            return
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
        clicked_clip = None
        for clip in reversed(self.timeline_clips):
            if self.clipRect(clip).contains(pos):
                clicked_clip = clip
                break
        if not clicked_clip:
            self.selectedClips = []
            self.rubberBand = QtWidgets.QRubberBand(QtWidgets.QRubberBand.Shape.Rectangle, self)
            self.selectionOrigin = pos
            self.rubberBand.setGeometry(QRect(pos, QSize()))
            self.rubberBand.show()
            self.activeClip = None
            return
        if self.toolMode == "blade":
            self.setCursor(QtGui.QCursor(Qt.CursorShape.CrossCursor))
        if self.toolMode == "select":
            if clicked_clip not in self.selectedClips:
                self.selectedClips.append(clicked_clip)
            self.activeClip = clicked_clip
            self.activeClipOriginalPositions = {id(c): (c.start_time, c.length) for c in self.selectedClips}
            if len(self.selectedClips) > 1:
                self.activeClipAction = "move"
                self.activeClipOffsets = {id(c): pos.x() - self.clipRect(c).left() for c in self.selectedClips}
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
        elif self.toolMode == "blade":
            self.activeClip = clicked_clip
        else:
            super().mousePressEvent(event)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.pos()
        if self.rubberBand:
            rect = QRect(self.selectionOrigin, event.pos()).normalized()
            self.rubberBand.setGeometry(rect)
            return
        if self.toolMode == "blade":
            self.setCursor(QtGui.QCursor(Qt.CursorShape.CrossCursor))
            for clip in self.timeline_clips:
                if self.clipRect(clip).contains(pos):
                    self.bladePreviewTime = self.playhead
                    break
            else:
                self.bladePreviewTime = None
        if not self.activeClip and not self.dragging_playhead:
            self.hoveredClip = None
            self.hoveredHandle = None
            # Increase hover area to 15 pixels.
            handle_zone = 15
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
                new_left = event.pos().x() - self.activeClipOffset
                snap_list = []
                for c in self.timeline_clips:
                    if c != self.activeClip:
                        snap_list.append(self.left_panel_width + int(c.start_time * self.scale))
                        snap_list.append(self.left_panel_width + int((c.start_time + c.length) * self.scale))
                new_left = self.snapValue(new_left, snap_list, threshold=15)
                new_start_time = self.view_offset + (new_left - self.left_panel_width) / self.scale
                self.activeClip.start_time = max(0.0, new_start_time)
                if self.activeClip.group_id is not None:
                    for other in self.timeline_clips:
                        if other != self.activeClip and other.group_id == self.activeClip.group_id:
                            other.start_time = self.activeClip.start_time
                            other.length = self.activeClip.length
                print(f"[DEBUG] Moving clip to start_time: {new_start_time}s")
            elif self.activeClipAction == "resize_left":
                new_left = event.pos().x()
                new_start_time = self.view_offset + (new_left - self.left_panel_width) / self.scale
                min_effective = 1.0 / self.fps
                if self.activeClipOriginalEnd - new_start_time < min_effective:
                    new_start_time = self.activeClipOriginalEnd - min_effective
                self.activeClip.start_time = new_start_time
                self.activeClip.length = self.activeClipOriginalEnd - new_start_time
                new_inPoint = self.activeClip.shot.outPoint - (self.activeClip.length / self.activeClip.shot.duration)
                self.activeClip.shot.inPoint = max(0.0, new_inPoint)
                if self.activeClip.group_id is not None:
                    for other in self.timeline_clips:
                        if other != self.activeClip and other.group_id == self.activeClip.group_id:
                            other.start_time = self.activeClip.start_time
                            other.length = self.activeClip.length
                            other.shot.inPoint = self.activeClip.shot.inPoint
                            other.shot.outPoint = self.activeClip.shot.outPoint
                print(
                    f"[DEBUG] Resizing left clip '{self.activeClip.shot.name}': new start_time {new_start_time}s, new inPoint {self.activeClip.shot.inPoint}")
            elif self.activeClipAction == "resize_right":
                new_right = event.pos().x()
                new_end_time = self.view_offset + (new_right - self.left_panel_width) / self.scale
                min_effective = 1.0 / self.fps
                if new_end_time - self.activeClipOriginalStart < min_effective:
                    new_end_time = self.activeClipOriginalStart + min_effective
                self.activeClip.length = new_end_time - self.activeClipOriginalStart
                new_outPoint = self.activeClip.shot.inPoint + (self.activeClip.length / self.activeClip.shot.duration)
                self.activeClip.shot.outPoint = min(1.0, max(new_outPoint, self.activeClip.shot.inPoint + 0.01))
                if self.activeClip.group_id is not None:
                    for other in self.timeline_clips:
                        if other != self.activeClip and other.group_id == self.activeClip.group_id:
                            other.length = self.activeClip.length
                            other.shot.outPoint = self.activeClip.shot.outPoint
                print(
                    f"[DEBUG] Resizing right clip '{self.activeClip.shot.name}': new end_time {new_end_time}s, new outPoint {self.activeClip.shot.outPoint}")
            self.update()
        elif self.dragging_playhead:
            new_playhead = self.view_offset + (event.pos().x() - self.left_panel_width) / self.scale
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
        if self.toolMode == "blade":
            self.unsetCursor()
            self.bladePreviewTime = None
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

    def paintEvent(self, event):
        self.updateTimelineRange()
        painter = QPainter(self)
        ruler_rect = QRect(self.left_panel_width, 0, self.width() - self.left_panel_width, self.ruler_height)
        painter.fillRect(ruler_rect, QColor(50, 50, 50))
        seconds_visible = int((self.width() - self.left_panel_width) / self.scale) + 1
        for s in range(seconds_visible):
            time_sec = self.view_offset + s
            x = self.left_panel_width + int(s * self.scale)
            painter.setPen(QPen(QColor(200, 200, 200), 1))
            painter.drawLine(x, 0, x, self.ruler_height)
            painter.drawText(x + 2, self.ruler_height - 2, f"{time_sec:.0f}s")
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
        if self.dropPreviewClip:
            target_track = self.dropPreviewClip.track
            preview_rect = QRect(
                self.left_panel_width + int((self.dropPreviewClip.start_time - self.view_offset) * self.scale),
                self.ruler_height + self.tracks.index(target_track) * self.track_height + 5,
                int(self.dropPreviewClip.length * self.scale),
                self.track_height - 10
            )
            preview_pen = QPen(QColor(0, 255, 255), 2, Qt.PenStyle.DashLine)
            painter.setPen(preview_pen)
            painter.drawRect(preview_rect)
            painter.setBrush(QColor(0, 255, 255, 50))
            painter.fillRect(preview_rect, QColor(0, 255, 255, 50))
            painter.setPen(QColor(0, 0, 0))
            painter.drawText(preview_rect, Qt.AlignmentFlag.AlignCenter, target_track)
        if self.dropPreviewAudioClip:
            preview_rect = QRect(
                self.left_panel_width + int((self.dropPreviewAudioClip.start_time - self.view_offset) * self.scale),
                self.ruler_height + self.tracks.index(self.dropPreviewAudioClip.track) * self.track_height + 5,
                int(self.dropPreviewAudioClip.length * self.scale),
                self.track_height - 10
            )
            preview_pen = QPen(QColor(0, 255, 255), 2, Qt.PenStyle.DashLine)
            painter.setPen(preview_pen)
            painter.drawRect(preview_rect)
            painter.setBrush(QColor(0, 255, 255, 50))
            painter.fillRect(preview_rect, QColor(0, 255, 255, 50))
            painter.setPen(QColor(0, 0, 0))
            painter.drawText(preview_rect, Qt.AlignmentFlag.AlignCenter, self.dropPreviewAudioClip.track)
        if self.toolMode == "blade" and self.bladePreviewTime is not None:
            blade_x = self.left_panel_width + int((self.bladePreviewTime - self.view_offset) * self.scale)
            painter.setPen(QPen(QColor(128, 0, 128), 2, Qt.PenStyle.DashLine))
            painter.drawLine(blade_x, self.ruler_height, blade_x, self.height())
        self.paintClips(painter)
        playhead_x = self.left_panel_width + int((self.playhead - self.view_offset) * self.scale)
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
                    drop_time = self.view_offset + max(0.0, (event.pos().x() - self.left_panel_width) / self.scale)
                    drop_y = event.pos().y()
                    track_index = (drop_y - self.ruler_height) // self.track_height
                    if track_index < 0:
                        track_index = 0
                    if track_index >= len(self.tracks):
                        track_index = len(self.tracks) - 1
                    selected_track = self.tracks[track_index]
                    self.dropPreviewClip = TimelineClip(shot=shot, track=selected_track, start_time=drop_time,
                                                        length=clip_length, inPoint=shot.inPoint, outPoint=shot.outPoint)
                    if shot.linkedAudio:
                        audio_track = None
                        if selected_track.lower().startswith("video"):
                            parts = selected_track.split()
                            if len(parts) == 2 and parts[1].isdigit():
                                audio_track_candidate = f"Audio {parts[1]}"
                                if audio_track_candidate in self.tracks:
                                    audio_track = audio_track_candidate
                        if not audio_track:
                            for t in self.tracks:
                                if t.lower().startswith("audio"):
                                    audio_track = t
                                    break
                            if not audio_track:
                                audio_track = "Audio"
                        self.dropPreviewAudioClip = TimelineClip(shot=shot, track=audio_track, start_time=drop_time,
                                                                 length=clip_length, inPoint=shot.inPoint, outPoint=shot.outPoint)
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
                    drop_time = self.view_offset + max(0.0, (event.pos().x() - self.left_panel_width) / self.scale)
                    drop_y = event.pos().y()
                    track_index = (drop_y - self.ruler_height) // self.track_height
                    if track_index < 0:
                        track_index = 0
                    if track_index >= len(self.tracks):
                        track_index = len(self.tracks) - 1
                    selected_track = self.tracks[track_index]
                    self.dropPreviewClip = TimelineClip(shot=shot, track=selected_track, start_time=drop_time,
                                                        length=clip_length)
                    if shot.linkedAudio:
                        audio_track = None
                        if selected_track.lower().startswith("video"):
                            parts = selected_track.split()
                            if len(parts) == 2 and parts[1].isdigit():
                                audio_track_candidate = f"Audio {parts[1]}"
                                if audio_track_candidate in self.tracks:
                                    audio_track = audio_track_candidate
                        if not audio_track:
                            for t in self.tracks:
                                if t.lower().startswith("audio"):
                                    audio_track = t
                                    break
                            if not audio_track:
                                audio_track = "Audio"
                        self.dropPreviewAudioClip = TimelineClip(shot=shot, track=audio_track, start_time=drop_time,
                                                                 length=clip_length)
            except Exception as e:
                print("[DEBUG] DragMove preview error:", e)
            self.update()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.dropPreviewClip = None
        self.dropPreviewAudioClip = None
        self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasFormat("application/x-shot"):
            data = event.mimeData().data("application/x-shot")
            try:
                shots_data = json.loads(bytes(data).decode("utf-8"))
                pos = event.pos()
                drop_time = self.view_offset + max(0.0, (pos.x() - self.left_panel_width) / self.scale)
                drop_y = pos.y()
                track_index = (drop_y - self.ruler_height) // self.track_height
                if track_index < 0:
                    track_index = 0
                if track_index >= len(self.tracks):
                    track_index = len(self.tracks) - 1
                selected_track = self.tracks[track_index]
                print(
                    f"[DEBUG] Drop event at pos {pos}, calculated drop_time: {drop_time}s on track '{selected_track}'")
                for shot_dict in shots_data:
                    shot = Shot(**shot_dict)
                    clip_length = (shot.outPoint - shot.inPoint) * shot.duration
                    # Create video clip first.
                    video_clip = TimelineClip(
                        shot=copy.deepcopy(shot),
                        track=selected_track,
                        start_time=drop_time,
                        length=clip_length,
                        inPoint=shot.inPoint,
                        outPoint=shot.outPoint
                    )
                    # Snap video clip to adjacent clips.
                    clips_on_track = [c for c in self.timeline_clips if c.track == selected_track]
                    if clips_on_track:
                        clips_on_track.sort(key=lambda c: c.start_time)
                        last_clip = clips_on_track[-1]
                        gap = video_clip.start_time - (last_clip.start_time + last_clip.length)
                        if abs(gap) < 0.2:
                            video_clip.start_time = last_clip.start_time + last_clip.length
                            print(
                                f"[DEBUG] Autosnapped new clip to {video_clip.start_time}s on track '{selected_track}'")
                    self.handleOverlap(video_clip)
                    self.timeline_clips.append(video_clip)
                    print(f"[DEBUG] Added clip: {video_clip}")
                    if shot.linkedAudio:
                        # For linked clips, assign the video clip's uid as group_id.
                        audio_track = None
                        if selected_track.lower().startswith("video"):
                            parts = selected_track.split()
                            if len(parts) == 2 and parts[1].isdigit():
                                audio_track_candidate = f"Audio {parts[1]}"
                                if audio_track_candidate in self.tracks:
                                    audio_track = audio_track_candidate
                        if not audio_track:
                            for t in self.tracks:
                                if t.lower().startswith("audio"):
                                    audio_track = t
                                    break
                            if not audio_track:
                                audio_track = "Audio"
                        audio_clip = TimelineClip(
                            shot=copy.deepcopy(shot),
                            track=audio_track,
                            start_time=video_clip.start_time,
                            length=clip_length,
                            inPoint=shot.inPoint,
                            outPoint=shot.outPoint,
                            group_id=video_clip.uid
                        )
                        self.handleOverlap(audio_clip)
                        self.timeline_clips.append(audio_clip)
                        print(f"[DEBUG] Added linked audio clip: {audio_clip}")
                self.dropPreviewClip = None
                self.dropPreviewAudioClip = None
                self.update()
                event.acceptProposedAction()
            except Exception as e:
                print(f"[DEBUG] Drop error: {e}")
                event.ignore()
        else:
            event.ignore()


########################################################################
# PreviewWidget
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
            effective_fraction = current_clip.shot.inPoint + fraction_in_clip * (
                        current_clip.shot.outPoint - current_clip.shot.inPoint)
            print(
                f"[DEBUG] Preview: showing frame for clip '{current_clip.shot.name}', effective fraction: {effective_fraction}")
            frame_pix = getVideoFrame(current_clip.shot.videoPath, effective_fraction, self.size())
            painter.drawPixmap(self.rect(), frame_pix)
        else:
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No clip under playhead")
        painter.end()


########################################################################
# ShotManagerWidget
########################################################################
class ShotManagerWidget:
    def __init__(self, main_window: QtWidgets.QMainWindow):
        self.main_window = main_window
        self.shotListView = ShotListView(None, self.main_window)
        self.shotListDock = QDockWidget("Shot Library", main_window)
        self.shotListDock.setWidget(self.shotListView)
        self.shotListDock.setObjectName("shot_library_dock")
        main_window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.shotListDock)
        print("[DEBUG] Registered Shot Library dock.")
        self.timelineWidget = MultiTrackTimelineWidget()
        self.timelineDock = QDockWidget("Timeline", main_window)
        self.timelineDock.setWidget(self.timelineWidget)
        self.timelineDock.setObjectName("timeline_dock")
        main_window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.timelineDock)
        print("[DEBUG] Registered Timeline dock.")
        self.previewWidget = PreviewWidget(self.timelineWidget)
        self.previewDock = QDockWidget("Preview", main_window)
        self.previewDock.setWidget(self.previewWidget)
        self.previewDock.setObjectName("preview_dock")
        main_window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.previewDock)
        print("[DEBUG] Registered Preview dock.")
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


# if __name__ == "__main__":
#     app = QtWidgets.QApplication(sys.argv)
#     window = QtWidgets.QMainWindow()
#     window.setWindowTitle("Robust Timeline Example")
#     window.resize(1400, 900)
#     window.shots = []
#     timeline_manager = ShotManagerWidget(window)
#     window.show()
#     sys.exit(app.exec())
