# Task: arm-installer-scratch-acl

- Status: verified; ready for integration
- Owner: luma-dev-2, Codex arm_apps_reuse assist
- Project: luma-os
- Branch: agent/1003/arm-installer-scratch-acl
- Base: 5719fbd72233f4d82c47292138b422dff99bd615
- Worktree: /srv/dev/worktrees/luma-os/arm-installer-scratch-acl
- Integration owner: arm-desktop coordinator

## Scope

Create a fresh private Lorax scratch child with inherited SGID and default ACL
removed before runtime creation, preserving shared parents and caches. Audit
machine-id and generated public settings, MIME, dconf and font caches in both
installer archives. Keep intentional private data untouched.

## Current handoff

Source and focused tests are implemented. Existing work in arm-apps-reuse is
preserved. No OS/application rebuild, media rebuild, signing or publication is
part of this assist task. A failed initial canary exposed an unmapped alternate
GID in the rootless test namespace; its portable fixture now retains the actual
SGID/ACL case and uses a mapped alternate group when available.

## Validation

Normal job b5959991 completed successfully on 2026-10-23 at 19:19 UTC. All
14 focused checks passed both unprivileged and in a bounded root unit, including
real named/default ACL and SGID canaries on disk and tmpfs. Shell syntax and
diff checks passed. The retained affected stage2 metadata was rejected for
machine-id and public GSettings, MIME, dconf and font caches; intentional private
data remained outside the public-file checks. Private artifact evidence remains
outside the repository. No installer image was rebuilt or qualified by this task.
