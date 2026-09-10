from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PLUGIN_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from soundcloud_recommender.http import HttpResponse  # noqa: E402


class FakeClock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class MockTransport:
    """Small deterministic transport; a missing route is a test failure."""

    def __init__(self, responder: Callable[..., HttpResponse] | None = None) -> None:
        self.responder = responder
        self.calls: list[dict[str, Any]] = []
        self.routes: dict[tuple[str, str], list[HttpResponse]] = {}

    def add(self, method: str, url: str, response: HttpResponse) -> None:
        self.routes.setdefault((method.upper(), url), []).append(response)

    def request(self, method: str, url: str, *, headers=None, body=None, timeout=20.0) -> HttpResponse:
        call = {
            "method": method.upper(),
            "url": url,
            "headers": dict(headers or {}),
            "body": body or b"",
            "timeout": timeout,
        }
        self.calls.append(call)
        if self.responder is not None:
            return self.responder(**call)
        queue = self.routes.get((method.upper(), url))
        if not queue:
            raise AssertionError(f"unexpected mocked request: {method} {url}")
        return queue.pop(0)


def json_response(payload: object, *, status: int = 200, headers: dict[str, str] | None = None, url: str = "") -> HttpResponse:
    return HttpResponse(
        status=status,
        headers={"content-type": "application/json", **(headers or {})},
        body=json.dumps(payload).encode("utf-8"),
        url=url,
    )


def item_payload(item_id: int | str, *, title: str = "Example track", kind: str = "track", creator: str = "Creator") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": item_id,
        "title": title,
        "user": {"id": 42, "username": creator, "permalink_url": "https://soundcloud.com/creator"},
        "permalink_url": f"https://soundcloud.com/creator/{str(item_id)}",
        "description": "mock description",
        "genre": "ambient",
    }
    if kind == "playlist":
        payload["track_count"] = 1
    return payload


def collection_payload(items: list[dict[str, Any]], next_href: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"collection": items}
    if next_href:
        payload["next_href"] = next_href
    return payload
