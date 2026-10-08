# FP6 QSEECom transport bridge

This directory contains a narrow, lab-only compatibility transport for the
Fairphone 6 fingerprint investigation. It maps the three legacy QSEECom
userspace calls imported by the archived FocalTech Android HAL onto Linux's
standard TEE userspace ABI:

- `QSEECom_start_app`
- `QSEECom_shutdown_app`
- `QSEECom_send_cmd`

It intentionally cannot load a trusted application. A separately confined
`qsee-app-loader` must first make the signed application available, after which
`QSEECom_start_app` attaches through `/dev/teeN`. This separation prevents an
untrusted consumer from gaining the privileged TA-loader surface.

The bridge understands no FocalTech commands, captures no images, handles no
templates, and performs no enrollment or authentication. Its request and
response buffers are opaque. The 4 MiB per-buffer ceiling, strict application
name validation, QSEECOM implementation check, serialized command path, and
three-symbol export map are fail-closed boundaries.

`make check` runs the transport against a synthetic TEE implementation; it
never opens a real device. `make all` builds the native Linux test artifact.
`make android ANDROID_CC=/path/to/aarch64-linux-android35-clang` builds a
Bionic-compatible artifact for a future contained protocol-discovery harness.
The Android artifact is not a production fingerprint backend.

The release architecture remains a Linux-native `libfprint` driver using this
kernel/TEE transport directly, with `fprintd`, PAM, and polkit providing the
shared desktop/mobile integration. No proprietary HAL or trustlet belongs in
the Luma source tree or distributable package.
