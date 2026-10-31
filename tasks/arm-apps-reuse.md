# ARM shared app and runtime reuse

- Owner: luma-dev-2, Codex /root/arm_apps_reuse; active assist to arm-desktop.
- Worktree: /srv/dev/worktrees/luma-os/arm-apps-reuse; branch agent/1003/arm-apps-reuse.
- Source base83167d6a; integration owner /root.
- Scope: reuse existing current ARM mobile inputs and complete shared desktop native helpers/app packaging. No separate ARM source tree or publication.

## Current results

- Existing released FP6 root deployment97268a77 contains ARM Viola native31 mobile12, Settings60, Filer65, shared platform113. Mobile uses host apps; no Flatpak runtime/app refs in its deployment var/lib/flatpak.
- Exact retained browser RPM: /var/tmp/luma-alpha1-compose-final-20261006-r1/source-r2/build/packages/viola-browser/aarch64/viola-browser-stable-151.0.7922.72-12.viola8210.native31.mobile20261006.1.aarch64.rpm.
- Its source snapshot docs/development/mobile-alpha1-viola12-source-input-20261006.md records normal74 host/sixGio cases and exact RPM/SRPM/archive hashes. Reuse with current desktop host needs explicit architecture-specific engine admission, not package relabeling.
- Stock mobile WebKit2.54.0 differs from current owner-patched2.54.1: cannot qualify desktop runtime with the old tuple.
- Added normal Messages bridges builder using same maintained spec/vendor hashes/tests for x86_64 and aarch64, plus affected-group matrix registration.
- Passed bash syntax, git diff --check and six matrix unit tests. Actual Messages bridge build has not run yet. Initial test discovery used tests/os and found0; corrected tests/unit run passed6.
- Main bulk outputs /srv/dev-storage/projects/luma-os/build/arm-desktop. Root and secondary observed22GiB free; preserve20GiB reserves.

## Next actions

Integration owner can cherry-pick this source commit and push purpose branch to start both native Messages jobs. Continue exact ARM engine/SRPM custody, cast source recipes, Android profile/native pair and Wine nativeARM provider. No ARM image, app baseline or public release claimed.
