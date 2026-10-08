# FP6 QCOMTEE identity lab

This directory contains a bounded userspace client for the upstream Linux
QCOMTEE object API. It exists to validate the Fairphone 6 secure fingerprint
backend without issuing a fingerprint command.

The client uses Qualcomm's documented object model and the exact FP6 stock
QSEEComCompat service contract:

- register a real UID/time credential callback;
- open QSEEComCompat app-loader service UID `122`;
- look up only Qualcomm's signed, non-biometric `smplap64` control TA;
- optionally reconstruct that TA's split ELF using each program header's
  `p_offset` and request `loadFromBuffer`;
- request the compatibility controller's unload operation, then release it,
  without invoking the TA itself.

It deliberately refuses `focal64`, enrollment, authentication, sensor capture,
template creation, or any TA command. The object transport implementation is
built from pinned upstream `qualcomm/quic-teec` source rather than copied into
the Luma tree.
