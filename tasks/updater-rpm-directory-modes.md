# Task: updater-rpm-directory-modes

- Owner: luma-dev-2, Codex /root/native_browser_environment
- Status: active; root owns integration and compose lifecycle.
- Worktree: /srv/dev/worktrees/luma-os/updater-rpm-directory-modes
- Branch: agent/1003/updater-rpm-directory-modes, based on fd68bd1c7.
- Scope: fix updater and remote RPM directory metadata only; release24 and pins.

Actual release23 updater headers contain unintended 042750 shared greenboot,
/etc/luma and /usr/lib/luma directories, plus restrictive generated directories.
The source spec did not define directory metadata. The private shared build
volume has inherited default ACLs and setgid; production source is unchanged.

Release24 explicitly defines all owned directories and default directories as
0755 root/root. The install stage removes inherited payload ACLs/setgid and
sets the generated tmpfiles configuration to0644. Mirrorlist remains0600.
The maintained package builder checks every actual binary RPM directory header,
including implicit Python/doc/license paths. Both package pins advance together.

Build, provenance and real greenboot RPM transaction checks pending. No source
pool, old failed compose or other packages changed.

## 2026-10-26 UTC — ready for integration

Actual `dev-updater24-frozen-build-transaction-ee9421ea` exited0. It used retained
provisioned Fedora image75707475 through the maintained existing-builder path,
a fully loaded frozen build script, own fresh HDD output and a disposable
rootless build container. No production runtime sandbox was changed. The normal
RPM %check ran347 tests successfully with8 existing skips. Actual binary RPM
headers show all19 directory entries0755 root/root, including generated Python,
doc and license directories; the mirrorlist remains0600.

Fedora44 DNF downloaded greenboot-0.16.4-0.fc44.x86_64. Normal rpmkeys verification
reported signature fingerprint36F612DCF27F7D1A48A835E4DBFCF71C6D9F90A6 and both
SHA256 digests OK. The old release23 reproduced exactly five shared-directory
conflicts. Release24 passed both RPM --test and actual installation of updater,
remote and greenboot in a fresh isolated RPM root. This focused file-coexistence
check uses --nodeps/--noscripts/--notriggers; full image dependency/scriptlet
qualification remains the integration owner's compose gate. No host RPMDB was
used. The empty isolated RPMDB has no imported Fedora key and emitted NOKEY;
the same greenboot body had already passed normal trusted-key verification.

Actual `dev-updater24-final-source-provenance-4da70b21` exited0. All three RPM
bodies have full independent SHA256 readback. SRPM spec matches the final spec;
all90 Source0 files match maintained source; all27 installed Python modules,
update.conf, remote configuration, mirrorlist, graph key and dearmored OS key
match source. Shipping updater code is unchanged against fd68bd1c7.

Final packages: build/packages/luma-update under this worktree's private HDD
build symlink. Detailed proof: second-account artifacts for the two successful
units above; source/payload details in INSPECTED.json. Root owns collection,
integration and the next compose. No canonical package pool was modified.

Earlier attempts are not claimed successful: initial ordinary job type blocked
newuidmap; r2 completed rpmbuild then an existing early-exit grep pipeline caused
SIGPIPE141. r3 completed the package/verifier but editing its running shell file
shifted Bash's read offset and caused EOF; the original trap discarded bodies.
The final builder consumes query output fully and retains failed workdirs.
The retained provisioned image enabled the final small package reproduction.
