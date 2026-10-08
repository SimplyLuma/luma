# SPDX-License-Identifier: Apache-2.0
"""Restart to finish a change to the system that is waiting for one.

The same path as the Updates tab and luma-update's notifier: ask the session
manager, so applications can save and the end-session dialog and inhibitors are
respected. gnome-session answers ``Reboot`` only when that dialog is settled,
so the call has no timeout, and a cancel or refusal is final.

Only when there is no session manager at all does Depot ask something else:
luma-updated's ``Apply()`` when the waiting deployment is luma-update's own
staged update (an app removal or restore staged on top of it is carried in the
same deployment), and otherwise logind's ``Reboot`` with interactive
authorization. ``Apply()`` refuses a deployment luma-update did not stage, which
is what an app removal on its own is.
"""

from __future__ import annotations

from gi.repository import Gio, GLib

try:
    from luma_installer.depot_system_update import restart_outcome
except ImportError:  # an older luma_installer on this computer
    def restart_outcome(remote_error: str) -> str:
        if remote_error in ('org.freedesktop.DBus.Error.ServiceUnknown',
                            'org.freedesktop.DBus.Error.NameHasNoOwner',
                            'org.freedesktop.DBus.Error.UnknownMethod'):
            return 'agent'
        if remote_error.endswith('.Code19') or remote_error == 'org.freedesktop.DBus.Error.Cancelled':
            return 'cancelled'
        return 'refused'


def _text(error) -> str:
    text = getattr(error, 'message', '') or ''
    if text.startswith('GDBus.Error:'):
        _name, _, text = text.partition(': ')
    return text


def restart(*, update_staged: bool, on_refused, system_updates=None) -> None:
    """Restart through the session; ``on_refused(sentence)`` hears a refusal."""

    def without_session_manager():
        if update_staged and system_updates is not None:
            system_updates.call('Apply')
            return
        try:
            system = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except GLib.Error as error:
            on_refused(_text(error) or 'Luma could not restart.')
            return

        def rebooted(connection, result):
            try:
                connection.call_finish(result)
            except GLib.Error as error:
                if restart_outcome(Gio.DBusError.get_remote_error(error) or '') != 'cancelled':
                    on_refused(_text(error) or 'Luma could not restart.')
        system.call('org.freedesktop.login1', '/org/freedesktop/login1', 'org.freedesktop.login1.Manager',
                    'Reboot', GLib.Variant('(b)', (True,)), None,
                    Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION, GLib.MAXINT, None, rebooted)

    try:
        session = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        without_session_manager()
        return

    def finished(connection, result):
        try:
            connection.call_finish(result)
        except GLib.Error as error:
            outcome = restart_outcome(Gio.DBusError.get_remote_error(error) or '')
            if outcome == 'agent':
                without_session_manager()
            elif outcome == 'refused':
                reason = _text(error)
                on_refused(f'Luma did not restart: {reason}' if reason and len(reason) < 200 else
                           'Luma did not restart. Restart from the system menu when you are ready.')
    session.call('org.gnome.SessionManager', '/org/gnome/SessionManager', 'org.gnome.SessionManager',
                 'Reboot', None, None, Gio.DBusCallFlags.NONE, GLib.MAXINT, None, finished)
