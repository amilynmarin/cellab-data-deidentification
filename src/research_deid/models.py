from __future__ import annotations

import re
from datetime import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .version import SCHEMA_FORMAT_VERSION


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ValueValidation(StrictModel):
    type: Literal["any", "string", "integer", "number", "boolean", "date", "datetime", "offset"] = "any"
    nullable: bool = False
    min_value: float | None = None
    max_value: float | None = None
    allowed_values: list[Any] | None = None
    regex: str | None = None

    @field_validator("regex")
    @classmethod
    def valid_regex(cls, value: str | None) -> str | None:
        if value is not None:
            re.compile(value)
        return value


class TokenSource(StrictModel):
    column: str
    namespace: Literal["global", "study"] = "study"


class ColumnRule(StrictModel):
    source: str
    action: Literal["keep", "remove", "tokenize", "timestamp", "age_at_index"]
    output: str | None = None
    required: bool = True
    description: str = ""
    role: str | None = None
    sensitivity: Literal["none", "direct_identifier", "free_text", "geography"] = "none"
    validation: ValueValidation = Field(default_factory=ValueValidation)
    missing_codes: list[Any] = Field(default_factory=list)
    value_labels: dict[str, str] = Field(default_factory=dict)
    token_domain: str | None = None
    token_prefix: str = "id"
    token_sources: list[TokenSource] = Field(default_factory=list)
    token_context: list[str] = Field(default_factory=list)
    index_event: str | None = None

    @model_validator(mode="after")
    def validate_action(self) -> "ColumnRule":
        if self.action == "keep":
            if self.output not in (None, self.source):
                raise ValueError("A retained source column must preserve its source name")
            if self.sensitivity in {"direct_identifier", "free_text"}:
                raise ValueError(f"{self.sensitivity} columns cannot be retained in the standard export")
            if self.sensitivity == "geography" and self.role != "site_code":
                raise ValueError("Only coded study-site geography may be retained")
        elif self.action in {"tokenize", "age_at_index"}:
            if not self.output:
                raise ValueError(f"action '{self.action}' requires an output name")
        elif self.action == "timestamp" and self.output is not None:
            raise ValueError("Timestamp output names belong in timestamp_groups, not column rules")
        elif self.action == "remove" and self.output is not None:
            raise ValueError("Removed columns cannot define an output name")

        if self.action == "tokenize":
            if not self.token_domain:
                raise ValueError("Tokenized columns require token_domain")
            if not self.token_sources:
                self.token_sources = [TokenSource(column=self.source, namespace="study")]
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,15}", self.token_prefix):
                raise ValueError("token_prefix must be 1-16 safe ASCII characters and begin with a letter")
        elif self.token_sources or self.token_context or self.token_domain:
            raise ValueError("Token settings are valid only for action 'tokenize'")

        if self.action == "age_at_index" and not self.index_event:
            raise ValueError("age_at_index requires index_event")
        return self


class TimezoneRule(StrictModel):
    fixed: str | None = None
    column: str | None = None
    mapping_column: str | None = None
    mapping: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def exactly_one_resolver(self) -> "TimezoneRule":
        selected = int(self.fixed is not None) + int(self.column is not None) + int(self.mapping_column is not None)
        if selected != 1:
            raise ValueError("Timezone rule requires exactly one of fixed, column, or mapping_column")
        if self.mapping_column and not self.mapping:
            raise ValueError("mapping_column requires a nonempty mapping")
        if not self.mapping_column and self.mapping:
            raise ValueError("mapping is valid only with mapping_column")
        return self


class TimestampField(StrictModel):
    name: str
    local_source: str | None = None
    utc_source: str | None = None
    offset_source: str | None = None
    output_local: str
    output_utc: str
    output_offset: str
    offset_numeric_unit: Literal["auto", "hours", "minutes"] = "auto"
    consistency_tolerance_seconds: int = Field(default=2, ge=0, le=60)

    @model_validator(mode="after")
    def has_source(self) -> "TimestampField":
        if not self.local_source and not self.utc_source:
            raise ValueError("Timestamp field requires local_source or utc_source")
        outputs = [self.output_local, self.output_utc, self.output_offset]
        if len(set(outputs)) != len(outputs):
            raise ValueError("Timestamp output names must be distinct")
        return self


class RareEventRule(StrictModel):
    flag_column: str
    values: list[Any]
    policy: Literal["research_day_only", "approved_exact"] = "research_day_only"
    approval_reference: str | None = None
    suppress_calendar_context: bool = True

    @model_validator(mode="after")
    def require_approval(self) -> "RareEventRule":
        if not self.values:
            raise ValueError("Rare-event values cannot be empty")
        if self.policy == "approved_exact" and not self.approval_reference:
            raise ValueError("approved_exact rare-event timing requires approval_reference")
        if self.policy == "research_day_only" and not self.suppress_calendar_context:
            raise ValueError(
                "research_day_only rare events must suppress all calendar context except the behavioral research day"
            )
        return self


class TimestampGroup(StrictModel):
    name: str
    anchor_columns: list[str]
    fields: list[TimestampField]
    reference_field: str
    timezone: TimezoneRule | None = None
    rare_event: RareEventRule | None = None

    @model_validator(mode="after")
    def validate_group(self) -> "TimestampGroup":
        if not self.anchor_columns:
            raise ValueError("Timestamp groups require at least one anchor column")
        names = [field.name for field in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("Timestamp field names must be unique within a group")
        if self.reference_field not in names:
            raise ValueError("reference_field must name one field in the group")
        return self


class ShiftRule(StrictModel):
    participant_sources: list[TokenSource]
    dyad_sources: list[TokenSource] = Field(default_factory=list)
    min_weeks: int = Field(default=4, ge=1)
    max_weeks: int = Field(default=52, ge=1)
    behavioral_day_boundary: str = "04:00"

    @model_validator(mode="after")
    def validate_shift(self) -> "ShiftRule":
        if not self.participant_sources:
            raise ValueError("At least one participant identity source is required")
        if self.min_weeks != 4 or self.max_weeks != 52:
            raise ValueError("v0.1 fixes whole-week shifts at a nonzero magnitude of 4 through 52 weeks")
        parse_boundary(self.behavioral_day_boundary)
        return self


class IndexEvent(StrictModel):
    name: str
    source_column: str
    kind: Literal["date", "local_datetime", "utc_datetime"] = "date"
    group_by_columns: list[str] = Field(default_factory=list)
    timezone: TimezoneRule | None = None
    offset_source: str | None = None
    required: bool = True

    @model_validator(mode="after")
    def validate_index(self) -> "IndexEvent":
        if self.kind == "utc_datetime" and self.timezone is None:
            raise ValueError("UTC index events require a timezone rule to derive a local research date")
        return self


class DerivedColumn(StrictModel):
    name: str
    kind: Literal[
        "elapsed_seconds",
        "weekday_weekend",
        "behavioral_research_day",
        "season",
        "holiday_proximity",
        "dst_status",
        "dst_transition_proximity",
        "age_at_index",
    ]
    description: str = ""
    timestamp_group: str | None = None
    timestamp_field: str | None = None
    index_event: str | None = None
    birth_date_column: str | None = None
    start_column: str | None = None
    end_column: str | None = None

    @model_validator(mode="after")
    def validate_derived(self) -> "DerivedColumn":
        temporal = {
            "weekday_weekend",
            "behavioral_research_day",
            "season",
            "holiday_proximity",
            "dst_status",
            "dst_transition_proximity",
        }
        if self.kind in temporal and not self.timestamp_group:
            raise ValueError(f"{self.kind} requires timestamp_group")
        if self.kind == "behavioral_research_day" and not self.index_event:
            raise ValueError("behavioral_research_day requires index_event")
        if self.kind == "elapsed_seconds" and (not self.start_column or not self.end_column):
            raise ValueError("elapsed_seconds requires start_column and end_column")
        if self.kind == "age_at_index" and (not self.birth_date_column or not self.index_event):
            raise ValueError("age_at_index requires birth_date_column and index_event")
        return self


class IntervalCheck(StrictModel):
    name: str
    start_column: str
    end_column: str
    min_seconds: float = 0.0
    max_seconds: float | None = None

    @model_validator(mode="after")
    def validate_interval(self) -> "IntervalCheck":
        if self.max_seconds is not None and self.max_seconds < self.min_seconds:
            raise ValueError("max_seconds must be at least min_seconds")
        return self


class SmallCellCheck(StrictModel):
    name: str
    columns: list[str]
    threshold: int = Field(default=5, ge=2)
    include_missing: bool = False
    require_stable_within_participant: bool = True

    @model_validator(mode="after")
    def nonempty_columns(self) -> "SmallCellCheck":
        if not self.columns:
            raise ValueError("Small-cell checks require at least one demographic column")
        if self.threshold != 5:
            raise ValueError("v0.1 fixes the demographic small-cell review threshold at fewer than five participants")
        return self


class HolidayRule(StrictModel):
    mode: Literal["us_federal", "explicit"] = "us_federal"
    dates: list[str] = Field(default_factory=list)
    additional_dates: list[str] = Field(default_factory=list)
    excluded_dates: list[str] = Field(default_factory=list)
    window_days: int = Field(default=7, ge=0, le=31)

    @model_validator(mode="after")
    def validate_holidays(self) -> "HolidayRule":
        if self.mode == "explicit" and not self.dates:
            raise ValueError("Explicit holiday mode requires dates")
        if self.window_days != 7:
            raise ValueError("v0.1 fixes holiday proximity at plus or minus seven days")
        return self


class InputRule(StrictModel):
    primary_key: list[str]
    excel_sheet: str | None = None
    unlisted_columns: Literal["error"] = "error"

    @model_validator(mode="after")
    def primary_key_required(self) -> "InputRule":
        if not self.primary_key:
            raise ValueError("A primary or composite key is required")
        return self


class OutputRule(StrictModel):
    analytic_csv: str = "deidentified.csv"
    data_dictionary: str = "data_dictionary.csv"
    qa_report: str = "qa_report.json"
    release_archive: str = "release.zip"

    @field_validator("analytic_csv", "data_dictionary", "qa_report", "release_archive")
    @classmethod
    def basename_only(cls, value: str) -> str:
        if not value or "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError("Output names must be plain basenames")
        return value

    @model_validator(mode="after")
    def validate_output_names(self) -> "OutputRule":
        names = [self.analytic_csv, self.data_dictionary, self.qa_report, self.release_archive]
        if len(names) != len(set(names)):
            raise ValueError("Release output filenames must be distinct")
        expected_suffixes = {
            "analytic_csv": ".csv",
            "data_dictionary": ".csv",
            "qa_report": ".json",
            "release_archive": ".zip",
        }
        for field_name, expected in expected_suffixes.items():
            value = getattr(self, field_name)
            if not value.lower().endswith(expected):
                raise ValueError(f"{field_name} must use the {expected} extension")
        return self


class ToolSchema(StrictModel):
    format_version: Literal[SCHEMA_FORMAT_VERSION] = SCHEMA_FORMAT_VERSION
    schema_version: str
    study_namespace: str
    collaboration_id: str
    key_alias: str
    key_version: str
    input: InputRule
    columns: list[ColumnRule]
    shift: ShiftRule
    timestamp_groups: list[TimestampGroup] = Field(default_factory=list)
    index_events: list[IndexEvent] = Field(default_factory=list)
    derived_columns: list[DerivedColumn] = Field(default_factory=list)
    interval_checks: list[IntervalCheck] = Field(default_factory=list)
    small_cell_checks: list[SmallCellCheck] = Field(default_factory=list)
    holidays: HolidayRule = Field(default_factory=HolidayRule)
    output: OutputRule = Field(default_factory=OutputRule)

    @field_validator("schema_version", "study_namespace", "collaboration_id", "key_alias", "key_version")
    @classmethod
    def nonempty_metadata(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Schema identifiers and versions must not be blank")
        return value

    @model_validator(mode="after")
    def cross_validate(self) -> "ToolSchema":
        if not self.columns:
            raise ValueError("Schema must define every source column")
        source_rules = {rule.source: rule for rule in self.columns}
        if len(source_rules) != len(self.columns):
            raise ValueError("Source columns may appear only once in column rules")

        def require_source(column: str, context: str) -> None:
            if column not in source_rules:
                raise ValueError(f"{context} references '{column}', which lacks a column rule")

        for column in self.input.primary_key:
            require_source(column, "primary_key")
        for source in self.shift.participant_sources + self.shift.dyad_sources:
            require_source(source.column, "shift identity")

        index_names = {event.name for event in self.index_events}
        if len(index_names) != len(self.index_events):
            raise ValueError("Index-event names must be unique")
        for event in self.index_events:
            require_source(event.source_column, f"index event '{event.name}'")
            for column in event.group_by_columns:
                require_source(column, f"index event '{event.name}'")
            if event.offset_source:
                require_source(event.offset_source, f"index event '{event.name}'")
            validate_timezone_dependencies(event.timezone, source_rules, f"index event '{event.name}'")

        group_names = {group.name for group in self.timestamp_groups}
        group_by_name = {group.name: group for group in self.timestamp_groups}
        if len(group_names) != len(self.timestamp_groups):
            raise ValueError("Timestamp-group names must be unique")
        timestamp_output_names: list[str] = []
        timestamp_sources: set[str] = set()
        for group in self.timestamp_groups:
            for column in group.anchor_columns:
                require_source(column, f"timestamp group '{group.name}' anchor")
            validate_timezone_dependencies(group.timezone, source_rules, f"timestamp group '{group.name}'")
            if group.rare_event:
                require_source(group.rare_event.flag_column, f"timestamp group '{group.name}' rare-event rule")
            for field in group.fields:
                for source in (field.local_source, field.utc_source, field.offset_source):
                    if source:
                        require_source(source, f"timestamp group '{group.name}'")
                        timestamp_sources.add(source)
                        if source_rules[source].action != "timestamp":
                            raise ValueError(f"Timestamp source '{source}' must use action 'timestamp'")
                timestamp_output_names.extend([field.output_local, field.output_utc, field.output_offset])

        timestamp_source_list = [
            source
            for group in self.timestamp_groups
            for field in group.fields
            for source in (field.local_source, field.utc_source, field.offset_source)
            if source
        ]
        repeated_timestamp_sources = sorted(
            {source for source in timestamp_source_list if timestamp_source_list.count(source) > 1}
        )
        if repeated_timestamp_sources:
            raise ValueError(
                "Each timestamp source column may belong to only one timestamp field; duplicates: "
                f"{repeated_timestamp_sources}"
            )

        for rule in self.columns:
            if rule.action == "timestamp" and rule.source not in timestamp_sources:
                raise ValueError(f"Timestamp action for '{rule.source}' is not used by a timestamp group")
            for source in rule.token_sources:
                require_source(source.column, f"token rule '{rule.source}'")
            for column in rule.token_context:
                require_source(column, f"token rule '{rule.source}' context")
            if rule.index_event and rule.index_event not in index_names:
                raise ValueError(f"Column '{rule.source}' references unknown index event '{rule.index_event}'")

        derived_names = [column.name for column in self.derived_columns]
        if len(derived_names) != len(set(derived_names)):
            raise ValueError("Derived output names must be unique")
        for derived in self.derived_columns:
            if derived.timestamp_group and derived.timestamp_group not in group_names:
                raise ValueError(f"Derived column '{derived.name}' references an unknown timestamp group")
            if derived.timestamp_group:
                group = group_by_name[derived.timestamp_group]
                field_name = derived.timestamp_field or group.reference_field
                if field_name not in {field.name for field in group.fields}:
                    raise ValueError(
                        f"Derived column '{derived.name}' references unknown timestamp field '{field_name}'"
                    )
                if derived.kind in {"dst_status", "dst_transition_proximity"} and group.timezone is None:
                    raise ValueError(
                        f"Derived column '{derived.name}' requires a named time-zone rule for timestamp group "
                        f"'{group.name}'"
                    )
            if derived.index_event and derived.index_event not in index_names:
                raise ValueError(f"Derived column '{derived.name}' references an unknown index event")
            for column in (derived.birth_date_column, derived.start_column, derived.end_column):
                if column:
                    require_source(column, f"derived column '{derived.name}'")

        for check in self.interval_checks:
            require_source(check.start_column, f"interval check '{check.name}'")
            require_source(check.end_column, f"interval check '{check.name}'")
        for check in self.small_cell_checks:
            for column in check.columns:
                require_source(column, f"small-cell check '{check.name}'")

        direct_output_names: list[str] = []
        for rule in self.columns:
            if rule.action == "keep":
                direct_output_names.append(rule.source)
            elif rule.action in {"tokenize", "age_at_index"}:
                direct_output_names.append(rule.output or "")
        all_outputs = direct_output_names + timestamp_output_names + derived_names
        if len(all_outputs) != len(set(all_outputs)):
            duplicates = sorted({name for name in all_outputs if all_outputs.count(name) > 1})
            raise ValueError(f"Output variable names must be unique; duplicates: {duplicates}")

        output_files = [
            self.output.analytic_csv,
            self.output.data_dictionary,
            self.output.qa_report,
            self.output.release_archive,
        ]
        if len(output_files) != len(set(output_files)):
            duplicates = sorted({name for name in output_files if output_files.count(name) > 1})
            raise ValueError(f"Output filenames must be unique; duplicates: {duplicates}")

        for group in self.timestamp_groups:
            if group.rare_event and group.rare_event.policy == "research_day_only":
                has_research_day = any(
                    derived.kind == "behavioral_research_day" and derived.timestamp_group == group.name
                    for derived in self.derived_columns
                )
                if not has_research_day:
                    raise ValueError(
                        f"Timestamp group '{group.name}' uses research_day_only but has no behavioral_research_day output"
                    )
                rare_sources = {
                    source
                    for field in group.fields
                    for source in (field.local_source, field.utc_source, field.offset_source)
                    if source
                }
                leaking_intervals = [
                    derived.name
                    for derived in self.derived_columns
                    if derived.kind == "elapsed_seconds"
                    and (derived.start_column in rare_sources or derived.end_column in rare_sources)
                ]
                if leaking_intervals:
                    raise ValueError(
                        f"Timestamp group '{group.name}' is research_day_only, but exact elapsed-time outputs "
                        f"reference its source fields: {sorted(leaking_intervals)}"
                    )
        return self


def validate_timezone_dependencies(
    timezone: TimezoneRule | None, source_rules: dict[str, ColumnRule], context: str
) -> None:
    if timezone is None:
        return
    column = timezone.column or timezone.mapping_column
    if column and column not in source_rules:
        raise ValueError(f"{context} timezone references '{column}', which lacks a column rule")
    if timezone.column and source_rules[timezone.column].action == "keep":
        raise ValueError(f"{context} uses a named time-zone column that cannot be released in the standard export")


def parse_boundary(value: str) -> time:
    match = re.fullmatch(r"(\d{2}):(\d{2})", value)
    if not match:
        raise ValueError("Behavioral-day boundary must use HH:MM")
    hour, minute = map(int, match.groups())
    if hour > 23 or minute > 59:
        raise ValueError("Behavioral-day boundary is outside the valid clock range")
    return time(hour=hour, minute=minute)
