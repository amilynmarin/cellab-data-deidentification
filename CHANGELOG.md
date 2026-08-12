# Changelog

## 0.1.1 — 2026-08-11

- Renamed the public project/package to `cellab-data-deidentification` and added the primary `cellab-deid` CLI while retaining `research-deid` as a compatibility alias.
- Licensed the project as GPL-3.0-or-later.
- Added a scientific, privacy, and regulatory disclaimer for public distribution.
- Updated public-facing documentation and package metadata for GitHub publication.

## 0.1.0 — 2026-08-11

Initial implementation of the consolidated v0.1 requirements baseline.

- Added strict YAML schemas for one-flat-file transformations.
- Added CSV, Excel, SPSS, and Stata readers, including explicit multi-sheet Excel selection.
- Added collaboration-scoped tokenization, participant/dyad whole-week shifting, event-anchor jitter, synthetic timestamps, and original-derived calendar variables.
- Added rare-event research-day-only handling, missing-code normalization, categorical metadata preservation, formula protection, small-cell review flags, and fail-closed validation.
- Added external 256-bit key generation, secure import, inspection, retirement, and versioned rotation.
- Added deterministic CSV, dictionary, QA, and optional ZIP outputs.
- Added end-to-end, temporal, cryptographic, input, schema, and key-management tests.
- Added normalized Stata/SPSS categorical metadata, source-order invariance, filename-leak prevention, unique output-name enforcement, and rollback-protected release commits.
