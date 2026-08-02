"""Code that calls tool names as Python functions is rejected up front.

Observed in Act mode: instead of issuing tool calls, the model answered a
modelling request with a ```python block calling `create_body(label="Die")`
and `create_primitive(...)`. Those are tools, not functions in FreeCAD's
interpreter — the code cannot run. The retry loop then burned two of its
three attempts on an unrelated "No active document" error, never reaching
the real problem.
"""

from freecad_ai.core.executor import _tool_names_called_as_functions as check


class TestDetection:
    def test_flags_the_observed_case(self):
        code = ('body = create_body(label="Die")\n'
                'box = create_primitive(shape_type="box", length=20)')
        msg = check(code)
        assert "create_body" in msg and "create_primitive" in msg
        assert "TOOLS" in msg

    def test_message_tells_the_model_what_to_do(self):
        msg = check("fillet_edges(object_name='Die')")
        assert "issue real tool calls" in msg
        assert "execute_code only for genuine FreeCAD Python" in msg

    def test_real_freecad_code_passes(self):
        code = ("import FreeCAD as App\n"
                "doc = App.ActiveDocument\n"
                "obj = doc.getObject('Body')\n"
                "doc.recompute()")
        assert check(code) == ""

    def test_method_calls_are_not_flagged(self):
        """`undo` is a tool name, but `doc.undo()` is legitimate Python."""
        assert check("doc.undo()") == ""
        assert check("App.ActiveDocument.undo()") == ""

    def test_bare_call_to_a_tool_name_is_flagged(self):
        assert check("undo()") != ""

    def test_empty_and_plain_code(self):
        assert check("") == ""
        assert check("x = 1 + 2\nprint(x)") == ""

    def test_several_names_listed_sorted(self):
        msg = check("pad_sketch()\ncreate_sketch()")
        assert msg.index("create_sketch") < msg.index("pad_sketch")


class TestAutoDocument:
    def test_execute_code_creates_a_document_like_the_tools(self):
        """The structured tools create a document rather than refusing;
        execute_code refusing was an inconsistency that blocked a fresh
        FreeCAD session."""
        import inspect
        from freecad_ai.core import executor

        src = inspect.getsource(executor.execute_code)
        assert "App.newDocument()" in src
        assert "refresh_gui_for_document(target_doc)" in src
        # the explicit refusal survives as the last resort
        assert "No active document" in src

    def test_confusion_check_runs_before_execution(self):
        import inspect
        from freecad_ai.core import executor

        src = inspect.getsource(executor.execute_code)
        assert "_tool_names_called_as_functions(code)" in src
        assert src.index("_tool_names_called_as_functions") < src.index("doc_name = target_doc.Name")
