// SPDX-License-Identifier: Apache-2.0

import Foundation

/// A verified set of Luma packages for one architecture.
///
/// The emulator's guest is Fedora, but a Fedora guest is not Luma. This is how
/// the real thing gets in: a bundle exported from the build host by
/// `scripts/emulator/export-luma-bundle`, pinned by digest, carrying a manifest
/// that says exactly which source revision and which package releases it holds.
///
/// Admission is deliberately unforgiving. An image that quietly fell back to
/// stock Fedora while still calling itself Luma would make every screenshot
/// taken from it a lie, so every failure here refuses rather than degrades.
public struct LumaBundleManifest: Codable, Sendable {
    public struct Package: Codable, Sendable {
        public var name: String
        public var scope: String
        public var requirement: String
        public var status: String
        public var nevra: String?
        public var version: String?
        public var release: String?
        public var arch: String?
        public var file: String?
        public var sha256: String?
        public var bytes: Int?
        public var pinnedRelease: String?
        public var omissionReason: String?
    }

    /// A package deliberately left out, with the reason recorded.
    ///
    /// This exists so a gap is visible rather than silent. A bundle carrying an
    /// omission is `incomplete` by definition, and the emulator says so.
    public struct Omission: Codable, Sendable {
        public var package: String
        public var requirement: String
        public var reason: String
    }

    public struct Substitution: Codable, Sendable {
        public var package: String
        public var pinnedRelease: String
        public var usedRelease: String
        public var reason: String
    }

    public var bundleFormat: Int
    public var bundleName: String
    public var architecture: String
    public var fedoraRelease: String
    public var sourceCommit: String
    public var sourceBranch: String
    public var sourceDirty: Bool
    public var compositionInputs: [String]
    public var createdAt: String
    public var completeness: String
    public var packageCount: Int
    public var substitutionCount: Int
    public var substitutions: [Substitution]
    public var absentOptional: [String]
    public var omitted: [Omission]?
    public var packages: [Package]

    /// The NEVRAs actually carried, for the image manifest and `status --json`.
    public var installedNEVRAs: [String] {
        packages.compactMap { $0.sha256 == nil ? nil : $0.nevra }.sorted()
    }

    public var requiredPackages: [Package] {
        packages.filter { $0.requirement == "required" }
    }
}

public struct LumaBundle: Sendable {
    public static let supportedFormat = 1

    public let url: URL
    public let manifest: LumaBundleManifest
    public let sha256: String
    public let entryPrefix: String

    /// Reads and admits a bundle without extracting it.
    ///
    /// `expectedSHA256` comes from the `.sha256` sidecar the exporter writes, or
    /// from an explicit pin. A bundle whose digest does not match is refused
    /// outright: at that point nothing about its contents can be trusted.
    public static func inspect(
        at url: URL,
        expectedSHA256: String? = nil,
        architecture: String = "aarch64",
        fedoraRelease: String = "44"
    ) throws -> LumaBundle {
        let manager = FileManager.default
        guard manager.fileExists(atPath: url.path) else {
            throw EmulatorError.usage("no bundle at \(url.path)")
        }

        let digest = try computeSHA256(url)
        var expected = expectedSHA256
        if expected == nil {
            let sidecar = URL(fileURLWithPath: url.path + ".sha256")
            if let text = try? String(contentsOf: sidecar, encoding: .utf8) {
                expected = text.split(separator: " ").first.map(String.init)
            }
        }
        if let expected, expected.lowercased() != digest {
            throw EmulatorError.failure(
                "bundle-digest-mismatch",
                """
                the bundle at \(url.lastPathComponent) does not match its pinned SHA-256.
                expected \(expected)
                observed \(digest)
                """
            )
        }

        let entries = try listEntries(url)
        guard let manifestEntry = entries.first(where: { $0.hasSuffix("/manifest.json") }) else {
            throw EmulatorError.failure(
                "bundle-no-manifest", "the bundle carries no manifest.json"
            )
        }
        let prefix = String(manifestEntry.dropLast("manifest.json".count))
        let manifestData = try extract(entry: manifestEntry, from: url)
        let manifest: LumaBundleManifest
        do {
            manifest = try JSONDecoder().decode(LumaBundleManifest.self, from: manifestData)
        } catch {
            throw EmulatorError.failure(
                "bundle-bad-manifest", "the bundle's manifest.json could not be read: \(error)"
            )
        }

        try admit(
            manifest: manifest, entries: entries, prefix: prefix,
            architecture: architecture, fedoraRelease: fedoraRelease
        )

        return LumaBundle(url: url, manifest: manifest, sha256: digest, entryPrefix: prefix)
    }

    /// Structural checks. Everything here is something that has actually gone
    /// wrong in package pipelines before: a bundle for the wrong architecture,
    /// a manifest that lists a file the archive does not contain, the same
    /// package twice at two releases, a required role silently dropped.
    static func admit(
        manifest: LumaBundleManifest,
        entries: [String],
        prefix: String,
        architecture: String,
        fedoraRelease: String
    ) throws {
        guard manifest.bundleFormat == supportedFormat else {
            throw EmulatorError.failure(
                "bundle-format",
                "bundle format \(manifest.bundleFormat) is not supported (this emulator reads \(supportedFormat))"
            )
        }
        guard manifest.architecture == architecture else {
            throw EmulatorError.failure(
                "bundle-architecture",
                "bundle is for \(manifest.architecture); this guest is \(architecture)"
            )
        }
        guard manifest.fedoraRelease == fedoraRelease else {
            throw EmulatorError.failure(
                "bundle-fedora-release",
                "bundle targets Fedora \(manifest.fedoraRelease); this image is Fedora \(fedoraRelease)"
            )
        }
        guard !manifest.sourceCommit.isEmpty else {
            throw EmulatorError.failure(
                "bundle-no-revision", "the bundle manifest records no source revision"
            )
        }

        let carried = manifest.packages.filter { $0.sha256 != nil }
        guard carried.count == manifest.packageCount else {
            throw EmulatorError.failure(
                "bundle-count-mismatch",
                "manifest claims \(manifest.packageCount) packages but lists \(carried.count)"
            )
        }

        var seenNames = Set<String>()
        let present = Set(entries)
        for package in carried {
            guard let file = package.file, let nevra = package.nevra else {
                throw EmulatorError.failure(
                    "bundle-incomplete-entry", "\(package.name) has no file or NEVRA"
                )
            }
            guard package.sha256?.count == 64 else {
                throw EmulatorError.failure(
                    "bundle-bad-digest", "\(nevra) has no usable SHA-256"
                )
            }
            guard let arch = package.arch, arch == architecture || arch == "noarch" else {
                throw EmulatorError.failure(
                    "bundle-package-architecture",
                    "\(nevra) is \(package.arch ?? "unknown"), not \(architecture) or noarch"
                )
            }
            guard present.contains(prefix + "rpms/" + file) else {
                throw EmulatorError.failure(
                    "bundle-missing-file",
                    "the manifest lists \(file) but the archive does not contain it"
                )
            }
            guard seenNames.insert(package.name).inserted else {
                throw EmulatorError.failure(
                    "bundle-duplicate-package",
                    "\(package.name) appears more than once; a bundle must carry one release of each"
                )
            }
        }

        // A required package may be absent only when the manifest records it as
        // a deliberate omission with a reason. Anything else is a bundle that
        // lost a package somewhere between export and here.
        let declaredOmissions = Set((manifest.omitted ?? []).map(\.package))
        let unexplained = manifest.requiredPackages
            .filter { $0.sha256 == nil && !declaredOmissions.contains($0.name) }
            .map(\.name)
        if !unexplained.isEmpty {
            throw EmulatorError.failure(
                "bundle-missing-required",
                "the bundle is missing required packages with no recorded reason: "
                    + unexplained.joined(separator: ", ")
            )
        }
        if !declaredOmissions.isEmpty, manifest.completeness != "incomplete" {
            throw EmulatorError.failure(
                "bundle-completeness-mismatch",
                """
                the manifest omits \(declaredOmissions.sorted().joined(separator: ", ")) but \
                calls itself '\(manifest.completeness)'. A bundle missing a required \
                package is incomplete.
                """
            )
        }

        let declared = Set(manifest.substitutions.map(\.package))
        let actual = Set(carried.filter { $0.status == "substituted" }.map(\.name))
        guard declared == actual else {
            throw EmulatorError.failure(
                "bundle-substitution-mismatch",
                """
                the manifest's substitution list does not match its packages.
                declared: \(declared.sorted().joined(separator: ", "))
                actual: \(actual.sorted().joined(separator: ", "))
                """
            )
        }
    }

    // MARK: - Archive access

    /// Bundles are gzip, which macOS's own bsdtar reads with no external tool.
    ///
    /// zstd would compress better but macOS ships neither zstd support in
    /// libarchive nor a zstd binary, so a .tar.zst bundle is unreadable on a Mac
    /// without Homebrew — which a public build must not require.
    static func listEntries(_ url: URL) throws -> [String] {
        let result = try Shell.run("/usr/bin/tar", ["-tf", url.path], timeout: 300)
        guard result.succeeded else {
            throw EmulatorError.failure(
                "bundle-unreadable",
                """
                could not read the bundle archive. macOS can read .tar.gz with no extra \
                tools; a .tar.zst bundle needs a zstd binary this Mac may not have.
                \(result.standardError.prefix(200))
                """
            )
        }
        return result.standardOutput.split(separator: "\n").map(String.init)
    }

    static func extract(entry: String, from url: URL) throws -> Data {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/tar")
        process.arguments = ["-xOf", url.path, entry]
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        try process.run()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            throw EmulatorError.failure("bundle-extract", "could not read \(entry) from the bundle")
        }
        return data
    }

    public static func computeSHA256(_ url: URL) throws -> String {
        guard let shasum = Shell.which("shasum") else {
            throw EmulatorError.failure("no-shasum", "shasum is missing from this Mac")
        }
        let result = try Shell.run(shasum, ["-a", "256", url.path], timeout: 900)
        guard result.succeeded,
              let digest = result.trimmedOutput.split(separator: " ").first else {
            throw EmulatorError.failure("bundle-digest", "could not hash \(url.lastPathComponent)")
        }
        return String(digest).lowercased()
    }

    // MARK: - Reporting

    public func describe() -> JSONValue {
        .object([
            "bundle": .string(url.path),
            "bundleName": .string(manifest.bundleName),
            "sha256": .string(sha256),
            "bytes": .int(Int(Shell.fileSize(url.path))),
            "architecture": .string(manifest.architecture),
            "fedoraRelease": .string(manifest.fedoraRelease),
            "sourceCommit": .string(manifest.sourceCommit),
            "sourceBranch": .string(manifest.sourceBranch),
            "sourceDirty": .bool(manifest.sourceDirty),
            "createdAt": .string(manifest.createdAt),
            "completeness": .string(manifest.completeness),
            "packageCount": .int(manifest.packageCount),
            "compositionInputs": .strings(manifest.compositionInputs),
            "packages": .strings(manifest.installedNEVRAs),
            "substitutions": .array(manifest.substitutions.map { substitution in
                .object([
                    "package": .string(substitution.package),
                    "pinnedRelease": .string(substitution.pinnedRelease),
                    "usedRelease": .string(substitution.usedRelease),
                    "reason": .string(substitution.reason),
                ])
            }),
            "absentOptional": .strings(manifest.absentOptional),
            "omitted": .array((manifest.omitted ?? []).map { omission in
                .object([
                    "package": .string(omission.package),
                    "requirement": .string(omission.requirement),
                    "reason": .string(omission.reason),
                ])
            }),
        ])
    }
}
