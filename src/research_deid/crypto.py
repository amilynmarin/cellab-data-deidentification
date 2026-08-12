from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable

import pandas as pd

from .version import ALGORITHM_VERSION


def is_missing(value: Any) -> bool:
    if value is None or value is pd.NA:
        return True
    try:
        result = pd.isna(value)
    except Exception:
        return False
    if hasattr(result, "shape") and getattr(result, "shape", ()) != ():
        return False
    try:
        return bool(result)
    except (TypeError, ValueError):
        return False


def canonical_value(value: Any) -> str:
    """Return a typed, stable representation without normalizing substantive text."""
    if is_missing(value):
        raise ValueError("Missing values cannot be canonicalized")
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, bool):
        return "b:true" if value else "b:false"
    if isinstance(value, int) and not isinstance(value, bool):
        return f"i:{value}"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Non-finite values cannot be canonicalized")
        return f"f:{format(value, '.17g')}"
    if isinstance(value, Decimal):
        return f"d:{format(value, 'f')}"
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        return f"dt:{value.isoformat(timespec='microseconds')}"
    if isinstance(value, date):
        return f"date:{value.isoformat()}"
    if isinstance(value, bytes):
        return "bytes:" + base64.urlsafe_b64encode(value).decode("ascii")
    text = unicodedata.normalize("NFC", str(value))
    return "s:" + text


def canonical_sequence(values: Iterable[Any]) -> list[str]:
    return [canonical_value(value) for value in values]


def _message(purpose: str, context: dict[str, Any], components: Iterable[Any]) -> bytes:
    payload = {
        "algorithm": ALGORITHM_VERSION,
        "purpose": purpose,
        "context": context,
        "components": canonical_sequence(components),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def keyed_digest(secret: bytes, purpose: str, context: dict[str, Any], components: Iterable[Any]) -> bytes:
    return hmac.new(secret, _message(purpose, context, components), hashlib.sha256).digest()


def uniform_int(
    secret: bytes,
    purpose: str,
    context: dict[str, Any],
    components: Iterable[Any],
    low: int,
    high: int,
    *,
    attempt: int = 0,
) -> int:
    """Draw an unbiased deterministic integer using HMAC rejection sampling."""
    if high < low:
        raise ValueError("high must be at least low")
    width = high - low + 1
    ceiling = (1 << 64) - ((1 << 64) % width)
    base = list(components)
    block = 0
    while True:
        digest = keyed_digest(secret, purpose, context, [*base, attempt, block])
        candidate = int.from_bytes(digest[:8], "big", signed=False)
        if candidate < ceiling:
            return low + (candidate % width)
        block += 1


def derive_token(
    secret: bytes,
    context: dict[str, Any],
    *,
    domain: str,
    prefix: str,
    components: Iterable[Any],
    bytes_of_entropy: int = 16,
) -> str:
    if bytes_of_entropy < 16:
        raise ValueError("Tokens must retain at least 128 bits")
    digest = keyed_digest(secret, f"token:{domain}", context, components)
    encoded = base64.b32encode(digest[:bytes_of_entropy]).decode("ascii").rstrip("=").lower()
    safe_prefix = re.sub(r"[^A-Za-z0-9_-]", "_", prefix)
    return f"{safe_prefix}_{encoded}"


def derivation_context(
    *,
    collaboration_id: str,
    schema_version: str,
    key_alias: str,
    key_version: str,
) -> dict[str, str]:
    return {
        "collaboration_id": collaboration_id,
        "schema_version": schema_version,
        "key_alias": key_alias,
        "key_version": key_version,
    }


def derive_shift_weeks(
    secret: bytes,
    context: dict[str, Any],
    *,
    scope: str,
    components: Iterable[Any],
    min_weeks: int = 4,
    max_weeks: int = 52,
) -> int:
    possibilities = 2 * (max_weeks - min_weeks + 1)
    draw = uniform_int(
        secret,
        "whole-week-date-shift",
        context,
        [scope, *list(components)],
        0,
        possibilities - 1,
    )
    magnitude = min_weeks + draw // 2
    return -magnitude if draw % 2 == 0 else magnitude


def jitter_candidates(
    secret: bytes,
    context: dict[str, Any],
    *,
    group: str,
    anchor_components: Iterable[Any],
) -> Iterable[int]:
    """Yield every integer from -180 to +180 in a keyed deterministic order."""
    components = [group, *list(anchor_components)]
    size = 361
    start = uniform_int(secret, "timestamp-jitter-start", context, components, 0, size - 1)
    coprime_steps = [step for step in range(1, size) if math.gcd(step, size) == 1]
    step_index = uniform_int(
        secret,
        "timestamp-jitter-step",
        context,
        components,
        0,
        len(coprime_steps) - 1,
    )
    step = coprime_steps[step_index]
    for position in range(size):
        yield -180 + ((start + position * step) % size)
