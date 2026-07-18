#!/usr/bin/env python
"""
Shot workflow node editor.

A QGraphicsView-based graph of the CURRENT shot's workflows: every
WorkflowAssignment is a node, params flagged graphInput/graphOutput become
input/output ports, and every node has an implicit "Result" output port (its
rendered file). Dragging an edge from an output port onto an input port stores
a connection in shot.graph["connections"]; node positions persist in
shot.graph["positions"] (keyed by workflow uid).

Rendering resolves these connections in
comfyhandler.py::resolveGraphInputs / topoSortedWorkflowIndices.
"""

import os

from qtpy import QtCore, QtGui, QtWidgets
from qtpy.QtCore import Qt, QPointF, QRectF
from qtpy.QtGui import QBrush, QColor, QPainterPath, QPen
from qtpy.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QMenu,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from comfystudio.sdmodules.comfy_datatypes import (
    color_for_type,
    infer_node_output_type,
    param_port_type,
    result_port_type,
    types_compatible,
)
from comfystudio.sdmodules.cs_datastruts import (
    RESULT_PORT,
    node_output_port_id,
    node_output_port_node_id,
    workflow_param_port_id,
)

PORT_RADIUS = 6
NODE_WIDTH = 230
HEADER_HEIGHT = 42
ROW_HEIGHT = 22
NODE_H_SPACING = 300
NODE_V_SPACING = 60


class PortItem(QGraphicsEllipseItem):
    """A connectable port dot on a workflow node."""

    def __init__(self, node_item, wf_uid, port_id, direction, ptype, label):
        super().__init__(-PORT_RADIUS, -PORT_RADIUS, PORT_RADIUS * 2, PORT_RADIUS * 2, node_item)
        self.node_item = node_item
        self.wf_uid = wf_uid
        self.port_id = port_id
        self.direction = direction  # "in" | "out"
        self.ptype = ptype  # canonical ComfyUI-style type (or "*")
        self._base_color = QColor(color_for_type(ptype))
        self.setBrush(QBrush(self._base_color))
        self.setPen(QPen(QColor("#222"), 1))
        self.setAcceptHoverEvents(True)
        self.setToolTip(f"{label} ({ptype})")
        self.setZValue(2)

    def hoverEnterEvent(self, event):
        self.setBrush(QBrush(self._base_color.lighter(150)))
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.setBrush(QBrush(self._base_color))
        super().hoverLeaveEvent(event)

    def scenePos_center(self) -> QPointF:
        return self.mapToScene(QPointF(0, 0))


class EdgeItem(QGraphicsPathItem):
    """A stored connection drawn as a cubic bezier between two ports."""

    def __init__(self, editor, connection, src_port: PortItem, dst_port: PortItem):
        super().__init__()
        self.editor = editor
        self.connection = connection
        self.src_port = src_port
        self.dst_port = dst_port
        self.setZValue(0)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        # ComfyUI convention: links take the source type's colour.
        self._base_color = QColor(color_for_type(src_port.ptype))
        self.setPen(QPen(self._base_color, 2))
        self.updatePath()

    def updatePath(self):
        src = self.src_port.scenePos_center()
        dst = self.dst_port.scenePos_center()
        ctrl = max(40.0, abs(dst.x() - src.x()) / 2)
        path = QPainterPath(src)
        path.cubicTo(QPointF(src.x() + ctrl, src.y()),
                     QPointF(dst.x() - ctrl, dst.y()),
                     dst)
        self.setPath(path)

    def paint(self, painter, option, widget=None):
        if self.isSelected():
            self.setPen(QPen(QColor("#ffe066"), 3))
        else:
            self.setPen(QPen(self._base_color, 2))
        super().paint(painter, option, widget)

    def contextMenuEvent(self, event):
        menu = QMenu()
        disconnect = menu.addAction("Disconnect")
        chosen = menu.exec(event.screenPos())
        if chosen == disconnect:
            self.editor.removeConnection(self.connection)
        event.accept()


class WorkflowNodeItem(QGraphicsRectItem):
    """One WorkflowAssignment of the current shot, with its exposed ports."""

    def __init__(self, editor, workflow, index):
        self.editor = editor
        self.workflow = workflow
        self.index = index
        self.ports = {}  # (port_id, direction) -> PortItem

        params = workflow.parameters.get("params", [])
        inputs = [p for p in params if p.get("graphInput")]
        # Output rows: exposed params, then exposed ComfyUI node outputs.
        out_entries = []
        for p in params:
            if p.get("graphOutput"):
                label = p.get("displayName", p.get("name", ""))
                ptype = param_port_type(p)
                out_entries.append({
                    "port": workflow_param_port_id(p),
                    "label": label,
                    "ptype": ptype,
                    "tooltip": f"{label} ({ptype})",
                })
        for entry in workflow.parameters.get("exposedNodeOutputs", []):
            port = entry.get("port", "")
            label = entry.get("label") or port
            ptype = entry.get("type", "*")  # inferred at exposure time
            tooltip = f"{label} ({ptype})"
            nid = node_output_port_node_id(port)
            node_file = workflow.lastNodeOutputs.get(nid, "") if nid else ""
            if node_file:
                tooltip += f"\n{node_file}"
            out_entries.append({"port": port, "label": label,
                                "ptype": ptype, "tooltip": tooltip})

        rows = max(len(inputs), len(out_entries) + 1)  # +1: implicit Result port
        height = HEADER_HEIGHT + rows * ROW_HEIGHT + 8
        super().__init__(0, 0, NODE_WIDTH, height)

        enabled = workflow.enabled
        self.setBrush(QBrush(QColor("#333333") if enabled else QColor("#2a2a2a")))
        self.setPen(QPen(QColor("#999999") if enabled else QColor("#555555"), 1))
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                      | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setZValue(1)

        title = QGraphicsSimpleTextItem(f"{index + 1}. {os.path.basename(workflow.path)}", self)
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        title.setBrush(QBrush(QColor("#ffffff" if enabled else "#888888")))
        title.setPos(8, 5)

        subtitle = QGraphicsSimpleTextItem("disabled" if not enabled else "", self)
        subtitle.setBrush(QBrush(QColor("#cc8888")))
        subtitle.setPos(8, 22)

        for i, param in enumerate(inputs):
            y = HEADER_HEIGHT + i * ROW_HEIGHT + ROW_HEIGHT / 2
            port_id = workflow_param_port_id(param)
            port = PortItem(self, workflow.uid, port_id, "in",
                            param_port_type(param),
                            param.get("displayName", param.get("name", "")))
            port.setPos(0, y)
            self.ports[(port_id, "in")] = port
            label = QGraphicsSimpleTextItem(param.get("displayName", param.get("name", "")), self)
            label.setBrush(QBrush(QColor("#dddddd")))
            label.setPos(12, y - 7)

        for i, entry in enumerate(out_entries):
            y = HEADER_HEIGHT + i * ROW_HEIGHT + ROW_HEIGHT / 2
            port = PortItem(self, workflow.uid, entry["port"], "out",
                            entry["ptype"], entry["label"])
            port.setToolTip(entry["tooltip"])
            port.setPos(NODE_WIDTH, y)
            self.ports[(entry["port"], "out")] = port
            label = QGraphicsSimpleTextItem(entry["label"], self)
            is_node_out = node_output_port_node_id(entry["port"]) is not None
            label.setBrush(QBrush(QColor("#e8c88f" if is_node_out else "#dddddd")))
            label.setPos(NODE_WIDTH - 12 - label.boundingRect().width(), y - 7)

        # Implicit Result port (rendered output) below the other outputs.
        y = HEADER_HEIGHT + len(out_entries) * ROW_HEIGHT + ROW_HEIGHT / 2
        result_type = result_port_type(workflow)
        result_tip = f"Result (rendered output, {result_type})"
        if workflow.lastOutput:
            result_tip += f"\n{workflow.lastOutput}"
        result_port = PortItem(self, workflow.uid, RESULT_PORT, "out", result_type, "Result")
        result_port.setToolTip(result_tip)
        result_port.setPos(NODE_WIDTH, y)
        self.ports[(RESULT_PORT, "out")] = result_port
        label = QGraphicsSimpleTextItem("Result", self)
        label.setBrush(QBrush(QColor("#7fd4ff")))
        label.setPos(NODE_WIDTH - 12 - label.boundingRect().width(), y - 7)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.editor.onNodeMoved(self)
        return super().itemChange(change, value)

    def contextMenuEvent(self, event):
        workflow = self.workflow
        params = workflow.parameters.get("params", [])
        menu = QMenu()

        def param_label(p):
            title = p.get("nodeMetaTitle", "")
            name = p.get("displayName", p.get("name", ""))
            return f"{name}  [{title}]" if title else name

        in_menu = menu.addMenu("Expose input port")
        in_actions = {}
        for p in params:
            act = in_menu.addAction(param_label(p))
            act.setCheckable(True)
            act.setChecked(bool(p.get("graphInput")))
            in_actions[act] = p

        out_menu = menu.addMenu("Expose output port")
        out_actions = {}
        for p in params:
            act = out_menu.addAction(param_label(p))
            act.setCheckable(True)
            act.setChecked(bool(p.get("graphOutput")))
            out_actions[act] = p

        # ComfyUI node outputs (discovered from the workflow file).
        node_out_menu = menu.addMenu("Expose node output")
        nodeout_actions = {}
        exposed_ports = {e.get("port")
                         for e in workflow.parameters.get("exposedNodeOutputs", [])}
        try:
            wf_json = self.editor.window.loadWorkflowJson(workflow.path)
            node_infos = self.editor.window.extractWorkflowNodeOutputs(wf_json)
        except Exception as e:
            node_infos = []
            node_out_menu.setEnabled(False)
            node_out_menu.setTitle(f"Expose node output (unavailable: {e})")
        for info in node_infos:
            for slot in info["slots"]:
                pid = node_output_port_id(info["node_id"], slot)
                ptype = infer_node_output_type(wf_json, info["node_id"], slot)
                text = f"{info['title']} [{info['node_id']}] out {slot} ({ptype})"
                act = node_out_menu.addAction(text)
                act.setCheckable(True)
                act.setChecked(pid in exposed_ports)
                nodeout_actions[act] = (pid, text, ptype)

        menu.addSeparator()
        toggle_enabled = menu.addAction("Disable" if workflow.enabled else "Enable")
        disconnect_all = menu.addAction("Disconnect all")

        chosen = menu.exec(event.screenPos())
        if chosen in in_actions:
            self.editor.togglePortFlag(workflow, in_actions[chosen], "graphInput")
        elif chosen in out_actions:
            self.editor.togglePortFlag(workflow, out_actions[chosen], "graphOutput")
        elif chosen in nodeout_actions:
            pid, text, ptype = nodeout_actions[chosen]
            self.editor.toggleNodeOutputExposure(workflow, pid, text, ptype)
        elif chosen == toggle_enabled:
            workflow.enabled = not workflow.enabled
            self.editor.markModified()
            self.editor.refresh()
            self.editor.notifyWorkflowListChanged()
        elif chosen == disconnect_all:
            self.editor.removeNodeConnections(workflow.uid)
        event.accept()


class ShotGraphView(QGraphicsView):
    def __init__(self, editor, scene, parent=None):
        super().__init__(scene, parent)
        self.editor = editor
        self.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(QColor("#232323")))
        self._temp_edge = None
        self._drag_src_port = None
        self._panning = False
        self._pan_start = QtCore.QPoint()

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        current = self.transform().m11()
        if 0.15 <= current * factor <= 4.0:
            self.scale(factor, factor)
        event.accept()

    def _portAt(self, view_pos, direction=None):
        for item in self.items(view_pos):
            if isinstance(item, PortItem) and (direction is None or item.direction == direction):
                return item
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_start = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            port = self._portAt(event.pos(), direction="out")
            if port is not None:
                self._drag_src_port = port
                self._temp_edge = QGraphicsPathItem()
                self._temp_edge.setPen(QPen(QColor(color_for_type(port.ptype)), 2,
                                            Qt.PenStyle.DashLine))
                self._temp_edge.setZValue(3)
                self.scene().addItem(self._temp_edge)
                self._updateTempEdge(event.pos())
                event.accept()
                return
        super().mousePressEvent(event)

    def _updateTempEdge(self, view_pos):
        src = self._drag_src_port.scenePos_center()
        dst = self.mapToScene(view_pos)
        ctrl = max(40.0, abs(dst.x() - src.x()) / 2)
        path = QPainterPath(src)
        path.cubicTo(QPointF(src.x() + ctrl, src.y()),
                     QPointF(dst.x() - ctrl, dst.y()), dst)
        self._temp_edge.setPath(path)

    def mouseMoveEvent(self, event):
        if self._panning:
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        if self._temp_edge is not None:
            self._updateTempEdge(event.pos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._panning and event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        if self._temp_edge is not None and event.button() == Qt.MouseButton.LeftButton:
            self.scene().removeItem(self._temp_edge)
            self._temp_edge = None
            src = self._drag_src_port
            self._drag_src_port = None
            dst = self._portAt(event.pos(), direction="in")
            if src is not None and dst is not None:
                self.editor.tryConnect(src, dst)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            edges = [it for it in self.scene().selectedItems() if isinstance(it, EdgeItem)]
            if edges:
                for edge in edges:
                    self.editor.removeConnection(edge.connection, refresh=False)
                self.editor.refresh()
                return
        super().keyPressEvent(event)


class ShotNodeEditorWidget(QWidget):
    """Dock widget content: toolbar + graph view for the current shot."""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self._dirty = False
        self._building = False
        self.node_items = {}  # uid -> WorkflowNodeItem
        self.edge_items = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QToolBar(self)
        auto_act = toolbar.addAction("Auto Layout")
        fit_act = toolbar.addAction("Fit")
        toolbar.addSeparator()
        self.hint_label = QtWidgets.QLabel("")
        toolbar.addWidget(self.hint_label)
        layout.addWidget(toolbar)

        self.scene = QGraphicsScene(self)
        self.view = ShotGraphView(self, self.scene, self)
        layout.addWidget(self.view)

        auto_act.triggered.connect(self.autoLayout)
        fit_act.triggered.connect(self.fitView)

    # --- helpers -----------------------------------------------------------

    def currentShot(self):
        idx = getattr(self.window, "currentShotIndex", -1)
        shots = getattr(self.window, "shots", [])
        if 0 <= idx < len(shots):
            return shots[idx]
        return None

    def markModified(self):
        if hasattr(self.window, "setProjectModified"):
            self.window.setProjectModified(True)

    def statusMessage(self, text):
        widgets = getattr(self.window, "status_widgets", None)
        if widgets and "statusMessage" in widgets:
            widgets["statusMessage"].setText(text)

    def notifyWorkflowListChanged(self):
        shot = self.currentShot()
        if shot is not None and hasattr(self.window, "refreshWorkflowsList"):
            self.window.refreshWorkflowsList(shot)

    # --- build/refresh ------------------------------------------------------

    def refresh(self):
        if self._building:
            return
        if not self.isVisible():
            self._dirty = True
            return
        self._dirty = False
        self._building = True
        try:
            self.scene.clear()
            self.node_items = {}
            self.edge_items = []
            shot = self.currentShot()
            if shot is None:
                self.hint_label.setText("No shot selected")
                return
            # Idempotent: converts any legacy prev-result flags added this
            # session into visible connections.
            if hasattr(self.window, "migrateShotGraph"):
                self.window.migrateShotGraph(shot)
            self.hint_label.setText(
                f"{shot.name} — right-click a node to expose ports; drag output → input to connect"
            )

            positions = shot.graph.setdefault("positions", {})
            auto = self._autoPositions(shot)
            for i, wf in enumerate(shot.workflows):
                node = WorkflowNodeItem(self, wf, i)
                pos = positions.get(wf.uid) or auto.get(wf.uid) or [i * NODE_H_SPACING, 0]
                node.setPos(QPointF(float(pos[0]), float(pos[1])))
                self.scene.addItem(node)
                self.node_items[wf.uid] = node

            for conn in list(shot.graph.get("connections", [])):
                src = self._findPort(conn.get("from", {}), "out")
                dst = self._findPort(conn.get("to", {}), "in")
                if src is None or dst is None:
                    continue
                edge = EdgeItem(self, conn, src, dst)
                self.scene.addItem(edge)
                self.edge_items.append(edge)
            self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-100, -100, 100, 100))
        finally:
            self._building = False

    def showEvent(self, event):
        super().showEvent(event)
        if self._dirty:
            # Refresh after the widget is actually visible.
            QtCore.QTimer.singleShot(0, self.refresh)

    def _findPort(self, endpoint, direction):
        node = self.node_items.get(endpoint.get("wf"))
        if node is None:
            return None
        return node.ports.get((endpoint.get("port"), direction))

    def _autoPositions(self, shot):
        """BFS layering by connections; unconnected nodes follow list order."""
        uids = [wf.uid for wf in shot.workflows]
        deps = {uid: set() for uid in uids}
        for conn in shot.graph.get("connections", []):
            src = conn.get("from", {}).get("wf")
            dst = conn.get("to", {}).get("wf")
            if src in deps and dst in deps and src != dst:
                deps[dst].add(src)
        layer = {}
        for _ in range(len(uids) + 1):
            changed = False
            for uid in uids:
                want = max((layer.get(d, 0) + 1 for d in deps[uid]), default=0)
                if layer.get(uid, 0) < want:
                    layer[uid] = want
                    changed = True
            if not changed:
                break
        positions = {}
        counts = {}
        for i, uid in enumerate(uids):
            lvl = layer.get(uid, 0)
            row = counts.get(lvl, 0)
            counts[lvl] = row + 1
            positions[uid] = [lvl * NODE_H_SPACING, row * (NODE_V_SPACING + 120)]
        return positions

    # --- graph edits --------------------------------------------------------

    def onNodeMoved(self, node: WorkflowNodeItem):
        if self._building:
            return
        shot = self.currentShot()
        if shot is None:
            return
        pos = node.pos()
        shot.graph.setdefault("positions", {})[node.workflow.uid] = [pos.x(), pos.y()]
        self.markModified()
        for edge in self.edge_items:
            edge.updatePath()

    def _wouldCreateCycle(self, shot, src_uid, dst_uid):
        # Adding src -> dst closes a cycle iff dst already reaches src.
        forward = {}
        for conn in shot.graph.get("connections", []):
            s = conn.get("from", {}).get("wf")
            d = conn.get("to", {}).get("wf")
            forward.setdefault(s, set()).add(d)
        seen = set()
        stack = [dst_uid]
        while stack:
            cur = stack.pop()
            if cur == src_uid:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(forward.get(cur, ()))
        return False

    def tryConnect(self, src: PortItem, dst: PortItem):
        shot = self.currentShot()
        if shot is None:
            return
        if src.wf_uid == dst.wf_uid:
            self.statusMessage("Cannot connect a workflow to itself.")
            return
        if not types_compatible(src.ptype, dst.ptype):
            self.statusMessage(
                f"Connection refused: type mismatch ({src.ptype} → {dst.ptype}).")
            return
        if self._wouldCreateCycle(shot, src.wf_uid, dst.wf_uid):
            self.statusMessage("Connection refused: it would create a cycle.")
            return
        connections = shot.graph.setdefault("connections", [])
        # One edge per input port: replace an existing connection.
        connections[:] = [
            c for c in connections
            if not (c.get("to", {}).get("wf") == dst.wf_uid
                    and c.get("to", {}).get("port") == dst.port_id)
        ]
        connections.append({
            "from": {"wf": src.wf_uid, "port": src.port_id},
            "to": {"wf": dst.wf_uid, "port": dst.port_id},
        })
        self.markModified()
        self.refresh()
        self.statusMessage("Connected.")

    def removeConnection(self, connection, refresh=True):
        shot = self.currentShot()
        if shot is None:
            return
        connections = shot.graph.get("connections", [])
        if connection in connections:
            connections.remove(connection)
            self.markModified()
        if refresh:
            self.refresh()

    def removeNodeConnections(self, uid):
        shot = self.currentShot()
        if shot is None:
            return
        connections = shot.graph.get("connections", [])
        kept = [c for c in connections
                if c.get("from", {}).get("wf") != uid and c.get("to", {}).get("wf") != uid]
        if len(kept) != len(connections):
            shot.graph["connections"] = kept
            self.markModified()
        self.refresh()

    def togglePortFlag(self, workflow, param, flag):
        turning_on = not param.get(flag)
        if turning_on:
            param[flag] = True
        else:
            param.pop(flag, None)
            # Dropping a port invalidates its connections.
            shot = self.currentShot()
            if shot is not None:
                port = workflow_param_port_id(param)
                direction_key = "to" if flag == "graphInput" else "from"
                connections = shot.graph.get("connections", [])
                shot.graph["connections"] = [
                    c for c in connections
                    if not (c.get(direction_key, {}).get("wf") == workflow.uid
                            and c.get(direction_key, {}).get("port") == port)
                ]
        self.markModified()
        self.refresh()

    def toggleNodeOutputExposure(self, workflow, port_id, label, ptype="*"):
        """Exposes/unexposes a ComfyUI node output as a graph output port."""
        exposures = workflow.parameters.setdefault("exposedNodeOutputs", [])
        existing = next((e for e in exposures if e.get("port") == port_id), None)
        if existing is None:
            exposures.append({"port": port_id, "label": label, "type": ptype})
        else:
            exposures.remove(existing)
            # Connections from a removed port are meaningless.
            shot = self.currentShot()
            if shot is not None:
                shot.graph["connections"] = [
                    c for c in shot.graph.get("connections", [])
                    if not (c.get("from", {}).get("wf") == workflow.uid
                            and c.get("from", {}).get("port") == port_id)
                ]
        self.markModified()
        self.refresh()

    # --- toolbar ------------------------------------------------------------

    def autoLayout(self):
        shot = self.currentShot()
        if shot is None:
            return
        shot.graph["positions"] = self._autoPositions(shot)
        self.markModified()
        self.refresh()
        self.fitView()

    def fitView(self):
        rect = self.scene.itemsBoundingRect()
        if not rect.isNull():
            self.view.fitInView(rect.adjusted(-50, -50, 50, 50),
                                Qt.AspectRatioMode.KeepAspectRatio)
