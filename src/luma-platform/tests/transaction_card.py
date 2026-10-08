#!/usr/bin/python3
"""Measure the same allocated controls in all six transaction states."""
import gi
gi.require_version('Gtk','4.0')
gi.require_version('Adw','1')
from gi.repository import Gtk, Adw, Gio, GLib, Pango
from luma_appkit import AppWindow, CommandRegistry, Command, CommandGroup, TransactionCard, TextButton
import time

Adw.init()
app = Adw.Application(application_id='org.projectluma.TransactionTest')
app.register(None)
actions=[]
window = AppWindow(application=app, app_id='org.projectluma.TransactionTest', title='Install',
                   icon_name='system-software-install', commands=CommandRegistry((CommandGroup(None,(Command("test.document", "Document", lambda: actions.append("first")),)),)),
                   default_width=520, default_height=648, minimum_width=340, minimum_height=600)
card = TransactionCard(); card.name.set_label('Application with a long but stable name')
card.sub.set_label('1.0 · Publisher'); window.set_body(card); window.present()

def settle():
    until=time.monotonic()+.15
    while time.monotonic()<until:
        while GLib.MainContext.default().pending(): GLib.MainContext.default().iteration(False)
        time.sleep(.002)

def bounds(widget):
    ok, rect = widget.compute_bounds(window)
    assert ok
    return tuple(round(v,2) for v in (rect.get_x(),rect.get_y(),rect.get_width(),rect.get_height()))

# An absolute desktop Icon path must resolve as a file, not image-missing.
import tempfile
from pathlib import Path
with tempfile.TemporaryDirectory() as root:
    icon_path = Path(root) / 'App Icon.svg'
    icon_path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><rect width="32" height="32" fill="red"/></svg>')
    card.set_icon(str(icon_path))
    # Complete artwork stays the paintable; no added tile, border or inset.
    paintable = card.icon.get_paintable()
    assert isinstance(paintable, Gtk.IconPaintable), paintable
    assert paintable.get_file().get_path() == str(icon_path), paintable

settings=Gtk.Settings.get_default(); settings.set_property('gtk-enable-animations',False)
for dark in (False,True):
    appearance = Gio.Settings.new('org.project_luma.shell-state')
    appearance.set_string('surface-treatment', 'dark' if dark else 'light')
    settle()
    assert Adw.StyleManager.get_default().get_dark() is dark
    for width in (360,500,1024,1440):
        window.set_default_size(width,648); settle()
        reference=None
        for removing in (False,True):
            for phase in ('ready','working','done'):
                card.set_phase(phase, 'Uninstall' if removing else 'Install',
                               'Register' if phase == 'working' else '', .5, removing=removing)
                settle()
                assert isinstance(card.primary, TextButton) and isinstance(card.secondary, TextButton)
                assert card.primary.has_css_class("danger-solid") == (removing and phase == "ready")
                assert card.primary.has_css_class("raised") == (not removing or phase != "ready")
                assert not card.primary.has_css_class("suggested-action")
                assert card.primary.get_child() is card.primary_overlay
                assert card.primary_label.get_label() == ('Uninstall' if removing else 'Install')
                assert card.fill.get_opacity() == (1 if phase == 'working' else 0)
                geometry=[bounds(w) for w in (card.stage,card.icon,card.name,card.sub,
                                              card.phase_label,card.primary,card.secondary_slot,card.foot)]
                if reference is None: reference=geometry
                assert reference == geometry, (dark,width,removing,phase,reference,geometry)
                assert card.name.get_pango_context().get_font_description().get_size()/Pango.SCALE == 29
                assert bounds(card.icon)[2:] == (116,116), bounds(card.icon)
                assert bounds(card.primary)[3] == 44, bounds(card.primary)
other = AppWindow(application=app, app_id='org.projectluma.TransactionTest', title='Second',
                  icon_name='system-software-install', commands=CommandRegistry((
                      CommandGroup(None, (Command('test.document', 'Document', lambda: actions.append('second')),)),)))
other.present(); settle()
app.lookup_action('test-document').activate(None)
assert actions == ['second'], actions
other.close(); window.present(); settle()
app.lookup_action('test-document').activate(None)
assert actions == ['second', 'first'], actions
window.close()
print('Transaction card: six phases, three widths, light/dark; stable geometry, 116 px icon, 44 px primary.')
