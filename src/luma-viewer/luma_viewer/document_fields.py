# SPDX-License-Identifier: Apache-2.0
"""Native editable text inside a PDF, with its own accessible textbox role."""
import gi

gi.require_version('Gtk','4.0')
from gi.repository import Gtk


class DocumentField(Gtk.Text):
    __gtype_name__='LumaViewerDocumentField'


# Gtk.Text normally lives inside Gtk.Entry and has no accessible role of its
# own. PDF fields stand alone at the document's actual field coordinates.
DocumentField.set_accessible_role(Gtk.AccessibleRole.TEXT_BOX)
