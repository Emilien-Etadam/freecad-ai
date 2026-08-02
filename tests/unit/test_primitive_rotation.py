"""create_primitive accepts a rotation, not just a position.

Cylinders and cones are Z-axis aligned in FreeCAD. Without rotation
parameters a subtractive cylinder could only cut into a horizontal face,
so "cylindrical pips on every face of a die" was impossible with the
structured tools — the model fell back to raw Part booleans in
execute_code, producing a dead solid with no feature tree.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2] / "freecad_ai"


def _src(relpath: str) -> str:
    return (_ROOT / relpath).read_text()


class TestRotationParameters:
    def test_handler_accepts_rotation(self):
        from freecad_ai.tools.freecad_tools import _handle_create_primitive
        import inspect

        params = inspect.signature(_handle_create_primitive).parameters
        for name in ("rot_x", "rot_y", "rot_z"):
            assert name in params, name
            assert params[name].default == 0.0

    def test_tool_definition_exposes_rotation(self):
        from freecad_ai.tools.freecad_tools import CREATE_PRIMITIVE

        names = [p.name for p in CREATE_PRIMITIVE.parameters]
        for name in ("rot_x", "rot_y", "rot_z"):
            assert name in names, name
        rot_x = next(p for p in CREATE_PRIMITIVE.parameters if p.name == "rot_x")
        assert rot_x.required is False
        # The description must tell the model how to aim a cylinder
        assert "90" in rot_x.description

    def test_rotation_applied_as_yaw_pitch_roll(self):
        """App.Rotation takes (yaw, pitch, roll) = (Z, Y, X) — an argument
        order swap here silently aims pockets at the wrong face."""
        src = _src("tools/handlers/part_creation.py")
        assert "App.Rotation(rot_z, rot_y, rot_x)" in src

    def test_position_only_path_preserved(self):
        """A call with no rotation must keep the original placement path."""
        src = _src("tools/handlers/part_creation.py")
        body = src.split("def _handle_create_primitive")[1].split("\ndef ")[0]
        assert "elif x != 0 or y != 0 or z != 0:" in body
        assert "obj.Placement.Base = App.Vector(x, y, z)" in body


class TestDiePatternGuidance:
    def test_pattern_covers_cylindrical_pips(self):
        src = _src("core/system_prompt.py")
        pattern = src.split("**Playing die")[1].split('"""')[0]
        assert "rot_x=90" in pattern and "rot_y=90" in pattern
        assert "sphere" in pattern  # the no-rotation option stays documented

    def test_pattern_forbids_dead_solid_fallback(self):
        src = _src("core/system_prompt.py")
        pattern = src.split("**Playing die")[1].split('"""')[0]
        assert "dead solid" in pattern
        assert "Part.makeBox" in pattern
