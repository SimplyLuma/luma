// SPDX-License-Identifier: MPL-2.0
//! Sealing a state snapshot with a key the platform keeps (the Secret Service
//! on the desktop, the Android keystore, a non-extractable WebCrypto key).
//!
//! `LUMAST1\0` | 24-byte nonce | XChaCha20-Poly1305(key, nonce, snapshot,
//! aad = magic). A wrong key or a changed byte fails to open; there is no
//! unsealed fallback.

use crate::error::{Error, Result};
use chacha20poly1305::aead::{Aead, KeyInit, Payload};
use chacha20poly1305::{XChaCha20Poly1305, XNonce};
use rand::RngCore;

pub const SEALED_MAGIC: &[u8; 8] = b"LUMAST1\0";
pub const KEY_BYTES: usize = 32;

pub fn new_key() -> [u8; KEY_BYTES] {
    let mut key = [0u8; KEY_BYTES];
    rand::rngs::OsRng.fill_bytes(&mut key);
    key
}

pub fn seal(key: &[u8], plaintext: &[u8]) -> Result<Vec<u8>> {
    if key.len() != KEY_BYTES {
        return Err(Error::Malformed("state key".into()));
    }
    let cipher = XChaCha20Poly1305::new_from_slice(key).map_err(|_| Error::Malformed("state key".into()))?;
    let mut nonce = [0u8; 24];
    rand::rngs::OsRng.fill_bytes(&mut nonce);
    let body = cipher
        .encrypt(XNonce::from_slice(&nonce), Payload { msg: plaintext, aad: SEALED_MAGIC })
        .map_err(|_| Error::Decrypt)?;
    let mut out = Vec::with_capacity(8 + 24 + body.len());
    out.extend_from_slice(SEALED_MAGIC);
    out.extend_from_slice(&nonce);
    out.extend_from_slice(&body);
    Ok(out)
}

pub fn open(key: &[u8], sealed: &[u8]) -> Result<Vec<u8>> {
    if key.len() != KEY_BYTES {
        return Err(Error::Malformed("state key".into()));
    }
    if sealed.len() < 8 + 24 + 16 || &sealed[..8] != SEALED_MAGIC {
        return Err(Error::Malformed("sealed state".into()));
    }
    let cipher = XChaCha20Poly1305::new_from_slice(key).map_err(|_| Error::Malformed("state key".into()))?;
    cipher
        .decrypt(XNonce::from_slice(&sealed[8..32]), Payload { msg: &sealed[32..], aad: SEALED_MAGIC })
        .map_err(|_| Error::Decrypt)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn opens_with_its_key_only() {
        let key = new_key();
        let sealed = seal(&key, b"state").unwrap();
        assert_eq!(open(&key, &sealed).unwrap(), b"state");
        assert!(!sealed.windows(5).any(|w| w == b"state"), "plaintext visible in the sealed bytes");
        assert_eq!(open(&new_key(), &sealed), Err(Error::Decrypt));
        let mut changed = sealed.clone();
        *changed.last_mut().unwrap() ^= 1;
        assert_eq!(open(&key, &changed), Err(Error::Decrypt));
        let mut header = sealed;
        header[0] = b'X';
        assert!(matches!(open(&key, &header), Err(Error::Malformed(_))));
    }
}
