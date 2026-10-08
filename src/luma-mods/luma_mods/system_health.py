"""Fixed boot-health entry points for a journaled system Mod candidate."""

from __future__ import annotations

import argparse
import subprocess

from .errors import LumaModsError, TransactionError
from .system_coordinator import SystemTransactionCoordinator
from .system_state import SystemStateStore


def _unit_active(unit: str) -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", "--quiet", unit],
        check=False,
        timeout=30,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C.UTF-8"},
    )
    return result.returncode == 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Observe and health-gate a Luma candidate deployment.")
    root.add_argument("action", choices=("observe", "promote", "watchdog"))
    return root


def main(arguments: list[str] | None = None) -> int:
    args = parser().parse_args(arguments)
    state = SystemStateStore()
    try:
        pending = state.read()["pending"]
        if pending is None:
            return 0
        coordinator = SystemTransactionCoordinator(state=state)
        if args.action == "observe":
            coordinator.observe_boot()
        elif args.action == "promote":
            if not _unit_active("graphical.target") or not _unit_active("display-manager.service"):
                raise TransactionError("graphical boot health is not ready")
            coordinator.promote_booted_candidate()
        else:
            current = state.read()["pending"]
            if current is not None and current["phase"] == "candidate-booted":
                coordinator.rollback(current["candidate_checksum"])
        return 0
    except (LumaModsError, OSError, subprocess.SubprocessError) as error:
        print(f"luma-mod-system-health: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
