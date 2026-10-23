# Shared desktop browser engines

The native Viola host, packaging and UI source are shared between desktop CPUs.
The engine is an architecture-specific retained input, with no Chromium rebuild
needed to change the host UI.

| CPU | Retained engine | Archive SHA-256 |
| --- | --- | --- |
| x86_64 | native30 | `2760bcadbb3864ffb637888b06e9717642c1add31656c2b81c6d0fb7fd5b1028` |
| aarch64 | native31 | `325f3ca8be682bbe6c267251a29315f01229a52971b4627c972dbe0a32552f30` |

The ARM input is the exact Source0 from the previously built mobile wrapper12
SRPM, whose SHA-256 is
`687d82f9c3edf498091a0c86526121bde99749d5b0b330d90781588bbc5571f3`.
Its original archive contains 248 engine files; all match the retained native31
manifest. The executable is ARM64, SHA-256
`4a0667c2551e8daaefa312e154732676fea9de84fb7689529c16509b45943137`.
Original engine provenance identifies Viola source commit
`2903d8558c752b5a554a1a47b4ea7219ba1a31ef`, corresponding to retained native31
source plus RPM-verifier patch
`85a98c21198086999f3113ec6cfba063f9bfac213d0a640639d4d01153916dd2`.
Upstream engine notices and license files remain inside the admitted archive.

This input recovery is not a claim that the current desktop host has passed ARM
runtime checks. The ordinary RPM check suite and real guest browser launch must
pass before admitting the resulting package. The architecture chooses the
engine input; it does not select a second application source tree or UI fork.

Both retained engine inputs are available in content-addressed
`media.simplyluma.com/inputs/viola-engine/<archive-sha256>/<filename>` paths,
with original license notices beside them. Native browser CI selects both CPUs
for shared host or packaging changes. An architecture-specific engine manifest
change selects only its CPU; workflow-only edits preserve already checked
packages. An explicit workflow dispatch requests fresh builds on both CPUs.
