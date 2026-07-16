"""Qt palette helpers for theme-aware widget styling."""

from .compat import QtGui

QPalette = QtGui.QPalette


def palette_color_name(palette, role):
    """Return a CSS color string for a QPalette role."""
    return palette.color(role).name()


def qtextedit_palette_stylesheet(palette, border_color=None):
    """Build QTextEdit stylesheet from the active Qt palette."""
    border = border_color or palette_color_name(palette, QPalette.Mid)
    return (
        "QTextEdit { "
        f"background-color: {palette_color_name(palette, QPalette.Base)}; "
        f"color: {palette_color_name(palette, QPalette.Text)}; "
        f"selection-background-color: {palette_color_name(palette, QPalette.Highlight)}; "
        f"selection-color: {palette_color_name(palette, QPalette.HighlightedText)}; "
        f"border: 1px solid {border}; "
        "padding: 8px; }"
    )


def qtextbrowser_palette_stylesheet(palette, border_color=None):
    """Build QTextBrowser stylesheet from the active Qt palette."""
    border = border_color or palette_color_name(palette, QPalette.Mid)
    return (
        "QTextBrowser { "
        f"border: 1px solid {border}; "
        f"background-color: {palette_color_name(palette, QPalette.Base)}; "
        f"color: {palette_color_name(palette, QPalette.Text)}; "
        "}"
    )


def pushbutton_accent_stylesheet(palette, *, padding="8px 16px"):
    """Primary action button using Highlight roles."""
    return (
        "QPushButton { "
        f"background-color: {palette_color_name(palette, QPalette.Highlight)}; "
        f"color: {palette_color_name(palette, QPalette.HighlightedText)}; "
        f"font-weight: bold; padding: {padding}; }}"
    )


def pushbutton_loading_stylesheet(palette, *, padding="8px 16px"):
    """Stop/loading button using secondary palette roles."""
    return (
        "QPushButton { "
        f"background-color: {palette_color_name(palette, QPalette.Mid)}; "
        f"color: {palette_color_name(palette, QPalette.Text)}; "
        f"font-weight: bold; padding: {padding}; }}"
    )


def progressbar_gauge_stylesheet(palette, *, chunk_color=None, height=4):
    """Flat thin gauge (context usage): no border, no text, palette fill.

    The fill uses the Highlight role unless an explicit semantic color is
    given (e.g. a warning color as the gauge approaches the compaction
    threshold).
    """
    fill = chunk_color or palette_color_name(palette, QPalette.Highlight)
    return (
        "QProgressBar { "
        f"background-color: {palette_color_name(palette, QPalette.Mid)}; "
        f"border: none; max-height: {height}px; min-height: {height}px; }} "
        "QProgressBar::chunk { "
        f"background-color: {fill}; }}"
    )


def label_muted_stylesheet(palette):
    """Muted helper/status label."""
    return f"color: {palette_color_name(palette, QPalette.PlaceholderText)};"


def label_status_stylesheet(color):
    """Bold status label using an explicit semantic color."""
    return f"color: {color}; font-weight: bold;"


def danger_banner_stylesheet(background_color, foreground_color):
    """High-visibility dangerous-mode banner."""
    return (
        f"background-color: {background_color}; "
        f"color: {foreground_color}; "
        "font-weight: bold; padding: 4px;"
    )
