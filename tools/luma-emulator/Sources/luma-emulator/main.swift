// SPDX-License-Identifier: Apache-2.0

import Foundation
import LumaEmulatorKit

let rawArguments = Array(CommandLine.arguments.dropFirst())
let arguments = Arguments(rawArguments)
let output = Output(json: arguments.flag("json"))
let paths = EmulatorPaths.resolved()
let client = ControlClient(socketPath: paths.controlSocket.path)
let bootstrap = Bootstrap(paths: paths, output: output)
let defaultTimeout = TimeInterval(arguments.integer("timeout") ?? 300)

func send(_ command: String, _ payload: [String: JSONValue] = [:]) throws -> JSONValue {
    let response = try client.send(
        ControlRequest(command: command, arguments: payload), timeout: defaultTimeout
    )
    guard response.ok else {
        throw EmulatorError(
            code: response.errorCode ?? "runtime",
            message: response.errorMessage ?? "the runtime refused the request",
            exit: ExitCode(rawValue: response.exitCode) ?? .failure
        )
    }
    return response.data ?? .object([:])
}

/// The runtime binary sits beside the CLI in a development build and inside the
/// application bundle in an installed one.
func runtimeExecutable() throws -> String {
    let here = URL(fileURLWithPath: CommandLine.arguments[0])
        .resolvingSymlinksInPath()
        .deletingLastPathComponent()
    let candidates = [
        here.appendingPathComponent("LumaEmulatorHost"),
        here.appendingPathComponent("../Luma Emulator.app/Contents/MacOS/LumaEmulatorHost"),
        here.appendingPathComponent("../../MacOS/LumaEmulatorHost"),
        URL(fileURLWithPath: "/Applications/Luma Emulator.app/Contents/MacOS/LumaEmulatorHost"),
    ]
    for candidate in candidates {
        let resolved = candidate.standardizedFileURL
        if FileManager.default.isExecutableFile(atPath: resolved.path) { return resolved.path }
    }
    throw EmulatorError.failure(
        "no-runtime",
        "the LumaEmulatorHost runtime was not found beside \(here.path). Run scripts/emulator/build.sh."
    )
}

/// A fresh overlay is a fresh guest with fresh host keys, usually at a NAT
/// address a previous guest also used. Carrying the old trust forward would make
/// every connection fail with a host-key mismatch, so the emulator's own
/// known_hosts and multiplexed socket are dropped whenever an overlay is
/// created or replaced. Only the emulator's files are touched; the developer's
/// ~/.ssh is never read or written.
func resetGuestTrust() {
    for name in ["known_hosts", "ssh-control"] {
        try? FileManager.default.removeItem(at: paths.run.appendingPathComponent(name))
    }
}

/// Waits, briefly and boundedly, for the runtime to release the control socket.
func awaitRuntimeExit(timeout: TimeInterval = 30) {
    let deadline = Date().addingTimeInterval(timeout)
    while Date() < deadline {
        if !client.isListening() { return }
        usleep(250_000)
    }
}

func machineName() -> String {
    arguments.value("machine") ?? ProcessInfo.processInfo.environment["LUMA_EMULATOR_MACHINE"] ?? "desktop"
}

func requireProject() throws -> ProjectLink {
    guard let data = try? Data(contentsOf: paths.projectLink),
          let link = try? JSONDecoder().decode(ProjectLink.self, from: data) else {
        throw EmulatorError.usage(
            "no Project Luma checkout is connected. Run `luma-emulator project connect <path>`."
        )
    }
    return link
}


/// Starts the runtime process and waits until it is answering on the control
/// socket. If it dies on the way up, the reason from its log is reported rather
/// than a bare timeout.
func spawnRuntime(name: String, mode: Mode, viewport: Viewport, headless: Bool) throws {
    let executable = try runtimeExecutable()
    var runtimeArguments = [
        "--machine", name,
        "--mode", mode.rawValue,
        "--viewport", viewport.descriptor,
        "--display", headless ? "headless" : "window",
        "--image", Bootstrap.base.id,
    ]
    if let cpu = arguments.integer("cpu") { runtimeArguments += ["--cpu", "\(cpu)"] }
    if let memory = arguments.integer("memory") { runtimeArguments += ["--memory", "\(memory)"] }

    let process = Process()
    process.executableURL = URL(fileURLWithPath: executable)
    process.arguments = runtimeArguments
    let runtimeLog = paths.logs.appendingPathComponent("runtime-\(name).log")
    if !FileManager.default.fileExists(atPath: runtimeLog.path) {
        FileManager.default.createFile(atPath: runtimeLog.path, contents: nil)
    }
    let handle = try FileHandle(forWritingTo: runtimeLog)
    handle.seekToEndOfFile()
    process.standardOutput = handle
    process.standardError = handle
    try process.run()

    let deadline = Date().addingTimeInterval(60)
    while Date() < deadline {
        if client.isListening() { return }
        if !process.isRunning {
            let tail = (try? String(contentsOf: runtimeLog, encoding: .utf8))?
                .split(separator: "\n").suffix(12).joined(separator: "\n") ?? ""
            throw EmulatorError.failure(
                "runtime-exited", "the runtime exited during start.\n\(tail)"
            )
        }
        usleep(250_000)
    }
    throw EmulatorError.timedOut("the runtime did not open its control socket within 60s")
}

/// Finds the Luma package bundle to admit.
///
/// An explicit `--luma-bundle` always wins. Otherwise the emulator looks in its
/// own bundles directory, and refuses to guess when there is more than one —
/// silently picking a bundle would decide which Luma revision the image is.
func resolveBundle() throws -> LumaBundle? {
    if arguments.flag("no-luma-bundle") {
        output.note("Building a Fedora substrate image: --no-luma-bundle was given.")
        return nil
    }
    var candidate: URL?
    if let explicit = arguments.value("luma-bundle") {
        candidate = URL(fileURLWithPath: explicit).standardizedFileURL
    } else {
        let directory = paths.state.appendingPathComponent("bundles", isDirectory: true)
        let found = ((try? FileManager.default.contentsOfDirectory(
            at: directory, includingPropertiesForKeys: nil
        )) ?? []).filter {
            $0.lastPathComponent.hasSuffix(".tar.gz")
                || $0.lastPathComponent.hasSuffix(".tar.zst")
        }
        if found.count > 1 {
            throw EmulatorError.usage(
                """
                \(found.count) bundles are in \(directory.path). Name the one to admit with \
                --luma-bundle <path>, or pass --no-luma-bundle to build a Fedora substrate.
                """
            )
        }
        candidate = found.first
    }
    guard let candidate else {
        throw EmulatorError.usage(
            """
            no Luma package bundle found. Export one on the build host with
            scripts/emulator/export-luma-bundle, put it in
            \(paths.state.appendingPathComponent("bundles").path), and try again —
            or pass --no-luma-bundle to build a Fedora substrate image that is
            explicitly not Luma.
            """
        )
    }
    let bundle = try LumaBundle.inspect(
        at: candidate, expectedSHA256: arguments.value("luma-bundle-sha256")
    )
    return bundle
}

func commandBundle() throws {
    let action = arguments.positional.count > 1 ? arguments.positional[1] : "inspect"
    guard action == "inspect" else {
        throw EmulatorError.usage("bundle currently supports 'inspect'")
    }
    let path = arguments.positional.count > 2
        ? arguments.positional[2]
        : (arguments.value("luma-bundle") ?? "")
    let url: URL
    if path.isEmpty {
        guard let found = try resolveBundle() else {
            throw EmulatorError.usage("bundle inspect needs a path")
        }
        output.emit(found.describe()) { describeBundle(found) }
        return
    }
    url = URL(fileURLWithPath: path).standardizedFileURL
    let bundle = try LumaBundle.inspect(
        at: url, expectedSHA256: arguments.value("luma-bundle-sha256")
    )
    output.emit(bundle.describe()) { describeBundle(bundle) }
}

func describeBundle(_ bundle: LumaBundle) -> String {
    let manifest = bundle.manifest
    var lines = [
        "\(manifest.bundleName)",
        "  sha256        \(bundle.sha256)",
        "  architecture  \(manifest.architecture), Fedora \(manifest.fedoraRelease)",
        "  source        \(manifest.sourceCommit) on \(manifest.sourceBranch)\(manifest.sourceDirty ? " (dirty)" : "")",
        "  built         \(manifest.createdAt)",
        "  completeness  \(manifest.completeness)",
        "  packages      \(manifest.packageCount)",
    ]
    if !manifest.substitutions.isEmpty {
        lines.append("  substitutions:")
        for substitution in manifest.substitutions {
            lines.append(
                "    \(substitution.package): pinned \(substitution.pinnedRelease), "
                + "using \(substitution.usedRelease)"
            )
        }
    }
    if !manifest.absentOptional.isEmpty {
        lines.append("  absent (optional): \(manifest.absentOptional.joined(separator: ", "))")
    }
    for omission in manifest.omitted ?? [] {
        lines.append("  OMITTED \(omission.package) (\(omission.requirement)): \(omission.reason)")
    }
    lines.append("")
    lines.append("  " + manifest.installedNEVRAs.joined(separator: "\n  "))
    return lines.joined(separator: "\n")
}

func commandProvision() throws {
    let provisioner = Provisioner(
        paths: paths, output: output, client: client, bootstrap: bootstrap,
        spawn: { name, mode, viewport, headless in
            try spawnRuntime(name: name, mode: mode, viewport: viewport, headless: headless)
        },
        resetTrust: resetGuestTrust,
        send: { command, payload in try send(command, payload) }
    )
    let bundle = try resolveBundle()
    try provisioner.run(force: arguments.flag("force"), bundle: bundle)
}

// MARK: - Commands


func commandDoctor() {
    let report = HostInspector.report(paths: paths)
    let payload = JSONValue.object([
        "supported": .bool(report.supported),
        "architecture": .string(report.architecture),
        "chip": .string(report.chip),
        "model": .string(report.model),
        "memoryGiB": .int(report.memoryGiB),
        "cpuCount": .int(report.cpuCount),
        "macOSVersion": .string(report.macOSVersion),
        "backend": .string("apple-virtualization-framework"),
        "guestArchitecture": .string("aarch64"),
        "checks": .array(report.checks.map { check in
            .object([
                "id": .string(check.id),
                "title": .string(check.title),
                "ok": .bool(check.ok),
                "required": .bool(check.required),
                "detail": .string(check.detail),
            ])
        }),
    ])
    output.emit(payload) {
        var lines = [
            "\(report.chip) — \(report.memoryGiB) GiB — macOS \(report.macOSVersion)",
            "Backend: Apple Virtualization.framework, native aarch64 guest",
            "",
        ]
        for check in report.checks {
            let mark = check.ok ? "ok  " : (check.required ? "FAIL" : "warn")
            lines.append("[\(mark)] \(check.title): \(check.detail)")
        }
        lines.append("")
        lines.append(report.supported
            ? "This Mac can run the Luma Emulator."
            : "This Mac cannot run the Luma Emulator.")
        return lines.joined(separator: "\n")
    }
    if !report.supported { exit(ExitCode.unsupportedHost.rawValue) }
}

func commandBootstrap() throws {
    let report = HostInspector.report(paths: paths)
    guard report.supported else {
        throw EmulatorError.unsupportedHost("run `luma-emulator doctor` to see what is missing")
    }
    try paths.createDirectories()
    let publicKey = try bootstrap.ensureGuestKey()
    output.note("Guest key ready at \(paths.guestKey.path) (0600, never committed).")

    let raw = try bootstrap.ensureBaseRaw()
    let imageDirectory = paths.image(Bootstrap.base.id)
    try FileManager.default.createDirectory(at: imageDirectory, withIntermediateDirectories: true)
    let factoryDisk = imageDirectory.appendingPathComponent("disk.raw")

    if !FileManager.default.fileExists(atPath: factoryDisk.path) || arguments.flag("force") {
        output.note("Creating the factory disk from the verified base image…")
        try bootstrap.cloneDisk(from: raw, to: factoryDisk)
    }
    let seed = imageDirectory.appendingPathComponent("seed.iso")
    try bootstrap.makeSeed(at: seed, publicKey: publicKey)

    let manifest = ImageManifest(
        imageID: Bootstrap.base.id,
        architecture: "aarch64",
        baseName: Bootstrap.base.fileName,
        baseURL: Bootstrap.base.url,
        baseSHA256: Bootstrap.base.sha256,
        sourceCommit: (try? requireProject()).flatMap { link -> String? in
            guard let git = Shell.which("git") else { return nil }
            return (try? Shell.run(git, ["-C", link.path, "rev-parse", "HEAD"], timeout: 15))?
                .trimmedOutput
        } ?? "unconnected",
        sourceBranch: "unknown",
        sourceDirty: false,
        lumaPackages: [],
        fedoraPackages: [],
        diskSHA256: nil,
        diskBytes: Shell.fileSize(factoryDisk.path),
        createdAt: ISO8601DateFormatter().string(from: Date()),
        builderVersion: EmulatorVersion.current,
        emulatorVersion: EmulatorVersion.current,
        buildTools: [
            "qemu-img": Shell.which("qemu-img") ?? "absent",
            "hdiutil": Shell.which("hdiutil") ?? "absent",
        ]
    )
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
    try encoder.encode(manifest).write(to: imageDirectory.appendingPathComponent("manifest.json"))

    output.emit(.object([
        "imageID": .string(manifest.imageID),
        "factoryDisk": .string(factoryDisk.path),
        "seed": .string(seed.path),
        "provisioned": .bool(false),
        "next": .string("luma-emulator provision"),
    ])) {
        """
        Factory disk ready at \(factoryDisk.path)
        The base system is Fedora 44 aarch64, verified against its pinned SHA-256.
        Run `luma-emulator provision` next to install the shells and preview tooling.
        """
    }
}

func commandStart() throws {
    let modeText = arguments.positional.count > 1 ? arguments.positional[1] : "desktop"
    guard let mode = Mode(rawValue: modeText) else {
        throw EmulatorError.usage("start needs 'desktop' or 'mobile'")
    }
    let name = arguments.value("machine") ?? mode.rawValue
    let viewport = try arguments.value("viewport").map { try Viewport.parse($0) }
        ?? mode.defaultViewport

    if client.isListening() {
        let status = try send("status")
        let running = status.objectValue?["machine"]?.stringValue
        if running == name {
            output.emit(status) { "The \(name) guest is already running." }
            return
        }
        throw EmulatorError.conflict(
            "the '\(running ?? "unknown")' guest already owns the control socket. Stop it first."
        )
    }

    let imageDisk = paths.image(Bootstrap.base.id).appendingPathComponent("disk.raw")
    guard FileManager.default.fileExists(atPath: imageDisk.path) else {
        throw EmulatorError.notProvisioned(
            "no factory image yet. Run `luma-emulator bootstrap` first."
        )
    }
    let machineDirectory = paths.machine(name)
    let disk = machineDirectory.appendingPathComponent("disk.raw")
    if !FileManager.default.fileExists(atPath: disk.path) {
        output.note("Creating the \(name) overlay from the factory image…")
        resetGuestTrust()
        try bootstrap.cloneDisk(from: imageDisk, to: disk)
        let seed = paths.image(Bootstrap.base.id).appendingPathComponent("seed.iso")
        if FileManager.default.fileExists(atPath: seed.path) {
            try bootstrap.cloneDisk(
                from: seed, to: machineDirectory.appendingPathComponent("seed.iso")
            )
        }
    }

    try spawnRuntime(name: name, mode: mode, viewport: viewport, headless: arguments.flag("headless"))

    let waitSeconds = arguments.integer("wait") ?? 300
    if arguments.flag("no-wait") {
        output.emit(try send("status")) { "The \(name) guest is starting." }
        return
    }
    output.note("Waiting for the guest to become ready…")
    // The transport must outlast the wait it is asking the runtime to perform,
    // or the client gives up on a guest that was going to arrive.
    let readyResponse = try client.send(
        ControlRequest(command: "await-ready", arguments: ["timeout": .int(waitSeconds)]),
        timeout: TimeInterval(waitSeconds + 60)
    )
    guard readyResponse.ok else {
        throw EmulatorError(
            code: readyResponse.errorCode ?? "runtime",
            message: readyResponse.errorMessage ?? "the guest did not become ready",
            exit: ExitCode(rawValue: readyResponse.exitCode) ?? .failure
        )
    }
    let ready = readyResponse.data ?? .object([:])
    output.emit(ready) {
        let address = ready.objectValue?["guestAddress"]?.stringValue ?? "unknown"
        return "The \(name) guest is ready at \(address)."
    }
}

func commandProject() throws {
    let action = arguments.positional.count > 1 ? arguments.positional[1] : "show"
    switch action {
    case "connect":
        let path = try arguments.requirePositional(2, "a path to a Project Luma checkout")
        let resolved = URL(fileURLWithPath: path).standardizedFileURL
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: resolved.path, isDirectory: &isDirectory),
              isDirectory.boolValue else {
            throw EmulatorError.usage("\(resolved.path) is not a directory")
        }
        // Refuse anything that is not recognisably Project Luma, so a mistyped
        // path cannot become the tree that gets synchronized into a guest.
        let markers = ["src/prairie-core", "src/luma-platform", "AGENTS.md"]
        let missing = markers.filter {
            !FileManager.default.fileExists(atPath: resolved.appendingPathComponent($0).path)
        }
        guard missing.isEmpty else {
            throw EmulatorError.usage(
                "\(resolved.path) does not look like a Project Luma checkout (missing \(missing.joined(separator: ", ")))"
            )
        }
        try paths.createDirectories()
        let link = ProjectLink(
            path: resolved.path, connectedAt: ISO8601DateFormatter().string(from: Date())
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(link).write(to: paths.projectLink)
        output.emit(.object([
            "project": .string(link.path),
            "connectedAt": .string(link.connectedAt),
            "access": .string("read-only, one-way host to guest"),
        ])) { "Connected \(link.path). The emulator reads it; it never writes to it." }
    case "show":
        let link = try requireProject()
        output.emit(.object([
            "project": .string(link.path),
            "connectedAt": .string(link.connectedAt),
        ])) { link.path }
    default:
        throw EmulatorError.usage("project takes 'connect <path>' or 'show'")
    }
}

func commandPreview() throws {
    let kind = try arguments.requirePositional(1, "'app' or 'shell'")
    switch kind {
    case "app":
        let name = try arguments.requirePositional(2, "an application name")
        let appearance = arguments.value("appearance", default: "light")
        if arguments.flag("matrix") {
            let widths = arguments.value("widths")?
                .split(separator: ",")
                .compactMap { Int($0.trimmingCharacters(in: .whitespaces)) }
            var payload: [String: JSONValue] = [
                "app": .string(name), "appearance": .string(appearance),
            ]
            if let widths { payload["widths"] = .array(widths.map { .int($0) }) }
            let result = try send("preview-matrix", payload)
            try collectMatrix(result, app: name)
            return
        }
        if arguments.flag("watch") {
            try watchLoop(app: name, appearance: appearance)
            return
        }
        _ = try send("sync")
        let result = try send("preview-app", [
            "app": .string(name), "appearance": .string(appearance),
        ])
        output.emit(result) { "Preview of \(name) is running from the connected checkout." }
    case "shell":
        let surface = arguments.positional.count > 2 ? arguments.positional[2] : "desktop"
        let result = try send("preview-shell", ["surface": .string(surface)])
        output.emit(result) { "Nested \(surface) shell preview started." }
    case "stop":
        let result = try send("preview-stop")
        output.emit(result) { "Stopped every emulator-created preview." }
    default:
        throw EmulatorError.usage("preview takes 'app', 'shell', or 'stop'")
    }
}

func collectMatrix(_ result: JSONValue, app: String) throws {
    let directory = arguments.value("output-directory")
        ?? FileManager.default.currentDirectoryPath + "/luma-emulator-matrix"
    try FileManager.default.createDirectory(
        atPath: directory, withIntermediateDirectories: true
    )
    var collected: [JSONValue] = []
    for capture in result.objectValue?["captures"]?.arrayValue ?? [] {
        guard let guestPath = capture.objectValue?["guestPath"]?.stringValue,
              let width = capture.objectValue?["width"]?.intValue else { continue }
        let local = "\(directory)/\(app)-\(width).png"
        _ = try send("exec", ["command": .string("test -s '\(guestPath)'")])
        let fetched = try send("fetch", [
            "remote": .string(guestPath), "local": .string(local),
        ])
        _ = fetched
        collected.append(.object(["width": .int(width), "output": .string(local)]))
    }
    output.emit(.object([
        "app": .string(app),
        "outputDirectory": .string(directory),
        "captures": .array(collected),
        "sameBinary": .bool(true),
    ])) {
        "Captured \(collected.count) widths of \(app) into \(directory) from one binary."
    }
}

/// Watch mode is a poll rather than an FSEvents subscription: it is trivially
/// portable, it cannot miss a change made by a tool that replaces files, and at
/// this scale it costs nothing measurable.
func watchLoop(app: String, appearance: String) throws {
    let link = try requireProject()
    let roots = [
        URL(fileURLWithPath: link.path).appendingPathComponent("src/prairie-core"),
        URL(fileURLWithPath: link.path).appendingPathComponent("src/luma-platform/appkit"),
    ]
    func fingerprint() -> String {
        var parts: [String] = []
        for root in roots {
            guard let walker = FileManager.default.enumerator(
                at: root, includingPropertiesForKeys: [.contentModificationDateKey, .fileSizeKey]
            ) else { continue }
            for case let url as URL in walker {
                let name = url.lastPathComponent
                if name == "__pycache__" || name.hasSuffix(".pyc") { continue }
                let values = try? url.resourceValues(
                    forKeys: [.contentModificationDateKey, .fileSizeKey]
                )
                let stamp = values?.contentModificationDate?.timeIntervalSince1970 ?? 0
                parts.append("\(url.path):\(stamp):\(values?.fileSize ?? 0)")
            }
        }
        return parts.sorted().joined(separator: "|")
    }

    output.note("Watching \(link.path) for changes. Press Control-C to stop.")
    var previous = ""
    while true {
        let current = fingerprint()
        if current != previous {
            previous = current
            let started = Date()
            do {
                _ = try send("sync")
                _ = try send("preview-app", [
                    "app": .string(app), "appearance": .string(appearance),
                ])
                let elapsed = Date().timeIntervalSince(started)
                output.note(String(
                    format: "%@  %@ refreshed in %.2fs",
                    ISO8601DateFormatter().string(from: Date()), app, elapsed
                ))
            } catch let error as EmulatorError {
                output.note("refresh failed: \(error.message)")
            }
        }
        usleep(600_000)
    }
}

func commandSnapshot() throws {
    let action = try arguments.requirePositional(1, "'create', 'restore', or 'list'")
    let name = machineName()
    let machineDirectory = paths.machine(name)
    let snapshots = machineDirectory.appendingPathComponent("snapshots", isDirectory: true)
    let disk = machineDirectory.appendingPathComponent("disk.raw")

    switch action {
    case "list":
        let entries = (try? FileManager.default.contentsOfDirectory(atPath: snapshots.path)) ?? []
        let names = entries.filter { $0.hasSuffix(".raw") }.map { String($0.dropLast(4)) }.sorted()
        output.emit(.object([
            "machine": .string(name), "snapshots": .strings(names),
        ])) { names.isEmpty ? "No snapshots for \(name)." : names.joined(separator: "\n") }
    case "create":
        let label = try arguments.requirePositional(2, "a snapshot name")
        guard !client.isListening() else {
            throw EmulatorError.conflict(
                "stop the guest before snapshotting; a snapshot of a running disk is not consistent"
            )
        }
        try FileManager.default.createDirectory(at: snapshots, withIntermediateDirectories: true)
        let target = snapshots.appendingPathComponent("\(label).raw")
        try paths.assertOwned(target)
        try bootstrap.cloneDisk(from: disk, to: target)
        output.emit(.object([
            "machine": .string(name), "snapshot": .string(label), "path": .string(target.path),
        ])) { "Snapshot '\(label)' created (copy-on-write, no extra disk used yet)." }
    case "restore":
        let label = try arguments.requirePositional(2, "a snapshot name")
        guard !client.isListening() else {
            throw EmulatorError.conflict("stop the guest before restoring a snapshot")
        }
        let source = snapshots.appendingPathComponent("\(label).raw")
        guard FileManager.default.fileExists(atPath: source.path) else {
            throw EmulatorError.usage("no snapshot called '\(label)' for machine '\(name)'")
        }
        try paths.assertOwned(disk)
        try bootstrap.cloneDisk(from: source, to: disk)
        output.emit(.object([
            "machine": .string(name), "restored": .string(label),
        ])) { "Machine '\(name)' restored to snapshot '\(label)'." }
    default:
        throw EmulatorError.usage("snapshot takes 'create', 'restore', or 'list'")
    }
}

func commandReset() throws {
    let name = machineName()
    let machineDirectory = paths.machine(name)
    guard FileManager.default.fileExists(atPath: machineDirectory.path) else {
        throw EmulatorError.usage("there is no machine called '\(name)' to reset")
    }
    try paths.assertOwned(machineDirectory)
    guard !client.isListening() else {
        throw EmulatorError.conflict("stop the guest before resetting its overlay")
    }
    if !arguments.flag("yes") {
        FileHandle.standardError.write(Data("""
        This replaces the '\(name)' overlay at:
          \(machineDirectory.path)
        Everything inside that guest — its user data, its snapshots — is destroyed.
        The factory image, the other machines, and your Project Luma checkout are untouched.
        Re-run with --yes to proceed.

        """.utf8))
        exit(ExitCode.usage.rawValue)
    }
    try FileManager.default.removeItem(at: machineDirectory)
    output.emit(.object([
        "machine": .string(name),
        "removed": .string(machineDirectory.path),
        "factoryImageTouched": .bool(false),
        "projectTouched": .bool(false),
    ])) { "Removed the '\(name)' overlay. The factory image and your checkout are unchanged." }
}

// MARK: - Dispatch

do {
    let command = arguments.positional.first ?? "help"
    switch command {
    case "help", "--help", "-h":
        print(usageText)
    case "version":
        output.emit(.object([
            "version": .string(EmulatorVersion.current),
            "backend": .string("apple-virtualization-framework"),
            "guestArchitecture": .string("aarch64"),
        ])) { EmulatorVersion.current }
    case "doctor":
        commandDoctor()
    case "bootstrap":
        try commandBootstrap()
    case "provision":
        try commandProvision()
    case "bundle":
        try commandBundle()
    case "start":
        try commandStart()
    case "stop":
        let result = try send("stop", ["force": .bool(arguments.flag("force"))])
        // The runtime exits with its guest, but not instantly. Returning before
        // it has released the control socket makes the next command — snapshot,
        // reset, start — believe a guest is still running.
        awaitRuntimeExit()
        output.emit(result) { "Guest stopped." }
    case "restart":
        _ = try? send("stop", ["force": .bool(true)])
        awaitRuntimeExit()
        try commandStart()
    case "pause":
        output.emit(try send("pause")) { "Guest paused." }
    case "resume":
        output.emit(try send("resume")) { "Guest resumed." }
    case "status":
        let status = try send("status")
        output.emit(status) {
            let object = status.objectValue ?? [:]
            return """
            machine    \(object["machine"]?.stringValue ?? "-")
            mode       \(object["mode"]?.stringValue ?? "-")
            viewport   \(object["viewport"]?.stringValue ?? "-")
            health     \(object["health"]?.stringValue ?? "-")
            address    \(object["guestAddress"]?.stringValue ?? "-")
            backend    \(object["backend"]?.stringValue ?? "-") (\(object["guestArchitecture"]?.stringValue ?? "-"))
            project    \(object["project"]?.stringValue ?? "not connected")
            revision   \(object["projectRevision"]?.stringValue ?? "-")
            """
        }
    case "health":
        let result = try send("health")
        output.emit(result) { result.objectValue?["health"]?.stringValue ?? "unknown" }
    case "project":
        try commandProject()
    case "sync":
        let result = try send("sync")
        output.emit(result) {
            let seconds = result.objectValue?["elapsedSeconds"].flatMap { value -> Double? in
                if case .double(let number) = value { return number }
                if case .int(let number) = value { return Double(number) }
                return nil
            } ?? 0
            return String(format: "Synchronized the connected checkout in %.2fs.", seconds)
        }
    case "preview":
        try commandPreview()
    case "scenario":
        let name = arguments.positional.count > 1 ? arguments.positional[1] : "list"
        let result = try send("scenario", ["name": .string(name)])
        output.emit(result) {
            if name == "list" {
                let rows = result.objectValue?["scenarios"]?.arrayValue ?? []
                return rows.map { row in
                    let object = row.objectValue ?? [:]
                    let supported = object["supported"]?.boolValue ?? false
                    let mark = supported ? "  " : "! "
                    let gap = object["gap"]?.stringValue.map { " — \($0)" } ?? ""
                    return "\(mark)\(object["id"]?.stringValue ?? "?"): \(object["summary"]?.stringValue ?? "")\(gap)"
                }.joined(separator: "\n")
            }
            return "Applied '\(name)'. This is a simulated state, not physical-device evidence."
        }
    case "screenshot":
        guard let out = arguments.value("output") else {
            throw EmulatorError.usage("screenshot needs --output <path>")
        }
        let result = try send("screenshot", ["output": .string(out)])
        output.emit(result) { "Wrote \(out) straight from the guest framebuffer." }
    case "window":
        let result = try send("window", [:])
        output.emit(result) {
            let object = result.objectValue ?? [:]
            guard object["present"]?.boolValue == true else {
                return "No Mac window: \(object["reason"]?.stringValue ?? "unknown")"
            }
            return object.keys.sorted().map { key in
                "  \(key.padding(toLength: max(32, key.count), withPad: " ", startingAt: 0)) "
                    + (object[key].map { value -> String in
                        switch value {
                        case .bool(let flag): return flag ? "yes" : "no"
                        case .int(let number): return "\(number)"
                        case .double(let number): return "\(number)"
                        case .string(let text): return text
                        default: return "-"
                        }
                    } ?? "-")
            }.joined(separator: "\n")
        }
    case "verify":
        let result = try send("verify", [:])
        output.emit(result) {
            let object = result.objectValue ?? [:]
            var lines = [
                "luma completeness   \(object["lumaCompleteness"]?.stringValue ?? "-")",
                "packages expected   \(object["expectedPackageCount"]?.intValue ?? 0)",
                "installed as pinned \(object["installedAsExpected"]?.intValue ?? 0)",
                "figtree resolves    \(object["figtreeResolves"]?.stringValue ?? "-")",
                "",
            ]
            for entry in object["runningProcesses"]?.arrayValue ?? [] {
                lines.append("  " + (entry.stringValue ?? ""))
            }
            let wrong = object["notInstalledAsExpected"]?.arrayValue ?? []
            if !wrong.isEmpty {
                lines.append("")
                lines.append("  NOT AS PINNED:")
                for entry in wrong { lines.append("    " + (entry.stringValue ?? "")) }
            }
            return lines.joined(separator: "\n")
        }
        if !(result.objectValue?["ok"]?.boolValue ?? false) {
            exit(ExitCode.guestUnhealthy.rawValue)
        }
    case "inspect":
        let what = arguments.positional.count > 1 ? arguments.positional[1] : "accessibility"
        guard what == "accessibility" else {
            throw EmulatorError.usage("inspect currently supports 'accessibility'")
        }
        output.emit(try send("inspect-accessibility")) { "Accessibility tree captured." }
    case "logs":
        let result = try send("logs", [
            "component": .string(arguments.value("component", default: "boot")),
            "lines": .int(arguments.integer("lines") ?? 200),
        ])
        output.emit(result) { result.objectValue?["text"]?.stringValue ?? "" }
    case "exec":
        let joined = arguments.trailing.joined(separator: " ")
        guard !joined.isEmpty else {
            throw EmulatorError.usage("exec needs a command after --")
        }
        let result = try send("exec", [
            "command": .string(joined),
            "session": .bool(arguments.flag("session")),
            "timeout": .int(arguments.integer("timeout") ?? 120),
        ])
        let object = result.objectValue ?? [:]
        if output.json {
            print(ControlCodec.pretty(result))
        } else {
            FileHandle.standardOutput.write(Data((object["stdout"]?.stringValue ?? "").utf8))
            FileHandle.standardError.write(Data((object["stderr"]?.stringValue ?? "").utf8))
        }
        exit(Int32(object["status"]?.intValue ?? 0))
    case "push":
        // Copies a local file or directory into a guest directory. Useful for
        // iterating on a guest-side helper without rebuilding a factory image,
        // and for an agent that needs to place a fixture.
        let local = try arguments.requirePositional(1, "a local file or directory")
        let remote = try arguments.requirePositional(2, "a guest directory")
        let result = try send("push-file", [
            "local": .string(URL(fileURLWithPath: local).standardizedFileURL.path),
            "remoteDirectory": .string(remote),
        ])
        output.emit(result) { "Copied \(local) into \(remote)." }
    case "snapshot":
        try commandSnapshot()
    case "reset":
        try commandReset()
    default:
        throw EmulatorError.usage("unknown command '\(command)'. Run `luma-emulator help`.")
    }
} catch let error as EmulatorError {
    output.fail(error)
} catch {
    output.fail(EmulatorError.failure("internal", "\(error)"))
}
