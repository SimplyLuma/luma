// SPDX-License-Identifier: Apache-2.0

import Foundation

/// Deterministic development scenarios.
///
/// Every supported scenario drives a real, supported interface that Luma
/// already speaks — NetworkManager, the freedesktop notification service, GTK
/// and GNOME settings schemas. A scenario that has no such interface yet is
/// listed with the gap stated plainly and refuses to run, because a convincing
/// fake would be worse than an honest absence.
///
/// The catalogue is data, not code: deleting `scenarios.json` removes every
/// scenario and changes nothing about how Luma behaves.
public struct Scenario: Codable, Sendable {
    public var id: String
    public var summary: String
    public var interface: String
    public var supported: Bool
    public var gap: String?
    public var script: String
}

/// Only exists so `Bundle(for:)` can locate the module that owns the catalogue.
final class CatalogueAnchor {}

public struct ScenarioCatalogue: Codable, Sendable {
    public var scenarios: [Scenario]

    public func scenario(named name: String) -> Scenario? {
        scenarios.first { $0.id == name }
    }

    public func describe() -> JSONValue {
        .object([
            "scenarios": .array(scenarios.map { scenario in
                .object([
                    "id": .string(scenario.id),
                    "summary": .string(scenario.summary),
                    "interface": .string(scenario.interface),
                    "supported": .bool(scenario.supported),
                    "gap": scenario.gap.map { .string($0) } ?? .null,
                ])
            }),
            "supportedCount": .int(scenarios.filter(\.supported).count),
            "catalogueCount": .int(scenarios.count),
        ])
    }

    /// Looked up beside the executable first so an installed copy is
    /// self-contained, then in the repository, then at an explicit override.
    public static func catalogueURL() -> URL? {
        let manager = FileManager.default
        if let override = ProcessInfo.processInfo.environment["LUMA_EMULATOR_SCENARIOS"],
           manager.fileExists(atPath: override) {
            return URL(fileURLWithPath: override)
        }
        let executable = URL(fileURLWithPath: CommandLine.arguments[0])
            .resolvingSymlinksInPath()
            .deletingLastPathComponent()
        var candidates = [
            executable.appendingPathComponent("scenarios.json"),
            executable.appendingPathComponent("../Resources/scenarios.json"),
            executable.appendingPathComponent("../share/luma-emulator/scenarios.json"),
        ]
        // Walk up towards a repository root so `swift run`, `swift test`, an
        // installed bundle, and a plain `./build/emulator/luma-emulator` all
        // find the same catalogue without anyone having to set an environment
        // variable first. Under a test runner the executable is the system
        // `xctest`, so the loaded bundle and the working directory are probed
        // as well.
        let anchors = [
            executable,
            Bundle(for: CatalogueAnchor.self).bundleURL,
            URL(fileURLWithPath: FileManager.default.currentDirectoryPath),
        ]
        for anchor in anchors {
            var probe = anchor
            for _ in 0..<10 {
                candidates.append(probe.appendingPathComponent("config/emulator/scenarios.json"))
                probe = probe.deletingLastPathComponent()
            }
        }
        for candidate in candidates {
            let resolved = candidate.standardizedFileURL
            if manager.fileExists(atPath: resolved.path) { return resolved }
        }
        return nil
    }

    public static func load() throws -> ScenarioCatalogue {
        guard let url = catalogueURL() else {
            throw EmulatorError.failure(
                "no-scenarios",
                "scenarios.json was not found. Set LUMA_EMULATOR_SCENARIOS to its path."
            )
        }
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(ScenarioCatalogue.self, from: data)
    }
}
