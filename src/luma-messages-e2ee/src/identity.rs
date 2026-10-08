// SPDX-License-Identifier: MPL-2.0
//! `luma1:<account>:<device>`: the MLS BasicCredential identity (ADR-051 §1,
//! delivery API "Names"). The account is the permanent account id, never a
//! username; the device is the Hub device id.

use crate::error::{Error, Result};

#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord, serde::Serialize, serde::Deserialize)]
pub struct Identity {
    pub account: String,
    pub device: String,
}

fn account_ok(value: &str) -> bool {
    (8..=200).contains(&value.len()) && value.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

fn device_ok(value: &str) -> bool {
    // A lower-case UUID, 36 characters.
    value.len() == 36
        && value.bytes().enumerate().all(|(i, b)| match i {
            8 | 13 | 18 | 23 => b == b'-',
            _ => b.is_ascii_digit() || (b'a'..=b'f').contains(&b),
        })
}

impl Identity {
    pub fn new(account: &str, device: &str) -> Result<Self> {
        if !account_ok(account) {
            return Err(Error::Malformed("account id".into()));
        }
        if !device_ok(device) {
            return Err(Error::Malformed("device id".into()));
        }
        Ok(Identity { account: account.to_owned(), device: device.to_owned() })
    }

    pub fn parse(bytes: &[u8]) -> Result<Self> {
        let text = std::str::from_utf8(bytes).map_err(|_| Error::Malformed("identity".into()))?;
        let rest = text.strip_prefix("luma1:").ok_or_else(|| Error::Malformed("identity".into()))?;
        let (account, device) = rest.rsplit_once(':').ok_or_else(|| Error::Malformed("identity".into()))?;
        Identity::new(account, device)
    }

    pub fn to_credential(&self) -> String {
        format!("luma1:{}:{}", self.account, self.device)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const DEVICE: &str = "0b7e3a52-8c1d-4f6e-9a20-3d5c7b1e9f04";

    #[test]
    fn round_trip() {
        let id = Identity::new("acct_Alice-1234", DEVICE).unwrap();
        assert_eq!(id.to_credential(), format!("luma1:acct_Alice-1234:{DEVICE}"));
        assert_eq!(Identity::parse(id.to_credential().as_bytes()).unwrap(), id);
    }

    #[test]
    fn refuses_what_is_not_a_luma_identity() {
        for bad in [
            "acct:alice/dev:laptop".to_string(),
            format!("luma2:acct_alice1:{DEVICE}"),
            format!("luma1:short:{DEVICE}"),
            "luma1:acct_alice1:0B7E3A52-8C1D-4F6E-9A20-3D5C7B1E9F04".into(),
            "luma1:acct_alice1:not-a-uuid".into(),
            format!("luma1:acct alice1:{DEVICE}"),
        ] {
            assert!(Identity::parse(bad.as_bytes()).is_err(), "accepted {bad}");
        }
    }
}
