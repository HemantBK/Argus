"""Structured logs: one JSON object per line on stdout, which Cloud Logging reads as a structured entry.

Every AI call is logged with log_event (E8): the event name plus its fields (model, prompt hash, sizes,
latency, tokens, cost estimate, verdict, run id). configure_logging attaches the JSON handler to the "argus"
logger tree at INFO; without it the records would fall through to the root logger, which uvicorn leaves at
WARNING, and be dropped. The API calls it at import.
"""

import json
import logging
import sys
from typing import Any

ROOT_LOGGER = "argus"
LEVEL = logging.INFO
_FIELDS = "json_fields"


def log_event(logger: logging.Logger, event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    """One record (INFO unless `level` says otherwise) whose JSON line holds `event` and every field."""
    logger.log(level, event, extra={_FIELDS: fields})


class JsonLineFormatter(logging.Formatter):
    """severity / logger / message (Cloud Logging's field names), then the event's own fields."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = getattr(record, _FIELDS, None)
        if fields is not None:
            clash = set(fields) & set(entry)
            if clash:
                raise ValueError(f"log fields {sorted(clash)} would overwrite the entry's own fields")
            entry.update(fields)
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


class _JsonHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Marker type, so configure_logging can tell its own handler apart from any other."""


def configure_logging() -> logging.Logger:
    """Send the "argus" loggers to stdout as JSON lines at INFO. Safe to call more than once."""
    logger = logging.getLogger(ROOT_LOGGER)
    logger.setLevel(LEVEL)
    if not any(isinstance(h, _JsonHandler) for h in logger.handlers):
        handler = _JsonHandler(sys.stdout)
        handler.setFormatter(JsonLineFormatter())
        logger.addHandler(handler)
    # The handler above is the one destination; propagating would print each line again wherever the root
    # logger has a handler.
    logger.propagate = False
    return logger
