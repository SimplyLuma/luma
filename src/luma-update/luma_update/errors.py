# SPDX-License-Identifier: Apache-2.0
"""Exceptions shared by the rpm-ostree client and the engine (no GLib needed)."""

from __future__ import annotations

__all__ = ("TransactionError", "BusyError")


class TransactionError(Exception):
    error_class = "transaction"


class BusyError(TransactionError):
    error_class = "busy"
