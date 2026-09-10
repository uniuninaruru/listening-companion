"""Dependency-free HTTP abstractions with redirect and host boundaries."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Mapping, Protocol

from .config import Config
from .errors import AllowlistError, ProviderError


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""
    url: str = ""

    def header(self, name: str) -> str | None:
        wanted = name.lower()
        for key, value in self.headers.items():
            if key.lower() == wanted:
                return value
        return None

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> object:
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            raise ProviderError("provider returned invalid JSON") from exc


class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 20.0,
    ) -> HttpResponse:
        ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def http_error_301(self, req, fp, code, msg, headers):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)

    http_error_302 = http_error_301
    http_error_303 = http_error_301
    http_error_307 = http_error_301
    http_error_308 = http_error_301


class UrllibTransport:
    """A synchronous transport that returns redirects for explicit validation."""

    def __init__(self, max_body_bytes: int = 8 * 1024 * 1024) -> None:
        self._opener = urllib.request.build_opener(_NoRedirect())
        self.max_body_bytes = max_body_bytes

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 20.0,
    ) -> HttpResponse:
        request = urllib.request.Request(url, data=body, headers=dict(headers or {}), method=method.upper())
        try:
            with self._opener.open(request, timeout=timeout) as response:
                payload = response.read(self.max_body_bytes + 1)
                if len(payload) > self.max_body_bytes:
                    raise ProviderError("provider response exceeded local size limit")
                return HttpResponse(
                    status=int(response.status),
                    headers={key.lower(): value for key, value in response.headers.items()},
                    body=payload,
                    url=response.geturl(),
                )
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                return HttpResponse(
                    status=int(exc.code),
                    headers={key.lower(): value for key, value in exc.headers.items()},
                    body=b"",
                    url=exc.geturl(),
                )
            raise ProviderError(f"provider request failed with HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError("HTTP transport failed") from exc


def form_encode(values: Mapping[str, str]) -> bytes:
    return urllib.parse.urlencode(values).encode("utf-8")


def response_error_message(response: HttpResponse) -> str:
    if response.status == 401:
        return "provider authorization was rejected"
    if response.status == 403:
        return "provider denied access to this resource"
    if response.status == 404:
        return "provider resource was not found"
    if response.status == 429:
        return "provider rate limit reached"
    if response.status in {301, 302, 303, 307, 308}:
        return "provider returned an unexpected redirect"
    if response.status >= 500:
        return "provider service is temporarily unavailable"
    return f"provider request failed with HTTP {response.status}"


class UrlAllowlist:
    """Compatibility wrapper for exact configured API-origin validation."""

    def __init__(self, settings: Config) -> None:
        self.settings = settings

    def api_url(self, url: str) -> str:
        if not self.settings.endpoint_allowed(url, "api"):
            raise AllowlistError("API URL is not allowlisted")
        if len(urllib.parse.urlsplit(url).query) > 4096:
            raise AllowlistError("API query is too long")
        return url

    def api_path(self, path: str) -> str:
        if not path.startswith("/") or "\\" in path or ".." in path.split("?")[0].split("/"):
            raise AllowlistError("API path is invalid")
        url = urllib.parse.urljoin(self.settings.api_base_url.rstrip("/") + "/", path.lstrip("/"))
        return self.api_url(url)

    def redirect_target(self, url: str) -> str:
        return self.api_url(url)
