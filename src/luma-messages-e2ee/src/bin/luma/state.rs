// SPDX-License-Identifier: MPL-2.0
//! Everything the helper keeps, in one sealed file: the MLS engine snapshot and
//! the helper's own records (conversations, recent messages, what is waiting
//! to go out, what has been processed but not yet acknowledged).
//!
//! One file, replaced by rename after an fsync, so the MLS state and the
//! records that depend on it can never disagree after a crash: a message is
//! acknowledged to the server only after the state that applied it is on disk.

use luma_mls::{AttachmentKey, Engine};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::io::Write;
use std::path::{Path, PathBuf};

pub const STATE_FILE: &str = "state.sealed";
const MAX_MESSAGES: usize = 300;
const MAX_SEEN: usize = 20_000;
const MAX_TOKENS: usize = 5_000;
const MAX_PROCESSED: usize = 5_000;

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Person {
    #[serde(default)]
    pub handle: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub hue: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Conversation {
    pub id: String,
    /// "direct" or "group".
    pub kind: String,
    #[serde(default)]
    pub name: String,
    /// The other account, for a direct conversation.
    #[serde(default)]
    pub peer: String,
    /// Accounts in the conversation, this one included.
    #[serde(default)]
    pub members: Vec<String>,
    /// The MLS groups behind it (a direct conversation can have more than one
    /// when both people start it at once; the lowest active id sends).
    #[serde(default)]
    pub groups: Vec<String>,
    pub created: u64,
    pub updated: u64,
    #[serde(default)]
    pub unread: u32,
    /// It arrived from someone this account has not accepted.
    #[serde(default)]
    pub request: bool,
    #[serde(default)]
    pub request_from: String,
    /// The safety number's QR text when the person verified it.
    #[serde(default)]
    pub verified: String,
    /// The other side's devices changed since it was verified.
    #[serde(default)]
    pub safety_changed: bool,
    /// Devices changed since the person last looked (unverified conversations).
    #[serde(default)]
    pub devices_changed: bool,
    /// This device is no longer in the conversation.
    #[serde(default)]
    pub removed: bool,
    #[serde(default)]
    pub hidden: bool,
    #[serde(default)]
    pub preview: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StoredAttachment {
    pub part: String,
    pub blob: String,
    pub key: AttachmentKey,
    pub mime: String,
    #[serde(default)]
    pub name: String,
    /// pending, downloading, done or failed.
    pub state: String,
    #[serde(default)]
    pub path: String,
    #[serde(default)]
    pub error: String,
    #[serde(default)]
    pub retryable: bool,
    #[serde(default)]
    pub attempts: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StoredMessage {
    pub id: String,
    #[serde(default)]
    pub sender: String,
    pub outgoing: bool,
    #[serde(default)]
    pub client_id: String,
    #[serde(default)]
    pub text: String,
    /// Unix seconds.
    pub time: u64,
    pub state: String,
    #[serde(default)]
    pub reply_to: String,
    #[serde(default)]
    pub attachments: Vec<StoredAttachment>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Outbound {
    /// "application" or "welcome".
    pub kind: String,
    pub group: String,
    pub epoch: u64,
    pub ciphertext: String,
    #[serde(default)]
    pub recipients: Vec<String>,
    #[serde(default)]
    pub conversation: String,
    #[serde(default)]
    pub message: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct GroupInfo {
    pub conversation: String,
    #[serde(default)]
    pub last_update: u64,
    #[serde(default)]
    pub sent_since_update: u32,
    /// Waiting for the creator's first message to learn what it is.
    #[serde(default)]
    pub awaiting_meta: bool,
    #[serde(default)]
    pub inviter: String,
    #[serde(default)]
    pub folder: String,
    #[serde(default)]
    pub joined: u64,
    /// Devices seen registered but not in the group, and since when (ms).
    #[serde(default)]
    pub missing_since: BTreeMap<String, u64>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct AppState {
    /// Origin that registered this sealed device; never reuse it on another Hub.
    #[serde(default)]
    pub hub: String,
    #[serde(default)]
    pub account: String,
    #[serde(default)]
    pub device: String,
    #[serde(default)]
    pub own: Person,
    #[serde(default)]
    pub conversations: BTreeMap<String, Conversation>,
    #[serde(default)]
    pub messages: BTreeMap<String, Vec<StoredMessage>>,
    #[serde(default)]
    pub groups: BTreeMap<String, GroupInfo>,
    #[serde(default)]
    pub seen: VecDeque<String>,
    #[serde(default)]
    pub processed: VecDeque<u64>,
    #[serde(default)]
    pub tokens: VecDeque<String>,
    #[serde(default)]
    pub outbox: Vec<Outbound>,
    /// Welcomes that go out once their commit is confirmed: group -> outbound.
    #[serde(default)]
    pub pending_welcomes: BTreeMap<String, Outbound>,
    /// (identity, signature key b64u) the delivery service lists as revoked.
    #[serde(default)]
    pub revoked: BTreeSet<(String, String)>,
    /// Accounts the delivery service no longer knows (deleted): every leaf of
    /// theirs is treated as revoked.
    #[serde(default)]
    pub deleted: BTreeSet<String>,
    #[serde(default)]
    pub people: BTreeMap<String, Person>,
    #[serde(default)]
    pub blocked: BTreeSet<String>,
    /// account -> ms of the last device-list read.
    #[serde(default)]
    pub devices_checked: BTreeMap<String, u64>,
    /// account -> sorted signature keys last seen, for change notices.
    #[serde(default)]
    pub device_keys: BTreeMap<String, Vec<String>>,
}

impl AppState {
    pub fn seen(&self, id: &str) -> bool {
        self.seen.iter().any(|s| s == id)
    }

    pub fn remember(&mut self, id: &str) {
        self.seen.push_back(id.to_owned());
        while self.seen.len() > MAX_SEEN {
            self.seen.pop_front();
        }
    }

    pub fn token_used(&self, token: &str) -> bool {
        self.tokens.iter().any(|t| t == token)
    }

    pub fn use_token(&mut self, token: &str) {
        self.tokens.push_back(token.to_owned());
        while self.tokens.len() > MAX_TOKENS {
            self.tokens.pop_front();
        }
    }

    pub fn was_processed(&self, seq: u64) -> bool {
        self.processed.contains(&seq)
    }

    pub fn mark_processed(&mut self, seq: u64) {
        self.processed.push_back(seq);
        while self.processed.len() > MAX_PROCESSED {
            self.processed.pop_front();
        }
    }

    pub fn add_message(&mut self, conversation: &str, message: StoredMessage) {
        let list = self.messages.entry(conversation.to_owned()).or_default();
        if let Some(existing) = list.iter_mut().find(|m| m.id == message.id) {
            *existing = message;
        } else {
            list.push(message);
            list.sort_by_key(|m| m.time);
            let excess = list.len().saturating_sub(MAX_MESSAGES);
            list.drain(..excess);
        }
    }

    pub fn message_mut(&mut self, conversation: &str, id: &str) -> Option<&mut StoredMessage> {
        self.messages.get_mut(conversation)?.iter_mut().find(|m| m.id == id)
    }

    pub fn name_of(&self, account: &str) -> String {
        match if account == self.account { Some(&self.own) } else { self.people.get(account) } {
            Some(p) if !p.name.trim().is_empty() && !p.name.contains('@') => p.name.clone(),
            Some(p) if !p.handle.is_empty() => format!("@{}", p.handle),
            _ => String::new(),
        }
    }
}

#[derive(Serialize, Deserialize)]
struct Sealed {
    engine: String,
    app: AppState,
}

pub fn state_path(data_dir: &Path) -> PathBuf {
    data_dir.join(STATE_FILE)
}

pub fn load(data_dir: &Path, key: &[u8]) -> Result<Option<(Engine, AppState)>, String> {
    let path = state_path(data_dir);
    let bytes = match std::fs::read(&path) {
        Ok(bytes) => bytes,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(format!("state: {}", e.kind())),
    };
    let plain = zeroize::Zeroizing::new(luma_mls::open_sealed(key, &bytes).map_err(|e| e.code().to_string())?);
    let sealed: Sealed = serde_json::from_slice(&plain).map_err(|_| "state is not readable".to_string())?;
    let engine = Engine::from_snapshot(&luma_mls::from_b64u(&sealed.engine).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    Ok(Some((engine, sealed.app)))
}

/// Write the state durably: a sealed temporary file, fsync, rename over the
/// old one, fsync the directory.
pub fn save(data_dir: &Path, key: &[u8], engine: &Engine, app: &AppState) -> Result<(), String> {
    let sealed = Sealed { engine: luma_mls::b64u(&engine.snapshot()), app: app.clone() };
    let plain = zeroize::Zeroizing::new(serde_json::to_vec(&sealed).map_err(|_| "state".to_string())?);
    let bytes = luma_mls::seal(key, &plain).map_err(|e| e.to_string())?;
    let path = state_path(data_dir);
    let temporary = data_dir.join(format!(".{STATE_FILE}.{}", std::process::id()));
    {
        use std::os::unix::fs::OpenOptionsExt;
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o600)
            .open(&temporary)
            .map_err(|e| format!("state: {}", e.kind()))?;
        file.write_all(&bytes).map_err(|e| format!("state: {}", e.kind()))?;
        file.sync_all().map_err(|e| format!("state: {}", e.kind()))?;
    }
    std::fs::rename(&temporary, &path).map_err(|e| format!("state: {}", e.kind()))?;
    if let Ok(dir) = std::fs::File::open(data_dir) {
        let _ = dir.sync_all();
    }
    Ok(())
}

#[cfg(test)]
mod naming_tests {
    use super::*;
    #[test]
    fn own_device_name_does_not_depend_on_a_peer_directory_lookup() {
        let mut app = AppState::default();
        app.account = "owned-account".into();
        app.own = Person { name: "Alice Example".into(), handle: "alice".into(), hue: 0 };
        assert_eq!(app.name_of("owned-account"), "Alice Example");
        app.own.name = "hello@example.test".into();
        assert_eq!(app.name_of("owned-account"), "@alice");
        app.people.insert("other-account".into(), Person { name: "Bob Example".into(), handle: "bob".into(), hue: 0 });
        assert_eq!(app.name_of("other-account"), "Bob Example");
        assert_eq!(app.name_of("unknown-account"), "");
    }
}
