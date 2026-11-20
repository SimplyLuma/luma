# noopenh264 patches

Applied by `0000-luma-fedora-spec.patch` (to Fedora's 2.6.0-4 spec, release
`.luma.N`) on top of `noopenh264-2.6.0.tar.gz`; built by
`scripts/packages/build-noopenh264.sh` (desktop-components group `noopenh264`).

| Patch | Origin | Why |
|---|---|---|
| 0001 | Luma, 2026-11-19; Luma-specific | noopenh264 is the stand-in Fedora links GStreamer's openh264 plugin and FFmpeg against, because only Cisco may distribute the real OpenH264 binary. On Fedora Workstation dnf swaps in Cisco's `openh264` package from the fedora-cisco-openh264 repository; an image-based system cannot, and layering it would make every update depend on that repository. Each entry point now forwards to a real library at the path set by the new `real_library` option (the spec sets `/var/lib/openh264/libopenh264.so.8`, where `luma-openh264.service` puts Cisco's binary after downloading it from Cisco) when that file exists, and is the old stub otherwise. The library is opened `RTLD_LOCAL`, once; a path naming the stub itself is ignored. Checked with Fedora 44's gstreamer1-plugin-openh264: no file, `openh264enc` fails to start as before; Cisco's 2.6.0 binary in place, a 640x360 encode and its decode succeed without a registry rebuild. |
