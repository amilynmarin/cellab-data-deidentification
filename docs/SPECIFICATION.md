# Research Data De-identification Tool

## Consolidated v0.1 Requirements Specification

**Status:** Requirements baseline for implementation  
**Release audience:** Approved external research collaborators working under controlled access and a data-use agreement  
**Core boundary:** One flat data file in; one de-identified analytic CSV out

## 1. Purpose

The tool converts one flat research dataset into a reproducible, de-identified CSV while preserving analytically useful temporal, dyadic, and categorical structure. It is intended for controlled collaborator releases, not public-use data.

The program must favor explicit, dataset-specific transformation schemas over assumptions about column meaning. It must not reshape the source into a universal event model or create multiple analytic tables.

## 2. v0.1 scope

### Inputs

The initial release accepts:

- CSV
- Excel `.xlsx`
- SPSS `.sav`
- Stata `.dta`

SAS files are out of scope.

For Excel workbooks:

- If exactly one populated sheet exists, the program may select it automatically.
- If more than one populated sheet exists, the user must explicitly select the sheet.

### Outputs

Each successful run produces:

1. One de-identified analytic CSV.
2. One data dictionary.
3. One QA/transformation report.

The dictionary and QA report are documentation, not additional analytic data files.

### Row and column invariants

- Every input row produces exactly one output row.
- Input row order is preserved.
- The output row count must equal the input row count.
- Retained source columns preserve their original order.
- Replacement variables occupy the position of the source variable they replace.
- Derived variables are appended in schema-defined order.
- Retained variables preserve their source names.
- Replacement and derived variables use schema-defined names.

### Explicitly out of scope

- Multiple input or output data tables
- Cross-table or referential-integrity checks
- Automatic row exclusion or deduplication
- Automatic aggregation, suppression, or participant exclusion for small demographic cells
- Public-use de-identification certification
- Organizational administration of archived keys
- Enforcement of two-person key-retrieval approval

## 3. Dataset transformation schema

Every run uses an approved, versioned transformation schema. The schema is fingerprinted and must identify, as applicable:

- Source participant identifier column
- NIH GUID column, when present
- Dyad or relationship identifier, when present
- EMA or other event-anchor identifier, when present
- Date and timestamp columns and the role of each
- Local timestamps, UTC timestamps, or the numeric UTC offsets needed to derive them
- Index event used for age and research-day derivations
- Required and optional variables
- Primary or composite key
- Allowed types and ranges used for validation
- Field-specific keep, remove, replace, or derive actions
- Rare or potentially identifiable event types
- Coded study-site column
- Behavioral-day boundary
- Holiday list
- Collaboration identifier
- Collaboration-key alias or external key-file reference, but never the secret key
- Schema version

There are no parent-child table declarations in v0.1. A flat file may contain participant, dyad, EMA, EAR, sensor, or other event information, but all needed linkage must exist within that file.

## 4. Identifiers and linkage

### Collaboration-scoped tokens

- Source study identifiers are replaced with collaboration-scoped tokens.
- NIH GUIDs are also tokenized in the standard collaborator export.
- Raw study identifiers and raw NIH GUIDs are omitted from the standard export.
- Raw GUIDs may be released only through specific approval when cross-repository or NDA linkage is part of the approved analysis.
- Raw GUIDs remain unchanged in a separately authorized NDA-submission workflow; that workflow is not the standard collaborator export.
- When a GUID is available, it may serve as the stable internal input for generating the collaboration token, allowing the same participant to receive the same token across studies within the same collaboration and schema/key lineage.
- Dyad, relationship, and event-anchor identifiers are de-identified in the same collaboration-scoped manner when retained.

### Patient-significant-other alignment

Dyadic data preserve alignment in two complementary ways:

1. **Shared synthetic calendar:** linked patient and significant-other records use the same dyad-level whole-week date shift.
2. **Explicit linkage:** de-identified `dyad_id`/relationship and `ema_anchor_id` fields, together with schema-approved relative-time fields, permit direct alignment without relying only on synthetic clock times.

For records not belonging to a declared dyad, the date shift is assigned at the individual participant level.

## 5. Direct identifiers, text, geography, and demographics

### Direct identifiers and text

- Direct identifiers are excluded unless a specific field is replaced by an approved token.
- Original free-text fields are excluded from the standard export.
- Approved variables derived from free text may be retained when the transformation schema explicitly permits them.

### Geography

- The standard export retains only coded `site_id` or the existing coded study-site variable.
- Site names and finer geographic variables are excluded.

### Age

- Date of birth is excluded.
- Age is retained in whole years at the study index event.
- Ages 90 years and older are top-coded as `90+`.

### Demographic small cells

- Combinations of demographic characteristics represented by fewer than five participants are flagged in the QA report.
- The program only flags these cells.
- It does not automatically aggregate categories, suppress variables, exclude records, or approve retention.
- Release reviewers decide what action, if any, is required outside the software.

## 6. Date and time transformation

### 6.1 Base date shift

- Each participant or linked dyad receives a nonzero whole-week shift.
- The absolute shift is randomly selected from 4 through 52 weeks.
- The direction is randomly forward or backward.
- All records assigned to the same participant or dyad use the same shift.
- Whole-week shifting preserves actual weekday and weekend classification.
- The shift is deterministic within the collaboration-key and schema-version lineage.

### 6.2 Routine timestamp jitter

- Each retained routine event anchor receives deterministic jitter drawn from minus 180 through plus 180 seconds.
- EMA records receive jitter at the EMA-anchor level.
- Other routine event anchors receive their own independent jitter.
- All timestamp fields belonging to one declared anchor use the anchor's shared jitter so their internal timing relationship is not arbitrarily distorted.
- Jitter is resampled when necessary to preserve:
  - Event order
  - Civil date and weekday classification
  - Behavioral research-day classification
- Exact elapsed-time variables are not reconstructed from jittered timestamps; the original UTC-derived intervals remain authoritative.

### 6.3 Rare or potentially identifiable events

- Rare or potentially identifiable events are represented at the research-day level in the standard export.
- Exact relative or clock timing for such events requires explicit analysis-specific approval.
- The dataset schema identifies which event types receive this treatment.

### 6.4 Behavioral day

- The behavioral-day boundary is one configurable value per dataset.
- The default is `04:00` local time.
- The selected boundary is recorded in the transformation schema/manifest.
- It is applied uniformly to patient, significant-other, EMA, EAR, and other linked records in that dataset.

### 6.5 Authoritative temporal derivations

The following variables are derived from original timestamps before shifting or jitter:

- Elapsed times and event intervals, calculated from original UTC timestamps
- Weekday/weekend classification, calculated from original local time
- Behavioral research day
- Four-season category
- Holiday proximity
- Daylight-saving status and transition proximity

These derived variables remain authoritative even when they cannot be exactly reconstructed from the synthetic timestamps.

### 6.6 Synthetic timestamps

The standard CSV includes:

- `synthetic_local_timestamp`
- `synthetic_utc_timestamp`
- Numeric UTC offset in `±HH:MM` form

Rules:

- Timestamps use ISO 8601 format with seconds: `YYYY-MM-DDTHH:MM:SS`.
- Named time zones are not released.
- The original local clock time and original numeric UTC offset take priority when a shift crosses a daylight-saving transition.
- The numeric offset is therefore frozen from the original event.
- Synthetic local and UTC values must be internally consistent with that frozen offset.
- The frozen offset may differ from the civil offset normally associated with the synthetic calendar date; collaborators must not interpret the synthetic timestamp as a historically valid local civil-time record.

### 6.7 Calendar-context variables

#### Season

- Retain a four-season category derived from the original local date.

#### Holidays

- Use a dataset-specific qualifying-holiday list.
- Default to U.S. federal holidays.
- Record the selected list in the schema/manifest.
- Retain the exact signed number of days to the nearest qualifying holiday only within seven days.
- Values range from `-7` through `+7`, with `0` indicating the holiday, negative values before it, and positive values after it.
- Dates outside the window are coded as not within the holiday window.

#### Daylight saving time

- Retain categorical daylight-saving status: daylight, standard, or not applicable.
- Retain signed days to the nearest daylight-saving transition only within 14 days.
- Values range from `-14` through `+14`; observations outside the window are coded as not near a transition.
- Both variables are based on the original local calendar and time-zone rules, not the synthetic date.

## 7. Missing values and categories

### Missing values

- Source-specific missing codes, including SPSS user-defined missing values, are converted to blank CSV cells.
- Analytically meaningful missingness reasons may be retained in separate approved reason fields.
- Missing-reason fields use a common semantic core:
  - `refused`
  - `not_administered`
  - `not_applicable`
  - `technical_failure`
  - `unknown`
- Dataset-specific additions are permitted and documented in the schema and data dictionary.
- New categorical reason fields use schema-defined numeric codes in the CSV and labels in the dictionary.

### Source categorical variables

The later metadata decision supersedes universal recoding:

- Preserve existing source category codes in retained variables.
- Preserve original value labels in the data dictionary.
- Do not recode source categories into a common cross-dataset scheme in v0.1.
- Where the source already uses numeric codes, those numeric codes remain in the CSV.

## 8. CSV safety

- Approved string values beginning with `=`, `+`, `-`, or `@` are escaped so spreadsheet software does not interpret them as formulas.
- Genuine numeric negative values remain numeric and unchanged.
- The QA report lists affected columns and counts.

## 9. Validation and failure behavior

### Conditions that halt the export

- An invalid or missing required value
- An impossible source-derived interval, such as a negative duration or invalid event sequence
- A duplicate value for a declared unique primary or composite key
- Failure to preserve one-to-one row mapping, row order, or row count
- Failure to locate or validate the required collaboration key

The program does not silently correct, omit, or deduplicate affected records.

### Conditions that do not halt the export

- An invalid optional value is blanked and reported by column and count.
- A demographic cell involving fewer than five participants is flagged for review only.
- Formula-like approved text is escaped and reported.

Sensitive source values should not be reproduced unnecessarily in the QA report.

## 10. Reproducibility and key handling

### Deterministic output

Given the same:

- Input file
- Transformation schema and schema version
- Collaboration identifier
- Collaboration key version

the program produces identical tokens, date shifts, jitter, and output.

The deterministic process must be keyed. It must not derive a seed directly from names, study IDs, NIH GUIDs, dates of birth, or other identifiable values without the collaboration secret.

### Collaboration key

- Use one cryptographically random 256-bit secret per collaboration/key version.
- Store it in a restricted external key file.
- Generate a new key by default during collaboration setup.
- Permit secure import of an existing authorized key.
- Verify owner-only or equivalent restricted file permissions before use.
- The schema contains only a key alias or external reference, never the secret.
- Exclude the key from data, dictionaries, QA reports, schemas, release archives, logs, and version control.

### Rotation

- An established key is not overwritten.
- Key replacement occurs only through explicit versioned rotation.
- A new key version creates a distinct release lineage.
- Retired keys are archived securely for historical reproducibility and blocked from authorizing new exports.

The following are organizational SOP matters, not v0.1 software features:

- Who administers the archive
- How archived keys are encrypted at rest
- Two-person approval by a key administrator and PI/data steward
- Retrieval and access-log procedures

## 11. Documentation requirements

### Data dictionary

The data dictionary records, as applicable:

- Output variable name
- Source variable name
- Variable type and format
- Description
- Retained, removed, replaced, or derived status
- Source and output category codes
- Source value labels
- Missing-value and missing-reason codes
- Derivation rule
- Calendar or behavioral-day interpretation
- Whether a timestamp is original-derived or synthetic

### QA/transformation report

The QA report includes:

- Tool version
- Schema version
- Collaboration identifier and key version/alias, but not the key
- Selected Excel sheet, when applicable
- Input and output row and column counts
- Verification of preserved row order and one-to-one mapping
- SHA-256 fingerprints of the input file, transformation schema, and output CSV
- Validation results
- Counts of blanked invalid optional values by column
- Duplicate-key and impossible-interval checks
- Small demographic cells flagged for review
- Formula-like text escape counts by column
- Date-shift and jitter rule summaries without exposing source dates
- Warnings and the final success/failure status

## 12. Precedence and resolved tensions

| Apparent tension | v0.1 resolution |
| --- | --- |
| One data file out vs. three output files | There is one analytic CSV; the dictionary and QA report are companion documentation. |
| Individual-level anonymized dates vs. dyadic alignment | Use participant-level shifts ordinarily and one shared shift for all members of a declared dyad. |
| Numeric categorical codes vs. preserved source codes | Preserve source codes for retained variables; use schema-defined numeric codes only for new categorical fields. |
| Synthetic local time vs. valid DST conversion on the shifted date | Preserve original local clock time and frozen numeric offset; label the date-time as synthetic. |
| Reproducibility vs. key replacement | A key is stable within one lineage; versioned rotation begins a new lineage. |
| Small-cell detection vs. disclosure remediation | The program flags cells under five; a human reviewer decides the remedy. |
| Exact intervals vs. jittered timestamps | Original UTC-derived intervals are authoritative; synthetic timestamps are not used to recalculate them. |

## 13. Remaining implementation decisions

The requirements are sufficient to begin implementation. The following can be resolved during engineering without reopening the de-identification policy:

- Command-line interface and configuration-file syntax
- Supported Stata file-version range
- Exact filenames and file formats for the dictionary and QA report
- Token encoding and display length, subject to an adequate collision margin
- Exact pseudorandom derivation construction and domain separation for tokens, shifts, and jitter
- CSV encoding and line-ending defaults
- Exact representation of “outside window” for holiday and DST proximity
- Tie-breaking when two qualifying holidays or DST transitions are equally near
- Platform-specific enforcement of restricted key-file permissions

Any implementation choice must preserve the behavioral requirements and invariants in this specification.
