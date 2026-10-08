# rmtfs FP6 compatibility patch

`0001-storage-add-fp6-modem-study.patch` is a faithful one-line backport of
upstream commit `27b3a6f00f121a0f8195e25c40faf97b57e2cec1` onto v1.1.1. It adds
the remote-storage path requested by the Milos/SM7635 Fairphone 6 modem and
maps it to the existing `study` partition label.

The patch is applied to Fedora 44's `rmtfs-1.1.1` source. It does not create,
copy, resize, mount, read, or write the partition. Runtime `rmtfs -r -P -s`
retains read-only partition backing with in-memory shadow writes.

`0003-fp6-persist-modemst-only.patch` adds a tightly guarded `-W` recovery
mode. It can write only block devices labelled `modemst1` and `modemst2` when
each is exactly 10 MiB; `-r -P` are mandatory and a custom storage root is
forbidden. The systemd service deliberately does not use `-W`. A recovery tool
must stop the modem, start the writable instance for the bounded operation,
stop it, restore normal `rmtfs -r -P -s`, and only then restart the modem or
shut down. Leaving `-W` active during shutdown is unsafe because late modem
writes can overwrite the recovered state.

Upstream: <https://github.com/linux-msm/rmtfs/commit/27b3a6f00f121a0f8195e25c40faf97b57e2cec1>
