"""Command-line entry point for the local MCP/viewer service."""

from __future__ import annotations

import argparse
import os
import sys

from .config import Config
from .mcp_stdio import MCPServer, run_stdio
from .service import ListeningService
from .viewer import LoopbackViewer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Listening Companion local MCP stdio server")
    parser.add_argument("--demo", action="store_true", help="use synthetic sources and never contact SoundCloud")
    parser.add_argument("--no-viewer", action="store_true", help="do not start the loopback viewer")
    parser.add_argument("--viewer-host", help="loopback host override")
    parser.add_argument("--viewer-port", type=int, help="loopback port override")
    parser.add_argument(
        "--framing",
        choices=("ndjson", "content-length"),
        default=os.environ.get("LISTENING_COMPANION_STDIO_FRAMING", "ndjson"),
        help="stdio framing; NDJSON is the default",
    )
    args = parser.parse_args(argv)
    env = dict(os.environ)
    if args.viewer_host:
        env["LISTENING_COMPANION_VIEWER_HOST"] = args.viewer_host
    if args.viewer_port:
        env["LISTENING_COMPANION_VIEWER_PORT"] = str(args.viewer_port)
    config = Config.from_env(env)
    service = ListeningService(config, demo_mode=args.demo)
    viewer = None
    if not args.no_viewer:
        viewer = LoopbackViewer(service)
        viewer.start()
    try:
        run_stdio(MCPServer(service), framing=args.framing)
    finally:
        if viewer is not None:
            viewer.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
