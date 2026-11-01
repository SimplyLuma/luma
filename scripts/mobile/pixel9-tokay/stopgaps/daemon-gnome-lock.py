F = "/var/lib/luma-tokay/prairie-phone-daemon"
s = open(F).read()
old_wake = """    def wake(self) -> None:
        if self._proxy is None:
            return
"""
new_wake = """    def wake(self) -> None:
        # GNOME Shell's SetActive(false) dismisses the lock screen without
        # authentication. Without layer-shell (GNOME Shell) the call's urgent
        # notification wakes the lock screen instead.
        if self._proxy is None or Gtk4LayerShell is None:
            return
"""
old_inc = """        self._incoming_locked = self._waker.locked()
        if not self._incoming_locked:
"""
new_inc = """        self._incoming_locked = self._waker.locked()
        if not self._incoming_locked or Gtk4LayerShell is None:
"""
assert s.count(old_wake) == 1 and s.count(old_inc) == 1
s = s.replace(old_wake, new_wake).replace(old_inc, new_inc)
open(F, "w").write(s)
print("patched")
