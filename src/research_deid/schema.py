from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError as PydanticValidationError

from .errors import SchemaError
from .models import ToolSchema


@dataclass(frozen=True)
class LoadedSchema:
    path: Path
    raw_bytes: bytes
    sha256: str
    model: ToolSchema


def load_schema(path: str | Path) -> LoadedSchema:
    schema_path = Path(path).expanduser().resolve()
    if not schema_path.is_file():
        raise SchemaError(f"Schema file does not exist: {schema_path}")
    try:
        raw = schema_path.read_bytes()
        payload = yaml.safe_load(raw)
    except Exception as exc:  # pragma: no cover - parser-specific detail
        raise SchemaError(f"Unable to read schema: {exc}") from exc
    if not isinstance(payload, dict):
        raise SchemaError("Schema root must be a mapping")
    try:
        model = ToolSchema.model_validate(payload)
    except PydanticValidationError as exc:
        problems = []
        for item in exc.errors(include_url=False):
            location = ".".join(str(part) for part in item["loc"])
            problems.append(f"{location}: {item['msg']}")
        raise SchemaError("Invalid transformation schema:\n- " + "\n- ".join(problems)) from exc
    return LoadedSchema(
        path=schema_path,
        raw_bytes=raw,
        sha256=hashlib.sha256(raw).hexdigest(),
        model=model,
    )
