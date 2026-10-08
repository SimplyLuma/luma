# SPDX-License-Identifier: Apache-2.0
"""What this machine can do, read from the capability report (ADR-048).

Monitor does not check anything and does not own any of these words. The
report at /var/lib/luma/capability-report.json is written after every
deployment by luma-capability-check, from the manifest that declares what Luma
promises a machine can do. Title, promise and remedy are reviewed as part of
that manifest, so they are shown verbatim: a second set of strings living here
would drift away from the ones `luma-capability` prints in a terminal.

Nothing in this module reaches the network, and nothing leaves the machine.
"""
from __future__ import annotations

import json
import os
import time

# Both paths are overridable for the same reason LUMA_MONITOR_STYLE_PATH is:
# so the packaged application can be exercised headlessly against a fixture.
REPORT = os.environ.get('LUMA_CAPABILITY_REPORT', '/var/lib/luma/capability-report.json')
CHECKER = os.environ.get('LUMA_CAPABILITY_CHECKER', '/usr/libexec/luma-capability-check')

# The four things a person can be told, and the words for them. These match
# `luma-capability` exactly. "Not checked here" is deliberately not a quiet
# synonym for "fine", and "Not on this machine" is deliberately not a failure.
WORKING = 'Working'
NOT_WORKING = 'Not working'
NOT_CHECKED = 'Not checked here'
NOT_PRESENT = 'Not on this machine'

STATES = {
    'pass': WORKING,
    'fail': NOT_WORKING,
    'unproven': NOT_CHECKED,
    'not-applicable': NOT_PRESENT,
    'skip': NOT_CHECKED,
}

# A capability declared at more than one tier is checked once per tier, so the
# report can carry two entries for one promise. The view shows one row, and
# the row tells the truth the person needs first: something broken outranks
# something unproven, which outranks something proven, and "not on this
# machine" only stands when no tier managed to prove anything.
PRECEDENCE = {'fail': 0, 'unproven': 1, 'skip': 2, 'pass': 3, 'not-applicable': 4}

TONE = {
    WORKING: 'working',
    NOT_WORKING: 'broken',
    NOT_CHECKED: 'unproven',
    NOT_PRESENT: 'absent',
}

NOT_CHECKED_YET = 'This machine has not been checked yet'
CHECK_COMMAND = 'sudo luma-capability --check'


def age_text(written_at, now=None):
    """The report's age, in the words `luma-capability` prints."""
    try:
        written = float(written_at)
    except (TypeError, ValueError):
        return None
    if written <= 0:
        return None
    hours = ((time.time() if now is None else now) - written) / 3600
    if hours < 0:
        hours = 0
    return 'checked just now' if hours < 1 else f'checked {hours:.0f} hours ago'


class Capability:
    """One promise and what became of it on this machine."""

    __slots__ = ('id', 'title', 'promise', 'remedy', 'severity', 'result', 'detail', 'state')

    def __init__(self, entry):
        self.id = str(entry.get('id', ''))
        self.title = str(entry.get('title') or self.id)
        self.promise = str(entry.get('promise') or '')
        self.remedy = str(entry.get('remedy') or '')
        self.severity = str(entry.get('severity') or 'required')
        self.result = str(entry.get('result') or 'unproven')
        self.detail = str(entry.get('detail') or '')
        self.state = STATES.get(self.result, NOT_CHECKED)

    @property
    def tone(self):
        return TONE.get(self.state, 'unproven')

    @property
    def note(self):
        """The sentence under the promise, or '' when there is nothing to say.

        For a broken capability this is the remedy: the one sentence somebody
        acts on, or pastes into a support conversation. For an unproven one it
        is why it could not be checked. Working and absent capabilities say
        nothing further, because there is nothing to do.
        """
        if self.state == NOT_WORKING:
            return self.remedy or self.detail
        if self.state == NOT_CHECKED:
            return self.detail
        return ''


class Report:
    """The report as Monitor shows it, or the absence of one."""

    __slots__ = ('path', 'present', 'error', 'hostname', 'written_at', 'capabilities')

    def __init__(self, path=None, data=None):
        path = path or REPORT
        self.path = path
        self.present = False
        self.error = ''
        self.hostname = ''
        self.written_at = 0.0
        self.capabilities = []
        if data is None:
            try:
                with open(path, encoding='utf-8') as handle:
                    data = json.load(handle)
            except OSError:
                self.error = NOT_CHECKED_YET
                return
            except ValueError:
                self.error = NOT_CHECKED_YET
                return
        if not isinstance(data, dict):
            self.error = NOT_CHECKED_YET
            return
        self.present = True
        self.hostname = str(data.get('hostname') or '')
        try:
            self.written_at = float(data.get('written_at') or 0)
        except (TypeError, ValueError):
            self.written_at = 0.0
        self.capabilities = self._collapse(data.get('capabilities'))

    @staticmethod
    def _collapse(entries):
        """One row per capability, in the manifest's order."""
        if not isinstance(entries, list):
            return []
        order, best = [], {}
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get('id'):
                continue
            row = Capability(entry)
            existing = best.get(row.id)
            if existing is None:
                order.append(row.id)
                best[row.id] = row
                continue
            if PRECEDENCE.get(row.result, 1) < PRECEDENCE.get(existing.result, 1):
                best[row.id] = row
        return [best[identifier] for identifier in order]

    @property
    def age_text(self):
        return age_text(self.written_at)

    def summary(self):
        """The one line under the list: where this came from and when."""
        age = self.age_text
        if not self.present:
            return NOT_CHECKED_YET
        if self.hostname and age:
            return f'{self.hostname} · {age}'
        return age or self.hostname or ''

    def counts(self):
        tally = {WORKING: 0, NOT_WORKING: 0, NOT_CHECKED: 0, NOT_PRESENT: 0}
        for row in self.capabilities:
            tally[row.state] = tally.get(row.state, 0) + 1
        return tally


def can_check(checker=None):
    """Whether there is a checker on this machine to run again."""
    return os.path.exists(checker or CHECKER)
