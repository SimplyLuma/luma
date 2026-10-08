# SPDX-License-Identifier: GPL-3.0-only
"""AppKit candidate: distinguish pointer hover from keyboard menu selection."""
from pathlib import Path
from gi.repository import Gdk, Gtk
from luma_appkit import add_style_sheet

_provider = None


def install(popovers):
    global _provider
    if _provider is None:
        _provider = add_style_sheet(
            str(Path(__file__).parent / 'kit_candidates/menu_interaction.css'),
            priority=Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    root = popovers[0]
    for owner, controller in getattr(root, '_interaction_controllers', []):
        owner.remove_controller(controller)
    root._interaction_controllers = []
    def mode(pointer):
        for popup in popovers:
            (popup.add_css_class if pointer else popup.remove_css_class)('pointer-navigation')
    for popup in popovers:
        motion = Gtk.EventControllerMotion()
        motion.connect('motion', lambda *_: mode(True))
        popup.add_controller(motion)
        root._interaction_controllers.append((popup, motion))
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect('key-pressed', lambda *_: mode(False) or False)
        popup.add_controller(keys)
        root._interaction_controllers.append((popup, keys))
    mode(True)
