# Ari search, actions and Models checkpoint

Status: server-qualified Ari2 r7 delivered and launched on the Dell for manual
testing. Visual conformance and live OpenAI qualification remain incomplete.

Owner: Codex, Ari ChatGPT task. Branch: `agent/1003/ari-chatgpt-20261029`.
Review: draft PR #92. Qualified r7 runtime source is
`79b0c448a23c6063dec78f5487a3c6893920bc27`; the independent Dell preview
now runs this revision. Earlier checkpoints below are history.

## October 29 evening: direct application records and wheel scrolling

The owner's r7 test found that the wheel could not reach the padded end of the
thread: the follow logic repeatedly aligned the final message against the
composer. The replacement follows the actual adjustment extent, including
padding, and stops following on manual wheel input. The full 50-test native
Wayland suite passes. An additional actual XTest wheel regression on a private
X11 display passes up/down scrolling, reaching the padding and remaining there.
The test injects input only into its disposable server display; Ari's runtime
still has no synthetic-input adapter.

The owner's Calendar task took 112.9 seconds, created an incorrect event from
its title field, and then removed it. A new typed bridge calls the installed
application model APIs directly through `query_application` and
`change_application`. Supported operations cover Calendar events, Notes,
Contacts, Tasks and saved Weather locations. They do not open windows, inspect
widgets or navigate forms. Calendar title, start, end and IANA time zone are
separate fields; each write returns the verified saved record.

The bridge retains read/action permissions, task-scoped approvals, named-app
checks and Advanced rules. Existing records need expiring revision-bound query
targets. Verified repeated writes in one turn are deduplicated. Notes retains
its concurrent-edit checks and Trash. Calendar writes use its existing durable
backup and deletion restoration APIs. Tasks completion uses its owning model
behavior. Queries return bounded complete records, with explicit preview and
truncation metadata. Unsupported operations remain explicit.

Current server evidence: 181 unit/protocol/policy tests, staged Meson imports,
schema checks and nine focused GTK tests pass. Real private EDS, Notes storage,
GWeather, MCP and agent tests pass: the exact Dog park event is saved for
October 30, 2–3 PM America/Chicago and read back with matching fields in about
0.03–0.04 seconds on local EDS. Calendar update/delete/restore and durable
backups, Notes conflict/Trash/restore, Contacts create/update, Tasks
create/complete and Weather add/remove/restore pass. The real agent performs
one typed operation and two scripted model responses, with one normal-mode
approval or zero in Let Ari act. Model decisions and private forecast/account
fixtures do not establish actual hosted ChatGPT behavior.

The app-model ABI was checked read-only on the Dell. No automated Dell app
interaction or test ran. Packaging and delivery of this next r8 are pending;
the installed preview remains r7. Messaging, calls, timers, invitations and
recurring/all-day edits still require their supported owner interfaces.
Calendar/Weather windows may need refresh/reopening to show another client's
provider change; completion here means the saved provider record was verified.

## October 29 follow-up: message stability and native commands

The owner reports that delivered r6 added Chicago to Weather and installed and
uninstalled applications successfully. The successful add took 34.1 seconds;
removing Chicago failed after 43.3 seconds. Older errors in the supplied chat
screenshots explicitly predate the last correction.

The delivered r7 displays local 12-hour message/action times, places
message times directly below their bubble and action times beside their text,
retains the composer and unchanged message widgets during updates, reserves the
same space for Send and Stop, and follows the conversation until the person
scrolls away. Sending or navigating to the end resumes following. Desktop and
phone streaming/manual-scroll/composer regressions pass in the full 50-test
native Wayland cohort; the actual private OAuth/keyring/settings fixture also
passes. This is functional evidence, not a visual-conformance pass.

The generic accessibility adapter now discovers native `org.gtk.Actions`
commands exported by the same application process. It invokes the existing app
handler directly, with an opaque target bound to its unique bus owner, process
start time, advertised parameter type, document/selection and editable-text
fingerprints. It preserves Ari's action permissions and approval modes. It
reports acknowledgement separately from completion and returns observed state
for verification. Unique application discovery includes the first snapshot,
avoiding a separate model inspection round trip. No Weather-specific dispatch,
application-database writer or synthetic input was added.

Real populated Weather tests remove exactly Chicago while retaining Boston and
restore it through its existing Undo command, both directly and over actual MCP.
Observed command-plus-inspection times are about 1.6–1.8 seconds on the server;
these exclude hosted model latency. Scripted Responses decisions through the
real agent remove Chicago in all three autonomy modes: Important asks once,
Let Ari act asks zero times, Every time asks for discovery and action. These
fixtures use real Weather controls/store and fixture forecast responses; they
do not establish real hosted model decisions. The latest source suite passes
174 unit/protocol/policy tests, repeated successfully in the final Fedora RPM.
All five CI checks passed at runtime source `79b0c448a23c6063dec78f5487a3c6893920bc27`.
The final r7 export passes account/profile/keyring isolation, installed launcher
and D-Bus activation, RPM byte comparison and native flow, public source
contracts, and actual r6-to-r7 update/retention/rollback. Archive SHA-256:
`660ca87d0136edc9aae7971e8758aec72e50e78ca01d6044ac6e5529a7e8450c`.
RPM SHA-256: `df3a728394c69f47daabc3351e87f8dff2efc591a2b2b71e99c066ac7913e138`.
SRPM SHA-256: `c066c50c4782c58b8a828a0324d65f6d36da8ffc775c70e6312d67bfb2be488a`.
The owner restored the Dell connection. The archive checksum was verified before
extraction; the updater ran after the old preview window and background service
were both closed. It retained chats, settings and keyring entries. Ari2 r7 was
launched in the active graphical session at 22:32 UTC on October 29; read-only
metadata confirmed the launch unit running and both preview bus names owned.
No automated Dell app/UI tests ran. Latest live ChatGPT acceptance remains manual.

## Application and development continuation

The later owner request to complete Ari's action capabilities adds concrete
adapters for the previously missing permission tiers:

- Semantic Broker application access, surface/object discovery and actual action
  invocation. The broker owns grants, private-data redaction, risk confirmation
  and dispatch. On an active user manager, the adapter runs in Ari's managed
  application scope; private test buses retain transient session identity.
- Native SDK project creation, source listing/reading and checksum-guarded
  edits under `Projects/Ari`. Build commands run in an isolated Bubblewrap
  workspace without network, other home files, credentials, session bus or
  system writes. Project approval in the default mode covers only that project
  in the current turn; All mode continues to ask for every change.
- Depot catalogue search and Flatpak source/commit/permission review before
  installation. The owning transaction rechecks the source; Ari verifies the
  resulting installed ref. Other catalogue backends use Depot's own interface.
- Exact local-package review and the existing installer workflow, plus removal
  that retains personal settings/data and protects essential components.
  Optional image applications use the existing polkit system helper, which
  independently checks its trusted catalogue, dependencies and staged result.
- Action receipts for these changes, persisted project targets with portal Open,
  safe migration of existing steps, and cancellation/restart of isolated project
  tool processes. Software transactions that need a restart report staging.

Tool routing now offers discovery, access and action tools together for in-app
work, and the complete workspace toolset for application development. Compound
requests do not stop after a simple app-open shortcut. Unknown tools remain
denied; no unrestricted privileged shell was added.

Later validation: 146 unit/protocol/policy tests and 11 focused GTK tests pass.
The full 46-test native Wayland suite, five form-factor checks and the actual
private Secret Service/OAuth/inference fixture pass in the broader qualification
cohort; the final managed-scope command assembly is additionally unit-tested.
Four native action tests pass using an actual private broker/provider, SDK
generation and MCP source edits, isolated Python tests/builds, and a real
portable-app install/removal with retained data. Catalogue commit substitution,
essential-component refusal and staged system changes use controlled boundary
fixtures; no real system deployment or public app repository was changed.

UI changes align the Models header, restore installed-model menus, correct
caption truncation, family grids and capability/detail icons, expand removal
task rows, and preserve labelled phone Download/Remove actions. Current captures
still require final comparison; earlier comparisons fail without changed
tolerances. OpenAI's required action wording remains Continue with ChatGPT,
which differs from the supplied reference. No conformance or 95% claim.

The implementation and evidence below describe the earlier checkpoint and
remain its history; its missing action adapters are superseded by this section.

## Implemented

- Request hosted ChatGPT web search, remove the local search/page-fetch tools
  from that route, retain search status and completed citations, and fail
  explicitly when a requested capability is rejected. Answer-tool permission
  also controls hosted search. Other routes no longer substitute Wikipedia for
  an unconfigured web search.
- Render escaped Markdown links, emphasis, lists and code; keep plain-text copy.
  Current-fact searches and truthful ChatGPT-history boundaries are in the
  persona sent as Responses instructions.
- Preserve advertised reasoning capabilities, store choices per account/model,
  validate request effort, and record it on replies. Explicit local thinking
  controls use the runtime's thinking switch.
- Dedicated app-opening permission without per-launch approval; correct app
  name parsing, one receipt, approval-to-step replacement and tool categories.
  A web result cannot substitute a different app for the one requested.
- Models tabs, task overrides, grouped families, size/detail pages, real pinned
  download choices, pause/resume/cancel, and removal confirmation inside the
  action bar. Cancellation discards partial data without deleting an installed
  model. Concurrent requests cannot replace a different local runtime while
  another answer is in progress.
- Quiet composer route control, grouped picker, capability-driven effort panel,
  word-boundary titles and the supplied Ari/model-family artwork.

The live local catalogue contains five pinned Qwen recipes, including reviewed
14B and 30B-A3B GGUF metadata. No model weights were downloaded or newly
qualified for local inference. Other makers appear in the synthetic design
fixture; this does not enable fictional live downloads or provider connections.
Anthropic integration remains deferred.

## Verified

- 126 unit/protocol/policy tests; nine focused async GTK regressions.
- Full isolated Wayland suite: 46 native tests, including 36 existing states at
  desktop/phone widths, model-page/effort interactions and single receipts.
- Private D-Bus, real disposable Secret Service, loopback OAuth, signed fixture
  identity, daemon restart, persisted effort, hosted-search/effort/persona request
  fields, completed fixture inference, late failure, signout and cancellation.
  This test contacts no real OpenAI account.
- Meson staged installation/imports, schemas, SVG parsing, registry checks and
  source whitespace checks.
- Five capture-context regressions, including agreement with canonical device,
  presentation and input context. Narrow desktop and physical phone are distinct.

The broad package-release contract still fails its existing unrelated Prairie
icon-theme pin/spec mismatch. Native narrow-window runs also produce GTK header
allocation warnings; passing functional assertions do not establish a clean
visual gate.

## Visual gate: FAIL

Reference: `SimplyLuma/design-center` revision
`255e38cc874fce276114d18e49a1232432f41886`, using `current.html` with `ari=b` and
its actual `phone=1` route. The added `ari-v73` scenario compares eight matching
views at 1600×1000 and 390×844. Native capture also includes the new effort panel,
which has no existing simulator counterpart.

The comparison uses the repository's normal tolerances and accepted deviations.
No thresholds were relaxed. The source/extracted-platform-109 capture profile
is explicitly separate from installed-RPM baseline qualification.

Remaining differences include family-page geometry and icons, grown removal
layout, installed-model options, picker wording/placement, Activity receipt
controls, and phone chat layout. The reference's sign-in wording also differs
from the native Continue with ChatGPT integration. No 95% UI/UX or conformance
PASS is claimed. Do not replace the Dell preview until the required gate passes
or the owner explicitly changes that requirement.

## OpenAI and action boundaries

Actual account acceptance of hosted search, an actual model capability row, and
live Fedora/Kansas City answers remain unverified. Fixture requests prove the
client's request construction, not service acceptance. Keep the PR draft.

Official [Sign in with ChatGPT guidance](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt)
says neither identity nor plan permission grants access to ChatGPT conversations.
The documented [models/inference route](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
provides model discovery and Responses; no supported history endpoint was found.
No authenticated history probe, unofficial endpoint, browser scraper or imported
CLI session was used. A read-only `conversations.json` export importer is only a
proposal and requires owner approval before implementation.

The continuation below supersedes the initial missing-adapter assessment. Ari now
connects concrete application actions, project builds and software workflows.
Unsupported actions still return their actual service error.

Tide exposes MPRIS playback controls but no search action; its `OpenUri` method
returns NotSupported. No song-search tool is claimed.

## Packaged source checkpoint

Runtime source commit: `ddd958bfed46dd6f60da76879f767f9d88f8ab5d`.
Fedora 44 RPM/SRPM build and the spec's 126 tests, installed-module imports,
identity refusal, metadata and responsive navigation/contrast checks passed.
All packaged Python/SVG runtime files matched the source before a fresh native
OAuth/inference fixture passed using the extracted payload. Public repository
source contracts also passed.

RPM SHA-256:
`aa36e9fad1c4749403eadb82ac67c8242a59bbae5829aad90d8f666c1fa5f349`.
SRPM SHA-256:
`76a90c961d33b32441e39a1725ddb3eea9d3a89b59dc41732ee9f27fae8a5d2c`.
These are server test artifacts, not a published release or a Dell delivery.
The visual and live-account gates above remain open.

## Application and development continuation

Ari now discovers published app surfaces, requests broker-owned access and invokes
real application actions. It creates SDK projects, reads and edits project files
with checksum conflict checks, and runs project builds/tests inside Bubblewrap
without network, desktop bus or access to the rest of the home directory.

Software tools search the Depot catalogue, review and install catalogue Flatpaks,
inspect/install local packages, remove owned installations while keeping data,
and remove/restore optional system-image apps through the existing trusted
installer helper. Essential apps are protected. System changes report pending
restart when appropriate. Actual package transactions and broker confirmations
retain their owning service's checks. Arbitrary shell and synthetic GUI input
are not exposed.

Consequential changes produce previews and completion receipts. Project approval
is scoped to one project within the current turn; permission revocation while a
confirmation is open prevents the mutation. Stop cancels isolated project work.
Activity can open actual project targets. Models now has installed-model menus,
family details, removal task rows and visible phone Download/Remove labels.

Validation: 147 unit/protocol/policy tests and source staging/import/schema checks
passed. The preceding full cohort passed 46 Wayland UI tests, five form-factor
checks, private OAuth/Secret Service fixtures, and four native action tests. The
action tests exercised a real private broker/provider, actual SDK scaffolding,
isolated Python builds, MCP stdio and portable-package install/removal with data
retention. Catalogue and privileged system helpers use controlled boundary
fixtures; no real system deployment was changed. Eleven focused GTK regressions
also passed, including visible phone action labels.

These checks do not establish actual ChatGPT hosted-search acceptance, every
application's action coverage, or visual conformance. The draft PR and Dell
replacement remain gated as described above.

## Functional preview delivery

Exact runtime/source commit: `e5968174784210d96103b74851c39c1275f83a5d`.
Its Fedora 44 RPM/SRPM build passed, including all 147 unit tests. All packaged
runtime Python/SVG files matched source, and the extracted payload passed the
private native OAuth flow and public source contracts. All five GitHub checks
passed.

RPM SHA-256: `155cf2dab576495deb7bf547433eae582c6a96af7a3c1b36a6da86f3d3cc21ec`.
SRPM SHA-256: `0adf0a9019f48c916fe0dfa622bcbedaba49518b39f7adb960ccc4baed30e357`.

The independent Ari2 r3 export also passed native account/profile/keyring
isolation and actual launcher/D-Bus activation checks. Its updater passed
running-UI/daemon refusal, altered-registration and corrupt-payload refusal,
profile preservation and rollback in a disposable server session.
Archive SHA-256: `c1520e496bc9011c4aaf0ef779384a74cb26fc2924e36ecab0d0102892ba808f`.

The owner explicitly authorized a functional-test exception to the ZIP's visual
gate: “Launch the functional-test build now.” After verifying the existing
preview was closed and the transferred archive matched, the owned Ari2 launcher
and activation registrations were updated and the candidate launched in the
Dell's graphical session. Its profile and keyring were retained. No automated
application testing ran on the Dell. This launch does not establish live account
acceptance or visual conformance; those remain open, and the PR remains draft.

## Manual-review corrections

The functional preview exposed three issues: loaded chat messages disappeared
when account/model metadata refreshed, Weather had no usable broker actions,
and upgraded account caches lacked reasoning metadata.

Conversation refresh now preserves loaded and streaming messages. Stored action
receipts are also included in subsequent model context when the successful
assistant text was deliberately omitted from the chat. Startup refreshes the
account-specific model catalogue once, so old saved rows acquire their actual
effort capabilities. A live catalogue refresh confirmed reasoning levels for
the selected model; no credentials or private conversation text were collected.

Applications without broker actions now have a Linux accessibility fallback.
Ari finds an application explicitly named by the person, inspects its visible
controls, and uses advertised button actions or editable-text interfaces.
Protected fields are excluded. Targets expire and are checked against process
start time, current tree position, object identity, name, role and sensitivity;
changed text is preserved. Mutations retain normal policy/preview checks and
require a fresh inspection afterward. No synthetic input is generated.

Validation passed 155 unit/protocol/policy tests, 48 native Wayland UI tests,
five capture-context checks, staged imports/schemas and private native account
fixtures. Real Weather tests used its actual city database and persistent store
in disposable server settings: enter Chicago, activate its result, then verify
the saved city and visible controls. Both direct adapter and MCP paths passed.
Forecast HTTP calls were stubbed; the actual Weather search, UI interaction and
persistence were exercised. These tests did not run on the Dell.

Completed ChatGPT responses without a reply/action now expose a specific error;
provider refusal text is preserved. Weather and other in-app requests receive
the full relevant tool bundle. Further visual fixes align approval insets and
controls, Activity links, task icons and installed-model rows. Visual conformance
still has no PASS claim. The replacement preview requires its own package,
isolation, activation and profile-preserving update checks before delivery.

## Replacement candidate ready

Runtime source: `6c2f6665a67ca13f7960fb2b0ec7b2172280df33` (all five CI checks
passed). The Fedora 44 package and isolated Ari2 r4 export built successfully.
Its native sign-in/isolation, launcher activation, extracted-package byte match,
source contracts and r3-to-r4 update/rollback checks passed. A further real
Weather test passed using the normal GTK accessibility defaults, without forcing
the accessibility backend or enabling its status property.

RPM SHA-256: `9e7dd7505d28a299d8d04f80a3a0e9b984faa2b5ab61fb575d7eff11b6fe0e0f`.
SRPM SHA-256: `0ff0dd4f27ea949e6671848fc5df286cae6e460a80630df062e6916c1d7d3bd8`.
Ari2 r4 archive SHA-256:
`dfd0dddf9180fd18d2703576f8fc74a1b90550dc8aaa05732c9e6d4aeaa6fefd`.

Delivery is pending: the Dell's preview connection timed out after this build
was ready. The existing r3 installation has not been replaced. The owner was
asked to wake/unlock the device and close Ari2 to preserve any draft.

Fresh desktop/phone captures and the unchanged standard comparison ran on the
replacement source. Visual conformance still fails; no thresholds or accepted
deviations were changed. The previously authorized exception permits functional
manual testing, and does not establish visual or release acceptance.

## Replacement delivered — 2026-10-29 20:25 UTC

The owner restored the Dell connection and confirmed Ari2 was closed. After the
old background service exited normally, the transferred r4 archive's SHA-256
was verified and the qualified updater replaced r3's owned registrations. It
reported retention of chats, settings and keyring entries. The candidate was
launched in the Dell's real graphical session; read-only metadata showed the
launch unit active/running and both Ari2 D-Bus names owned.

Candidate: `ari2-chatgpt-6c2f666-r4`.
Launch unit: `luma-preview-ari2-chatgpt-6c2f666-r4-c20e7531.service`.
This was a requested launch, not an automated Dell application test. Manual
confirmation remains pending for conversation retention, the effort picker,
Weather changes and actual account hosted-search acceptance. The visual gate
still fails and PR #92 remains draft.

## Live Weather retry and stream integration correction

The owner's r4 retry still returned a completed response without a reply/action.
This is a failed live acceptance check. The earlier direct adapter and MCP tests
did not establish that the hosted model would invoke those controls.

The provider now retains individual completed output items when a successful
terminal event has an empty output summary. Calls remain blocked until terminal
success; incomplete, conflicting or discontinuous item sequences are refused.
Reasoning and function-call items are retained for the subsequent tool result.
Unexpected/empty streams emit only allowlisted event/item counts in diagnostics,
without response text, identifiers, arguments, credentials or encrypted content.

A separate action-loop bug was found: application discovery and several other
tools return list data, while the tool-result formatter assumed a dictionary.
The formatter now preserves list/scalar data for the next model call.

162 unit tests and eleven focused GTK checks passed. The expanded native Weather
test passed through a scripted Responses stream, the real agent and approval
engine, actual MCP process and real Weather controls/store, including two
approvals and two receipts. Direct adapter/MCP checks also passed. The model
decisions in this test are scripted; actual ChatGPT account acceptance remains
pending, and the supplied screenshot's exact remote response shape is unknown.

## Stream correction delivered — 2026-10-29 20:37 UTC

Runtime source: `8795dce3a9aa257de91fb27b700e73140c7ea91a`. All five CI checks
passed. The Fedora 44 RPM/SRPM and Ari2 r5 export passed, including 162 tests in
the package build. Actual isolated sign-in/keyring, launcher activation,
extracted-package byte comparison/native flow, source contracts and r4-to-r5
update/rollback checks passed in disposable server sessions.

RPM SHA-256: `c2cda51be30ab24863c74282d8474fb384d33f985c9abcddc39fb930bc08627b`.
SRPM SHA-256: `8cb160b17e06db24daf25e89b417eaa99fb0e49e3741e29b18a94d58fa6221ce`.
Ari2 r5 archive SHA-256:
`37a6fe940f492819c2f7e4071bb0d9a9cb18b7fb76a38d3ca845efc20a6e27f7`.

After the owner closed Ari2 and its background service exited normally, the
checksum-verified replacement updated its owned registrations and retained
chats, settings and keyring entries. Candidate `ari2-chatgpt-8795dce-r5` launched
in the Dell graphical session under
`luma-preview-ari2-chatgpt-8795dce-r5-436461f9.service`. Read-only metadata showed
active/running with UI and daemon D-Bus names owned. Manual Weather retry is
pending; no automated Dell application tests ran. The screenshot failure is not
declared resolved until live acceptance. Visual conformance still fails.

## Task autonomy continuation — implementation awaiting full qualification

The owner's r5 manual retry opened Weather and entered Chicago, then stopped
after 104 seconds despite visible matching suggestions. Completion failed.
Prior empty-window fixtures did not reproduce the populated forecast layout.
The old adapter returned layout nodes and forecast prose alongside controls;
even a small 66-node snapshot exceeded the agent's 12 KB tool-result budget.

The continuation implements compact, filtered and paginated actionable controls,
with a fresh snapshot after a change. A populated Weather fixture now includes
a saved city, 48 forecast hours and ten days. Normal approval is scoped to one
requested task in one application/process identity. The private preview exporter
also removes its forced Every-change default, restoring the normal source mode.

Permissions and autonomy is exposed in the native app menu, model picker and
settings command. Its three choices are Ask every time, Ask for important actions
and Let Ari act. Advanced Allow/Ask/Block rules can be reviewed and removed;
remembering an approval/decline requires an explicit checkbox. Remembered app
text/control choices can be scoped to that app. Master/capability restrictions,
administrator locks, broker consent and operating-system controls remain in
force. Permission revocation is checked again before a pending change.

Exact same-user process discovery and end/kill are connected through a new MCP
server. Short-lived targets and pidfds prevent PID substitution; query and force
intent must be named by the person. A sent signal is distinguished from verified
exit. Ambiguous references to a selected item require actual app context or
clarification; timer, phone, music and email routing does not invent app support.

171 unit tests and eleven existing focused GTK checks passed during development,
including a real disposable process termination and stale-identity refusal. The
expanded native settings/restart checks, full UI suite and populated Weather
checks in all three modes are pending server capacity. No r6 package or Dell
replacement is yet qualified. Keep PR #92 draft; no live completion or visual
conformance claim.

The expanded native checks subsequently passed: 49 Wayland UI tests, settings
interaction at desktop/phone widths, actual private D-Bus remembered-permission
persistence across daemon restart, and the populated Weather workflow through
direct API/MCP and all three agent approval modes. The normal mode asked once,
Let Ari act asked zero times, and Ask every time asked six times. The actual city
database, controls and saved-city store were used; forecast HTTP responses and
model decisions were controlled fixtures. Missing server Cairo bindings and
temporary document-portal teardown were corrected in the test environment; no
host package or real device state was changed by those tests. Package and Dell
qualification remain the next gate; this is not live ChatGPT acceptance.

## Autonomy preview delivered — 2026-10-29 21:36 UTC

Runtime source: `9e726a46aff06a1e7af6ac335e523d5d3bcd8c12`. All five CI checks
passed. The final Fedora 44 package passed 172 tests, including a real disposable
process that ignored normal termination and was then force-killed with verified
exit. The native settings panel, cold-start opening, remembered permissions and
daemon restart passed; the broader cohort passed 49 Wayland UI tests. Populated
Weather passed with all three approval modes and no Python callback failures.
Forecasts and model decisions were fixtures, with the real city database,
controls, MCP transport, approval engine and saved-city store.

Exact r6 native sign-in/profile/keyring isolation, actual installed activation,
RPM runtime byte comparison, extracted-package native flow, source contracts
and r5-to-r6 update/rollback qualification passed. The private qualification
expectations now check the intended Important-mode default rather than the old
preview's forced Every-change default. No product permission checks were waived.

RPM SHA-256: `bef99f9317c3454f4ed9fd6816a2b1e32cad4562f894715fdc297d581ba42d08`.
SRPM SHA-256: `ec1ceb2c69a7bfb41f080a0deaea6914072e1a76125dd191c165e70dcc88d511`.
Ari2 r6 archive SHA-256:
`d98e55fa87f60a68a895a5618a53f25261988ae43a0a34236be91cdc527b62a5`.

Both previous preview D-Bus names were unowned before replacement. The
checksum-verified candidate updated its owned registrations and retained chats,
settings and keyring entries. `ari2-chatgpt-9e726a4-r6` launched with
`--settings permissions` in the Dell graphical session under
`luma-preview-ari2-chatgpt-9e726a4-r6-1466daa3.service`; metadata showed it
active/running with UI and daemon names owned. This was a requested launch,
not an automated Dell application test. Live Weather/search acceptance and
visual conformance remain open. PR #92 remains draft.
