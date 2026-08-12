from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from . import __version__
from .calendars import (
    behavioral_date,
    dst_status,
    holiday_dates,
    nearest_dst_transition_distance,
    nearest_holiday_distance,
    season_for_date,
)
from .crypto import canonical_value, derivation_context, derive_token, is_missing
from .errors import DataValidationError, DeidError, OutputError, TransformationError
from .io import LoadedData, SourceMetadata, load_input, sha256_file
from .keys import KeyRecord, load_key
from .models import ColumnRule, DerivedColumn, ToolSchema
from .reporting import (
    compact_json,
    deterministic_json,
    escape_formula_like,
    normalize_json_value,
    write_bytes,
    write_csv,
    write_release_archive,
)
from .schema import LoadedSchema, load_schema
from .temporal import IndexValue, Moment, TemporalResults, process_temporal
from .validation import (
    ValidationSummary,
    evaluate_small_cells,
    normalize_missing_values,
    validate_interval_series,
    validate_primary_key,
    validate_source_columns,
    validate_values,
)


@dataclass
class BuiltColumn:
    name: str
    series: pd.Series
    source: str | None
    status: str
    semantic_type: str
    description: str
    derivation: str
    temporal_interpretation: str = ""
    timestamp_provenance: str = ""
    value_labels: dict[str, str] | None = None
    output_format: str = ""


@dataclass(frozen=True)
class ExportResult:
    analytic_csv: Path
    data_dictionary: Path
    qa_report: Path
    release_archive: Path | None


def _safe_error_payload(error: Exception) -> dict[str, Any]:
    return {
        "status": "failure",
        "error_category": error.__class__.__name__,
        "error": str(error),
    }


def _write_failure_report(
    output_dir: Path,
    schema: LoadedSchema | None,
    input_path: Path,
    error: Exception,
    *,
    overwrite: bool,
) -> None:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        base = schema.model.output.qa_report if schema is not None else "qa_report.json"
        destination = output_dir / f"{Path(base).stem}.failed.json"
        payload = {
            **_safe_error_payload(error),
            "tool": {"name": "cellab-data-deidentification", "version": __version__},
            "schema_version": schema.model.schema_version if schema is not None else None,
            "collaboration": (
                {
                    "id": schema.model.collaboration_id,
                    "key_alias": schema.model.key_alias,
                    "key_version": schema.model.key_version,
                }
                if schema is not None
                else None
            ),
        }
        write_bytes(destination, deterministic_json(payload), overwrite=overwrite)
    except Exception:
        return


def _context(schema: ToolSchema) -> dict[str, str]:
    return derivation_context(
        collaboration_id=schema.collaboration_id,
        schema_version=schema.schema_version,
        key_alias=schema.key_alias,
        key_version=schema.key_version,
    )


def _validate_output_path_separation(
    *,
    input_path: Path,
    schema_path: Path,
    key_path: Path,
    output_paths: list[Path],
) -> None:
    protected = {
        input_path.expanduser().resolve(),
        schema_path.expanduser().resolve(),
        key_path.expanduser().resolve(),
    }
    collisions = [path.name for path in output_paths if path.expanduser().resolve() in protected]
    if collisions:
        raise OutputError(
            "Release outputs must not overwrite the source data, transformation schema, or collaboration key: "
            + ", ".join(sorted(collisions))
        )


def _commit_staged_outputs(
    pairs: list[tuple[Path, Path]],
    *,
    overwrite: bool,
) -> None:
    """Commit a staged release as one rollback-protected file set."""
    existing = [final.name for _, final in pairs if os.path.lexists(final)]
    if existing and not overwrite:
        raise OutputError("Refusing to overwrite existing output files: " + ", ".join(existing))

    backups: dict[Path, Path] = {}
    installed: set[Path] = set()
    try:
        if overwrite:
            for index, (_, final) in enumerate(pairs):
                if not os.path.lexists(final):
                    continue
                backup = pairs[0][0].parent / f".rollback-{index}-{final.name}"
                os.replace(final, backup)
                backups[final] = backup

        for staged, final in pairs:
            os.replace(staged, final)
            installed.add(final)
            if os.name == "posix":
                final.chmod(0o600)
    except Exception as exc:
        rollback_errors: list[str] = []
        for _, final in reversed(pairs):
            backup = backups.get(final)
            try:
                if backup is not None and os.path.lexists(backup):
                    os.replace(backup, final)
                elif final in installed and os.path.lexists(final):
                    final.unlink()
            except Exception:
                rollback_errors.append(final.name)
        if rollback_errors:
            raise OutputError(
                "Release commit failed and rollback was incomplete for: "
                + ", ".join(sorted(rollback_errors))
            ) from exc
        raise OutputError("Release commit failed; prior outputs were restored.") from exc


def _token_components(row: pd.Series, schema: ToolSchema, rule: ColumnRule) -> list[Any] | None:
    components: list[Any] = []
    for source in rule.token_sources:
        value = row[source.column]
        if is_missing(value):
            return None
        namespace = schema.study_namespace if source.namespace == "study" else "global"
        components.extend([namespace, value])
    for column in rule.token_context:
        value = row[column]
        if is_missing(value):
            return None
        components.extend([f"context:{column}", value])
    return components


def _tokenize(
    frame: pd.DataFrame,
    schema: ToolSchema,
    key: KeyRecord,
    rule: ColumnRule,
) -> pd.Series:
    values: list[Any] = []
    token_origins: dict[str, tuple[str, ...]] = {}
    context = _context(schema)
    missing_rows: list[int] = []
    for index, row in frame.iterrows():
        components = _token_components(row, schema, rule)
        if components is None:
            values.append(pd.NA)
            if rule.required:
                missing_rows.append(index)
            continue
        token = derive_token(
            key.secret,
            context,
            domain=rule.token_domain or rule.source,
            prefix=rule.token_prefix,
            components=components,
        )
        canonical = tuple(canonical_value(value) for value in components)
        previous = token_origins.get(token)
        if previous is not None and previous != canonical:
            raise TransformationError(f"A cryptographic token collision occurred in output {rule.output}.")
        token_origins[token] = canonical
        values.append(token)
    if missing_rows:
        displayed = [index + 2 for index in missing_rows[:20]]
        extra = len(missing_rows) - len(displayed)
        suffix = f" and {extra} additional rows" if extra else ""
        raise DataValidationError(
            f"Token source values required for {rule.output} are missing at rows {displayed}{suffix}."
        )
    return pd.Series(values, index=frame.index, dtype="object")


def _parse_date(value: Any) -> date:
    parsed = pd.Timestamp(pd.to_datetime(value, errors="raise"))
    if pd.isna(parsed):
        raise ValueError("date is missing")
    return parsed.date()


def _age_value(birth_value: Any, index_value: IndexValue | None) -> int | str | None:
    if is_missing(birth_value) or index_value is None:
        return None
    birth = _parse_date(birth_value)
    reference = index_value.local_date
    age = reference.year - birth.year - ((reference.month, reference.day) < (birth.month, birth.day))
    if age < 0 or age > 130:
        raise ValueError("age is outside the valid range")
    return "90+" if age >= 90 else age


def _age_series(
    frame: pd.DataFrame,
    birth_column: str,
    index_series: pd.Series,
    *,
    required: bool,
    validation_summary: ValidationSummary,
) -> pd.Series:
    values: list[Any] = []
    invalid: list[int] = []
    for index in frame.index:
        try:
            values.append(_age_value(frame.at[index, birth_column], index_series.at[index]))
        except (ValueError, TypeError, OverflowError):
            values.append(pd.NA)
            invalid.append(index)
    if invalid and required:
        rows = [index + 2 for index in invalid[:20]]
        raise DataValidationError(f"Age at index could not be derived for required values at rows {rows}.")
    if invalid:
        validation_summary.optional_invalid_blanked[birth_column] = (
            validation_summary.optional_invalid_blanked.get(birth_column, 0) + len(invalid)
        )
    return pd.Series(values, index=frame.index, dtype="object")


def _utc_series_for_source(frame: pd.DataFrame, temporal: TemporalResults, source: str) -> pd.Series:
    if source in temporal.source_moments:
        return temporal.source_moments[source].map(
            lambda moment: None if moment is None else moment.utc
        )
    values: list[datetime | None] = []
    invalid: list[int] = []
    for index, value in frame[source].items():
        if is_missing(value):
            values.append(None)
            continue
        try:
            values.append(pd.Timestamp(pd.to_datetime(value, errors="raise", utc=True)).to_pydatetime())
        except Exception:
            values.append(None)
            invalid.append(index)
    if invalid:
        raise DataValidationError(f"UTC interval source {source} contains invalid timestamps.")
    return pd.Series(values, index=frame.index, dtype="object")


def _derive_temporal_column(
    item: DerivedColumn,
    frame: pd.DataFrame,
    schema: ToolSchema,
    temporal: TemporalResults,
    interval_cache: dict[tuple[str, str], pd.Series],
    validation_summary: ValidationSummary,
) -> tuple[pd.Series, str, str, dict[str, str] | None]:
    kind = item.kind
    if kind == "elapsed_seconds":
        assert item.start_column and item.end_column
        key = (item.start_column, item.end_column)
        if key not in interval_cache:
            starts = _utc_series_for_source(frame, temporal, item.start_column)
            ends = _utc_series_for_source(frame, temporal, item.end_column)
            interval_cache[key] = validate_interval_series(
                f"derived:{item.name}",
                starts,
                ends,
                minimum_seconds=0.0,
                maximum_seconds=None,
                summary=validation_summary,
            )
        return interval_cache[key], "number", "Original UTC-derived elapsed seconds", None

    if kind == "age_at_index":
        assert item.birth_date_column and item.index_event
        rule = next(rule for rule in schema.columns if rule.source == item.birth_date_column)
        series = _age_series(
            frame,
            item.birth_date_column,
            temporal.index_events[item.index_event],
            required=rule.required,
            validation_summary=validation_summary,
        )
        return series, "string", f"Whole years at index event {item.index_event}; ages 90 and older coded 90+", None

    assert item.timestamp_group
    group_result = temporal.groups[item.timestamp_group]
    moments = temporal.moment_series(item.timestamp_group, item.timestamp_field)
    suppress_mask = group_result.rare_suppressed if (
        group_result.spec.rare_event is not None and group_result.spec.rare_event.suppress_calendar_context
    ) else pd.Series(False, index=frame.index, dtype="boolean")

    if kind == "weekday_weekend":
        series = moments.map(
            lambda moment: None if moment is None else ("weekend" if moment.local.weekday() >= 5 else "weekday")
        )
        series = series.mask(suppress_mask, pd.NA)
        return series, "string", "Derived from the original local calendar date", {"weekday": "Weekday", "weekend": "Weekend"}

    if kind == "behavioral_research_day":
        assert item.index_event
        index_values = temporal.index_events[item.index_event]
        boundary = schema.shift.behavioral_day_boundary
        values: list[Any] = []
        for moment, index_value in zip(moments, index_values, strict=True):
            if moment is None or index_value is None:
                values.append(pd.NA)
                continue
            event_day = behavioral_date(moment.local, boundary)
            if index_value.moment is not None:
                index_day = behavioral_date(index_value.moment.local, boundary)
            else:
                index_day = index_value.local_date
            values.append((event_day - index_day).days)
        return pd.Series(values, index=frame.index, dtype="Int64"), "integer", (
            f"Original local timestamp relative to index event {item.index_event}; day 0 is the index behavioral day; "
            f"boundary {boundary}"
        ), None

    if kind == "season":
        series = moments.map(lambda moment: None if moment is None else season_for_date(moment.local.date()))
        series = series.mask(suppress_mask, pd.NA)
        labels = {"winter": "Winter", "spring": "Spring", "summer": "Summer", "autumn": "Autumn"}
        return series, "string", "Four-season category derived from the original local date", labels

    if kind == "holiday_proximity":
        years = [moment.local.year for moment in moments if moment is not None]
        holidays = holiday_dates(schema.holidays, years)
        series = moments.map(
            lambda moment: None
            if moment is None
            else nearest_holiday_distance(moment.local.date(), holidays, schema.holidays.window_days)
        ).astype("Int64")
        series = series.mask(suppress_mask, pd.NA)
        return series, "integer", (
            "Signed days to the nearest qualifying holiday within seven days, derived from the original local date; "
            "negative before and positive after"
        ), None

    if kind == "dst_status":
        series = moments.map(
            lambda moment: None if moment is None else dst_status(moment.local_aware, moment.timezone_name)
        )
        series = series.mask(suppress_mask, pd.NA)
        labels = {"daylight": "Daylight time", "standard": "Standard time", "not_applicable": "Not applicable"}
        return series, "string", "Daylight-saving status derived from the original local timestamp and named time-zone rules", labels

    if kind == "dst_transition_proximity":
        series = moments.map(
            lambda moment: None
            if moment is None
            else nearest_dst_transition_distance(moment.local_aware, moment.timezone_name, 14)
        ).astype("Int64")
        series = series.mask(suppress_mask, pd.NA)
        return series, "integer", (
            "Signed days to the nearest daylight-saving transition within 14 days, based on the original local calendar; "
            "negative before and positive after"
        ), None

    raise TransformationError(f"Unsupported derived column kind: {kind}")


def _value_labels(rule: ColumnRule, metadata: SourceMetadata) -> dict[Any, str]:
    if rule.value_labels:
        return dict(rule.value_labels)
    return dict(metadata.value_labels.get(rule.source, {}))


def _dictionary_rows(
    source_columns: list[str],
    schema: ToolSchema,
    metadata: SourceMetadata,
    output_columns: list[BuiltColumn],
) -> list[dict[str, Any]]:
    rule_map = {rule.source: rule for rule in schema.columns}
    by_source = {column.source: column for column in output_columns if column.source is not None}
    output_order = {column.name: index + 1 for index, column in enumerate(output_columns)}
    rows: list[dict[str, Any]] = []
    for source_order, source in enumerate(source_columns, start=1):
        rule = rule_map[source]
        built = by_source.get(source)
        source_labels = _value_labels(rule, metadata)
        rows.append(
            {
                "output_order": output_order.get(built.name) if built else None,
                "output_variable": built.name if built else None,
                "source_order": source_order,
                "source_variable": source,
                "status": built.status if built else "removed",
                "output_type": built.semantic_type if built else None,
                "source_type_or_format": metadata.source_formats.get(source, rule.validation.type),
                "source_variable_label": metadata.variable_labels.get(source, ""),
                "description": built.description if built else rule.description,
                "source_category_codes": compact_json(list(source_labels.keys())) if source_labels else "",
                "source_value_labels": compact_json(source_labels) if source_labels else "",
                "output_category_codes": compact_json(list((built.value_labels or {}).keys())) if built and built.value_labels else "",
                "output_value_labels": compact_json(built.value_labels) if built and built.value_labels else "",
                "source_missing_codes": compact_json(rule.missing_codes) if rule.missing_codes else "",
                "output_missing_codes": "blank CSV cell",
                "derivation_rule": built.derivation if built else "Removed from standard collaborator export",
                "calendar_or_behavioral_day_interpretation": built.temporal_interpretation if built else "",
                "timestamp_provenance": built.timestamp_provenance if built else "",
            }
        )
    for built in output_columns:
        if built.source is not None:
            continue
        rows.append(
            {
                "output_order": output_order[built.name],
                "output_variable": built.name,
                "source_order": None,
                "source_variable": None,
                "status": built.status,
                "output_type": built.semantic_type,
                "source_type_or_format": "",
                "source_variable_label": "",
                "description": built.description,
                "source_category_codes": "",
                "source_value_labels": "",
                "output_category_codes": compact_json(list((built.value_labels or {}).keys())) if built.value_labels else "",
                "output_value_labels": compact_json(built.value_labels) if built.value_labels else "",
                "source_missing_codes": "",
                "output_missing_codes": "blank CSV cell",
                "derivation_rule": built.derivation,
                "calendar_or_behavioral_day_interpretation": built.temporal_interpretation,
                "timestamp_provenance": built.timestamp_provenance,
            }
        )
    return rows


def _build_output(
    frame: pd.DataFrame,
    source_columns: list[str],
    schema: ToolSchema,
    key: KeyRecord,
    temporal: TemporalResults,
    metadata: SourceMetadata,
    validation_summary: ValidationSummary,
) -> tuple[pd.DataFrame, list[BuiltColumn], dict[str, Any]]:
    rule_map = {rule.source: rule for rule in schema.columns}
    replacements: dict[str, BuiltColumn] = {}
    token_summary: dict[str, int] = {}

    for rule in schema.columns:
        if rule.action == "tokenize":
            series = _tokenize(frame, schema, key, rule)
            replacements[rule.source] = BuiltColumn(
                name=rule.output or rule.source,
                series=series,
                source=rule.source,
                status="replaced",
                semantic_type="string",
                description=rule.description,
                derivation=f"Collaboration-scoped keyed token; domain {rule.token_domain}",
            )
            token_summary[rule.output or rule.source] = int(series.notna().sum())
        elif rule.action == "age_at_index":
            assert rule.index_event and rule.output
            series = _age_series(
                frame,
                rule.source,
                temporal.index_events[rule.index_event],
                required=rule.required,
                validation_summary=validation_summary,
            )
            replacements[rule.source] = BuiltColumn(
                name=rule.output,
                series=series,
                source=rule.source,
                status="replaced",
                semantic_type="string",
                description=rule.description or "Age in whole years at the study index event",
                derivation=f"Whole years at index event {rule.index_event}; ages 90 and older coded 90+",
            )

    for source, (name, series, provenance) in temporal.replacement_outputs.items():
        rule = rule_map[source]
        replacements[source] = BuiltColumn(
            name=name,
            series=series,
            source=source,
            status="replaced",
            semantic_type="offset" if provenance == "frozen_utc_offset" else "string",
            description=rule.description,
            derivation=(
                "Original timestamp shifted by the deterministic participant/dyad whole-week offset and shared "
                "event-anchor jitter; numeric UTC offset frozen from the original event"
            ),
            timestamp_provenance=provenance,
        )

    interval_cache: dict[tuple[str, str], pd.Series] = {}
    for check in schema.interval_checks:
        starts = _utc_series_for_source(frame, temporal, check.start_column)
        ends = _utc_series_for_source(frame, temporal, check.end_column)
        interval_cache[(check.start_column, check.end_column)] = validate_interval_series(
            check.name,
            starts,
            ends,
            minimum_seconds=check.min_seconds,
            maximum_seconds=check.max_seconds,
            summary=validation_summary,
        )

    built_columns: list[BuiltColumn] = []
    for source in source_columns:
        rule = rule_map[source]
        if source in replacements:
            built_columns.append(replacements[source])
        elif rule.action == "keep":
            labels = _value_labels(rule, metadata)
            built_columns.append(
                BuiltColumn(
                    name=source,
                    series=frame[source],
                    source=source,
                    status="retained",
                    semantic_type=rule.validation.type,
                    description=rule.description,
                    derivation="Retained unchanged except source missing-code normalization and CSV formula safety",
                    value_labels={str(key): str(value) for key, value in labels.items()} if labels else None,
                    output_format=rule.validation.type,
                )
            )
        elif rule.action in {"remove", "timestamp"}:
            continue
        else:
            raise TransformationError(f"No output implementation exists for column action {rule.action}.")

    for name, series, provenance, description in temporal.appended_outputs:
        built_columns.append(
            BuiltColumn(
                name=name,
                series=series,
                source=None,
                status="derived",
                semantic_type="offset" if provenance == "frozen_utc_offset" else "string",
                description=description,
                derivation="Synthetic timestamp component appended because no corresponding source component existed",
                timestamp_provenance=provenance,
            )
        )

    for item in schema.derived_columns:
        series, semantic, derivation, labels = _derive_temporal_column(
            item,
            frame,
            schema,
            temporal,
            interval_cache,
            validation_summary,
        )
        built_columns.append(
            BuiltColumn(
                name=item.name,
                series=series,
                source=None,
                status="derived",
                semantic_type=semantic,
                description=item.description,
                derivation=derivation,
                temporal_interpretation=derivation if item.kind in {
                    "weekday_weekend",
                    "behavioral_research_day",
                    "season",
                    "holiday_proximity",
                    "dst_status",
                    "dst_transition_proximity",
                } else "",
                value_labels=labels,
                timestamp_provenance=(
                    "original-derived" if item.kind not in {"age_at_index"} else ""
                ),
            )
        )

    names = [column.name for column in built_columns]
    if len(names) != len(set(names)):
        raise TransformationError("Output variable names are not unique after transformation.")
    output = pd.DataFrame({column.name: column.series for column in built_columns}, index=frame.index)
    semantic_types = {column.name: column.semantic_type for column in built_columns}
    output, formula_counts = escape_formula_like(output, semantic_types)
    return output, built_columns, {"tokens_generated": token_summary, "formula_escape_counts": formula_counts}


def _success_report(
    *,
    loaded_schema: LoadedSchema,
    loaded_data: LoadedData,
    output: pd.DataFrame,
    output_hash: str,
    dictionary_rows: int,
    temporal: TemporalResults,
    validation: ValidationSummary,
    small_cells: list[dict[str, Any]],
    build_summary: dict[str, Any],
) -> dict[str, Any]:
    schema = loaded_schema.model
    warnings: list[str] = []
    if validation.optional_columns_absent:
        warnings.append("One or more optional schema columns were absent and represented as blank values.")
    if small_cells:
        warnings.append("Demographic cells involving fewer than five participants require human release review.")
    return {
        "status": "success",
        "tool": {"name": "cellab-data-deidentification", "version": __version__},
        "schema": {
            "version": schema.schema_version,
            "sha256": loaded_schema.sha256,
        },
        "collaboration": {
            "id": schema.collaboration_id,
            "key_alias": schema.key_alias,
            "key_version": schema.key_version,
        },
        "input": {
            "format": loaded_data.path.suffix.lower(),
            "selected_excel_sheet": loaded_data.metadata.selected_excel_sheet,
            "rows": int(len(loaded_data.dataframe)),
            "columns": int(len(loaded_data.dataframe.columns)),
            "sha256": loaded_data.sha256,
        },
        "output": {
            "analytic_csv": {
                "filename": schema.output.analytic_csv,
                "rows": int(len(output)),
                "columns": int(len(output.columns)),
                "sha256": output_hash,
            },
            "data_dictionary": {
                "filename": schema.output.data_dictionary,
                "rows": dictionary_rows,
            },
            "qa_report": {"filename": schema.output.qa_report},
        },
        "invariants": {
            "one_input_row_per_output_row": len(output) == len(loaded_data.dataframe),
            "row_count_preserved": len(output) == len(loaded_data.dataframe),
            "row_order_preserved": output.index.equals(pd.RangeIndex(len(output))),
            "one_to_one_mapping_verified": len(output) == len(loaded_data.dataframe) and output.index.is_unique,
        },
        "validation": {
            "primary_key_checked": validation.primary_key_checked,
            "duplicate_key_row_count": validation.duplicate_key_count,
            "missing_codes_converted": validation.missing_codes_converted,
            "optional_invalid_values_blanked": validation.optional_invalid_blanked,
            "optional_columns_absent": validation.optional_columns_absent,
            "interval_checks": validation.interval_checks,
            "small_demographic_cells": small_cells,
        },
        "transformations": {
            **temporal.summary,
            "tokenization": build_summary["tokens_generated"],
            "csv_formula_safety": build_summary["formula_escape_counts"],
        },
        "warnings": warnings,
    }


def run_export(
    input_path: str | Path,
    schema_path: str | Path,
    key_path: str | Path,
    output_dir: str | Path,
    *,
    sheet: str | None = None,
    overwrite: bool = False,
    create_archive: bool = False,
    allow_unverified_key_permissions: bool = False,
) -> ExportResult:
    input_file = Path(input_path).expanduser()
    destination = Path(output_dir).expanduser().resolve()
    loaded_schema: LoadedSchema | None = None
    stage: Path | None = None
    try:
        loaded_schema = load_schema(schema_path)
        schema = loaded_schema.model
        prospective_outputs = [
            destination / schema.output.analytic_csv,
            destination / schema.output.data_dictionary,
            destination / schema.output.qa_report,
        ]
        if create_archive:
            prospective_outputs.append(destination / schema.output.release_archive)
        _validate_output_path_separation(
            input_path=input_file,
            schema_path=loaded_schema.path,
            key_path=Path(key_path),
            output_paths=prospective_outputs,
        )
        key = load_key(
            key_path,
            collaboration_id=schema.collaboration_id,
            key_alias=schema.key_alias,
            key_version=schema.key_version,
            allow_retired=False,
            allow_unverified_permissions=allow_unverified_key_permissions,
        )
        loaded = load_input(input_file, schema, sheet_override=sheet)
        source_columns = list(loaded.dataframe.columns)

        working, validation = normalize_missing_values(loaded.dataframe, schema)
        working = validate_source_columns(working, schema, validation)
        for column in working.columns:
            if column not in source_columns:
                source_columns.append(column)
        working = validate_values(working, schema, validation)
        validate_primary_key(working, schema, validation)
        temporal = process_temporal(working, schema, key, validation)
        small_cells = evaluate_small_cells(working, schema)
        output, built_columns, build_summary = _build_output(
            working,
            source_columns,
            schema,
            key,
            temporal,
            loaded.metadata,
            validation,
        )

        if len(output) != len(loaded.dataframe) or not output.index.equals(pd.RangeIndex(len(output))):
            raise TransformationError("The transformation failed to preserve one-to-one row mapping and row order.")

        dictionary_rows = _dictionary_rows(source_columns, schema, loaded.metadata, built_columns)
        dictionary = pd.DataFrame(dictionary_rows)

        destination.mkdir(parents=True, exist_ok=True)
        final_csv = destination / schema.output.analytic_csv
        final_dictionary = destination / schema.output.data_dictionary
        final_qa = destination / schema.output.qa_report
        final_archive = destination / schema.output.release_archive if create_archive else None
        targets = [final_csv, final_dictionary, final_qa] + ([final_archive] if final_archive else [])
        if not overwrite:
            existing = [path.name for path in targets if path is not None and path.exists()]
            if existing:
                raise OutputError("Refusing to overwrite existing output files: " + ", ".join(existing))

        stage = Path(tempfile.mkdtemp(prefix=".research-deid-", dir=destination))
        if os.name == "posix":
            stage.chmod(0o700)
        stage_csv = stage / schema.output.analytic_csv
        stage_dictionary = stage / schema.output.data_dictionary
        stage_qa = stage / schema.output.qa_report
        write_csv(stage_csv, output, overwrite=True)
        output_hash = sha256_file(stage_csv)
        write_csv(stage_dictionary, dictionary, overwrite=True)
        report = _success_report(
            loaded_schema=loaded_schema,
            loaded_data=loaded,
            output=output,
            output_hash=output_hash,
            dictionary_rows=len(dictionary_rows),
            temporal=temporal,
            validation=validation,
            small_cells=small_cells,
            build_summary=build_summary,
        )
        write_bytes(stage_qa, deterministic_json(report), overwrite=True)
        stage_archive: Path | None = None
        if create_archive:
            stage_archive = stage / schema.output.release_archive
            write_release_archive(
                stage_archive,
                [stage_csv, stage_dictionary, stage_qa],
                overwrite=True,
            )

        commit_pairs = [
            (stage_csv, final_csv),
            (stage_dictionary, final_dictionary),
            (stage_qa, final_qa),
        ]
        if stage_archive is not None and final_archive is not None:
            commit_pairs.append((stage_archive, final_archive))
        _commit_staged_outputs(commit_pairs, overwrite=overwrite)
        shutil.rmtree(stage, ignore_errors=True)
        stage = None
        return ExportResult(final_csv, final_dictionary, final_qa, final_archive)
    except Exception as error:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        if isinstance(error, DeidError):
            _write_failure_report(destination, loaded_schema, input_file, error, overwrite=overwrite)
            raise
        wrapped = TransformationError(f"Unexpected transformation failure: {error.__class__.__name__}.")
        _write_failure_report(destination, loaded_schema, input_file, wrapped, overwrite=overwrite)
        raise wrapped from error
