# SPDX-License-Identifier: Apache-2.0
"""The ModemManager modem that Phone and Messages use."""

from __future__ import annotations

import re
from typing import Callable


def modem_indexes(listing: str) -> tuple[str, ...]:
    """Modem indexes in `mmcli -L` output, in order, without repeats."""
    return tuple(dict.fromkeys(re.findall(r"/Modem/(\d+)", listing)))


def _unusable(status: str | None) -> bool:
    if status is None:
        return True
    lowered = status.lower()
    if "sim-missing" in lowered:
        return True
    for line in lowered.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip().endswith(".generic.state") and value.strip() == "failed":
            return True
    return False


def choose_modem(listing: str, status_of: Callable[[str], str | None]
                 ) -> tuple[tuple[str, ...], str | None, str | None]:
    """Return (all indexes, the modem to use or None, its key/value status).

    One modem is used whatever its state, so the caller can say why it is not
    ready. Of several, the one that is not failed is used: a dual-SIM modem
    with one SIM lists a second modem for the empty stack, which ModemManager
    leaves failed (sim-missing), and that is not a choice for the user. With
    more than one usable modem there is no answer here.
    """
    indexes = modem_indexes(listing)
    if len(indexes) == 1:
        return indexes, indexes[0], status_of(indexes[0])
    statuses = {index: status_of(index) for index in indexes}
    usable = [index for index in indexes if not _unusable(statuses[index])]
    if len(usable) == 1:
        return indexes, usable[0], statuses[usable[0]]
    return indexes, None, None
