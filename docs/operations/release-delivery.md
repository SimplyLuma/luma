# Release delivery

Luma delivers OS updates, application updates and installation media separately.
A source commit or successful build does not publish a release. Each delivery
must retain its source identity, actual qualification results and signatures.

## Release cadence

Collect ordinary OS fixes into one Nightly release at the end of the day's
work, when the release owner closes the batch. Qualify changes during the day
without publishing each intermediate candidate. Match that release with one
installer ISO; reuse already qualified artifacts instead of rebuilding them
just to change publication metadata.

An additional same-day public OS release is an exception for an urgent issue,
such as session crashes, data loss, or a security fix. Record the reason and
retain its predecessor. Candidate suffixes identify build attempts, not a
promise that every number was published. Show the exact offered build identity
alongside its brief, concrete changes so users can distinguish releases.

Independent app updates do not require another OS build or ISO. Refreshing a
signed update graph likewise does not create a release. The legacy Nightly
timer does not authorize publishing an unreviewed intermediate candidate.

## OS updates

Build and export the candidate through the normal OS pipeline, then complete
the fresh-install, no-account, update and rollback gates. Retain the genuine
gate result, provenance and signed release manifest. Failed or skipped phases
do not become passing evidence when publication is retried.

Mirror the immutable OSTree objects to R2 using a frozen inventory and complete
checksum comparisons. Coverage includes the candidate, retained rollback
commits, every head advertised by the served repository and the collection
metadata created by normal channel publication. Existing matching objects can
be reused; an earlier inventory cannot establish coverage of newly created
metadata.

Installed clients keep their canonical repository address and verify commits
and summaries with their installed release key. Verify immutable delivery
before exposing the corresponding signed refs and summary. Then qualify the
canonical HTTPS path with an actual trusted client pull, including both commit
and summary verification, before exposing an update graph that offers that
release. Complete signed summary readback is required; a redirect or one
downloaded object is insufficient.

The early Beta installation bridge must wait until its Nightly destination is
qualified and publicly reachable without staff credentials. Preserve an
explicit user's channel choice. Signed graph refresh uses reviewed, sealed
release inputs; see [approved OS graph refresh](approved-os-graph-refresh.md)
for admission and activation. Refreshing an approved graph does not admit a
new release or build another image.

## Application updates

The signed application feed has its own publication and compatibility checks.
An app update can ship independently when its runtime and native host
requirements are satisfied. Preserve other application and runtime heads when
publishing a targeted update, and check against the versions already shipped
in installation media so that an update check cannot offer an older build.

Keep the application's identity and user storage intact. Updates must preserve
local history, bookmarks, documents and settings; they must not require
uninstalling the application or deleting its data. A host requirement that
needs an OS update must be declared rather than bypassed.

### Standalone Linux applications

Keep the public application commit and complete build inputs pinned in its
Depot recipe. Shared toolkit sources, symbolic icons, engine dependencies,
licenses and required corresponding sources are release inputs too. A desktop
theme installed on the developer's machine is not a package dependency.

Use a versioned GitHub draft release for download assets and source archives.
Qualify the built package, seal the publisher control and artifact inputs, and
sign through the separate networkless publisher. Publish runtime objects and
delta generations before indexes and signed summaries. Only OSTree objects
are immutable: regenerated delta paths and their indexes require checksum
replacement and HTTP revalidation. A failed delta upload stops publication.

Test an empty Flatpak installation against the canonical HTTPS remote without
disabling deltas or signature verification. Confirm required extensions install
automatically, then launch the exact signed application and exercise document
save/reopen and export. Include a screenshot captured from that application in
its package metadata and Depot listing. Add only the admitted listing to the
current signed catalogue, preserving other releases, and publish media before
the newly signed catalogue. Verify the public catalogue signature and entry.

Publish the GitHub release with the signed Flatpak, a stable `.flatpakref`,
checksums and corresponding sources. Both downloads use the same Depot identity
and runtime. If the remote's default branch is beta, update the app's beta ref
alongside stable so the default descriptor cannot install an obsolete version.
Announce only the architectures and formats actually qualified for that release.

## Installation media and the website

Build final candidate media through `scripts/os/nightly-media.sh --candidate
BUILD_ID --no-publish` after qualification. Measure the completed ISO and check
its adjacent SHA-256 file before a resumable upload. The ISO, release notes,
signed media index and their signatures all go to R2. The current release path
does not upload ISO bytes or media listings to the legacy droplet publisher.

Keep Beta and Nightly indexes separate. An ISO URL must be in the same channel
directory as its index; notes use that channel's notes directory. Publish the
ISO and verify its public bytes first, then the notes and their signature,
and the signed index pair last. Index sizes and hashes describe the bytes
actually uploaded. Signing or uploading a draft does not establish readiness.

The website's main download selection is an explicit release decision. Retain
the previous ISO for at least seven days after the actual website switch,
recording that switch time. A subsequent build must not shorten this period,
and the currently selected latest ISO never expires. These media retention
rules do not authorize deletion of OS update or rollback objects.

The obsolete automatic Nightly publisher has been retired on the production
host. Its legacy droplet media and repository publication paths are not the
current release procedure. Final publication remains a reviewed operation;
there is no claim that a new unattended build-and-publish service is active.
