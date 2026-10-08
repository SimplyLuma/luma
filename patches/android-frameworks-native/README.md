# Android task layer ownership

The maintained patch extends Waydroid's existing layer-name transport to carry
task ownership for video and BLAST child surfaces. Android WindowManager already
publishes `METADATA_TASK_ID` on task containers. SurfaceFlinger resolves that
metadata through drawing ancestry, accepting only system-owned containers, and
leaves unrelated layer debug names unchanged. No HIDL or public application ABI
is changed, and no new service is introduced.

The baseline is the resolved LineageOS 20 frameworks/native tree after official
Waydroid patches, observed at `e8f70d446d3361b79fc2d96c07b21795a033921a`.
Existing AOSP/LineageOS Apache-2.0 notices remain intact. Luma's additions are
Apache-2.0. Source preparation records the exact baseline and patch digest and
retains dirty-tree/manifest drift refusal for both incremental and clean builds.

This is a compatibility rendering correction, not a replacement for Android's
application sandbox. The paired hardware patch resolves inherited child task
IDs to the existing task/package record before filtering client composition.
See [Android frame evidence](../android-hardware-waydroid/README.md)
for observed results and open multi-application, media and release gates.
