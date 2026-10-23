# ARM installer publication and website compatibility

The existing download consumer reads the signed aggregate at
`media/<channel>/latest.json`. Its entries already distinguish `(build_id, arch)`.
It requires ISO and notes addresses to be beside that aggregate: a valid scoped
`media/aarch64/<channel>/` entry alone will not appear on this consumer.

## Initial compatibility bridge

Use `scripts/os/lib/media_aggregate.py` to prepare an **unsigned** aggregate and
alias plan from the actual signed canonical and scoped ARM indexes. Supply the
original JSON/signature pairs, with their signed notes under `notes/` beside each
local index. The helper verifies the maintained update-graph issuer, exact
trusted comments and input identities. It does not sign, upload or qualify an ISO.

Canonical entries are preserved. Public ARM entries receive architecture-distinct
ISO aliases under the canonical prefix, retaining their exact size and hash.
Notes are copied with their original signatures. A shared build ID requires
byte-identical notes; differing notes fail rather than overwrite another CPU's
notes. Private/interim entries receive no aliases. Conflicting identities, public
object collisions and history truncation fail. Keep all original scoped objects.

Preparation requires the normal shared publication lock. Before publication,
the authorized operator must:

1. Materialize every planned ISO, checksum and notes alias and perform normal
   public byte/hash checks. Preserve the existing x86 `*-latest.iso` alias.
2. Seal, sign and verify the prepared aggregate through the normal isolated
   signer. Its trusted comment is exactly
   `luma-media-index channel=<channel> generated_utc=<timestamp>`; the aggregate
   has no architecture suffix. Original scoped signatures remain unchanged.
3. Reacquire the shared canonical publication lock and compare the current
   canonical index's full hash with `canonical_prior_sha256` in the preparation
   receipt. A concurrent publication requires a fresh preparation. Upload the
   new metadata last, then read back its signature and the actual website and
   download routes for both CPUs. The helper's preparation receipt is not a
   publication or alias-verification receipt.

The canonical and scoped input URLs must have the same credential-free HTTPS
origin. `--output` must be a new directory. Run preparation with the normal
private tools containing minisign and Python; no signing key is needed. The
operator must seal its source/control and prepared inputs as usual.

## Every subsequent publication preserves both CPUs

The same helper accepts `--incoming-arch x86_64` with a separately staged signed
x86 candidate at the canonical URL, or the default scoped ARM input. It always
starts from the actual verified canonical aggregate and keeps every existing
identity. Known entries retain their exact canonical annotations and retirement
state after immutable artifact fields agree. Incoming x86 objects already have
canonical addresses; their plan says to verify existing uploaded ISO bytes or
publish the exact signed notes input, rather than perform an object self-copy.

The normal `nightly-media.sh` producer now fetches and verifies its actual remote
predecessor and refuses to discard or change another CPU's entries. A stale local
x86 media tree therefore cannot silently erase ARM downloads. The producer holds
a shared publication lock across the final predecessor check, signing and upload,
and compares the remote predecessor again before changing metadata. Configure
`LUMA_MEDIA_PUBLICATION_LOCK` to the same canonical publication lock in every
architecture/release operator. An operator that already holds it may pass that
open descriptor in `LUMA_MEDIA_PUBLICATION_LOCK_FD`; the script validates the
descriptor's device/inode and acquires its existing lock without reopening it.

The default path is local to a release root; it does not establish a cross-root
lock by itself. Before admitting either operator, resolve both configured lock
paths and compare their device/inode identities. Use the existing canonical
nightly publication lock when the qualified wrapper already holds that lock, and
pass its inherited descriptor into the media producer. Do not acquire the same
lock through a second file description: that can deadlock the wrapper. A
build-specific owner lock is additional protection, not a substitute for the
shared canonical lock. The generic nightly wrapper holds descriptor 4; a protected
publication wrapper may instead hold descriptor 8. Pass whichever descriptor
actually owns the configured inode, rather than relying on a fixed number.
Preserve sealed historical operators; generate and seal
new wrappers with this configuration and the preservation checks before their
next publication. Until both operators are configured and independently checked,
this source change does not establish live publication serialization.

The protected operator must apply `check_other_architectures()` and `check_prior()`
to its freshly verified remote prior and final candidate while holding that same
lock. The guard is also available as `media_aggregate.py check-preservation`.
Old private `preserved_entries` adapters that key only on build ID must use the
shared `(build_id, arch)` helper when both CPUs can share an ID. Do not resume a
legacy x86-only metadata upload after an ARM aggregate has been published.

## Maintained website change to remove aliases

The website owner can retain the current aggregate and eliminate duplicate ISO
objects with a narrow consumer change:

- Validate each entry's architecture before deriving allowed paths. Accept the
  existing canonical prefix or exactly `media/<entry.arch>/<channel>/` on the
  configured trusted origin. Require ISO and notes to use that same prefix.
  Continue rejecting arbitrary URLs and architecture-mismatched scoped paths.
- Fetch notes from that validated prefix. Keep signature verification and the
  exact channel/build-ID trusted comment. Separate scoped addresses allow the
  same build ID to have different notes for each CPU without replacing x86 notes.
- Make the counted download route architecture-aware, or use the validated direct
  scoped address until that route is updated. The existing canonical route must
  not redirect a scoped filename into the wrong directory.
- Pass architecture into the update-availability lookup, select that CPU's signed
  graph address and require its declared architecture to match. A graph for x86
  must not decide whether an ARM build is offered as an update.

Regression checks should cover both CPUs, shared build IDs, scoped notes, rejected
cross-origin/mismatched-CPU entries, and preserved historical x86 links. This is a
source handoff; no website or Hub deployment is performed by the bridge helper.

## Hub channel records are a separate concern

The maintained Hub accepts both CPU identifiers, but its original release-schema
seed creates only x86 channel rows. Hub-rendered ARM graphs require an additive
channel migration; publishing an ISO or ingesting a build does not create those
rows. The initial preview can retain the existing authorized local graph renderer
over published release manifests and the normal isolated graph signer. A download
index update alone must not silently switch graph ownership to Hub.

The older asynchronous image-record producer/consumer also addresses records as
`records/<channel>/<version>/<kind>.json`. Before publishing both CPUs with the
same version through that path, the Hub owner must add architecture to its record
identity while retaining old x86 records. Standalone image database uniqueness
already includes architecture. A distinct initial ARM build/version avoids the
old object-key collision; it is not a permanent architecture model.
