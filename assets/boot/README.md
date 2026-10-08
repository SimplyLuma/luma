# Native Luma boot presentations

The desktop selects `luma-loading`. It starts on the firmware's BGRT logo
(through Luma's Plymouth script patch), cross-fades to the Ink surface with the
wordmark and loader, and draws its own unlock prompt and update screen.
`luma-firmware`, Plymouth's two-step plugin keeping the firmware logo for the
whole boot, stays packaged; its images are rendered from the brand SVGs at
package build by `scripts/boot/render-firmware-theme.py`, which also renders
the prompt pieces for `luma-loading`'s surface. See `config/boot/README.md` for
the selection and migration policy.

One shared renderer (`luma-theme.script.in`) is compiled into the two script
Plymouth themes by `scripts/boot/compile-theme.py`. Desktop defaults to
Hearth/Ink/dots/word/indeterminate; handheld keeps Hearth/Paper/dots/word/activity.
The generated scripts remain checked in for source/composition inspections;
`tests/smoke/mobile-boot-theme-source.sh` rejects drift from their profiles.

The desktop build profile `config/boot/desktop-theme.json` supports Hearth and
Paper layouts, Ink/Paper/Slate surfaces, ring/bar/hairline/dots loaders,
stacked/inline/word/mark lockups, and determinate/indeterminate progress.
Native fractions animate determinate progress; activity never invents service
readiness. Native prompts and daemon messages supply localized text; the theme
adds no canned first-boot/shutdown/authentication claims. Escape remains native.

The compiler retains exact canonical wordmark geometry, separates its orange
stop into a sprite, and generates ring/capsule/dot assets. Only selected profile
assets enter the RPM; stale ring variants are removed on regeneration.

Changing a profile is an administrator/build choice: regenerate checked-in
scripts with `python3 scripts/boot/compile-theme.py --output assets/boot --scripts-only`,
build the new RPM, and compose the corresponding initramfs/deployment. Do not
copy theme files manually into a release image or advertise live user settings.

GRUB owns Threshold's real pre-kernel choices and timeout. Preview timeout policy
with `/usr/libexec/luma-apply-grub-policy --timeout 5 --print-policy`
on an installed image (or the source script in `config/boot`); applying
it requires the existing authorized OSTree composition path. The theme cannot
invent boot entries or select another boot device after kernel handoff.

See `docs/changes/2026-09-07-boot-sequence-audit-001.md` for evidence, license
records, exact rollback, and outstanding physical/security acceptance gates.
