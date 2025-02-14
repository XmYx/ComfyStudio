#!/usr/bin/env python
import copy
import json
import logging
import os
import random
import subprocess
import sys
import tempfile
import urllib
from typing import Any, Dict

import requests
from PyQt6 import QtCore
from PyQt6.QtCore import QThreadPool, QUrl, QMetaObject, pyqtSignal, Qt
from PyQt6.QtGui import QAction, QCursor
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDockWidget,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QDoubleSpinBox,
    QComboBox,
    QTextEdit,
    QMenu,
    QTableWidget,
    QTableWidgetItem
)


# -------------------------------
# NEW: DynamicParam CLASS
# -------------------------------
class DynamicParam:
    """
    A parameter that can hold a static value, an expression, or be linked to a global variable.
    It can be evaluated dynamically given a context.
    """

    def __init__(self, name: str, param_type: str = "string", value: Any = None,
                 expression: str = "", global_var: str = ""):
        self.name = name
        self.type = param_type
        self.value = value
        self.expression = expression
        self.global_var = global_var

    def evaluate(self, context: Dict[str, Any] = None) -> Any:
        """
        Evaluate the parameter value using the provided context.
        If a global variable is specified and exists in context, use that.
        Otherwise, if an expression is provided, try to evaluate it.
        Otherwise return the static value.

        Note: The context dictionary is passed as the globals for eval(),
        so that all keys (e.g. 'pi') are directly accessible in the expression.
        """
        context = context or {}
        # If a specific global variable is set, return its value.
        if self.global_var and self.global_var in context:
            return context[self.global_var]
        if self.expression:
            try:
                # Pass the context as the globals so that keys like "pi" are available.
                return eval(self.expression, context)
            except Exception as e:
                logging.error(f"Error evaluating expression for param '{self.name}': {e}")
                return None
        return self.value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "value": self.value,
            "expression": self.expression,
            "global_var": self.global_var
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "DynamicParam":
        return DynamicParam(
            name=data.get("name", ""),
            param_type=data.get("type", "string"),
            value=data.get("value", None),
            expression=data.get("expression", ""),
            global_var=data.get("global_var", "")
        )


# -------------------------------
# NEW: DynamicParamEditor WIDGET
# -------------------------------
class DynamicParamEditor(QDialog):
    """
    A dialog to edit a DynamicParam. It allows the user to set a static value,
    write an expression, or choose a global variable from a list.
    Also includes a preview area to show the evaluated result.

    NOTE: If an expression is provided, the static value is ignored.
    """

    def __init__(self, dynamic_param: DynamicParam, global_vars: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Edit Parameter: {dynamic_param.name}")
        self.dynamic_param = dynamic_param
        self.global_vars = global_vars  # Reference to the global variables dictionary

        self.layout = QVBoxLayout(self)

        # --- Static value editor ---
        self.value_label = QLabel("Static Value:")
        self.value_edit = QLineEdit(str(dynamic_param.value) if dynamic_param.value is not None else "")
        self.layout.addWidget(self.value_label)
        self.layout.addWidget(self.value_edit)

        # --- Expression editor ---
        self.expr_label = QLabel("Expression:")
        self.expr_edit = QLineEdit(dynamic_param.expression)
        self.layout.addWidget(self.expr_label)
        self.layout.addWidget(self.expr_edit)

        # --- Global variable selector ---
        self.glob_label = QLabel("Global Variable:")
        self.glob_combo = QComboBox()
        self.glob_combo.addItem("")  # empty selection
        for key in sorted(self.global_vars.keys()):
            self.glob_combo.addItem(key)
        if dynamic_param.global_var:
            idx = self.glob_combo.findText(dynamic_param.global_var)
            if idx >= 0:
                self.glob_combo.setCurrentIndex(idx)
        self.layout.addWidget(self.glob_label)
        self.layout.addWidget(self.glob_combo)

        # --- Preview result area ---
        self.preview_btn = QPushButton("Preview Result")
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.layout.addWidget(self.preview_btn)
        self.layout.addWidget(self.preview_text)
        self.preview_btn.clicked.connect(self.previewResult)

        # --- Dialog buttons ---
        self.btn_layout = QHBoxLayout()
        self.save_btn = QPushButton("Save")
        self.cancel_btn = QPushButton("Cancel")
        self.btn_layout.addWidget(self.save_btn)
        self.btn_layout.addWidget(self.cancel_btn)
        self.layout.addLayout(self.btn_layout)

        self.save_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)

    def previewResult(self):
        # Update the dynamic param from current editor values.
        # If an expression is provided, the static value is ignored.
        current_expr = self.expr_edit.text().strip()
        self.dynamic_param.expression = current_expr
        if current_expr:
            self.dynamic_param.value = None
        else:
            self.dynamic_param.value = self.value_edit.text().strip()
        self.dynamic_param.global_var = self.glob_combo.currentText().strip()
        # Evaluate the parameter using the global variables as context.
        result = self.dynamic_param.evaluate(self.global_vars)
        self.preview_text.setPlainText(str(result))

    def accept(self):
        # Save the edited values back to the dynamic parameter object.
        # Prioritize the expression: if an expression is non-empty, ignore the static value.
        current_expr = self.expr_edit.text().strip()
        self.dynamic_param.expression = current_expr
        if current_expr:
            self.dynamic_param.value = None
        else:
            self.dynamic_param.value = self.value_edit.text().strip()
        self.dynamic_param.global_var = self.glob_combo.currentText().strip()
        super().accept()


# -------------------------------
# NEW: GlobalVariablesEditor WIDGET (VISUALLY ENHANCED)
# -------------------------------
class GlobalVariablesEditor(QDialog):
    """
    A visually enhanced editor for managing global variables.
    Users can add, name, and set variables to int, float, or string values.
    These variables can then be referenced directly by their names in expressions.
    """
    variablesChanged = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Global Variables Editor")
        self.resize(500, 400)
        self.global_vars: Dict[str, Any] = {}  # Dictionary holding global variables

        # Create a table widget to display variables.
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Type", "Value"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.AllEditTriggers)

        # Buttons to add or remove variables.
        self.add_btn = QPushButton("Add Variable")
        self.remove_btn = QPushButton("Remove Selected")
        self.save_btn = QPushButton("Save")
        self.cancel_btn = QPushButton("Cancel")

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.add_btn)
        btn_layout.addWidget(self.remove_btn)

        btn_layout2 = QHBoxLayout()
        btn_layout2.addStretch()
        btn_layout2.addWidget(self.save_btn)
        btn_layout2.addWidget(self.cancel_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addLayout(btn_layout)
        layout.addLayout(btn_layout2)

        # Connect button signals.
        self.add_btn.clicked.connect(self.addVariable)
        self.remove_btn.clicked.connect(self.removeSelectedVariable)
        self.save_btn.clicked.connect(self.saveVariables)
        self.cancel_btn.clicked.connect(self.reject)

        self.loadVariables()

    def loadVariables(self):
        # For demonstration, initialize with some default global variables.
        self.global_vars = {"defaultVar": 42, "pi": 3.14, "greeting": "Hello"}
        self.table.setRowCount(0)
        for var, val in self.global_vars.items():
            self.insertRowForVariable(var, val)

    def insertRowForVariable(self, name: str, value: Any):
        row = self.table.rowCount()
        self.table.insertRow(row)
        # Name item.
        name_item = QTableWidgetItem(name)
        # Determine type automatically.
        if isinstance(value, int):
            type_str = "int"
        elif isinstance(value, float):
            type_str = "float"
        else:
            type_str = "string"
        type_item = QTableWidgetItem(type_str)
        value_item = QTableWidgetItem(str(value))
        self.table.setItem(row, 0, name_item)
        self.table.setItem(row, 1, type_item)
        self.table.setItem(row, 2, value_item)

    def addVariable(self):
        row = self.table.rowCount()
        self.table.insertRow(row)
        # Default new variable settings.
        name_item = QTableWidgetItem("newVar")
        type_item = QTableWidgetItem("string")
        value_item = QTableWidgetItem("")
        self.table.setItem(row, 0, name_item)
        self.table.setItem(row, 1, type_item)
        self.table.setItem(row, 2, value_item)
        self.table.editItem(name_item)

    def removeSelectedVariable(self):
        selected_rows = list(set(item.row() for item in self.table.selectedItems()))
        for row in sorted(selected_rows, reverse=True):
            self.table.removeRow(row)

    def saveVariables(self):
        new_globals: Dict[str, Any] = {}
        row_count = self.table.rowCount()
        for row in range(row_count):
            name_item = self.table.item(row, 0)
            type_item = self.table.item(row, 1)
            value_item = self.table.item(row, 2)
            if name_item is None or type_item is None or value_item is None:
                continue
            name = name_item.text().strip()
            type_str = type_item.text().strip().lower()
            value_str = value_item.text().strip()
            if not name:
                continue
            try:
                if type_str == "int":
                    value = int(value_str)
                elif type_str == "float":
                    value = float(value_str)
                else:
                    value = value_str
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Invalid value for variable '{name}': {e}")
                return
            new_globals[name] = value
        self.global_vars = new_globals
        self.variablesChanged.emit(new_globals)
        QMessageBox.information(self, "Info", "Global variables updated.")
        self.accept()