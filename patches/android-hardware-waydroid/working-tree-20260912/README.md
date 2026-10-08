# hwcomposer working tree, 2026-09-12

These are **sources, not patches**, and they are here because the tree they
came from is not durable.

The Waydroid hwcomposer was being built out of
`/tmp/luma-arm-overlay-20260911/source` on the build host. That path is a
**tmpfs at 93% full** — the tree lives in RAM and goes away on reboot — and it
is not a git checkout, so there is nothing to diff against upstream to produce
a proper `0005-…patch` without first restoring a pristine lineage tree.

Two changes exist only in these files and in no patch in this series:

- **`wayland-hwc.cpp` — `luma_logical_to_px()`** and its use in
  `request_task_resize`. Android was being told a window's size in logical
  pixels while the surface was scaled, so at 1.25 every window carried a gap
  of about twenty percent down its right side and along the bottom. Built and
  deployed to the ThinkPad on 2026-09-11; never captured here until now.

- **`prairie-titlebar.cpp` — the window shadow.** It was a single Gaussian at
  four percent opacity, matched against a small island's shadow rather than a
  window's, and halved again by the edge-coverage term, so its darkest pixel
  came out at alpha 5 of 255. That is why Android windows appeared to have no
  drop shadow. It is now the three-layer elevation Luma's own windows carry,
  taken from the window elevation rule in the patched libadwaita, and it
  follows the surface treatment rather than assuming one. **Not yet compiled**:
  the build host had 2.4 GB free on the tmpfs holding the tree, which is not
  enough to run soong.

To turn these into patches: restore a pristine lineage-20 `hardware/waydroid`,
apply `0001`–`0004`, then diff these files against that.
