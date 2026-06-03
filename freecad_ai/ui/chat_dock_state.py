"""Persist and restore chat dock layout."""

from .compat import QtWidgets, QtCore, QtGui
from ..i18n import translate

QDockWidget = QtWidgets.QDockWidget
QWidget = QtWidgets.QWidget
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QTextBrowser = QtWidgets.QTextBrowser
QTextEdit = QtWidgets.QTextEdit
QPushButton = QtWidgets.QPushButton
QComboBox = QtWidgets.QComboBox
QLabel = QtWidgets.QLabel
QApplication = QtWidgets.QApplication
Qt = QtCore.Qt
Signal = QtCore.Signal
QThread = QtCore.QThread
Slot = QtCore.Slot
QFont = QtGui.QFont
QTextCursor = QtGui.QTextCursor

from ..config import get_config

def _area_to_str(area):
    """Convert Qt.DockWidgetArea to a JSON-friendly string."""
    mapping = {
        Qt.LeftDockWidgetArea: "left",
        Qt.RightDockWidgetArea: "right",
        Qt.TopDockWidgetArea: "top",
        Qt.BottomDockWidgetArea: "bottom",
    }
    return mapping.get(area, "")


def _str_to_area(s):
    """Inverse of _area_to_str. Defaults to right on unknown."""
    mapping = {
        "left": Qt.LeftDockWidgetArea,
        "right": Qt.RightDockWidgetArea,
        "top": Qt.TopDockWidgetArea,
        "bottom": Qt.BottomDockWidgetArea,
    }
    return mapping.get(s, Qt.RightDockWidgetArea)


def _apply_saved_dock_state(mw, dock):
    """Reposition dock per saved AppConfig fields.

    Must be called after mw.addDockWidget. Prefers the full mw.saveState()
    blob (captures tabification); falls back to the surgical fields if the
    blob is absent or restoreState rejects it.
    """
    cfg = get_config()

    restored_via_state = False
    if cfg.chat_dock_mw_state:
        try:
            import base64
            raw = base64.b64decode(cfg.chat_dock_mw_state.encode("ascii"))
            ba = QtCore.QByteArray(raw)
            restored_via_state = bool(mw.restoreState(ba))
        except Exception:
            restored_via_state = False

    if restored_via_state:
        # Qt handled area, tabification, and splitter sizes. Apply floating
        # geometry only — restoreState sometimes loses the exact window rect
        # for floating docks.
        if cfg.chat_dock_floating and len(cfg.chat_dock_geometry) == 4:
            try:
                dock.setFloating(True)
                x, y, w, h = cfg.chat_dock_geometry
                dock.setGeometry(int(x), int(y), int(w), int(h))
            except Exception:
                pass
        return

    # Fallback path: surgical fields used only when mw.saveState blob is
    # absent (first run) or restoreState fails.
    try:
        for name in cfg.chat_dock_tabified_with or []:
            if not name:
                continue
            sibling = mw.findChild(QDockWidget, name)
            if sibling is not None and sibling is not dock:
                mw.tabifyDockWidget(sibling, dock)
        if cfg.chat_dock_floating and len(cfg.chat_dock_geometry) == 4:
            dock.setFloating(True)
            x, y, w, h = cfg.chat_dock_geometry
            dock.setGeometry(int(x), int(y), int(w), int(h))
    except Exception:
        pass
