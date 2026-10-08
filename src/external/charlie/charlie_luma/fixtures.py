# SPDX-License-Identifier: Apache-2.0
"""Synthetic, network-free mailbox used by first-run and runtime tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .model import Account, Attachment, Message, stable_thread_id


DEMO_ACCOUNT = Account(
    id="demo-luma",
    display_name="Luma",
    address="you@example.test",
    provider="demo",
    colour="violet",
)


def _message(
    uid: int,
    sender: str,
    address: str,
    subject: str,
    body: str,
    *,
    minutes_ago: int,
    unread: bool = False,
    outgoing: bool = False,
    attachments: tuple[Attachment, ...] = (),
    body_html: str = "",
    root: str | None = None,
) -> Message:
    message_id = f"<demo-{uid}@example.test>"
    references = (root,) if root else ()
    thread_id = stable_thread_id(message_id, subject, references)
    return Message(
        id=f"demo-luma:{'sent' if outgoing else 'inbox'}:{uid}",
        account_id=DEMO_ACCOUNT.id,
        folder="sent" if outgoing else "inbox",
        uid=uid,
        message_id=message_id,
        thread_id=thread_id,
        subject=subject,
        sender_name=sender,
        sender_address=address,
        recipients=(DEMO_ACCOUNT.address,),
        sent_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
        snippet=" ".join(body.split())[:240],
        body_text=body,
        body_html=body_html,
        unread=unread,
        outgoing=outgoing,
        attachments=attachments,
        references=references,
    )


def demo_messages() -> tuple[Message, ...]:
    root = "<demo-thread-carla@example.test>"
    attachments = (
        Attachment("demo-report", "Q3-Report-final.pdf", "application/pdf", 28, data=b"%PDF-1.4\n% Charlie preview\n"),
        Attachment("demo-budget", "Budget-2026.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 16, data=b"Charlie workbook\n"),
    )
    return (
        _message(1, "Carla Mendes", "carla@example.test", "Q3 report — final draft before Friday",
                 "Morning! The final draft is up. I folded in your notes on the shell section and tightened the roadmap page.",
                 minutes_ago=62, unread=True, root=root),
        _message(2, "You", DEMO_ACCOUNT.address, "Re: Q3 report — final draft before Friday",
                 "Perfect — reading it now. The Filer and Viola milestones read much better.",
                 minutes_ago=51, outgoing=True, root=root),
        _message(3, "Carla Mendes", "carla@example.test", "Re: Q3 report — final draft before Friday",
                 "Here’s the PDF and the budget sheet so you don’t have to dig. Theo signed off on Q4 this morning.",
                 minutes_ago=39, attachments=attachments,
                 body_html="""<h2>Files for Friday</h2>
                 <p>Here’s the <strong>final report</strong> and the budget sheet so you don’t have to dig.</p>
                 <p>Theo signed off on Q4 this morning. Please review the highlighted totals before Friday.</p>
                 <ul><li>PDF: final narrative</li><li>Workbook: approved budget</li></ul>
                 <p><a href="https://example.test/review">Open the review page</a></p>""",
                 root=root),
        _message(4, "You", DEMO_ACCOUNT.address, "Re: Q3 report — final draft before Friday",
                 "Two small things: the Viola date on page 4 should be Oct 2, and the chart legend still says ‘Q3 est.’ Otherwise good to go.",
                 minutes_ago=18, outgoing=True, root=root),
        _message(10, "Priya Raman", "priya@example.test", "Design review on Friday",
                 "Yes — sidebar first, then context menus. I have the long-string pass ready too.",
                 minutes_ago=104, unread=True),
        _message(11, "GitHub", "notifications@example.test", "[luma/shell] Fix dock focus ring on minimized windows",
                 "Merged the review into main. All checks passed.", minutes_ago=178),
        _message(12, "Jordan Ellis", "jordan@example.test", "Hiking Saturday?",
                 "You: See you at 6:15.", minutes_ago=1_400),
        _message(13, "Stripe", "receipts@example.test", "Your receipt from Framework Computer",
                 "Amount paid $1,449.00 · Framework Laptop 13.", minutes_ago=1_470,
                 attachments=(Attachment("receipt", "Receipt.pdf", "application/pdf", 92_000),)),
        _message(14, "Mina Okafor", "mina@example.test", "Read-aloud stops at chapter breaks",
                 "You: Thanks, Mina — that’s on us. There’s a fix in review.", minutes_ago=4_300),
        _message(15, "Airbnb", "trips@example.test", "Your trip to Joshua Tree is coming up",
                 "Check-in Friday after 3 PM. Your host Dana has shared the arrival details.", minutes_ago=7_200),
    )
