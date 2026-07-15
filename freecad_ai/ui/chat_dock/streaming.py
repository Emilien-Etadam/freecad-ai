"""Stream tokens, tool calls, and post-response validation."""
import html as html_mod

from ..compat import QtWidgets, QtCore, QtGui
from ...i18n import translate
from ...config import get_config
from ..message_view import (
    CHAT_STREAM_END,
    render_message,
    render_tool_call,
    render_execution_result,
    render_thinking_stream_open,
    render_thinking_stream_chunk,
    render_tool_summary,
)
from ...core.executor import extract_code_blocks
from ..chat_workers import _LLMWorker

Qt = QtCore.Qt
Slot = QtCore.Slot
QTextCursor = QtGui.QTextCursor


class ChatDockStreamingMixin:
    """Handle LLM streaming signals and tool-call UI updates."""

    # ── Streaming handlers ──────────────────────────────────

    @Slot(str)
    def _on_thinking(self, chunk):
        """Handle a thinking/reasoning delta — render dimmed."""
        import html as html_mod
        self._stream_chars = getattr(self, "_stream_chars", 0) + len(chunk)
        self._set_chat_activity("think")
        if not self._in_thinking:
            self._in_thinking = True
            # Start a thinking block
            cursor = self.chat_display.textCursor()
            cursor.movePosition(QTextCursor.End)
            cursor.insertHtml(render_thinking_stream_open(palette=self.palette()))
            self.chat_display.setTextCursor(cursor)

        escaped = html_mod.escape(chunk)
        escaped = escaped.replace("\n", "<br>")

        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml(render_thinking_stream_chunk(chunk, palette=self.palette()))
        self.chat_display.setTextCursor(cursor)
        self.chat_display.ensureCursorVisible()

    @Slot(str)
    def _on_token(self, chunk):
        """Handle a streamed token — append to the display."""
        import html as html_mod

        self._stream_chars = getattr(self, "_stream_chars", 0) + len(chunk)
        self._set_chat_activity("respond")

        # Close thinking block if transitioning from thinking to regular content
        if self._in_thinking:
            self._in_thinking = False
            cursor = self.chat_display.textCursor()
            cursor.movePosition(QTextCursor.End)
            cursor.insertHtml('</div>')
            self.chat_display.setTextCursor(cursor)

        escaped = html_mod.escape(chunk)
        escaped = escaped.replace("\n", "<br>")
        self._streaming_html += chunk

        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml(escaped)
        self.chat_display.setTextCursor(cursor)
        self.chat_display.ensureCursorVisible()

    def _store_tool_results(self, full_response=""):
        """Store tool results from worker into conversation. Idempotent — skips if already stored."""
        if not (self._worker and self._worker._tool_results):
            if full_response:
                self.conversation.add_assistant_message(full_response)
            return

        # Guard against double-storage (e.g., if both response_finished and error fire)
        if getattr(self, '_tool_results_stored', False):
            return
        self._tool_results_stored = True

        try:
            for turn_info in self._worker._tool_results:
                tc_dicts = turn_info["tool_calls"]
                self.conversation.add_assistant_message(
                    turn_info["assistant_text"], tool_calls=tc_dicts
                )
                for r in turn_info["results"]:
                    self.conversation.add_tool_result(r["tool_call_id"], r["content"])
            # Store the final text-only response
            # Extract just the final part (after last tool round)
            last_tool_end = sum(
                len(t["assistant_text"]) for t in self._worker._tool_results
            )
            final_text = full_response[last_tool_end:] if last_tool_end < len(full_response) else full_response
            if final_text.strip():
                self.conversation.add_assistant_message(final_text)
        except Exception as e:
            try:
                import FreeCAD
                FreeCAD.Console.PrintError(f"_store_tool_results error: {e}\n")
            except Exception:
                pass
            # Fallback: store at least the full response text
            if full_response.strip():
                self.conversation.add_assistant_message(full_response)

    @Slot(str)
    def _on_response_finished(self, full_response):
        """Handle completion of LLM response."""
        self._set_loading(False)

        # Close the streaming div
        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml(CHAT_STREAM_END)

        # Store in conversation - include any tool call info from the worker
        self._store_tool_results(full_response)

        self._update_token_count()

        # Auto-save conversation for resume capability
        self.conversation.save()

        # Post-response hook
        from ...hooks import fire_hook
        fire_hook("post_response", {
            "response_text": full_response,
            "tool_calls_count": len(self._worker._tool_results) if self._worker and self._worker._tool_results else 0,
            "mode": "plan" if self.mode_combo.currentIndex() == 0 else "act",
        })

        # Auto-save session log when tool calls were used
        if self._worker and self._worker._tool_results:
            self._auto_save_log()

        # Re-render the full chat to get proper code block formatting
        self._rerender_chat()

        # Tool call summary (after re-render so it's not wiped)
        if self._worker and self._worker._tool_timeline and not getattr(self, '_summary_rendered', False):
            self._summary_rendered = True
            self._append_html(self._render_tool_summary(self._worker._tool_timeline))

        # Handle code execution based on mode (only if tools were NOT used)
        mode = "plan" if self.mode_combo.currentIndex() == 0 else "act"
        if not (self._worker and self._worker._tool_results):
            code_blocks = extract_code_blocks(full_response)
            if not code_blocks:
                for msg in reversed(self.conversation.messages):
                    if msg.get("role") != "assistant":
                        continue
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        code_blocks = extract_code_blocks(content)
                    break
            if code_blocks and mode == "act":
                self._handle_act_mode(code_blocks)
            elif mode == "act" and full_response.strip():
                tools_were_offered = bool(
                    self._worker and getattr(self._worker, "tools", None)
                )
                if tools_were_offered:
                    hint = translate(
                        "ChatDockWidget",
                        "Act mode: the model responded without calling any tools. "
                        "Try again or rephrase your request.",
                    )
                else:
                    hint = translate(
                        "ChatDockWidget",
                        "Act mode: no Python code block found to run. "
                        "Use a ```python fenced block or enable tool calling in Settings.",
                    )
                self._append_html(self._render_message("system", hint))

        # After-changes viewport capture: queue screenshot for next message
        capture_mode = self._capture_mode_override or get_config().viewport_capture
        if capture_mode == "after_changes" and self._worker and self._worker._tool_results:
            vp_img = self._capture_viewport_for_chat()
            if vp_img:
                self._pending_viewport_image = vp_img

        # Run geometry validation if --validate was requested
        if getattr(self, "_validate_pending", False):
            self._validate_pending = False
            self._run_post_validation()

    def _run_post_validation(self):
        """Run geometry validation after skill completes."""

        skill_name = getattr(self, "_active_skill_name", "")
        if not skill_name:
            self._append_html(self._render_message("system",
                "No skill detected \u2014 cannot validate without VALIDATION.md."))
            return

        try:
            from ...extensions.skills import SkillsRegistry
            registry = SkillsRegistry()
            skill = registry.get_skill(skill_name)
        except Exception:
            self._append_html(self._render_message("system",
                f"Could not load skill '{skill_name}'."))
            return

        if not skill or not skill.validation_path:
            self._append_html(self._render_message("system",
                f"Skill '{skill_name}' has no VALIDATION.md \u2014 skipping validation."))
            return

        try:
            with open(skill.validation_path) as f:
                validation_content = f.read()
        except OSError as e:
            self._append_html(self._render_message("system",
                f"Could not read VALIDATION.md: {e}"))
            return

        # Get params from report_skill_params tool
        from ...tools.freecad_tools import (
            get_reported_skill_params, clear_reported_skill_params,
        )
        params = get_reported_skill_params() or {}
        clear_reported_skill_params()

        if not params:
            self._append_html(self._render_message("system",
                "No parameters reported \u2014 LLM did not call report_skill_params. "
                "Cannot validate."))
            return

        try:
            import FreeCAD as App
            doc = App.ActiveDocument
        except ImportError:
            self._append_html(self._render_message("system",
                "FreeCAD not available \u2014 cannot validate."))
            return

        if not doc:
            self._append_html(self._render_message("system",
                "No active document \u2014 cannot validate."))
            return

        from ...extensions.skill_validator import validate_skill, compute_pass_rate
        results = validate_skill(doc, params, validation_content)

        if not results:
            self._append_html(self._render_message("system",
                "No validation checks found."))
            return

        # Format results
        passed = sum(1 for r in results if r.passed)
        lines = [f"Validation: {passed}/{len(results)} checks passed"]
        for r in results:
            icon = "\u2713" if r.passed else "\u2717"
            lines.append(f"  {icon}  {r.message}")

        self._append_html(self._render_message("system", "\n".join(lines)))

    @Slot(str)
    def _on_error(self, error_msg):
        """Handle LLM communication error.

        Preserves any tool results from earlier turns, then appends the error
        without re-rendering (to keep the streaming HTML intact).
        """
        self._set_loading(False)

        # Close the streaming div
        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml(CHAT_STREAM_END)

        # Store any tool results that were collected before the error
        self._store_tool_results()

        # Save conversation so tool results aren't lost
        if len(self.conversation.messages) > 1:
            self.conversation.save()
            if self._worker and self._worker._tool_results:
                self._auto_save_log()

        # If tools ran successfully but the final LLM turn failed,
        # generate a summary from the tool trace instead of just showing an error.
        if self._worker and self._worker._tool_results:
            summary_parts = []
            for turn in self._worker._tool_results:
                for tc, r in zip(turn["tool_calls"], turn["results"]):
                    summary_parts.append(f"- **{tc['name']}**: {r['content']}")
            summary = "\n".join(summary_parts)
            self._append_html(self._render_message(
                "assistant",
                translate("ChatDockWidget",
                          "All operations completed successfully:") + "\n\n" + summary
            ))
            # Store the summary in conversation
            self.conversation.add_assistant_message(
                translate("ChatDockWidget",
                          "All operations completed successfully:") + "\n\n" + summary
            )
            self.conversation.save()
        else:
            # No tool results — show the raw error
            self._append_html(self._render_message("system", translate("ChatDockWidget", "Error: ") + error_msg))

    # ── Tool call handlers ──────────────────────────────────

    @Slot(str, str)
    def _on_tool_call_started(self, tool_name, call_id):
        """Render tool call start in the chat."""
        self._set_chat_activity("tool", tool_name)
        self._append_html(self._render_tool_call(tool_name, call_id, started=True))

    @Slot(str, str, bool, str, float, str)
    def _on_tool_call_finished(self, tool_name, call_id, success, output,
                               elapsed=0.0, args_json=""):
        """Render tool call result as a compact line in the chat.

        The full output is stashed in ``_tool_call_details`` and reachable
        through the line's "details" anchor.
        """
        from ..chat_utils import _summarize_tool_args

        if not hasattr(self, "_tool_call_details"):
            self._tool_call_details = {}
        detail_anchor = ""
        if output and output.strip():
            self._tool_call_details[call_id] = output
            detail_anchor = f"tooldetail:{call_id}"

        self._append_html(self._render_tool_call(
            tool_name, call_id, started=False, success=success, output=output,
            elapsed=elapsed, args_summary=_summarize_tool_args(args_json),
            detail_anchor=detail_anchor,
        ))

    def _on_vision_note(self, message: str):
        """Show a subtle note when images are auto-described."""
        self._append_html(
            f'<div style="color: #888; font-size: 9pt; margin: 2px 12px;">'
            f'{message}</div>'
        )

    @Slot(str, str)
    def _execute_tool_call(self, tool_name, arguments_json):
        """Execute a tool call on the main thread. Connected to worker's tool_exec_requested signal."""
        if not self._tool_registry:
            result = {"success": False, "output": "", "error": "No tool registry"}
        else:
            try:
                arguments = json.loads(arguments_json)
            except json.JSONDecodeError:
                arguments = {}
            tool_result = self._tool_registry.execute(tool_name, arguments)
            result = {
                "success": tool_result.success,
                "output": tool_result.output,
                "error": tool_result.error,
            }

        # Signal the worker thread that the result is ready
        if self._worker:
            self._worker.set_tool_result(result)
