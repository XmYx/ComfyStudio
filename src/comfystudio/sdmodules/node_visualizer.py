import math
import random
from qtpy.QtWidgets import (
    QDialog, QGraphicsView, QGraphicsScene, QGraphicsTextItem, QGraphicsRectItem,
    QVBoxLayout, QGraphicsItem, QGraphicsPathItem
)
from qtpy.QtGui import QPen, QBrush, QColor, QPainterPath, QFont
from qtpy.QtCore import Qt, QPointF, QRectF

class WorkflowVisualizer(QDialog):
    """
    A node-based workflow visualizer that:
    - Automatically arranges nodes by BFS layering
    - Uses QGraphicsRectItem for nodes, with 'title' from _meta.title
      and 'class_type' displayed
    - Shows cubic bezier edges between output and input
    - Allows nodes to be moved by the user (ItemIsMovable)
    """
    def __init__(self, workflow_json, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Workflow Visualizer")
        self.resize(800, 600)
        self.workflow_json = workflow_json

        # Main scene / view
        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene)
        # self.view.setRenderHint(self.view.renderHints() | RenderHints.Antialiasing)

        layout = QVBoxLayout(self)
        layout.addWidget(self.view)

        # Build the graph
        self.buildGraph()

    def buildGraph(self):
        """
        Lay out nodes in BFS layers and draw edges as cubic beziers.
        Each node is QGraphicsRectItem with text for _meta.title and class_type.
        """
        # 1) Build adjacency: for each node, figure out who depends on it
        dependents = {}  # node_id -> list of node_ids that depend on it
        for nid, ndata in self.workflow_json.items():
            for _, value in ndata.get("inputs", {}).items():
                if isinstance(value, list) and len(value) == 2:
                    src_id = str(value[0])
                    if src_id in self.workflow_json:
                        dependents.setdefault(src_id, []).append(nid)

        # 2) BFS layering: identify "roots" (no one provides them) and traverse
        all_nodes = set(self.workflow_json.keys())
        used_by_others = set()
        for lst in dependents.values():
            used_by_others.update(lst)
        # Roots: those not used as a target in dependents
        root_nodes = list(all_nodes - used_by_others)
        visited = set()
        layers = {}  # layer_index -> [node_ids]

        def bfs(start, layer_idx):
            queue = [(start, layer_idx)]
            while queue:
                current, lvl = queue.pop(0)
                if current in visited:
                    continue
                visited.add(current)
                layers.setdefault(lvl, []).append(current)
                next_list = dependents.get(current, [])
                for n2 in next_list:
                    if n2 not in visited:
                        queue.append((n2, lvl + 1))

        if not root_nodes:
            # If there's no "root," just pick an arbitrary node to start BFS
            if all_nodes:
                root_nodes = [list(all_nodes)[0]]

        for rn in root_nodes:
            bfs(rn, 0)

        # Check for unvisited nodes (disconnected subgraphs)
        unvisited = all_nodes - visited
        while unvisited:
            # start another BFS for each disconnected component
            any_node = unvisited.pop()
            bfs(any_node, 0)

        # 3) Positioning: each layer gets a column, each node in that layer is spaced vertically
        max_layer = max(layers.keys()) if layers else 0
        node_positions = {}
        node_items = {}

        # For consistent vertical spacing, measure the largest number of nodes in any layer
        largest_layer_size = max(len(layers[l]) for l in layers.keys()) if layers else 1
        y_spacing = 150
        x_spacing = 250

        for layer_idx in range(max_layer + 1):
            if layer_idx not in layers:
                continue
            nodes_in_layer = layers[layer_idx]
            for idx, node_id in enumerate(nodes_in_layer):
                x = layer_idx * x_spacing + 50
                # center them around the scene's vertical midpoint:
                # we assume total needed space is largest_layer_size * y_spacing
                total_height = (largest_layer_size - 1) * y_spacing
                base_y = - total_height / 2
                y = base_y + idx * y_spacing + 300  # 300 offset to keep them visible
                node_positions[node_id] = (x, y)

        # 4) Create a QGraphicsRectItem for each node
        for node_id, node_data in self.workflow_json.items():
            x, y = node_positions.get(node_id, (random.randint(100, 600), random.randint(100, 400)))
            rect_item = QGraphicsRectItem(0, 0, 180, 60)
            rect_item.setBrush(QBrush(QColor("#333")))
            rect_item.setPen(QPen(QColor("#999"), 1))
            rect_item.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                               QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
            rect_item.setPos(x, y)

            # Show the _meta title
            title = node_data.get("_meta", {}).get("title", f"Node {node_id}")
            class_type = node_data.get("class_type", "Unknown")

            # Title text
            title_text = QGraphicsTextItem(title, rect_item)
            font_t = QFont()
            font_t.setBold(True)
            title_text.setFont(font_t)
            title_text.setDefaultTextColor(Qt.white)
            title_text.setPos(5, 2)

            # Class text
            class_text = QGraphicsTextItem(class_type, rect_item)
            class_text.setDefaultTextColor(Qt.lightGray)
            class_text.setPos(5, 22)

            # Node id text
            id_text = QGraphicsTextItem(f"ID: {node_id}", rect_item)
            id_text.setDefaultTextColor(Qt.gray)
            id_text.setPos(5, 40)

            self.scene.addItem(rect_item)
            node_items[node_id] = rect_item

        # 5) Draw edges as cubic bezier from right center of src node to left center of target node
        for node_id, node_data in self.workflow_json.items():
            for input_name, value in node_data.get("inputs", {}).items():
                if isinstance(value, list) and len(value) == 2:
                    src_id = str(value[0])
                    if src_id in node_items and node_id in node_items:
                        src_item = node_items[src_id]
                        dst_item = node_items[node_id]

                        # anchor points
                        src_rect = src_item.mapRectToScene(src_item.rect())
                        dst_rect = dst_item.mapRectToScene(dst_item.rect())

                        src_point = QPointF(src_rect.right(), src_rect.top() + src_rect.height()/2)
                        dst_point = QPointF(dst_rect.left(), dst_rect.top() + dst_rect.height()/2)

                        ctrl_offset = (dst_point.x() - src_point.x()) / 2

                        path = QPainterPath(src_point)
                        # first control point
                        path.cubicTo(
                            QPointF(src_point.x() + ctrl_offset, src_point.y()),
                            QPointF(dst_point.x() - ctrl_offset, dst_point.y()),
                            dst_point
                        )

                        edge_item = QGraphicsPathItem(path)
                        pen = QPen(QColor("#F00"), 2)
                        edge_item.setPen(pen)
                        self.scene.addItem(edge_item)
