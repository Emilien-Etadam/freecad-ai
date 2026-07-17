"""Dock layout persistence and shutdown hooks."""
from ..compat import QtWidgets, QtCore, QtGui
from ...config import get_config, save_current_config
from ..chat_dock_state import _area_to_str

Qt = QtCore.Qt
QDockWidget = QtWidgets.QDockWidget


class ChatDockLayoutMixin:
    """Save/restore dock geometry; detect main-window close."""

    def _mark_shutdown(self):
        # Called from the main-window Close event filter — not a closeEvent
        # override, so there is no event to forward.
        self._shutting_down = True
        t = getattr(self, "_dock_poll_timer", None)
        if t is not None:
            try:
                t.stop()
            except Exception:
                pass

    def _get_main_window(self):
        """Resolve the QMainWindow. self.parent() returns None when floating."""
        try:
            import FreeCADGui as Gui
            mw = Gui.getMainWindow()
            if mw is not None:
                return mw
        except Exception:
            pass
        return self.parent()

    def _save_dock_state(self, *_):
        """Snapshot dock layout so get_chat_dock can restore it next startup.

        FreeCAD restores main-window state before our workbench activates,
        which means our dock misses the restore. Saving our own state here
        and reapplying on creation is the workaround.
        """
        if getattr(self, "_shutting_down", False):
            return
        if not getattr(self, "_saves_enabled", False):
            return
        try:
            import base64
            cfg = get_config()
            prev_area = cfg.chat_dock_area
            prev_floating = cfg.chat_dock_floating
            prev_tabified = list(cfg.chat_dock_tabified_with or [])
            prev_state = cfg.chat_dock_mw_state

            cfg.chat_dock_floating = bool(self.isFloating())
            if self.isFloating():
                g = self.geometry()
                cfg.chat_dock_geometry = [g.x(), g.y(), g.width(), g.height()]

            mw = self._get_main_window()
            area = None
            if mw is not None and hasattr(mw, "dockWidgetArea"):
                try:
                    area = mw.dockWidgetArea(self)
                except Exception:
                    area = None
            cfg.chat_dock_area = _area_to_str(area) or cfg.chat_dock_area

            tabified = []
            if mw is not None and hasattr(mw, "tabifiedDockWidgets"):
                try:
                    for s in mw.tabifiedDockWidgets(self) or []:
                        n = s.objectName()
                        if n:
                            tabified.append(n)
                except Exception:
                    pass
            cfg.chat_dock_tabified_with = tabified

            new_state = prev_state
            if mw is not None and hasattr(mw, "saveState"):
                try:
                    raw = bytes(mw.saveState())
                    new_state = base64.b64encode(raw).decode("ascii")
                    cfg.chat_dock_mw_state = new_state
                except Exception:
                    pass

            changed = (
                prev_area != cfg.chat_dock_area
                or prev_floating != cfg.chat_dock_floating
                or prev_tabified != tabified
                or prev_state != new_state
            )
            if changed:
                save_current_config()
        except Exception:
            pass

    def moveEvent(self, event):
        super().moveEvent(event)
        timer = getattr(self, "_dock_save_timer", None)
        if timer is not None:
            timer.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        timer = getattr(self, "_dock_save_timer", None)
        if timer is not None:
            timer.start()

    def closeEvent(self, event):
        """Save conversation, dock layout, and disconnect MCP when widget is closed."""
        if self.conversation.messages:
            self.conversation.save()
        # Snapshot final dock layout — dockLocationChanged/topLevelChanged
        # don't always fire for tabify-by-drag, so closeEvent is our backstop.
        self._save_dock_state()
        # Disconnect MCP servers
        if self._mcp_connected:
            try:
                from ...mcp.manager import get_mcp_manager
                get_mcp_manager().disconnect_all()
            except Exception:
                pass
