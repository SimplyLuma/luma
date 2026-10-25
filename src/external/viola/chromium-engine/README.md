# Shared Chromium engine inputs

The engine uses one pinned Chromium checkout and one shared product patch stack.
Architecture differences belong in the small GN profiles. Desktop and mobile
hosts use the same engine inputs; their window and device integration lives in
host components and device profiles.

`upstream.env` pins Chromium 151.0.7922.72 and its exact commit. Apply
`patches/0001-shared-product.patch` to that unmodified checkout with `git apply`.
The patch includes the complete 169-file product delta, including binary assets
and executable modes. It was independently applied against the exact upstream
preimages and checked byte for byte. It records source inputs, rather than an
assertion about an earlier ARM build's undocumented compiler arguments.

Combine `profiles/common.gn` with the architecture profile. The ARM profile
bounds link memory by disabling ThinLTO; upstream Linux ARM64 CFI is already off.
The x64 profile retains upstream's official CFI/ThinLTO defaults. Both retain the
upstream PGO default. These are explicit producer profiles, and a generated
build graph does not establish runtime qualification.

The optional `0002-native-shared-memory-diagnostic.patch` adds a forced raw
shared-memory capture experiment, explicitly selected by
`--viola-native-shm-frame-probe`. Its default DMA path remains unchanged. The
strict ARM capture translation-unit compile, full Chrome link and complete
runtime assembly have passed. Native rendering remains unqualified. This
diagnostic patch is not an automatic fallback policy or a qualified engine
release.
Do not replace a released engine merely because these inputs build successfully;
its native rendering, input, resize and window lifecycle checks must also pass.

Chromium's license is preserved in `CHROMIUM-LICENSE`. The custom Viola source
headers separately specify GPL-3.0; that notice remains in the patch, with the
GNU version 3 license text preserved in `COPYING-GPL-3.0`. These notices cover
their respective code, rather than assigning Chromium's license to the custom
product delta. Retain the checkout's AUTHORS, LICENSE and all third-party
notices when building or distributing it.
No binary engine or generated build output belongs in this directory.

## Preparing a producer

Start with a Chromium checkout at the exact `CHROMIUM_COMMIT`, with its DEPS
revisions synchronized by `gclient sync`. Run that checkout's maintained hooks
for Clang, Rust, Node and sysroots; keep their exact DEPS package versions. ARM
cross-compilation uses the Linux x64 host tools and the checkout's pinned Linux
ARM64 sysroot. The host compiler version alone does not prove the complete
cross-compilation inputs are installed.

Source-version generation supports the following explicit upstream controls;
use them when building from a verified source export without Chromium's Git
metadata. Do not substitute a surrounding Luma repository's Git identity.

```sh
. /path/to/chromium-engine/upstream.env
export BASE_COMMIT_HASH="$CHROMIUM_COMMIT"
export BASE_COMMIT_SUBMISSION_MS="$CHROMIUM_BASE_COMMIT_SUBMISSION_MS"
export DEPOT_TOOLS_UPDATE=0
```

Keep the concatenated common and architecture GN arguments, evaluated GN
arguments, compiler and sysroot inventory, actual build commands, source and
patch hashes with the producer's private build receipt. Preserve the completed
Ninja outputs for subsequent incremental builds. A shipping runtime also needs
its resource packs, locales, required shared libraries, crash handler and sandbox
configuration; a standalone `chrome` ELF is not a complete runtime.

When a private source export preserves a different owner for nested Git
repositories, keep Git's ownership protection. Validate each actual repository
root and HEAD against the pinned DEPS before granting a process-only
`safe.directory` entry for a metadata generator. The exercised ARM producer
requires literal entries for `third_party/angle`, `third_party/dawn` and
`third_party/swiftshader`. It records those exact environment values and proves
the repository metadata, generators and root Git configuration stay unchanged.
Do not use a wildcard or modify global Git trust for this purpose. An ordinary
checkout owned by its builder does not need this exception.

The exercised bounded service also gives build children a private home and
cache directories inside its admitted build-state tree. Dawn's pinned Go
source generator needs explicit `GOCACHE` and `GOPATH` in that environment.
It uses `GOENV=off`, `GOTOOLCHAIN=local` and `GOFLAGS=-mod=readonly -p=2`, with
the actual Go binary, `go.mod`, `go.sum` and generator hashes retained before
and after the build. This exact source revision has no Dawn vendor directory
or Go workspace; do not override a future revision's vendoring policy by
copying these flags without checking its inputs. The source generator has
actually completed with these controls. Host account homes and global tool
configuration remain outside the build environment.

The exercised Python interpreter also receives `PYTHON_CPU_COUNT=2`. Blink
bindings generation creates its own multiprocessing pool from
`multiprocessing.cpu_count()`, independently of Ninja's job count. The producer
verifies that both `os.cpu_count()` and `multiprocessing.cpu_count()` return two
and records the actual interpreter and generator hashes. An interpreter that
does not honor this control requires a separately verified bounded setup; do not
assume that lowering Ninja concurrency also limits these child pools.

The ARM experiment has compiled the changed capture translation unit and linked
the full Chrome target with the unaltered strict compiler flags. Its complete GN
runtime inventory has been assembled with resource packs, locales, notices and
an independently built sandbox helper. The producer retains its raw binary,
debug data and completed objects. These build results do not establish native
rendering, input, resize or window lifecycle qualification.

## Recorded build sequence

Use the maintained `gclient` checkout workflow outside a containing Luma Git
checkout. Pin `src` to `CHROMIUM_COMMIT` and synchronize DEPS before patching.
The source control and Linux x64 compiler archive identities are recorded in
`toolchain-inputs.json`; the upstream hooks remain authoritative for the other
DEPS tools. Verify these hashes before invoking a recovered hook. Do not replace
missing cross-target tools with a system compiler or a mutable latest download.

```sh
# In an already synchronized exact Chromium checkout:
git apply --check /path/to/chromium-engine/patches/0001-shared-product.patch
git apply /path/to/chromium-engine/patches/0001-shared-product.patch
# Only for the explicitly unqualified SHM experiment:
git apply --check /path/to/chromium-engine/patches/0002-native-shared-memory-diagnostic.patch
git apply /path/to/chromium-engine/patches/0002-native-shared-memory-diagnostic.patch
# Set the version environment above; choose an admitted output tree and job count.
cat /path/to/chromium-engine/profiles/common.gn /path/to/chromium-engine/profiles/arm64.gn > "$ENGINE_OUT/args.gn"
gn gen "$ENGINE_OUT" --root-target=//chrome:chrome --root-pattern=//chrome:chrome
ninja -C "$ENGINE_OUT" -j "$ENGINE_JOBS" chrome
```

The exercised ARM producer uses the above Chrome root selection to avoid loading
omitted Blink web-test fixtures. Sandbox helper generation is a separate graph
with the same architecture/toolchain arguments and
`--root-target=//sandbox/linux:chrome_sandbox` and
`--root-pattern=//sandbox/linux:chrome_sandbox`; build its `chrome_sandbox` target.
Retain raw GN warnings and evaluated flags. Record exact output/runtime manifests,
not just the source recipe. Source exports may omit only explicitly inventoried
non-build fixture paths; a pruned export is not a full upstream checkout.
