# Mobile status icon proportions

- Owner: Codex.
- Status: source validation complete; package rebuild pending.
- Scope: collapsed mobile Wi-Fi and battery proportions and foreground strength.
- Base: 484bc6716b7b949c44349da8f74775056ebd70f0.
- Components: luma-phosh 0.55.0-1.luma.60 and prairie-icon-theme 0.1.0-1.luma.39.

The horizontal battery needs a larger optical size than the square network
canvas. Scoped classes reserve space before scaling the native status images;
quick settings and the clock retain their existing layout. Prairie supplies a
matching disabled Wi-Fi glyph, including its separated slash.

Validation: the full 21-patch Phosh series applies with zero fuzz to the verified
pinned upstream. Status vectors pass the existing fill-only SVG contract. GTK 3
parses the CSS; native librsvg renders all battery assets and seven network states
on dark, light and blue surfaces (276 lookups). The repository checker passes
60 tests and device-manifest validation; six notification unit tests also pass.
The existing mobile shell smoke stops at an unchanged reference to missing
`docs/decisions/007-converged-adaptive-luma-shell.md`.

Builder handoff: rebuild both source packages and promote their verified artifact
hashes and pins through the existing build lane. No complete RPM build, ARM
hardware qualification, merge or release publication is claimed. Clock placement
and dock appearance are independent changes and are absent from this patch.
