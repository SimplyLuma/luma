// SPDX-License-Identifier: Apache-2.0

import Foundation

/// Exit codes are part of the automation contract. A coding agent must be able
/// to branch on the reason a command failed without parsing prose.
public enum ExitCode: Int32 {
    case success = 0
    case failure = 1
    case usage = 2
    case notRunning = 3
    case unsupportedHost = 4
    case timedOut = 5
    case resourceConflict = 6
    case guestUnhealthy = 7
    case notProvisioned = 8
}

public struct EmulatorError: Error, CustomStringConvertible {
    public let code: String
    public let message: String
    public let exit: ExitCode

    public init(code: String, message: String, exit: ExitCode = .failure) {
        self.code = code
        self.message = message
        self.exit = exit
    }

    public var description: String { message }

    public static func unsafePath(_ message: String) -> EmulatorError {
        EmulatorError(code: "unsafe-path", message: message)
    }

    public static func usage(_ message: String) -> EmulatorError {
        EmulatorError(code: "usage", message: message, exit: .usage)
    }

    public static func notRunning(_ message: String) -> EmulatorError {
        EmulatorError(code: "not-running", message: message, exit: .notRunning)
    }

    public static func unsupportedHost(_ message: String) -> EmulatorError {
        EmulatorError(code: "unsupported-host", message: message, exit: .unsupportedHost)
    }

    public static func timedOut(_ message: String) -> EmulatorError {
        EmulatorError(code: "timed-out", message: message, exit: .timedOut)
    }

    public static func conflict(_ message: String) -> EmulatorError {
        EmulatorError(code: "resource-conflict", message: message, exit: .resourceConflict)
    }

    public static func unhealthy(_ message: String) -> EmulatorError {
        EmulatorError(code: "guest-unhealthy", message: message, exit: .guestUnhealthy)
    }

    public static func notProvisioned(_ message: String) -> EmulatorError {
        EmulatorError(code: "not-provisioned", message: message, exit: .notProvisioned)
    }

    public static func failure(_ code: String, _ message: String) -> EmulatorError {
        EmulatorError(code: code, message: message)
    }
}
