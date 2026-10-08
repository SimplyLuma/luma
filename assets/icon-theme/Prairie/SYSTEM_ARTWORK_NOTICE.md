# Approved system artwork snapshot — 7 September 2026

The frozen `system-manifest.json` names 51 approved regular system icons from
41 unique user-supplied SVGs. The original bytes, approval metadata, inventory
and construction notes are retained in the SRPM's `system-artwork.tar` under
`artwork/system-20260907`. Production staging reads those repository sources,
not a Downloads folder or a running Design Center.

Luma integration changes lookup names and theme metadata only. Transparent
padding, path geometry, gradients, masks, internal references and shadows are
unchanged. Approved aliases remain byte-identical. The empty Trash icon also
serves its pre-existing Applications-context dock alias. The previous full
Trash artwork is preserved and may not match the new empty icon's family.

Lucide-derived geometry retains the exact embedded ISC and Feather-derived MIT
notices in the supplied SVGs. The existing package's
`LUCIDE_REFERENCE_LICENSE.txt` is also shipped. Five supplied Lucide reference
SVGs (house, monitor, network, panels-top-left, recycle) remain in the source
snapshot, with their file digests in the integration record. The supplied files
do not establish an exact Lucide release for every incorporated glyph; none is
invented here. Upstream reference packs and screenshots are not imported.

Original artwork authorship and redistribution authorization have not been
certified by this package integration. `LicenseRef-Luma-System-Artwork-Pending`
records this unresolved part of the private candidate; it is not a grant of
rights. Resolve that record before public distribution or Stage promotion.
Existing application artwork and existing ISC/MIT/Apache-2.0 notices retain
their earlier terms. An agent cannot provide owner certification or DCO signoff.
