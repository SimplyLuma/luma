// SPDX-License-Identifier: Apache-2.0

import AppKit
import Foundation
import Virtualization
import LumaEmulatorKit

/// The Luma Emulator runtime process.
///
/// It owns the guest, serves the control socket, and — when asked — draws the
/// Mac window. The `luma-emulator` command and the window are both clients of
/// the same `HostController`, so there is exactly one place that knows what the
/// emulator is doing.
struct HostArguments {
    var machine = "default"
    var mode = Mode.desktop
    var viewport: Viewport?
    var display = "window"
    var cpuCount: Int?
    var memoryMiB: Int?
    var imageID = "fedora44-aarch64"

    static func parse(_ arguments: [String]) throws -> HostArguments {
        var parsed = HostArguments()
        var index = 0
        while index < arguments.count {
            let argument = arguments[index]
            func value() throws -> String {
                index += 1
                guard index < arguments.count else {
                    throw EmulatorError.usage("\(argument) needs a value")
                }
                return arguments[index]
            }
            switch argument {
            case "--machine": parsed.machine = try value()
            case "--mode":
                let raw = try value()
                guard let mode = Mode(rawValue: raw) else {
                    throw EmulatorError.usage("mode must be desktop or mobile")
                }
                parsed.mode = mode
            case "--viewport": parsed.viewport = try Viewport.parse(try value())
            case "--display": parsed.display = try value()
            case "--cpu": parsed.cpuCount = Int(try value())
            case "--memory": parsed.memoryMiB = Int(try value())
            case "--image": parsed.imageID = try value()
            default:
                throw EmulatorError.usage("unknown runtime argument: \(argument)")
            }
            index += 1
        }
        return parsed
    }
}

func fail(_ message: String, code: ExitCode = .failure) -> Never {
    FileHandle.standardError.write(Data("luma-emulator-runtime: \(message)\n".utf8))
    exit(code.rawValue)
}

let paths = EmulatorPaths.resolved()
do {
    try paths.createDirectories()
} catch {
    fail("could not create the emulator state directory: \(error)")
}

let arguments: HostArguments
do {
    arguments = try HostArguments.parse(Array(CommandLine.arguments.dropFirst()))
} catch {
    fail("\(error)", code: .usage)
}

let report = HostInspector.report(paths: paths)
guard report.supported else {
    let failures = report.checks.filter { $0.required && !$0.ok }.map(\.detail)
    fail("this Mac cannot run the emulator: \(failures.joined(separator: "; "))", code: .unsupportedHost)
}

// Sizing follows the host rather than a hard-coded guess, and stays well
// inside what the Mac can spare so the developer's own tools keep running.
let defaultCPU = max(2, min(report.cpuCount - 2, 6))
let defaultMemory = max(2048, min((report.memoryGiB * 1024) / 4, 6144))

let spec = MachineSpec(
    name: arguments.machine,
    mode: arguments.mode,
    viewport: arguments.viewport ?? arguments.mode.defaultViewport,
    cpuCount: arguments.cpuCount ?? defaultCPU,
    memoryMiB: arguments.memoryMiB ?? defaultMemory,
    imageID: arguments.imageID
)

let runtime = VMRuntime(paths: paths, spec: spec)
let controller = HostController(paths: paths, runtime: runtime)

do {
    try FileManager.default.createDirectory(
        at: runtime.machineDirectory, withIntermediateDirectories: true
    )
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    try encoder.encode(spec).write(to: runtime.specURL)
    try controller.startServing()
} catch let error as EmulatorError {
    fail(error.message, code: error.exit)
} catch {
    fail("\(error)")
}

let windowController = DisplayWindowController(controller: controller)
let showsWindow = arguments.display == "window"
// The control layer captures through the window when there is one. It never
// reaches into AppKit itself; the window hands it a closure.
controller.hostCapture = showsWindow ? { windowController.capture() } : nil
controller.hostWindowState = showsWindow ? { windowController.describe() } : nil

let startSemaphore = DispatchSemaphore(value: 0)
var startError: Error?
runtime.start { result in
    if case .failure(let error) = result { startError = error }
    startSemaphore.signal()
}
_ = startSemaphore.wait(timeout: .now() + 60)
if let startError {
    controller.stopServing()
    fail("the guest failed to start: \(startError)")
}

// The console log carries the guest's address announcement; watch it so
// `status` can answer truthfully as soon as the guest is up.
DispatchQueue.global().async {
    while true {
        controller.refreshGuestAddress()
        sleep(2)
    }
}

signal(SIGTERM, SIG_IGN)
let termination = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
termination.setEventHandler {
    runtime.forceStop { _ in
        controller.stopServing()
        exit(0)
    }
}
termination.resume()

// A headless runtime whose guest has stopped has nothing left to serve, and
// leaving it holding the control socket would block the next start. It exits
// with the guest rather than lingering.
NotificationCenter.default.addObserver(
    forName: .lumaEmulatorGuestStopped, object: nil, queue: nil
) { _ in
    guard !showsWindow else { return }
    // Not immediately: the request that asked for this shutdown is still
    // waiting for its reply, and exiting now would close the socket underneath
    // it and report a clean stop as a transport failure.
    DispatchQueue.global().asyncAfter(deadline: .now() + 3) {
        controller.stopServing()
        exit(0)
    }
}

if showsWindow {
    let application = NSApplication.shared
    application.setActivationPolicy(.regular)
    let delegate = HostAppDelegate(
        controller: controller, runtime: runtime, windows: windowController
    )
    application.delegate = delegate
    application.run()
} else {
    RunLoop.main.run()
}
