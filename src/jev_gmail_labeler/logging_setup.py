"""Logging configuration with secret redaction."""

import logging
import os
import sys
from collections.abc import Mapping
from typing import TextIO

MIN_SECRET_CHARS = 8
_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Remember a secret so it is replaced by ``***`` in every log record."""
    if value and len(value) >= MIN_SECRET_CHARS:
        _secrets.add(value)


class RedactingFilter(logging.Filter):
    """Replace registered secrets in log messages with ``***``."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for secret in sorted(_secrets, key=len, reverse=True):
            message = message.replace(secret, '***')
        record.msg = message
        record.args = ()
        return True


def configure_logging(
    level: str, *, environ: Mapping[str, str] = os.environ, stream: TextIO = sys.stderr
) -> None:
    """Send logs to ``stream``, replacing any existing root handlers."""
    fmt = '%(levelname)s %(name)s: %(message)s'
    if 'JOURNAL_STREAM' not in environ:  # journald adds its own timestamps
        fmt = '%(asctime)s ' + fmt
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(fmt))
    handler.addFilter(RedactingFilter())

    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(level)

    for name, quiet_level in (
        ('presidio-analyzer', logging.ERROR),
        ('presidio-anonymizer', logging.ERROR),
        ('googleapiclient.discovery_cache', logging.ERROR),
        ('typesafe_sdk', logging.WARNING),
    ):
        logging.getLogger(name).setLevel(quiet_level)
