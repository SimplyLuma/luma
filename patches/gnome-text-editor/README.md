# Luma Text Editor downstream

This directory carries the focused Fedora 44 GNOME Text Editor downstream used
by Luma. It preserves the upstream executable, application ID, actions,
sessions, editing engine, shortcuts, portals and adaptive property panel.

`0001-luma-native-application-frame.patch` is the reference conversion for a
curated non-Luma application. It composes the real upstream commands into the
native Luma frame: application identity at the leading edge, one semantic
command row, and one work surface. It does not inject CSS at runtime, wrap the
application, or mirror actions into another state model.
