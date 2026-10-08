# SPDX-License-Identifier: Apache-2.0
"""Native mailbox owner. Modem and carrier files remain on the Fedora host."""
from pathlib import Path
import tempfile
from .messages_accounts import AccountStore
from .messages_backend import MessageStore, MAX_ATTACHMENT_BYTES, normalize_address, _parse_timestamp
from .messages_mms import read_mms_part

class NativeMailbox(AccountStore):
    # Add only request-authorization tables to the existing native database;
    # phone identity and reconciliation retain the native storage contract.
    canonical_address = MessageStore.canonical_address
    reconcile_phone_identities = MessageStore.reconcile_phone_identities


def import_mms(writer, path, properties):
    staged = []
    address = ''
    try:
        status = properties.get("Status", "")
        native_id = "mmsd:" + path
        if writer.is_transport_deleted(native_id):
            return
        existing = writer.uid_for_transport(native_id)
        if existing:
            if status in {"sent", "sending_failed"}:
                writer.update_state(existing, "sent" if status == "sent" else "failed")
            return
        # Queue acceptance can precede SendMessage's reply. Unmatched
        # outbound events must not manufacture a second local message.
        if status not in {"received", "read"}:
            return
        address = normalize_address(str(properties.get("Sender", "")))
        peers = {normalize_address(str(peer)) for peer in properties.get("Recipients", ())}
        own = str(properties.get("Modem Number", ""))
        if own:
            peers.discard(normalize_address(own))
        peers.discard(address)
        if peers:
            raise ValueError("Group MMS needs a group conversation model; the original remains in the MMS service.")
        body_parts = []
        with tempfile.TemporaryDirectory(prefix=".mms-import-", dir=writer.path.parent) as temporary:
            total = 0
            parts = properties.get("Attachments", ())
            if len(parts) > 100:
                raise ValueError("The MMS contains too many parts.")
            for index, part in enumerate(parts):
                name, content_type, filename, offset, length = part
                total += int(length)
                if total > MAX_ATTACHMENT_BYTES:
                    raise ValueError("The MMS exceeds the local attachment limit.")
                data = read_mms_part(str(filename), int(offset), int(length), root=Path.home() / ".mms", limit=MAX_ATTACHMENT_BYTES)
                if str(content_type).split(";", 1)[0] == "text/plain" and len(data) <= 65536:
                    body_parts.append(data.decode("utf-8", errors="replace"))
                    continue
                source = Path(temporary) / str(index)
                source.write_bytes(data); source.chmod(0o600)
                attachment = writer.attach_file(address, source, name=str(name))
                writer.set_attachment_type(attachment.uid, str(content_type).split(";", 1)[0])
                staged.append(attachment.uid)
            body = "\n".join(body_parts) or str(properties.get("Subject", ""))
            writer.add(address, body, direction="incoming", state=status,
                       timestamp=_parse_timestamp(str(properties.get("Date", ""))),
                       transport_id=native_id, attachment_uids=tuple(staged))
            staged.clear()
    finally:
        for uid in staged:
            writer.remove_draft_attachment(address, uid)
