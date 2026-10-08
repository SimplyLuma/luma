"""Strict, import-safe validation for the privileged system Mod gate."""

from __future__ import annotations

from typing import Any

from .errors import TransactionError

RECOVERY_SCHEMA = "org.luma.mod-recovery-readiness/v0.1"
RECOVERY_HEALTH_UNIT = "luma-mod-boot-promote.service"
RECOVERY_BOOT_ATTEMPT_LIMIT = 2
POLKIT_CHECK_AUTHORIZATION_SIGNATURE = "((sa{sv})sa{ss}us)"


def validate_recovery_record(value: Any) -> bool:
    """Accept only the recovery contract implemented by the shipped units."""

    return (
        isinstance(value, dict)
        and set(value) == {
            "schema", "known_good_checksum", "health_unit", "boot_attempt_limit"
        }
        and value["schema"] == RECOVERY_SCHEMA
        and isinstance(value["known_good_checksum"], str)
        and len(value["known_good_checksum"]) >= 32
        and value["health_unit"] == RECOVERY_HEALTH_UNIT
        and value["boot_attempt_limit"] == RECOVERY_BOOT_ATTEMPT_LIMIT
    )


def polkit_authorized(value: Any) -> bool:
    """Unwrap the real Gio proxy method-result tuple and validate its shape."""

    if (
        not isinstance(value, tuple)
        or len(value) != 1
        or not isinstance(value[0], tuple)
        or len(value[0]) != 3
    ):
        raise TransactionError("PolicyKit returned an invalid authorization result")
    authorized, challenge, details = value[0]
    if (
        not isinstance(authorized, bool)
        or not isinstance(challenge, bool)
        or not isinstance(details, dict)
        or any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in details.items()
        )
    ):
        raise TransactionError("PolicyKit returned invalid authorization fields")
    return authorized
