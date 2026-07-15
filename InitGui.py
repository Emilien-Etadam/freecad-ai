"""FreeCAD AI Workbench — GUI initialization."""

import FreeCADGui as Gui
import FreeCAD as App


class FreeCADAIWorkbench(Gui.Workbench):
    """AI assistant workbench for FreeCAD."""

    # FreeCAD auto-translates MenuText/ToolTip using class name as context.
    # The .qm file provides translations under the "FreeCADAIWorkbench" context.
    MenuText = "FreeCAD AI"
    ToolTip = "AI-powered assistant for 3D modeling"

    def __init__(self):
        from freecad_ai.paths import get_icon_path
        icon = get_icon_path()
        if icon:
            self.__class__.Icon = icon

    def Initialize(self):
        """Called when the workbench is first activated."""
        self.appendToolbar("FreeCAD AI", ["FreeCADAI_OpenChat", "FreeCADAI_OpenSettings"])
        self.appendMenu("FreeCAD AI", ["FreeCADAI_OpenChat", "FreeCADAI_OpenSettings",
                                       "FreeCADAI_ToggleKeepDock"])

    def Activated(self):
        """Called when the workbench is selected."""
        from freecad_ai.ui.chat_widget import get_chat_dock
        dock = get_chat_dock()
        if dock:
            dock.show()

    def Deactivated(self):
        """Called when leaving this workbench.

        By default the chat dock is hidden when leaving the workbench. When
        the ``keep_dock_on_workbench_switch`` setting is enabled, the dock
        stays open so the FreeCAD AI panel remains usable in other
        workbenches.
        """
        try:
            from freecad_ai.config import get_config
            if get_config().keep_dock_on_workbench_switch:
                return
        except Exception:
            pass
        from freecad_ai.ui.chat_widget import get_chat_dock
        dock = get_chat_dock(create=False)
        if dock:
            dock.hide()

    def GetClassName(self):
        return "Gui::PythonWorkbench"


class OpenChatCommand:
    """Command to open/show the AI chat panel."""

    def GetResources(self):
        from freecad_ai.paths import get_icon_path
        from freecad_ai.i18n import translate
        d = {
            "GroupName": "FreeCAD AI",
            "MenuText": translate("OpenChatCommand", "Open AI Chat"),
            "ToolTip": translate("OpenChatCommand", "Open the FreeCAD AI chat panel"),
        }
        icon = get_icon_path()
        if icon:
            d["Pixmap"] = icon
        return d

    def Activated(self, index=0):
        from freecad_ai.ui.chat_widget import get_chat_dock
        dock = get_chat_dock()
        if dock:
            dock.show()
            dock.raise_()

    def IsActive(self):
        return True


class OpenSettingsCommand:
    """Command to open the settings dialog."""

    def GetResources(self):
        from freecad_ai.i18n import translate
        return {
            "GroupName": "FreeCAD AI",
            "MenuText": translate("OpenSettingsCommand", "AI Settings"),
            "ToolTip": translate("OpenSettingsCommand", "Configure FreeCAD AI providers and options"),
        }

    def Activated(self, index=0):
        from freecad_ai.ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(Gui.getMainWindow())
        dlg.exec()

    def IsActive(self):
        return True


class ToggleKeepDockCommand:
    """Command to toggle 'keep chat panel open across workbench switches'.

    Registered as a command so it can be bound to a keyboard shortcut via
    Edit -> Preferences -> Keyboard. Flips the persisted config flag and
    syncs the dock's visibility so the keybind has an immediate effect.
    """

    def GetResources(self):
        from freecad_ai.i18n import translate
        return {
            "GroupName": "FreeCAD AI",
            "MenuText": translate("ToggleKeepDockCommand", "Keep Chat Panel Open"),
            "ToolTip": translate(
                "ToggleKeepDockCommand",
                "Toggle whether the FreeCAD AI chat panel stays open when "
                "switching to other workbenches"),
            "Checkable": True,
        }

    def Activated(self, index=0):
        from freecad_ai.config import get_config, save_current_config
        cfg = get_config()
        cfg.keep_dock_on_workbench_switch = not cfg.keep_dock_on_workbench_switch
        save_current_config()
        # Make the change visible right away: showing when turned on,
        # hiding when turned off.
        from freecad_ai.ui.chat_widget import get_chat_dock
        if cfg.keep_dock_on_workbench_switch:
            dock = get_chat_dock()
            if dock:
                dock.show()
                dock.raise_()
        else:
            dock = get_chat_dock(create=False)
            if dock:
                dock.hide()

    def IsChecked(self):
        try:
            from freecad_ai.config import get_config
            return bool(get_config().keep_dock_on_workbench_switch)
        except Exception:
            return False

    def IsActive(self):
        return True


# Register translation path early so command strings are translated
# before the workbench is activated.
try:
    from freecad_ai.paths import get_translations_path as _gtp
    _tr_path = _gtp()
    if _tr_path:
        Gui.addLanguagePath(_tr_path)
        Gui.updateLocale()
except Exception:
    pass

# Register the icons directory so FreeCAD can find preferences-freecadai.svg
# (the sidebar icon for our Edit → Preferences page).
try:
    from freecad_ai.paths import get_icons_dir as _gid
    _icons_dir = _gid()
    if _icons_dir:
        Gui.addIconPath(_icons_dir)
except Exception:
    pass

# Register the FreeCAD AI preferences page in Edit → Preferences. The
# Gui::Pref* widgets in the form auto-save to BaseApp/Preferences/Mod/FreeCADAI;
# our config layer mirrors values from there into ~/.config/FreeCAD/FreeCADAI/config.json
# on load so both this page and the workbench's Settings dialog stay in sync.
try:
    from freecad_ai.paths import get_prefs_ui_path as _gpup
    _prefs_ui = _gpup()
    if _prefs_ui:
        Gui.addPreferencePage(_prefs_ui, "FreeCAD AI")
except Exception:
    pass

# Seed the FreeCAD parameter store from JSON so the preferences page shows
# current values even when the user goes straight to Edit → Preferences
# without first activating the workbench. load_config writes JSON values to
# the param store, where the Gui::Pref* widgets read from.
try:
    from freecad_ai.config import get_config as _gcfg
    _gcfg()
except Exception:
    pass

Gui.addCommand("FreeCADAI_OpenChat", OpenChatCommand())
Gui.addCommand("FreeCADAI_OpenSettings", OpenSettingsCommand())
Gui.addCommand("FreeCADAI_ToggleKeepDock", ToggleKeepDockCommand())
Gui.addWorkbench(FreeCADAIWorkbench())
