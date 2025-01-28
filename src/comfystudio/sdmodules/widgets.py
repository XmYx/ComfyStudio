#!/usr/bin/env python
from typing import List

from qtpy.QtCore import (
    Qt,
    QSize,
    QTimer,
    QRect
)
from qtpy.QtGui import (
    QDrag,
    QPixmap,
    QPainter,
    QPen,
    QColor
)
from qtpy.QtWidgets import (
    QWidget,
    QListWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSlider,
    QListWidgetItem,
    QLabel,
    QSizePolicy,
    QPushButton,  # <-- Added for view-mode toggle
    QListView
)

from comfystudio.sdmodules.dataclasses import Shot


class ReorderableListWidget(QWidget):
    """
    A Reorderable ListWidget that behaves similarly to a DaVinci Resolve style hover-scrub:
     - As you hover across each item's icon, you 'scrub' through the shot's duration
     - Draws a red timeline handle at the hover position (fraction of total clip)
     - In/Out points can be set by pressing 'I' or 'O' while hovering over an item
     - Zoom slider at top to control icon size
     - Items can be drag-reordered internally
     - Supports toggling between a "Grid" (IconMode) and "List" (ListMode) view
       and ensures the preview (icon) maintains aspect ratio and shot details are shown.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.drag_item = None

        # Instead of using QListWidgetItem as a dict key (unhashable),
        # we'll store item data keyed by id(item).
        self.hover_fraction_map = {}  # item_id -> fraction [0..1]
        self.in_point_map = {}        # item_id -> in fraction
        self.out_point_map = {}       # item_id -> out fraction

        self.current_hover_item = None

        # Main layout
        self.layout = QVBoxLayout(self)
        self.slider_layout = QHBoxLayout()

        # Zoom Label
        self.zoom_label = QLabel("Zoom:")
        self.zoom_label.setFixedWidth(50)
        self.slider_layout.addWidget(self.zoom_label)

        # Zoom Slider
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(50, 2000)  # up to 2000%
        self.zoom_slider.setValue(100)
        self.zoom_slider.setTickInterval(100)
        self.zoom_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.zoom_slider.valueChanged.connect(self.onZoomChanged)
        self.slider_layout.addWidget(self.zoom_slider)

        # View Mode Toggle Button
        self.view_switch_button = QPushButton("Switch to List View")
        self.view_switch_button.setFixedWidth(150)
        self.view_switch_button.clicked.connect(self.onViewModeSwitch)
        self.slider_layout.addWidget(self.view_switch_button)

        self.layout.addLayout(self.slider_layout)

        # List Widget
        self.listWidget = HoverScrubList(self)
        self.listWidget.setViewMode(QListView.IconMode)
        self.listWidget.setFlow(QListWidget.LeftToRight)
        self.listWidget.setWrapping(True)
        self.listWidget.setResizeMode(QListWidget.Adjust)
        self.listWidget.setMovement(QListWidget.Static)
        self.listWidget.setIconSize(QSize(120, 90))
        self.listWidget.setSpacing(10)
        self.listWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.listWidget.customContextMenuRequested.connect(self.onListWidgetContextMenu)
        self.listWidget.setDragEnabled(True)
        self.listWidget.setAcceptDrops(True)
        self.listWidget.setDropIndicatorShown(True)
        self.listWidget.setDragDropMode(QListWidget.InternalMove)
        self.listWidget.setSelectionMode(QListWidget.ExtendedSelection)
        self.listWidget.itemSelectionChanged.connect(self.onSelectionChanged)
        self.listWidget.itemEntered.connect(self.onItemEntered)

        # Make listWidget expand but not unbounded
        self.listWidget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.layout.addWidget(self.listWidget)

        # Timer to repaint on small intervals if needed
        self.repaint_timer = QTimer(self)
        self.repaint_timer.setInterval(50)  # ~20 fps
        self.repaint_timer.timeout.connect(self.onRepaintTimer)
        self.repaint_timer.start()

        self.setMouseTracking(True)
        self.listWidget.setMouseTracking(True)

    def onViewModeSwitch(self):
        """
        Toggle between a "Grid" (IconMode) and a "List" (ListMode) view.
        """
        if self.listWidget.viewMode() == QListView.IconMode:
            # Switch to List Mode
            self.listWidget.setViewMode(QListView.ListMode)
            self.listWidget.setFlow(QListView.TopToBottom)
            self.listWidget.setWrapping(False)
            self.view_switch_button.setText("Switch to Grid View")
        else:
            # Switch back to Grid (Icon) Mode
            self.listWidget.setViewMode(QListView.IconMode)
            self.listWidget.setFlow(QListView.LeftToRight)
            self.listWidget.setWrapping(True)
            self.view_switch_button.setText("Switch to List View")
        # Trigger a repaint to adjust layout
        self.listWidget.viewport().update()

    def onZoomChanged(self, value):
        """
        Adjust icon size and spacing based on the zoom slider.
        Also applies a grid size so that items occupy the correct overall space.
        """
        icon_size = QSize(int(120 * value / 100), int(90 * value / 100))
        self.listWidget.setIconSize(icon_size)
        spacing = max(int(10 * value / 100), 5)
        self.listWidget.setSpacing(spacing)
        # Also adjust grid size so the item area expands to match the icon
        if self.listWidget.viewMode() == QListView.IconMode:
            self.listWidget.setGridSize(QSize(icon_size.width() + spacing,
                                             icon_size.height() + spacing))
        else:
            # In ListMode, set grid size to accommodate image and text
            self.listWidget.setGridSize(QSize(self.listWidget.viewport().width(),
                                             icon_size.height() + spacing + 50))  # 50 for text

    def mouseMoveEvent(self, event):
        # Convert global position to listWidget coords
        pos_in_list = self.listWidget.mapFrom(self, event.pos())
        item = self.listWidget.itemAt(pos_in_list)
        if item:
            self.current_hover_item = item
            item_id = id(item)
            rect = self.listWidget.visualItemRect(item)
            if rect.width() > 0:
                # Fraction is computed across the entire item width
                fraction = (pos_in_list.x() - rect.x()) / float(rect.width())
                fraction = max(0.0, min(1.0, fraction))
                self.hover_fraction_map[item_id] = fraction
        else:
            self.current_hover_item = None
        super().mouseMoveEvent(event)

    def onItemEntered(self, item):
        self.current_hover_item = item
        if item:
            item_id = id(item)
            if item_id not in self.hover_fraction_map:
                self.hover_fraction_map[item_id] = 0.0

    def leaveEvent(self, event):
        self.current_hover_item = None
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        if self.current_hover_item:
            item_id = id(self.current_hover_item)
            if event.key() == Qt.Key.Key_I:
                # set In point
                fraction = self.hover_fraction_map.get(item_id, 0.0)
                self.in_point_map[item_id] = fraction
            elif event.key() == Qt.Key.Key_O:
                # set Out point
                fraction = self.hover_fraction_map.get(item_id, 0.0)
                self.out_point_map[item_id] = fraction
        super().keyPressEvent(event)

    def onRepaintTimer(self):
        # Force the listWidget to repaint if there's a hover
        if self.current_hover_item:
            self.listWidget.viewport().update()

    def onListWidgetContextMenu(self, pos):
        if hasattr(self.parent_window, "onListWidgetContextMenu"):
            self.parent_window.onListWidgetContextMenu(pos)

    def onSelectionChanged(self):
        if hasattr(self.parent_window, "onSelectionChanged"):
            self.parent_window.onSelectionChanged()

    def addItem(self, icon, label, shot):
        """
        Add a new Shot item to the list.
        """
        item = QListWidgetItem(icon, label)
        item.setData(Qt.ItemDataRole.UserRole, shot)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
        self.listWidget.addItem(item)

    def clearItems(self):
        self.listWidget.clear()

    def updateItems(self, shots: List[Shot]):
        self.clearItems()
        for i, shot in enumerate(shots):
            # The getShotIcon method is assumed to be in the parent_window
            icon = self.parent_window.getShotIcon(shot) if hasattr(self.parent_window, "getShotIcon") else QPixmap()
            label_text = f"Shot {i + 1}"
            self.addItem(icon, label_text, shot)

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

    def dragMoveEvent(self, event):
        event.setDropAction(Qt.MoveAction)
        event.accept()

    def dropEvent(self, event):
        pos = event.pos()
        drop_item = self.listWidget.itemAt(pos)
        if drop_item is None:
            drop_row = self.listWidget.count()
        else:
            drop_row = self.listWidget.row(drop_item)

        drag_row = self.listWidget.row(self.drag_item)

        if drag_row != drop_row:
            item = self.listWidget.takeItem(drag_row)
            self.listWidget.insertItem(drop_row, item)
            self.listWidget.setCurrentItem(item)
            if hasattr(self.parent_window, 'syncShotsFromList'):
                self.parent_window.syncShotsFromList()
        self.drag_item = None
        event.accept()


class HoverScrubList(QListWidget):
    """
    Subclass of QListWidget to handle painting a 'timeline handle' or
    'hover fraction' marker, in the style of a DaVinci Resolve hover-scrub.
    Additionally, it draws a prominent border around selected items and
    displays shot details on the right in ListMode.
    """
    def __init__(self, reorderable_parent):
        super().__init__()
        self.reorderable_parent = reorderable_parent
        self.setMouseTracking(True)
        self.setDefaultDropAction(Qt.MoveAction)
        # This enables the ability to get an "itemEntered" signal
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.InternalMove)
        self.setSelectionRectVisible(True)
        self.setProperty("showDropIndicator", True)

        self.setViewMode(QListWidget.IconMode)
        self.setMovement(QListWidget.Static)
        self.setEditTriggers(QListWidget.NoEditTriggers)
        self.setAlternatingRowColors(False)
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setResizeMode(QListWidget.Adjust)
        self.setWrapping(True)

        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAttribute(Qt.WidgetAttribute.WA_MouseTracking, True)

    def startDrag(self, supportedActions):
        if self.reorderable_parent:
            self.reorderable_parent.startDrag(supportedActions)

    def dragMoveEvent(self, event):
        if self.reorderable_parent:
            self.reorderable_parent.dragMoveEvent(event)

    def dropEvent(self, event):
        if self.reorderable_parent:
            self.reorderable_parent.dropEvent(event)

    def paintEvent(self, event):
        """
        Custom paint event to:
         - Draw each item's icon with maintained aspect ratio.
         - Draw shot details on the right in ListMode.
         - Draw a prominent border around selected items.
         - Draw hover-scrub handles and in/out points.
        """
        painter = QPainter(self.viewport())

        for i in range(self.count()):
            item = self.item(i)
            itemRect = self.visualItemRect(item)
            if not itemRect.isValid():
                continue

            # Determine if the item is selected
            is_selected = item.isSelected()  # Corrected line

            # Draw background if selected
            if is_selected:
                painter.fillRect(itemRect, QColor(200, 200, 255, 100))  # Light blue overlay

            # Draw the item icon scaled with aspect ratio
            pixmap = item.icon().pixmap(self.iconSize())
            if not pixmap.isNull():
                if self.viewMode() == QListWidget.IconMode:
                    scaled_pixmap = pixmap.scaled(
                        itemRect.size(),
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation
                    )
                    # Center the pixmap
                    x = itemRect.x() + (itemRect.width() - scaled_pixmap.width()) // 2
                    y = itemRect.y() + (itemRect.height() - scaled_pixmap.height()) // 2
                    painter.drawPixmap(x, y, scaled_pixmap)
                    shot_idx: Shot = item.data(Qt.ItemDataRole.UserRole)
                    if shot_idx:
                        shot = self.reorderable_parent.parent().parent().shots[shot_idx]
                        nameRect = QRect(
                            itemRect.x() + 2,
                            itemRect.bottom() - 20,  # last ~20 px for text
                            itemRect.width() - 4,
                            18
                        )
                        painter.setPen(Qt.black)
                        painter.drawText(
                            nameRect,
                            Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextSingleLine,
                            shot.name
                        )

                else:
                    # ListMode: Allocate left part for image and right for details
                    image_width = int(itemRect.height() * 16 / 9)  # Assuming 16:9 aspect ratio
                    image_rect = QRect(itemRect.x(), itemRect.y(),
                                       image_width, itemRect.height())
                    scaled_pixmap = pixmap.scaled(
                        image_rect.size(),
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation
                    )
                    # Center the pixmap vertically
                    img_x = image_rect.x() + (image_rect.width() - scaled_pixmap.width()) // 2
                    img_y = image_rect.y() + (image_rect.height() - scaled_pixmap.height()) // 2
                    painter.drawPixmap(img_x, img_y, scaled_pixmap)

                    # Draw shot details on the right
                    shot_idx: Shot = item.data(Qt.ItemDataRole.UserRole)
                    if shot_idx:
                        shot = self.reorderable_parent.parent().parent().shots[shot_idx]
                        details_x = image_rect.right() + 10
                        details_y = itemRect.y() + 10
                        details_width = itemRect.width() - image_rect.width() - 20
                        details_height = itemRect.height() - 20

                        # Prepare the text
                        details_text = f"Name: {shot.name}\n"
                        details_text += f"Video: {shot.videoPath}\n"
                        details_text += f"Still: {shot.stillPath}\n"

                        # Optionally add more details excluding 'params'
                        if shot.workflows:
                            details_text += f"Workflows: {len(shot.workflows)}\n"

                        # Draw the text
                        painter.setPen(Qt.black)
                        painter.drawText(QRect(details_x, details_y,
                                               details_width, details_height),
                                         Qt.TextFlag.TextWordWrap, details_text)

            # Draw a red hover-scrub handle if we have a fraction
            item_id = id(item)
            fraction = self.reorderable_parent.hover_fraction_map.get(item_id, None)
            if fraction is not None:
                if self.viewMode() == QListWidget.IconMode:
                    handle_x = int(itemRect.x() + itemRect.width() * fraction)
                    painter.setPen(QPen(QColor(255, 0, 0), 3))
                    painter.drawLine(handle_x, itemRect.y(),
                                     handle_x, itemRect.y() + itemRect.height())
                else:
                    # In ListMode, handle is relative to the image area
                    image_width = int(itemRect.height() * 16 / 9)
                    handle_x = int(itemRect.x() + image_width * fraction)
                    painter.setPen(QPen(QColor(255, 0, 0), 2))
                    painter.drawLine(handle_x, itemRect.y(),
                                     handle_x, itemRect.y() + itemRect.height())

            # Draw in/out markers if any
            in_frac = self.reorderable_parent.in_point_map.get(item_id, None)
            if in_frac is not None:
                if self.viewMode() == QListWidget.IconMode:
                    ix = int(itemRect.x() + itemRect.width() * in_frac)
                else:
                    image_width = int(itemRect.height() * 16 / 9)
                    ix = int(itemRect.x() + image_width * in_frac)
                painter.setBrush(QColor(0, 255, 0))  # Green
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(ix - 4, itemRect.y() + itemRect.height() // 2 - 4,
                                    8, 8)

            out_frac = self.reorderable_parent.out_point_map.get(item_id, None)
            if out_frac is not None:
                if self.viewMode() == QListWidget.IconMode:
                    ox = int(itemRect.x() + itemRect.width() * out_frac)
                else:
                    image_width = int(itemRect.height() * 16 / 9)
                    ox = int(itemRect.x() + image_width * out_frac)
                painter.setBrush(QColor(255, 165, 0))  # Orange
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(ox - 4, itemRect.y() + itemRect.height() // 2 - 4,
                                    8, 8)

            # Draw a prominent border if selected
            if is_selected:
                pen = QPen(QColor(0, 120, 215), 3)  # Bright blue border
                painter.setPen(pen)
                painter.drawRect(itemRect.adjusted(1, 1, -2, -2))  # Adjust to fit inside

        painter.end()

    def sizeHint(self):
        return super().sizeHint()