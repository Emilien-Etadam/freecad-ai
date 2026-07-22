import ssl

import pytest

from freecad_ai.mcp.client import (
    make_client_transport, _validate_url, _build_ssl_context, MCPClient,
)
from freecad_ai.mcp.transport import (
    StdioClientTransport, SSEClientTransport, StreamableHTTPClientTransport,
)


class TestValidateUrl:
    def test_https_ok(self):
        _validate_url("https://example.com/sse")  # no raise

    def test_http_loopback_ok(self):
        _validate_url("http://localhost:3000/sse")
        _validate_url("http://127.0.0.1:3000/sse")

    def test_http_remote_rejected(self):
        with pytest.raises(ValueError):
            _validate_url("http://example.com/sse")

    def test_non_http_scheme_rejected(self):
        with pytest.raises(ValueError):
            _validate_url("ftp://example.com/x")


class TestBuildSSLContext:
    def test_none_when_no_tls_fields(self):
        assert _build_ssl_context({"url": "https://x/"}) is None

    def test_context_when_ca_bundle_set(self, tmp_path):
        # A CA file that doesn't parse still exercises the "build a context"
        # branch via the raised error; here we assert a context is attempted.
        ca = tmp_path / "ca.pem"
        ca.write_text("not a real cert")
        with pytest.raises(ssl.SSLError):
            _build_ssl_context({"ca_bundle": str(ca)})

    def test_client_cert_bad_path_raises(self):
        with pytest.raises(Exception):
            _build_ssl_context({"client_cert": "/nonexistent/client.pem"})


class TestMakeClientTransport:
    def test_absent_transport_is_stdio(self):
        t = make_client_transport({"command": "echo", "args": ["hi"]})
        assert isinstance(t, StdioClientTransport)

    def test_explicit_stdio(self):
        t = make_client_transport({"transport": "stdio", "command": "echo"})
        assert isinstance(t, StdioClientTransport)

    def test_sse_transport_with_headers(self):
        t = make_client_transport({
            "transport": "sse", "url": "https://h/sse",
            "headers": {"Authorization": "Bearer x"}})
        assert isinstance(t, SSEClientTransport)
        assert t._headers["Authorization"] == "Bearer x"

    def test_http_transport(self):
        t = make_client_transport({"transport": "http", "url": "https://h/mcp"})
        assert isinstance(t, StreamableHTTPClientTransport)

    def test_unknown_transport_raises(self):
        with pytest.raises(ValueError):
            make_client_transport({"transport": "carrier-pigeon", "url": "https://h/"})

    def test_remote_http_url_rejected(self):
        with pytest.raises(ValueError):
            make_client_transport({"transport": "sse", "url": "http://example.com/sse"})


class TestMCPClientInjection:
    def test_injected_transport_used(self):
        sentinel = SSEClientTransport("https://h/sse")
        client = MCPClient("srv", transport=sentinel)
        assert client._transport is sentinel

    def test_backward_compatible_stdio_construction(self):
        client = MCPClient("srv", ["echo", "hi"], {"A": "B"})
        assert isinstance(client._transport, StdioClientTransport)
