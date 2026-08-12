# Security and Operational Use

## Intended boundary

Version 0.1.1 is designed for controlled collaborator releases of one flat research dataset. It is not a public-use-data anonymization certificate, a substitute for expert disclosure review, or a complete data-governance system.

## Collaboration keys

- Use one cryptographically random 256-bit secret for each collaboration and key version.
- Store the key outside the source-data directory and outside every release directory.
- Never place the key in a schema, notebook, source repository, release archive, email attachment, or shared analysis folder.
- On POSIX systems, key files must be regular files owned by the current user with mode `0600` or stricter. Symbolic links are rejected.
- Imported raw key material is subject to the same permission check.
- The software cannot verify Windows ACLs. Windows production use requires an institutionally reviewed control outside this release. The CLI override exists only for testing and is not a security control.
- Rotation creates a new file and lineage; it does not overwrite an established key. The old key is marked retired and cannot authorize new exports.
- Secure archiving, encryption at rest, two-person retrieval approval, and access logging are organizational SOP responsibilities.

## Data handling

- Run the tool only on an approved secure system.
- Keep source data, schemas, keys, temporary work areas, and release outputs under appropriate access controls.
- Do not use real identifiers in bug reports, screenshots, test fixtures, or support messages.
- Review the QA report and data dictionary before release.
- Treat a successful run as evidence that declared transformations and invariants executed—not as evidence that residual re-identification risk is acceptable.
- Small-cell flags require human disposition. The software does not automatically suppress, aggregate, exclude, or approve records.
- Free text is removed, not automatically de-identified. Retaining an already-derived text variable requires declaring that variable separately as an approved non-free-text field.

## Output protections

The program stages the complete release before commit, uses rollback-protected overwrite commits, applies owner-only file modes on POSIX systems, refuses accidental overwrites unless explicitly instructed, and refuses to place an output over the input data, schema, or key. The optional ZIP contains only the analytic CSV, dictionary, and QA report.

## Failure reports

A failed run may create `qa_report.failed.json`. The report contains schema/key metadata aliases, row locations, and a sanitized error description. Source and schema filenames are intentionally omitted. It is designed not to reproduce sensitive source values, but it should still be handled as controlled project documentation.

## Incident handling

If a key may have been disclosed, stop new exports under that key, preserve the affected release lineage, follow institutional incident-response procedures, and perform explicit versioned rotation. Do not delete the historical key when prior releases must remain reproducible; archive it under the applicable SOP and block it from new use.
