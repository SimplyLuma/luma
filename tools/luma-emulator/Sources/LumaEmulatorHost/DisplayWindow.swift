// SPDX-License-Identifier: Apache-2.0

import AppKit
import Virtualization
import LumaEmulatorKit

/// The Mac-side presentation.
///
/// Both modes retain the configured guest viewport. Resizing the Mac window
/// scales its presentation; choosing a viewport explicitly changes the guest
/// layout and rendering budget.
final class DisplayWindowController: NSObject, NSWindowDelegate {
    private var window: NSWindow?
    private var view: VZVirtualMachineView?
    private let controller: HostController
    private var adaptiveDesktopDisplay = false
    private var requestedDisplayScale = 1

    init(controller: HostController) {
        self.controller = controller
    }

    func present(machine: VZVirtualMachine, spec: MachineSpec) {
        let view = VZVirtualMachineView()
        view.virtualMachine = machine
        view.capturesSystemKeys = true

        let isDesktop = spec.mode == .desktop
        // Apple's automatic mode follows the Mac's Retina backing scale,
        // overriding an explicit @1 viewport and multiplying software rendering.
        // Scale the host window while retaining the explicitly configured guest
        // viewport. Larger Mac windows must not multiply software-rendered pixels.
        view.automaticallyReconfiguresDisplay = false
        adaptiveDesktopDisplay = false
        requestedDisplayScale = spec.viewport.scale

        let size = NSSize(width: spec.viewport.width, height: spec.viewport.height)
        var style: NSWindow.StyleMask = [.titled, .closable, .miniaturizable]
        if isDesktop { style.insert(.resizable) }

        let window = NSWindow(
            contentRect: NSRect(origin: .zero, size: size),
            styleMask: style,
            backing: .buffered,
            defer: false
        )
        window.title = isDesktop
            ? "Luma Emulator — Desktop"
            : "Luma Emulator — Mobile \(spec.viewport.name)"
        window.contentView = view
        window.delegate = self
        window.center()
        window.setFrameAutosaveName("luma-emulator-\(spec.mode.rawValue)")
        if !isDesktop {
            window.contentAspectRatio = size
            window.contentMinSize = NSSize(
                width: size.width / 2, height: size.height / 2
            )
        }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        self.window = window
        self.view = view
        view.window?.makeFirstResponder(view)
    }


    /// Captures the guest display from the Mac side.
    ///
    /// This is the preferred desktop capture path because it needs nothing from
    /// the guest session: no portal, no permission, no screenshot service. What
    /// it returns is the guest's own framebuffer as the view is drawing it —
    /// the Mac window's title bar and shadow are not part of the view, so they
    /// are not in the image.
    ///
    /// It returns nil rather than a blank PNG when the view cannot be cached,
    /// which happens when the renderer draws through a surface `cacheDisplay`
    /// cannot read. Reporting a uniform grey rectangle as a successful
    /// screenshot would be worse than admitting the path is unavailable.
    func capture() -> (data: Data, pixelWidth: Int, pixelHeight: Int)? {
        guard Thread.isMainThread else {
            var result: (Data, Int, Int)?
            DispatchQueue.main.sync { result = self.capture() }
            return result
        }
        guard let view, view.bounds.width > 0, view.bounds.height > 0 else { return nil }
        guard let representation = view.bitmapImageRepForCachingDisplay(in: view.bounds) else {
            return nil
        }
        view.cacheDisplay(in: view.bounds, to: representation)
        guard isMeaningful(representation) else { return nil }
        guard let data = representation.representation(using: .png, properties: [:]) else {
            return nil
        }
        return (data, representation.pixelsWide, representation.pixelsHigh)
    }

    /// A cached Metal-backed view yields a single flat colour. Sampling a grid
    /// is enough to tell that apart from a real desktop.
    private func isMeaningful(_ representation: NSBitmapImageRep) -> Bool {
        let width = representation.pixelsWide
        let height = representation.pixelsHigh
        guard width > 8, height > 8 else { return false }
        var seen = Set<UInt32>()
        for row in stride(from: 2, to: height - 2, by: max(1, height / 16)) {
            for column in stride(from: 2, to: width - 2, by: max(1, width / 16)) {
                guard let colour = representation.colorAt(x: column, y: row) else { continue }
                let packed = (UInt32(colour.redComponent * 255) << 16)
                    | (UInt32(colour.greenComponent * 255) << 8)
                    | UInt32(colour.blueComponent * 255)
                seen.insert(packed)
                if seen.count > 3 { return true }
            }
        }
        return false
    }

    /// Everything about the Mac window that can be checked without a person
    /// looking at it. The parts that genuinely need eyes — sharpness, scroll
    /// feel, how full-screen behaves — are not claimed here.
    func describe() -> [String: Any] {
        guard Thread.isMainThread else {
            var result: [String: Any] = [:]
            DispatchQueue.main.sync { result = self.describe() }
            return result
        }
        guard let window else { return ["present": false] }
        let mask = window.styleMask
        let content = window.contentRect(forFrameRect: window.frame)
        var payload: [String: Any] = [
            "present": true,
            "title": window.title,
            "visible": window.isVisible,
            "resizable": mask.contains(.resizable),
            "closable": mask.contains(.closable),
            "miniaturizable": mask.contains(.miniaturizable),
            "fullScreen": window.styleMask.contains(.fullScreen),
            "contentWidth": Int(content.width),
            "contentHeight": Int(content.height),
            "backingScaleFactor": window.backingScaleFactor,
        ]
        let ratio = window.contentAspectRatio
        payload["aspectRatioLocked"] = ratio.width > 0 && ratio.height > 0
        if ratio.width > 0 {
            payload["aspectRatio"] = "\(Int(ratio.width))x\(Int(ratio.height))"
        }
        if let view {
            payload["viewWidth"] = Int(view.bounds.width)
            payload["viewHeight"] = Int(view.bounds.height)
            payload["requestedDisplayScale"] = requestedDisplayScale
            payload["adaptiveDesktopDisplay"] = adaptiveDesktopDisplay
            payload["automaticallyReconfiguresDisplay"] = view.automaticallyReconfiguresDisplay
            payload["capturesSystemKeys"] = view.capturesSystemKeys
            payload["isFirstResponder"] = (window.firstResponder === view)
        }
        return payload
    }

    var hasVisibleWindow: Bool {
        guard Thread.isMainThread else {
            var visible = false
            DispatchQueue.main.sync { visible = self.hasVisibleWindow }
            return visible
        }
        return window?.isVisible ?? false
    }

    /// Closing the window must not silently destroy the developer's guest, so
    /// it hides the presentation and leaves the runtime serving the CLI.
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        sender.orderOut(nil)
        return false
    }

    func show() {
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}
