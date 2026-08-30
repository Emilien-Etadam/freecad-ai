# Streamable HTTP Server Transport (#65) — Design

**Issue:** [#65 — Server: add the Streamable HTTP transport (HTTP+SSE is deprecated with a removal window)](https://github.com/ghbalf/freecad-ai/issues/65)
**Date:** 2026-08-30
**Status:** Approved (design)

## Goal

Serve MCP over the **Streamable HTTP** transport in addition to the HTTP+SSE
transport we ship today, on the same listener and the same port, so a client
gets a working connection whichever of the two it speaks.

HTTP+SSE was superseded by Streamable HTTP in `2025-03-26` and formally
**deprecated** in `2026-07-28` under a minimum twelve-month removal window. This
is phase 1 of #64, split out because it has a clock on it and is worth doing
regardless of whether we adopt the newer protocol revision.

Our **client** has spoken Streamable HTTP since #41. Only the server is behind.

## Approved decisions

| Decision | Choice |
|----------|--------|
| Exposure | **One listener serves both** — `/mcp` alongside `/sse` + `/messages`. Users never choose a transport |
| Class | `SSEServerTransport` renamed **`HTTPServerTransport`**, with `SSEServerTransport` kept as a module-level alias |
| Response mode | **`application/json` inline only** — no SSE branch on `POST /mcp` |
| Sessions | **None.** No `Mcp-Session-Id` is ever issued |
| Resumability | **None.** No SSE event ids, no `Last-Event-ID` |
| `GET /mcp` | **`405 Method Not Allowed`** — explicitly permitted for a server offering no server-to-client stream |
| `MCP-Protocol-Version` | Absent → assume `2025-03-26`. `2025-03-26` / `2025-06-18` / `2025-11-25` accepted; anything else → `400` |
| Advertised URL | `ServerController.start()` returns `.../mcp`; the console line names both endpoints |
| Configuration | **No new setting and no new env var.** Host, port and `MCP_ALLOWED_HOSTS` carry over unchanged |
| Dependencies | **stdlib only**, consistent with the rest of `freecad_ai/mcp/` |

## Why skipping sessions, resumability and the GET stream is the forward-compatible choice

Those three are precisely what `2026-07-28` removes: protocol-level sessions and
`Mcp-Session-Id` are gone, the GET endpoint is replaced by
`subscriptions/listen`, and SSE resumability with `Last-Event-ID` is removed
outright. A POST-only, sessionless, non-resumable Streamable HTTP server is
conformant to the legacy revisions **and** closer to the modern shape than what
we ship today. Implementing them would be work we would later have to undo.

They are also unnecessary for us: every method we serve — `initialize`,
`tools/list`, `tools/call`, `ping` — is strict request/response. We originate no
server-initiated messages, so there is nothing for a server→client stream to
carry and no cross-request state for a session id to key.

## Architecture

`SSEServerTransport` already owns everything the new transport needs: the split
`bind()` / `serve()` / `stop()` lifecycle from #61, the `Host` and `Origin` gate
in `_request_allowed()`, and the threaded `HTTPServer` built by `_make_server()`.
Streamable HTTP adds no lifecycle, no new socket and no new configuration — only
a third route inside the same `RequestHandler`.

So it becomes routes on the existing class rather than a second class. A separate
`StreamableHTTPServerTransport` would duplicate the lifecycle verbatim and force
users to pick a transport before starting the server, with a wrong guess showing
up as a connection failure that names no cause.

The class is renamed to `HTTPServerTransport` because it is no longer SSE-only
and a reader looking for `/mcp` should find it under a name that admits it
exists. `SSEServerTransport = HTTPServerTransport` stays as a module-level alias:
`mcp_server_entry.py`, the wiki, existing tests and any user script keep working
untouched.

### Request routing

| Request | Response |
|---------|----------|
| `POST /mcp`, message has an `id` (a request) | `200`, `Content-Type: application/json`, the JSON-RPC response as the body |
| `POST /mcp`, message has no `id` (notification or response) | `202 Accepted`, empty body |
| `POST /mcp`, body is not valid JSON | `400`, JSON-RPC `-32700` Parse error |
| `POST /mcp`, body is valid JSON but not an object (a batch array, a bare scalar) | `400`, JSON-RPC `-32600` Invalid Request |
| `POST /mcp`, unsupported `MCP-Protocol-Version` | `400`, body naming the supported revisions |
| `GET /mcp` | `405`, `Allow: POST` |
| `DELETE /mcp` | `405`, `Allow: POST` — no session exists to terminate |
| `GET /sse` | unchanged — SSE stream, `endpoint` event, keepalives |
| `POST /messages` | unchanged — `202` then the reply pushed over SSE |
| anything else | `404` |

`Host` and `Origin` are checked before any of this, by the existing
`_authorized()` helper that already fronts `do_GET` and `do_POST`, and which the
new `do_DELETE` calls the same way. The
DNS-rebinding and CSRF guards, and the `MCP_ALLOWED_HOSTS` allowlist merged for
#60, therefore cover `/mcp` with no new code.

### Where the version set lives

`_SUPPORTED_PROTOCOL_VERSIONS = frozenset({"2025-03-26", "2025-06-18",
"2025-11-25"})` goes in `protocol.py`, not `server.py`.

`server.py` imports `transport.py` (`from .transport import
StdioServerTransport`), so `transport.py` importing `server.py` back would be an
import cycle. `protocol.py` is the shared leaf both already depend on, and the
constant is protocol knowledge rather than server behaviour.

The three revisions are accepted because our entire wire surface is unchanged
across them: `2025-06-18` and `2025-11-25` add optional fields (structured tool
output, elicitation) that we neither emit nor require. `2026-07-28` is excluded
deliberately — it is a redesign, and claiming it would be a promise we do not
keep. That is #64's job.

### Why not stricter

A literal reading of the spec would reject every value except the one revision
`initialize` advertises. That is correct on paper and hostile in practice: a
client that sends its own preferred revision rather than the negotiated one gets
a bare `400` with nothing in its UI to explain it — structurally the same trap as
#60, where a technically-correct rejection read to users as a broken server. We
keep the `MUST`-level rejection for revisions we genuinely cannot serve and drop
it for revisions where the distinction is invisible on the wire.

## Error handling

JSON-RPC errors travel in the body at HTTP `200`. A tool that raises is a
successful HTTP exchange carrying an error object — the same treatment
`_handle_messages` gives it today.

HTTP `4xx` is reserved for the three transport-level refusals: `403` (Host or
Origin rejected), `400` (unparseable body or unsupported protocol revision), and
`405` (method not allowed on `/mcp`).

Exceptions raised by the handler are caught exactly as on the `/messages` path:
turned into a JSON-RPC `-32603` when the message had an `id`, and swallowed when
it did not, since a notification has nowhere to put an error.

## What users see

`ServerController.start()` returns `http://host:port/mcp` instead of
`http://host:port/sse`. That string is what the toolbar toggle writes to the
Report view and the status bar, so it becomes the URL users copy into a client.

The console line names both endpoints, so an existing `/sse` configuration does
not look abandoned. Old configurations keep working unchanged — this alters which
URL we *advertise*, not which ones we serve.

No new `AppConfig` field, no new Settings row, no new environment variable.

## Testing

- **Round trip.** Our own `StreamableHTTPClientTransport` against
  `HTTPServerTransport` on an ephemeral loopback port, in-process — the pattern
  `tests/unit/test_mcp_sse_client.py:107` already uses for the SSE pair. The
  client sends `Accept: application/json, text/event-stream` and parses an inline
  JSON body, so the two halves exercise each other with no external MCP client.
- **Routing matrix.** Every row of the table above, driven against a really-bound
  socket rather than a mocked handler.
- **Version matrix.** Absent, each of the three accepted revisions, `2026-07-28`,
  and a garbage value.
- **Authorization carries over.** A non-loopback `Host` and a cross-origin
  `Origin` are both rejected on `/mcp`, not just on `/messages`.
- **Legacy regression.** The existing SSE suite passes unmodified, apart from the
  controller tests that assert the advertised URL.

## Out of scope

- **Client-side `MCP-Protocol-Version`.** Our client does not send the header,
  which has been a client `MUST` since `2025-06-18`. It works only because a
  server seeing no header is told to assume `2025-03-26`, which is what we speak
  — correct by coincidence. Belongs to #64.
- **Adopting a newer protocol revision.** `initialize` keeps answering
  `2025-03-26`. #64.
- **Removing HTTP+SSE.** The offramp is at minimum twelve months and the spec's
  own guidance is for servers to host both during it.
- **JSON-RPC batching.** `2025-03-26` permits batches; our `/messages` path has
  never handled them and `2025-11-25` removes them again. A batch array is
  rejected with `400` and JSON-RPC `-32600` rather than silently half-processed,
  which is also what a bare scalar body gets.
- **Bearer-token authentication.** Still #59. The `Host` allowlist remains the
  only access control, unchanged by this work.

## Risks

| Risk | Mitigation |
|------|------------|
| The advertised URL changes under existing users | `/sse` keeps working; the console line names both |
| The rename splits the name in two places | Alias is module-level and permanent; docstring states which name is current |
| Accepting three revisions over-promises | Justified per-revision above; the accepted set is a single frozenset to revisit when #64 lands |
