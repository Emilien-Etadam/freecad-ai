"""Integration test for sandbox post-execution validation.

Regression test for a false-positive in _sandbox_test: code that produced
FreeCAD C++ console errors (e.g. "PositionBySupport: AttachEngine3D: subshape
not found") or built null/invalid shapes was reported as safe because no
Python exception was raised during recompute.
"""

import os
import subprocess
import tempfile

import pytest

from freecad_ai.core.executor import _sandbox_test, _find_freecad_cmd

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def freecad_available():
    if not _find_freecad_cmd():
        pytest.skip("No FreeCAD binary available for sandbox tests")


# A solid built from an open (non-watertight) shell is OCC-invalid — the same
# class of invalidity an imported-and-converted STL mesh→solid produces.
_INVALID_SOLID = (
    "import Part\n"
    "box = Part.makeBox(10, 10, 10)\n"
    "bad = Part.Solid(Part.Shell(box.Faces[:-1]))\n"
    "f = doc.addObject('Part::Feature', 'BadImport')\n"
    "f.Shape = bad\n"
)


@pytest.fixture
def doc_with_invalid_solid(freecad_available):
    """Path to a saved .FCStd containing a pre-existing OCC-invalid solid."""
    fd, doc_path = tempfile.mkstemp(suffix=".FCStd")
    os.close(fd)
    os.unlink(doc_path)  # FreeCAD writes it fresh
    builder = (
        "import FreeCAD as App, Part\n"
        "doc = App.newDocument('Inv')\n"
        "box = Part.makeBox(10, 10, 10)\n"
        "bad = Part.Solid(Part.Shell(box.Faces[:-1]))\n"
        "f = doc.addObject('Part::Feature', 'BadImport')\n"
        "f.Shape = bad\n"
        "doc.saveAs(" + repr(doc_path) + ")\n"
        "exit(0)\n"
    )
    script = tempfile.mktemp(suffix=".py")
    with open(script, "w") as fh:
        fh.write(builder)
    subprocess.run(
        [_find_freecad_cmd(), "-c", script],
        capture_output=True,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        timeout=120,
    )
    assert os.path.isfile(doc_path), "fixture failed to save the invalid document"
    yield doc_path
    for p in (doc_path, script):
        try:
            os.unlink(p)
        except OSError:
            pass


class TestSandboxPostExecValidation:
    def test_valid_partdesign_body_passes(self, freecad_available):
        # Positive control — a clean PartDesign body must still pass.
        code = (
            "body = doc.addObject('PartDesign::Body', 'Body')\n"
            "box = body.newObject('PartDesign::AdditiveBox', 'Box')\n"
        )
        ok, err = _sandbox_test(code, timeout=30)
        assert ok is True, "Valid code should pass sandbox; got err=" + err

    def test_bad_attachment_face_is_caught(self, freecad_available):
        # Attaching a sketch to a face that doesn't exist — the exact class
        # of failure that slipped through before: no Python exception, but
        # the Report View fills with PositionBySupport errors and the sketch
        # ends up in an invalid state.
        code = (
            "body = doc.addObject('PartDesign::Body', 'Body')\n"
            "box = body.newObject('PartDesign::AdditiveBox', 'Box')\n"
            "doc.recompute()\n"
            "sketch = body.newObject('Sketcher::SketchObject', 'Sketch')\n"
            "sketch.AttachmentSupport = [(box, 'Face99')]\n"
            "sketch.MapMode = 'FlatFace'\n"
        )
        ok, err = _sandbox_test(code, timeout=30)
        assert ok is False, "Sandbox should catch bad attachment"
        assert err, "Sandbox failure must include a reason"


class TestSandboxIgnoresPreexistingInvalidity:
    """Regression: the sandbox opens a copy of the saved document, so a
    pre-existing OCC-invalid object (e.g. an imported mesh→solid) is present
    on every dry-run. Code that never touched it — like a sketch on a selected
    face — was failed by the post-execution validator and the model chased a
    phantom bug across all retries. The validator must blame the code only for
    shapes it creates or newly breaks.
    """

    def test_unrelated_code_passes_despite_preexisting_invalid_solid(
        self, doc_with_invalid_solid
    ):
        # Clean, unrelated code added to a document that already contains an
        # invalid solid must pass — the invalid solid is not this code's fault.
        clean = (
            "import Part\n"
            "newf = doc.addObject('Part::Feature', 'CleanBox')\n"
            "newf.Shape = Part.makeBox(5, 5, 5)\n"
        )
        ok, err = _sandbox_test(clean, timeout=90, document_path=doc_with_invalid_solid)
        assert ok is True, (
            "unrelated valid code must not be failed by a pre-existing invalid "
            "object; got err=" + err
        )

    def test_newly_created_invalid_shape_still_caught(self, freecad_available):
        # Negative control: a NEW invalid object the code creates is still its
        # fault and must be reported, fix or no fix.
        ok, err = _sandbox_test(_INVALID_SOLID, timeout=90)
        assert ok is False, "a newly-created invalid shape must still fail"
        assert "invalid shape" in err, "failure must name the invalid shape"
