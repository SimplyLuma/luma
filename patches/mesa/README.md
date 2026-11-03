# mesa patches

Applied by `0000-luma-fedora-spec.patch` to Fedora's mesa 26.2.5-1 spec
(release `.luma.N`) on top of `mesa-26.2.5.tar.xz`; built by
`scripts/packages/build-mesa.sh` (desktop-components group `mesa`).

| Patch | Origin | Why |
|---|---|---|
| 0001 | Luma, 2026-11-02; not upstream yet (submit to mesa/mesa, applies to main as of 2026-11-02) | v3d (Raspberry Pi 4/5): a blit from a raster (linear) source, such as a compositor's scanout buffer, into a texture of another format or at another offset filled its tiled temporary with `util_resource_copy_region()`: the whole source level mapped and tiled on the CPU, after a GPU wait. A backdrop blur does this every frame. The temporary is now filled by a TLB load/store job covering only the tiles the blit reads; the CPU copy stays as the fallback (multisample, depth/stencil, non-renderable formats, arrays, 3D). |
| 0002 | Luma, 2026-11-02; not upstream yet | v3d `texture_subdata()` tiled whole-texture uploads on the CPU (scattered utile writes into the BO). Whole 2D levels of at least 128x128 now go through a raster staging BO and a TFU job. Pi 5: 1600x1200 RGBA8 upload CPU time 2.96 -> 1.61 ms, 3840x2160 13.5 -> 7.8 ms; bit-identical. |
| 0003 | Luma, 2026-11-02; not upstream yet | 0001 still filled a temporary as large as the whole raster source (33 MB at 4K) per blit, and the BO cache hands out no busy BO, so the kernel zeroed new pages every blit (14% of the compositor's frame). One TFU job now tiles only the blit's region (the source described as a raster image starting at the region) into a region-sized temporary. Pi 5 dock-sized backdrop blit 2.26 -> 1.18 ms (0.13 ms CPU); bit-identical. |

Measured on a Raspberry Pi 5 (Luma Pi kernel 7.2.15, 3840x2160, XRGB2101010
linear source, RGBA8 destination, `blittest` in the Pi graphics tools): a
1600x240 dock backdrop 47.6 ms per blit (47.4 ms of it CPU) before, 4.7 ms
(0.9 ms CPU) after; a 1400x1200 panel backdrop 50.4 ms before, 10.6 ms after.
Results are bit-identical. The patch only changes v3d.
