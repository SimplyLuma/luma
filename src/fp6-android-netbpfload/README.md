# FP6 isolated Android netd BPF loader

This is a narrowly scoped adapter for the stock-Android C1 telephony container.
It uses the exact QREL 16.95.0 `libbpf_android.so` to load the exact signed
Connectivity APEX `netd.o` into C1's private bpffs. It does not expose the host
bpffs, change host-global BPF sysctls, or spoof the host kernel version.

The adapter exists because Android 16's `netbpfload` rejects the FP6 Linux 7.1
kernel at its LTS-policy preflight before it asks the stock loader to load the
object. It is not a general Android BPF loader and must remain hash-gated to the
validated stock library and object in the physical-device runner.
