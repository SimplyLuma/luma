// SPDX-License-Identifier: Apache-2.0

import Foundation
#if canImport(Darwin)
import Darwin
#endif

/// A deliberately small UNIX-domain socket pair.
///
/// The control plane is a filesystem socket with 0600 permissions rather than a
/// TCP port: it cannot be reached from the network at all, it inherits the
/// user's own access control, and it removes any question of binding beyond
/// loopback.
public enum UnixSocket {
    static func makeAddress(_ path: String) throws -> sockaddr_un {
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let maximum = MemoryLayout.size(ofValue: address.sun_path)
        guard path.utf8.count < maximum else {
            throw EmulatorError.failure(
                "socket-path-too-long",
                "control socket path exceeds \(maximum) bytes: \(path)"
            )
        }
        withUnsafeMutablePointer(to: &address.sun_path) { pointer in
            pointer.withMemoryRebound(to: CChar.self, capacity: maximum) { buffer in
                _ = strlcpy(buffer, path, maximum)
            }
        }
        return address
    }

    static func writeAll(_ descriptor: Int32, _ data: Data) throws {
        var offset = 0
        try data.withUnsafeBytes { raw in
            guard let base = raw.baseAddress else { return }
            while offset < data.count {
                let written = write(descriptor, base.advanced(by: offset), data.count - offset)
                if written <= 0 {
                    if errno == EINTR { continue }
                    throw EmulatorError.failure(
                        "control-write", "control socket write failed: \(String(cString: strerror(errno)))"
                    )
                }
                offset += written
            }
        }
    }

    static func readLine(_ descriptor: Int32, timeout: TimeInterval) throws -> Data {
        var accumulated = Data()
        var byte = [UInt8](repeating: 0, count: 4096)
        let deadline = Date().addingTimeInterval(timeout)
        while true {
            var poller = pollfd(fd: descriptor, events: Int16(POLLIN), revents: 0)
            let remaining = deadline.timeIntervalSinceNow
            if remaining <= 0 {
                throw EmulatorError.timedOut("timed out reading from the control socket")
            }
            let ready = poll(&poller, 1, Int32(remaining * 1000))
            if ready == 0 {
                throw EmulatorError.timedOut("timed out reading from the control socket")
            }
            if ready < 0 {
                if errno == EINTR { continue }
                throw EmulatorError.failure("control-poll", "control socket poll failed")
            }
            let count = read(descriptor, &byte, byte.count)
            if count == 0 { break }
            if count < 0 {
                if errno == EINTR { continue }
                throw EmulatorError.failure("control-read", "control socket read failed")
            }
            accumulated.append(contentsOf: byte[0..<count])
            if let newline = accumulated.firstIndex(of: 0x0A) {
                return accumulated[accumulated.startIndex..<newline]
            }
        }
        return accumulated
    }
}

public struct ControlClient {
    public let socketPath: String

    public init(socketPath: String) {
        self.socketPath = socketPath
    }

    public func isListening() -> Bool {
        (try? send(ControlRequest(command: "ping"), timeout: 2)) != nil
    }

    public func send(_ request: ControlRequest, timeout: TimeInterval = 120) throws -> ControlResponse {
        let descriptor = socket(AF_UNIX, SOCK_STREAM, 0)
        guard descriptor >= 0 else {
            throw EmulatorError.failure("control-socket", "could not create a control socket")
        }
        defer { close(descriptor) }

        var address = try UnixSocket.makeAddress(socketPath)
        let result = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { generic in
                connect(descriptor, generic, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard result == 0 else {
            throw EmulatorError.notRunning(
                "the Luma Emulator runtime is not running (no control socket at \(socketPath))"
            )
        }

        try UnixSocket.writeAll(descriptor, ControlCodec.encode(request))
        let line = try UnixSocket.readLine(descriptor, timeout: timeout)
        guard !line.isEmpty else {
            throw EmulatorError.failure("control-empty", "the runtime closed the control connection")
        }
        return try ControlCodec.decode(ControlResponse.self, from: line)
    }
}

/// Serves control requests on a UNIX socket: one connection, one request, one
/// response.
///
/// Connections are handled concurrently. A long request — waiting several
/// minutes for a guest to become ready, or provisioning an image — must not
/// make `status` and `health` hang behind it, which is exactly what a serial
/// handler would do. Machine operations still marshal onto the single queue
/// Virtualization.framework requires; the handler itself does not own that
/// serialization.
public final class ControlServer {
    public typealias Handler = (ControlRequest) -> ControlResponse

    private let socketPath: String
    private let queue: DispatchQueue
    private let workers = DispatchQueue(
        label: "org.projectluma.emulator.control.workers", attributes: .concurrent
    )
    private var listener: Int32 = -1
    private var source: DispatchSourceRead?
    private let handler: Handler

    public init(socketPath: String, queue: DispatchQueue, handler: @escaping Handler) {
        self.socketPath = socketPath
        self.queue = queue
        self.handler = handler
    }

    /// Refuses to steal a socket that another live runtime is serving; a stale
    /// socket from a crashed run is removed rather than left to block startup.
    public func start() throws {
        if FileManager.default.fileExists(atPath: socketPath) {
            if ControlClient(socketPath: socketPath).isListening() {
                throw EmulatorError.conflict(
                    "another Luma Emulator runtime already owns \(socketPath)"
                )
            }
            try? FileManager.default.removeItem(atPath: socketPath)
        }

        listener = socket(AF_UNIX, SOCK_STREAM, 0)
        guard listener >= 0 else {
            throw EmulatorError.failure("control-listen", "could not create the control socket")
        }
        var address = try UnixSocket.makeAddress(socketPath)
        let bound = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { generic in
                bind(listener, generic, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard bound == 0 else {
            throw EmulatorError.conflict(
                "could not bind the control socket at \(socketPath): \(String(cString: strerror(errno)))"
            )
        }
        chmod(socketPath, 0o600)
        guard listen(listener, 16) == 0 else {
            throw EmulatorError.failure("control-listen", "could not listen on the control socket")
        }

        let source = DispatchSource.makeReadSource(fileDescriptor: listener, queue: queue)
        source.setEventHandler { [weak self] in self?.accept() }
        source.resume()
        self.source = source
    }

    private func accept() {
        let connection = Darwin.accept(listener, nil, nil)
        guard connection >= 0 else { return }
        workers.async { [handler] in
            defer { close(connection) }
            guard let line = try? UnixSocket.readLine(connection, timeout: 30),
                  !line.isEmpty else { return }
            let response: ControlResponse
            if let request = try? ControlCodec.decode(ControlRequest.self, from: line) {
                response = handler(request)
            } else {
                response = .failure(.usage("malformed control request"))
            }
            if let data = try? ControlCodec.encode(response) {
                try? UnixSocket.writeAll(connection, data)
            }
        }
    }

    public func stop() {
        source?.cancel()
        if listener >= 0 { close(listener) }
        try? FileManager.default.removeItem(atPath: socketPath)
    }
}
