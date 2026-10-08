# SPDX-License-Identifier: GPL-3.0-only
"""Chromium CursorType to standard names resolved by the installed GTK theme.

Custom bitmap cursors remain a separate transport capability.
"""
CURSORS = dict(enumerate([
    'default', 'crosshair', 'pointer', 'text', 'wait', 'help',
    'e-resize', 'n-resize', 'ne-resize', 'nw-resize', 's-resize',
    'se-resize', 'sw-resize', 'w-resize', 'ns-resize', 'ew-resize',
    'nesw-resize', 'nwse-resize', 'col-resize', 'row-resize',
    'all-scroll', 'e-resize', 'n-resize', 'ne-resize', 'nw-resize',
    's-resize', 'se-resize', 'sw-resize', 'w-resize', 'move',
    'vertical-text', 'cell', 'context-menu', 'alias', 'progress',
    'no-drop', 'copy', 'none', 'not-allowed', 'zoom-in', 'zoom-out',
    'grab', 'grabbing', 'ns-resize', 'ew-resize', 'default',
    'no-drop', 'move', 'copy', 'alias', 'not-allowed', 'not-allowed',
    'not-allowed', 'not-allowed']))
