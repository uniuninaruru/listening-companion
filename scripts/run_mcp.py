#!/usr/bin/env python3
"""Portable plugin-root launcher; no installation or cwd assumption is required."""

from __future__ import annotations

import pathlib
import sys


PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC_ROOT = PLUGIN_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from soundcloud_recommender.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
