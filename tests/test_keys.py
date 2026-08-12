from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from research_deid.errors import KeyManagementError
from research_deid.keys import generate_key, import_key, load_key, rotate_key


def test_key_permissions_rotation_and_retirement(tmp_path: Path) -> None:
    current = tmp_path / "current.key.json"
    replacement = tmp_path / "replacement.key.json"
    generated = generate_key(
        current,
        collaboration_id="collab",
        key_alias="key",
        key_version="1",
    )
    assert len(generated.secret) == 32
    if os.name == "posix":
        assert stat.S_IMODE(current.stat().st_mode) & 0o077 == 0

    retired, active = rotate_key(
        current,
        replacement,
        new_alias="key",
        new_version="2",
    )
    assert retired.status == "retired"
    assert active.status == "active"
    with pytest.raises(KeyManagementError, match="Retired"):
        load_key(current)
    assert load_key(replacement).key_version == "2"


def test_key_metadata_must_match_schema_expectation(example_key: Path) -> None:
    with pytest.raises(KeyManagementError, match="does not match"):
        load_key(example_key, collaboration_id="wrong")


def test_import_requires_restricted_source_permissions(tmp_path: Path) -> None:
    source = tmp_path / "authorized-secret.bin"
    source.write_bytes(bytes(range(32)))
    output = tmp_path / "imported.key.json"
    if os.name == "posix":
        source.chmod(0o644)
        with pytest.raises(KeyManagementError, match="owner-only"):
            import_key(
                source,
                output,
                collaboration_id="collab",
                key_alias="key",
                key_version="1",
            )
        source.chmod(0o600)
    record = import_key(
        source,
        output,
        collaboration_id="collab",
        key_alias="key",
        key_version="1",
        allow_unverified_permissions=os.name != "posix",
    )
    assert record.secret == bytes(range(32))
