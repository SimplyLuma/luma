// SPDX-License-Identifier: Apache-2.0

import Foundation

/// The single control plane. The Mac application and the `luma-emulator`
/// command both speak this; neither owns a second state machine.
///
/// Framing is one JSON object per line, request then response, connection
/// closed. It is deliberately boring: an agent can drive it with `nc` if the
/// CLI is unavailable.
public struct ControlRequest: Codable, Sendable {
    public var command: String
    public var arguments: [String: JSONValue]

    public init(command: String, arguments: [String: JSONValue] = [:]) {
        self.command = command
        self.arguments = arguments
    }
}

public struct ControlResponse: Codable, Sendable {
    public var ok: Bool
    public var data: JSONValue?
    public var errorCode: String?
    public var errorMessage: String?
    public var exitCode: Int32

    public init(ok: Bool, data: JSONValue? = nil, errorCode: String? = nil,
                errorMessage: String? = nil, exitCode: Int32 = 0) {
        self.ok = ok
        self.data = data
        self.errorCode = errorCode
        self.errorMessage = errorMessage
        self.exitCode = exitCode
    }

    public static func success(_ data: JSONValue) -> ControlResponse {
        ControlResponse(ok: true, data: data, exitCode: 0)
    }

    public static func failure(_ error: EmulatorError) -> ControlResponse {
        ControlResponse(
            ok: false,
            errorCode: error.code,
            errorMessage: error.message,
            exitCode: error.exit.rawValue
        )
    }
}

/// A minimal JSON tree. Every status and query command answers with one of
/// these so `--json` output is machine-readable and free of ANSI formatting.
public enum JSONValue: Codable, Sendable, Equatable {
    case null
    case bool(Bool)
    case int(Int)
    case double(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Int.self) {
            self = .int(value)
        } else if let value = try? container.decode(Double.self) {
            self = .double(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container, debugDescription: "unsupported JSON value"
            )
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .null: try container.encodeNil()
        case .bool(let value): try container.encode(value)
        case .int(let value): try container.encode(value)
        case .double(let value): try container.encode(value)
        case .string(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        }
    }

    public var stringValue: String? {
        if case .string(let value) = self { return value }
        return nil
    }

    public var intValue: Int? {
        if case .int(let value) = self { return value }
        if case .string(let value) = self { return Int(value) }
        return nil
    }

    public var boolValue: Bool? {
        if case .bool(let value) = self { return value }
        if case .string(let value) = self { return value == "true" }
        return nil
    }

    public var objectValue: [String: JSONValue]? {
        if case .object(let value) = self { return value }
        return nil
    }

    public var arrayValue: [JSONValue]? {
        if case .array(let value) = self { return value }
        return nil
    }
}

public extension JSONValue {
    static func strings(_ values: [String]) -> JSONValue {
        .array(values.map { .string($0) })
    }
}

public enum ControlCodec {
    public static func encode<T: Encodable>(_ value: T) throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        var data = try encoder.encode(value)
        data.append(0x0A)
        return data
    }

    public static func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        try JSONDecoder().decode(type, from: data)
    }

    /// Human-readable JSON for `--json` output.
    public static func pretty(_ value: JSONValue) -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        guard let data = try? encoder.encode(value),
              let text = String(data: data, encoding: .utf8) else {
            return "{}"
        }
        return text
    }
}
