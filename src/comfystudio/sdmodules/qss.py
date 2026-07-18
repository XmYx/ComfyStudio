"""Application stylesheets.

Provides a dark and a light theme. Use get_stylesheet(name) to fetch one;
the module-level `qss` remains the default (dark) stylesheet for backward
compatibility.
"""

dark_qss = """
/* General */
QMainWindow, QDialog, QWizard {
    background-color: #121212;
    color: #E0E0E0;
}

QWidget {
    background-color: #121212;
    color: #E0E0E0;
    font-family: "Segoe UI", sans-serif;
    font-size: 10pt;
}

QToolTip {
    background-color: #2C2C2C;
    color: #E0E0E0;
    border: 1px solid #555555;
    padding: 3px;
}

/* QMenuBar and QMenu */
QMenuBar {
    background-color: #1E1E1E;
    color: #E0E0E0;
}

QMenuBar::item {
    background: transparent;
    padding: 4px 12px;
}

QMenuBar::item:selected {
    background-color: #333333;
}

QMenu {
    background-color: #1E1E1E;
    color: #E0E0E0;
    border: 1px solid #333333;
}

QMenu::item:selected {
    background-color: #333333;
}

QMenu::separator {
    height: 1px;
    background: #333333;
    margin: 4px 8px;
}

/* QToolBar */
QToolBar {
    background-color: #1E1E1E;
    border-bottom: 1px solid #333333;
    spacing: 4px;
    padding: 2px;
}

QToolBar::separator {
    width: 1px;
    background: #333333;
    margin: 4px 4px;
}

QToolButton {
    background-color: transparent;
    color: #E0E0E0;
    padding: 4px 8px;
    border-radius: 4px;
}

QToolButton:hover {
    background-color: #3C3C3C;
}

QToolButton:pressed, QToolButton:checked {
    background-color: #4C4C4C;
}

/* QStatusBar */
QStatusBar {
    background-color: #1E1E1E;
    color: #E0E0E0;
    border-top: 1px solid #333333;
}

QStatusBar::item {
    border: none;
}

/* QPushButton */
QPushButton {
    background-color: #2C2C2C;
    border: 1px solid #444444;
    padding: 5px 10px;
    color: #E0E0E0;
    border-radius: 4px;
}

QPushButton:hover {
    background-color: #3C3C3C;
}

QPushButton:pressed {
    background-color: #4C4C4C;
}

QPushButton:disabled {
    color: #666666;
    border-color: #333333;
}

/* QLabel */
QLabel {
    color: #E0E0E0;
    background: transparent;
}

/* QLineEdit */
QLineEdit {
    background-color: #2C2C2C;
    border: 1px solid #444444;
    padding: 4px;
    color: #E0E0E0;
    border-radius: 4px;
    selection-background-color: #4A6B8A;
}

QLineEdit:focus {
    border: 1px solid #5A5A5A;
}

/* QSpinBox and QDoubleSpinBox */
QSpinBox, QDoubleSpinBox {
    background-color: #2C2C2C;
    border: 1px solid #444444;
    padding: 2px;
    color: #E0E0E0;
    border-radius: 4px;
}

QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    background-color: #3C3C3C;
    border: none;
    width: 16px;
    height: 16px;
}

QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: #4C4C4C;
}

/* QComboBox */
QComboBox {
    background-color: #2C2C2C;
    border: 1px solid #444444;
    padding: 4px;
    color: #E0E0E0;
    border-radius: 4px;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 20px;
}

QComboBox::down-arrow {
    width: 10px;
    height: 10px;
}

QComboBox QAbstractItemView {
    background-color: #1E1E1E;
    color: #E0E0E0;
    selection-background-color: #333333;
}

/* QCheckBox / QRadioButton */
QCheckBox, QRadioButton {
    color: #E0E0E0;
    background: transparent;
    spacing: 6px;
}

QCheckBox::indicator, QRadioButton::indicator {
    width: 14px;
    height: 14px;
    border: 1px solid #555555;
    border-radius: 3px;
    background-color: #2C2C2C;
}

QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background-color: #4A8AF4;
    border-color: #4A8AF4;
}

/* QGroupBox */
QGroupBox {
    border: 1px solid #333333;
    border-radius: 4px;
    margin-top: 10px;
    color: #E0E0E0;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0px 4px;
}

/* QTextEdit */
QTextEdit, QPlainTextEdit {
    background-color: #1E1E1E;
    border: 1px solid #444444;
    color: #E0E0E0;
    padding: 5px;
    border-radius: 4px;
}

/* Item views */
QListWidget, QListView, QTreeView, QTableView, QTableWidget {
    background-color: #1E1E1E;
    border: 1px solid #444444;
    color: #E0E0E0;
    padding: 5px;
    border-radius: 4px;
    alternate-background-color: #232323;
}

QListWidget::item {
    background-color: #2C2C2C;
    margin: 2px;
    padding: 5px;
    border-radius: 4px;
}

QListWidget::item:selected {
    background-color: #4A6B8A;
    color: #FFFFFF;
}

QHeaderView::section {
    background-color: #2C2C2C;
    color: #E0E0E0;
    border: 1px solid #444444;
    padding: 4px;
}

/* QTabWidget */
QTabWidget::pane {
    border: 1px solid #444444;
}

QTabBar::tab {
    background: #2C2C2C;
    color: #E0E0E0;
    padding: 8px 16px;
    border: 1px solid #444444;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    margin-right: 2px;
}

QTabBar::tab:selected, QTabBar::tab:hover {
    background: #3C3C3C;
}

/* QSlider */
QSlider::groove:horizontal {
    height: 8px;
    background: #444444;
    border-radius: 4px;
}

QSlider::handle:horizontal {
    background: #888888;
    border: 1px solid #555555;
    width: 14px;
    height: 14px;
    margin: -3px 0;
    border-radius: 7px;
}

QSlider::handle:horizontal:hover {
    background: #AAAAAA;
}

/* QScrollBar */
QScrollBar:vertical {
    background: #1E1E1E;
    width: 12px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background: #444444;
    min-height: 24px;
    border-radius: 6px;
}

QScrollBar::handle:vertical:hover {
    background: #555555;
}

QScrollBar:horizontal {
    background: #1E1E1E;
    height: 12px;
    margin: 0;
}

QScrollBar::handle:horizontal {
    background: #444444;
    min-width: 24px;
    border-radius: 6px;
}

QScrollBar::handle:horizontal:hover {
    background: #555555;
}

QScrollBar::add-line, QScrollBar::sub-line {
    width: 0;
    height: 0;
}

QScrollBar::add-page, QScrollBar::sub-page {
    background: none;
}

QScrollArea {
    border: none;
}

/* QDockWidget */
QDockWidget {
    background-color: #1E1E1E;
    color: #E0E0E0;
    border: 1px solid #444444;
    titlebar-close-icon: none;
}

QDockWidget::title {
    background-color: #2C2C2C;
    color: #E0E0E0;
    border-bottom: 1px solid #444444;
    padding: 5px;
}

QDockWidget::close-button:hover, QDockWidget::float-button:hover {
    background-color: #555555;
}

/* QProgressBar / QProgressDialog */
QProgressBar {
    background-color: #2C2C2C;
    border: 1px solid #444444;
    border-radius: 4px;
    text-align: center;
    color: #E0E0E0;
}

QProgressBar::chunk {
    background-color: #4A8AF4;
    border-radius: 3px;
}

/* QSplitter */
QSplitter::handle {
    background-color: #1E1E1E;
}

QSplitter::handle:hover {
    background-color: #333333;
}
"""

light_qss = """
/* General */
QMainWindow, QDialog, QWizard {
    background-color: #F5F5F5;
    color: #202020;
}

QWidget {
    background-color: #F5F5F5;
    color: #202020;
    font-family: "Segoe UI", sans-serif;
    font-size: 10pt;
}

QToolTip {
    background-color: #FFFFFF;
    color: #202020;
    border: 1px solid #B0B0B0;
    padding: 3px;
}

QMenuBar {
    background-color: #EAEAEA;
    color: #202020;
}

QMenuBar::item {
    background: transparent;
    padding: 4px 12px;
}

QMenuBar::item:selected {
    background-color: #D5D5D5;
}

QMenu {
    background-color: #FFFFFF;
    color: #202020;
    border: 1px solid #C0C0C0;
}

QMenu::item:selected {
    background-color: #D8E6F5;
}

QToolBar {
    background-color: #EAEAEA;
    border-bottom: 1px solid #C8C8C8;
    spacing: 4px;
    padding: 2px;
}

QToolButton {
    background-color: transparent;
    color: #202020;
    padding: 4px 8px;
    border-radius: 4px;
}

QToolButton:hover {
    background-color: #D5D5D5;
}

QStatusBar {
    background-color: #EAEAEA;
    color: #202020;
    border-top: 1px solid #C8C8C8;
}

QPushButton {
    background-color: #FFFFFF;
    border: 1px solid #B8B8B8;
    padding: 5px 10px;
    color: #202020;
    border-radius: 4px;
}

QPushButton:hover {
    background-color: #E8F0FA;
}

QPushButton:pressed {
    background-color: #D0E0F0;
}

QLabel {
    color: #202020;
    background: transparent;
}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #FFFFFF;
    border: 1px solid #B8B8B8;
    padding: 4px;
    color: #202020;
    border-radius: 4px;
}

QComboBox QAbstractItemView {
    background-color: #FFFFFF;
    color: #202020;
    selection-background-color: #D8E6F5;
}

QGroupBox {
    border: 1px solid #C8C8C8;
    border-radius: 4px;
    margin-top: 10px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0px 4px;
}

QTextEdit, QPlainTextEdit {
    background-color: #FFFFFF;
    border: 1px solid #B8B8B8;
    color: #202020;
    padding: 5px;
    border-radius: 4px;
}

QListWidget, QListView, QTreeView, QTableView, QTableWidget {
    background-color: #FFFFFF;
    border: 1px solid #B8B8B8;
    color: #202020;
    padding: 5px;
    border-radius: 4px;
}

QListWidget::item {
    margin: 2px;
    padding: 5px;
    border-radius: 4px;
}

QListWidget::item:selected {
    background-color: #D8E6F5;
    color: #202020;
}

QTabWidget::pane {
    border: 1px solid #C0C0C0;
}

QTabBar::tab {
    background: #EAEAEA;
    color: #202020;
    padding: 8px 16px;
    border: 1px solid #C0C0C0;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    margin-right: 2px;
}

QTabBar::tab:selected, QTabBar::tab:hover {
    background: #FFFFFF;
}

QDockWidget {
    background-color: #F0F0F0;
    color: #202020;
}

QDockWidget::title {
    background-color: #E0E0E0;
    border-bottom: 1px solid #C0C0C0;
    padding: 5px;
}

QProgressBar {
    background-color: #FFFFFF;
    border: 1px solid #B8B8B8;
    border-radius: 4px;
    text-align: center;
}

QProgressBar::chunk {
    background-color: #4A8AF4;
    border-radius: 3px;
}
"""

THEMES = {
    "dark": dark_qss,
    "light": light_qss,
}


def get_stylesheet(theme: str = "dark") -> str:
    """Return the stylesheet for the given theme name (falls back to dark)."""
    return THEMES.get(str(theme).lower(), dark_qss)


# Backward-compatible default stylesheet.
qss = dark_qss
