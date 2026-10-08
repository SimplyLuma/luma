// SPDX-License-Identifier: Apache-2.0

import Foundation
import LumaEmulatorKit

/// Guest-side work: source synchronization, the preview lanes, screenshots,
/// scenarios, and logs.
///
/// None of this installs over the factory system packages. Every preview runs
/// from a user-owned prefix with the style and import paths that Luma's own
/// source already supports through environment overrides, so the packaged
/// application stays available for comparison at all times.
extension HostController {
    /// Absolute, not `$HOME`-relative. Modern `scp` speaks SFTP, which performs
    /// no shell expansion, so a `$HOME` in a transfer path is taken literally.
    /// The emulator creates this account itself through its own cloud-init
    /// seed, so the home directory is a contract it owns rather than a guess
    /// about someone else's system.
    static let guestHome = "/home/\(Guest.user)"
    static let guestRoot = "\(guestHome)/.local/share/luma-emulator"
    static let guestSource = "\(guestRoot)/source"
    static let guestShots = "\(guestRoot)/screenshots"
    static let guestBin = "\(guestRoot)/bin"

    /// The application name to entry-point map is derived from the real
    /// `prairie-core` binaries, not from a list the emulator invents.
    static let applications: [String: String] = [
        "notes": "prairie-notes",
        "messages": "prairie-messages",
        "phone": "prairie-phone",
        "contacts": "prairie-contacts",
        "calendar": "prairie-calendar",
        "tasks": "prairie-tasks",
        "photos": "prairie-photos",
        "weather": "prairie-weather",
        "clock": "prairie-clock",
        "camera": "prairie-camera",
        "voice-memos": "prairie-voice-memos",
    ]

    func previewEnvironment(appearance: String) -> String {
        let source = Self.guestSource
        let appkit = appearance == "dark" ? "luma-appkit-dark.css" : "luma-appkit.css"
        // Double quotes throughout: every one of these paths is rooted at the
        // guest's $HOME, and single quotes would ship the literal string.
        return """
        export PYTHONPATH="\(source)/prairie-core:\(source)/luma-platform/appkit"
        export PYTHONDONTWRITEBYTECODE=1
        export PRAIRIE_STYLE_PATH="\(source)/prairie-core/style/prairie.css"
        export LUMA_PHONE_STYLE_PATH="\(source)/prairie-core/style/phone.css"
        export LUMA_MESSAGES_STYLE_PATH="\(source)/prairie-core/style/messages.css"
        export LUMA_CAMERA_STYLE_PATH="\(source)/prairie-core/style/camera.css"
        export LUMA_PHOTOS_STYLE_PATH="\(source)/prairie-core/style/photos.css"
        export LUMA_APPKIT_STYLE_PATH="\(source)/luma-platform/appkit/\(appkit)"
        export XDG_DATA_DIRS="$HOME/.local/share/luma-emulator/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
        # Locale-shaped scenarios (right-to-left, long translations) are armed
        # here rather than applied to a running process, because text direction
        # is fixed when the application starts.
        [ -f "\(Self.guestRoot)/scenario.env" ] && . "\(Self.guestRoot)/scenario.env"
        """
    }

    // MARK: - exec

    func execute(_ request: ControlRequest) throws -> JSONValue {
        guard let command = request.arguments["command"]?.stringValue, !command.isEmpty else {
            throw EmulatorError.usage("exec needs a command")
        }
        let timeout = TimeInterval(request.arguments["timeout"]?.intValue ?? 120)
        let inSession = request.arguments["session"]?.boolValue ?? false
        let guest = try requireGuest()
        let result = inSession
            ? try guest.runInSession(command, timeout: timeout)
            : try guest.run(command, timeout: timeout)
        return .object([
            "status": .int(Int(result.status)),
            "stdout": .string(result.standardOutput),
            "stderr": .string(result.standardError),
        ])
    }

    // MARK: - sync

    /// One-way, changed-file synchronization from the connected Mac checkout
    /// into a guest-local tree. The host repository is never written to, and
    /// build output never lands in the developer's source.
    func synchronizeSource(_ request: ControlRequest) throws -> JSONValue {
        let project = try loadProject()
        let guest = try requireGuest()
        let root = URL(fileURLWithPath: project.path)

        let subtrees = [
            ("src/prairie-core", "prairie-core"),
            ("src/luma-platform", "luma-platform"),
        ]
        let excludes = [
            "__pycache__", "*.pyc", ".git", "build", "*.png", "*.qcow2", "*.raw",
        ]

        _ = try guest.run(
            "mkdir -p \"\(Self.guestSource)\" \"\(Self.guestShots)\" \"\(Self.guestBin)\""
        )
        try refreshGuestHelpers(guest)

        let started = Date()
        var synchronized: [JSONValue] = []
        for (hostRelative, guestRelative) in subtrees {
            let source = root.appendingPathComponent(hostRelative)
            guard FileManager.default.fileExists(atPath: source.path) else {
                throw EmulatorError.failure(
                    "missing-source",
                    "the connected project has no \(hostRelative). Is \(project.path) a Project Luma checkout?"
                )
            }
            _ = try guest.run("mkdir -p \"\(Self.guestSource)/\(guestRelative)\"")
            let result = try guest.push(
                from: source,
                to: "\(Self.guestSource)/\(guestRelative)",
                excludes: excludes
            )
            guard result.succeeded else {
                throw EmulatorError.failure(
                    "sync-failed",
                    "rsync of \(hostRelative) failed: \(result.standardError)"
                )
            }
            synchronized.append(.object([
                "hostPath": .string(hostRelative),
                "guestPath": .string("\(Self.guestSource)/\(guestRelative)"),
            ]))
        }

        let elapsed = Date().timeIntervalSince(started)
        return .object([
            "project": .string(project.path),
            "subtrees": .array(synchronized),
            "elapsedSeconds": .double((elapsed * 1000).rounded() / 1000),
        ])
    }

    /// Copies the emulator's own guest-side helpers into a user-owned prefix.
    ///
    /// They are versioned with the emulator, not with the factory image, so a
    /// guest built before a helper was fixed still runs the current one — and
    /// no `sudo` is needed to put them there.
    func refreshGuestHelpers(_ guest: Guest, force: Bool = false) throws {
        guard force || helpersNeedRefresh(for: guest.address) else { return }
        guard let recipe = try? EmulatorResources.provisionDirectory() else { return }
        for helper in [
            "luma-emulator-capture", "luma-emulator-a11y.py", "luma-emulator-portal-shot.py",
        ] {
            let source = recipe.appendingPathComponent(helper)
            guard FileManager.default.fileExists(atPath: source.path) else { continue }
            _ = try guest.pushFile(source, to: Self.guestBin)
        }
        _ = try guest.run("chmod 0755 \"\(Self.guestBin)\"/* 2>/dev/null || true")
    }

    // MARK: - preview app

    func previewApp(_ request: ControlRequest) throws -> JSONValue {
        guard let name = request.arguments["app"]?.stringValue,
              let binary = Self.applications[name] else {
            throw EmulatorError.usage(
                "unknown application. Known: \(Self.applications.keys.sorted().joined(separator: ", "))"
            )
        }
        let appearance = request.arguments["appearance"]?.stringValue ?? "light"
        let guest = try requireGuest()
        let unit = "luma-preview-\(name)"
        let entry = "\(Self.guestSource)/prairie-core/bin/\(binary)"

        _ = try guest.runInSession("systemctl --user reset-failed \(unit) 2>/dev/null; systemctl --user stop \(unit) 2>/dev/null; true")

        let launch = """
        \(previewEnvironment(appearance: appearance))
        test -f "\(entry)" || { echo "preview source is missing; run 'luma-emulator sync' first" >&2; exit 8; }
        systemd-run --user --unit=\(unit) --collect --quiet \\
          --setenv=PYTHONPATH="$PYTHONPATH" \\
          --setenv=PYTHONDONTWRITEBYTECODE=1 \\
          --setenv=PRAIRIE_STYLE_PATH="$PRAIRIE_STYLE_PATH" \\
          --setenv=LUMA_PHONE_STYLE_PATH="$LUMA_PHONE_STYLE_PATH" \\
          --setenv=LUMA_MESSAGES_STYLE_PATH="$LUMA_MESSAGES_STYLE_PATH" \\
          --setenv=LUMA_CAMERA_STYLE_PATH="$LUMA_CAMERA_STYLE_PATH" \\
          --setenv=LUMA_PHOTOS_STYLE_PATH="$LUMA_PHOTOS_STYLE_PATH" \\
          --setenv=LUMA_APPKIT_STYLE_PATH="$LUMA_APPKIT_STYLE_PATH" \\
          --setenv=WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \\
          --setenv=XDG_RUNTIME_DIR=/run/user/1000 \\
          --setenv=GDK_BACKEND=wayland \\
          /usr/bin/python3 "\(entry)"
        echo launched
        """
        let result = try guest.runInSession(launch, timeout: 90)
        guard result.succeeded else {
            throw EmulatorError.failure(
                result.status == 8 ? "not-provisioned" : "preview-failed",
                result.standardError.isEmpty ? result.standardOutput : result.standardError
            )
        }
        markPreview(name, active: true)
        return .object([
            "app": .string(name),
            "unit": .string(unit),
            "entryPoint": .string(entry),
            "appearance": .string(appearance),
            "systemPackageTouched": .bool(false),
        ])
    }

    /// The width matrix. Each width runs the *same* source in its own nested
    /// headless compositor, so what is captured is one binary answering five
    /// widths — not five variants and not a screenshot montage.
    func previewMatrix(_ request: ControlRequest) throws -> JSONValue {
        guard let name = request.arguments["app"]?.stringValue,
              let binary = Self.applications[name] else {
            throw EmulatorError.usage("unknown application for the width matrix")
        }
        let appearance = request.arguments["appearance"]?.stringValue ?? "light"
        let widths = (request.arguments["widths"]?.arrayValue?.compactMap { $0.intValue })
            ?? [360, 390, 500, 1024, 1440]
        let scale = request.arguments["scale"]?.intValue ?? 2
        let guest = try requireGuest()
        let entry = "\(Self.guestSource)/prairie-core/bin/\(binary)"
        let settleMS = request.arguments["settleMilliseconds"]?.intValue ?? 2500

        var captures: [JSONValue] = []
        for width in widths {
            let height = width < 500 ? 844 : 900
            let output = "\(Self.guestShots)/\(name)-\(width)x\(height).png"
            let script = """
            \(previewEnvironment(appearance: appearance))
            test -f "\(entry)" || { echo "preview source is missing" >&2; exit 8; }
            rm -f "\(output)"
            export WLR_BACKENDS=headless
            export WLR_LIBINPUT_NO_DEVICES=1
            export WLR_RENDERER_ALLOW_SOFTWARE=1
            export WLR_HEADLESS_OUTPUTS=1
            export GDK_BACKEND=wayland
            export XDG_RUNTIME_DIR=/run/user/1000
            unset WAYLAND_DISPLAY
            capture="\(Self.guestBin)/luma-emulator-capture"
            test -x "$capture" || capture=/usr/bin/luma-emulator-capture
            "$capture" "\(width)" "\(height)" "\(scale)" "\(settleMS)" "\(output)" \\
              /usr/bin/python3 "\(entry)"
            """
            let result = try guest.run(script, timeout: 180)
            guard result.succeeded else {
                throw EmulatorError.failure(
                    "matrix-failed",
                    "the \(width)px capture failed: \(result.standardError.isEmpty ? result.standardOutput : result.standardError)"
                )
            }
            captures.append(.object([
                "width": .int(width),
                "height": .int(height),
                "scale": .int(scale),
                "guestPath": .string(output),
            ]))
        }
        return .object([
            "app": .string(name),
            "entryPoint": .string(entry),
            "captures": .array(captures),
            "sameBinary": .bool(true),
        ])
    }

    // MARK: - preview shell

    /// A disposable nested shell session. The factory shell keeps running; a
    /// CSS or JavaScript change is never previewed by replacing the session the
    /// developer is looking at.
    func previewShell(_ request: ControlRequest) throws -> JSONValue {
        let surface = request.arguments["surface"]?.stringValue ?? "desktop"
        let guest = try requireGuest()
        let unit = "luma-preview-shell-\(surface)"

        // The nested command is derived from the guest's own session files, not
        // guessed. Luma's phosh installs its binary under /opt/luma and a
        // hard-coded /usr/bin/phosh silently starts nothing at all.
        let command: String
        let processPattern: String
        switch surface {
        case "desktop":
            // GNOME Shell 50 removed `--nested`: running without
            // `--display-server` *is* nested now, and passing the old flag makes
            // the option parser reject the whole command line. Wrapped in a
            // shell because systemd-run's argument handling eats the `--`.
            command = "/bin/sh -c 'exec dbus-run-session -- gnome-shell --wayland "
                + "--wayland-display luma-nested-desktop'"
            processPattern = "wayland-display luma-nested-desktop"
        case "handheld":
            let resolved = try guest.run("""
            exec_line=$(grep -h '^Exec=' /usr/share/wayland-sessions/phosh.desktop 2>/dev/null | head -1)
            exec_line=${exec_line#Exec=}
            if [ -n "$exec_line" ] && command -v "${exec_line%% *}" >/dev/null 2>&1; then
              echo "$exec_line"
            elif command -v phosh-session >/dev/null 2>&1; then
              echo phosh-session
            fi
            """, timeout: 60)
            let session = resolved.trimmedOutput
            guard !session.isEmpty else {
                throw EmulatorError.failure(
                    "not-provisioned",
                    "no handheld session command could be resolved in this factory image"
                )
            }
            command = "/bin/sh -c 'exec phoc -S -E \"\(session)\"'"
            // phoc nests into the parent socket the session prelude resolved.
            processPattern = "phoc -S -E"
        default:
            throw EmulatorError.usage("shell surface must be 'desktop' or 'handheld'")
        }

        _ = try guest.runInSession(
            "systemctl --user reset-failed \(unit) 2>/dev/null; "
            + "systemctl --user stop \(unit) 2>/dev/null; true"
        )
        let launch = """
        systemd-run --user --unit=\(unit) --collect --quiet \
          --setenv=XDG_RUNTIME_DIR=/run/user/1000 \
          --setenv=WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
          --setenv=WLR_RENDERER_ALLOW_SOFTWARE=1 \
          --setenv=MUTTER_DEBUG_DUMMY_MODE_SPECS=\(runtime.spec.viewport.width)x\(runtime.spec.viewport.height) \
          \(command)
        # Wait for the nested compositor process itself rather than for the
        # unit's state. `--collect` removes a unit the moment it exits, so unit
        # state is a poor signal, and what actually matters is whether a second
        # compositor is running.
        found=""
        for attempt in $(seq 1 15); do
          sleep 1
          found=$(pgrep -f '\(processPattern)' 2>/dev/null | head -1)
          [ -n "$found" ] && break
        done
        if [ -z "$found" ]; then
          echo "the nested \(surface) shell did not start:" >&2
          journalctl --user -u \(unit) --since "-2min" -n 20 --no-pager 2>&1 | tail -20 >&2
          exit 9
        fi
        echo "running as pid $found"
        """
        let result = try guest.runInSession(launch, timeout: 120)
        guard result.succeeded else {
            throw EmulatorError.failure(
                result.status == 8 ? "not-provisioned" : "shell-preview-failed",
                result.standardError.isEmpty ? result.standardOutput : result.standardError
            )
        }
        markPreview("shell-\(surface)", active: true)
        return .object([
            "surface": .string(surface),
            "unit": .string(unit),
            "command": .string(command),
            "state": .string(result.trimmedOutput),
            "factoryShellReplaced": .bool(false),
        ])
    }

    func stopPreviews() throws -> JSONValue {
        let guest = try requireGuest()
        // Only units this emulator created are touched. Nothing else in the
        // guest is enumerated, signalled, or killed.
        let result = try guest.runInSession("""
        stopped=""
        for unit in $(systemctl --user list-units --plain --no-legend 'luma-preview-*' | awk '{print $1}'); do
          systemctl --user stop "$unit" >/dev/null 2>&1 || true
          systemctl --user reset-failed "$unit" >/dev/null 2>&1 || true
          stopped="$stopped $unit"
        done
        echo "$stopped"
        """)
        for name in Self.applications.keys { markPreview(name, active: false) }
        markPreview("shell-desktop", active: false)
        markPreview("shell-handheld", active: false)
        return .object([
            "stopped": .strings(
                result.trimmedOutput.split(separator: " ").map(String.init)
            )
        ])
    }

    // MARK: - screenshot

    /// Captures the guest display through a supported path.
    ///
    /// The order matters and is deliberate:
    ///
    /// 1. **Host-side**, from the real `VZVirtualMachineView`, when a Mac window
    ///    exists. Needs nothing from the guest session and captures the guest's
    ///    framebuffer without the Mac window frame.
    /// 2. **grim**, on wlroots sessions — the handheld shell.
    /// 3. **The desktop portal**, the interface that replaced the old GNOME
    ///    screenshot D-Bus method.
    ///
    /// `org.gnome.Shell.Screenshot` is deliberately absent. GNOME 45 and later
    /// refuse it to unprivileged callers, and that refusal is correct; the
    /// emulator works with it rather than around it.
    func screenshot(_ request: ControlRequest) throws -> JSONValue {
        guard let destination = request.arguments["output"]?.stringValue else {
            throw EmulatorError.usage("screenshot needs an output path")
        }
        let requestedSource = request.arguments["source"]?.stringValue ?? "auto"
        let local = URL(fileURLWithPath: destination)
        try? FileManager.default.createDirectory(
            at: local.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        let started = Date()
        var attempts: [String] = []

        // 1. Host-side capture of the actual VM view.
        if requestedSource == "auto" || requestedSource == "host" {
            if let capture = hostCapture?() {
                try capture.data.write(to: local)
                return captureResult(
                    local: local, backend: "host-vz-view", started: started,
                    pixelWidth: capture.pixelWidth, pixelHeight: capture.pixelHeight,
                    attempts: attempts
                )
            }
            attempts.append(
                "host-vz-view: no visible Mac window, or its contents could not be read"
            )
            if requestedSource == "host" {
                throw EmulatorError.failure(
                    "capture-host-unavailable",
                    "host-side capture needs a visible Mac window. Start without --headless."
                )
            }
        }

        // 2 and 3, both inside the guest.
        let guest = try requireGuest()
        let stamp = Int(Date().timeIntervalSince1970 * 1000)
        let remote = "\(Self.guestShots)/capture-\(stamp).png"

        let grim = try guest.runInSession("""
        mkdir -p "\(Self.guestShots)"
        command -v grim >/dev/null 2>&1 || { echo "grim absent" >&2; exit 20; }
        grim "\(remote)" 2>&1 || exit 21
        test -s "\(remote)" || exit 22
        echo grim
        """, timeout: 90)

        if grim.succeeded {
            return try fetchCapture(
                guest: guest, remote: remote, local: local,
                backend: "guest-grim", started: started, attempts: attempts
            )
        }
        attempts.append("guest-grim: \(grim.standardError.trimmingCharacters(in: .whitespacesAndNewlines).prefix(160))")

        let portal = try guest.runInSession("""
        helper="\(Self.guestBin)/luma-emulator-portal-shot.py"
        test -f "$helper" || helper=/usr/libexec/luma-emulator-portal-shot.py
        test -f "$helper" || { echo "portal helper is not installed" >&2; exit 30; }
        /usr/bin/python3 "$helper" "\(remote)" --timeout 20
        """, timeout: 120)

        if portal.succeeded {
            return try fetchCapture(
                guest: guest, remote: remote, local: local,
                backend: "guest-portal", started: started, attempts: attempts
            )
        }
        attempts.append("guest-portal: \(portal.standardError.trimmingCharacters(in: .whitespacesAndNewlines).prefix(240))")

        throw EmulatorError.failure(
            "screenshot-unavailable",
            """
            no supported capture backend produced an image.
            \(attempts.joined(separator: "\n"))
            The old org.gnome.Shell.Screenshot method is deliberately not attempted:
            GNOME refuses it to unprivileged callers and the emulator does not
            weaken that.
            """
        )
    }

    private func fetchCapture(
        guest: Guest, remote: String, local: URL,
        backend: String, started: Date, attempts: [String]
    ) throws -> JSONValue {
        try guest.fetch(remotePath: remote, to: local)
        _ = try? guest.run("rm -f \"\(remote)\"")
        let size = PNG.dimensions(of: local)
        return captureResult(
            local: local, backend: backend, started: started,
            pixelWidth: size?.width ?? 0, pixelHeight: size?.height ?? 0,
            attempts: attempts
        )
    }

    private func captureResult(
        local: URL, backend: String, started: Date,
        pixelWidth: Int, pixelHeight: Int, attempts: [String]
    ) -> JSONValue {
        let bytes = Int(Shell.fileSize(local.path))
        var payload: [String: JSONValue] = [
            "output": .string(local.path),
            "backend": .string(backend),
            "bytes": .int(bytes),
            "pixelWidth": .int(pixelWidth),
            "pixelHeight": .int(pixelHeight),
            "logicalWidth": .int(runtime.spec.viewport.width),
            "logicalHeight": .int(runtime.spec.viewport.height),
            "scale": .int(runtime.spec.viewport.scale),
            "surface": .string(runtime.spec.mode.rawValue),
            "machine": .string(runtime.spec.name),
            "capturedAt": .string(ISO8601DateFormatter().string(from: Date())),
            "latencySeconds": .double((Date().timeIntervalSince(started) * 1000).rounded() / 1000),
            "decoratedByHost": .bool(false),
        ]
        if let manifest = try? loadManifest() {
            payload["imageID"] = .string(manifest.imageID)
            payload["lumaCompleteness"] = .string(manifest.lumaCompleteness)
            if let bundle = manifest.lumaBundle {
                payload["lumaSourceCommit"] = .string(bundle.sourceCommit)
            }
        }
        if let project = try? loadProject(), let revision = projectRevisionPublic(project) {
            payload["projectRevision"] = .string(revision)
        }
        if !attempts.isEmpty {
            payload["attemptsBeforeSuccess"] = .strings(attempts)
        }
        return .object(payload)
    }

    // MARK: - presentation mode

    /// Keeps the guest's display awake.
    ///
    /// A development guest that blanks itself after a few minutes cannot be
    /// screenshotted or watched, and the blanked output also refuses mode
    /// changes. These are the ordinary user-level settings a person would
    /// change themselves; nothing is patched.
    @discardableResult
    func prepareSession() -> JSONValue {
        guard let guest = try? requireGuest() else { return .null }
        let result = try? guest.runInSession("""
        gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null || true
        gsettings set org.gnome.desktop.screensaver lock-enabled false 2>/dev/null || true
        gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type nothing 2>/dev/null || true
        gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type nothing 2>/dev/null || true
        # Without this the desktop session answers the ACPI shutdown request
        # with a confirmation dialog, and a headless guest can never dismiss it —
        # so an ordinary `stop` looks like a hang.
        gsettings set org.gnome.settings-daemon.plugins.power power-button-action shutdown 2>/dev/null || true
        # The handheld shell locks on start. In a disposable development guest
        # the lock protects nothing and there is nobody to swipe it away, so it
        # would simply block every preview and screenshot. Unlocking here does
        # not change Luma's lock behaviour on a real device — it is this VM's
        # session being unlocked, exactly as a person sitting at it would.
        sudo -n loginctl unlock-sessions 2>/dev/null || true
        echo prepared
        """, timeout: 60)
        return .string(result?.trimmedOutput ?? "unavailable")
    }

    /// Points the guest's display manager at the shell for this machine's mode.
    ///
    /// Desktop and handheld are presentation modes of one platform: this selects
    /// a *session*, from the same installed packages. It never selects a
    /// different application build, and it is a no-op when the guest is already
    /// in the right mode, so an ordinary restart does not bounce the session.
    func applyPresentationMode() throws -> JSONValue {
        let guest = try requireGuest()
        let desired = runtime.spec.mode == .mobile ? "phosh" : "gnome"
        let viewport = runtime.spec.viewport

        let currentSession = (try? guest.run("cat /etc/luma-emulator/session 2>/dev/null"))?
            .trimmedOutput ?? ""
        // The viewport is part of what a mode application decides, and it can
        // change without the session changing. Comparing only the session name
        // leaves the compositor configured for the previous size.
        // Only the scale is written. Virtualization.framework aligns the
        // scanout width (390x3 becomes 1168, 390x2 becomes 776), so a `mode`
        // line computed from the requested viewport names a mode the display
        // does not advertise; phoc then cannot apply it and the configured
        // geometry and the real geometry disagree. Letting phoc take the
        // preferred mode means it always matches the scanout exactly.
        let desiredPhoc = """
        # Written by the Luma Emulator for the requested handheld viewport.
        # The mode is deliberately not pinned here: the virtual display's
        # preferred mode is the scanout the emulator asked for.
        [output:Virtual-1]
        scale = \(viewport.scale)
        """
        let currentPhoc = (try? guest.run("cat /etc/phosh/phoc.ini 2>/dev/null"))?
            .standardOutput.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let phocMatches = currentPhoc == desiredPhoc.trimmingCharacters(in: .whitespacesAndNewlines)

        guard currentSession != desired || !phocMatches else {
            return .object([
                "session": .string(desired), "changed": .bool(false),
                "viewport": .string(viewport.descriptor),
            ])
        }

        // The compositor's own configuration carries the viewport. Writing it
        // before the session starts is the supported path; changing the mode at
        // runtime underneath a running compositor on a software-rendered GPU
        // leaves the display corrupt.
        let encoded = Data(desiredPhoc.utf8).base64EncodedString()
        let result = try guest.run("""
        set -e
        echo '\(desired)' | sudo -n tee /etc/luma-emulator/session >/dev/null
        sudo -n install -d -m 0755 /etc/phosh
        printf '%s' '\(encoded)' | base64 -d | sudo -n tee /etc/phosh/phoc.ini >/dev/null
        sudo -n /usr/local/sbin/luma-emulator-session
        sudo -n systemctl restart gdm
        """, timeout: 240)
        guard result.succeeded else {
            throw EmulatorError.failure(
                "mode-switch-failed",
                "could not select the \(desired) session: \(result.standardError)"
            )
        }
        return .object([
            "session": .string(desired), "changed": .bool(true),
            "viewport": .string(viewport.descriptor),
            "detail": .string(result.trimmedOutput),
        ])
    }

    /// Makes the handheld compositor actually use the viewport that was asked
    /// for.
    ///
    /// The virtual display offers the requested mode, but phoc picks its own
    /// default and the guest ends up laying out against a size nobody chose.
    /// Selecting the preferred mode and the requested scale is what makes
    /// `--viewport 390x844` mean 390 logical points to Luma's breakpoints.
    @discardableResult
    func applyHandheldViewport() -> JSONValue {
        guard runtime.spec.mode == .mobile, let guest = try? requireGuest() else { return .null }
        let scale = runtime.spec.viewport.scale

        // Retried from the host rather than in a shell loop, so each attempt
        // re-resolves the session's Wayland socket. During start-up the greeter's
        // socket and the user session's socket both exist for a while, and a
        // loop that resolved it once can spend its whole life talking to the
        // wrong one.
        var observed = ""
        for _ in 0..<20 {
            guard let result = try? guest.runInSession("""
            command -v wlr-randr >/dev/null 2>&1 || { echo no-wlr-randr; exit 0; }
            output=$(wlr-randr 2>/dev/null | awk 'NR==1{print $1}')
            if [ -z "$output" ]; then echo no-output; exit 0; fi
            preferred=$(wlr-randr | awk '/preferred/{print $1; exit}')
            if [ -n "$preferred" ]; then
              wlr-randr --output "$output" --on --mode "$preferred" --scale \(scale) \
                >/dev/null 2>&1 || true
              sleep 1
            fi
            wlr-randr | awk '/current/{print $1; exit}'
            """, timeout: 90) else {
                sleep(3)
                continue
            }
            observed = result.trimmedOutput
            if observed == "no-wlr-randr" { return .object(["applied": .bool(false)]) }
            if !observed.isEmpty, observed != "no-output" { break }
            sleep(3)
        }

        return .object([
            "requested": .string(runtime.spec.viewport.name),
            "requestedPixels": .string(
                "\(runtime.spec.viewport.pixelWidth)x\(runtime.spec.viewport.pixelHeight)"
            ),
            "guestMode": .string(observed.isEmpty ? "unknown" : observed),
            "scale": .int(scale),
        ])
    }

    // MARK: - file transfer

    func fetchFile(_ request: ControlRequest) throws -> JSONValue {
        guard let remote = request.arguments["remote"]?.stringValue,
              let local = request.arguments["local"]?.stringValue else {
            throw EmulatorError.usage("fetch needs 'remote' and 'local'")
        }
        let guest = try requireGuest()
        let url = URL(fileURLWithPath: local)
        try? FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        try guest.fetch(remotePath: remote, to: url)
        let size = Int(Shell.fileSize(url.path))
        return .object(["local": .string(url.path), "bytes": .int(size)])
    }

    /// Used by provisioning to place the version-controlled recipe in the
    /// guest. The destination is always a directory the emulator created.
    func pushFile(_ request: ControlRequest) throws -> JSONValue {
        guard let local = request.arguments["local"]?.stringValue,
              let remoteDirectory = request.arguments["remoteDirectory"]?.stringValue else {
            throw EmulatorError.usage("push-file needs 'local' and 'remoteDirectory'")
        }
        let guest = try requireGuest()
        _ = try guest.run("mkdir -p \"\(remoteDirectory)\"")
        let source = URL(fileURLWithPath: local)
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: source.path, isDirectory: &isDirectory) else {
            throw EmulatorError.usage("\(source.path) does not exist on this Mac")
        }
        let result = isDirectory.boolValue
            ? try guest.push(from: source, to: remoteDirectory, delete: false, timeout: 300)
            : try guest.pushFile(source, to: remoteDirectory, timeout: 300)
        guard result.succeeded else {
            throw EmulatorError.failure("push-failed", result.standardError)
        }
        return .object(["remoteDirectory": .string(remoteDirectory)])
    }

    // MARK: - scenarios

    func scenario(_ request: ControlRequest) throws -> JSONValue {
        guard let name = request.arguments["name"]?.stringValue else {
            throw EmulatorError.usage("scenario needs a name")
        }
        let catalogue = try ScenarioCatalogue.load()
        if name == "list" {
            return catalogue.describe()
        }
        guard let scenario = catalogue.scenario(named: name) else {
            throw EmulatorError.usage(
                "unknown scenario '\(name)'. Run `luma-emulator scenario list` for the catalogue."
            )
        }
        guard scenario.supported else {
            throw EmulatorError.failure(
                "scenario-unsupported",
                """
                '\(name)' is catalogued but not implemented in this release: \(scenario.gap ?? "no supported interface yet").
                It is deliberately absent rather than faked.
                """
            )
        }
        let guest = try requireGuest()
        let result = try guest.runInSession(scenario.script, timeout: 120)
        guard result.succeeded else {
            throw EmulatorError.failure("scenario-failed", result.standardError)
        }
        return .object([
            "scenario": .string(name),
            "interface": .string(scenario.interface),
            "simulated": .bool(true),
            "physicalEvidence": .bool(false),
            "output": .string(result.trimmedOutput),
        ])
    }

    // MARK: - runtime proof

    /// Proves what is actually installed and running, rather than trusting the
    /// image manifest.
    ///
    /// A manifest records what provisioning believed it did. This asks the guest
    /// three separate questions: is every package in the manifest installed at
    /// that exact NEVRA, has anything modified the files those packages own, and
    /// do the processes actually running resolve back to Luma's packages. A
    /// shell that came from Fedora will show up here even if the manifest looks
    /// right.
    func verifyGuest(_ request: ControlRequest) throws -> JSONValue {
        let guest = try requireGuest()
        let manifest = try? loadManifest()
        let expected = manifest?.lumaPackages ?? []

        var payload: [String: JSONValue] = [
            "lumaCompleteness": .string(manifest?.lumaCompleteness ?? "fedora-fallback"),
            "expectedPackageCount": .int(expected.count),
        ]
        if let bundle = manifest?.lumaBundle {
            payload["bundleName"] = .string(bundle.bundleName)
            payload["bundleSHA256"] = .string(bundle.bundleSHA256)
            payload["lumaSourceCommit"] = .string(bundle.sourceCommit)
            payload["substitutions"] = .strings(bundle.substitutions)
        }

        // Prove the tool works before believing anything it says.
        //
        // A broken rpm reports every package as missing, which is
        // indistinguishable from a genuinely empty image unless the tool itself
        // is checked first. Turning a tool failure into "24 packages missing"
        // is the worst thing a verifier can do, because it is a confident lie.
        let probe = try guest.run(
            "rpm --version 2>&1 | head -1; echo \"rc=$?\"; "
            + "rpm -q rpm 2>&1 | head -1", timeout: 90
        )
        let probeText = probe.standardOutput + probe.standardError
        let rpmUsable = probe.succeeded
            && !probeText.contains("Unable to open")
            && probeText.contains("RPM version")
        payload["packageToolUsable"] = .bool(rpmUsable)
        if !rpmUsable {
            payload["packageToolError"] = .string(
                probeText.trimmingCharacters(in: .whitespacesAndNewlines).prefix(400).description
            )
            payload["ok"] = .bool(false)
            payload["verdict"] = .string("indeterminate")
            payload["reason"] = .string(
                """
                rpm is not usable in this guest, so installed packages cannot be verified. \
                This is NOT evidence that packages are missing — it is evidence that the \
                check could not run. A guest that has been mutated by hand can end up here; \
                a machine created fresh from the factory image should not.
                """
            )
            return .object(payload)
        }

        // 1. Every manifest package installed, at exactly that NEVRA.
        var installed: [String] = []
        var wrong: [String] = []
        if !expected.isEmpty {
            let script = expected.map { nevra in
                let name = nevra.split(separator: "-").dropLast(2).joined(separator: "-")
                return "rpm -q --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\\n' \"\(name)\" 2>/dev/null || echo MISSING"
            }.joined(separator: "\n")
            let result = try guest.run(script, timeout: 180)
            let lines = result.standardOutput.split(separator: "\n").map(String.init)
            for (index, expectedNEVRA) in expected.enumerated() {
                let actual = index < lines.count ? lines[index] : "MISSING"
                if actual == expectedNEVRA {
                    installed.append(expectedNEVRA)
                } else {
                    wrong.append("\(expectedNEVRA) -> \(actual)")
                }
            }
        }
        payload["installedAsExpected"] = .int(installed.count)
        payload["notInstalledAsExpected"] = .strings(wrong)

        // 2. rpm's own integrity check on the packages that define the look.
        let critical = ["gtk4", "libadwaita", "libhandy", "gtk3",
                        "luma-developer-platform", "prairie-core-apps",
                        "google-figtree-fonts", "prairie-icon-theme",
                        "luma-phosh", "phoc", "gnome-shell"]
        let verification = try guest.run(
            "for p in \(critical.joined(separator: " ")); do "
            + "if rpm -q \"$p\" >/dev/null 2>&1; then out=$(rpm -V \"$p\" 2>&1); "
            + "if [ -z \"$out\" ]; then echo \"$p ok\"; else echo \"$p MODIFIED\"; fi; "
            + "else echo \"$p absent\"; fi; done",
            timeout: 300
        )
        payload["packageIntegrity"] = .strings(
            verification.standardOutput.split(separator: "\n").map(String.init)
        )

        // 3. Executables resolved back to their owning package.
        let executables = try guest.run("""
        for path in /opt/luma/phosh/libexec/phosh /usr/bin/phoc /usr/bin/gnome-shell \
                    /usr/bin/prairie-notes /usr/bin/prairie-clock; do
          if [ ! -e "$path" ]; then echo "$path: absent"; continue; fi
          owner=$(rpm -qf "$path" 2>/dev/null | head -1)
          [ -n "$owner" ] || owner="not owned by any package"
          echo "$path <- $owner"
        done
        """, timeout: 180)
        payload["executableOwnership"] = .strings(
            executables.standardOutput.split(separator: "\n").map(String.init)
        )

        // 4. Running processes resolved back to their owning package.
        let processes = try guest.run("""
        for name in gnome-shell phoc phosh gdm; do
          pid=$(pgrep -x "$name" 2>/dev/null | head -1)
          if [ -z "$pid" ]; then echo "$name: not running"; continue; fi
          exe=$(sudo -n readlink -f "/proc/$pid/exe" 2>/dev/null)
          if [ -z "$exe" ]; then echo "$name: running (pid $pid), executable not readable"; continue; fi
          owner=$(rpm -qf "$exe" 2>/dev/null | head -1)
          [ -n "$owner" ] || owner="not owned by any package"
          echo "$name: $exe <- $owner"
        done
        """, timeout: 180)
        payload["runningProcesses"] = .strings(
            processes.standardOutput.split(separator: "\n").map(String.init)
        )

        // 5. Typography, asked of fontconfig rather than of rpm.
        let font = try guest.run(
            "rpm -q google-figtree-fonts 2>/dev/null || echo absent; "
            + "fc-match Figtree 2>/dev/null || echo 'fc-match unavailable'; "
            + "fc-list 2>/dev/null | grep -ci figtree || echo 0",
            timeout: 120
        )
        let fontLines = font.standardOutput.split(separator: "\n").map(String.init)
        payload["figtreePackage"] = .string(fontLines.first ?? "unknown")
        payload["figtreeResolves"] = .string(fontLines.count > 1 ? fontLines[1] : "unknown")
        payload["figtreeFaces"] = .int(fontLines.count > 2 ? Int(fontLines[2]) ?? 0 : 0)
        let figtreeOK = (fontLines.count > 1 ? fontLines[1] : "").hasPrefix("Figtree")
        payload["figtreeOK"] = .bool(figtreeOK)

        // 6. Every visible core launcher must have artwork the icon theme can
        //    actually resolve. A question-mark tile is a packaging defect, and
        //    it is invisible to rpm because the desktop file is installed fine.
        let icons = try guest.run("""
        python3 - <<'ICONS'
        import glob, os, re
        missing, ok = [], []
        for path in sorted(glob.glob("/usr/share/applications/org.projectluma.*.desktop")):
            text = open(path, errors="replace").read()
            if re.search(r"^NoDisplay=true", text, re.M):
                continue
            match = re.search(r"^Icon=(.+)$", text, re.M)
            if not match:
                continue
            name = match.group(1).strip()
            found = False
            for root in ("/usr/share/icons", "/usr/share/pixmaps"):
                for _dir, _sub, files in os.walk(root):
                    if any(f.startswith(name + ".") for f in files):
                        found = True
                        break
                if found:
                    break
            (ok if found else missing).append(name)
        print("RESOLVED:" + ",".join(ok))
        print("MISSING:" + ",".join(missing))
        ICONS
        """, timeout: 180)
        var resolvedIcons: [String] = []
        var missingIcons: [String] = []
        for line in icons.standardOutput.split(separator: "\n") {
            if line.hasPrefix("RESOLVED:") {
                resolvedIcons = line.dropFirst("RESOLVED:".count)
                    .split(separator: ",").map(String.init)
            } else if line.hasPrefix("MISSING:") {
                missingIcons = line.dropFirst("MISSING:".count)
                    .split(separator: ",").map(String.init)
            }
        }
        payload["launcherIconsResolved"] = .strings(resolvedIcons)
        payload["launcherIconsMissing"] = .strings(missingIcons)
        payload["launcherIconsOK"] = .bool(missingIcons.isEmpty)

        let allInstalled = wrong.isEmpty && !expected.isEmpty
        payload["ok"] = .bool(allInstalled && figtreeOK && missingIcons.isEmpty)
        payload["verdict"] = .string(
            allInstalled && figtreeOK && missingIcons.isEmpty ? "verified" : "defects-found"
        )
        return .object(payload)
    }

    // MARK: - accessibility

    func inspectAccessibility(_ request: ControlRequest) throws -> JSONValue {
        let guest = try requireGuest()
        let result = try guest.runInSession("""
        if ! command -v python3 >/dev/null 2>&1; then echo "python3 missing" >&2; exit 1; fi
        helper="\(Self.guestBin)/luma-emulator-a11y.py"
        test -f "$helper" || helper=/usr/libexec/luma-emulator-a11y.py
        python3 "$helper" 2>/dev/null || {
          echo "the accessibility bridge is not available in this factory image" >&2
          exit 8
        }
        """, timeout: 90)
        guard result.succeeded else {
            throw EmulatorError.failure(
                result.status == 8 ? "not-provisioned" : "a11y-failed",
                result.standardError
            )
        }
        if let data = result.standardOutput.data(using: .utf8),
           let parsed = try? JSONDecoder().decode(JSONValue.self, from: data) {
            return parsed
        }
        return .object(["raw": .string(result.standardOutput)])
    }

    // MARK: - logs

    func logs(_ request: ControlRequest) throws -> JSONValue {
        let component = request.arguments["component"]?.stringValue ?? "boot"
        let lines = request.arguments["lines"]?.intValue ?? 200

        if component == "boot" {
            let text = tail(of: runtime.consoleLogURL, lines: lines)
            return .object([
                "component": .string("boot"),
                "source": .string(runtime.consoleLogURL.path),
                "text": .string(text),
            ])
        }

        let guest = try requireGuest()
        let selector: String
        switch component {
        case "shell":
            selector = "journalctl --user -n \(lines) --no-pager -u 'gnome-shell*' -u 'phosh*' -u 'org.gnome.Shell*' 2>/dev/null || journalctl -n \(lines) --no-pager"
        case "preview":
            selector = "journalctl --user -n \(lines) --no-pager -u 'luma-preview-*'"
        case "session":
            selector = "journalctl --user -n \(lines) --no-pager"
        case "system":
            selector = "journalctl -n \(lines) --no-pager"
        default:
            selector = "journalctl --user -n \(lines) --no-pager -u '\(component)'"
        }
        let result = try guest.runInSession(selector, timeout: 60)
        return .object([
            "component": .string(component),
            "source": .string("guest journal"),
            "text": .string(result.standardOutput),
        ])
    }

    private func tail(of url: URL, lines: Int) -> String {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return "" }
        defer { try? handle.close() }
        let size = (try? handle.seekToEnd()) ?? 0
        let window = UInt64(min(Int(size), max(4096, lines * 512)))
        try? handle.seek(toOffset: size - window)
        guard let data = try? handle.readToEnd() else { return "" }
        let text = String(decoding: data, as: UTF8.self)
        return text.split(separator: "\n").suffix(lines).joined(separator: "\n")
    }
}
