# SPDX-License-Identifier: Apache-2.0
"""Defensive MIME parsing for the mail engine."""
from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass
from email import policy
from email.message import Message as EmailMessage
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from base64 import b64encode
import re
from urllib.parse import unquote

from .model import Attachment, Message, address_parts, stable_thread_id


@dataclass(frozen=True, slots=True)
class MessagePresentation:
    """The smallest faithful surface for one message.

    MIME type is not a presentation decision: ordinary replies are commonly
    HTML solely because the sender used Gmail or Outlook. Charlie renders those
    as native conversation bubbles after removing quoted history, while keeping
    genuinely designed documents in the locked-down HTML reader.
    """

    designed_html: bool
    text: str
    html: str
    inline_images: tuple[Attachment, ...] = ()


_QUOTE_CLASSES = {
    "gmail_attr",
    "gmail_quote",
    "gmail_quote_container",
    "moz-cite-prefix",
    "protonmail_quote",
    "yahoo_quoted",
}
_QUOTE_IDS = {"divrplyfwdmsg", "appendonsend"}
_BLOCK_TAGS = {
    "address", "article", "br", "dd", "div", "dl", "dt", "footer",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "p",
    "section", "tr",
}
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class _PresentationExtractor(HTMLParser):
    """Extract current-message text and layout signals without active content."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.cid_references: list[str] = []
        self.hidden_depth = 0
        self.quote_depth = 0
        self.frames: list[tuple[str, bool, bool]] = []
        self.has_quote = False
        self.tables = 0
        self.images = 0
        self.style_chars = 0
        self.layout_signals = 0
        self.button_links = 0
        self._in_style = False

    @staticmethod
    def _is_quote(tag: str, attrs: dict[str, str]) -> bool:
        classes = {item.casefold() for item in attrs.get("class", "").split()}
        marker = attrs.get("data-marker", "").casefold()
        return (
            tag == "blockquote"
            or bool(classes & _QUOTE_CLASSES)
            or attrs.get("id", "").casefold() in _QUOTE_IDS
            or marker == "__quoted_text__"
        )

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.casefold()
        values = {str(key).casefold(): str(value or "") for key, value in attrs}
        hidden = tag in {"head", "script", "style", "template"}
        quoted = self._is_quote(tag, values)
        if tag not in _VOID_TAGS:
            self.frames.append((tag, hidden, quoted))
            if hidden:
                self.hidden_depth += 1
            if quoted:
                self.quote_depth += 1
                self.has_quote = True
        if self.hidden_depth or self.quote_depth:
            self._in_style = tag == "style"
            return
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("• ")
        if tag == "table":
            self.tables += 1
        if tag == "img":
            self.images += 1
            source = values.get("src", "").strip()
            if source.casefold().startswith("cid:"):
                self.cid_references.append(unquote(source[4:]).strip("<> ").casefold())
        style = values.get("style", "").casefold()
        if any(token in style for token in ("background:", "background-color:", "display:grid", "display:flex")):
            self.layout_signals += 1
        if values.get("role", "").casefold() == "presentation" or "bgcolor" in values:
            self.layout_signals += 1
        if tag == "a" and any(token in style for token in ("background:", "background-color:", "border-radius:")):
            self.button_links += 1
        if tag in {"canvas", "svg", "video"}:
            self.layout_signals += 2

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        match = next(
            (index for index in range(len(self.frames) - 1, -1, -1) if self.frames[index][0] == tag),
            None,
        )
        if match is None:
            return
        was_visible = not self.hidden_depth and not self.quote_depth
        closing = self.frames[match:]
        del self.frames[match:]
        self.hidden_depth = max(0, self.hidden_depth - sum(item[1] for item in closing))
        self.quote_depth = max(0, self.quote_depth - sum(item[2] for item in closing))
        if was_visible and tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "style":
            self._in_style = False

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self.style_chars += len(data)
        elif not self.hidden_depth and not self.quote_depth:
            self.parts.append(data)


def _clean_lines(value: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in unescape(value).replace("\xa0", " ").splitlines()]
    output: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if output and not blank:
                output.append("")
            blank = True
            continue
        output.append(line)
        blank = False
    return "\n".join(output).strip()


def strip_quoted_text(value: str) -> str:
    """Return the authored portion of a plain-text reply.

    The patterns are intentionally restricted to conventional mail quote
    boundaries. They do not attempt fuzzy natural-language signature removal.
    """

    lines = (value or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()
    boundary = len(lines)
    for index, line in enumerate(lines):
        stripped = line.strip()
        lowered = stripped.casefold()
        if stripped.startswith(">"):
            boundary = index
            break
        if re.match(r"^on .{1,500} wrote:$", lowered):
            boundary = index
            break
        if re.match(r"^-{2,}\s*(original message|forwarded message)\s*-{2,}$", lowered):
            boundary = index
            break
        if lowered.startswith("from:"):
            nearby = {item.strip().split(":", 1)[0].casefold() for item in lines[index:index + 6] if ":" in item}
            if len(nearby & {"from", "sent", "date", "to", "subject"}) >= 3:
                boundary = index
                break
    return _clean_lines("\n".join(lines[:boundary]))


def _designed_html(extracted: _PresentationExtractor) -> bool:
    """Prefer native conversation UI unless HTML carries real layout intent."""

    strong_layout = (
        extracted.tables >= 2
        or extracted.layout_signals >= 2
        or extracted.button_links >= 2
        or (extracted.tables >= 1 and (extracted.images >= 1 or extracted.layout_signals >= 1))
        or extracted.images >= 3
    )
    if extracted.has_quote and not strong_layout:
        return False
    return strong_layout or extracted.style_chars >= 600


def _resolve_cid_images(source: str, attachments: tuple[Attachment, ...]) -> str:
    payloads = {
        attachment.content_id.strip("<> ").casefold(): attachment
        for attachment in attachments
        if attachment.content_id and attachment.content_type.casefold().startswith("image/") and attachment.data
    }

    def replace(match: re.Match[str]) -> str:
        key = unquote(match.group(2)).strip("<> ").casefold()
        attachment = payloads.get(key)
        if attachment is None:
            return match.group(0)
        encoded = b64encode(attachment.data).decode("ascii")
        return f"{match.group(1)}data:{attachment.content_type};base64,{encoded}{match.group(3)}"

    return re.sub(r"(?i)(src\s*=\s*['\"])cid:([^'\"]+)(['\"])", replace, source or "")


def message_presentation(message: Message) -> MessagePresentation:
    """Classify and simplify a MIME message for the conversation transcript."""

    if not message.body_html:
        return MessagePresentation(False, strip_quoted_text(message.body_text), "")
    parser = _PresentationExtractor()
    try:
        parser.feed(message.body_html)
        parser.close()
    except (AssertionError, ValueError):
        return MessagePresentation(False, strip_quoted_text(message.body_text), "")
    text = _clean_lines("".join(parser.parts)) or strip_quoted_text(message.body_text)
    attachment_by_cid = {
        item.content_id.strip("<> ").casefold(): item
        for item in message.attachments
        if item.content_id and item.content_type.casefold().startswith("image/") and item.data
    }
    inline_images = tuple(
        attachment_by_cid[key]
        for key in dict.fromkeys(parser.cid_references)
        if key in attachment_by_cid
    )
    designed = _designed_html(parser)
    return MessagePresentation(
        designed,
        text,
        _resolve_cid_images(message.body_html, message.attachments) if designed else "",
        inline_images,
    )


class _TextExtractor(HTMLParser):
    BLOCKS = {"p", "div", "li", "br", "tr", "h1", "h2", "h3", "h4", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        del attrs
        if tag in {"script", "style", "head"}:
            self.hidden += 1
        elif not self.hidden and tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "head"} and self.hidden:
            self.hidden -= 1
        elif not self.hidden and tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def html_to_text(source: str) -> str:
    parser = _TextExtractor()
    parser.feed(source or "")
    value = unescape("".join(parser.parts)).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _payload_text(part: EmailMessage) -> str:
    try:
        return part.get_content()
    except (LookupError, UnicodeError):
        payload = part.get_payload(decode=True) or b""
        return payload.decode(part.get_content_charset() or "utf-8", "replace")


def _date(value: str | None) -> datetime:
    try:
        parsed = parsedate_to_datetime(value or "")
    except (TypeError, ValueError, OverflowError):
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_message(
    raw: bytes,
    *,
    account_id: str,
    folder: str,
    uid: int,
    unread: bool,
    flagged: bool,
    outgoing: bool,
) -> Message:
    envelope = BytesParser(policy=policy.default).parsebytes(raw)
    plain = ""
    html = ""
    attachments: list[Attachment] = []
    parts = envelope.walk() if envelope.is_multipart() else (envelope,)
    for index, part in enumerate(parts):
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        disposition = part.get_content_disposition()
        filename = part.get_filename()
        decoded = part.get_payload(decode=True) or b""
        if disposition == "attachment" or filename:
            attachments.append(
                Attachment(
                    id=f"{uid}:{index}",
                    filename=filename or f"Attachment {index + 1}",
                    content_type=content_type,
                    size=len(decoded),
                    content_id=(part.get("Content-ID") or "").strip("<>"),
                    data=decoded,
                )
            )
        elif content_type == "text/plain" and not plain:
            plain = _payload_text(part)
        elif content_type == "text/html" and not html:
            html = _payload_text(part)
    body = plain.strip() or html_to_text(html)
    compact = " ".join(body.split())
    references = list((envelope.get("References") or "").split())
    in_reply_to = (envelope.get("In-Reply-To") or "").strip()
    if in_reply_to and in_reply_to not in references:
        references.append(in_reply_to)
    message_id = (envelope.get("Message-ID") or "").strip()
    sender_name, sender_address = address_parts(envelope.get("From") or "")
    recipient_headers = envelope.get_all("To", []) + envelope.get_all("Cc", [])
    recipients = tuple(address_parts(item)[1] for value in recipient_headers for item in value.split(","))
    subject = str(envelope.get("Subject") or "(No subject)")
    return Message(
        id=f"{account_id}:{folder}:{uid}",
        account_id=account_id,
        folder=folder,
        uid=uid,
        message_id=message_id,
        thread_id=stable_thread_id(message_id, subject, references),
        subject=subject,
        sender_name=sender_name,
        sender_address=sender_address,
        recipients=recipients,
        sent_at=_date(envelope.get("Date")),
        snippet=compact[:240],
        body_text=body,
        body_html=html,
        unread=unread,
        flagged=flagged,
        outgoing=outgoing,
        attachments=tuple(attachments),
        references=tuple(references),
    )
