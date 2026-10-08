// SPDX-License-Identifier: Apache-2.0

import Foundation

/// Finds the version-controlled files that ship beside the emulator.
///
/// An installed copy carries them inside the application bundle; a development
/// build finds them in the repository. Either way they are files a reviewer can
/// read, not strings baked into a binary.
public enum EmulatorResources {
    private final class Anchor {}

    public static func locate(
        _ relative: String, environmentOverride: String, marker: String? = nil
    ) -> URL? {
        let manager = FileManager.default
        if let override = ProcessInfo.processInfo.environment[environmentOverride],
           manager.fileExists(atPath: override) {
            return URL(fileURLWithPath: override)
        }
        let name = (relative as NSString).lastPathComponent
        let executable = URL(fileURLWithPath: CommandLine.arguments[0])
            .resolvingSymlinksInPath()
            .deletingLastPathComponent()
        var candidates = [
            executable.appendingPathComponent(name),
            executable.appendingPathComponent("../Resources/\(name)"),
            executable.appendingPathComponent("../share/luma-emulator/\(name)"),
        ]
        for anchor in [
            executable,
            Bundle(for: Anchor.self).bundleURL,
            URL(fileURLWithPath: manager.currentDirectoryPath),
        ] {
            var probe = anchor
            for _ in 0..<10 {
                candidates.append(probe.appendingPathComponent(relative))
                probe = probe.deletingLastPathComponent()
            }
        }
        for candidate in candidates {
            let resolved = candidate.standardizedFileURL
            let proof = marker.map { resolved.appendingPathComponent($0).path } ?? resolved.path
            if manager.fileExists(atPath: proof) { return resolved }
        }
        return nil
    }

    /// The guest provisioning recipe and the guest-side helper programs.
    public static func provisionDirectory() throws -> URL {
        guard let url = locate(
            "config/emulator/provision",
            environmentOverride: "LUMA_EMULATOR_PROVISION",
            marker: "provision.sh"
        ) else {
            throw EmulatorError.failure(
                "no-provision-recipe",
                "config/emulator/provision was not found. Set LUMA_EMULATOR_PROVISION to its path."
            )
        }
        return url
    }
}
