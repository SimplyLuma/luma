// SPDX-License-Identifier: Apache-2.0

import Foundation
import LumaEmulatorKit

/// Builds the immutable factory image, then hands out copy-on-write overlays.
///
/// Nothing here treats a running VM as source of truth. The image is rebuilt
/// from a pinned Fedora artifact plus a version-controlled provisioning script,
/// and the manifest records exactly what went in so a later claim about the
/// guest can be checked rather than believed.
struct Bootstrap {
    let paths: EmulatorPaths
    let output: Output

    struct BaseImage {
        let id: String
        let fileName: String
        let url: String
        let sha256: String
        let diskGiB: Int
    }

    /// Pinned by version and SHA-256. Changing either is a deliberate,
    /// reviewable edit, never something the emulator does on its own.
    static let base = BaseImage(
        id: "fedora44-aarch64",
        fileName: "Fedora-Cloud-Base-Generic-44-1.7.aarch64.qcow2",
        url: "https://dl.fedoraproject.org/pub/fedora/linux/releases/44/Cloud/aarch64/images/Fedora-Cloud-Base-Generic-44-1.7.aarch64.qcow2",
        sha256: "55c60a3b80d3616a08705afd0459e75fe9f03c54aba7a46e4002a41a72fa0d5b",
        diskGiB: 20
    )

    // MARK: - Guest key

    /// One key pair per installation, never committed, never shared, 0600.
    @discardableResult
    func ensureGuestKey() throws -> String {
        let manager = FileManager.default
        if manager.fileExists(atPath: paths.guestPublicKey.path),
           manager.fileExists(atPath: paths.guestKey.path) {
            return try String(contentsOf: paths.guestPublicKey, encoding: .utf8)
                .trimmingCharacters(in: .whitespacesAndNewlines)
        }
        guard let keygen = Shell.which("ssh-keygen") else {
            throw EmulatorError.failure("no-ssh-keygen", "ssh-keygen is missing from this Mac")
        }
        try? manager.removeItem(at: paths.guestKey)
        try? manager.removeItem(at: paths.guestPublicKey)
        let result = try Shell.run(keygen, [
            "-t", "ed25519", "-N", "", "-C", "luma-emulator",
            "-f", paths.guestKey.path,
        ], timeout: 60)
        guard result.succeeded else {
            throw EmulatorError.failure("keygen-failed", result.standardError)
        }
        try manager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: paths.guestKey.path)
        try manager.setAttributes(
            [.posixPermissions: 0o644], ofItemAtPath: paths.guestPublicKey.path
        )
        return try String(contentsOf: paths.guestPublicKey, encoding: .utf8)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    // MARK: - Base artifact

    /// Downloads with resume, then verifies. An interrupted download is
    /// resumable; an image that fails verification is removed rather than left
    /// where a later run could mistake it for good.
    func ensureBaseRaw() throws -> URL {
        let manager = FileManager.default
        let raw = paths.cache.appendingPathComponent("fedora44-base.raw")
        if manager.fileExists(atPath: raw.path) { return raw }

        let archive = paths.cache.appendingPathComponent(Self.base.fileName)
        var archiveIsGood = manager.fileExists(atPath: archive.path)
        if archiveIsGood {
            archiveIsGood = try verify(archive, Self.base.sha256)
        }
        if !archiveIsGood {
            output.note("Downloading \(Self.base.fileName) (about 500 MB)…")
            guard let curl = Shell.which("curl") else {
                throw EmulatorError.failure("no-curl", "curl is missing from this Mac")
            }
            let result = try Shell.run(curl, [
                "-fL", "--retry", "5", "--retry-delay", "3", "-C", "-",
                "-o", archive.path, Self.base.url,
            ], timeout: 3600)
            guard result.succeeded else {
                throw EmulatorError.failure("download-failed", result.standardError)
            }
            guard try verify(archive, Self.base.sha256) else {
                try? manager.removeItem(at: archive)
                throw EmulatorError.failure(
                    "checksum-mismatch",
                    "the downloaded Fedora image did not match its pinned SHA-256 and was discarded"
                )
            }
        }
        output.note("Verified \(Self.base.fileName) against its pinned SHA-256.")

        guard let qemuImg = Shell.which("qemu-img") else {
            throw EmulatorError.failure(
                "no-converter",
                """
                qemu-img is needed once, to turn Fedora's qcow2 into the raw disk \
                Virtualization.framework requires. Install it with `brew install qemu`, \
                or place an already-converted fedora44-base.raw in \(paths.cache.path).
                """
            )
        }
        output.note("Converting the pinned image to a sparse raw disk…")
        let partial = paths.cache.appendingPathComponent("fedora44-base.raw.partial")
        try? manager.removeItem(at: partial)
        var result = try Shell.run(qemuImg, [
            "convert", "-O", "raw", "-S", "64k", archive.path, partial.path,
        ], timeout: 1800)
        guard result.succeeded else {
            try? manager.removeItem(at: partial)
            throw EmulatorError.failure("convert-failed", result.standardError)
        }
        result = try Shell.run(qemuImg, [
            "resize", "-f", "raw", partial.path, "\(Self.base.diskGiB)G",
        ], timeout: 300)
        guard result.succeeded else {
            try? manager.removeItem(at: partial)
            throw EmulatorError.failure("resize-failed", result.standardError)
        }
        // Only a fully converted and resized disk is given the final name, so an
        // interrupted preparation can never be mistaken for accepted state.
        try manager.moveItem(at: partial, to: raw)
        return raw
    }

    func verify(_ url: URL, _ expected: String) throws -> Bool {
        guard let shasum = Shell.which("shasum") else {
            throw EmulatorError.failure("no-shasum", "shasum is missing from this Mac")
        }
        let result = try Shell.run(shasum, ["-a", "256", url.path], timeout: 900)
        guard result.succeeded else { return false }
        let actual = result.trimmedOutput.split(separator: " ").first.map(String.init) ?? ""
        return actual == expected
    }

    // MARK: - cloud-init seed

    /// A NoCloud seed carrying the account, the public half of the guest key,
    /// and the boot-time address announcement. It holds no secret.
    func makeSeed(at destination: URL, publicKey: String) throws {
        let manager = FileManager.default
        let staging = paths.cache.appendingPathComponent("seed-staging", isDirectory: true)
        try? manager.removeItem(at: staging)
        try manager.createDirectory(at: staging, withIntermediateDirectories: true)

        let userData = """
        #cloud-config
        users:
          - name: luma
            gecos: Luma Emulator
            groups: [wheel]
            sudo: ["ALL=(ALL) NOPASSWD:ALL"]
            shell: /bin/bash
            lock_passwd: true
            ssh_authorized_keys:
              - \(publicKey)
        ssh_pwauth: false
        disable_root: true
        growpart:
          mode: auto
          devices: ['/']
        resize_rootfs: true
        write_files:
          - path: /etc/systemd/system/luma-emulator-announce.service
            permissions: '0644'
            content: |
              [Unit]
              Description=Announce the Luma Emulator guest address on the virtio console
              After=network-online.target
              Wants=network-online.target
              [Service]
              Type=oneshot
              RemainAfterExit=yes
              ExecStart=/usr/local/sbin/luma-emulator-announce
              [Install]
              WantedBy=multi-user.target
          - path: /usr/local/sbin/luma-emulator-announce
            permissions: '0755'
            content: |
              #!/bin/bash
              # The host reads this from the serial log so it never has to guess
              # the guest's address or scan the network for it.
              for attempt in $(seq 1 60); do
                address=$(ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | head -1)
                if [ -n "$address" ]; then
                  for repeat in 1 2 3; do
                    echo "luma-emulator-address=$address" > /dev/hvc0 2>/dev/null || true
                    sleep 1
                  done
                  exit 0
                fi
                sleep 1
              done
              exit 0
        runcmd:
          - [systemctl, enable, --now, luma-emulator-announce.service]
          - [bash, -c, "touch /etc/luma-emulator-seeded"]
        """

        let metaData = """
        instance-id: luma-emulator-factory
        local-hostname: luma-emulator
        """

        try userData.write(
            to: staging.appendingPathComponent("user-data"), atomically: true, encoding: .utf8
        )
        try metaData.write(
            to: staging.appendingPathComponent("meta-data"), atomically: true, encoding: .utf8
        )

        guard let hdiutil = Shell.which("hdiutil") else {
            throw EmulatorError.failure("no-hdiutil", "hdiutil is missing from this Mac")
        }
        try? manager.removeItem(at: destination)
        let isoBase = destination.deletingPathExtension().path
        let result = try Shell.run(hdiutil, [
            "makehybrid", "-iso", "-joliet",
            "-default-volume-name", "CIDATA",
            "-o", isoBase, staging.path,
        ], timeout: 120)
        guard result.succeeded else {
            throw EmulatorError.failure("seed-failed", result.standardError)
        }
        try? manager.removeItem(at: staging)
    }

    // MARK: - Copy-on-write overlays

    /// APFS clones the disk, so an overlay costs almost nothing until it is
    /// written to and creation is effectively instant.
    func cloneDisk(from source: URL, to destination: URL) throws {
        let manager = FileManager.default
        try? manager.removeItem(at: destination)
        try manager.createDirectory(
            at: destination.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        try manager.copyItem(at: source, to: destination)
    }
}
