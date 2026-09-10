from __future__ import annotations

import socket
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from soundcloud_recommender.config import Config
from soundcloud_recommender.models import ProviderItem
from soundcloud_recommender.service import ListeningService
from soundcloud_recommender.viewer import LoopbackViewer


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_viewer_is_loopback_only_and_escapes_provider_html(tmp_path) -> None:
    config = Config(preferences_db=tmp_path / "preferences.sqlite3", viewer_port=free_port())
    service = ListeningService(config, demo_mode=True)
    viewer = LoopbackViewer(service)
    viewer.start()
    try:
        result = service._expose(
            "test",
            [ProviderItem(kind="track", item_id="html", title="<script>alert(1)</script>", creator="Creator")],
        )
        try:
            with urlopen(Request(result["viewer_url"]), timeout=2) as response:
                body = response.read().decode("utf-8")
                assert response.status == 200
                assert response.headers["Content-Security-Policy"]
                assert response.headers["X-Content-Type-Options"] == "nosniff"
                assert response.headers["Referrer-Policy"] == "no-referrer"
                assert response.headers["Cache-Control"] == "no-store"
                assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
                assert "<script>alert(1)</script>" not in body
        except HTTPError as exc:  # pragma: no cover - makes an auth failure readable
            raise AssertionError(f"authenticated viewer request failed: {exc.code}") from exc

        unauthorized = result["viewer_url"].split("?", 1)[0]
        try:
            urlopen(unauthorized, timeout=2)
        except HTTPError as exc:
            assert exc.code == 401
        else:  # pragma: no cover - defensive assertion
            raise AssertionError("viewer route accepted a request without capability")
    finally:
        viewer.stop()
