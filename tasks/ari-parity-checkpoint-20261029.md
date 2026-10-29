# Ari search, actions and Models checkpoint

Status: source implementation checkpoint; visual conformance and live OpenAI
qualification remain incomplete. No updated Dell candidate is qualified.

Owner: Codex, Ari ChatGPT task. Branch: `agent/1003/ari-chatgpt-20261029`.
Review: draft PR #92. The independent Ari2 preview already delivered from
`7a2964d12` is not changed by this checkpoint.

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
