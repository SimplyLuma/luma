// SPDX-License-Identifier: MPL-2.0
use std::fmt;

/// One error type for the whole surface. `code()` is what a client branches
/// on; the text is for logs and never contains key material or plaintext.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    /// Input that is not what it claims to be (bad encoding, wrong wire format).
    Malformed(String),
    /// A group this device does not have.
    UnknownGroup,
    /// The message was sent in an epoch this device no longer has keys for,
    /// or one it never had (it was removed, or joined later).
    WrongEpoch,
    /// This device is not, or is no longer, a member of the group.
    NotMember,
    /// A message from a credential this device refuses (revoked, or not a
    /// Luma identity).
    RefusedSender(String),
    /// The same message was processed before; its keys are gone.
    Replay,
    /// A commit of this device's own is still waiting for the server.
    PendingCommit,
    /// Sealed state or an attachment did not authenticate: wrong key, or the
    /// bytes were changed.
    Decrypt,
    /// Anything OpenMLS reports that is not one of the above.
    Mls(String),
}

pub type Result<T> = std::result::Result<T, Error>;

impl Error {
    pub fn code(&self) -> &'static str {
        match self {
            Error::Malformed(_) => "malformed",
            Error::UnknownGroup => "group_unknown",
            Error::WrongEpoch => "wrong_epoch",
            Error::NotMember => "not_member",
            Error::RefusedSender(_) => "refused_sender",
            Error::Replay => "replay",
            Error::PendingCommit => "pending_commit",
            Error::Decrypt => "decrypt",
            Error::Mls(_) => "mls",
        }
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Malformed(what) => write!(f, "malformed {what}"),
            Error::RefusedSender(why) => write!(f, "refused sender: {why}"),
            Error::Mls(what) => write!(f, "mls: {what}"),
            other => f.write_str(other.code()),
        }
    }
}

impl std::error::Error for Error {}

/// Maps an OpenMLS error's debug text to the cases a client acts on. OpenMLS
/// nests its errors several enums deep; the variant names are stable within
/// the pinned 0.6.0 and the tests pin each mapping.
pub(crate) fn from_mls<E: fmt::Debug>(error: E) -> Error {
    let text = format!("{error:?}");
    if text.contains("WrongEpoch") {
        Error::WrongEpoch
    } else if text.contains("SecretReuseError") || text.contains("SecretTreeError(TooDistantInThePast)") {
        Error::Replay
    } else if text.contains("UseAfterEviction") || text.contains("GroupStateError(UseAfterEviction)") {
        Error::NotMember
    } else if text.contains("PendingCommit") {
        Error::PendingCommit
    } else {
        let mut short = text;
        short.truncate(240);
        Error::Mls(short)
    }
}
