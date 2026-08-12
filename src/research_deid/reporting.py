from __future__ import annotations

import csv
import json
import math
import os
import re
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd

from .errors import OutputError


_NUMERIC_TEXT = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?$")
_DANGEROUS_PREFIXES = ("=", "+", "-", "@")


def _is_numeric_semantic(kind: str) -> bool:
    return kind in {"integer", "number", "offset"}


def escape_formula_like(
    frame: pd.DataFrame,
    semantic_types: dict[str, str],
) -> tuple[pd.DataFrame, dict[str, int]]:
    output = frame.copy(deep=True)
    counts: dict[str, int] = {}
    for column in output.columns:
        semantic = semantic_types.get(column, "any")
        changed = 0
        values: list[Any] = []
        for value in output[column].tolist():
            if not isinstance(value, str) or not value.startswith(_DANGEROUS_PREFIXES):
                values.append(value)
                continue
            if semantic == "offset":
                values.append(value)
                continue
            if _is_numeric_semantic(semantic) and _NUMERIC_TEXT.fullmatch(value):
                values.append(value)
                continue
            if semantic == "any" and _NUMERIC_TEXT.fullmatch(value):
                values.append(value)
                continue
            values.append("'" + value)
            changed += 1
        if changed:
            output[column] = pd.Series(values, index=output.index, dtype="object")
            counts[column] = changed
    return output, counts


def deterministic_json(payload: Any) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
            separators=(",", ": "),
        )
        + "\n"
    ).encode("utf-8")


def _secure_temp_path(directory: Path, name: str) -> Path:
    fd, temporary = tempfile.mkstemp(prefix=f".{name}.", dir=directory)
    if os.name == "posix":
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
    os.close(fd)
    return Path(temporary)


def write_csv(path: Path, frame: pd.DataFrame, *, overwrite: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise OutputError(f"Refusing to overwrite existing output: {path.name}")
    temporary = _secure_temp_path(path.parent, path.name)
    try:
        frame.to_csv(
            temporary,
            index=False,
            encoding="utf-8",
            lineterminator="\n",
            na_rep="",
            quoting=csv.QUOTE_MINIMAL,
        )
        os.replace(temporary, path)
        if os.name == "posix":
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def write_bytes(path: Path, data: bytes, *, overwrite: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise OutputError(f"Refusing to overwrite existing output: {path.name}")
    temporary = _secure_temp_path(path.parent, path.name)
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
        if os.name == "posix":
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def write_release_archive(
    path: Path,
    members: list[Path],
    *,
    overwrite: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise OutputError(f"Refusing to overwrite existing output: {path.name}")
    temporary = _secure_temp_path(path.parent, path.name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for member in sorted(members, key=lambda item: item.name):
                info = zipfile.ZipInfo(member.name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (0o600 & 0xFFFF) << 16
                archive.writestr(info, member.read_bytes())
        os.replace(temporary, path)
        if os.name == "posix":
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def normalize_json_value(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
