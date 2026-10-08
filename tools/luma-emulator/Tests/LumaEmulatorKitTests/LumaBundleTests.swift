// SPDX-License-Identifier: Apache-2.0

import XCTest
@testable import LumaEmulatorKit

/// Admission is the only thing standing between "a Fedora guest" and "a guest
/// the emulator will describe as Luma", so these tests are about refusal much
/// more than about acceptance.
final class LumaBundleAdmissionTests: XCTestCase {
    private func manifest(
        completeness: String = "complete",
        packages: [LumaBundleManifest.Package],
        substitutions: [LumaBundleManifest.Substitution] = [],
        omitted: [LumaBundleManifest.Omission]? = nil,
        architecture: String = "aarch64",
        fedoraRelease: String = "44",
        format: Int = 1,
        sourceCommit: String = "59791baf3cc91c15f4d378dec3b38ac1518d52dc"
    ) -> LumaBundleManifest {
        LumaBundleManifest(
            bundleFormat: format,
            bundleName: "test-bundle",
            architecture: architecture,
            fedoraRelease: fedoraRelease,
            sourceCommit: sourceCommit,
            sourceBranch: "work/test",
            sourceDirty: false,
            compositionInputs: ["config/desktop/inputs.env"],
            createdAt: "20260903T000000Z",
            completeness: completeness,
            packageCount: packages.filter { $0.sha256 != nil }.count,
            substitutionCount: substitutions.count,
            substitutions: substitutions,
            absentOptional: [],
            omitted: omitted,
            packages: packages
        )
    }

    private func package(
        _ name: String,
        requirement: String = "required",
        status: String = "exact",
        arch: String = "aarch64",
        carried: Bool = true
    ) -> LumaBundleManifest.Package {
        LumaBundleManifest.Package(
            name: name, scope: "shared", requirement: requirement, status: status,
            nevra: "\(name)-1.0-1.luma.1.fc44.\(arch)",
            version: "1.0", release: "1.luma.1.fc44", arch: arch,
            file: carried ? "\(name)-1.0-1.luma.1.fc44.\(arch).rpm" : nil,
            sha256: carried ? String(repeating: "a", count: 64) : nil,
            bytes: 1024, pinnedRelease: "1.luma.1.fc44", omissionReason: nil
        )
    }

    private func entries(for packages: [LumaBundleManifest.Package]) -> [String] {
        ["b/", "b/manifest.json", "b/SHA256SUMS", "b/rpms/"]
            + packages.compactMap { $0.file.map { "b/rpms/\($0)" } }
    }

    private func admit(
        _ manifest: LumaBundleManifest,
        entries: [String]? = nil,
        architecture: String = "aarch64",
        fedoraRelease: String = "44"
    ) throws {
        try LumaBundle.admit(
            manifest: manifest,
            entries: entries ?? self.entries(for: manifest.packages),
            prefix: "b/",
            architecture: architecture,
            fedoraRelease: fedoraRelease
        )
    }

    func testACoherentBundleIsAdmitted() throws {
        let packages = [package("gtk4"), package("libadwaita")]
        XCTAssertNoThrow(try admit(manifest(packages: packages)))
    }

    func testABundleForAnotherArchitectureIsRefused() {
        let manifest = manifest(packages: [package("gtk4")], architecture: "x86_64")
        XCTAssertThrowsError(try admit(manifest))
    }

    func testAPackageForAnotherArchitectureIsRefused() {
        let manifest = manifest(packages: [package("gtk4", arch: "x86_64")])
        XCTAssertThrowsError(try admit(manifest))
    }

    func testNoarchPackagesAreAccepted() {
        let manifest = manifest(packages: [package("prairie-core-apps", arch: "noarch")])
        XCTAssertNoThrow(try admit(manifest))
    }

    func testAnotherFedoraReleaseIsRefused() {
        let manifest = manifest(packages: [package("gtk4")], fedoraRelease: "45")
        XCTAssertThrowsError(try admit(manifest))
    }

    func testAnUnknownBundleFormatIsRefused() {
        XCTAssertThrowsError(try admit(manifest(packages: [package("gtk4")], format: 99)))
    }

    func testAManifestWithoutASourceRevisionIsRefused() {
        XCTAssertThrowsError(
            try admit(manifest(packages: [package("gtk4")], sourceCommit: ""))
        )
    }

    func testAFileListedButAbsentFromTheArchiveIsRefused() {
        let packages = [package("gtk4")]
        XCTAssertThrowsError(
            try admit(manifest(packages: packages), entries: ["b/", "b/manifest.json"])
        )
    }

    func testADuplicatedPackageIsRefused() {
        let packages = [package("gtk4"), package("gtk4")]
        XCTAssertThrowsError(try admit(manifest(packages: packages)))
    }

    func testAMissingRequiredPackageWithNoReasonIsRefused() {
        let packages = [package("gtk4", carried: false)]
        XCTAssertThrowsError(try admit(manifest(packages: packages)))
    }

    /// A gap is allowed only when it is declared and the bundle admits to being
    /// incomplete. This is the whole point: absence must be visible.
    func testARecordedOmissionIsAdmittedAsIncomplete() throws {
        let packages = [package("gtk4"), package("gnome-shell", status: "omitted", carried: false)]
        let manifest = manifest(
            completeness: "incomplete",
            packages: packages,
            omitted: [.init(package: "gnome-shell", requirement: "required", reason: "uninstallable")]
        )
        XCTAssertNoThrow(try admit(manifest))
    }

    func testAnOmissionThatStillClaimsCompletenessIsRefused() {
        let packages = [package("gtk4"), package("gnome-shell", status: "omitted", carried: false)]
        let manifest = manifest(
            completeness: "complete",
            packages: packages,
            omitted: [.init(package: "gnome-shell", requirement: "required", reason: "uninstallable")]
        )
        XCTAssertThrowsError(try admit(manifest))
    }

    func testAnUndeclaredSubstitutionIsRefused() {
        let packages = [package("gtk4", status: "substituted")]
        XCTAssertThrowsError(try admit(manifest(packages: packages)))
    }

    func testADeclaredSubstitutionIsAdmitted() throws {
        let packages = [package("nautilus", status: "substituted")]
        let manifest = manifest(
            completeness: "complete-with-substitutions",
            packages: packages,
            substitutions: [.init(
                package: "nautilus", pinnedRelease: "1.luma.33.fc44",
                usedRelease: "1.luma.22.fc44", reason: "no aarch64 build at the pinned release"
            )]
        )
        XCTAssertNoThrow(try admit(manifest))
    }

    func testAPackageCountThatDisagreesWithTheListIsRefused() {
        var manifest = manifest(packages: [package("gtk4")])
        manifest.packageCount = 5
        XCTAssertThrowsError(try admit(manifest))
    }

    func testInstalledNEVRAsExcludeAbsentPackages() {
        let manifest = manifest(packages: [
            package("gtk4"), package("gnome-shell", status: "omitted", carried: false),
        ])
        XCTAssertEqual(manifest.installedNEVRAs.count, 1)
        XCTAssertTrue(manifest.installedNEVRAs[0].hasPrefix("gtk4-"))
    }
}

final class ImageManifestCompletenessTests: XCTestCase {
    /// An image with no bundle is a Fedora substrate, and must never report
    /// itself as anything else.
    func testAnImageWithNoBundleIsFedoraFallback() {
        let manifest = ImageManifest(
            imageID: "fedora44-aarch64", architecture: "aarch64", baseName: "base",
            baseURL: "https://example.invalid", baseSHA256: "x", sourceCommit: "y",
            sourceBranch: "z", sourceDirty: false, lumaPackages: [], fedoraPackages: [],
            diskSHA256: nil, diskBytes: 0, createdAt: "now", builderVersion: "0.1.0",
            emulatorVersion: "0.1.0", buildTools: [:]
        )
        XCTAssertEqual(manifest.lumaCompleteness, "fedora-fallback")
    }

    func testAnImageReportsItsBundleCompleteness() {
        let record = LumaBundleRecord(
            bundleName: "b", bundleSHA256: "s", sourceCommit: "c", sourceBranch: "br",
            completeness: "incomplete", packageCount: 22, substitutions: [], absentOptional: []
        )
        let manifest = ImageManifest(
            imageID: "fedora44-aarch64", architecture: "aarch64", baseName: "base",
            baseURL: "https://example.invalid", baseSHA256: "x", sourceCommit: "y",
            sourceBranch: "z", sourceDirty: false, lumaPackages: ["a-1-1.noarch"],
            fedoraPackages: [], diskSHA256: nil, diskBytes: 0, createdAt: "now",
            builderVersion: "0.1.0", emulatorVersion: "0.1.0", buildTools: [:],
            lumaBundle: record
        )
        XCTAssertEqual(manifest.lumaCompleteness, "incomplete")
    }
}

final class PNGTests: XCTestCase {
    /// A capture is only reported as successful when the file really decodes,
    /// so this is what stands between "wrote 0 bytes" and "screenshot ok".
    private func write(_ bytes: [UInt8]) throws -> URL {
        let url = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("png-\(UUID().uuidString).png")
        try Data(bytes).write(to: url)
        return url
    }

    func testDimensionsAreReadFromTheHeader() throws {
        var bytes = PNG.signature
        bytes += [0, 0, 0, 13]
        bytes += Array("IHDR".utf8)
        bytes += [0, 0, 0x04, 0x92]     // 1170
        bytes += [0, 0, 0x09, 0xE4]     // 2532
        bytes += [8, 6, 0, 0, 0, 0, 0, 0, 0]
        let url = try write(bytes)
        defer { try? FileManager.default.removeItem(at: url) }
        let size = PNG.dimensions(of: url)
        XCTAssertEqual(size?.width, 1170)
        XCTAssertEqual(size?.height, 2532)
    }

    func testANonPNGIsRejected() throws {
        let url = try write(Array("not a png at all, not even close".utf8))
        defer { try? FileManager.default.removeItem(at: url) }
        XCTAssertNil(PNG.dimensions(of: url))
        XCTAssertFalse(PNG.isValid(url))
    }

    func testAnEmptyFileIsRejected() throws {
        let url = try write([])
        defer { try? FileManager.default.removeItem(at: url) }
        XCTAssertFalse(PNG.isValid(url))
    }

    func testAZeroSizedImageIsRejected() throws {
        var bytes = PNG.signature
        bytes += [0, 0, 0, 13] + Array("IHDR".utf8)
        bytes += [0, 0, 0, 0, 0, 0, 0, 0]
        bytes += [8, 6, 0, 0, 0, 0, 0, 0, 0]
        let url = try write(bytes)
        defer { try? FileManager.default.removeItem(at: url) }
        XCTAssertNil(PNG.dimensions(of: url))
    }
}
