// swift-tools-version:5.9
// SPDX-License-Identifier: Apache-2.0

import PackageDescription

// The Luma Emulator is deliberately dependency-free. Everything it needs on a
// Mac ships with the operating system: Virtualization.framework owns the guest,
// AppKit owns the window, and Foundation owns the control plane. A public
// build must not require Homebrew, Docker, or a separate QEMU package, so a
// third-party Swift package would be a liability rather than a convenience.
let package = Package(
    name: "luma-emulator",
    platforms: [.macOS(.v14)],
    targets: [
        .target(name: "LumaEmulatorKit"),
        .executableTarget(
            name: "LumaEmulatorHost",
            dependencies: ["LumaEmulatorKit"]
        ),
        .executableTarget(
            name: "luma-emulator",
            dependencies: ["LumaEmulatorKit"]
        ),
        .testTarget(
            name: "LumaEmulatorKitTests",
            dependencies: ["LumaEmulatorKit"]
        ),
    ]
)
