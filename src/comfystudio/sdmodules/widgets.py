#!/usr/bin/env python
import os
from qtpy.QtCore import (
    Qt,
    QMimeData,
    QPoint,
    QSize,
    QEvent,
    QUrl,
    QTimer
)
from qtpy.QtGui import (
    QDrag,
    QPixmap
)
from qtpy.QtWidgets import (
    QWidget,
    QListWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSlider,
    QListWidgetItem,
    QMessageBox,
    QLabel
)
from qtpy.QtMultimedia import QMediaPlayer, QAudioOutput
from qtpy.QtMultimediaWidgets import QVideoWidget


class ReorderableListWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.drag_item = None
        self.current_hover_item = None

        # Initialize Layouts
        self.layout = QVBoxLayout(self)
        self.slider_layout = QHBoxLayout()

        # Zoom Label
        self.zoom_label = QLabel("Zoom:")
        self.zoom_label.setFixedWidth(50)
        self.slider_layout.addWidget(self.zoom_label)

        # Zoom Slider
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(50, 2000)  # Broadened range to 2000%
        self.zoom_slider.setValue(100)
        self.zoom_slider.setTickInterval(100)
        self.zoom_slider.setTickPosition(QSlider.TicksBelow)
        self.zoom_slider.valueChanged.connect(self.onZoomChanged)
        self.slider_layout.addWidget(self.zoom_slider)

        self.layout.addLayout(self.slider_layout)

        # List Widget
        self.listWidget = QListWidget()
        self.listWidget.setViewMode(QListWidget.IconMode)
        self.listWidget.setFlow(QListWidget.LeftToRight)
        self.listWidget.setWrapping(True)
        self.listWidget.setResizeMode(QListWidget.Adjust)
        self.listWidget.setMovement(QListWidget.Static)
        self.listWidget.setIconSize(QSize(120, 90))
        self.listWidget.setSpacing(10)
        self.listWidget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.listWidget.customContextMenuRequested.connect(self.onListWidgetContextMenu)
        self.listWidget.setDragEnabled(True)
        self.listWidget.setAcceptDrops(True)
        self.listWidget.setDropIndicatorShown(True)
        self.listWidget.setDragDropMode(QListWidget.InternalMove)
        self.listWidget.setSelectionMode(QListWidget.ExtendedSelection)
        self.listWidget.itemSelectionChanged.connect(self.onSelectionChanged)

        self.layout.addWidget(self.listWidget)

        # Video player for hover
        self.video_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.video_player.setAudioOutput(self.audio_output)
        self.video_output = QVideoWidget(self)
        self.video_player.setVideoOutput(self.video_output)
        self.video_output.hide()

        # Timer for hover delay
        self.hover_timer = QTimer()
        self.hover_timer.setSingleShot(True)
        self.hover_timer.setInterval(200)  # 200ms delay
        self.hover_timer.timeout.connect(self.playCurrentHoverVideo)

        # To track mouse movement
        self.setMouseTracking(True)
        self.listWidget.setMouseTracking(True)

    def onZoomChanged(self, value):
        icon_size = QSize(int(120 * value / 100), int(90 * value / 100))
        self.listWidget.setIconSize(icon_size)
        spacing = max(int(10 * value / 100), 5)  # Prevent spacing from being too small
        self.listWidget.setSpacing(spacing)
        #print(f"Zoom changed to {value}%. Icon size set to {icon_size.width()}x{icon_size.height()}, spacing set to {spacing}.")

    def mouseMoveEvent(self, event):
        pos = event.pos()
        # Correctly map the position from ReorderableListWidget to listWidget's viewport
        relative_pos = self.listWidget.viewport().mapFromParent(pos)
        item = self.listWidget.itemAt(relative_pos)
        if item != self.current_hover_item:
            #print(f"Hover moved to item: {self.getItemLabel(item)}")
            self.hover_timer.stop()
            self.stopVideo()
            self.current_hover_item = item
            if item:
                shot_idx = item.data(Qt.UserRole)
                shot = self.parent_window.shots[shot_idx]
                video_path = shot.get("videoPath")
                if video_path and os.path.exists(video_path):
                    #print(f"Item '{self.getItemLabel(item)}' has a video. Starting hover timer.")
                    self.hover_timer.start()
                else:
                    pass
                    #print(f"Item '{self.getItemLabel(item)}' has no video.")

    def leaveEvent(self, event):
        #print("Cursor left the widget. Stopping video playback.")
        self.hover_timer.stop()
        self.stopVideo()
        self.current_hover_item = None
        super().leaveEvent(event)

    def playCurrentHoverVideo(self):
        if self.current_hover_item:
            shot_idx = self.current_hover_item.data(Qt.UserRole)
            shot = self.parent_window.shots[shot_idx]
            video_path = shot.get("videoPath")
            if video_path and os.path.exists(video_path):
                #print(f"Hover timer elapsed. Playing video: {video_path}")
                self.playVideo(video_path, self.current_hover_item)
            else:
                pass
                #print(f"No valid video to play for item: {self.getItemLabel(self.current_hover_item)}")

    def playVideo(self, video_path, item):
        try:
            self.video_player.setSource(QUrl.fromLocalFile(video_path))
            # Position the video widget over the item
            item_rect = self.listWidget.visualItemRect(item)
            if item_rect.isValid():
                # Map the item's rectangle top-left to the ReorderableListWidget's coordinate system
                global_pos = self.listWidget.viewport().mapToGlobal(item_rect.topLeft())
                widget_pos = self.mapFromGlobal(global_pos)
                # Set the geometry of the video widget relative to ReorderableListWidget
                self.video_output.setGeometry(widget_pos.x(), widget_pos.y(), item_rect.width(), item_rect.height())
                self.video_output.show()
                self.video_player.play()
                #print(f"Playing video: {video_path} on item: {self.getItemLabel(item)}")
            else:
                pass
                #print(f"Invalid item rectangle for item: {self.getItemLabel(item)}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to play video: {e}")
            #print(f"Error playing video '{video_path}' for item '{self.getItemLabel(item)}': {e}")

    def stopVideo(self):
        if self.video_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.video_player.stop()
            #print("Stopped video playback.")
        self.video_output.hide()

    def startDrag(self, supportedActions):
        item = self.listWidget.currentItem()
        if item is None:
            return
        self.drag_item = item
        drag = QDrag(self.listWidget)
        mimeData = self.listWidget.mimeData([item])
        drag.setMimeData(mimeData)
        drag.setHotSpot(self.listWidget.visualItemRect(item).topLeft())

        pixmap = item.icon().pixmap(self.listWidget.iconSize())
        drag.setPixmap(pixmap)
        drag.exec_(Qt.MoveAction)
        #print(f"Started dragging item: {self.getItemLabel(item)}")

    def dragMoveEvent(self, event):
        event.setDropAction(Qt.MoveAction)
        event.accept()

    def dropEvent(self, event):
        pos = event.pos()
        # Correctly map the position from ReorderableListWidget to listWidget's viewport
        relative_pos = self.listWidget.viewport().mapFromParent(pos)
        drop_item = self.listWidget.itemAt(relative_pos)

        if drop_item is None:
            drop_row = self.listWidget.count()
        else:
            drop_row = self.listWidget.row(drop_item)

        drag_row = self.listWidget.row(self.drag_item)

        if drag_row != drop_row:
            # Reorder items
            item = self.listWidget.takeItem(drag_row)
            self.listWidget.insertItem(drop_row, item)
            self.listWidget.setCurrentItem(item)
            #print(f"Moved item '{self.getItemLabel(item)}' from row {drag_row} to {drop_row}.")
            # Update the parent's shots order
            if hasattr(self.parent_window, 'syncShotsFromList'):
                self.parent_window.syncShotsFromList()
        self.drag_item = None
        event.accept()

    def onListWidgetContextMenu(self, pos):
        #print(f"Context menu requested at position: {pos}")
        self.parent_window.onListWidgetContextMenu(pos)

    def onSelectionChanged(self):
        #print("Selection changed in list widget.")
        self.parent_window.onSelectionChanged()

    def addItem(self, icon, label, shot):
        item = QListWidgetItem(icon, label)
        item.setData(Qt.UserRole, shot)  # Store the shot data
        item.setFlags(item.flags() | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        self.listWidget.addItem(item)
        #print(f"Added item: {label}")

    def clearItems(self):
        self.listWidget.clear()
        #print("Cleared all items from the list widget.")

    def updateItems(self, shots):
        self.clearItems()
        for i, shot in enumerate(shots):
            icon = self.parent_window.getShotIcon(shot)
            label_text = f"Shot {i + 1}"
            self.addItem(icon, label_text, shot)

    def getItemLabel(self, item):
        if item:
            return item.text()
        return "None"
