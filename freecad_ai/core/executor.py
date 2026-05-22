"""Code execution engine for FreeCAD AI.

Extracts Python code from LLM responses and executes them in FreeCAD's
interpreter with the appropriate modules in scope.

Safety layers:
  1. Static validation — block dangerous patterns
  2. Subprocess sandbox — test code in a headless FreeCAD process first
  3. Undo transactions — roll back failed operations
  4. Auto-save — save document before execution so crashes don't lose work
"""

import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import traceback
from dataclasses import dataclass


@dataclass
class ExecutionResult:
    success: bool
    stdout: str
    stderr: str
    code: str


# Regex to extract ```python ... ``` code blocks
CODE_BLOCK_RE = re.compile(r"```python\s*\n(.*?)```", re.DOTALL)


def extract_code_blocks(text: str) -> list[str]:
    """Extract all Python code blocks from markdown-formatted text."""
    return CODE_BLOCK_RE.findall(text)


def _find_freecad_cmd() -> str:
    """Find the FreeCAD executable for console-mode subprocess runs.

    Handles AppImages, wrapper scripts, and standard installs.
    """
    import glob

    # 1. Look for AppImages in ~/bin (preferred — direct binary, not a wrapper script)
    appimage_patterns = [
        os.path.expanduser("~/bin/FreeCAD*.AppImage"),
        "/usr/local/bin/FreeCAD*.AppImage",
    ]
    for pattern in appimage_patterns:
        matches = sorted(glob.glob(pattern), reverse=True)  # newest version first
        if matches:
            return matches[0]

    # 2. Check standard install locations
    candidates = [
        "/usr/bin/freecadcmd",
        "/usr/bin/freecad",
        "/usr/local/bin/freecad",
        os.path.expanduser("~/bin/freecad"),
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c

    # 3. Fallback: try PATH
    for name in ("freecadcmd", "freecad"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def _sandbox_test(code: str, timeout: int = 15, document_path: str | None = None) -> tuple:
    """Test code in a headless FreeCAD subprocess.

    Returns (safe: bool, error_message: str).
    If FreeCAD console is not available, returns (True, "") to skip sandboxing.
    """
    freecad_bin = _find_freecad_cmd()
    if not freecad_bin:
        return True, ""  # Can't sandbox, let it through

    result_file = tempfile.mktemp(suffix=".json")
    script_file = tempfile.mktemp(suffix=".py")

    if document_path:
        open_block = (
            "    App.openDocument({path!r})\n"
            "    doc = App.ActiveDocument\n"
            "    if doc is None:\n"
            "        raise RuntimeError('Sandbox: openDocument did not set ActiveDocument')\n"
            "    App.setActiveDocument(doc.Name)"
        ).format(path=document_path)
    else:
        open_block = '    doc = App.newDocument("SandboxTest")'

    # Harness: run user code, then close all documents without saving (temp copy is disposable).
    # Stub FreeCADGui view methods that only work in a graphical session.
    # Harness captures two classes of failure that Python exceptions miss:
    #   1. FreeCAD.Console.PrintError messages (C++ layer logging — e.g.
    #      PositionBySupport attachment failures, topological naming mismatches)
    #   2. Features that build a null/invalid Shape without raising
    harness = '''import sys, json, traceback
result = {{"ok": False, "error": ""}}
try:
    import FreeCAD as App

    # Console observer — captures errors/warnings the C++ layer logs to the
    # Report View. Without this, attachment and recompute failures silently
    # pass the sandbox because no Python exception is raised.
    class _ErrObs:
        def __init__(self):
            self.errors = []
            self.warnings = []
        def OnError(self, msg, *a, **kw):
            self.errors.append(str(msg).strip())
        def OnWarning(self, msg, *a, **kw):
            self.warnings.append(str(msg).strip())
    _err_obs = _ErrObs()
    _observer_installed = False
    try:
        App.Console.AddObserver(_err_obs)
        _observer_installed = True
    except Exception:
        pass

    try:
        import FreeCADGui as Gui
        # Console mode: Gui module exists but has no active view.
        # Stub methods that LLM code commonly calls so they become no-ops.
        if not hasattr(Gui, "ActiveDocument") or Gui.ActiveDocument is None:
            Gui.SendMsgToActiveView = lambda *a, **kw: None
            Gui.updateGui = lambda *a, **kw: None
    except ImportError:
        pass
{open_block}
    # --- user code ---
{indented_code}
    # --- end user code ---
    doc.recompute()

    # Post-execution validation: collect console errors + walk objects for
    # null/invalid shapes. Either signal means the code "ran" but broke the
    # model — exactly the case where Python-exception-only checking fails.
    _issues = []
    if _observer_installed:
        # De-dup — C++ logs the same error per failed recompute iteration
        _seen = set()
        for _e in _err_obs.errors:
            if _e and _e not in _seen:
                _seen.add(_e)
                _issues.append("FreeCAD error: " + _e)
    for _obj in doc.Objects:
        _shape = getattr(_obj, "Shape", None)
        if _shape is not None:
            try:
                if _shape.isNull():
                    _issues.append("Object '" + _obj.Name + "' has null shape")
                elif not _shape.isValid():
                    _issues.append("Object '" + _obj.Name + "' has invalid shape")
            except Exception:
                pass
        _state = getattr(_obj, "State", None)
        if _state and "Invalid" in _state:
            _issues.append("Object '" + _obj.Name + "' is in Invalid state")

    if _issues:
        result["error"] = "Post-execution validation found issues:\\n" + "\\n".join(_issues)
    else:
        result["ok"] = True
except Exception as e:
    result["error"] = traceback.format_exc()
finally:
    try:
        import FreeCAD as App
        for _dn in list(App.listDocuments().keys()):
            App.closeDocument(_dn)
    except Exception:
        pass
    with open({result_path!r}, "w") as f:
        json.dump(result, f)
'''.format(
        open_block=open_block,
        indented_code="\n".join("    " + line for line in code.splitlines()),
        result_path=result_file,
    )

    try:
        with open(script_file, "w") as f:
            f.write(harness)

        proc = subprocess.run(
            [freecad_bin, "-c", script_file],
            timeout=timeout,
            capture_output=True,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )

        if proc.returncode != 0 and proc.returncode > 0:
            # Non-zero but not a signal — Python error
            stderr = proc.stderr.decode(errors="replace")[-500:]
            return False, "Sandbox: code raised an error:\n" + stderr

        if proc.returncode < 0:
            # Killed by signal (e.g. SIGSEGV = -11)
            sig = -proc.returncode
            sig_name = signal.Signals(sig).name if sig in signal.Signals._value2member_map_ else str(sig)
            return False, (
                "Sandbox: code CRASHED FreeCAD (signal {}). "
                "This code is not safe to execute.".format(sig_name)
            )

        # Read result
        if os.path.exists(result_file):
            with open(result_file) as f:
                result = json.load(f)
            if result["ok"]:
                return True, ""
            else:
                return False, "Sandbox: " + result["error"]

        return True, ""  # No result file but process exited OK

    except subprocess.TimeoutExpired:
        return False, "Sandbox: code timed out after {} seconds".format(timeout)
    except Exception as e:
        # Sandbox itself failed — don't block execution
        return True, ""
    finally:
        for f in (script_file, result_file):
            try:
                os.unlink(f)
            except OSError:
                pass


def _auto_save(namespace: dict):
    """Save a recovery copy of the active document before executing code."""
    try:
        from .active_document import resolve_active_document
        doc = resolve_active_document()
        if not doc or not doc.FileName:
            return  # Unsaved document, nothing to back up
        backup = doc.FileName + ".ai-backup"
        doc.saveAs(backup)
        # Restore the original filename so the user doesn't notice
        doc.FileName = doc.FileName.replace(".ai-backup", "")
    except Exception:
        pass  # Best-effort


def execute_code(code: str, timeout: int = 30, sandbox: bool = True,
                 skip_safety: bool = False) -> ExecutionResult:
    """Execute Python code in FreeCAD's context.

    The code runs with FreeCAD modules available in its namespace.
    stdout/stderr are captured and returned along with success status.

    Safety layers:
      1. Static validation (block dangerous patterns)
      2. Subprocess sandbox (test in headless FreeCAD first)
      3. Undo transactions (roll back on Python-level failure)
      4. Auto-save (backup document before execution)
    """
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

    from .active_document import get_synced_active_document, refresh_gui_for_document

    # Layer 2: Subprocess sandbox — optional copy of saved document so getObject-style code validates safely
    sandbox_copy_path = None
    if sandbox and not skip_safety:
        pre_doc = get_synced_active_document()
        fn = getattr(pre_doc, "FileName", "") if pre_doc else ""
        if fn and os.path.isfile(fn):
            try:
                fd, sandbox_copy_path = tempfile.mkstemp(suffix=".FCStd")
                os.close(fd)
                shutil.copy2(fn, sandbox_copy_path)
            except OSError as e:
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr=f"Sandbox: could not copy document for validation: {e}",
                    code=code,
                )
        try:
            safe, sandbox_err = _sandbox_test(
                code, timeout=min(timeout, 15), document_path=sandbox_copy_path
            )
            if not safe:
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr=sandbox_err,
                    code=code,
                )
        finally:
            if sandbox_copy_path:
                try:
                    os.unlink(sandbox_copy_path)
                except OSError:
                    pass

    target_doc = get_synced_active_document()
    if target_doc is None:
        return ExecutionResult(
            success=False,
            stdout="",
            stderr=(
                "No active document — open a document in FreeCAD or click "
                "its tab so it is the focused window."
            ),
            code=code,
        )
    doc_name = target_doc.Name

    # Build execution namespace with FreeCAD modules
    namespace = _build_namespace()

    # Layer 4: Auto-save before execution
    _auto_save(namespace)

    # Capture stdout/stderr
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    captured_out = io.StringIO()
    captured_err = io.StringIO()
    sys.stdout = captured_out
    sys.stderr = captured_err

    doc = target_doc
    success = True
    try:
        # Layer 3: Undo transaction
        if doc:
            doc.openTransaction("AI Code Execution")

        # Set an alarm timeout to catch infinite loops / hangs
        _old_handler = None
        if not skip_safety:
            try:
                def _timeout_handler(signum, frame):
                    raise TimeoutError("Code execution timed out after {} seconds".format(timeout))
                _old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
                signal.alarm(timeout)
            except (OSError, AttributeError):
                pass

        try:
            exec(code, namespace)
        finally:
            try:
                signal.alarm(0)
                if _old_handler is not None:
                    signal.signal(signal.SIGALRM, _old_handler)
            except (OSError, AttributeError):
                pass

        # Recompute and commit
        _recompute(namespace)
        if doc:
            doc.commitTransaction()
        import FreeCAD as App
        d = App.getDocument(doc_name)
        if d is None:
            raise RuntimeError(
                "Target document is no longer available after execution."
            )
        refresh_gui_for_document(d)
    except Exception:
        success = False
        traceback.print_exc(file=captured_err)
        if doc:
            try:
                doc.abortTransaction()
                doc.recompute()
            except Exception:
                pass
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    return ExecutionResult(
        success=success,
        stdout=captured_out.getvalue(),
        stderr=captured_err.getvalue(),
        code=code,
    )


def validate_code(code: str, timeout: int = 15, skip_safety: bool = False) -> ExecutionResult:
    """Run static + sandbox validation without touching the live document.

    Runs Layer 1 (static pattern check) and Layer 2 (headless subprocess
    against a temp copy of the active document). Skips Layer 3/4. Returns
    an ExecutionResult so callers can reuse the same error-surfacing path
    they use for actual execution.

    If no FreeCAD console binary is available, the sandbox is skipped and
    the result is a pass — matches execute_code()'s fallback behavior.
    """
    if skip_safety:
        return ExecutionResult(success=True, stdout="", stderr="", code=code)
    warnings = _validate_code(code)
    if warnings:
        return ExecutionResult(
            success=False,
            stdout="",
            stderr="Static validation failed:\n" + "\n".join(warnings),
            code=code,
        )

    from .active_document import get_synced_active_document
    pre_doc = get_synced_active_document()
    fn = getattr(pre_doc, "FileName", "") if pre_doc else ""
    sandbox_copy_path = None
    if fn and os.path.isfile(fn):
        try:
            fd, sandbox_copy_path = tempfile.mkstemp(suffix=".FCStd")
            os.close(fd)
            shutil.copy2(fn, sandbox_copy_path)
        except OSError as e:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Sandbox: could not copy document for validation: {e}",
                code=code,
            )
    try:
        safe, err = _sandbox_test(code, timeout=timeout, document_path=sandbox_copy_path)
    finally:
        if sandbox_copy_path:
            try:
                os.unlink(sandbox_copy_path)
            except OSError:
                pass
    if safe:
        return ExecutionResult(success=True, stdout="", stderr="", code=code)
    return ExecutionResult(success=False, stdout="", stderr=err, code=code)


def _validate_code(code: str) -> list[str]:
    """Check code for patterns known to crash FreeCAD.

    Returns a list of warning strings. Empty list means no issues found.
    """
    warnings = []

    # Dangerous imports / operations that could crash or damage
    dangerous_patterns = [
        (r"\bos\.system\s*\(", "os.system() calls are not allowed"),
        (r"\bsubprocess\b", "subprocess module is not allowed"),
        (r"\bshutil\.rmtree\b", "shutil.rmtree() is not allowed"),
        (r"\b__import__\s*\(\s*['\"]os['\"]\s*\)", "Dynamic import of os is not allowed"),
    ]
    for pattern, msg in dangerous_patterns:
        if re.search(pattern, code):
            warnings.append(msg)

    # FreeCAD crash-prone patterns
    has_revolution = bool(re.search(r"Revolution|Revolve|makeRevolution", code))
    if has_revolution:
        # Revolution with a full circle profile is a known crash
        has_full_circle = bool(re.search(r"Part\.Circle\s*\(", code))
        has_arc = bool(re.search(r"ArcOfCircle|Arc\s*\(", code))
        if has_full_circle and not has_arc:
            warnings.append(
                "Revolution with a full Part.Circle profile will crash FreeCAD. "
                "Use Part.ArcOfCircle (semicircle) + a closing line instead, "
                "or use Part.makeSphere() for spheres."
            )
        # Check for 360 degree revolution — always risky with sketch profiles
        if re.search(r"\.Angle\s*=\s*360", code):
            warnings.append(
                "360-degree Revolution detected. Ensure the profile is an OPEN "
                "shape (semicircle + straight line along axis), NOT a closed "
                "circle. If you want a sphere, use Part.makeSphere() instead."
            )

    return warnings


def _build_namespace() -> dict:
    """Build a namespace dict with FreeCAD modules for code execution."""
    ns = {"__builtins__": __builtins__}

    # Try to import each FreeCAD module
    modules = [
        ("FreeCAD", "App"),
        ("FreeCADGui", "Gui"),
        ("Part", None),
        ("PartDesign", None),
        ("Sketcher", None),
        ("Draft", None),
        ("Mesh", None),
        ("BOPTools", None),
    ]
    for mod_name, alias in modules:
        try:
            mod = __import__(mod_name)
            ns[mod_name] = mod
            if alias:
                ns[alias] = mod
        except ImportError:
            pass

    # Convenience: math module is often useful
    import math
    ns["math"] = math

    return ns


def _recompute(namespace: dict):
    """Recompute the GUI-aligned active document if available."""
    from .active_document import resolve_active_document
    doc = resolve_active_document()
    if doc:
        doc.recompute()
