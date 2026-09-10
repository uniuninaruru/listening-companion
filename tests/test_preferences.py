from __future__ import annotations

import os
import pytest

from soundcloud_recommender.errors import ConsentRequired
from soundcloud_recommender.preferences import PreferenceStore


def test_missing_get_and_delete_do_not_create_database(tmp_path) -> None:
    path = tmp_path / "new-parent" / "preferences.sqlite3"
    store = PreferenceStore(path)
    assert store.get() is None
    store.delete()
    assert not path.exists()
    assert not path.parent.exists()


def test_save_requires_consent_and_creates_private_storage(tmp_path) -> None:
    path = tmp_path / "private" / "preferences.sqlite3"
    store = PreferenceStore(path)
    with pytest.raises(ConsentRequired):
        store.save({"genres": ["ambient"]}, consent=False)
    assert not path.exists()

    profile = store.save({"genres": [" ambient "], "discovery_level": "balanced"}, consent=True)
    assert profile == {"genres": ["ambient"], "discovery_level": "balanced"}
    assert path.exists()
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(path.parent).st_mode & 0o777 == 0o700
    assert store.get() == profile


def test_delete_removes_profile_and_uses_sqlite_secure_delete(tmp_path) -> None:
    path = tmp_path / "preferences.sqlite3"
    store = PreferenceStore(path)
    store.save({"podcast_topics": ["science"]}, consent=True)
    store.delete()
    assert store.get() is None
    assert b"science" not in path.read_bytes()
    # secure_delete is a connection setting. Inspect a connection opened by
    # the application, rather than assuming SQLite persists the pragma in the
    # database header.
    connection = store._connect(create=False)
    assert connection is not None
    try:
        assert connection.execute("PRAGMA secure_delete").fetchone()[0] in {1, "1", "on"}
    finally:
        connection.close()
