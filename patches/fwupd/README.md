# fwupd

Fedora's fwupd, rebuilt by `scripts/packages/build-fwupd.sh` from the SRPM
pinned in `config/desktop/inputs.env`.

- `0001-modem-manager-mhi-firehose-prepare-before-detach.patch`: fwupd 2.1.7
  made every QCDM modem parse its firmware only after detaching. PCIe (MHI)
  firehose modems, such as the Quectel RM520N-GL and EM120R-GL in ThinkPads,
  need the firehose programmer from that firmware *before* they detach, so
  every update failed at once with "failed to detach using modem_manager:
  unspecified error" (nothing was written). The patch restores the 2.1.6 order
  for that device type and gives the missing-programmer case a real error.
  Upstream: https://github.com/fwupd/fwupd/issues/10939. Drop it once a
  fwupd release carries the fix.
- `0000-luma-fedora-spec.patch`: adds the patch and the `luma.N` release.
