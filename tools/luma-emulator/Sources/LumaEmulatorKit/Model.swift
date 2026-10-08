// SPDX-License-Identifier: Apache-2.0

import Foundation

/// Desktop and Mobile are presentation modes of one Luma platform, exactly as
/// they are on real hardware. They select a shell session and a viewport; they
/// never select a different application build.
public enum Mode: String, Codable, CaseIterable, Sendable {
    case desktop
    case mobile

    public var sessionName: String {
        switch self {
        case .desktop: return "luma-desktop"
        case .mobile: return "luma-handheld"
        }
    }

    public var defaultViewport: Viewport {
        switch self {
        case .desktop: return Viewport(width: 1440, height: 900, scale: 2)
        case .mobile: return Viewport.presets["390x844"]!
        }
    }
}

/// A logical viewport. `scale` is the guest's HiDPI factor: the guest is given
/// `width * scale` by `height * scale` physical pixels and told to scale by
/// `scale`, so adaptive layout still sees the logical width Luma cares about.
public struct Viewport: Codable, Equatable, Sendable {
    public var width: Int
    public var height: Int
    public var scale: Int

    public init(width: Int, height: Int, scale: Int = 2) {
        self.width = width
        self.height = height
        self.scale = scale
    }

    public var pixelWidth: Int { width * scale }
    public var pixelHeight: Int { height * scale }
    public var name: String { "\(width)x\(height)" }

    /// Round-trips through `parse`, scale included. `name` alone loses the
    /// scale, and a viewport handed to the runtime as "390x844" is silently
    /// re-read as the preset rather than what the caller asked for.
    public var descriptor: String { "\(width)x\(height)@\(scale)" }

    /// Deterministic handheld presets. These are logical sizes; Luma's adaptive
    /// breakpoints are driven by `width`.
    public static let presets: [String: Viewport] = [
        "360x740": Viewport(width: 360, height: 740, scale: 3),
        "390x844": Viewport(width: 390, height: 844, scale: 3),
        "430x932": Viewport(width: 430, height: 932, scale: 3),
    ]

    public static func parse(_ text: String) throws -> Viewport {
        if let preset = presets[text] { return preset }

        // Accepted forms: "390x844" and "1440x900@2". The scale suffix has to
        // come off before the height is read, or the height parse fails and a
        // documented form is quietly rejected.
        var body = text
        var scale = 2
        if let at = text.firstIndex(of: "@") {
            body = String(text[text.startIndex..<at])
            guard let parsed = Int(text[text.index(after: at)...]) else {
                throw EmulatorError.usage("viewport scale must be a number (got \(text))")
            }
            scale = parsed
        }

        let parts = body.split(separator: "x")
        guard parts.count == 2,
              let width = Int(parts[0]),
              let height = Int(parts[1]),
              width >= 240, height >= 240,
              width <= 8192, height <= 8192
        else {
            throw EmulatorError.usage(
                "viewport must look like 390x844 or 1440x900@2 (got \(text))"
            )
        }
        guard (1...3).contains(scale) else {
            throw EmulatorError.usage("viewport scale must be 1, 2, or 3 (got \(scale))")
        }
        return Viewport(width: width, height: height, scale: scale)
    }

    public func rotated() -> Viewport {
        Viewport(width: height, height: width, scale: scale)
    }
}

/// The lifecycle a coding agent is allowed to branch on. `health` never lies:
/// `ready` means the guest answered a real liveness probe, not that the VM
/// process exists.
public enum Health: String, Codable, Sendable {
    case stopped
    case booting
    case ready
    case previewing
    case failed
}

public struct MachineSpec: Codable, Sendable {
    public var name: String
    public var mode: Mode
    public var viewport: Viewport
    public var cpuCount: Int
    public var memoryMiB: Int
    public var imageID: String

    public init(
        name: String,
        mode: Mode,
        viewport: Viewport,
        cpuCount: Int,
        memoryMiB: Int,
        imageID: String
    ) {
        self.name = name
        self.mode = mode
        self.viewport = viewport
        self.cpuCount = cpuCount
        self.memoryMiB = memoryMiB
        self.imageID = imageID
    }
}

/// What a factory image actually is, recorded at build time so no claim about
/// a running guest has to be taken on trust.
/// What the emulator knows about the Luma package set inside an image.
///
/// `completeness` is the honest answer to "is this Luma?", and the emulator
/// refuses to describe a `fedora-fallback` image as Luma anywhere a person
/// might read it.
public struct LumaBundleRecord: Codable, Sendable {
    public var bundleName: String
    public var bundleSHA256: String
    public var sourceCommit: String
    public var sourceBranch: String
    public var completeness: String
    public var packageCount: Int
    public var substitutions: [String]
    public var absentOptional: [String]

    public init(
        bundleName: String, bundleSHA256: String, sourceCommit: String,
        sourceBranch: String, completeness: String, packageCount: Int,
        substitutions: [String], absentOptional: [String]
    ) {
        self.bundleName = bundleName
        self.bundleSHA256 = bundleSHA256
        self.sourceCommit = sourceCommit
        self.sourceBranch = sourceBranch
        self.completeness = completeness
        self.packageCount = packageCount
        self.substitutions = substitutions
        self.absentOptional = absentOptional
    }
}

public struct ImageManifest: Codable, Sendable {
    public var imageID: String
    public var architecture: String
    public var baseName: String
    public var baseURL: String
    public var baseSHA256: String
    public var sourceCommit: String
    public var sourceBranch: String
    public var sourceDirty: Bool
    public var lumaPackages: [String]
    public var fedoraPackages: [String]
    public var diskSHA256: String?
    public var diskBytes: Int64
    public var createdAt: String
    public var builderVersion: String
    public var emulatorVersion: String
    public var buildTools: [String: String]
    public var lumaBundle: LumaBundleRecord?

    /// The one question a caller most often needs answered, in one word.
    public var lumaCompleteness: String {
        lumaBundle?.completeness ?? "fedora-fallback"
    }

    public init(
        imageID: String,
        architecture: String,
        baseName: String,
        baseURL: String,
        baseSHA256: String,
        sourceCommit: String,
        sourceBranch: String,
        sourceDirty: Bool,
        lumaPackages: [String],
        fedoraPackages: [String],
        diskSHA256: String?,
        diskBytes: Int64,
        createdAt: String,
        builderVersion: String,
        emulatorVersion: String,
        buildTools: [String: String],
        lumaBundle: LumaBundleRecord? = nil
    ) {
        self.imageID = imageID
        self.architecture = architecture
        self.baseName = baseName
        self.baseURL = baseURL
        self.baseSHA256 = baseSHA256
        self.sourceCommit = sourceCommit
        self.sourceBranch = sourceBranch
        self.sourceDirty = sourceDirty
        self.lumaPackages = lumaPackages
        self.fedoraPackages = fedoraPackages
        self.diskSHA256 = diskSHA256
        self.diskBytes = diskBytes
        self.createdAt = createdAt
        self.builderVersion = builderVersion
        self.emulatorVersion = emulatorVersion
        self.buildTools = buildTools
        self.lumaBundle = lumaBundle
    }
}

public struct ProjectLink: Codable, Sendable {
    public var path: String
    public var connectedAt: String

    public init(path: String, connectedAt: String) {
        self.path = path
        self.connectedAt = connectedAt
    }
}

public enum EmulatorVersion {
    public static let current = "0.1.0"
}
