# Transformation Schema Reference

Schemas use YAML and are validated strictly: unknown keys are rejected. The schema file itself is SHA-256 fingerprinted in the QA report.

The complete example is [`../examples/example_schema.yaml`](../examples/example_schema.yaml).

## Top-level fields

| Field | Purpose |
| --- | --- |
| `format_version` | Schema-format version. Version 0.1 requires `"1"`. |
| `schema_version` | Version of the dataset-specific transformation policy. |
| `study_namespace` | Namespace applied to sources marked `namespace: study`. |
| `collaboration_id` | Collaboration-scoped release identifier. Must match the key file. |
| `key_alias` / `key_version` | External key reference. The secret is never stored here. |
| `input` | Primary key, optional Excel sheet, and unlisted-column policy. |
| `columns` | One rule for every possible source column. |
| `shift` | Participant/dyad identity sources and behavioral-day boundary. |
| `timestamp_groups` | Event anchors, timestamp components, time-zone resolution, and rare-event policy. |
| `index_events` | Study index dates/times used for age and research-day derivations. |
| `derived_columns` | Approved output variables computed from original values. |
| `interval_checks` | Required source-derived sequence and duration checks. |
| `small_cell_checks` | Participant-level demographic combinations flagged below five. |
| `holidays` | U.S. federal or explicit qualifying-holiday calendar. |
| `output` | Plain, distinct output basenames. |

## Input rule

```yaml
input:
  primary_key: [participant_id, ema_id]
  excel_sheet: null
  unlisted_columns: error
```

The primary or composite key is mandatory, nonmissing, and unique. The tool never silently deduplicates. `unlisted_columns` is fixed at `error` in v0.1.

## Column rules

Each source column is declared once.

```yaml
- source: participant_id
  action: tokenize
  output: participant_id
  required: true
  description: Collaboration-scoped participant token
  sensitivity: direct_identifier
  validation: {type: string, nullable: false}
  token_domain: participant
  token_prefix: p
  token_sources:
    - {column: nih_guid, namespace: global}
```

### Actions

| Action | Behavior |
| --- | --- |
| `keep` | Retains the source name, position, codes, and approved values. |
| `remove` | Excludes the source field. |
| `tokenize` | Replaces the source in place using a domain-separated keyed token. |
| `timestamp` | Declares a source timestamp component consumed by a timestamp group. |
| `age_at_index` | Replaces date of birth with whole-year age at a named index event; `90+` is top-coded. |

Direct identifiers and original free text cannot use `keep`. Geography can be retained only when marked as a coded study-site field (`role: site_code`).

### Validation

`validation.type` may be `any`, `string`, `integer`, `number`, `boolean`, `date`, `datetime`, or `offset`. Optional constraints include:

```yaml
validation:
  type: integer
  nullable: true
  min_value: 0
  max_value: 10
  allowed_values: [0, 1, 2, 3, 4, 5]
  regex: null
```

Invalid required values halt the export. Invalid optional values are blanked and counted in the QA report. `missing_codes` are converted to blank CSV cells before validation. `value_labels` are preserved in the dictionary.

## Token derivation

A token rule names its cryptographic domain and the source components used to establish identity. `namespace: global` supports stable collaboration tokens across studies when a stable global identifier such as an NIH GUID is available. `namespace: study` includes `study_namespace` in the derivation.

Token and shift derivations are domain separated; a participant token cannot equal a dyad token merely because their source strings match.

## Date shifting

```yaml
shift:
  participant_sources:
    - {column: nih_guid, namespace: global}
  dyad_sources:
    - {column: dyad_id, namespace: study}
  min_weeks: 4
  max_weeks: 52
  behavioral_day_boundary: "04:00"
```

The range is fixed at a nonzero ±4–52 weeks. A complete declared dyad identity takes precedence; otherwise the participant identity is used. One participant cannot alternate between dyad and participant shift scopes within the same dataset.

## Timestamp groups

```yaml
timestamp_groups:
  - name: ema
    anchor_columns: [participant_id, ema_id]
    reference_field: prompt
    timezone: {fixed: America/New_York}
    fields:
      - name: prompt
        local_source: prompt_local
        utc_source: prompt_utc
        offset_source: prompt_offset
        output_local: synthetic_local_timestamp
        output_utc: synthetic_utc_timestamp
        output_offset: utc_offset
```

A group receives one deterministic jitter value per event anchor. Every field in that anchor receives the same jitter, preserving internal intervals. The algorithm constrains jitter to prevent reversal of event order and crossing of the original civil date or behavioral-day boundary.

Time-zone resolution can use one of:

```yaml
timezone: {fixed: America/New_York}
```

```yaml
timezone: {column: source_timezone_name}
```

```yaml
timezone:
  mapping_column: site_id
  mapping: {S1: America/New_York, S2: America/Chicago}
```

Named time-zone fields can be used internally but cannot be retained in the standard export. The synthetic local and UTC timestamps use the source event's numeric offset, even when that offset is not historically valid on the shifted date.

## Rare events

```yaml
rare_event:
  flag_column: rare_event_flag
  values: [1]
  policy: research_day_only
  suppress_calendar_context: true
```

For `research_day_only`, all synthetic timestamp components are blank for flagged anchors and only an approved `behavioral_research_day` derivation may represent timing. Calendar-context outputs are suppressed for those rows. Exact elapsed-time outputs cannot reference the group's source timestamp fields.

`approved_exact` requires an `approval_reference` and records it in the QA summary.

## Index events

```yaml
index_events:
  - name: study_index
    source_column: index_local
    kind: local_datetime
    group_by_columns: [participant_id]
    timezone: {fixed: America/New_York}
    offset_source: index_offset
    required: true
```

Index events may be dates, local datetimes, or UTC datetimes. Grouped index events must be internally consistent.

## Derived columns

Supported kinds are:

- `elapsed_seconds`: exact duration from original UTC values.
- `weekday_weekend`: original local weekday/weekend classification.
- `behavioral_research_day`: integer day relative to a declared index event; the index behavioral day is 0.
- `season`: Northern Hemisphere meteorological season by original local month.
- `holiday_proximity`: signed days to the nearest qualifying holiday within ±7 days; otherwise blank.
- `dst_status`: `daylight`, `standard`, or `not_applicable` based on original time-zone rules.
- `dst_transition_proximity`: signed days to the nearest transition within ±14 days; otherwise blank.
- `age_at_index`: whole-year age at a declared index event, top-coded at `90+`.

## Interval checks

```yaml
interval_checks:
  - name: response_duration
    start_column: response_start_utc
    end_column: response_end_utc
    min_seconds: 0
    max_seconds: 3600
```

An impossible or disallowed interval halts the export. The check is calculated from original UTC timestamps, not synthetic timestamps.

## Small-cell checks

```yaml
small_cell_checks:
  - name: sex_by_race
    columns: [sex_code, race_code]
    threshold: 5
    include_missing: false
    require_stable_within_participant: true
```

The threshold is fixed at fewer than five participants. The QA report lists only the combination of codes and participant count; it does not list participant identifiers. The tool flags but does not remediate or approve the cell.

## Holiday rule

```yaml
holidays:
  mode: us_federal
  window_days: 7
  additional_dates: []
  excluded_dates: []
```

Alternatively, use `mode: explicit` with ISO `YYYY-MM-DD` values in `dates`. The window is fixed at ±7 days.

## Output rule

```yaml
output:
  analytic_csv: deidentified.csv
  data_dictionary: data_dictionary.csv
  qa_report: qa_report.json
  release_archive: release.zip
```

Names must be distinct plain basenames with the shown file types. The application refuses to overwrite the source data, schema, or key, even when `--overwrite` is supplied.
