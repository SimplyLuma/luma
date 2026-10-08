# SPDX-License-Identifier: Apache-2.0
"""luma-update-boot: what became of a staged update, run at boot and by greenboot.

    luma-update-boot reconcile   # luma-update-boot.service, every boot
    luma-update-boot green       # /etc/greenboot/green.d/ : health checks passed
    luma-update-boot red         # /etc/greenboot/red.d/   : a required check failed

It uses the same locked state file as luma-updated and reads deployments
from rpm-ostree over D-Bus. It never changes deployments: greenboot's own
fallback performs the rollback, and this records it, marks the failed commit
as not to be retried, and leaves the notice for the person's session.
"""

from __future__ import annotations

import logging
import sys

from .config import Paths, load_settings
from .engine import Engine, ensure_runtime_dir
from .http import Http

__all__ = ("main",)


class _NoProbes:
    class _Network:
        available, metered, connectivity = False, None, 0

    class _Power:
        available, on_battery, percentage = False, False, None

    def network(self):
        return self._Network()

    def power(self):
        return self._Power()


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="luma-update-boot: %(message)s", stream=sys.stderr)
    argv = list(sys.argv[1:] if argv is None else argv)
    action = argv[0] if argv else "reconcile"
    if action not in ("reconcile", "green", "red"):
        print("usage: luma-update-boot reconcile|green|red", file=sys.stderr)
        return 2
    from .rpmostree import RpmOstree
    paths = Paths.from_environment()
    settings = load_settings(paths)
    ensure_runtime_dir(paths)
    engine = Engine(paths, settings, RpmOstree(), _NoProbes(), Http(allow_insecure=settings.allow_insecure_urls))
    health = None if action == "reconcile" else action
    try:
        outcome = engine.reconcile_boot(health)
    except Exception as error:
        # Never fail a boot or a greenboot hook because the agent could not record.
        logging.getLogger("luma-update").error("could not reconcile: %s", error)
        return 0
    print(outcome)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
