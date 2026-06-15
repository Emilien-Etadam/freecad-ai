"""Regression tests for the SSE MCP server transport (PR #29 review fixes).

Covers two issues found in code review:
  - SSE socket writes must be serialized under ``_sse_lock`` so the keepalive
    loop and a tool response (running on different ThreadingMixIn threads)
    cannot interleave bytes and corrupt the event stream.
  - The HTTP entry-point script must not reference ``__file__`` unguarded,
    because its documented ``exec(open(...).read())`` usage has no ``__file__``.
"""

import pathlib

import pytest

from freecad_ai.mcp.transport import SSEServerTransport


# ---------------------------------------------------------------------------
# Issue A — concurrent SSE writes must hold the lock
# ---------------------------------------------------------------------------

class _LockProbe:
    """Fake wfile that records whether ``_sse_lock`` is held during write().

    A plain ``threading.Lock`` returns ``False`` from ``acquire(blocking=False)``
    whenever it is already held, so this deterministically detects whether the
    transport serializes the actual write (not merely the pointer read).
    """

    def __init__(self, lock):
        self._lock = lock
        self.lock_held_during_write = None
        self.written = b""

    def write(self, data):
        acquired = self._lock.acquire(blocking=False)
        self.lock_held_during_write = not acquired
        if acquired:
            self._lock.release()
        self.written += data

    def flush(self):
        pass


def test_send_sse_holds_lock_during_write():
    transport = SSEServerTransport()
    probe = _LockProbe(transport._sse_lock)
    transport._sse_wfile = probe

    transport._send_sse({"jsonrpc": "2.0", "id": 1, "result": {}})

    assert probe.lock_held_during_write is True


def test_send_sse_frames_message_as_sse_event():
    transport = SSEServerTransport()
    probe = _LockProbe(transport._sse_lock)
    transport._sse_wfile = probe

    transport._send_sse({"jsonrpc": "2.0", "id": 7, "result": {"ok": True}})

    text = probe.written.decode()
    assert text.startswith("event: message\ndata: ")
    assert text.endswith("\n\n")
    assert '"id":7' in text


def test_write_locked_holds_lock_during_write():
    transport = SSEServerTransport()
    probe = _LockProbe(transport._sse_lock)
    transport._sse_wfile = probe

    assert transport._write_locked(b": keepalive\n\n") is True
    assert probe.lock_held_during_write is True
    assert probe.written == b": keepalive\n\n"


def test_write_locked_returns_false_without_client():
    transport = SSEServerTransport()
    transport._sse_wfile = None

    assert transport._write_locked(b"data") is False


def test_write_locked_clears_client_on_broken_pipe():
    transport = SSEServerTransport()

    class _Broken:
        def write(self, data):
            raise BrokenPipeError()

        def flush(self):
            pass

    transport._sse_wfile = _Broken()

    assert transport._write_locked(b"data") is False
    assert transport._sse_wfile is None


# ---------------------------------------------------------------------------
# Issue D — entry-point script must be safe under exec() (no __file__)
# ---------------------------------------------------------------------------

def test_http_entry_point_safe_under_exec():
    """exec(open('mcp_server_http.py').read()) must not raise NameError on __file__.

    The script's docstring documents this exact usage. ``exec`` of source text
    defines no ``__file__``; the script must guard the reference. Running it in
    a unit env can't import the FreeCAD C++ module, so reaching ``import
    FreeCAD`` (ImportError) proves we got past the ``__file__`` handling.
    """
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    source = (repo_root / "mcp_server_http.py").read_text()
    code = compile(source, "mcp_server_http.py", "exec")

    namespace = {}  # mimics exec(open(...).read()): no __file__ defined
    try:
        exec(code, namespace)
    except (ImportError, ModuleNotFoundError):
        pass  # reached `import FreeCAD` — past the __file__ guard, as intended
    except NameError as exc:
        if "__file__" in str(exc):
            pytest.fail(
                "mcp_server_http.py references __file__ unguarded — "
                "breaks the documented exec(open(...).read()) usage"
            )
        raise
