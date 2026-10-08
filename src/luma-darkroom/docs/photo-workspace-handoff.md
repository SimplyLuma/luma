# Photo workspace redesign

Darkroom's photo workflow now uses AppKit islands and toolbars, following the
installed Canvas panel structure. The photo gets the main work area. The right
inspector contains Adjust, Crop, Retouch, and History; photo information is in
History. Legacy layer documents remain supported through View → Advanced Layers.

Changes belong to `luma-darkroom`: window.py, engine.py, darkroom.css and focused
tests. No shared platform files or new dependencies were changed. Canvas was a
layout reference; no Canvas source or third-party product assets were copied.

- Adjustments are grouped into Light, Color, Detail and Tone curve. Six looks
  use the real recipe pipeline and create one undo step. Looks replace color
  treatment controls while preserving exposure, crop and detail adjustments.
- Crop has framing presets, movable framing, straighten, rotate and flip.
  Presets set the frame; dragging an edge returns to free framing. Rotation and
  flip return to the edited view; framing coordinates retain the document's
  original-source coordinate system.
- Retouch exposes the existing point-based Heal and Clone engine. It does not
  claim generative removal, subject selection or continuous brush strokes.
- Comparison is honestly labeled Original / Edited. Fit removes forced image
  sizing; zoom requests full-resolution rendering and supports scrolling.
- Switching open photos retains their editor/undo state. Existing recovery
  writes preserve dirty inactive documents; originals are never overwritten.
  Dropping a photo opens it instead of silently compositing a new layer.
- EXIF orientation is applied on decode. Exports retaining metadata normalize
  the orientation tag so other viewers do not rotate the pixels a second time.
- Native breakpoints switch to Photo / Edit at compact widths. Tab is available
  for ordinary keyboard focus; Ctrl+backslash toggles panels.

## Validation

Run with the installed Luma GTK/AppKit and Pillow, with a temporary display and
recovery store. `tests/workspace_runtime.py` requires `DARKROOM_WORKSPACE_OUTPUT`
for visual captures. It covers light/dark desktop, empty/loaded states, widths
360/420/500/1024, undo/redo, looks, comparison, crop/retouch/history navigation,
zoom and switching photos. `tests/crop_frame_runtime.py` checks pixel placement
of crop shading and handles; it snapshots the canvas directly so toolkit window
shadows do not skew pixel coordinates.

Observed on 2026-09-22: all 28 application tests passed; desktop and compact
runtime smoke checks passed; crop-frame pixel checks passed; and the workspace
runtime checks passed. Light and dark captures and the 360/420-pixel layouts were
visually inspected. The source preview launched on the ThinkPad without logged
errors. Appearance is inherited from the shared system policy; tests change an
isolated in-memory settings backend, never the desktop preferences. The runtime
checks use generated fixtures, not the user's photo library. The review preview
uses a separate recovery folder. Package build/full release checks belong to the
coordinator's end-of-day batch.

## Limits and rollback

The existing baseline adjustment engine remains. RAW input now uses the
required LibRaw package; see `raw-support.md`. Adjustments are not a new
high-bit-depth RAW/color-science pipeline.
Physical touch, full translation/RTL, and packaged cold-boot checks remain release
gates. No claim of those checks is made by the desktop captures.

Quit the source preview and launch `/usr/bin/luma-darkroom` to return to the
installed build. Revert the feature commit to remove the source changes. No
recipe format or installed package was changed. Keep any saved editable documents
and recovery files when reverting.

## Gallery and interactive preview — 2026-09-22 follow-up

The bottom Gallery handle remains visible in wide and compact Photo/Edit views,
including when the inspector is hidden. G toggles the drawer. Add Photos accepts
multiple files; Browse Folder (Ctrl+Shift+O) reads one folder without recursive
indexing. Opening a single photo into an empty workspace also discovers its
siblings. The current collection is session-local, not a persistent photo catalog.
Existing edit documents and recovery remain unchanged. Folder selection does not
copy, move, or import originals. Tiles show the source until edited in this session.

The drawer has 24 tiles per page and one thumbnail worker, with at most 128 cached
textures. Hidden drawers stop requesting more thumbnails. RAW tiles use LibRaw's
embedded thumbnail API; a missing thumbnail leaves an openable photo tile rather
than demosaicing every original in the background. Main previews and exports
continue to decode RAW sensor pixels. No new runtime dependency or daemon.

The preview keeps a prepared source and original comparison image for the active
file and resolution. File path, nanosecond mtime and size invalidate that cache.
Dragging uses a 960px preview and retains completed intermediate frames, with one
worker and one latest desired recipe; a different document's late frame is rejected.
After 180ms without a change, the normal 1800px fit preview (or full resolution at
actual-pixel zoom) is rendered. Texture upload uses Gdk.MemoryTexture, removing
PNG compression/decompression from the interactive path. Export always uses the
full-resolution source. Metadata, history and gallery rebuilds are kept out of the
slider hot path. Quit now closes windows through their edit-preservation hook.

Measured on the ThinkPad with the same public Fujifilm X-T20 compressed RAF:
previous warmed-source renders took 206–254ms, redundant original renders
202–251ms, and PNG compression 391–423ms (excluding original PNG upload and GUI).
The new prepared 960px exposure renders took 5.5–5.6ms, with raw texture creation
0.5–1.4ms; embedded RAW thumbnail extraction took 93ms. These are local pipeline
measurements, not end-to-end input/display latency or guarantees for all edits.
The first full RAW decode still takes several seconds on this CPU.

Validation: 32 unit tests pass. The prepared-source regression fails against the
previous engine and passes with the change. `tests/gallery_preview_runtime.py`
checks folder filtering/non-recursion, paging, always-accessible compact gallery,
source reuse, intermediate-frame delivery, latest-value coalescing, photo-switch
races, drag undo, retained edits and immediate Quit recovery. Existing workspace
checks cover light/dark, 360/420/500/1024 widths, comparison, crop, undo, and broken
RAW recovery. All use disposable fixtures and an isolated recovery store.

Workflow references (behavior only; no code copied):
- [Lightroom filmstrip and workspace](https://helpx.adobe.com/lightroom-classic/desktop/workspace/workspace-basics.html)
- [Photos editing basics](https://support.apple.com/en-ke/guide/photos/pht304c2ace6/mac)
- [Lightroom Smart Previews](https://helpx.adobe.com/lightroom-classic/desktop/viewing-photos/lightroom-smart-previews.html)
- [LibRaw thumbnail C API](https://www.libraw.org/docs/API-C.html)

Rollback: revert this follow-up commit and keep saved documents/recovery files.
No schema migration, library database, system configuration or original writes.
Package/release, physical-touch and cold-boot gates remain with the coordinator.

## RAW precision correction

The earlier 8-bit development boundary described above is superseded by the
[linear RAW development correction](linear-raw-development.md). Camera samples
remain 16-bit, and primary RAW adjustments use float32 before display conversion.
The linked document records tests, dependency changes and remaining limits.
