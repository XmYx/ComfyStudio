#!/usr/bin/env python

from qtpy.QtCore import (
    Qt
)
from qtpy.QtGui import (
    QDrag
)
from qtpy.QtWidgets import (
    QListWidget
)

class ReorderableListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)
        self.drag_item = None

    def startDrag(self, supportedActions):
        item = self.currentItem()
        self.drag_item = item
        drag = QDrag(self)
        mimeData = self.mimeData([item])
        drag.setMimeData(mimeData)
        drag.setHotSpot(self.visualItemRect(item).topLeft())

        pixmap = item.icon().pixmap(self.iconSize())
        drag.setPixmap(pixmap)
        drag.exec_(Qt.MoveAction)

    def dragMoveEvent(self, event):
        event.setDropAction(Qt.MoveAction)
        event.accept()

    def dropEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, 'position') else event.pos()
        drop_item = self.itemAt(pos)

        if drop_item is None:
            drop_row = self.count()
        else:
            drop_row = self.row(drop_item)

        drag_row = self.row(self.drag_item)

        if drag_row != drop_row:
            # Reorder items
            item = self.takeItem(drag_row)
            self.insertItem(drop_row, item)
            self.setCurrentItem(item)
            # Update the parent's shots order
            if hasattr(self.parent(), 'syncShotsFromList'):
                self.parent().syncShotsFromList()
        self.drag_item = None
        event.accept()
