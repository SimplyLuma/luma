
`0014-constraints-Keep-every-window-inside-one-monitor-work-area.patch` adds a
highest-priority constraint: every normal window, dialog, utility and splash
screen is fully inside the work area of the monitor it overlaps most (else the
nearest), shrunk to fit but not below its minimum size, from its first frame.
It covers app-requested positions and sizes (X11 hints and ConfigureRequests,
Electron restoring saved bounds), placement, monitor changes, un-minimize and
late Wayland configure acknowledgements, which are now constrained outside a
drag. The person moving or resizing a window is exempt, and a window the person
left partly outside stays there until something else moves it or the monitors
change. Tested by the app-requested position sweep in
`tests/tiling-shell/window-memory/resume-trace.js`.

`0015-compositor-Cache-the-blurred-backdrop-and-repaint-only-what-changed.patch`
keeps, per blurred surface and view, the backdrop at the blur's downscaled
resolution and updates only the framebuffer blocks repainted beneath the
surface, so a redraw touching a blurred window grows by the damage plus the
blur's sampling reach instead of the window's whole sampled area. Before this,
a drag near stacked Frost or Glass windows repainted most of both monitors and
re-blurred every blurred window each frame. The cache is rebuilt from a full
repaint on move, resize, scale or radius changes and after a culled frame;
offscreen paints keep the uncached path. `LUMA_MUTTER_BLUR_CACHE=0` disables it
for A/B runs; `MUTTER_DEBUG=render` logs each blurred paint. Measured by
`tests/gnome-shell/blur-motion/`.

`0016-display-Let-the-shell-reserve-work-area-on-any-monitor-edge.patch`: `meta_display_set_monitor_edge_reservation(display, monitor, side, depth)` and `meta_display_clear_monitor_edge_reservations()`. A reservation keeps one monitor's work area `depth` clear of one edge, shared with another monitor or not; Mutter drops struts on shared edges. The Luma Shell (0171) reserves Dash bands on shared edges with it (the sides of a middle display); outer edges keep struts. The work-area inset of 0011 still applies only to edges nothing else moved in. Exercised by the Shell oracle `tests/gnome-shell/shelf-arrange/edges3.js` and `tiling3.js` on three monitors (maximize and Tiling Shell tiles).

`0019-input-Adjustable-scroll-speed-for-mice-and-touchpads.patch`: per-device scroll speed, applied where libinput hands scroll events to Mutter so every Wayland and Xwayland client and the Shell follow it. Settings `org.projectluma.peripherals.mouse` and `.touchpad` `scroll-speed` (0.25–4.0, default 1.0; schema shipped in mutter-common) split devices like natural-scroll: touchpads use the touchpad value, every other pointer the mouse one. Finger and continuous deltas are multiplied (the end-of-gesture zero stays zero, so client kinetic scrolling stays proportional); wheel value120 is multiplied with the sub-unit remainder kept per device and dropped on reversal, and delivered at most one detent per event. At 1.0 the upstream calls run unchanged. The arithmetic is tested by `src/tests/scroll-scale-test.c` in the package's `%check`, which requires all six tests to pass. Same approach as KWin's per-device scroll factor.

`0020-x11-Keep-the-geometry-the-shell-chose-before-map.patch`: a shell that places a new window in its initial `configure` signal gives a frame rectangle, and `meta_window_apply_config()` applies it as one, but `meta_window_x11_configure()` also stored it in the size hints, which hold a client rectangle. When a decorated window's frame arrived, `meta_window_x11_initialize_state()` read those hints back as a client rectangle and grew them by the decorations, so a window placed in a tile briefly became one title bar taller, was pushed back inside the work area, and had to be moved again. The size hints now get the matching client rectangle, and a decorated window keeps the chosen frame rectangle until its frame is there. Windows the shell does not place are configured as before. Measured by `tests/tiling-shell/open-in-place/` ("sized once, at its tile").
