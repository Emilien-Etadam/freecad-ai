"""A pattern or mirror that changes nothing says so.

Observed on a real flange: `polar_pattern(occurrences=8)` returned
"Created polar pattern … (8 occurrences, 360.0° span, axis=Z)" and the
model reported eight bolt holes. `describe_model` showed **10 faces** —
the count for a single hole. The pattern had produced no occurrence at
all, and nothing in the result said so.
"""

import inspect
from pathlib import Path

from freecad_ai.tools.tool_common import (
    _body_solid_stats,
    _feature_error_state,
    _pattern_effect_note,
)

_MODIFIERS = (Path(__file__).resolve().parents[2]
              / "freecad_ai" / "tools" / "handlers" / "modifiers.py")


class _FakeShape:
    def __init__(self, volume, faces):
        self.Volume = volume
        self.Faces = list(range(faces))


class _FakeBody:
    def __init__(self, volume=None, faces=0):
        if volume is not None:
            self.Shape = _FakeShape(volume, faces)


class _FakeFeature:
    def __init__(self, state):
        self.State = state


class TestSolidStats:
    def test_reads_volume_and_faces(self):
        assert _body_solid_stats(_FakeBody(100.0, 10)) == (100.0, 10)

    def test_unmeasurable(self):
        assert _body_solid_stats(_FakeBody()) == (None, None)


class TestFeatureState:
    def test_clean_state(self):
        assert _feature_error_state(_FakeFeature(["Up-to-date"])) == ""

    def test_error_state(self):
        assert _feature_error_state(_FakeFeature(["Error"])) == "Error"

    def test_invalid_state(self):
        assert _feature_error_state(_FakeFeature(["Invalid"])) == "Invalid"

    def test_touched_is_not_an_error(self):
        """Touched just means "needs recompute" — not a failure."""
        assert _feature_error_state(_FakeFeature(["Touched"])) == ""

    def test_missing_state_attribute(self):
        assert _feature_error_state(object()) == ""


class TestEffectNote:
    def test_the_observed_flange_case(self):
        """8 occurrences requested, solid identical — must be flagged."""
        note = _pattern_effect_note(
            "polar pattern", (149160.0, 10), (149160.0, 10), occurrences=8)
        assert "NO change" in note
        assert "do NOT assume it worked" in note

    def test_working_pattern_reports_face_delta(self):
        note = _pattern_effect_note(
            "polar pattern", (150000.0, 10), (143000.0, 24), occurrences=8)
        assert "8 occurrences" in note
        assert "+14 faces" in note
        assert "[!]" not in note

    def test_volume_change_alone_counts(self):
        """An additive pattern may merge faces while adding material."""
        note = _pattern_effect_note(
            "linear pattern", (100.0, 6), (200.0, 6), occurrences=2)
        assert "[!]" not in note

    def test_face_change_alone_counts(self):
        note = _pattern_effect_note("mirror", (100.0, 6), (100.0, 11))
        assert "[!]" not in note

    def test_error_state_takes_precedence(self):
        note = _pattern_effect_note(
            "polar pattern", (100.0, 6), (100.0, 6),
            occurrences=8, feature_state="Error")
        assert "did not build" in note
        assert "Error" in note

    def test_unmeasurable_is_silent(self):
        assert _pattern_effect_note("mirror", (None, None), (100.0, 6)) == ""
        assert _pattern_effect_note("mirror", (100.0, 6), (None, None)) == ""

    def test_mirror_note_without_occurrences(self):
        note = _pattern_effect_note("mirror", (100.0, 6), (120.0, 10))
        assert "faces" in note
        assert "occurrences" not in note

    def test_float_noise_is_no_change(self):
        note = _pattern_effect_note(
            "polar pattern", (100.0, 6), (100.0 - 1e-9, 6), occurrences=4)
        assert "NO change" in note


class TestWiring:
    def _src(self):
        return _MODIFIERS.read_text()

    def test_all_three_transformations_check(self):
        src = self._src()
        assert src.count("_pattern_effect_note(") == 3  # polar, linear, mirror
        assert src.count("before = _body_solid_stats(body)") == 3

    def test_each_recomputes_before_measuring(self):
        """Without a recompute inside the transaction the shape is stale and
        every pattern would look like it changed nothing."""
        src = self._src()
        for handler in ("_handle_polar_pattern", "_handle_linear_pattern",
                        "_handle_mirror_feature"):
            body = src.split(f"def {handler}")[1].split("\ndef ")[0]
            assert "doc.recompute()" in body, handler
            assert "_pattern_effect_note(" in body, handler
            assert "_feature_error_state(" in body, handler

    def test_note_reaches_the_output(self):
        src = self._src()
        assert src.count("{note}\",") == 3  # interpolated into all three outputs

    def test_helpers_exported(self):
        from freecad_ai.tools import tool_common
        from freecad_ai.tools.handlers import modifiers

        for name in ("_body_solid_stats", "_feature_error_state",
                     "_pattern_effect_note"):
            assert name in tool_common.__all__, name
            assert hasattr(modifiers, name), name
