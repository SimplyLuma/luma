# SPDX-License-Identifier: Apache-2.0
"""Public pairing defaults; importing the UI never loads host identity owners."""
DEFAULT_PHONE_TO_DESKTOP = ('device.status', 'notifications.mirror', 'clipboard.write', 'files.write', 'links.open',
                            'media.mirror', 'dnd.set', 'auth.response')
# Camera, screen, control, messages and calls are offered but never pre-selected:
# each is its own decision on the pairing screen.
DEFAULT_DESKTOP_TO_PHONE = ('device.ring', 'clipboard.write', 'links.open', 'files.write', 'notifications.act',
                            'media.control', 'dnd.set', 'hotspot.request', 'auth.request')
