# Task: arm-graph-refresher

- Status: implemented and focused checks passed; source review ready
- Owner: luma-dev-2 / arm_server_qualification delegated agent
- Worktree: /srv/dev/accounts/second/experiments/arm-desktop/task-hdd/arm-server-qualification-admission/arm-graph-refresher
- Branch: agent/1003/arm-graph-refresher
- Base: 71f0f720a (includes architecture/device client changes51b0f7830)

## Scope

Repair approved graph preparation/refresh/signer entry routing for architecture and explicit registered hardware targets. Preserve existing seals, installed-key trust, same-policy and genuine publication evidence checks. Root owns publication, signing infrastructure and other source paths. No live graph, key, approved state, unit, repository or feed mutations are authorized here.

## Initial finding

Refresher passes a purported final graph directory to a signer that appends architecture or device routing, then verifies/uploads the old parent file. Device identity is missing from refresh comment/release-path checks. Plan a shared bucket helper with explicit base-versus-bucket semantics, registered device release namespaces and x86 legacy aliases; test only private fixtures without real signing/publication.

## Result and checks

A shared graph bucket resolver now binds generic architecture and registered device identities throughout preparation, signer entry, signing, refresh readback and refresh unit naming. Refresh passes an unscoped root to the signer and then verifies the actual resulting bucket. Generic x86 refresh verifies and updates its legacy alias; device buckets bind exact hardware identity, trusted comments and release manifest namespaces. The legacy five-positional signer entry remains supported.

Actual admitted unit job `1d823286` passed 8 routing tests and the existing 14 approved-state tests, Python syntax, shell syntax and scoped whitespace checks. Actual admitted root fixture job `44db2c77` passed 7 flow tests, including four architecture/device routing subcases. It executes real sealed preparation, signer shell and refresher logic with explicitly synthetic signing/container/network transports. It proves routing and refusal behavior only; it does not qualify real keys, signing, publication or a nightly. Initial fixture setup/schema failures were preserved and corrected without weakening production guards. No live feed, keys, service units or publication state changed.
