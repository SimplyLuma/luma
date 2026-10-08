# SPDX-License-Identifier: Apache-2.0
"""Text-file encoding only. File windows never instantiate the Notes database."""
from dataclasses import dataclass

MAX_TEXT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class TextDocument:
    text: str
    encoding: str = "utf-8"
    newline: str = "\n"

    @classmethod
    def decode(cls, data: bytes) -> "TextDocument":
        if len(data) > MAX_TEXT_BYTES:
            raise ValueError("This file is larger than Notes can edit (16 MB).")
        encoding = ("utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else
                    "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8")
        text = data.decode(encoding)
        if "\0" in text:
            raise ValueError("This appears to be a binary file, rather than a text document.")
        # Preserve the source byte order as well as its BOM when saving UTF-16.
        if encoding == "utf-16":
            encoding = "utf-16-le" if data.startswith(b"\xff\xfe") else "utf-16-be"
        newline = "\r\n" if "\r\n" in text else "\r" if "\r" in text else "\n"
        return cls(text.replace("\r\n", "\n").replace("\r", "\n"), encoding, newline)

    def encode(self, text: str) -> bytes:
        data = text.replace("\n", self.newline).encode(self.encoding)
        if self.encoding in ("utf-16-le", "utf-16-be"):
            data = (b"\xff\xfe" if self.encoding == "utf-16-le" else b"\xfe\xff") + data
        return data
