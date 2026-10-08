# SPDX-License-Identifier: Apache-2.0
"""Parity: Toast (action_toast.py), the card itself."""

CASES = [
    ("done", lambda C, Gtk: C.Toast.new("Saved", None, False, False), lambda K, Gtk: K.Toast("Saved")),
    ("copied", lambda C, Gtk: C.Toast.new("Copied", "copied", False, False),
     lambda K, Gtk: K.Toast("Copied", kind="copied")),
    ("undo", lambda C, Gtk: C.Toast.new("Deleted 3 photos", "deleted", True, False),
     lambda K, Gtk: K.Toast("Deleted 3 photos", kind="deleted", undo=lambda: None)),
    ("error", lambda C, Gtk: C.Toast.new("Couldn't send", "error", False, False),
     lambda K, Gtk: K.Toast("Couldn't send", kind="error")),
    ("busy", lambda C, Gtk: C.Toast.new("Uploading…", None, False, True),
     lambda K, Gtk: K.Toast("Uploading…", busy=True)),
]
