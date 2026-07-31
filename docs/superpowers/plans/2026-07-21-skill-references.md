# Skill `references/` Resource Loading — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add tier-3 progressive disclosure to skills — a `references/` subdirectory whose markdown files the model loads on demand via `use_skill(name, resource=...)`.

**Architecture:** Extend the existing skill loader to scan `references/` into a per-skill `{key -> absolute_path}` allowlist. Extend the `use_skill` tool with an optional `resource` argument that indexes that allowlist (the model passes a key, never a path — traversal is impossible by construction). When a skill with references is loaded, append an auto-generated "Available references" manifest to its `SKILL.md` result so the model knows what it can pull in and the exact call to do so.

**Tech Stack:** Python 3.11, stdlib only (`os`, `dataclasses`). Tests via pytest, headless (no FreeCAD).

**Design doc:** `docs/superpowers/specs/2026-07-21-skill-references-design.md`. Issue [#37](https://github.com/ghbalf/freecad-ai/issues/37).

## Global Constraints

- No external dependencies — stdlib only.
- Fully backward-compatible: a skill with no `references/` directory must behave exactly as today (empty `references` dict, no manifest, no error).
- Model input is **never** joined into a filesystem path. The `resource` argument is looked up as a key in a pre-scanned dict; unknown keys return an error.
- Follow the repo test gotcha: run pytest as `env PYTHONPATH= .venv/bin/pytest ...` (a leaked `PYTHONPATH` shadows the venv's pluggy and crashes pytest).
- `references/` scan is top-level files only (no recursion) — the Agent Skills spec recommends keeping references one level deep.
- Text files read as UTF-8; undecodable/unreadable files are skipped at scan and error cleanly at read (same posture as the existing `SKILL.md` loader).

---

## File Structure

- `freecad_ai/extensions/skills.py` — loader + registry. Add `Skill.references` field, scan logic in `_scan_skills_dir`, `get_skill_resource()` method, `render_references_manifest()` method, `_reference_summary()` module helper, and manifest append in `execute_skill()`.
- `freecad_ai/tools/freecad_tools.py` — `_handle_use_skill` gains a `resource` branch; `USE_SKILL` gains a `resource` parameter and an updated description.
- `tests/unit/test_skills.py` — all new tests (loader, resource resolution, traversal-safety, manifest).
- `tests/unit/test_tool_routing.py` — one text-assertion guard for the `use_skill` description advertising the two-step read.

---

### Task 1: Loader scans `references/` into `Skill.references`

**Files:**
- Modify: `freecad_ai/extensions/skills.py` (`Skill` dataclass ~lines 27-36; `_scan_skills_dir` ~lines 57-109)
- Test: `tests/unit/test_skills.py`

**Interfaces:**
- Produces: `Skill.references: dict[str, str]` mapping a normalized key (filename stem, lowercased) to the reference file's absolute path. Empty dict when the skill has no `references/` dir.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_skills.py`:

```python
class TestSkillReferences:
    def _make_skill_with_refs(self, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        sd = skills_dir / "debug-model"
        sd.mkdir()
        (sd / "SKILL.md").write_text("# Debug Model\n\nDiagnose broken models.\n")
        refs = sd / "references"
        refs.mkdir()
        (refs / "freecad-gotchas.md").write_text(
            "# FreeCAD gotchas\n\ngetObjectsByLabel vs getObject, AttachmentSupport.\n"
        )
        (refs / "fix-attachment.md").write_text(
            "# Fix attachment\n\nRe-attach a face or datum plane.\n"
        )
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(skills_dir))
        monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", str(tmp_path / "nonexistent"))
        return skills_dir

    def test_scans_reference_files_into_dict(self, tmp_path, monkeypatch):
        self._make_skill_with_refs(tmp_path, monkeypatch)
        reg = SkillsRegistry()
        skill = reg.get_skill("debug-model")
        assert set(skill.references) == {"freecad-gotchas", "fix-attachment"}
        assert skill.references["freecad-gotchas"].endswith(
            os.path.join("references", "freecad-gotchas.md")
        )

    def test_skill_without_references_has_empty_dict(self, mock_skills_dir, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(mock_skills_dir))
        monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", str(mock_skills_dir))
        reg = SkillsRegistry()
        assert reg.get_skill("test-skill").references == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_skills.py::TestSkillReferences -v`
Expected: FAIL — `Skill` has no attribute `references` (both tests error/fail).

- [ ] **Step 3: Add the `references` field to the dataclass**

In `freecad_ai/extensions/skills.py`, in the `Skill` dataclass, add after `validation_path`:

```python
    references: dict = field(default_factory=dict)  # key (lowercased stem) -> abspath
```

(`field` is already imported: `from dataclasses import dataclass, field`.)

- [ ] **Step 4: Scan `references/` in `_scan_skills_dir`**

In `_scan_skills_dir`, immediately before the `self._skills[entry] = Skill(...)` construction, add:

```python
            references = {}
            refs_dir = os.path.join(skill_dir, "references")
            if os.path.isdir(refs_dir):
                for ref_entry in sorted(os.listdir(refs_dir)):
                    ref_path = os.path.join(refs_dir, ref_entry)
                    if not os.path.isfile(ref_path):
                        continue
                    key = os.path.splitext(ref_entry)[0].lower()
                    references[key] = ref_path
```

Then add `references=references,` to the `Skill(...)` call.

- [ ] **Step 5: Run tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_skills.py::TestSkillReferences -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/extensions/skills.py tests/unit/test_skills.py
git commit -m "feat(skills): scan references/ subdirectory into Skill.references"
```

---

### Task 2: `get_skill_resource` resolves a key to file contents (traversal-safe)

**Files:**
- Modify: `freecad_ai/extensions/skills.py` (add method to `SkillsRegistry`)
- Test: `tests/unit/test_skills.py`

**Interfaces:**
- Consumes: `Skill.references` from Task 1.
- Produces: `SkillsRegistry.get_skill_resource(name: str, resource: str) -> dict` returning `{"output": <file contents>}` on success or `{"error": <message>}` on unknown skill / no references / unknown key / unreadable file. Accepts the key with or without an extension (`"foo"` or `"foo.md"`), case-insensitively.

- [ ] **Step 1: Write the failing test**

Add to `TestSkillReferences` in `tests/unit/test_skills.py`:

```python
    def test_get_resource_returns_file_contents(self, tmp_path, monkeypatch):
        self._make_skill_with_refs(tmp_path, monkeypatch)
        reg = SkillsRegistry()
        result = reg.get_skill_resource("debug-model", "freecad-gotchas")
        assert "output" in result
        assert "AttachmentSupport" in result["output"]

    def test_get_resource_accepts_md_extension(self, tmp_path, monkeypatch):
        self._make_skill_with_refs(tmp_path, monkeypatch)
        reg = SkillsRegistry()
        result = reg.get_skill_resource("debug-model", "fix-attachment.md")
        assert "output" in result
        assert "datum plane" in result["output"]

    def test_get_resource_unknown_key_lists_available(self, tmp_path, monkeypatch):
        self._make_skill_with_refs(tmp_path, monkeypatch)
        reg = SkillsRegistry()
        result = reg.get_skill_resource("debug-model", "nope")
        assert "error" in result
        assert "freecad-gotchas" in result["error"]
        assert "fix-attachment" in result["error"]

    def test_get_resource_skill_without_references(self, mock_skills_dir, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(mock_skills_dir))
        monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", str(mock_skills_dir))
        reg = SkillsRegistry()
        result = reg.get_skill_resource("test-skill", "anything")
        assert "error" in result
        assert "no references" in result["error"].lower()

    def test_get_resource_unknown_skill(self, tmp_path, monkeypatch):
        self._make_skill_with_refs(tmp_path, monkeypatch)
        reg = SkillsRegistry()
        result = reg.get_skill_resource("no-such-skill", "freecad-gotchas")
        assert "error" in result

    def test_get_resource_traversal_is_not_found(self, tmp_path, monkeypatch):
        """Model input is a key, never a path — traversal keys just miss."""
        self._make_skill_with_refs(tmp_path, monkeypatch)
        reg = SkillsRegistry()
        for evil in ["../SKILL", "../../conftest", "/etc/passwd", "..\\SKILL"]:
            result = reg.get_skill_resource("debug-model", evil)
            assert "error" in result, f"{evil!r} should not resolve"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_skills.py::TestSkillReferences -v`
Expected: the 6 new tests FAIL — `SkillsRegistry` has no attribute `get_skill_resource`.

- [ ] **Step 3: Implement `get_skill_resource`**

In `freecad_ai/extensions/skills.py`, add this method to `SkillsRegistry` (e.g. after `execute_skill`):

```python
    def get_skill_resource(self, name: str, resource: str) -> dict:
        """Return the contents of a skill's reference file.

        `resource` is a KEY into the pre-scanned Skill.references allowlist —
        it is never treated as a filesystem path, so directory traversal is
        impossible. The key may be given with or without an extension and is
        matched case-insensitively.

        Returns {"output": contents} or {"error": message}.
        """
        skill = self._skills.get(name)
        if not skill:
            return {"error": f"Unknown skill: {name}"}
        if not skill.references:
            return {"error": f"Skill '{name}' has no references."}

        key = os.path.splitext(resource.strip())[0].lower()
        path = skill.references.get(key)
        if not path:
            available = ", ".join(sorted(skill.references))
            return {
                "error": (
                    f"Reference '{resource}' not found in skill '{name}'. "
                    f"Available: {available}"
                )
            }
        try:
            with open(path, "r", encoding="utf-8") as f:
                return {"output": f.read()}
        except (OSError, UnicodeDecodeError) as e:
            return {"error": f"Could not read reference '{resource}': {e}"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_skills.py::TestSkillReferences -v`
Expected: PASS (all TestSkillReferences tests green).

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/extensions/skills.py tests/unit/test_skills.py
git commit -m "feat(skills): add get_skill_resource with allowlist-key lookup"
```

---

### Task 3: Append an "Available references" manifest to the `use_skill` result

**Files:**
- Modify: `freecad_ai/extensions/skills.py` (add `_reference_summary` module helper, `render_references_manifest` method, append in `execute_skill`)
- Test: `tests/unit/test_skills.py`

**Interfaces:**
- Consumes: `Skill.references` (Task 1).
- Produces: `SkillsRegistry.render_references_manifest(skill: Skill) -> str` — returns a markdown block (leading with two newlines) listing each reference key, a one-line summary, and the exact `use_skill(..., resource=...)` call; returns `""` when the skill has no references. `execute_skill` appends this to the `inject_prompt` content on the SKILL.md path only.

- [ ] **Step 1: Write the failing test**

Add to `TestSkillReferences`:

```python
    def test_execute_skill_appends_references_manifest(self, tmp_path, monkeypatch):
        self._make_skill_with_refs(tmp_path, monkeypatch)
        reg = SkillsRegistry()
        result = reg.execute_skill("debug-model")
        content = result["inject_prompt"]
        assert "Diagnose broken models." in content            # original SKILL.md
        assert "Available references" in content                # manifest heading
        assert "freecad-gotchas" in content and "fix-attachment" in content
        # advertises the exact invocation syntax
        assert "resource='freecad-gotchas'" in content
        # includes a one-line summary drawn from the file
        assert "getObjectsByLabel" in content

    def test_execute_skill_no_manifest_without_references(self, mock_skills_dir, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(mock_skills_dir))
        monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", str(mock_skills_dir))
        reg = SkillsRegistry()
        result = reg.execute_skill("test-skill")
        assert "Available references" not in result["inject_prompt"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_skills.py::TestSkillReferences::test_execute_skill_appends_references_manifest tests/unit/test_skills.py::TestSkillReferences::test_execute_skill_no_manifest_without_references -v`
Expected: the append test FAILS ("Available references" not in content); the no-manifest test PASSES already.

- [ ] **Step 3: Add the summary helper and manifest renderer**

In `freecad_ai/extensions/skills.py`, add a module-level helper near `_file_hash`:

```python
def _reference_summary(path: str) -> str:
    """First non-empty content line of a reference file, heading marks stripped."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip().lstrip("#").strip()
                if line:
                    return line[:100]
    except (OSError, UnicodeDecodeError):
        pass
    return ""
```

Add this method to `SkillsRegistry`:

```python
    def render_references_manifest(self, skill: Skill) -> str:
        """Markdown block advertising a skill's on-demand reference files."""
        if not skill.references:
            return ""
        lines = [
            "\n\n## Available references",
            f"Load one when needed with "
            f"use_skill(name='{skill.name}', resource='<key>'):",
        ]
        for key in sorted(skill.references):
            summary = _reference_summary(skill.references[key])
            bullet = f"- `{key}` (resource='{key}')"
            if summary:
                bullet += f" — {summary}"
            lines.append(bullet)
        return "\n".join(lines)
```

- [ ] **Step 4: Append the manifest in `execute_skill`**

In `execute_skill`, replace the final return:

```python
        # Default: inject SKILL.md content into the prompt
        return {"inject_prompt": skill.content}
```

with:

```python
        # Default: inject SKILL.md content into the prompt, plus a manifest of
        # any on-demand reference files the skill bundles (tier-3 disclosure).
        content = skill.content + self.render_references_manifest(skill)
        return {"inject_prompt": content}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_skills.py::TestSkillReferences -v`
Expected: PASS (all green).

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/extensions/skills.py tests/unit/test_skills.py
git commit -m "feat(skills): advertise references via an auto-generated manifest"
```

---

### Task 4: `use_skill` tool gains a `resource` parameter

**Files:**
- Modify: `freecad_ai/tools/freecad_tools.py` (`_handle_use_skill` ~lines 5305-5347; `USE_SKILL` ~lines 5350-5367)
- Test: `tests/unit/test_skills.py` (end-to-end through the handler), `tests/unit/test_tool_routing.py` (description guard)

**Interfaces:**
- Consumes: `SkillsRegistry.get_skill_resource` (Task 2).
- Produces: `use_skill(name, args="", resource="")` — when `resource` is non-empty, returns the reference file contents (or an error `ToolResult`); when empty, unchanged behavior (loads `SKILL.md` + manifest).

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_skills.py` (imports the handler directly):

```python
class TestUseSkillResource:
    def _refs_skill(self, tmp_path, monkeypatch):
        import freecad_ai.extensions.skills as skills_mod
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        sd = skills_dir / "debug-model"
        sd.mkdir()
        (sd / "SKILL.md").write_text("# Debug Model\n\nDiagnose broken models.\n")
        refs = sd / "references"
        refs.mkdir()
        (refs / "freecad-gotchas.md").write_text("# Gotchas\n\nAttachmentSupport not Support.\n")
        monkeypatch.setattr(skills_mod, "SKILLS_DIR", str(skills_dir))
        monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", str(tmp_path / "none"))

    def test_use_skill_resource_returns_file(self, tmp_path, monkeypatch):
        from freecad_ai.tools.freecad_tools import _handle_use_skill
        self._refs_skill(tmp_path, monkeypatch)
        result = _handle_use_skill("debug-model", resource="freecad-gotchas")
        assert result.success is True
        assert "AttachmentSupport" in result.output

    def test_use_skill_resource_unknown_errors(self, tmp_path, monkeypatch):
        from freecad_ai.tools.freecad_tools import _handle_use_skill
        self._refs_skill(tmp_path, monkeypatch)
        result = _handle_use_skill("debug-model", resource="missing")
        assert result.success is False
        assert "freecad-gotchas" in result.error

    def test_use_skill_without_resource_still_loads_skill(self, tmp_path, monkeypatch):
        from freecad_ai.tools.freecad_tools import _handle_use_skill
        self._refs_skill(tmp_path, monkeypatch)
        result = _handle_use_skill("debug-model")
        assert result.success is True
        assert "Diagnose broken models." in result.output
```

Add to `tests/unit/test_tool_routing.py` (import `USE_SKILL`):

```python
class TestUseSkillDescription:
    def test_advertises_resource_two_step(self):
        from freecad_ai.tools.freecad_tools import USE_SKILL
        desc = USE_SKILL.description.lower()
        assert "resource" in desc
        assert "reference" in desc
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_skills.py::TestUseSkillResource tests/unit/test_tool_routing.py::TestUseSkillDescription -v`
Expected: FAIL — `_handle_use_skill` takes no `resource` kwarg; description lacks "resource".

- [ ] **Step 3: Add the `resource` branch to the handler**

In `freecad_ai/tools/freecad_tools.py`, change the `_handle_use_skill` signature and add the branch right after the registry is built:

```python
def _handle_use_skill(name: str, args: str = "", resource: str = "") -> ToolResult:
    """Load a skill's instructions (or one of its reference files) for the model.

    With `resource` set, returns the contents of that reference file (tier-3
    progressive disclosure). Otherwise returns SKILL.md plus a manifest of any
    references the skill bundles.
    """
    from ..extensions.skills import SkillsRegistry
    registry = SkillsRegistry()

    if resource:
        res = registry.get_skill_resource(name, resource)
        if "error" in res:
            return ToolResult(success=False, output="", error=res["error"])
        return ToolResult(success=True, output=res["output"])

    result = registry.execute_skill(name, args)
    # ... existing body unchanged ...
```

(Leave the rest of the existing function body exactly as-is below the new branch.)

- [ ] **Step 4: Add the `resource` parameter and update the description**

Replace the `USE_SKILL` definition's `description` and `parameters`:

```python
USE_SKILL = ToolDefinition(
    name="use_skill",
    description=(
        "Load a skill's detailed instructions for a complex task. "
        "Skills provide step-by-step construction guides (e.g. enclosure, gear). "
        "Call this when the user's request matches a skill, then follow the "
        "returned instructions using your tools. If the skill lists 'Available "
        "references', pull one into context on demand by calling use_skill again "
        "with the same name and the reference's `resource` key."
    ),
    parameters=[
        ToolParam("name", "string",
                  "Skill name (e.g. 'enclosure', 'gear', 'fastener-hole')"),
        ToolParam("args", "string",
                  "User's parameters for the skill (e.g. '120x80x60mm, screw lid')",
                  required=False, default=""),
        ToolParam("resource", "string",
                  "Optional reference key from the skill's 'Available references' "
                  "list, to load that reference file instead of the skill itself",
                  required=False, default=""),
    ],
    handler=_handle_use_skill,
    category="query",
)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_skills.py::TestUseSkillResource tests/unit/test_tool_routing.py::TestUseSkillDescription -v`
Expected: PASS.

- [ ] **Step 6: Run the full unit suite (regression)**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/ --ignore=tests/unit/test_document_attach.py -q`
Expected: all green (baseline 946 + new tests). `test_document_attach.py` is excluded because it Qt-segfaults on clean master.

- [ ] **Step 7: Commit**

```bash
git add freecad_ai/tools/freecad_tools.py tests/unit/test_skills.py tests/unit/test_tool_routing.py
git commit -m "feat(skills): use_skill resource param loads reference files (closes #37)"
```

---

## Self-Review

**Spec coverage:**
- Tier-3 `references/` loading → Tasks 1–4. ✓
- Extend `use_skill` (not a new tool) → Task 4. ✓
- Auto-generated manifest with exact invocation syntax → Task 3 (`render_references_manifest`, asserted in test). ✓
- Security-by-construction (key lookup, no path handling) → Task 2 (`get_skill_resource`) + traversal test. ✓
- Unknown-key error lists available → Task 2. ✓
- Top-level scan only → Task 1 (`os.listdir`, `isfile` filter, no recursion). ✓
- Backward compatibility (no references → unchanged) → Task 1 empty-dict test + Task 3 no-manifest test. ✓
- All headless-unit-testable → every task's tests avoid FreeCAD. ✓
- `scripts/`/`assets/`/nesting out of scope → not implemented. ✓

**Placeholder scan:** none — every code and test step shows complete content.

**Type consistency:** `Skill.references: dict[str,str]` (Task 1) is consumed unchanged by `get_skill_resource` (Task 2), `render_references_manifest` (Task 3), and via the handler in Task 4. `get_skill_resource` returns `{"output"|"error"}` (Task 2), consumed as such by the handler (Task 4). `render_references_manifest(skill)` name/signature consistent between Task 3 definition and its call in `execute_skill`. Handler signature `_handle_use_skill(name, args="", resource="")` matches the `USE_SKILL` parameter list.

**Note on commits:** the per-task commit steps follow the repo's TDD convention; run them during execution, when the maintainer has authorized committing.
