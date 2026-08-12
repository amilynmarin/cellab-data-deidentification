from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

import pandas as pd

from .crypto import canonical_value, is_missing
from .errors import DataValidationError
from .models import ColumnRule, SmallCellCheck, ToolSchema, ValueValidation


_NUMERIC_TEXT = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?$")
_OFFSET_TEXT = re.compile(r"^(?:Z|[+-](?:0\d|1[0-4]):[0-5]\d)$")


@dataclass
class ValidationSummary:
    missing_codes_converted: dict[str, int] = field(default_factory=dict)
    optional_invalid_blanked: dict[str, int] = field(default_factory=dict)
    optional_columns_absent: list[str] = field(default_factory=list)
    primary_key_checked: bool = False
    duplicate_key_count: int = 0
    interval_checks: list[dict[str, Any]] = field(default_factory=list)


def _row_summary(indices: Iterable[int], limit: int = 20) -> str:
    rows = sorted({int(index) + 2 for index in indices})
    shown = rows[:limit]
    suffix = f" and {len(rows) - limit} additional rows" if len(rows) > limit else ""
    return f"rows {shown}{suffix}"


def _same_value(left: Any, right: Any) -> bool:
    if is_missing(left) or is_missing(right):
        return is_missing(left) and is_missing(right)
    try:
        if canonical_value(left) == canonical_value(right):
            return True
    except ValueError:
        return False
    left_number = _to_number(left)
    right_number = _to_number(right)
    if left_number is not None and right_number is not None:
        return left_number == right_number
    return str(left) == str(right)


def normalize_missing_values(frame: pd.DataFrame, schema: ToolSchema) -> tuple[pd.DataFrame, ValidationSummary]:
    working = frame.copy(deep=True)
    summary = ValidationSummary()
    for column in working.columns:
        series = working[column]
        empty_mask = series.map(lambda value: isinstance(value, str) and value == "")
        if bool(empty_mask.any()):
            working.loc[empty_mask, column] = pd.NA
    for rule in schema.columns:
        if rule.source not in working.columns:
            continue
        if not rule.missing_codes:
            continue
        mask = working[rule.source].map(
            lambda value: False if is_missing(value) else any(_same_value(value, code) for code in rule.missing_codes)
        )
        count = int(mask.sum())
        if count:
            working.loc[mask, rule.source] = pd.NA
            summary.missing_codes_converted[rule.source] = count
    return working, summary


def validate_source_columns(frame: pd.DataFrame, schema: ToolSchema, summary: ValidationSummary) -> pd.DataFrame:
    working = frame.copy(deep=True)
    declared = {rule.source for rule in schema.columns}
    unlisted = [str(column) for column in working.columns if str(column) not in declared]
    if unlisted:
        raise DataValidationError(
            "Input contains columns not declared by the approved schema: " + ", ".join(sorted(unlisted))
        )
    for rule in schema.columns:
        if rule.source in working.columns:
            continue
        if rule.required:
            raise DataValidationError(f"Required input column is absent: {rule.source}")
        working[rule.source] = pd.NA
        summary.optional_columns_absent.append(rule.source)
    return working


def _to_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str) and _NUMERIC_TEXT.fullmatch(value):
        number = float(value)
        return number if math.isfinite(number) else None
    return None


def _is_integer(value: Any) -> bool:
    number = _to_number(value)
    return number is not None and number.is_integer()


def _is_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, int) and value in {0, 1}:
        return True
    if isinstance(value, str) and value.casefold() in {"true", "false", "0", "1"}:
        return True
    return False


def _is_date(value: Any, *, datetime_allowed: bool) -> bool:
    if isinstance(value, pd.Timestamp):
        return True
    if isinstance(value, datetime):
        return datetime_allowed
    if isinstance(value, date):
        return True
    try:
        parsed = pd.to_datetime(value, errors="raise")
    except Exception:
        return False
    if pd.isna(parsed):
        return False
    if datetime_allowed:
        return True
    if isinstance(value, str):
        return not bool(re.search(r"[T ]\d{1,2}:\d{2}", value))
    return True


def _valid_type(value: Any, kind: str) -> bool:
    if kind == "any":
        return True
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return _is_integer(value)
    if kind == "number":
        return _to_number(value) is not None
    if kind == "boolean":
        return _is_boolean(value)
    if kind == "date":
        return _is_date(value, datetime_allowed=False)
    if kind == "datetime":
        return _is_date(value, datetime_allowed=True)
    if kind == "offset":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return math.isfinite(float(value)) and abs(float(value)) <= 24 * 60
        return isinstance(value, str) and bool(_OFFSET_TEXT.fullmatch(value))
    return False


def _valid_constraints(value: Any, validation: ValueValidation) -> bool:
    if not _valid_type(value, validation.type):
        return False
    if validation.allowed_values is not None:
        if not any(_same_value(value, allowed) for allowed in validation.allowed_values):
            return False
    if validation.min_value is not None or validation.max_value is not None:
        number = _to_number(value)
        if number is None:
            return False
        if validation.min_value is not None and number < validation.min_value:
            return False
        if validation.max_value is not None and number > validation.max_value:
            return False
    if validation.regex is not None:
        if not isinstance(value, str) or re.fullmatch(validation.regex, value) is None:
            return False
    return True


def validate_values(frame: pd.DataFrame, schema: ToolSchema, summary: ValidationSummary) -> pd.DataFrame:
    working = frame.copy(deep=True)
    for rule in schema.columns:
        series = working[rule.source]
        missing_mask = series.map(is_missing)
        if rule.required and not rule.validation.nullable and bool(missing_mask.any()):
            indices = series.index[missing_mask]
            raise DataValidationError(
                f"Required values are missing in column {rule.source} at {_row_summary(indices)}."
            )
        invalid_mask = (~missing_mask) & (~series.map(lambda value: _valid_constraints(value, rule.validation)))
        if not bool(invalid_mask.any()):
            continue
        indices = series.index[invalid_mask]
        if rule.required:
            raise DataValidationError(
                f"Invalid required values were found in column {rule.source} at {_row_summary(indices)}."
            )
        count = int(invalid_mask.sum())
        working.loc[invalid_mask, rule.source] = pd.NA
        summary.optional_invalid_blanked[rule.source] = count
    return working


def validate_primary_key(frame: pd.DataFrame, schema: ToolSchema, summary: ValidationSummary) -> None:
    columns = schema.input.primary_key
    missing = frame[columns].apply(lambda column: column.map(is_missing)).any(axis=1)
    if bool(missing.any()):
        raise DataValidationError(
            "The declared primary/composite key contains missing values at "
            + _row_summary(frame.index[missing])
            + "."
        )
    canonical = frame[columns].apply(
        lambda row: tuple(canonical_value(value) for value in row), axis=1
    )
    duplicate_mask = canonical.duplicated(keep=False)
    summary.primary_key_checked = True
    summary.duplicate_key_count = int(duplicate_mask.sum())
    if bool(duplicate_mask.any()):
        raise DataValidationError(
            "The declared primary/composite key is not unique at "
            + _row_summary(frame.index[duplicate_mask])
            + ". The program does not deduplicate records."
        )


def validate_interval_series(
    name: str,
    starts: pd.Series,
    ends: pd.Series,
    *,
    minimum_seconds: float,
    maximum_seconds: float | None,
    summary: ValidationSummary,
) -> pd.Series:
    durations: list[float | None] = []
    invalid_indices: list[int] = []
    for index, (start, end) in enumerate(zip(starts, ends, strict=True)):
        if start is None or end is None or is_missing(start) or is_missing(end):
            durations.append(None)
            continue
        seconds = (end - start).total_seconds()
        if seconds < minimum_seconds or (maximum_seconds is not None and seconds > maximum_seconds):
            invalid_indices.append(index)
        durations.append(seconds)
    summary.interval_checks.append(
        {
            "name": name,
            "minimum_seconds": minimum_seconds,
            "maximum_seconds": maximum_seconds,
            "invalid_count": len(invalid_indices),
        }
    )
    if invalid_indices:
        raise DataValidationError(
            f"Impossible or disallowed source-derived interval for check {name} at "
            + _row_summary(invalid_indices)
            + "."
        )
    return pd.Series(durations, index=starts.index, dtype="Float64")


def _participant_keys(frame: pd.DataFrame, schema: ToolSchema) -> pd.Series:
    sources = schema.shift.participant_sources
    def build(row: pd.Series) -> tuple[str, ...]:
        values: list[str] = []
        for source in sources:
            value = row[source.column]
            if is_missing(value):
                raise DataValidationError("Participant identity values required for small-cell review are missing.")
            namespace = schema.study_namespace if source.namespace == "study" else "global"
            values.extend([namespace, canonical_value(value)])
        return tuple(values)
    return frame.apply(build, axis=1)


def evaluate_small_cells(frame: pd.DataFrame, schema: ToolSchema) -> list[dict[str, Any]]:
    if not schema.small_cell_checks:
        return []
    participant_keys = _participant_keys(frame, schema)
    flags: list[dict[str, Any]] = []
    for check in schema.small_cell_checks:
        work = frame[check.columns].copy()
        work["__participant_key"] = participant_keys
        if check.require_stable_within_participant:
            for column in check.columns:
                distinct = work.groupby("__participant_key", dropna=False)[column].nunique(dropna=False)
                if bool((distinct > 1).any()):
                    raise DataValidationError(
                        f"Small-cell check {check.name} requires participant-stable demographics, but {column} varies within participant."
                    )
        participant_rows = work.drop_duplicates("__participant_key")
        if not check.include_missing:
            nonmissing = ~participant_rows[check.columns].apply(lambda column: column.map(is_missing)).any(axis=1)
            participant_rows = participant_rows.loc[nonmissing]
        grouped = participant_rows.groupby(check.columns, dropna=False)["__participant_key"].nunique()
        for cell, count in grouped.items():
            if int(count) >= check.threshold:
                continue
            values = cell if isinstance(cell, tuple) else (cell,)
            flags.append(
                {
                    "check": check.name,
                    "columns": check.columns,
                    "cell": {
                        column: (
                            None
                            if is_missing(value)
                            else (value.item() if hasattr(value, "item") else value)
                        )
                        for column, value in zip(check.columns, values, strict=True)
                    },
                    "participant_count": int(count),
                    "threshold": check.threshold,
                }
            )
    return flags
