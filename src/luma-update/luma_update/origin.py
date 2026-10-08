# SPDX-License-Identifier: Apache-2.0
"""Which Luma channel an rpm-ostree origin refspec follows, if any."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .config import CHANNELS, Settings

__all__ = ("ChannelOrigin", "parse_origin", "refspec_for")

_REMOTE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


@dataclass(frozen=True)
class ChannelOrigin:
    remote: str
    ref: str
    channel: str
    arch: str

    @property
    def refspec(self) -> str:
        return f"{self.remote}:{self.ref}"


def parse_origin(origin: str, settings: Settings) -> ChannelOrigin | None:
    """Return the channel for ``luma:luma/1/x86_64/<channel>`` origins, else None.

    Only the image's ``luma`` remote and the configured ref shape count: a
    deployment from any other remote or ref is not Luma's to update.
    """
    if not isinstance(origin, str) or ":" not in origin:
        return None
    remote, ref = origin.split(":", 1)
    if not _REMOTE.match(remote) or remote != settings.stable_remote:
        return None
    for channel in CHANNELS:
        for arch in ("x86_64", "aarch64"):
            if ref == settings.ref(channel, arch):
                return ChannelOrigin(remote, ref, channel, arch)
    return None


def refspec_for(channel: str, arch: str, settings: Settings) -> str:
    return f"{settings.remote_for(channel)}:{settings.ref(channel, arch)}"
