# depot-review

The Depot submission pipeline. It claims developer submissions from the Hub, checks them, runs them in a throwaway Luma VM, and reports back. It publishes approved builds to the `luma` Flatpak remote, withdraws releases, and signs the Depot catalogue.

It runs on the build host as `luma-depot-review.service`. Configuration is `/etc/luma/depot-review.json`; see `depot_review/config.py` for every setting and its default.

## Checks (every new app and every update)

| Check | How |
|---|---|
| Format and integrity | The digest is compared with the Hub's record. The package is opened with its own format's tools in the review container (no network, no capabilities): `flatpak build-import-bundle` and `ostree`, `rpm`, `dpkg-deb` or `unsquashfs`. |
| Identity | The app id matches the listing. Reserved prefixes and ids Luma ships are refused (`config/policy.json`, `packaging/flatpak/apps/titles.json`, Luma beta/nightly refs). |
| Runtime | Must be one of `config/policy.json` `runtimes`. |
| Architecture | x86_64 is required; aarch64 is welcome. |
| Malware | ClamAV scans the upload and every file it would install. Signatures are refreshed with `freshclam` every 3 hours. |
| Permissions | What the package can reach, in Depot's eleven words (`depot_review/words.py`). Flatpak reach comes from `scripts/depot/flatpak-permissions.py`. A universal package always reaches Files and Internet, plus System or Devices for services, administrator rules, udev rules or maintainer scripts. Any word the listing doesn't declare fails, with a plain message. |
| Metadata | Summary of 60 characters or fewer. Icon is an SVG or a square PNG of at least 512 px. Desktop screenshots are at least 1180 × 698; phone screenshots are 1170 × 2532. An app needs at least one desktop screenshot. |
| VM run | Install, launch, screenshot and uninstall in a copy-on-write overlay of a Luma base disk (`depot_review/vm.py`). The launch screenshot goes to the Hub. |

Results are `passed` (to Review), `needs_changes` (back to the developer, with the failed checks' messages), or `attention` (only internal problems; Luma looks at it).

AppImages are opened and scanned, then answered "coming soon". `.rpm`/`.deb` are fully checked but don't publish yet.

## Publishing and withdrawal

On approval, each reviewed bundle becomes an unsigned repository holding exactly `app/<id>/<arch>/stable`. It is sealed as a signer snapshot and published with the existing chain, from a sealed control snapshot of this checkout:

`seal-signing-control.py seal-inputs` → `tools-container.sh --signing` → `publish-app.sh --third-party` → `update-remote.sh` → `sync-remote.sh`

- `--third-party` keeps developers' apps on the stable branch.
- It refuses any id Luma owns.
- Luma's own publishes refuse ids a third party holds.

`withdraw-app.sh` moves a ref forward to a new signed commit with the previous release's content. If there is no previous release, it re-signs the ref as end-of-life. Both take `/srv/luma-build/depot-publication.lock`.

## Catalogue

The Hub's catalogue is signed with the `depot-catalogue-v1` minisign key (`/srv/luma-build/depot-keys/minisign/`). It is published to `https://dl.simplyluma.com/depot/catalogue/v1/catalogue.json` and `.minisig`. Pictures are copied to `https://media.simplyluma.com/depot/<app-id>/<sha256>.<ext>`.

## Commands

```
python3 -m depot_review serve
python3 -m depot_review check FILE --app-id ID [--declare Internet Files] [--no-vm]
python3 -m depot_review publish --id SUBMISSION --app-id ID BUNDLE:ARCH ...
python3 -m depot_review withdraw --id REQUEST --app-id ID --arch ARCH [--target COMMIT]
python3 -m depot_review catalogue [--force]
python3 -m depot_review keygen
python3 -m depot_review.vm build-base --iso LUMA.iso --out DIR
```

`testapp/` is Depot Hello, a one-file GTK app used for the end-to-end test. Build it with `testapp/build.sh VERSION ARCH OUT.flatpak`.

## Tests

```
python3 -m unittest discover -s tools/depot-review/tests
```
