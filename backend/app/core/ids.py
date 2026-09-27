"""Deterministic identifiers and content hashes."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

_ID_HEX_LENGTH = 16


def _default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def canonical_json(payload: Any) -> str:
    """Serialize ``payload`` deterministically (sorted keys, no whitespace)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_default)


def content_hash(payload: Any) -> str:
    """SHA-256 of the canonical JSON form of ``payload``."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: object) -> str:
    """Build a deterministic identifier such as ``pair_3f9a...`` from its components."""
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:_ID_HEX_LENGTH]}"
