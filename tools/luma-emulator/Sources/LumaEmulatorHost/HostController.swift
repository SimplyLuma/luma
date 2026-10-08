// SPDX-License-Identifier: Apache-2.0

import Foundation
import LumaEmulatorKit

/// The one control layer. The Mac window and the `luma-emulator` command are
/// both clients of this object; it is the only thing that decides what the
/// emulator's state is.
final class HostController {
    let paths: EmulatorPaths
    let runtime: VMRuntime
    private var server: ControlServer?
    private var previewing = Set<String>()
    private let lock = NSLock()
    private var reachableUntil = Date.distantPast
    private var lastReachable = false
    private var helpersRefreshedFor: String?

    /// Supplied by the runtime when a Mac window exists. Returning nil means
    /// there is nothing on screen to capture, which is a fact rather than a
    /// failure — the guest-side paths are tried next.
    var hostCapture: (() -> (data: Data, pixelWidth: Int, pixelHeight: Int)?)?

    /// Supplied by the runtime when a Mac window exists, so window state can be
    /// checked from the command line instead of being described from source.
    var hostWindowState: (() -> [String: Any])?

    init(paths: EmulatorPaths, runtime: VMRuntime) {
        self.paths = paths
        self.runtime = runtime
    }

    var guest: Guest? {
        guard let address = runtime.guestAddress else { return nil }
        return Guest(
            address: address,
            keyPath: paths.guestKey.path,
            knownHostsPath: paths.run.appendingPathComponent("known_hosts").path,
            controlPath: paths.run.appendingPathComponent("ssh-control").path
        )
    }

    func startServing() throws {
        let server = ControlServer(
            socketPath: paths.controlSocket.path,
            queue: DispatchQueue(label: "org.projectluma.emulator.control")
        ) { [weak self] request in
            guard let self else { return .failure(.failure("gone", "the runtime is shutting down")) }
            do {
                return .success(try self.handle(request))
            } catch let error as EmulatorError {
                return .failure(error)
            } catch {
                return .failure(.failure("internal", "\(error)"))
            }
        }
        try server.start()
        self.server = server
        try "\(ProcessInfo.processInfo.processIdentifier)"
            .write(to: paths.hostPIDFile, atomically: true, encoding: .utf8)
    }

    func stopServing() {
        server?.stop()
        try? FileManager.default.removeItem(at: paths.hostPIDFile)
    }

    // MARK: - Dispatch

    func handle(_ request: ControlRequest) throws -> JSONValue {
        switch request.command {
        case "ping":
            return .object(["pong": .bool(true), "version": .string(EmulatorVersion.current)])
        case "status":
            return status()
        case "health":
            return .object(["health": .string(currentHealth().rawValue)])
        case "stop":
            return try stop(force: request.arguments["force"]?.boolValue ?? false)
        case "pause":
            return try synchronously("pause") { self.runtime.pause(completion: $0) }
        case "resume":
            return try synchronously("resume") { self.runtime.resume(completion: $0) }
        case "apply-mode":
            return try applyPresentationMode()
        case "await-ready":
            let seconds = request.arguments["timeout"]?.intValue ?? 240
            return try awaitReady(timeout: TimeInterval(seconds))
        case "exec":
            return try execute(request)
        case "sync":
            return try synchronizeSource(request)
        case "preview-app":
            return try previewApp(request)
        case "preview-matrix":
            return try previewMatrix(request)
        case "preview-shell":
            return try previewShell(request)
        case "preview-stop":
            return try stopPreviews()
        case "fetch":
            return try fetchFile(request)
        case "push-file":
            return try pushFile(request)
        case "screenshot":
            return try screenshot(request)
        case "scenario":
            return try scenario(request)
        case "window":
            guard let state = hostWindowState?() else {
                return .object(["present": .bool(false), "reason": .string("running headless")])
            }
            var payload: [String: JSONValue] = [:]
            for (key, value) in state {
                switch value {
                case let flag as Bool: payload[key] = .bool(flag)
                case let number as Int: payload[key] = .int(number)
                case let number as Double: payload[key] = .double(number)
                case let text as String: payload[key] = .string(text)
                default: payload[key] = .string("\(value)")
                }
            }
            payload["mode"] = .string(runtime.spec.mode.rawValue)
            payload["viewport"] = .string(runtime.spec.viewport.name)
            payload["viewportScale"] = .int(runtime.spec.viewport.scale)
            return .object(payload)
        case "verify":
            return try verifyGuest(request)
        case "inspect-accessibility":
            return try inspectAccessibility(request)
        case "logs":
            return try logs(request)
        case "viewport":
            return .object([
                "viewport": .string(runtime.spec.viewport.name),
                "scale": .int(runtime.spec.viewport.scale),
                "note": .string("Changing the viewport of a running guest requires a restart in this release."),
            ])
        default:
            throw EmulatorError.usage("unknown control command: \(request.command)")
        }
    }

    // MARK: - State

    /// Liveness is a real probe into the guest, but it is cached briefly.
    /// Callers ask often — the window, `status`, `health`, and the readiness
    /// wait all do — and an uncached probe per call is what makes a guest look
    /// unreachable rather than merely busy.
    private func guestIsLive() -> Bool {
        lock.lock()
        if Date() < reachableUntil {
            let cached = lastReachable
            lock.unlock()
            return cached
        }
        lock.unlock()
        let live = guest?.isReachable(timeout: 6) ?? false
        lock.lock()
        lastReachable = live
        reachableUntil = Date().addingTimeInterval(live ? 3 : 2)
        lock.unlock()
        return live
    }

    func currentHealth() -> Health {
        if runtime.machine == nil { return runtime.health == .failed ? .failed : .stopped }
        if runtime.runState != "running" { return .booting }
        guard guestIsLive() else { return .booting }
        lock.lock()
        let hasPreview = !previewing.isEmpty
        lock.unlock()
        return hasPreview ? .previewing : .ready
    }

    /// Reads the address from the boot log and the DHCP lease file only. It
    /// deliberately never opens an SSH connection: this runs on a timer, and a
    /// probe on a timer is how the guest's sshd gets overwhelmed.
    func refreshGuestAddress() {
        guard runtime.machine != nil else {
            if let guest { guest.disconnect() }
            runtime.setGuestAddress(nil)
            helpersRefreshedFor = nil
            lock.lock()
            lastReachable = false
            reachableUntil = .distantPast
            lock.unlock()
            return
        }
        if runtime.guestAddress != nil { return }
        let mac = GuestDiscovery.normalizeMAC(runtime.macAddress.string)
        if let found = GuestDiscovery.address(consoleLog: runtime.consoleLogURL, macAddress: mac) {
            runtime.setGuestAddress(found)
        }
    }

    func status() -> JSONValue {
        refreshGuestAddress()
        let health = currentHealth()
        var payload: [String: JSONValue] = [
            "machine": .string(runtime.spec.name),
            "mode": .string(runtime.spec.mode.rawValue),
            "viewport": .string(runtime.spec.viewport.name),
            "viewportScale": .int(runtime.spec.viewport.scale),
            "pixelWidth": .int(runtime.spec.viewport.pixelWidth),
            "pixelHeight": .int(runtime.spec.viewport.pixelHeight),
            "cpuCount": .int(runtime.spec.cpuCount),
            "memoryMiB": .int(runtime.spec.memoryMiB),
            "processState": .string(runtime.runState),
            "health": .string(health.rawValue),
            "guestAddress": runtime.guestAddress.map { .string($0) } ?? .null,
            "backend": .string("apple-virtualization-framework"),
            "guestArchitecture": .string("aarch64"),
            "emulatorVersion": .string(EmulatorVersion.current),
            "hostPID": .int(Int(ProcessInfo.processInfo.processIdentifier)),
            "controlSocket": .string(paths.controlSocket.path),
            "consoleLog": .string(runtime.consoleLogURL.path),
        ]
        if let startedAt = runtime.startedAt {
            payload["startedAt"] = .string(ISO8601DateFormatter().string(from: startedAt))
            payload["uptimeSeconds"] = .int(Int(Date().timeIntervalSince(startedAt)))
        }
        if let failure = runtime.lastFailure {
            payload["lastFailure"] = .string(failure)
        }
        lock.lock()
        payload["previews"] = .strings(previewing.sorted())
        lock.unlock()

        if let manifest = try? loadManifest() {
            payload["imageID"] = .string(manifest.imageID)
            payload["imageSourceCommit"] = .string(manifest.sourceCommit)
            payload["imageCreatedAt"] = .string(manifest.createdAt)
            payload["lumaPackages"] = .strings(manifest.lumaPackages)
        }
        if let project = try? loadProject() {
            payload["project"] = .string(project.path)
            payload["projectConnectedAt"] = .string(project.connectedAt)
            if let revision = projectRevision(project) {
                payload["projectRevision"] = .string(revision)
            }
        }
        return .object(payload)
    }

    func loadManifest() throws -> ImageManifest {
        let url = paths.image(runtime.spec.imageID).appendingPathComponent("manifest.json")
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(ImageManifest.self, from: data)
    }

    func loadProject() throws -> ProjectLink {
        let data = try Data(contentsOf: paths.projectLink)
        return try JSONDecoder().decode(ProjectLink.self, from: data)
    }

    func projectRevisionPublic(_ project: ProjectLink) -> String? {
        projectRevision(project)
    }

    private func projectRevision(_ project: ProjectLink) -> String? {
        guard let git = Shell.which("git") else { return nil }
        guard let result = try? Shell.run(
            git, ["-C", project.path, "rev-parse", "--short", "HEAD"], timeout: 15
        ), result.succeeded else { return nil }
        let head = result.trimmedOutput
        let dirty = (try? Shell.run(git, ["-C", project.path, "status", "--porcelain"], timeout: 30))?
            .trimmedOutput.isEmpty == false
        return dirty ? "\(head)-dirty" : head
    }

    // MARK: - Lifecycle helpers

    private func synchronously(
        _ label: String,
        _ operation: (@escaping (Result<Void, Error>) -> Void) -> Void
    ) throws -> JSONValue {
        let semaphore = DispatchSemaphore(value: 0)
        var failure: Error?
        operation { result in
            if case .failure(let error) = result { failure = error }
            semaphore.signal()
        }
        if semaphore.wait(timeout: .now() + 60) == .timedOut {
            throw EmulatorError.timedOut("\(label) did not complete within 60s")
        }
        if let failure {
            throw EmulatorError.failure(label, "\(failure)")
        }
        return .object(["ok": .bool(true), "action": .string(label)])
    }

    /// Shuts the guest down.
    ///
    /// The guest's own `systemctl poweroff` is preferred over the virtual power
    /// button: the button is an ACPI event that a desktop session can swallow
    /// into a confirmation dialog nobody is there to click, and a graceful
    /// shutdown is the whole point of a non-forced stop. The power button
    /// remains the fallback for a guest that is up but not answering ssh.
    func stop(force: Bool) throws -> JSONValue {
        if force {
            return try synchronously("force-stop") { self.runtime.forceStop(completion: $0) }
        }

        var method = "power-button"
        if let guest = try? requireGuest() {
            // systemd closes the connection as it goes down, so a failed ssh
            // here says nothing about whether the shutdown was accepted.
            _ = try? guest.run("sudo -n systemctl poweroff --no-block", timeout: 30)
            guest.disconnect()
            method = "systemctl-poweroff"
        } else {
            _ = try synchronously("stop") { self.runtime.requestStop(completion: $0) }
        }

        let deadline = Date().addingTimeInterval(150)
        while Date() < deadline {
            if runtime.machine == nil {
                return .object([
                    "ok": .bool(true), "action": .string("stop"),
                    "clean": .bool(true), "method": .string(method),
                ])
            }
            usleep(250_000)
        }
        throw EmulatorError.timedOut(
            "the guest did not shut down within 150s. Use `luma-emulator stop --force` to power it off."
        )
    }

    func awaitReady(timeout: TimeInterval) throws -> JSONValue {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            refreshGuestAddress()
            let health = currentHealth()
            if health == .ready || health == .previewing {
                // A guest is only "ready" once it is presenting the mode this
                // machine was started in; reporting ready before that would send
                // a caller to screenshot the wrong shell.
                let session = (try? applyPresentationMode()) ?? .null
                prepareSession()
                let display = applyHandheldViewport()
                return .object([
                    "session": session,
                    "display": display,
                    "health": .string(currentHealth().rawValue),
                    "guestAddress": runtime.guestAddress.map { .string($0) } ?? .null,
                    "waitedSeconds": .int(Int(timeout - deadline.timeIntervalSinceNow)),
                ])
            }
            if runtime.health == .failed {
                throw EmulatorError.unhealthy(runtime.lastFailure ?? "the guest failed to start")
            }
            sleep(1)
        }
        throw EmulatorError.timedOut("the guest was not ready within \(Int(timeout))s")
    }

    /// The guest-side helpers only need pushing once per guest. Doing it on
    /// every sync costs two extra round trips in the loop a developer runs most.
    func helpersNeedRefresh(for address: String) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        if helpersRefreshedFor == address { return false }
        helpersRefreshedFor = address
        return true
    }

    func markPreview(_ name: String, active: Bool) {
        lock.lock()
        if active { previewing.insert(name) } else { previewing.remove(name) }
        lock.unlock()
    }

    func requireGuest() throws -> Guest {
        refreshGuestAddress()
        guard let guest else {
            throw EmulatorError.notRunning("the guest has no address yet; it may still be booting")
        }
        guard guest.isReachable(timeout: 6) else {
            throw EmulatorError.unhealthy("the guest is not answering on \(guest.address)")
        }
        return guest
    }
}
