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


class TestEmptySketchNullShapeNotReported:
    """Regression for issue #18 follow-up: "create a sketch on the selected
    face" produces an EMPTY sketch (geometry is drawn later in the editor).
    On FreeCAD 1.1 an empty Sketcher::SketchObject has Shape.isNull() == True
    while its State stays "Up-to-date" — a valid intermediate state, not a
    defect. The post-execution validator reported it as "has null shape",
    failing the exact "sketch on a selected face" workflow #20 was meant to
    enable; the model then injected a junk placeholder circle to defeat the
    check, which is what the user saw ("a random circular sketch somewhere
    instead of the selected rectangular face").
    """

    def test_empty_sketch_on_valid_face_passes(self, freecad_available):
        # Mirror the user's case: an empty sketch attached to a real planar
        # face of an imported-like solid. The attachment is valid, so the only
        # thing that can fail is the empty sketch's (benign) null shape.
        code = (
            "import Part\n"
            "f = doc.addObject('Part::Feature', 'ImportedSolid')\n"
            "f.Shape = Part.makeBox(40, 40, 40)\n"
            "doc.recompute()\n"
            "sk = doc.addObject('Sketcher::SketchObject', 'Sketch_Face1')\n"
            "sk.AttachmentSupport = [(f, 'Face1')]\n"
            "sk.MapMode = 'FlatFace'\n"
            "doc.recompute()\n"
        )
        ok, err = _sandbox_test(code, timeout=60)
        assert ok is True, (
            "an empty sketch validly attached to a face must pass the sandbox; "
            "got err=" + err
        )

    def test_empty_body_passes(self, freecad_available):
        # A PartDesign::Body before its first feature also has a benign null
        # shape while Up-to-date.
        code = "doc.addObject('PartDesign::Body', 'Body')\n"
        ok, err = _sandbox_test(code, timeout=60)
        assert ok is True, "an empty body must pass the sandbox; got err=" + err


@pytest.fixture
def simple_saved_doc(freecad_available):
    """Path to a trivial saved .FCStd (one box) — exercises the openDocument
    branch of the sandbox harness."""
    fd, doc_path = tempfile.mkstemp(suffix=".FCStd")
    os.close(fd)
    os.unlink(doc_path)
    builder = (
        "import FreeCAD as App, Part\n"
        "doc = App.newDocument('Simple')\n"
        "f = doc.addObject('Part::Feature', 'Box')\n"
        "f.Shape = Part.makeBox(10, 10, 10)\n"
        "doc.recompute()\n"
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
    assert os.path.isfile(doc_path), "fixture failed to save the document"
    yield doc_path
    for p in (doc_path, script):
        try:
            os.unlink(p)
        except OSError:
            pass


class TestSandboxExitsAgainstOpenedDocument:
    """Regression for issue #14: the harness script wrote its result file but
    never forced the interpreter to exit. On FreeCAD builds where running a
    script via `-c` against an OPENED document leaves the process in
    interactive mode (the Qt/console event loop never returns), the subprocess
    never terminated, so subprocess.run() blocked until its timeout and the
    sandbox reported a spurious "code timed out" — even for trivial code like
    creating a box. The harness now calls os._exit(0) after writing the result.

    Diagnosed and first patched by @galberding on the issue thread.
    """

    def test_trivial_code_against_opened_document_does_not_hang(
        self, simple_saved_doc
    ):
        # Without the os._exit(0) fix this blocks for the full timeout and
        # returns ("Sandbox: code timed out after N seconds"); with it, the
        # subprocess exits as soon as the result file is written.
        code = (
            "f = doc.addObject('Part::Feature', 'Box2')\n"
            "import Part\n"
            "f.Shape = Part.makeBox(5, 5, 5)\n"
            "doc.recompute()\n"
        )
        ok, err = _sandbox_test(code, timeout=30, document_path=simple_saved_doc)
        assert "timed out" not in err, (
            "sandbox hung on the openDocument path — the harness must force "
            "the interpreter to exit after writing its result (issue #14)"
        )
        assert ok is True, "trivial valid code on an opened document must pass; got err=" + err
