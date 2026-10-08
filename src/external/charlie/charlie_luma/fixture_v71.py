# SPDX-License-Identifier: Apache-2.0
"""Simulator v71's sample mailbox, for LUMA_CHARLIE_FIXTURE=v71.

The conform gate and previews run Charlie against exactly the mail the v71
simulator shows (`luma-next-71.html`, `CRT`, `CRP`, `CRB`, `CRACCT`): two
accounts, ten threads, three senders that send designed mail, a draft and a
sent message, at the simulator's own clock (Thursday 1 October 2026).

Nothing here touches disk: the store is SQLite in memory and the window
never syncs, sends or asks the background agent for anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import os
from pathlib import Path

from .model import Account, Attachment, Message, ServerConfig, stable_thread_id

#: The simulator's "now": the day its tickets are for.
NOW = datetime(2026, 10, 1, 11, 52).astimezone()

ME = "nick@simplyluma.com"
HOME = "nick@studionorth.net"

ACCOUNTS = (
    Account(id="luma", display_name="Luma", address=ME, provider="fixture", colour="blue"),
    Account(id="home", display_name="Personal", address=HOME, provider="fixture", colour="green"),
)


@dataclass(frozen=True)
class FixturePerson:
    name: str
    address: str
    #: v71 PPL face: (photo, x, y, zoom) in social-luma/photos; None draws initials.
    face: tuple[str, float, float, float] | None = None
    #: A sender that sends designed mail (v71 CRB): its monogram and colour.
    brand: tuple[str, str] | None = None
    hue: float | None = None


PEOPLE = {
    "PR": FixturePerson("Priya Raman", "priya@simplyluma.com", ("life-ari-street", .705, .2, 5.2), hue=330),
    "NF": FixturePerson("Nora Feld", "nora@simplyluma.com", ("life-sync-terrace", .36, .27, 5), hue=45),
    "SK": FixturePerson("Sam Kaur", "samk@simplyluma.com", ("life-suite-bg-write", .47, .3, 5), hue=200),
    "TH": FixturePerson("Theo Marsh", "theo@simplyluma.com", ("take-back", .575, .12, 5.4), hue=350),
    "AR": FixturePerson("Alex Ruiz", "alexr@simplyluma.com", ("life-professionals-loft", .55, .36, 5.2), hue=262),
    "MS": FixturePerson("Maya Singh", "maya@fieldhouse.co", hue=20),
    "BO": FixturePerson("Ben Okoro", "ben@simplyluma.com", hue=140),
    "EN": FixturePerson("Eli Novak", "eli@simplyluma.com", hue=285),
    "rail": FixturePerson("Coastline Rail", "tickets@coastlinerail.com", brand=("C", "#0f5d73")),
    "light": FixturePerson("Slow Light", "letters@slowlight.email", brand=("S", "#8a5a2b")),
    "parcel": FixturePerson("Parcel & Post", "orders@parcelandpost.com", brand=("P", "#3d4b8f")),
}
BY_ADDRESS = {person.address: person for person in PEOPLE.values()}

#: Labels the mailbox column lists (v71 crNav): name, hue, what tapping says.
LABELS = (("Launch", 30, "Launch: 4 threads"), ("Receipts", 150, "Receipts: 2 threads"))

#: Recent files the editor's Attach offers (v71 crPick).
RECENT_FILES = (("launch-notes.pdf", "96 KB"), ("homepage-crop.png", "2.4 MB"), ("budget-q4.sheet", "64 KB"))


def _at(days_ago: int, hour: int, minute: int) -> datetime:
    day = NOW - timedelta(days=days_ago)
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _file(name: str, size: int, content_type: str) -> Attachment:
    return Attachment(f"att-{name}", name, content_type, size)


_DECK = "launch-walkthrough-v3.stage"
RAIL_HTML = """<body style="margin:0;background:#f1efe9;font:14px/1.5 -apple-system,'Segoe UI',Helvetica,Arial,sans-serif;color:#1d2230"><div style="max-width:560px;margin:0 auto;padding:26px 22px 30px">
<div style="display:flex;align-items:center;gap:10px;margin-bottom:18px"><div style="width:30px;height:30px;border-radius:8px;background:#0f5d73;color:#fff;font-weight:700;display:grid;place-items:center">C</div><b style="font-size:15px">Coastline Rail</b><span style="margin-left:auto;font-size:12px;color:#6b7280">Booking CR-48213</span></div>
<div style="background:#fff;border-radius:16px;padding:22px;box-shadow:0 1px 3px rgba(0,0,0,.08)">
<div style="font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:#6b7280;font-weight:600">Thursday, October 1 · One way</div>
<div style="display:flex;justify-content:space-between;align-items:flex-end;margin:12px 0 18px"><div><div style="font-size:32px;font-weight:700;letter-spacing:-.02em">7:05</div><div style="color:#4b5563">Oakland Jack London</div></div><div style="color:#9aa0a8;font-size:12px;padding-bottom:6px">1 h 52 m</div><div style="text-align:right"><div style="font-size:32px;font-weight:700;letter-spacing:-.02em">8:57</div><div style="color:#4b5563">Sacramento Valley</div></div></div>
<table style="font-size:13px;border-collapse:collapse;width:100%;border-top:1px dashed #d8d4ca"><tr><td style="color:#6b7280;padding:12px 0 3px">Passenger</td><td style="text-align:right;font-weight:600">Nick</td></tr><tr><td style="color:#6b7280;padding:3px 0">Coach · Seat</td><td style="text-align:right;font-weight:600">3 · 14A, window</td></tr><tr><td style="color:#6b7280;padding:3px 0">Fare</td><td style="text-align:right;font-weight:600">$31.00</td></tr></table></div>
<p style="color:#6b7280;font-size:12.5px;margin:18px 2px">Show this code on board. Changes are free until two hours before departure.</p>
<a style="display:inline-block;background:#0f5d73;color:#fff;padding:11px 18px;border-radius:10px;font-weight:600;font-size:13.5px">Add to calendar</a> <a style="display:inline-block;color:#0f5d73;padding:11px 12px;font-weight:600;font-size:13.5px">Manage booking</a></div></body>"""
LIGHT_HTML = """<body style="margin:0;background:#fbf8f3;font:15px/1.65 Georgia,'Times New Roman',serif;color:#2a2622"><div style="max-width:580px;margin:0 auto;padding:30px 24px 36px">
<div style="text-align:center;font:600 11px/1 -apple-system,Helvetica,sans-serif;letter-spacing:.24em;color:#8a5a2b;text-transform:uppercase">Slow Light · No. 112</div>
<h1 style="font-weight:400;font-size:30px;line-height:1.2;text-align:center;margin:14px 0 22px">The hour after the golden hour</h1>
<img src="https://slowlight.email/i/112/hero.jpg" style="display:block;width:100%;height:280px;object-fit:cover;border-radius:6px" alt="">
<p style="margin:18px 0">Everyone chases the gold. This week we stayed out after it, when the light goes blue and soft and a room starts to glow from the inside.</p>
<p style="margin:0 0 18px">Three things to try: expose for the window, not the room. Let the lamps clip. And wait ten minutes longer than feels reasonable.</p>
<a style="display:inline-block;border:1px solid #2a2622;padding:10px 18px;border-radius:999px;font:600 13px -apple-system,Helvetica,sans-serif">Read the full letter</a>
<p style="margin:30px 0 0;font:12px/1.5 -apple-system,Helvetica,sans-serif;color:#9a9186;text-align:center">You're getting this because you subscribed at slowlight.email. <u>Unsubscribe</u></p></div></body>"""
PARCEL_HTML = """<body style="margin:0;background:#eef0f6;font:14px/1.5 -apple-system,'Segoe UI',Helvetica,Arial,sans-serif;color:#1e2233"><div style="max-width:540px;margin:0 auto;padding:26px 22px 30px">
<b style="display:block;font-size:17px;color:#3d4b8f;margin-bottom:16px">Parcel &amp; Post</b>
<div style="background:#fff;border-radius:14px;padding:22px 22px 12px"><div style="font-size:20px;font-weight:700">Thanks, Nick. It's on the way.</div><div style="color:#6b7280;margin:4px 0 16px">Order #2048 · arriving Friday, September 25</div>
<table style="width:100%;border-collapse:collapse;font-size:13.5px"><tr><td style="padding:9px 0;border-top:1px solid #eceef4">Linen notebook, A5 × 2</td><td style="text-align:right;border-top:1px solid #eceef4">$24.00</td></tr><tr><td style="padding:9px 0;border-top:1px solid #eceef4">Brass pen, medium nib</td><td style="text-align:right;border-top:1px solid #eceef4">$38.00</td></tr><tr><td style="padding:9px 0;border-top:1px solid #eceef4">Shipping</td><td style="text-align:right;border-top:1px solid #eceef4">$0.00</td></tr>
<tr><td style="padding:12px 0;border-top:2px solid #1e2233;font-weight:700">Total</td><td style="text-align:right;border-top:2px solid #1e2233;font-weight:700">$62.00</td></tr></table></div>
<a style="display:inline-block;margin-top:16px;background:#3d4b8f;color:#fff;padding:11px 18px;border-radius:10px;font-weight:600">Track package</a></div></body>"""


@dataclass(frozen=True)
class FixtureMessage:
    id: str
    sender: str                      # a PEOPLE key, or "me"
    to: tuple[str, ...]
    when: datetime
    text: tuple[str, ...] = ()
    unread: bool = False
    signature: str = ""
    files: tuple[Attachment, ...] = ()
    photos: tuple[str, ...] = ()     # social-luma/photos names, shown as the message's photos
    quote: str = ""
    html: str = ""
    snippet: str = ""
    blocked: bool = False            # remote images held back until asked (v71 "Load images")
    event: tuple[str, datetime, str, str] | None = None   # (title, start, when, where)


@dataclass(frozen=True)
class FixtureThread:
    id: str
    subject: str
    folder: str
    messages: tuple[FixtureMessage, ...]
    account: str = "luma"
    flagged: bool = False
    update: bool = False             # in the Updates bundle (v71 `upd`)
    draft: str = ""


THREADS = (
    FixtureThread("deck", "Launch walkthrough deck", "inbox", flagged=True, messages=(
        FixtureMessage("d1", "PR", ("me", "NF"), _at(2, 16, 12), (
            "Hi both,",
            "Here’s v3 of the walkthrough deck. I moved the milestones slide up front and cut the pricing section; we can talk to that live.",
            "Two things I need before Thursday:\n• Nick: the ISO timeline for slide 7\n• Nora: the homepage crop for slide 2"),
            signature="Priya Raman\nDesign lead, Luma",
            files=(_file(_DECK, 18_400_000, "application/x-luma-stage"), _file("milestones.pdf", 412_000, "application/pdf"))),
        FixtureMessage("d2", "NF", ("PR", "me"), _at(2, 17, 3), (
            "Crop’s in. I went with the wider one; it breathes better on the slide.",),
            photos=("life-sync-terrace",),
            quote="On Tue, Sep 22, Priya Raman wrote:\nHere’s v3 of the walkthrough deck. I moved the milestones slide up front and cut the pricing section…"),
        FixtureMessage("d3", "me", ("PR", "NF"), _at(1, 9, 15), (
            "Timeline is on slide 7: nightly ISO on the 28th, sign-off on the 30th, launch on the 1st.",
            "Looks great, both of you.")),
        FixtureMessage("d4", "PR", ("me", "NF"), _at(0, 9, 41), (
            "Perfect. Last thing: can you both do a 20-minute dry run at 7:30 tonight? I’ll send an invite.",)),
    )),
    FixtureThread("lease", "Studio lease renewal", "inbox", messages=(
        FixtureMessage("l1", "TH", ("me",), _at(0, 8, 2), (
            "Hi Nick,",
            "Attached is the draft for the 2027 renewal. Same terms as this year, except the landlord wants to move the rent review from January to March.",
            "If that works for you, sign page 4 and I’ll send it over. Happy to walk through it by phone."),
            unread=True, signature="Theo Marsh\nMarsh & Co. Property · (510) 555-0199",
            files=(_file("Lease-2027-draft.pdf", 1_200_000, "application/pdf"),)),
    )),
    FixtureThread("rail", "Your tickets: Oakland to Sacramento", "inbox", update=True, messages=(
        FixtureMessage("r1", "rail", ("me",), _at(0, 7, 30), unread=True, html=RAIL_HTML,
                       snippet="Thursday, October 1 · 7:05 → 8:57 · Coach 3, seat 14A"),
    )),
    FixtureThread("offsite", "Photos from the offsite", "inbox", messages=(
        FixtureMessage("o1", "SK", ("me",), _at(3, 18, 40), (
            "A few favorites. The full set is in Photos under “Offsite”.",),
            photos=("life-hero-studio", "life-professionals-loft", "life-work-suite", "life-hero-writing")),
        FixtureMessage("o2", "me", ("SK",), _at(3, 19, 2), (
            "These are great. The second one might be the new homepage.",)),
    )),
    FixtureThread("light", "The hour after the golden hour", "inbox", update=True, messages=(
        FixtureMessage("g1", "light", ("me",), _at(3, 6, 0), html=LIGHT_HTML, blocked=True,
                       snippet="Everyone chases the gold. This week we stayed out after it…"),
    )),
    FixtureThread("iso", "Re: ISO sign-off checklist", "inbox", messages=(
        FixtureMessage("i1", "BO", ("me", "EN"), _at(4, 11, 20), (
            "Checklist for the 30th. Anything missing?",
            "1. Clean boot on the ThinkPad and the test box\n2. Depot channels point at stable\n3. Release notes signed off\n4. Checksums published"),
            files=(_file("signoff.sheet", 48_000, "application/x-luma-sheet"),)),
        FixtureMessage("i2", "EN", ("BO", "me"), _at(4, 14, 5), (
            "Add “upgrade from the last nightly”. That’s what bit us last time.",)),
    )),
    FixtureThread("dinner", "Dinner Saturday?", "inbox", account="home", messages=(
        FixtureMessage("n1", "MS", ("me",), _at(12, 15, 30), (
            "We’re doing dinner at ours on the 26th. Seven-ish? Bring nothing, or maybe dessert.",),
            event=("Dinner at Maya’s", datetime(2026, 9, 26, 19, 0).astimezone(), "Saturday, 7:00 PM", "Maya’s place")),
    )),
    FixtureThread("parcel", "Receipt for order #2048", "inbox", account="home", update=True, messages=(
        FixtureMessage("p1", "parcel", ("me",), _at(13, 10, 14), html=PARCEL_HTML,
                       snippet="Thanks, Nick. It’s on the way. Arriving Friday, September 25."),
    )),
    FixtureThread("speakers", "Speaker list for launch night", "drafts", draft="Thinking Priya opens, then a two-minute demo, then", messages=()),
    FixtureThread("venue", "Venue confirmed", "sent", messages=(
        FixtureMessage("v1", "me", ("NF", "PR"), _at(14, 13, 10), ("The loft is booked for the 1st, 6 to 10 PM.",)),
    )),
)


@dataclass
class Fixture:
    """What the v71 window needs beyond the mail model: who people are and how they look."""

    people: dict[str, FixturePerson] = field(default_factory=lambda: dict(PEOPLE))
    #: message id (store) -> the FixtureMessage it came from
    messages: dict[str, FixtureMessage] = field(default_factory=dict)
    #: thread subject (normalized) -> thread
    updates: set[str] = field(default_factory=set)
    drafts: dict[str, FixtureThread] = field(default_factory=dict)
    labels: tuple = LABELS
    recent_files: tuple = RECENT_FILES
    now: datetime = NOW
    assets: Path | None = None

    def person(self, address: str) -> FixturePerson | None:
        return BY_ADDRESS.get(address.casefold())

    def photo(self, name: str) -> Path | None:
        if self.assets is None:
            return None
        for suffix in (".webp", ".jpg", ".png"):
            path = self.assets / f"{name}{suffix}"
            if path.exists():
                return path
        return None


def _address(key: str, account: str) -> str:
    if key == "me":
        return HOME if account == "home" else ME
    return PEOPLE[key].address


def load(store) -> Fixture:
    """Fill an empty in-memory store with v71's mailbox; return what the window needs beside it."""
    fixture = Fixture()
    assets = os.environ.get("LUMA_CHARLIE_FIXTURE_ASSETS", "")
    fixture.assets = Path(assets) if assets else None
    for account in ACCOUNTS:
        store.upsert_account(account)
        store.upsert_server_config(account.id, ServerConfig(imap_host="fixture.invalid"))
    uid = 0
    for thread in THREADS:
        if thread.folder == "drafts":
            fixture.drafts[thread.id] = thread
        root = f"<{thread.id}@fixture.v71>"
        for index, item in enumerate(thread.messages):
            uid += 1
            message_id = root if index == 0 else f"<{thread.id}-{item.id}@fixture.v71>"
            references = () if index == 0 else (root,)
            outgoing = item.sender == "me"
            sender = PEOPLE.get(item.sender)
            text = "\n\n".join(item.text)
            if item.signature:
                text = f"{text}\n\n-- \n{item.signature}"
            subject = thread.subject if index == 0 else (
                thread.subject if thread.subject.startswith("Re:") else f"Re: {thread.subject}")
            # What you sent lives in Sent, as on a server; the thread still reads as one conversation.
            folder = "sent" if outgoing and thread.folder == "inbox" else thread.folder
            message = Message(
                id=f"{thread.account}:{folder}:{item.id}",
                account_id=thread.account,
                folder=folder,
                uid=uid,
                message_id=message_id,
                thread_id=stable_thread_id(message_id, subject, references),
                subject=subject,
                sender_name="" if outgoing else sender.name,
                sender_address=_address(item.sender, thread.account),
                recipients=tuple(_address(key, thread.account) for key in item.to),
                sent_at=item.when,
                snippet=item.snippet or " ".join(" ".join(item.text).split())[:240],
                body_text=text if not item.html else item.snippet,
                body_html=item.html,
                unread=item.unread,
                flagged=thread.flagged,
                outgoing=outgoing,
                attachments=item.files,
                references=references,
            )
            store.upsert_message(message)
            fixture.messages[message.id] = item
            if thread.update:
                fixture.updates.add(message.id)
    return fixture
