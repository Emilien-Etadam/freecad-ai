"""Transformation features must become the Body's Tip.

`body.newObject()` appends a feature to the Body's group but does not
always advance `body.Tip`. When it doesn't, the Body's shape is still
computed up to the previous feature: the pattern shows up in the model
tree and in `describe_model`'s feature list while contributing nothing to
the geometry.

Observed twice on a real flange. First run: `polar_pattern(occurrences=8)`
reported success, `describe_model` showed 10 faces — the count for a
single hole. Second run: two `polar_pattern` calls reported "NO change",
then `multi_transform` with the *same* polar step produced all six holes.
The only difference between the two code paths was `body.Tip = multi`.
"""

from pathlib import Path

_MODIFIERS = (Path(__file__).resolve().parents[2]
              / "freecad_ai" / "tools" / "handlers" / "modifiers.py")


def _handler_src(name: str) -> str:
    src = _MODIFIERS.read_text()
    return src.split(f"def {name}")[1].split("\ndef ")[0]


class TestTipIsAdvanced:
    def test_polar_pattern_sets_tip(self):
        assert "body.Tip = pattern" in _handler_src("_handle_polar_pattern")

    def test_linear_pattern_sets_tip(self):
        assert "body.Tip = pattern" in _handler_src("_handle_linear_pattern")

    def test_mirror_feature_sets_tip(self):
        assert "body.Tip = mirror" in _handler_src("_handle_mirror_feature")

    def test_multi_transform_still_sets_tip(self):
        """The one path that already worked — must not regress."""
        assert "body.Tip = multi" in _handler_src("_handle_multi_transform")

    def test_tip_is_set_before_the_measurement(self):
        """Setting the Tip after recomputing would measure the stale shape and
        report a false 'NO change'."""
        for handler, tip in (("_handle_polar_pattern", "body.Tip = pattern"),
                             ("_handle_linear_pattern", "body.Tip = pattern"),
                             ("_handle_mirror_feature", "body.Tip = mirror")):
            body = _handler_src(handler)
            assert body.index(tip) < body.index("doc.recompute()"), handler

    def test_every_transformation_handler_is_covered(self):
        """A new transformation tool must not silently skip the Tip."""
        src = _MODIFIERS.read_text()
        assert src.count("body.Tip = ") == 4
