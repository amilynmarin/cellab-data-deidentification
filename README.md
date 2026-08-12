# CELLab Data De-identification

**CELLab Data De-identification** is a schema-driven command-line tool for converting **one flat research dataset** into a deterministic, de-identified collaborator release while preserving analytically useful temporal, dyadic, and categorical structure.

Version 0.1.1 implements the frozen v0.1 requirements baseline in [`docs/SPECIFICATION.md`](docs/SPECIFICATION.md). It is intended for approved research collaborators operating under controlled access and a data-use agreement. It is **not** a public-use-data certification system and does not replace institutional privacy, legal, IRB, or data-steward review. See [`DISCLAIMER.md`](DISCLAIMER.md) and [`SECURITY.md`](SECURITY.md) before processing sensitive data.

## What it produces

Each successful run creates:

1. One de-identified analytic CSV.
2. One CSV data dictionary.
3. One JSON QA/transformation report.
4. Optionally, one deterministic ZIP containing those three release files.

The analytic output preserves input row count and row order. Retained source columns keep their original names and order; replacement variables occupy the source column's position; derived variables are appended in schema order.

## Implemented controls

- Explicit, versioned YAML transformation schemas with strict validation and SHA-256 fingerprinting.
- CSV, Excel `.xlsx`, SPSS `.sav`, and Stata `.dta` inputs; SAS is rejected as out of scope.
- Explicit Excel sheet selection when a workbook contains multiple populated sheets.
- Collaboration-scoped HMAC-SHA-256 tokens with at least 128 bits of displayed entropy.
- One nonzero deterministic whole-week shift of 4–52 weeks per participant or declared dyad.
- Deterministic event-anchor jitter from −180 through +180 seconds, constrained to preserve event order, civil date, weekday/weekend status, and behavioral research day.
- Synthetic local and UTC timestamps with the original numeric UTC offset frozen in `±HH:MM` form.
- Original-derived elapsed time, behavioral day, season, holiday proximity, and daylight-saving variables.
- Research-day-only handling for schema-declared rare or potentially identifiable events.
- Required-value, type, range, primary-key, duplicate-key, interval, row-mapping, and small-cell checks.
- Source missing-code normalization, preserved source categorical codes and labels, and CSV formula-injection protection.
- Restricted 256-bit external key files, secure generation/import, retirement, and versioned rotation.
- Deterministic release files and release ZIP for identical inputs, schema, key lineage, and tool version.
- QA fingerprints without release of source-data or schema filenames.
- Rollback-protected commit of a complete release file set when overwrite is explicitly enabled.

## Installation

Python 3.11 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install .
```

For development and testing:

```bash
python -m pip install -e ".[dev]"
pytest
```

SPSS support is provided through `pyreadstat`, which is a declared runtime dependency. Stata support uses pandas' Stata reader.

## Controlled demonstration

The bundled example data are fictional. Keep the collaboration key outside the release directory.

```bash
cellab-deid schema validate examples/example_schema.yaml

mkdir -p demo-control demo-release
cellab-deid key generate \
  --output demo-control/example.key.json \
  --collaboration-id example-collaboration \
  --alias example-key \
  --version 1

cellab-deid run \
  --input examples/example_input.csv \
  --schema examples/example_schema.yaml \
  --key-file demo-control/example.key.json \
  --output-dir demo-release \
  --archive
```

`research-deid` remains available as a compatibility alias for the same CLI.

## Command-line interface

### Validate a schema

```bash
cellab-deid schema validate PATH/transform.yaml
```

### Run an export

```bash
cellab-deid run \
  --input PATH/source.csv \
  --schema PATH/transform.yaml \
  --key-file PATH/control/collaboration.key.json \
  --output-dir PATH/release \
  [--sheet SHEET_NAME] \
  [--archive] \
  [--overwrite]
```

### Manage collaboration keys

```bash
cellab-deid key generate --output KEY.json --collaboration-id ID --alias ALIAS --version VERSION
cellab-deid key inspect KEY.json
cellab-deid key retire KEY.json
cellab-deid key rotate --current OLD.json --output NEW.json --new-version 2 [--new-alias ALIAS]
cellab-deid key import --secret-file SECRET.bin --output KEY.json --collaboration-id ID --alias ALIAS --version VERSION
```

The imported secret-material file must itself have owner-only permissions on POSIX systems. A key is never printed, placed in a schema, copied into a QA report, or included in a release archive.

## Schema model

Every source column must be declared. Unlisted columns halt the export. A column action is one of:

- `keep`: retain the source name and position.
- `remove`: omit the field from the collaborator release.
- `tokenize`: replace it in place with a collaboration-scoped keyed token.
- `timestamp`: replace source timestamp components with schema-named synthetic components.
- `age_at_index`: replace date of birth with whole-year age at a declared index event, top-coded at `90+`.

The schema also declares participant/dyad identity sources, timestamp groups and anchors, index events, derived variables, interval checks, demographic small-cell checks, holiday rules, and output filenames. See [`docs/SCHEMA_REFERENCE.md`](docs/SCHEMA_REFERENCE.md) and [`examples/example_schema.yaml`](examples/example_schema.yaml).

## Determinism and release lineage

Exact reproduction requires the same input bytes, transformation schema bytes and version, collaboration identifier, collaboration-key alias/version/secret, and tool/algorithm version. Versioned key rotation intentionally starts a distinct release lineage. Retired keys remain usable only for authorized historical reproduction outside the standard export command; they cannot authorize new exports.

## Operational security boundary

This tool reduces specified disclosure risks; it does not prove that a dataset is anonymous. De-identification risk depends on the dataset, external linkage information, analytic purpose, recipient controls, and the full release environment. Human review remains mandatory, particularly for demographic cells flagged below five participants and for any analysis-specific exception involving exact timing or raw linkage identifiers.

Read [`SECURITY.md`](SECURITY.md), [`DISCLAIMER.md`](DISCLAIMER.md), and [`docs/VALIDATION_AND_LIMITATIONS.md`](docs/VALIDATION_AND_LIMITATIONS.md) before use.

## License

CELLab Data De-identification is licensed under the **GNU General Public License, version 3 or any later version (`GPL-3.0-or-later`)**. You may use, study, modify, and redistribute the software under those terms. Distributed derivative works must comply with the GPL; see [`LICENSE`](LICENSE) for the full license text.
