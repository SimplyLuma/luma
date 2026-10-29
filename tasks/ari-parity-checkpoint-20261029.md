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

Ari currently lacks general application-action, project-building, installation
and removal tools. Policy tiers for future capabilities are explicitly disabled;
changing that list alone would not implement them. Luma already has the Semantic
Broker action interface, SDK scaffolding/checks, and installer workflows. Ari
needs adapters and verified completion/receipt handling for those services.

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
