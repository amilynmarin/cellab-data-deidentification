from __future__ import annotations

from pathlib import Path

import pytest

from research_deid.keys import generate_key


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def example_schema(project_root: Path) -> Path:
    return project_root / "examples" / "example_schema.yaml"


@pytest.fixture
def example_input(project_root: Path) -> Path:
    return project_root / "examples" / "example_input.csv"


@pytest.fixture
def example_key(tmp_path: Path) -> Path:
    path = tmp_path / "example.key.json"
    generate_key(
        path,
        collaboration_id="example-collaboration",
        key_alias="example-key",
        key_version="1",
    )
    return path
