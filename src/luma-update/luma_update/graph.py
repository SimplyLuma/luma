# SPDX-License-Identifier: Apache-2.0
"""The signed update graph (ADR-030 section 5) and target selection (section 6).

Parsing is strict: a graph that does not match schema 1 exactly is refused as a
whole rather than partly believed. Selection is a pure function of the verified
graph, the booted system, the device's local wariness and the clock, so every
rule is unit-tested without a network or rpm-ostree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re

from . import versions
from .versions import Version

__all__ = (
    "Graph", "Release", "Rollout", "RollbackTarget", "GraphError", "StaleGraph",
    "parse_graph", "check_freshness", "rollout_reach", "select_target", "select_switch_now",
    "Decision", "MAX_GRAPH_BYTES", "FRESHNESS_SECONDS", "FUTURE_TOLERANCE_SECONDS",
)

SCHEMA_VERSION = 1
MAX_GRAPH_BYTES = 1024 * 1024
MAX_RELEASES = 512
MAX_DISPLAY_NAME = 200
#: How old a signed graph may be. It bounds how long someone who can serve an
#: old but validly signed graph can hide newer releases (a freeze attack); the
#: release pipeline re-signs every channel's graph at least daily.
FRESHNESS_SECONDS = 3 * 24 * 3600
FUTURE_TOLERANCE_SECONDS = 24 * 3600
CHANNELS = ("stable", "beta", "nightly")
IMPORTANCE = ("normal", "security")

_COMMIT = re.compile(r"[0-9a-f]{64}\Z")
_ARCH = re.compile(r"[a-z0-9_]{1,16}\Z")


class GraphError(ValueError):
    """The graph is malformed or does not belong to this device's channel."""

    error_class = "graph"


class StaleGraph(GraphError):
    """The graph is too old, older than one already seen, or from the future."""

    error_class = "stale-graph"


@dataclass(frozen=True)
class Rollout:
    start_at: float
    start_percentage: float
    duration_minutes: int


@dataclass(frozen=True)
class RollbackTarget:
    version: Version
    commit: str


@dataclass(frozen=True)
class Release:
    version: Version
    commit: str
    released_at: float
    rollout: Rollout | None
    paused: bool = False
    deadend: bool = False
    deadend_reason: str | None = None
    barrier: bool = False
    importance: str = "normal"
    notes_url: str | None = None
    summary: str = ""
    download_bytes_estimate: int = 0
    rollback_to: RollbackTarget | None = None
    #: What people call the release ("Luma (Prairie, Beta 1)"). Optional and
    #: presentation only: selection never reads it, and a graph without it parses.
    display_name: str = ""


@dataclass(frozen=True)
class Graph:
    channel: str
    arch: str
    generated_at: float
    releases: tuple[Release, ...] = field(default_factory=tuple)

    def by_commit(self, commit: str) -> Release | None:
        for release in self.releases:
            if release.commit == commit:
                return release
        return None


def _timestamp(value, where: str) -> float:
    if not isinstance(value, str) or not value.endswith("Z") or len(value) > 40:
        raise GraphError(f"{where} must be an RFC 3339 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise GraphError(f"{where} is not a valid timestamp") from None
    return parsed.astimezone(timezone.utc).timestamp()


def _bool(obj: dict, key: str, where: str, default: bool | None = None) -> bool:
    if key not in obj and default is not None:
        return default
    value = obj.get(key)
    if not isinstance(value, bool):
        raise GraphError(f"{where}.{key} must be true or false")
    return value


def _text(value, where: str, maximum: int, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise GraphError(f"{where} must be a single-line string of at most {maximum} characters")
    return value


def _https(value, where: str) -> str | None:
    if value is None:
        return None
    text = _text(value, where, 2048)
    if not text.startswith("https://") or any(c.isspace() for c in text):
        raise GraphError(f"{where} must be an https URL")
    return text


def _commit(value, where: str) -> str:
    if not isinstance(value, str) or not _COMMIT.match(value):
        raise GraphError(f"{where} must be a 64-character lowercase OSTree checksum")
    return value


def _rollout(value, where: str) -> Rollout | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise GraphError(f"{where} must be an object or null")
    start = _timestamp(value.get("start_at"), f"{where}.start_at")
    percentage = value.get("start_percentage")
    if isinstance(percentage, bool) or not isinstance(percentage, (int, float)) or not 0 <= percentage <= 1:
        raise GraphError(f"{where}.start_percentage must be a number from 0 to 1")
    duration = value.get("duration_minutes")
    if isinstance(duration, bool) or not isinstance(duration, int) or not 0 <= duration <= 525600:
        raise GraphError(f"{where}.duration_minutes must be a whole number of minutes up to a year")
    return Rollout(start, float(percentage), duration)


def _release(value, index: int) -> Release:
    where = f"releases[{index}]"
    if not isinstance(value, dict):
        raise GraphError(f"{where} must be an object")
    try:
        version = versions.parse(value.get("version"))
    except versions.InvalidVersion as error:
        raise GraphError(f"{where}.version: {error}") from None
    importance = value.get("importance", "normal")
    if importance not in IMPORTANCE:
        raise GraphError(f"{where}.importance must be one of {', '.join(IMPORTANCE)}")
    size = value.get("download_bytes_estimate", 0)
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise GraphError(f"{where}.download_bytes_estimate must be a non-negative integer")
    rollback = value.get("rollback_to")
    rollback_target = None
    if rollback is not None:
        if not isinstance(rollback, dict):
            raise GraphError(f"{where}.rollback_to must be an object or null")
        try:
            rollback_version = versions.parse(rollback.get("version"))
        except versions.InvalidVersion as error:
            raise GraphError(f"{where}.rollback_to.version: {error}") from None
        rollback_target = RollbackTarget(rollback_version, _commit(rollback.get("commit"), f"{where}.rollback_to.commit"))
    deadend = _bool(value, "deadend", where, False)
    if rollback_target is not None and not deadend:
        raise GraphError(f"{where}.rollback_to is only meaningful on a deadend release")
    return Release(
        version=version,
        commit=_commit(value.get("commit"), f"{where}.commit"),
        released_at=_timestamp(value.get("released_at"), f"{where}.released_at"),
        rollout=_rollout(value.get("rollout"), f"{where}.rollout"),
        paused=_bool(value, "paused", where, False),
        deadend=deadend,
        deadend_reason=_text(value.get("deadend_reason"), f"{where}.deadend_reason", 500, allow_none=True),
        barrier=_bool(value, "barrier", where, False),
        importance=importance,
        notes_url=_https(value.get("notes_url"), f"{where}.notes_url"),
        summary=_text(value.get("summary", ""), f"{where}.summary", 1000) or "",
        download_bytes_estimate=size,
        rollback_to=rollback_target,
        display_name=(_text(value.get("display_name"), f"{where}.display_name", MAX_DISPLAY_NAME,
                            allow_none=True) or "").strip(),
    )


def parse_graph(data: bytes) -> Graph:
    """Parse verified graph bytes. Call only after the signature has verified."""
    if len(data) > MAX_GRAPH_BYTES:
        raise GraphError("graph is larger than 1 MiB")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise GraphError("graph is not UTF-8 JSON") from None
    if not isinstance(value, dict):
        raise GraphError("graph must be a JSON object")
    if value.get("schema_version") != SCHEMA_VERSION or isinstance(value.get("schema_version"), bool):
        raise GraphError(f"unsupported graph schema_version (expected {SCHEMA_VERSION})")
    channel = value.get("channel")
    if channel not in CHANNELS:
        raise GraphError("graph channel is not stable, beta or nightly")
    arch = value.get("arch")
    if not isinstance(arch, str) or not _ARCH.match(arch):
        raise GraphError("graph arch is invalid")
    releases_value = value.get("releases")
    if not isinstance(releases_value, list) or len(releases_value) > MAX_RELEASES:
        raise GraphError(f"graph releases must be a list of at most {MAX_RELEASES}")
    releases = tuple(_release(item, index) for index, item in enumerate(releases_value))
    seen_versions: set[Version] = set()
    seen_commits: set[str] = set()
    for release in releases:
        if release.version in seen_versions:
            raise GraphError(f"graph lists version {release.version} twice")
        if release.commit in seen_commits:
            raise GraphError(f"graph lists commit {release.commit[:12]} twice")
        seen_versions.add(release.version)
        seen_commits.add(release.commit)
    for release in releases:
        target = release.rollback_to
        if target is None:
            continue
        listed = next((r for r in releases if r.commit == target.commit), None)
        if listed is None or listed.version != target.version:
            raise GraphError(f"rollback_to of {release.version} must name a release in this graph")
        if not target.version < release.version:
            raise GraphError(f"rollback_to of {release.version} must be an older release")
    return Graph(channel=channel, arch=arch,
                 generated_at=_timestamp(value.get("generated_at"), "generated_at"),
                 releases=tuple(sorted(releases, key=lambda r: r.version)))


def check_freshness(graph: Graph, *, channel: str, arch: str, now: float,
                    last_generated_at: float | None) -> None:
    if graph.channel != channel:
        raise GraphError(f"graph is for channel {graph.channel}, expected {channel}")
    if graph.arch != arch:
        raise GraphError(f"graph is for {graph.arch}, this computer is {arch}")
    if graph.generated_at > now + FUTURE_TOLERANCE_SECONDS:
        raise StaleGraph("graph is dated in the future; check this computer's clock")
    if graph.generated_at < now - FRESHNESS_SECONDS:
        raise StaleGraph(f"graph is more than {FRESHNESS_SECONDS // 86400} days old")
    if last_generated_at is not None and graph.generated_at < last_generated_at:
        raise StaleGraph("graph is older than one this computer has already seen")


def rollout_reach(release: Release, now: float) -> float:
    """Fraction of devices the release has reached, from 0 to 1."""
    rollout = release.rollout
    if now < _start_of(release):
        return 0.0
    if release.importance == "security" or rollout is None:
        return 1.0
    if rollout.duration_minutes == 0:
        return 1.0
    elapsed = (now - rollout.start_at) / (rollout.duration_minutes * 60.0)
    return rollout.start_percentage + (1.0 - rollout.start_percentage) * min(1.0, max(0.0, elapsed))


@dataclass(frozen=True)
class Decision:
    """What the device should do. ``action`` is none, update or rollback."""

    action: str
    release: Release | None = None
    reason: str = ""
    booted_release: Release | None = None
    waiting: Release | None = None  # newest release not yet reaching this device


def _start_of(release: Release) -> float:
    return release.rollout.start_at if release.rollout is not None else release.released_at


def _barrier(graph: Graph, booted: Version) -> Release | None:
    """The first non-deadend barrier newer than the booted version."""
    barriers = [r for r in graph.releases if r.barrier and not r.deadend and booted < r.version]
    return min(barriers, key=lambda r: r.version) if barriers else None


def select_target(graph: Graph, *, booted_version: Version, booted_commit: str, wariness: float,
                  now: float, do_not_retry=frozenset()) -> Decision:
    """Choose what to do. ``do_not_retry`` holds the commits not to offer now.

    A barrier in ``do_not_retry`` still bounds the path: nothing newer than it
    is offered, and when nothing older is left either the decision is
    ``barrier-blocked`` with the barrier as ``waiting`` -- never ``up-to-date``.
    The caller decides when a marked barrier may be tried again."""
    if not 0.0 <= wariness < 1.0:
        raise ValueError("wariness must be in [0, 1)")
    booted_release = graph.by_commit(booted_commit)
    barrier = _barrier(graph, booted_version)
    limit = barrier.version if barrier is not None else None
    barrier_marked = barrier is not None and barrier.commit in do_not_retry

    def within_barrier(release: Release) -> bool:
        return limit is None or not limit < release.version

    newer = [r for r in graph.releases
             if booted_version < r.version and not r.deadend and not r.paused
             and r.commit not in do_not_retry and within_barrier(r)]

    if booted_release is not None and booted_release.deadend:
        # Pulled release: leave it as soon as anything newer has started,
        # regardless of this device's place in the rollout.
        started = [r for r in newer if now >= _start_of(r)]
        if started:
            return Decision("update", started[-1], "booted-release-pulled", booted_release)
        target = booted_release.rollback_to
        if target is not None and target.commit not in do_not_retry:
            listed = graph.by_commit(target.commit)
            if listed is not None and not listed.deadend and listed.version < booted_version:
                return Decision("rollback", listed, "booted-release-pulled-rollback", booted_release)
        if barrier_marked:
            return Decision("none", None, "barrier-blocked", booted_release, barrier)
        return Decision("none", None, "booted-release-pulled-no-target", booted_release)

    eligible = [r for r in newer if wariness < rollout_reach(r, now)]
    waiting = None
    if newer and (not eligible or eligible[-1].version < newer[-1].version):
        waiting = newer[-1]
    if eligible:
        reason = "barrier" if limit is not None and eligible[-1].version == limit else "newest-eligible"
        return Decision("update", eligible[-1], reason, booted_release, waiting or (barrier if barrier_marked else None))
    if barrier_marked:
        return Decision("none", None, "barrier-blocked", booted_release, barrier)
    if waiting is not None:
        return Decision("none", None, "rollout-not-reached", booted_release, waiting)
    return Decision("none", None, "up-to-date", booted_release)


def select_switch_now(graph: Graph, *, wariness: float, now: float, do_not_retry=frozenset()) -> Decision:
    """The newest release a person may switch to immediately on a new channel.

    Used only when the person chooses "Switch now" onto a less advanced channel:
    a clean deploy of that channel's newest usable release, which may be older
    than the booted system. Barriers are upgrade-path stepping stones and do
    not apply to a clean deploy.
    """
    usable = [r for r in graph.releases
              if not r.deadend and not r.paused and r.commit not in do_not_retry
              and wariness < rollout_reach(r, now)]
    if not usable:
        return Decision("none", None, "no-usable-release")
    return Decision("rollback", usable[-1], "switch-now")
