"""Every relative import in the package must resolve to a real module.

Deferred imports inside functions (`from ...utils.viewport import ...`)
are invisible to compileall and only fail when that code path runs — in
practice, when a user calls the tool. Two shipped bugs of this class came
from the mixin/handlers refactors moving files one level deeper without
updating the dot count:

  - chat_dock/*.py used `..core` instead of `...core`
  - handlers/view.py used `..utils.viewport` (= freecad_ai.tools.utils)

This walks every `from . / .. / ... import` in the package's AST — module
level and inside functions — resolves it against the file's package, and
asserts the target exists on disk.
"""

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_PKG = _ROOT / "freecad_ai"

_PY_FILES = sorted(
    p for p in _PKG.rglob("*.py") if "__pycache__" not in p.parts
)


def _package_parts(path: Path) -> list[str]:
    """Package path of the module's *containing package*."""
    rel = path.relative_to(_ROOT)
    parts = list(rel.parts[:-1])  # drop the filename
    if rel.name == "__init__.py":
        return parts
    return parts


def _target_exists(parts: list[str]) -> bool:
    base = _ROOT.joinpath(*parts)
    return (base.with_suffix(".py").exists()
            or (base / "__init__.py").exists()
            or base.is_dir())


@pytest.mark.parametrize("path", _PY_FILES, ids=lambda p: str(p.relative_to(_PKG)))
def test_relative_imports_resolve(path):
    tree = ast.parse(path.read_text(), filename=str(path))
    pkg = _package_parts(path)
    broken = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.level:
            continue
        # level=1 → current package, level=2 → parent, …
        up = node.level - 1
        base = pkg[:len(pkg) - up] if up else list(pkg)
        if up and len(pkg) - up < 0:
            broken.append(f"line {node.lineno}: too many dots for {path.name}")
            continue
        module_parts = base + (node.module.split(".") if node.module else [])
        if _target_exists(module_parts):
            continue
        # `from ..x import y` may name a symbol in package __init__ rather
        # than a submodule — accept when the parent package exists.
        if _target_exists(module_parts[:-1]):
            continue
        dots = "." * node.level
        broken.append(
            f"line {node.lineno}: from {dots}{node.module or ''} "
            f"→ {'.'.join(module_parts)} (not found)")

    assert not broken, f"{path.relative_to(_PKG)} has unresolvable imports: {broken}"
