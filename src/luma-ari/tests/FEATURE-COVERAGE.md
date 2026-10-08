# Ari v70 feature coverage

This inventory maps all 36 desktop scenario states to their native surfaces and
existing runtime interfaces. All 36 also appear in `phone_states`; the fixture
parity test rejects desktop-only additions. See [PHONE-COVERAGE.md](PHONE-COVERAGE.md)
for the approval-working drawer interaction and captured assertions. Four fresh
conform PASS reports are still required; this document does not claim them.

| Surface and scenario states | Composition and behavior | Native interface |
| --- | --- | --- |
| Conversation: budget, tips, shots, quiet, ram, new, busy, declined, approved, undo, approval-working | App-owned welcome, user chip, unboxed reply, route/time metadata and receipts; kit ActionCenter/BarEntry, Card, ParagraphField, controls and ToastHost. Suggestions submit their selected prompt; approval drafts are read-only previews with a separate Edit action. Send, stop, approval, decline, timed Undo/Keep and working spinners preserve their states across navigation. | ListConversations, GetConversation, Ask, Stop, Approve, Undo, KeepStep |
| Activity: activity-all, activity-files, activity-people, activity-apps, activity-settings | Kit modes and scroll surfaces; Ari event rows and source links with owning-app content. Filters retain the event inventory; source actions open their associated conversation. | Activity, GetConversation |
| Catalog: models-all, models-chat, models-code, models-images, models-reasoning, models-search, models-empty, model-downloading, model-local, model-cloud | Kit search/modes and controls; Ari hero, local rows, capability chips, fit information and official provider marks. Filter/search, model selection, progress and retry use their existing runtime paths. Hero rearranges at narrow widths. | Models, Cloud, SetActiveModel, SetCloudModel, InstallModel, Release |
| Chat search: chats-search, chats-empty | Kit sidebar search and navigation; Ari chat rows and empty results. Selecting a chat or New chat closes Places at phone width. | ListConversations, GetConversation |
| Routing: model-picker | Kit FloatingMenu, phone MenuDrawer and terminal AddRows. Selecting a route waits for backend success before updating the composer and heading. | SetBrain |
| Provider sheet: providers, provider-key, provider-own, provider-connected, provider-selection, provider-checking, provider-error | Kit LayerHost, TextField and controls; Ari provider choices, status and result rows. Password visibility, checking, result selection, retry and clearing on close retain the approved behavior. Disabled Connect sensitivity follows v70. | Existing OpenRouter Cloud/SetCloudKey/SetCloudModel; other account paths remain isolated fixture coverage until their live provider/account integration is qualified. |

## Runtime and data boundaries

Required unresolved runtime contracts: composer Attach and approval Edit keep
their visible v70 controls and exact isolated prototype feedback. Production
attachment handoff and pending-change editing have no shipped Ari1 method or
established Mail/Pick provider contract. Live Edit reports its unavailable state
rather than claiming an editor opened; generic approvals are Change previews,
not email drafts. This is missing runtime behavior, not an accepted deviation or
complete feature claim. Coordinator request:
coordinator-inbox/20260926-ari-runtime-attachment-and-edit-contract.md.

The exact seven fixture data sets remain read-only; simulated approvals,
downloads and provider checks change isolated memory only. Fixture windows never
connect LiveDaemon. Real async reads and writes use generation/request guards;
late replies cannot replace a different conversation or draft. Reply metadata
records the actual route and elapsed time. Concurrent model downloads retain
separate request identifiers and replay early signals without duplicate starts.

Config changes read the latest document under a lock and change only the edited
fields, with one-time private backups and atomic replacement. Existing SQLite
stores receive a one-time online backup before schema setup. New native capacity
reads inspect host memory counters, existing filesystem free space and DMI name
without creating directories or estimating individual model allocations. Missing
measurements stay unknown; no benchmark or allocation is fabricated. Additional
provider accounts remain Nick's documented section 5 decision in MORNING-DECISIONS.

## Verification and remaining shared dependencies

The draft checkpoint762844bc20bff6b37553c88367a101dc0a8018cb is published.
The daytime tooltip and lifecycle follow-up has runtime SHA256
7c7212bbd5df20c2e83d8bcf35fd62ab1086bef74938bb838047a9554f674366.
Fresh guarded build and full package-layout check PASS, including all54 tests
and the mandatory AppStream validator. Staged private Wayland runtime passes
all27 unchanged tests in actual normal and high contrast, including all36
desktop/36 phone compositions, real390px Places navigation, pending-approval
receipt bounds and complete draft allocation at1180/390/360. Full logs are
reports/ari-daytime-20260926/package-after-paint.log and
reports/ari-daytime-20260926/native-private-wayland-listen.log under LumaDesign.
The identity test owns production and preview IDs on a private bus and passed
the Photos-style wrong-hook negative control, preview activation isolation,
closure and production ID preservation. Navigation focus lifecycle correction
passed. The scenario-derived native composition check passed all 36 states at
desktop and phone widths, with a fresh fixture per state and real picker/sheet
overlays. The two fresh native logs contain no GTK criticals. Ari releases sheet
focus before removing/replacing fields and disconnects its fixture after-paint
callback on close. Shared391-vs390 toolbar warnings remain unresolved; passing
assertions do not establish conform PASS. Partial Undo uses BarAction.tooltip
with the exact v70 condition and explanation that email cannot be unsent.
Tests, scenarios, assertions and timing budgets are unchanged. Earlier timer,
frame/map, broker-resource and bare-X11 failures remain retained as failed
diagnostics; none supplies native acceptance or an exception.
The real-data preview remains uninstalled until the full gate passes.

The isolated daytime follow-up makes the ParagraphField draft read-only,
matching arChain's preview paragraph and separate Edit action. Its native test
retains all draft text/allocation assertions and now rejects an editable preview
or insertion cursor. Two added callback regressions verify that late approval,
Undo/Keep, route selection, download and provider results cannot render or
repopulate a closed window. Fresh guarded build and full54-test package check
pass on runtime SHA256
768bf62531bc79ad6ba1608a83cf612a83542970199d2d0014a64fd44b9be7a7.
All29 native tests pass in actual normal and high contrast; full logs are
followup-package.log and followup-native.log in the same evidence directory.
The broader follow-up log has one normal and two HC GTK ancestor criticals
during scenario composition, plus shared toolbar warnings. These are retained
and traced, not suppressed, waived or claimed as a clean runtime. The original
c88ec6c8 server submission is preserved; this follow-up is not gate acceptance.

The later follow-up also makes live picker labels truthful when measured speed
and memory are unavailable; sample Quick/Slower labels remain fixture-only.
Picker closure restores anchor focus through the kit, so window teardown clears
focus again after closing its picker and sheet. The retained before-fix native
regression failed with a focused GtkLabel and passes after the correction.
Full110804 image review found fallback draft vertical expansion obscuring its
separate actions. Ari now requests natural vertical allocation and repeats chat
scrolling once after layout paints; that frame callback is disconnected on close.
The complete draft text is retained. New1180/390/360 native action bounds pass.
Final runtime SHA256 bedb2cd857d60a5d7d0027bfd41b562aad28d9ab659a5f34bafe6fec47f8bb15;
native test SHA256 c5cbad1e4f141ef6dafb8551ccd517c8172d193978995ada84fe732710e597e9.
Fresh guarded build, all54 package tests including actual AppStream validation,
and full31 native tests in normal49.685s/HC42.760s completed0. Logs:
followup-layout-{build,package,native}.log in the same evidence directory.
Normal has no GTK criticals; HC retains one unsuppressed ancestor critical in
scenario composition. This is unresolved, not a baseline exception or clean
runtime claim. Earlier failures/logs remain. All36 desktop/36 phone states and
all original assertions/time budgets remain intact. Four110804 reports FAIL;
all144 side images/inventories reviewed in overseer/ari-110804-complete-review.md.
No production DONE or real-data preview installation/launch.

Later native teardown disconnects the real proxy signal and releases window
callbacks; a connection finishing after closure cannot restore them. Live
generic Edit no longer falsely claims Charlie opened, and its field says Change
preview. The exact fixture callback stays intact. Full115548 image review found
padded-end overscroll: Ari now reveals actual latest content above the composer
on paint frames until placement settles, disconnecting its callback on close.
New bounds also enforce actions above the composer and preserve headings when
content fits; an earlier one-frame attempt failed and remains in the evidence.
Final runtime SHA25634dd356139ea3e8f8429a9c7ec03983d746569afc351051e15e9e7b24da7df5e;
native test SHA256e381d7fbf5cc2e34a11886926bb13b5797c57186a451e47ae533c9674217cca0.
Guarded build/full54 package including actual AppStream completed0. Full34
native assertions passed normal36.897s/HC32.964s, with zero normal/three HC
unsuppressed Gtk ancestor criticals. Runtime acceptance remains unresolved.
Logs: followup-scroll-{build,native,package}.log; focused9 normal/HC passed in
followup-scroll-settled.log. Earlier failures are preserved, not waived.
115548 allfour reports FAIL at measured0d258b80, actual gate exit1; all144
side/inventory pairs reviewed in overseer/ari-115548-complete-review.md. Later
source is not canonical acceptance. All36/36 states and thresholds remain.

Independent package follow-up explicitly closes its temporary SQLite connection
and mock HTTPError, including on assertion failure. Full54 and the exact staged
package/AppStream checks completed0 in package-resource-cleanup.log with neither
earlier ResourceWarning; runtime/native test hashes above are unchanged. The
243f82aa server gate remains frozen separately while these test-only changes
are verified/published on the follow-up branch.

Assigned shared dependencies remain ari-03 DT2 SegmentBar, ari-04 sidebar/row
metrics, ari-05 R2-SHEET, ari-06 generated Ari roles/metrics and ari-07 R2-BTN.
The app consumes their semantic APIs as they land, without editing kit paths or
restyling shared parts. The receipt's exact inline DetailsItem composition also
awaits coordinator clarification in ari-02. No thresholds or deviations changed.

## Package check and smoke commands

`packaging/rpm/luma-ari.spec` runs from the packaged source root:

```sh
PYTHONPATH=$PWD GSETTINGS_BACKEND=memory python3 -m unittest discover -s tests -p "test_*.py" -v
appstream-util validate-relax --nonet data/org.projectluma.Ari.metainfo.xml
python3 -m py_compile ari/*.py ari/tools/*.py ari_ui/*.py eval/run.py
PYTHONPATH=<buildroot>/<python3_sitelib> python3 -P -c "import ari.cloud, ari.service"
glib-compile-schemas --strict --dry-run data
python3 -c "import json; json.load(open('eval/cases.json'))"
```

The app-owned archive script now includes the canonical fixture and scenario;
fixture tests resolve both repository and packaged layouts. A temporary packaged
source tree discovered all 54 tests: its 52 GTK-free tests, 31 compile inputs and
eval JSON passed locally. Strict schema dry-run and production desktop-file
validation also passed locally. Earlier full `%check` attempts failed because
required `appstream-util` was absent in the approved toolbox and host (the RPM
already declares `libappstream-glib` as BuildRequires). Historical1191 failed
solely on that missing validator and remains recorded. The fresh daytime full
check now passes using an existing retained Fedora44 libappstream-glib RPM
extracted only into scratch. Its real executable/library are placed on private
PATH/LD_LIBRARY_PATH inside the final toolbox runtime; the exact mandatory
validate-relax --nonet command is unchanged. All54 tests, staged -P imports,
compile/schema/eval/desktop checks and the actual completion marker pass on
the runtime hash above. No checker substitution, package installation or
assertion skip was used.
INT's exact retained package/VM script and source SHA are awaiting evidence.
No RPM build or package installation was attempted.

The native fixture smoke is `bash src/luma-ari/tests/run-lumaui-runtime.sh`, run
through the authorized direct host helper; it creates a private display, bus and XDG stores,
with fixture mode and a guard rejecting LiveDaemon. Production's public command
is `org.projectluma.Ari` (`python3 -m ari_ui.app`); its VM launch is the integrator's
check. Morning build eligibility does not replace four conform PASS reports and
the remaining OVERSEER section 4 completion requirements.
