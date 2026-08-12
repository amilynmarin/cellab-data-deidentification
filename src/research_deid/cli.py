from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .engine import run_export
from .errors import DeidError
from .keys import generate_key, import_key, load_key, retire_key, rotate_key
from .schema import load_schema


def _add_permission_override(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--allow-unverified-key-permissions",
        action="store_true",
        help="Testing override for platforms where owner-only key permissions cannot be verified; not a production control.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research-deid",
        description="Deterministic, schema-driven de-identification for one flat research dataset.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="Transform one approved flat dataset.")
    run.add_argument("--input", required=True, type=Path)
    run.add_argument("--schema", required=True, type=Path)
    run.add_argument("--key-file", required=True, type=Path)
    run.add_argument("--output-dir", required=True, type=Path)
    run.add_argument("--sheet", help="Explicit Excel sheet selection.")
    run.add_argument("--overwrite", action="store_true")
    run.add_argument("--archive", action="store_true", help="Also create a deterministic ZIP containing the three release files.")
    _add_permission_override(run)

    schema = commands.add_parser("schema", help="Transformation-schema operations.")
    schema_commands = schema.add_subparsers(dest="schema_command", required=True)
    schema_validate = schema_commands.add_parser("validate", help="Validate a YAML transformation schema.")
    schema_validate.add_argument("schema", type=Path)

    key = commands.add_parser("key", help="Collaboration-key operations.")
    key_commands = key.add_subparsers(dest="key_command", required=True)

    generate = key_commands.add_parser("generate", help="Generate a new 256-bit collaboration key file.")
    generate.add_argument("--output", required=True, type=Path)
    generate.add_argument("--collaboration-id", required=True)
    generate.add_argument("--alias", required=True)
    generate.add_argument("--version", required=True)

    imported = key_commands.add_parser("import", help="Securely import authorized 256-bit key material from a file.")
    imported.add_argument("--secret-file", required=True, type=Path)
    imported.add_argument("--output", required=True, type=Path)
    imported.add_argument("--collaboration-id", required=True)
    imported.add_argument("--alias", required=True)
    imported.add_argument("--version", required=True)
    _add_permission_override(imported)

    inspect = key_commands.add_parser("inspect", help="Display key metadata without displaying the secret.")
    inspect.add_argument("key_file", type=Path)
    _add_permission_override(inspect)

    retire = key_commands.add_parser("retire", help="Mark a collaboration key retired and block new exports.")
    retire.add_argument("key_file", type=Path)
    _add_permission_override(retire)

    rotate = key_commands.add_parser("rotate", help="Create a versioned replacement and retire the current key.")
    rotate.add_argument("--current", required=True, type=Path)
    rotate.add_argument("--output", required=True, type=Path)
    rotate.add_argument("--new-version", required=True)
    rotate.add_argument("--new-alias")
    _add_permission_override(rotate)

    return parser


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            result = run_export(
                args.input,
                args.schema,
                args.key_file,
                args.output_dir,
                sheet=args.sheet,
                overwrite=args.overwrite,
                create_archive=args.archive,
                allow_unverified_key_permissions=args.allow_unverified_key_permissions,
            )
            payload = {
                "analytic_csv": str(result.analytic_csv),
                "data_dictionary": str(result.data_dictionary),
                "qa_report": str(result.qa_report),
                "release_archive": str(result.release_archive) if result.release_archive else None,
            }
            _print_json(payload)
            return 0

        if args.command == "schema" and args.schema_command == "validate":
            loaded = load_schema(args.schema)
            _print_json(
                {
                    "status": "valid",
                    "schema_version": loaded.model.schema_version,
                    "collaboration_id": loaded.model.collaboration_id,
                    "key_alias": loaded.model.key_alias,
                    "key_version": loaded.model.key_version,
                    "sha256": loaded.sha256,
                }
            )
            return 0

        if args.command == "key" and args.key_command == "generate":
            record = generate_key(
                args.output,
                collaboration_id=args.collaboration_id,
                key_alias=args.alias,
                key_version=args.version,
            )
            _print_json(record.public_metadata())
            return 0

        if args.command == "key" and args.key_command == "import":
            record = import_key(
                args.secret_file,
                args.output,
                collaboration_id=args.collaboration_id,
                key_alias=args.alias,
                key_version=args.version,
                allow_unverified_permissions=args.allow_unverified_key_permissions,
            )
            _print_json(record.public_metadata())
            return 0

        if args.command == "key" and args.key_command == "inspect":
            record = load_key(
                args.key_file,
                allow_retired=True,
                allow_unverified_permissions=args.allow_unverified_key_permissions,
            )
            _print_json(record.public_metadata())
            return 0

        if args.command == "key" and args.key_command == "retire":
            record = retire_key(
                args.key_file,
                allow_unverified_permissions=args.allow_unverified_key_permissions,
            )
            _print_json(record.public_metadata())
            return 0

        if args.command == "key" and args.key_command == "rotate":
            current = load_key(
                args.current,
                allow_retired=False,
                allow_unverified_permissions=args.allow_unverified_key_permissions,
            )
            retired, created = rotate_key(
                args.current,
                args.output,
                new_alias=args.new_alias or current.key_alias,
                new_version=args.new_version,
                retire_current=True,
                allow_unverified_permissions=args.allow_unverified_key_permissions,
            )
            _print_json({"retired": retired.public_metadata(), "created": created.public_metadata()})
            return 0

        parser.error("Unsupported command")
    except DeidError as error:
        print(f"error: {error}", file=sys.stderr)
        return error.exit_code
    return 1
