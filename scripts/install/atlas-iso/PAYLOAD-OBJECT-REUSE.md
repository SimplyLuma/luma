# Exact payload-object reuse

The measured Oct27 writer processed 153,155 OSTree object nodes in 2,194 seconds
(about 70 nodes per second). Replacing the whole repository reads unchanged
compressed bodies from the host filesystem. A small package delta does not mean
a small repository walk or small storage cost.

`payload-object-reuse.py` provides a conditional mapping path. Existing calls
without explicitly pinned body custody keep the full mapper. Invalid supplied
proof fails closed, rather than enabling unverified reuse. Nothing here declares
media or a release qualified, and no current release used this new path.

## Export-time custody

Set `LUMA_OS_CAPTURE_PAYLOAD_BODIES=1` on either normal signed exporter to capture
`builds/ID/payload-object-bodies.json` as a candidate-cache index immediately
after signing, while the candidate repository is hot and its writer lock is held. Capture runs complete
OSTree verification, then records the compressed SHA256 and size of every object.
The read-only, fsynced index binds the exact source, candidate, architecture,
profile and ref. Its digest goes into the export log; retain that digest in the
authenticated export custody record. Production private keys stay on the normal
signing infrastructure.

For a persistent candidate store, `LUMA_OS_PREVIOUS_BODY_INDEX` and
`LUMA_OS_PREVIOUS_BODY_INDEX_SHA256` can reuse previous protected body records only
when device, inode, size, nanosecond mtime and ctime still match. Changed/new files
are hashed again. This does not replace the complete OSTree verification. The exporter emits a `candidate-cache`
index that can include other
architecture/channel heads without changing them. It is a body-hash cache only
and is refused as direct media proof. `build-staff-media.sh` captures a separate
`OUTPUT.payload-index.json` from its normal selected, signed repository, optionally
reusing unchanged cache inode hashes. That payload-scoped index must match its
actual complete object set exactly; deleting a captured body fails closed. Both
captures retain full OSTree verification, and parent/private configuration is
refused. The export environment and media release sidecar bind the respective
index digest. For manually assembled selected payloads, run the capture CLI
with its default `payload` scope and retain its externally pinned digest.
The caller must budget capture, its index and the
normal verification; running it after archiving onto cold storage defeats the
purpose of capturing while objects and inode metadata are hot.

## Establish the first donor

Pass the payload-scoped index (never the candidate cache) with
`--payload-body-index FILE --payload-body-index-sha256 SHA` to the runtime
reuse helper. Without a donor body receipt it uses the normal full mapper, then
records `OUTPUT.payload-bodies.json`. That receipt checks the actual exact ISO
object and mutable-metadata set, compressed body SHA256 and sizes, permissions,
ownership and directory attributes. Body reads run in physical LBA order directly
from the ISO, avoiding extraction and 150,000 host-file opens. The receipt also
pins the complete ISO SHA256. Unsupported multi-section objects, nonregular
payload entries and missing/extra/mismatched data fail closed.

The normal `build-staff-media.sh` full-builder path also runs
`verify-payload-bodies.py` against its pinned selected index after assembly,
creating `OUTPUT.payload-bodies.json` and binding that digest in its release
sidecar. This establishes a real first donor without enabling object reuse or
changing the existing full media builder.

Normal signature, runtime equality, source, clock, volume ID, EFI and font checks
remain in place. Retain the completed build's custody and qualification evidence
separately: a body receipt alone does not qualify a donor.

## Use a qualified donor

Additionally pass `--donor-payload-receipt FILE
--donor-payload-receipt-sha256 SHA`. The helper authenticates both read-only custody
files and the complete donor ISO; verifies the new signed summary and candidate
in a fresh GPG-verifying OSTree client; and gives a pinned immutable options file
to the existing Lorax wrapper. EFI rebuilding and normal output finalization stay
in place.

Only objects with equal compressed-body SHA256, length, ownership and permissions
are retained. `.filez` names alone never prove byte equality. Detached
`.commitmeta` always maps from the new source, even when its name and body match.
Objects and object directories absent from the exact new set are removed. All
refs, config, summary, signatures and other allowed mutable metadata are replaced,
and retained directory permissions/ownership are updated. Parent dependencies,
private/control files, unexpected heads, path escapes and symlinks are refused.
The options file avoids an unbounded argv.

Every actual final ISO body and directory is checked again, and a new receipt is
written for the next run. The runtime result binds the exact plan, custody hashes,
actual mapping tools and output-body receipt. Read-only sources and normal
pipeline locks remain the operator's responsibility throughout the job.

## Evidence and remaining measurement

Focused tests use real xorriso output/extraction with exact file sets and bytes,
and an activated signed OSTree fixture: export CLI, prior-inode reuse, donor ISO
receipt, pinned mapper, final ISO receipt, summary/commit GPG validation, full
closure pull and fsck. Tests also reject invalid signatures, changed encodings,
stale detached metadata, extra/removed objects, wrong pins, private/parent inputs,
corrupt/missing donor bodies and stale permissions. A 1,000-unchanged-body fixture
checks that mapping opens only the required mutable commitmeta body.

A representative corpus benchmark on the intended storage is still needed before
claiming any nightly duration. ISO output still writes a complete medium and
xorriso still walks nodes. The small functional fixture is not a production speed
measurement or a 30-minute guarantee.
