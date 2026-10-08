// SPDX-License-Identifier: MPL-2.0
//! What goes inside an MLS application message, the same for every client.
//!
//! The sender is never in here: it is the authenticated MLS credential of the
//! leaf that encrypted the message. A payload that claimed one would be a
//! place for a member to lie about who wrote something.

use crate::attachment::AttachmentKey;
use crate::error::{Error, Result};
use serde::{Deserialize, Serialize};

pub const VERSION: u32 = 1;
pub const MAX_TEXT: usize = 64 * 1024;
pub const MAX_ATTACHMENTS: usize = 10;
pub const MAX_NAME: usize = 120;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ConversationKind {
    Direct,
    Group,
}

/// Sent by whoever creates or renames a conversation, so members who join by
/// Welcome learn what it is.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ConversationMeta {
    pub kind: ConversationKind,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub name: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Attachment {
    /// The delivery service's blob id.
    pub blob: String,
    #[serde(flatten)]
    pub key: AttachmentKey,
    pub mime: String,
    #[serde(default)]
    pub name: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Payload {
    pub v: u32,
    /// 32 lower-case hex characters, random: the message's id on every device,
    /// and what a device uses to recognise a message it already has.
    pub id: String,
    /// Milliseconds since the Unix epoch, by the sender's clock.
    pub sent: u64,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub text: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub attachments: Vec<Attachment>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub conversation: Option<ConversationMeta>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reply_to: Option<String>,
    /// The sender's @username as they claim it. Only a hint: a client shows it
    /// after the Hub confirms that username belongs to the sending account.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub handle: String,
}

pub fn new_message_id() -> String {
    use rand::RngCore;
    let mut bytes = [0u8; 16];
    rand::rngs::OsRng.fill_bytes(&mut bytes);
    crate::attachment::hex(&bytes)
}

impl Payload {
    pub fn text(text: &str, sent: u64) -> Self {
        Payload { v: VERSION, id: new_message_id(), sent, text: text.to_owned(), attachments: vec![], conversation: None, reply_to: None, handle: String::new() }
    }

    pub fn encode(&self) -> Result<Vec<u8>> {
        self.check()?;
        serde_json::to_vec(self).map_err(|_| Error::Malformed("payload".into()))
    }

    pub fn decode(bytes: &[u8]) -> Result<Self> {
        let payload: Payload = serde_json::from_slice(bytes).map_err(|_| Error::Malformed("payload".into()))?;
        payload.check()?;
        Ok(payload)
    }

    fn check(&self) -> Result<()> {
        let bad = |what: &str| Err(Error::Malformed(what.into()));
        if self.v != VERSION {
            return bad("payload version");
        }
        if self.id.len() != 32 || !self.id.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) {
            return bad("message id");
        }
        if self.text.len() > MAX_TEXT {
            return bad("text too long");
        }
        if self.attachments.len() > MAX_ATTACHMENTS {
            return bad("too many attachments");
        }
        for attachment in &self.attachments {
            if attachment.blob.is_empty() || attachment.blob.len() > 64 || attachment.mime.len() > 100 || attachment.name.len() > 255 {
                return bad("attachment");
            }
        }
        if let Some(meta) = &self.conversation {
            if meta.name.chars().count() > MAX_NAME {
                return bad("conversation name");
            }
        }
        if self.handle.len() > 40 {
            return bad("handle");
        }
        if let Some(reply) = &self.reply_to {
            if reply.len() != 32 {
                return bad("reply_to");
            }
        }
        if self.text.is_empty() && self.attachments.is_empty() && self.conversation.is_none() {
            return bad("empty message");
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip_and_refusals() {
        let payload = Payload::text("hello", 1);
        assert_eq!(Payload::decode(&payload.encode().unwrap()).unwrap(), payload);
        assert!(Payload::decode(br#"{"v":1,"id":"00000000000000000000000000000000","sent":1}"#).is_err(), "an empty message decoded");
        assert!(Payload::decode(br#"{"v":2,"id":"00000000000000000000000000000000","sent":1,"text":"x"}"#).is_err());
        assert!(Payload::decode(br#"{"v":1,"id":"not-hex","sent":1,"text":"x"}"#).is_err());
        let long = Payload::text(&"x".repeat(MAX_TEXT + 1), 1);
        assert!(long.encode().is_err());
    }
}
