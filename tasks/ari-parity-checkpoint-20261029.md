# Ari search, actions and Models checkpoint

Status: source implementation checkpoint; visual conformance and live OpenAI
qualification remain incomplete. No updated Dell candidate is qualified.

Owner: Codex, Ari ChatGPT task. Branch: `agent/1003/ari-chatgpt-20261029`.
Review: draft PR #92. The independent Ari2 preview already delivered from
`7a2964d12` is not changed by this checkpoint.

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
