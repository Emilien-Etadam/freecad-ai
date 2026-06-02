"""Code review dialog for Plan mode and Act mode confirmation.

Shows proposed code in a read-only editor with Check, Execute, Edit, Fix, and
Cancel buttons. After execution or validation, shows the result inline; when
the result is an error, Fix becomes enabled and — on click — the dialog
closes with fix_requested set so the caller can feed the error back to the
LLM for self-correction.
"""

from .compat import QtWidgets, QtCore, QtGui
from .message_view import colors_from_palette, refresh_theme_cache
from .theme_palette import (
    label_status_stylesheet,
    pushbutton_accent_stylesheet,
    qtextedit_palette_stylesheet,
)
from ..i18n import translate

QDialog = QtWidgets.QDialog
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QTextEdit = QtWidgets.QTextEdit
QLabel = QtWidgets.QLabel
QPushButton = QtWidgets.QPushButton
QFont = QtGui.QFont
QPalette = QtGui.QPalette

from ..core.executor import ExecutionResult, execute_code, validate_code


class _FixPromptDialog(QDialog):
    """Prompt composer for Fix with AI."""

    def __init__(self, code, last_error_result, execution_result, parent=None):
        super().__init__(parent)
        self.code = code
        self.last_error_result = last_error_result
        self.execution_result = execution_result
        self.prompt_text = ""
        self.confirmed = False

        self.setWindowTitle(translate("CodeReviewDialog", "Fix with AI"))
        self.setMinimumSize(560, 320)
        refresh_theme_cache()
        self._build_ui()

    def _default_prompt(self):
        if self.last_error_result and self.last_error_result.stderr.strip():
            return (
                translate("CodeReviewDialog",
                          "The code produced the following error. Please fix it:")
                + "\n\n"
                + self.last_error_result.stderr.strip()
                + "\n"
            )
        if self.execution_result and self.execution_result.success:
            return (
                translate("CodeReviewDialog",
                          "The code ran without errors, but the result isn't "
                          "what I wanted. Please fix it.")
                + "\n\n"
                + translate("CodeReviewDialog",
                            "[Describe the problem here]")
                + "\n"
            )
        return (
            translate("CodeReviewDialog",
                      "I'd like you to change something about this code.")
            + "\n\n"
            + translate("CodeReviewDialog",
                        "[Describe what to change]")
            + "\n"
        )

    def _build_ui(self):
        layout = QVBoxLayout(self)

        header = QLabel(translate(
            "CodeReviewDialog",
            "Describe what the AI should fix. Edit the message as needed:"))
        header.setStyleSheet("font-weight: bold; margin-bottom: 4px;")
        header.setWordWrap(True)
        layout.addWidget(header)

        self.prompt_edit = QTextEdit()
        font = QFont("Monospace", 10)
        font.setStyleHint(QFont.TypeWriter)
        self.prompt_edit.setFont(font)
        self.prompt_edit.setPlainText(self._default_prompt())
        layout.addWidget(self.prompt_edit)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.cancel_btn = QPushButton(translate("CodeReviewDialog", "Cancel"))
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        self.send_btn = QPushButton(translate("CodeReviewDialog", "Send"))
        self.send_btn.clicked.connect(self._send)
        btn_layout.addWidget(self.send_btn)

        layout.addLayout(btn_layout)
        self._apply_palette_styles()

    def showEvent(self, event):
        super().showEvent(event)
        refresh_theme_cache()
        self._apply_palette_styles()

    def _apply_palette_styles(self):
        self.prompt_edit.setStyleSheet(qtextedit_palette_stylesheet(self.palette()))
        self.send_btn.setStyleSheet(
            pushbutton_accent_stylesheet(self.palette(), padding="6px 20px"))

    def _send(self):
        text = self.prompt_edit.toPlainText().strip()
        if not text:
            return
        self.prompt_text = text
        self.confirmed = True
        self.accept()


class CodeReviewDialog(QDialog):
    """Dialog for reviewing and optionally executing LLM-generated code."""

    def __init__(self, code, parent=None):
        super().__init__(parent)
        self.code = code
        self.execution_result = None
        self.last_error_result = None
        self.fix_requested = False
        self._editable = False

        self.setWindowTitle(translate("CodeReviewDialog", "Review Code"))
        self.setMinimumSize(600, 450)
        refresh_theme_cache()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        header = QLabel(translate("CodeReviewDialog", "Review the proposed code before executing:"))
        header.setStyleSheet("font-weight: bold; margin-bottom: 4px;")
        layout.addWidget(header)

        self.code_edit = QTextEdit()
        font = QFont("Monospace", 11)
        font.setStyleHint(QFont.TypeWriter)
        self.code_edit.setFont(font)
        self.code_edit.setPlainText(self.code)
        self.code_edit.setReadOnly(True)
        layout.addWidget(self.code_edit)

        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        self.result_label.setVisible(False)
        layout.addWidget(self.result_label)

        self.result_text = QTextEdit()
        self.result_text.setFont(font)
        self.result_text.setReadOnly(True)
        self.result_text.setMaximumHeight(150)
        self.result_text.setVisible(False)
        layout.addWidget(self.result_text)

        btn_layout = QHBoxLayout()

        self.edit_btn = QPushButton(translate("CodeReviewDialog", "Edit"))
        self.edit_btn.clicked.connect(self._toggle_edit)
        btn_layout.addWidget(self.edit_btn)

        self.check_btn = QPushButton(translate("CodeReviewDialog", "Check"))
        self.check_btn.setToolTip(translate(
            "CodeReviewDialog",
            "Validate the code in a headless FreeCAD sandbox against a copy "
            "of the current document without modifying anything."))
        self.check_btn.clicked.connect(self._check)
        btn_layout.addWidget(self.check_btn)

        btn_layout.addStretch()

        self.fix_btn = QPushButton(translate("CodeReviewDialog", "Fix with AI"))
        self.fix_btn.setToolTip(translate(
            "CodeReviewDialog",
            "Compose a message (prefilled with the latest error or a blank "
            "template) and send it to the AI to refine the code."))
        self.fix_btn.clicked.connect(self._request_fix)
        btn_layout.addWidget(self.fix_btn)

        self.execute_btn = QPushButton(translate("CodeReviewDialog", "Execute"))
        self.execute_btn.clicked.connect(self._execute)
        btn_layout.addWidget(self.execute_btn)

        self.cancel_btn = QPushButton(translate("CodeReviewDialog", "Cancel"))
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        layout.addLayout(btn_layout)
        self._apply_palette_styles()

    def showEvent(self, event):
        super().showEvent(event)
        refresh_theme_cache()
        self._apply_palette_styles()

    def _apply_palette_styles(self):
        self._apply_code_edit_style(self.code_edit, editable=self._editable)
        self.result_text.setStyleSheet(qtextedit_palette_stylesheet(self.palette()))
        self.execute_btn.setStyleSheet(
            pushbutton_accent_stylesheet(self.palette(), padding="6px 20px"))

    def _apply_code_edit_style(self, text_edit, *, editable=False):
        border = None
        if editable:
            border = self.palette().color(QPalette.Highlight).name()
        text_edit.setStyleSheet(qtextedit_palette_stylesheet(self.palette(), border))

    def _toggle_edit(self):
        self._editable = not self._editable
        self.code_edit.setReadOnly(not self._editable)
        if self._editable:
            self.edit_btn.setText(translate("CodeReviewDialog", "Lock"))
        else:
            self.edit_btn.setText(translate("CodeReviewDialog", "Edit"))
        self._apply_code_edit_style(self.code_edit, editable=self._editable)

    def _render_result(self, result, success_msg, failure_msg):
        self.result_label.setVisible(True)
        colors = colors_from_palette(self.palette())
        if result.success:
            self.result_label.setText(success_msg)
            self.result_label.setStyleSheet(
                label_status_stylesheet(colors["tool_success_text"]))
        else:
            self.result_label.setText(failure_msg)
            self.result_label.setStyleSheet(
                label_status_stylesheet(colors["tool_error_text"]))

        output = ""
        if result.stdout.strip():
            output += result.stdout
        if result.stderr.strip():
            if output:
                output += "\n"
            output += result.stderr
        if output.strip():
            self.result_text.setPlainText(output)
            self.result_text.setVisible(True)
        else:
            self.result_text.setVisible(False)

    def _check(self):
        self.code = self.code_edit.toPlainText()
        from ..core.dangerous_mode import get_dangerous_mode
        result = validate_code(self.code, skip_safety=get_dangerous_mode().active)
        self._render_result(
            result,
            translate("CodeReviewDialog", "Validated — no errors in sandbox."),
            translate("CodeReviewDialog", "Validation found issues:"),
        )
        self.last_error_result = None if result.success else result

    def _execute(self):
        self.code = self.code_edit.toPlainText()
        self.execution_result = execute_code(self.code)
        self._render_result(
            self.execution_result,
            translate("CodeReviewDialog", "Code executed successfully."),
            translate("CodeReviewDialog", "Execution failed:"),
        )

        if self.execution_result.success:
            self.last_error_result = None
            self.execute_btn.setEnabled(False)
            self.check_btn.setEnabled(False)
            self.cancel_btn.setText(translate("CodeReviewDialog", "Close"))
            self.cancel_btn.clicked.disconnect()
            self.cancel_btn.clicked.connect(self.accept)
        else:
            self.last_error_result = self.execution_result

    def _request_fix(self):
        self.code = self.code_edit.toPlainText()
        prompt_dlg = _FixPromptDialog(
            self.code, self.last_error_result, self.execution_result, parent=self)
        prompt_dlg.exec()
        if not prompt_dlg.confirmed:
            return
        self.last_error_result = ExecutionResult(
            success=False,
            stdout="",
            stderr=prompt_dlg.prompt_text,
            code=self.code,
        )
        self.fix_requested = True
        self.accept()

    def get_result(self):
        return self.execution_result
