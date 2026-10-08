# Darkroom

Darkroom is Project Luma's native, adaptive image editor. The original image
is referenced but never overwritten. A `.luma-darkroom` document contains a
versioned editing recipe: development settings, crop, layers, masks, retouch
operations, history, snapshots, export presets, metadata edits, and per-
arrangement workspace state.

The GTK application uses Luma UI for identity, panes, contextual actions,
histograms, and curves. LibRaw and NumPy provide 16-bit camera decode and
floating-point RAW development; Pillow/ImageCms provide raster decoding,
spatial finishing and 8-bit delivery. See [RAW precision and limits](docs/linear-raw-development.md). Full-resolution export always reopens the original rather than
upscaling a display proxy.

Darkroom is distinct from Photos. Photos launches this app with the selected
asset URI. Darkroom may export a derivative back into the user's chosen Photos
location, but does not mirror or own the photo library.

## Source safety

- Save and Save As write editable `.luma-darkroom` JSON atomically.
- Save Version creates a named recipe snapshot.
- Export renders a delivery file and cannot silently claim to preserve layers.
- Autosaves live below the user's state directory and are offered after an
  abnormal exit.
- Missing linked originals retain the complete recipe and expose Relink.

## Development

```sh
PYTHONPATH=src/luma-darkroom python3 -m unittest discover \
  -s src/luma-darkroom/tests -p 'test_*.py' -v
```

