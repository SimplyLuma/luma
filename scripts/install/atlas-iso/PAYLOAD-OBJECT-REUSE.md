# Exact payload-object reuse prototype

The measured Oct27 writer processed 153,155 OSTree object nodes in 2,194 seconds
(about 70 nodes per second). Replacing the whole repository makes the writer read
unchanged compressed bodies from the host filesystem. A small package delta does
not imply a small repository walk or small filesystem I/O cost.

`payload-object-reuse.py` implements a tested planning primitive. It is deliberately
not connected to the shipping mapper: current releases keep the existing path.
The primitive creates an xorriso options file that keeps byte-identical donor
objects, maps changed/new bodies, removes objects absent from the new exact set,
and completely replaces refs, config, summary, signatures and other allowed
mutable repository roots. Detached `.commitmeta` always maps from the new source,
even when the name and body match. A `.filez` name never substitutes for the SHA256
and length of its compressed bytes. The command file avoids an unbounded argv.

The prototype accepts only a single desktop architecture/profile/channel and its
collection metadata head. It rejects parent-dependent repositories, private or
unexpected top-level data, object-path escapes, symlinks, changed metadata, and
new object inventories that differ from their manifest. Unknown repository
formats fail closed rather than broadening the accepted paths.

## Custody needed before production activation

1. During the authenticated export, while objects are hot, produce and protect a
   complete body manifest with schema `org.projectluma.iso-payload-bodies/v1`.
   Record source revision, candidate, architecture, profile, ref and exact heads;
   config text, allowed metadata roots and SHA256/size records for every mutable
   metadata file; and SHA256/size records for every compressed object file.
   Normal complete OSTree closure and commit/summary GPG validation still run.
2. Bind the producer's manifest hash to the existing export custody receipt.
   After creating the donor ISO, verify its **actual** exact object inventory and
   bodies against that manifest, then bind that verification to the complete ISO
   SHA256. `verify_object_inventory` provides the full body check for an extracted
   ISO repository. Source manifests alone do not prove what was written to an ISO.
3. Authenticate both externally pinned manifests and the full donor ISO before
   calling `write_options`; keep source and donor read-only for the operation.
   `load` rejects writable/symlink manifests and external-pin mismatches. Reused
   host bodies are not read again: their new intended bytes are authenticated by
   the new export manifest and supplied by the verified donor. Replacement bodies
   and all mutable metadata are independently read and checked before mapping.
4. Invoke the options file at the existing repository-map hook while retaining
   the existing clock, EFI, fonts, volume ID, source, GPG and payload guards.
   Verify the final ISO's exact object set and actual bodies, refs, signatures and
   full closure; retain the plan and full ISO digest. Do not silently fall back
   to keeping unreferenced donor objects or qualifying the build from a plan.
5. Benchmark the exact same signed corpus and tools against whole-repository
   replacement on the intended storage. Keeping ISO bodies avoids host object
   reads, but ISO output still writes the full medium and xorriso still walks
   nodes. The fixture does not establish an end-to-end timing target.

The focused tests use real xorriso extraction to compare the entire output file
set and bytes, and a real signed OSTree fixture to verify summary/commit trust,
pull the complete candidate closure and read its content. They also test changed
compressed encodings, detached signatures, removed metadata/objects, corrupted or
missing donor bodies, private/parent input rejection, pinned manifests and bounded
mapping reads for 1,000 unchanged object bodies. No production keys or artifacts
are involved.
