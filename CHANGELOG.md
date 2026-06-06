# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.16.1-alpha] - 2026-06-06

A bug-fix patch for [issue #14](https://github.com/ghbalf/freecad-ai/issues/14): generated code could time out on large or detailed models regardless of the operation, with no way to extend the limit.

### Fixed

- **Code-execution timeout is now configurable, and the default was raised 30 → 60s** (`freecad_ai/core/executor.py`, `freecad_ai/config.py`, `freecad_ai/ui/settings_dialog.py`). The budget applied to **both** the headless sandbox pre-check and live execution was hardcoded at 30s. Heavy-but-valid geometry operations — most notably scaling a detailed or imported model via `Shape.transformGeometry`, whose cost grows with the model's face count — genuinely exceed 30s and were killed on both paths with no recourse. The previous patch ([0.15.1-alpha](https://github.com/ghbalf/freecad-ai/releases/tag/v0.15.1-alpha)) only moved the wall from 15s to 30s. A new **"Code execution timeout"** setting (Settings, range 5–600s) now controls this budget; `execute_code` reads it when no explicit timeout is given, so large-model users can raise it as needed. ([issue #14](https://github.com/ghbalf/freecad-ai/issues/14), reported by trougnouf; still-broken reports by JohnMcLear and galberding) ([#24](https://github.com/ghbalf/freecad-ai/pull/24))

### Tests

- Unit: `tests/unit/test_config.py` (`execution_timeout` default and round-trip) and `tests/unit/test_executor.py::TestConfigurableExecutionTimeout` (`execute_code` honors the configured value and the new 60s default when no explicit timeout is passed).

## [0.16.0-alpha] - 2026-06-03

A feature release adding a datum-geometry and transform/duplicate toolset — sketching on faces and named planes, parametric datum planes and lines, relative transforms, and independent parametric copies — together with a sandbox fix that unblocks editing imported (mesh→solid) parts. This work grew out of [issue #18](https://github.com/ghbalf/freecad-ai/issues/18) (reported by 0xrushi): a snap-fit workflow on an imported solid that fell out of the parametric toolchain.

### Added

- **Sketch on a planar face or a named plane** (`freecad_ai/tools/freecad_tools.py:_handle_create_sketch`). `create_sketch` gained `support`/`face` parameters and a GUI-selection fallback: it can now attach to a planar face of an existing object — including a standalone imported mesh→solid, where it creates a standalone sketch — or to a datum/origin plane by name, not just the XY/XZ/YZ origin planes. Planar-face validation; offset along the face normal. ([#20](https://github.com/ghbalf/freecad-ai/pull/20))
- **`create_datum_plane` tool** — a parametric datum plane offset (parallel) from an origin plane, a planar face, an existing plane, or the current selection. Creates a `PartDesign::Plane` (inside a Body, or standalone), referenceable as `create_sketch(support=...)`. ([#21](https://github.com/ghbalf/freecad-ai/pull/21))
- **`create_datum_line` tool** — a datum line (axis) defined by two points, a straight edge of an object, or an origin axis (X/Y/Z). Usable as a `revolve_sketch` rotation axis or a mirror reference. ([#22](https://github.com/ghbalf/freecad-ai/pull/22))
- **`duplicate_object` tool** — an independent, history-preserving copy of an object's whole feature tree (e.g. a Body with its sketches and pads), leaving the original unchanged, with an optional relative translate/rotate to offset the copy in one call. ([#23](https://github.com/ghbalf/freecad-ai/pull/23))

### Changed

- **`transform_object` is now relative by default** (`freecad_ai/tools/freecad_tools.py:_handle_transform_object`). Translation adds to the object's current position and rotation spins it in place about its own origin; `0` means no change. Previously the tool overwrote the placement absolutely, which silently reset an object to the world origin when asked only to rotate it. Pass `relative=False` to restore the absolute-overwrite behavior. ([#23](https://github.com/ghbalf/freecad-ai/pull/23))

### Fixed

- **Sandbox validator blamed candidate code for pre-existing invalid shapes** (`freecad_ai/core/executor.py`). The headless dry-run walked every object in the document and reported any with an invalid/null shape, with no baseline — so a document already containing an OCC-invalid object (very common with imported mesh→solid conversions) made every subsequent edit fail the post-execution check, naming objects the code never touched. The validator now snapshots each object's problem state before running the candidate code and reports only objects that are **new** or **newly broken**. ([#18](https://github.com/ghbalf/freecad-ai/issues/18)/[#19](https://github.com/ghbalf/freecad-ai/pull/19), reported by 0xrushi)

### Tests

- Unit: `tests/unit/test_sketch_attachment.py`, `test_datum_plane.py`, `test_datum_line.py`, `test_transform_duplicate.py`, and `test_executor.py::TestCollectObjectIssues`.
- Integration (real FreeCAD): `tests/integration/test_sketch_attachment_integration.py`, `test_datum_plane_integration.py`, `test_datum_line_integration.py`, `test_transform_duplicate_integration.py`, and `test_sandbox_validation.py::TestSandboxIgnoresPreexistingInvalidity` — covering face/plane/line attachment, the relative-transform footgun fix, full-tree duplication, and the end-to-end "datum line + duplicate → parallel line" workflow.

## [0.15.2-alpha] - 2026-05-30

A bug-fix patch for [issue #17](https://github.com/ghbalf/freecad-ai/issues/17): cutting or fusing into an existing model could collapse its parametric feature tree, leaving the original sketches and features buried and uneditable.

### Fixed

- **Boolean operations collapsed the parametric feature tree** (`freecad_ai/tools/freecad_tools.py:_handle_boolean_operation`). Asking the AI to cut or fuse into an existing model could leave the original sketches and features buried and uneditable: a `Part::Cut`/`Fuse`/`Common` claims its operands as tree children, so the base Body was reparented underneath the boolean node and stopped being a top-level, editable object. `boolean_operation` now detects when **both** operands are PartDesign Bodies and routes through a parametric `PartDesign::Boolean` appended inside the base Body — the Body stays top-level with its full feature history intact and the result is identical geometry. Operations involving a plain Part shape still use a `Part::` boolean. ([issue #17](https://github.com/ghbalf/freecad-ai/issues/17), reported by 0xrushi)

### Changed

- **Tool-selection guidance now favors history-preserving edits** (`freecad_ai/core/system_prompt.py`). The Act-mode prompt steers the AI to modify an existing solid by appending a feature inside its Body (`create_primitive(operation="subtractive", body_name=...)`, `pocket_sketch`, etc.) rather than reaching for a Part-workbench boolean, and the `boolean_operation` tool description spells out that it is for two separate objects and auto-uses `PartDesign::Boolean` between Bodies.

### Tests

- `tests/integration/test_boolean_transform.py::TestBooleanHistoryPreservation` — cutting and fusing two PartDesign Bodies keeps the base Body top-level, produces a `PartDesign::Boolean` (never a `Part::Cut`), and yields a valid shape.

## [0.15.1-alpha] - 2026-05-29

A bug-fix patch driven by three GitHub issues: spurious sandbox timeouts on valid-but-slow code ([#14](https://github.com/ghbalf/freecad-ai/issues/14)), a stale default Anthropic model ([#15](https://github.com/ghbalf/freecad-ai/issues/15)), and a dark UI rendered with unreadable light-on-dark text when set via a StyleSheet alone ([#16](https://github.com/ghbalf/freecad-ai/issues/16)).

### Fixed

- **Spurious "Sandbox: code timed out after 15 seconds"** (`freecad_ai/core/executor.py`). The headless sandbox pre-check was hard-capped at `min(timeout, 15)`s while the live execution armed a SIGALRM for the full `timeout` (default 30s). Valid-but-slow operations — e.g. scaling a complex shape with `Shape.transformGeometry` — failed the dry-run at 15s and never ran. The sandbox now gets the same time budget as execution, matching `validate_code()`. ([issue #14](https://github.com/ghbalf/freecad-ai/issues/14), reported by trougnouf)
- **Dark UI rendered unreadable when set via StyleSheet alone** (`freecad_ai/ui/message_view.py:_read_freecad_mode_name`). A user running a dark `.qss` (e.g. `OpenDark.qss`) without selecting a PreferencePack left the `Theme` preference empty, so detection fell through to the unreliable QPalette probe and painted light-on-dark text. The detector now consults the `StyleSheet` preference as a secondary signal after `Theme`. ([issue #16](https://github.com/ghbalf/freecad-ai/issues/16), reported by JohnMcLear)

### Changed

- **Default Anthropic model bumped to `claude-sonnet-4-6`** (was the stale dated alias `claude-sonnet-4-20250514`), across the `anthropic` and `openrouter` provider presets and the `ProviderConfig` default. ([issue #15](https://github.com/ghbalf/freecad-ai/issues/15), reported by JohnMcLear)

### Docs

- **Installation path updated for FreeCAD 1.1+** version-scoped user dirs (`~/.local/share/FreeCAD/v1.1/Mod/`), with a troubleshooting note for the "installed but doesn't appear" case. ([issue #14](https://github.com/ghbalf/freecad-ai/issues/14), reported by trougnouf)

### Tests

- `tests/unit/test_executor.py::TestSandboxTimeout` — parametrized check that the sandbox dry-run receives the full configured timeout, not a value capped at 15s.
- `tests/unit/test_theme_detection.py::TestStyleSheetFallback` — StyleSheet consulted when Theme is empty, Theme takes precedence over StyleSheet, both-empty falls back to `Custom/Unknown`.

## [0.15.0-alpha] - 2026-05-23

Adds a `run_macro` tool, a configurable agentic loop count with a Stop button, an opt-in **Dangerous mode** that trades the workbench's code-safety checks for power-user reach, and shell-style Up/Down history in the chat input. The first three are driven by [issue #13](https://github.com/ghbalf/freecad-ai/issues/13).

### Added

- **`run_macro` tool** — runs an existing FreeCAD macro file and feeds its console output back to the AI. In normal mode it accepts a bare macro name resolved from FreeCAD's macro directory; file paths require Dangerous mode. ([issue #13](https://github.com/ghbalf/freecad-ai/issues/13))
- **Configurable agentic loop count** (Settings → "Max tool-loop turns"). `0` means endless; previously hardcoded at 30. Default remains 30.
- **Stop button** — the Send button becomes "Stop" while the AI is working and interrupts the loop (the only brake when the loop is set to endless).
- **Dangerous mode** — a session-scoped toggle that disables the code safety checks (static pattern blocking, headless sandbox pre-check, execution timeout) and lets `run_macro` run files from any path. Off at every launch; a red banner shows whenever it is active. **Use at your own risk** — see README.
- **Input history in the chat input** — Up/Down navigates through the current conversation's prior user messages, shell-style: first Up saves whatever you'd typed as a draft, walks newest→oldest with no wrap; Down walks back, returning the saved draft past the newest entry. Caret-position gated so multi-line editing still works. Scope follows the conversation — switching conversations gives you that conversation's history with no new storage on disk.

## [0.14.3-alpha] - 2026-05-15

A small but data-preserving patch driven by [issue #12](https://github.com/ghbalf/freecad-ai/issues/12): users on the **Custom** LLM provider lost their gateway URL and model on every FreeCAD restart, with the provider selector reverting to "anthropic" while the custom-provider field values stayed visible.

### Fixed

- **Custom-provider persistence across restart** (`freecad_ai/config.py:_write_to_param_store`). The FreeCAD `ParamGet` mirror only encodes the 11 providers that appear in the Edit → Preferences dropdown — `custom`, `github`, `huggingface`, and `zhipu` are not in that list. Previously, saving a non-prefs provider left the previous `ProviderIndex` (typically `0` = anthropic) in the param store, which then shadowed the JSON's correct `provider.name` on the next `load_config()`. The fix clears the stale index via `group.RemInt("ProviderIndex")` when the current provider isn't representable in the combo, so the load path falls back to the JSON value. Round-trip is now symmetric.
- **Settings dialog wiped custom-provider fields on switch** (`freecad_ai/ui/settings_dialog.py:_on_provider_changed`). The "custom" preset ships empty `base_url` and `default_model`, and the dialog was applying them unconditionally — switching the combo to "custom" instantly cleared any gateway URL or model name the user had typed. Now `setText` is skipped when the preset value is empty, so switching to "custom" preserves whatever's in the form. Real providers always have non-empty preset values, so their behavior is unchanged.

### Tests

- 3 new tests in `tests/unit/test_config.py::TestParamStoreBridge` covering (a) save-then-reload of a non-prefs provider with a stale `ProviderIndex` in the param store, (b) the same guarantee parametrized across every provider in `PROVIDERS` but absent from `_PARAM_PROVIDERS`, and (c) a drift guard that fails if `_PARAM_PROVIDERS` ever names a provider not registered in `providers.py`. The `FakeGroup` fixture gained `RemInt`/`RemString`/`RemBool` to mirror real `ParamGet` semantics.
- 3 new tests in `tests/unit/test_settings_dialog_provider_change.py` exercising `_on_provider_changed` via the unbound-method-with-fake-self pattern: switching to "custom" preserves fields, switching to a real provider applies preset values, out-of-range index is a no-op.

## [0.14.2-alpha] - 2026-05-09

A small UI patch: the chat panel painted dark even with FreeCAD set to a light theme.

### Fixed

- **Theme detector at `freecad_ai/ui/message_view.py:_is_dark_mode`** read `QTreeView.palette().color(Base)` before consulting FreeCAD's `Theme` preference. On Linux when the host Qt theme is dark, the palette reports dark Base even though FreeCAD's QSS stylesheet renders the workbench as light — so the chat input, MCP status banner, and message view painted dark while the rest of the FreeCAD window painted light. Reordered to name-first: trust the user's selected `Theme` ("light"/"classic"/"default" → light, "dark" → dark) and only fall back to palette introspection when the name is empty/custom. Adds 13 parametrized regression tests in `tests/unit/test_theme_detection.py`.

## [0.14.1-alpha] - 2026-05-09

A targeted patch driven by [issue #10](https://github.com/ghbalf/freecad-ai/issues/10): users picking the GitHub provider hit a confusing 400 in Act mode because (a) GitHub Models' per-request input cap is much smaller than the model's native context, and (b) two built-in tool schemas were rejected by GitHub's strict JSON Schema validation. Both are now fixed, and the github provider preset ships a sensible reranker default so first-time users don't have to debug their way to it.

### Added

- **GitHub provider preset now ships a recommended reranker default** (`{"method": "keyword", "top_n": 8}`). GitHub Models enforces a small per-request input cap independent of the underlying model's native context window — with ~50 built-in tool schemas attached, a fresh Act-mode turn overshoots it before the user types anything. The Settings dialog applies this on provider switch only when the reranker UI is still at its factory default (off + top_n=15), so an explicit user choice — even "off" — is never silently overwritten. Done via a new `default_rerank` field on the provider preset that mirrors the existing `default_params` pattern; other providers don't ship a recommendation today (anthropic and openai-direct have generous per-request budgets, and the right `top_n` is workload-dependent elsewhere).

### Fixed

- **`create_assembly` and `add_part_to_assembly` tool schemas** declared `array`-typed parameters (`part_names`, `position`) without an `items` keyword. Anthropic and Ollama silently accept this; OpenAI's marketplace API (GitHub Models) enforces the JSON Schema spec and rejects the request with `invalid_function_parameters`. Surfaced by issue #10 once the keyword reranker reduced the prompt enough to clear the input-size cap. Added a regression test in `tests/unit/test_registry.py` that walks every built-in tool and asserts no array property is missing `items`, so future tool additions can't reintroduce the same class of bug.

## [0.14.0-alpha] - 2026-05-06

Authoring hooks and user tools is now a first-class flow inside Settings: **New…** writes a starter template and opens it for editing, **Edit…** opens the selected file. A new "Editor" preference routes file edits through either FreeCAD's docked Python editor (default) or the user's OS-default editor — keeping with the workbench's principle of not constraining users to its choice of tools.

### Added

- **New… button on Hooks and User Tools panels** in `freecad_ai/ui/settings_dialog.py`. Prompts for a name (kebab-case dir for hooks, snake_case identifier for user tools), writes a starter template, opens the file in the configured editor. Templates live in `freecad_ai/extensions/file_templates.py`:
  - **Hook template** is registry-sourced from `freecad_ai/hooks/registry.VALID_EVENTS` — one `on_<event>(context)` stub per valid event, so adding a new event in the registry automatically extends the template with no manual sync.
  - **User-tool template** ships a typed example function with a docstring — passes `validate_file()` clean (no warnings) the moment the user saves it.
- **Edit… button on both panels** — opens the selected hook's `hook.py` or the selected user-tool file in the configured editor.
- **Editor preference** — `AppConfig.use_external_editor: bool = False`. New "Editor" group in Settings with a single checkbox: *"Open hooks and user tools in the OS-default editor (instead of FreeCAD's docked script editor)"*. Defaults off (FreeCAD editor); opt in for vim/VS Code/etc. workflows. Read live from the checkbox at routing time, so toggling and clicking New/Edit applies immediately even if the dialog is later cancelled (no save required for the toggle to take effect for the current action).

### Behavior

- **FreeCAD editor path** (default): clicking New/Edit prompts **Save / Discard / Cancel** because `Gui::PythonEditor` is an MDI sub-window of `MainWindow` and is unreachable while a modal dialog is up. Save and Discard both close the Settings dialog before opening the file; Cancel keeps the dialog open and aborts the action (no debris — for New, the file isn't written until after the prompt is confirmed).
- **External editor path**: opens via `QtGui.QDesktopServices.openUrl()` — no prompt, Settings dialog stays open, the list refreshes to show the new entry.

### Tests

- 10 new tests in `tests/unit/test_file_templates.py`: hook template parses, contains exactly one handler per `VALID_EVENTS` entry (in both directions — no missing, no extras), includes the hook name in its docstring; user-tool template parses, passes `validate_file()` with zero warnings, the function name matches the input. Plus `AppConfig.use_external_editor` default-is-False, JSON save/load roundtrip, and a backwards-compat assertion that older configs without the field load with the default.

## [0.13.1-alpha] - 2026-05-01

Patch release. Fixes a v0.13.0-alpha follow-up: session logs were still writing to the legacy hardcoded path after the migration. Adds bounded retention so `<FreeCADAI dir>/conversations/` and `<FreeCADAI dir>/logs/` no longer grow without limit.

### Fixed

- **Session logs now follow `CONFIG_DIR`** — `_save_session_log` and `_auto_save_log` in `freecad_ai/ui/chat_widget.py` had a hardcoded `~/.config/FreeCAD/FreeCADAI/logs` path that escaped the v0.13.0-alpha config-dir migration. After upgrade the rest of the workbench config moved to `<FreeCAD user config dir>/FreeCADAI/` but new session logs continued landing in the legacy unversioned location. Both methods now use the new `LOGS_DIR` constant from `freecad_ai/config.py`, which is computed as `os.path.join(CONFIG_DIR, "logs")` and so picks up any future config-dir change automatically.

### Added

- **Opt-in retention, configurable via `config.json`** — `Conversation.save()` and `_save_session_log()` can now prune the oldest files in their respective directories, but **disabled by default** to preserve v0.13.0-alpha behavior on upgrade. Three new `AppConfig` fields, all defaulting to `0` (= dimension disabled):
  - `max_saved_conversations` — count cap on `<FreeCADAI dir>/conversations/conv_*.json`. Suggested opt-in: `100` (the Load dialog already only shows the newest 20).
  - `max_session_logs` — count cap on `<FreeCADAI dir>/logs/session_*.json`. The auto-saved `latest_session.json` is a single file and exempt. Suggested opt-in: `50`.
  - `max_retention_age_days` — age cap applied to both directories. Files older than this are deleted regardless of count.
  - Both dimensions combine: a file is kept only if it's both within the newest-N AND younger than the age cap. Setting all three to `0` (the default) disables retention entirely — nothing is ever auto-deleted.
- **`prune_oldest_files(directory, pattern_fn, keep, max_age_days=0)`** in `freecad_ai/config.py` — generic helper used by both call sites. Files ranked by mtime (newest preserved). Best-effort: missing directories and individual `unlink` failures don't disrupt save paths.

### Tests

- 12 new tests in `TestPruneOldestFiles`, `TestLogsDir`, and `TestRetention`: mtime-ordered pruning, pattern filter, below-cap short-circuit, missing-directory no-op, age-cap-only pruning, count-and-age combined (union semantics), zero-zero disables pruning, `LOGS_DIR` invariant under `CONFIG_DIR`, `_ensure_dirs` creates `LOGS_DIR`, save-prunes-by-count, save-below-cap-keeps-everything, save-prunes-by-age, and a backwards-compat assertion that a default `AppConfig` leaves 201 pre-existing files untouched on save. Full unit suite: 763 passed, 11 skipped.

## [0.13.0-alpha] - 2026-05-01

Aligns the workbench's config dir with FreeCAD 1.1's version-scoped user dirs. Reported by @egandro on issue #9 — users on FreeCAD 1.1+ saw two `FreeCADAI/` trees side-by-side: the live unversioned one at `~/.config/FreeCAD/FreeCADAI/`, plus a stale snapshot inside `~/.config/FreeCAD/v1-1/` that FreeCAD 1.1's own first-launch migration of the legacy `~/.config/FreeCAD/` tree created. Documentation referenced the unversioned path throughout, but FreeCAD's actual version-scoped config layout drifted from where the workbench was writing.

### Changed

- **Config dir now resolves to `<FreeCAD user config dir>/FreeCADAI/`** — `freecad_ai/config.py` no longer hardcodes `~/.config/FreeCAD/FreeCADAI/`. New resolution order (highest to lowest precedence): `$FREECAD_AI_CONFIG_DIR` env var → `<user-config-dir>/FreeCADAI/` (on FreeCAD 1.1+ Linux: `~/.config/FreeCAD/v1-1/FreeCADAI/`) → `~/.config/FreeCAD/FreeCADAI/` (legacy fallback for pytest, console scripts, plain Python REPL). The user-config-dir is obtained via `FreeCAD.getUserConfigDir()` if the API exposes it, otherwise derived from `FreeCAD.Version()` plus `$XDG_CONFIG_HOME`. Constants `CONFIG_DIR`, `CONFIG_FILE`, `CONVERSATIONS_DIR`, `SKILLS_DIR`, `USER_TOOLS_DIR`, `HOOKS_DIR` are still importable module attributes — value is computed once at import time, so all 30+ consumers keep working without code changes.
- **Path lives under `XDG_CONFIG_HOME`, not `XDG_DATA_HOME`** — workbench data is config-shaped (settings, secrets, conversation logs) so it belongs alongside FreeCAD's own `FreeCAD.conf` / `user.cfg` / `system.cfg`. `Mod/` and `Macro/` (under `XDG_DATA_HOME`) are for code, not config; the FreeCAD AI workbench code itself is still installed under `Mod/freecad-ai/`.

### Migration

- **One-shot config migration on first launch of v0.13.0-alpha+** — runs lazily on first import of `freecad_ai.config` inside FreeCAD. Rename-then-move with a two-stage candidate search:
  1. If `<target>` already exists *without* a marker (e.g. a stale `FreeCADAI/` left by FreeCAD's own first-launch migration), rename it to `<target>.pre-v0.13-snapshot/` — frees the name without overwriting.
  2. Pick the first **historical candidate** with user content as migration source. Order: (a) `<FreeCAD user data dir>/FreeCADAI/` (the v0.13.0-alpha pre-release wrote here briefly under XDG_DATA_HOME, only relevant on the maintainer's machine), (b) `~/.config/FreeCAD/FreeCADAI/` (every released build before v0.13.0-alpha).
  3. `shutil.move` the source → target. Atomic same-filesystem rename when possible, copy-then-remove fallback for cross-device moves. Source ceases to exist.
  4. **Sweep**: any remaining historical candidates that still have content get renamed to `<candidate>.duplicate-cleanup/` (timestamped if name collision). Catches duplicates left by an aborted prior migration. Sweep runs on every launch, not just first migration.
  5. Drop `.freecad_ai_active_marker` in target.
- **Idempotent on subsequent runs** — marker file blocks re-migration; the sweep still runs (cheap if no candidates exist).
- **Best-effort fallback on migration failure** — if `_migrate_to_target` raises, log to stderr and fall back to a candidate path that still exists so the workbench loads. Does not crash.
- **Collision-safe** — pre-existing `*.pre-v0.13-snapshot/` or `*.duplicate-cleanup/` from a prior aborted migration is preserved with a Unix-timestamp suffix appended to the new backup name.
- **`FREECAD_AI_CONFIG_DIR` env var override** — bypasses FreeCAD-based resolution entirely. Useful for isolated profiles, sync-friendly locations, or pinning a fixed path during testing.

### Docs

- **Canonical "Configuration paths" section** in README and wiki `Configuration.md` documents the resolution order, the five-step migration, backup-dir semantics, and the env-var override. Path references throughout README + wiki use the `<FreeCADAI dir>` placeholder consistently, with the canonical section as the source of truth — no more literal `~/.config/FreeCAD/FreeCADAI/` paths that drift from the actual location on FreeCAD 1.1+.
- **Wiki**: `Configuration.md`, `Architecture.md`, `Skills.md`, `Skills-Reference.md`, `Creating-Skills.md`, `Creating-Custom-Tools.md`, `Tool-Reranking.md`, `MCP-Integration.md`, `AGENTS-md.md`, `Getting-Started.md`, `FAQ.md` updated.

## [0.12.1-alpha] - 2026-04-28

Patch release fixing the Edit → Preferences page showing blank fields after a v0.12.0 install.

### Fixed

- **Edit → Preferences → FreeCAD AI page showed empty fields when JSON config was already populated** — `Gui::Pref*` widgets read directly from FreeCAD's parameter store at `BaseApp/Preferences/Mod/FreeCADAI`, but `_write_to_param_store` only fired when the user clicked Save in the AI Settings dialog. Users who upgraded from v0.11.x or earlier (where the bridge didn't exist) saw blanks on the preferences page until they re-saved through the dialog. `load_config` now mirrors the merged JSON+ParamGet result back to the param store on every load, so both UIs stay coherent on first activation. `InitGui.py` also calls `get_config()` after registering the preferences page so the seeding happens even when the user opens Edit → Preferences before activating the workbench.

## [0.12.0-alpha] - 2026-04-28

FreeCAD addon-index conformance — preparation for Addon Manager submission. Adds a FreeCAD-native preferences page (the convention every indexed workbench follows), promotes the existing `file:` / `cmd:` API-key indirection through documentation and tooltips, and fixes a silently-degrading PySide2 hard import that broke vision detection on FreeCAD 1.1.0 for non-Ollama providers.

### Added

- **Edit → Preferences → FreeCAD AI page** — a FreeCAD-native preferences entry point with 8 fields (provider, base URL, model, API key, max tokens, mode, thinking, enable tools), registered via `Gui.addPreferencePage` and backed by `Gui::Pref*` widgets that auto-save into FreeCAD's `BaseApp/Preferences/Mod/FreeCADAI` parameter store. Coexists with the existing AI Settings dialog: the dialog remains primary and exposes the full surface (MCP, skills, hooks, system prompt, model parameters, etc.); the preferences page exposes only the basics that map naturally to FreeCAD's flat parameter store.
- **ParamGet ↔ JSON config bridge** — `_apply_param_store_overrides` on load and `_write_to_param_store` on save keep both UIs in sync without restructuring the config layer. JSON stays primary (nested `mcp_servers`, `model_params`, dock state base64 don't flatten cleanly to ParamGet). Out-of-range enum indices in ParamGet are ignored defensively in case a user hand-edited the param file.
- **Cross-OS environment-variable expansion in `file:` API key prefix** — `os.path.expandvars` runs alongside `os.path.expanduser`, so `file:%APPDATA%\\freecad-ai\\token` works on Windows in addition to the existing `file:~/...` and `file:$HOME/...` syntaxes.
- **Secure API key storage UX** — README and the new preferences page both promote the existing `file:` / `cmd:` prefixes with per-OS examples (Linux `secret-tool`, macOS `security`, Windows CredentialManager / GPG-symmetric / DPAPI). Settings dialog API-key field gets a rich tooltip with the same examples. The maintainer is Linux-only — macOS and Windows examples ship with an explicit "untested" disclaimer.

### Fixed

- **PySide2 hard import in `_generate_probe_image` silently downgraded vision detection on FreeCAD 1.1.0** — the function had `from PySide2.QtGui import QImage, ...` inside a try/except. On FreeCAD 1.1.0 (PySide6 only) the ImportError fell through to a 1×1 white-pixel fallback meant for headless unit tests, and every non-Ollama provider's vision probe ran against that pixel — getting `vision_detected = False` regardless of actual model capability. Now routes through `freecad_ai/ui/compat.py`. The 1×1 fallback still exists for genuinely Qt-less environments but now logs a warning when it activates.

### Changed

- **License SPDX identifier normalized to `LGPL-2.1-or-later`** — the bare `LGPL-2.1` form normalizes to a non-FSF-Libre identifier per the FreeCAD addon Qualities checklist. License text in `LICENSE-CODE` is unchanged; only the `package.xml` `<license>` element was updated.

### Docs

- **Install instructions corrected** — `Resources/Documents/Overview.md` previously claimed Addon Manager install was available. It is not: the workbench is not in any FreeCAD addon registry yet. Submission is in progress (this release is part of that work). Direct clone / symlink remain the only install methods.

## [0.11.1-alpha] - 2026-04-28

Patch release fixing Ollama vision detection and extending the same `/api/show` capability check to tool calling and thinking. Reported by @MuhvICo on issue #8.

### Fixed

- **Ollama vision falsely reported as unsupported** — the previous `vision_probe()` rendered a 64×32 PNG with a 3-digit number and asked the model to OCR it. Many vision-capable models (qwen2.5vl, qwen3-vl, gemma4) handle real photos fine but choke on tiny low-resolution text, producing false negatives. The probe now consults Ollama's native `/api/show` endpoint for the model's `capabilities` array — authoritative for that provider — and only falls back to the OCR probe when `/api/show` is unavailable (older Ollama, transient errors).

### Added

- **Per-model tool-calling and thinking detection** — `/api/show` capabilities also surface `"tools"` and `"thinking"` per model. `AppConfig.supports_tools` now consults `tools_detected` before the provider-wide static flag, so accidentally selecting an Ollama embedding/reranker model (`nomic-embed-text`, `*reranker*`) as the main chat model no longer ships a tools array to a model that can't use it.
- **Capabilities summary in Settings dialog** — Test Connection now appends a one-liner like "Capabilities: tools: yes, thinking: no" to the status label and persists `tools_detected` / `thinking_detected` alongside `vision_detected`. All three reset when the user changes provider or model.

### Changed

- **Behavioral OCR probe enlarged from 64×32 / 16pt to 128×64 / 32pt** — empirical sweep against `qwen3-vl:32b` and `gemma3:4b` showed 64×32 sat right at qwen3-vl's image-preprocessing cliff (smaller inputs returned empty content in 0.1s — image rejected before inference). 128×64 gives 4× area headroom, both tested models hit 100%, PNG stays under 1KB. Only matters for non-Ollama providers and older Ollama without `/api/show`.

## [0.11.0-alpha] - 2026-04-23

Plan-mode feedback loop for local-LLM users: sandbox validation that catches FreeCAD's C++ console errors, Check and Fix-with-AI buttons in the Review Code dialog, and viewport screenshots attached to error-retry messages. Plus dock layout persistence — the chat widget now remembers its area, tab siblings, and floating geometry across sessions.

### Added

- **Check button in Review Code dialog** — runs the generated Python in the existing subprocess sandbox, hooks `App.Console.AddObserver` to catch FreeCAD's C++-logged errors (topological naming, attachment, recompute failures) that never raise Python exceptions, and walks `doc.Objects` to flag invalid or null shapes. Reports issues without touching the live document.
- **Fix with AI button in Review Code dialog** — always enabled. Opens a prompt composer pre-filled with a context-aware template (error, succeeded-but-wrong-output, or blank), which the user can edit before sending. Loops the generated code + feedback back to the LLM through the existing `_handle_execution_error` retry path.
- **Viewport capture on error retries** — when `_handle_execution_error` hands code back to the LLM (either from the Act-mode agentic loop or the new Fix button), the current viewport is attached to the retry message. Vision-capable models can now see the visual effect of broken code, not just the traceback. Respects the existing `capture_mode` setting (off / every_message / after_changes).
- **Chat dock layout persistence** — the FreeCAD AI dock now remembers its last area, tab siblings (e.g. tabified with Tasks), floating state, and geometry across FreeCAD sessions. Saves via `QMainWindow.saveState()` into AppConfig on dock signals, debounced move/resize, and a 3s safety-net poll. Save-enabled and shutdown guards prevent startup/teardown transients from overwriting the last good state.

### Fixed

- **Sandbox false-positive for FreeCAD console errors** — `_sandbox_test` previously wrapped `doc.recompute()` in `try/except` and reported success when no Python exception fired, even though the C++ layer had logged multiple `subshape not found` errors to the Report View. The validation now installs a Console observer before running user code and scans `doc.Objects` for invalid/null shapes after recompute, so C++-only failures surface as sandbox errors.

## [0.10.0-alpha] - 2026-04-21

Tool reranking — keyword and LLM-based filtering to keep the tool-schema token footprint small when many tools are registered.

### Added

- **Tool reranking** — opt-in per-turn filter that sends only the top-N most relevant tools to the LLM, plus a user-configured pinned set. Two methods available in Settings:
  - **Keyword** — pure-Python IDF-weighted token match. Zero extra LLM call, zero latency, lexical-only filtering.
  - **LLM** — semantic ranking via a small/fast LLM (same provider as main by default, or a full provider override for e.g. running reranking on a local Ollama model while the main chat uses a cloud provider). Hallucinated tool names are dropped; slots not filled by the LLM are topped up from the keyword reranker so the filter set is never under-sized.
- **Test Reranker button** in Settings — sends a canonical probe to the reranker LLM with current dialog values (no save needed) and displays success (with LLM-vs-top-up breakdown) or the exact provider error.
- **Diagnostic logging for reranking** — each LLM-reranker call prints its decision points (candidates sent, raw response preview, parsed count, top-up fired) to FreeCAD's Report View.
- **Registry filter plumbing** — `to_openai_schema`/`to_anthropic_schema`/`to_mcp_schema` accept `filter_names=...`; excluded tools skip `resolve_params()`, avoiding MCP schema-fetch round-trips when the reranker filters them out.

### Fixed

- **Ollama Base URL documentation** — clarified that Ollama's OpenAI-compatible endpoints live under `/v1/*`, not `/api/*`. A `/api/` base URL previously produced silent HTTP 404s.

## [0.9.0-alpha] - 2026-04-17

Sketch editing, image-to-sketch, file attachments.

### Added

- **`edit_sketch` tool** — unified tool for modifying existing sketches: add/remove geometry and constraints, or wipe everything with `clear_all=true` and provide fresh geometry. Makes iterative sketch refinement reliable without recreating from scratch.
- **`sketch-from-image` skill** (`/sketch-from-image`) — attach an image (PNG, JPG, SVG) and convert it to a constrained FreeCAD sketch at a specified real-world size. SVG inputs are read as text so the LLM parses coordinates directly. Works with vision-capable models or via a vision-fallback MCP. Handles rectangles, circles, polygons, and lines; curves are approximated.
- **Document attachments** — chat now accepts non-image files. Text files are read and included in the message; binary files (PDF, DOCX, etc.) fire a `file_attach` hook for user-defined processing. Drag-and-drop, paste, and the attach button all work.
- **MCP timeout configuration** — per-server tool call timeout in the Add/Edit MCP server dialog (default 600s).
- **Auto-generated sketch constraints** — `create_sketch` and `edit_sketch` now automatically add `DistanceX`, `DistanceY` for rectangles and `Radius` for circles. LLMs no longer need to specify dimensional constraints by hand.

### Fixed

- **Duplicate constraints in sketches** — explicit constraints now overwrite auto-generated ones instead of creating duplicates (matched by Type + First + Second geometry indices).

### Changed

- **Tool descriptions carry Y-axis warning** — SVG/image coordinates use Y-down while FreeCAD uses Y-up. Tool descriptions now remind LLMs to negate Y values when converting.
- **50 tools total** (was 48).
- **648 unit tests** (was 626).

## [0.8.0-alpha] - 2026-04-05

Parametric modeling, per-model parameters, batch operations, and multi-document support.

### Added

- **Parametric modeling with variable sets** — `create_variable_set` creates an `App::VarSet` with typed, named variables (length, width, height, etc.) editable in the Data panel. `create_spreadsheet` creates a `Spreadsheet::Sheet` with cell aliases as an alternative. Both work the same way with expression bindings.
- **`set_expression` tool** — bind any object property to an expression (`"Variables.length"`, `"Variables.wall * 2"`). Supports indexed properties (`Constraints[N]`) and nested properties (`Placement.Base.x`).
- **Expression support in `create_sketch`** — rectangle dimensions accept expression strings directly: `width="Variables.length"`. Adds DistanceX/DistanceY constraints and binds them automatically.
- **Expression support in `pad_sketch`** — length accepts expression strings: `length="Variables.height"`.
- **Per-model parameters** — freeform key-value parameter table in Settings (temperature, top_p, top_k, etc.), saved per model name. Providers can ship default parameter presets. Replaces the single global temperature field.
- **Strip thinking history** — tristate checkbox in Settings to remove thinking/reasoning content from conversation history. Auto-enabled for Gemma models, required by models that reject thinking content in multi-turn conversations.
- **Tool call summary** — compact visualization after tool loop: tool count, elapsed time, flow diagram (tool1 → tool2 → ...), and per-tool timing with success/failure indicators.
- **Batch edge/face operations** — `fillet_edges`, `chamfer_edges`, and `shell_object` now accept filter keywords: `"all"`, `"vertical"`, `"horizontal"`, `"top"`, `"bottom"`, `"front"`, `"back"`, `"left"`, `"right"`, `"circular"`. Filters can be combined: `["top", "vertical"]`.
- **Filtered queries** — `list_edges` and `list_faces` accept an optional `filter` parameter to show only matching edges/faces.
- **Constraint solver feedback** — `create_sketch` now reports constraint status (fully constrained, under-constrained with DOF count, over-constrained) and lists all constraints with dimension ones marked `← bindable`.
- **Multi-document support** — `list_documents` shows all open documents with object counts and active indicator. `switch_document` changes the active document by name or label.
- **Relative expressions in `modify_property`** — values can be `"+10%"`, `"-20%"`, `"*1.5"`, `"+5"`, `"-3"` for relative modifications instead of requiring absolute values.

### Fixed

- **Fillet/chamfer/shell on Bodies** — when called with a `PartDesign::Body` as `object_name`, now correctly uses `Body.Tip` as the feature base. Previously `_find_body_for` returned `None` (Body doesn't contain itself), causing silent failure.
- **Moonshot temperature** — removed hardcoded temperature overrides. Moonshot's parameters are now user-editable defaults via the Model Parameters table.

### Changed

- **Settings UI** — merged "Parameters" and "Model Parameters" into a single "Model Parameters" section with fixed fields (Max Output Tokens, Context Window) above the freeform key-value table.
- **48 tools total** (was 42).
- **626 unit tests** (was 577).

## [0.7.0-alpha] - 2026-04-03

Assembly tools, geometry query tools, rate limiting, and community contributions.

### Added

- **Assembly tools** — `create_assembly`, `add_assembly_joint`, `add_part_to_assembly` using FreeCAD's native Assembly workbench solver. Supports Fixed, Revolute, Cylindrical, Slider, and Ball joint types. Includes face selection guide in tool descriptions for correct joint setup.
- **`list_faces` tool** — list all faces of an object with names, descriptive labels (top, bottom, front, etc.), normals, center positions, and areas.
- **`list_edges` tool** — list all edges with names, descriptive labels (top-front horizontal, etc.), and lengths.
- **HTTP retry with exponential backoff** — `_http_post()` and `_http_stream()` now retry on 429 rate-limit errors with configurable max retries, Retry-After header support, and jittered exponential backoff.
- **Enhanced context extraction** — Pad/Pocket features now include sketch plane, offset, and geometry count. Revolution features include axis reference and angle.
- **Dark mode** — chat widget automatically adapts to FreeCAD's light/dark theme. Color palette cached for performance with `refresh_theme_cache()` available for runtime switching. (PR #5, @yas1nsyed)
- **GUI active document resolution** — tools and `execute_code` now prefer `FreeCADGui.ActiveDocument.Document` over `App.ActiveDocument`, fixing desync when multiple documents are open. New `active_document.py` module with `resolve_active_document()` / `get_synced_active_document()`. (PR #3, @dpappo)
- **OpenAI GPT-5 support** — `max_completion_tokens` instead of `max_tokens`, temperature omitted (GPT-5 rejects non-default values). Handled via `_apply_provider_overrides()`. (PR #3, @dpappo)
- **Sandbox document copy** — `execute_code` subprocess sandbox now opens a temp copy of the saved `.FCStd` file so `getObject()`-style code validates against real document state instead of an empty `SandboxTest` doc. (PR #3, @dpappo)

### Fixed

- **Assembly ViewProvider** — joints and grounded joints now get proper ViewProvider setup for GUI integration (icons, Simulation support).
- **OpenAI reasoning_content** — preserve `reasoning_content` field in message format for models that return chain-of-thought.
- **Dark mode for all widgets** — extended theme support to all UI widgets, not just chat. Added sandbox GUI stub for headless environments.

## [0.6.0-alpha] - 2026-03-28

New tools, Skills management, skill optimizer, hooks, and snap packaging fix.

### Added

- **Skill optimizer** — `/optimize-skill` command that iteratively improves SKILL.md files by running test cases, scoring results (completion, errors, geometric correctness, efficiency, visual similarity), and using the LLM to modify instructions. Includes PySide2 configuration dialog, version history with original backup, three optimization strategies (conservative, balanced, aggressive), and configurable network retry with exponential backoff. Inspired by [autoresearch](https://github.com/karpathy/autoresearch).
- **Built-in skills auto-discovery** — SkillsRegistry now scans both the repo's `skills/` directory and the user's `~/.config/FreeCAD/FreeCADAI/skills/`. User skills override built-in skills with the same name. No more manual copying of built-in skills.
- **Hooks system** — user-defined Python hooks that fire on lifecycle events (`pre_tool_use`, `post_tool_use`, `user_prompt_submit`, `post_response`). Hooks can block actions, modify input, or log activity. Directory-based discovery at `~/.config/FreeCAD/FreeCADAI/hooks/`. Includes built-in `log-tool-calls` hook and Settings UI for managing hooks.
- **Configurable context window** — new "Context Window" setting controls when automatic conversation compaction triggers. Set to your model's context limit or lower to control API costs.
- **`describe_model` tool** — comprehensive geometry summary of an object in one call: bounding box, volume, face/edge counts, hollow/solid detection, estimated wall thickness, and PartDesign feature list.
- **`redo` tool** — redo previously undone operations.
- **`undo_history` tool** — show the undo/redo stack with named transactions, so the model can see what's available before deciding what to undo.
- **`undo` enhanced** — new `until` parameter to undo back to a named transaction (e.g., `until="Pocket"`). Returns what was undone and remaining undo/redo counts.
- **Fuzzy skill matching** — `use_skill` now does substring search on skill names and descriptions when the exact name isn't found.
- **Skills management in Settings** — new "Skills" section showing all installed skills with status indicators (built-in, modified, user). "Reset to Built-in" button reverts stale user copies to the repo version.
- **"Model supports tool calling" checkbox** — `enable_tools` config exposed in Settings UI. Uncheck for models that don't support tool calling.
- **CONTRIBUTING.md** — contributor guide with fork/clone setup, commit conventions, and how to add skills/providers/tools.

### Fixed

- **Snap-packaged FreeCAD SSL** — handle missing `_ssl` module gracefully. HTTP connections (Ollama) work without SSL; HTTPS gives a clear error suggesting Ollama.
- **Snap tabs default clearance** — changed from 0.2mm to 1.0mm so tabs have proper protrusion even when the model omits the parameter.
- **`describe_model` FreeCAD Quantity** — cast `Base.Quantity` to `float` before formatting.
- **Settings Test Connection crash** — removed leftover `prompt_style_combo` reference.

### Changed

- **`create_inner_ridge` simplified** — extracted `_add_rect` helper, 28 lines → 18 lines.
- **37 tools total** (was 34).

## [0.5.0-alpha] - 2026-03-27

Autonomous skill invocation and editable system prompt.

### Added

- **`use_skill` tool** — the model can now autonomously invoke skills when the user's request matches one. Instead of redirecting users to type `/enclosure`, the model calls `use_skill("enclosure", "120x80x60mm, screw lid")`, gets the step-by-step instructions, and executes them with tools. Natural language "create an enclosure" now works end-to-end.
- **Editable system prompt** — the full system prompt is now visible and editable in Settings, with a "Reset to Default" button. Users can customize the instructions sent to the LLM.

### Fixed

- **Enclosure skill screw geometry** — screw posts now start from the floor surface (offset=T) instead of z=0, and screw holes use fixed depth (H-T) instead of through_all so they don't exit through the bottom wall.
- **PROVIDER_PRESETS consolidation** — eliminated duplicated provider config between `config.py` and `providers.py`. Adding a new provider is now a single-file change.

### Changed

- **Skills no longer redirect** — the system prompt no longer tells the model to ask users to type slash commands. The model uses `use_skill` to load instructions and executes them directly.

## [0.4.0-alpha] - 2026-03-26

Multi-provider support and tool calling reliability.

### Added

- **16 new LLM providers** — DeepSeek, Qwen (DashScope), Groq, Mistral, Together AI, Fireworks AI, xAI (Grok), Cohere, SambaNova, MiniMax, Llama (Meta), GitHub Models, HuggingFace, Zhipu (GLM), Moonshot (Kimi). All OpenAI-compatible with tool calling support. Total: 22 providers + custom.
- **Dynamic API key resolution** — API keys support `file:/path/to/token` (re-read each call) and `cmd:command` (run command, use stdout) prefixes to avoid storing keys in plaintext.
- **Smart object name resolution** — `_get_object()` auto-resolves common LLM naming mistakes (`Sketch0`→`Sketch`, `Sketch1`→`Sketch001`, `Body1`→`Body001`). Error messages now list available objects via `_suggest_similar()` for LLM self-correction.

### Fixed

- **Streaming `finish_reason` handling** — tool calls no longer silently dropped when providers return `"stop"` instead of `"tool_calls"` as the finish reason.
- **`tool_choice="auto"` now explicit** — some providers (e.g. Moonshot/Kimi-K2.5) require this to be set explicitly or they ignore tools entirely. Now sent with every OpenAI-compatible tool-calling request.
- **`reasoning_content` preservation** — thinking models (e.g. Kimi-K2.5) that return `reasoning_content` in assistant messages now have it preserved across agentic loop turns. Without this, multi-turn tool chaining broke after the first turn.
- **Moonshot parameter constraints** — temperature, top_p, and penalty values are automatically overridden to Kimi-K2.5's required fixed values. Temperature field is greyed out in Settings when Moonshot is selected.
- **Non-streaming `stop_reason` detection** — now correctly sets `stop_reason="tool_use"` when tool calls are present regardless of the provider's `finish_reason` value.

### Changed

- **Snap tabs as PartDesign features** — `create_snap_tabs` now creates `PartDesign::AdditiveBox` features inside the lid body instead of a standalone `Part::Feature`. Tabs are individually editable and compatible with fillet, chamfer, pattern, and other PartDesign tools.
- **Better tool success messages** — `create_sketch`, `create_body`, `pad_sketch`, and `pocket_sketch` now include explicit naming hints (e.g., "Use sketch_name='Sketch001' in pad_sketch/pocket_sketch").

## [0.3.0-alpha] - 2026-03-14

Vision routing, image support, user extension tools, and deferred MCP tool loading.

### Added

- **Vision routing** — automatically detect whether the LLM supports vision via a probe image during Test Connection. Vision-capable models receive images inline; non-vision models get images auto-described via an MCP `describe_image` tool (e.g., [llm-vision-mcp](https://github.com/ghbalf/llm-vision-mcp)). When no vision path exists, image controls (Capture, Attach, drag-drop, paste) are disabled. Includes a manual override checkbox in Settings.
- **Image support** — attach viewport screenshots and images to chat messages (Capture button, Attach button, drag-drop, paste)
- **User extension tools** — register custom Python functions (`.py` / `.FCMacro`) as LLM-callable tools. Functions with type hints are auto-discovered from `~/.config/FreeCAD/FreeCADAI/tools/`, validated via AST, and registered into the tool registry. Includes Settings UI for managing tools and optional FreeCAD macro directory scanning.
- **Deferred MCP tool loading** — tool schemas are loaded lazily on first use instead of eagerly on connect, configurable per-server via the `deferred` setting (default: `true`)
- **Tool search** — `MCPClient.search_tools()`, `MCPManager.search_tools()`, and `ToolRegistry.search_tools()` for keyword-based tool discovery across all registered tools
- **Lazy parameter resolution** — `ToolDefinition.lazy_params` callable and `resolve_params()` method for on-demand schema loading
- **Settings UI** — "Deferred tool loading" checkbox in the Add MCP Server dialog; server list shows `(deferred)` / `(disabled)` tags
- **24 new unit tests** for deferred loading, lazy params, tool search, and MCP manager integration

## [0.2.0-alpha] - 2026-02-24

PartDesign-native primitives, patterns, and multi-transform.

### Changed

- **`create_primitive` converted to PartDesign** — creates AdditiveBox, SubtractiveCylinder, etc. inside a Body instead of Part::Box/Part::Cylinder. Supports `operation="additive"|"subtractive"` and `body_name` for adding to existing bodies.
- **`create_wedge` converted to PartDesign** — now uses a loft-based approach instead of Part::Wedge
- **`shell_object` defaults to `reversed=True`** — inward shelling preserves outer dimensions (more intuitive default)
- **`multi_transform` accepts multiple features** — can chain linear pattern + polar pattern + mirror in one operation

### Added

- **`mirror_feature` tool** — mirror a PartDesign feature across XY, XZ, or YZ plane (`PartDesign::Mirrored`)
- **`multi_transform` tool** — chain multiple transformation patterns (linear, polar, mirror) in a single PartDesign::MultiTransform feature
- Integration tests for PartDesign `create_primitive` and `create_wedge`

### Fixed

- LLM stringified-list bug in `shell_object`, `fillet_edges`, `chamfer_edges` — handle `"['Face1']"` strings from some LLMs
- `multi_transform` visibility — ensure intermediate features are hidden after transform
- Added missing tools to system prompt strategy list and stop-when-done instruction

## [0.1.0] - 2026-02-23

Initial alpha release.

### Added

- **Chat interface** with streaming LLM responses in a FreeCAD dock widget
- **Plan / Act modes** — review code before execution or auto-execute
- **Tool calling system** with 21 structured tools:
  - Primitives: `create_primitive`, `create_body`, `create_wedge`
  - Sketching: `create_sketch` (lines, circles, arcs, rectangles, constraints, plane offset)
  - PartDesign: `pad_sketch`, `pocket_sketch`, `revolve_sketch`, `loft_sketches`, `sweep_sketch`
  - Booleans: `boolean_operation` (fuse, cut, common)
  - Transforms: `transform_object`, `scale_object`
  - Edge ops: `fillet_edges`, `chamfer_edges`, `shell_object`
  - Patterns: `linear_pattern`, `polar_pattern`
  - Enclosure helpers: `create_inner_ridge`, `create_snap_tabs`, `create_enclosure_lid`
  - Cross-sections: `section_object`
  - Query: `measure`, `get_document_state`
  - Utility: `modify_property`, `export_model`, `execute_code`, `undo`
  - Interactive: `select_geometry` (viewport picking)
  - View: `capture_viewport`, `set_view`, `zoom_object`
- **Skills system** — reusable instruction sets invoked via `/command`:
  - `/enclosure` — parametric electronics enclosure with snap-fit lid
  - `/gear` — involute spur gear from module and tooth count
  - `/fastener-hole` — clearance, counterbore, countersink holes (ISO dims)
  - `/thread-insert` — heat-set thread insert holes (M2-M5)
  - `/lattice` — grid, honeycomb, diagonal infill patterns
  - `/skill-creator` — create new skills interactively
- **Multiple LLM providers** — Anthropic, OpenAI, Ollama, Gemini, OpenRouter, custom endpoints
- **Thinking mode** — Off / On / Extended reasoning for complex tasks
- **Context compacting** — auto-summarize older messages near context limits
- **Session resume** — auto-save conversations, load from last 20 sessions
- **AGENTS.md support** — project-level instructions with includes and variable substitution
- **MCP support** — STDIO transport, JSON-RPC 2.0, client + server, tool namespacing
- **German translation** (i18n via Qt .ts/.qm)
- **Safety features:**
  - Undo transactions wrapping all tool operations
  - Subprocess sandbox for code execution
  - Sketcher constraint validation to prevent segfaults
  - Pocket auto-direction detection
  - Auto-hide sketches after pad/pocket
- **Test suite** — 243 unit tests
- **Dual licensing** — LGPL-2.1 (code) + CC0-1.0 (icons)
- **Zero external dependencies** — uses only Python stdlib

[0.8.0-alpha]: https://github.com/ghbalf/freecad-ai/releases/tag/v0.8.0-alpha
[0.7.0-alpha]: https://github.com/ghbalf/freecad-ai/releases/tag/v0.7.0-alpha
[0.6.0-alpha]: https://github.com/ghbalf/freecad-ai/releases/tag/v0.6.0-alpha
[0.5.0-alpha]: https://github.com/ghbalf/freecad-ai/releases/tag/v0.5.0-alpha
[0.4.0-alpha]: https://github.com/ghbalf/freecad-ai/releases/tag/v0.4.0-alpha
[0.3.0-alpha]: https://github.com/ghbalf/freecad-ai/releases/tag/v0.3.0-alpha
[0.2.0-alpha]: https://github.com/ghbalf/freecad-ai/releases/tag/v0.2.0-alpha
[0.1.0]: https://github.com/ghbalf/freecad-ai/releases/tag/v0.1.0
