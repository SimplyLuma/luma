# Keeping approved OS update graphs fresh

Beta and nightly graphs are signed again every 12 hours, even when no new OS
release is published. The refresh does not build an image, modify application
feeds, or publish an OS payload. Clients can continue checking for updates while
the release team prepares the next version.

The release administrator seals two separate snapshots: publisher control
(`seal-signing-control.py seal-graph`) and approved release inputs (`seal-inputs`).
The approved input contains each channel's exact signed graph, published
manifest hashes, passing fresh/no-account/upgrade/rollback gate hashes and
complete public-delivery/signature evidence. It also pins the qualified tools
image, installed-client public-key hash, pipeline paths and origin destination.
Private key bytes never enter either snapshot. Keep this host configuration and
its evidence outside the source repository.

Pin both the normal local channel repository and its actual public URL. A local
directory named `preview-repo` is a staging implementation detail, not evidence
that people can download a nightly. Each unpaused release's public-delivery
receipt must bind the same unauthenticated repository URL installed clients
use. Confirm beta users opting into nightly can fetch it before admitting that
channel's refresh state.

The initial already-distributed Beta 1 installation baseline is the sole special
case: its existing signed node can be retained while paused. Its incomplete
original gates do not authorize an upgrade or become a passing release.

Make refresh admission part of every normal OS publication. After the new
payload passes its normal release gates and public readback, create and seal
the new approved input from its genuine publication manifests and receipts.
`prepare-approved-graph-state.py` reads those exact files and checks their
gate/delivery bindings. Its host configuration pins the pipeline, public URL,
key identity, storage, image, workspace and publication lock:

```sh
python3 -B "$control/scripts/os/prepare-approved-graph-state.py" \
  "$control" "$control_sha" "$host_config" "$signed_graph_dir" "$inputs" "$channel"
python3 -B "$control/scripts/depot/seal-signing-control.py" \
  seal-inputs "$inputs" "$state"
```

Keep the returned input digest as `state_sha`. The activation check verifies
the graph signature with installed-client trust before any state becomes live.
Use the sealed control's installer with its exact control and input digests:

```sh
python3 -B "$control/scripts/os/install-approved-graph-refresh.py" \
  "$control" "$control_sha" "$state" "$state_sha" "$channel" --apply
```

This replaces the pinned unit, disables the obsolete bootstrap refresh, and
leaves the new timer inactive. Publish the reviewed signed graph while holding
the same root-owned publication lock used by the refresh job. After public
signature and exact-byte readback verification, complete the release with:

```sh
python3 -B "$control/scripts/os/install-approved-graph-refresh.py" \
  "$control" "$control_sha" "$state" "$state_sha" "$channel" --activate
```

Activation performs a normal signing dry run against the actual public policy,
starts one verified refresh, and enables that channel's persistent timer. Both
commands are required publication steps; keep their receipts with the release.
Admit beta and nightly separately so a missing nightly backend cannot affect
the beta timer. A future release replaces its channel's state by repeating this
same sequence rather than editing an existing immutable snapshot.

Before signing and immediately before upload, refresh verifies that the signed
canonical graph still has exactly the approved release policy. It refuses a
changed policy, a replay older than its previous successful refresh, missing
backend commits, changed manifests, failed gates or incomplete delivery proof.
It uploads only that channel's graph and signature, verifies the public bytes
and signature, and writes a receipt. A failed check leaves the served feed
unchanged; investigate the failed service rather than admitting an unsigned
release or silently restoring an older policy.
