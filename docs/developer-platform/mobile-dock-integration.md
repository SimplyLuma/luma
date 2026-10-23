# Approved mobile dock layout candidate

This bounded patch targets the native v71 shell in Mobile simulator
20261022-r10, after the simulator's downstream patches through 0043. That
baseline is not yet present on public main. Keep this candidate as a draft
until its prerequisite shell baseline is integrated; it is intentionally not
listed in the current main branch's RPM recipe. Do not add the whole preview
patch stack to this PR just to make the candidate apply.

`patches/phosh/0044-luma-uniform-dock-layout.patch` gives the four app slots
and Search equal horizontal allocation, centers their row within the existing
88px dock, uses a 55px Search tile with 13px corners, and normalizes the
simulator fallback browser's visible tile size. It preserves app bindings,
Search's action, source artwork and upstream attribution. The fallback browser
is not an installed or working browser; this patch changes only its artwork
canvas. The approved clock and battery/network changes are separate.

Builder handoff: confirm these pre-patch SHA-256 values in the owning mobile
source, apply with `patch --fuzz=0 -p1`, then wire the patch into that source
package's recipe and builder. These hashes deliberately reject public main's
older launcher. Rebuild and qualify that component before promoting artifacts
or release pins. No full package build or physical-device qualification is
claimed by this draft.

- `src/ui/app-grid.ui`: `b3b852cd4090977271dabdee45294df56d5d695f56f6119efbea599bb787b01c`
- `src/stylesheet/common.css`: `14fdbfbe924a3a586329d9028e6206157b855a4b93e8060d7a8672f3f96be8fc`
- `data/icons/luma-v4-browser.svg`: `6c0fbce2c049e883f9fdcbed508ac619f0c2922722b69fa2e0847d201ce7be2a`
