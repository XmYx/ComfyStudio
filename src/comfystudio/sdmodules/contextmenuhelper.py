from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu
from qtpy.QtCore import QPoint

def create_context_menu(parent, action_specs, pos: QPoint):
    """
    Creates and executes a context menu.

    Parameters:
        parent: The widget that will be used as the parent for the menu.
        action_specs: A list of dictionaries where each dictionary represents an action.
                      Each dictionary may include:
                          - "text": The text to display.
                          - "tooltip": (Optional) Tooltip text.
                          - "callback": The function to call when the action is triggered.
                          - "data": (Optional) Data to associate with the action.
                          - "enabled": (Optional, default True) Whether the action is enabled.
        pos: The position (usually from a context menu event) where the menu should appear.

    Returns:
        The QAction that was triggered (or None if the user cancelled).
    """
    menu = QMenu(parent)
    actions = []
    for spec in action_specs:
        action = QAction(spec.get("text", ""), parent)
        if "tooltip" in spec:
            action.setToolTip(spec["tooltip"])
        if "enabled" in spec:
            action.setEnabled(spec["enabled"])
        # Optionally, if you want to store extra data:
        if "data" in spec:
            action.setData(spec["data"])
        if "callback" in spec:
            action.triggered.connect(spec["callback"])
        menu.addAction(action)
        actions.append(action)
    # Execute the menu. It will pop up at the global position converted from pos.
    return menu.exec(parent.mapToGlobal(pos))