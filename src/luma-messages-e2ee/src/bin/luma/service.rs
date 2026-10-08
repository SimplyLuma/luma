// SPDX-License-Identifier: MPL-2.0
//! The Luma Messages account: registration with the delivery service, key
//! packages, conversations as MLS groups, sending, the receive loop, requests,
//! blocks, reports and safety numbers.
//!
//! Locks, always taken in this order when more than one is needed:
//! `ops` (serialises this device's commits and sends, so a send never lands
//! between a commit and its confirmation), then `inner` (the engine and the
//! records, held only for in-memory work and the state write, never across a
//! network call).

use crate::hub::{self, Hub, HubError};
use crate::state::{self, AppState, Conversation, GroupInfo, Outbound, Person, StoredAttachment, StoredMessage};
use crate::{log, BridgeError, Output, PROTOCOL};
use luma_mls::{b64u, from_b64u, ConversationKind, ConversationMeta, Engine, Error as MlsError, Identity, Incoming, Member, Payload, PendingDevice};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet, HashSet, VecDeque};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

type R<T> = Result<T, BridgeError>;

const KEY_PACKAGE_TARGET: usize = 100;
const KEY_PACKAGE_LOW: u64 = 20;
const UPDATE_EVERY_MS: u64 = 24 * 3600 * 1000;
const UPDATE_AFTER_SENT: u32 = 200;
const DEVICES_EVERY_MS: u64 = 30 * 60 * 1000;
const MISSING_GRACE_MS: u64 = 10 * 60 * 1000;
const BURST_LIMIT: usize = 12;
const BURST_WINDOW: Duration = Duration::from_secs(60);
const MAX_MEDIA: u64 = 25 * 1024 * 1024;
const MESSAGES_V1: &str = "/api/messages/v1";

fn validate_registration(answer: &Value, identity: &Identity, signature: Option<&str>) -> Result<(), HubError> {
    let credential = identity.to_credential();
    if answer.get("account").and_then(Value::as_str) != Some(identity.account.as_str())
        || answer.get("device").and_then(Value::as_str) != Some(identity.device.as_str())
        || answer.get("identity").and_then(Value::as_str) != Some(credential.as_str())
        || signature.is_some_and(|key| answer.get("signature_key").and_then(Value::as_str) != Some(key)) {
        return Err(HubError { status: 502, code: "identity_mismatch".into(), message: "The delivery service returned another device identity.".into(), retry_after: 0 });
    }
    Ok(())
}

fn validate_enrolment_binding(app: &AppState, identity: &Identity, enrolment: &hub::Enrolment) -> Result<(), ()> {
    if app.account != identity.account || app.device != identity.device
        || app.device != enrolment.device || (!app.hub.is_empty() && app.hub != enrolment.hub) {
        return Err(());
    }
    Ok(())
}

fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
}

fn now_s() -> u64 {
    now_ms() / 1000
}

struct Inner {
    key: Option<zeroize::Zeroizing<Vec<u8>>>,
    engine: Option<Engine>,
    app: AppState,
}

struct Shared {
    data_dir: PathBuf,
    out: Output,
    inner: Mutex<Inner>,
    ops: Mutex<()>,
    hub: Mutex<Option<Hub>>,
    status: Mutex<(String, String)>,
    stop: AtomicBool,
    generation: AtomicU64,
    connecting: AtomicBool,
    burst: Mutex<VecDeque<Instant>>,
    downloads: Mutex<HashSet<String>>,
}

#[derive(Clone)]
pub struct Service(Arc<Shared>);

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|p| p.into_inner())
}

fn invalid(message: &str) -> BridgeError {
    BridgeError::new("invalid", message)
}

fn from_hub(e: &HubError) -> BridgeError {
    let code = e.bridge_code();
    let message = if e.message.is_empty() { code.to_owned() } else { e.message.clone() };
    BridgeError { code: code.into(), message, retryable: matches!(code, "network" | "server" | "rate_limited") }
}

fn from_mls(e: &MlsError) -> BridgeError {
    BridgeError::new(e.code(), &e.to_string())
}

fn arg<'a>(args: &'a Value, name: &str) -> &'a str {
    args.get(name).and_then(Value::as_str).unwrap_or("")
}

fn is_hex32(s: &str) -> bool {
    s.len() == 32 && s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn clean_handle(value: &str) -> Option<String> {
    let text = value.trim().trim_start_matches('@');
    let ok = (1..=40).contains(&text.len()) && text.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'_');
    ok.then(|| text.to_owned())
}

fn account_ok(value: &str) -> bool {
    (8..=200).contains(&value.len()) && value.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

fn extension(mime: &str) -> &'static str {
    match mime {
        "image/jpeg" => "jpg",
        "image/png" => "png",
        "image/gif" => "gif",
        "image/webp" => "webp",
        "image/heic" => "heic",
        "video/mp4" => "mp4",
        _ => "bin",
    }
}

fn keys_by_account(members: &[Member]) -> BTreeMap<String, Vec<Vec<u8>>> {
    let mut map: BTreeMap<String, Vec<Vec<u8>>> = BTreeMap::new();
    for m in members {
        map.entry(m.identity.account.clone()).or_default().push(m.signature_key.clone());
    }
    for keys in map.values_mut() {
        keys.sort();
        keys.dedup();
    }
    map
}

impl Service {
    pub fn new(data_dir: PathBuf, out: Output) -> Self {
        Service(Arc::new(Shared {
            data_dir,
            out,
            inner: Mutex::new(Inner { key: None, engine: None, app: AppState::default() }),
            ops: Mutex::new(()),
            hub: Mutex::new(None),
            status: Mutex::new(("connecting".into(), String::new())),
            stop: AtomicBool::new(false),
            generation: AtomicU64::new(0),
            connecting: AtomicBool::new(false),
            burst: Mutex::new(VecDeque::new()),
            downloads: Mutex::new(HashSet::new()),
        }))
    }

    pub fn shutdown(&self) {
        self.0.stop.store(true, Ordering::SeqCst);
        self.0.generation.fetch_add(1, Ordering::SeqCst);
    }

    fn emit(&self, name: &str, data: Value) {
        self.0.out.event(name, data);
    }

    fn set_status(&self, state: &str, detail: &str) {
        let changed = {
            let mut status = lock(&self.0.status);
            let changed = status.0 != state || status.1 != detail;
            *status = (state.to_owned(), detail.to_owned());
            changed
        };
        if changed {
            let mut data = json!({"state": state});
            if !detail.is_empty() {
                data["detail"] = json!(detail);
            }
            self.emit("status", data);
        }
    }

    fn hub(&self) -> R<Hub> {
        lock(&self.0.hub).clone().ok_or_else(|| BridgeError::retry("not_connected", "Luma Messages isn't connected."))
    }

    fn persist(&self, inner: &Inner) -> R<()> {
        let (Some(key), Some(engine)) = (&inner.key, &inner.engine) else {
            return Err(BridgeError::new("not_connected", "no state to save"));
        };
        state::save(&self.0.data_dir, key, engine, &inner.app).map_err(|e| {
            log("error", &format!("state could not be saved: {e}"));
            BridgeError::retry("io", "Luma Messages couldn't save its state.")
        })
    }

    // ── Commands ────────────────────────────────────────────────────────

    pub fn command(&self, name: &str, args: &Value) -> R<Value> {
        match name {
            "hello" => Ok(self.hello(args)),
            "session.load" => self.session_load(args),
            "connect" => {
                self.start_connect();
                Ok(json!({}))
            }
            "status" => Ok(self.status_value()),
            "login.start" if arg(args, "method") == "reset" => self.reset(),
            "login.cancel" => Ok(json!({})),
            "conversations.list" => self.conversations_list(args),
            "messages.list" => self.messages_list(args),
            "message.send" => self.message_send(args),
            "media.send" => self.media_send(args),
            "media.fetch" => self.media_fetch(args),
            "message.read" => self.message_read(args),
            "conversation.create" => self.conversation_create(args),
            "logout" => self.logout(args),
            "luma.identity" => self.identity(),
            "luma.handle.check" => self.handle_check(args),
            "luma.handle.claim" => self.handle_claim(args),
            "luma.people" => self.people(args),
            "luma.requests" => self.requests(),
            "luma.request.answer" => self.request_answer(args),
            "luma.block" => self.block(args),
            "luma.blocks" => self.blocks(),
            "luma.report" => self.report(args),
            "luma.safety" => self.safety(args),
            "luma.verify" => self.verify(args),
            "luma.devices" => self.devices(args),
            "luma.sync" => self.sync_now(),
            _ => Err(BridgeError::new("unsupported", "Luma Messages doesn't do that.")),
        }
    }

    fn hello(&self, args: &Value) -> Value {
        if arg(args, "protocol") != PROTOCOL {
            log("warn", "Messages asked for another protocol version");
        }
        json!({
            "protocol": PROTOCOL,
            "network": "luma",
            "helper_version": env!("CARGO_PKG_VERSION"),
            "capabilities": {
                "login": [],
                "media": true,
                "max_media_bytes": MAX_MEDIA,
                "reactions": false,
                "replies": true,
                "typing": false,
                "read_receipts": false,
                "groups": true,
                "create_conversations": true,
                "unofficial": false,
                "media_fetch": true,
                "encrypted": true,
                "luma": 1
            }
        })
    }

    fn session_load(&self, args: &Value) -> R<Value> {
        use base64::Engine as _;
        let text = arg(args, "session");
        let key = base64::engine::general_purpose::STANDARD
            .decode(text)
            .or_else(|_| from_b64u(text).map_err(|_| ()))
            .map_err(|_| invalid("The stored key isn't readable."))?;
        if key.len() != luma_mls::sealed::KEY_BYTES {
            return Err(invalid("The stored key isn't readable."));
        }
        lock(&self.0.inner).key = Some(zeroize::Zeroizing::new(key));
        Ok(json!({}))
    }

    fn status_value(&self) -> Value {
        let (state, detail) = lock(&self.0.status).clone();
        let inner = lock(&self.0.inner);
        let mut value = json!({"state": state});
        if !detail.is_empty() {
            value["detail"] = json!(detail);
        }
        if !inner.app.account.is_empty() {
            let handle = if inner.app.own.handle.is_empty() { String::new() } else { format!("@{}", inner.app.own.handle) };
            value["account"] = json!({"name": inner.app.own.name, "handle": handle, "id": inner.app.account});
        }
        value
    }

    // ── Connecting ──────────────────────────────────────────────────────

    fn start_connect(&self) {
        if self.0.connecting.swap(true, Ordering::SeqCst) {
            return;
        }
        let service = self.clone();
        std::thread::spawn(move || {
            let generation = service.0.generation.fetch_add(1, Ordering::SeqCst) + 1;
            let result = service.connect(generation);
            service.0.connecting.store(false, Ordering::SeqCst);
            match result {
                Ok(()) => {
                    let (a, b) = (service.clone(), service.clone());
                    std::thread::spawn(move || a.receive_loop(generation));
                    std::thread::spawn(move || b.maintenance_loop(generation));
                }
                Err((state, detail)) => service.set_status(&state, &detail),
            }
        });
    }

    fn connect(&self, generation: u64) -> Result<(), (String, String)> {
        self.set_status("connecting", "");
        let fail = |s: &str, d: &str| (s.to_owned(), d.to_owned());
        let enrolment = match hub::load_enrolment(&hub::device_file()) {
            Ok(Some(e)) => e,
            Ok(None) => return Err(fail("needs_login", "Sign in to Luma Connect to use Luma Messages.")),
            Err(e) => {
                log("error", &e);
                return Err(fail("error", "Luma Connect's sign-in on this device can't be read."));
            }
        };
        let hub = Hub::new(&enrolment);
        {
            let mut inner = lock(&self.0.inner);
            let Some(key) = inner.key.clone() else {
                return Err(fail("needs_login", "keyring"));
            };
            if inner.engine.is_none() {
                match state::load(&self.0.data_dir, &key) {
                    Ok(Some((engine, app))) if app.device == enrolment.device => {
                        inner.engine = Some(engine);
                        inner.app = app;
                    }
                    Ok(Some(_)) => {
                        // This computer was enrolled again as a new Hub device.
                        log("warn", "state belongs to a previous enrolment; starting this device again");
                        let _ = std::fs::rename(state::state_path(&self.0.data_dir), self.0.data_dir.join("state.sealed.previous"));
                    }
                    Ok(None) => {}
                    Err(e) => {
                        log("error", &format!("state could not be opened: {e}"));
                        return Err(fail("needs_login", "state"));
                    }
                }
            }
        }
        {
            let inner = lock(&self.0.inner);
            if let Some(engine) = &inner.engine {
                if validate_enrolment_binding(&inner.app, engine.identity(), &enrolment).is_err() {
                    return Err(fail("needs_login", "The Luma Messages device does not match this sign-in."));
                }
            }
        }
        let registered = lock(&self.0.inner).engine.is_some();
        if registered {
            self.ensure_registered(&hub).map_err(|e| self.connect_failure(&e))?;
        } else {
            self.register_new(&hub, &enrolment.device).map_err(|e| self.connect_failure(&e))?;
        }
        {
            let mut inner = lock(&self.0.inner);
            inner.app.hub = enrolment.hub.clone();
            self.persist(&inner).map_err(|_| fail("error", "state"))?;
        }
        *lock(&self.0.hub) = Some(hub.clone());
        if let Err(e) = self.refresh_own(&hub) {
            log("info", &format!("own profile not read: {e}"));
        }
        if let Err(e) = self.top_up_key_packages(&hub) {
            log("warn", &format!("key packages not topped up: {}", e.code));
        }
        if generation != self.0.generation.load(Ordering::SeqCst) {
            return Err(fail("connecting", ""));
        }
        self.recover_pending_commits();
        self.flush_outbox();
        self.set_status("connected", "");
        Ok(())
    }

    fn connect_failure(&self, e: &HubError) -> (String, String) {
        match e.status {
            401 => ("needs_login".into(), "Sign in to Luma Connect again to use Luma Messages.".into()),
            0 => ("connecting".into(), "network".into()),
            _ => ("error".into(), e.code.clone()),
        }
    }

    fn register_new(&self, hub: &Hub, device: &str) -> Result<(), HubError> {
        let bad = |what: &str| HubError { status: 502, code: "server".into(), message: what.into(), retry_after: 0 };
        let pending = PendingDevice::new().map_err(|_| bad("no signing key"))?;
        let answer = hub.post(&format!("{MESSAGES_V1}/devices"), &json!({"signature_key": b64u(&pending.signature_key())}))?;
        let account = answer.get("account").and_then(Value::as_str).unwrap_or("").to_owned();
        let registered_device = answer.get("device").and_then(Value::as_str).unwrap_or("").to_owned();
        if registered_device != device {
            return Err(bad("the delivery service registered another device"));
        }
        let identity = Identity::new(&account, &registered_device).map_err(|_| bad("unusable account id"))?;
        validate_registration(&answer, &identity, None)?;
        let engine = pending.into_engine(&account, &registered_device).map_err(|_| bad("unusable account id"))?;
        let mut inner = lock(&self.0.inner);
        inner.app = AppState { account, device: registered_device, ..AppState::default() };
        inner.engine = Some(engine);
        self.persist(&inner).map_err(|_| bad("state could not be saved"))?;
        log("info", "registered this device with Luma Messages");
        Ok(())
    }

    fn ensure_registered(&self, hub: &Hub) -> Result<(), HubError> {
        let (ours, identity) = {
            let inner = lock(&self.0.inner);
            let engine = inner.engine.as_ref().expect("engine");
            (b64u(&engine.signature_key()), engine.identity().clone())
        };
        match hub.get(&format!("{MESSAGES_V1}/devices/self")) {
            Ok(v) => {
                // A key replacement may be repaired only for the same identity.
                validate_registration(&v, &identity, None)?;
                if v.get("signature_key").and_then(Value::as_str) == Some(ours.as_str()) {
                    Ok(())
                } else {
                    let answer = hub.post(&format!("{MESSAGES_V1}/devices"), &json!({"signature_key": ours}))?;
                    validate_registration(&answer, &identity, None)
                }
            }
            Err(e) if e.status == 403 && e.code == "not_registered" => {
                let answer = hub.post(&format!("{MESSAGES_V1}/devices"), &json!({"signature_key": ours}))?;
                validate_registration(&answer, &identity, None)
            }
            Err(e) => Err(e),
        }
    }

    fn refresh_own(&self, hub: &Hub) -> Result<(), HubError> {
        let handle = hub.get("/api/hub/sync/identity/handle")?;
        let account = hub.get("/api/hub/sync/account").unwrap_or(Value::Null);
        let name = account
            .pointer("/profile/display_name")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .or_else(|| account.pointer("/account/name").and_then(Value::as_str))
            .unwrap_or("")
            .to_owned();
        let mut inner = lock(&self.0.inner);
        inner.app.own.handle = handle.get("handle").and_then(Value::as_str).unwrap_or("").to_owned();
        if !name.is_empty() {
            inner.app.own.name = name;
        }
        let _ = self.persist(&inner);
        Ok(())
    }

    fn top_up_key_packages(&self, hub: &Hub) -> R<()> {
        let counts = hub.get(&format!("{MESSAGES_V1}/key-packages")).map_err(|e| from_hub(&e))?;
        let available = counts.get("available").and_then(Value::as_u64).unwrap_or(0);
        let has_last_resort = counts.get("last_resort").and_then(Value::as_bool).unwrap_or(false);
        if available >= KEY_PACKAGE_LOW && has_last_resort {
            return Ok(());
        }
        let (packages, last_resort) = {
            let inner = lock(&self.0.inner);
            let engine = inner.engine.as_ref().ok_or_else(|| invalid("no device"))?;
            let want = if available < KEY_PACKAGE_LOW { KEY_PACKAGE_TARGET.saturating_sub(available as usize) } else { 0 };
            let packages = engine.key_packages(want).map_err(|e| from_mls(&e))?;
            let last = if has_last_resort { None } else { Some(engine.last_resort_key_package().map_err(|e| from_mls(&e))?) };
            // The private halves live in the engine: save before they are handed out.
            self.persist(&inner)?;
            (packages, last)
        };
        let mut body = json!({});
        if !packages.is_empty() {
            body["key_packages"] = json!(packages.iter().map(|p| b64u(p)).collect::<Vec<_>>());
        }
        if let Some(last) = last_resort {
            body["last_resort"] = json!(b64u(&last));
        }
        hub.post(&format!("{MESSAGES_V1}/key-packages"), &body).map_err(|e| from_hub(&e))?;
        log("info", &format!("uploaded {} key packages", packages.len()));
        Ok(())
    }

    // ── Receiving ───────────────────────────────────────────────────────

    fn live(&self, generation: u64) -> bool {
        !self.0.stop.load(Ordering::SeqCst) && generation == self.0.generation.load(Ordering::SeqCst)
    }

    fn receive_loop(&self, generation: u64) {
        let mut cursor = 0u64;
        let mut backoff = 1u64;
        while self.live(generation) {
            let Ok(hub) = self.hub() else { return };
            match hub.long_get(&format!("{MESSAGES_V1}/queue?after={cursor}&limit=100&wait=50")) {
                Ok(answer) => {
                    backoff = 1;
                    if !self.live(generation) {
                        return;
                    }
                    self.set_status("connected", "");
                    let items = answer.get("items").and_then(Value::as_array).cloned().unwrap_or_default();
                    let acks = self.process_items(&items);
                    if let Some(last) = items.iter().filter_map(|i| i.get("seq").and_then(Value::as_u64)).max() {
                        cursor = cursor.max(last);
                    }
                    self.ack(&hub, &acks);
                }
                Err(e) if e.status == 401 => {
                    self.set_status("needs_login", "Sign in to Luma Connect again to use Luma Messages.");
                    return;
                }
                Err(e) if e.status == 403 => {
                    log("warn", "the delivery service no longer knows this device; registering again");
                    if self.ensure_registered(&hub).is_err() {
                        self.set_status("error", "not_registered");
                        std::thread::sleep(Duration::from_secs(30));
                    }
                }
                Err(e) => {
                    if e.status == 0 {
                        self.set_status("connecting", "network");
                    }
                    let wait = if e.retry_after > 0 { e.retry_after } else { backoff };
                    std::thread::sleep(Duration::from_secs(wait.min(120)));
                    backoff = (backoff * 2).min(60);
                }
            }
        }
    }

    /// Fetch what is waiting now, without holding a long poll (after a
    /// refused commit, before retrying it).
    fn drain_now(&self) {
        let Ok(hub) = self.hub() else { return };
        for _ in 0..10 {
            match hub.get(&format!("{MESSAGES_V1}/queue?after=0&limit=200&wait=0")) {
                Ok(answer) => {
                    let items = answer.get("items").and_then(Value::as_array).cloned().unwrap_or_default();
                    let acks = self.process_items(&items);
                    self.ack(&hub, &acks);
                    if !answer.get("more").and_then(Value::as_bool).unwrap_or(false) {
                        return;
                    }
                }
                Err(_) => return,
            }
        }
    }

    fn ack(&self, hub: &Hub, seqs: &[u64]) {
        for chunk in seqs.chunks(500) {
            if let Err(e) = hub.post(&format!("{MESSAGES_V1}/queue/ack"), &json!({"seqs": chunk})) {
                log("warn", &format!("acknowledgement failed: {e}"));
            }
        }
    }

    /// Apply each item, save, then say so; returns the sequence numbers that
    /// may be acknowledged (every one that was applied or can never be).
    fn process_items(&self, items: &[Value]) -> Vec<u64> {
        let mut acks = Vec::new();
        let mut downloads = Vec::new();
        let mut handles = Vec::new();
        for item in items {
            let Some(seq) = item.get("seq").and_then(Value::as_u64) else { continue };
            let mut inner = lock(&self.0.inner);
            if inner.engine.is_none() {
                return acks;
            }
            if inner.app.was_processed(seq) {
                acks.push(seq);
                continue;
            }
            let mut effects = Effects::default();
            self.apply(&mut inner, item, &mut effects);
            inner.app.mark_processed(seq);
            if self.persist(&inner).is_err() {
                // Not acknowledged: the server keeps it and it comes again.
                return acks;
            }
            drop(inner);
            for (name, data) in effects.events {
                self.emit(&name, data);
            }
            downloads.extend(effects.downloads);
            handles.extend(effects.handles);
            acks.push(seq);
        }
        for (conversation, message) in downloads {
            self.schedule_download(&conversation, &message);
        }
        for (account, handle) in handles {
            self.confirm_handle(&account, &handle);
        }
        acks
    }

    fn apply(&self, inner: &mut Inner, item: &Value, fx: &mut Effects) {
        let kind = item.get("kind").and_then(Value::as_str).unwrap_or("");
        let folder = item.get("folder").and_then(Value::as_str).unwrap_or("inbox").to_owned();
        let Ok(bytes) = from_b64u(item.get("ciphertext").and_then(Value::as_str).unwrap_or("")) else {
            log("warn", "a queued item was not base64url");
            return;
        };
        let Inner { engine, app, .. } = inner;
        let engine = engine.as_mut().expect("engine");
        match kind {
            "welcome" => match engine.join(&bytes) {
                Ok(joined) => {
                    let gid = b64u(&joined.group);
                    let inviter = joined.inviter.identity.account.clone();
                    if revoked(app, &joined.inviter) || app.blocked.contains(&inviter) {
                        let _ = engine.forget(&joined.group);
                        return;
                    }
                    let info = app.groups.entry(gid.clone()).or_default();
                    info.awaiting_meta = info.conversation.is_empty();
                    info.inviter = inviter;
                    info.folder = folder;
                    info.joined = now_ms();
                    info.last_update = now_ms();
                    log("info", "joined a conversation");
                }
                Err(MlsError::NotMember) => log("info", "a welcome for another device was ignored"),
                Err(e) => log("warn", &format!("welcome not applied: {}", e.code())),
            },
            "application" | "commit" => {
                let accept = |m: &Member| !revoked(app, m) && (m.identity.account == app.account || !app.blocked.contains(&m.identity.account));
                match engine.decrypt(&bytes, &accept) {
                    Ok(Incoming::Application { group, sender, plaintext, .. }) => {
                        self.on_payload(engine, app, &b64u(&group), &sender, &plaintext, &folder, fx);
                    }
                    Ok(Incoming::Commit { group, removed_self, added, removed, .. }) => {
                        self.on_commit(engine, app, &b64u(&group), removed_self, !added.is_empty() || !removed.is_empty(), fx);
                    }
                    Ok(Incoming::Proposal { .. }) => {}
                    Err(e) => log("info", &format!("a queued {kind} was not applied: {}", e.code())),
                }
            }
            _ => log("warn", "a queued item of an unknown kind"),
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn on_payload(&self, engine: &mut Engine, app: &mut AppState, gid: &str, sender: &Member, plaintext: &[u8], folder: &str, fx: &mut Effects) {
        let payload = match Payload::decode(plaintext) {
            Ok(p) => p,
            Err(e) => {
                log("info", &format!("a message was not readable: {}", e.code()));
                return;
            }
        };
        if app.seen(&payload.id) {
            return;
        }
        app.remember(&payload.id);
        let raw = from_b64u(gid).unwrap_or_default();
        let members = engine.members(&raw).unwrap_or_default();
        let accounts: BTreeSet<String> = members.iter().map(|m| m.identity.account.clone()).collect();
        let outgoing = sender.identity.account == app.account;
        let info = app.groups.entry(gid.to_owned()).or_default();
        let conversation_id = if !info.conversation.is_empty() {
            info.conversation.clone()
        } else {
            let direct = match &payload.conversation {
                Some(meta) => meta.kind == ConversationKind::Direct,
                None => accounts.len() == 2,
            };
            let peer = accounts.iter().find(|a| **a != app.account).cloned().unwrap_or_else(|| if direct { app.account.clone() } else { String::new() });
            let id = if direct && !peer.is_empty() { format!("u:{peer}") } else { format!("g:{gid}") };
            info.conversation = id.clone();
            id
        };
        info.awaiting_meta = false;
        let (inviter, joined_folder) = (info.inviter.clone(), info.folder.clone());
        let is_new = !app.conversations.contains_key(&conversation_id);
        let conversation = app.conversations.entry(conversation_id.clone()).or_insert_with(|| {
            let direct = conversation_id.starts_with("u:");
            Conversation {
                id: conversation_id.clone(),
                kind: if direct { "direct".into() } else { "group".into() },
                name: String::new(),
                peer: if direct { conversation_id[2..].to_owned() } else { String::new() },
                members: vec![],
                groups: vec![],
                created: now_s(),
                updated: now_s(),
                unread: 0,
                request: false,
                request_from: String::new(),
                verified: String::new(),
                safety_changed: false,
                devices_changed: false,
                removed: false,
                hidden: false,
                preview: String::new(),
            }
        });
        if is_new {
            let stranger = if !inviter.is_empty() && inviter != app.account { inviter.clone() } else { sender.identity.account.clone() };
            if (folder == "requests" || joined_folder == "requests") && stranger != app.account {
                conversation.request = true;
                conversation.request_from = stranger;
            }
        }
        if !conversation.groups.iter().any(|g| g == gid) {
            conversation.groups.push(gid.to_owned());
            conversation.groups.sort();
        }
        conversation.members = accounts.iter().cloned().collect();
        conversation.removed = false;
        conversation.hidden = false;
        if let Some(meta) = &payload.conversation {
            if conversation.kind == "group" && !meta.name.is_empty() {
                conversation.name = meta.name.clone();
            }
        }
        if !outgoing && !payload.handle.is_empty() {
            let known = app.people.get(&sender.identity.account).map(|p| p.handle.eq_ignore_ascii_case(&payload.handle)).unwrap_or(false);
            if !known {
                fx.handles.push((sender.identity.account.clone(), payload.handle.clone()));
            }
        }
        note_device_keys(app, &members, fx);
        let conversation = app.conversations.get_mut(&conversation_id).expect("conversation");
        if payload.text.is_empty() && payload.attachments.is_empty() {
            fx.events.push(("conversation".into(), conversation_json(app, &conversation_id)));
            return;
        }
        let mut time = payload.sent / 1000;
        if time == 0 || time > now_s() + 86_400 {
            time = now_s();
        }
        let attachments: Vec<StoredAttachment> = payload
            .attachments
            .iter()
            .enumerate()
            .map(|(i, a)| StoredAttachment {
                part: i.to_string(),
                blob: a.blob.clone(),
                key: a.key.clone(),
                mime: a.mime.clone(),
                name: a.name.clone(),
                state: "pending".into(),
                path: String::new(),
                error: String::new(),
                retryable: true,
                attempts: 0,
            })
            .collect();
        conversation.updated = conversation.updated.max(time);
        conversation.preview = preview(&payload.text, attachments.first().map(|a| a.mime.as_str()));
        if !outgoing {
            conversation.unread += 1;
        }
        let message = StoredMessage {
            id: payload.id.clone(),
            sender: sender.identity.account.clone(),
            outgoing,
            client_id: String::new(),
            text: payload.text.clone(),
            time,
            state: if outgoing { "sent".into() } else { "received".into() },
            reply_to: payload.reply_to.clone().unwrap_or_default(),
            attachments,
        };
        let has_media = !message.attachments.is_empty();
        app.add_message(&conversation_id, message);
        fx.events.push(("conversation".into(), conversation_json(app, &conversation_id)));
        fx.events.push(("message".into(), message_json(app, &conversation_id, &payload.id, &self.0.data_dir)));
        if has_media {
            fx.downloads.push((conversation_id, payload.id));
        }
    }

    fn on_commit(&self, engine: &mut Engine, app: &mut AppState, gid: &str, removed_self: bool, membership: bool, fx: &mut Effects) {
        let raw = from_b64u(gid).unwrap_or_default();
        let conversation_id = app.groups.get(gid).map(|g| g.conversation.clone()).unwrap_or_default();
        if removed_self {
            let _ = engine.forget(&raw);
            app.groups.remove(gid);
            if let Some(c) = app.conversations.get_mut(&conversation_id) {
                c.groups.retain(|g| g != gid);
                if c.groups.is_empty() {
                    c.removed = true;
                }
                fx.events.push(("conversation".into(), conversation_json(app, &conversation_id)));
            }
            log("info", "this device was removed from a conversation");
            return;
        }
        if let Some(info) = app.groups.get_mut(gid) {
            info.missing_since.clear();
        }
        if !membership {
            return;
        }
        let members = engine.members(&raw).unwrap_or_default();
        note_device_keys(app, &members, fx);
        if let Some(c) = app.conversations.get_mut(&conversation_id) {
            let accounts: BTreeSet<String> = members.iter().map(|m| m.identity.account.clone()).collect();
            c.members = accounts.into_iter().collect();
            fx.events.push(("conversation".into(), conversation_json(app, &conversation_id)));
        }
    }

    fn confirm_handle(&self, account: &str, handle: &str) {
        let Some(handle) = clean_handle(handle) else { return };
        let Ok(hub) = self.hub() else { return };
        let Ok(person) = hub.get(&format!("/api/hub/sync/people/{handle}")) else { return };
        if person.get("account").and_then(Value::as_str) != Some(account) {
            log("warn", "a sender claimed a username that is not theirs");
            return;
        }
        let mut inner = lock(&self.0.inner);
        inner.app.people.insert(account.to_owned(), person_from(&person));
        let ids: Vec<String> = inner.app.conversations.values().filter(|c| c.members.iter().any(|m| m == account)).map(|c| c.id.clone()).collect();
        let _ = self.persist(&inner);
        let events: Vec<Value> = ids.iter().map(|id| conversation_json(&inner.app, id)).collect();
        drop(inner);
        for e in events {
            self.emit("conversation", e);
        }
    }

    // ── Attachments ─────────────────────────────────────────────────────

    fn schedule_download(&self, conversation: &str, message: &str) {
        let key = format!("{conversation}|{message}");
        if !lock(&self.0.downloads).insert(key.clone()) {
            return;
        }
        let service = self.clone();
        let (conversation, message) = (conversation.to_owned(), message.to_owned());
        std::thread::spawn(move || {
            service.download(&conversation, &message);
            lock(&service.0.downloads).remove(&key);
        });
    }

    fn download(&self, conversation: &str, message: &str) {
        let parts: Vec<StoredAttachment> = {
            let mut inner = lock(&self.0.inner);
            let Some(m) = inner.app.message_mut(conversation, message) else { return };
            for a in m.attachments.iter_mut().filter(|a| a.state != "done") {
                a.state = "downloading".into();
            }
            m.attachments.iter().filter(|a| a.state == "downloading").cloned().collect()
        };
        for part in parts {
            self.emit("media", media_json(conversation, message, &part));
            let result = self.hub().map_err(|_| ("not_connected", true)).and_then(|hub| {
                hub.download_blob(&part.blob).map_err(|e| match (e.status, e.code.as_str()) {
                    (404, _) => ("expired", false),
                    (0, _) => ("network", true),
                    (401, _) => ("unauthorized", true),
                    _ => ("server", true),
                })
            });
            let outcome = result.and_then(|blob| luma_mls::open_attachment(&blob, &part.key).map_err(|_| ("decrypt", false))).and_then(|plain| {
                let path = self.0.data_dir.join("media").join(format!("{message}-{}.{}", part.part, extension(&part.mime)));
                write_private(&path, &plain).map(|_| path).map_err(|_| ("io", true))
            });
            let mut inner = lock(&self.0.inner);
            let Some(m) = inner.app.message_mut(conversation, message) else { return };
            let Some(a) = m.attachments.iter_mut().find(|a| a.part == part.part) else { return };
            a.attempts += 1;
            match outcome {
                Ok(path) => {
                    a.state = "done".into();
                    a.path = path.to_string_lossy().into_owned();
                    a.error.clear();
                }
                Err((code, retryable)) => {
                    a.state = "failed".into();
                    a.error = code.into();
                    a.retryable = retryable;
                }
            }
            let data = media_json(conversation, message, a);
            let _ = self.persist(&inner);
            drop(inner);
            self.emit("media", data);
        }
    }

    fn media_fetch(&self, args: &Value) -> R<Value> {
        let (conversation, message) = (arg(args, "conversation"), arg(args, "message"));
        {
            let mut inner = lock(&self.0.inner);
            let Some(m) = inner.app.message_mut(conversation, message) else {
                return Err(BridgeError::new("not_found", "That message isn't here."));
            };
            let part = arg(args, "part");
            for a in m.attachments.iter_mut().filter(|a| a.state != "done" && (part.is_empty() || a.part == part)) {
                a.state = "pending".into();
            }
        }
        self.schedule_download(conversation, message);
        Ok(json!({}))
    }

    // ── Sending ─────────────────────────────────────────────────────────

    fn kill_switch(&self) -> PathBuf {
        self.0.data_dir.parent().unwrap_or(Path::new("/")).join("outbound-disabled")
    }

    /// A delivery needs the person's token, used once; the kill switch and a
    /// burst beyond what a person does stop it (the same rules Messages keeps).
    /// `token` is the person's request; `part` tells apart the attachments one
    /// request sends (Messages sends each with the same token).
    fn guard_outbound(&self, token: &str, part: &str) -> R<()> {
        if std::env::var("LUMA_MESSAGES_OUTBOUND").as_deref() == Ok("off") || self.kill_switch().exists() {
            return Err(BridgeError::new("outbound_disabled", "Sending is turned off."));
        }
        if !is_hex32(token) {
            self.trip("a delivery without a person's token");
            return Err(BridgeError::new("outbound_disabled", "A delivery without a person's request was refused."));
        }
        if lock(&self.0.inner).app.token_used(&ledger_key(token, part)) {
            return Err(BridgeError::new("duplicate", "That request was already sent."));
        }
        let mut burst = lock(&self.0.burst);
        let now = Instant::now();
        while burst.front().map(|t| now.duration_since(*t) > BURST_WINDOW).unwrap_or(false) {
            burst.pop_front();
        }
        if burst.len() >= BURST_LIMIT {
            drop(burst);
            self.trip("more than 12 deliveries in 60 seconds");
            return Err(BridgeError::new("outbound_disabled", "Sending was turned off."));
        }
        burst.push_back(now);
        Ok(())
    }

    fn trip(&self, reason: &str) {
        log("error", &format!("outbound delivery disabled: {reason}"));
        let _ = std::fs::write(self.kill_switch(), format!("{reason}\n"));
        self.emit("outbound_disabled", json!({"reason": reason}));
    }

    fn message_send(&self, args: &Value) -> R<Value> {
        let text = arg(args, "text");
        if text.trim().is_empty() {
            return Err(BridgeError::new("invalid", "There's nothing to send."));
        }
        self.guard_outbound(arg(args, "user_token"), arg(args, "client_id"))?;
        let mut payload = Payload::text(text, now_ms());
        let reply = arg(args, "reply_to");
        if is_hex32(reply) {
            payload.reply_to = Some(reply.to_owned());
        }
        self.send_payload(arg(args, "conversation"), payload, arg(args, "client_id"), arg(args, "user_token"), vec![])
    }

    fn media_send(&self, args: &Value) -> R<Value> {
        self.guard_outbound(arg(args, "user_token"), arg(args, "client_id"))?;
        let path = PathBuf::from(arg(args, "path"));
        let meta = std::fs::metadata(&path).map_err(|_| BridgeError::new("not_attempted", "That file can't be read."))?;
        if meta.len() > MAX_MEDIA {
            return Err(BridgeError::new("too_large", "Photos can be up to 25 MB."));
        }
        let bytes = std::fs::read(&path).map_err(|_| BridgeError::new("not_attempted", "That file can't be read."))?;
        let mime = arg(args, "mime").to_owned();
        let name = std::path::Path::new(arg(args, "name")).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        let sealed = luma_mls::seal_attachment(&bytes).map_err(|e| from_mls(&e))?;
        let hub = self.hub()?;
        let answer = hub.upload_blob(&sealed.blob).map_err(|e| {
            let mut error = from_hub(&e);
            // Nothing reached anyone: the upload is only a blob nobody can find.
            error.code = "not_attempted".into();
            error
        })?;
        let blob = answer.get("blob").and_then(Value::as_str).unwrap_or("").to_owned();
        if blob.is_empty() {
            return Err(BridgeError::new("not_attempted", "The photo couldn't be uploaded."));
        }
        let mut payload = Payload::text(arg(args, "caption"), now_ms());
        payload.attachments.push(luma_mls::Attachment { blob: blob.clone(), key: sealed.key.clone(), mime: mime.clone(), name: name.clone() });
        let own = self.0.data_dir.join("media").join(format!("{}-0.{}", payload.id, extension(&mime)));
        write_private(&own, &bytes).map_err(|_| BridgeError::new("not_attempted", "The photo couldn't be kept."))?;
        let stored = vec![StoredAttachment {
            part: "0".into(),
            blob,
            key: sealed.key,
            mime,
            name,
            state: "done".into(),
            path: own.to_string_lossy().into_owned(),
            error: String::new(),
            retryable: false,
            attempts: 0,
        }];
        self.send_payload(arg(args, "conversation"), payload, arg(args, "client_id"), arg(args, "user_token"), stored)
    }

    fn send_payload(&self, conversation: &str, mut payload: Payload, client_id: &str, token: &str, attachments: Vec<StoredAttachment>) -> R<Value> {
        let _ops = lock(&self.0.ops);
        let hub = self.hub()?;
        let gid = match self.primary_group(conversation)? {
            Some(gid) => gid,
            None => {
                // A direct conversation whose groups are gone (the other side
                // reinstalled, or this device was removed): start a new one.
                let peer = conversation.strip_prefix("u:").ok_or_else(|| BridgeError::new("not_member", "You're no longer in this conversation."))?;
                self.new_group(&hub, &[peer.to_owned()], ConversationKind::Direct, "", conversation)?
            }
        };
        let (ciphertext, epoch, message_id) = {
            let mut inner = lock(&self.0.inner);
            let ledger = ledger_key(token, client_id);
            if inner.app.token_used(&ledger) {
                return Err(BridgeError::new("duplicate", "That request was already sent."));
            }
            if inner.app.conversations.get(conversation).map(|c| c.request).unwrap_or(false) {
                return Err(BridgeError::new("not_attempted", "Accept this request to reply."));
            }
            payload.handle = inner.app.own.handle.clone();
            let bytes = payload.encode().map_err(|e| from_mls(&e))?;
            let raw = from_b64u(&gid).map_err(|e| from_mls(&e))?;
            let Inner { engine, app, .. } = &mut *inner;
            let (ct, epoch) = engine.as_mut().expect("engine").encrypt(&raw, &bytes).map_err(|e| from_mls(&e))?;
            app.use_token(&ledger);
            app.remember(&payload.id);
            let time = payload.sent / 1000;
            app.add_message(
                conversation,
                StoredMessage {
                    id: payload.id.clone(),
                    sender: app.account.clone(),
                    outgoing: true,
                    client_id: client_id.to_owned(),
                    text: payload.text.clone(),
                    time,
                    state: "sending".into(),
                    reply_to: payload.reply_to.clone().unwrap_or_default(),
                    attachments,
                },
            );
            if let Some(c) = app.conversations.get_mut(conversation) {
                c.updated = time;
                c.preview = preview(&payload.text, payload.attachments.first().map(|a| a.mime.as_str()));
            }
            if let Some(info) = app.groups.get_mut(&gid) {
                info.sent_since_update += 1;
            }
            app.outbox.push(Outbound {
                kind: "application".into(),
                group: gid.clone(),
                epoch,
                ciphertext: b64u(&ct),
                recipients: vec![],
                conversation: conversation.to_owned(),
                message: payload.id.clone(),
            });
            self.persist(&inner)?;
            (b64u(&ct), epoch, payload.id.clone())
        };
        let result = hub.post(&format!("{MESSAGES_V1}/groups/{gid}/messages"), &json!({"kind": "application", "epoch": epoch, "ciphertext": ciphertext}));
        let mut inner = lock(&self.0.inner);
        inner.app.outbox.retain(|o| o.message != message_id);
        let state = if result.is_ok() { "sent" } else { "failed" };
        if let Some(m) = inner.app.message_mut(conversation, &message_id) {
            m.state = state.into();
        }
        let _ = self.persist(&inner);
        let conversation_event = conversation_json(&inner.app, conversation);
        let event = message_json(&inner.app, conversation, &message_id, &self.0.data_dir);
        drop(inner);
        self.emit("conversation", conversation_event);
        self.emit("message", event);
        match result {
            Ok(_) => Ok(json!({"message": message_id})),
            Err(e) => {
                log("warn", &format!("a send failed: {e}"));
                Err(from_hub(&e))
            }
        }
    }

    /// The group a conversation sends through: the lowest active group id, so
    /// both sides of a conversation started twice at once agree.
    fn primary_group(&self, conversation: &str) -> R<Option<String>> {
        let inner = lock(&self.0.inner);
        let c = inner.app.conversations.get(conversation).ok_or_else(|| BridgeError::new("not_found", "That conversation isn't here."))?;
        let engine = inner.engine.as_ref().ok_or_else(|| BridgeError::retry("not_connected", "Luma Messages isn't connected."))?;
        Ok(c
            .groups
            .iter()
            .filter(|g| from_b64u(g).ok().map(|raw| engine.is_active(&raw).unwrap_or(false)).unwrap_or(false))
            .min()
            .cloned())
    }

    fn flush_outbox(&self) {
        let Ok(hub) = self.hub() else { return };
        let pending: Vec<Outbound> = lock(&self.0.inner).app.outbox.clone();
        for item in pending {
            let mut body = json!({"kind": item.kind, "epoch": item.epoch, "ciphertext": item.ciphertext});
            if item.kind == "welcome" {
                body["recipients"] = json!(item.recipients);
            }
            let result = hub.post(&format!("{MESSAGES_V1}/groups/{}/messages", item.group), &body);
            if let Err(e) = &result {
                if e.status == 0 || e.status == 429 || e.status >= 500 {
                    return; // try again on the next connect
                }
                log("warn", &format!("a waiting {} was refused: {e}", item.kind));
            }
            let mut inner = lock(&self.0.inner);
            inner.app.outbox.retain(|o| !(o.kind == item.kind && o.group == item.group && o.ciphertext == item.ciphertext));
            let mut event = None;
            if item.kind == "application" {
                if let Some(m) = inner.app.message_mut(&item.conversation, &item.message) {
                    m.state = if result.is_ok() { "sent".into() } else { "failed".into() };
                    event = Some(message_json(&inner.app, &item.conversation, &item.message, &self.0.data_dir));
                }
            }
            let _ = self.persist(&inner);
            drop(inner);
            if let Some(e) = event {
                self.emit("message", e);
            }
        }
    }

    // ── Commits ─────────────────────────────────────────────────────────

    /// Make, send and confirm one commit (caller holds `ops`). A losing race
    /// discards it, applies the winner from the queue, and tries again.
    fn commit(&self, gid: &str, op: &Op) -> R<()> {
        let raw = from_b64u(gid).map_err(|e| from_mls(&e))?;
        for _attempt in 0..4 {
            let hub = self.hub()?;
            let (commit, epoch, members) = {
                let mut inner = lock(&self.0.inner);
                let Inner { engine, app, .. } = &mut *inner;
                let engine = engine.as_mut().ok_or_else(|| BridgeError::retry("not_connected", "not connected"))?;
                if engine.has_pending_commit(&raw).unwrap_or(false) {
                    let _ = engine.discard(&raw);
                }
                let out = match op {
                    Op::Add(packages) => engine.add(&raw, &packages.iter().map(|(_, kp)| kp.clone()).collect::<Vec<_>>()),
                    Op::Remove(identities) => engine.remove(&raw, identities),
                    Op::Update => engine.update(&raw),
                }
                .map_err(|e| from_mls(&e))?;
                if let (Some(welcome), Op::Add(packages)) = (&out.welcome, op) {
                    app.pending_welcomes.insert(
                        gid.to_owned(),
                        Outbound {
                            kind: "welcome".into(),
                            group: gid.to_owned(),
                            epoch: out.epoch + 1,
                            ciphertext: b64u(welcome),
                            recipients: packages.iter().map(|(device, _)| device.clone()).collect(),
                            conversation: String::new(),
                            message: String::new(),
                        },
                    );
                }
                let members: Vec<String> = out.members_after.iter().map(|m| m.identity.device.clone()).collect();
                self.persist(&inner)?;
                (b64u(&out.commit), out.epoch, members)
            };
            let result = hub.post(&format!("{MESSAGES_V1}/groups/{gid}/messages"), &json!({"kind": "commit", "epoch": epoch, "ciphertext": commit, "members": members}));
            match result {
                Ok(_) => {
                    let mut inner = lock(&self.0.inner);
                    let Inner { engine, app, .. } = &mut *inner;
                    let engine = engine.as_mut().expect("engine");
                    engine.confirm(&raw).map_err(|e| from_mls(&e))?;
                    if let Some(welcome) = app.pending_welcomes.remove(gid) {
                        app.outbox.push(welcome);
                    }
                    if let Some(info) = app.groups.get_mut(gid) {
                        info.missing_since.clear();
                        if matches!(op, Op::Update) {
                            info.last_update = now_ms();
                            info.sent_since_update = 0;
                        }
                    }
                    let members = engine.members(&raw).unwrap_or_default();
                    let conversation_id = app.groups.get(gid).map(|g| g.conversation.clone()).unwrap_or_default();
                    if let Some(c) = app.conversations.get_mut(&conversation_id) {
                        let accounts: BTreeSet<String> = members.iter().map(|m| m.identity.account.clone()).collect();
                        c.members = accounts.into_iter().collect();
                    }
                    let mut fx = Effects::default();
                    note_device_keys(app, &members, &mut fx);
                    self.persist(&inner)?;
                    drop(inner);
                    for (name, data) in fx.events {
                        self.emit(&name, data);
                    }
                    self.flush_outbox();
                    return Ok(());
                }
                Err(e) => {
                    {
                        let mut inner = lock(&self.0.inner);
                        let Inner { engine, app, .. } = &mut *inner;
                        let _ = engine.as_mut().expect("engine").discard(&raw);
                        app.pending_welcomes.remove(gid);
                        let _ = self.persist(&inner);
                    }
                    if e.status == 409 && e.code == "epoch" {
                        log("info", "a commit lost a race; applying the winner and trying again");
                        self.drain_now();
                        continue;
                    }
                    return Err(from_hub(&e));
                }
            }
        }
        Err(BridgeError::retry("busy", "The conversation is changing; try again."))
    }

    /// After a crash between sending a commit and confirming it: the queue
    /// first (a winner of the race arrives there), then the group's epoch
    /// tells whether ours was the one taken.
    fn recover_pending_commits(&self) {
        let _ops = lock(&self.0.ops);
        let pending: Vec<Vec<u8>> = {
            let inner = lock(&self.0.inner);
            let Some(engine) = inner.engine.as_ref() else { return };
            engine.groups().into_iter().filter(|g| engine.has_pending_commit(g).unwrap_or(false)).collect()
        };
        if pending.is_empty() {
            return;
        }
        self.drain_now();
        let Ok(hub) = self.hub() else { return };
        for raw in pending {
            let gid = b64u(&raw);
            let server = hub.get(&format!("{MESSAGES_V1}/groups/{gid}")).ok().and_then(|v| v.get("epoch").and_then(Value::as_u64));
            let mut inner = lock(&self.0.inner);
            let Inner { engine, app, .. } = &mut *inner;
            let engine = engine.as_mut().expect("engine");
            if !engine.has_pending_commit(&raw).unwrap_or(false) {
                continue;
            }
            let local = engine.epoch(&raw).unwrap_or(0);
            if server == Some(local + 1) {
                let _ = engine.confirm(&raw);
                if let Some(w) = app.pending_welcomes.remove(&gid) {
                    app.outbox.push(w);
                }
                log("info", "a commit sent before a restart was taken; confirmed it");
            } else {
                let _ = engine.discard(&raw);
                app.pending_welcomes.remove(&gid);
            }
            let _ = self.persist(&inner);
        }
    }

    // ── Conversations ───────────────────────────────────────────────────

    fn resolve(&self, hub: &Hub, participant: &str) -> R<(String, Person)> {
        if let Some(account) = participant.strip_prefix("account:") {
            if !account_ok(account) {
                return Err(invalid("That isn't a Luma account."));
            }
            let person = lock(&self.0.inner).app.people.get(account).cloned().unwrap_or_default();
            return Ok((account.to_owned(), person));
        }
        let handle = clean_handle(participant).ok_or_else(|| invalid("That isn't a Luma username."))?;
        let answer = hub.get(&format!("/api/hub/sync/people/{handle}")).map_err(|e| from_hub(&e))?;
        let account = answer.get("account").and_then(Value::as_str).unwrap_or("").to_owned();
        if !account_ok(&account) {
            return Err(BridgeError::new("handle_unknown", "No one on Luma has that username."));
        }
        if answer.get("blocked").and_then(Value::as_bool).unwrap_or(false) {
            return Err(BridgeError::new("blocked_by_you", "You blocked this person. Unblock them to send a message."));
        }
        let person = person_from(&answer);
        lock(&self.0.inner).app.people.insert(account.clone(), person.clone());
        Ok((account, person))
    }

    fn conversation_create(&self, args: &Value) -> R<Value> {
        let hub = self.hub()?;
        let participants: Vec<String> = args
            .get("participants")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_owned)).collect())
            .unwrap_or_default();
        if participants.is_empty() || participants.len() > 50 {
            return Err(invalid("Choose who to message."));
        }
        let own = lock(&self.0.inner).app.account.clone();
        let mut accounts = Vec::new();
        for p in &participants {
            let (account, _) = self.resolve(&hub, p)?;
            if !accounts.contains(&account) {
                accounts.push(account);
            }
        }
        let name: String = arg(args, "name").trim().chars().take(luma_mls::payload::MAX_NAME).collect();
        let direct = accounts.len() == 1 && name.is_empty();
        let conversation_id = if direct { format!("u:{}", accounts[0]) } else { String::new() };
        let _ops = lock(&self.0.ops);
        if direct {
            if let Ok(Some(_)) = self.primary_group(&conversation_id) {
                let inner = lock(&self.0.inner);
                let c = inner.app.conversations.get(&conversation_id).cloned();
                if let Some(mut c) = c {
                    drop(inner);
                    if c.hidden {
                        c.hidden = false;
                        let mut inner = lock(&self.0.inner);
                        inner.app.conversations.insert(c.id.clone(), c);
                        let _ = self.persist(&inner);
                    }
                    return Ok(json!({"conversation": conversation_json(&lock(&self.0.inner).app, &conversation_id)}));
                }
            }
        }
        // Say who is being messaged before anything is encrypted, so a refusal
        // (only people they've accepted, a block) reads as one.
        for account in &accounts {
            // Notes to oneself use the same encrypted, multi-device MLS
            // conversation. They do not create a stranger request.
            if *account == own { continue; }
            hub.post("/api/hub/sync/chat/requests", &json!({"account": account})).map_err(|e| from_hub(&e))?;
        }
        let kind = if direct { ConversationKind::Direct } else { ConversationKind::Group };
        let gid = self.new_group(&hub, &accounts, kind, &name, &conversation_id)?;
        let id = lock(&self.0.inner).app.groups.get(&gid).map(|g| g.conversation.clone()).unwrap_or_default();
        let conversation = conversation_json(&lock(&self.0.inner).app, &id);
        self.emit("conversation", conversation.clone());
        Ok(json!({"conversation": conversation}))
    }

    /// A new MLS group with every device of these accounts and this account's
    /// other devices, and its first message saying what it is. Caller holds `ops`.
    fn new_group(&self, hub: &Hub, accounts: &[String], kind: ConversationKind, name: &str, conversation_id: &str) -> R<String> {
        let own = lock(&self.0.inner).app.account.clone();
        let mut wanted: Vec<String> = accounts.to_vec();
        wanted.push(own.clone());
        wanted.sort();
        wanted.dedup();
        let claim = hub.post(&format!("{MESSAGES_V1}/key-packages/claim"), &json!({"accounts": wanted})).map_err(|e| from_hub(&e))?;
        let mut packages = Vec::new();
        let mut found: BTreeSet<String> = BTreeSet::new();
        for entry in claim.get("accounts").and_then(Value::as_array).cloned().unwrap_or_default() {
            let account = entry.get("account").and_then(Value::as_str).unwrap_or("").to_owned();
            for device in entry.get("devices").and_then(Value::as_array).cloned().unwrap_or_default() {
                let (Some(id), Some(kp)) = (device.get("device").and_then(Value::as_str), device.get("key_package").and_then(Value::as_str)) else { continue };
                let Ok(bytes) = from_b64u(kp) else { continue };
                packages.push((id.to_owned(), bytes));
                found.insert(account.clone());
            }
        }
        for account in accounts {
            if *account != own && !found.contains(account) {
                let name = lock(&self.0.inner).app.name_of(account);
                let who = if name.is_empty() { "This person".to_owned() } else { name };
                return Err(BridgeError::new("not_on_luma", &format!("{who} hasn't set up Luma Messages yet.")));
            }
        }
        let gid = {
            let mut inner = lock(&self.0.inner);
            let Inner { engine, app, .. } = &mut *inner;
            let raw = engine.as_mut().ok_or_else(|| BridgeError::retry("not_connected", "not connected"))?.create_group().map_err(|e| from_mls(&e))?;
            let gid = b64u(&raw);
            let id = if conversation_id.is_empty() { format!("g:{gid}") } else { conversation_id.to_owned() };
            app.groups.insert(gid.clone(), GroupInfo { conversation: id.clone(), last_update: now_ms(), joined: now_ms(), ..GroupInfo::default() });
            let mut members: Vec<String> = accounts.to_vec();
            members.push(own.clone());
            members.sort();
            members.dedup();
            let c = app.conversations.entry(id.clone()).or_insert_with(|| Conversation {
                id: id.clone(),
                kind: if kind == ConversationKind::Direct { "direct".into() } else { "group".into() },
                name: name.to_owned(),
                peer: if kind == ConversationKind::Direct { accounts[0].clone() } else { String::new() },
                members: members.clone(),
                groups: vec![],
                created: now_s(),
                updated: now_s(),
                unread: 0,
                request: false,
                request_from: String::new(),
                verified: String::new(),
                safety_changed: false,
                devices_changed: false,
                removed: false,
                hidden: false,
                preview: String::new(),
            });
            c.groups.push(gid.clone());
            c.groups.sort();
            c.removed = false;
            c.hidden = false;
            self.persist(&inner)?;
            gid
        };
        // A newly enrolled account may have just this one device. Publish a
        // normal self-update commit to establish its delivery group; OpenMLS
        // does not accept an empty Add proposal.
        let result = if packages.is_empty() { self.commit(&gid, &Op::Update) }
                     else { self.commit(&gid, &Op::Add(packages)) };
        if let Err(e) = result {
            let mut inner = lock(&self.0.inner);
            let Inner { engine, app, .. } = &mut *inner;
            let _ = engine.as_mut().expect("engine").forget(&from_b64u(&gid).unwrap_or_default());
            let id = app.groups.remove(&gid).map(|g| g.conversation).unwrap_or_default();
            if let Some(c) = app.conversations.get_mut(&id) {
                c.groups.retain(|g| g != &gid);
                if c.groups.is_empty() && !app.messages.contains_key(&id) {
                    app.conversations.remove(&id);
                }
            }
            let _ = self.persist(&inner);
            return Err(e);
        }
        self.send_meta(hub, &gid, kind, name)?;
        Ok(gid)
    }

    /// The conversation's kind and name, for devices that just joined.
    fn send_meta(&self, hub: &Hub, gid: &str, kind: ConversationKind, name: &str) -> R<()> {
        let (ct, epoch) = {
            let mut inner = lock(&self.0.inner);
            let mut payload = Payload::text("", now_ms());
            payload.conversation = Some(ConversationMeta { kind, name: name.to_owned() });
            payload.handle = inner.app.own.handle.clone();
            inner.app.remember(&payload.id);
            let bytes = payload.encode().map_err(|e| from_mls(&e))?;
            let raw = from_b64u(gid).map_err(|e| from_mls(&e))?;
            let (ct, epoch) = inner.engine.as_mut().expect("engine").encrypt(&raw, &bytes).map_err(|e| from_mls(&e))?;
            inner.app.outbox.push(Outbound { kind: "application".into(), group: gid.to_owned(), epoch, ciphertext: b64u(&ct), recipients: vec![], conversation: String::new(), message: String::new() });
            self.persist(&inner)?;
            (b64u(&ct), epoch)
        };
        let result = hub.post(&format!("{MESSAGES_V1}/groups/{gid}/messages"), &json!({"kind": "application", "epoch": epoch, "ciphertext": ct}));
        let mut inner = lock(&self.0.inner);
        if result.is_ok() || result.as_ref().map_err(|e| e.status != 0 && e.status < 500 && e.status != 429).unwrap_err() {
            inner.app.outbox.retain(|o| o.ciphertext != ct);
            let _ = self.persist(&inner);
        }
        result.map(|_| ()).map_err(|e| from_hub(&e))
    }

    fn conversations_list(&self, args: &Value) -> R<Value> {
        let limit = args.get("limit").and_then(Value::as_u64).unwrap_or(50).clamp(1, 500) as usize;
        let offset: usize = arg(args, "cursor").parse().unwrap_or(0);
        let inner = lock(&self.0.inner);
        let mut list: Vec<&Conversation> = inner.app.conversations.values().filter(|c| !c.hidden).collect();
        list.sort_by(|a, b| b.updated.cmp(&a.updated).then(a.id.cmp(&b.id)));
        let page: Vec<Value> = list.iter().skip(offset).take(limit).map(|c| conversation_json(&inner.app, &c.id)).collect();
        let mut result = json!({"conversations": page});
        if offset + limit < list.len() {
            result["cursor"] = json!((offset + limit).to_string());
        }
        Ok(result)
    }

    fn messages_list(&self, args: &Value) -> R<Value> {
        let conversation = arg(args, "conversation");
        let limit = args.get("limit").and_then(Value::as_u64).unwrap_or(30).clamp(1, 300) as usize;
        let before = arg(args, "before");
        let inner = lock(&self.0.inner);
        let list = inner.app.messages.get(conversation).cloned().unwrap_or_default();
        let end = if before.is_empty() { list.len() } else { list.iter().position(|m| m.id == before).unwrap_or(list.len()) };
        let start = end.saturating_sub(limit);
        let messages: Vec<Value> = list[start..end].iter().map(|m| message_json(&inner.app, conversation, &m.id, &self.0.data_dir)).collect();
        Ok(json!({"messages": messages, "more": start > 0}))
    }

    fn message_read(&self, args: &Value) -> R<Value> {
        let mut inner = lock(&self.0.inner);
        if let Some(c) = inner.app.conversations.get_mut(arg(args, "conversation")) {
            c.unread = 0;
        }
        let _ = self.persist(&inner);
        Ok(json!({}))
    }

    fn reset(&self) -> R<Value> {
        let mut inner = lock(&self.0.inner);
        let path = state::state_path(&self.0.data_dir);
        if path.exists() {
            let _ = std::fs::rename(&path, self.0.data_dir.join("state.sealed.previous"));
        }
        inner.engine = None;
        inner.app = AppState::default();
        drop(inner);
        self.start_connect();
        Ok(json!({}))
    }

    fn logout(&self, args: &Value) -> R<Value> {
        if args.get("remote").and_then(Value::as_bool).unwrap_or(false) {
            if let Ok(hub) = self.hub() {
                if let Err(e) = hub.delete(&format!("{MESSAGES_V1}/devices/self")) {
                    log("warn", &format!("sign-out not confirmed by the server: {e}"));
                }
            }
        }
        self.0.generation.fetch_add(1, Ordering::SeqCst);
        *lock(&self.0.hub) = None;
        let mut inner = lock(&self.0.inner);
        let _ = std::fs::remove_file(state::state_path(&self.0.data_dir));
        let _ = std::fs::remove_dir_all(self.0.data_dir.join("media"));
        let _ = std::fs::create_dir_all(self.0.data_dir.join("media"));
        inner.engine = None;
        inner.app = AppState::default();
        drop(inner);
        self.set_status("needs_login", "signed_out");
        Ok(json!({}))
    }

    // ── Identity, requests, blocks, reports ─────────────────────────────

    fn identity(&self) -> R<Value> {
        let hub = self.hub()?;
        let mut present = hub.get("/api/hub/sync/identity/handle").map_err(|e| from_hub(&e))?;
        let mut inner = lock(&self.0.inner);
        inner.app.own.handle = present.get("handle").and_then(Value::as_str).unwrap_or("").to_owned();
        present["account"] = json!(inner.app.account);
        present["name"] = json!(inner.app.own.name);
        let _ = self.persist(&inner);
        Ok(present)
    }

    fn handle_check(&self, args: &Value) -> R<Value> {
        let Some(handle) = clean_handle(arg(args, "handle")) else {
            return Ok(json!({"available": false, "code": "handle_characters", "reason": "Use only the letters a to z, digits, dots and underscores."}));
        };
        self.hub()?.get(&format!("/api/hub/sync/identity/handle/check?handle={handle}")).map_err(|e| from_hub(&e))
    }

    fn handle_claim(&self, args: &Value) -> R<Value> {
        let hub = self.hub()?;
        let answer = hub.put("/api/hub/sync/identity/handle", &json!({"handle": arg(args, "handle").trim()}));
        let answer = match answer {
            Ok(v) => v,
            Err(e) => {
                let mut error = from_hub(&e);
                if !e.code.is_empty() {
                    error.code = e.code.clone();
                }
                return Err(error);
            }
        };
        let mut inner = lock(&self.0.inner);
        inner.app.own.handle = answer.get("handle").and_then(Value::as_str).unwrap_or("").to_owned();
        let _ = self.persist(&inner);
        drop(inner);
        self.emit("status", self.status_value());
        Ok(answer)
    }

    fn people(&self, args: &Value) -> R<Value> {
        let hub = self.hub()?;
        let handle = clean_handle(arg(args, "handle")).ok_or_else(|| invalid("That isn't a Luma username."))?;
        let answer = hub.get(&format!("/api/hub/sync/people/{handle}")).map_err(|e| from_hub(&e))?;
        if let Some(account) = answer.get("account").and_then(Value::as_str) {
            lock(&self.0.inner).app.people.insert(account.to_owned(), person_from(&answer));
        }
        Ok(answer)
    }

    fn requests(&self) -> R<Value> {
        let answer = self.hub()?.get("/api/hub/sync/chat/requests").map_err(|e| from_hub(&e))?;
        let mut inner = lock(&self.0.inner);
        for item in answer.get("items").and_then(Value::as_array).cloned().unwrap_or_default() {
            if let Some(account) = item.get("account").and_then(Value::as_str) {
                inner.app.people.insert(account.to_owned(), person_from(&item));
            }
        }
        Ok(answer)
    }

    fn account_of(&self, args: &Value) -> R<String> {
        let account = arg(args, "account");
        if account_ok(account) {
            return Ok(account.to_owned());
        }
        let inner = lock(&self.0.inner);
        let c = inner.app.conversations.get(arg(args, "conversation")).ok_or_else(|| invalid("Choose a person."))?;
        let account = if !c.request_from.is_empty() { c.request_from.clone() } else { c.peer.clone() };
        if account.is_empty() {
            return Err(invalid("Choose a person."));
        }
        Ok(account)
    }

    fn request_answer(&self, args: &Value) -> R<Value> {
        let state = arg(args, "state");
        if state != "accepted" && state != "declined" {
            return Err(invalid("Accept or decline."));
        }
        let account = self.account_of(args)?;
        match self.hub()?.put(&format!("/api/hub/sync/chat/requests/{account}"), &json!({"state": state})) {
            Ok(_) => {}
            Err(e) if e.code == "request_unknown" => {}
            Err(e) => return Err(from_hub(&e)),
        }
        let mut inner = lock(&self.0.inner);
        let ids: Vec<String> = inner.app.conversations.values().filter(|c| c.request && c.request_from == account).map(|c| c.id.clone()).collect();
        for id in &ids {
            if let Some(c) = inner.app.conversations.get_mut(id) {
                c.request = false;
                if state == "declined" {
                    c.hidden = true;
                }
            }
        }
        if state == "declined" {
            let Inner { engine, app, .. } = &mut *inner;
            for id in &ids {
                if let Some(c) = app.conversations.get(id) {
                    for g in c.groups.clone() {
                        let _ = engine.as_mut().expect("engine").forget(&from_b64u(&g).unwrap_or_default());
                        app.groups.remove(&g);
                    }
                }
                app.messages.remove(id);
                if let Some(c) = app.conversations.get_mut(id) {
                    c.groups.clear();
                }
            }
        }
        let _ = self.persist(&inner);
        let events: Vec<Value> = ids.iter().map(|id| conversation_json(&inner.app, id)).collect();
        drop(inner);
        for e in events {
            self.emit("conversation", e);
        }
        Ok(json!({"state": state, "conversations": ids}))
    }

    fn block(&self, args: &Value) -> R<Value> {
        let account = self.account_of(args)?;
        let on = args.get("on").and_then(Value::as_bool).unwrap_or(true);
        let hub = self.hub()?;
        let path = format!("/api/hub/sync/chat/blocks/{account}");
        if on { hub.put(&path, &json!({})) } else { hub.delete(&path) }.map_err(|e| from_hub(&e))?;
        let mut inner = lock(&self.0.inner);
        let mut events = Vec::new();
        if on {
            inner.app.blocked.insert(account.clone());
            let id = format!("u:{account}");
            let request_ids: Vec<String> = inner.app.conversations.values().filter(|c| c.id == id || (c.request && c.request_from == account)).map(|c| c.id.clone()).collect();
            for cid in request_ids {
                if let Some(c) = inner.app.conversations.get_mut(&cid) {
                    c.hidden = true;
                    c.request = false;
                }
                events.push(conversation_json(&inner.app, &cid));
            }
        } else {
            inner.app.blocked.remove(&account);
        }
        let _ = self.persist(&inner);
        drop(inner);
        for e in events {
            self.emit("conversation", e);
        }
        Ok(json!({"blocked": on}))
    }

    fn blocks(&self) -> R<Value> {
        self.hub()?.get("/api/hub/sync/chat/blocks").map_err(|e| from_hub(&e))
    }

    fn report(&self, args: &Value) -> R<Value> {
        let account = self.account_of(args)?;
        let reason = arg(args, "reason");
        let chosen: Vec<String> = args
            .get("messages")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_owned)).take(20).collect())
            .unwrap_or_default();
        let conversation = arg(args, "conversation");
        let messages: Vec<Value> = {
            let inner = lock(&self.0.inner);
            inner
                .app
                .messages
                .get(conversation)
                .map(|list| {
                    list.iter()
                        .filter(|m| chosen.contains(&m.id) && m.sender == account && !m.text.is_empty())
                        .map(|m| json!({"id": m.id, "body": m.text.chars().take(4000).collect::<String>(), "sent_at": m.time * 1000, "from": m.sender}))
                        .collect()
                })
                .unwrap_or_default()
        };
        let block = args.get("block").and_then(Value::as_bool).unwrap_or(false);
        let mut body = json!({"account": account, "reason": reason, "messages": messages, "block": block});
        let detail = arg(args, "detail");
        if !detail.is_empty() {
            body["detail"] = json!(detail.chars().take(1000).collect::<String>());
        }
        let answer = self.hub()?.post("/api/hub/sync/chat/reports", &body).map_err(|e| from_hub(&e))?;
        if block {
            let _ = self.block(&json!({"account": account, "on": true}));
        }
        Ok(answer)
    }

    fn safety(&self, args: &Value) -> R<Value> {
        let conversation = arg(args, "conversation");
        let gid = self.primary_group(conversation)?.ok_or_else(|| BridgeError::new("not_member", "You're no longer in this conversation."))?;
        let inner = lock(&self.0.inner);
        let c = inner.app.conversations.get(conversation).ok_or_else(|| invalid("no conversation"))?;
        if c.kind != "direct" {
            return Err(BridgeError::new("unsupported", "Safety numbers are for conversations with one person."));
        }
        let members = inner.engine.as_ref().expect("engine").members(&from_b64u(&gid).unwrap_or_default()).map_err(|e| from_mls(&e))?;
        let keys = keys_by_account(&members);
        let empty = Vec::new();
        let mine = keys.get(&inner.app.account).unwrap_or(&empty);
        let theirs = keys.get(&c.peer).unwrap_or(&empty);
        let number = luma_mls::safety_number(&inner.app.account, mine, &c.peer, theirs).map_err(|e| from_mls(&e))?;
        Ok(json!({
            "digits": number.digits,
            "qr": number.qr,
            "verified": !c.verified.is_empty() && c.verified == number.qr,
            "changed": c.safety_changed,
            "devices": theirs.len(),
        }))
    }

    fn verify(&self, args: &Value) -> R<Value> {
        let conversation = arg(args, "conversation");
        let current = self.safety(args)?;
        let qr = current.get("qr").and_then(Value::as_str).unwrap_or("").to_owned();
        let scanned = arg(args, "scanned");
        if !scanned.is_empty() && scanned.trim() != qr {
            return Err(BridgeError::new("mismatch", "These codes don't match. The conversation is not verified."));
        }
        let verified = args.get("verified").and_then(Value::as_bool).unwrap_or(true);
        let mut inner = lock(&self.0.inner);
        if let Some(c) = inner.app.conversations.get_mut(conversation) {
            c.verified = if verified { qr } else { String::new() };
            c.safety_changed = false;
            c.devices_changed = false;
        }
        let _ = self.persist(&inner);
        let event = conversation_json(&inner.app, conversation);
        drop(inner);
        self.emit("conversation", event);
        Ok(json!({"verified": verified}))
    }

    fn devices(&self, args: &Value) -> R<Value> {
        let account = match arg(args, "account") {
            "" => lock(&self.0.inner).app.account.clone(),
            other if account_ok(other) => other.to_owned(),
            _ => return Err(invalid("That isn't a Luma account.")),
        };
        self.hub()?.get(&format!("{MESSAGES_V1}/accounts/{account}/devices")).map_err(|e| from_hub(&e))
    }

    // ── Upkeep ──────────────────────────────────────────────────────────

    fn maintenance_loop(&self, generation: u64) {
        let mut next = Instant::now();
        while self.live(generation) {
            if Instant::now() >= next {
                let _ = self.upkeep(false);
                next = Instant::now() + Duration::from_secs(300);
            }
            std::thread::sleep(Duration::from_secs(5));
        }
    }

    fn sync_now(&self) -> R<Value> {
        self.drain_now();
        self.upkeep(true)?;
        self.drain_now();
        Ok(json!({}))
    }

    /// Key packages, device lists (revocations and new devices), and the
    /// scheduled key updates. `force` reads every device list now.
    fn upkeep(&self, force: bool) -> R<()> {
        let hub = self.hub()?;
        self.top_up_key_packages(&hub)?;
        let (accounts, own) = {
            let inner = lock(&self.0.inner);
            let mut set: BTreeSet<String> = BTreeSet::new();
            set.insert(inner.app.account.clone());
            for c in inner.app.conversations.values().filter(|c| !c.removed && !c.hidden) {
                set.extend(c.members.iter().cloned());
            }
            let due: Vec<String> = set
                .into_iter()
                .filter(|a| force || now_ms().saturating_sub(*inner.app.devices_checked.get(a).unwrap_or(&0)) > DEVICES_EVERY_MS)
                .collect();
            (due, inner.app.account.clone())
        };
        let mut registered: BTreeMap<String, Vec<(String, String)>> = BTreeMap::new();
        for account in &accounts {
            let list = match hub.get(&format!("{MESSAGES_V1}/accounts/{account}/devices")) {
                Ok(list) => list,
                Err(e) if e.status == 404 && e.code == "account_unknown" && *account != own => {
                    let mut inner = lock(&self.0.inner);
                    inner.app.deleted.insert(account.clone());
                    inner.app.devices_checked.insert(account.clone(), now_ms());
                    continue;
                }
                Err(_) => continue,
            };
            let devices: Vec<(String, String)> = list
                .get("devices")
                .and_then(Value::as_array)
                .map(|a| {
                    a.iter()
                        .filter_map(|d| Some((d.get("device")?.as_str()?.to_owned(), d.get("signature_key")?.as_str()?.to_owned())))
                        .collect()
                })
                .unwrap_or_default();
            let mut inner = lock(&self.0.inner);
            for r in list.get("revoked").and_then(Value::as_array).cloned().unwrap_or_default() {
                if let (Some(device), Some(key)) = (r.get("device").and_then(Value::as_str), r.get("signature_key").and_then(Value::as_str)) {
                    inner.app.revoked.insert((format!("luma1:{account}:{device}"), key.to_owned()));
                }
            }
            inner.app.devices_checked.insert(account.clone(), now_ms());
            registered.insert(account.clone(), devices);
        }
        let _ = self.persist(&lock(&self.0.inner));
        let _ops = lock(&self.0.ops);
        let groups: Vec<(String, Vec<Member>, GroupInfo)> = {
            let inner = lock(&self.0.inner);
            let Some(engine) = inner.engine.as_ref() else { return Ok(()) };
            engine
                .groups()
                .into_iter()
                .filter(|g| engine.is_active(g).unwrap_or(false))
                .map(|g| {
                    let gid = b64u(&g);
                    (gid.clone(), engine.members(&g).unwrap_or_default(), inner.app.groups.get(&gid).cloned().unwrap_or_default())
                })
                .collect()
        };
        for (gid, members, info) in groups {
            // Revoked devices out, first.
            let gone: Vec<Identity> = {
                let inner = lock(&self.0.inner);
                members.iter().filter(|m| revoked(&inner.app, m)).map(|m| m.identity.clone()).collect()
            };
            if !gone.is_empty() {
                match self.commit(&gid, &Op::Remove(gone)) {
                    Ok(()) => log("info", "removed revoked devices from a conversation"),
                    Err(e) => log("warn", &format!("revoked devices not removed yet: {}", e.code)),
                }
                continue;
            }
            // Devices registered for a member account but not in the group.
            let present: BTreeSet<String> = members.iter().map(|m| m.identity.device.clone()).collect();
            let member_accounts: BTreeSet<String> = members.iter().map(|m| m.identity.account.clone()).collect();
            let mut missing: Vec<(String, String)> = Vec::new();
            for account in &member_accounts {
                for (device, _) in registered.get(account).cloned().unwrap_or_default() {
                    if !present.contains(&device) {
                        missing.push((account.clone(), device));
                    }
                }
            }
            if !missing.is_empty() {
                // The member with the lowest leaf adds at once; the others wait,
                // so every device does not claim a key package for the same one.
                let me = lock(&self.0.inner).app.device.clone();
                let lowest = members.iter().min_by_key(|m| m.leaf).map(|m| m.identity.device == me).unwrap_or(false);
                let first_seen = missing.iter().map(|(_, d)| *info.missing_since.get(d).unwrap_or(&now_ms())).min().unwrap_or(now_ms());
                {
                    let mut inner = lock(&self.0.inner);
                    if let Some(g) = inner.app.groups.get_mut(&gid) {
                        for (_, d) in &missing {
                            g.missing_since.entry(d.clone()).or_insert_with(now_ms);
                        }
                    }
                }
                if lowest || force || now_ms().saturating_sub(first_seen) > MISSING_GRACE_MS {
                    let accounts: BTreeSet<String> = missing.iter().map(|(a, _)| a.clone()).collect();
                    if let Ok(claim) = hub.post(&format!("{MESSAGES_V1}/key-packages/claim"), &json!({"accounts": accounts})) {
                        let wanted: BTreeSet<String> = missing.iter().map(|(_, d)| d.clone()).collect();
                        let mut packages = Vec::new();
                        for entry in claim.get("accounts").and_then(Value::as_array).cloned().unwrap_or_default() {
                            for d in entry.get("devices").and_then(Value::as_array).cloned().unwrap_or_default() {
                                let (Some(id), Some(kp)) = (d.get("device").and_then(Value::as_str), d.get("key_package").and_then(Value::as_str)) else { continue };
                                if wanted.contains(id) {
                                    if let Ok(bytes) = from_b64u(kp) {
                                        packages.push((id.to_owned(), bytes));
                                    }
                                }
                            }
                        }
                        if !packages.is_empty() {
                            match self.commit(&gid, &Op::Add(packages)) {
                                Ok(()) => {
                                    let (kind, name) = {
                                        let inner = lock(&self.0.inner);
                                        let c = inner.app.conversations.get(&info.conversation);
                                        (
                                            if c.map(|c| c.kind == "direct").unwrap_or(false) { ConversationKind::Direct } else { ConversationKind::Group },
                                            c.map(|c| c.name.clone()).unwrap_or_default(),
                                        )
                                    };
                                    let _ = self.send_meta(&hub, &gid, kind, &name);
                                    log("info", "added a new device to a conversation");
                                }
                                Err(e) => log("warn", &format!("a new device was not added yet: {}", e.code)),
                            }
                        }
                    }
                }
                continue;
            }
            if info.sent_since_update >= UPDATE_AFTER_SENT || now_ms().saturating_sub(info.last_update) > UPDATE_EVERY_MS {
                if let Err(e) = self.commit(&gid, &Op::Update) {
                    log("info", &format!("scheduled key update deferred: {}", e.code));
                }
            }
        }
        Ok(())
    }
}

enum Op {
    Add(Vec<(String, Vec<u8>)>),
    Remove(Vec<Identity>),
    Update,
}

#[derive(Default)]
struct Effects {
    events: Vec<(String, Value)>,
    downloads: Vec<(String, String)>,
    handles: Vec<(String, String)>,
}

fn ledger_key(token: &str, part: &str) -> String {
    if part.is_empty() { token.to_owned() } else { format!("{token}:{part}") }
}

fn revoked(app: &AppState, m: &Member) -> bool {
    app.deleted.contains(&m.identity.account) || app.revoked.contains(&(m.identity.to_credential(), b64u(&m.signature_key)))
}

/// A device added to or gone from someone's account: a quiet notice, or a
/// strong one where the person had verified the safety number.
fn note_device_keys(app: &mut AppState, members: &[Member], fx: &mut Effects) {
    for (account, keys) in keys_by_account(members) {
        if account == app.account {
            continue;
        }
        let encoded: Vec<String> = keys.iter().map(|k| b64u(k)).collect();
        let before = app.device_keys.insert(account.clone(), encoded.clone());
        if matches!(&before, Some(old) if *old != encoded) {
            let ids: Vec<String> = app.conversations.values().filter(|c| c.kind == "direct" && c.peer == account).map(|c| c.id.clone()).collect();
            for id in ids {
                if let Some(c) = app.conversations.get_mut(&id) {
                    if c.verified.is_empty() {
                        c.devices_changed = true;
                    } else {
                        c.safety_changed = true;
                    }
                }
                fx.events.push(("conversation".into(), conversation_json(app, &id)));
            }
        }
    }
}

fn person_from(v: &Value) -> Person {
    Person {
        handle: v.get("handle").and_then(Value::as_str).unwrap_or("").to_owned(),
        name: v.get("display_name").and_then(Value::as_str).filter(|name| !name.contains('@')).unwrap_or("").to_owned(),
        hue: v.get("hue").and_then(Value::as_i64).unwrap_or(0),
    }
}

fn preview(text: &str, mime: Option<&str>) -> String {
    if !text.trim().is_empty() {
        return text.chars().take(200).collect();
    }
    match mime {
        Some(m) if m.starts_with("image/") => "Photo".into(),
        Some(_) => "Attachment".into(),
        None => String::new(),
    }
}

fn write_private(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    use std::io::Write;
    use std::os::unix::fs::OpenOptionsExt;
    let temporary = path.with_extension("part");
    let mut file = std::fs::OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(&temporary)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    std::fs::rename(&temporary, path)
}

fn conversation_json(app: &AppState, id: &str) -> Value {
    let Some(c) = app.conversations.get(id) else { return json!({"id": id}) };
    let participants: Vec<Value> = c
        .members
        .iter()
        .filter(|a| **a != app.account)
        .map(|a| {
            let mut p = json!({"id": a, "name": app.name_of(a)});
            if let Some(person) = app.people.get(a) {
                if !person.handle.is_empty() {
                    p["handle"] = json!(format!("@{}", person.handle));
                }
                p["hue"] = json!(person.hue);
            }
            p
        })
        .collect();
    let name = if c.kind == "group" {
        if !c.name.is_empty() {
            c.name.clone()
        } else {
            let names: Vec<String> = participants.iter().filter_map(|p| p.get("name").and_then(Value::as_str)).filter(|n| !n.is_empty()).map(str::to_owned).collect();
            names.join(", ")
        }
    } else {
        app.name_of(&c.peer)
    };
    let handle = app.people.get(&c.peer).map(|p| p.handle.clone()).unwrap_or_default();
    let mut value = json!({
        "id": c.id,
        "name": name,
        "kind": c.kind,
        "participants": participants,
        "updated": c.updated,
        "unread": c.unread,
        "archived": c.hidden,
        "luma": {
            "encrypted": true,
            "request": c.request,
            "request_from": c.request_from,
            "verified": !c.verified.is_empty(),
            "safety_changed": c.safety_changed,
            "devices_changed": c.devices_changed,
            "removed": c.removed,
            "hidden": c.hidden,
            "account": c.peer,
            "handle": if handle.is_empty() { String::new() } else { format!("@{handle}") },
        }
    });
    if !c.preview.is_empty() {
        value["preview"] = json!(c.preview);
    }
    value
}

fn media_json(conversation: &str, message: &str, a: &StoredAttachment) -> Value {
    let mut v = json!({"conversation": conversation, "message": message, "part": a.part, "state": a.state, "mime": a.mime, "name": a.name, "size": a.key.size, "attempt": a.attempts});
    if !a.path.is_empty() {
        v["path"] = json!(a.path);
    }
    if !a.error.is_empty() {
        v["error"] = json!(a.error);
        v["retryable"] = json!(a.retryable);
    }
    v
}

fn message_json(app: &AppState, conversation: &str, id: &str, _data_dir: &Path) -> Value {
    let Some(m) = app.messages.get(conversation).and_then(|l| l.iter().find(|m| m.id == id)) else {
        return json!({"id": id, "conversation": conversation});
    };
    let sender = if m.outgoing { Value::Null } else { json!({"id": m.sender, "name": app.name_of(&m.sender)}) };
    let attachments: Vec<Value> = m
        .attachments
        .iter()
        .map(|a| {
            let mut v = json!({"part": a.part, "state": a.state, "mime": a.mime, "name": a.name, "size": a.key.size, "attempt": a.attempts});
            if !a.path.is_empty() {
                v["path"] = json!(a.path);
            }
            if !a.error.is_empty() {
                v["error"] = json!(a.error);
                v["retryable"] = json!(a.retryable);
            }
            v
        })
        .collect();
    let mut v = json!({
        "id": m.id,
        "conversation": conversation,
        "sender": sender,
        "outgoing": m.outgoing,
        "text": m.text,
        "time": m.time,
        "state": m.state,
        "attachments": attachments,
        "encrypted": true,
    });
    if !m.client_id.is_empty() {
        v["client_id"] = json!(m.client_id);
    }
    if !m.reply_to.is_empty() {
        v["reply_to"] = json!(m.reply_to);
    }
    v
}

#[cfg(test)]
mod identity_binding_tests {
    use super::*;
    const DEVICE: &str = "0b7e3a52-8c1d-4f6e-9a20-3d5c7b1e9f04";
    const OTHER: &str = "1b7e3a52-8c1d-4f6e-9a20-3d5c7b1e9f04";

    #[test]
    fn registration_rejects_each_foreign_identity_field_even_with_our_key() {
        let id = Identity::new("acct_Alice-1234", DEVICE).unwrap();
        let response = json!({"account": id.account, "device": id.device,
            "identity": id.to_credential(), "signature_key": "our-key"});
        validate_registration(&response, &id, Some("our-key")).unwrap();
        for (field, wrong) in [("account", "acct_Bob-123456"), ("device", OTHER),
            ("identity", "luma1:acct_Bob-123456:1b7e3a52-8c1d-4f6e-9a20-3d5c7b1e9f04"),
            ("signature_key", "another-key")] {
            let mut answer = response.clone();
            answer[field] = json!(wrong);
            assert!(validate_registration(&answer, &id, Some("our-key")).is_err(), "accepted foreign {field}");
            answer.as_object_mut().unwrap().remove(field);
            assert!(validate_registration(&answer, &id, Some("our-key")).is_err(), "accepted missing {field}");
        }
        // POST registration answers have no key, but must bind all identity fields.
        let mut post = response;
        post.as_object_mut().unwrap().remove("signature_key");
        validate_registration(&post, &id, None).unwrap();
        post["account"] = json!("acct_Bob-123456");
        assert!(validate_registration(&post, &id, None).is_err());
    }

    #[test]
    fn reconnect_refuses_changed_device_origin_or_sealed_identity() {
        let id = Identity::new("acct_Alice-1234", DEVICE).unwrap();
        let mut app = AppState { account: id.account.clone(), device: id.device.clone(), hub: "https://hub.example".into(), ..AppState::default() };
        let mut enrolment = hub::Enrolment { hub: app.hub.clone(), device: DEVICE.into(), token: "test".into() };
        validate_enrolment_binding(&app, &id, &enrolment).unwrap();
        enrolment.device = OTHER.into();
        assert!(validate_enrolment_binding(&app, &id, &enrolment).is_err());
        enrolment.device = DEVICE.into();
        enrolment.hub = "https://another-hub.example".into();
        assert!(validate_enrolment_binding(&app, &id, &enrolment).is_err());
        enrolment.hub = app.hub.clone();
        app.account = "acct_Bob-123456".into();
        assert!(validate_enrolment_binding(&app, &id, &enrolment).is_err());
        app.account = id.account.clone();
        app.device = OTHER.into();
        assert!(validate_enrolment_binding(&app, &id, &enrolment).is_err());
    }
}
