# Quick View placement and render oracle

A headless Luma Shell (`gnome-shell --headless` with two virtual monitors) with
the real Filer and the candidate `luma-quick-view`. `probe.js` applies a
mixed-scale layout, opens Filer on each monitor in turn (centred, top-left and
bottom-right of the work area), presses Space on a real selection, steps
through every file kind with Right, and closes with Escape.

Each case records the preview's frame, its monitor, whether it is transient for
the Filer window, whether it has keyboard focus, whether it is inside the work
area of Filer's monitor and whether it is centred on Filer (or pushed in by the
work-area edge). `results.json` and one PNG per file kind land in `QV_OUT`.

Layouts (`QV_LAYOUT`): `desk` (5120x1440 at 1x beside a 1920x1200 panel at
1.25x), `stacked` (the panel above the ultrawide) and `hidpi` (2880x1800 at 2x
beside 1920x1080 at 1x). `QV_THEME` is `light` or `dark`.

Container: Fedora 44 with the nightly Luma repository's `gnome-shell`,
`mutter`, `gtk3`, `luma-shell-state`, `nautilus` and `prairie-icon-theme`, the
candidate RPMs, `mesa-dri-drivers` and fonts. Remove the system bus's
`org.freedesktop.login1.service` so Shell does not wait for logind. Run
`run.sh` as root; it starts the system bus and drops to an ordinary user,
because Filer does not run as root.
