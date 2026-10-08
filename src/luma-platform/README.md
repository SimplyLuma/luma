# Luma Developer Platform

This tree contains the first buildable release of the Luma Developer Platform.
It is deliberately split into two libraries:

- `LumaSemantics-1`: display-independent semantic objects, actions, risk and
  privacy metadata, and Live Extension state.
- `LumaUI-1`: GTK4/libadwaita adaptive application components and the explicit
  presentation/input capability contract.

Both libraries expose a GObject API and introspection metadata. Applications
remain ordinary GTK applications and can be packaged for any Linux
distribution. The SDK tooling lives in `sdk/`; JSON contracts live in
`schemas/`; starter applications live in `templates/`.

Applications call `luma_init()` in C or `LumaUI.init()` through introspection
once, after GTK has a display, to install the shared application-scoped style.

This is a platform preview. ABI stability begins with the eventual 1.0 release.
Until then, incompatible changes require a version and migration note.

Application contract 0.2 keeps Flatpak, portals, AppStream, and freedesktop
desktop files as the portable substrate. The SDK binds their identities,
rejects broad sandbox permissions, checks lifecycle declarations, and derives
Native status only from cross-architecture desktop/mobile evidence. It does
not create a Luma-only package format.

## Build note: design tokens and `generate-luma-platform-tokens.py --check`

Do not add `scripts/developer/generate-luma-platform-tokens.py --check` to
`%check` (or any gate) before fixing the generator. The light, dark and
high-contrast kit token sheets (`appkit/luma-appkit-tokens.css`,
`luma-appkit-dark-tokens.css`, `luma-appkit-high-contrast-tokens.css`) carry
lines the generator does not emit. These are the accent-as-text roles
(`luma_accent_ink`, `luma_accent_soft`, `luma_accent_link`) and, for high
contrast, their ordering. So `--check` already reports those three files as out
of date, and running the generator without `--check` deletes the hand-written
lines. Until the generator learns those roles, add a new token to
`config/shared/design-tokens.json` and run the generator for the frost and
glass sheets (fully generated), then append the same `@define-color` lines to
the three hand-kept sheets by hand, as the ADR-043 `controls` roles were.
`tests/filled_controls.py` in `%check` reads the sheets themselves, so a
missing token fails the build there.
