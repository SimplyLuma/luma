# IBus downstream

Project Luma builds Fedora 44's `ibus-1.5.34-4.fc44` source package with
ordered downstream patches (`scripts/packages/build-ibus.sh`). IBus remains
LGPL-2.1-or-later and keeps its upstream name and identity.

`0000-luma-fedora-spec.patch` sets the Luma release, adds the patch below and
extends `%check`.

`0001-luma-emojier-letter-names.patch` lets the symbol picker (Super+., the
IBus emojier) find letters with marks by short names. The picker matches the
typed text against Unicode names as substrings, so "e acute" found nothing
useful ("LATIN SMALL LETTER E WITH ACUTE" does not contain it) and "eacute",
which is what arrives when Space is used between the words (Space moves
between candidates; Shift+Space types a space), found nothing at all. Each
"<script> <case> LETTER <x> WITH <marks>" name now also has the keys
"<x> <marks>" and "<x><marks>" ("e acute", "eacute", "n tilde", "ntilde";
"and" is dropped, capital letters keep their case: "E acute", "Eacute").
Full names and substrings still work as before. The key function sits
between `// Luma: begin/end unicode-letter-keys` markers in `emojier.vala`;
`%check` compiles exactly that block with
`ui/gtk3/test-unicode-letter-keys.vala` and runs it against the build's own
`unicode-names.dict`, looking names up as the emojier does.

The image also sets `load-unicode-at-startup=true`
(`config/desktop/dconf/db/luma.d/00-luma-desktop`) so the names are loaded
with the session rather than on the first search.

The release ships as a whole set: `ibus` requires `python3-ibus` and
`ibus-libs` of the same release, and Luma images carry ibus, ibus-libs,
ibus-gtk3, ibus-gtk4, ibus-setup and python3-ibus next to Fedora's engines.
`scripts/packages/check-ibus-upgrade.sh RPMDIR` proves a set installs as an
upgrade of Fedora's IBus in a clean Fedora 44 container with the engines
(anthy, chewing, hangul, libpinyin, m17n, typing-booster) and removes none.
4.luma.1 shipped without python3-ibus and failed that; 4.luma.2 replaces it.

