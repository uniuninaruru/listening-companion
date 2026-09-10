from __future__ import annotations

import io
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from soundcloud_recommender.config import Config
from soundcloud_recommender.mcp_stdio import (
    MAX_MESSAGE_BYTES,
    MCPServer,
    PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    iter_messages,
    run_stdio,
    write_message,
)
from soundcloud_recommender.models import ProviderItem
from soundcloud_recommender.oauth import OAuthToken, SessionTokenStore
from soundcloud_recommender.service import ListeningService

from .conftest import MockTransport, collection_payload, item_payload, json_response


def test_initialize_falls_back_to_supported_protocol_and_tools_are_annotated(tmp_path) -> None:
    service = ListeningService(Config(preferences_db=tmp_path / "prefs.sqlite3"), demo_mode=True)
    server = MCPServer(service)

    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "unknown-version"},
        }
    )
    assert response["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert PROTOCOL_VERSION in SUPPORTED_PROTOCOL_VERSIONS

    listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tools = {tool["name"]: tool for tool in listed["result"]["tools"]}
    assert len(tools) == 24
    assert tools["connection_status"]["annotations"] == {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    assert tools["connect_account"]["annotations"]["readOnlyHint"] is False
    assert tools["disconnect_account"]["annotations"]["destructiveHint"] is True
    assert tools["save_preferences"]["annotations"]["readOnlyHint"] is False
    assert tools["delete_preferences"]["annotations"]["destructiveHint"] is True


def test_tools_call_connection_status_and_demo_recommendation(tmp_path) -> None:
    service = ListeningService(Config(preferences_db=tmp_path / "prefs.sqlite3"), demo_mode=True)
    server = MCPServer(service)

    status = server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "connection_status", "arguments": {}}}
    )
    assert status["result"]["structuredContent"]["status"] == "configuration_required"

    recommendation = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "recommend", "arguments": {"limit": 2, "content_type": "mixed"}},
        }
    )
    public = recommendation["result"]["structuredContent"]
    assert public["status"] == "ok"
    assert public["count"] == 2
    assert set(public) == {"status", "result_id", "count", "viewer_url", "expires_in_seconds", "warnings"}


def test_live_mcp_result_contains_no_provider_metadata(tmp_path) -> None:
    transport = MockTransport()
    transport.add(
        "GET",
        "https://api.soundcloud.com/me/recently-played/tracks",
        json_response(collection_payload([item_payload(7, title="Secret live title", creator="Private artist")])),
    )
    store = SessionTokenStore()
    store.save(OAuthToken(access_token="access", refresh_token="refresh", expires_at=10_000))
    config = Config(preferences_db=tmp_path / "prefs.sqlite3")
    service = ListeningService(config, transport=transport, token_store=store, clock=lambda: 1_000)

    public = service.recent_plays({})
    serialized = json.dumps(public, ensure_ascii=False)
    assert "Secret live title" not in serialized
    assert "Private artist" not in serialized
    assert "soundcloud.com/creator/7" not in serialized
    assert public["result_id"]
    assert public["viewer_url"].startswith("http://127.0.0.1:8765/view/")

    result_id = public["result_id"]
    capability = parse_qs(urlsplit(public["viewer_url"]).query)["token"][0]
    _operation, rows, _warnings = service.render_cached_result(result_id, capability)
    assert rows[0]["title"] == "Secret live title"


def test_invalid_request_method_type_returns_jsonrpc_error() -> None:
    server = MCPServer(ListeningService(demo_mode=True))
    response = server.handle({"jsonrpc": "2.0", "id": "bad", "method": 17})
    assert response["error"]["code"] == -32600


def test_tool_arguments_reject_unknown_fields() -> None:
    server = MCPServer(ListeningService(demo_mode=True))
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "connection_status", "arguments": {"unexpected": True}},
        }
    )
    assert response["result"]["isError"] is True
    error = json.loads(response["result"]["content"][0]["text"])
    assert error["code"] == "invalid_input"


def test_malformed_json_is_parse_error_without_process_exception() -> None:
    output = io.BytesIO()
    run_stdio(MCPServer(ListeningService(demo_mode=True)), input_stream=io.BytesIO(b"not-json\n"), output_stream=output)
    response = json.loads(output.getvalue().decode("utf-8"))
    assert response["id"] is None
    assert response["error"]["code"] == -32700


def test_stdio_size_limits_and_default_ndjson_framing() -> None:
    with pytest.raises(ValueError, match="outside"):
        list(iter_messages(io.BytesIO(b"Content-Length: -1\r\n\r\n")))
    with pytest.raises(ValueError, match="outside"):
        list(iter_messages(io.BytesIO(f"Content-Length: {MAX_MESSAGE_BYTES + 1}\r\n\r\n".encode())))
    with pytest.raises(ValueError, match="exceeds"):
        list(iter_messages(io.BytesIO(b"{" + b"a" * MAX_MESSAGE_BYTES + b"}\n")))

    payload = {"jsonrpc": "2.0", "id": 1, "method": "ping", "data": "x" * (MAX_MESSAGE_BYTES - 100)}
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    assert len(encoded) <= MAX_MESSAGE_BYTES
    assert list(iter_messages(io.BytesIO(encoded + b"\n")))[0]["method"] == "ping"

    output = io.BytesIO()
    write_message(output, {"jsonrpc": "2.0", "id": 1, "result": {} })
    assert not output.getvalue().startswith(b"Content-Length:")
    assert json.loads(output.getvalue()) ["result"] == {}
