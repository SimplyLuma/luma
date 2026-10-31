# Task: native-ci-assist

- Status: targeted fixes and focused checks complete; ready for source integration
- Owner: luma-dev-2 / arm_server_qualification delegated agent
- Worktree: /srv/dev/accounts/second/experiments/arm-desktop/task-hdd/arm-server-qualification-admission/native-ci-assist
- Base: 31696869f

Investigated failed Native desktop UI run 40145550254 and Native desktop components run 40145550177. Both display-config builds stopped before rpmbuild on Git's container checkout ownership guard. All six UI builds stopped before compiling because their toolkit admission retained an older source/run whose input closure differed. Refresh exact pins to the already successful current-source toolkit run 40145550176 and scope the display backend recipe's fallback Git trust to its own checkout. Preserve all source-closure, successful-run, archive and RPM hash checks. No deployments or generated packages belong to this source task.


Actual admitted check `61c86768` passed two ownership/timestamp regression tests that execute the recipe fallback using Git's real different-owner guard, shell/Python syntax, unchanged toolkit source-closure comparison against the newly pinned successful source and whitespace checks. GitHub API confirmed successful run/head and unexpired archive identities/digests; both native checkout logs independently confirmed the exact merge receipt. The toolkit archive trust/admission implementation is unchanged, and full downloaded-archive/RPM verification remains performed by the real UI job. Native component rebuild and UI CI must run on the fixed source; replaying an older failed source cannot pick up this fix. No publication or local toolkit/UI compilation was performed here.
