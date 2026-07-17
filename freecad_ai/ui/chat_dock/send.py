"""LLM send pipeline: message dispatch, compaction, worker wiring."""
import json
import time

from ..compat import QtWidgets, QtCore, QtGui
from ...config import LOGS_DIR, get_config, prune_oldest_files, save_current_config
from ...i18n import translate
from ..message_view import (
    render_message,
    render_assistant_stream_open,
    render_stream_activity_hint,
)
from ..chat_utils import _run_reranker, _extract_latest_user_text, _is_binary_content
from ..chat_workers import _LLMWorker, _CompactionWorker
from ..chat_constants import TEXT_FILE_EXTENSIONS

Qt = QtCore.Qt
Slot = QtCore.Slot

class ChatDockSendMixin:

    def _send_message(self):
        """Send the current input to the LLM.

        Never silent: any exception in the send path surfaces as a system
        bubble + Report View entry instead of dying inside the Qt slot.
        """
        try:
            self._send_message_impl()
        except Exception as e:
            err = str(e) or type(e).__name__
            try:
                self._set_loading(False)
            except Exception:
                pass
            try:
                self._append_html(self._render_message(
                    "system", translate("ChatDockWidget", "Error: ") + err))
            except Exception:
                pass
            try:
                import traceback
                import FreeCAD as _App
                _App.Console.PrintError(
                    "[FreeCAD AI] Send failed: {}\n".format(traceback.format_exc()))
            except Exception:
                pass

    def _detach_stuck_worker(self):
        """Abandon a worker stuck in a blocked network read.

        requestInterruption() is only honored between stream events — a
        socket read that never returns ignores it. Disconnect the worker's
        signals and drop the reference so the user can send again; the
        orphaned thread exits on its own when its read finally times out.
        """
        worker = self._worker
        for sig, slot in (
            (worker.token_received, self._on_token),
            (worker.thinking_received, self._on_thinking),
            (worker.response_finished, self._on_response_finished),
            (worker.error_occurred, self._on_error),
            (worker.tool_call_started, self._on_tool_call_started),
            (worker.tool_call_finished, self._on_tool_call_finished),
            (worker.tool_exec_requested, self._execute_tool_call),
            (worker.vision_note, self._on_vision_note),
        ):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass  # never connected (plan-mode worker) or already gone
        self._worker = None
        self._stop_requested = False
        self._set_loading(False)
        self._append_html(self._render_message(
            "system",
            translate("ChatDockWidget",
                      "Request abandoned (the server did not answer). "
                      "You can send again.")))

    def _send_message_impl(self):
        if self._worker and self._worker.isRunning():
            # Button is in "Stop" state — interrupt the in-flight run instead
            # of sending. Input is usually empty here, so this must run before
            # the empty-text guard below. Give visible feedback: a blocked
            # socket read ignores the interruption, and a silent click here
            # reads as "the button is dead".
            self._worker.requestInterruption()
            if getattr(self, "_stop_requested", False):
                # Second click while still stuck → force-detach.
                self._detach_stuck_worker()
            else:
                self._stop_requested = True
                self._append_html(self._render_message(
                    "system",
                    translate("ChatDockWidget",
                              "Stopping the current request… Click Stop again "
                              "to abandon it immediately.")))
            return

        text = self.input_edit.toPlainText().strip()
        if not text:
            return

        self.input_edit.clear()
        self._retry_count = 0  # Reset retries for new user message
        self._active_skill_name = ""

        # Check for --validate flag
        self._validate_pending = False
        if "--validate" in text:
            text = text.replace("--validate", "").strip()
            self._validate_pending = True

        # Check for skill commands
        if text.startswith("/"):
            handled = self._handle_skill_command(text)
            if handled:
                return

        # Fire user_prompt_submit hook
        from ...hooks import fire_hook
        mode = "plan" if self.mode_combo.currentIndex() == 0 else "act"
        hook_result = fire_hook("user_prompt_submit", {
            "text": text, "images": [], "mode": mode,
        })
        if hook_result.get("block"):
            self._append_html(self._render_message("system",
                f"Blocked by hook: {hook_result.get('reason', 'no reason given')}"))
            return
        if hook_result.get("modify"):
            text = hook_result["modify"]

        # Show one-time hint if vision not tested and user is sending images
        pending_images = self._attachment_strip.get_images()
        cfg = get_config()
        if pending_images and cfg.vision_detected is None and not self._vision_hint_shown:
            self._vision_hint_shown = True
            self._append_html(
                '<div style="color: #888; font-size: 9pt; margin: 4px 12px;">'
                'Tip: click Test Connection in Settings to enable vision auto-detection.'
                '</div>'
            )

        # Collect attached images
        images = pending_images or None

        # Collect attached documents
        pending_docs = self._attachment_strip.get_documents()
        documents = pending_docs or None

        # Auto-capture viewport if configured
        capture_mode = getattr(self, "_capture_mode_override", None) or get_config().viewport_capture
        if capture_mode == "every_message":
            vp_img = self._capture_viewport_for_chat()
            if vp_img:
                images = (images or []) + [vp_img]

        # Prepend pending viewport image (from after_changes mode)
        if getattr(self, "_pending_viewport_image", None):
            images = (images or []) + [self._pending_viewport_image]
            self._pending_viewport_image = None

        # Add to conversation and display
        self.conversation.add_user_message(text, images=images, documents=documents)
        self._refresh_input_history()
        display_content = self.conversation.messages[-1]["content"]
        self._append_html(self._render_message(
            "user", display_content, ts=self.conversation.messages[-1].get("ts")))
        self._attachment_strip.clear()

        # Check if conversation needs compaction
        cfg = get_config()
        if self.conversation.needs_compaction(cfg.context_window):
            self._compact_and_send()
            return

        self._continue_send()

    def _compact_and_send(self):
        """Compact conversation by summarizing older messages, then continue sending."""
        self._set_chat_activity("compact")
        self._append_html(
            '<div style="margin: 4px 0; padding: 6px 10px; '
            'background-color: #fff3e0; border-left: 3px solid #ff9800; '
            'border-radius: 0 4px 4px 0; font-size: 12px; color: #e65100;">'
            '{}</div>'.format(
                translate("ChatDockWidget", "Compacting context (~{}k tokens)...").format(
                    self.conversation.estimated_tokens() // 1000))
        )

        # Build summary of older messages (all except last 4)
        keep_recent = 4
        older = self.conversation.messages[:-keep_recent] if len(self.conversation.messages) > keep_recent else []
        if not older:
            # Nothing to compact, just send normally
            self._continue_send()
            return

        # Build a text summary of older messages for the LLM to compress
        summary_parts = []
        for msg in older:
            role = msg["role"]
            content = msg.get("content", "")
            if role == "tool_result":
                # Truncate long tool results for the summary request
                if len(content) > 500:
                    content = content[:500] + "..."
                summary_parts.append(f"[Tool Result] {content}")
            elif role == "assistant" and msg.get("tool_calls"):
                tc_names = [tc["name"] for tc in msg["tool_calls"]]
                summary_parts.append(f"[Assistant] Called tools: {', '.join(tc_names)}")
                if content:
                    summary_parts.append(f"  Text: {content[:300]}")
            else:
                label = "User" if role == "user" else "Assistant" if role == "assistant" else "System"
                if len(content) > 500:
                    content = content[:500] + "..."
                summary_parts.append(f"[{label}] {content}")

        summary_text = "\n".join(summary_parts)

        # Use a background thread to generate the summary
        self._set_loading(True)
        self._compaction_worker = _CompactionWorker(summary_text, parent=self)
        self._compaction_worker.finished.connect(self._on_compaction_finished)
        self._compaction_worker.start()

    def _on_compaction_finished(self, summary):
        """Handle compaction result and continue sending."""
        if summary:
            self.conversation.compact(summary, keep_recent=4)
            self._append_html(
                '<div style="margin: 4px 0; padding: 6px 10px; '
                'background-color: #e8f5e9; border-left: 3px solid #4caf50; '
                'border-radius: 0 4px 4px 0; font-size: 12px; color: #2e7d32;">'
                '{}</div>'.format(
                    translate("ChatDockWidget", "Context compacted to ~{}k tokens").format(
                        self.conversation.estimated_tokens() // 1000))
            )
        self._set_loading(False)
        self._update_token_count()
        # Continue with the normal send flow
        self._continue_send()

    def _continue_send(self):
        """Continue the send flow after optional compaction."""
        # Show loading immediately — MCP connect and LLM reranking can block
        # the main thread for several seconds; without this the UI looks frozen
        # after the user message appears.
        self._set_loading(True)
        self._streaming_html = ""
        self._in_thinking = False
        self._tool_results_stored = False
        self._summary_rendered = False
        cfg = get_config()
        hint_kind = "thinking" if cfg.thinking != "off" else "connecting"
        import time as _time
        self._append_html(render_assistant_stream_open(
            palette=self.palette(), ts=_time.time()))
        self._append_html(render_stream_activity_hint(self.palette(), hint_kind))
        self._set_chat_activity(
            "think" if cfg.thinking != "off" else "connect",
        )

        try:
            self._continue_send_impl()
        except Exception as e:
            self._set_loading(False)
            err = str(e) or type(e).__name__
            self._append_html(self._render_message(
                "system",
                translate("ChatDockWidget", "Error: ") + err,
            ))
            try:
                import FreeCAD as _App
                _App.Console.PrintError(
                    "[FreeCAD AI] Send failed: {}\n".format(err)
                )
            except Exception:
                pass

    def _continue_send_impl(self):
        """Build prompts/tools and start the LLM worker."""
        from ...core.system_prompt import build_system_prompt
        mode = "plan" if self.mode_combo.currentIndex() == 0 else "act"
        cfg = get_config()

        # Determine if we should use tools. cfg.supports_tools combines the
        # provider-wide flag with per-model detection from /api/show — so an
        # Ollama embedding/reranker accidentally selected as the main model
        # won't get tools sent to it.
        use_tools = cfg.enable_tools and mode == "act" and cfg.supports_tools
        tools_schema = None
        api_style = "openai"

        if use_tools:
            # Connect MCP servers on first tool-enabled send
            if not self._mcp_connected:
                self._connect_mcp_servers(cfg)

            from ...tools.setup import create_default_registry
            from ...llm.providers import get_api_style

            # Build extra tools for active optimization
            extra_tools = []
            if self._optimization_active:
                try:
                    from ...tools.optimize_tools import get_optimize_iteration_tool, _active_config
                    extra_tools = [get_optimize_iteration_tool()]
                    # Pass the tool executor to the active config so evaluator can dispatch
                    if _active_config is not None:
                        from ...tools.executor_utils import (
                            MainThreadToolExecutor, _HAS_QT,
                        )
                        if _HAS_QT:
                            from ...tools.executor_utils import QtMainThreadToolExecutor
                            executor = QtMainThreadToolExecutor()
                        else:
                            executor = MainThreadToolExecutor()
                        executor.set_registry(None)  # will be set after registry creation
                        _active_config["_tool_executor"] = executor
                except ImportError:
                    pass

            self._tool_registry = create_default_registry(include_mcp=True, extra_tools=extra_tools)

            # Update executor registry if optimization active
            if self._optimization_active and extra_tools:
                try:
                    from ...tools.optimize_tools import _active_config
                    if _active_config and "_tool_executor" in _active_config:
                        _active_config["_tool_executor"].set_registry(self._tool_registry)
                except ImportError:
                    pass

            # Search for vision fallback after registry (with MCP tools) is created
            if not cfg.supports_vision and self._vision_fallback_tool is None:
                from ...mcp.manager import find_vision_fallback
                self._vision_fallback_tool = find_vision_fallback(self._tool_registry)
                self._refresh_image_controls()
            api_style = get_api_style(cfg.provider.name)

            # Optional tool reranking: filter schemas down to the top-N
            # relevant tools (+ pinned) based on the latest user message.
            filter_names = None
            if cfg.rerank_method in ("keyword", "llm"):
                user_text = _extract_latest_user_text(self.conversation)
                pairs = self._tool_registry.list_name_description_pairs()
                ranked = _run_reranker(cfg, pairs, user_text)
                filter_names = set(ranked)
                try:
                    import FreeCAD as _App
                    _App.Console.PrintMessage(
                        "[FreeCAD AI] Reranker ({}): {} of {} tools -> {}\n".format(
                            cfg.rerank_method, len(ranked), len(pairs),
                            ", ".join(ranked))
                    )
                except Exception:
                    pass

            if api_style == "anthropic":
                tools_schema = self._tool_registry.to_anthropic_schema(filter_names)
            else:
                tools_schema = self._tool_registry.to_openai_schema(filter_names)
            system_prompt = build_system_prompt(
                mode=mode, tools_enabled=True,
                override=cfg.system_prompt_override)
        else:
            self._tool_registry = None
            system_prompt = build_system_prompt(
                mode=mode, override=cfg.system_prompt_override)

        # Build describe_fn for non-vision LLMs
        describe_fn = None
        conversation_ref = None
        if not cfg.supports_vision:
            fallback = getattr(self, '_vision_fallback_tool', None)
            if fallback and self._tool_registry:
                _reg = self._tool_registry
                _tool = fallback
                def _make_describe(reg, tool_name):
                    def describe(b64_data):
                        result = reg.execute(
                            tool_name, {"image": b64_data, "prompt": "Describe this image in detail."}
                        )
                        if result.success:
                            return result.output
                        raise RuntimeError(result.error or "describe_image failed")
                    return describe
                describe_fn = _make_describe(_reg, _tool)
                conversation_ref = self.conversation

        # Get messages for API
        from ...llm.client import should_strip_thinking
        strip = should_strip_thinking(
            cfg.provider.model, cfg.strip_thinking_history)
        # When the model has no vision and no describe_image fallback is
        # available, drop history image blocks to a placeholder so they aren't
        # sent raw to a provider that would reject them (issue #30). When a
        # describe_fn exists, the worker rebuilds messages with descriptions.
        strip_images = not cfg.supports_vision and describe_fn is None
        messages = self.conversation.get_messages_for_api(
            api_style=api_style, strip_images=strip_images, strip_thinking=strip)

        self._worker = _LLMWorker(
            messages, system_prompt,
            tools=tools_schema, registry=self._tool_registry,
            api_style=api_style, conversation=conversation_ref,
            describe_fn=describe_fn, parent=self,
        )
        self._worker.token_received.connect(self._on_token)
        self._worker.thinking_received.connect(self._on_thinking)
        self._worker.response_finished.connect(self._on_response_finished)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.tool_call_started.connect(self._on_tool_call_started)
        self._worker.tool_call_finished.connect(self._on_tool_call_finished)
        self._worker.tool_exec_requested.connect(self._execute_tool_call)
        self._worker.vision_note.connect(self._on_vision_note)
        self._worker.start()

