# libmanette patches

Applied by `packaging/rpm/libmanette.spec` (Fedora's 0.2.13-2 spec, release
`.luma.N`) on top of `libmanette-0.2.13.tar.xz`.

| Patch | Origin | Why |
|---|---|---|
| 0001 | Luma, 2026-11-02; not upstream yet (submit to GNOME/libmanette) | The evdev backend watched its device fd for `G_IO_IN` only. After an unplug the fd reports POLLHUP/POLLERR forever; the GIOChannel watch never dispatches but stays in the poll set, so the owning main loop spins at full CPU until the device is finalized, which GJS/Python can put off for minutes. In TV Mode that was the Shell (tvInput.js reads pads through libmanette): every pad disconnect, Bluetooth sleep or USB unplug left gnome-shell at ~100 % CPU and frames late. The watch now includes HUP/ERR/NVAL and removes itself, and draining stops when `libevdev_next_event()` fails (-ENODEV kept it looping). Reproducer (a script holding the device, a uinput pad unplugged): 100 % CPU after the unplug before, 0 % after. |
