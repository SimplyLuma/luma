# Luma Emulator (source)

Swift package for the Luma Emulator. Build it with
[`scripts/emulator/build.sh`](../../scripts/emulator/build.sh).
[`Package.swift`](Package.swift) declares the package dependencies and targets;
[platform scope](../../docs/platforms.md) distinguishes emulator development
from supported hardware releases.

## Targets

| Target | What it is |
| --- | --- |
| `LumaEmulatorKit` | Paths, models, the control protocol and its UNIX-socket transport, the guest channel, host inspection, and the scenario catalogue. Everything testable lives here. |
| `LumaEmulatorHost` | The runtime. Owns the `VZVirtualMachine`, draws the Mac window, and serves the control socket. This is the target that must carry the virtualization entitlement. |
| `luma-emulator` | The command. A thin client of the control socket plus the bootstrap and provisioning flows. |

## Two rules worth knowing before editing

**There is one control layer.** `HostController` is the only thing that decides
what the emulator's state is. The Mac window and the CLI are both its clients.
If you find yourself adding state to the window, add it to the controller
instead.

**The runtime must be signed.** Virtualization.framework refuses to create a
guest for a process without `com.apple.security.virtualization`, and SwiftPM
produces unsigned binaries, so `swift build` alone gives you something that
cannot start a VM. Use the build script.

## No dependencies, on purpose

The package has no third-party Swift dependencies and is not going to acquire
any casually. A public build must not require Homebrew or a package fetch, and a
control surface that coding agents depend on should not change behaviour because
someone else's argument parser changed its flag semantics.

## Tests

```sh
swift test --package-path tools/luma-emulator   # unit tests
tests/emulator/run.sh                           # unit + host + hygiene
tests/emulator/run.sh --with-guest              # also end-to-end against a guest
```
