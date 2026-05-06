"""Tests for hook and user-tool starter templates and editor preference."""
import ast

import freecad_ai.config as config_mod
from freecad_ai.extensions.file_templates import (
    render_hook_template,
    render_user_tool_template,
)
from freecad_ai.extensions.user_tools import validate_file
from freecad_ai.hooks.registry import VALID_EVENTS


class TestHookTemplate:
    def test_parses_as_python(self):
        ast.parse(render_hook_template("my-hook"))

    def test_includes_handler_for_every_valid_event(self):
        rendered = render_hook_template("x")
        for event in VALID_EVENTS:
            assert f"def on_{event}(context):" in rendered

    def test_no_extra_handlers_beyond_valid_events(self):
        tree = ast.parse(render_hook_template("x"))
        funcs = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
        expected = {f"on_{e}" for e in VALID_EVENTS}
        assert set(funcs) == expected

    def test_name_appears_in_docstring(self):
        assert "my-hook" in render_hook_template("my-hook")


class TestUserToolTemplate:
    def test_parses_as_python(self):
        ast.parse(render_user_tool_template("my_tool"))

    def test_passes_validate_file(self, tmp_path):
        fpath = tmp_path / "my_tool.py"
        fpath.write_text(render_user_tool_template("my_tool"))
        result = validate_file(str(fpath))
        assert result.valid, f"template failed validation: {result.error}"
        assert result.warnings == [], f"template emitted warnings: {result.warnings}"

    def test_function_name_matches_argument(self, tmp_path):
        fpath = tmp_path / "compute_thing.py"
        fpath.write_text(render_user_tool_template("compute_thing"))
        result = validate_file(str(fpath))
        assert [f.name for f in result.functions] == ["compute_thing"]


class TestEditorPreference:
    def test_default_is_false(self):
        """New AppConfig defaults to FreeCAD's docked editor."""
        assert config_mod.AppConfig().use_external_editor is False

    def test_config_roundtrip(self, tmp_config_dir):
        """use_external_editor survives save/load."""
        cfg = config_mod.AppConfig()
        cfg.use_external_editor = True
        config_mod.save_config(cfg)
        loaded = config_mod.load_config()
        assert loaded.use_external_editor is True

    def test_unknown_field_filtered_on_load(self, tmp_config_dir):
        """Older configs without the field still load with the default."""
        import json
        # Simulate a pre-feature config.json
        with open(config_mod.CONFIG_FILE, "w") as f:
            json.dump({"provider": {}, "max_tokens": 4096}, f)
        loaded = config_mod.load_config()
        assert loaded.use_external_editor is False
