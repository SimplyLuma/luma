# October 27 targeted desktop nightly

Owner: luma-dev-2, root release integration agent.
Worktree: `/srv/dev/worktrees/luma-os/nightly-20261027-polish`.
Branch: `agent/1003/nightly-20261027-polish`.

User authorized a prompt x86_64 and ARM64 desktop nightly containing PRs 76,
77 and 78 for the Tester launch. Exclude PR79 installer behavior. Start from
the qualified shared source `6863ccf3dd549a4d555cb14f8e9e1e8d77249561`.
Preserve the published October 26 releases and their actual evidence.

Root owns source integration and release scope. Existing mobile_oct26 agent
owns x86 packaging/release and common installer-runtime reuse; arm_apps_reuse
owns the changed native toolkit and signed application baseline; the
arm_failures_oct26 agent owns the native M4 worker and ARM compose path.

Reuse requires exact unchanged input hashes and explicit evidence provenance.
Changed Shell, native frame/platform, sandbox metadata and icons require
focused component and packaged desktop checks. Check real candidate boot,
previous-release update, user data and manual rollback. Keep signatures,
architecture separation and current public readbacks. Record reused versus
new results explicitly; do not rewrite prior failed results or assert unrun
full qualification. The release scope excludes phones and device kernels.

2026-10-27: source integration started. Native Mac tunnel reconnect requested;
server builds and authenticated native toolkit artifact intake progress
independently. Remote fetch hit an unrelated invalid local Codex capture ref;
do not delete another owner's refs. GitHub API confirms source PR identities.

2026-10-27 validation: merged exactly PR76/77/78 deltas. The package closure
uses Shell125, Platform/Sdk118 and Icons42, with installer76 retained. Genuine
native CI artifacts are admitted against their actual producer revisions and
consumed-source equality; no architecture is relabeled. Cherry-picked the
already-qualified actual-UEFI-extent check and disposable public-keyring fixture
readability corrections. Renewed only the eight exact Fedora SELinux audit
allowances from the retained October26 automated review through November3;
its file digest matches that review exactly. This changes qualification input,
not SELinux policy or the installed payload.

Focused job5616a8eb passed Shell veil2, sandbox permission26 and source89 tests;
the repository command then stopped on missing jsonschema. Rerun7cb591be with
CI-pinned jsonschema4.23.0 passed source89, device13+22 and both device records.
The failed dependency attempt remains retained. User explicitly approved only
the22 bundled-app ShellState1 additions after reviewing that the interface
also permits desktop preference changes. Actual immutable export diffs must
match this scope before publication.
