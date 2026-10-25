# Task: displays-retirement

- Status: source complete; focused policy, root boundary and actual isolated Flatpak checks passed
- Owner: luma-dev-2 / arm_server_qualification delegated agent
- Worktree: /srv/dev/accounts/second/experiments/arm-desktop/task-hdd/arm-server-qualification-admission/displays-retirement
- Base: c7a22ec8f

Retire standalone Displays from active app producer registry, OS Flatpak baseline and public Depot seed, preserving archived recipes, prior metadata, migration and historical role identities. Settings owns display controls. Root owns RPM/compose/native backend and shell changes; this work ships shared system/user retirement policy and unit sources for root's installer RPM integration. Normal uninstall-only Flatpak transactions retain app data, avoid network/prompts/force-stop/runtime cleanup and retry after failures. No release/deployment mutation is authorized in this source task.


Verification: ordinary admitted job `c06693bf` passed policy 5, baseline inventory 3, registry/catalog 4 and deterministic generated seed checks. Its attempted root-only handoff suite refused UID1003 as designed; actual admitted root fixture `7ad83258` subsequently passed all 14 handoff tests without mocking ownership boundaries. Follow-up `4b78ae18` passed the final 6 policy/serialization tests and unit syntax. Actual isolated Flatpak job `a64d0807` installed two local unsigned dummy apps in an explicit private user installation, removed Displays through the production policy, preserved the unrelated deployment and private app-data marker, and verified repeat no-op. It launched no app and touched no default installation. The first fixture export lacked build-finish and was corrected without changing production policy. Both services use Type=exec so target completion does not wait for removal, with a two-minute runtime limit and five-minute failure retry.

Root owns installer RPM service installation/wants links and all native package, compose and shell changes. This commit publishes no application, OS image or feed. The changed shipping selection requires newly generated per-architecture baselines; previous 24-app artifacts are not reused as the new 23-app baseline. Historical recipes, role recognition and migration data are retained.
