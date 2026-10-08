// SPDX-License-Identifier: Apache-2.0

import Foundation

/// Every file the emulator owns lives under one macOS-native state directory.
///
/// Nothing outside this tree is ever created, moved, or deleted. `reset` and
/// `uninstall` resolve their targets through `EmulatorPaths` precisely so a
/// destructive operation can be proven to stay inside emulator-owned storage.
public struct EmulatorPaths: Sendable {
    public static let stateDirectoryName = "Luma Emulator"

    public let state: URL

    public init(state: URL) {
        self.state = state
    }

    public init() {
        let support = FileManager.default.urls(
            for: .applicationSupportDirectory, in: .userDomainMask
        )[0]
        self.state = support.appendingPathComponent(Self.stateDirectoryName, isDirectory: true)
    }

    /// Honours `LUMA_EMULATOR_STATE` so tests never touch the developer's own
    /// machines, images, or keys.
    public static func resolved() -> EmulatorPaths {
        if let override = ProcessInfo.processInfo.environment["LUMA_EMULATOR_STATE"],
           !override.isEmpty {
            return EmulatorPaths(state: URL(fileURLWithPath: override, isDirectory: true))
        }
        return EmulatorPaths()
    }

    public var cache: URL { sub("cache") }
    public var images: URL { sub("images") }
    public var machines: URL { sub("machines") }
    public var run: URL { sub("run") }
    public var logs: URL { sub("logs") }
    public var keys: URL { sub("keys") }

    public var controlSocket: URL { run.appendingPathComponent("control.sock") }
    public var hostPIDFile: URL { run.appendingPathComponent("host.pid") }
    public var projectLink: URL { sub("project.json") }
    public var guestKey: URL { keys.appendingPathComponent("guest_ed25519") }
    public var guestPublicKey: URL { keys.appendingPathComponent("guest_ed25519.pub") }

    public func machine(_ name: String) -> URL {
        machines.appendingPathComponent(name, isDirectory: true)
    }

    public func image(_ id: String) -> URL {
        images.appendingPathComponent(id, isDirectory: true)
    }

    private func sub(_ name: String) -> URL {
        state.appendingPathComponent(name, isDirectory: true)
    }

    public func createDirectories() throws {
        let manager = FileManager.default
        for url in [state, cache, images, machines, run, logs] {
            try manager.createDirectory(at: url, withIntermediateDirectories: true)
        }
        try manager.createDirectory(
            at: keys,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
    }

    /// A destructive path must be inside the state tree and must not be the
    /// state tree itself. Anything else is refused rather than "cleaned".
    public func assertOwned(_ candidate: URL) throws {
        let root = state.standardizedFileURL.resolvingSymlinksInPath().path
        let target = candidate.standardizedFileURL.resolvingSymlinksInPath().path
        guard target != root else {
            throw EmulatorError.unsafePath("refusing to remove the emulator state root itself: \(target)")
        }
        guard target.hasPrefix(root + "/") else {
            throw EmulatorError.unsafePath("refusing to remove a path outside emulator state: \(target)")
        }
        guard target.count > root.count + 1 else {
            throw EmulatorError.unsafePath("refusing to remove an unnamed path: \(target)")
        }
    }
}
