# SPDX-License-Identifier: Apache-2.0
"""The daemon: read today's calendar, publish the next event, keep it current."""

from __future__ import annotations

import logging
import sys
import threading
from datetime import datetime
from typing import Any

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from luma_semantic_broker.client import LiveExtensionPublisher  # noqa: E402

from . import extension  # noqa: E402
from .events import Upcoming, end_of_day, refresh_delay, select  # noqa: E402

LOGGER = logging.getLogger("luma-agenda")

#: Reads in flight at once. The ladder can outpace a calendar service that is
#: still starting, and every waiting attempt is waiting on the same thing.
MAX_CONCURRENT_READS = 3

class AgendaProducer:
    """Reads today's calendar and keeps the next event published.

    Not a Gio.Application subclass. A service that nobody activates never gets
    do_activate, so holding the application there meant the process started,
    found nothing keeping its loop alive, and exited cleanly — enabled, dead,
    and silent. The application is registered and held by main() instead, which
    is what the calls producer already does.
    """

    def __init__(self, application, *, reader=None, clock=None) -> None:
        self.application = application
        self._reader = reader or self._read_calendar
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._state: Upcoming | None = None
        # Counts the empty reads before anything has ever been shown, so a
        # producer that came up before the calendar backend looks again soon
        # instead of sleeping the full ceiling.
        self._warmup = 0
        self._published = False
        self._tick_source = 0
        # Reads run off the main loop, so each carries a number and a result
        # that has been overtaken is discarded rather than published late.
        self._generation = 0
        self._applied = 0
        self._reading = 0
        # The number of events the last logged read found. A read is logged
        # when that number changes or the read was slow, not every two minutes
        # on a day with nothing on it.
        self._logged_count: int | None = None
        self.publisher = LiveExtensionPublisher(
            application, extension.APPLICATION_ID, extension.EXTENSION_ID,
            self._extension,
        )
        self._tick()

    # -- Calendar ---------------------------------------------------------

    @staticmethod
    def _read_calendar(start: datetime, end: datetime):
        """Today's events, from the same backend the Calendar app reads.

        Calendar owns this data and its source list; this daemon does not open
        its own registry or invent a second idea of which calendars count.
        """
        from prairie_apps import calendar_backend

        # The timeout parameter is newer than some installed copies of the
        # backend, and passing it to one that predates it raises TypeError on
        # every read -- which does not show up as a slow island but as no
        # island at all, ever. Ask whether the copy on this machine has it.
        options = {}
        seconds = getattr(
            calendar_backend, "BACKGROUND_CONNECT_TIMEOUT_SECONDS", None)
        if seconds is not None:
            options["timeout"] = seconds
        return calendar_backend.list_events(start, end, **options)

    def _refresh(self) -> None:
        """Start a read. It finishes on the main loop, not here.

        Opening a calendar blocks, with a timeout of its own, and each source
        is opened in turn: on a cold session the first read was measured at
        60.1 seconds against 0.03 once the calendar service is up. Waiting for
        it inline meant the producer sat on that first answer for a minute
        while the retry ladder behind it could do nothing, and the upcoming
        event only reached the shelf a minute after login.

        Reading off the main loop lets the ladder keep its appointments. A read
        started once the calendar service is actually up returns immediately
        and publishes, whatever the first one is still waiting on, so the delay
        becomes however long the service itself takes rather than the sum of
        every timeout before it.
        """

        if self._reading >= MAX_CONCURRENT_READS:
            # Every attempt is still waiting on the same unready service.
            # Another would tell us nothing and would outlive its usefulness.
            return

        now = self._clock()
        self._generation += 1
        generation = self._generation
        self._reading += 1

        def read() -> None:
            started = GLib.get_monotonic_time()
            try:
                events = self._reader(now, end_of_day(now))
            except Exception as error:  # noqa: BLE001 - a broken calendar is not fatal
                # Never the event, never the calendar: only the failure's shape.
                LOGGER.warning("calendar read failed: %s", type(error).__name__)
                events = []
            elapsed = (GLib.get_monotonic_time() - started) / 1e6
            self._deliver(generation, events, now, elapsed)

        self._spawn(read)

    @staticmethod
    def _spawn(work) -> None:
        """Overridden in tests, which have no use for a second thread."""
        threading.Thread(target=work, name="luma-agenda-read", daemon=True).start()

    def _deliver(self, *result) -> None:
        """Hand a finished read back to the main loop.

        Called from the reading thread, so it must not touch any state itself:
        everything the result changes happens in _reading_done, on the loop.
        Overridden in tests, which have no loop to hand anything to.
        """
        GLib.idle_add(self._reading_done, *result)

    def _reading_done(self, generation: int, events, now: datetime,
                      elapsed: float) -> bool:
        self._reading = max(0, self._reading - 1)
        if generation <= self._applied:
            # Overtaken while it waited. Its answer is the older one.
            return GLib.SOURCE_REMOVE
        self._applied = generation

        if elapsed >= 1 or len(events) != self._logged_count:
            LOGGER.info("calendar read took %.1fs, %d event(s) today",
                        elapsed, len(events))
            self._logged_count = len(events)

        self._state = select(events, now)
        if self._state is not None:
            self._published = True
        if self.publisher is not None:
            self.publisher.sync()
        return GLib.SOURCE_REMOVE

    # -- Publication ------------------------------------------------------

    def _extension(self) -> dict[str, Any] | None:
        """What the publisher asks for on every sync."""
        if self._state is None:
            return None
        return extension.build(self._state, now=self._clock(), clock24=uses_24_hour())

    def _tick(self) -> bool:
        # _refresh only starts the read; the publication happens when it lands.
        self._refresh()
        self._schedule()
        return GLib.SOURCE_REMOVE

    def _schedule(self) -> None:
        if self._tick_source:
            GLib.source_remove(self._tick_source)
        warmup = None if self._published else self._warmup
        if warmup is not None and self._state is None:
            self._warmup += 1
        delay = refresh_delay(self._state, self._clock(), warmup=warmup)
        self._tick_source = GLib.timeout_add_seconds(delay, self._tick)

    # -- Lifecycle --------------------------------------------------------

    def close(self) -> None:
        if self._tick_source:
            GLib.source_remove(self._tick_source)
            self._tick_source = 0


def uses_24_hour() -> bool:
    """Follow the desktop's own clock format rather than guessing from locale."""
    try:
        settings = Gio.Settings.new("org.gnome.desktop.interface")
        return settings.get_string("clock-format") == "24h"
    except Exception:  # noqa: BLE001 - absent schema is not a reason to fail
        return False


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="luma-agenda: %(levelname)s %(message)s")
    application = Gio.Application(
        application_id="org.projectluma.Agenda",
        flags=Gio.ApplicationFlags.IS_SERVICE,
    )
    try:
        application.register(None)
    except GLib.Error:
        LOGGER.error("could not register on the session bus")
        return 1
    if application.get_is_remote():
        LOGGER.info("another agenda producer already owns the session; exiting")
        return 0
    try:
        producer = AgendaProducer(application)
    except (GLib.Error, OSError, RuntimeError, ValueError):
        LOGGER.error("could not start the agenda producer")
        return 1
    # Held before run, or the loop has nothing to keep it alive.
    application.hold()
    try:
        return application.run(None)
    finally:
        producer.close()
