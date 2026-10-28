# Task: try-preview-login-sidebar

- Status: ready for integration
- Owner: luma-dev-2 / Codex delegated preview_login_fix
- Updated: 2026-10-28 15:26 UTC
- Project: luma-os
- Branch: agent/1003/try-preview-login-sidebar
- Worktree: /srv/dev/worktrees/luma-os/try-preview-login-sidebar
- Parent task/integration owner: codex-luma-tester-20261017 / root Codex
- Scope: preview credentials and first-use configuration for legacy installed managed workspaces and prepared unified images; no GPU work or release publication.

## Result

The sidebar already contained Preview login, but hid it whenever a selected/running workspace lacked `luma-unified-v1`. Existing installer-created managed workspaces predate this protocol and were excluded. Shared `guest_preview.supported` now requires the Luma account plus either unified protocol or the existing exact completed setup receipt and workspace. Both sidebar visibility and graphical startup defaults use this classifier. Arbitrary imported installers do not advertise public credentials. The note mentions unlocking Luma and installing apps. Preview rows remain outside Developer-only disclosure, available in Try and Developer.

First-use configuration now reaches legacy completed installer workspaces too, sets the local TryLuma password and disables idle locking using existing once-only marker; later user passwords/settings are preserved. SSH remains key-only. No running user VM was stopped, restarted or modified during this task.

## Validation

- Full component suite: 147 tests PASS, dev-try-preview-login-tests-r2-4daa7515.service; result /srv/dev/accounts/second/artifacts/dev-try-preview-login-tests-r2-4daa7515/result.json.
- Real bundled GTK/LumaUI runtime with source overlay: preview_login_ui_smoke.py PASS, dev-try-preview-login-native-ui-r2-f5463712.service. Covers no selection and existing installed ISO workspace, Try and Developer; imported nonmanaged installer hidden. Prior run against unchanged packaged source reproduced failure for legacy workspace.
- git diff --check PASS.
- Mac native UI/package not rebuilt here; parent owns coordinated Mac GPU/package integration.

## Handoff

Cherry-pick this task's implementation commit into the parent worktree before coordinated package rebuild. Native UI smoke command uses bundled AppImage52 AppDir/python under Xvfb, TRY_UI_SOURCE pointing to src/luma-tester, GSK_RENDERER=cairo, isolated temp state and memory settings. No jobs remain running.
