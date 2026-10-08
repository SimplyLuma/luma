// SPDX-License-Identifier: MPL-2.0
//! The OpenMLS provider: RustCrypto for the primitives and an in-memory key
//! value store whose whole contents are the snapshot a client seals and keeps.
//!
//! One map rather than a database keeps the state change of one operation
//! atomic: a client writes the snapshot after each operation and replaces the
//! previous file in one rename, so a crash leaves either the state before the
//! operation or the state after it, never half of each.

use openmls_memory_storage::MemoryStorage;
use openmls_rust_crypto::RustCrypto;
use openmls_traits::OpenMlsProvider;
use std::collections::HashMap;

#[derive(Default)]
pub struct LumaProvider {
    crypto: RustCrypto,
    storage: MemoryStorage,
}

impl LumaProvider {
    pub fn from_values(values: HashMap<Vec<u8>, Vec<u8>>) -> Self {
        let provider = LumaProvider::default();
        *provider.storage.values.write().expect("storage lock") = values;
        provider
    }

    pub fn values(&self) -> HashMap<Vec<u8>, Vec<u8>> {
        self.storage.values.read().expect("storage lock").clone()
    }
}

impl OpenMlsProvider for LumaProvider {
    type CryptoProvider = RustCrypto;
    type RandProvider = RustCrypto;
    type StorageProvider = MemoryStorage;

    fn storage(&self) -> &Self::StorageProvider {
        &self.storage
    }

    fn crypto(&self) -> &Self::CryptoProvider {
        &self.crypto
    }

    fn rand(&self) -> &Self::RandProvider {
        &self.crypto
    }
}
