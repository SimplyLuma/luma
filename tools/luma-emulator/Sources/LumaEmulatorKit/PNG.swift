// SPDX-License-Identifier: Apache-2.0

import Foundation

/// Just enough PNG to answer "how big is this, and is it really a PNG?".
///
/// A capture is only reported as successful when the file it produced actually
/// decodes, so every screenshot result can state real pixel dimensions instead
/// of the ones that were requested.
public enum PNG {
    static let signature: [UInt8] = [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]

    public static func dimensions(of url: URL) -> (width: Int, height: Int)? {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return nil }
        defer { try? handle.close() }
        guard let header = try? handle.read(upToCount: 33), header.count >= 33 else { return nil }
        let bytes = [UInt8](header)
        guard Array(bytes[0..<8]) == signature else { return nil }
        // Bytes 12..16 are the chunk type, which must be IHDR for a valid file.
        guard Array(bytes[12..<16]) == Array("IHDR".utf8) else { return nil }
        func integer(_ range: Range<Int>) -> Int {
            bytes[range].reduce(0) { ($0 << 8) | Int($1) }
        }
        let width = integer(16..<20)
        let height = integer(20..<24)
        guard width > 0, height > 0 else { return nil }
        return (width, height)
    }

    public static func isValid(_ url: URL) -> Bool {
        dimensions(of: url) != nil
    }
}
