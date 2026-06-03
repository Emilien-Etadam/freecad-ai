"""Background workers for LLM streaming and compaction."""

from .compat import QtWidgets, QtCore, QtGui
from ..i18n import translate

QDockWidget = QtWidgets.QDockWidget
QWidget = QtWidgets.QWidget
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QTextBrowser = QtWidgets.QTextBrowser
QTextEdit = QtWidgets.QTextEdit
QPushButton = QtWidgets.QPushButton
QComboBox = QtWidgets.QComboBox
QLabel = QtWidgets.QLabel
QApplication = QtWidgets.QApplication
Qt = QtCore.Qt
Signal = QtCore.Signal
QThread = QtCore.QThread
Slot = QtCore.Slot
QFont = QtGui.QFont
QTextCursor = QtGui.QTextCursor

import json
import time

from ..config import get_config
from ..core.loop_control import should_continue_loop

class _LLMWorker(QThread):
    """Background thread that streams LLM responses with optional tool loop.

    When tools are provided, implements an agentic loop:
      1. Stream LLM response, collecting text + tool calls
      2. If no tool calls -> done
      3. For each tool call, dispatch to main thread and wait for result
      4. Append results to messages, loop back to step 1
    """

    token_received = Signal(str)           # Text delta
    thinking_received = Signal(str)        # Thinking/reasoning delta
    response_finished = Signal(str)        # Full response text (final turn only)
    error_occurred = Signal(str)           # Error message
    tool_call_started = Signal(str, str)   # (tool_name, call_id)
    tool_call_finished = Signal(str, str, bool, str)  # (tool_name, call_id, success, output)
    tool_exec_requested = Signal(str, str) # (tool_name, arguments_json) — dispatches to main thread
    vision_note = Signal(str)              # Vision description status note

    def __init__(self, messages, system_prompt, tools=None, registry=None,
                 api_style="openai", conversation=None, describe_fn=None, parent=None):
        super().__init__(parent)
        self.messages = list(messages)
        self.system_prompt = system_prompt
        self.tools = tools
        self.registry = registry
        self.api_style = api_style
        self.conversation = conversation
        self.describe_fn = describe_fn
        self._full_response = ""
        self._thinking_text = ""
        self._tool_results = []
        self._tool_result_ready = QtCore.QMutex()
        self._tool_result_wait = QtCore.QWaitCondition()
        self._pending_result = None
        self._max_tool_turns = get_config().max_tool_turns  # 0 = endless
        self._strip_thinking = False  # resolved in run()
        self._tool_timeline = []  # timing data for summary visualization

    def run(self):
        try:
            from ..llm.client import create_client_from_config, should_strip_thinking
            from ..config import get_config as _get_config
            client = create_client_from_config()
            self._strip_thinking = should_strip_thinking(
                client.model, _get_config().strip_thinking_history)

            # Re-format messages with image interception on worker thread
            if self.conversation and self.describe_fn:
                wrapped = self._wrap_describe_fn(self.describe_fn)
                self.messages = self.conversation.get_messages_for_api(
                    api_style=self.api_style, describe_fn=wrapped,
                    strip_thinking=self._strip_thinking,
                )

            if not self.tools:
                # Simple non-tool streaming (backward compat)
                self._simple_stream(client)
                return

            # Agentic tool loop
            self._tool_loop(client)

        except Exception as e:
            self.error_occurred.emit(str(e))

    def _wrap_describe_fn(self, describe_fn):
        """Wrap describe_fn to emit vision_note signals."""
        def wrapped(b64_data):
            try:
                result = describe_fn(b64_data)
                self.vision_note.emit("Image auto-described by llm-vision-mcp")
                return result
            except Exception as e:
                self.vision_note.emit(f"Image description failed: {e}")
                raise
        return wrapped

    def _simple_stream(self, client):
        """Stream without tools (original behavior)."""
        for chunk in client.stream(self.messages, system=self.system_prompt):
            if self.isInterruptionRequested():
                break
            self._full_response += chunk
            self.token_received.emit(chunk)
        self.response_finished.emit(self._full_response)

    def _tool_loop(self, client):
        """Agentic loop: stream -> execute tools -> feed results -> repeat."""
        messages = list(self.messages)

        turn = 0
        while should_continue_loop(self._max_tool_turns, turn, self.isInterruptionRequested()):
            text_parts = []
            thinking_parts = []
            tool_calls = []

            # Stream with tools
            for event in client.stream_with_tools(
                messages, system=self.system_prompt, tools=self.tools
            ):
                if self.isInterruptionRequested():
                    break
                if event.type == "text_delta":
                    text_parts.append(event.text)
                    self._full_response += event.text
                    self.token_received.emit(event.text)
                elif event.type == "thinking_delta":
                    thinking_parts.append(event.text)
                    self._thinking_text += event.text
                    self.thinking_received.emit(event.text)
                elif event.type == "tool_call_start":
                    if event.tool_call:
                        self.tool_call_started.emit(event.tool_call.name, event.tool_call.id)
                elif event.type == "tool_call_end":
                    if event.tool_call:
                        tool_calls.append(event.tool_call)
                elif event.type == "done":
                    break

            turn_text = "".join(text_parts)
            turn_thinking = "".join(thinking_parts)

            if self.isInterruptionRequested():
                self._full_response += "\n\n_⏹ Stopped by user._"
                self.response_finished.emit(self._full_response)
                return
            if not tool_calls:
                # No tool calls — we're done
                self.response_finished.emit(self._full_response)
                return

            # Store the assistant message with tool calls in the conversation
            tc_dicts = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in tool_calls
            ]

            # Add assistant message to local messages for next turn
            if self.api_style == "anthropic":
                content_blocks = []
                if turn_text:
                    content_blocks.append({"type": "text", "text": turn_text})
                for tc in tool_calls:
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                messages.append({"role": "assistant", "content": content_blocks})
            else:
                oai_tcs = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in tool_calls
                ]
                assistant_msg = {
                    "role": "assistant",
                    "content": turn_text or None,
                    "tool_calls": oai_tcs,
                }
                # Preserve reasoning_content unless the model wants it stripped
                # (e.g. Gemma strips thinking; Kimi-K2.5 requires it)
                if turn_thinking and not self._strip_thinking:
                    assistant_msg["reasoning_content"] = turn_thinking
                messages.append(assistant_msg)

            # Execute each tool call on the main thread
            # Exception: optimize_iteration runs on worker thread (long-running
            # LLM calls would freeze the UI if dispatched to main thread).
            # Its inner tool calls dispatch to main thread via QtMainThreadToolExecutor.
            tool_result_messages = []
            for tc in tool_calls:
                # Pre-tool-use hook
                from ..hooks import fire_hook as _fire_hook
                hook_result = _fire_hook("pre_tool_use", {
                    "tool_name": tc.name,
                    "arguments": tc.arguments,
                    "turn": turn,
                })
                t0 = time.time()
                if hook_result.get("block"):
                    result = {"success": False, "output": "",
                              "error": f"Blocked by hook: {hook_result.get('reason', '')}"}
                elif tc.name == "optimize_iteration" and self.registry:
                    tr = self.registry.execute(tc.name, tc.arguments)
                    result = {"success": tr.success, "output": tr.output, "error": tr.error}
                else:
                    result = self._execute_tool_on_main_thread(tc.name, tc.arguments)
                elapsed = time.time() - t0
                success = result.get("success", False)
                output = result.get("output", "")
                error = result.get("error", "")
                result_text = output if success else f"Error: {error}"

                # Track timing for summary
                self._tool_timeline.append({
                    "name": tc.name, "success": success,
                    "elapsed": elapsed, "turn": turn,
                })

                self.tool_call_finished.emit(tc.name, tc.id, success, result_text)

                # Post-tool-use hook
                _fire_hook("post_tool_use", {
                    "tool_name": tc.name,
                    "arguments": tc.arguments,
                    "success": success,
                    "output": output,
                    "error": error,
                    "turn": turn,
                })

                if self.api_style == "anthropic":
                    tool_result_messages.append({
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tc.id,
                                "content": result_text,
                            }
                        ],
                    })
                else:
                    tool_result_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_text,
                    })

            messages.extend(tool_result_messages)

            # Store tool call info so the parent can update the conversation
            self._tool_results.append({
                "assistant_text": turn_text,
                "tool_calls": tc_dicts,
                "results": [
                    {"tool_call_id": tc.id, "content": r["content"] if self.api_style != "anthropic" else r["content"][0]["content"]}
                    for tc, r in zip(tool_calls, tool_result_messages)
                ],
            })
            turn += 1

        # If a tool was interrupted mid-wait, the user already saw a tool-failure
        # bubble; this chat-level note is an intentional, clearer second signal.
        if self.isInterruptionRequested():
            self._full_response += "\n\n_⏹ Stopped by user._"
            self.response_finished.emit(self._full_response)
            return

        # If we reach here, we hit the max turns limit
        limit_msg = "\n\n[{}]".format(
            translate("ChatDockWidget", "Reached maximum tool call iterations"))
        self._full_response += limit_msg
        self.token_received.emit(limit_msg)
        self.response_finished.emit(self._full_response)

    def _execute_tool_on_main_thread(self, tool_name: str, arguments: dict) -> dict:
        """Dispatch tool execution to the main thread and wait for the result.

        Emits tool_exec_requested signal (runs slot on main thread via
        Qt.QueuedConnection), then blocks on a mutex until the main thread
        calls set_tool_result().
        """
        self._pending_result = None
        self.tool_exec_requested.emit(tool_name, json.dumps(arguments))

        self._tool_result_ready.lock()
        elapsed = 0
        deadline = 300000  # ms (5 min) — backstop against a hung/crashed main thread
        while self._pending_result is None:
            if self.isInterruptionRequested():
                self._tool_result_ready.unlock()
                return {"success": False, "output": "", "error": "Stopped by user"}
            if elapsed >= deadline:
                self._tool_result_ready.unlock()
                return {"success": False, "output": "", "error": "Tool execution timed out (main thread did not respond)"}
            # Wake every 250 ms so a Stop request is noticed promptly while the
            # cumulative deadline still guards against a hung main thread.
            self._tool_result_wait.wait(self._tool_result_ready, 250)
            elapsed += 250
        self._tool_result_ready.unlock()

        return self._pending_result

    def set_tool_result(self, result: dict):
        """Called from the main thread to provide a tool execution result."""
        self._tool_result_ready.lock()
        self._pending_result = result
        self._tool_result_wait.wakeAll()
        self._tool_result_ready.unlock()


class _CompactionWorker(QThread):
    """Background thread that summarizes older messages for context compaction."""
    finished = Signal(str)  # summary text

    def __init__(self, conversation_text, parent=None):
        super().__init__(parent)
        self.conversation_text = conversation_text

    def run(self):
        try:
            from ..llm.client import create_client_from_config
            client = create_client_from_config()

            messages = [
                {
                    "role": "user",
                    "content": (
                        "Summarize the following conversation concisely. "
                        "Focus on: what the user asked for, what was created/modified "
                        "(object names, dimensions, operations), any errors encountered "
                        "and how they were resolved, and the current state of the project. "
                        "Keep technical details (names, numbers, tool calls) that would be "
                        "needed to continue the conversation.\n\n"
                        "CONVERSATION:\n" + self.conversation_text
                    ),
                }
            ]
            summary = client.send(
                messages,
                system="You are a conversation summarizer. Be concise but preserve key technical details."
            )
            self.finished.emit(summary)
        except Exception as e:
            # On failure, emit empty string (compaction will be skipped)
            self.finished.emit("")
