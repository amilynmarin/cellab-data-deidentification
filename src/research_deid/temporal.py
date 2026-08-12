from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

from .calendars import behavioral_date
from .crypto import (
    canonical_value,
    derivation_context,
    derive_shift_weeks,
    is_missing,
    jitter_candidates,
)
from .errors import DataValidationError, TransformationError
from .keys import KeyRecord
from .models import IndexEvent, TimestampField, TimestampGroup, TimezoneRule, ToolSchema, parse_boundary
from .validation import ValidationSummary


_OFFSET_PATTERN = re.compile(r"^([+-])(\d{2}):(\d{2})$")


@dataclass(frozen=True)
class Moment:
    local: datetime
    utc: datetime
    offset_minutes: int
    timezone_name: str | None
    local_aware: datetime


@dataclass(frozen=True)
class IndexValue:
    local_date: date
    moment: Moment | None = None


@dataclass
class TimestampGroupResult:
    spec: TimestampGroup
    moments: dict[str, pd.Series]
    synthetic: dict[str, dict[str, pd.Series]]
    rare_suppressed: pd.Series
    rare_approved: pd.Series
    jitter_seconds: pd.Series


@dataclass
class TemporalResults:
    shift_weeks: pd.Series
    shift_scope: pd.Series
    groups: dict[str, TimestampGroupResult]
    index_events: dict[str, pd.Series]
    source_moments: dict[str, pd.Series]
    replacement_outputs: dict[str, tuple[str, pd.Series, str]]
    appended_outputs: list[tuple[str, pd.Series, str, str]]
    summary: dict[str, Any] = field(default_factory=dict)

    def moment_series(self, group_name: str, field_name: str | None = None) -> pd.Series:
        group = self.groups[group_name]
        chosen = field_name or group.spec.reference_field
        return group.moments[chosen]


def _safe_rows(indices: Iterable[int], limit: int = 20) -> str:
    rows = sorted({int(index) + 2 for index in indices})
    shown = rows[:limit]
    suffix = f" and {len(rows) - limit} additional rows" if len(rows) > limit else ""
    return f"rows {shown}{suffix}"


def parse_offset(value: Any, unit: str = "auto") -> int:
    if is_missing(value):
        raise ValueError("offset is missing")
    if isinstance(value, str):
        text = value.strip()
        if text == "Z":
            return 0
        match = _OFFSET_PATTERN.fullmatch(text)
        if match:
            sign, hours_text, minutes_text = match.groups()
            hours = int(hours_text)
            minutes = int(minutes_text)
            if hours > 14 or minutes > 59 or (hours == 14 and minutes != 0):
                raise ValueError("offset is outside the supported range")
            total = hours * 60 + minutes
            return -total if sign == "-" else total
        try:
            numeric = float(text)
        except ValueError as exc:
            raise ValueError("offset is invalid") from exc
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
    else:
        raise ValueError("offset is invalid")
    if not math.isfinite(numeric):
        raise ValueError("offset is not finite")
    if unit == "hours" or (unit == "auto" and abs(numeric) <= 24):
        minutes_value = numeric * 60
    else:
        minutes_value = numeric
    rounded = round(minutes_value)
    if abs(minutes_value - rounded) > 1e-6 or abs(rounded) > 14 * 60:
        raise ValueError("offset must resolve to whole minutes within plus or minus 14 hours")
    return int(rounded)


def format_offset(minutes: int) -> str:
    sign = "+" if minutes >= 0 else "-"
    absolute = abs(minutes)
    return f"{sign}{absolute // 60:02d}:{absolute % 60:02d}"


def _parse_datetime(value: Any, *, utc: bool) -> datetime:
    parsed = pd.to_datetime(value, errors="raise", utc=utc)
    if isinstance(parsed, pd.DatetimeIndex):
        raise ValueError("timestamp is not scalar")
    stamp = pd.Timestamp(parsed)
    if pd.isna(stamp):
        raise ValueError("timestamp is missing")
    result = stamp.to_pydatetime()
    if utc:
        return result.astimezone(timezone.utc)
    return result


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("time-zone name is invalid") from exc


def resolve_timezone(rule: TimezoneRule | None, row: pd.Series) -> str | None:
    if rule is None:
        return None
    if rule.fixed is not None:
        name = rule.fixed
    elif rule.column is not None:
        value = row[rule.column]
        if is_missing(value):
            raise ValueError("time-zone value is missing")
        name = str(value)
    else:
        value = row[rule.mapping_column or ""]
        if is_missing(value):
            raise ValueError("time-zone mapping value is missing")
        key = str(value)
        if key not in rule.mapping:
            raise ValueError("time-zone mapping value is not configured")
        name = rule.mapping[key]
    _zone(name)
    return name


def _localize_naive(local: datetime, zone_name: str, preferred_offset: int | None) -> datetime:
    zone = _zone(zone_name)
    candidates: list[datetime] = []
    seen_utc: set[datetime] = set()
    for fold in (0, 1):
        aware = local.replace(tzinfo=zone, fold=fold)
        roundtrip = aware.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
        if roundtrip != local:
            continue
        utc_value = aware.astimezone(timezone.utc)
        if utc_value in seen_utc:
            continue
        seen_utc.add(utc_value)
        candidates.append(aware)
    if preferred_offset is not None:
        candidates = [
            candidate
            for candidate in candidates
            if int((candidate.utcoffset() or timedelta(0)).total_seconds() // 60) == preferred_offset
        ]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError("local timestamp is nonexistent or inconsistent with its offset/time zone")
    raise ValueError("local timestamp is ambiguous without a matching UTC value or numeric offset")


def parse_moment(
    *,
    local_value: Any,
    utc_value: Any,
    offset_value: Any,
    timezone_name: str | None,
    offset_unit: str = "auto",
    tolerance_seconds: int = 2,
) -> Moment | None:
    local_missing = is_missing(local_value)
    utc_missing = is_missing(utc_value)
    offset_missing = is_missing(offset_value)
    if local_missing and utc_missing:
        return None

    source_offset = None if offset_missing else parse_offset(offset_value, offset_unit)
    local_parsed: datetime | None = None
    local_embedded_offset: int | None = None
    local_embedded_utc: datetime | None = None
    if not local_missing:
        local_parsed = _parse_datetime(local_value, utc=False)
        if local_parsed.tzinfo is not None:
            local_embedded_offset = int((local_parsed.utcoffset() or timedelta(0)).total_seconds() // 60)
            local_embedded_utc = local_parsed.astimezone(timezone.utc)
            local_parsed = local_parsed.replace(tzinfo=None)

    utc_parsed = None if utc_missing else _parse_datetime(utc_value, utc=True)
    preferred_offset = source_offset if source_offset is not None else local_embedded_offset

    if utc_parsed is not None and timezone_name is not None:
        aware_from_utc = utc_parsed.astimezone(_zone(timezone_name))
        local_from_utc = aware_from_utc.replace(tzinfo=None)
        offset_from_utc = int((aware_from_utc.utcoffset() or timedelta(0)).total_seconds() // 60)
        if local_parsed is not None and abs((local_from_utc - local_parsed).total_seconds()) > tolerance_seconds:
            raise ValueError("local and UTC timestamp values are inconsistent")
        if preferred_offset is not None and preferred_offset != offset_from_utc:
            raise ValueError("numeric offset is inconsistent with local/UTC timestamp and time zone")
        return Moment(
            local=local_from_utc if local_parsed is None else local_parsed,
            utc=utc_parsed,
            offset_minutes=offset_from_utc,
            timezone_name=timezone_name,
            local_aware=aware_from_utc,
        )

    if utc_parsed is not None and local_parsed is not None:
        inferred_seconds = (local_parsed - utc_parsed.replace(tzinfo=None)).total_seconds()
        inferred_minutes = round(inferred_seconds / 60)
        if abs(inferred_seconds - inferred_minutes * 60) > tolerance_seconds or abs(inferred_minutes) > 14 * 60:
            raise ValueError("local and UTC timestamp values imply an invalid offset")
        if preferred_offset is not None and inferred_minutes != preferred_offset:
            raise ValueError("numeric offset is inconsistent with local and UTC timestamp values")
        fixed = timezone(timedelta(minutes=inferred_minutes))
        return Moment(
            local=local_parsed,
            utc=utc_parsed,
            offset_minutes=inferred_minutes,
            timezone_name=None,
            local_aware=local_parsed.replace(tzinfo=fixed),
        )

    if utc_parsed is not None:
        if source_offset is None:
            raise ValueError("a UTC-only timestamp requires a named time zone or numeric UTC offset")
        local = utc_parsed.replace(tzinfo=None) + timedelta(minutes=source_offset)
        fixed = timezone(timedelta(minutes=source_offset))
        return Moment(
            local=local,
            utc=utc_parsed,
            offset_minutes=source_offset,
            timezone_name=None,
            local_aware=local.replace(tzinfo=fixed),
        )

    assert local_parsed is not None
    if local_embedded_utc is not None:
        if source_offset is not None and source_offset != local_embedded_offset:
            raise ValueError("embedded and separate numeric offsets are inconsistent")
        if timezone_name is not None:
            aware = local_embedded_utc.astimezone(_zone(timezone_name))
            if abs((aware.replace(tzinfo=None) - local_parsed).total_seconds()) > tolerance_seconds:
                raise ValueError("embedded offset is inconsistent with the named time zone")
            return Moment(
                local=local_parsed,
                utc=local_embedded_utc,
                offset_minutes=local_embedded_offset or 0,
                timezone_name=timezone_name,
                local_aware=aware,
            )
        fixed = timezone(timedelta(minutes=local_embedded_offset or 0))
        return Moment(
            local=local_parsed,
            utc=local_embedded_utc,
            offset_minutes=local_embedded_offset or 0,
            timezone_name=None,
            local_aware=local_parsed.replace(tzinfo=fixed),
        )

    if timezone_name is not None:
        aware = _localize_naive(local_parsed, timezone_name, source_offset)
        offset = int((aware.utcoffset() or timedelta(0)).total_seconds() // 60)
        return Moment(
            local=local_parsed,
            utc=aware.astimezone(timezone.utc),
            offset_minutes=offset,
            timezone_name=timezone_name,
            local_aware=aware,
        )
    if source_offset is not None:
        fixed = timezone(timedelta(minutes=source_offset))
        aware = local_parsed.replace(tzinfo=fixed)
        return Moment(
            local=local_parsed,
            utc=aware.astimezone(timezone.utc),
            offset_minutes=source_offset,
            timezone_name=None,
            local_aware=aware,
        )
    raise ValueError("a local-only timestamp requires a named time zone or numeric UTC offset")


def _identity_components(frame: pd.DataFrame, schema: ToolSchema) -> tuple[pd.Series, pd.Series]:
    scopes: list[tuple[str, tuple[str, ...]]] = []
    shifts: list[int] = []
    # The caller replaces the placeholder shifts after a key and derivation context are available.
    for _, row in frame.iterrows():
        dyad_available = bool(schema.shift.dyad_sources) and all(
            not is_missing(row[source.column]) for source in schema.shift.dyad_sources
        )
        sources = schema.shift.dyad_sources if dyad_available else schema.shift.participant_sources
        scope = "dyad" if dyad_available else "participant"
        components: list[str] = []
        for source in sources:
            value = row[source.column]
            if is_missing(value):
                raise DataValidationError("A participant identity value required for deterministic date shifting is missing.")
            namespace = schema.study_namespace if source.namespace == "study" else "global"
            components.extend([namespace, canonical_value(value)])
        scopes.append((scope, tuple(components)))
        shifts.append(0)
    return pd.Series(scopes, index=frame.index, dtype="object"), pd.Series(shifts, index=frame.index, dtype="Int64")


def _shift_series(frame: pd.DataFrame, schema: ToolSchema, key: KeyRecord) -> tuple[pd.Series, pd.Series]:
    scope_series, shifts = _identity_components(frame, schema)
    participant_scopes: dict[tuple[str, ...], set[Any]] = {}
    for index, row in frame.iterrows():
        participant: list[str] = []
        for source in schema.shift.participant_sources:
            value = row[source.column]
            if is_missing(value):
                raise DataValidationError("A participant identity value required for deterministic date shifting is missing.")
            namespace = schema.study_namespace if source.namespace == "study" else "global"
            participant.extend([namespace, canonical_value(value)])
        participant_scopes.setdefault(tuple(participant), set()).add(scope_series.at[index])
    if any(len(scopes) > 1 for scopes in participant_scopes.values()):
        raise DataValidationError(
            "One or more participants are assigned inconsistently across participant and dyad shift scopes. "
            "All linked records for a participant must declare the same dyad scope in v0.1."
        )
    context = derivation_context(
        collaboration_id=schema.collaboration_id,
        schema_version=schema.schema_version,
        key_alias=schema.key_alias,
        key_version=schema.key_version,
    )
    cache: dict[tuple[str, tuple[str, ...]], int] = {}
    for index, (scope, components) in scope_series.items():
        identity = (scope, components)
        if identity not in cache:
            cache[identity] = derive_shift_weeks(
                key.secret,
                context,
                scope=scope,
                components=components,
                min_weeks=schema.shift.min_weeks,
                max_weeks=schema.shift.max_weeks,
            )
        shifts.at[index] = cache[identity]
    return scope_series, shifts


def _component_value(row: pd.Series, source: str | None) -> Any:
    return pd.NA if source is None else row[source]


def _parse_group(
    frame: pd.DataFrame,
    group: TimestampGroup,
    rule_map: dict[str, Any],
    summary: ValidationSummary,
) -> dict[str, pd.Series]:
    results: dict[str, pd.Series] = {}
    for field_spec in group.fields:
        values: list[Moment | None] = []
        invalid_rows: list[int] = []
        for index, row in frame.iterrows():
            try:
                timezone_name = resolve_timezone(group.timezone, row)
                moment = parse_moment(
                    local_value=_component_value(row, field_spec.local_source),
                    utc_value=_component_value(row, field_spec.utc_source),
                    offset_value=_component_value(row, field_spec.offset_source),
                    timezone_name=timezone_name,
                    offset_unit=field_spec.offset_numeric_unit,
                    tolerance_seconds=field_spec.consistency_tolerance_seconds,
                )
            except (ValueError, TypeError, OverflowError):
                moment = None
                invalid_rows.append(index)
            values.append(moment)
        if invalid_rows:
            sources = [
                source
                for source in (field_spec.local_source, field_spec.utc_source, field_spec.offset_source)
                if source
            ]
            required = any(rule_map[source].required for source in sources)
            if required:
                raise DataValidationError(
                    f"Timestamp group {group.name}, field {field_spec.name} contains invalid required timestamps at "
                    + _safe_rows(invalid_rows)
                    + "."
                )
            for source in sources:
                count = len(invalid_rows)
                frame.loc[invalid_rows, source] = pd.NA
                summary.optional_invalid_blanked[source] = summary.optional_invalid_blanked.get(source, 0) + count
        results[field_spec.name] = pd.Series(values, index=frame.index, dtype="object")
    return results


def _same_moment(left: Moment, right: Moment, tolerance_seconds: int = 2) -> bool:
    return (
        abs((left.utc - right.utc).total_seconds()) <= tolerance_seconds
        and left.offset_minutes == right.offset_minutes
    )


def _anchor_key(row: pd.Series, columns: list[str]) -> tuple[str, ...]:
    values: list[str] = []
    for column in columns:
        value = row[column]
        if is_missing(value):
            raise DataValidationError("A timestamp event-anchor value is missing.")
        values.append(canonical_value(value))
    return tuple(values)


def _flag_matches(value: Any, options: list[Any]) -> bool:
    if is_missing(value):
        return False
    target = canonical_value(value)
    for option in options:
        if target == canonical_value(option) or str(value) == str(option):
            return True
        try:
            if float(value) == float(option):
                return True
        except (TypeError, ValueError):
            pass
    return False


def _jitter_bounds(base_local: datetime, boundary: time) -> tuple[int, int]:
    midnight = datetime.combine(base_local.date(), time(0, 0))
    next_midnight = midnight + timedelta(days=1)
    research = behavioral_date(base_local, boundary)
    research_start = datetime.combine(research, boundary)
    research_end = research_start + timedelta(days=1)
    lower = max(
        -180,
        math.ceil((midnight - base_local).total_seconds()),
        math.ceil((research_start - base_local).total_seconds()),
    )
    upper = min(
        180,
        math.ceil((next_midnight - base_local).total_seconds()) - 1,
        math.ceil((research_end - base_local).total_seconds()) - 1,
    )
    return int(lower), int(upper)


def _process_synthetic_group(
    frame: pd.DataFrame,
    schema: ToolSchema,
    key: KeyRecord,
    group: TimestampGroup,
    moments: dict[str, pd.Series],
    shift_scope: pd.Series,
    shift_weeks: pd.Series,
) -> tuple[dict[str, dict[str, pd.Series]], pd.Series, pd.Series, pd.Series, dict[str, Any]]:
    context = derivation_context(
        collaboration_id=schema.collaboration_id,
        schema_version=schema.schema_version,
        key_alias=schema.key_alias,
        key_version=schema.key_version,
    )
    boundary = parse_boundary(schema.shift.behavioral_day_boundary)
    anchor_for_row: dict[int, tuple[str, ...]] = {}
    anchor_rows: dict[tuple[str, ...], list[int]] = {}
    for index, row in frame.iterrows():
        if all(moments[field.name].at[index] is None for field in group.fields):
            continue
        anchor = _anchor_key(row, group.anchor_columns)
        anchor_for_row[index] = anchor
        anchor_rows.setdefault(anchor, []).append(index)

    anchor_records: dict[tuple[str, ...], dict[str, Any]] = {}
    rare_suppressed = pd.Series(False, index=frame.index, dtype="boolean")
    rare_approved = pd.Series(False, index=frame.index, dtype="boolean")
    for anchor, rows in anchor_rows.items():
        shifts = {int(shift_weeks.at[index]) for index in rows}
        scopes = {shift_scope.at[index] for index in rows}
        if len(shifts) != 1 or len(scopes) != 1:
            raise DataValidationError(
                f"Timestamp group {group.name} has one event anchor assigned to inconsistent participant/dyad shift scopes."
            )
        representative: dict[str, Moment | None] = {}
        for field_spec in group.fields:
            observed = [moments[field_spec.name].at[index] for index in rows if moments[field_spec.name].at[index] is not None]
            if not observed:
                representative[field_spec.name] = None
                continue
            first = observed[0]
            assert isinstance(first, Moment)
            if any(not _same_moment(first, item) for item in observed[1:]):
                raise DataValidationError(
                    f"Timestamp group {group.name} contains inconsistent timestamps within a repeated event anchor."
                )
            representative[field_spec.name] = first
        primary = representative[group.reference_field]
        if primary is None:
            raise DataValidationError(
                f"Timestamp group {group.name} has an event anchor without its required reference timestamp."
            )
        suppressed = False
        approved = False
        if group.rare_event is not None:
            statuses = {
                _flag_matches(frame.at[index, group.rare_event.flag_column], group.rare_event.values)
                for index in rows
            }
            if len(statuses) != 1:
                raise DataValidationError(
                    f"Timestamp group {group.name} has inconsistent rare-event flags within one event anchor."
                )
            is_rare = statuses.pop()
            suppressed = is_rare and group.rare_event.policy == "research_day_only"
            approved = is_rare and group.rare_event.policy == "approved_exact"
            for index in rows:
                rare_suppressed.at[index] = suppressed
                rare_approved.at[index] = approved
        shift = next(iter(shifts))
        lower, upper = -180, 180
        for moment in representative.values():
            if moment is None:
                continue
            candidate_lower, candidate_upper = _jitter_bounds(moment.local + timedelta(weeks=shift), boundary)
            lower = max(lower, candidate_lower)
            upper = min(upper, candidate_upper)
        if lower > upper:
            raise TransformationError(
                f"No allowable timestamp jitter preserves civil and behavioral-day classification in group {group.name}."
            )
        anchor_records[anchor] = {
            "rows": rows,
            "scope": next(iter(scopes)),
            "shift": shift,
            "moments": representative,
            "base_primary": primary.utc + timedelta(weeks=shift),
            "lower": lower,
            "upper": upper,
            "suppressed": suppressed,
            "approved": approved,
        }

    jitter_by_anchor: dict[tuple[str, ...], int] = {}
    attempts_used = 0
    scopes: dict[Any, list[tuple[str, ...]]] = {}
    for anchor, record in anchor_records.items():
        if record["suppressed"]:
            continue
        scopes.setdefault(record["scope"], []).append(anchor)
    for scope, anchors in scopes.items():
        anchors.sort(key=lambda anchor: (anchor_records[anchor]["base_primary"], anchor))
        feasible_upper: dict[tuple[str, ...], int] = {}
        next_anchor: tuple[str, ...] | None = None
        for anchor in reversed(anchors):
            record = anchor_records[anchor]
            upper = int(record["upper"])
            if next_anchor is not None:
                next_record = anchor_records[next_anchor]
                spacing = (next_record["base_primary"] - record["base_primary"]).total_seconds()
                upper = min(upper, math.floor(spacing + feasible_upper[next_anchor]))
            if upper < int(record["lower"]):
                raise TransformationError(
                    f"No deterministic jitter assignment can preserve event order in timestamp group {group.name}."
                )
            feasible_upper[anchor] = upper
            next_anchor = anchor
        previous: datetime | None = None
        for anchor in anchors:
            record = anchor_records[anchor]
            lower = int(record["lower"])
            if previous is not None:
                lower = max(lower, math.ceil((previous - record["base_primary"]).total_seconds()))
            upper = feasible_upper[anchor]
            chosen = None
            for attempt, candidate in enumerate(
                jitter_candidates(
                    key.secret,
                    context,
                    group=group.name,
                    anchor_components=anchor,
                )
            ):
                if lower <= candidate <= upper:
                    chosen = candidate
                    attempts_used += attempt
                    break
            if chosen is None:
                raise TransformationError(
                    f"Unable to draw an allowable deterministic timestamp jitter in group {group.name}."
                )
            jitter_by_anchor[anchor] = chosen
            previous = record["base_primary"] + timedelta(seconds=chosen)

    jitter_series = pd.Series(pd.NA, index=frame.index, dtype="Int64")
    synthetic: dict[str, dict[str, pd.Series]] = {}
    for field_spec in group.fields:
        local_values: list[Any] = []
        utc_values: list[Any] = []
        offset_values: list[Any] = []
        for index in frame.index:
            moment = moments[field_spec.name].at[index]
            anchor = anchor_for_row.get(index)
            if moment is None or anchor is None or bool(rare_suppressed.at[index]):
                local_values.append(pd.NA)
                utc_values.append(pd.NA)
                offset_values.append(pd.NA)
                continue
            jitter = jitter_by_anchor[anchor]
            jitter_series.at[index] = jitter
            shifted_local = (moment.local + timedelta(weeks=int(shift_weeks.at[index]), seconds=jitter)).replace(
                microsecond=0
            )
            shifted_utc = (
                shifted_local - timedelta(minutes=moment.offset_minutes)
            ).replace(tzinfo=timezone.utc)
            local_values.append(shifted_local.strftime("%Y-%m-%dT%H:%M:%S"))
            utc_values.append(shifted_utc.strftime("%Y-%m-%dT%H:%M:%S"))
            offset_values.append(format_offset(moment.offset_minutes))
        synthetic[field_spec.name] = {
            "local": pd.Series(local_values, index=frame.index, dtype="object"),
            "utc": pd.Series(utc_values, index=frame.index, dtype="object"),
            "offset": pd.Series(offset_values, index=frame.index, dtype="object"),
        }
    assigned = [int(value) for value in jitter_by_anchor.values()]
    group_summary = {
        "event_anchor_count": len(anchor_records),
        "jittered_anchor_count": len(jitter_by_anchor),
        "rare_research_day_only_anchor_count": sum(bool(record["suppressed"]) for record in anchor_records.values()),
        "approved_exact_rare_anchor_count": sum(bool(record["approved"]) for record in anchor_records.values()),
        "jitter_min_seconds": min(assigned) if assigned else None,
        "jitter_max_seconds": max(assigned) if assigned else None,
        "resampled_candidate_count": attempts_used,
    }
    if group.rare_event and group.rare_event.policy == "approved_exact":
        group_summary["approval_reference"] = group.rare_event.approval_reference
    return synthetic, rare_suppressed, rare_approved, jitter_series, group_summary


def _parse_index_event(frame: pd.DataFrame, event: IndexEvent) -> pd.Series:
    parsed: list[IndexValue | None] = []
    invalid: list[int] = []
    for index, row in frame.iterrows():
        value = row[event.source_column]
        if is_missing(value):
            parsed.append(None)
            continue
        try:
            if event.kind == "date":
                stamp = pd.Timestamp(pd.to_datetime(value, errors="raise"))
                parsed.append(IndexValue(local_date=stamp.date()))
            else:
                timezone_name = resolve_timezone(event.timezone, row)
                moment = parse_moment(
                    local_value=value if event.kind == "local_datetime" else pd.NA,
                    utc_value=value if event.kind == "utc_datetime" else pd.NA,
                    offset_value=row[event.offset_source] if event.offset_source else pd.NA,
                    timezone_name=timezone_name,
                )
                if moment is None:
                    parsed.append(None)
                else:
                    parsed.append(IndexValue(local_date=moment.local.date(), moment=moment))
        except (ValueError, TypeError, OverflowError):
            parsed.append(None)
            invalid.append(index)
    if invalid:
        raise DataValidationError(
            f"Index event {event.name} contains invalid values at " + _safe_rows(invalid) + "."
        )
    series = pd.Series(parsed, index=frame.index, dtype="object")
    if event.group_by_columns:
        group_keys = frame[event.group_by_columns].apply(
            lambda row: tuple(canonical_value(value) for value in row), axis=1
        )
        for _, indices in group_keys.groupby(group_keys).groups.items():
            observed = [series.at[index] for index in indices if series.at[index] is not None]
            if not observed:
                if event.required:
                    raise DataValidationError(f"Required index event {event.name} is missing for one or more groups.")
                continue
            first = observed[0]
            if any(item.local_date != first.local_date for item in observed[1:]):
                raise DataValidationError(f"Index event {event.name} is inconsistent within its declared grouping.")
            if first.moment is not None and any(
                item.moment is None or not _same_moment(first.moment, item.moment)
                for item in observed[1:]
            ):
                raise DataValidationError(f"Index event {event.name} is inconsistent within its declared grouping.")
            for index in indices:
                series.at[index] = first
    elif event.required and bool(series.map(lambda value: value is None).any()):
        missing_indices = series.index[series.map(lambda value: value is None)]
        raise DataValidationError(
            f"Required index event {event.name} is missing at " + _safe_rows(missing_indices) + "."
        )
    return series


def process_temporal(
    frame: pd.DataFrame,
    schema: ToolSchema,
    key: KeyRecord,
    validation_summary: ValidationSummary,
) -> TemporalResults:
    shift_scope, shift_weeks = _shift_series(frame, schema, key)
    rule_map = {rule.source: rule for rule in schema.columns}
    groups: dict[str, TimestampGroupResult] = {}
    source_moments: dict[str, pd.Series] = {}
    replacements: dict[str, tuple[str, pd.Series, str]] = {}
    appended: list[tuple[str, pd.Series, str, str]] = []
    group_summaries: dict[str, Any] = {}

    for group in schema.timestamp_groups:
        moments = _parse_group(frame, group, rule_map, validation_summary)
        synthetic, rare_suppressed, rare_approved, jitter_series, group_summary = _process_synthetic_group(
            frame, schema, key, group, moments, shift_scope, shift_weeks
        )
        groups[group.name] = TimestampGroupResult(
            spec=group,
            moments=moments,
            synthetic=synthetic,
            rare_suppressed=rare_suppressed,
            rare_approved=rare_approved,
            jitter_seconds=jitter_series,
        )
        group_summaries[group.name] = group_summary
        for field_spec in group.fields:
            for source in (field_spec.local_source, field_spec.utc_source, field_spec.offset_source):
                if source:
                    source_moments[source] = moments[field_spec.name]
            mappings = (
                (field_spec.local_source, field_spec.output_local, synthetic[field_spec.name]["local"], "synthetic_local"),
                (field_spec.utc_source, field_spec.output_utc, synthetic[field_spec.name]["utc"], "synthetic_utc"),
                (field_spec.offset_source, field_spec.output_offset, synthetic[field_spec.name]["offset"], "frozen_utc_offset"),
            )
            for source, output_name, series, kind in mappings:
                if source:
                    replacements[source] = (output_name, series, kind)
                else:
                    appended.append((output_name, series, kind, f"timestamp group {group.name}, field {field_spec.name}"))

    index_results = {event.name: _parse_index_event(frame, event) for event in schema.index_events}
    shifts = [int(value) for value in shift_weeks.dropna().tolist()]
    summary = {
        "date_shift": {
            "scope_count": int(shift_scope.nunique()),
            "minimum_weeks": min(shifts) if shifts else None,
            "maximum_weeks": max(shifts) if shifts else None,
            "rule": "nonzero whole-week shift with absolute magnitude 4 through 52",
        },
        "timestamp_groups": group_summaries,
        "behavioral_day_boundary": schema.shift.behavioral_day_boundary,
    }
    return TemporalResults(
        shift_weeks=shift_weeks,
        shift_scope=shift_scope,
        groups=groups,
        index_events=index_results,
        source_moments=source_moments,
        replacement_outputs=replacements,
        appended_outputs=appended,
        summary=summary,
    )
