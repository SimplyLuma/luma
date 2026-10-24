#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Supply libdnf5's documented forcearch transaction setting to Lorax 44."""
import platform
import sys
from pathlib import Path


def configure_target(base, target, host):
    if base is None:
        return base
    if base.get_vars().get_value('arch') != target:
        raise RuntimeError('Lorax DNF package architecture differs from target')
    if Path(str(base.get_config().installroot)).resolve() == Path('/'):
        raise RuntimeError('Lorax must install into an isolated runtime root')
    # The arch variable selects compatible packages. RPM additionally needs
    # ignorearch for a foreign installroot, exactly like dnf5 --forcearch:
    # https://dnf5.readthedocs.io/en/stable/tutorial/bindings/python3/session.html
    base.get_config().get_ignorearch_option().set(target != host)
    return base


def main():
    from pylorax.cmdline import lorax
    try:
        target = sys.argv[sys.argv.index('--buildarch') + 1]
    except (ValueError, IndexError):
        raise SystemExit('run-lorax.py requires explicit --buildarch')
    if target not in ('x86_64', 'aarch64'):
        raise SystemExit('unsupported Lorax target architecture')
    original = lorax.get_dnf_base_object

    def target_base(*args, **kwargs):
        return configure_target(original(*args, **kwargs), target, platform.machine())

    lorax.get_dnf_base_object = target_base
    return lorax.main()


if __name__ == '__main__':
    sys.exit(main())
