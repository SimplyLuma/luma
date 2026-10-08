# SPDX-License-Identifier: Apache-2.0
"""LayerHost (structure_layers.LayerHost)."""


def _py(K, Gtk):
    from luma_appkit.structure_layers import LayerHost
    return LayerHost(Gtk.Label(label="content"))


CASES = [
    ("host", lambda C, Gtk: C.LayerHost.new(Gtk.Label(label="content")), _py),
]
