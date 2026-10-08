# Tide provenance

Tide's implementation and application icon were created for Project Luma and
are licensed under Apache-2.0. No simulator HTML, CSS, JavaScript, sample music,
album art, or third-party application source is copied into this tree.

Runtime dependencies are used through their supported system interfaces:

- GTK 4, libadwaita, GLib/GIO, and GObject Introspection: LGPL-2.1-or-later.
- GStreamer and its base/good plugin sets: LGPL-2.1-or-later, with individual
  codec/plugin licensing governed by Fedora's packages.
- PipeWire GStreamer integration: MIT.
- Mutagen: GPL-2.0-or-later. It is dynamically imported as a system Python
  package for metadata parsing and is not vendored or copied into Tide.
- Luma UI and Luma Semantics: Project Luma Apache-2.0 components.
- libsecret (`Secret-1` introspection): LGPL-2.1-or-later, for remote source
  sign-in details.

The Navidrome/Subsonic adapter (`subsonic.py`, `remote.py`, `credentials.py`)
implements the public Subsonic REST API using only the Python standard
library and GObject Introspection bindings already listed above. No
third-party Subsonic client source or text was copied into Tide.

- libsecret (`Secret-1` introspection): LGPL-2.1-or-later, for source passwords.
- glib-networking: LGPL-2.1-or-later, the TLS backend GStreamer's libsoup HTTP
  source uses for HTTPS streams.

The Navidrome/Subsonic adapter implements the public Subsonic REST API and the
OpenSubsonic specification (https://opensubsonic.netlify.app) using the Python
standard library. No client source was copied. Sonora
(https://github.com/sonorahq/sonora, GPL-3.0) was examined only to learn which
public APIs it uses; none of its code or text is in Tide.

Tide does not bundle music, codec implementations, server credentials, or
Design Center assets. Fedora's package policy determines which codecs are
available on a composed image; Tide reports unsupported media honestly.

