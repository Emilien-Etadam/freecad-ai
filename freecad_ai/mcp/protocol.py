"""JSON-RPC 2.0 message helpers for MCP protocol.

Provides encode/decode functions and message constructors for the
Model Context Protocol, which uses JSON-RPC 2.0 over STDIO.
"""

import json
from typing import Any

# Standard JSON-RPC 2.0 error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# The revision we advertise from initialize, and the one a client is told to
# assume when it sends no MCP-Protocol-Version header.
DEFAULT_PROTOCOL_VERSION = "2025-03-26"

# Revisions the Streamable HTTP endpoint accepts in MCP-Protocol-Version.
# Wider than what initialize advertises on purpose: our whole wire surface
# (initialize, tools/list, tools/call, ping) is identical across these three,
# because 2025-06-18 and 2025-11-25 only add optional fields we neither emit
# nor require. Rejecting a client for naming one of them would be a 400 it
# could not act on. 2026-07-28 is excluded deliberately — it is a redesign,
# and accepting it would promise behaviour we do not have (#64).
SUPPORTED_PROTOCOL_VERSIONS = frozenset({
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
})


def encode(msg: dict) -> bytes:
    """Serialize a JSON-RPC message to bytes (JSON + newline)."""
    return (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")


def decode(line: str) -> dict:
    """Parse a JSON-RPC message from a line of text."""
    return json.loads(line.strip())


def make_request(method: str, params: dict | None = None, id: Any = None) -> dict:
    """Create a JSON-RPC 2.0 request message."""
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if id is not None:
        msg["id"] = id
    return msg


def make_response(id: Any, result: Any) -> dict:
    """Create a JSON-RPC 2.0 success response."""
    return {"jsonrpc": "2.0", "id": id, "result": result}


def make_error(id: Any, code: int, message: str, data: Any = None) -> dict:
    """Create a JSON-RPC 2.0 error response."""
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": id, "error": error}


def make_notification(method: str, params: dict | None = None) -> dict:
    """Create a JSON-RPC 2.0 notification (no id, no response expected)."""
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return msg
