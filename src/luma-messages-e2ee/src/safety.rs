// SPDX-License-Identifier: MPL-2.0
//! Safety numbers (ADR-051 §6): 60 digits and a QR code over both accounts'
//! sorted device signature keys, the same on both sides of a conversation.
//!
//! Each account's half is 30 digits: SHA-512 iterated 5,200 times over a
//! version label, the account id and its sorted keys; the first 30 bytes
//! read as six 40-bit numbers, each mod 100,000. The halves are ordered by
//! account id so both people see one number. A device added to either account
//! changes it.

use crate::error::{Error, Result};
use sha2::{Digest, Sha512};

const VERSION: &[u8] = b"luma-safety-v1\0";
const ITERATIONS: usize = 5200;

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct SafetyNumber {
    /// 60 digits in twelve groups of five, space separated.
    pub digits: String,
    /// What the QR code encodes: `luma-safety:1:<b64u of both 30-byte halves>`.
    pub qr: String,
}

fn half(account: &str, keys: &[Vec<u8>]) -> Result<[u8; 30]> {
    if keys.is_empty() {
        return Err(Error::Malformed("an account with no device keys".into()));
    }
    let mut sorted: Vec<&Vec<u8>> = keys.iter().collect();
    sorted.sort();
    sorted.dedup();
    let mut material = Vec::new();
    for key in &sorted {
        material.extend_from_slice(&(key.len() as u16).to_be_bytes());
        material.extend_from_slice(key);
    }
    let mut hasher = Sha512::new();
    hasher.update(VERSION);
    hasher.update((account.len() as u16).to_be_bytes());
    hasher.update(account.as_bytes());
    hasher.update(&material);
    let mut hash = hasher.finalize();
    for _ in 0..ITERATIONS {
        let mut next = Sha512::new();
        next.update(hash);
        next.update(&material);
        hash = next.finalize();
    }
    let mut out = [0u8; 30];
    out.copy_from_slice(&hash[..30]);
    Ok(out)
}

fn digits(half: &[u8; 30]) -> Vec<String> {
    half.chunks(5)
        .map(|chunk| {
            let value = chunk.iter().fold(0u64, |acc, b| (acc << 8) | u64::from(*b));
            format!("{:05}", value % 100_000)
        })
        .collect()
}

/// The safety number of a conversation between two accounts.
pub fn safety_number(account_a: &str, keys_a: &[Vec<u8>], account_b: &str, keys_b: &[Vec<u8>]) -> Result<SafetyNumber> {
    if account_a == account_b {
        return Err(Error::Malformed("a safety number is between two accounts".into()));
    }
    let (first, second) = if account_a < account_b {
        (half(account_a, keys_a)?, half(account_b, keys_b)?)
    } else {
        (half(account_b, keys_b)?, half(account_a, keys_a)?)
    };
    let mut groups = digits(&first);
    groups.extend(digits(&second));
    let mut both = first.to_vec();
    both.extend_from_slice(&second);
    Ok(SafetyNumber { digits: groups.join(" "), qr: format!("luma-safety:1:{}", crate::b64u(&both)) })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn both_sides_agree_and_a_new_device_changes_it() {
        let alice = vec![vec![1u8; 32], vec![2u8; 32]];
        let bob = vec![vec![3u8; 32]];
        let from_alice = safety_number("alice-account", &alice, "bobby-account", &bob).unwrap();
        let from_bob = safety_number("bobby-account", &bob, "alice-account", &alice.iter().rev().cloned().collect::<Vec<_>>()).unwrap();
        assert_eq!(from_alice, from_bob);
        assert_eq!(from_alice.digits.replace(' ', "").len(), 60);
        assert!(from_alice.digits.replace(' ', "").bytes().all(|b| b.is_ascii_digit()));

        let mut more = bob.clone();
        more.push(vec![4u8; 32]);
        let changed = safety_number("alice-account", &alice, "bobby-account", &more).unwrap();
        assert_ne!(changed.digits, from_alice.digits, "a device added to bob's account left the number unchanged");
        assert_ne!(changed.qr, from_alice.qr);
    }
}
