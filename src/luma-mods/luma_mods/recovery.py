"""Session-start recovery for interrupted bounded user Mod transactions."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import AbstractSet

from .errors import LumaModsError, TransactionError
from .lifecycle import PreferenceLifecycle
from .paths import UserPaths
from .profile import FilePreferenceBackend
from .registry import SUPPORTED_PREFERENCE_DOMAINS
from .state import StateStore


def recover_pending(
    *,
    state_root: Path,
    preference_file: Path,
    supported_domains: AbstractSet[str],
) -> bool:
    store = StateStore(state_root)
    current = store.read()
    pending = current["pending_transaction"]
    if pending is None:
        return False
    if pending["backend"] != "luma-preferences-v1":
        raise TransactionError(
            f"cannot recover unsupported backend {pending['backend']}"
        )
    recovery_domains = set(pending["backend_recovery"])
    unsupported = recovery_domains - set(supported_domains)
    if unsupported:
        raise TransactionError(
            "cannot recover unregistered Luma preference domains: "
            + ", ".join(sorted(unsupported))
        )
    lifecycle = PreferenceLifecycle(
        store,
        FilePreferenceBackend(preference_file, set(supported_domains)),
    )
    lifecycle.recover()
    return True


def main() -> int:
    try:
        paths = UserPaths.current()
        recovered = recover_pending(
            state_root=paths.state_root,
            preference_file=paths.preference_file,
            supported_domains=SUPPORTED_PREFERENCE_DOMAINS,
        )
        if recovered:
            print("Recovered an interrupted Luma Mod preference transaction.")
        return 0
    except LumaModsError as error:
        print(f"luma-mod-recover: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
