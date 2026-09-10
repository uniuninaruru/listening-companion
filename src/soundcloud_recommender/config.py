"""Environment-backed configuration with official-host allowlisting."""

from __future__ import annotations

import os
import platform
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .errors import ConfigurationError


OFFICIAL_API_HOST = "api.soundcloud.com"
OFFICIAL_AUTH_HOST = "secure.soundcloud.com"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _private_data_dir() -> Path:
    home = Path.home()
    if platform.system() == "Darwin":
        return home / "Library" / "Application Support" / "Listening Companion"
    if platform.system() == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", str(home))) / "Listening Companion"
    return Path(os.environ.get("XDG_STATE_HOME", str(home / ".local" / "state"))) / "listening-companion"


def _optional_text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _parse_int(environ: dict[str, str], name: str, default: int) -> int:
    raw = _optional_text(environ.get(name))
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc


@dataclass(frozen=True, slots=True)
class Config:
    client_id: str | None = None
    client_secret: str | None = None
    api_base_url: str = "https://api.soundcloud.com"
    authorize_url: str = "https://secure.soundcloud.com/authorize"
    token_url: str = "https://secure.soundcloud.com/oauth/token"
    redirect_uri: str = "http://127.0.0.1:8765/oauth/callback"
    scope: str | None = None
    preferences_db: Path | None = None
    cache_ttl_seconds: int = 600
    oauth_state_ttl_seconds: int = 600
    viewer_host: str = "127.0.0.1"
    viewer_port: int = 8765
    request_timeout_seconds: int = 20
    # Only direct test construction may enable controlled non-official hosts.
    from_env_is_safe: bool = True
    allow_test_endpoints: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "client_id", _optional_text(self.client_id))
        object.__setattr__(self, "client_secret", _optional_text(self.client_secret))
        object.__setattr__(self, "scope", _optional_text(self.scope))
        if self.preferences_db is None:
            object.__setattr__(self, "preferences_db", _private_data_dir() / "preferences.sqlite3")
        else:
            object.__setattr__(self, "preferences_db", Path(self.preferences_db).expanduser())
        self.validate()

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "Config":
        env = dict(os.environ if environ is None else environ)
        preferences_db = _optional_text(env.get("LISTENING_COMPANION_PREFERENCES_DB"))
        return cls(
            client_id=_optional_text(env.get("SOUNDCLOUD_CLIENT_ID")),
            client_secret=_optional_text(env.get("SOUNDCLOUD_CLIENT_SECRET")),
            api_base_url=(env.get("SOUNDCLOUD_API_BASE_URL") or "https://api.soundcloud.com").strip(),
            authorize_url=(env.get("SOUNDCLOUD_AUTHORIZE_URL") or "https://secure.soundcloud.com/authorize").strip(),
            token_url=(env.get("SOUNDCLOUD_TOKEN_URL") or "https://secure.soundcloud.com/oauth/token").strip(),
            redirect_uri=(env.get("LISTENING_COMPANION_REDIRECT_URI") or "http://127.0.0.1:8765/oauth/callback").strip(),
            scope=_optional_text(env.get("SOUNDCLOUD_SCOPE")),
            preferences_db=Path(preferences_db).expanduser() if preferences_db else None,
            cache_ttl_seconds=_parse_int(env, "LISTENING_COMPANION_CACHE_TTL_SECONDS", 600),
            oauth_state_ttl_seconds=_parse_int(env, "LISTENING_COMPANION_OAUTH_STATE_TTL_SECONDS", 600),
            viewer_host=(env.get("LISTENING_COMPANION_VIEWER_HOST") or "127.0.0.1").strip(),
            viewer_port=_parse_int(env, "LISTENING_COMPANION_VIEWER_PORT", 8765),
            request_timeout_seconds=_parse_int(env, "LISTENING_COMPANION_REQUEST_TIMEOUT_SECONDS", 20),
            from_env_is_safe=True,
            allow_test_endpoints=False,
        )

    @property
    def has_client_credentials(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def has_oauth_client_id(self) -> bool:
        return bool(self.client_id)

    def _allowed_hosts(self, kind: str) -> frozenset[str]:
        official = OFFICIAL_API_HOST if kind == "api" else OFFICIAL_AUTH_HOST
        hosts = {official}
        if self.allow_test_endpoints:
            candidate = urlsplit(
                {"api": self.api_base_url, "authorize": self.authorize_url, "token": self.token_url}[kind]
            ).hostname
            if candidate:
                hosts.add(candidate.lower())
        return frozenset(hosts)

    def validate_endpoint(self, url: str, kind: str) -> str:
        if kind not in {"api", "authorize", "token"}:
            raise ConfigurationError("unknown endpoint kind")
        if _CONTROL_RE.search(url):
            raise ConfigurationError(f"{kind} endpoint contains control characters")
        parsed = urlsplit(url)
        if parsed.scheme != "https" and not (self.allow_test_endpoints and parsed.scheme == "http"):
            raise ConfigurationError(f"{kind} endpoint must use HTTPS")
        if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ConfigurationError(f"{kind} endpoint is malformed")
        if parsed.hostname.lower() not in self._allowed_hosts(kind):
            raise ConfigurationError(f"{kind} endpoint host is not allowlisted")
        return url.rstrip("/")

    def validate(self) -> None:
        if not 1 <= self.cache_ttl_seconds <= 3600:
            raise ConfigurationError("cache TTL must be between 1 and 3600 seconds")
        if not 60 <= self.oauth_state_ttl_seconds <= 600:
            raise ConfigurationError("OAuth state TTL must be between 60 and 600 seconds")
        if not 1024 <= self.viewer_port <= 65535:
            raise ConfigurationError("viewer port is invalid")
        if self.viewer_host not in LOOPBACK_HOSTS:
            raise ConfigurationError("the viewer must bind to a loopback host")
        if _CONTROL_RE.search(self.redirect_uri):
            raise ConfigurationError("redirect_uri contains control characters")
        redirect = urlsplit(self.redirect_uri)
        if redirect.scheme != "http" or redirect.hostname not in LOOPBACK_HOSTS or not redirect.path:
            raise ConfigurationError("redirect_uri must be an http loopback callback URL")
        self.validate_endpoint(self.api_base_url, "api")
        self.validate_endpoint(self.authorize_url, "authorize")
        self.validate_endpoint(self.token_url, "token")
        if self.api_base_url.rstrip("/").endswith("/resolve"):
            raise ConfigurationError("api_base_url must be an API origin, not an endpoint")

    def endpoint_allowed(self, url: str, kind: str = "api") -> bool:
        try:
            self.validate_endpoint(url, kind)
        except ConfigurationError:
            return False
        return True


Settings = Config
