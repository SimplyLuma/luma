// SPDX-License-Identifier: MPL-2.0
//! One device's MLS state: its signing key, its key packages and every group
//! it is in. Everything that changes state changes the provider's map, and
//! `snapshot()` is that map plus the few names needed to reopen it.
//!
//! A commit this device makes stays pending until the client says the server
//! took it (`confirm`) or refused it (`discard`); a commit is never merged on
//! the assumption that it was delivered.

use crate::error::{from_mls, Error, Result};
use crate::identity::Identity;
use crate::provider::LumaProvider;
use crate::CIPHERSUITE;
use openmls::prelude::tls_codec::{Deserialize as _, Serialize as _};
use openmls::prelude::*;
use openmls_basic_credential::SignatureKeyPair;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap};

/// Out-of-order tolerance and epochs kept for late messages (ADR-051 §1).
pub const OUT_OF_ORDER: u32 = 100;
pub const MAX_FORWARD: u32 = 1000;
pub const PAST_EPOCHS: usize = 3;
const SNAPSHOT_VERSION: u32 = 1;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Member {
    pub identity: Identity,
    /// Ed25519 public key, 32 bytes.
    pub signature_key: Vec<u8>,
    pub leaf: u32,
}

#[derive(Debug, Clone)]
pub struct CommitOut {
    pub group: Vec<u8>,
    /// TLS-encoded MLSMessage (a PrivateMessage carrying the commit).
    pub commit: Vec<u8>,
    /// TLS-encoded MLSMessage (Welcome) when the commit adds devices.
    pub welcome: Option<Vec<u8>>,
    /// The epoch the commit names; the group moves to `epoch + 1`.
    pub epoch: u64,
    /// The group's devices once the commit is merged, this one included.
    pub members_after: Vec<Member>,
}

#[derive(Debug, Clone)]
pub enum Incoming {
    Application { group: Vec<u8>, sender: Member, plaintext: Vec<u8>, epoch: u64 },
    Commit { group: Vec<u8>, sender: Option<Member>, added: Vec<Member>, removed: Vec<Member>, removed_self: bool, epoch: u64 },
    /// A proposal, kept for the next commit.
    Proposal { group: Vec<u8> },
}

#[derive(Debug, Clone)]
pub struct Joined {
    pub group: Vec<u8>,
    pub inviter: Member,
    pub members: Vec<Member>,
    pub epoch: u64,
}

#[derive(Serialize, Deserialize)]
struct Snapshot {
    v: u32,
    identity: Identity,
    signature_key: String,
    groups: Vec<String>,
    values: Vec<(String, String)>,
}

pub struct Engine {
    provider: LumaProvider,
    signer: SignatureKeyPair,
    identity: Identity,
    groups: BTreeMap<Vec<u8>, MlsGroup>,
}

fn create_config() -> MlsGroupCreateConfig {
    MlsGroupCreateConfig::builder()
        .ciphersuite(CIPHERSUITE)
        .use_ratchet_tree_extension(true)
        .wire_format_policy(PURE_CIPHERTEXT_WIRE_FORMAT_POLICY)
        .max_past_epochs(PAST_EPOCHS)
        .sender_ratchet_configuration(SenderRatchetConfiguration::new(OUT_OF_ORDER, MAX_FORWARD))
        .build()
}

fn join_config() -> MlsGroupJoinConfig {
    MlsGroupJoinConfig::builder()
        .use_ratchet_tree_extension(true)
        .wire_format_policy(PURE_CIPHERTEXT_WIRE_FORMAT_POLICY)
        .max_past_epochs(PAST_EPOCHS)
        .sender_ratchet_configuration(SenderRatchetConfiguration::new(OUT_OF_ORDER, MAX_FORWARD))
        .build()
}

fn wire(message: &MlsMessageOut) -> Result<Vec<u8>> {
    message.tls_serialize_detached().map_err(|_| Error::Malformed("mls message".into()))
}

fn member_of(credential: &Credential, signature_key: &[u8], leaf: u32) -> Result<Member> {
    let basic = BasicCredential::try_from(credential.clone()).map_err(|_| Error::RefusedSender("not a basic credential".into()))?;
    let identity = Identity::parse(basic.identity()).map_err(|_| Error::RefusedSender("not a Luma identity".into()))?;
    Ok(Member { identity, signature_key: signature_key.to_vec(), leaf })
}

fn members(group: &MlsGroup) -> Vec<Member> {
    group
        .members()
        .filter_map(|m| member_of(&m.credential, &m.signature_key, m.index.u32()).ok())
        .collect()
}

/// A device between making its signing key and learning its account: the
/// delivery service answers the account id only once the public half is
/// registered. The private half never leaves the provider.
pub struct PendingDevice {
    provider: LumaProvider,
    signer: SignatureKeyPair,
}

impl PendingDevice {
    pub fn new() -> Result<Self> {
        let provider = LumaProvider::default();
        let signer = SignatureKeyPair::new(CIPHERSUITE.signature_algorithm()).map_err(from_mls)?;
        signer.store(provider.storage()).map_err(from_mls)?;
        Ok(PendingDevice { provider, signer })
    }

    pub fn signature_key(&self) -> Vec<u8> {
        self.signer.to_public_vec()
    }

    pub fn into_engine(self, account: &str, device: &str) -> Result<Engine> {
        let identity = Identity::new(account, device)?;
        Ok(Engine { provider: self.provider, signer: self.signer, identity, groups: BTreeMap::new() })
    }
}

impl Engine {
    /// A new device: a fresh Ed25519 signing key for `luma1:<account>:<device>`.
    pub fn new(account: &str, device: &str) -> Result<Self> {
        let identity = Identity::new(account, device)?;
        let provider = LumaProvider::default();
        let signer = SignatureKeyPair::new(CIPHERSUITE.signature_algorithm()).map_err(from_mls)?;
        signer.store(provider.storage()).map_err(from_mls)?;
        Ok(Engine { provider, signer, identity, groups: BTreeMap::new() })
    }

    pub fn from_snapshot(bytes: &[u8]) -> Result<Self> {
        let snapshot: Snapshot = serde_json::from_slice(bytes).map_err(|_| Error::Malformed("snapshot".into()))?;
        if snapshot.v != SNAPSHOT_VERSION {
            return Err(Error::Malformed("snapshot version".into()));
        }
        let mut values = HashMap::with_capacity(snapshot.values.len());
        for (key, value) in &snapshot.values {
            values.insert(crate::from_b64u(key)?, crate::from_b64u(value)?);
        }
        let provider = LumaProvider::from_values(values);
        let public = crate::from_b64u(&snapshot.signature_key)?;
        let signer = SignatureKeyPair::read(provider.storage(), &public, CIPHERSUITE.signature_algorithm())
            .ok_or_else(|| Error::Malformed("snapshot has no signing key".into()))?;
        let mut groups = BTreeMap::new();
        for id in &snapshot.groups {
            let raw = crate::from_b64u(id)?;
            let group = MlsGroup::load(provider.storage(), &GroupId::from_slice(&raw))
                .map_err(from_mls)?
                .ok_or_else(|| Error::Malformed("snapshot names a group it does not hold".into()))?;
            groups.insert(raw, group);
        }
        Ok(Engine { provider, signer, identity: snapshot.identity, groups })
    }

    /// Everything needed to reopen this device, as bytes the client seals.
    pub fn snapshot(&self) -> Vec<u8> {
        let mut values: Vec<(String, String)> =
            self.provider.values().iter().map(|(k, v)| (crate::b64u(k), crate::b64u(v))).collect();
        values.sort();
        let snapshot = Snapshot {
            v: SNAPSHOT_VERSION,
            identity: self.identity.clone(),
            signature_key: crate::b64u(self.signer.public()),
            groups: self.groups.keys().map(|g| crate::b64u(g)).collect(),
            values,
        };
        serde_json::to_vec(&snapshot).expect("snapshot serialises")
    }

    pub fn identity(&self) -> &Identity {
        &self.identity
    }

    pub fn signature_key(&self) -> Vec<u8> {
        self.signer.to_public_vec()
    }

    fn credential(&self) -> CredentialWithKey {
        CredentialWithKey {
            credential: BasicCredential::new(self.identity.to_credential().into_bytes()).into(),
            signature_key: self.signer.public().into(),
        }
    }

    fn key_package(&self, last_resort: bool) -> Result<Vec<u8>> {
        let mut builder = KeyPackage::builder();
        if last_resort {
            builder = builder.mark_as_last_resort();
        }
        let bundle = builder.build(CIPHERSUITE, &self.provider, &self.signer, self.credential()).map_err(from_mls)?;
        wire(&MlsMessageOut::from(bundle.key_package().clone()))
    }

    /// One-time key packages for the delivery service (100 kept there).
    pub fn key_packages(&self, count: usize) -> Result<Vec<Vec<u8>>> {
        (0..count).map(|_| self.key_package(false)).collect()
    }

    /// The package handed out when a device has no one-time packages left.
    pub fn last_resort_key_package(&self) -> Result<Vec<u8>> {
        self.key_package(true)
    }

    pub fn groups(&self) -> Vec<Vec<u8>> {
        self.groups.keys().cloned().collect()
    }

    fn group(&self, id: &[u8]) -> Result<&MlsGroup> {
        self.groups.get(id).ok_or(Error::UnknownGroup)
    }

    pub fn members(&self, group: &[u8]) -> Result<Vec<Member>> {
        Ok(members(self.group(group)?))
    }

    pub fn epoch(&self, group: &[u8]) -> Result<u64> {
        Ok(self.group(group)?.epoch().as_u64())
    }

    pub fn is_active(&self, group: &[u8]) -> Result<bool> {
        Ok(self.group(group)?.is_active())
    }

    pub fn has_pending_commit(&self, group: &[u8]) -> Result<bool> {
        Ok(self.group(group)?.pending_commit().is_some())
    }

    /// A new group with only this device in it, under a random 32-byte id.
    pub fn create_group(&mut self) -> Result<Vec<u8>> {
        let mut id = [0u8; 32];
        rand::RngCore::fill_bytes(&mut rand::rngs::OsRng, &mut id);
        let group = MlsGroup::new_with_group_id(&self.provider, &self.signer, &create_config(), GroupId::from_slice(&id), self.credential())
            .map_err(from_mls)?;
        self.groups.insert(id.to_vec(), group);
        Ok(id.to_vec())
    }

    fn validate_key_package(&self, bytes: &[u8]) -> Result<KeyPackage> {
        let message = MlsMessageIn::tls_deserialize_exact(bytes).map_err(|_| Error::Malformed("key package".into()))?;
        let MlsMessageBodyIn::KeyPackage(package) = message.extract() else {
            return Err(Error::Malformed("not a key package".into()));
        };
        let package = package.validate(self.provider.crypto(), ProtocolVersion::Mls10).map_err(from_mls)?;
        if package.ciphersuite() != CIPHERSUITE {
            return Err(Error::Malformed("key package ciphersuite".into()));
        }
        // Only Luma identities join a Luma group.
        member_of(package.leaf_node().credential(), package.leaf_node().signature_key().as_slice(), 0)?;
        Ok(package)
    }

    fn commit_out(&self, group: &[u8], commit: MlsMessageOut, welcome: Option<MlsMessageOut>, epoch: u64) -> Result<CommitOut> {
        let g = self.group(group)?;
        let staged = g.pending_commit().ok_or_else(|| Error::Mls("no pending commit after committing".into()))?;
        // The tree after the commit: current members, minus removals, plus adds.
        let removed: Vec<u32> = staged.remove_proposals().map(|p| p.remove_proposal().removed().u32()).collect();
        let mut after: Vec<Member> = members(g).into_iter().filter(|m| !removed.contains(&m.leaf)).collect();
        for add in staged.add_proposals() {
            let leaf = add.add_proposal().key_package().leaf_node();
            after.push(member_of(leaf.credential(), leaf.signature_key().as_slice(), u32::MAX)?);
        }
        Ok(CommitOut { group: group.to_vec(), commit: wire(&commit)?, welcome: welcome.as_ref().map(wire).transpose()?, epoch, members_after: after })
    }

    /// Add devices by their key packages (as the delivery service hands them
    /// out). The commit stays pending until `confirm` or `discard`.
    pub fn add(&mut self, group: &[u8], key_packages: &[Vec<u8>]) -> Result<CommitOut> {
        if key_packages.is_empty() {
            return Err(Error::Malformed("no key packages".into()));
        }
        let packages = key_packages.iter().map(|b| self.validate_key_package(b)).collect::<Result<Vec<_>>>()?;
        let (provider, signer) = (&self.provider, &self.signer);
        let g = self.groups.get_mut(group).ok_or(Error::UnknownGroup)?;
        let epoch = g.epoch().as_u64();
        let (commit, welcome, _info) = g.add_members(provider, signer, &packages).map_err(from_mls)?;
        self.commit_out(group, commit, Some(welcome), epoch)
    }

    /// Remove devices by identity (a revoked or signed-out device, or every
    /// device of an account leaving a group).
    pub fn remove(&mut self, group: &[u8], identities: &[Identity]) -> Result<CommitOut> {
        let leaves: Vec<LeafNodeIndex> = members(self.group(group)?)
            .into_iter()
            .filter(|m| identities.contains(&m.identity) && m.identity != self.identity)
            .map(|m| LeafNodeIndex::new(m.leaf))
            .collect();
        if leaves.is_empty() {
            return Err(Error::Malformed("none of those devices is in the group".into()));
        }
        let (provider, signer) = (&self.provider, &self.signer);
        let g = self.groups.get_mut(group).ok_or(Error::UnknownGroup)?;
        let epoch = g.epoch().as_u64();
        let (commit, welcome, _info) = g.remove_members(provider, signer, &leaves).map_err(from_mls)?;
        self.commit_out(group, commit, welcome, epoch)
    }

    /// A key update for post-compromise security (ADR-051 §1: daily, or every
    /// 200 messages sent).
    pub fn update(&mut self, group: &[u8]) -> Result<CommitOut> {
        let (provider, signer) = (&self.provider, &self.signer);
        let g = self.groups.get_mut(group).ok_or(Error::UnknownGroup)?;
        let epoch = g.epoch().as_u64();
        let (commit, welcome, _info) = g.self_update(provider, signer, LeafNodeParameters::default()).map_err(from_mls)?;
        self.commit_out(group, commit, welcome, epoch)
    }

    /// The server took this device's pending commit.
    pub fn confirm(&mut self, group: &[u8]) -> Result<()> {
        let provider = &self.provider;
        self.groups.get_mut(group).ok_or(Error::UnknownGroup)?.merge_pending_commit(provider).map_err(from_mls)
    }

    /// The server refused it (another commit won the epoch).
    pub fn discard(&mut self, group: &[u8]) -> Result<()> {
        let provider = &self.provider;
        self.groups.get_mut(group).ok_or(Error::UnknownGroup)?.clear_pending_commit(provider.storage()).map_err(from_mls)
    }

    /// Forget a group entirely (left, removed, or the conversation deleted).
    pub fn forget(&mut self, group: &[u8]) -> Result<()> {
        if let Some(mut g) = self.groups.remove(group) {
            g.delete(self.provider.storage()).map_err(from_mls)?;
        }
        Ok(())
    }

    /// Join from a Welcome. Refuses an inviter that is not a Luma identity.
    pub fn join(&mut self, welcome: &[u8]) -> Result<Joined> {
        let message = MlsMessageIn::tls_deserialize_exact(welcome).map_err(|_| Error::Malformed("welcome".into()))?;
        let MlsMessageBodyIn::Welcome(welcome) = message.extract() else {
            return Err(Error::Malformed("not a welcome".into()));
        };
        let staged = StagedWelcome::new_from_welcome(&self.provider, &join_config(), welcome, None).map_err(|e| {
            let text = format!("{e:?}");
            if text.contains("NoMatchingKeyPackage") { Error::NotMember } else { from_mls(e) }
        })?;
        let sender = staged.welcome_sender().map_err(from_mls)?;
        let inviter = member_of(sender.credential(), sender.signature_key().as_slice(), staged.welcome_sender_index().u32())?;
        let group = staged.into_group(&self.provider).map_err(from_mls)?;
        let id = group.group_id().as_slice().to_vec();
        let joined = Joined { group: id.clone(), inviter, members: members(&group), epoch: group.epoch().as_u64() };
        if let Some(mut old) = self.groups.insert(id, group) {
            // A second Welcome to a group this device was already in replaces it.
            let _ = old.delete(self.provider.storage());
        }
        Ok(joined)
    }

    /// Encrypt an application message in the group's current epoch.
    pub fn encrypt(&mut self, group: &[u8], plaintext: &[u8]) -> Result<(Vec<u8>, u64)> {
        let (provider, signer) = (&self.provider, &self.signer);
        let g = self.groups.get_mut(group).ok_or(Error::UnknownGroup)?;
        if !g.is_active() {
            return Err(Error::NotMember);
        }
        let epoch = g.epoch().as_u64();
        let message = g.create_message(provider, signer, plaintext).map_err(from_mls)?;
        Ok((wire(&message)?, epoch))
    }

    /// The group a protocol message is for, from its clear header.
    pub fn group_of(bytes: &[u8]) -> Result<Vec<u8>> {
        let message = MlsMessageIn::tls_deserialize_exact(bytes).map_err(|_| Error::Malformed("mls message".into()))?;
        let protocol: ProtocolMessage = message.try_into_protocol_message().map_err(|_| Error::Malformed("not a protocol message".into()))?;
        Ok(protocol.group_id().as_slice().to_vec())
    }

    /// Decrypt and apply one message. `accept` sees the authenticated sender
    /// first; a sender it refuses (a revoked device) is dropped without any
    /// change to the group. A commit is merged here: the client persists the
    /// snapshot before acknowledging the message to the server.
    pub fn decrypt(&mut self, bytes: &[u8], accept: &dyn Fn(&Member) -> bool) -> Result<Incoming> {
        let message = MlsMessageIn::tls_deserialize_exact(bytes).map_err(|_| Error::Malformed("mls message".into()))?;
        let protocol: ProtocolMessage = message.try_into_protocol_message().map_err(|_| Error::Malformed("not a protocol message".into()))?;
        let id = protocol.group_id().as_slice().to_vec();
        let provider = &self.provider;
        let g = self.groups.get_mut(&id).ok_or(Error::UnknownGroup)?;
        if !g.is_active() {
            return Err(Error::NotMember);
        }
        let processed = g.process_message(provider, protocol).map_err(from_mls)?;
        let epoch = processed.epoch().as_u64();
        let sender_leaf = match processed.sender() {
            Sender::Member(leaf) => Some(*leaf),
            _ => None,
        };
        let sender_key = sender_leaf.and_then(|leaf| g.members().find(|m| m.index == leaf).map(|m| m.signature_key));
        let sender = match sender_leaf {
            Some(leaf) => Some(member_of(processed.credential(), sender_key.as_deref().unwrap_or(&[]), leaf.u32())?),
            None => None,
        };
        if let Some(sender) = &sender {
            if !accept(sender) {
                return Err(Error::RefusedSender(format!("{} is refused", sender.identity.to_credential())));
            }
        }
        match processed.into_content() {
            ProcessedMessageContent::ApplicationMessage(app) => {
                let sender = sender.ok_or_else(|| Error::RefusedSender("an application message not from a member".into()))?;
                Ok(Incoming::Application { group: id, sender, plaintext: app.into_bytes(), epoch })
            }
            ProcessedMessageContent::StagedCommitMessage(staged) => {
                let removed: Vec<Member> = staged
                    .remove_proposals()
                    .filter_map(|p| {
                        let leaf = p.remove_proposal().removed();
                        g.members().find(|m| m.index == leaf).and_then(|m| member_of(&m.credential, &m.signature_key, leaf.u32()).ok())
                    })
                    .collect();
                let mut added = Vec::new();
                for add in staged.add_proposals() {
                    let leaf = add.add_proposal().key_package().leaf_node();
                    // A commit that adds a non-Luma identity is refused whole.
                    added.push(member_of(leaf.credential(), leaf.signature_key().as_slice(), u32::MAX)?);
                }
                let removed_self = staged.self_removed();
                g.merge_staged_commit(provider, *staged).map_err(from_mls)?;
                Ok(Incoming::Commit { group: id, sender, added, removed, removed_self, epoch: g.epoch().as_u64() })
            }
            ProcessedMessageContent::ProposalMessage(proposal) => {
                g.store_pending_proposal(provider.storage(), *proposal).map_err(from_mls)?;
                Ok(Incoming::Proposal { group: id })
            }
            ProcessedMessageContent::ExternalJoinProposalMessage(_) => Err(Error::Malformed("external joins are not used".into())),
        }
    }
}
