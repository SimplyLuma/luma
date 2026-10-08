# Darkroom document format 1

The top-level `format` is `org.projectluma.darkroom-document` and
`schema_version` is an integer. Unknown newer versions are rejected without
rewriting them. Saves use an fsynced temporary sibling followed by
`os.replace`; recovery records use the same atomic path.

The document stores source identity and relink metadata, not source pixels.
Normalized crop, mask, transform, and retouch coordinates remain independent
of preview resolution. Cached previews, GTK objects, decoded pixels, histogram
bins, and background-task state are derived data and are never serialized as
document truth.

The current baseline renderer accepts JPEG, PNG, TIFF, WebP, BMP, GIF, and the
formats supported by the installed Pillow build. Camera RAW decoding requires
the packaged LibRaw dependency. RAW development keeps 16-bit camera samples and
uses floating-point adjustments before display conversion. These derived buffers
never enter the document. See [processing evidence and limits](linear-raw-development.md).

