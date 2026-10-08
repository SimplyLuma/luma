"""Reusable native Settings page over the account-independent status reader.

The Settings owner embeds this page. It exposes no install/enrollment/restart
action until the canonical updater supplies its reviewed authorization flow.
"""
import threading
import gi
gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk
from .updates import read_status


class UpdateStatusPage(Adw.PreferencesPage):
    def __init__(self, *, reader=read_status):
        super().__init__(title='Software updates', icon_name='software-update-available-symbolic')
        self.reader = reader
        self.snapshot = None
        self.busy = False
        self.disposed = False
        self.serial = 0
        group = Adw.PreferencesGroup(title='Software updates',
            description='System updates work without a Luma account.')
        self.add(group)
        self.status_row = Adw.ActionRow(title='System status', subtitle='Refresh to read this device’s update status.', use_markup=False)
        group.add(self.status_row)
        self.refresh_button = Gtk.Button(label='Refresh status', valign=Gtk.Align.CENTER)
        self.refresh_button.connect('clicked', lambda *_: self.refresh())
        self.observed_row = Adw.ActionRow(title='Last checked', subtitle='Not checked', use_markup=False)
        group.add(self.observed_row)
        group.add(self.refresh_button)
        self.problem_row = Adw.ActionRow(title='Previous update attempt',
            subtitle='The updater reported a problem. The running system is unchanged by this status check.',
            visible=False, use_markup=False)
        group.add(self.problem_row)

    def close(self):
        """Owner calls before discarding page; in-flight results are ignored."""
        self.disposed = True
        self.serial += 1

    def refresh(self):
        if self.disposed or self.busy: return
        self.busy = True
        self.serial += 1
        serial, previous = self.serial, self.snapshot
        self.refresh_button.set_sensitive(False)
        self.status_row.set_subtitle('Reading system status…')
        def work():
            try: snapshot = self.reader(previous=previous)
            except Exception:
                snapshot = ({**previous, 'stale': True} if previous else {'available': False})
            GLib.idle_add(self._received, serial, snapshot)
        threading.Thread(target=work, name='luma-update-status', daemon=True).start()

    def _received(self, serial, snapshot):
        if self.disposed or serial != self.serial: return GLib.SOURCE_REMOVE
        self.busy = False
        self.refresh_button.set_sensitive(True)
        self.snapshot = snapshot
        self.problem_row.set_visible(bool(snapshot.get('failure')) and not snapshot.get('stale'))
        if snapshot.get('stale'):
            title, subtitle = 'Status could not be refreshed', 'The time of the last successful check is shown below. Try again when the update service is available.'
        elif not snapshot.get('available'):
            title, subtitle = 'Update status unavailable', 'This system is not reporting update status yet.'
        elif snapshot.get('transaction_active'):
            title, subtitle = 'System changes in progress', 'Refresh again after the current operation finishes.'
        elif snapshot.get('pending_checksum') or snapshot.get('staged_checksum'):
            title, subtitle = 'Restart pending', 'A system change is prepared for the next time you restart.'
        else:
            title, subtitle = 'No restart pending', 'Refresh checks local status. It does not download updates.'
        self.status_row.set_title(title)
        self.status_row.set_subtitle(subtitle)
        observed = snapshot.get('observed_at')
        date = GLib.DateTime.new_from_unix_local(observed) if type(observed) is int else None
        self.observed_row.set_subtitle(date.format('%b %e, %Y %H:%M') if date else 'Not available')
        return GLib.SOURCE_REMOVE
