"""Interactive geometry selection panel.

Provides a floating panel that lets the user select edges, faces, or vertices
in the 3D viewport. Used by the select_geometry tool to pause the agentic
loop and collect user clicks before returning precise sub-element names.
"""

from .compat import QtWidgets, QtCore
from .theme_palette import label_muted_stylesheet


class _SelectionObserver:
    """FreeCAD selection observer that collects sub-element picks."""

    def __init__(self, select_type, on_changed):
        self._select_type = select_type
        self._on_changed = on_changed
        self.selections = []

    def _matches_type(self, sub):
        if self._select_type == "any":
            return True
        prefix = {"edge": "Edge", "face": "Face", "vertex": "Vertex"}.get(
            self._select_type, ""
        )
        return sub.startswith(prefix)

    def addSelection(self, doc, obj, sub, pnt):  # noqa: N802 — FreeCAD API
        if sub and self._matches_type(sub):
            self.selections.append({
                "object": obj,
                "sub_element": sub,
                "point": [pnt.x, pnt.y, pnt.z],
            })
            self._on_changed()

    def removeSelection(self, doc, obj, sub):  # noqa: N802
        self.selections = [
            s for s in self.selections
            if not (s["object"] == obj and s["sub_element"] == sub)
        ]
        self._on_changed()

    def clearSelection(self, doc):  # noqa: N802
        self.selections.clear()
        self._on_changed()


class SelectionPanel(QtWidgets.QWidget):
    """Floating panel that collects geometry selections from the viewport.

    Usage::

        panel = SelectionPanel("Select edges to fillet", "edge", max_count=0)
        selections = panel.exec()  # blocks until Done/Cancel
    """

    def __init__(self, prompt="Select geometry", select_type="any", max_count=0):
        super().__init__(None)
        self._select_type = select_type
        self._max_count = max_count
        self._selections = []
        self._loop = None

        self.setWindowTitle("Select Geometry")
        self.setWindowFlags(
            QtCore.Qt.Tool | QtCore.Qt.WindowStaysOnTopHint
        )
        self.setMinimumWidth(300)

        layout = QtWidgets.QVBoxLayout(self)

        # Prompt label
        label = QtWidgets.QLabel(prompt)
        label.setWordWrap(True)
        layout.addWidget(label)

        # Type hint
        if select_type != "any":
            hint = QtWidgets.QLabel(f"(Accepting: {select_type}s only)")
            hint.setStyleSheet(label_muted_stylesheet(hint.palette()) + " font-style: italic;")
            layout.addWidget(hint)

        # Live selection list
        self._list = QtWidgets.QListWidget()
        layout.addWidget(self._list)

        # Buttons
        btn_layout = QtWidgets.QHBoxLayout()
        self._done_btn = QtWidgets.QPushButton("Done")
        self._cancel_btn = QtWidgets.QPushButton("Cancel")
        btn_layout.addWidget(self._done_btn)
        btn_layout.addWidget(self._cancel_btn)
        layout.addLayout(btn_layout)

        self._done_btn.clicked.connect(self._on_done)
        self._cancel_btn.clicked.connect(self._on_cancel)

    def _on_selection_changed(self):
        """Update the list widget from the observer's selections."""
        self._list.clear()
        for s in self._observer.selections:
            self._list.addItem(f"{s['object']}.{s['sub_element']}")
        # Auto-finish if max_count reached
        if self._max_count > 0 and len(self._observer.selections) >= self._max_count:
            self._on_done()

    def _on_done(self):
        self._selections = list(self._observer.selections)
        if self._loop:
            self._loop.quit()

    def _on_cancel(self):
        self._selections = []
        if self._loop:
            self._loop.quit()

    def exec(self):
        """Show panel, collect selections, return list of dicts.

        Blocks the caller via a local QEventLoop while still processing
        Qt events (so the 3D viewport remains interactive).

        Returns:
            List of {"object": str, "sub_element": str, "point": [x,y,z]}
        """
        self._observer = _SelectionObserver(
            self._select_type, self._on_selection_changed
        )
        import FreeCADGui as Gui
        Gui.Selection.addObserver(self._observer)
        self.show()
        self._loop = QtCore.QEventLoop()
        self._loop.exec_()
        Gui.Selection.removeObserver(self._observer)
        self.hide()
        self.deleteLater()
        return self._selections
