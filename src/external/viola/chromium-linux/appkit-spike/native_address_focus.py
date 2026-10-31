# SPDX-License-Identifier: GPL-3.0-only
"""Preserve an in-progress address edit across native bar reparenting."""
from dataclasses import dataclass


@dataclass(frozen=True)
class AddressEdit:
    text: str
    position: int
    selection: tuple
    new_tab_pending: bool

    @classmethod
    def capture(cls, editable, pending):
        bounds=tuple(editable.get_selection_bounds())
        # Gtk's editable override returns two positions or an empty tuple.
        return cls(editable.get_text(),editable.get_position(),bounds,bool(pending))

    def restore(self, editable):
        editable.set_text(self.text)
        editable.grab_focus()
        # Focus-enter handlers may select all; restore the user's exact edit.
        if len(self.selection)==2:
            start,end=self.selection
            editable.select_region(end,start) if self.position==start else editable.select_region(start,end)
        else:
            editable.set_position(self.position)
