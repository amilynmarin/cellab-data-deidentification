from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pandas as pd
import pytest
import yaml

from research_deid.engine import run_export
from research_deid.errors import DataValidationError


def test_end_to_end_is_byte_deterministic_and_safe(
    tmp_path: Path,
    example_input: Path,
    example_schema: Path,
    example_key: Path,
) -> None:
    first = run_export(example_input, example_schema, example_key, tmp_path / "first", create_archive=True)
    second = run_export(example_input, example_schema, example_key, tmp_path / "second", create_archive=True)

    for name in ("deidentified.csv", "data_dictionary.csv", "qa_report.json", "release.zip"):
        assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "second" / name).read_bytes()

    output = pd.read_csv(first.analytic_csv, dtype=str, keep_default_na=False)
    assert len(output) == 8
    assert output["participant_id"].iloc[0] == output["participant_id"].iloc[1]
    assert output["dyad_id"].iloc[0] == output["dyad_id"].iloc[2]
    assert output["response_elapsed_seconds"].tolist() == [str(value) + ".0" for value in range(36, 44)]
    assert output["utc_offset"].eq("-05:00").all()
    assert output["approved_code"].iloc[0].startswith("'=")
    assert output["approved_code"].iloc[1].startswith("'-")
    assert output["distress"].iloc[6] == ""
    assert output["distress"].iloc[7] == ""

    rare = output.loc[output["rare_event_flag"] == "1"].iloc[0]
    assert rare["synthetic_hospital_event_local"] == ""
    assert rare["synthetic_hospital_event_utc"] == ""
    assert rare["synthetic_hospital_event_offset"] == ""
    assert rare["hospital_event_research_day"] == "3"

    combined = first.analytic_csv.read_text() + first.data_dictionary.read_text() + first.qa_report.read_text()
    for raw_value in ("P001", "GUID-001", "North Hospital", "Original narrative"):
        assert raw_value not in combined

    report = json.loads(first.qa_report.read_text())
    assert report["status"] == "success"
    assert "filename" not in report["input"]
    assert "filename" not in report["schema"]
    assert report["invariants"]["row_order_preserved"] is True
    assert report["validation"]["optional_invalid_values_blanked"]["distress"] == 1
    assert report["validation"]["missing_codes_converted"]["distress"] == 1
    assert report["transformations"]["timestamp_groups"]["hospital_event"][
        "rare_research_day_only_anchor_count"
    ] == 1
    assert report["transformations"]["csv_formula_safety"] == {"approved_code": 2}
    assert "secret" not in first.qa_report.read_text().lower()

    secret_b64 = json.loads(example_key.read_text())["secret_b64"]
    assert first.release_archive is not None
    with zipfile.ZipFile(first.release_archive) as release:
        assert sorted(release.namelist()) == ["data_dictionary.csv", "deidentified.csv", "qa_report.json"]
        release_text = "\n".join(release.read(name).decode("utf-8") for name in release.namelist())
    assert secret_b64 not in release_text


def test_duplicate_key_halts_without_deduplicating(
    tmp_path: Path,
    example_input: Path,
    example_schema: Path,
    example_key: Path,
) -> None:
    frame = pd.read_csv(example_input, dtype=str, keep_default_na=False)
    frame.loc[1, "ema_id"] = frame.loc[0, "ema_id"]
    bad_input = tmp_path / "P001-GUID-001.csv"
    frame.to_csv(bad_input, index=False)

    with pytest.raises(DataValidationError, match="not unique"):
        run_export(bad_input, example_schema, example_key, tmp_path / "out")
    failure = tmp_path / "out" / "qa_report.failed.json"
    assert failure.exists()
    text = failure.read_text()
    assert "P001" not in text
    assert "GUID-001" not in text
    assert "input_filename" not in text
    assert "schema_filename" not in text
    assert '"status": "failure"' in text


def test_release_output_cannot_overwrite_an_input_or_key(
    tmp_path: Path,
    example_input: Path,
    example_schema: Path,
    example_key: Path,
) -> None:
    copied_input = tmp_path / "source.csv"
    copied_input.write_bytes(example_input.read_bytes())
    copied_schema = tmp_path / "schema.yaml"
    copied_schema.write_text(
        example_schema.read_text().replace("analytic_csv: deidentified.csv", "analytic_csv: source.csv")
    )

    from research_deid.errors import OutputError

    with pytest.raises(OutputError, match="must not overwrite"):
        run_export(copied_input, copied_schema, example_key, tmp_path, overwrite=True)


def test_output_column_order_follows_source_not_schema_order(
    tmp_path: Path,
    example_input: Path,
    example_schema: Path,
    example_key: Path,
) -> None:
    payload = yaml.safe_load(example_schema.read_text())
    payload["columns"] = list(reversed(payload["columns"]))
    reordered_schema = tmp_path / "reordered-schema.yaml"
    reordered_schema.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    baseline = run_export(example_input, example_schema, example_key, tmp_path / "baseline")
    reordered = run_export(example_input, reordered_schema, example_key, tmp_path / "reordered")

    assert baseline.analytic_csv.read_bytes() == reordered.analytic_csv.read_bytes()
    columns = pd.read_csv(reordered.analytic_csv, nrows=0).columns.tolist()
    assert columns[:7] == [
        "participant_id",
        "nih_guid_token",
        "dyad_id",
        "ema_anchor_id",
        "site_id",
        "role",
        "age_at_index",
    ]


def test_release_commit_restores_prior_outputs_after_a_partial_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import research_deid.engine as engine
    from research_deid.errors import OutputError

    stage = tmp_path / "stage"
    stage.mkdir()
    first_stage = stage / "first.csv"
    second_stage = stage / "second.csv"
    first_stage.write_text("new first", encoding="utf-8")
    second_stage.write_text("new second", encoding="utf-8")
    first_final = tmp_path / "first.csv"
    second_final = tmp_path / "second.csv"
    first_final.write_text("old first", encoding="utf-8")
    second_final.write_text("old second", encoding="utf-8")

    real_replace = os.replace
    failed = False

    def fail_once(source: str | Path, destination: str | Path) -> None:
        nonlocal failed
        if Path(source) == second_stage and not failed:
            failed = True
            raise OSError("simulated commit failure")
        real_replace(source, destination)

    monkeypatch.setattr(engine.os, "replace", fail_once)
    with pytest.raises(OutputError, match="prior outputs were restored"):
        engine._commit_staged_outputs(
            [(first_stage, first_final), (second_stage, second_final)], overwrite=True
        )

    assert first_final.read_text(encoding="utf-8") == "old first"
    assert second_final.read_text(encoding="utf-8") == "old second"
