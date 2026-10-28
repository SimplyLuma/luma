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

2026-10-28 16:03 UTC: Latest native Mac password-value click/clipboard read-back and keyboard focus PASS, dev-try-password-click-mac-r2-9fb686bb.service. Exact integrated source4af269d5018c438383c119f4f9d1f23478b4f6c5 GUI and updated test copied to isolated LoginQA source. Native test initializes actual appkit platform boot, verifies no copy icon/note, normal heading, both modes/legacy visibility, actual native clipboard TryLuma and keyboard focus. No VM started. Copied latest GUI into isolated GPUQA candidate and ad-hoc signed/deep-verified candidate; GPU core/runtime untouched. Receipt /srv/dev/accounts/second/artifacts/try-password-click-mac-r2/PASS.json; remote /Volumes/A016/Luma-Try-Login-QA-20261028-r1/PASSWORD-CLICK-PASS.json. Linux/Windows0.7.5 candidate build/validation completed in separate task try-sidebar-platform-builds with pinned source4af269 and notescommitb296c4a90. Parent owns remaining native GPU texture issue and publication; no builds/QA remain active here.

2026-10-28 16:26 UTC: User's final spacing refinements implemented through shared LumaUI APIs: SidebarSection(divider=True) uses the existing action-heading border/spacing and normal typography (no heavy ruled heading); TextButton(inline=True) is a plain text action without horizontal padding. Preview login uses both. Native Linux UI measurement/screenshot PASS cd6b56a6: Preview→Username12px equals Presets→No presets12px; username/password text right edge216px each; actual clipboard readback/focus stillPASS. Screenshot inspected at /srv/dev/accounts/second/artifacts/dev-try-sidebar-spacing-native-ui-r6-cd6b56a6/sidebar-spacing.png. Native test waits allocated wide viewport/adaptive sidebar instead of asserting focus on an unmapped collapsed sidebar.

Icon audit: installed/mountedpublic/signed075/QA ICNSallidentical9b0cc44454ce101663b7ed43f312fafa8a85539b5555951a1bcc892dfb824c9c; approvedSVGalle77a25f74e4069179491d1f50584d704c5d4ec5969abc948380a08cf215195b2. Extracted1024PNG inspected: approvedorangeplaytile withleftnudgedplay. QAglobalidentityrewrite incorrectlychangedGTKiconlookup; correctedonlyQAcanonicallookup, appidentityremainsisolated. Productsourceiconunchanged. SameownoverlaysoftwareA/B bfde3f91 PASS: allguestdockiconspresentinsoftware, confirmsMetalGLESblankdockwasGPUissue. ParentCGLGPUworkindependent.

Next: parentcherry-pickcode, propagate BOTH changed kitPython files andbase/rowsCSS into allbundles; oldLinuxwrapper onlyreplacedwidgets.py, WindowscurrentlycopiesfreshCSSbutonlywidgets.pyfromPythonkit. MacnativeQA+latestCGLruntimeintegrationrequestedbyparent, pending. Existing0.7.5draft426896575 isheld/unpublished andrequiresrebuildafterthissourcechange.
