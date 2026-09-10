#!/usr/bin/env python3
"""Dependency-free packaging and local startup validation."""

from __future__ import annotations

import ast
import json
import pathlib
import sys
import tomllib


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    if manifest.get("name") != "soundcloud-recommender":
        raise SystemExit("manifest name mismatch")
    if manifest.get("interface", {}).get("displayName") != "Listening Companion":
        raise SystemExit("manifest display name mismatch")
    mcp = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
    entry = mcp.get("mcpServers", {}).get("listening-companion", {})
    if entry.get("command") != "python3" or not entry.get("args"):
        raise SystemExit(".mcp.json does not declare a runnable command")
    tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    for path in (ROOT / "src").rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    from soundcloud_recommender.mcp_stdio import MCPServer  # noqa: PLC0415
    from soundcloud_recommender.service import ListeningService  # noqa: PLC0415

    server = MCPServer(ListeningService(demo_mode=True))
    initialized = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    if initialized is None or listed is None or len(listed["result"]["tools"]) < 20:
        raise SystemExit("MCP startup validation failed")
    demo = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "demo_catalog", "arguments": {}}})
    if not demo or demo.get("result", {}).get("isError"):
        raise SystemExit("synthetic demo validation failed")
    print("local validation: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
