# Bounded media upload transport

`scripts/os/lib/parallel_multipart.py` is the source-owned transport primitive for
release infrastructure. It does not publish a release, sign a catalog or qualify
an image. The production publisher and its credential files are installed on the
authorized release host separately; this source tree does not contain their
credentials or assume that a source merge deploys a publisher.

A reviewed publisher bridge must explicitly pin the module bytes and preserve its
existing R2 client, signing, public delivery and catalog update checks. Pass:

- The existing client and a factory creating independent clients for the same
  endpoint, bucket and credential files. `call()` must enforce bounded network
  timeouts, raise on failed HTTP responses, and return `(status, headers, body)`.
- The immutable source path and its externally authenticated complete SHA256.
  The module rehashes that source before upload, checks nanosecond file identity
  around each part read, and refuses source changes before completion.
- A private, durable state directory owned by the publisher identity. It holds
  upload IDs and ETags and must not be public or checked into Git.
- A 32 MiB part size and at most four workers, budgeted alongside the publisher
  and any native processes. Each worker uses its own file handle and client.

The bridge calls `multipart_upload(client, key, path, 32 * 1024**2,
content_type, client_factory=make_client, expected_sha256=authenticated_sha,
state_dir=private_state, workers=4)`. Exceptions fail the publication attempt;
they must not advance signed listings. Accepted parts are atomically checkpointed
by the coordinator, and a retry skips only those accepted parts for the same key,
source digest and part layout. Old v1 checkpoints remain intact and require their
original uploader; they are never silently promoted to authenticated v2 state.
Do not delete failed checkpoints to make a retry appear clean.

After upload, the normal publisher must perform its full HTTPS GET and compare
the entire remote body with the authenticated SHA256 before signing or advancing
media listings. Preserve immutable-name protections, catalog signature checks,
compare-and-swap updates, existing architecture/device entries and all release
qualification requirements. A successful multipart response is not public-byte
readback. A transport checkpoint is not a release receipt.

If the server completes an upload but its completion response is lost, a later
attempt can find a stale upload ID. Reconcile the actual remote object's complete
bytes with the authenticated digest through the normal publisher before retiring
that checkpoint; do not assume a missing upload ID proves success or restart a
published immutable name blindly. This helper does not automate that ambiguous
remote-completion recovery.

Focused tests cover concurrent out-of-order parts, exact reconstructed bodies,
ordered completion, accepted-part resume, source/layout changes, old checkpoint
refusal, failed atomic checkpoints, source mutation and HTTP-200 XML completion
errors. A representative production upload and end-to-end timing measurement
remain necessary before claiming a speedup or a fixed nightly duration.
