# SPDX-License-Identifier: Apache-2.0
"""A small EPUB 3 written on the fly, so the tests carry no book files."""
from __future__ import annotations

from pathlib import Path
import zipfile

CHAPTER_ONE = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en"><head><title>One</title></head>
<body><section id="c1"><h2 id="h1">CHAPTER I.</h2>
<p>It is a truth universally acknowledged, that a single man in possession of a good fortune must be in want of a wife.</p>
<p>“My dear Mr. Bennet,” said his lady to him one day, “have you heard that Netherfield Park is let at last?” Mr. Bennet replied that he had not.</p>
</section>
<section id="c2"><h2 id="h2">CHAPTER II.</h2>
<p>Mr. Bennet was among the earliest of those who waited on Mr. Bingley. He had always intended to visit him&nbsp;though.</p>
</section></body></html>"""

CHAPTER_TWO = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="en"><head><title>Two</title></head>
<body><h2>Chapter the Third</h2><p>Not all that Mrs. Bennet, however, with the assistance of her five daughters, could ask on the subject, was sufficient.</p></body></html>"""

NAV = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head>
<body><nav epub:type="toc"><ol>
<li><a href="one.xhtml#h1">CHAPTER I.</a></li>
<li><a href="one.xhtml#h2">I hope Mr. Bingley will like it. CHAPTER II.</a></li>
<li><a href="two.xhtml">CHAPTER III. The Visit</a></li>
</ol></nav></body></html>"""

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="uid">urn:leaf:test-book</dc:identifier><dc:title>A Test of Manners</dc:title>
<dc:creator>Jane Tester</dc:creator><dc:language>en</dc:language></metadata>
<manifest>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="one" href="text/one.xhtml" media-type="application/xhtml+xml"/>
<item id="two" href="text/two.xhtml" media-type="application/xhtml+xml"/>
<item id="cover" href="cover.png" media-type="image/png" properties="cover-image"/>
</manifest>
<spine><itemref idref="one"/><itemref idref="two"/></spine></package>"""

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c63000100000500010d0a2db40000000049454e44ae426082")


def write_epub(path: Path, *, cover: bool = True, identifier: str | None = None, title: str | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        archive.writestr("META-INF/container.xml", """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>""")
        opf = OPF if cover else OPF.replace('<item id="cover" href="cover.png" media-type="image/png" properties="cover-image"/>\n', "")
        if identifier is not None:
            opf = opf.replace("urn:leaf:test-book", identifier)
        if title is not None:
            opf = opf.replace("A Test of Manners", title)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/nav.xhtml", NAV.replace('href="one', 'href="text/one').replace('href="two', 'href="text/two'))
        archive.writestr("OEBPS/text/one.xhtml", CHAPTER_ONE)
        archive.writestr("OEBPS/text/two.xhtml", CHAPTER_TWO)
        if cover:
            archive.writestr("OEBPS/cover.png", PNG)
    return path


# ── a book that exercises the page: every way a section has read as blank ──

def solid_png(width: int, height: int, rgb: tuple[int, int, int] = (90, 90, 90)) -> bytes:
    """A plain PNG of one colour, written without an imaging library."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    row = b"\x00" + bytes(rgb) * width
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(row * height)) + chunk(b"IEND", b""))


def _prose(sentences: int, seed: str) -> str:
    words = ("the reader turned the page and found the morning light across the table where "
             "letters waited unopened beside a cup of tea gone cold").split()
    out = []
    for i in range(sentences):
        start = (i * 7) % len(words)
        sentence = " ".join(words[(start + j) % len(words)] for j in range(14))
        out.append(f"{seed} {sentence}.")
    return " ".join(out)


def _xhtml(title: str, body: str, *, head: str = "") -> str:
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en">'
            f'<head><title>{title}</title>{head}</head><body>{body}</body></html>')


# A publisher's stylesheet that paints text black, the way Calibre's
# conversions often do: one rule !important, one not, on elements Leaf's own
# rule did not name.
PUBLISHER_CSS = """.ink { color: #000 !important; }
cite.plain, .shade { color: black; background-color: #fff; }
"""

READER_OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="uid">urn:leaf:reader-cases</dc:identifier><dc:title>Pages; or, The Blank Leaf</dc:title>
<dc:creator>Tester, Jane</dc:creator><dc:language>en</dc:language></metadata>
<manifest>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="cover" href="text/cover.xhtml" media-type="application/xhtml+xml" properties="svg"/>
<item id="front" href="text/front.xhtml" media-type="application/xhtml+xml"/>
<item id="opener" href="text/opener.xhtml" media-type="application/xhtml+xml"/>
<item id="body" href="text/body.xhtml" media-type="application/xhtml+xml"/>
<item id="plate" href="text/plate.xhtml" media-type="application/xhtml+xml"/>
<item id="after" href="text/after.xhtml" media-type="application/xhtml+xml"/>
<item id="css" href="style.css" media-type="text/css"/>
<item id="cover-image" href="images/cover.png" media-type="image/png" properties="cover-image"/>
<item id="rule" href="images/rule.png" media-type="image/png"/>
<item id="plate-image" href="images/plate.png" media-type="image/png"/>
</manifest>
<spine><itemref idref="cover"/><itemref idref="front"/><itemref idref="opener"/><itemref idref="body"/>
<itemref idref="plate"/><itemref idref="after"/></spine></package>"""

READER_NAV = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head>
<body><nav epub:type="toc"><ol>
<li><a href="text/front.xhtml">Introduction</a></li>
<li><a href="text/opener.xhtml">Chapter One</a></li>
<li><a href="text/after.xhtml">Chapter Two</a></li>
</ol></nav></body></html>"""

# Spine indexes of the sections above.
COVER, FRONT, OPENER, BODY, PLATE, AFTER = range(6)


def write_reader_epub(path: Path) -> Path:
    """Six sections: an SVG-wrapped cover, long front matter, a chapter opener
    split from its text (as Calibre splits them), the chapter set in a
    publisher's black, a picture alone on its page, and a last chapter whose
    XHTML uses HTML entities XML does not define."""
    link = '<link rel="stylesheet" type="text/css" href="../style.css"/>'
    sections = {
        "cover": _xhtml("Cover", '<div><svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                                 'version="1.1" width="100%" height="100%" viewBox="0 0 600 900" preserveAspectRatio="xMidYMid meet">'
                                 '<image width="600" height="900" xlink:href="../images/cover.png"/></svg></div>'),
        "front": _xhtml("Introduction", "<h2>Introduction</h2>" +
                        "".join(f"<p>{_prose(6, 'Before')}</p>" for _ in range(30))),
        "opener": _xhtml("Chapter One", '<h2 class="calibre24" id="c1"><span><small>CHAPTER ONE</small></span></h2>'
                                        '<p class="calibre1"><img alt="" src="../images/rule.png"/></p><div>&#160;</div>'),
        "body": _xhtml("Childhood", '<h2 id="start">CHILDHOOD</h2>' +
                       "".join(f'<p class="ink">{_prose(5, "Chapter")}</p>' for _ in range(20)) +
                       '<p>From <cite class="plain">a letter</cite> kept in <span class="shade">a drawer</span>.</p>',
                       head=link),
        "plate": _xhtml("Plate", '<div class="plate"><img alt="A plate" src="../images/plate.png"/></div>'),
        # HTML's entities and a bare ampersand in XHTML: not XML, and common.
        "after": _xhtml("Chapter Two", "<h2>Chapter Two</h2><p>Mr.&nbsp;Tester wrote to AT&T at once.</p>" +
                        "".join(f"<p>{_prose(4, 'After')}</p>" for _ in range(3))),
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        archive.writestr("META-INF/container.xml", """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>""")
        archive.writestr("OEBPS/content.opf", READER_OPF)
        archive.writestr("OEBPS/nav.xhtml", READER_NAV)
        archive.writestr("OEBPS/style.css", PUBLISHER_CSS)
        for name, document in sections.items():
            archive.writestr(f"OEBPS/text/{name}.xhtml", document)
        archive.writestr("OEBPS/images/cover.png", solid_png(60, 90, (40, 70, 110)))
        archive.writestr("OEBPS/images/rule.png", solid_png(300, 2, (120, 110, 100)))
        archive.writestr("OEBPS/images/plate.png", solid_png(400, 600, (150, 120, 90)))
    return path
