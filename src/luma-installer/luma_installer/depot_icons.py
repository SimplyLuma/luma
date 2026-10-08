# SPDX-License-Identifier: Apache-2.0
"""Which picture Depot draws for an application, decided without a toolkit.

Depot has four places an icon can come from, and they arrive at different
times on a new computer:

1. the signed catalogue's own icon, fetched by URL and kept under its SHA-256
   (``depot_media``) -- available as soon as the catalogue has been read;
2. Flathub's AppStream icon cache under ``/var/lib/flatpak/appstream`` --
   absent on a fresh installation, because ``/var`` is machine-local and
   nothing has downloaded it yet;
3. the icon theme, for an application this computer has installed;
4. nothing at all, for everything else.

The rule that was missing is that a name is not a picture. AppStream spells a
*cached* icon as a bare file name (``org.example.App.png``) which belongs to
the icon cache directory, not to the icon theme. Handing that to a toolkit as
a themed icon name raises no error and finds no file: it draws the toolkit's
own missing-image glyph, which is the empty box a person sees. So every
candidate is classified here, a themed name is used only when the theme
actually has it, and anything left over becomes the monogram placeholder --
which is a real answer, not a blank.

This module is deliberately free of gi, Gtk and the network: the policy is a
function of what is on disk, so it can be tested for a first boot, an offline
boot and a settled machine without any of them.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re

#: The anonymous cog. Drawing it is worse than drawing nothing: a shelf of
#: identical tiles cannot be read at all, so these never count as an icon.
SENTINELS = frozenset({
    'application-x-executable',
    'application-x-executable-symbolic',
    'application-default-icon',
    'package-x-generic',
    'package-x-generic-symbolic',
    'image-missing',
    'gtk-missing-image',
})

#: A name that ends like a file is a file name, never an icon theme name.
#: This is how AppStream spells ``type="cached"``, and it is the spelling that
#: used to reach the toolkit as a themed icon and draw as an empty box.
IMAGE_SUFFIXES = frozenset({'.png', '.jpg', '.jpeg', '.webp', '.svg', '.svgz', '.xpm', '.gif', '.ico'})

#: Icon sizes Flathub publishes in a remote's AppStream cache, largest first.
APPSTREAM_ICON_SIZES = (128, 64)

#: Where libflatpak keeps a remote's AppStream branch. ``/var`` is local to the
#: machine on an image-based system, so this directory cannot ship in the image
#: and is empty until something downloads it.
FLATPAK_APPSTREAM_ROOTS = (
    '/var/lib/flatpak/appstream',
    '~/.local/share/flatpak/appstream',
)

_THEMED_PREFIX = '. GThemedIcon '
_SHA256 = re.compile(r'[0-9a-f]{64}\Z')


@dataclass(frozen=True)
class Choice:
    """What to draw, and where it came from.

    ``kind`` is one of:

    ``file``
        ``value`` is a path to an image that exists now.
    ``themed``
        ``value`` is an icon theme name the theme was asked about and has.
    ``placeholder``
        ``value`` is the monogram to draw in the application's own tile.

    ``kind`` is never anything else, and ``value`` is never empty: every
    application gets something, which is the whole point.
    """

    kind: str
    value: str
    source: str  # catalog | appstream | theme | none

    @property
    def drawn(self) -> bool:
        """True when this is the application's real artwork."""
        return self.kind != 'placeholder'


def monogram(name: str) -> str:
    """The letters somebody would use to pick an application off a shelf."""
    words = [word for word in re.split(r'[^0-9A-Za-z]+', name) if word]
    if not words:
        return '?'
    if len(words) > 1:
        return (words[0][0] + words[1][0]).upper()
    word = words[0]
    # An acronym is already the short form; cutting it makes it unreadable.
    if word.isupper() and len(word) <= 4:
        return word
    # A name that capitalises inside itself says where its parts are. An
    # all-capital name does not: every letter would qualify.
    inner = [] if word.isupper() else [character for character in word[1:] if character.isupper()]
    if inner:
        return (word[0] + inner[0]).upper()
    return word[0].upper()


def themed_names(value: str) -> tuple[str, ...]:
    """The icon theme names in a serialised GIcon, best first.

    ``g_icon_to_string`` writes a themed icon with fallbacks as
    ``". GThemedIcon firefox firefox-symbolic"``. A single name is written
    plainly. Anything that names a file is not a themed icon at all.
    """
    text = (value or '').strip()
    if not text:
        return ()
    if text.startswith(_THEMED_PREFIX):
        candidates = text[len(_THEMED_PREFIX):].split()
    elif text.startswith('.'):
        # Some other serialised GIcon (GFileIcon, GBytesIcon, GEmblemedIcon).
        return ()
    else:
        candidates = [text]
    return tuple(
        name for name in candidates
        if name and name not in SENTINELS
        and not name.startswith(('/', '.'))
        and Path(name).suffix.lower() not in IMAGE_SUFFIXES
    )


def looks_like_a_path(value: str) -> bool:
    return bool(value) and value.startswith('/') and '\x00' not in value


def appstream_document(directory: str | os.PathLike[str] | None) -> Path | None:
    """A remote's AppStream collection file, compressed or not.

    libflatpak checks the branch out as ``appstream.xml`` and also keeps the
    ``appstream.xml.gz`` it hands to appstreamcli. Which of the two exists has
    changed between flatpak releases, so read whichever is there rather than
    losing every icon to the wrong file name.
    """
    if directory is None:
        return None
    base = Path(directory)
    for name in ('appstream.xml', 'appstream.xml.gz'):
        candidate = base / name
        if candidate.is_file():
            return candidate
    return None


def appstream_icon(directory: str | os.PathLike[str], filename: str, *,
                   size: int = 64, scale: int = 1) -> Path | None:
    """The cached icon for ``filename``, at the best size for ``size``/``scale``.

    ``filename`` comes from a remote's metadata and is only ever a bare file
    name: a component that asks for ``../../secret.png`` is refused rather
    than followed.
    """
    name = (filename or '').strip()
    if not name or name != Path(name).name or name in {'.', '..'}:
        return None
    wanted = max(1, int(size)) * max(1, int(scale))
    base = Path(directory) / 'icons'
    available = [(pixels, base / f'{pixels}x{pixels}' / name)
                 for pixels in APPSTREAM_ICON_SIZES]
    present = [(pixels, path) for pixels, path in available if path.is_file()]
    if not present:
        return None
    # The smallest artwork that still covers the request, so a 40pt tile does
    # not carry a 128px image and a 76pt tile at 2x does not draw a 64px one
    # scaled up. Failing that, the largest there is.
    covering = [(pixels, path) for pixels, path in present if pixels >= wanted]
    if covering:
        return min(covering, key=lambda item: item[0])[1]
    return max(present, key=lambda item: item[0])[1]


def flatpak_appstream_directory(remote: str, architecture: str,
                               roots: tuple[str, ...] = FLATPAK_APPSTREAM_ROOTS) -> Path | None:
    """Where a remote's AppStream branch is checked out, if it is."""
    for root in roots:
        candidate = Path(os.path.expanduser(root)) / remote / architecture / 'active'
        if appstream_document(candidate) is not None:
            return candidate
    return None


def usable_catalog_icon(icon) -> bool:
    """Whether a catalogue entry's icon can be fetched and checked at all.

    An icon without an https URL or without a SHA-256 is not an icon: nothing
    would verify it, so nothing may draw it.
    """
    url = getattr(icon, 'url', '') or (icon or {}).get('url', '') if icon is not None else ''
    digest = getattr(icon, 'sha256', '') or (icon or {}).get('sha256', '') if icon is not None else ''
    return bool(url.startswith('https://')) and bool(_SHA256.fullmatch(digest or ''))


def choose(*, name: str, media_path: str = '', appstream_path: str = '',
           icon_name: str = '', theme_has=None, exists=None) -> Choice:
    """What to draw for one application.

    ``media_path``
        A verified file from the signed catalogue's own icon, or "" when there
        is none yet. Highest priority: it is the artwork the listing was
        reviewed with, and its bytes have been checked against the catalogue.
    ``appstream_path``
        A file from the remote's AppStream icon cache, or "" on a computer
        that has not downloaded it.
    ``icon_name``
        What the installed application or the metadata calls its icon: a
        serialised GIcon, a theme name, or a path.
    ``theme_has``
        Asked before any theme name is used. Without it, no theme name is
        trusted -- which is the safe answer, because an absent theme icon
        draws the toolkit's missing-image glyph and no error is raised.
    ``exists``
        Overridable file test, so a first boot can be described in a test
        without building one.
    """
    present = exists if exists is not None else (lambda path: Path(path).is_file())

    if media_path and present(media_path):
        return Choice('file', media_path, 'catalog')
    if appstream_path and present(appstream_path):
        return Choice('file', appstream_path, 'appstream')

    value = (icon_name or '').strip()
    if looks_like_a_path(value) and present(value):
        return Choice('file', value, 'theme')
    if theme_has is not None:
        for candidate in themed_names(value):
            if theme_has(candidate):
                return Choice('themed', candidate, 'theme')
    return Choice('placeholder', monogram(name), 'none')
