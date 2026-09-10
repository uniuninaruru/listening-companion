"""URL allowlists and non-secret capability helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import AllowlistError, ValidationError


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9:_-]{1,180}$")
_TYPED_REFERENCE_RE = re.compile(r"^soundcloud:(tracks|playlists|users):[A-Za-z0-9_-]{1,100}$")
_SOUNDCLOUD_HOSTS = {"soundcloud.com", "www.soundcloud.com", "on.soundcloud.com", "m.soundcloud.com"}


def random_opaque_id(length: int = 24) -> str:
    return secrets.token_urlsafe(length)


def constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def validate_identifier(value: str, label: str = "identifier") -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ValidationError(f"{label} must be a short identifier")
    return value


def canonical_resource_reference(value: str, kind: str) -> str:
    """Return the OpenAPI typed reference used in resource path parameters."""
    if kind not in {"track", "playlist", "user"}:
        raise ValidationError("unsupported resource kind")
    if not isinstance(value, str) or not value or len(value) > 180:
        raise ValidationError("resource reference is invalid")
    plural = {"track": "tracks", "playlist": "playlists", "user": "users"}[kind]
    if value.isdigit() and len(value) <= 32:
        return f"soundcloud:{plural}:{value}"
    match = _TYPED_REFERENCE_RE.fullmatch(value)
    if not match:
        raise ValidationError("resource reference must be a numeric ID or typed SoundCloud reference")
    if match.group(1) != plural:
        raise ValidationError("resource reference kind does not match the endpoint")
    return value


def validate_soundcloud_permalink(value: str) -> str:
    if not isinstance(value, str) or len(value) > 2000:
        raise ValidationError("url must be a SoundCloud permalink")
    parts = urlsplit(value)
    if parts.scheme != "https" or parts.hostname not in _SOUNDCLOUD_HOSTS:
        raise ValidationError("url must use an https SoundCloud host")
    if parts.username or parts.password or not parts.path or "#" in value:
        raise ValidationError("url must be a clean SoundCloud permalink")
    return value


@dataclass(frozen=True, slots=True)
class EndpointAllowlist:
    """Exact-origin allowlist for API requests and validated redirects."""

    api_base_url: str
    authorize_url: str
    token_url: str

    def __post_init__(self) -> None:
        for name, url in (
            ("api_base_url", self.api_base_url),
            ("authorize_url", self.authorize_url),
            ("token_url", self.token_url),
        ):
            parts = urlsplit(url)
            if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
                raise AllowlistError(f"{name} is not an https origin")

    @staticmethod
    def _origin(url: str) -> tuple[str, str, int | None]:
        parts = urlsplit(url)
        return parts.scheme, (parts.hostname or "").lower(), parts.port

    def assert_api_url(self, url: str) -> str:
        candidate = self._origin(url)
        expected = self._origin(self.api_base_url)
        parts = urlsplit(url)
        if candidate != expected or parts.username or parts.password:
            raise AllowlistError("API URL is outside the exact API origin allowlist")
        return url

    def assert_authorize_url(self, url: str) -> str:
        if self._origin(url) != self._origin(self.authorize_url):
            raise AllowlistError("authorization URL is outside the allowlist")
        return url

    def assert_token_url(self, url: str) -> str:
        if self._origin(url) != self._origin(self.token_url):
            raise AllowlistError("token URL is outside the allowlist")
        return url

    def build_api_url(self, path: str) -> str:
        if not path.startswith("/") or "//" in path:
            raise AllowlistError("API path must be relative to the allowlisted origin")
        return self.assert_api_url(self.api_base_url.rstrip("/") + path)
