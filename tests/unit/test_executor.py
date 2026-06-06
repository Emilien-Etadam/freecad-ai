"""Tests for code execution engine — extract, validate, and safety checks."""

import pytest

from unittest.mock import patch

from freecad_ai.core import executor
from freecad_ai.core.executor import (
    ExecutionResult,
    extract_code_blocks,
    validate_code,
    _validate_code,
)


class TestExtractCodeBlocks:
    def test_single_block(self):
        text = "Here's code:\n```python\nprint('hello')\n```\nDone."
        blocks = extract_code_blocks(text)
        assert len(blocks) == 1
        assert "print('hello')" in blocks[0]

    def test_multiple_blocks(self):
        text = (
            "First:\n```python\na = 1\n```\n"
            "Second:\n```python\nb = 2\n```\n"
        )
        blocks = extract_code_blocks(text)
        assert len(blocks) == 2

    def test_no_blocks(self):
        text = "No code here, just text."
        blocks = extract_code_blocks(text)
        assert blocks == []

    def test_non_python_block_ignored(self):
        text = "```javascript\nconsole.log('hi')\n```"
        blocks = extract_code_blocks(text)
        assert blocks == []

    def test_multiline_code(self):
        text = "```python\ndef foo():\n    return 42\n\nresult = foo()\n```"
        blocks = extract_code_blocks(text)
        assert len(blocks) == 1
        assert "def foo():" in blocks[0]
        assert "result = foo()" in blocks[0]

    def test_empty_block(self):
        text = "```python\n```"
        blocks = extract_code_blocks(text)
        # Empty match
        assert len(blocks) == 1
        assert blocks[0].strip() == ""

    def test_nested_backticks_in_string(self):
        text = '```python\nx = "```"\n```'
        blocks = extract_code_blocks(text)
        # Regex matches greedily but should get at least one block
        assert len(blocks) >= 1


class TestValidateCode:
    # ── Dangerous patterns ──

    def test_blocks_os_system(self):
        warnings = _validate_code("os.system('rm -rf /')")
        assert any("os.system" in w for w in warnings)

    def test_blocks_subprocess(self):
        warnings = _validate_code("import subprocess\nsubprocess.run(['ls'])")
        assert any("subprocess" in w for w in warnings)

    def test_blocks_shutil_rmtree(self):
        warnings = _validate_code("shutil.rmtree('/home')")
        assert any("shutil.rmtree" in w for w in warnings)

    def test_blocks_dynamic_os_import(self):
        warnings = _validate_code("__import__('os').system('ls')")
        assert any("Dynamic import" in w for w in warnings)

    def test_safe_code_passes(self):
        warnings = _validate_code(
            "import FreeCAD as App\n"
            "doc = App.newDocument('Test')\n"
            "box = doc.addObject('Part::Box', 'Box')\n"
        )
        assert warnings == []

    # ── Revolution crash patterns ──

    def test_blocks_revolution_with_full_circle(self):
        code = (
            "import Part\n"
            "circle = Part.Circle()\n"
            "feat = body.newObject('PartDesign::Revolution', 'Rev')\n"
        )
        warnings = _validate_code(code)
        assert any("Revolution" in w or "crash" in w.lower() for w in warnings)

    def test_allows_revolution_with_arc(self):
        code = (
            "arc = Part.ArcOfCircle(circ, 0, 3.14)\n"
            "feat = body.newObject('PartDesign::Revolution', 'Rev')\n"
        )
        warnings = _validate_code(code)
        # ArcOfCircle should NOT trigger the revolution warning
        assert not any("crash" in w.lower() for w in warnings)

    def test_blocks_360_degree_revolution(self):
        code = (
            "feat = body.newObject('PartDesign::Revolution', 'Rev')\n"
            "feat.Angle = 360\n"
        )
        warnings = _validate_code(code)
        assert any("360" in w for w in warnings)

    def test_allows_partial_revolution(self):
        code = (
            "feat = body.newObject('PartDesign::Revolution', 'Rev')\n"
            "feat.Angle = 180\n"
        )
        warnings = _validate_code(code)
        assert not any("360" in w for w in warnings)

    # ── False positive checks ──

    def test_subprocess_in_comment_still_blocked(self):
        # The validator does simple regex matching, not AST — it blocks
        # "subprocess" anywhere in code text. This is intentional.
        code = "# We could use subprocess but we don't\nsubprocess.call(['ls'])"
        warnings = _validate_code(code)
        assert any("subprocess" in w for w in warnings)

    def test_os_in_variable_name_ok(self):
        # "os_path" should NOT trigger os.system warning
        warnings = _validate_code("os_path = '/tmp/test'")
        assert warnings == []

    def test_safe_revolution_mention_in_string(self):
        # "Revolution" in a string without Part.Circle should be fine
        code = "name = 'Revolution'\nprint(name)"
        warnings = _validate_code(code)
        assert warnings == []


class TestValidateCodePublic:
    """validate_code() is the Check-button entry point — returns ExecutionResult."""

    def test_static_failure_returns_error_result(self):
        dangerous = "os" + ".system('rm -rf /')"
        result = validate_code(dangerous)
        assert isinstance(result, ExecutionResult)
        assert result.success is False
        assert "os.system" in result.stderr
        assert result.code == dangerous

    def test_static_failure_mentions_static_validation(self):
        # The stderr prefix distinguishes static from sandbox failures so the
        # UI (and the LLM, when Fix fires) knows which layer complained.
        result = validate_code("subprocess.run(['x'])")
        assert "Static validation" in result.stderr

    def test_passes_when_sandbox_unavailable(self):
        # If no FreeCAD binary is on the system, _sandbox_test returns
        # (True, "") — validate_code should surface that as a pass.
        with patch("freecad_ai.core.executor._find_freecad_cmd", return_value=""):
            with patch(
                "freecad_ai.core.active_document.get_synced_active_document",
                return_value=None,
            ):
                result = validate_code("import FreeCAD as App\ndoc = App.newDocument()")
        assert result.success is True
        assert result.stderr == ""

    def test_sandbox_failure_propagates_error(self):
        # Simulate a sandbox-detected error; validate_code should wrap it.
        with patch("freecad_ai.core.executor._sandbox_test", return_value=(False, "boom")):
            with patch(
                "freecad_ai.core.active_document.get_synced_active_document",
                return_value=None,
            ):
                result = validate_code("x = 1")
        assert result.success is False
        assert "boom" in result.stderr

    def test_returns_execution_result_shape(self):
        # The Fix button feeds last_error_result into _handle_execution_error,
        # which reads .stderr and .success — this contract must not drift.
        with patch("freecad_ai.core.executor._sandbox_test", return_value=(False, "err")):
            with patch(
                "freecad_ai.core.active_document.get_synced_active_document",
                return_value=None,
            ):
                result = validate_code("x = 1")
        assert hasattr(result, "success")
        assert hasattr(result, "stdout")
        assert hasattr(result, "stderr")
        assert hasattr(result, "code")


class TestSkipSafety:
    """skip_safety bypasses static validation, the sandbox, and the timeout, while keeping the undo transaction."""

    def test_safe_mode_blocks_dangerous_code(self):
        code = "import subprocess\nsubprocess.run(['ls'])"
        res = executor.execute_code(code, sandbox=False, skip_safety=False)
        assert res.success is False
        assert "validation failed" in res.stderr.lower()

    def test_skip_safety_bypasses_static_validation(self):
        # With skip_safety=True the static deny-list is skipped, so execution does
        # NOT short-circuit at static validation. With no active document it falls
        # through to the active-document guard — proving validation did not block.
        code = "import subprocess\nsubprocess.run(['ls'])"
        with patch(
            "freecad_ai.core.active_document.get_synced_active_document",
            return_value=None,
        ):
            res = executor.execute_code(code, sandbox=False, skip_safety=True)
        assert res.success is False
        assert "no active document" in res.stderr.lower()

    def test_validate_code_skip_safety_returns_pass(self):
        code = "import subprocess\nsubprocess.run(['ls'])"
        res = executor.validate_code(code, skip_safety=True)
        assert res.success is True


class TestSandboxTimeout:
    """The headless sandbox dry-run must get the same time budget as the
    real execution. Issue #14: execute_code() previously capped the sandbox
    at min(timeout, 15)s, so a valid-but-slow operation (e.g. scaling a
    complex shape with Shape.transformGeometry) failed the pre-check with
    "Sandbox: code timed out after 15 seconds" and never ran — even though
    the live execution would have allowed the full timeout.
    """

    @pytest.mark.parametrize("configured", [20, 30, 45])
    def test_sandbox_receives_full_configured_timeout(self, configured):
        seen = {}

        def _capture(code, timeout=15, document_path=None):
            seen["timeout"] = timeout
            return True, ""

        with patch("freecad_ai.core.executor._sandbox_test", side_effect=_capture):
            with patch(
                "freecad_ai.core.active_document.get_synced_active_document",
                return_value=None,
            ):
                executor.execute_code("x = 1", timeout=configured)

        assert seen["timeout"] == configured, (
            "sandbox dry-run was throttled below the configured execution "
            "timeout — slow-but-valid code will falsely time out"
        )


class TestConfigurableExecutionTimeout:
    """Issue #14 (reopened): the execution timeout was hardcoded at 30s with no
    user override, so heavy-but-valid operations — scaling a detailed model via
    Shape.transformGeometry, whose cost is O(geometry complexity) — exceeded 30s
    and failed on BOTH the sandbox dry-run and the live SIGALRM path. The timeout
    is now sourced from AppConfig.execution_timeout (default 60) whenever the
    caller passes no explicit timeout, so users can raise it for big models.
    """

    def _captured_timeout(self, configured):
        from freecad_ai.config import AppConfig

        seen = {}

        def _capture(code, timeout=15, document_path=None):
            seen["timeout"] = timeout
            return True, ""

        cfg = AppConfig()
        if configured is not None:
            cfg.execution_timeout = configured

        with patch("freecad_ai.config.get_config", return_value=cfg):
            with patch(
                "freecad_ai.core.executor._sandbox_test", side_effect=_capture
            ):
                with patch(
                    "freecad_ai.core.active_document.get_synced_active_document",
                    return_value=None,
                ):
                    executor.execute_code("x = 1")  # no explicit timeout
        return seen["timeout"]

    def test_default_execution_timeout_is_60(self):
        assert self._captured_timeout(None) == 60, (
            "execute_code() with no explicit timeout must use the bumped "
            "60s default, not the old hardcoded 30s"
        )

    def test_configured_execution_timeout_is_honored(self):
        assert self._captured_timeout(120) == 120, (
            "execute_code() must source its timeout from "
            "AppConfig.execution_timeout when the caller passes none"
        )


class TestCollectObjectIssues:
    """Post-execution validation must blame the code only for shapes it
    created or newly broke — never for objects that were already invalid
    before the code ran.

    Issue: an STL imported and converted to a solid yields an OCC-invalid
    Part::Feature. The sandbox opens a copy of the saved document, so that
    pre-existing invalid solid is present on every dry-run. The validator
    used to walk *all* objects and report it, failing code (e.g. a sketch on
    a selected face) that never touched the solid — sending the model to
    chase a phantom bug across all retries.
    """

    def test_preexisting_invalid_shape_is_suppressed(self):
        # The imported mesh→solid was already invalid before the code ran.
        objects_state = [
            {"name": "roundedBox_solid", "null": False,
             "invalid": True, "invalid_state": False},
        ]
        baseline_bad = {"roundedBox_solid"}
        issues = executor._collect_object_issues(objects_state, baseline_bad)
        assert issues == [], (
            "code that never touched a pre-existing invalid object must not "
            "be blamed for it"
        )

    def test_newly_created_invalid_object_is_reported(self):
        # A brand-new object the code created has a broken shape — its fault.
        objects_state = [
            {"name": "roundedBox_solid", "null": False,
             "invalid": True, "invalid_state": False},
            {"name": "SnapFitBox", "null": False,
             "invalid": True, "invalid_state": False},
        ]
        baseline_bad = {"roundedBox_solid"}
        issues = executor._collect_object_issues(objects_state, baseline_bad)
        assert issues == ["Object 'SnapFitBox' has invalid shape"]

    def test_object_newly_broken_by_code_is_reported(self):
        # Object existed and was fine before; the code broke it.
        objects_state = [
            {"name": "Pad", "null": False,
             "invalid": True, "invalid_state": False},
        ]
        baseline_bad = set()  # Pad was valid before the code ran
        issues = executor._collect_object_issues(objects_state, baseline_bad)
        assert issues == ["Object 'Pad' has invalid shape"]

    def test_null_shape_on_new_object_is_reported(self):
        objects_state = [
            {"name": "Pocket", "null": True,
             "invalid": False, "invalid_state": False},
        ]
        issues = executor._collect_object_issues(objects_state, set())
        assert issues == ["Object 'Pocket' has null shape"]

    def test_invalid_state_on_new_object_is_reported(self):
        objects_state = [
            {"name": "Sketch", "null": False,
             "invalid": False, "invalid_state": True},
        ]
        issues = executor._collect_object_issues(objects_state, set())
        assert issues == ["Object 'Sketch' is in Invalid state"]

    def test_valid_object_never_reported(self):
        objects_state = [
            {"name": "Box", "null": False,
             "invalid": False, "invalid_state": False},
        ]
        issues = executor._collect_object_issues(objects_state, set())
        assert issues == []
