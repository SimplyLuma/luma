# Task: project-titan-usb-20261029

- Status: active
- Owner: luma-dev-2 / Codex Project Titan chat
- Project: luma-os
- Worktree: /srv/dev/worktrees/luma-os/project-titan-usb-20261029
- Branch: codex/project-titan-usb-20261029

## Scope

Restore agent access to the owner's Titan via opt-in USB ECM and a dedicated,
key-only SSH endpoint. The phone has no working Wi-Fi and its release image
exposes no USB transport. Source changes belong to this task, preserving existing
Titan owners. This is personal-device recovery, not a general release.

## Evidence and plan

- Worktree based on cached origin/main f020713b6. Fetch attempted but failed due
  to an unrelated invalid refs/codex/turn-diffs object; no refs repaired/deleted.
- Owner explicitly authorizes phone recovery and setup, including card edits.
- Card inspected read-only via Dell; installed deployment matches retained
  Titan20261020.22. Private device identity/logs stay in coordination/evidence.
- Matching kernel has built-in configfs ECM; no new kernel/boot image needed.
- Saved Wi-Fi logs report WF MCU init and CONNSYS power-completion failures;
  root cause remains unproven. Firmware/calibration files exist on card.
- New access must bind only USB, admit only its dedicated public key and fixed
  USB peer, preserve normal SSH policy and have an explicit removal path.
- Source tests: nine tests PASS under admitted server unit
  dev-titan-usb-access-checks-r3-c89c5a74.service, including actual sshd policy
  parsing and systemd unit verification. Prior r1/r2 passed before added cases.
- Actual device boot/SSH acceptance pending. Initial access files installed on
  owner's card and verified against their manifest; original SSH config, Wi-Fi
  helper and boot entry hashes unchanged. Filesystem journal recovery completed
  after retaining a metadata-only backup outside Git.

## Source and security boundary

scripts/mobile/titan2e-usb-access contains the opt-in recovery tool and tests.
It uses the existing kernel configfs ECM interface, fixed local MACs and /30
addresses with no default route. Socket activation binds only the USB device;
root administration requires the dedicated public key from the fixed USB peer.
Normal SSH policy is not changed. This is explicitly owner-requested local
recovery access, not a production default or qualified general release.
Rollback removes only the manifest-listed newly added files and enable links.

Private provisioning state, key custody, exact card identity and captured logs
are held outside Git in the existing coordination/account/device paths.

## Latest handoff

- Final inspection of matching kernel u_ether.c caught a configfs interface-name
  requirement: gether_set_ifname requires exactly one %d. Corrected the template
  to titanusb%d and added a MAC-matched link file to preserve titanusb0.
- Ten tests PASS under dev-titan-usb-access-checks-r4-1276b2ba.service, including
  regression rejection of a fixed configfs name and actual SSH/systemd parsing.
- The earlier installer had safely powered the reader off. The corrected source
  is staged on Dell but is NOT applied to the card yet. A targeted reconnect of
  only the reader's independently switched USB port re-enumerated the reader
  with no media capacity; port state was restored. Physical reader reconnect is
  required. Do not describe current card as ready for USB acceptance yet.
- Next: user reconnects reader (card remains in reader); run the staged scoped
  finalizer, which verifies the old manifest, applies only the two naming changes,
  updates/verifies the manifest and safely unmounts. Then cold-boot the Titan and
  prove USB enumeration/key authentication. Wi-Fi cause and repair remain open.
