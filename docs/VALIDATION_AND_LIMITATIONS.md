# Validation Scope and Known Limitations

## Validation completed for v0.1.1

The automated suite covers:

- Byte-identical analytic CSV, data dictionary, QA report, and release ZIP across repeated runs.
- Collaboration token stability and cryptographic domain separation.
- Nonzero ±4–52-week shifts and shared dyad shifts.
- Shared anchor jitter and preservation of within-anchor elapsed intervals.
- Civil-day and behavioral-day jitter boundaries.
- Rare-event research-day-only suppression.
- Missing-code conversion and optional invalid-value blanking.
- Formula-like text escaping without altering numeric negatives or UTC offsets.
- Raw identifier, site-name, free-text, key-secret, source-filename, and schema-filename absence from release contents.
- Primary/composite-key duplicate failure and sanitized failure reporting.
- Excel multi-sheet selection behavior.
- Stata string-identifier and value-label preservation through the input reader.
- Literal `NA` preservation in CSV inputs.
- Source-order output invariance when schema column rules are reordered.
- Key permission checks, retirement, and versioned rotation.
- Schema enforcement of fixed policy values and prevention of exact rare-event interval leakage.
- Prevention of output paths overwriting input data, schema, or key.
- Restoration of prior release files after a simulated partial overwrite-commit failure.
- Rejection of duplicate release-output filenames.

## Not exercised in the build environment

The SPSS reader is implemented through `pyreadstat` and declared as a runtime dependency, but `pyreadstat` was not available in the build environment used for this archive. It therefore was not executed in the final local test run. SPSS handling should be validated with representative `.sav` fixtures—including user-defined missing values and value labels—before institutional production deployment.

The application reads Stata through pandas, but the exact Stata format-version range depends on the installed pandas release. A basic `.dta` round trip is tested; production qualification should include the Stata versions used by the institution.

## Deliberate v0.1 exclusions

- SAS `.sas7bdat` and transport files.
- Multiple input tables, multiple analytic output tables, or referential-integrity checks.
- Automatic row exclusion, correction, or deduplication.
- Automatic demographic aggregation, suppression, participant exclusion, or release approval.
- Natural-language de-identification of free text.
- Raw GUID release or NDA-submission workflow.
- Public-use-data certification or a formal quantitative re-identification-risk estimator.
- Organizational archived-key administration, encryption, two-person approval, and access logging.
- Verified Windows ACL enforcement.

## Interpretation cautions

- A deterministic pseudonym is not anonymity. Repeated collaboration releases can be linked by design within the same key/schema lineage.
- Whole-week shifting preserves weekday but does not preserve actual holidays, seasons, or daylight-saving transitions. The authoritative calendar-context fields are therefore derived from the original timestamp.
- The frozen source UTC offset can be civilly unusual on the shifted date. Synthetic timestamps are analytic surrogates, not historically valid local-time records.
- Season uses Northern Hemisphere meteorological month groupings. Dataset-specific alternatives require a future schema extension.
- The default holiday calendar uses observed U.S. federal holidays. A project should use an explicit list when another jurisdiction or study-specific holiday set is required.
- Event order is preserved within a timestamp group and shift scope. Cross-group ordering is not jointly constrained in v0.1.
- Small-cell checks cover only combinations explicitly declared in the schema. They are a release-review aid, not an exhaustive disclosure analysis.
- Exact reproduction should use the same tool version and dependency lock, in addition to the same input, schema, and key lineage.
