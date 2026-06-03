"""Message rendering helpers for the chat widget.

Converts chat messages (with markdown-ish formatting and code blocks)
into HTML suitable for display in a QTextBrowser.
"""

import html
import re

from ..i18n import translate

# Match ```python ... ``` code blocks
CODE_BLOCK_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)

# Match <think>...</think> blocks
THINK_BLOCK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)

# Match inline `code`
INLINE_CODE_RE = re.compile(r"`([^`]+)`")

# Match **bold**
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")

# Match *italic*
ITALIC_RE = re.compile(r"\*(.+?)\*")

_CACHED_THEME_NAME = None
_CACHED_THEME_COLORS = None

_LIGHT_THEME_COLORS = {
    "chat_bg": "#ffffff",
    "chat_text": "#000000",
    "chat_border": "#ababab",
    "user_bg": "#e3f2fd",
    "user_label": "#1565c0",
    "assistant_bg": "#dcffdc",
    "assistant_label": "#2e7d32",
    "system_bg": "#ffffff5e",
    "system_label": "#e65100",
    "code_bg": "#f5f7fa",
    "code_border": "#d6dde8",
    "code_lang_bg": "#e9eef5",
    "code_lang": "#475569",
    "code_text": "#666565",
    "result_bg": "#fafafa",
    "stdout_bg": "#f0f0f0",
    "stdout_text": "#333333",
    "stderr_bg": "#fce4ec",
    "stderr_text": "#b71c1c",
    "tool_success_bg": "#e8f5e9",
    "tool_success_border": "#4caf50",
    "tool_success_text": "#2e7d32",
    "tool_error_bg": "#fce4ec",
    "tool_error_border": "#ef5350",
    "tool_error_text": "#c62828",
    "tool_output_bg": "rgba(0,0,0,0.05)",
    "thinking_bg": "#f0f0f0",
    "thinking_border": "#cccccc",
    "thinking_text": "#999999",
    "thinking_label": "#aaaaaa",
    "inline_code_bg": "#e0e0e0",
    "inline_code_text": "#111111",
}

_DARK_THEME_COLORS = {
    "chat_bg": "#252525",
    "chat_text": "#ffffff",
    "chat_border": "#020202",
    "user_bg": "#163142",
    "user_label": "#8cc8ff",
    "assistant_bg": "#243229",
    "assistant_label": "#22CA00",
    "system_bg": "#3d2f1f",
    "system_label": "#ffffff65",
    "code_bg": "#141414",
    "code_border": "#2a2a2a",
    "code_lang_bg": "#525252",
    "code_lang": "#ffffff",
    "code_text": "#aaaaaa",
    "result_bg": "#242424",
    "stdout_bg": "#1f1f1f",
    "stdout_text": "#dddddd",
    "stderr_bg": "#3a2028",
    "stderr_text": "#ff9aa2",
    "tool_success_bg": "#1f3323",
    "tool_success_border": "#4caf50",
    "tool_success_text": "#9de7a7",
    "tool_error_bg": "#3a2028",
    "tool_error_border": "#ef5350",
    "tool_error_text": "#ff9aa2",
    "tool_output_bg": "rgba(255,255,255,0.08)",
    "thinking_bg": "#242424",
    "thinking_border": "#555555",
    "thinking_text": "#b0b0b0",
    "thinking_label": "#c0c0c0",
    "inline_code_bg": "#3a3a3a",
    "inline_code_text": "#f2f2f2",
}

# Optional palette used while rendering nested HTML (code blocks inside messages).
_RENDER_PALETTE = None


def _palette_role_ids():
    """Return QPalette role ids, with stable fallbacks for headless CI."""
    try:
        from .compat import QtGui
        qp = QtGui.QPalette
        return qp.Base, qp.Text, qp.Mid, qp.AlternateBase
    except ImportError:
        return 9, 10, 11, 12


def colors_from_palette(palette) -> dict:
    """Build a theme color dict aligned with the active Qt palette."""
    base_role, text_role, mid_role, alt_role = _palette_role_ids()
    is_dark = palette.color(base_role).lightness() < 128
    colors = dict(_DARK_THEME_COLORS if is_dark else _LIGHT_THEME_COLORS)
    c = palette.color
    colors["chat_bg"] = c(base_role).name()
    colors["chat_text"] = c(text_role).name()
    colors["chat_border"] = c(mid_role).name()
    colors["code_bg"] = c(base_role).name()
    colors["code_text"] = c(text_role).name()
    colors["code_border"] = c(mid_role).name()
    colors["stdout_text"] = c(text_role).name()
    colors["inline_code_text"] = c(text_role).name()
    colors["inline_code_bg"] = c(alt_role).name()
    return colors


def _resolve_colors(palette=None) -> dict:
    """Return colors from an explicit palette, render context, or theme cache."""
    pal = palette if palette is not None else _RENDER_PALETTE
    if pal is not None:
        return colors_from_palette(pal)
    return _get_theme_colors()



def _read_freecad_mode_name() -> str:
    """Read FreeCAD's current UI mode/theme name from preferences.

    Typical values (from PreferencePacks) are:
      - "FreeCAD Dark"
      - "FreeCAD Light"
      - "FreeCAD Classic"

    A user can also run a dark/light UI by setting only the `StyleSheet`
    preference (e.g. "OpenDark.qss") without selecting a PreferencePack,
    leaving `Theme` empty. We consult `StyleSheet` as a secondary signal
    so those sessions don't fall through to the unreliable QPalette probe
    and render unreadable light-on-dark text (issue #16).

    Returns:
        The theme name string, or a sensible fallback like "Custom/Unknown".
    """
    try:
        import FreeCAD as App

        hgrp = App.ParamGet("User parameter:BaseApp/Preferences/MainWindow")
        theme = hgrp.GetString("Theme", "").strip()

        if theme:
            return theme

        stylesheet = hgrp.GetString("StyleSheet", "").strip()
        if stylesheet:
            return stylesheet

        return "Custom/Unknown"
    except Exception:
        return "Custom/Unknown"


def get_freecad_mode_name(force_refresh: bool = False) -> str:
    """Return cached FreeCAD mode name.
    Args:
        force_refresh: Re-read FreeCAD preferences instead of using cached value.
    """
    global _CACHED_THEME_NAME

    if force_refresh or _CACHED_THEME_NAME is None:
        _CACHED_THEME_NAME = _read_freecad_mode_name()
    return _CACHED_THEME_NAME


_LIGHT_THEME_HINTS = ("light", "classic", "default")
_DARK_THEME_HINTS = ("dark",)


def _is_dark_mode(theme_name: str) -> bool:
    """Return True when FreeCAD is using a dark color scheme.

    The user's `Theme` preference (from preference packs like
    "FreeCAD Light", "FreeCAD Dark", "OpenLight", "OpenDark") is the
    most reliable signal: it reflects the choice the user made, and
    won't be misled by Qt palette quirks.

    Why we don't lead with the palette: FreeCAD 1.1+ applies themes
    via QSS stylesheets, which override visual appearance but do NOT
    update QPalette on Linux when the system Qt theme is dark. A
    "FreeCAD Light" workbench on a KDE-dark host renders light but
    `widget.palette().color(Base)` still reports dark — the previous
    palette-first logic flipped to dark mode incorrectly.

    Palette probing remains as a fallback for the unnamed/custom case.
    Note: switching themes mid-session requires restarting FreeCAD.
    """
    name = (theme_name or "").strip().lower()
    if any(hint in name for hint in _DARK_THEME_HINTS):
        return True
    if any(hint in name for hint in _LIGHT_THEME_HINTS):
        return False
    # No name match — fall back to palette introspection.
    try:
        import FreeCADGui as Gui
        from .compat import QtWidgets, QtGui
        mw = Gui.getMainWindow()
        if mw:
            trees = mw.findChildren(QtWidgets.QTreeView)
            if trees:
                bg = trees[0].palette().color(QtGui.QPalette.Base)
                return bg.lightness() < 128
    except Exception:
        pass
    return False


def _colors_for_theme(theme_name: str) -> dict:
    """Return color palette matching a theme name."""
    if _is_dark_mode(theme_name):
        return _DARK_THEME_COLORS
    # Unknown/custom theme names intentionally fall back to light for readability.
    return _LIGHT_THEME_COLORS


def refresh_theme_cache() -> str:
    """Force refresh theme name and colors, then return the current theme name."""
    global _CACHED_THEME_NAME
    global _CACHED_THEME_COLORS

    _CACHED_THEME_NAME = _read_freecad_mode_name()
    _CACHED_THEME_COLORS = _colors_for_theme(_CACHED_THEME_NAME)
    return _CACHED_THEME_NAME


def _get_theme_colors(force_refresh: bool = False) -> dict:
    """Return cached colors selected by FreeCAD mode."""
    global _CACHED_THEME_COLORS

    if force_refresh:
        refresh_theme_cache()
    elif _CACHED_THEME_COLORS is None:
        _CACHED_THEME_COLORS = _colors_for_theme(get_freecad_mode_name())

    return _CACHED_THEME_COLORS


def get_chat_display_stylesheet(palette=None) -> str:
    """Return QTextBrowser stylesheet from palette or cached theme."""
    if palette is not None:
        from .theme_palette import qtextbrowser_palette_stylesheet
        return qtextbrowser_palette_stylesheet(palette)
    colors = _get_theme_colors()
    return (
        "QTextBrowser { "
        f"border: 1px solid {colors['chat_border']}; "
        f"background-color: {colors['chat_bg']}; "
        f"color: {colors['chat_text']}; "
        "}"
    )



def _chat_message_html(role, label, label_color, bg_color, body_html, palette=None, *, open_content=False):
    """Chat bubble via HTML tables (QTextBrowser ignores most CSS flex/float)."""
    colors = _resolve_colors(palette)
    text_color = colors.get("chat_text", "#e0e0e0")
    safe_label = html.escape(label)
    label_para = (
        f'<p style="margin:0 0 8px 0; color:{label_color}; font-weight:bold; '
        f'font-size:9pt;">{safe_label}</p>'
    )
    body_open = f'<p style="margin:0; color:{text_color};">'

    if role == "user":
        # Spacer columns push the colored cell to the right (~70% width).
        table = (
            '<table width="100%" cellspacing="0" cellpadding="0" '
            'style="margin-top:12px;margin-bottom:8px;">'
            "<tr>"
            '<td width="26%"></td>'
            f'<td width="66%" bgcolor="{bg_color}" style="padding:12px 14px;">'
        )
        tail = '</p></td><td width="8%"></td></tr></table>'
    elif role == "assistant":
        table = (
            '<table width="100%" cellspacing="0" cellpadding="0" '
            'style="margin-top:12px;margin-bottom:8px;">'
            "<tr>"
            '<td width="8%"></td>'
            f'<td width="66%" bgcolor="{bg_color}" style="padding:12px 14px;">'
        )
        tail = '</p></td><td width="26%"></td></tr></table>'
    else:
        table = (
            '<table width="100%" cellspacing="0" cellpadding="0" '
            'style="margin-top:8px;margin-bottom:6px;">'
            "<tr>"
            f'<td width="96%" bgcolor="{bg_color}" style="padding:10px 12px; '
            f'border-left:4px solid {label_color};">'
        )
        tail = '</p></td><td width="4%"></td></tr></table>'

    head = table + label_para + body_open
    if open_content:
        return head
    return head + body_html + tail


CHAT_STREAM_END = '</p></td><td width="26%"></td></tr></table>'


def render_message(role: str, content, palette=None) -> str:
    """Render a single chat message as an HTML block."""
    global _RENDER_PALETTE
    old_palette = _RENDER_PALETTE
    _RENDER_PALETTE = palette
    try:
        colors = _resolve_colors(palette)
        if role == "user":
            label = translate("MessageView", "You")
            bg_color = colors["user_bg"]
            label_color = colors["user_label"]
        elif role == "assistant":
            label = translate("MessageView", "AI")
            bg_color = colors["assistant_bg"]
            label_color = colors["assistant_label"]
        else:
            label = translate("MessageView", "System")
            bg_color = colors["system_bg"]
            label_color = colors["system_label"]
        if isinstance(content, list):
            formatted_content = _format_content_blocks(content)
        else:
            formatted_content = _format_content(content)
        return _chat_message_html(
            role, label, label_color, bg_color, formatted_content, palette=palette,
        )
    finally:
        _RENDER_PALETTE = old_palette


def render_code_block(code: str, language: str = "python", palette=None) -> str:
    """Render a code block as a standalone HTML element with a copy-friendly format."""
    colors = _resolve_colors(palette)
    escaped = html.escape(code.strip())
    return (
        f'<div style="margin: 6px 0; background-color: {colors["code_bg"]}; '
        f'border: 1px solid {colors["code_border"]}; border-radius: 4px; padding: 2px 0;">'
        f'<div style="padding: 2px 8px; font-size: 11px; color: {colors["code_lang"]}; '
        f'background-color: {colors["code_lang_bg"]};">{language}</div>'
        f'<pre style="margin: 0; padding: 8px; color: {colors["code_text"]}; '
        f'font-family: monospace; font-size: 13px; overflow-x: auto;">'
        f'{escaped}</pre></div>'
    )


def render_execution_result(success: bool, stdout: str, stderr: str, palette=None) -> str:
    """Render code execution results."""
    colors = _resolve_colors(palette)

    if success:
        icon = "&#10003;"  # checkmark
        color = colors["tool_success_text"]
        status = translate("MessageView", "Code executed successfully")
    else:
        icon = "&#10007;"  # X
        color = colors["tool_error_text"]
        status = translate("MessageView", "Execution failed")

    parts = [
        f'<div style="margin: 6px 0; padding: 8px 12px; '
        f'border-left: 3px solid {color}; background-color: {colors["result_bg"]}; '
        f'border-radius: 0 4px 4px 0;">'
        f'<span style="color: {color}; font-weight: bold;">'
        f'{icon} {status}</span>'
    ]

    if stdout.strip():
        escaped_out = html.escape(stdout.strip())
        parts.append(
            f'<pre style="margin: 4px 0 0 0; padding: 4px 8px; '
            f'background-color: {colors["stdout_bg"]}; font-size: 12px; '
            f'font-family: monospace; color: {colors["stdout_text"]};">{escaped_out}</pre>'
        )

    if stderr.strip():
        escaped_err = html.escape(stderr.strip())
        parts.append(
            f'<pre style="margin: 4px 0 0 0; padding: 4px 8px; '
            f'background-color: {colors["stderr_bg"]}; font-size: 12px; '
            f'font-family: monospace; color: {colors["stderr_text"]};">{escaped_err}</pre>'
        )

    parts.append('</div>')
    return "".join(parts)


def render_tool_call(tool_name: str, call_id: str, started: bool = True,
                     success: bool = True, output: str = "", palette=None) -> str:
    """Render a tool call indicator in the chat.

    Args:
        tool_name: Name of the tool being called
        call_id: Unique ID of the tool call
        started: True for "calling..." state, False for completed
        success: Whether the tool call succeeded (only used when started=False)
        output: Tool result output (only used when started=False)
    """
    colors = _resolve_colors(palette)

    if started:
        calling_text = translate("MessageView", "Calling {}...").format(
            '<b>{}</b>'.format(html.escape(tool_name)))
        return (
            f'<div style="margin: 4px 0; padding: 6px 10px; '
            f'background-color: {colors["tool_success_bg"]}; '
            f'border-left: 3px solid {colors["tool_success_border"]}; '
            f'border-radius: 0 4px 4px 0; font-size: 12px;">'
            f'<span style="color: {colors["tool_success_text"]};">&#9881; {{}}</span>'
            '</div>'.format(calling_text)
        )
    else:
        if success:
            icon = "&#10003;"
            color = colors["tool_success_text"]
            bg = colors["tool_success_bg"]
            border_color = colors["tool_success_border"]
        else:
            icon = "&#10007;"
            color = colors["tool_error_text"]
            bg = colors["tool_error_bg"]
            border_color = colors["tool_error_border"]

        parts = [
            f'<div style="margin: 4px 0; padding: 6px 10px; '
            f'background-color: {bg}; border-left: 3px solid {border_color}; '
            f'border-radius: 0 4px 4px 0; font-size: 12px;">'
            f'<span style="color: {color};">{icon} <b>{html.escape(tool_name)}</b></span>'
        ]

        if output:
            escaped_output = html.escape(output.strip())
            # Truncate very long output
            if len(escaped_output) > 500:
                escaped_output = escaped_output[:500] + "..."
            parts.append(
                f'<pre style="margin: 4px 0 0 0; padding: 4px 8px; '
                f'background-color: {colors["tool_output_bg"]}; font-size: 11px; '
                f'font-family: monospace; color: {colors["stdout_text"]};">{escaped_output}</pre>'
            )

        parts.append('</div>')
        return "".join(parts)


def render_tool_summary(timeline: list[dict], palette=None) -> str:
    """Render a compact summary of tool calls after the agentic loop.

    Args:
        timeline: List of dicts with keys: name, success, elapsed, turn.

    Returns HTML for a summary panel showing tool flow, counts, and timing.
    """
    if not timeline:
        return ""

    colors = _resolve_colors(palette)
    total = len(timeline)
    succeeded = sum(1 for t in timeline if t["success"])
    failed = total - succeeded
    total_time = sum(t["elapsed"] for t in timeline)

    # Build flow diagram: tool1 → tool2 → tool3
    flow_parts = []
    for t in timeline:
        name = html.escape(t["name"])
        if t["success"]:
            flow_parts.append(
                f'<span style="color: {colors["tool_success_text"]};">{name}</span>')
        else:
            flow_parts.append(
                f'<span style="color: {colors["tool_error_text"]};">{name}</span>')
    flow_html = ' <span style="color: {col};">&rarr;</span> '.format(
        col=colors["thinking_text"]).join(flow_parts)

    # Stats line
    if failed:
        stats = translate("MessageView",
                          "{total} tools ({succeeded} ok, {failed} failed) in {time:.1f}s"
                          ).format(total=total, succeeded=succeeded,
                                   failed=failed, time=total_time)
    else:
        stats = translate("MessageView",
                          "{total} tools in {time:.1f}s"
                          ).format(total=total, time=total_time)

    # Per-tool timing (compact)
    timing_parts = []
    for t in timeline:
        name = html.escape(t["name"])
        ms = t["elapsed"] * 1000
        if ms >= 1000:
            time_str = f"{t['elapsed']:.1f}s"
        else:
            time_str = f"{ms:.0f}ms"
        icon = "&#10003;" if t["success"] else "&#10007;"
        col = colors["tool_success_text"] if t["success"] else colors["tool_error_text"]
        timing_parts.append(
            f'<span style="color: {col};">{icon}</span> {name} '
            f'<span style="color: {colors["thinking_text"]};">{time_str}</span>')
    timing_html = " &middot; ".join(timing_parts)

    return (
        f'<div style="margin: 8px 0 4px 0; padding: 8px 10px; '
        f'background-color: {colors["code_bg"]}; '
        f'border: 1px solid {colors["code_border"]}; '
        f'border-radius: 4px; font-size: 11px;">'
        f'<div style="margin-bottom: 4px; color: {colors["thinking_label"]};">'
        f'&#9881; {stats}</div>'
        f'<div style="margin-bottom: 4px; line-height: 1.6;">{flow_html}</div>'
        f'<div style="color: {colors["code_text"]}; line-height: 1.6;">{timing_html}</div>'
        f'</div>'
    )


def _render_thinking_block(thinking_text: str, palette=None) -> str:
    """Render a <think> block as a dimmed, collapsible-style block."""
    colors = _resolve_colors(palette)

    escaped = html.escape(thinking_text.strip())
    # Truncate very long thinking
    if len(escaped) > 2000:
        escaped = escaped[:2000] + "..."
    return (
        f'<div style="margin: 4px 0; padding: 4px 8px; '
        f'background-color: {colors["thinking_bg"]}; '
        f'border-left: 2px solid {colors["thinking_border"]}; '
        f'font-size: 11px; color: {colors["thinking_text"]}; font-style: italic;">'
        f'<span style="color: {colors["thinking_label"]};">{{label}}</span><br>'
        '{text}</div>'.format(
            label=translate("MessageView", "Thinking"),
            text=escaped)
    )


def _format_content_blocks(blocks: list) -> str:
    """Convert a list of content blocks (text + images) to HTML."""
    parts = []
    for i, block in enumerate(blocks):
        if block.get("type") == "text":
            parts.append(_format_content(block["text"]))
        elif block.get("type") == "image":
            data_uri = f"data:{block['media_type']};base64,{block['data']}"
            parts.append(
                f'<a href="image:{i}">'
                f'<img src="{data_uri}" '
                f'style="max-width:150px; max-height:150px; border-radius:4px; cursor:pointer;" '
                f'title="Click to enlarge" />'
                f'</a>'
            )
    return "".join(parts)


def _format_content(text: str) -> str:
    """Convert markdown-ish text to HTML, handling code blocks and think blocks."""
    # First strip <think> blocks
    parts = []
    last_end = 0

    # Combine code blocks and think blocks into a single pass
    # by finding all special blocks and processing in order
    code_matches = list(CODE_BLOCK_RE.finditer(text))
    think_matches = list(THINK_BLOCK_RE.finditer(text))

    # Merge and sort all matches by start position
    all_matches = [(m, "code") for m in code_matches] + [(m, "think") for m in think_matches]
    all_matches.sort(key=lambda x: x[0].start())

    for match, match_type in all_matches:
        if match.start() < last_end:
            continue  # Skip overlapping matches

        # Process text before this block
        before = text[last_end:match.start()]
        if before:
            parts.append(_format_inline(html.escape(before)))

        if match_type == "code":
            language = match.group(1) or "python"
            code = match.group(2)
            parts.append(render_code_block(code, language))
        elif match_type == "think":
            parts.append(_render_thinking_block(match.group(1)))

        last_end = match.end()

    # Process remaining text after last block
    remaining = text[last_end:]
    if remaining:
        parts.append(_format_inline(html.escape(remaining)))

    return "".join(parts)


def _format_inline(text: str) -> str:
    """Apply inline formatting (bold, italic, inline code) to already-escaped HTML text."""
    colors = _resolve_colors()

    # Inline code
    text = INLINE_CODE_RE.sub(
        '<code style="background-color: {bg}; color: {fg}; padding: 1px 4px; '
        'border-radius: 3px; font-family: monospace;">\\1</code>'.format(
            bg=colors["inline_code_bg"],
            fg=colors["inline_code_text"],
        ),
        text
    )
    # Bold
    text = BOLD_RE.sub(r"<b>\1</b>", text)
    # Italic
    text = ITALIC_RE.sub(r"<i>\1</i>", text)
    return text


def render_hint(text: str, palette=None) -> str:
    """Subtle inline hint (vision tip, notes)."""
    colors = _resolve_colors(palette)
    return (
        f'<div style="color: {colors["thinking_text"]}; font-size: 9pt; '
        f'margin: 4px 12px;">{html.escape(text)}</div>'
    )


def render_status_line(text: str, variant: str = "info", palette=None) -> str:
    """Compact status banner (compaction, MCP, warnings)."""
    colors = _resolve_colors(palette)
    if variant == "success":
        bg = colors["tool_success_bg"]
        border = colors["tool_success_border"]
        fg = colors["tool_success_text"]
    elif variant == "warning":
        bg = colors["system_bg"]
        border = colors["system_label"]
        fg = colors["system_label"]
    elif variant == "error":
        bg = colors["tool_error_bg"]
        border = colors["tool_error_border"]
        fg = colors["tool_error_text"]
    else:
        bg = colors["thinking_bg"]
        border = colors["thinking_border"]
        fg = colors["thinking_text"]
    return (
        f'<div style="margin: 4px 0; padding: 6px 10px; '
        f'background-color: {bg}; border-left: 3px solid {border}; '
        f'border-radius: 0 4px 4px 0; font-size: 12px; color: {fg};">'
        f'{html.escape(text)}</div>'
    )


def render_assistant_stream_open(palette=None) -> str:
    """Open an assistant streaming message container (closes with CHAT_STREAM_END)."""
    colors = _resolve_colors(palette)
    return _chat_message_html(
        "assistant",
        translate("MessageView", "AI"),
        colors["assistant_label"],
        colors["assistant_bg"],
        "",
        palette=palette,
        open_content=True,
    )


def render_thinking_stream_open(palette=None) -> str:
    """Open a live thinking stream block."""
    colors = _resolve_colors(palette)
    return (
        f'<div style="margin: 4px 0; padding: 4px 8px; '
        f'background-color: {colors["thinking_bg"]}; '
        f'border-left: 2px solid {colors["thinking_border"]}; '
        f'font-size: 11px; color: {colors["thinking_text"]}; font-style: italic;">'
        f'<span style="color: {colors["thinking_label"]};">'
        f'{translate("MessageView", "Thinking...")}</span><br>'
    )


def render_thinking_stream_chunk(chunk: str, palette=None) -> str:
    """Append escaped text to an open thinking stream block."""
    colors = _resolve_colors(palette)
    escaped = html.escape(chunk).replace("\n", "<br>")
    return (
        f'<span style="color: {colors["thinking_text"]}; font-size: 11px;">'
        f'{escaped}</span>'
    )


def render_plan_buttons(code: str, palette=None) -> str:
    """Plan-mode Execute/Copy anchor buttons."""
    import base64

    colors = _resolve_colors(palette)
    encoded = base64.b64encode(code.encode()).decode()
    execute_lbl = translate("MessageView", "Execute")
    copy_lbl = translate("MessageView", "Copy")
    return (
        '<div style="margin: 2px 0 8px 0;">'
        f'<a href="execute:{encoded}" style="text-decoration: none; '
        f'background-color: {colors["tool_success_border"]}; '
        f'color: {colors["chat_bg"]}; padding: 3px 12px; '
        f'border-radius: 3px; font-size: 12px; margin-right: 6px;">'
        f'{execute_lbl}</a> '
        f'<a href="copy:{encoded}" style="text-decoration: none; '
        f'background-color: {colors["code_lang_bg"]}; '
        f'color: {colors["code_text"]}; padding: 3px 12px; '
        f'border-radius: 3px; font-size: 12px;">{copy_lbl}</a>'
        '</div>'
    )
