# SPDX-License-Identifier: Apache-2.0
"""Parity: DestructiveDialog (action_dialog.py), the card itself."""

CASES = [
    ("plain", lambda C, Gtk: C.DestructiveDialog.new("Delete this conversation?", "It's removed from this computer.",
                                                     None, None, None),
     lambda K, Gtk: K.DestructiveDialog(title="Delete this conversation?", body="It's removed from this computer.")),
    ("option", lambda C, Gtk: C.DestructiveDialog.new("Uninstall Kiln?", "Its settings go too.", "Uninstall",
                                                      "package-x", "Also delete its files"),
     lambda K, Gtk: K.DestructiveDialog(title="Uninstall Kiln?", body="Its settings go too.", action="Uninstall",
                                        icon="package-x", option="Also delete its files")),
]
