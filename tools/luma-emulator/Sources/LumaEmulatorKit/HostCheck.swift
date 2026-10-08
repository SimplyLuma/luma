// SPDX-License-Identifier: Apache-2.0

import Foundation

/// What `luma-emulator doctor` reports. Every entry is observed on this machine
/// at the moment it is asked; nothing is assumed from the build configuration.
public struct HostCheck: Codable, Sendable {
    public var id: String
    public var title: String
    public var ok: Bool
    public var detail: String
    public var required: Bool
}

public struct HostReport: Codable, Sendable {
    public var architecture: String
    public var model: String
    public var chip: String
    public var memoryGiB: Int
    public var macOSVersion: String
    public var cpuCount: Int
    public var checks: [HostCheck]
    public var supported: Bool
}

public enum HostInspector {
    public static func sysctl(_ name: String) -> String? {
        var size = 0
        guard sysctlbyname(name, nil, &size, nil, 0) == 0, size > 0 else { return nil }
        var buffer = [CChar](repeating: 0, count: size)
        guard sysctlbyname(name, &buffer, &size, nil, 0) == 0 else { return nil }
        return String(cString: buffer)
    }

    public static func report(paths: EmulatorPaths) -> HostReport {
        let info = ProcessInfo.processInfo
        let architecture = sysctl("hw.machine") ?? "unknown"
        let chip = sysctl("machdep.cpu.brand_string") ?? "unknown"
        let model = sysctl("hw.model") ?? "unknown"
        let memoryGiB = Int(info.physicalMemory / 1_073_741_824)
        let version = info.operatingSystemVersion
        let macOS = "\(version.majorVersion).\(version.minorVersion).\(version.patchVersion)"

        var checks: [HostCheck] = []

        let isARM = architecture.hasPrefix("arm64")
        checks.append(HostCheck(
            id: "host-architecture",
            title: "Apple Silicon (arm64) host",
            ok: isARM,
            detail: isARM
                ? "\(chip) reports \(architecture); the guest runs native AArch64 with no CPU translation."
                : "\(architecture) is not Apple Silicon. Native AArch64 virtualization is unavailable.",
            required: true
        ))

        let macOSOK = version.majorVersion >= 14
        checks.append(HostCheck(
            id: "macos-version",
            title: "macOS 14 or newer",
            ok: macOSOK,
            detail: "macOS \(macOS)",
            required: true
        ))

        let framework = FileManager.default.fileExists(
            atPath: "/System/Library/Frameworks/Virtualization.framework"
        )
        checks.append(HostCheck(
            id: "virtualization-framework",
            title: "Apple Virtualization.framework present",
            ok: framework,
            detail: framework ? "available" : "missing",
            required: true
        ))

        let memoryOK = memoryGiB >= 8
        checks.append(HostCheck(
            id: "host-memory",
            title: "At least 8 GiB of host memory",
            ok: memoryOK,
            detail: "\(memoryGiB) GiB installed",
            required: true
        ))

        // Disk is reported in allocated bytes, never apparent size. Every disk
        // the emulator creates is a 20 GiB sparse file occupying a fraction of
        // that, and APFS clones share blocks on top, so apparent size would be
        // doubly wrong and would refuse to run on a machine with plenty of room.
        let free = freeDiskGiB(at: paths.state.deletingLastPathComponent())
        let used = allocatedGiB(at: paths.state)
        let hasImage = FileManager.default.fileExists(
            atPath: paths.images.appendingPathComponent("fedora44-aarch64/disk.raw").path
        )
        let needed = hasImage ? runningHeadroomGiB : firstBuildHeadroomGiB
        let purpose = hasImage
            ? "to start machines from the existing factory image"
            : "to download, convert and provision a first factory image"
        checks.append(HostCheck(
            id: "host-disk",
            title: "Free space for images and overlays",
            ok: free >= needed,
            detail: free >= needed
                ? "\(free) GiB free, about \(needed) GiB needed \(purpose). The emulator occupies \(used) GiB."
                : "only \(free) GiB free but about \(needed) GiB is needed \(purpose). "
                    + "The emulator occupies \(used) GiB. Reclaim one with "
                    + "`luma-emulator reset --machine <name> --yes`, or delete the verified "
                    + "download cache at \(paths.cache.path) — it is re-downloaded and re-verified on demand.",
            required: false
        ))

        let qemuImg = Shell.which("qemu-img")
        checks.append(HostCheck(
            id: "qcow2-converter",
            title: "qcow2 to raw converter (factory image build only)",
            ok: qemuImg != nil,
            detail: qemuImg.map { "qemu-img at \($0)" }
                ?? "qemu-img not found. Only the factory image builder needs it; running an already-built image does not.",
            required: false
        ))

        let xz = Shell.which("xz") ?? Shell.which("python3")
        checks.append(HostCheck(
            id: "xz-decompressor",
            title: "xz decompression (factory image build only)",
            ok: xz != nil,
            detail: xz.map { "using \($0)" } ?? "neither xz nor python3 was found",
            required: false
        ))

        let ssh = Shell.which("ssh")
        checks.append(HostCheck(
            id: "ssh-client",
            title: "OpenSSH client for guest control",
            ok: ssh != nil,
            detail: ssh ?? "missing",
            required: true
        ))

        let rsync = Shell.which("rsync")
        checks.append(HostCheck(
            id: "rsync",
            title: "rsync for changed-file source synchronization",
            ok: rsync != nil,
            detail: rsync ?? "missing",
            required: true
        ))

        let stateOK = FileManager.default.fileExists(atPath: paths.state.path)
        checks.append(HostCheck(
            id: "state-directory",
            title: "Emulator state directory",
            ok: stateOK,
            detail: stateOK ? paths.state.path : "\(paths.state.path) (created by bootstrap)",
            required: false
        ))

        let supported = checks.filter(\.required).allSatisfy(\.ok)
        return HostReport(
            architecture: architecture,
            model: model,
            chip: chip,
            memoryGiB: memoryGiB,
            macOSVersion: macOS,
            cpuCount: info.processorCount,
            checks: checks,
            supported: supported
        )
    }

    /// A first build needs the download, the converted raw base, the factory
    /// image and the provisioning work disk alive at the same time.
    static let firstBuildHeadroomGiB = 12
    /// Running from an existing factory image only needs overlay divergence.
    static let runningHeadroomGiB = 4

    /// Allocated size, walking the tree. Sparse disk images report an apparent
    /// size many times larger than what they occupy, and APFS clones share
    /// blocks, so apparent size would be doubly wrong.
    public static func allocatedGiB(at url: URL) -> Int {
        guard let walker = FileManager.default.enumerator(
            at: url, includingPropertiesForKeys: [.totalFileAllocatedSizeKey, .isRegularFileKey]
        ) else { return 0 }
        var total: Int64 = 0
        for case let entry as URL in walker {
            let values = try? entry.resourceValues(
                forKeys: [.totalFileAllocatedSizeKey, .isRegularFileKey]
            )
            guard values?.isRegularFile == true else { continue }
            total += Int64(values?.totalFileAllocatedSize ?? 0)
        }
        return Int(total / 1_073_741_824)
    }

    public static func freeDiskGiB(at url: URL) -> Int {
        let values = try? url.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
        let bytes = values?.volumeAvailableCapacityForImportantUsage ?? 0
        return Int(bytes / 1_073_741_824)
    }
}
