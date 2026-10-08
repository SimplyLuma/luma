# SPDX-License-Identifier: Apache-2.0
"""GTK-free, read-only Phone conformance data and presentation helpers.

LUMA_PHONE_FIXTURE names a JSON file. This source reads that file and its
explicitly referenced local photos only. It never imports an address book,
store, call transport, audio adapter or cloud provider. Shared parts still being built are tracked in the Phone kit requests.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Mapping


def digits(value: str) -> str:
    return "".join(character for character in value if character in "0123456789")


def dial_display(value: str) -> str:
    """v71 pnFmt: format ten digits and seven (555-0178), separate longer numbers, preserve short input."""
    number = digits(value)
    if len(number) == 10:
        return f"({number[:3]}) {number[3:6]}-{number[6:]}"
    if len(number) == 7:
        return f"{number[:3]}-{number[3:]}"
    if len(number) > 6:
        return f"{number[:3]}-{number[3:6]}-{number[6:]}"
    return value


@dataclass(frozen=True)
class PhonePerson:
    uid: str
    name: str
    phone: str
    handle: str = ""
    email: str = ""
    hue: int | None = None
    online: bool = False
    photo: str = ""
    face: tuple[float, ...] = ()
    luma_account: str = ""


def matching_person(people: tuple[PhonePerson, ...], value: str) -> PhonePerson | None:
    """The pad shows a match after three input characters; empty digits never match."""
    number = digits(value)
    if len(value) <= 2 or not number:
        return None
    return next((person for person in people if number in digits(person.phone)), None)


def visible_people(people: tuple[PhonePerson, ...], query: str) -> tuple[PhonePerson, ...]:
    """Search names and phone digits without duplicating a numeric name as its subtitle."""
    text, number = query.strip().casefold(), digits(query)
    numeric = bool(number) and not any(character.isalpha() for character in text)
    return tuple(sorted((person for person in people if not text
                         or text in person.name.casefold()
                         or (person.luma_account and text.lstrip("@") in person.handle.casefold())
                         or (numeric and number in digits(person.phone))),
                        key=lambda person: person.name.casefold()))


def search_people(people: tuple[PhonePerson, ...], query: str, limit: int = 5) -> tuple[PhonePerson, ...]:
    """The phone bar's search (v71 pnSearchHits): a word of the name starts with the text,
    or three or more typed digits appear in the number. At most five, in address-book order."""
    text, number = query.strip().casefold(), digits(query)
    if not text:
        return ()
    return tuple(person for person in people
                 if any(word.startswith(text) for word in person.name.casefold().split())
                 or person.name.casefold().startswith(text)
                 or (person.luma_account and person.handle.casefold().startswith(text.lstrip("@")))
                 or (len(number) > 2 and number in digits(person.phone)))[:limit]


@dataclass(frozen=True)
class FixtureCall:
    uid: str
    people: tuple[str, ...]
    kind: str
    direction: str
    when: str
    duration: str = ""
    group: str = ""
    number: str = ""
    spam: bool = False

    @property
    def icon(self) -> str:
        if self.kind == "video":
            return "video-off" if self.direction == "missed" else "video"
        return {"missed": "phone-missed", "in": "phone-incoming", "out": "phone-outgoing"}[self.direction]


@dataclass(frozen=True)
class FixtureVoicemail:
    uid: str
    person: str
    when: str
    duration: int
    transcript: str


@dataclass(frozen=True)
class TogetherItem:
    icon: str
    title: str
    when: str
    duration: str = ""
    missed: bool = False


class FixtureSource:
    """Immutable sample records. Malformed fixtures fail; never fall back to real data."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        document = json.loads(self.path.read_text(encoding="utf-8"))
        self.people = tuple(PhonePerson(**{**raw, "face": tuple(raw.get("face", ()))})
                            for raw in document["people"])
        known = {person.uid for person in self.people}
        if len(known) != len(self.people):
            raise ValueError("Duplicate Phone fixture person")
        self.favourites = tuple(document["favourites"])
        self.calls = tuple(FixtureCall(raw["id"], tuple(raw["who"]), raw["kind"], raw["dir"],
                                      raw["when"], raw.get("len", ""), raw.get("group", ""),
                                      raw.get("num", ""), raw.get("spam", False))
                           for raw in document["calls"])
        self.voicemails = tuple(FixtureVoicemail(raw["id"], raw["who"], raw["when"], raw["len"], raw["t"])
                                for raw in document["voicemails"])
        references = {*self.favourites, *(uid for call in self.calls for uid in call.people),
                      *(vm.person for vm in self.voicemails)}
        if references - known - {"?"}:
            raise ValueError("Unknown Phone fixture person")
        for call in self.calls:
            if call.kind not in {"voice", "video"} or call.direction not in {"in", "out", "missed"}:
                raise ValueError("Invalid Phone fixture call kind or direction")
        for person in self.people:
            if person.photo:
                self._asset(person.photo)
        self.initial = dict(document["initial"])
        self._messages = document.get("messages", {})
        self._emails = document.get("emails", {})
        self.spam = document.get("spam", {})
        self.audio_outputs = tuple(document.get("audio_outputs", ()))

    def _asset(self, name: str) -> Path:
        base = self.path.parent.resolve()
        target = (base / name).resolve()
        if Path(name).is_absolute() or not target.is_relative_to(base):
            raise ValueError("Phone fixture assets must stay beside the fixture")
        return target

    def photo(self, person: PhonePerson) -> bytes:
        return self._asset(person.photo).read_bytes() if person.photo else b""

    def together(self, uid: str) -> tuple[TogetherItem, ...]:
        person = next(person for person in self.people if person.uid == uid)
        first = person.name.split()[0]
        items = []
        for call in self.calls:
            if call.people != (uid,):
                continue
            title = "Video call" if call.kind == "video" else "Call"
            title += ", missed" if call.direction == "missed" else f" from {first}" if call.direction == "in" else ""
            items.append(TogetherItem(call.icon, title, call.when, call.duration, call.direction == "missed"))
        message = self._messages.get(uid)
        if message:
            text = message["text"]
            sender = "You" if message["from"] == "me" else first
            title = f'{sender} texted: “{text[:44]}{"…" if len(text) > 44 else ""}”'
            items.append(TogetherItem("message-square", title, message["when"]))
        for email in self._emails.get(uid, ())[:2]:
            items.append(TogetherItem("mail", f'Email: {email["subject"]}', email["when"]))
        return tuple(items)


def source_from_environment(environ: Mapping[str, str] | None = None) -> FixtureSource | None:
    environment = os.environ if environ is None else environ
    path = environment.get("LUMA_PHONE_FIXTURE")
    return FixtureSource(Path(path)) if path else None
