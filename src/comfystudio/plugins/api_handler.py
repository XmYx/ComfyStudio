"""
plugins/api_handler.py

This plugin does two things:
  • It adds an “API Options” panel (accessible from the Settings menu) that lets the user
    create, start, and stop one or more API endpoints. Each endpoint runs in its own QThread
    (using Python’s HTTPServer) and listens on a custom port.
  • It extends the dynamic parameter context menu so that for string parameters the user
    may choose “Set Param to API Image”. Such a parameter is flagged with a dynamic override
    (type "api"). When an external app sends an image to one of these endpoints, the server
    callback calls back into the main app (via process_api_request) to update the parameter,
    run the workflow, and return the output.
"""

import os
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from PyQt6.QtCore import QThread, pyqtSignal, QObject, Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import ( QDialog, QFormLayout, QLineEdit, QComboBox, QHBoxLayout,
    QVBoxLayout, QDialogButtonBox, QLabel, QPushButton, QMessageBox, QInputDialog
)

# ---------------------------------------------------------
# HTTP Server Classes
# ---------------------------------------------------------
class ApiRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress default logging
        return

    def do_POST(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            # (For simplicity, assume the entire POST body is the raw image data.)
            image_data = post_data

            # Call the callback defined on the HTTPServer instance.
            if self.server.server_callback:
                output_file = self.server.server_callback(self.server.endpoint_config, image_data)
                if output_file and os.path.exists(output_file):
                    with open(output_file, "rb") as f:
                        response_data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(response_data)))
                    self.end_headers()
                    self.wfile.write(response_data)
                else:
                    self.send_error(500, "Workflow processing failed.")
            else:
                self.send_error(500, "No callback defined.")
        except Exception as e:
            self.send_error(500, f"Exception: {e}")

    def do_GET(self):
        # A simple GET to allow stopping the server.
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")


class ApiServerThread(QThread):
    def __init__(self, endpoint_config, server_callback, parent=None):
        super().__init__(parent)
        self.endpoint_config = endpoint_config  # e.g. {"name": "MyAPI", "port": "8001"}
        self.server_callback = server_callback  # callback from the main app
        self.httpd = None
        self._running = True

    def run(self):
        port = int(self.endpoint_config.get("port", 8000))
        server_address = ('', port)
        self.httpd = HTTPServer(server_address, ApiRequestHandler)
        # Pass the endpoint config and callback to the server instance.
        self.httpd.endpoint_config = self.endpoint_config
        self.httpd.server_callback = self.server_callback
        while self._running:
            self.httpd.handle_request()  # Process one request at a time

    def stop(self):
        self._running = False
        if self.httpd:
            try:
                # Connect to the server to unblock handle_request.
                import socket
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect(('localhost', int(self.endpoint_config.get("port", 8000))))
                s.send(b"GET /shutdown HTTP/1.1\r\nHost: localhost\r\n\r\n")
                s.close()
            except Exception:
                pass
        self.quit()
        self.wait()


# ---------------------------------------------------------
# API Options Dialog
# ---------------------------------------------------------
class ApiOptionsDialog(QDialog):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("API Options")
        self.endpoints = self.app.settingsManager.get("api_endpoints", [])
        self.server_threads = {}  # key: endpoint name, value: ApiServerThread instance
        self.initUI()

    def initUI(self):
        layout = QVBoxLayout(self)

        # Combo box listing endpoints.
        self.endpointCombo = QComboBox()
        self.refreshEndpointList()
        layout.addWidget(QLabel("Configured API Endpoints:"))
        layout.addWidget(self.endpointCombo)

        # Form for entering/editing an endpoint.
        form = QFormLayout()
        self.nameEdit = QLineEdit()
        self.portEdit = QLineEdit()
        form.addRow("Name:", self.nameEdit)
        form.addRow("Port:", self.portEdit)
        layout.addLayout(form)

        # Buttons: Add, Edit, Delete.
        btnLayout = QHBoxLayout()
        addBtn = QPushButton("Add")
        editBtn = QPushButton("Edit")
        deleteBtn = QPushButton("Delete")
        btnLayout.addWidget(addBtn)
        btnLayout.addWidget(editBtn)
        btnLayout.addWidget(deleteBtn)
        layout.addLayout(btnLayout)

        # Buttons: Start and Stop server.
        btnLayout2 = QHBoxLayout()
        startBtn = QPushButton("Start Server")
        stopBtn = QPushButton("Stop Server")
        btnLayout2.addWidget(startBtn)
        btnLayout2.addWidget(stopBtn)
        layout.addLayout(btnLayout2)

        # OK button.
        self.buttonBox = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        layout.addWidget(self.buttonBox)

        # Connections.
        addBtn.clicked.connect(self.addEndpoint)
        editBtn.clicked.connect(self.editEndpoint)
        deleteBtn.clicked.connect(self.deleteEndpoint)
        startBtn.clicked.connect(self.startServer)
        stopBtn.clicked.connect(self.stopServer)
        self.endpointCombo.currentIndexChanged.connect(self.loadEndpointDetails)
        self.buttonBox.accepted.connect(self.accept)

    def refreshEndpointList(self):
        self.endpointCombo.clear()
        for ep in self.endpoints:
            display = f"{ep.get('name', 'Unnamed')} (Port: {ep.get('port','')})"
            self.endpointCombo.addItem(display)

    def loadEndpointDetails(self, index):
        if index < 0 or index >= len(self.endpoints):
            return
        ep = self.endpoints[index]
        self.nameEdit.setText(ep.get("name", ""))
        self.portEdit.setText(str(ep.get("port", "")))

    def addEndpoint(self):
        name = self.nameEdit.text().strip()
        port = self.portEdit.text().strip()
        if not name or not port:
            QMessageBox.warning(self, "Input Error", "Fill in name and port.")
            return
        try:
            int(port)
        except ValueError:
            QMessageBox.warning(self, "Input Error", "Port must be an integer.")
            return
        new_ep = {"name": name, "port": port}
        self.endpoints.append(new_ep)
        self.app.settingsManager.set("api_endpoints", self.endpoints)
        self.app.settingsManager.save()
        self.refreshEndpointList()

    def editEndpoint(self):
        index = self.endpointCombo.currentIndex()
        if index < 0 or index >= len(self.endpoints):
            return
        name = self.nameEdit.text().strip()
        port = self.portEdit.text().strip()
        if not name or not port:
            QMessageBox.warning(self, "Input Error", "Fill in all fields.")
            return
        try:
            int(port)
        except ValueError:
            QMessageBox.warning(self, "Input Error", "Port must be an integer.")
            return
        self.endpoints[index] = {"name": name, "port": port}
        self.app.settingsManager.set("api_endpoints", self.endpoints)
        self.app.settingsManager.save()
        self.refreshEndpointList()

    def deleteEndpoint(self):
        index = self.endpointCombo.currentIndex()
        if index < 0 or index >= len(self.endpoints):
            return
        ep = self.endpoints.pop(index)
        if ep.get("name") in self.server_threads:
            self.server_threads[ep["name"]].stop()
            del self.server_threads[ep["name"]]
        self.app.settingsManager.set("api_endpoints", self.endpoints)
        self.app.settingsManager.save()
        self.refreshEndpointList()

    def startServer(self):
        index = self.endpointCombo.currentIndex()
        if index < 0 or index >= len(self.endpoints):
            return
        ep = self.endpoints[index]
        if ep.get("name") in self.server_threads:
            QMessageBox.information(self, "API Server", "Server already running.")
            return
        thread = ApiServerThread(ep, self.app.process_api_request)
        thread.start()
        self.server_threads[ep["name"]] = thread
        QMessageBox.information(self, "API Server", f"Server '{ep.get('name')}' started on port {ep.get('port')}.")

    def stopServer(self):
        index = self.endpointCombo.currentIndex()
        if index < 0 or index >= len(self.endpoints):
            return
        ep = self.endpoints[index]
        if ep.get("name") in self.server_threads:
            self.server_threads[ep["name"]].stop()
            del self.server_threads[ep["name"]]
            QMessageBox.information(self, "API Server", f"Server '{ep.get('name')}' stopped.")
        else:
            QMessageBox.information(self, "API Server", "Server not running.")


# ---------------------------------------------------------
# Plugin Registration and Dynamic Param Extension
# ---------------------------------------------------------
def register(app):
    # Add API Options menu item to the Settings menu.
    apiOptionsAction = QAction("API Options", app)
    settings_menu = None
    for action in app.menuBar().actions():
        if action.text() == "Settings":
            settings_menu = action.menu()
            break
    if settings_menu:
        settings_menu.addAction(apiOptionsAction)
    apiOptionsAction.triggered.connect(lambda: openApiOptionsDialog(app))
    # Instead of hijacking the context menu, import the default action specs.
    from comfystudio.sdmodules.core.param_context_menu import register_param_context_action_spec
    # Define the API image callback using a closure to capture `app`.
    def set_api_image(app, param):
        param["useApiImage"] = True
        param["dynamicOverrides"] = {"type": "api"}
        QMessageBox.information(app, "Dynamic Parameter", "Parameter set to use API image input.")
        if app.workflowListWidget.currentItem():
            app.onWorkflowItemClicked(app.workflowListWidget.currentItem())

    # Extend the default context menu actions with the new API action.
    register_param_context_action_spec({
        "text": "Set Param to API Image",
        "callback": set_api_image,
        "param_types": ["string"]

    })

def openApiOptionsDialog(app):
    dialog = ApiOptionsDialog(app)
    dialog.exec()
