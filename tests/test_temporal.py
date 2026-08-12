from __future__ import annotations

from datetime import datetime, time
from pathlib import Path

import pandas as pd

from research_deid.io import load_input
from research_deid.keys import load_key
from research_deid.schema import load_schema
from research_deid.temporal import _jitter_bounds, process_temporal
from research_deid.validation import (
    normalize_missing_values,
    validate_primary_key,
    validate_source_columns,
    validate_values,
)


def test_shift_is_nonzero_4_to_52_weeks_and_shared_by_dyad(
    example_input: Path,
    example_schema: Path,
    example_key: Path,
) -> None:
    loaded_schema = load_schema(example_schema)
    schema = loaded_schema.model
    loaded = load_input(example_input, schema)
    frame, summary = normalize_missing_values(loaded.dataframe, schema)
    frame = validate_source_columns(frame, schema, summary)
    frame = validate_values(frame, schema, summary)
    validate_primary_key(frame, schema, summary)
    key = load_key(
        example_key,
        collaboration_id=schema.collaboration_id,
        key_alias=schema.key_alias,
        key_version=schema.key_version,
    )
    temporal = process_temporal(frame, schema, key, summary)
    shifts = [int(value) for value in temporal.shift_weeks]
    assert all(4 <= abs(value) <= 52 for value in shifts)
    assert all(value != 0 for value in shifts)
    assert shifts[0] == shifts[1] == shifts[2]
    assert shifts[3] == shifts[4]


def test_jitter_bounds_protect_midnight_and_behavioral_day() -> None:
    boundary = time(4, 0)
    assert _jitter_bounds(datetime(2026, 1, 1, 0, 0, 30), boundary)[0] == -30
    assert _jitter_bounds(datetime(2026, 1, 1, 3, 59, 30), boundary)[1] == 29
    assert _jitter_bounds(datetime(2026, 1, 1, 4, 0, 30), boundary)[0] == -30
    assert _jitter_bounds(datetime(2026, 1, 1, 23, 59, 30), boundary)[1] == 29


def test_shared_anchor_jitter_preserves_internal_intervals(
    tmp_path: Path,
    example_input: Path,
    example_schema: Path,
    example_key: Path,
) -> None:
    from research_deid.engine import run_export

    result = run_export(example_input, example_schema, example_key, tmp_path / "out")
    output = pd.read_csv(result.analytic_csv, dtype=str, keep_default_na=False)
    prompt = pd.to_datetime(output["synthetic_utc_timestamp"])
    start = pd.to_datetime(output["synthetic_response_start_utc"])
    end = pd.to_datetime(output["synthetic_response_end_utc"])
    assert ((start - prompt).dt.total_seconds() == 20).all()
    assert ((end - start).dt.total_seconds().tolist() == list(range(36, 44)))
