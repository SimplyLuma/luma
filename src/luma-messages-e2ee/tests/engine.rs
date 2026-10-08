// SPDX-License-Identifier: MPL-2.0
//! The spike's cases (ADR-051 §1) as tests of the crate, plus what the spike
//! did not cover: state that survives a restart, a racing commit, a refused
//! sender and a group of three accounts.
//!
//! LUMA_MLS_BREAK=keep-revoked leaves the revoked device in the group; the
//! revocation test must then fail. That is how it was seen red.

use luma_mls::{Engine, Error, Identity, Incoming};

fn device(account: &str, n: u8) -> Engine {
    Engine::new(account, &format!("00000000-0000-4000-8000-0000000000{n:02x}")).unwrap()
}

fn text(incoming: Incoming) -> Vec<u8> {
    match incoming {
        Incoming::Application { plaintext, .. } => plaintext,
        other => panic!("expected an application message, got {other:?}"),
    }
}

fn accept_all(_: &luma_mls::Member) -> bool {
    true
}

/// Alice's laptop makes a group with Bob and her own desktop.
fn three() -> (Engine, Engine, Engine, Vec<u8>) {
    let mut laptop = device("alice-account", 1);
    let mut desk = device("alice-account", 2);
    let mut bob = device("bobby-account", 3);
    let group = laptop.create_group().unwrap();
    let kps = vec![bob.key_packages(1).unwrap().remove(0), desk.key_packages(1).unwrap().remove(0)];
    let out = laptop.add(&group, &kps).unwrap();
    assert_eq!(out.epoch, 0);
    assert_eq!(out.members_after.len(), 3);
    laptop.confirm(&group).unwrap();
    let welcome = out.welcome.unwrap();
    let joined = bob.join(&welcome).unwrap();
    assert_eq!(joined.inviter.identity.account, "alice-account");
    assert_eq!(joined.members.len(), 3);
    desk.join(&welcome).unwrap();
    (laptop, desk, bob, group)
}

#[test]
fn messages_reach_every_device_both_ways() {
    let (mut laptop, mut desk, mut bob, group) = three();
    let (m, epoch) = laptop.encrypt(&group, b"hello from alice").unwrap();
    assert_eq!(epoch, 1);
    assert!(!m.windows(5).any(|w| w == b"hello"), "plaintext on the wire");
    assert_eq!(text(bob.decrypt(&m, &accept_all).unwrap()), b"hello from alice");
    assert_eq!(text(desk.decrypt(&m, &accept_all).unwrap()), b"hello from alice");
    let (r, _) = bob.encrypt(&group, b"hi alice").unwrap();
    assert_eq!(text(laptop.decrypt(&r, &accept_all).unwrap()), b"hi alice");
    match desk.decrypt(&r, &accept_all).unwrap() {
        Incoming::Application { sender, .. } => assert_eq!(sender.identity.account, "bobby-account"),
        other => panic!("{other:?}"),
    }
    assert_eq!(Engine::group_of(&r).unwrap(), group);
}

#[test]
fn a_removed_device_cannot_read_what_follows() {
    let (mut laptop, mut desk, mut bob, group) = three();
    let desk_id = desk.identity().clone();
    if std::env::var("LUMA_MLS_BREAK").as_deref() != Ok("keep-revoked") {
        let out = laptop.remove(&group, &[desk_id.clone()]).unwrap();
        assert_eq!(out.members_after.len(), 2);
        laptop.confirm(&group).unwrap();
        match bob.decrypt(&out.commit, &accept_all).unwrap() {
            Incoming::Commit { removed, removed_self, .. } => {
                assert!(!removed_self);
                assert_eq!(removed.len(), 1);
                assert_eq!(removed[0].identity, desk_id);
            }
            other => panic!("{other:?}"),
        }
        match desk.decrypt(&out.commit, &accept_all).unwrap() {
            Incoming::Commit { removed_self, .. } => assert!(removed_self, "the removed device was not told"),
            other => panic!("{other:?}"),
        }
        assert_eq!(bob.members(&group).unwrap().len(), 2);
    }
    let (after, _) = bob.encrypt(&group, b"after revoke").unwrap();
    assert_eq!(text(laptop.decrypt(&after, &accept_all).unwrap()), b"after revoke");
    match desk.decrypt(&after, &accept_all) {
        Ok(Incoming::Application { plaintext, .. }) => panic!("REVOKED DEVICE READ: {:?}", String::from_utf8_lossy(&plaintext)),
        Ok(other) => panic!("unexpected {other:?}"),
        Err(e) => assert!(matches!(e, Error::NotMember | Error::WrongEpoch), "{e:?}"),
    }
}

#[test]
fn state_survives_a_restart_and_an_old_snapshot_cannot_replay() {
    let (mut laptop, _desk, mut bob, group) = three();
    let before = bob.snapshot();
    let (m1, _) = laptop.encrypt(&group, b"one").unwrap();
    assert_eq!(text(bob.decrypt(&m1, &accept_all).unwrap()), b"one");
    let saved = bob.snapshot();
    drop(bob);
    let mut bob = Engine::from_snapshot(&saved).unwrap();
    assert_eq!(bob.identity().account, "bobby-account");
    let (m2, _) = laptop.encrypt(&group, b"two").unwrap();
    assert_eq!(text(bob.decrypt(&m2, &accept_all).unwrap()), b"two", "restored state could not read the next message");
    // The same ciphertext again: its key is gone.
    assert!(bob.decrypt(&m2, &accept_all).is_err(), "a message decrypted twice");
    // A snapshot from before m1 still reads m1 (a crash before the snapshot
    // was written: the queue redelivers and nothing is lost).
    let mut crashed = Engine::from_snapshot(&before).unwrap();
    assert_eq!(text(crashed.decrypt(&m1, &accept_all).unwrap()), b"one");
    // And a reply from the restored device reaches the others.
    let (r, _) = bob.encrypt(&group, b"back").unwrap();
    assert_eq!(text(laptop.decrypt(&r, &accept_all).unwrap()), b"back");
}

#[test]
fn a_losing_commit_is_discarded_and_the_winner_applied() {
    let (mut laptop, mut desk, mut bob, group) = three();
    let win = bob.update(&group).unwrap();
    let lose = laptop.update(&group).unwrap();
    assert_eq!(win.epoch, lose.epoch);
    bob.confirm(&group).unwrap();
    // The server refuses the loser with 409 epoch; it discards and applies the winner.
    laptop.discard(&group).unwrap();
    laptop.decrypt(&win.commit, &accept_all).unwrap();
    desk.decrypt(&win.commit, &accept_all).unwrap();
    let (m, epoch) = laptop.encrypt(&group, b"rebased").unwrap();
    assert_eq!(epoch, 2);
    assert_eq!(text(bob.decrypt(&m, &accept_all).unwrap()), b"rebased");
    assert_eq!(text(desk.decrypt(&m, &accept_all).unwrap()), b"rebased");
}

#[test]
fn a_refused_sender_changes_nothing() {
    let (mut laptop, mut desk, mut bob, group) = three();
    let desk_id = desk.identity().clone();
    let refuse_desk = move |m: &luma_mls::Member| m.identity != desk_id;
    let (m, _) = desk.encrypt(&group, b"from a revoked device").unwrap();
    assert!(matches!(bob.decrypt(&m, &refuse_desk), Err(Error::RefusedSender(_))));
    // A commit from the refused device is not merged either.
    let c = desk.update(&group).unwrap();
    assert!(matches!(bob.decrypt(&c.commit, &refuse_desk), Err(Error::RefusedSender(_))));
    assert_eq!(bob.epoch(&group).unwrap(), 1);
    let (ok, _) = laptop.encrypt(&group, b"still fine").unwrap();
    assert_eq!(text(bob.decrypt(&ok, &refuse_desk).unwrap()), b"still fine");
}

#[test]
fn a_group_of_three_accounts() {
    let (mut laptop, mut desk, mut bob, group) = three();
    let mut carol = device("carol-account", 4);
    let out = bob.add(&group, &carol.key_packages(1).unwrap()).unwrap();
    bob.confirm(&group).unwrap();
    laptop.decrypt(&out.commit, &accept_all).unwrap();
    desk.decrypt(&out.commit, &accept_all).unwrap();
    carol.join(out.welcome.as_ref().unwrap()).unwrap();
    let accounts: std::collections::BTreeSet<String> = carol.members(&group).unwrap().into_iter().map(|m| m.identity.account).collect();
    assert_eq!(accounts.len(), 3);
    let (m, _) = carol.encrypt(&group, b"hi all").unwrap();
    for engine in [&mut laptop, &mut desk, &mut bob] {
        assert_eq!(text(engine.decrypt(&m, &accept_all).unwrap()), b"hi all");
    }
}

#[test]
fn key_packages_carry_the_identity_and_last_resort_marks_itself() {
    let engine = device("alice-account", 1);
    let one = engine.key_packages(2).unwrap();
    assert_eq!(one.len(), 2);
    assert_ne!(one[0], one[1]);
    let identity = engine.identity().to_credential();
    assert!(one[0].windows(identity.len()).any(|w| w == identity.as_bytes()));
    let last = engine.last_resort_key_package().unwrap();
    // The last_resort extension type 0x000a is on the wire in the last-resort
    // package's extensions and nowhere in a one-time package's.
    let marker = [0x00u8, 0x0a, 0x00];
    assert!(last.windows(3).any(|w| w == marker));
    let _ = Identity::parse(identity.as_bytes()).unwrap();
}

#[test]
fn a_welcome_for_someone_else_is_refused() {
    let mut alice = device("alice-account", 1);
    let bob = device("bobby-account", 2);
    let mut eve = device("evesy-account", 3);
    let group = alice.create_group().unwrap();
    let out = alice.add(&group, &bob.key_packages(1).unwrap()).unwrap();
    assert!(matches!(eve.join(out.welcome.as_ref().unwrap()), Err(Error::NotMember)));
}

#[test]
fn a_non_luma_identity_cannot_be_added() {
    use openmls::prelude::{tls_codec::Serialize as _, *};
    use openmls_basic_credential::SignatureKeyPair;
    let mut alice = device("alice-account", 1);
    let provider = openmls_rust_crypto::OpenMlsRustCrypto::default();
    let signer = SignatureKeyPair::new(luma_mls::CIPHERSUITE.signature_algorithm()).unwrap();
    signer.store(openmls_traits::OpenMlsProvider::storage(&provider)).unwrap();
    let cred = CredentialWithKey { credential: BasicCredential::new(b"acct:mallory".to_vec()).into(), signature_key: signer.public().into() };
    let kp = KeyPackage::builder().build(luma_mls::CIPHERSUITE, &provider, &signer, cred).unwrap();
    let bytes = MlsMessageOut::from(kp.key_package().clone()).tls_serialize_detached().unwrap();
    let group = alice.create_group().unwrap();
    assert!(matches!(alice.add(&group, &[bytes]), Err(luma_mls::Error::RefusedSender(_))));
}
