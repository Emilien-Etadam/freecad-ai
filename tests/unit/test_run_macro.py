from unittest.mock import patch
from freecad_ai.core.executor import ExecutionResult
from freecad_ai.tools import freecad_tools


class _DM:
    def __init__(self, active):
        self.active = active


def test_run_macro_refuses_path_in_safe_mode(tmp_path):
    with patch.object(freecad_tools, "_macro_allowed_dirs", return_value=[str(tmp_path)]), \
         patch("freecad_ai.core.dangerous_mode.get_dangerous_mode", return_value=_DM(False)):
        res = freecad_tools._handle_run_macro("/etc/passwd")
    assert res.success is False
    assert "dangerous" in res.error.lower()


def test_run_macro_runs_resolved_file_in_safe_mode(tmp_path):
    (tmp_path / "Hello.FCMacro").write_text("print('ran')")
    fake = ExecutionResult(success=True, stdout="ran\n", stderr="", code="")
    with patch.object(freecad_tools, "_macro_allowed_dirs", return_value=[str(tmp_path)]), \
         patch("freecad_ai.core.dangerous_mode.get_dangerous_mode", return_value=_DM(False)), \
         patch.object(freecad_tools, "execute_code", return_value=fake) as exec_mock:
        res = freecad_tools._handle_run_macro("Hello")
    assert res.success is True
    assert "ran" in res.output
    assert exec_mock.call_args.kwargs.get("skip_safety") is False


def test_run_macro_dangerous_passes_skip_safety(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("print('x')")
    fake = ExecutionResult(success=True, stdout="x\n", stderr="", code="")
    with patch.object(freecad_tools, "_macro_allowed_dirs", return_value=[]), \
         patch("freecad_ai.core.dangerous_mode.get_dangerous_mode", return_value=_DM(True)), \
         patch.object(freecad_tools, "execute_code", return_value=fake) as exec_mock:
        res = freecad_tools._handle_run_macro(str(f))
    assert res.success is True
    assert exec_mock.call_args.kwargs.get("skip_safety") is True
