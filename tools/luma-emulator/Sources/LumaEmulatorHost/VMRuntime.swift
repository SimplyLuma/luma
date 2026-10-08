// SPDX-License-Identifier: Apache-2.0

import Foundation
import Virtualization
import LumaEmulatorKit

/// Owns exactly one `VZVirtualMachine`.
///
/// Virtualization.framework requires that every call touching the machine
/// happens on one serial queue. That queue is the emulator's only writer, and
/// both the Mac application and the control socket funnel through it, which is
/// why there is no second state machine to keep in step.
final class VMRuntime: NSObject, VZVirtualMachineDelegate {
    enum Failure: Error, CustomStringConvertible {
        case message(String)
        var description: String {
            if case .message(let text) = self { return text }
            return "unknown"
        }
    }

    let queue = DispatchQueue(label: "org.projectluma.emulator.vm")
    let paths: EmulatorPaths
    let spec: MachineSpec
    private(set) var machine: VZVirtualMachine?
    private(set) var health: Health = .stopped
    private(set) var startedAt: Date?
    private(set) var lastFailure: String?
    private(set) var guestAddress: String?

    var machineDirectory: URL { paths.machine(spec.name) }
    var diskURL: URL { machineDirectory.appendingPathComponent("disk.raw") }
    var seedURL: URL { machineDirectory.appendingPathComponent("seed.iso") }
    var nvramURL: URL { machineDirectory.appendingPathComponent("efi.nvram") }
    var consoleLogURL: URL { machineDirectory.appendingPathComponent("console.log") }
    var specURL: URL { machineDirectory.appendingPathComponent("machine.json") }

    /// A stable, locally administered MAC so the guest keeps one DHCP lease
    /// across restarts and the host can find it again without guessing.
    var macAddress: VZMACAddress {
        var bytes: [UInt8] = [0x52, 0x54, 0x00, 0x00, 0x00, 0x00]
        var hash: UInt32 = 2166136261
        for byte in spec.name.utf8 {
            hash = (hash ^ UInt32(byte)) &* 16777619
        }
        bytes[3] = UInt8((hash >> 16) & 0x7F)
        bytes[4] = UInt8((hash >> 8) & 0xFF)
        bytes[5] = UInt8(hash & 0xFF)
        var address = ether_addr_t()
        withUnsafeMutableBytes(of: &address) { raw in
            for (index, byte) in bytes.enumerated() { raw[index] = byte }
        }
        return VZMACAddress(ethernetAddress: address)
    }

    init(paths: EmulatorPaths, spec: MachineSpec) {
        self.paths = paths
        self.spec = spec
    }

    // MARK: - Configuration

    func makeConfiguration() throws -> VZVirtualMachineConfiguration {
        let manager = FileManager.default
        guard manager.fileExists(atPath: diskURL.path) else {
            throw Failure.message(
                "machine '\(spec.name)' has no disk. Run `luma-emulator bootstrap` first."
            )
        }

        let configuration = VZVirtualMachineConfiguration()
        configuration.cpuCount = spec.cpuCount
        configuration.memorySize = UInt64(spec.memoryMiB) * 1024 * 1024
        configuration.platform = VZGenericPlatformConfiguration()

        let bootLoader = VZEFIBootLoader()
        if manager.fileExists(atPath: nvramURL.path) {
            bootLoader.variableStore = VZEFIVariableStore(url: nvramURL)
        } else {
            bootLoader.variableStore = try VZEFIVariableStore(creatingVariableStoreAt: nvramURL)
        }
        configuration.bootLoader = bootLoader

        var storage: [VZStorageDeviceConfiguration] = [
            VZVirtioBlockDeviceConfiguration(
                // Explicit caching avoids the ARM disk-corruption path reported
                // by Lima/UTM with Apple's automatic/uncached mode (Lima #2026).
                // Full synchronization still honors guest durability requests.
                attachment: try VZDiskImageStorageDeviceAttachment(
                    url: diskURL, readOnly: false,
                    cachingMode: .cached, synchronizationMode: .full
                )
            )
        ]
        // The cloud-init seed is only present until the guest has been
        // provisioned. It is read-only and carries no secret beyond the public
        // half of the per-installation guest key.
        if manager.fileExists(atPath: seedURL.path) {
            storage.append(
                VZVirtioBlockDeviceConfiguration(
                    attachment: try VZDiskImageStorageDeviceAttachment(url: seedURL, readOnly: true)
                )
            )
        }
        configuration.storageDevices = storage

        let network = VZVirtioNetworkDeviceConfiguration()
        network.attachment = VZNATNetworkDeviceAttachment()
        network.macAddress = macAddress
        configuration.networkDevices = [network]

        let graphics = VZVirtioGraphicsDeviceConfiguration()
        graphics.scanouts = [
            VZVirtioGraphicsScanoutConfiguration(
                widthInPixels: spec.viewport.pixelWidth,
                heightInPixels: spec.viewport.pixelHeight
            )
        ]
        configuration.graphicsDevices = [graphics]

        // Expose playback through the Mac's current output; no capture stream
        // or microphone permission is needed.
        let audioOutput = VZVirtioSoundDeviceOutputStreamConfiguration()
        audioOutput.sink = VZHostAudioOutputStreamSink()
        let audio = VZVirtioSoundDeviceConfiguration()
        audio.streams = [audioOutput]
        configuration.audioDevices = [audio]

        configuration.keyboards = [VZUSBKeyboardConfiguration()]
        configuration.pointingDevices = [VZUSBScreenCoordinatePointingDeviceConfiguration()]
        configuration.entropyDevices = [VZVirtioEntropyDeviceConfiguration()]
        configuration.memoryBalloonDevices = [VZVirtioTraditionalMemoryBalloonDeviceConfiguration()]

        // The serial console is the boot log. It is how `logs --component boot`
        // can explain a failure that happens before SSH exists.
        if !manager.fileExists(atPath: consoleLogURL.path) {
            manager.createFile(atPath: consoleLogURL.path, contents: nil)
        }
        let serial = VZVirtioConsoleDeviceSerialPortConfiguration()
        serial.attachment = try VZFileSerialPortAttachment(url: consoleLogURL, append: true)
        configuration.serialPorts = [serial]

        // Clipboard in both directions, through the guest's spice-vdagent.
        let console = VZVirtioConsoleDeviceConfiguration()
        let port = VZVirtioConsolePortConfiguration()
        port.name = VZSpiceAgentPortAttachment.spiceAgentPortName
        port.attachment = VZSpiceAgentPortAttachment()
        console.ports[0] = port
        configuration.consoleDevices = [console]

        try configuration.validate()
        return configuration
    }

    // MARK: - Lifecycle

    func start(completion: @escaping (Result<Void, Error>) -> Void) {
        queue.async {
            guard self.machine == nil else {
                completion(.success(()))
                return
            }
            do {
                let configuration = try self.makeConfiguration()
                let machine = VZVirtualMachine(configuration: configuration, queue: self.queue)
                machine.delegate = self
                self.machine = machine
                self.health = .booting
                self.startedAt = Date()
                self.lastFailure = nil
                machine.start { result in
                    switch result {
                    case .success:
                        completion(.success(()))
                    case .failure(let error):
                        self.health = .failed
                        self.lastFailure = error.localizedDescription
                        completion(.failure(error))
                    }
                }
            } catch {
                self.health = .failed
                self.lastFailure = "\(error)"
                completion(.failure(error))
            }
        }
    }

    /// Asks the guest to shut down through ACPI, then reports whether it did.
    /// A forced teardown is a separate, explicit call — never a silent fallback,
    /// because an unclean stop can lose the developer's work in the guest.
    func requestStop(completion: @escaping (Result<Void, Error>) -> Void) {
        queue.async {
            guard let machine = self.machine else {
                completion(.success(()))
                return
            }
            guard machine.canRequestStop else {
                completion(.failure(Failure.message("the guest cannot accept a shutdown request yet")))
                return
            }
            do {
                try machine.requestStop()
                completion(.success(()))
            } catch {
                completion(.failure(error))
            }
        }
    }

    func forceStop(completion: @escaping (Result<Void, Error>) -> Void) {
        queue.async {
            guard let machine = self.machine else {
                completion(.success(()))
                return
            }
            machine.stop { error in
                if let error {
                    completion(.failure(error))
                } else {
                    self.machine = nil
                    self.health = .stopped
                    // A forced stop does not go through the delegate, so it has
                    // to announce itself. Without this a headless runtime keeps
                    // holding the control socket after its guest is gone, and
                    // the next start reports a guest that is already running.
                    NotificationCenter.default.post(
                        name: .lumaEmulatorGuestStopped, object: nil
                    )
                    completion(.success(()))
                }
            }
        }
    }

    func pause(completion: @escaping (Result<Void, Error>) -> Void) {
        queue.async {
            guard let machine = self.machine, machine.canPause else {
                completion(.failure(Failure.message("the guest cannot be paused in its current state")))
                return
            }
            machine.pause { result in completion(result.map { _ in () }) }
        }
    }

    func resume(completion: @escaping (Result<Void, Error>) -> Void) {
        queue.async {
            guard let machine = self.machine, machine.canResume else {
                completion(.failure(Failure.message("the guest cannot be resumed in its current state")))
                return
            }
            machine.resume { result in completion(result.map { _ in () }) }
        }
    }

    var runState: String {
        guard let machine else { return "stopped" }
        switch machine.state {
        case .stopped: return "stopped"
        case .running: return "running"
        case .paused: return "paused"
        case .error: return "error"
        case .starting: return "starting"
        case .pausing: return "pausing"
        case .resuming: return "resuming"
        case .stopping: return "stopping"
        case .saving: return "saving"
        case .restoring: return "restoring"
        @unknown default: return "unknown"
        }
    }

    func setHealth(_ value: Health) {
        health = value
    }

    func setGuestAddress(_ value: String?) {
        guestAddress = value
    }

    // MARK: - VZVirtualMachineDelegate

    func guestDidStop(_ virtualMachine: VZVirtualMachine) {
        machine = nil
        health = .stopped
        NotificationCenter.default.post(name: .lumaEmulatorGuestStopped, object: nil)
    }

    func virtualMachine(_ virtualMachine: VZVirtualMachine, didStopWithError error: Error) {
        machine = nil
        health = .failed
        lastFailure = error.localizedDescription
        NotificationCenter.default.post(name: .lumaEmulatorGuestStopped, object: nil)
    }
}

extension Notification.Name {
    static let lumaEmulatorGuestStopped = Notification.Name("org.projectluma.emulator.guestStopped")
}
