# SPDX-License-Identifier: Apache-2.0
"""Fixed Charlie mailbox operations; credentials are never response values."""
import base64
from dataclasses import asdict
import json
import re
from .model import Account, ServerConfig, Draft

BUS = "org.projectluma.MailHost1"
OBJECT = "/org/projectluma/Charlie/MailHost1"
MAX_WIRE = 24 * 1024 * 1024
MAX_ATTACHMENTS = 12 * 1024 * 1024
OPERATIONS = frozenset({"SaveAccount", "RemoveAccount", "Sync", "MarkRead", "Send", "GoogleSignIn", "MicrosoftSignIn", "HydrateAvatar"})


def encode(value):
    pieces = []; size = 0
    for piece in json.JSONEncoder(ensure_ascii=True, allow_nan=False, separators=(",", ":")).iterencode(value):
        size += len(piece)
        if size > MAX_WIRE: raise ValueError("The mail request is too large.")
        pieces.append(piece)
    return "".join(pieces)


def decode(text):
    if not isinstance(text, str) or len(text) > MAX_WIRE: raise ValueError("The mail request is too large.")
    doc = json.loads(text, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("Invalid mail value.")))
    if not isinstance(doc, dict): raise ValueError("Invalid mail request.")
    return doc


def string(value, maximum=4096):
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value: raise ValueError("Invalid mail text.")
    return value


def account(value):
    if not isinstance(value, dict) or set(value) != set(Account.__dataclass_fields__): raise ValueError("Invalid mail account.")
    for key in value:
        if key == "enabled":
            if type(value[key]) is not bool: raise ValueError("Invalid account state.")
        else: string(value[key], 2048)
    if not re.fullmatch(r"[A-Za-z0-9_.@+-]{1,256}", value["id"]): raise ValueError("Invalid account identifier.")
    if value["provider"] not in {"gmail", "microsoft", "imap", "custom", "icloud", "yahoo", "fastmail", "outlook", "zoho", "aol"}: raise ValueError("Unknown mail provider.")
    return Account(**value)


def config(value):
    if not isinstance(value, dict) or set(value) != set(ServerConfig.__dataclass_fields__): raise ValueError("Invalid mail server configuration.")
    for key in ("imap_host", "smtp_host"):
        host = string(value[key], 253)
        if not host or any(c.isspace() for c in host) or any(c in host for c in "/\\@?#"): raise ValueError("Invalid mail server address.")
    string(value["username"], 2048)
    for key in ("imap_port", "smtp_port"):
        if type(value[key]) is not int or not 1 <= value[key] <= 65535: raise ValueError("Invalid mail port.")
    if type(value["use_starttls"]) is not bool: raise ValueError("Invalid mail TLS setting.")
    return ServerConfig(**value)


def strings(value, count=256, length=2048):
    if not isinstance(value, list) or len(value) > count: raise ValueError("Too many mail items.")
    return tuple(string(item, length) for item in value)


def draft(value):
    if not isinstance(value, dict) or set(value) != set(Draft.__dataclass_fields__): raise ValueError("Invalid draft.")
    data = dict(value)
    for key in ("account_id", "subject", "in_reply_to"): string(data[key], 8192)
    string(data["body"], 8 * 1024 * 1024)
    for key in ("to", "cc", "bcc", "references"): data[key] = list(strings(data[key]))
    if not isinstance(data["attachments"], list) or len(data["attachments"]) > 32: raise ValueError("Too many attachments.")
    payloads = []; total = 0
    for item in data["attachments"]:
        if not isinstance(item, dict) or set(item) != {"filename", "data"}: raise ValueError("Attachments must contain bytes, not host paths.")
        name = string(item["filename"], 255)
        if not name or name in {".", ".."} or "/" in name or "\\" in name: raise ValueError("Invalid attachment name.")
        text = string(item["data"], 16 * 1024 * 1024)
        content = base64.b64decode(text, validate=True); total += len(content)
        if total > MAX_ATTACHMENTS: raise ValueError("Attachments exceed the supported size.")
        payloads.append((name, content))
    data["attachments"] = []
    return Draft(**data), payloads


def validate(operation, data):
    if operation not in OPERATIONS or not isinstance(data, dict): raise ValueError("Unsupported mail operation.")
    fields = {"SaveAccount": {"account", "config", "password"}, "RemoveAccount": {"account_id"}, "Sync": {"account_id"}, "HydrateAvatar": {"account_id"}, "MarkRead": {"message_ids", "read"}, "Send": {"draft"}, "GoogleSignIn": set(), "MicrosoftSignIn": {"client_id"}}[operation]
    if set(data) != fields: raise ValueError("Unexpected mail fields.")
    if operation == "SaveAccount":
        return {"account": account(data["account"]), "config": config(data["config"]), "password": string(data["password"], 65536)}
    if "account_id" in data: return {"account_id": string(data["account_id"], 256)}
    if operation == "MarkRead":
        if type(data["read"]) is not bool: raise ValueError("Invalid read state.")
        return {"message_ids": strings(data["message_ids"]), "read": data["read"]}
    if operation == "Send":
        message, attachments = draft(data["draft"]); return {"draft": message, "attachments": attachments}
    if operation == "MicrosoftSignIn": return {"client_id": string(data["client_id"], 256)}
    return {}
