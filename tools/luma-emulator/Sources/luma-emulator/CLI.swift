// SPDX-License-Identifier: Apache-2.0

import Foundation
import LumaEmulatorKit

/// A hand-rolled argument reader.
///
/// The emulator has no third-party Swift dependencies on purpose: a public
/// build must not need Homebrew or a package fetch, and a control surface a
/// coding agent depends on should not break because someone else's parser
/// changed its flag semantics.
struct Arguments {
    private(set) var positional: [String] = []
    private var flags: [String: String] = [:]
    private(set) var trailing: [String] = []

    init(_ raw: [String]) {
        var index = 0
        var seenSeparator = false
        while index < raw.count {
            let item = raw[index]
            if seenSeparator {
                trailing.append(item)
            } else if item == "--" {
                seenSeparator = true
            } else if item.hasPrefix("--") {
                let name = String(item.dropFirst(2))
                if let equals = name.firstIndex(of: "=") {
                    flags[String(name[name.startIndex..<equals])] =
                        String(name[name.index(after: equals)...])
                } else if index + 1 < raw.count, !raw[index + 1].hasPrefix("--") {
                    index += 1
                    flags[name] = raw[index]
                } else {
                    flags[name] = "true"
                }
            } else {
                positional.append(item)
            }
            index += 1
        }
    }

    func flag(_ name: String) -> Bool {
        flags[name] == "true"
    }

    func value(_ name: String) -> String? {
        flags[name]
    }

    func value(_ name: String, default fallback: String) -> String {
        flags[name] ?? fallback
    }

    func integer(_ name: String) -> Int? {
        flags[name].flatMap(Int.init)
    }

    func requirePositional(_ index: Int, _ description: String) throws -> String {
        guard index < positional.count else {
            throw EmulatorError.usage("expected \(description)")
        }
        return positional[index]
    }
}

/// Output discipline: in `--json` mode nothing but JSON reaches stdout, and no
/// ANSI escape ever does. Human mode gets plain text, still without colour, so
/// piping into a log stays readable.
struct Output {
    let json: Bool

    func emit(_ value: JSONValue, human: () -> String) {
        if json {
            print(ControlCodec.pretty(value))
        } else {
            let text = human()
            if !text.isEmpty { print(text) }
        }
    }

    func note(_ text: String) {
        guard !json else { return }
        print(text)
        // Flushed explicitly. Standard output is block-buffered when it is a
        // pipe or a file, so a developer running `preview app … --watch | tee`
        // or redirecting to a log would otherwise see nothing at all for
        // minutes while the loop is working perfectly.
        fflush(stdout)
    }

    func fail(_ error: EmulatorError) -> Never {
        if json {
            let payload = JSONValue.object([
                "ok": .bool(false),
                "error": .object([
                    "code": .string(error.code),
                    "message": .string(error.message),
                ]),
            ])
            print(ControlCodec.pretty(payload))
        } else {
            FileHandle.standardError.write(Data("luma-emulator: \(error.message)\n".utf8))
        }
        exit(error.exit.rawValue)
    }
}

let usageText = """
luma-emulator \(EmulatorVersion.current) — run real Project Luma software on Apple Silicon.

  doctor                          Check this Mac and report what is missing
  bootstrap [--force]             Build the pinned Fedora/Luma factory image
  start desktop|mobile [options]  Start the guest (--viewport, --headless, --cpu, --memory)
  stop [--force]                  Shut the guest down (ACPI unless --force)
  restart [desktop|mobile]        Stop and start again
  pause | resume                  Suspend or resume the running guest
  status                          Full machine, image and project state
  health                          One word: stopped|booting|ready|previewing|failed
  project connect <path>          Point the emulator at a Project Luma checkout
  project show                    Show the connected checkout and its revision
  sync                            One-way changed-file sync of the checkout into the guest
  preview app <name> [--matrix] [--watch] [--appearance light|dark]
  preview shell desktop|handheld
  preview stop                    Stop every emulator-created preview unit
  scenario <name>|list            Drive a deterministic development scenario
  screenshot --output <path>      Capture the guest framebuffer
  inspect accessibility           Dump the guest accessibility tree
  logs --component boot|shell|preview|session|system
  exec -- <command>               Run an unprivileged command in the guest
  snapshot create|restore|list <name>
  reset [--machine <name>] [--yes] Replace one emulator overlay
  version

Global: --json  --timeout <seconds>  --machine <name>
Applications: \(["notes", "messages", "phone", "contacts", "calendar", "tasks", "photos", "weather", "clock", "camera", "voice-memos"].joined(separator: ", "))
"""
