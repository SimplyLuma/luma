# minigbm patches

Applied to `external/minigbm` by `scripts/android/prepare-prairie-waydroid-tree.sh`.

`0001` describes gbm_mesa YV12 (`YVU420_ANDROID`) buffers as the three planes
they hold. The backend allocates YV12 as a GR88 surface of half width and
three-halves height, but described it as a single R8 plane, so the handle
carried no chroma planes: decoders and `lockYCbCr` users wrote and read
chroma at the wrong place, and EGL imports of the buffer (SurfaceFlinger's
RenderEngine, the host compositor) failed. Luma, Cr and Cb now sit at the
surface stride and half of it, filling the allocation exactly; chroma rows
round up for odd heights. Each plane holds its own descriptor for the one
buffer instead of sharing one that would be closed once per plane.
