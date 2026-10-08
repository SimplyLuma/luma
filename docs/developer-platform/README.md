# Luma Developer Platform preview

The Luma Developer Platform makes ordinary GTK applications adaptive,
consistent, accessible, semantically inspectable, and portable. It does not
replace GTK, libadwaita, Flatpak, portals, AT-SPI, or AppStream.

## Product map

| Part | Responsibility |
| --- | --- |
| Luma UI | Adaptive application structure and app-scoped design tokens |
| Luma Semantics | Typed objects, actions, privacy/risk, Live Extensions |
| Luma SDK | Creation, inspection, linting, testing, previews, packaging |
| Design Guidelines | Open behavioral and visual conformance contract |
| Semantic Broker | Installed permission, consent, invocation, and payload-free audit boundary for agents/shell |

Start with the [Luma Application Kit](application-kit.md) for the supported
window, island, command/menu, state, token, and convergence contracts.
Use the [product terminology](product-terminology.md) for shared shell names,
including Dash and Dash Settings.

The preview source is in [`src/luma-platform`](../../src/luma-platform). Build
it with Meson and run `meson test`. Install the SDK Python project from
`src/luma-platform/sdk`, then run:

```sh
luma new "Example" --id org.example.Example --destination example
cd example
luma lint
luma inspect
luma test
```

## Non-negotiable application rules

1. There is one source tree and one binary across Luma presentations.
2. Width changes layout; it never identifies a device.
3. Presentation mode controls chrome independently of width.
4. Input capability controls target size and affordances independently.
5. Compact layouts preserve functionality and move actions into navigation,
   sheets, or overflow instead of deleting them.
6. GTK accessibility metadata is required for every custom component.
7. Each meaningful operation has one stable semantic action ID.
8. Secret data is never part of a semantic object or Live Extension.
9. Third-party code never renders inside the shell.
10. Native status is earned by test evidence, not appearance or package type.

## API stability

The `0.x` preview is intentionally not ABI stable. Public types use the
parallel-installable `LumaUI-1` and `LumaSemantics-1` namespaces so 1.0 can
stabilize without claiming compatibility prematurely. Every incompatible
preview change needs a migration note.

## Native evidence

A Native candidate must pass:

- 360x294, 500x800, 1024x600, and a wide desktop layout;
- pointer, touch, keyboard-only, and screen-reader interaction;
- long translated text, RTL, large text, high contrast, and reduced motion;
- portal and sandbox tests for every requested permission;
- semantic inspection and action risk classification;
- desktop and mobile package-composition contracts;
- x86_64 and aarch64 package/runtime checks.

The SDK's local `luma test` command validates declarations. It does not claim
the graphical and physical-device gates passed; those remain CI/release gates.

The [application contract 0.2](application-contract.md) defines portable
Flatpak/AppStream identity, portal-first sandbox policy, legible data removal,
and evidence-derived Native status. `luma lint` checks the project; `luma
release-check` checks the cross-architecture, desktop/mobile release evidence.
