# libcamera patches (desktop)

Applied by `packaging/rpm/libcamera.spec` (Fedora's spec, release `.luma.N`)
on top of Fedora's `libcamera-v0.7.2.tar.bz2` and Fedora's own patches 0001
and 0002. `patches/libcamera` is the separate FP6 series on another base.

| Patch | Origin | Why |
|---|---|---|
| 0003 | Luma, 2026-11-20; not upstream yet (submit to libcamera-devel) | The software ISP statistics index the 64-bin luminance histogram with the raw sample value. Unpacked 10/12-bit samples sit in 16-bit containers, so a frame with larger values (the Intel IPU7 ISYS returns frames its firmware rejected, filled with 0xffff) indexes past the array; the `_GLIBCXX_ASSERTIONS` check aborts WirePlumber, and the PipeWire camera node goes with it. Clamps to the top bin and adds the `swstats-cpu` test, which `%check` runs. libcamera main (2026-11-20) has the same code. |
