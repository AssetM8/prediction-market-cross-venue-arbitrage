"""Structured logging with correlation context and secret redaction.

Each log record is emitted as one JSON object that carries the current correlation ID and,
when set, the venue, market, pair, opportunity and paper-order identifiers. Context is
carried in :mod:`contextvars`, so it follows asyncio tasks.

A :class:`RedactingFilter` scrubs anything that looks like a credential (PEM blocks,
bearer tokens, API-key assignments, 64-hex private keys, BIP-39-style seed phrases) from
both the message and structured fields before any handler sees the record.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Final

CONTEXT_FIELDS: Final = (
    "correlation_id",
    "venue",
    "market_id",
    "pair_id",
    "opportunity_id",
    "paper_order_id",
)

_context: dict[str, contextvars.ContextVar[str | None]] = {
    name: contextvars.ContextVar(name, default=None) for name in CONTEXT_FIELDS
}

REDACTED: Final = "[REDACTED]"

_SECRET_KEY_NAMES: Final = re.compile(
    r"(^|[_-])(api[_-]?key|secret|passphrase|private[_-]?key|password|signature|mnemonic|"
    r"seed[_-]?phrase|authorization|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"kalshi-access-signature|poly[_-]signature|poly[_-]passphrase|poly[_-]api[_-]key)($|[_-])",
    re.IGNORECASE,
)

_SECRET_PATTERNS: Final = (
    # PEM blocks (RSA / EC private keys).
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    # Authorization: Bearer <token>
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    # key=value / key: value assignments for secret-looking keys.
    re.compile(
        r"(?i)(?<![a-z0-9])(api[_-]?key|secret|passphrase|private[_-]?key|password|signature)"
        r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
    ),
    # Anthropic / OpenAI style keys.
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    # 32-byte hex secrets (EVM private keys), with or without 0x prefix.
    re.compile(r"\b(0x)?[0-9a-fA-F]{64}\b"),
)

# A string made only of 12-24 lowercase words of 3-8 letters resembles a BIP-39 seed
# phrase. Matching the whole string (not substrings) avoids redacting ordinary prose.
_SEED_PHRASE: Final = re.compile(r"\s*(?:[a-z]{3,8}\s+){11,23}[a-z]{3,8}\s*")


def redact_text(text: str) -> str:
    """Remove secret-looking substrings from ``text``."""
    if _SEED_PHRASE.fullmatch(text):
        return REDACTED
    result = text
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 3:
            result = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", result)
        else:
            result = pattern.sub(REDACTED, result)
    return result


def redact_value(key: str, value: Any) -> Any:
    """Redact a structured field: secret-named keys are always masked."""
    if _SECRET_KEY_NAMES.search(key):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {str(k): redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact_value(key, item) for item in value]
    return value


class RedactingFilter(logging.Filter):
    """Scrub secrets from the message, arguments and ``extra`` fields of a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        record.msg = redact_text(message)
        record.args = None
        fields = getattr(record, "fields", None)
        if isinstance(fields, Mapping):
            record.fields = {str(k): redact_value(str(k), v) for k, v in fields.items()}
        return True


class ContextFilter(logging.Filter):
    """Attach the current correlation context to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        for name, var in _context.items():
            setattr(record, name, var.get())
        return True


class JsonFormatter(logging.Formatter):
    """Render a record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in CONTEXT_FIELDS:
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = value
        fields = getattr(record, "fields", None)
        if isinstance(fields, Mapping):
            payload["fields"] = dict(fields)
        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


class PlainFormatter(logging.Formatter):
    """Human-readable single-line format that still shows context."""

    def format(self, record: logging.LogRecord) -> str:
        context = " ".join(
            f"{name}={getattr(record, name)}"
            for name in CONTEXT_FIELDS
            if getattr(record, name, None) is not None
        )
        fields = getattr(record, "fields", None)
        extra = f" {json.dumps(dict(fields), default=str)}" if isinstance(fields, Mapping) else ""
        base = f"{record.levelname:<7} {record.name}: {record.getMessage()}"
        return f"{base} [{context}]{extra}" if context else f"{base}{extra}"


_configured = False


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """Install the structured handler on the root logger (idempotent)."""
    global _configured  # noqa: PLW0603 - process-wide logging setup happens once
    root = logging.getLogger()
    root.setLevel(level.upper())
    if _configured:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(ContextFilter())
    handler.addFilter(RedactingFilter())
    handler.setFormatter(JsonFormatter() if json_output else PlainFormatter())
    root.addHandler(handler)
    # httpx logs full request URLs at INFO; they are public but noisy.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    """Log ``message`` with structured ``fields`` (redacted by the handler filter)."""
    logger.log(level, message, extra={"fields": fields})


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def current_correlation_id() -> str | None:
    return _context["correlation_id"].get()


@contextmanager
def log_context(**values: str | None) -> Iterator[None]:
    """Bind context fields (e.g. ``venue="kalshi"``) for the duration of a block."""
    tokens = []
    for name, value in values.items():
        if name not in _context:
            raise KeyError(f"unknown log context field: {name}")
        tokens.append((_context[name], _context[name].set(value)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
