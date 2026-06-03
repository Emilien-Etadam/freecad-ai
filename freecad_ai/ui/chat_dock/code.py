"""Plan/Act code execution and skill command shortcuts."""
from ..compat import QtWidgets, QtCore, QtGui
from ...core.executor import extract_code_blocks, execute_code
from ...i18n import translate
from ..message_view import render_code_block, render_execution_result
from ..code_review_dialog import CodeReviewDialog
from ..chat_workers import _LLMWorker

Qt = QtCore.Qt


class ChatDockCodeMixin:
    """Execute code blocks and run skill-injected prompts."""

    # ── Code execution ──────────────────────────────────────

    def _handle_act_mode(self, code_blocks):
        """Execute code blocks in Act mode."""
        cfg = get_config()

        for code in code_blocks:
            if cfg.auto_execute:
                result = execute_code(code)
            else:
                try:
                    import FreeCADGui as Gui
                    parent = Gui.getMainWindow()
                except ImportError:
                    parent = self
                dlg = CodeReviewDialog(code, parent)
                dlg.exec()
                if dlg.fix_requested and dlg.last_error_result:
                    self._handle_execution_error(dlg.last_error_result)
                    return
                result = dlg.get_result()
                if not result:
                    continue

            self._append_html(render_execution_result(
                result.success, result.stdout, result.stderr
            ))

            if result.success:
                # Reset retry counter on success
                self._retry_count = 0
            else:
                self._handle_execution_error(result)
                break

    def _handle_execution_error(self, result):
        """Handle code execution failure — send error back to LLM for self-correction."""
        if self._retry_count >= get_config().max_retries:
            self._append_html(render_message(
                "system",
                translate("ChatDockWidget",
                          "Max retries ({}) reached. "
                          "Please review the error and provide guidance.").format(
                    get_config().max_retries)
            ))
            self._retry_count = 0
            return

        self._retry_count += 1
        error_msg = translate(
            "ChatDockWidget",
            "The code failed with the following error:\n\n"
            "{}\n\n"
            "Please fix the code and try again. (Attempt {}/{})").format(
                result.stderr, self._retry_count, get_config().max_retries)

        # Attach a viewport snapshot so vision-capable LLMs can see the state
        # that produced the error — especially useful for "runs but result is
        # wrong" cases the user flagged via the Fix-with-AI composer.
        capture_mode = (getattr(self, "_capture_mode_override", None)
                        or get_config().viewport_capture)
        vp_img = (self._capture_viewport_for_chat()
                  if capture_mode != "off" else None)
        self.conversation.add_system_message(
            error_msg, images=[vp_img] if vp_img else None)
        self._append_html(render_message("system", error_msg))

        from ..core.system_prompt import build_system_prompt
        from ..llm.client import should_strip_thinking
        mode = "plan" if self.mode_combo.currentIndex() == 0 else "act"
        system_prompt = build_system_prompt(mode=mode)
        cfg = get_config()
        strip = should_strip_thinking(
            cfg.provider.model, cfg.strip_thinking_history)
        messages = self.conversation.get_messages_for_api(strip_thinking=strip)

        self._set_loading(True)
        self._streaming_html = ""
        self._append_html(
            '<div style="margin: 8px 0; padding: 8px 12px; '
            'background-color: #f5f5f5; border-radius: 6px;">'
            '<div style="font-weight: bold; color: #2e7d32; margin-bottom: 4px;">AI</div>'
            '<div style="white-space: pre-wrap;">'
        )

        self._tool_results_stored = False
        self._worker = _LLMWorker(messages, system_prompt, parent=self)
        self._worker.token_received.connect(self._on_token)
        self._worker.response_finished.connect(self._on_response_finished)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.start()

    def execute_code_from_plan(self, code):
        """Execute a code block from Plan mode (called from Execute button)."""
        try:
            import FreeCADGui as Gui
            parent = Gui.getMainWindow()
        except ImportError:
            parent = self
        dlg = CodeReviewDialog(code, parent)
        dlg.exec()
        if dlg.fix_requested and dlg.last_error_result:
            self._handle_execution_error(dlg.last_error_result)
            return
        result = dlg.get_result()

        if result:
            self._append_html(render_execution_result(
                result.success, result.stdout, result.stderr
            ))
            if result.success:
                self.conversation.add_system_message(
                    translate("ChatDockWidget", "Code executed successfully.") + "\n" + result.stdout
                )
            else:
                self.conversation.add_system_message(
                    translate("ChatDockWidget", "Code execution failed:") + "\n" + result.stderr
                )

    # ── Skill commands ──────────────────────────────────────

    def _handle_skill_command(self, text):
        """Handle /command-style skill invocations. Returns True if handled."""
        from ..extensions.skills import SkillsRegistry
        registry = SkillsRegistry()
        result = registry.match_command(text)
        if not result:
            return False

        skill_name, args = result
        skill = registry.get_skill(skill_name)
        if not skill:
            return False

        # Collect attachments (images/documents) from the strip and attach
        # them to the visible user message, same as a regular send.
        pending_images = self._attachment_strip.get_images() or None
        pending_docs = self._attachment_strip.get_documents() or None

        # Display the command (with any attachments)
        self.conversation.add_user_message(text, images=pending_images,
                                           documents=pending_docs)
        display_content = self.conversation.messages[-1]["content"]
        self._append_html(render_message("user", display_content))
        self._attachment_strip.clear()

        # Execute the skill
        exec_result = registry.execute_skill(skill_name, args)

        # Check if this is the optimize-skill handler
        if skill_name == "optimize-skill":
            self._optimization_active = True

        self._active_skill_name = skill_name

        if exec_result.get("inject_prompt"):
            # Inject skill prompt and send to LLM
            prompt_text = exec_result["inject_prompt"]
            if args:
                prompt_text += f"\n\nUser request: {args}"
            self.conversation.add_user_message(prompt_text)
            # Trigger LLM with the injected prompt
            self._send_with_injected_prompt()
        elif exec_result.get("output"):
            self._append_html(render_message("system", exec_result["output"]))
            self.conversation.add_system_message(exec_result["output"])

        return True

    def _send_with_injected_prompt(self):
        """Send the current conversation to the LLM (used after skill injection).

        Reuses _continue_send to ensure tools are available in Act mode.
        """
        self._continue_send()
