// SPDX-License-Identifier: Apache-2.0

import AppKit
import LumaEmulatorKit

/// Menu-bar actions are thin wrappers over the same control layer the CLI uses.
/// Nothing here reimplements lifecycle logic.
final class HostAppDelegate: NSObject, NSApplicationDelegate {
    private let controller: HostController
    private let runtime: VMRuntime
    private let windows: DisplayWindowController
    private var terminationRequested = false
    private var guestStopped = false

    init(controller: HostController, runtime: VMRuntime, windows: DisplayWindowController) {
        self.controller = controller
        self.runtime = runtime
        self.windows = windows
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        installMenu()
        runtime.queue.async {
            guard let machine = self.runtime.machine else { return }
            DispatchQueue.main.async {
                self.windows.present(machine: machine, spec: self.runtime.spec)
            }
        }
        NotificationCenter.default.addObserver(
            forName: .lumaEmulatorGuestStopped, object: nil, queue: nil
        ) { [weak self] _ in
            // Notification delivery is synchronous on the VM queue. AppKit's
            // terminateLater waits for that queue, so always release the post
            // before asking AppKit to enter its termination loop.
            DispatchQueue.main.async {
                guard let self else { return }
                self.guestStopped = true
                guard !self.terminationRequested else { return }
                NSApp.terminate(nil)
            }
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        terminationRequested = true
        controller.stopServing()
        if guestStopped { return .terminateNow }
        runtime.forceStop { _ in
            // AppKit may be inside its termination run loop while a main-queue
            // block is still active; schedule the reply on the run loop itself.
            RunLoop.main.perform(inModes: [.common]) {
                NSApp.reply(toApplicationShouldTerminate: true)
            }
        }
        return .terminateLater
    }

    private func installMenu() {
        let main = NSMenu()

        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(
            withTitle: "Luma Emulator \(EmulatorVersion.current)", action: nil, keyEquivalent: ""
        )
        appMenu.addItem(.separator())
        appMenu.addItem(
            withTitle: "Show Display", action: #selector(showDisplay), keyEquivalent: "0"
        ).target = self
        appMenu.addItem(
            withTitle: "Copy Connection Information",
            action: #selector(copyConnectionInformation),
            keyEquivalent: "c"
        ).target = self
        appMenu.addItem(
            withTitle: "Open Logs", action: #selector(openLogs), keyEquivalent: "l"
        ).target = self
        appMenu.addItem(.separator())
        appMenu.addItem(
            withTitle: "Pause Guest", action: #selector(pauseGuest), keyEquivalent: "p"
        ).target = self
        appMenu.addItem(
            withTitle: "Resume Guest", action: #selector(resumeGuest), keyEquivalent: "r"
        ).target = self
        appMenu.addItem(.separator())
        appMenu.addItem(
            withTitle: "Shut Down Guest and Quit", action: #selector(quit), keyEquivalent: "q"
        ).target = self
        appItem.submenu = appMenu
        main.addItem(appItem)

        let editItem = NSMenuItem()
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Cut", action: Selector(("cut:")), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: Selector(("copy:")), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: Selector(("paste:")), keyEquivalent: "v")
        editItem.submenu = editMenu
        main.addItem(editItem)

        NSApp.mainMenu = main
    }

    @objc private func showDisplay() { windows.show() }

    @objc private func openLogs() {
        NSWorkspace.shared.selectFile(
            runtime.consoleLogURL.path,
            inFileViewerRootedAtPath: runtime.machineDirectory.path
        )
    }

    @objc private func copyConnectionInformation() {
        let status = controller.status()
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(ControlCodec.pretty(status), forType: .string)
    }

    @objc private func pauseGuest() {
        _ = try? controller.handle(ControlRequest(command: "pause"))
    }

    @objc private func resumeGuest() {
        _ = try? controller.handle(ControlRequest(command: "resume"))
    }

    @objc private func quit() { NSApp.terminate(nil) }
}
