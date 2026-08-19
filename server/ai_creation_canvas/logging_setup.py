"""Rotating file-backed logging for the serve entry points.

Not invoked by ``create_app`` so tests and embedding hosts keep their own
logging configuration.
"""

from __future__ import annotations

import datetime
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S,%f%z"
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5
_configured = False


def logs_dir(data_dir: Path | str) -> Path:
    return Path(data_dir) / "logs"


class _EveryLineFormatter(logging.Formatter):
    """Re-emit multi-line records so every physical line carries the header."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        if datefmt is None:
            return super().formatTime(record, datefmt)
        # datetime.strftime supports %f and %z, which time.strftime does not
        # on some platforms (e.g. macOS renders them literally).
        return datetime.datetime.fromtimestamp(record.created).astimezone().strftime(datefmt)

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        if "\n" not in rendered:
            return rendered
        prefix = f"{self.formatTime(record, self.datefmt)} {record.levelname} {record.name} "
        first_line, *rest = rendered.split("\n")
        return "\n".join([first_line, *(prefix + line for line in rest)])


def configure_logging(data_dir: Path | str) -> Path | None:
    """Attach console + rotating-file handlers to the root logger (idempotent).

    Returns the log file path, or None when the logs directory cannot be
    created (server keeps running with console-only output).
    """
    global _configured
    target = logs_dir(data_dir)
    if _configured:
        return target / "server.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = _EveryLineFormatter(_FORMAT, datefmt=_DATEFMT)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)
    try:
        target.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            target / "server.log",
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
            delay=True,
        )
        handler.setFormatter(formatter)
        root.addHandler(handler)
    except OSError as error:
        logging.getLogger(__name__).warning(
            "file logging unavailable, continuing with console only: %s", error
        )
        return None
    _configured = True
    return target / "server.log"
