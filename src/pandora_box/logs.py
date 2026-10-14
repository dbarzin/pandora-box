"""Structured JSON logging."""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOGGER_NAME = "pandora_box"


class JsonFormatter(logging.Formatter):
    """Format each record as one JSON object per line."""

    def __init__(self, kiosk: str) -> None:
        super().__init__()
        self.kiosk = kiosk

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "kiosk": self.kiosk,
            "event": record.getMessage(),
        }
        data.update(getattr(record, "fields", {}))
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        # ensure_ascii keeps undecodable file names (surrogate escapes) serializable
        return json.dumps(data, default=str)


def setup_logging(kiosk: str, level: str, file: Path | None) -> None:
    """Send the application logs to a JSON lines file (or nowhere when file is None)."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    for old in list(logger.handlers):
        logger.removeHandler(old)
        old.close()
    handler: logging.Handler
    if file is None:
        handler = logging.NullHandler()
    else:
        file.parent.mkdir(parents=True, exist_ok=True)
        # WatchedFileHandler reopens the file after logrotate moves it
        handler = logging.handlers.WatchedFileHandler(file, encoding="utf-8")
    handler.setFormatter(JsonFormatter(kiosk))
    logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Log an event with structured fields."""
    logger.log(level, event, extra={"fields": fields})
