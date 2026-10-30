# Shared mobile Keycap keyboard

Owner: Codex / luma-dev-2. Ready for source review, 2026-10-29.
Branch: agent/1003/mobile-keycap-20261029.
Worktree: /srv/dev/worktrees/luma-os/mobile-keycap-20261029.
Scope: shared Stevia Keycap theme, input tools and privacy policy. No Titan
hardware policy changes; separate Titan/Twitter work is preserved.

Pinned source: Stevia0.55.0 cd8cc1d33a701573af106c53ed4dc493ef68b88c.
Preserves patches0001/0002 and main's exact0003 theme-variant fix; adds0004,
ordered source preparation, matching bundle schemas/font, and component tests.
Read source before implementation; audit, behavior and explicit gaps are in
[Keycap keyboard](../docs/mobile/keycap-keyboard.md).

Validation: eleven native Meson suites passed. Native light/dark GTK captures
at360/390/768logical pixels passed allocation/minimum-row checks. Headless Phoc
activated normal, hidden PIN and URL purposes and accepted synthetic input in
normal/hidden fields. It forced visibility; this is not hardware suppression
acceptance. Final series replay from the clean pin matches implementation;
Python compilation, shell syntax and source whitespace checks passed.
ARM bundle/real LatinIME decoder and physical FP6/Fold acceptance not run.

Private build/logs/captures: account experiments/mobile-keycap-20261029.
Native final job: dev-keycap-final-source-a70981ac (exit0).
Wayland routing job: dev-keycap-wayland-routing-f7f261bd (exit0).
No active phone services or deployment. Next: review the source, then build the
ARM bundle and validate on a keyboardless phone, including real touch gestures,
privacy transitions, lock-screen purpose and hardware-keyboard suppression.
