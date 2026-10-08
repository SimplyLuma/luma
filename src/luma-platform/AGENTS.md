# Luma Developer Platform agent contract

- Extend the existing GTK4/libadwaita and GObject-introspection foundation. Do
  not introduce a second UI runtime, browser shell, or app-specific toolkit.
- `config/shared/design-tokens.json` is the only token authority. Regenerate
  derived CSS with `scripts/developer/generate-luma-platform-tokens.py`.
- A command has one stable ID and one behavior. Menus, shortcuts,
  accessibility, semantics, and agent actions must bind that command rather
  than duplicate handlers.
- Width adapts layout. Presentation mode and input capability are explicit and
  independent. Never infer phone/tablet identity from window width.
- Do not globally restructure or restyle arbitrary third-party Linux apps.
- Every new public component needs keyboard, accessibility, reduced-motion,
  high-contrast, long-text/RTL, and desktop/mobile contract coverage.
