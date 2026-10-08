# SPDX-License-Identifier: Apache-2.0
from email.message import EmailMessage
from datetime import datetime, timezone
import unittest

from charlie_luma.mime import (
    html_to_text,
    message_presentation,
    parse_message,
    strip_quoted_text,
)
from charlie_luma.model import Attachment, Message


class MimeTests(unittest.TestCase):
    @staticmethod
    def _message(
        html: str,
        *,
        text: str = "",
        attachments: tuple[Attachment, ...] = (),
        outgoing: bool = False,
    ) -> Message:
        return Message(
            id="demo:inbox:1",
            account_id="demo",
            folder="sent" if outgoing else "inbox",
            uid=1,
            message_id="<one@example.test>",
            thread_id="thread",
            subject="Re: Hello",
            sender_name="Leon",
            sender_address="leon@example.test",
            recipients=("you@example.test",),
            sent_at=datetime.now(timezone.utc),
            snippet="Hello",
            body_text=text,
            body_html=html,
            outgoing=outgoing,
            attachments=attachments,
        )

    def test_html_extraction_drops_active_content(self):
        value = html_to_text("<style>secret{}</style><p>Hello&nbsp;there</p><script>bad()</script>")
        self.assertEqual(value, "Hello there")

    def test_multipart_prefers_plain_text_and_records_attachment(self):
        mail = EmailMessage()
        mail["From"] = "Carla <carla@example.test>"
        mail["To"] = "you@example.test"
        mail["Subject"] = "Status"
        mail["Message-ID"] = "<status@example.test>"
        mail.set_content("Plain body")
        mail.add_alternative("<p>HTML body</p>", subtype="html")
        mail.add_attachment(b"report", maintype="application", subtype="pdf", filename="report.pdf")
        parsed = parse_message(mail.as_bytes(), account_id="demo", folder="inbox", uid=42,
                               unread=True, flagged=False, outgoing=False)
        self.assertEqual(parsed.body_text, "Plain body")
        self.assertIn("HTML body", parsed.body_html)
        self.assertEqual(parsed.attachments[0].filename, "report.pdf")
        self.assertEqual(parsed.attachments[0].data, b"report")
        self.assertTrue(parsed.unread)

    def test_in_reply_to_is_used_when_references_is_missing(self):
        mail = EmailMessage()
        mail["From"] = "Leon <leon@example.test>"
        mail["To"] = "you@example.test"
        mail["Subject"] = "Re: Project"
        mail["Message-ID"] = "<reply@example.test>"
        mail["In-Reply-To"] = "<root@example.test>"
        mail.set_content("Reply")
        parsed = parse_message(mail.as_bytes(), account_id="demo", folder="inbox", uid=43,
                               unread=False, flagged=False, outgoing=False)
        self.assertEqual(parsed.references, ("<root@example.test>",))

    def test_plain_quote_history_is_removed_at_conventional_boundary(self):
        value = "Thanks — that works.\n\nOn Sun, Sep 13, 2026, Alex wrote:\n> Old reply\n> Older reply"
        self.assertEqual(strip_quoted_text(value), "Thanks — that works.")

    def test_gmail_html_reply_becomes_native_text_with_inline_image(self):
        image = Attachment(
            "sig",
            "signature.png",
            "image/png",
            8,
            content_id="leon-image",
            data=b"not-a-real-png",
        )
        message = self._message(
            """<div>Hello <strong>there</strong>.</div>
            <img src="cid:leon-image">
            <div class="gmail_attr">On Sunday, Alex wrote:</div>
            <blockquote class="gmail_quote"><div>Old history</div></blockquote>""",
            text="Hello there.\n\n> Old history",
            attachments=(image,),
        )
        presentation = message_presentation(message)
        self.assertFalse(presentation.designed_html)
        self.assertEqual(presentation.text, "Hello there.")
        self.assertEqual(presentation.inline_images, (image,))
        self.assertEqual(presentation.html, "")

    def test_real_layout_keeps_html_and_embeds_cid_payload(self):
        image = Attachment(
            "hero", "hero.png", "image/png", 3, content_id="hero", data=b"png"
        )
        message = self._message(
            """<table role="presentation"><tr><td><table><tr><td>
            <img src='cid:hero'><a style="background:#fff;border-radius:8px">Open</a>
            </td></tr></table></td></tr></table>""",
            attachments=(image,),
        )
        presentation = message_presentation(message)
        self.assertTrue(presentation.designed_html)
        self.assertIn("data:image/png;base64,cG5n", presentation.html)
        self.assertEqual(presentation.inline_images, (image,))

    def test_lightweight_formatted_text_stays_in_native_conversation(self):
        message = self._message("<p>Hello <strong>there</strong>.</p><p>See you soon.</p>")
        presentation = message_presentation(message)
        self.assertFalse(presentation.designed_html)
        self.assertEqual(presentation.text, "Hello there.\n\nSee you soon.")
