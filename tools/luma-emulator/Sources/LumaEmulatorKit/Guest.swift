// SPDX-License-Identifier: Apache-2.0

import Foundation

/// Everything the host does inside the guest goes through here.
///
/// The channel is OpenSSH to an unprivileged account, authenticated by a key
/// generated per installation. There is no broad unauthenticated API: an agent
/// that can run `luma-emulator exec` already has the user's own shell access on
/// this Mac, and nothing more is granted than that.
public struct Guest: Sendable {
    public static let user = "luma"
    private static let masterLock = NSLock()

    public let address: String
    public let keyPath: String
    public let knownHostsPath: String
    public let controlPath: String

    public init(address: String, keyPath: String, knownHostsPath: String, controlPath: String) {
        self.address = address
        self.keyPath = keyPath
        self.knownHostsPath = knownHostsPath
        self.controlPath = controlPath
    }

    /// Options shared by every invocation. `ControlPath` names one multiplexed
    /// connection; whether a given call creates it or reuses it is decided by
    /// the explicit `ControlMaster` value each caller passes.
    private var baseOptions: [String] {
        [
            "-i", keyPath,
            "-o", "BatchMode=yes",
            // A fresh guest gets a fresh host key at a recycled NAT address, so
            // the emulator keeps its own known_hosts rather than churning the
            // developer's, and never falls back to no checking at all.
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "UserKnownHostsFile=\(Self.quoted(knownHostsPath))",
            "-o", "ConnectTimeout=5",
            // Generous, deliberately. A guest in the middle of a large rpm
            // transaction can stall its ssh replies for a minute at a time, and
            // an aggressive keepalive tears down the connection — and the work
            // running on it — exactly when the guest is busiest.
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=60",
            "-o", "LogLevel=ERROR",
            "-o", "ControlPath=\(Self.quoted(controlPath))",
            "-o", "ControlPersist=120",
        ]
    }

    /// `ssh -o` values are parsed as configuration lines, so an unquoted space
    /// silently changes their meaning: the emulator's state directory is
    /// "Luma Emulator", which turns one `UserKnownHostsFile` into two file
    /// names and makes `ControlPath` a syntax error. Quoting is not cosmetic
    /// here — without it nothing connects at all.
    static func quoted(_ path: String) -> String {
        "\"" + path.replacingOccurrences(of: "\"", with: "\\\"") + "\""
    }

    public var sshOptions: [String] {
        // Never `ControlMaster=auto` here. A master started by an ordinary
        // command inherits that command's stdout and stderr and then outlives
        // it, so the pipe never reaches end-of-file and the caller waits for a
        // process that has already exited. The master is started separately,
        // detached, by `ensureControlMaster()`.
        baseOptions + ["-o", "ControlMaster=no"]
    }

    /// Starts the shared connection if it is not already up.
    ///
    /// One multiplexed connection carries every later command, which is what
    /// keeps the edit-to-preview loop fast and stops status polling from
    /// opening a new TCP connection and key exchange every time.
    public func ensureControlMaster() {
        guard let ssh = Shell.which("ssh") else { return }
        // Control requests are served concurrently, so without this two of them
        // can each decide there is no master and race to create one on the same
        // socket. The loser's connection is then refused mid-command.
        Self.masterLock.lock()
        defer { Self.masterLock.unlock() }
        if let check = try? Shell.run(
            ssh, baseOptions + ["-O", "check", "\(Self.user)@\(address)"], timeout: 8
        ), check.succeeded {
            return
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: ssh)
        // `-f` backgrounds the master once authenticated; its standard streams
        // go to the null device so it cannot hold anyone else's pipes open.
        //
        // `ControlMaster=yes` alone, with no `-M`: `-M` *increments* the setting,
        // so passing both asks for `ask` mode, where every multiplexed session
        // needs interactive confirmation — which BatchMode then refuses, and
        // every command silently falls back to its own full connection.
        process.arguments = baseOptions + [
            "-o", "ControlMaster=yes", "-N", "-f", "\(Self.user)@\(address)",
        ]
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        guard (try? process.run()) != nil else { return }
        process.waitUntilExit()
    }

    /// Closes the multiplexed connection. Used when the guest goes away, so a
    /// stale socket cannot make the next start look unreachable.
    public func disconnect() {
        guard let ssh = Shell.which("ssh") else { return }
        _ = try? Shell.run(
            ssh, baseOptions + ["-O", "exit", "\(Self.user)@\(address)"], timeout: 10
        )
        try? FileManager.default.removeItem(atPath: controlPath)
    }

    /// Runs a command in the guest. The command is passed as one argument to a
    /// login shell in the guest; it is never concatenated into a host shell.
    @discardableResult
    public func run(_ command: String, timeout: TimeInterval = 120) throws -> CommandResult {
        guard let ssh = Shell.which("ssh") else {
            throw EmulatorError.failure("no-ssh", "the OpenSSH client is missing from this Mac")
        }
        ensureControlMaster()
        return try Shell.run(
            ssh,
            sshOptions + ["\(Self.user)@\(address)", "--", command],
            timeout: timeout
        )
    }

    /// A session command carries the Wayland and D-Bus environment of the
    /// logged-in graphical session, which is what preview and scenario work
    /// needs. `1000` is the guest's only interactive user.
    @discardableResult
    public func runInSession(_ command: String, timeout: TimeInterval = 120) throws -> CommandResult {
        // The display name is read from the running compositor's own
        // environment. A guest can have several Wayland sockets at once — the
        // display manager's greeter and the user session both create one — and
        // picking the newest by modification time lands on the wrong one often
        // enough to make previews and output configuration fail mysteriously.
        let prelude = """
        export XDG_RUNTIME_DIR=/run/user/1000
        export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
        export XDG_SESSION_TYPE=wayland
        __luma_display=""
        for __luma_pid in $(pgrep -x gnome-shell 2>/dev/null) $(pgrep -x phoc 2>/dev/null); do
          __luma_display=$(sudo -n tr '\\0' '\\n' < "/proc/$__luma_pid/environ" 2>/dev/null \
            | sed -n 's/^WAYLAND_DISPLAY=//p' | head -1)
          [ -n "$__luma_display" ] && break
        done
        if [ -z "$__luma_display" ]; then
          __luma_display=$(basename "$(ls -1t /run/user/1000/wayland-[0-9] 2>/dev/null | head -1)" 2>/dev/null || echo wayland-0)
        fi
        export WAYLAND_DISPLAY="$__luma_display"

        """
        return try run(prelude + command, timeout: timeout)
    }

    public func isReachable(timeout: TimeInterval = 8) -> Bool {
        ensureControlMaster()
        guard let result = try? run("true", timeout: timeout) else { return false }
        return result.succeeded
    }

    /// One-way, changed-file synchronization. `--delete` is scoped to the
    /// destination subtree the emulator owns; the host checkout is opened
    /// read-only and can never be written back to from the guest.
    @discardableResult
    public func push(
        from source: URL,
        to destination: String,
        excludes: [String] = [],
        delete: Bool = true,
        timeout: TimeInterval = 300
    ) throws -> CommandResult {
        guard let rsync = Shell.which("rsync") else {
            throw EmulatorError.failure("no-rsync", "rsync is missing from this Mac")
        }
        guard let ssh = Shell.which("ssh") else {
            throw EmulatorError.failure("no-ssh", "the OpenSSH client is missing from this Mac")
        }
        var arguments = ["-rlpt", "--checksum"]
        if delete { arguments.append("--delete") }
        for exclude in excludes {
            arguments.append(contentsOf: ["--exclude", exclude])
        }
        // rsync re-splits `-e` with shell-like quoting, and these options now
        // carry both spaces and double quotes, so every word is single-quoted.
        let remoteShell = ([ssh] + sshOptions)
            .map { "'" + $0.replacingOccurrences(of: "'", with: "'\\''") + "'" }
            .joined(separator: " ")
        arguments.append(contentsOf: [
            "-e", remoteShell,
            source.path.hasSuffix("/") ? source.path : source.path + "/",
            "\(Self.user)@\(address):\(destination)/",
        ])
        return try Shell.run(rsync, arguments, timeout: timeout)
    }

    /// Copies one file into a guest directory, preserving its mode.
    @discardableResult
    public func pushFile(
        _ source: URL, to destination: String, timeout: TimeInterval = 120
    ) throws -> CommandResult {
        guard let rsync = Shell.which("rsync"), let ssh = Shell.which("ssh") else {
            throw EmulatorError.failure("no-rsync", "rsync or ssh is missing from this Mac")
        }
        ensureControlMaster()
        let remoteShell = ([ssh] + sshOptions)
            .map { "'" + $0.replacingOccurrences(of: "'", with: "'\\''") + "'" }
            .joined(separator: " ")
        return try Shell.run(rsync, [
            "-lpt", "--checksum", "-e", remoteShell,
            source.path, "\(Self.user)@\(address):\(destination)/",
        ], timeout: timeout)
    }

    public func fetch(remotePath: String, to local: URL, timeout: TimeInterval = 120) throws {
        guard let scp = Shell.which("scp") else {
            throw EmulatorError.failure("no-scp", "the OpenSSH scp client is missing from this Mac")
        }
        let result = try Shell.run(
            scp,
            sshOptions + ["\(Self.user)@\(address):\(remotePath)", local.path],
            timeout: timeout
        )
        guard result.succeeded else {
            throw EmulatorError.failure(
                "guest-fetch",
                "could not copy \(remotePath) out of the guest: \(result.standardError.trimmingCharacters(in: .whitespacesAndNewlines))"
            )
        }
    }
}

public enum GuestDiscovery {
    /// Finds the guest's address without asking the guest.
    ///
    /// The boot log is authoritative because cloud-init prints the address to
    /// the serial console before anything else needs it. The DHCP lease file is
    /// the fallback for a guest that has been running since before this host
    /// process started.
    public static func address(consoleLog: URL, macAddress: String) -> String? {
        if let fromConsole = addressFromConsole(consoleLog) { return fromConsole }
        return addressFromLeases(macAddress: macAddress)
    }

    public static func addressFromConsole(_ consoleLog: URL) -> String? {
        guard let handle = try? FileHandle(forReadingFrom: consoleLog) else { return nil }
        defer { try? handle.close() }
        // Only the tail matters, and a long-lived console log can be large.
        let size = (try? handle.seekToEnd()) ?? 0
        let window: UInt64 = 262_144
        try? handle.seek(toOffset: size > window ? size - window : 0)
        guard let data = try? handle.readToEnd() else { return nil }
        // A serial console carries terminal escape sequences and partial
        // multi-byte writes, so it is not reliably valid UTF-8. Decoding
        // strictly here silently loses the address the guest did announce, so
        // decode leniently and let the pattern match do the filtering.
        let text = String(decoding: data, as: UTF8.self)
        var found: String?
        for line in text.split(separator: "\n") {
            guard let range = line.range(of: "luma-emulator-address=") else { continue }
            let value = line[range.upperBound...]
                .prefix { $0 == "." || $0.isNumber }
            if isIPv4(String(value)) { found = String(value) }
        }
        return found
    }

    public static func addressFromLeases(macAddress: String) -> String? {
        let path = "/var/db/dhcpd_leases"
        guard let text = try? String(contentsOfFile: path, encoding: .utf8) else { return nil }
        let wanted = normalizeMAC(macAddress)
        var address: String?
        var candidate: String?
        for rawLine in text.split(separator: "\n") {
            let line = rawLine.trimmingCharacters(in: .whitespaces)
            if line == "{" { candidate = nil; address = nil; continue }
            if line.hasPrefix("ip_address=") {
                address = String(line.dropFirst("ip_address=".count))
            }
            if line.hasPrefix("hw_address=") {
                // Recorded as `1,52:54:0:1:2:3` — the leading type is not a MAC.
                let value = line.dropFirst("hw_address=".count)
                candidate = normalizeMAC(String(value.split(separator: ",").last ?? ""))
            }
            if line == "}" , candidate == wanted, let address { return address }
        }
        return nil
    }

    public static func normalizeMAC(_ value: String) -> String {
        value.split(separator: ":")
            .map { String(format: "%02x", UInt8($0, radix: 16) ?? 0) }
            .joined(separator: ":")
    }

    public static func isIPv4(_ value: String) -> Bool {
        let parts = value.split(separator: ".")
        guard parts.count == 4 else { return false }
        return parts.allSatisfy { part in
            guard let number = Int(part) else { return false }
            return number >= 0 && number <= 255
        }
    }
}
