// SPDX-License-Identifier: MPL-2.0
//! Attachments (ADR-051 §3): AES-256-GCM with a fresh key per file. The
//! server stores `nonce | ciphertext` under a random id; the key, the SHA-256
//! of those stored bytes, the type and the size travel inside the MLS message.

use crate::error::{Error, Result};
use aes_gcm::aead::{Aead, KeyInit};
use aes_gcm::{Aes256Gcm, Nonce};
use rand::RngCore;
use sha2::{Digest, Sha256};

/// 25 MiB, the delivery service's limit per file.
pub const MAX_ATTACHMENT_BYTES: usize = 25 * 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct AttachmentKey {
    /// 32 bytes, base64url.
    pub key: String,
    /// SHA-256 of the stored (encrypted) bytes, hex.
    pub sha256: String,
    /// Plaintext size in bytes.
    pub size: u64,
}

pub struct SealedAttachment {
    pub blob: Vec<u8>,
    pub key: AttachmentKey,
}

pub fn seal_attachment(plaintext: &[u8]) -> Result<SealedAttachment> {
    if plaintext.len() > MAX_ATTACHMENT_BYTES {
        return Err(Error::Malformed("attachment larger than 25 MiB".into()));
    }
    let mut key = [0u8; 32];
    let mut nonce = [0u8; 12];
    rand::rngs::OsRng.fill_bytes(&mut key);
    rand::rngs::OsRng.fill_bytes(&mut nonce);
    let cipher = Aes256Gcm::new_from_slice(&key).map_err(|_| Error::Malformed("key".into()))?;
    let body = cipher.encrypt(Nonce::from_slice(&nonce), plaintext).map_err(|_| Error::Decrypt)?;
    let mut blob = Vec::with_capacity(12 + body.len());
    blob.extend_from_slice(&nonce);
    blob.extend_from_slice(&body);
    let sha256 = hex(&Sha256::digest(&blob));
    let key = AttachmentKey { key: crate::b64u(&key), sha256, size: plaintext.len() as u64 };
    Ok(SealedAttachment { blob, key })
}

pub fn open_attachment(blob: &[u8], key: &AttachmentKey) -> Result<Vec<u8>> {
    if hex(&Sha256::digest(blob)) != key.sha256 {
        return Err(Error::Decrypt);
    }
    if blob.len() < 12 + 16 {
        return Err(Error::Malformed("attachment".into()));
    }
    let raw = crate::from_b64u(&key.key)?;
    let cipher = Aes256Gcm::new_from_slice(&raw).map_err(|_| Error::Malformed("attachment key".into()))?;
    let plaintext = cipher.decrypt(Nonce::from_slice(&blob[..12]), &blob[12..]).map_err(|_| Error::Decrypt)?;
    if plaintext.len() as u64 != key.size {
        return Err(Error::Decrypt);
    }
    Ok(plaintext)
}

pub(crate) fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip_and_refusals() {
        let photo = b"\x89PNG\r\n\x1a\n pretend pixels".repeat(100);
        let sealed = seal_attachment(&photo).unwrap();
        assert!(!sealed.blob.windows(8).any(|w| w == b"\x89PNG\r\n\x1a\n"), "plaintext in the blob");
        assert_eq!(open_attachment(&sealed.blob, &sealed.key).unwrap(), photo);

        let mut changed = sealed.blob.clone();
        changed[20] ^= 1;
        assert_eq!(open_attachment(&changed, &sealed.key), Err(Error::Decrypt));

        let other = seal_attachment(&photo).unwrap();
        assert_ne!(other.key.key, sealed.key.key, "a key was reused");
        let wrong = AttachmentKey { key: other.key.key.clone(), ..sealed.key.clone() };
        assert_eq!(open_attachment(&sealed.blob, &wrong), Err(Error::Decrypt));
    }
}
