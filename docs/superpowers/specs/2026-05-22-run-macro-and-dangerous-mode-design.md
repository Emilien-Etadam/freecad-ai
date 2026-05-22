# Design: `run_macro` tool, configurable agentic loop, and Dangerous mode

- **Date:** 2026-05-22
- **Issue:** [#13 — Ability to run a FC Macro](https://github.com/ghbalf/freecad-ai/issues/13) (KeithSloan)
- **Status:** Approved design, pending implementation plan

## Background

Keith's workflow: have the AI create test cases, write code to exercise them, and
process the results. Because of the sandbox the AI tends to write a test macro and
ask the user to run it manually, then copy/paste the Report View output back. He
wants the AI to run the macro itself and read the log directly — ideally looping
until no failures remain.

The workbench already has most of the plumbing:

- `execute_code` tool -> `core/executor.py:execute_code()` runs arbitrary Python,
  captures `stdout`/`stderr`, and the agentic loop feeds that back to the next turn.
- `core/executor.py` has **four** safety layers: static pattern blocking
  (`_validate_code`), a headless subprocess sandbox pre-check (`_sandbox_test` /
  `validate_code`), a SIGALRM execution timeout, and an undo-transaction rollback.
- `extensions/user_tools.py` can load `.FCMacro`/`.py` files, but only as
  AST-parseable **typed functions** — script-style macros (top-level statements,
  the usual test-harness shape) are silently skipped.
- The agentic loop in `ui/chat_widget.py:_LLMWorker._tool_loop` is bounded by a
  hardcoded `_max_tool_turns = 30`. There is no general Stop/cancel for the loop.

This design adds four coordinated capabilities to close the gap.

## Goals

1. A `run_macro` tool that runs an existing macro **file** and returns its console output.
2. A user-configurable agentic loop count, including a truly endless (`0`) option.
3. A user-triggered Stop button that aborts the in-flight action and breaks the loop.
4. A "Dangerous mode" escape hatch (Claude Code `--dangerously-skip-permissions` style)
   that relaxes safety, gated behind loud, opt-in, session-scoped controls and big warnings.

## Non-goals

- Automatic "run until no failures" autonomy as a built-in mode. The combination of
  endless loop + the AI's own tool use already enables this behaviorally; we are not
  adding a dedicated test-runner loop or pass/fail detector in this iteration.
- Exposing script-style macros as individual tools (the `scan_freecad_macros` path
  stays function-only).
- Persisting Dangerous mode through the GUI (only via hand-editing the settings file).

## Backwards compatibility

All new config fields default to current behavior (`feedback_backwards_compat_defaults`):
`max_tool_turns = 30`, `dangerous_skip_safety = False`. With defaults unchanged, behavior
is identical to today.

---

## Section 1 — Dangerous mode (foundation)

The safety toggle is the keystone the other features key off. It does two things:
relaxes the executor's safety layers, and **widens `run_macro`'s file-resolution reach**.

### Config

`config.py` gains one field:

```python
dangerous_skip_safety: bool = False
```

- Default `False`.
- The **GUI never writes `True`** to this field persistently. The settings save path
  must guard against persisting `True` (regression-tested).
- On load, a hand-edited `dangerous_skip_safety: true` IS honored — this is the
  documented power-user persistence escape hatch (`feedback_dont_constrain_user_world_view`).

### Runtime state

- The chat dock holds an **in-memory** `unsafe_active` flag, reset to `False` at every
  FreeCAD launch.
- A clearly-marked control in the chat dock (near the model selector) arms it for the
  current session.
- The **first activation each session** pops a blocking confirmation dialog with the
  BIG warning (see Section 4) listing exactly what gets disabled and the
  `while True` -> frozen-FreeCAD failure mode. The user must confirm.
- If startup honors a hand-edited `dangerous_skip_safety: true`, `unsafe_active` starts
  `True` AND the banner shows immediately (no silent danger).

### Banner

A persistent, hard-to-miss strip at the top of the chat dock, visible whenever
`unsafe_active` is `True`. Not dismissable while active. This is
`feedback_observability_silent_fallback` applied to danger: a mode that changes safety
behavior can never be silently on.

### Executor wiring

`core/executor.py`: `execute_code()` and `validate_code()` accept an explicit
`skip_safety: bool` parameter (read from config/flag at the call sites — the executor
does not reach into global config, keeping it unit-testable in isolation).

When `skip_safety` is `True`:

| Layer | Behavior |
|-------|----------|
| Static pattern blocking (`_validate_code`) | **Skipped** |
| Headless sandbox pre-check (`_sandbox_test`) | **Skipped** |
| SIGALRM execution timeout | **Skipped** |
| Undo-transaction rollback | **Kept** (document integrity preserved) |

---

## Section 2 — `run_macro` tool

New tool in `freecad_tools.py`, registered in `ALL_TOOLS`, `category="general"`.

### Signature

```
run_macro(macro: str)
```

Description tells the LLM this runs an **existing FreeCAD macro file** and returns its
console output — distinct from `execute_code` (inline code).

### Resolution (safety-gated)

- **Safe mode (default):** `macro` is a **name**. Resolved against an enumerable set of
  dirs: FreeCAD's user macro dir via `App.getUserMacroDir()` (the canonical API — NOT a
  hardcoded `~/.config/...` path; see `reference_freecad_user_dirs`) plus the workbench's
  `USER_TOOLS_DIR`. Tries `<name>`, `<name>.FCMacro`, `<name>.py`. Input containing a
  path separator, `..`, or resolving outside those dirs is **refused** with an error
  pointing the user to Dangerous mode for arbitrary paths.
- **Dangerous mode ON:** `macro` may be a name OR any absolute/relative filepath,
  anywhere. Relative paths resolve against the active document's dir, then CWD.

### Execution

Read the resolved file, then run its contents through the **same `execute_code()`
pipeline** (not a separate direct call to the interpreter). This inherits undo-rollback,
stdout/stderr capture, and — in safe mode — the sandbox pre-check, pattern checks, and
timeout. Captured output returns in the `ToolResult`, which the agentic loop already
feeds to the next turn.

**Decision:** in safe mode, `run_macro` DOES run the `validate_code` sandbox pre-check
before live execution (consistency with `execute_code`; safe mode should be safe). The
extra headless-FreeCAD round-trip (~seconds) is accepted.

### Rationale

Routing through the single `execute_code()` pipeline keeps one execution chokepoint to
secure; `run_macro` only adds file resolution on top. The resolution-scope gating
(Section 1's reach-widening) and the executor's safety gating compose as different
layers of one pipeline.

---

## Section 3 — Configurable loop count + Stop button

### Config

`config.py` gains:

```python
max_tool_turns: int = 30
```

`0` means endless/unbounded. Settings dialog gets a spinbox (min 0, max 999, special
value `0` displayed as "endless").

### Loop wiring

`_LLMWorker.__init__` reads `get_config().max_tool_turns` instead of the hardcoded `30`
(`chat_widget.py:237`). The loop (`chat_widget.py:291`) changes from
`for turn in range(...)` to a `while` bounded by the config value, treating `0` as
unbounded, and checking `isInterruptionRequested()` at the top of each turn.

The loop-bound + interruption decision is factored into a tiny **pure helper**
(no Qt, no FreeCAD) so it is unit-testable: given `(max_turns, turn, interrupted)` ->
should-continue.

### Stop button (cooperative cancellation)

- The existing `send_btn` already morphs to "..." and disables while loading
  (`_set_loading`, `chat_widget.py:2542`). Instead, while loading it becomes an
  **enabled "Stop" button** (same location, no new layout). Clicking calls
  `self._worker.requestInterruption()`.
- The worker checks `isInterruptionRequested()` at three points:
  1. Top of each turn.
  2. Inside the streaming loop (break out of `stream_with_tools`).
  3. In `_execute_tool_on_main_thread`'s wait loop — a stop during a long tool wait
     must wake the `QWaitCondition` and bail (otherwise the user waits up to the 5-min
     `deadline = 300000` for Stop to register).
- On interruption the worker emits `response_finished` with whatever partial text
  exists plus a "Stopped by user" system note, so the conversation stays consistent
  and saveable.

### Rationale

`requestInterruption()`/`isInterruptionRequested()` is correct because the loop is
**cooperative** — a QThread cannot be safely killed mid-execution (risk of half-mutated
C++ document state). Stopping at turn/tool boundaries is where the document is consistent.
The Stop button is the **enabling precondition** for endless mode: with
`max_tool_turns == 0`, interruption is the only exit.

---

## Section 4 — Docs, wiki, and tests

### Docs / wiki (the BIG warning)

New "Running macros & Dangerous mode" section in README + wiki. The warning is concrete,
not vague:

- **Pattern blocking off** -> AI-run code can call shell commands, delete files, and
  touch anything your user account can.
- **Timeout off** -> a macro with an infinite loop **freezes FreeCAD's main thread with
  no recovery; unsaved work is lost** (undo-rollback cannot save a wedged event loop).
- **Sandbox pre-check off** -> broken geometry/code hits the live document directly.
- **Endless loop** -> unbounded token spend; the **Stop button is the only brake**.
- Plain responsibility statement: the user enabled it and owns the consequences; the
  maintainer ships it untested-against-malice as a power-user convenience.
- Hand-edit persistence (`dangerous_skip_safety: true`) documented as deliberately
  inconvenient, with an explicit "recommended against" note.
- Per `user_platform`: behavior is described for Linux; macro-dir resolution via
  `getUserMacroDir()` is cross-platform but untested off-Linux.
- CHANGELOG entry (`feedback_changelog_release_check`).

### Tests

Unit (`tests/unit/`, no FreeCAD needed):

- **`run_macro` resolution:** name resolves within allowed dirs; path-like / `..` /
  outside-dir input refused in safe mode, accepted in dangerous mode (mock flag + FS).
- **Executor `skip_safety`:** pattern blocking, sandbox, and timeout bypassed when
  `True`; undo-rollback still taken. Static-pattern test asserts a shell-command import
  is blocked when `False`, allowed when `True`.
- **Config:** `max_tool_turns` default 30; `0` round-trips save/load;
  `dangerous_skip_safety` defaults `False` and the GUI save path never writes `True`
  (regression guard for the session-only contract).
- **Loop logic:** pure helper treats `0` as unbounded, `N` as N turns, and an
  interruption flag breaks the loop.
- **Stop checkpoints:** interruption checkpoints break out (helper level).

Integration (`-m integration`, needs AppImage):

- `run_macro` executes a real `.FCMacro` and captures its printed output.
- Dangerous mode lets a macro that imports a normally-blocked module run.

### Rationale

Factoring loop-bound and resolution logic into pure helpers makes the scary parts
unit-testable at `.venv/bin/pytest` speed; the QThread and FreeCAD APIs become thin
shells around tested cores, matching how `executor.py`'s logic is already separable
from the subprocess it spawns.

---

## Risk summary

The genuinely dangerous combination is **endless loop + Dangerous mode together** — an
unattended loop running unsanitized code forever. The design makes this a deliberate,
eyes-open choice: Dangerous mode is session-scoped, loudly gated, and always bannered;
endless mode ships only because a hard Stop interrupt exists. Undo-rollback stays on in
all modes.
