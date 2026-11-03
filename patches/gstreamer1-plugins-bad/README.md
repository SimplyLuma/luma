# gstreamer1-plugins-bad patches

Applied by `packaging/rpm/gstreamer1-plugins-bad-free.spec` (Fedora's spec,
release `.luma.N`) on top of Fedora's `gst-plugins-bad-1.28.8.tar.xz`.

| Patch | Origin | Why |
|---|---|---|
| 0001 | Luma, 2026-11-02; not upstream yet (submit to gstreamer/gstreamer) | `v4l2slh265dec` on the Raspberry Pi HEVC decoder (`rpi-hevc-dec`, Luma Pi kernel) negotiates DMA_DRM `NV12:0x0700000000000004` (SAND128), which has no GstVideoFormat; GstVideoBufferPool rejects it and negotiation fails with every sink. GStreamer main (2026-11-02) has the same code. |

Nothing here adds a codec Fedora excludes: `h265parse` and `v4l2slh265dec`
are in Fedora's package already; the patch only fixes buffer-pool setup.
Measured on a Pi 5 (Luma Pi kernel 7.2.15): 1080p30 HEVC decodes at 196 fps,
2160p60 at 62.6 fps, 19-29 % of one CPU core, through `glupload`
(Mesa v3d imports SAND128 as an external texture).
