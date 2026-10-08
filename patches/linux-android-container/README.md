# Android memfd/ashmem compatibility

This patch set carries Google's upstream `MEMFD_ASHMEM_SHIM` implementation
for the bounded Fairphone 6 stock-Android container kernel.  It lets current
Android userspace issue legacy ashmem ioctls against sealed memfd objects; it
does not restore the retired `/dev/ashmem` driver.

- `0001-mm-memfd-add-ashmem-ioctl-compatibility.patch` is adapted from
  Android common-kernel commit `46cd1ff96ae14bd35f39e6544e82512bbc0ac527`.
- `0002-mm-shmem-route-ioctls-through-ashmem-shim.patch` is adapted from
  Android common-kernel commit `6e4f491`.

The implementation retains the upstream GPL-2.0 license headers and author
attribution.  Adaptation is limited to surrounding Linux 7.1.2 context.
