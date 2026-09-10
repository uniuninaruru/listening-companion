"""Explicit, bounded preference_profile storage with no provider data."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Mapping

from .errors import ConsentRequired, ValidationError


PROFILE_KEYS = {
    "genres",
    "podcast_topics",
    "languages",
    "preferred_creators",
    "avoid_terms",
    "preferred_duration_minutes",
    "discovery_level",
}
LIST_KEYS = {
    "genres": 12,
    "podcast_topics": 12,
    "languages": 8,
    "preferred_creators": 12,
    "avoid_terms": 20,
}
DISCOVERY_LEVELS = {"low", "balanced", "high"}


def _clean_list(name: str, value: Any, max_items: int) -> list[str]:
    if not isinstance(value, list) or len(value) > max_items:
        raise ValidationError(f"{name} must be a list of at most {max_items} strings")
    output: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item.strip()) > 80:
            raise ValidationError(f"{name} contains an invalid preference")
        output.append(item.strip())
    return output


def validate_preference_profile(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError("preference_profile must be an object")
    unknown = set(value) - PROFILE_KEYS
    if unknown:
        raise ValidationError("preference_profile contains unsupported fields")
    profile: dict[str, Any] = {}
    for key, max_items in LIST_KEYS.items():
        if key in value:
            profile[key] = _clean_list(key, value[key], max_items)
    if "preferred_duration_minutes" in value:
        duration = value["preferred_duration_minutes"]
        if isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= 720:
            raise ValidationError("preferred_duration_minutes must be an integer from 1 to 720")
        profile["preferred_duration_minutes"] = duration
    if "discovery_level" in value:
        level = value["discovery_level"]
        if not isinstance(level, str) or level not in DISCOVERY_LEVELS:
            raise ValidationError("discovery_level must be low, balanced, or high")
        profile["discovery_level"] = level
    try:
        serialized = json.dumps(profile, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValidationError("preference_profile is not serializable") from exc
    if len(serialized.encode("utf-8")) > 8192:
        raise ValidationError("preference_profile is too large")
    return profile


class PreferenceStore:
    """SQLite stores exactly one user-provided profile and nothing else."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser()
        self._lock = threading.RLock()
        self._initialized = False

    def _connect(self, *, create: bool) -> sqlite3.Connection | None:
        if not create and not self.path.exists():
            return None
        path_existed = self.path.exists()
        parent_existed = self.path.parent.exists()
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Do not change permissions on an existing user-controlled parent.
            if not parent_existed:
                try:
                    self.path.parent.chmod(0o700)
                except OSError:
                    pass
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA secure_delete = ON")
        if create and not path_existed:
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
        connection.execute(
            "CREATE TABLE IF NOT EXISTS preference_profile ("
            "profile_id INTEGER PRIMARY KEY CHECK (profile_id = 1), "
            "payload TEXT NOT NULL, updated_at INTEGER NOT NULL)"
        )
        connection.commit()
        return connection

    def get(self) -> dict[str, Any] | None:
        with self._lock:
            connection = self._connect(create=False)
            if connection is None:
                return None
            try:
                row = connection.execute(
                    "SELECT payload FROM preference_profile WHERE profile_id = 1"
                ).fetchone()
            except sqlite3.OperationalError:
                row = None
            finally:
                connection.close()
            if not row:
                return None
            try:
                return validate_preference_profile(json.loads(row[0]))
            except (TypeError, json.JSONDecodeError, ValidationError):
                return None

    def save(self, profile: Any, *, consent: bool) -> dict[str, Any]:
        if consent is not True:
            raise ConsentRequired("saving preferences requires consent=true")
        normalized = validate_preference_profile(profile)
        serialized = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            connection = self._connect(create=True)
            assert connection is not None
            try:
                connection.execute(
                    "INSERT INTO preference_profile(profile_id, payload, updated_at) VALUES (1, ?, strftime('%s','now')) "
                    "ON CONFLICT(profile_id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
                    (serialized,),
                )
                connection.commit()
            finally:
                connection.close()
        return normalized

    def delete(self) -> None:
        with self._lock:
            connection = self._connect(create=False)
            if connection is None:
                return
            try:
                try:
                    connection.execute("DELETE FROM preference_profile WHERE profile_id = 1")
                except sqlite3.OperationalError:
                    return
                connection.commit()
                # secure_delete is enabled above; VACUUM compacts the now-free
                # pages for the explicit user-requested deletion path.
                connection.execute("VACUUM")
            finally:
                connection.close()
