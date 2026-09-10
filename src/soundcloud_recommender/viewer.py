"""Loopback-only authenticated result viewer."""

from __future__ import annotations

import html
import json
import logging
import threading
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from .errors import CacheMiss, ListeningCompanionError, OAuthError


LOGGER = logging.getLogger("listening_companion.viewer")
_LINK_HOSTS = {"soundcloud.com", "www.soundcloud.com", "on.soundcloud.com", "m.soundcloud.com"}


class _ViewerHandler(BaseHTTPRequestHandler):
    server: "ViewerHTTPServer"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        # Request paths contain OAuth codes, state values, and viewer
        # capabilities. Never emit access logs, even to stderr.
        return

    def _send(self, status: int, body: bytes, content_type: str = "text/html; charset=utf-8", extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _html(self, status: int, title: str, body: str, *, extra: dict[str, str] | None = None) -> None:
        document = (
            "<!doctype html><html lang='en'><meta charset='utf-8'>"
            "<meta name='referrer' content='no-referrer'>"
            f"<title>{html.escape(title)}</title><body><main>"
            f"<h1>{html.escape(title)}</h1>{body}</main></body></html>"
        ).encode("utf-8")
        self._send(status, document, extra=extra)

    def _json(self, status: int, payload: dict[str, object]) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _query(self) -> dict[str, list[str]]:
        return parse_qs(urlsplit(self.path).query, keep_blank_values=False)

    def _token(self) -> str | None:
        query_token = self._query().get("token", [None])[0]
        if query_token:
            return query_token
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get("lc_viewer")
        return morsel.value if morsel else None

    def do_GET(self) -> None:  # noqa: N802
        if not self._valid_loopback_headers():
            self._html(HTTPStatus.BAD_REQUEST, "Invalid local request", "<p>This viewer accepts only loopback requests.</p>")
            return
        path = urlsplit(self.path).path
        if path == "/healthz":
            self._json(HTTPStatus.OK, {"status": "ok", "app": "Listening Companion"})
            return
        if path == "/setup":
            self._html(
                HTTPStatus.OK,
                "Local setup required",
                "<p>Set private SoundCloud client credentials in the local environment, then call connect_account again. Never paste a secret into chat.</p>",
            )
            return
        if path.startswith("/auth/start/"):
            self._auth_start(unquote(path.rsplit("/", 1)[-1]))
            return
        if path == "/oauth/callback":
            self._callback()
            return
        if path.startswith("/auth/result/"):
            self._auth_result(unquote(path.rsplit("/", 1)[-1]))
            return
        if path.startswith("/view/"):
            self._view(unquote(path.rsplit("/", 1)[-1]))
            return
        self._html(HTTPStatus.NOT_FOUND, "Not found", "<p>This local route does not exist.</p>")

    def _valid_loopback_headers(self) -> bool:
        host_header = self.headers.get("Host", "")
        host = host_header.rsplit(":", 1)[0] if host_header.count(":") == 1 else host_header.strip("[]")
        if host not in {"127.0.0.1", "localhost", "::1"}:
            return False
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                return False
            server_port = int(self.server.server_address[1])
            if parsed.port not in {None, server_port}:
                return False
        return True

    def _auth_start(self, flow_id: str) -> None:
        token = self._token()
        if not token:
            self._html(HTTPStatus.UNAUTHORIZED, "Authorization link expired", "<p>Request a new connection link.</p>")
            return
        try:
            url = self.server.service.oauth.authorization_url(flow_id, token)
        except ListeningCompanionError:
            self._html(HTTPStatus.GONE, "Authorization link expired", "<p>Request a new connection link.</p>")
            return
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", url)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _callback(self) -> None:
        query = self._query()
        state = query.get("state", [""])[0]
        code = query.get("code", [None])[0]
        error = query.get("error", [None])[0]
        try:
            browser = self.server.service.oauth.complete_callback(state=state, code=code, error=error)
        except ListeningCompanionError:
            self._html(HTTPStatus.BAD_REQUEST, "Authorization failed", "<p>The callback state was invalid, expired, or already used.</p>")
            return
        location = f"/auth/result/{browser.flow_id}?token={browser.capability}"
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _auth_result(self, flow_id: str) -> None:
        token = self._token()
        if not token:
            self._html(HTTPStatus.UNAUTHORIZED, "Authorization result expired", "<p>Request a new connection link.</p>")
            return
        try:
            flow = self.server.service.oauth.viewer_flow(flow_id, token)
        except ListeningCompanionError:
            self._html(HTTPStatus.GONE, "Authorization result expired", "<p>Request a new connection link.</p>")
            return
        if flow.status == "connected":
            self._html(HTTPStatus.OK, "Account connected", "<p>The local account connection was verified. You may close this tab.</p>")
        else:
            self._html(HTTPStatus.BAD_REQUEST, "Account not connected", "<p>The account was not connected. Request a new link if needed.</p>")

    def _view(self, result_id: str) -> None:
        token = self._token()
        if not token:
            self._html(HTTPStatus.UNAUTHORIZED, "Viewer link required", "<p>Open the complete local viewer URL returned by the app.</p>")
            return
        try:
            operation, rows, warnings = self.server.service.render_cached_result(result_id, token)
        except CacheMiss:
            self._html(HTTPStatus.GONE, "Viewer result expired", "<p>Run the operation again to create a new session result.</p>")
            return
        except ListeningCompanionError:
            self._html(HTTPStatus.FORBIDDEN, "Viewer result unavailable", "<p>This local capability is not valid.</p>")
            return
        cards = "".join(_render_row(row) for row in rows)
        warning_html = "".join(
            f"<li>{html.escape(str(warning.get('message', 'source warning')))}</li>" for warning in warnings
        )
        body = f"<p><strong>Operation:</strong> {html.escape(operation)}</p>"
        if warnings:
            body += f"<ul>{warning_html}</ul>"
        body += "<p><strong>Attribution:</strong> SoundCloud content is shown here only for this local session. Use the links below to open the source.</p>"
        body += f"<section>{cards or '<p>No records are available.</p>'}</section>"
        cookie = f"lc_viewer={token}; HttpOnly; SameSite=Strict; Max-Age=600; Path=/view/{result_id}"
        self._html(HTTPStatus.OK, f"Listening Companion — {operation}", body, extra={"Set-Cookie": cookie})


def _render_row(row: dict[str, object]) -> str:
    title = html.escape(str(row.get("title") or row.get("username") or row.get("id") or "Untitled"))
    creator = html.escape(str(row.get("creator") or row.get("username") or ""))
    description = html.escape(str(row.get("description") or ""))[:4000]
    url = str(row.get("permalink_url") or "")
    link_html = ""
    parsed = urlsplit(url)
    if parsed.scheme == "https" and parsed.hostname in _LINK_HOSTS and not parsed.username and not parsed.password:
        link_html = f"<p><a rel='noreferrer noopener' href='{html.escape(url, quote=True)}'>Open on SoundCloud</a></p>"
    reasons = row.get("recommendation_reasons") or row.get("podcast_reasons") or []
    reason_html = ""
    if isinstance(reasons, list) and reasons:
        reason_html = "<p>Signals: " + html.escape(", ".join(str(item) for item in reasons)) + "</p>"
    return f"<article><h2>{title}</h2><p>{creator}</p><p>{description}</p>{reason_html}{link_html}</article>"


class ViewerHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], service) -> None:  # type: ignore[no-untyped-def]
        self.service = service
        super().__init__(address, _ViewerHandler)


class LoopbackViewer:
    def __init__(self, service) -> None:  # type: ignore[no-untyped-def]
        self.service = service
        self.server: ViewerHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> str:
        if self.server is not None:
            return self.base_url
        self.server = ViewerHTTPServer(
            (self.service.config.viewer_host, self.service.config.viewer_port), self.service
        )
        self.thread = threading.Thread(target=self.server.serve_forever, name="listening-companion-viewer", daemon=True)
        self.thread.start()
        return self.base_url

    @property
    def base_url(self) -> str:
        if self.server is None:
            return self.service.viewer_base_url
        host, port = self.server.server_address[:2]
        display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        return f"http://{display_host}:{port}"

    def stop(self) -> None:
        if self.server is None:
            return
        self.server.shutdown()
        self.server.server_close()
        self.server = None
        self.thread = None
