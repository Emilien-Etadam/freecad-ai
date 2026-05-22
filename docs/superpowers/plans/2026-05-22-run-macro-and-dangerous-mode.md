# run_macro, Configurable Loop & Dangerous Mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `run_macro` tool that runs existing FreeCAD macro files through the existing execution pipeline, make the agentic loop count user-configurable (0 = endless), add a Stop button that interrupts the loop, and add a session-scoped "Dangerous mode" escape hatch that relaxes executor safety.

**Architecture:** Keep all decision logic in small Qt-free / FreeCAD-free helper modules (`core/dangerous_mode.py`, `core/loop_control.py`, `tools/macro_runner.py`) so it is fully unit-testable. The executor gains a `skip_safety` flag; tool handlers read the dangerous-mode singleton and pass it through. The Qt UI (chat dock, settings dialog) becomes a thin shell over these tested cores.

**Tech Stack:** Python 3.11, PySide6 (via `freecad_ai/ui/compat.py`), pytest. FreeCAD APIs only at the edges.

**Spec:** `docs/superpowers/specs/2026-05-22-run-macro-and-dangerous-mode-design.md`

**Conventions:**
- Run unit tests with: `env -u PYTHONPATH .venv/bin/python -m pytest <path> -v` (the `-u PYTHONPATH` avoids the dist-packages leak that breaks pluggy — see `reference_pythonpath_leak`).
- Every commit message ends with the trailer:
  `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`
- Qt enums use the flat form (e.g. `QMessageBox.Warning`) for PySide2/6 compat.

---

## File Structure

**Create:**
- `freecad_ai/core/dangerous_mode.py` — `DangerousMode` state holder + `get_dangerous_mode()` singleton. Source of truth for "is dangerous mode active right now".
- `freecad_ai/core/loop_control.py` — pure `should_continue_loop(max_turns, turn, interrupted)` helper.
- `freecad_ai/tools/macro_runner.py` — pure `resolve_macro_path(...)` file-resolution helper.
- `tests/unit/test_dangerous_mode.py`
- `tests/unit/test_loop_control.py`
- `tests/unit/test_macro_runner.py`

**Modify:**
- `freecad_ai/config.py` — add `max_tool_turns` and `dangerous_skip_safety` fields.
- `freecad_ai/core/executor.py` — add `skip_safety` param to `execute_code()` and `validate_code()`.
- `freecad_ai/tools/freecad_tools.py` — add `run_macro` tool + handler + `_macro_allowed_dirs()`; wire `execute_code` handler to dangerous mode; register in `ALL_TOOLS`.
- `freecad_ai/ui/chat_widget.py` — read `max_tool_turns` from config, use `should_continue_loop`, add interruption checkpoints, morph send button into Stop, wire dangerous-mode banner.
- `freecad_ai/ui/settings_dialog.py` — `max_tool_turns` spinbox + dangerous-mode session toggle with confirmation dialog.
- `freecad_ai/ui/code_review_dialog.py` — `_check` passes dangerous flag to `validate_code`.
- `tests/unit/test_config.py` — new-field tests.
- `tests/unit/test_executor.py` — `skip_safety` tests.
- `README.md`, `CHANGELOG.md` — docs + the BIG warning.

---

## Task 1: Config fields

**Files:**
- Modify: `freecad_ai/config.py:398` (near `max_retries`) and `:407` (near `scan_freecad_macros`)
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_config.py`:

```python
def test_max_tool_turns_default():
    from freecad_ai.config import AppConfig
    assert AppConfig().max_tool_turns == 30


def test_dangerous_skip_safety_default():
    from freecad_ai.config import AppConfig
    assert AppConfig().dangerous_skip_safety is False


def test_new_fields_roundtrip():
    from freecad_ai.config import AppConfig
    cfg = AppConfig(max_tool_turns=0, dangerous_skip_safety=True)
    restored = AppConfig.from_dict(cfg.to_dict())
    assert restored.max_tool_turns == 0
    assert restored.dangerous_skip_safety is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_config.py -v -k "max_tool_turns or dangerous or new_fields"`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'max_tool_turns'` / `AttributeError`.

- [ ] **Step 3: Add the fields**

In `freecad_ai/config.py`, inside `class AppConfig`, add after `max_retries: int = 3` (line 398):

```python
    # Max agentic tool-loop turns per user message. 0 = endless (the Stop
    # button is then the only brake). Default 30 preserves prior behavior.
    max_tool_turns: int = 30
```

And add after `scan_freecad_macros: bool = False` (line 407):

```python
    # Dangerous mode: relaxes executor safety layers (static pattern blocking,
    # headless sandbox pre-check, execution timeout) and widens run_macro's
    # file-resolution reach to arbitrary paths. The GUI never writes True here;
    # persistence is only via hand-editing config.json. Honored on load.
    dangerous_skip_safety: bool = False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_config.py -v -k "max_tool_turns or dangerous or new_fields"`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/config.py tests/unit/test_config.py
git commit -m "feat(config): add max_tool_turns and dangerous_skip_safety fields (#13)"
```

---

## Task 2: DangerousMode state holder

**Files:**
- Create: `freecad_ai/core/dangerous_mode.py`
- Test: `tests/unit/test_dangerous_mode.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_dangerous_mode.py`:

```python
from freecad_ai.config import AppConfig
from freecad_ai.core.dangerous_mode import DangerousMode, get_dangerous_mode


def test_inactive_by_default(monkeypatch):
    monkeypatch.setattr("freecad_ai.config.get_config", lambda: AppConfig())
    assert DangerousMode().active is False


def test_arm_disarm_session(monkeypatch):
    monkeypatch.setattr("freecad_ai.config.get_config", lambda: AppConfig())
    dm = DangerousMode()
    dm.arm()
    assert dm.active is True
    dm.disarm()
    assert dm.active is False


def test_persisted_flag_honored(monkeypatch):
    cfg = AppConfig(dangerous_skip_safety=True)
    monkeypatch.setattr("freecad_ai.config.get_config", lambda: cfg)
    dm = DangerousMode()
    assert dm.active is True          # persisted hand-edit, no session arm
    assert dm.persisted is True


def test_arming_does_not_mutate_config(monkeypatch):
    cfg = AppConfig()
    monkeypatch.setattr("freecad_ai.config.get_config", lambda: cfg)
    dm = DangerousMode()
    dm.arm()
    assert cfg.dangerous_skip_safety is False  # session arm never persists


def test_singleton_identity():
    assert get_dangerous_mode() is get_dangerous_mode()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_dangerous_mode.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'freecad_ai.core.dangerous_mode'`.

- [ ] **Step 3: Write the implementation**

Create `freecad_ai/core/dangerous_mode.py`:

```python
"""Dangerous-mode state for FreeCAD AI.

Dangerous mode relaxes the executor's safety layers and widens run_macro's
file-resolution reach. It can be armed for the current session (in-memory,
never persisted) or persisted by hand-editing ``dangerous_skip_safety: true``
in config.json. ``active`` is the single source of truth consulted at the
execution edges.
"""


class DangerousMode:
    def __init__(self):
        self._session_armed = False

    @property
    def persisted(self) -> bool:
        """True if config.json has dangerous_skip_safety set (hand-edited)."""
        from ..config import get_config
        return bool(getattr(get_config(), "dangerous_skip_safety", False))

    @property
    def active(self) -> bool:
        """True if dangerous mode is in effect (session-armed OR persisted)."""
        return self._session_armed or self.persisted

    def arm(self) -> None:
        """Arm for the current session only. Never touches config."""
        self._session_armed = True

    def disarm(self) -> None:
        self._session_armed = False


_INSTANCE = None


def get_dangerous_mode() -> DangerousMode:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = DangerousMode()
    return _INSTANCE
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_dangerous_mode.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/core/dangerous_mode.py tests/unit/test_dangerous_mode.py
git commit -m "feat(core): add DangerousMode session/persisted state holder (#13)"
```

---

## Task 3: Executor `skip_safety` parameter

**Files:**
- Modify: `freecad_ai/core/executor.py:258` (`execute_code`) and `:403` (`validate_code`)
- Test: `tests/unit/test_executor.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_executor.py`:

```python
from unittest.mock import patch
from freecad_ai.core import executor


def test_safe_mode_blocks_dangerous_code():
    code = "import subprocess\nsubprocess.run(['ls'])"
    res = executor.execute_code(code, sandbox=False, skip_safety=False)
    assert res.success is False
    assert "validation failed" in res.stderr.lower()


def test_skip_safety_bypasses_static_validation():
    # With skip_safety=True the static deny-list is skipped, so execution does
    # NOT short-circuit at layer 1. With no active document it falls through to
    # the active-document guard — proving validation did not block.
    code = "import subprocess\nsubprocess.run(['ls'])"
    with patch(
        "freecad_ai.core.active_document.get_synced_active_document",
        return_value=None,
    ):
        res = executor.execute_code(code, sandbox=False, skip_safety=True)
    assert res.success is False
    assert "no active document" in res.stderr.lower()


def test_validate_code_skip_safety_returns_pass():
    code = "import subprocess\nsubprocess.run(['ls'])"
    res = executor.validate_code(code, skip_safety=True)
    assert res.success is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_executor.py -v -k "skip_safety or safe_mode_blocks"`
Expected: FAIL — `TypeError: execute_code() got an unexpected keyword argument 'skip_safety'`.

- [ ] **Step 3: Modify `execute_code`**

In `freecad_ai/core/executor.py`, change the signature (line 258):

```python
def execute_code(code: str, timeout: int = 30, sandbox: bool = True,
                 skip_safety: bool = False) -> ExecutionResult:
```

Replace the Layer 1 block (lines 270-278) with:

```python
    # Layer 1: Static validation (skipped in dangerous mode)
    if not skip_safety:
        warnings = _validate_code(code)
        if warnings:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="Pre-execution validation failed:\n" + "\n".join(warnings),
                code=code,
            )
```

Change the Layer 2 guard (line 284) from `if sandbox:` to:

```python
    if sandbox and not skip_safety:
```

Change the alarm-arming block (lines 352-359) so the timeout is skipped in dangerous mode. Replace:

```python
        _old_handler = None
        try:
            def _timeout_handler(signum, frame):
                raise TimeoutError("Code execution timed out after {} seconds".format(timeout))
            _old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(timeout)
        except (OSError, AttributeError):
            pass
```

with:

```python
        _old_handler = None
        if not skip_safety:
            try:
                def _timeout_handler(signum, frame):
                    raise TimeoutError("Code execution timed out after {} seconds".format(timeout))
                _old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
                signal.alarm(timeout)
            except (OSError, AttributeError):
                pass
```

(The `finally` that calls `signal.alarm(0)` is safe to leave unconditional — `alarm(0)` is a no-op when no alarm was set, and `_old_handler` stays `None`.)

- [ ] **Step 4: Modify `validate_code`**

Change the signature (line 403):

```python
def validate_code(code: str, timeout: int = 15, skip_safety: bool = False) -> ExecutionResult:
```

Immediately after the docstring (before `warnings = _validate_code(code)` at line 414) add:

```python
    if skip_safety:
        return ExecutionResult(success=True, stdout="", stderr="", code=code)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_executor.py -v`
Expected: all pass (existing + 3 new).

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/core/executor.py tests/unit/test_executor.py
git commit -m "feat(executor): add skip_safety to bypass validation/sandbox/timeout (#13)"
```

---

## Task 4: Loop-control helper

**Files:**
- Create: `freecad_ai/core/loop_control.py`
- Test: `tests/unit/test_loop_control.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_loop_control.py`:

```python
from freecad_ai.core.loop_control import should_continue_loop


def test_bounded_continues_until_limit():
    assert should_continue_loop(30, 0, False) is True
    assert should_continue_loop(30, 29, False) is True
    assert should_continue_loop(30, 30, False) is False


def test_endless_always_continues():
    assert should_continue_loop(0, 0, False) is True
    assert should_continue_loop(0, 100000, False) is True


def test_interrupt_stops_regardless():
    assert should_continue_loop(30, 0, True) is False
    assert should_continue_loop(0, 0, True) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_loop_control.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

Create `freecad_ai/core/loop_control.py`:

```python
"""Pure decision helper for the agentic tool loop bound."""


def should_continue_loop(max_turns: int, turn: int, interrupted: bool) -> bool:
    """Return whether the agentic loop should run another turn.

    max_turns == 0 means endless. An interruption always stops the loop.
    """
    if interrupted:
        return False
    if max_turns == 0:
        return True
    return turn < max_turns
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_loop_control.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/core/loop_control.py tests/unit/test_loop_control.py
git commit -m "feat(core): add should_continue_loop helper for configurable/endless loop (#13)"
```

---

## Task 5: Macro path resolver

**Files:**
- Create: `freecad_ai/tools/macro_runner.py`
- Test: `tests/unit/test_macro_runner.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_macro_runner.py`:

```python
from freecad_ai.tools.macro_runner import resolve_macro_path


def test_safe_mode_resolves_fcmacro(tmp_path):
    (tmp_path / "Foo.FCMacro").write_text("print('hi')")
    path, err = resolve_macro_path("Foo", [str(tmp_path)], dangerous=False)
    assert err is None
    assert path == str(tmp_path / "Foo.FCMacro")


def test_safe_mode_resolves_py(tmp_path):
    (tmp_path / "Bar.py").write_text("print('hi')")
    path, err = resolve_macro_path("Bar", [str(tmp_path)], dangerous=False)
    assert err is None
    assert path == str(tmp_path / "Bar.py")


def test_safe_mode_refuses_absolute_path(tmp_path):
    path, err = resolve_macro_path("/etc/passwd", [str(tmp_path)], dangerous=False)
    assert path is None
    assert "dangerous" in err.lower()


def test_safe_mode_refuses_dotdot(tmp_path):
    path, err = resolve_macro_path("../escape", [str(tmp_path)], dangerous=False)
    assert path is None
    assert "dangerous" in err.lower()


def test_safe_mode_not_found(tmp_path):
    path, err = resolve_macro_path("Missing", [str(tmp_path)], dangerous=False)
    assert path is None
    assert "not found" in err.lower()


def test_dangerous_mode_allows_absolute_path(tmp_path):
    f = tmp_path / "anywhere.py"
    f.write_text("print(1)")
    path, err = resolve_macro_path(str(f), [], dangerous=True)
    assert err is None
    assert path == str(f)


def test_dangerous_mode_still_resolves_name(tmp_path):
    (tmp_path / "Named.FCMacro").write_text("print(1)")
    path, err = resolve_macro_path("Named", [str(tmp_path)], dangerous=True)
    assert err is None
    assert path == str(tmp_path / "Named.FCMacro")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_macro_runner.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

Create `freecad_ai/tools/macro_runner.py`:

```python
"""Resolve a macro identifier to a file path, gated by dangerous mode.

Safe mode: only a bare name resolved within an enumerable set of allowed dirs.
Dangerous mode: any name or absolute/relative path, anywhere.
"""

import os

_DANGEROUS_HINT = (
    "Refused: in safe mode run_macro accepts only a bare macro name resolved "
    "from FreeCAD's macro directories. Enable Dangerous mode to run arbitrary "
    "file paths."
)


def _resolve_name(name: str, allowed_dirs: list):
    """Find <name>, <name>.FCMacro, or <name>.py within allowed_dirs.

    The resolved real path must stay inside the allowed dir. Returns the path
    or None.
    """
    for d in allowed_dirs:
        if not d:
            continue
        base = os.path.realpath(d)
        for candidate in (name, name + ".FCMacro", name + ".py"):
            full = os.path.realpath(os.path.join(d, candidate))
            if not (full == base or full.startswith(base + os.sep)):
                continue
            if os.path.isfile(full):
                return os.path.join(d, candidate)
    return None


def resolve_macro_path(macro, allowed_dirs, dangerous,
                       active_doc_dir=None, cwd=None):
    """Return (path, error). Exactly one of the two is non-None."""
    macro = (macro or "").strip()
    if not macro:
        return None, "No macro specified."

    if dangerous:
        candidates = []
        if os.path.isabs(macro):
            candidates.append(macro)
        else:
            if active_doc_dir:
                candidates.append(os.path.join(active_doc_dir, macro))
            candidates.append(os.path.join(cwd or os.getcwd(), macro))
            candidates.append(macro)
        for c in candidates:
            if os.path.isfile(c):
                return c, None
        named = _resolve_name(macro, allowed_dirs)
        if named:
            return named, None
        return None, f"Macro not found: {macro}"

    # Safe mode — bare name only.
    if os.sep in macro or (os.altsep and os.altsep in macro) or ".." in macro:
        return None, _DANGEROUS_HINT
    named = _resolve_name(macro, allowed_dirs)
    if named:
        return named, None
    return None, (
        f"Macro '{macro}' not found in macro directories: "
        f"{', '.join(d for d in allowed_dirs if d)}"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_macro_runner.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/tools/macro_runner.py tests/unit/test_macro_runner.py
git commit -m "feat(tools): add macro path resolver gated by dangerous mode (#13)"
```

---

## Task 6: `run_macro` tool + wire execute_code handler to dangerous mode

**Files:**
- Modify: `freecad_ai/tools/freecad_tools.py:2670` (`_handle_execute_code`), `:2668-2695` (add run_macro), `:4730` (`ALL_TOOLS`)
- Test: `tests/unit/test_run_macro.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_run_macro.py`:

```python
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
    # safe mode -> skip_safety must be False
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
```

> Note: this test patches `execute_code` as a module attribute of `freecad_tools`, so Step 3 must import it at module top (`from ..core.executor import execute_code`) rather than inside the handler.

- [ ] **Step 2: Run test to verify it fails**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_run_macro.py -v`
Expected: FAIL — `AttributeError: module 'freecad_ai.tools.freecad_tools' has no attribute '_handle_run_macro'`.

- [ ] **Step 3: Implement the tool**

In `freecad_ai/tools/freecad_tools.py`, ensure this module-level import exists near the top (add if missing):

```python
from ..core.executor import execute_code
```

Then modify `_handle_execute_code` (line 2670) to consult dangerous mode. Replace the two local imports + call (lines 2672-2675 — `from ..core.active_document import resolve_active_document`, `from ..core.executor import execute_code`, blank line, and `result = execute_code(code)`) with:

```python
    from ..core.active_document import resolve_active_document
    from ..core.dangerous_mode import get_dangerous_mode

    result = execute_code(code, skip_safety=get_dangerous_mode().active)
```

(`resolve_active_document` is still needed later in the handler, so it is kept. The local `from ..core.executor import execute_code` is dropped since `execute_code` is now imported at module top.)

Add the new helper + handler + tool definition right after the `EXECUTE_CODE` definition (after line 2695):

```python
def _macro_allowed_dirs() -> list:
    """Enumerable macro dirs for safe-mode resolution."""
    dirs = []
    try:
        import FreeCAD as App
        d = App.getUserMacroDir(True)  # True = create if missing
        if d:
            dirs.append(d)
    except Exception:
        pass
    try:
        from ..config import USER_TOOLS_DIR
        dirs.append(USER_TOOLS_DIR)
    except Exception:
        pass
    return dirs


def _active_doc_dir():
    try:
        import FreeCAD as App
        doc = App.ActiveDocument
        fn = getattr(doc, "FileName", "") if doc else ""
        return os.path.dirname(fn) if fn else None
    except Exception:
        return None


def _handle_run_macro(macro: str) -> ToolResult:
    """Run an existing FreeCAD macro file and return its console output."""
    from ..core.dangerous_mode import get_dangerous_mode
    from .macro_runner import resolve_macro_path

    dangerous = get_dangerous_mode().active
    path, err = resolve_macro_path(
        macro, _macro_allowed_dirs(), dangerous=dangerous,
        active_doc_dir=_active_doc_dir(),
    )
    if err:
        return ToolResult(success=False, output="", error=err)
    try:
        with open(path, "r", encoding="utf-8") as f:
            code = f.read()
    except OSError as e:
        return ToolResult(success=False, output="", error=f"Could not read macro: {e}")

    result = execute_code(code, skip_safety=dangerous)
    if result.success:
        out = result.stdout.strip() or f"Macro '{macro}' ran successfully (no output)."
        return ToolResult(success=True, output=out,
                          data={"macro": path, "stdout": result.stdout})
    return ToolResult(success=False, output=result.stdout, error=result.stderr)


RUN_MACRO = ToolDefinition(
    name="run_macro",
    description=(
        "Run an EXISTING FreeCAD macro file and return its console output "
        "(stdout/stderr). Use this to execute a macro the user already has on "
        "disk, e.g. a test harness. In normal mode, pass a bare macro NAME "
        "(without extension) that lives in FreeCAD's macro directory; file "
        "paths are refused unless the user has enabled Dangerous mode. Use "
        "execute_code instead when you want to run code you are writing inline."
    ),
    category="general",
    parameters=[
        ToolParam("macro", "string",
                  "Macro name (normal mode) or file path (Dangerous mode only)."),
    ],
    handler=_handle_run_macro,
)
```

Then register it in `ALL_TOOLS` (line 4730+): add `RUN_MACRO,` next to `EXECUTE_CODE,`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_run_macro.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run the registry + tools tests for regressions**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_registry.py tests/unit/test_tools.py tests/unit/test_executor.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/tools/freecad_tools.py tests/unit/test_run_macro.py
git commit -m "feat(tools): add run_macro tool wired to dangerous mode (#13)"
```

---

## Task 7: Plan-mode Check button respects dangerous mode

**Files:**
- Modify: `freecad_ai/ui/code_review_dialog.py:260`

- [ ] **Step 1: Make the change**

In `freecad_ai/ui/code_review_dialog.py`, in `_check` (line 257), change line 260 from:

```python
        result = validate_code(self.code)
```

to:

```python
        from ..core.dangerous_mode import get_dangerous_mode
        result = validate_code(self.code, skip_safety=get_dangerous_mode().active)
```

- [ ] **Step 2: Verify import compiles**

Run: `env -u PYTHONPATH .venv/bin/python -c "import freecad_ai.ui.code_review_dialog"`
Expected: no error (module imports cleanly).

- [ ] **Step 3: Commit**

```bash
git add freecad_ai/ui/code_review_dialog.py
git commit -m "feat(ui): Plan-mode Check honors dangerous mode (#13)"
```

---

## Task 8: Configurable loop + interruption checkpoints in the worker

**Files:**
- Modify: `freecad_ai/ui/chat_widget.py:237` (init), `:287-448` (`_tool_loop`), `:450-477` (`_execute_tool_on_main_thread`)

- [ ] **Step 1: Read max_tool_turns from config**

In `_LLMWorker.__init__`, replace line 237:

```python
        self._max_tool_turns = 30  # Safety limit
```

with:

```python
        from ..config import get_config as _gc
        self._max_tool_turns = _gc().max_tool_turns  # 0 = endless
```

- [ ] **Step 2: Convert the loop to use should_continue_loop**

At the top of `_tool_loop` (line 288, after `messages = list(self.messages)`), add:

```python
        from ..core.loop_control import should_continue_loop
        turn = 0
```

Replace the loop header `for turn in range(self._max_tool_turns):` (line 291) with:

```python
        while should_continue_loop(self._max_tool_turns, turn, self.isInterruptionRequested()):
```

At the very end of the loop body (after `messages.extend(tool_result_messages)`, the last statement in the loop body, ~line 448) add:

```python
            turn += 1
```

After the loop ends, add a guard so an interrupted loop finalizes cleanly. Immediately after the `while` block, add:

```python
        if self.isInterruptionRequested():
            self._full_response += "\n\n_⏹ Stopped by user._"
            self.response_finished.emit(self._full_response)
            return
```

- [ ] **Step 3: Add a streaming-loop checkpoint**

Inside the streaming `for event in client.stream_with_tools(...)` loop (starts ~line 299), add as the first statement in the loop body:

```python
            if self.isInterruptionRequested():
                break
```

- [ ] **Step 4: Wake the tool-wait on interruption**

In `_execute_tool_on_main_thread` (line 450), change the wait loop (lines ~468-475) so it bails on interruption. Replace:

```python
        self._tool_result_ready.lock()
        deadline = 300000  # ms (5 min)
        while self._pending_result is None:
            if not self._tool_result_wait.wait(self._tool_result_ready, deadline):
                # Timed out
                self._tool_result_ready.unlock()
                return {"success": False, "output": "", "error": "Tool execution timed out (main thread did not respond)"}
```

with:

```python
        self._tool_result_ready.lock()
        while self._pending_result is None:
            if self.isInterruptionRequested():
                self._tool_result_ready.unlock()
                return {"success": False, "output": "", "error": "Stopped by user"}
            # Wake every 250 ms so a Stop request is noticed promptly. Replaces
            # the old fixed 5-minute single wait — cooperative interruption is
            # now the way long interactive tools end.
            self._tool_result_wait.wait(self._tool_result_ready, 250)
```

- [ ] **Step 5: Verify it imports**

Run: `env -u PYTHONPATH .venv/bin/python -c "import freecad_ai.ui.chat_widget"`
Expected: no error.

- [ ] **Step 6: Run loop-control tests (logic already covered)**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_loop_control.py -v`
Expected: pass (regression guard for the bound logic this task depends on).

- [ ] **Step 7: Commit**

```bash
git add freecad_ai/ui/chat_widget.py
git commit -m "feat(ui): configurable/endless tool loop with interruption checkpoints (#13)"
```

---

## Task 9: Stop button (send button morph)

**Files:**
- Modify: `freecad_ai/ui/chat_widget.py:1175` (`_send_message`), `:2542` (`_set_loading`)

- [ ] **Step 1: Route clicks by state in `_send_message`**

In `_send_message` (line 1175), replace the existing early-return (lines 1180-1181):

```python
        if self._worker and self._worker.isRunning():
            return
```

with:

```python
        if self._worker and self._worker.isRunning():
            # Button is in "Stop" state — interrupt instead of sending.
            self._worker.requestInterruption()
            return
```

- [ ] **Step 2: Morph the button label/state in `_set_loading`**

In `_set_loading` (line 2542), change the enabled-state and loading label so the button stays clickable and reads "Stop" while loading. Replace line 2545:

```python
        self.send_btn.setEnabled(not loading)
```

with:

```python
        self.send_btn.setEnabled(True)
```

and change the loading label (line 2548) from:

```python
            self.send_btn.setText("...")
```

to:

```python
            self.send_btn.setText("Stop")
```

(Leave the existing stylesheet and the `else:` branch that restores the "Send" text/style untouched.)

- [ ] **Step 3: Verify it imports**

Run: `env -u PYTHONPATH .venv/bin/python -c "import freecad_ai.ui.chat_widget"`
Expected: no error.

- [ ] **Step 4: Manual verification (needs FreeCAD)**

Launch FreeCAD with the workbench, set `max_tool_turns` to 0 (endless) in settings, send a prompt that triggers tool use, and confirm:
- The Send button reads "Stop" while running.
- Clicking "Stop" ends the run within ~1s and appends "⏹ Stopped by user".
- The button returns to "Send" afterward.

Note the result in the commit body.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/ui/chat_widget.py
git commit -m "feat(ui): Send button doubles as Stop to interrupt the loop (#13)"
```

---

## Task 10: Settings — loop spinbox + dangerous-mode session toggle + banner

**Files:**
- Modify: `freecad_ai/ui/settings_dialog.py`, `freecad_ai/ui/chat_widget.py`, `tests/unit/test_dangerous_mode.py`

- [ ] **Step 1: Add the max_tool_turns spinbox to settings**

In `freecad_ai/ui/settings_dialog.py`, locate where simple int settings like `max_retries` are built (search for `max_retries`). Following that exact pattern, add a `QSpinBox` labeled "Max tool-loop turns (0 = endless)":

```python
        self.max_tool_turns_spin = QtWidgets.QSpinBox()
        self.max_tool_turns_spin.setRange(0, 999)
        self.max_tool_turns_spin.setSpecialValueText("endless")  # shown when value == 0
        self.max_tool_turns_spin.setValue(self.config.max_tool_turns)
```

Add it to the same form layout the `max_retries` widget uses, and in the dialog's save/apply method (search for the `max_retries =` assignment) add alongside it:

```python
        self.config.max_tool_turns = self.max_tool_turns_spin.value()
```

> Do NOT add any widget that writes `config.dangerous_skip_safety` — the GUI must never persist it (see spec §1). The session toggle below is separate and lives in the chat dock, not the persisted settings.

- [ ] **Step 2: Add the session toggle + confirmation in the chat dock**

In `freecad_ai/ui/chat_widget.py`, in the dock's UI construction (near the model selector / mode controls — search where `self.send_btn` is created), add a checkable control:

```python
        self.danger_toggle = QtWidgets.QCheckBox("⚠ Dangerous mode")
        self.danger_toggle.setToolTip(
            "Disable code safety checks and allow running macros from any path. "
            "Session-only — resets when FreeCAD restarts.")
        self.danger_toggle.toggled.connect(self._on_danger_toggled)
```

Add the handler method on the dock class:

```python
    def _on_danger_toggled(self, checked):
        from ..core.dangerous_mode import get_dangerous_mode
        dm = get_dangerous_mode()
        if checked:
            box = QtWidgets.QMessageBox(self)
            box.setIcon(QtWidgets.QMessageBox.Warning)
            box.setWindowTitle("Enable Dangerous mode?")
            box.setText(
                "Dangerous mode disables the safety checks built into FreeCAD AI.")
            box.setInformativeText(
                "While active:\n"
                "• AI-run code may call shell commands, delete files, and touch "
                "anything your user account can.\n"
                "• A macro with an infinite loop will FREEZE FreeCAD with no "
                "recovery — unsaved work will be lost.\n"
                "• Generated code runs against your live document without the "
                "headless sandbox pre-check.\n\n"
                "You are solely responsible for what you run. Continue?")
            box.setStandardButtons(
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
            box.setDefaultButton(QtWidgets.QMessageBox.No)
            if box.exec() != QtWidgets.QMessageBox.Yes:
                self.danger_toggle.blockSignals(True)
                self.danger_toggle.setChecked(False)
                self.danger_toggle.blockSignals(False)
                return
            dm.arm()
        else:
            dm.disarm()
        self._update_danger_banner()
```

(`QtWidgets` is already imported at the top of `chat_widget.py` via `compat`; confirm and reuse that import rather than adding a new one.)

- [ ] **Step 3: Add the always-visible banner**

Build a banner widget and insert it as the first row of the dock's main vertical layout (where the layout is assembled):

```python
        self.danger_banner = QtWidgets.QLabel(
            "⚠ DANGEROUS MODE ACTIVE — safety checks disabled")
        self.danger_banner.setStyleSheet(
            "background-color: #b00020; color: white; font-weight: bold; "
            "padding: 4px;")
        self.danger_banner.setVisible(False)
        # main_layout.insertWidget(0, self.danger_banner)  # use the dock's layout var
```

Add the updater method:

```python
    def _update_danger_banner(self):
        from ..core.dangerous_mode import get_dangerous_mode
        active = get_dangerous_mode().active
        self.danger_banner.setVisible(active)
        if active and not self.danger_toggle.isChecked():
            self.danger_toggle.blockSignals(True)
            self.danger_toggle.setChecked(True)
            self.danger_toggle.blockSignals(False)
```

Call `self._update_danger_banner()` at the end of the dock's `__init__` (after the toggle/banner exist) so a hand-edited `dangerous_skip_safety: true` shows the banner on startup.

- [ ] **Step 4: Verify imports**

Run: `env -u PYTHONPATH .venv/bin/python -c "import freecad_ai.ui.settings_dialog; import freecad_ai.ui.chat_widget"`
Expected: no error.

- [ ] **Step 5: Add a regression test that the session toggle never persists**

Add to `tests/unit/test_dangerous_mode.py`:

```python
def test_arm_then_save_config_does_not_persist(tmp_path, monkeypatch):
    import json
    import freecad_ai.config as config_mod
    cfg = config_mod.AppConfig()
    monkeypatch.setattr(config_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", str(tmp_path / "config.json"))

    from freecad_ai.core.dangerous_mode import DangerousMode
    dm = DangerousMode()
    dm.arm()                       # session-only
    config_mod.save_config(cfg)    # any save (e.g. dock-layout change)

    with open(str(tmp_path / "config.json")) as f:
        reloaded = config_mod.AppConfig.from_dict(json.load(f))
    assert reloaded.dangerous_skip_safety is False
```

> If `save_config` writes to a path constant other than `CONFIG_FILE`, adjust the monkeypatch target to the actual module-level name used inside `def save_config` in `config.py` (line ~533).

- [ ] **Step 6: Run the test**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/test_dangerous_mode.py -v`
Expected: all pass.

- [ ] **Step 7: Manual verification (needs FreeCAD)**

- Toggle "⚠ Dangerous mode" → confirm dialog appears; clicking No leaves it unchecked.
- Clicking Yes shows the red banner; `run_macro` with an absolute path now succeeds.
- Restart FreeCAD → toggle is off, banner gone (session reset).
- Hand-edit `dangerous_skip_safety: true` in config.json, restart → banner shows on startup.

- [ ] **Step 8: Commit**

```bash
git add freecad_ai/ui/settings_dialog.py freecad_ai/ui/chat_widget.py tests/unit/test_dangerous_mode.py
git commit -m "feat(ui): loop-turns spinbox + session dangerous-mode toggle, banner & confirm (#13)"
```

---

## Task 11: Docs, wiki, CHANGELOG

**Files:**
- Modify: `README.md`, `CHANGELOG.md`

- [ ] **Step 1: Add CHANGELOG entry**

Add a new top entry to `CHANGELOG.md` (match the existing version-heading format; pick the next alpha version, e.g. `0.15.0-alpha`):

```markdown
## [0.15.0-alpha] - 2026-05-22

### Added
- `run_macro` tool: run an existing FreeCAD macro file and feed its console
  output back to the AI. In normal mode it accepts a bare macro name resolved
  from FreeCAD's macro directory; file paths require Dangerous mode. (#13)
- Configurable agentic loop count (Settings → "Max tool-loop turns"). `0` means
  endless; previously hardcoded at 30. Default remains 30.
- Stop button: the Send button becomes "Stop" while the AI is working and
  interrupts the loop (the only brake when the loop is set to endless).
- Dangerous mode: a session-scoped toggle that disables the code safety checks
  (static pattern blocking, headless sandbox pre-check, execution timeout) and
  lets `run_macro` run files from any path. Off at every launch; a red banner
  shows whenever it is active. **Use at your own risk** — see README.
```

- [ ] **Step 2: Add the README section with the BIG warning**

Add a new section to `README.md`:

```markdown
## Running macros & Dangerous mode

The `run_macro` tool lets the assistant run an existing FreeCAD macro and read
its console output directly — useful for AI-written test harnesses. By default
it only runs a macro by **name** from FreeCAD's macro directory.

### ⚠️ Dangerous mode — read this

Dangerous mode is an opt-in escape hatch that **removes the safety checks**
FreeCAD AI normally applies to code it runs. Enable it from the chat panel
(a confirmation dialog explains the risks; it resets to OFF every time FreeCAD
restarts, and a red banner shows while it is active).

While Dangerous mode is active:

- **Arbitrary code/commands:** AI-run code can call shell commands, delete
  files, and do anything your user account is allowed to do.
- **No timeout:** a macro containing an infinite loop will **freeze FreeCAD's
  main window with no way to recover — any unsaved work is lost.**
- **No sandbox pre-check:** broken or destructive code runs straight against
  your live document.
- **Endless loops:** if you also set the tool-loop count to `0` (endless), the
  AI can keep acting until you press **Stop**. There is no other limit, and it
  can consume a large number of tokens.

**You are solely responsible for anything you run in this mode.** It is provided
as a power-user convenience and is not tested against malicious or destructive
input. Document integrity (undo rollback) is the only safeguard that remains on.

To make Dangerous mode persist across restarts (not recommended), set
`"dangerous_skip_safety": true` in your `config.json` by hand. The GUI will
never write this for you.

> Note: behavior described here is verified on Linux; the macro-directory
> resolution uses FreeCAD's cross-platform API but is untested on macOS/Windows.
```

- [ ] **Step 3: Verify both files mention it (sanity grep)**

Run: `grep -n "Dangerous mode" README.md CHANGELOG.md`
Expected: matches in both files.

- [ ] **Step 4: Commit**

```bash
git add README.md CHANGELOG.md
git commit -m "docs: document run_macro, loop count, and Dangerous mode warning (#13)"
```

---

## Task 12: Full suite + integration smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the full unit suite**

Run: `env -u PYTHONPATH .venv/bin/python -m pytest tests/unit/ -v`
Expected: all pass, no regressions.

- [ ] **Step 2: (Optional, needs AppImage) Integration smoke**

If the FreeCAD AppImage is available, write a throwaway `Hello.FCMacro` in the
user macro dir containing `print("integration-ok")`, then in Act mode ask the
assistant to "run the Hello macro" and confirm `integration-ok` appears in the
tool result. Then enable Dangerous mode and confirm a macro that imports a
normally-blocked module (e.g. `subprocess`) runs.

- [ ] **Step 3: Final commit (if any cleanup)**

```bash
git add -A
git commit -m "chore: finalize run_macro + dangerous mode feature (#13)"
```

---

## Wiki (separate repo)

The wiki lives at `/home/alf/Projects/programming/misc/freecad-ai-wiki` (separate
git repo). After merging, mirror the README "Running macros & Dangerous mode"
section into the wiki's Tool-Reference page (add a `run_macro` entry) and a new
"Dangerous mode" page. This is a separate commit in that repo — out of scope for
this plan's task list but noted so it isn't forgotten.
