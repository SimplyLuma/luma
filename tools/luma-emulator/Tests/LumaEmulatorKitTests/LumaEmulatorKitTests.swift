// SPDX-License-Identifier: Apache-2.0

import XCTest
@testable import LumaEmulatorKit

final class ViewportTests: XCTestCase {
    func testHandheldPresetsKeepLogicalWidth() throws {
        // Adaptive layout is driven by the logical width. The preset must give
        // the guest more physical pixels without changing the width Luma sees.
        let viewport = try Viewport.parse("390x844")
        XCTAssertEqual(viewport.width, 390)
        XCTAssertEqual(viewport.scale, 3)
        XCTAssertEqual(viewport.pixelWidth, 1170)
        XCTAssertEqual(viewport.pixelHeight, 2532)
    }

    func testArbitraryViewportIsAccepted() throws {
        let viewport = try Viewport.parse("1024x768")
        XCTAssertEqual(viewport.width, 1024)
        XCTAssertEqual(viewport.height, 768)
    }

    /// The runtime re-parses whatever string the CLI hands it, so a viewport
    /// that does not round-trip loses its scale and silently becomes a preset.
    func testDescriptorRoundTripsThroughParse() throws {
        for text in ["390x844@2", "1440x900@1", "360x740@3", "1024x768@2"] {
            let parsed = try Viewport.parse(text)
            let again = try Viewport.parse(parsed.descriptor)
            XCTAssertEqual(parsed, again, "\(text) did not round-trip")
            XCTAssertEqual(again.scale, parsed.scale)
        }
    }

    func testPresetDescriptorKeepsItsScale() throws {
        let preset = try Viewport.parse("390x844")
        XCTAssertEqual(preset.scale, 3)
        XCTAssertEqual(try Viewport.parse(preset.descriptor).scale, 3)
    }

    func testRotationSwapsAxesAndKeepsScale() throws {
        let landscape = try Viewport.parse("390x844").rotated()
        XCTAssertEqual(landscape.width, 844)
        XCTAssertEqual(landscape.height, 390)
        XCTAssertEqual(landscape.scale, 3)
    }

    func testExplicitScaleSuffixIsAccepted() throws {
        let viewport = try Viewport.parse("1440x900@2")
        XCTAssertEqual(viewport.width, 1440)
        XCTAssertEqual(viewport.height, 900)
        XCTAssertEqual(viewport.scale, 2)
        XCTAssertEqual(viewport.pixelWidth, 2880)
    }

    func testScaleOneIsAccepted() throws {
        XCTAssertEqual(try Viewport.parse("1280x800@1").scale, 1)
    }

    func testNonsenseViewportIsRejected() {
        for text in ["", "wide", "0x0", "12x12", "99999x99999", "390", "390x844@9", "390x844@x", "1x2x3"] {
            XCTAssertThrowsError(try Viewport.parse(text), "accepted \(text)")
        }
    }

    func testModeDefaultsDifferButShareOneContract() {
        XCTAssertEqual(Mode.desktop.defaultViewport.width, 1440)
        XCTAssertEqual(Mode.mobile.defaultViewport.width, 390)
        XCTAssertNotEqual(Mode.desktop.sessionName, Mode.mobile.sessionName)
    }
}

final class PathSafetyTests: XCTestCase {
    private func makeRoot() throws -> EmulatorPaths {
        let root = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("luma-emulator-tests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return EmulatorPaths(state: root)
    }

    func testOwnedPathsAreAccepted() throws {
        let paths = try makeRoot()
        defer { try? FileManager.default.removeItem(at: paths.state) }
        XCTAssertNoThrow(try paths.assertOwned(paths.machine("desktop")))
        XCTAssertNoThrow(try paths.assertOwned(paths.image("fedora44-aarch64")))
    }

    func testTheStateRootItselfIsRefused() throws {
        let paths = try makeRoot()
        defer { try? FileManager.default.removeItem(at: paths.state) }
        XCTAssertThrowsError(try paths.assertOwned(paths.state))
    }

    func testPathsOutsideTheStateTreeAreRefused() throws {
        let paths = try makeRoot()
        defer { try? FileManager.default.removeItem(at: paths.state) }
        for candidate in ["/", "/Users", NSHomeDirectory(), "/tmp"] {
            XCTAssertThrowsError(
                try paths.assertOwned(URL(fileURLWithPath: candidate)),
                "accepted \(candidate)"
            )
        }
    }

    func testTraversalOutOfTheStateTreeIsRefused() throws {
        let paths = try makeRoot()
        defer { try? FileManager.default.removeItem(at: paths.state) }
        let escape = paths.machines.appendingPathComponent("../../../../etc")
        XCTAssertThrowsError(try paths.assertOwned(escape))
    }

    func testStateLocationHonoursTheTestOverride() {
        XCTAssertEqual(
            EmulatorPaths(state: URL(fileURLWithPath: "/tmp/example")).state.lastPathComponent,
            "example"
        )
    }
}

final class ControlProtocolTests: XCTestCase {
    func testRequestsAndResponsesSurviveARoundTrip() throws {
        let request = ControlRequest(
            command: "preview-app",
            arguments: ["app": .string("notes"), "widths": .array([.int(360), .int(390)])]
        )
        let decoded = try ControlCodec.decode(
            ControlRequest.self, from: ControlCodec.encode(request)
        )
        XCTAssertEqual(decoded.command, "preview-app")
        XCTAssertEqual(decoded.arguments["app"]?.stringValue, "notes")
        XCTAssertEqual(decoded.arguments["widths"]?.arrayValue?.count, 2)
    }

    func testFramingTerminatesWithExactlyOneNewline() throws {
        let data = try ControlCodec.encode(ControlRequest(command: "ping"))
        XCTAssertEqual(data.last, 0x0A)
        XCTAssertEqual(data.filter { $0 == 0x0A }.count, 1)
    }

    func testFailureCarriesTheExitCodeTheCLIWillUse() {
        let response = ControlResponse.failure(.timedOut("late"))
        XCTAssertFalse(response.ok)
        XCTAssertEqual(response.exitCode, ExitCode.timedOut.rawValue)
        XCTAssertEqual(response.errorCode, "timed-out")
    }

    func testJSONOutputCarriesNoANSIEscapes() {
        let text = ControlCodec.pretty(.object([
            "health": .string("ready"), "guestAddress": .string("192.168.64.2"),
        ]))
        XCTAssertFalse(text.contains("\u{001B}"))
        XCTAssertTrue(text.contains("\"health\""))
    }

    func testExitCodesAreDistinct() {
        let codes: [ExitCode] = [
            .success, .failure, .usage, .notRunning, .unsupportedHost,
            .timedOut, .resourceConflict, .guestUnhealthy, .notProvisioned,
        ]
        XCTAssertEqual(Set(codes.map(\.rawValue)).count, codes.count)
    }
}

final class GuestDiscoveryTests: XCTestCase {
    func testTheLastAnnouncedAddressWins() throws {
        let url = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("console-\(UUID().uuidString).log")
        try """
        [  OK  ] Started something
        luma-emulator-address=192.168.64.9
        more boot noise
        luma-emulator-address=192.168.64.2
        login:
        """.write(to: url, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: url) }
        XCTAssertEqual(GuestDiscovery.addressFromConsole(url), "192.168.64.2")
    }

    func testGarbageIsNotMistakenForAnAddress() throws {
        let url = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("console-\(UUID().uuidString).log")
        try "luma-emulator-address=999.1.1.1\nluma-emulator-address=..\n"
            .write(to: url, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: url) }
        XCTAssertNil(GuestDiscovery.addressFromConsole(url))
    }

    func testLeaseMACsAreNormalisedBeforeComparison() {
        XCTAssertEqual(GuestDiscovery.normalizeMAC("52:54:0:1:2:3"), "52:54:00:01:02:03")
        XCTAssertEqual(GuestDiscovery.normalizeMAC("52:54:00:01:02:03"), "52:54:00:01:02:03")
    }
}

final class GuestOptionQuotingTests: XCTestCase {
    /// The emulator's state directory is "Luma Emulator". An unquoted space in
    /// an `ssh -o` value is not a cosmetic problem: `ControlPath` becomes a
    /// syntax error and `UserKnownHostsFile` silently becomes two file names.
    func testPathsWithSpacesAreQuoted() {
        let quoted = Guest.quoted("/Users/x/Library/Application Support/Luma Emulator/run/known_hosts")
        XCTAssertTrue(quoted.hasPrefix("\""))
        XCTAssertTrue(quoted.hasSuffix("\""))
        XCTAssertTrue(quoted.contains("Luma Emulator"))
    }

    func testEmbeddedQuotesAreEscaped() {
        XCTAssertEqual(Guest.quoted("/tmp/a\"b"), "\"/tmp/a\\\"b\"")
    }

    func testEverySSHOptionPathIsQuoted() {
        let guest = Guest(
            address: "192.168.64.2",
            keyPath: "/tmp/key",
            knownHostsPath: "/tmp/with space/known_hosts",
            controlPath: "/tmp/with space/ssh-control"
        )
        let options = guest.sshOptions
        for option in options where option.contains("=") && option.contains(" ") {
            let value = String(option.split(separator: "=", maxSplits: 1)[1])
            XCTAssertTrue(
                value.hasPrefix("\"") && value.hasSuffix("\""),
                "unquoted ssh option value with a space: \(option)"
            )
        }
        // The master must never be started implicitly by an ordinary command:
        // it would inherit that command's pipes and outlive it.
        XCTAssertTrue(options.contains("ControlMaster=no"))
    }
}

final class ScenarioCatalogueTests: XCTestCase {
    /// The catalogue is the emulator's promise about what is real. These checks
    /// exist so an unsupported scenario can never quietly acquire a script that
    /// fakes the state it claims to reproduce.
    func testCatalogueLoadsAndIsInternallyConsistent() throws {
        let catalogue = try ScenarioCatalogue.load()
        XCTAssertFalse(catalogue.scenarios.isEmpty)
        var seen = Set<String>()
        for scenario in catalogue.scenarios {
            XCTAssertTrue(seen.insert(scenario.id).inserted, "duplicate id \(scenario.id)")
            XCTAssertFalse(scenario.summary.isEmpty, "\(scenario.id) has no summary")
            XCTAssertFalse(scenario.interface.isEmpty, "\(scenario.id) names no interface")
            if scenario.supported {
                XCTAssertFalse(scenario.script.isEmpty, "\(scenario.id) is supported but empty")
                XCTAssertNil(scenario.gap, "\(scenario.id) is supported but records a gap")
            } else {
                XCTAssertTrue(scenario.script.isEmpty, "\(scenario.id) is unsupported but scripted")
                XCTAssertNotNil(scenario.gap, "\(scenario.id) is unsupported but states no gap")
            }
        }
    }

    func testNormalScenarioExistsSoEveryOtherOneCanBeUndone() throws {
        let catalogue = try ScenarioCatalogue.load()
        let normal = try XCTUnwrap(catalogue.scenario(named: "normal"))
        XCTAssertTrue(normal.supported)
    }

    func testTelephonyAndPowerAreHonestlyMarkedUnsupported() throws {
        // The emulator has no modem and no battery. Claiming either would be
        // exactly the kind of convincing fake the contract forbids.
        let catalogue = try ScenarioCatalogue.load()
        for id in ["incoming-call", "missed-call", "active-call", "low-battery", "charging"] {
            let scenario = try XCTUnwrap(catalogue.scenario(named: id), "missing \(id)")
            XCTAssertFalse(scenario.supported, "\(id) claims to be supported")
        }
    }

    func testDescribeReportsSupportedAndTotalCounts() throws {
        let catalogue = try ScenarioCatalogue.load()
        let described = catalogue.describe().objectValue
        XCTAssertEqual(described?["catalogueCount"]?.intValue, catalogue.scenarios.count)
        XCTAssertEqual(
            described?["supportedCount"]?.intValue,
            catalogue.scenarios.filter(\.supported).count
        )
    }
}

final class HostReportTests: XCTestCase {
    func testDoctorDetectsThisMachineAndTheRequiredChecksAreNamed() throws {
        let paths = EmulatorPaths(
            state: URL(fileURLWithPath: NSTemporaryDirectory())
                .appendingPathComponent("luma-doctor-\(UUID().uuidString)")
        )
        let report = HostInspector.report(paths: paths)
        XCTAssertFalse(report.architecture.isEmpty)
        XCTAssertFalse(report.macOSVersion.isEmpty)
        XCTAssertGreaterThan(report.cpuCount, 0)
        let required = Set(report.checks.filter(\.required).map(\.id))
        for id in ["host-architecture", "macos-version", "virtualization-framework"] {
            XCTAssertTrue(required.contains(id), "\(id) is not treated as required")
        }
        // Every check must explain itself; a bare pass or fail is not a report.
        for check in report.checks {
            XCTAssertFalse(check.detail.isEmpty, "\(check.id) has no detail")
        }
    }
}
