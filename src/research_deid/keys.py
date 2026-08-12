from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import stat
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import KeyManagementError
from .version import KEY_FORMAT, __version__


def _decode_base64_secret(value: str) -> bytes:
    try:
        return base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
    except (ValueError, UnicodeEncodeError, binascii.Error) as exc:
        raise KeyManagementError("Key secret is not valid base64") from exc


def _validate_metadata_value(name: str, value: str) -> str:
    if not str(value).strip():
        raise KeyManagementError(f"{name} must not be blank")
    return str(value)


@dataclass(frozen=True)
class KeyRecord:
    collaboration_id: str
    key_alias: str
    key_version: str
    status: str
    secret: bytes
    created_utc: str
    retired_utc: str | None = None

    def public_metadata(self) -> dict[str, str | None]:
        return {
            "format": KEY_FORMAT,
            "collaboration_id": self.collaboration_id,
            "key_alias": self.key_alias,
            "key_version": self.key_version,
            "status": self.status,
            "created_utc": self.created_utc,
            "retired_utc": self.retired_utc,
        }

    def to_payload(self) -> dict[str, Any]:
        return {
            **self.public_metadata(),
            "created_by_tool_version": __version__,
            "secret_b64": base64.urlsafe_b64encode(self.secret).decode("ascii"),
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _atomic_json_write(path: Path, payload: dict[str, Any], *, replace_existing: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not replace_existing:
        raise KeyManagementError(f"Refusing to overwrite existing key file: {path}")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp_path = Path(temporary)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists() and not replace_existing:
            raise KeyManagementError(f"Refusing to overwrite existing key file: {path}")
        os.replace(tmp_path, path)
        if os.name == "posix":
            os.chmod(path, 0o600)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def verify_key_permissions(path: str | Path, *, allow_unverified: bool = False) -> None:
    key_path = Path(path).expanduser()
    if key_path.is_symlink():
        raise KeyManagementError("Key files must not be symbolic links")
    if not key_path.is_file():
        raise KeyManagementError(f"Key file does not exist: {key_path}")
    if os.name != "posix":
        if allow_unverified:
            return
        raise KeyManagementError(
            "This release cannot verify Windows ACLs. Production use must occur on a platform where "
            "owner-only key permissions can be verified; the testing override is not a security control."
        )
    info = key_path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise KeyManagementError("Key path is not a regular file")
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise KeyManagementError("Key file is not owned by the current operating-system user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise KeyManagementError("Key file permissions are not owner-only; expected mode 0600 or stricter")


def generate_key(
    path: str | Path,
    *,
    collaboration_id: str,
    key_alias: str,
    key_version: str,
) -> KeyRecord:
    record = KeyRecord(
        collaboration_id=_validate_metadata_value("collaboration_id", collaboration_id),
        key_alias=_validate_metadata_value("key_alias", key_alias),
        key_version=_validate_metadata_value("key_version", key_version),
        status="active",
        secret=secrets.token_bytes(32),
        created_utc=_utc_now(),
    )
    _atomic_json_write(Path(path).expanduser(), record.to_payload(), replace_existing=False)
    return record


def _parse_record(payload: dict[str, Any]) -> KeyRecord:
    required = {
        "format",
        "collaboration_id",
        "key_alias",
        "key_version",
        "status",
        "created_utc",
        "secret_b64",
    }
    missing = sorted(required - payload.keys())
    if missing:
        raise KeyManagementError(f"Key file is missing required metadata fields: {', '.join(missing)}")
    if payload["format"] != KEY_FORMAT:
        raise KeyManagementError("Unsupported key-file format")
    if payload["status"] not in {"active", "retired"}:
        raise KeyManagementError("Key status must be active or retired")
    secret = _decode_base64_secret(str(payload["secret_b64"]))
    if len(secret) != 32:
        raise KeyManagementError("Collaboration key must contain exactly 256 bits")
    return KeyRecord(
        collaboration_id=_validate_metadata_value("collaboration_id", str(payload["collaboration_id"])),
        key_alias=_validate_metadata_value("key_alias", str(payload["key_alias"])),
        key_version=_validate_metadata_value("key_version", str(payload["key_version"])),
        status=str(payload["status"]),
        secret=secret,
        created_utc=str(payload["created_utc"]),
        retired_utc=None if payload.get("retired_utc") is None else str(payload["retired_utc"]),
    )


def load_key(
    path: str | Path,
    *,
    collaboration_id: str | None = None,
    key_alias: str | None = None,
    key_version: str | None = None,
    allow_retired: bool = False,
    allow_unverified_permissions: bool = False,
) -> KeyRecord:
    key_path = Path(path).expanduser()
    verify_key_permissions(key_path, allow_unverified=allow_unverified_permissions)
    try:
        payload = json.loads(key_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise KeyManagementError(f"Unable to read key file: {exc}") from exc
    if not isinstance(payload, dict):
        raise KeyManagementError("Key-file root must be a JSON object")
    record = _parse_record(payload)
    if record.status == "retired" and not allow_retired:
        raise KeyManagementError("Retired collaboration keys cannot authorize new exports")
    expected = {
        "collaboration_id": collaboration_id,
        "key_alias": key_alias,
        "key_version": key_version,
    }
    actual = {
        "collaboration_id": record.collaboration_id,
        "key_alias": record.key_alias,
        "key_version": record.key_version,
    }
    mismatches = [name for name, value in expected.items() if value is not None and value != actual[name]]
    if mismatches:
        raise KeyManagementError("Key metadata does not match the transformation schema: " + ", ".join(mismatches))
    return record


def retire_key(path: str | Path, *, allow_unverified_permissions: bool = False) -> KeyRecord:
    key_path = Path(path).expanduser()
    record = load_key(key_path, allow_retired=True, allow_unverified_permissions=allow_unverified_permissions)
    if record.status == "retired":
        return record
    retired = replace(record, status="retired", retired_utc=_utc_now())
    _atomic_json_write(key_path, retired.to_payload(), replace_existing=True)
    return retired


def rotate_key(
    current_path: str | Path,
    new_path: str | Path,
    *,
    new_alias: str,
    new_version: str,
    retire_current: bool = True,
    allow_unverified_permissions: bool = False,
) -> tuple[KeyRecord, KeyRecord]:
    current = load_key(
        current_path,
        allow_retired=False,
        allow_unverified_permissions=allow_unverified_permissions,
    )
    if new_alias == current.key_alias and new_version == current.key_version:
        raise KeyManagementError("Rotation must use a new key alias or version")
    created = generate_key(
        new_path,
        collaboration_id=current.collaboration_id,
        key_alias=new_alias,
        key_version=new_version,
    )
    if not retire_current:
        return current, created
    try:
        retired = retire_key(current_path, allow_unverified_permissions=allow_unverified_permissions)
    except Exception:
        Path(new_path).expanduser().unlink(missing_ok=True)
        raise
    return retired, created


def import_key(
    secret_path: str | Path,
    output_path: str | Path,
    *,
    collaboration_id: str,
    key_alias: str,
    key_version: str,
    allow_unverified_permissions: bool = False,
) -> KeyRecord:
    source = Path(secret_path).expanduser()
    verify_key_permissions(source, allow_unverified=allow_unverified_permissions)
    raw = source.read_bytes()
    if len(raw) == 32:
        secret = raw
    else:
        try:
            text = raw.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise KeyManagementError(
                "Imported key material must be 32 raw bytes, 64 hexadecimal characters, or base64"
            ) from exc
        try:
            if len(text) == 64:
                secret = bytes.fromhex(text)
            else:
                secret = _decode_base64_secret(text)
        except (ValueError, KeyManagementError) as exc:
            raise KeyManagementError(
                "Imported key material must be 32 raw bytes, 64 hexadecimal characters, or base64"
            ) from exc
    if len(secret) != 32:
        raise KeyManagementError("Imported collaboration key must contain exactly 256 bits")
    record = KeyRecord(
        collaboration_id=_validate_metadata_value("collaboration_id", collaboration_id),
        key_alias=_validate_metadata_value("key_alias", key_alias),
        key_version=_validate_metadata_value("key_version", key_version),
        status="active",
        secret=secret,
        created_utc=_utc_now(),
    )
    _atomic_json_write(Path(output_path).expanduser(), record.to_payload(), replace_existing=False)
    return record
