# mesa patches

Applied by `packaging/rpm/mesa.spec` (Fedora's mesa 26.2.5-1 spec, release
`.luma.N`; Source1 and the crate tarballs come from Fedora's SRPM) on top of
`mesa-26.2.5.tar.xz`.

| Patch | Origin | Why |
|---|---|---|
| 0001 | Luma, 2026-11-02; not upstream yet (submit to mesa/mesa, applies to main as of 2026-11-02) | v3d (Raspberry Pi 4/5): a blit from a raster (linear) source, such as a compositor's scanout buffer, into a texture of another format or at another offset filled its tiled temporary with `util_resource_copy_region()`: the whole source level mapped and tiled on the CPU, after a GPU wait. A backdrop blur does this every frame. The temporary is now filled by a TLB load/store job covering only the tiles the blit reads; the CPU copy stays as the fallback (multisample, depth/stencil, non-renderable formats, arrays, 3D). |

Measured on a Raspberry Pi 5 (Luma Pi kernel 7.2.15, 3840x2160, XRGB2101010
linear source, RGBA8 destination, `blittest` in the Pi graphics tools): a
1600x240 dock backdrop 47.6 ms per blit (47.4 ms of it CPU) before, 4.7 ms
(0.9 ms CPU) after; a 1400x1200 panel backdrop 50.4 ms before, 10.6 ms after.
Results are bit-identical. The patch only changes v3d.
