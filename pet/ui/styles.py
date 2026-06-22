"""QSS 样式库 —— 扁平化圆角风格。"""

import os

# ── 资源路径 ──

_PROJECT_ROOT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..",
)
ICON_PATH = os.path.join(_PROJECT_ROOT, "assets", "icon", "sys_tray.png")
SETTING_ICON_PATH = os.path.join(_PROJECT_ROOT, "assets", "icon", "setting.png")
SHOW_ICON_PATH = os.path.join(_PROJECT_ROOT, "assets", "icon", "show.png")
HIDE_ICON_PATH = os.path.join(_PROJECT_ROOT, "assets", "icon", "hide.png")
CHECKMARK_SVG = os.path.join(_PROJECT_ROOT, "assets", "icon", "checkmark.svg").replace("\\", "/")

# ── 色彩 ──

_COLOR_BG         = "#f5f2ed"
_COLOR_SURFACE     = "#ffffff"
_COLOR_BORDER      = "#ddd"
_COLOR_BORDER_DARK = "#ccc"
_COLOR_BORDER_FOCUS = "#aaa"
_COLOR_TEXT        = "#333"
_COLOR_TEXT_TITLE  = "#444"
_COLOR_TEXT_SEC    = "#666"
_COLOR_TEXT_MUTED  = "#999"
_COLOR_ACCENT      = "#4a90d9"
_COLOR_DANGER      = "#e81123"
_COLOR_WARNING     = "#e67e22"
_COLOR_HOVER_BG   = "#e0e0e0"

# ── 窗口 / 根 ──

WINDOW_QSS = """
QWidget#FlatWindow {
    background: """ + _COLOR_BG + """;
}
"""

# ── 面板 ──

PANEL_QSS = """
QGroupBox {
    font-size: 12px;
    font-weight: bold;
    color: """ + _COLOR_TEXT_SEC + """;
    background: transparent;
    border: 1px solid """ + _COLOR_BORDER + """;
    border-radius: 8px;
    margin-top: 10px;
    padding: 14px 8px 8px 8px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    color: """ + _COLOR_TEXT_SEC + """;
}
"""

# ── 按钮通用 ──

_BTN_BASE = """
    background: """ + _COLOR_SURFACE + """;
    border: 1px solid """ + _COLOR_BORDER + """;
    border-radius: 6px;
    padding: 4px 12px;
    font-size: 12px;
    color: """ + _COLOR_TEXT + """;
"""

BUTTON_QSS = """
QPushButton {
""" + _BTN_BASE + """
}
QPushButton:hover {
    background: #e8e8e8;
    border-color: """ + _COLOR_BORDER_FOCUS + """;
}
QPushButton:pressed {
    background: #d8d8d8;
}
QPushButton:checked {
    background: """ + _COLOR_ACCENT + """;
    color: #fff;
    border-color: """ + _COLOR_ACCENT + """;
}
QPushButton:disabled {
    background: #f0f0f0;
    color: #bbb;
    border-color: #e0e0e0;
}
"""

BUTTON_PRIMARY_QSS = """
QPushButton {
""" + _BTN_BASE + """
    background: """ + _COLOR_ACCENT + """;
    color: #fff;
    border-color: """ + _COLOR_ACCENT + """;
}
QPushButton:hover {
    background: #3a7bc8;
}
QPushButton:pressed {
    background: #2d6ab5;
}
QPushButton:disabled {
    background: #e0e0e0;
    color: #aaa;
    border-color: #d0d0d0;
}
"""

# ── 按钮 - 危险/关闭 ──

BUTTON_DANGER_QSS = """
QPushButton {
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 14px;
    color: #888;
}
QPushButton:hover {
    background: #e81123;
    color: #fff;
}
"""

# ── 输入框 ──

INPUT_QSS = """
QLineEdit, QSpinBox {
    background: """ + _COLOR_SURFACE + """;
    border: 1px solid """ + _COLOR_BORDER + """;
    border-radius: 6px;
    padding: 3px 8px;
    min-height: 20px;
    font-size: 12px;
    color: """ + _COLOR_TEXT + """;
}
QLineEdit:focus, QSpinBox:focus {
    border-color: """ + _COLOR_BORDER_FOCUS + """;
}
QLineEdit:disabled, QSpinBox:disabled {
    background: #f5f5f5;
    color: #bbb;
    border-color: #e0e0e0;
}
"""

# ── 高亮输入框（重要字段用）──

INPUT_HIGHLIGHT_QSS = """
QLineEdit {
    background: #f8fafc;
    border: 1.5px solid #d0e3ff;
    border-radius: 6px;
    padding: 5px 10px;
    font-size: 12px;
    color: """ + _COLOR_TEXT + """;
}
QLineEdit:focus {
    border-color: """ + _COLOR_ACCENT + """;
    background: """ + _COLOR_SURFACE + """;
}
QLineEdit:disabled {
    background: #f5f5f5;
    color: #bbb;
    border-color: #e0e0e0;
}
"""

# ── 下拉框 ──

COMBOBOX_QSS = """
QComboBox {
    background: #f8fafc;
    border: 1.5px solid #d0e3ff;
    border-radius: 6px;
    padding: 5px 10px;
    min-height: 20px;
    font-size: 12px;
    color: """ + _COLOR_TEXT + """;
    min-width: 64px;
}
QComboBox:hover {
    border-color: """ + _COLOR_BORDER_FOCUS + """;
}
QComboBox:focus {
    border-color: """ + _COLOR_ACCENT + """;
    background: """ + _COLOR_SURFACE + """;
}
QComboBox:disabled {
    background: #f5f5f5;
    color: #bbb;
    border-color: #e0e0e0;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: right center;
    width: 16px;
    border: none;
}
QComboBox QAbstractItemView {
    background: """ + _COLOR_SURFACE + """;
    border: 1px solid """ + _COLOR_BORDER + """;
    border-radius: 4px;
    selection-background-color: #e0e0e0;
    color: """ + _COLOR_TEXT + """;
    font-size: 12px;
    outline: none;
}
"""

# ── 多行文本框 ──

TEXTEDIT_QSS = """
QTextEdit, QPlainTextEdit {
    background: """ + _COLOR_SURFACE + """;
    border: 1px solid """ + _COLOR_BORDER + """;
    border-radius: 8px;
    padding: 6px 8px;
    font-family: "Consolas", "Microsoft YaHei", monospace;
    font-size: 12px;
    color: """ + _COLOR_TEXT + """;
    selection-background-color: #b3d9ff;
}
QTextEdit:focus, QPlainTextEdit:focus {
    border-color: """ + _COLOR_BORDER_FOCUS + """;
}
QTextEdit:disabled, QPlainTextEdit:disabled {
    background: #f5f5f5;
    color: #bbb;
    border-color: #e0e0e0;
}
"""

# ── 列表 ──

LIST_QSS = """
QListWidget {
    background: """ + _COLOR_SURFACE + """;
    border: 1px solid """ + _COLOR_BORDER + """;
    border-radius: 6px;
    font-family: "Consolas", "Microsoft YaHei", monospace;
    font-size: 11px;
    color: """ + _COLOR_TEXT + """;
    outline: none;
}
QListWidget::item {
    padding: 2px 4px;
}
QListWidget::item:selected {
    background: #e0e0e0;
    color: """ + _COLOR_TEXT + """;
}
"""

# ── 菜单 ──

MENU_QSS = """
QMenu {
    background: """ + _COLOR_SURFACE + """;
    border: 1px solid """ + _COLOR_BORDER + """;
    border-radius: 8px;
    padding: 6px 4px;
}
QMenu::item {
    padding: 7px 32px 7px 14px;
    font-size: 13px;
    color: """ + _COLOR_TEXT + """;
    border-radius: 4px;
    background: transparent;
}
QMenu::item:selected {
    background: #e8e8e8;
}
QMenu::item:disabled {
    color: #bbb;
}
QMenu::separator {
    height: 1px;
    background: """ + _COLOR_BORDER + """;
    margin: 4px 10px;
}
"""

# ── 复选框 ──

CHECKBOX_QSS = """
QCheckBox {
    font-size: 12px;
    color: """ + _COLOR_TEXT + """;
    spacing: 6px;
}
QCheckBox:disabled {
    color: #bbb;
}
QCheckBox::indicator {
    width: 14px;
    height: 14px;
    margin: 3px 0;
    background: """ + _COLOR_SURFACE + """;
    border: 1px solid """ + _COLOR_BORDER_FOCUS + """;
    border-radius: 3px;
}
QCheckBox::indicator:disabled {
    background: #f5f5f5;
    border-color: #e0e0e0;
}
QCheckBox::indicator:checked {
    background: """ + _COLOR_SURFACE + """;
    image: url(""" + CHECKMARK_SVG + """);
    border-color: """ + _COLOR_ACCENT + """;
    border-width: 2px;
}
QCheckBox::indicator:hover {
    border-color: """ + _COLOR_ACCENT + """;
}
"""

# ── 标签 ──

LABEL_SECONDARY_QSS = """
    font-size: 12px;
    color: """ + _COLOR_TEXT_SEC + """;
"""

LABEL_MONO_QSS = """
    font-family: "Consolas", "Microsoft YaHei", monospace;
    font-size: 12px;
    color: """ + _COLOR_TEXT + """;
"""

# ── 水平分隔线 ──

SEPARATOR_QSS = """
QFrame[frameShape="4"] {
    color: """ + _COLOR_BORDER + """;
}
"""

# ── Tab Bar（设置界面用）

TAB_BAR_QSS = """
QTabWidget::pane {
    border: 1px solid """ + _COLOR_BORDER + """;
    border-radius: 6px;
    background: """ + _COLOR_SURFACE + """;
    padding: 4px;
    top: -1px;
}
QTabBar::tab {
    background: """ + _COLOR_BG + """;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 8px 0;
    font-size: 12px;
    color: """ + _COLOR_TEXT_SEC + """;
}
QTabBar::tab:selected {
    background: """ + _COLOR_BG + """;
    color: """ + _COLOR_TEXT + """;
    font-weight: bold;
    border-bottom: 2px solid """ + _COLOR_ACCENT + """;
}
QTabBar::tab:hover:!selected {
    background: #e8e8e8;
}
"""
