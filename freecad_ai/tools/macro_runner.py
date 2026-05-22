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

    if "\x00" in macro:
        return None, "Invalid macro name (contains a null byte)."

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
