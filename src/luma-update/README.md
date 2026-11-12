# luma-update

Luma's system update agent verifies signed release information and stages
OS deployments through rpm-ostree.

- `luma-updated` — root, sandboxed, D-Bus-activated system service
  `org.projectluma.Update1`. Verifies the minisign-signed update graph for the
  channel this computer follows, selects a release (staged rollouts with local
  wariness, dead-ends, barriers, pauses, security releases, signed
  `rollback_to`), stages that exact OSTree commit through rpm-ostree's D-Bus
  API, and restarts only when a person asks.
- `luma-update` — the command line; `luma-update status --json` is the contract
  Depot reads.
- `luma-update-notifier` — user-session notifications (no resident process).
- `luma-update-boot` and greenboot checks — confirm a new deployment on its
  trial boots and record rollbacks.

The [engine](luma_update/engine.py), [configuration](luma_update/config.py) and
[tests](tests) define the state machine, policy defaults and interface checks.
The [public build guide](../../BUILDING.md) describes release-pipeline inputs.

Run the unit tests with:

```sh
cd src/luma-update && python3 -m unittest discover -s tests -p 'test_*.py'
```


Update streams are architecture-qualified (`graph/{arch}/{channel}.json`).
The signer also maintains the older x86 `graph/{channel}.json` alias. Phone
policies additionally select `hardware_target`, a device ref template and the
per-device graph URL: `graph/aarch64/fairphone-fp6/{channel}.json`. The Titan
2 Elite and the Raspberry Pi 5 follow the generic ARM stream; their kernel and
platform packages come from a device repository. The kernel device-tree board
must match that policy; a signed graph cannot authorize another device or an
explicitly different commit architecture. Older generic commits without an
architecture metadata key remain accepted.

A device with a layered device kit (`/var/lib/luma-device-layer/layered` and
the `/etc/yum.repos.d/ID.repo` luma-device-layer wrote) is also offered
device-package updates when the graph has no newer OS commit
([device_repo.py](luma_update/device_repo.py)): when the repository's
`primary` metadata has a newer EVR of an installed package from the repo
file's `includepkgs`, the check publishes `available` with the booted version
and commit, `last_check_reason` `device-update` and a `Device update: ...`
summary. Staging forces `RefreshMd` and redeploys the booted base commit
through the same path as an OS update (kernel arguments, greenboot,
rollback); the pending change is recognised by its layered deployment
checksum. A rolled-back or unchanged device update is not offered again on
that deployment until the repository has newer builds. Devices without a
device repository read nothing and behave as before.

To publish a device-only OTA, build the newer device packages (a higher
version or release than the installed ones; the kit's `packages`, so the repo
file's `includepkgs` admits them), sign them and `repomd.xml` with the Luma OS
Release key, and publish the repository to its `[device_repo] url` (for
example `https://dl.simplyluma.com/os/devices/aarch64/fairphone-fp6/`). No
graph or OS commit changes; devices offer the update on their next check.
