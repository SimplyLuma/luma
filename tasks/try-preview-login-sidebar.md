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

2026-10-28 15:28 UTC: Parent integrated implementation as cf15baaea846fc4779d057e1207b68f3197c0fb8. Native Mac credential UI source candidate PASS, dev-try-login-mac-ui-r1-baf9bbb3.service (8.17s). Exact integrated source archived to /Volumes/A016/Luma-Try-Login-QA-20261028-r1/source; isolated copied GUI application ID io.luma.Tester.LoginQA and private state/cache/log; original signed13 runtime deep signature verified unchanged afterward. Existing user app/VM untouched. Local receipt /srv/dev/accounts/second/artifacts/try-login-mac-ui-r1/PASS.json. Both modes and completed legacy installer selection display credentials; arbitrary installer selection hides them. No full Linux/Windows rebuild performed while GPU runtime is pending, avoiding unnecessary package rounds. Parent owns coordinated final packaging/release.

2026-10-28 15:35 UTC: Coordinated GPU UI candidate prepared but NOT launched: /Volumes/A016/Luma-Try-GPU-UI-Candidate-20261028-r1/Try Luma GPU QA.app. APFS cp -cRp clone of signed13 (independent inodes; no mutable hardlinks), integrated cf15 credential source overlaid into Resources/app/luma_tester, copied GUI/Info identity io.luma.Tester.GPUQA. Launcher exports LUMA_TESTER_HOME=/Volumes/A016/Luma-Try-GPU-UI-Candidate-20261028-r1/state with isolated cache/log. Original runtime/frameworks/native VNC unchanged, original signed13 deep signature verified unchanged. Candidate signature needs renewal AFTER parent replaces GPU runtime and integrated GPU source. Parent owns replacements/ports/start. dev-try-login-gpu-candidate-mac-r2-df63ef24 PASS, external free8.11GB, remote PREPARED.json records details. Earlier local controller quoting failed before any remote mutation and was corrected. No full builds or VM launches performed.

2026-10-28 15:56 UTC: User-requested sidebar polish: removed separate password copy icon and explanatory note. Password value is now the existing LumaUI plain/small TextButton, clickable and keyboard focusable to copy, accessible label and Copy password tooltip. Preview login uses default SidebarSection typography like Snapshots (removed ruled variant's heavier heading). Native GTK clipboard read-back confirms TryLuma copied, keyboard focus works, no icon/note, both modes/legacy classification remain correct. dev-try-password-value-ui-4bfb627a PASS. Source files changed only gui.py and preview_login_ui_smoke.py; parent owns GPU integration.

GPU native QA delegated evidence: dev-try-gpu-native-ui-r6-648bb44e PASS functional checks, actual guest virgl/Accelerated yes, embedded native viewer, actual pointer drag moved green window120x60 guestpixels, Try/Developer credentials, clean shutdown. Functional receipt /srv/dev/accounts/second/artifacts/try-gpu-native-functional-r6-PASS.json. Visual release qualification explicitly FAIL/PENDING: dock main app icons remain blank even with global GSK_RENDERER=gl, while GTK4 GL window draws correctly. Screenshot inspected /srv/dev/accounts/second/artifacts/try-gpu-native-global-gl-r6.png. Reported to parent. Own QA VM shut down; no user VM changes. Native candidate root /Volumes/A016/Luma-Try-GPU-UI-Candidate-20261028-r1 retained. No GPU product code committed from this task.
