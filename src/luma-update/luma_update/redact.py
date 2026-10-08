# SPDX-License-Identifier: Apache-2.0
"""Keep the preview credential out of the journal and out of D-Bus errors.

rpm-ostree's transaction messages and errors name the repository URL they
pulled from, and a preview repository URL contains the device's credential
(``/os/preview/<credential>/repo``). Everything the agent logs passes through
``RedactingFilter`` on the ``luma-update`` logger, and every error text the
daemon returns to a D-Bus caller passes through ``redact``.
"""

from __future__ import annotations

import logging
import re

__all__ = ("redact", "RedactingFilter", "install", "PLACEHOLDER")

PLACEHOLDER = "<credential>"
_PREVIEW_PATH = re.compile(r"(/os/preview/)[^/\s'\"<>]+")
_secrets_provider = None


def redact(text, secrets=None) -> str:
    """``text`` with every preview-path segment and every known credential replaced."""
    text = str(text)
    text = _PREVIEW_PATH.sub(r"\1" + PLACEHOLDER, text)
    if secrets is None:
        secrets = _current_secrets()
    for secret in secrets:
        if isinstance(secret, str) and len(secret) >= 16:
            text = text.replace(secret, PLACEHOLDER)
    return text


def _current_secrets() -> tuple[str, ...]:
    if _secrets_provider is None:
        return ()
    try:
        return tuple(_secrets_provider())
    except Exception:  # never let redaction break logging
        return ()


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        clean = redact(message)
        if clean != message:
            record.msg, record.args = clean, None
        return True


def install(logger: logging.Logger, secrets_provider=None) -> None:
    """Attach the filter once, and name where the installed credential is read from."""
    global _secrets_provider
    if secrets_provider is not None:
        _secrets_provider = secrets_provider
    if not any(isinstance(item, RedactingFilter) for item in logger.filters):
        logger.addFilter(RedactingFilter())
