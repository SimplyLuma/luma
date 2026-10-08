# foliate-js (vendored)

- Upstream: https://github.com/johnfactotum/foliate-js
- Commit: 052123beafed921a9a2a45ef6330c235289a634e (the submodule pinned by Foliate 3.3.0, the version Fedora 44 ships)
- License: MIT (see LICENSE)
- Files: `epubcfi.js`, unmodified.

Leaf reads and writes EPUB CFIs with it. Foliate's paginator and view are not used: Leaf's page
geometry, sentence model and read-aloud are specified by its own handoff and live in `../../reader.js`.
