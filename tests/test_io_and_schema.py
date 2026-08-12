from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from openpyxl import Workbook

from research_deid.errors import InputDataError, SchemaError
from research_deid.io import load_input
from research_deid.schema import load_schema


def test_excel_multiple_populated_sheets_requires_explicit_selection(
    tmp_path: Path,
    example_schema: Path,
) -> None:
    workbook = Workbook()
    first = workbook.active
    first.title = "first"
    first.append(["participant_id"])
    first.append(["P1"])
    second = workbook.create_sheet("second")
    second.append(["participant_id"])
    second.append(["P2"])
    path = tmp_path / "two_sheets.xlsx"
    workbook.save(path)

    schema = load_schema(example_schema).model
    with pytest.raises(InputDataError, match="multiple populated sheets"):
        load_input(path, schema)
    loaded = load_input(path, schema, sheet_override="second")
    assert loaded.metadata.selected_excel_sheet == "second"


def test_schema_freezes_shift_and_small_cell_policy(tmp_path: Path, example_schema: Path) -> None:
    text = example_schema.read_text()
    bad_shift = tmp_path / "bad_shift.yaml"
    bad_shift.write_text(text.replace("min_weeks: 4", "min_weeks: 3"))
    with pytest.raises(SchemaError, match="4 through 52"):
        load_schema(bad_shift)

    bad_cell = tmp_path / "bad_cell.yaml"
    bad_cell.write_text(text.replace("threshold: 5", "threshold: 4"))
    with pytest.raises(SchemaError, match="fewer than five"):
        load_schema(bad_cell)


def test_schema_blocks_exact_interval_derivation_from_research_day_only_event(
    tmp_path: Path,
    example_schema: Path,
) -> None:
    text = example_schema.read_text()
    injection = """
  - name: leaked_rare_interval
    kind: elapsed_seconds
    start_column: hospital_event_local
    end_column: hospital_event_local

"""
    bad = tmp_path / "rare_interval.yaml"
    bad.write_text(text.replace("interval_checks:\n", injection + "interval_checks:\n"))
    with pytest.raises(SchemaError, match="exact elapsed-time outputs"):
        load_schema(bad)


def test_stata_reader_preserves_string_identifiers_and_normalizes_labels(
    tmp_path: Path, example_schema: Path
) -> None:
    path = tmp_path / "minimal.dta"
    pd.DataFrame({"participant_id": ["001", "002"], "code": [1, 2]}).to_stata(
        path,
        write_index=False,
        version=118,
        variable_labels={"code": "Study arm"},
        value_labels={"code": {1: "Control", 2: "Treatment"}},
    )
    schema = load_schema(example_schema).model
    loaded = load_input(path, schema)
    assert loaded.dataframe["participant_id"].tolist() == ["001", "002"]
    assert loaded.metadata.variable_labels["code"] == "Study arm"
    assert loaded.metadata.value_labels["code"] == {"1": "Control", "2": "Treatment"}


def test_csv_literal_na_is_not_coerced_to_missing(tmp_path: Path, example_schema: Path) -> None:
    path = tmp_path / "literal-na.csv"
    path.write_text("participant_id,note\n001,NA\n", encoding="utf-8")
    schema = load_schema(example_schema).model
    loaded = load_input(path, schema)
    assert loaded.dataframe.at[0, "participant_id"] == "001"
    assert loaded.dataframe.at[0, "note"] == "NA"


def test_schema_rejects_duplicate_output_filenames(tmp_path: Path, example_schema: Path) -> None:
    bad = tmp_path / "duplicate-output.yaml"
    bad.write_text(
        example_schema.read_text().replace(
            "data_dictionary: data_dictionary.csv", "data_dictionary: deidentified.csv"
        )
    )
    with pytest.raises(SchemaError, match="Release output filenames must be distinct"):
        load_schema(bad)
