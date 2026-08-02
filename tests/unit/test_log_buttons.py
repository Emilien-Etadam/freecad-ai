from pathlib import Path
_UI = Path("/home/user/freecad-ai/freecad_ai/ui")

def test_buttons_built():
    src = (_UI / "chat_dock" / "ui.py").read_text()
    assert 'QPushButton(translate("ChatDockWidget", "Open Log"))' in src
    assert 'QPushButton(translate("ChatDockWidget", "Copy Log"))' in src
    assert "open_log_btn.clicked.connect(self._open_session_log)" in src
    assert "copy_log_btn.clicked.connect(self._copy_session_log)" in src
    # both must join the theme-conflict list like their siblings
    conflict = src.split("_theme_ui_conflict_buttons = [")[1].split("]")[0]
    assert "open_log_btn" in conflict and "copy_log_btn" in conflict

def test_handlers_exist_and_fall_back():
    src = (_UI / "chat_dock" / "session.py").read_text()
    for name in ("_open_session_log", "_copy_session_log", "_latest_log_path"):
        assert f"def {name}(self" in src, name
    body = src.split("def _latest_log_path")[1].split("\n    def ")[0]
    assert "os.listdir(LOGS_DIR)" in body   # works after a restart
    assert "getmtime" in body               # newest wins
    save = src.split("def _save_session_log")[1].split("\n    def ")[0]
    assert "self._last_log_path = filepath" in save

def test_open_uses_qt_desktop_services():
    src = (_UI / "chat_dock" / "session.py").read_text()
    body = src.split("def _open_session_log")[1].split("\n    def ")[0]
    assert "QDesktopServices.openUrl" in body   # cross-platform
    assert "Could not open the log" in body     # failure is visible

def test_copy_reads_utf8_and_reports_size():
    src = (_UI / "chat_dock" / "session.py").read_text()
    body = src.split("def _copy_session_log")[1].split("\n    def ")[0]
    assert 'encoding="utf-8"' in body
    assert "clipboard().setText(content)" in body
    assert "characters" in body
