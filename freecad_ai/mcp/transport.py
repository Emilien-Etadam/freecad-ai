"""Transports for MCP communication.

StdioClientTransport — manages a subprocess MCP server (client side).
StdioServerTransport — reads stdin / writes stdout (server side).
SSEServerTransport  — serves MCP over HTTP with Server-Sent Events.
"""

import json
import logging
import subprocess
import sys
import threading
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Any, Callable

from . import protocol

logger = logging.getLogger(__name__)


class StdioClientTransport:
    """Manages a subprocess MCP server via stdin/stdout pipes."""

    def __init__(self, command: list[str], env: dict | None = None):
        self._command = command
        self._env = env
        self._process: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._pending: dict[Any, dict] = {}  # id -> {"event": Event, "response": dict|None}
        self._lock = threading.Lock()
        self._next_id = 1
        self._running = False

    def start(self):
        """Launch the subprocess and start the reader thread."""
        import os
        env = os.environ.copy()

        # FreeCAD's AppImage sets PYTHONHOME/PYTHONPATH to its bundled
        # Python, which breaks any subprocess that uses a different Python.
        # Strip these so the subprocess inherits a clean environment.
        for key in ("PYTHONHOME", "PYTHONPATH"):
            env.pop(key, None)

        # Restore a sane PATH — the AppImage prepends its own bin dirs.
        # Keep system paths so npx/node/python3 are findable.
        path = env.get("PATH", "")
        clean_parts = [p for p in path.split(os.pathsep)
                       if ".mount_FreeCA" not in p]
        if clean_parts:
            env["PATH"] = os.pathsep.join(clean_parts)

        if self._env:
            env.update(self._env)

        self._process = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self._running = True
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def send_request(self, method: str, params: dict | None = None,
                     timeout: float = 30) -> dict:
        """Send a JSON-RPC request and wait for the matching response."""
        with self._lock:
            req_id = self._next_id
            self._next_id += 1

        event = threading.Event()
        with self._lock:
            self._pending[req_id] = {"event": event, "response": None}

        msg = protocol.make_request(method, params, id=req_id)
        self._write(msg)

        if not event.wait(timeout):
            with self._lock:
                self._pending.pop(req_id, None)
            raise TimeoutError(f"MCP request '{method}' timed out after {timeout}s")

        with self._lock:
            entry = self._pending.pop(req_id)
        return entry["response"]

    def send_notification(self, method: str, params: dict | None = None):
        """Send a JSON-RPC notification (fire-and-forget)."""
        msg = protocol.make_notification(method, params)
        self._write(msg)

    def _write(self, msg: dict):
        """Write a JSON-RPC message to the subprocess stdin."""
        if self._process and self._process.stdin:
            data = protocol.encode(msg)
            self._process.stdin.write(data)
            self._process.stdin.flush()

    def _read_loop(self):
        """Background thread: read stdout line-by-line, match responses."""
        while self._running and self._process and self._process.stdout:
            try:
                line = self._process.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8").strip()
                if not text:
                    continue
                msg = protocol.decode(text)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            except Exception:
                break

            # Match response to pending request by id
            msg_id = msg.get("id")
            if msg_id is not None:
                with self._lock:
                    entry = self._pending.get(msg_id)
                    if entry:
                        entry["response"] = msg
                        entry["event"].set()

        self._running = False

    def stop(self):
        """Terminate the subprocess."""
        self._running = False
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

        # Unblock any pending requests
        with self._lock:
            for entry in self._pending.values():
                entry["response"] = protocol.make_error(
                    None, protocol.INTERNAL_ERROR, "Transport stopped"
                )
                entry["event"].set()
            self._pending.clear()

    @property
    def is_alive(self) -> bool:
        return self._running and self._process is not None and self._process.poll() is None


class StdioServerTransport:
    """Server-side transport: reads JSON-RPC from stdin, writes to stdout."""

    def run(self, handler: Callable[[dict], dict | None]):
        """Blocking loop: read requests from stdin, dispatch to handler, write responses."""
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                text = line.strip()
                if not text:
                    continue
                msg = protocol.decode(text)
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._write(protocol.make_error(
                    None, protocol.PARSE_ERROR, "Parse error"
                ))
                continue
            except Exception:
                break

            try:
                response = handler(msg)
            except Exception as e:
                msg_id = msg.get("id")
                if msg_id is not None:
                    response = protocol.make_error(
                        msg_id, protocol.INTERNAL_ERROR, str(e)
                    )
                else:
                    response = None

            if response is not None:
                self._write(response)

    def _write(self, msg: dict):
        """Write a JSON-RPC message to stdout."""
        data = json.dumps(msg, separators=(",", ":")) + "\n"
        sys.stdout.write(data)
        sys.stdout.flush()


class SSEServerTransport:
    """Server-side transport: serves MCP over HTTP + Server-Sent Events.

    Endpoints:
        GET  /sse       — SSE event stream (client subscribes here)
        POST /messages  — JSON-RPC requests (responses arrive via SSE)

    Designed for a single connected client at a time (typical for a
    desktop-app MCP server like FreeCAD).
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 3000):
        self._host = host
        self._port = port
        self._handler: Callable[[dict], dict | None] | None = None
        self._sse_wfile = None
        self._sse_lock = threading.Lock()

    def run(self, handler: Callable[[dict], dict | None]):
        """Start the HTTP server (blocking)."""
        self._handler = handler
        transport = self

        class RequestHandler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                logger.debug(fmt, *args)

            def _base_path(self):
                return self.path.split("?")[0].rstrip("/")

            def do_GET(self):
                if self._base_path() == "/sse":
                    self._handle_sse()
                else:
                    self.send_error(404)

            def do_POST(self):
                if self._base_path() == "/messages":
                    self._handle_messages()
                else:
                    self.send_error(404)

            def _handle_sse(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()

                session_id = uuid.uuid4().hex
                with transport._sse_lock:
                    transport._sse_wfile = self.wfile

                try:
                    endpoint_data = f"/messages?sessionId={session_id}"
                    self.wfile.write(
                        f"event: endpoint\ndata: {endpoint_data}\n\n".encode()
                    )
                    self.wfile.flush()

                    while True:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        time.sleep(15)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with transport._sse_lock:
                        if transport._sse_wfile is self.wfile:
                            transport._sse_wfile = None

            def _handle_messages(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")

                try:
                    msg = json.loads(body)
                except json.JSONDecodeError:
                    err = protocol.make_error(
                        None, protocol.PARSE_ERROR, "Parse error"
                    )
                    self._send_json(400, err)
                    return

                try:
                    response = transport._handler(msg) if transport._handler else None
                except Exception as e:
                    msg_id = msg.get("id")
                    response = protocol.make_error(
                        msg_id, protocol.INTERNAL_ERROR, str(e)
                    ) if msg_id is not None else None

                self.send_response(202)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"accepted":true}')
                self.wfile.flush()

                if response is not None:
                    transport._send_sse(response)

            def _send_json(self, code: int, msg: dict):
                data = json.dumps(msg, separators=(",", ":")).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

        class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
            daemon_threads = True

        server = ThreadedHTTPServer((self._host, self._port), RequestHandler)
        logger.info("MCP SSE server listening on http://%s:%d", self._host, self._port)
        server.serve_forever()

    def _send_sse(self, msg: dict):
        """Send a JSON-RPC message to the connected SSE client."""
        data = json.dumps(msg, separators=(",", ":"))
        payload = f"event: message\ndata: {data}\n\n".encode()
        with self._sse_lock:
            wfile = self._sse_wfile
        if wfile is None:
            return
        try:
            wfile.write(payload)
            wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            with self._sse_lock:
                self._sse_wfile = None
