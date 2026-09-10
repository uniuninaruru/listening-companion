"""Direct MCP JSON-RPC stdio server, with NDJSON as the default framing."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from typing import Any

from .errors import ListeningCompanionError, ValidationError
from .service import ListeningService


PROTOCOL_VERSION = "2024-11-05"
SUPPORTED_PROTOCOL_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18"}
MAX_MESSAGE_BYTES = 1024 * 1024


def _object_schema(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


_PAGING = {
    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
    "cursor": {"type": "string", "minLength": 16, "maxLength": 200},
}
_PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "genres": {"type": "array", "items": {"type": "string", "maxLength": 80}, "maxItems": 12},
        "podcast_topics": {"type": "array", "items": {"type": "string", "maxLength": 80}, "maxItems": 12},
        "languages": {"type": "array", "items": {"type": "string", "maxLength": 80}, "maxItems": 8},
        "preferred_creators": {"type": "array", "items": {"type": "string", "maxLength": 80}, "maxItems": 12},
        "avoid_terms": {"type": "array", "items": {"type": "string", "maxLength": 80}, "maxItems": 20},
        "preferred_duration_minutes": {"type": "integer", "minimum": 1, "maximum": 720},
        "discovery_level": {"type": "string", "enum": ["low", "balanced", "high"]},
    },
    "additionalProperties": False,
}


def _tool(
    name: str,
    description: str,
    schema: dict[str, Any],
    *,
    read_only: bool = True,
    destructive: bool = False,
    idempotent: bool = True,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": schema,
        "annotations": {
            "readOnlyHint": read_only,
            "destructiveHint": destructive,
            "idempotentHint": idempotent,
            "openWorldHint": False,
        },
    }


TOOL_DEFINITIONS = [
    _tool("connection_status", "Return local connection state without provider metadata or tokens.", _object_schema()),
    _tool("connect_account", "Create a short-lived local browser authorization receipt; never returns a token.", _object_schema(), read_only=False, idempotent=False),
    _tool("disconnect_account", "Clear local tokens and all in-memory provider/result/cursor state.", _object_schema(), read_only=False, destructive=True),
    _tool("get_profile", "Fetch the authenticated profile into an expiring local viewer receipt.", _object_schema()),
    _tool("recent_plays", "Fetch only the current API's last-25 recently played tracks.", _object_schema()),
    _tool("liked_tracks", "Fetch liked tracks into an expiring local viewer receipt.", _object_schema(_PAGING)),
    _tool("liked_playlists", "Fetch liked playlists into an expiring local viewer receipt.", _object_schema(_PAGING)),
    _tool("my_playlists", "Fetch the user's playlists into an expiring local viewer receipt.", _object_schema(_PAGING)),
    _tool("followings", "Fetch followed users into an expiring local viewer receipt.", _object_schema(_PAGING)),
    _tool(
        "following_tracks",
        "Fetch followed-user tracks with limit and offset; this endpoint does not use linked_partitioning.",
        _object_schema({"limit": _PAGING["limit"], "offset": {"type": "integer", "minimum": 0, "maximum": 10000}}),
    ),
    _tool("search_tracks", "Search public tracks; live metadata stays in the local viewer.", _object_schema({**{"query": {"type": "string", "minLength": 1, "maxLength": 120}}, **_PAGING}, ["query"])),
    _tool("search_playlists", "Search public playlists; live metadata stays in the local viewer.", _object_schema({**{"query": {"type": "string", "minLength": 1, "maxLength": 120}}, **_PAGING}, ["query"])),
    _tool("search_users", "Search public users; live metadata stays in the local viewer.", _object_schema({**{"query": {"type": "string", "minLength": 1, "maxLength": 120}}, **_PAGING}, ["query"])),
    _tool("resolve_resource", "Resolve an HTTPS SoundCloud permalink through an allowlisted API redirect.", _object_schema({"url": {"type": "string", "format": "uri", "maxLength": 2000}}, ["url"])),
    _tool("get_track", "Fetch a track by numeric ID or canonical soundcloud:tracks:... reference.", _object_schema({"reference": {"type": "string", "minLength": 1, "maxLength": 180}}, ["reference"])),
    _tool("get_playlist", "Fetch a playlist by numeric ID or canonical soundcloud:playlists:... reference.", _object_schema({"reference": {"type": "string", "minLength": 1, "maxLength": 180}}, ["reference"])),
    _tool("playlist_tracks", "Fetch playlist tracks with bounded pagination.", _object_schema({"reference": {"type": "string", "minLength": 1, "maxLength": 180}, **_PAGING}, ["reference"])),
    _tool("related_tracks", "Fetch related tracks using /tracks/{soundcloud:tracks:...}/related.", _object_schema({"reference": {"type": "string", "minLength": 1, "maxLength": 180}, **_PAGING}, ["reference"])),
    _tool(
        "classify_podcasts",
        "Classify a prior local result, or classify explicitly synthetic fixture items.",
        {
            "type": "object",
            "properties": {
                "result_id": {"type": "string", "minLength": 16, "maxLength": 200},
                "items": {"type": "array", "maxItems": 100, "items": {"type": "object"}},
                "synthetic": {"type": "boolean"},
            },
            "additionalProperties": False,
            "anyOf": [{"required": ["result_id"]}, {"required": ["items", "synthetic"]}],
        },
    ),
    _tool("recommend", "Combine local history/likes/following/search signals into new deterministic recommendations.", _object_schema({"preference_profile": _PROFILE_SCHEMA, "limit": {"type": "integer", "minimum": 1, "maximum": 20}, "content_type": {"type": "string", "enum": ["music", "podcast", "mixed"]}})),
    _tool("get_preferences", "Read the explicitly saved minimal preference_profile only.", _object_schema()),
    _tool("save_preferences", "Save a minimal preference_profile only after explicit consent=true.", _object_schema({"preference_profile": _PROFILE_SCHEMA, "consent": {"const": True}}, ["preference_profile", "consent"]), read_only=False),
    _tool("delete_preferences", "Delete the saved preference_profile.", _object_schema(), read_only=False, destructive=True),
    _tool("demo_catalog", "Return synthetic fixture metadata for local testing; it never contacts SoundCloud.", _object_schema({"content_type": {"type": "string", "enum": ["music", "podcast", "mixed"]}})),
]


_TOOL_NAMES = {entry["name"] for entry in TOOL_DEFINITIONS}


class MCPServer:
    def __init__(self, service: ListeningService | None = None) -> None:
        self.service = service or ListeningService()

    @staticmethod
    def _response(request_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "message": message}
        if data:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}

    def handle(self, request: Any) -> dict[str, Any] | None:
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or "method" not in request:
            return self._error(request.get("id") if isinstance(request, dict) else None, -32600, "Invalid Request")
        request_id = request.get("id")
        method = request.get("method")
        if not isinstance(method, str):
            return self._error(request_id, -32600, "Invalid Request")
        params = request.get("params") or {}
        if method.startswith("notifications/"):
            return None
        if method == "initialize":
            if not isinstance(params, dict):
                return self._error(request_id, -32602, "Invalid params")
            return self._response(
                request_id,
                {
                    "protocolVersion": params.get("protocolVersion") if params.get("protocolVersion") in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "listening-companion", "title": "Listening Companion", "version": "0.1.0"},
                },
            )
        if method == "ping":
            return self._response(request_id, {})
        if method == "tools/list":
            return self._response(request_id, {"tools": TOOL_DEFINITIONS})
        if method == "tools/call":
            if not isinstance(params, dict) or not isinstance(params.get("name"), str) or params["name"] not in _TOOL_NAMES:
                return self._error(request_id, -32602, "Unknown or missing tool name")
            arguments = params.get("arguments", {})
            if not isinstance(arguments, dict):
                return self._error(request_id, -32602, "Tool arguments must be an object")
            try:
                result = self.service.call(params["name"], arguments)
            except ListeningCompanionError as exc:
                # Keep expected operational failures inside the tool result and
                # expose only a stable app-owned error code/message.
                result = {
                    "content": [{"type": "text", "text": json.dumps({"status": "error", "code": getattr(exc, "code", "operation_failed"), "message": str(exc)}, ensure_ascii=False)}],
                    "isError": True,
                }
                return self._response(request_id, result)
            except Exception:
                result = {
                    "content": [{"type": "text", "text": json.dumps({"status": "error", "code": "operation_failed", "message": "operation failed"})}],
                    "isError": True,
                }
                return self._response(request_id, result)
            text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
            return self._response(
                request_id,
                {"content": [{"type": "text", "text": text}], "structuredContent": result, "isError": False},
            )
        return self._error(request_id, -32601, "Method not found")


def iter_messages(stream: Any) -> Iterable[Any]:
    """Accept NDJSON and Content-Length input without making the latter default."""
    while True:
        first = stream.readline()
        if not first:
            return
        if isinstance(first, str):
            first_bytes = first.encode("utf-8")
        else:
            first_bytes = first
        if not first_bytes.strip():
            continue
        if len(first_bytes) > MAX_MESSAGE_BYTES:
            raise ValueError("NDJSON message exceeds the 1 MiB limit")
        if first_bytes.lower().startswith(b"content-length:"):
            headers = first_bytes.decode("ascii", errors="ignore").strip().split(":", 1)
            try:
                length = int(headers[1].strip())
            except (IndexError, ValueError) as exc:
                raise ValueError("invalid Content-Length") from exc
            if length < 0 or length > MAX_MESSAGE_BYTES:
                raise ValueError("Content-Length is outside the 0..1 MiB limit")
            while True:
                line = stream.readline()
                if not line or (line.strip() if isinstance(line, bytes) else line.strip().encode()) == b"":
                    break
            body = stream.read(length)
            if isinstance(body, str):
                body = body.encode("utf-8")
            if len(body) != length:
                raise ValueError("Content-Length body was truncated")
            yield json.loads(body.decode("utf-8"))
            continue
        yield json.loads(first_bytes.decode("utf-8"))


def write_message(stream: Any, message: dict[str, Any], *, framing: str = "ndjson") -> None:
    import io

    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    binary = isinstance(stream, (io.BufferedIOBase, io.RawIOBase, io.BytesIO))
    if framing == "content-length":
        header = f"Content-Length: {len(payload)}\r\n\r\n"
        stream.write(header.encode("ascii") if binary else header)
        stream.write(payload if binary else payload.decode("utf-8"))
    else:
        stream.write(payload + b"\n" if binary else payload.decode("utf-8") + "\n")
    stream.flush()


def run_stdio(server: MCPServer | None = None, *, input_stream=None, output_stream=None, framing: str = "ndjson") -> None:  # type: ignore[no-untyped-def]
    server = server or MCPServer()
    input_stream = input_stream or sys.stdin.buffer
    output_stream = output_stream or sys.stdout.buffer
    iterator = iter_messages(input_stream)
    while True:
        try:
            request = next(iterator)
        except StopIteration:
            return
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            write_message(
                output_stream,
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
                framing=framing,
            )
            return
        response = server.handle(request)
        if response is not None:
            write_message(output_stream, response, framing=framing)
