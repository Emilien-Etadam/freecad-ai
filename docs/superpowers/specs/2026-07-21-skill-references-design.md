# Design: skill `references/` resource loading (progressive disclosure, tier 3)

- **Date:** 2026-07-21
- **Status:** Approved design, pending implementation plan
- **Issue:** [#37](https://github.com/ghbalf/freecad-ai/issues/37) (@3dyuval)
- **Scope:** `references/` subdirectory only. `scripts/` is **explicitly out of
  scope** for v1 — see "Why not `scripts/` (yet)".
- **Base:** branch `feature/skill-references` off `master`. Self-contained; no
  dependency on the #38/#39 fixes.

## Background

Skills today have exactly two tiers of context loading, and no third:

1. **Tier 1 — metadata.** `SkillsRegistry.get_descriptions()`
   (`freecad_ai/extensions/skills.py:127`) injects every skill's name +
   description into the system prompt. Always present, cheap.
2. **Tier 2 — instructions.** `use_skill` (`freecad_tools.py:5350`) returns the
   whole `SKILL.md` as a tool result via `execute_skill` →
   `{"inject_prompt": skill.content}` (`skills.py:183`). Loaded on demand when
   the skill is invoked.

The [Agent Skills specification](https://agentskills.io/specification) and
[Claude's own Skills docs](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
define a **third tier**: bundled resource files (`references/`, `scripts/`,
`assets/`) loaded only when a task actually needs them, at *zero* context cost
until read. This workbench implements tiers 1 and 2 but not tier 3, so there is
no way to keep `SKILL.md` lean while shipping deep reference material — the exact
gap #37 describes.

**Why this matters more here than in a general agent:** issue #10 showed this
workbench already hits hard per-request context caps on some providers (GitHub
Models), which is why the tool reranker exists. A fat `SKILL.md` competes for
that same scarce budget on every invocation. Tier-3 references let a skill carry
extensive gotcha docs / fix procedures that cost nothing until the model chooses
to read one.

### The mechanism mismatch we have to design around

In every reference harness, tier 3 works because **the agent has a filesystem
and bash** and simply reads/runs the files (`cat references/FORMS.md`,
`python scripts/fill_form.py`). There is *no dedicated resource-loading API* —
the general-purpose Read/Bash tools are the mechanism.

This workbench **deliberately does not give the model a filesystem or bash
tool.** It exposes a curated ~50 domain tools, `use_skill`, and a sandboxed
last-resort `execute_code`. So the mechanism that makes tier 3 "free" elsewhere
does not exist here — we must synthesize a narrow equivalent.

## Approach

Extend the existing `use_skill` tool with an optional `resource` argument, rather
than adding a new tool.

- **Why extend, not add:** a new tool schema competes for the per-request budget
  that #10 already showed is scarce on some providers. Extending `use_skill`
  costs one parameter, and the model already reaches for `use_skill`.
- **Alternative considered — a dedicated `read_skill_file` tool.** Closer to how
  real harnesses model it, but adds tool surface and (if it took a path) a
  traversal boundary to police. Rejected on budget + safety grounds.

### Flow

1. `use_skill("debug-model")` → returns `SKILL.md` **plus an auto-generated
   "Available references" block** enumerating each reference file, a one-line
   summary, and the exact call to load it.
2. `use_skill("debug-model", resource="freecad-gotchas")` → returns the contents
   of `references/freecad-gotchas.md` as the tool result.

The auto-generated manifest is important: because the model can't list a
directory itself, the loader advertises the available references explicitly, with
the precise invocation syntax, so the model never has to guess a filename.

### Security by construction (no path handling of model input)

The loader scans `references/` **once** at registry-build time into a dict
`Skill.references: {key -> absolute_path}`. At load time the model passes a
**key**, which we look up in that dict. **Model input is never joined into a
filesystem path**, so `../` traversal, absolute paths, and symlink escapes are
structurally impossible — the model can only name a key that was pre-scanned from
inside the skill's own `references/` directory. An unknown key returns an error
listing the valid keys (mirroring the existing fuzzy skill-not-found UX in
`_handle_use_skill`).

This is a stronger guarantee than "validate the path" — there is no path to
validate.

### Trust boundary (why references are safe additively)

A reference file's contents are injected into the model's context, exactly like
`SKILL.md` already is. It is the **same trust boundary as tier 2** — the user
installed the skill; its markdown was already going to be trusted. `references/`
adds *more of the same kind of content*, not a new kind of capability. This is
the key contrast with `scripts/` (below), which would add an *execution* surface.

## Components

All changes are in `freecad_ai/extensions/skills.py` and the `use_skill` tool in
`freecad_ai/tools/freecad_tools.py`. No change to the agentic loop, config, or
system prompt.

1. **`Skill` dataclass** — add `references: dict = field(default_factory=dict)`
   mapping a normalized key → absolute file path.

2. **`SkillsRegistry._scan_skills_dir`** — after locating `SKILL.md`, scan a
   sibling `references/` directory (top level only). For each readable file,
   compute a key (the filename stem, lowercased) and store `key -> abspath`.
   Extract a one-line summary per file (first heading or first non-empty line —
   reuse the existing description-extraction logic) for the manifest. Undecodable
   files are skipped, matching the existing `SKILL.md` read behavior.

3. **`SkillsRegistry.get_skill_resource(name, key) -> dict`** — new method:
   look up the key in `skill.references`; on hit read the file (UTF-8) and return
   `{"output": content}`; on miss return
   `{"error": "..."}` listing available keys. Never touches model-supplied paths.

4. **References manifest helper** — render an "Available references" markdown
   block from `skill.references` + summaries, appended to the `inject_prompt`
   content in `execute_skill`/`_handle_use_skill` when the skill has any
   references. Absent entirely when it has none (no behavior change for existing
   skills).

5. **`use_skill` tool** — add an optional `resource` string parameter. When
   present, the handler routes to `get_skill_resource` instead of loading
   `SKILL.md`. Description updated to explain the two-step read pattern.

## Data flow

```
model: use_skill(name="debug-model")
  -> _handle_use_skill -> execute_skill -> inject_prompt = SKILL.md
     + "\n\n## Available references\n- freecad-gotchas — getObjectsByLabel,
        AttachmentSupport, isNull... (load: use_skill name='debug-model'
        resource='freecad-gotchas'))\n- fix-attachment — ..."
  <- tool result

model: use_skill(name="debug-model", resource="freecad-gotchas")
  -> _handle_use_skill (resource branch) -> get_skill_resource("debug-model",
        "freecad-gotchas") -> read references/freecad-gotchas.md
  <- tool result = file contents
```

## Error handling

- **Unknown resource key** → `ToolResult(success=False, error="Reference
  'X' not found in skill 'debug-model'. Available: freecad-gotchas,
  fix-attachment")`. Same shape/spirit as the existing unknown-skill error.
- **`resource` given for a skill with no `references/`** → error stating the
  skill has no references.
- **Undecodable / unreadable file at read time** → error; such files are also
  omitted from the manifest so this should be unreachable in practice.
- **Skill not found** with a `resource` set → the existing fuzzy-match path runs
  first (unchanged); resource resolution only happens once a skill is resolved.

## Backward compatibility

Fully additive. Skills without a `references/` directory get an empty
`references` dict, no manifest block, and behave exactly as today. No config
field, no migration, no system-prompt change.

## Testing (all headless-unit-testable, no FreeCAD needed)

New tests in `tests/unit/test_skills.py`:

1. Loader discovers files in `references/` and populates `Skill.references`.
2. A skill with no `references/` dir has an empty `references` dict (regression /
   backward-compat guard).
3. `get_skill_resource` returns file contents for a valid key.
4. `get_skill_resource` with an unknown key returns an error listing valid keys.
5. **Traversal is impossible:** a key like `"../SKILL"` or `"../../secret"` is
   simply "not found" (proves model input never reaches the filesystem as a path).
6. The "Available references" manifest is appended to the `use_skill` result when
   references exist, and *absent* when they don't.
7. `use_skill(resource=...)` routes through the tool handler and returns the file
   contents (end-to-end through `_handle_use_skill`).
8. Manifest advertises the exact invocation syntax (a text-assertion guard, like
   the #28 routing guards, so the steering can't silently regress).

Follow TDD: each behavior gets a failing test first.

## Why not `scripts/` (yet)

`references/` is a **read** feature; `scripts/` is an **execute** feature, and
that difference collides with the workbench's deliberately-controlled execution
model (Plan/Dangerous mode, the #14/#18 sandbox validator, `execute_code` framed
last-resort). Concretely:

- **No invocation path exists.** With no bash/filesystem tool, `scripts/` is
  inert unless we add a tool that lets the *model* run a skill's script file —
  i.e. move the trust boundary from "harness invokes one fixed entrypoint" to
  "model chooses which of N files to execute."
- **It forces a trust decision with no free answer.** A skill script run
  in-process like `handler.py` (`skills.py:185`, `importlib.exec_module`, no
  sandbox, full FreeCAD access) would **bypass every safety layer** the sandbox
  work built — now triggered by the model. Run through the `execute_code`
  sandbox instead and `scripts/` becomes *almost exactly `execute_code` with a
  file as its source* — largely duplicating an existing tool.
- **`handler.py` already occupies this niche**, narrowly: one `execute(args)`
  entrypoint, harness-invoked, not model-chosen. `scripts/` widens that to N
  model-invokable entrypoints, dragging in per-script interface contracts,
  Dangerous-mode consistency questions, and a larger segfault blast radius
  (in-process scripts share the FreeCAD process).

So `scripts/` is not a folder — it is an execution-model change. It should be its
own scoped proposal *if and when* a concrete need appears that `handler.py`
cannot serve, and ideally with a proposed answer to the trust question above.
`references/` delivers the progressive-disclosure value #37 actually argues for,
at zero new execution surface.

## Out of scope for v1

- `scripts/` and `assets/` subdirectories.
- Recursive/nested reference directories (spec recommends keeping references one
  level deep; v1 scans top-level files only).
- Any UI surface for browsing references (they are a model-facing mechanism).
