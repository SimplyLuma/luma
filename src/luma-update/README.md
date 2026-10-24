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
per-device graph URL: `graph/aarch64/fairphone-fp6/{channel}.json` or
`graph/aarch64/unihertz-titan2e/{channel}.json`. The kernel device-tree board
must match that policy; a signed graph cannot authorize another device or an
explicitly different commit architecture. Older generic commits without an
architecture metadata key remain accepted.
