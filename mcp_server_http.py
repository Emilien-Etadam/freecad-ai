#!/usr/bin/env python3
"""MCP server entry point for FreeCAD AI (HTTP/SSE mode).

Starts FreeCAD with GUI and exposes all built-in tools via the MCP
protocol over HTTP + Server-Sent Events, so you can watch FreeCAD
update in real-time while an AI client calls tools.

Usage:
    # Start FreeCAD with this script as an argument:
    /path/to/FreeCAD.AppImage /path/to/freecad-ai/mcp_server_http.py

    # Or from inside a running FreeCAD via macro / exec:
    exec(open("/path/to/freecad-ai/mcp_server_http.py").read())

MCP configuration:
{
    "freecad": {
      "type": "remote",
      "url": "http://127.0.0.1:3000/sse"
    }
}

Environment variables:
    MCP_HOST  — listen address  (default: 127.0.0.1)
    MCP_PORT  — listen port     (default: 3000)
"""

import os
import sys
import logging
import threading

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

import FreeCAD

if not FreeCAD.ActiveDocument:
    FreeCAD.newDocument("Unnamed")

from freecad_ai.tools.setup import create_default_registry
from freecad_ai.tools.executor_utils import QtMainThreadToolExecutor
from freecad_ai.mcp.server import MCPServer
from freecad_ai.mcp.transport import SSEServerTransport

registry = create_default_registry(include_mcp=False)

executor = QtMainThreadToolExecutor()
executor.set_registry(registry)

host = os.environ.get("MCP_HOST", "127.0.0.1")
port = int(os.environ.get("MCP_PORT", "3000"))

transport = SSEServerTransport(host=host, port=port)
server = MCPServer(registry, transport=transport, executor=executor)

server_thread = threading.Thread(target=server.run, daemon=True)
server_thread.start()

print(f"MCP SSE server running on http://{host}:{port}/sse", flush=True)
