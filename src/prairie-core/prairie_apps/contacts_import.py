# SPDX-License-Identifier: Apache-2.0
"""Bounded vCard envelopes; EBookContacts remains the field parser."""

MAX_VCARD_BYTES = 8 * 1024 * 1024
MAX_VCARDS = 1000


def split_vcards(text: str) -> tuple[str, ...]:
    """Reject truncated/nested input before the address book is mutated."""
    if len(text.encode("utf-8")) > MAX_VCARD_BYTES:
        raise ValueError("Choose a vCard file smaller than 8 MiB.")
    if "\0" in text:
        raise ValueError("The vCard file contains invalid text.")
    cards: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        marker = line.upper()
        if marker == "BEGIN:VCARD":
            if current is not None:
                raise ValueError("The vCard file contains a nested card.")
            current = [line]
        elif marker == "END:VCARD":
            if current is None:
                raise ValueError("The vCard file has an unmatched end marker.")
            current.append(line)
            cards.append("\r\n".join(current) + "\r\n")
            if len(cards) > MAX_VCARDS:
                raise ValueError("Import at most 1,000 contacts at a time.")
            current = None
        elif current is not None:
            current.append(line)
        elif line.strip():
            raise ValueError("The file contains text outside a vCard.")
    if current is not None:
        raise ValueError("The vCard file ends before the last card is complete.")
    if not cards:
        raise ValueError("The file contains no contacts.")
    return tuple(cards)
