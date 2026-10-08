# Qualcomm QSEECOM TEE patch series

This directory carries Dawid Wróbel's experimental GPL QSEECOM TEE work from
[`wrobelda/linux`](https://github.com/wrobelda/linux/tree/qcom-qseecom-tee),
upstream commit `662435b853d865d360507f5daee6ff309838c5ca`.

The 41 commits were mechanically rebased, in original order and with original
authorship, from signed Linux tag `v7.1` onto signed stable tag `v7.1.2` so they
apply to Luma's pinned `linux-v7.1.2-milos` archive (SHA-256
`6043a062d595e1b4913512c3414387d9a016c4690d8832720651df07276dc78b`).
The complete series passes `git apply --check` against that archive.

The series adds an application-agnostic Linux TEE device for legacy Qualcomm
QSEECOM, trusted-application loading/unloading, listener delivery, strict
staging through TrustZone memory, and the MDT image assembly needed by the
loader. It does not implement FocalTech's application protocol, include the
FP6 trustlet, expose a fingerprint image, or make fingerprint authentication
ready.

This code is not merged upstream. Keep `CONFIG_TEE_QSEECOM` disabled in release
kernels until the FP6-specific transport, supplicant policy, firmware
provenance, and physical security gates are accepted. A candidate must limit
access to `/dev/tee*` and `/dev/teepriv*`; arbitrary access would allow sending
opaque commands to loaded trusted applications.

To verify the carry against a clean source tree:

```sh
for patch in patches/linux-qseecom/*.patch; do
  git -C /path/to/linux-v7.1.2-milos apply --check "$patch"
  git -C /path/to/linux-v7.1.2-milos apply "$patch"
done
```

