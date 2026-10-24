# Task: arm-server-qualification

- Status: active; provider preparation, no VM or guest qualification claimed
- Owner: luma-dev-2, delegated agent /root/arm_server_qualification; root is release integration owner
- Branch: agent/1003/arm-server-qualification, based on 0223ebfef
- Worktree: /srv/dev/worktrees/luma-os/arm-server-qualification
- Scope: source-independent, explicitly TCG server installed-app provider, strict preparation/proof closure guards and negative tests. Historical Mac provider and sibling release artifacts are read-only.
- Owned files: scripts/os/lib/server_installed_qualification.py, tests/os/test_server_installed_qualification.py.
- Actual guest/install tests: pending new final signed candidate/ISO and normally admitted VM lifecycle. No VM launched by this task.
- Root approved protocol: private arm-publication-r5/native-shm-mechanism-r1/server-arm-installed-qualification-plan-r1.json.
- Worktree creation automatically claimed task; duplicate `devctl claim` refused existing own record. Other work preserved.

## Implementation and actual validation

- Source-independent explicit provider `server-qemu-tcg-aarch64-ssd/v1` and CLI preparation: scripts/os/lib/server_installed_qualification.py. Independently supplied final build/source/profile/candidate/ISO SHA+bytes and static/checks/27-head/native-header proof hashes are required; no old build IDs or Mac defaults.
- `prepare_fixture` freezes read-only owned input proofs exclusively; `create_fresh_disk` actually creates/examines a standalone 48GiB qcow2 using separately pinned qemu-img; `ServerInputBridge` uses actual local QMP with exact QEMU PID birth/argv and actual frame dimensions; `observe_disk_consumers` scans privileged /proc, exact own ARM cgroup and inactive successful units.
- `validate_server_qualification` checks PREPARED→CREATED→INSTALLED→QUALIFIED→CLOSED, all 11 unique rows/9 categories/3 mapped launchers, signed heads/RPM headers, original driver outcome before evidence transfer, no unsigned overlays, actual EFI graphical candidate boot, full archive body/attrs/XML custody and unchanged source ISO.
- Runner preparer: scripts/os/lib/server_installed_runner.py. It extracts the actual frozen R5 setup/seat/guest assertions/input behavior block byte-for-byte; one reversible patch changes only checker source path to the immutable fixture capsule. New server transport/prelude/finale are separate. Guest deadlines remain unchanged. Historical Mac source/proofs remain untouched.
- Normal tiny admission `arm-server-provider-contract-r1` refused disk1 before launch. Root reviewed zero-GiB growth for these KB-only temp/artifact fixtures within existing plan; normal memory1 admission retained. No policy changed.
- Actual normal `3625f41a`: 33 tests PASS, one syntax warning subsequently fixed.
- Actual normal `987b53ea`: 36 tests PASS, including a real host qemu-img fresh48GiB virtual disk create/info/map/no-backing/zero guest-data and actual local UNIX QMP protocol/key/click translation. No real guest or QEMU VM started.
- Actual normal `f758faf7`: 41 tests PASS, plus actual frozen original R5 host driver extraction/syntax preparation. Intermediate runner SHA84ccfa4b retained.
- Added complete source/archive metadata/body custody checks. Actual `1694209a` failed one synthetic fixture xattr expectation: Fedora supplies a real SELinux xattr, whereas test fixture assumed no attrs. Corrected only test fixture to copy actual source attrs; `00ddd536` 36 tests PASS. Guard retained.
- Latest actual normal `9636a2f4`: 44 tests PASS (39 provider/negative/Qcow/QMP +5 runner adapter). Syntax warnings treated as errors, diff check PASS. Real key hold/click release are separate events. Actual frozen driver preparer output is `/srv/dev/accounts/second/artifacts/dev-arm-server-runner-preparation-r2-9636a2f4/prepared-server-runner.py`, SHA `ee6177842ecc448927b119b7c85713ee12d079c195ab9daeae985235f93b12bd`.
- Original frozen driver SHA `5e9346a1745d60667448e55ac84d474a64145d919d89cf1791efe7049fb23387`; original behavior block SHA `5c51fcf455c64eb714888cd538bec8283c3bbe7fe834988d77b2ec86b16fc80d`; adapted block SHA `2aff8acecb4c9d9db503432c1786345c8179a826a6c62b8b3faf6aae2b44b731`.

## Remaining gate

Root and arm_publication_r5 requested to review concrete interface. No actual server installation, app guest behavior, normal wrapper closure or publication has run under this provider. Final signed rebuilt source/candidate/static-qualified ISO does not yet exist. Root must coordinate normal VM10GiB/MAIN SSD lifecycle after compilation closes. Fresh final Luma ISO installation is supported; optional full-OS disk reuse intentionally not implemented absent exact valid provenance. Actual final shared checker capsule must have reviewed source anchors and manifest supplied independently; historical R5 capsule cannot be relabeled as new source.
