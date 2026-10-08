// SPDX-License-Identifier: MPL-2.0
//! Luma Messages end-to-end encryption (ADR-051).
//!
//! Every device is a leaf and every conversation is an MLS group (RFC 9420,
//! OpenMLS, ciphersuite 0x0003). This crate is the part every Luma client
//! shares: device identity, key packages, groups, encrypt and decrypt,
//! attachment sealing, safety numbers and a state snapshot the client seals
//! and stores where its platform keeps things.
//!
//! The surface is kept to owned bytes, strings, integers and plain records,
//! with one error type, so it can be exported through UniFFI (Android) or
//! wasm-bindgen (web) without an adapter layer. Nothing here opens a file, a
//! socket or a thread; the desktop helper (`src/bin/luma`) adds those.

pub mod attachment;
pub mod engine;
pub mod error;
pub mod identity;
pub mod payload;
pub mod provider;
pub mod safety;
pub mod sealed;

pub use attachment::{open_attachment, seal_attachment, AttachmentKey, SealedAttachment};
pub use engine::{CommitOut, Engine, Incoming, Joined, Member, PendingDevice};
pub use error::{Error, Result};
pub use identity::Identity;
pub use payload::{Attachment, ConversationKind, ConversationMeta, Payload};
pub use safety::{safety_number, SafetyNumber};
pub use sealed::{open as open_sealed, seal, SEALED_MAGIC};

/// MLS_128_DHKEMX25519_CHACHA20POLY1305_SHA256_Ed25519, ADR-051 §1.
pub const CIPHERSUITE: openmls::prelude::Ciphersuite =
    openmls::prelude::Ciphersuite::MLS_128_DHKEMX25519_CHACHA20POLY1305_SHA256_Ed25519;

/// Base64url without padding: how binary values travel in the delivery API.
pub fn b64u(bytes: &[u8]) -> String {
    use base64::Engine as _;
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)
}

pub fn from_b64u(text: &str) -> Result<Vec<u8>> {
    use base64::Engine as _;
    base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(text.trim_end_matches('='))
        .map_err(|_| Error::Malformed("base64url".into()))
}
