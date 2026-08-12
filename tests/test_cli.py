from __future__ import annotations

import os
from pathlib import Path

from research_deid.cli import main


def test_cli_schema_key_and_export_commands(
    tmp_path: Path,
    example_input: Path,
    example_schema: Path,
) -> None:
    key_file = tmp_path / "control" / "example.key.json"
    assert main([
        "schema", "validate", str(example_schema),
    ]) == 0
    assert main([
        "key", "generate",
        "--output", str(key_file),
        "--collaboration-id", "example-collaboration",
        "--alias", "example-key",
        "--version", "1",
    ]) == 0
    run_args = [
        "run",
        "--input", str(example_input),
        "--schema", str(example_schema),
        "--key-file", str(key_file),
        "--output-dir", str(tmp_path / "release"),
        "--archive",
    ]
    if os.name != "posix":
        run_args.append("--allow-unverified-key-permissions")
    assert main(run_args) == 0
    assert (tmp_path / "release" / "deidentified.csv").is_file()

    secret_file = tmp_path / "authorized-secret.bin"
    secret_file.write_bytes(bytes(range(32)))
    if os.name == "posix":
        secret_file.chmod(0o600)
    import_args = [
        "key", "import",
        "--secret-file", str(secret_file),
        "--output", str(tmp_path / "control" / "imported.key.json"),
        "--collaboration-id", "another-collaboration",
        "--alias", "imported-key",
        "--version", "1",
    ]
    if os.name != "posix":
        import_args.append("--allow-unverified-key-permissions")
    assert main(import_args) == 0
