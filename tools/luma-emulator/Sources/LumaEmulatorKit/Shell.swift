// SPDX-License-Identifier: Apache-2.0

import Foundation

public struct CommandResult: Sendable {
    public let status: Int32
    public let standardOutput: String
    public let standardError: String

    public var succeeded: Bool { status == 0 }

    public var trimmedOutput: String {
        standardOutput.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

public enum Shell {
    /// Runs a command with an explicit argument vector. There is no shell
    /// interpolation anywhere in the emulator's own control paths, so a project
    /// path containing a space or a quote cannot become an injection.
    @discardableResult
    public static func run(
        _ executable: String,
        _ arguments: [String],
        environment: [String: String]? = nil,
        currentDirectory: URL? = nil,
        input: Data? = nil,
        timeout: TimeInterval? = nil
    ) throws -> CommandResult {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        if let environment {
            var merged = ProcessInfo.processInfo.environment
            for (key, value) in environment { merged[key] = value }
            process.environment = merged
        }
        if let currentDirectory { process.currentDirectoryURL = currentDirectory }

        let outPipe = Pipe()
        let errPipe = Pipe()
        process.standardOutput = outPipe
        process.standardError = errPipe
        if input != nil {
            process.standardInput = Pipe()
        }

        // Drain both pipes concurrently. A command that writes more than a pipe
        // buffer to stderr would otherwise deadlock against waitUntilExit.
        var outData = Data()
        var errData = Data()
        let lock = NSLock()
        let group = DispatchGroup()
        for (pipe, isOut) in [(outPipe, true), (errPipe, false)] {
            group.enter()
            DispatchQueue.global().async {
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                lock.lock()
                if isOut { outData = data } else { errData = data }
                lock.unlock()
                group.leave()
            }
        }

        try process.run()

        if let input, let handle = (process.standardInput as? Pipe)?.fileHandleForWriting {
            handle.write(input)
            try? handle.close()
        }

        if let timeout {
            let deadline = Date().addingTimeInterval(timeout)
            while process.isRunning && Date() < deadline {
                usleep(20_000)
            }
            if process.isRunning {
                process.terminate()
                usleep(200_000)
                if process.isRunning { kill(process.processIdentifier, SIGKILL) }
                _ = group.wait(timeout: .now() + 5)
                throw EmulatorError.timedOut(
                    "\(URL(fileURLWithPath: executable).lastPathComponent) timed out after \(Int(timeout))s"
                )
            }
        }

        process.waitUntilExit()
        // Bounded: if some descendant inherited the pipe and outlived the
        // command, report what was read rather than blocking the caller forever.
        _ = group.wait(timeout: .now() + 10)

        return CommandResult(
            status: process.terminationStatus,
            standardOutput: String(data: outData, encoding: .utf8) ?? "",
            standardError: String(data: errData, encoding: .utf8) ?? ""
        )
    }

    /// Size in bytes, or zero when the file is absent. Used wherever a report
    /// needs a number rather than an optional chain.
    public static func fileSize(_ path: String) -> Int64 {
        let attributes = try? FileManager.default.attributesOfItem(atPath: path)
        return (attributes?[.size] as? NSNumber)?.int64Value ?? 0
    }

    public static func which(_ name: String) -> String? {
        let candidates = [
            "/usr/bin/\(name)", "/bin/\(name)", "/usr/sbin/\(name)",
            "/opt/homebrew/bin/\(name)", "/usr/local/bin/\(name)",
        ]
        for candidate in candidates where FileManager.default.isExecutableFile(atPath: candidate) {
            return candidate
        }
        return nil
    }
}
